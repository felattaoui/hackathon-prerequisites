"""Shared fixtures.

Unit tests use a synthetic subscription id and a FakeRunner: their results are tagged
runner="fake" and are written only under pytest's tmp_path, never into results/.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hapq.azcli import Az, CmdResult, FakeRunner
from hapq.config import REPO_ROOT, load_config

FAKE_SUB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def cfg(tmp_path):
    toml = tmp_path / "kit.toml"
    shutil.copy(REPO_ROOT / "config" / "kit.toml", toml)
    text = toml.read_text(encoding="utf-8").replace('dir = "results"', f'dir = "{(tmp_path / "results").as_posix()}"')
    toml.write_text(text, encoding="utf-8")
    return load_config(toml_path=toml, env_path=tmp_path / "none.env", environ={"AZURE_SUBSCRIPTION_ID": FAKE_SUB})


def ok(payload) -> CmdResult:
    return CmdResult(0, json.dumps(payload), "")


def err(msg="ERROR: boom") -> CmdResult:
    return CmdResult(1, "", msg)


def has(*tokens):
    """Route predicate: all tokens appear in the az argv."""
    return lambda tool, args: all(t in args for t in tokens)


@pytest.fixture
def make_az():
    def _make(routes):
        return Az(FAKE_SUB, FakeRunner(routes))
    return _make


@pytest.fixture(scope="session")
def real_cfg():
    """Configuration with the real .env; only for -m azure tests."""
    return load_config()


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
