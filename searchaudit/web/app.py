"""FastAPI app: serves stored audit reports (HTML, print view, PDF, raw JSON) and the methodology page.

    uv run uvicorn searchaudit.web.app:app --reload
    AUDITS_DIR=audits SHOW_FIXTURE=1 uv run uvicorn searchaudit.web.app:app

Env: AUDITS_DIR (default "audits"), SHOW_FIXTURE=1 lists audits/fixture on the homepage.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from searchaudit.report.format import FILTERS
from searchaudit.report.pitch import build_pitch
from searchaudit.web.store import Audit, AuditStore

HERE = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


def _json_script(data: Any) -> Markup:
    """JSON safe to embed inside <script type="application/json">."""
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=HERE / "templates")
    t.env.filters.update(FILTERS)
    t.env.filters["json_script"] = _json_script
    t.env.trim_blocks = True
    t.env.lstrip_blocks = True
    return t


def _pdf_name(company: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_") + "_search_audit.pdf"


class _Static(StaticFiles):
    """Revalidate CSS and JS on each load so a report refresh picks up a template change."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(audits_dir: Path | None = None, show_fixture: bool | None = None) -> FastAPI:
    root = audits_dir or Path(os.environ.get("AUDITS_DIR", "audits"))
    show = os.environ.get("SHOW_FIXTURE") == "1" if show_fixture is None else show_fixture
    # Public address of this site, printed in the PDF where it points at the online report.
    site_url = os.environ.get("SITE_URL", "https://search-audit-production.up.railway.app").rstrip("/")
    store = AuditStore(root, show)
    templates = _templates()
    app = FastAPI(title="Search Audit", docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.mount("/static", _Static(directory=HERE / "static"), name="static")

    def audit_or_404(slug: str) -> Audit:
        audit = store.get(slug)
        if audit is None:
            raise HTTPException(404, "No such report")
        return audit

    def page(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"site_url": site_url, **ctx})

    def report_ctx(audit: Audit) -> dict[str, Any]:
        pitch = build_pitch(root, audit.vm) if audit.slug == "spacex" else None
        if pitch and pitch.get("prior"):
            job_of = {pid: job["id"] for job in pitch["prior"]["jobs"] for pid in job["ids"]}
            for row in audit.vm["client"]["prompts"]:
                row["job"] = job_of.get(row["id"], "")
        return {
            "vm": audit.vm,
            "slug": audit.slug,
            "is_fixture": audit.is_fixture,
            "has_pdf": audit.pdf_path.is_file(),
            "pdf_name": _pdf_name(audit.results.meta.company),
            "pitch": pitch,
            "embedded": pitch is not None,
            "solo": audit.slug == "spacex",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html", audits=store.listed(), nav="home")

    @app.get("/methodology", response_class=HTMLResponse)
    def methodology(request: Request) -> HTMLResponse:
        return page(request, "methodology.html", nav="methodology")

    @app.get("/r/spacex-pitch", response_model=None)
    def spacex_pitch(request: Request) -> HTMLResponse | RedirectResponse:
        if store.get("spacex") is not None:
            return RedirectResponse("/r/spacex", status_code=302)
        return page(request, "pitch.html", pitch=build_pitch(root), nav="home", solo=True)

    # Declared before /r/{slug} so "<slug>.pdf" is not taken for a slug.
    @app.get("/r/{slug}.pdf")
    def report_pdf(slug: str) -> FileResponse:
        pitch_pdf = root / "spacex" / "report.pdf"
        if slug == "spacex" and store.get(slug) is None and pitch_pdf.is_file():
            return FileResponse(pitch_pdf, media_type="application/pdf", filename=_pdf_name("SpaceX"))
        audit = audit_or_404(slug)
        if not audit.pdf_path.is_file():
            raise HTTPException(404, "The PDF for this report has not been generated yet")
        return FileResponse(
            audit.pdf_path, media_type="application/pdf", filename=_pdf_name(audit.results.meta.company)
        )

    @app.get("/r/{slug}", response_class=HTMLResponse)
    def report(request: Request, slug: str) -> HTMLResponse:
        if slug == "spacex" and store.get(slug) is None:
            return page(request, "pitch.html", pitch=build_pitch(root), nav="home", solo=True)
        return page(request, "report.html", **report_ctx(audit_or_404(slug)))

    @app.get("/r/{slug}/print", response_class=HTMLResponse)
    def report_print(request: Request, slug: str) -> HTMLResponse:
        if slug == "spacex" and store.get(slug) is None:
            return page(request, "pitch.html", pitch=build_pitch(root), nav="home", solo=True, printing=True)
        return page(request, "print.html", **report_ctx(audit_or_404(slug)), printing=True)

    @app.get("/r/{slug}/print/full", response_class=HTMLResponse)
    def report_print_full(request: Request, slug: str) -> HTMLResponse:
        """The print view plus every prompt with its reference and every provider's full answer."""
        return page(request, "print.html", **report_ctx(audit_or_404(slug)), printing=True, full=True)

    @app.get("/r/{slug}/q/{prompt_id}", response_class=HTMLResponse)
    def prompt_page(request: Request, slug: str, prompt_id: int) -> HTMLResponse:
        audit = audit_or_404(slug)
        rows = audit.vm["prompts"]
        idx = next((i for i, r in enumerate(rows) if r["id"] == prompt_id), None)
        if idx is None:
            raise HTTPException(404, "No such prompt in this report")
        return page(
            request,
            "prompt.html",
            **report_ctx(audit),
            p=rows[idx],
            prev=rows[idx - 1]["id"] if idx > 0 else None,
            next=rows[idx + 1]["id"] if idx + 1 < len(rows) else None,
        )

    @app.get("/r/{slug}/results.json")
    def results_json(slug: str) -> FileResponse:
        audit = audit_or_404(slug)
        return FileResponse(
            audit.results_path, media_type="application/json", filename=f"{audit.slug}_results.json"
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc: HTTPException) -> HTMLResponse:
        detail = getattr(exc, "detail", "Not found")
        resp = templates.TemplateResponse(request, "404.html", {"detail": detail}, status_code=404)
        return resp

    return app


app = create_app()
