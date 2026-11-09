"""ToolService: one place that turns a raw tool call into a ToolResult envelope.

Order of work for every call:
1. validate the args against the tool's schema (unknown fields such as incident_id fail);
2. count the attempt for this normalised call (its fingerprint), so a retry is attempt 2;
3. wait the simulated latency;
4. ask the failure injector; a failed attempt never reaches the backend;
5. run the backend query; any exception becomes an "error" result, never a crash;
6. apply a partial-result rule if one matched, and build the envelope.

The attempt counter lives in this object, and there is one server (so one ToolService) per
run, so attempts are counted per run.
"""

import asyncio

from pydantic import ValidationError

from investigator.models.tools import ARGS_MODELS, ToolItem, ToolRequest, ToolResult
from investigator.tools.backend import BackendError

# How each data source describes itself in failure messages.
SOURCE_NAMES = {
    "query_logs": "log store",
    "query_metrics": "metrics store",
    "get_change_history": "change tracker",
    "get_service_dependencies": "service catalog",
    "search_past_incidents": "incident archive",
}


class UnknownToolError(Exception):
    pass


def validation_message(error):
    parts = []
    for problem in error.errors():
        location = []
        for piece in problem["loc"]:
            location.append(str(piece))
        parts.append("%s: %s" % (".".join(location), problem["msg"]))
    return "invalid arguments: " + "; ".join(parts)


class ToolService:
    def __init__(self, backend, injector, latency_ms=0):
        self.backend = backend
        self.injector = injector
        self.latency_ms = latency_ms
        self.attempts = {}       # fingerprint -> attempts so far
        self.call_count = 0
        self.handlers = {
            "query_logs": backend.query_logs,
            "query_metrics": backend.query_metrics,
            "get_change_history": backend.get_change_history,
            "get_service_dependencies": backend.get_service_dependencies,
            "search_past_incidents": backend.search_past_incidents,
        }

    def next_call_id(self):
        self.call_count = self.call_count + 1
        return "call-%06d" % self.call_count

    def count_attempt(self, fingerprint):
        attempt = self.attempts.get(fingerprint, 0) + 1
        self.attempts[fingerprint] = attempt
        return attempt

    async def call(self, tool, raw_args):
        if tool not in self.handlers:
            raise UnknownToolError("unknown tool: " + str(tool))
        call_id = self.next_call_id()

        if raw_args is None:
            raw_args = {}
        try:
            request = ToolRequest(tool=tool, args=raw_args)
        except ValidationError as error:
            return ToolResult(call_id=call_id, tool=tool, status="error",
                              error=validation_message(error))

        attempt = self.count_attempt(request.fingerprint())

        if self.latency_ms > 0:
            await asyncio.sleep(self.latency_ms / 1000.0)

        decision = self.injector.decide(tool, request.args, attempt)
        if decision.action == "fail":
            source = SOURCE_NAMES[tool]
            if decision.status == "timeout":
                message = "%s did not answer within 5000ms" % source
            else:
                message = "%s returned HTTP 503" % source
            return ToolResult(call_id=call_id, tool=tool, status=decision.status, error=message)

        args_model = ARGS_MODELS[tool].model_validate(request.args)
        handler = self.handlers[tool]
        try:
            page = await handler(args_model)
        except BackendError as error:
            return ToolResult(call_id=call_id, tool=tool, status="error", error=str(error))
        except Exception as error:
            return ToolResult(call_id=call_id, tool=tool, status="error",
                              error="%s failed: %s" % (SOURCE_NAMES[tool], type(error).__name__))

        items = page.items
        warnings = list(page.warnings)
        status = "ok"
        if decision.action == "partial":
            ids = []
            for item in items:
                ids.append(item.evidence_id)
            kept_ids = self.injector.keep_ids(tool, request.args, ids, decision.keep_fraction)
            kept = []
            for item in items:
                if item.evidence_id in kept_ids:
                    kept.append(item)
            if len(kept) < len(items):
                warnings.append("%s returned partial data: %d of %d results are missing"
                                % (SOURCE_NAMES[tool], len(items) - len(kept), len(items)))
                status = "partial"
            items = kept

        if status == "ok" and len(items) == 0:
            status = "empty"

        copied = []
        for item in items:
            copied.append(ToolItem(evidence_id=item.evidence_id, data=item.data))
        return ToolResult(call_id=call_id, tool=tool, status=status, items=copied,
                          truncated=page.truncated, next_cursor=page.next_cursor,
                          warnings=warnings)
