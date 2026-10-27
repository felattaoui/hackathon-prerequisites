"""Public repository hygiene: no confidential terms, no real identifiers, output-free notebooks."""
import json
import re
import subprocess

import pytest

GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
ALLOWED_GUIDS = {"00000000-0000-0000-0000-000000000000",
                 "a97b65f3-24c7-4388-baec-2e87135dc908"}   # built-in role: Cognitive Services User


def versioned_files(pii_root):
    """Files git would publish under pii/ (tracked or untracked but not ignored)."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "."],
                         cwd=pii_root, capture_output=True, text=True, check=True).stdout
    return [pii_root / line for line in out.splitlines() if line.strip()]


def test_confidential_paths_are_ignored(pii_root):
    for path in ["inputs/any.docx", ".env", "results/x.ipynb", "config/forbidden_terms.local.txt"]:
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=pii_root).returncode == 0, path
    assert not any("inputs" in p.relative_to(pii_root).parts for p in versioned_files(pii_root))


def test_forbidden_terms_absent(pii_root):
    terms_file = pii_root / "config" / "forbidden_terms.local.txt"
    if not terms_file.exists():
        pytest.skip("config/forbidden_terms.local.txt not present: create it to enforce this check")
    terms = [t.strip().lower() for t in terms_file.read_text(encoding="utf-8").splitlines()
             if t.strip() and not t.startswith("#")]
    for p in versioned_files(pii_root):
        text = p.read_text(encoding="utf-8", errors="ignore").lower()
        hits = [t for t in terms if t in text]
        assert not hits, f"{p.name}: {hits}"


def test_no_real_subscription_or_unknown_guid(pii_root):
    env = pii_root / ".env"
    real = re.search(r"^AZURE_SUBSCRIPTION_ID=(.+)$", env.read_text(), re.M) if env.exists() else None
    for p in versioned_files(pii_root):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if real:
            assert real.group(1).strip().strip('"') not in text, p.name
        assert not ({g.lower() for g in GUID.findall(text)} - ALLOWED_GUIDS), p.name


def test_notebooks_are_output_free(pii_root):
    for nb in (pii_root / "notebooks").glob("*.ipynb"):
        cells = json.loads(nb.read_text(encoding="utf-8"))["cells"]
        assert all(not c.get("outputs") for c in cells if c["cell_type"] == "code"), nb.name
