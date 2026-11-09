"""FastAPI app for the viewer.

Pages (HTML): / (the test cases), /incident/{id} (one case).
JSON: /api/info, /api/evaluation, /api/incidents/{id}[?run=label],
/api/incidents/{id}/series/{sid}, POST /api/incidents/{id}/rerun, /api/rerun.

The viewer reads recorded runs. The only thing it can start is a rerun of one case, which
runs as a separate worker process (web/rerun.py) under a new label, so the official results
never change. There is no login (the data is synthetic); the server listens on 127.0.0.1
only (see __main__.py).
"""

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from investigator.web.exchanges import agent_prompts

INCIDENT_ID = re.compile(r"^inc_[0-9a-f]{8}$")
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class RerunRequest(BaseModel):
    systems: list[str] = ["multi_agent", "single_agent"]
    mode: str = "replay"
    model: str = "haiku"

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
STATIC = HERE / "static"


def create_app(store, reruns=None):
    app = FastAPI(title="Incident investigator viewer", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # Browsers must re-check scripts and styles, so a new version is never mixed with
        # an old cached one.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def page(name):
        response = FileResponse(PAGES / name)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def overview_page():
        return page("index.html")

    @app.get("/incident/{incident_id}")
    def incident_page(incident_id: str):
        return page("incident.html")

    @app.get("/api/info")
    def info():
        return {"label": store.label}

    @app.get("/api/evaluation")
    def evaluation():
        return store.evaluation()

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str, run: str | None = None):
        # Only the official label or one of its reruns can be shown.
        if run is not None and run != store.label and not run.startswith(store.label + "-rerun-"):
            return JSONResponse({"error": "unknown run"}, status_code=404)
        found = store.incident(incident_id, run_label=run)
        if found is None:
            return JSONResponse({"error": "unknown incident"}, status_code=404)
        return found

    @app.post("/api/incidents/{incident_id}/rerun")
    def rerun(incident_id: str, request: RerunRequest):
        if reruns is None:
            return JSONResponse({"error": "reruns are not enabled"}, status_code=503)
        if not INCIDENT_ID.match(incident_id):
            return JSONResponse({"error": "unknown incident"}, status_code=404)
        job, error = reruns.start(incident_id, request.systems, request.mode, request.model)
        if error is not None:
            status = 409 if "still running" in error else 400
            return JSONResponse({"error": error}, status_code=status)
        return JSONResponse(job, status_code=202)

    @app.get("/api/rerun")
    def rerun_status():
        if reruns is None:
            return {"state": "disabled"}
        summary = reruns.summary()
        if summary.get("label"):
            summary["progress"] = store.live_progress(summary["label"])
        return summary

    @app.get("/api/runs/{run_id}/conversation")
    def conversation(run_id: str):
        # The ID becomes part of a file path, so only a real run ID is accepted.
        if not RUN_ID.match(run_id):
            return JSONResponse({"error": "unknown run"}, status_code=404)
        return store.conversation(run_id)

    @app.get("/prompts")
    def prompts_page():
        return page("prompts.html")

    @app.get("/api/prompts")
    def prompts():
        return {"prompts": agent_prompts()}

    @app.get("/api/incidents/{incident_id}/exchanges")
    def exchanges(incident_id: str, system: str = "multi_agent", run: str | None = None):
        if system not in ("multi_agent", "single_agent"):
            return JSONResponse({"error": "unknown system"}, status_code=404)
        if run is not None and run != store.label and not run.startswith(store.label + "-rerun-"):
            return JSONResponse({"error": "unknown run"}, status_code=404)
        return store.exchanges(incident_id, system, run_label=run)

    @app.get("/api/incidents/{incident_id}/tool-data/{tool}")
    def tool_data(incident_id: str, tool: str):
        found = store.tool_data(incident_id, tool)
        if found is None:
            return JSONResponse({"error": "unknown tool"}, status_code=404)
        return found

    @app.get("/api/incidents/{incident_id}/series/{evidence_id}")
    def series(incident_id: str, evidence_id: str):
        found = store.series_points(incident_id, evidence_id)
        if found is None:
            return JSONResponse({"error": "unknown series"}, status_code=404)
        return found

    return app
