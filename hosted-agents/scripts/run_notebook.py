"""Execute a notebook top to bottom in a fresh kernel (no hidden state).

The executed copy goes to results/notebooks/ (git-ignored); the versioned notebook stays output-free.
Usage: python scripts/run_notebook.py notebooks/00_preflight_and_region.ipynb
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def main(path: str) -> int:
    src = Path(path)
    nb = nbformat.read(src, as_version=4)
    NotebookClient(nb, timeout=900, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}}).execute()
    out_dir = ROOT / "results" / "notebooks"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{src.stem}.{stamp}.executed.ipynb"
    nbformat.write(nb, out)
    text = out.read_text(encoding="utf-8")
    leaks = set(GUID.findall(text))
    print(f"executed: {out.relative_to(ROOT)}")
    print("GUIDs in outputs:", "none" if not leaks else f"{len(leaks)} FOUND")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
