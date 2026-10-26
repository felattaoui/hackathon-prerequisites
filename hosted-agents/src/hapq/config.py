"""Configuration loading.

The subscription id comes from `.env` (key AZURE_SUBSCRIPTION_ID) or the process environment.
Everything else comes from a TOML file (config/kit.toml by default, HAPQ_CONFIG to override).
The subscription id is never part of repr() and only a fingerprint is exposed for reports.
"""
from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION_KEY = "AZURE_SUBSCRIPTION_ID"
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ConfigError(ValueError):
    pass


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, optional quotes, '#' comments."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = val.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class ModelConfig:
    name: str
    version: str
    sku: str
    capacity: int


@dataclass(frozen=True)
class Limits:
    max_agent_versions: int = 6
    max_sessions: int = 12
    max_calls: int = 40
    max_concurrency: int = 1
    max_output_tokens: int = 300
    max_total_tokens: int = 150000
    call_timeout_s: int = 120
    deploy_timeout_s: int = 900
    trace_wait_s: int = 600
    idle_timeout_s: int = 120
    cpu: str = "0.5"
    memory: str = "1Gi"
    max_retries: int = 2
    max_polls: int = 120


@dataclass(frozen=True)
class KitConfig:
    subscription_id: str = field(repr=False)
    resource_group: str
    lab_region: str
    tracked_regions: tuple[str, ...]
    required_region: str
    required_providers: tuple[str, ...]
    kit_tag_key: str
    kit_tag_value: str
    model: ModelConfig
    execution_point: str
    results_dir: Path
    stale_after_days: int
    config_path: Path
    config_hash: str
    limits: Limits = field(default_factory=Limits)

    @property
    def subscription_fingerprint(self) -> str:
        """Stable, non-reversible identifier to correlate runs without exposing the id."""
        return "sub-" + hashlib.sha256(self.subscription_id.lower().encode()).hexdigest()[:10]

    @property
    def ledger_path(self) -> Path:
        return self.results_dir / "ledger.jsonl"

    @property
    def rg_scope(self) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"


def load_config(
    toml_path: Path | str | None = None,
    env_path: Path | str | None = None,
    environ: dict[str, str] | None = None,
) -> KitConfig:
    environ = dict(os.environ) if environ is None else environ
    toml_path = Path(toml_path or environ.get("HAPQ_CONFIG") or REPO_ROOT / "config" / "kit.toml")
    env_path = Path(env_path or REPO_ROOT / ".env")

    raw_toml = toml_path.read_bytes()
    data = tomllib.loads(raw_toml.decode("utf-8"))

    sub = environ.get(SUBSCRIPTION_KEY) or read_dotenv(env_path).get(SUBSCRIPTION_KEY, "")
    if not sub:
        raise ConfigError(f"{SUBSCRIPTION_KEY} not found in environment or {env_path.name}")
    if not _GUID.match(sub):
        raise ConfigError(f"{SUBSCRIPTION_KEY} is not a GUID")

    az, model, ex, res = data["azure"], data["model"], data.get("execution", {}), data.get("results", {})
    execution_point = environ.get("HAPQ_EXECUTION_POINT") or ex.get("execution_point", "unknown")
    results_dir = Path(res.get("dir", "results"))
    if not results_dir.is_absolute():
        results_dir = REPO_ROOT / results_dir

    return KitConfig(
        subscription_id=sub,
        resource_group=az["resource_group"],
        lab_region=az["lab_region"],
        tracked_regions=tuple(az.get("tracked_regions", [])),
        required_region=az["required_region"],
        required_providers=tuple(az.get("required_providers", [])),
        kit_tag_key=az["kit_tag"]["key"],
        kit_tag_value=az["kit_tag"]["value"],
        model=ModelConfig(model["name"], model["version"], model["sku"], int(model["capacity"])),
        execution_point=execution_point,
        results_dir=results_dir,
        stale_after_days=int(res.get("stale_after_days", 7)),
        config_path=toml_path,
        config_hash=hashlib.sha256(raw_toml).hexdigest()[:12],
        limits=Limits(**data.get("limits", {})),
    )


def load_toml(name: str) -> dict:
    """Load a catalog file from config/ (criteria.toml, doc_assertions.toml)."""
    return tomllib.loads((REPO_ROOT / "config" / name).read_text(encoding="utf-8"))
