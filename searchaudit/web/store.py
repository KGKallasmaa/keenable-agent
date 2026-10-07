"""Loads audits/<slug>/results.json and caches the parsed results and view model in memory."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from searchaudit.models import AuditResults
from searchaudit.report.model import build_view_model

FIXTURE = "fixture"
log = logging.getLogger(__name__)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Audit:
    slug: str
    dir: Path
    results: AuditResults
    vm: dict[str, Any]
    mtime: float

    @property
    def results_path(self) -> Path:
        return self.dir / "results.json"

    @property
    def pdf_path(self) -> Path:
        return self.dir / "report.pdf"

    @property
    def is_fixture(self) -> bool:
        return self.slug == FIXTURE


class AuditStore:
    """Reads audits lazily; a cached audit is rebuilt when its results.json changes on disk."""

    def __init__(self, root: Path, show_fixture: bool = False) -> None:
        self.root = root
        self.show_fixture = show_fixture
        self._cache: dict[str, Audit] = {}

    def get(self, slug: str) -> Audit | None:
        if not SLUG_RE.match(slug):
            return None
        path = self.root / slug / "results.json"
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            self._cache.pop(slug, None)
            return None
        cached = self._cache.get(slug)
        if cached and cached.mtime == mtime:
            return cached
        try:
            results = AuditResults.model_validate_json(path.read_text())
        except (OSError, ValueError) as exc:  # pydantic's ValidationError is a ValueError
            log.warning("skipping unreadable audit %s: %s", slug, str(exc).splitlines()[0])
            return None
        audit = Audit(slug, path.parent, results, build_view_model(results), mtime)
        self._cache[slug] = audit
        return audit

    def listed(self) -> list[Audit]:
        """Audits for the homepage, newest first. The fixture is hidden unless show_fixture."""
        if not self.root.is_dir():
            return []
        slugs = sorted(p.parent.name for p in self.root.glob("*/results.json"))
        audits = [a for s in slugs if (s != FIXTURE or self.show_fixture) and (a := self.get(s))]
        return sorted(audits, key=lambda a: a.results.meta.created_at, reverse=True)
