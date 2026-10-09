"""Render a report's print view (/r/<slug>/print) to audits/<slug>/report.pdf with headless Chromium.

    uv run python scripts/make_pdf.py fixture                      # starts the app on a free port
    uv run python scripts/make_pdf.py spacex-jobs --url http://127.0.0.1:8000
    uv run python scripts/make_pdf.py fixture --screenshots out/   # also PNGs of /r/<slug> and the print view

Needs Chromium once: `uv run playwright install chromium`.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from playwright.sync_api import Page, sync_playwright

from searchaudit.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
FOOTER = (
    '<div style="width:100%;font:8px IBM Plex Sans,system-ui,sans-serif;color:#6b7280;padding:0 12mm;'
    'display:flex;justify-content:space-between"><span class="title"></span>'
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve(audits_dir: Path) -> Iterator[str]:
    """Run the report app in a background thread; yields its base URL."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(audits_dir, show_fixture=True), port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("report app did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _load(page: Page, url: str) -> None:
    resp = page.goto(url, wait_until="networkidle", timeout=60_000)
    if resp is None or not resp.ok:
        raise RuntimeError(f"GET {url} -> {resp.status if resp else 'no response'}")
    page.evaluate("document.fonts.ready")


def render(base: str, slug: str, out: Path, screenshots: Path | None = None) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            _load(page, f"{base}/r/{slug}/print")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(
                path=str(out),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=FOOTER,
            )
            if screenshots:
                screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshots / f"{slug}_print.png"), full_page=False)
                for scheme in ("light", "dark"):
                    page.emulate_media(color_scheme=scheme)
                    _load(page, f"{base}/r/{slug}")
                    page.screenshot(path=str(screenshots / f"{slug}_report_{scheme}.png"), full_page=True)
        finally:
            browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug")
    ap.add_argument("--url", help="base URL of a running app (default: start one)")
    ap.add_argument("--audits-dir", type=Path, default=ROOT / "audits")
    ap.add_argument("--out", type=Path, help="default: <audits-dir>/<slug>/report.pdf")
    ap.add_argument("--screenshots", type=Path, help="also save PNG screenshots into this directory")
    args = ap.parse_args()
    out = args.out or args.audits_dir / args.slug / "report.pdf"
    if args.url:
        render(args.url.rstrip("/"), args.slug, out, args.screenshots)
    else:
        with serve(args.audits_dir) as base:
            render(base, args.slug, out, args.screenshots)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
