"""Test doubles. Kept out of src/ so production code carries no test-only paths."""

import asyncio

from investigator.models.tools import ToolItem, ToolResult
from investigator.stable_hash import call_fingerprint
from investigator.tools.backend import BackendPage, EvidenceBackend

HANG = "hang"


def ok_result(tool, evidence_ids, call_id="fake"):
    items = []
    for evidence_id in evidence_ids:
        items.append(ToolItem(evidence_id=evidence_id, data={}))
    return ToolResult(call_id=call_id, tool=tool, status="ok", items=items)


def failed_result(tool, status, call_id="fake"):
    if status == "empty":
        return ToolResult(call_id=call_id, tool=tool, status="empty")
    if status == "partial":
        return ToolResult(call_id=call_id, tool=tool, status="partial",
                          items=[ToolItem(evidence_id="L-0000000a-99", data={})],
                          warnings=["some results are missing"])
    return ToolResult(call_id=call_id, tool=tool, status=status, error="simulated " + status)


class ScriptedCaller:
    """Stands in for the MCP client. respond(tool, args, attempt) decides each answer:
    a ToolResult, an exception to raise, or HANG to never answer. attempt counts calls with
    the same fingerprint, like the real server does."""

    def __init__(self, respond=None, latency_s=0.0):
        self.respond = respond
        self.latency_s = latency_s
        self.calls = []
        self.counts = {}
        self.in_flight = 0
        self.peak = 0

    async def call(self, tool, args):
        fingerprint = call_fingerprint(tool, args)
        attempt = self.counts.get(fingerprint, 0) + 1
        self.counts[fingerprint] = attempt
        self.calls.append([tool, args])
        self.in_flight = self.in_flight + 1
        if self.in_flight > self.peak:
            self.peak = self.in_flight
        try:
            if self.latency_s > 0:
                await asyncio.sleep(self.latency_s)
            if self.respond is None:
                number = len(self.calls)
                return ok_result(tool, ["L-0000000a-%d" % number])
            answer = self.respond(tool, args, attempt)
            if isinstance(answer, str) and answer == HANG:
                await asyncio.sleep(3600)
            if isinstance(answer, Exception):
                raise answer
            return answer
        finally:
            self.in_flight = self.in_flight - 1


class MemoryCallLog:
    """Collects tool-call log rows in a list instead of eval.tool_calls."""

    def __init__(self):
        self.rows = []

    async def record(self, row):
        self.rows.append(row)


def default_items():
    items = {}
    items["query_logs"] = []
    for number in range(1, 7):
        items["query_logs"].append(
            ToolItem(evidence_id="L-0000000a-%d" % number,
                     data={"service": "api", "message": "line %d" % number})
        )
    items["query_metrics"] = [ToolItem(evidence_id="M-0000000a-1", data={"metric_name": "cpu_pct"})]
    items["get_change_history"] = [ToolItem(evidence_id="C-0000000a-1", data={"summary": "deploy"})]
    items["get_service_dependencies"] = [
        ToolItem(evidence_id="D-0000000a-1", data={"caller": "api", "callee": "db"})
    ]
    items["search_past_incidents"] = []   # an empty tool, to exercise status "empty"
    return items


class FakeBackend(EvidenceBackend):
    """Returns canned items per tool and records every call it receives."""

    def __init__(self, items_by_tool=None, error=None, rules=None):
        if items_by_tool is None:
            items_by_tool = default_items()
        self.items_by_tool = items_by_tool
        self.error = error
        self.rules = rules or []
        self.calls = []

    def page(self, tool, args):
        """Canned items, each stamped with the request, so a test can match answers to asks."""
        request = args.model_dump(mode="json")
        self.calls.append([tool, request])
        if self.error is not None:
            raise self.error
        items = []
        for item in self.items_by_tool.get(tool, []):
            data = dict(item.data)
            data["request"] = request
            items.append(ToolItem(evidence_id=item.evidence_id, data=data))
        return BackendPage(items=items)

    async def query_logs(self, args):
        return self.page("query_logs", args)

    async def query_metrics(self, args):
        return self.page("query_metrics", args)

    async def get_change_history(self, args):
        return self.page("get_change_history", args)

    async def get_service_dependencies(self, args):
        return self.page("get_service_dependencies", args)

    async def search_past_incidents(self, args):
        return self.page("search_past_incidents", args)

    async def failure_rules(self):
        return self.rules
