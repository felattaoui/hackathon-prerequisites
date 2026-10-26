"""Error attribution for the LangGraph and A2A runs (pure logic; not evidence)."""
from hapq.lab import qualify
from hapq.results import Status


class _HttpErr(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.status_code = code


def test_platform_vs_kit_errors():
    st, msg = qualify.platform_or_kit(_HttpErr(400, "Protocol a2a is not supported for hosted agents"))
    assert st is Status.FAIL and msg.startswith("explicit platform refusal")
    assert qualify.platform_or_kit(_HttpErr(500, "boom"))[0] is Status.FAIL
    st, msg = qualify.platform_or_kit(TypeError("bad argument"))
    assert st is Status.NOT_TESTED and msg.startswith("kit/code error")


def test_a2a_message_outcome_separates_protocol_and_platform():
    assert qualify.a2a_message_outcome(403, {}, "", "m")[0] is Status.FAIL
    st, msg = qualify.a2a_message_outcome(200, {"error": {"code": -32601, "message": "Method not found"}}, "", "m")
    assert st is Status.NOT_TESTED and msg.startswith("kit/protocol error")
    st, msg = qualify.a2a_message_outcome(200, {"error": {"code": -32004, "message": "Unsupported operation"}}, "", "m")
    assert st is Status.FAIL and "platform" in msg
    assert qualify.a2a_message_outcome(200, {}, "m hello", "m")[0] is Status.PASS
    assert qualify.a2a_message_outcome(200, {}, "hello", "m")[0] is Status.FAIL
    assert qualify.a2a_message_outcome(None, {}, "", "m")[0] is Status.NOT_TESTED


def test_lock_file_replaces_requirements_without_touching_reference_zip(tmp_path):
    import io, zipfile
    import pytest
    from hapq.config import REPO_ROOT
    if not (REPO_ROOT / "results" / "reference" / "trial-A-v2" / "hapq-min-v2.zip").exists():
        pytest.skip("local reference archive not present")
    from hapq.lab.scenarios import AGENTS_DIR, zip_dir
    from hapq.config import REPO_ROOT
    archived = zipfile.ZipFile(REPO_ROOT / "results" / "reference" / "trial-A-v2" / "hapq-min-v2.zip")
    current = zipfile.ZipFile(io.BytesIO(zip_dir(AGENTS_DIR / "minimal")))
    for name in archived.namelist():                           # reference agent v2 source unchanged (content)
        assert archived.read(name) == current.read(name), name
    assert zip_dir(AGENTS_DIR / "minimal") == zip_dir(AGENTS_DIR / "minimal")   # deterministic archive
    locked = zip_dir(AGENTS_DIR / "minimal", qualify.LOCKS / "minimal.requirements.txt")
    req = zipfile.ZipFile(io.BytesIO(locked)).read("requirements.txt").decode()
    assert "agent-framework-foundry-hosting==1.0.0b260918" in req


def test_hosted_agent_not_supported_is_an_explicit_platform_refusal():
    payload = {"error": {"code": -32099, "message": "The requested A2A operation is not supported for this hosted-agent target."}}
    st, msg = qualify.a2a_message_outcome(200, payload, "", "m")
    assert st is Status.FAIL and msg.startswith("explicit platform refusal")
