import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.cost import CONTEXT_USD_PER_MILLION, KEENABLE_REQUEST_USD
from app.pipeline import load_sample, run_queries
from app.store import RunStore

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
STORE = RunStore(DATA_DIR)
USD_PER_MILLION = float(os.environ.get("CONTEXT_USD_PER_MILLION", CONTEXT_USD_PER_MILLION))
KEENABLE_USD = float(os.environ.get("KEENABLE_USD_PER_REQUEST", KEENABLE_REQUEST_USD))

app = FastAPI(title="Replay")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class QueryIn(BaseModel):
    id: str | None = None
    query: str


class RunIn(BaseModel):
    mode: str = "preview"
    queries: list[QueryIn] = Field(default_factory=list)
    password: str = ""


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status(request: Request) -> dict:
    return {
        "exa_configured": bool(os.environ.get("EXA_API_KEY")),
        "keenable_configured": bool(os.environ.get("KEENABLE_API_KEY")),
        "password_required": _password_required(request),
        "context_usd_per_million": USD_PER_MILLION,
        "keenable_usd_per_request": KEENABLE_USD,
        "exa_list_usd": 0.007,
    }


@app.get("/api/sample")
def sample() -> dict:
    return {"queries": load_sample(), "label": "Sample questions. Not your Exa log."}


@app.get("/api/runs/latest")
def latest() -> dict:
    found = STORE.latest()
    if found is None:
        return {"status": "empty"}
    return found


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    found = STORE.get(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return found


@app.post("/api/runs")
def start_run(body: RunIn, request: Request) -> dict:
    mode = body.mode
    if mode not in {"preview", "live"}:
        raise HTTPException(status_code=400, detail="mode must be preview or live")
    queries = [item.model_dump() for item in body.queries] or load_sample()
    if mode == "live":
        _guard_live(request, body.password)
    run_id = STORE.create({"mode": mode, "query_count": len(queries)})
    thread = threading.Thread(
        target=_execute,
        args=(run_id, queries, mode),
        daemon=True,
    )
    thread.start()
    return {"id": run_id, "status": "queued"}


def _execute(run_id: str, queries: list[dict], mode: str) -> None:
    try:
        payload = run_queries(
            queries,
            mode,
            os.environ.get("EXA_API_KEY", ""),
            os.environ.get("KEENABLE_API_KEY", ""),
            USD_PER_MILLION,
            KEENABLE_USD,
        )
        STORE.update(run_id, "done", payload)
    except Exception as exc:  # noqa: BLE001 — surface the failure on the run, do not drop it
        STORE.update(run_id, "error", {"error": str(exc), "mode": mode})


def _password_required(request: Request) -> bool:
    host = (request.url.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False
    return True


def _guard_live(request: Request, password: str) -> None:
    if not _password_required(request):
        return
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Set APP_PASSWORD before a live run on a public host. It spends API credit.",
        )
    if password != expected:
        raise HTTPException(status_code=401, detail="Wrong password")
