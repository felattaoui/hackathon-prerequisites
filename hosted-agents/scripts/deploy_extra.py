"""Tracked deployment of an additional environment-B template (what-if guard, baseline, ledger).

Usage:
  python scripts/deploy_extra.py proj3   # Standard-setup project proj3 (S07 on the Standard path)
  python scripts/deploy_extra.py portal  # Bastion + Windows VM for the manual in-VNet portal check

The VM admin password is generated locally, written to config/portal-vm.local.txt (git-ignored) and never printed.
"""
from __future__ import annotations

import json
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hapq.azcli import Az  # noqa: E402
from hapq.config import load_config  # noqa: E402
from hapq.ledger import Ledger  # noqa: E402
from hapq.results import new_run_id  # noqa: E402
from hapq.tracking import tracked_deploy  # noqa: E402

TEMPLATES = {"proj3": "standard-proj3.bicep", "portal": "portal-access.bicep"}


def password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!#"
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(24))
        if any(c.isupper() for c in p) and any(c.islower() for c in p) and any(c.isdigit() for c in p) \
                and any(c in "-_.!#" for c in p):
            return p


def main(which: str) -> int:
    cfg = load_config()
    extra = json.loads((ROOT / "config" / "lab-b-extra.local.json").read_text(encoding="utf-8"))
    profile = (ROOT / "config" / "lab-b.local.toml").read_text(encoding="utf-8")
    account = next(l.split("=", 1)[1].strip().strip('"') for l in profile.splitlines() if l.startswith("account_name"))
    suffix = account.rsplit("-", 1)[-1]
    vnet = f"vnet-hapq-b-{suffix}"
    params = {"location": cfg.lab_region, "suffix": suffix, "vnetName": vnet}
    if which == "proj3":
        params["accountName"] = account
        if len(sys.argv) > 2:
            params["cosmosLocation"] = sys.argv[2]
    else:
        pw_file = ROOT / "config" / "portal-vm.local.txt"
        if not pw_file.exists():
            pw_file.write_text(password(), encoding="utf-8")
        params["adminPassword"] = pw_file.read_text(encoding="utf-8").strip()
    out = tracked_deploy(cfg, Az(cfg.subscription_id), Ledger(cfg.ledger_path), "B", f"hapq-b-{suffix}-{which}",
                         ROOT / "infra" / "phase-b-private" / TEMPLATES[which], params, new_run_id())
    print(json.dumps({"outputs": {k: v for k, v in out["outputs"].items()}, "recorded": out["recorded"]}, default=str)[:2000])
    _ = extra
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
