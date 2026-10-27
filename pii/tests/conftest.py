import sys
from pathlib import Path

import pytest

PII_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PII_ROOT / "notebooks"))


@pytest.fixture
def pii_root():
    return PII_ROOT
