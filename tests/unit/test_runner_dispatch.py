"""Runner VM busy handling: identify the holder from the dispatch journal; never wait on an unknown or stuck command."""
import json
import time

from hapq.lab import private_run as pr


def _journal(tmp_path, monkeypatch, entries):
    path = tmp_path / "dispatch.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    monkeypatch.setattr(pr, "DISPATCH", path)


def test_no_journal_means_unknown_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "DISPATCH", tmp_path / "missing.jsonl")
    assert pr.busy_holder() is None


def test_finished_commands_are_not_holders(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [
        {"event": "start", "id": "a", "label": "invoke", "timeout": 600, "t": time.time(), "at": "x"},
        {"event": "end", "id": "a", "label": "invoke", "timeout": 600, "t": time.time(), "at": "x"},
    ])
    assert pr.busy_holder() is None


def test_open_command_within_budget_is_identified(tmp_path, monkeypatch):
    now = time.time()
    _journal(tmp_path, monkeypatch, [{"event": "start", "id": "a", "label": "deploy-image", "timeout": 600, "t": now, "at": "x"}])
    h = pr.busy_holder(exclude="b")
    assert h["label"] == "deploy-image" and h["deadline"] > now


def test_deadline_capped_by_run_command_limit(tmp_path, monkeypatch):
    t0 = time.time() - pr.RUN_COMMAND_MAX_S - 300
    _journal(tmp_path, monkeypatch, [{"event": "start", "id": "a", "label": "invoke", "timeout": 10 * 3600, "t": t0, "at": "x"}])
    assert pr.busy_holder()["deadline"] < time.time()  # past deadline -> treated as stuck by the caller


def test_own_dispatch_is_excluded(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [{"event": "start", "id": "self", "label": "invoke", "timeout": 600, "t": time.time(), "at": "x"}])
    assert pr.busy_holder(exclude="self") is None
