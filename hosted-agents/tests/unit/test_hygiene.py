"""Repository hygiene: no secrets, no confidential inputs, no implicit provisioning in notebooks."""
import hashlib
import json
import re

import pytest

DELIVERABLE_GLOBS = ["*.md", "config/*.toml", "src/**/*.py", "tests/**/*.py", "notebooks/*.ipynb",
                     "infra/**/*.bicep", "infra/**/*.json", "scripts/*", "docs/*.md", ".env.example",
                     "tools/**/*", "agents/**/*"]
GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Well-known public GUIDs allowed in deliverables (built-in role/policy definition ids, test placeholders)
ALLOWED_GUIDS = {"00000000-0000-0000-0000-000000000000", "11111111-2222-3333-4444-555555555555",
                 "99999999-2222-3333-4444-555555555555",
                 "eed3b665-ab3a-47b6-8f48-c9382fb1dad6",   # Foundry Agent Consumer role (Learn)
                 "e56962a6-4747-49cd-b67b-bf8b01975c4c",   # built-in policy: Allowed locations
                 "e765b5de-1225-4ba3-bd56-1ac6695af988",   # built-in policy: Allowed locations for RGs
                 "7f951dda-4ed3-4680-a7ca-43fe172d538d",   # built-in role: AcrPull
                 "8ebe5a00-799e-43f5-93ac-243d3dce84a7",   # built-in role: Search Index Data Contributor
                 "7ca78c08-252a-4471-8644-bb5ff32d4ba0",   # built-in role: Search Service Contributor
                 "230815da-be43-4aae-9cb4-875f7bd000aa",   # built-in role: Cosmos DB Operator
                 "b7e6dc6d-f1e8-4753-8033-0f276bb0955b",   # built-in role: Storage Blob Data Owner
                 "00000000-0000-0000-0000-000000000002",   # Cosmos DB built-in data contributor (SQL role)
                 "04b07795-8ddb-461a-bbee-02f9e1bf7b46",   # first-party Azure CLI client id (public)
                 "53ca6127-db72-4b80-b1b0-d745d6d5456d",   # built-in role: Foundry User
                 "8311e382-0749-4cb8-b61a-304f252e45ec",   # built-in role: AcrPush
                 "ba92f5b4-2d11-453d-a403-e96b0029c9fe",   # built-in role: Storage Blob Data Contributor
                 "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",   # built-in role: Storage Blob Data Reader
                 "43d0d8ad-25c7-4714-9337-8ba259a9fe05"}   # built-in role: Monitoring Reader


def deliverables(root):
    seen = set()
    for g in DELIVERABLE_GLOBS:
        for p in root.glob(g):
            # *.local.* files are generated, git-ignored and environment-specific: not deliverables
            if p.is_file() and ".conda" not in p.parts and ".local." not in p.name and p not in seen:
                seen.add(p)
                yield p


def test_gitignore_excludes_sensitive_paths(repo_root):
    # the kit lives in a sub-folder of the git repository: use the .gitignore of the repository root
    root = next(p for p in (repo_root, *repo_root.parents) if (p / ".gitignore").exists())
    ignore = (root / ".gitignore").read_text().splitlines()
    for entry in (".env", "inputs/", "results/", ".conda/", "*.local.*", "deliverables/"):
        assert entry in ignore


def test_no_real_subscription_or_unknown_guid_in_deliverables(repo_root):
    env = repo_root / ".env"
    real = env.read_text().split("=", 1)[1].strip().strip('"') if env.exists() else None
    for p in deliverables(repo_root):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if real:
            assert real not in text, p
        unknown = {g.lower() for g in GUID.findall(text)} - ALLOWED_GUIDS
        assert not unknown, f"{p}: {unknown}"


def test_forbidden_terms_absent(repo_root):
    """Terms from the confidential source (customer, hostnames, people) listed in a git-ignored local file."""
    terms_file = repo_root / "config" / "forbidden_terms.local.txt"
    if not terms_file.exists():
        pytest.skip("config/forbidden_terms.local.txt not present: create it to enforce this check")
    terms = [t.strip().lower() for t in terms_file.read_text(encoding="utf-8").splitlines()
             if t.strip() and not t.startswith("#")]
    for p in deliverables(repo_root):
        text = p.read_text(encoding="utf-8", errors="ignore").lower()
        hits = [t for t in terms if t in text]
        assert not hits, f"{p}: {hits}"


def test_inputs_not_copied_into_deliverables(repo_root):
    inputs = repo_root / "inputs"
    if not inputs.exists():
        pytest.skip("no inputs directory")
    digests = {hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.rglob("*") if p.is_file()}
    for p in repo_root.rglob("*"):
        if p.is_file() and not ({"inputs", ".conda", "results", ".git"} & set(p.parts)) and p.stat().st_size < 50_000_000:
            assert hashlib.sha256(p.read_bytes()).hexdigest() not in digests, p


def test_notebooks_have_no_outputs_and_no_provisioning(repo_root):
    forbidden = re.compile(r"(az\s+group\s+create|deployment\s+group\s+create|\bcleanup\b.*--execute|"
                           r"az\s+\S+\s+delete|create_version|begin_create|azd\s+(up|provision|deploy))", re.I)
    for nb_path in (repo_root / "notebooks").glob("*.ipynb"):
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                assert not cell.get("outputs"), f"{nb_path.name}: committed outputs"
                src = "".join(cell["source"])
                assert not forbidden.search(src), f"{nb_path.name}: provisioning/cleanup command in code cell"
