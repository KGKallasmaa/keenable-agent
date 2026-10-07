"""Audit configuration (audits/<slug>/audit.yaml) and runtime settings from the environment."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from searchaudit.models import Prompt, ProviderId, ProviderInfo

DEFAULT_JUDGE_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_ROUTER_MODEL = "typesafe/jev-1.13"
DEFAULT_QUERYGEN_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_KEENABLE_USD_PER_REQUEST = 0.004


# Display defaults for providers that need none in audit.yaml (`- {id: none}` is enough).
PROVIDER_DEFAULTS: dict[str, dict] = {
    "none": {"label": "No search (model memory)", "short": "No search", "domain": "", "hex": "#9ca3af"},
}


class ProviderConfig(BaseModel):
    id: ProviderId
    label: str
    short: str
    domain: str
    hex: str
    uses_today: bool = False
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("id") in PROVIDER_DEFAULTS:
            return {**PROVIDER_DEFAULTS[data["id"]], **data}
        return data


class Budget(BaseModel):
    max_tool_calls: int = 15
    max_seconds: float = 180.0


class Concurrency(BaseModel):
    global_limit: int = Field(12, alias="global")
    per_provider: dict[str, int] = Field(default_factory=dict)
    default_per_provider: int = 4

    def limit_for(self, provider: str) -> int:
        return self.per_provider.get(provider, self.default_per_provider)


class QuerygenConfig(BaseModel):
    """Settings for `searchaudit querygen` (all optional)."""

    model: str = DEFAULT_QUERYGEN_MODEL  # researches use cases, writes tasks, verifies references
    # Search for research/verification. Must not be an audited provider's engine, or the prompt set is
    # biased toward it: "native" = the model provider's own search (Anthropic's for Claude models).
    search_engine: str = "native"
    oversample: float = 3.0  # candidates generated per final prompt
    routine_share: float = 0.10  # share of the final set kept as "routine" controls the model knows
    fresh_days: int = 90  # "fresh" means published within this many days
    max_usd: float = 5.0  # querygen stops launching calls at this spend
    concurrency: int = 6


class AuditConfig(BaseModel):
    slug: str
    company: str
    category: str
    company_url: str = ""  # querygen: the company's site, to research its agent workflows
    use_case_hints: list[str] = Field(default_factory=list)  # querygen: workflows to make sure are covered
    querygen: QuerygenConfig = Field(default_factory=QuerygenConfig)
    prompts_file: str = "prompts.json"
    model: str = "openrouter/auto"
    temperature: float = 0.2
    judge_model: str = DEFAULT_JUDGE_MODEL
    router_model: str = DEFAULT_ROUTER_MODEL
    budget: Budget = Field(default_factory=Budget)
    cost_cap_usd: float = 40.0
    concurrency: Concurrency = Field(default_factory=Concurrency)
    providers: list[ProviderConfig]
    base_dir: Path = Path(".")  # directory of the yaml file; all outputs live here

    def enabled_providers(self, only: list[str] | None = None) -> list[ProviderConfig]:
        chosen = [p for p in self.providers if p.enabled]
        if only:
            unknown = set(only) - {p.id for p in self.providers}
            if unknown:
                raise ValueError(f"unknown provider(s): {', '.join(sorted(unknown))}")
            chosen = [p for p in chosen if p.id in only]
        return chosen

    @property
    def runs_path(self) -> Path:
        return self.base_dir / "runs.jsonl"

    @property
    def judgments_path(self) -> Path:
        return self.base_dir / "judgments.jsonl"

    @property
    def results_path(self) -> Path:
        return self.base_dir / "results.json"

    @property
    def pinned_path(self) -> Path:
        return self.base_dir / "pinned.json"

    @property
    def querygen_dir(self) -> Path:
        return self.base_dir / "querygen"

    @property
    def probes_dir(self) -> Path:
        return self.base_dir / "probes"


def load_config(path: str | Path) -> AuditConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    return AuditConfig.model_validate({**raw, "base_dir": path.parent})


def load_prompts(config: AuditConfig) -> list[Prompt]:
    path = config.base_dir / config.prompts_file
    if not path.exists():
        raise FileNotFoundError(f"prompts file not found: {path}")
    return [Prompt.model_validate(item) for item in json.loads(path.read_text())]


def provider_info(cfg: ProviderConfig, via: str, price_note: str) -> ProviderInfo:
    return ProviderInfo(
        id=cfg.id, label=cfg.label, short=cfg.short, domain=cfg.domain, hex=cfg.hex,
        uses_today=cfg.uses_today, via=via, price_note=price_note,
    )


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = ""
    keenable_api_key: str = ""
    keenable_usd_per_request: float = DEFAULT_KEENABLE_USD_PER_REQUEST

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            keenable_api_key=os.environ.get("KEENABLE_API_KEY", ""),
            keenable_usd_per_request=float(
                os.environ.get("KEENABLE_USD_PER_REQUEST") or DEFAULT_KEENABLE_USD_PER_REQUEST
            ),
        )
