"""Append-only ledger of the objects this kit actually created.

Cleanup deletes only what the ledger says the kit created. Objects found before a creation step are recorded
as `preexisting` and are never deleted. The ledger lives in results/ledger.jsonl (git-ignored, local). It holds
resource ids, which are not secrets; it never holds tokens or keys.

Kinds:
  arm            ARM resource (incl. child resources, budgets, role assignments created by a deployment)
  role           role assignment created outside a deployment
  entra_app      Entra application registration (Graph object id)
  entra_sp       Entra service principal (Graph object id)
  agent          hosted agent (data plane; id = "<project endpoint>|<agent name>")
  toolbox        toolbox (data plane)
  connection     project connection (ARM child id)
  resource_group the kit resource group (deleted last, only on request and only if empty)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("arm", "role", "entra_app", "entra_sp", "agent", "toolbox", "connection", "resource_group")


class LedgerError(RuntimeError):
    pass


@dataclass
class Entry:
    kind: str
    id: str
    name: str
    environment: str
    event: str  # created | preexisting | deleted | purged | delete_failed
    at: str = ""
    run_id: str = ""
    extra: dict = field(default_factory=dict)


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _append(self, e: Entry) -> Entry:
        if e.kind not in KINDS:
            raise LedgerError(f"unknown kind {e.kind}")
        e.at = e.at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(e)) + "\n")
        return e

    def created(self, kind, id, name, environment, run_id="", **extra) -> Entry:
        return self._append(Entry(kind, id, name, environment, "created", run_id=run_id, extra=extra))

    def preexisting(self, kind, id, name, environment, run_id="", **extra) -> Entry:
        return self._append(Entry(kind, id, name, environment, "preexisting", run_id=run_id, extra=extra))

    def event(self, entry: Entry, event: str, **extra) -> Entry:
        return self._append(Entry(entry.kind, entry.id, entry.name, entry.environment, event,
                                  run_id=entry.run_id, extra={**entry.extra, **extra}))

    def entries(self) -> list[Entry]:
        if not self.path.exists():
            return []
        out = []
        try:
            for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    out.append(Entry(**json.loads(line)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise LedgerError(f"ledger unreadable at line {n}: {exc}") from exc
        return out

    def state(self) -> dict[tuple[str, str], Entry]:
        """Last event per (kind, lowercase id)."""
        last: dict[tuple[str, str], Entry] = {}
        for e in self.entries():
            last[(e.kind, e.id.lower())] = e
        return last

    def owned(self, environment: str | None = None) -> list[Entry]:
        """Objects created by the kit and not yet confirmed deleted (soft-deleted accounts stay owned until purged)."""
        out = []
        for e in self.state().values():
            if e.event in ("created", "delete_failed") or (e.event == "deleted" and e.extra.get("soft_deleted")):
                if environment in (None, e.environment):
                    out.append(e)
        return out

    def is_preexisting(self, kind: str, id: str) -> bool:
        e = self.state().get((kind, id.lower()))
        return bool(e and e.event == "preexisting")
