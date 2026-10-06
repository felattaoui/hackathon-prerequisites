import pytest

from hapq.config import ConfigError, load_config, read_dotenv
from hapq.redact import redact, redact_text

from ..conftest import FAKE_SUB


def test_redacts_guid_token_and_secrets():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4eHh4eHh4In0.c2lnbmF0dXJl"
    text = (f"/subscriptions/{FAKE_SUB}/rg Bearer {jwt} InstrumentationKey=abcd;IngestionEndpoint=x "
            "user alice@contoso.com AccountKey=Zm9vYmFy==")
    out = redact_text(text)
    assert FAKE_SUB not in out and "<guid>" in out
    assert jwt not in out and "<token>" in out
    assert "InstrumentationKey=<secret>" in out and "AccountKey=<secret>" in out
    assert "alice@contoso.com" not in out


def test_redact_is_recursive():
    data = {"a": [FAKE_SUB, {"b": f"x {FAKE_SUB}"}], FAKE_SUB: 1}
    assert FAKE_SUB not in str(redact(data))


def test_config_reads_dotenv_and_masks_repr(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f'# comment\nAZURE_SUBSCRIPTION_ID="{FAKE_SUB}"\n', encoding="utf-8")
    assert read_dotenv(env)["AZURE_SUBSCRIPTION_ID"] == FAKE_SUB
    cfg = load_config(env_path=env, environ={})
    assert cfg.subscription_id == FAKE_SUB
    assert FAKE_SUB not in repr(cfg)
    assert cfg.subscription_fingerprint.startswith("sub-") and FAKE_SUB not in cfg.subscription_fingerprint


def test_environment_overrides_dotenv(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AZURE_SUBSCRIPTION_ID=00000000-0000-0000-0000-000000000000\n", encoding="utf-8")
    assert load_config(env_path=env, environ={"AZURE_SUBSCRIPTION_ID": FAKE_SUB}).subscription_id == FAKE_SUB


@pytest.mark.parametrize("value", ["", "not-a-guid"])
def test_invalid_subscription_rejected(tmp_path, value):
    env = tmp_path / ".env"
    env.write_text(f"AZURE_SUBSCRIPTION_ID={value}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_path=env, environ={})


def test_execution_point_is_declared_not_inferred(cfg, tmp_path):
    assert cfg.execution_point == "workstation-public"
    other = load_config(toml_path=cfg.config_path, env_path=tmp_path / "x",
                        environ={"AZURE_SUBSCRIPTION_ID": FAKE_SUB, "HAPQ_EXECUTION_POINT": "in-vnet-runner"})
    assert other.execution_point == "in-vnet-runner"


def test_guid_after_json_escape_is_redacted():
    escape = chr(92) + "u0060"  # literal backslash-u sequence as found in JSON container logs
    text = "The principal " + escape + FAKE_SUB + escape + " lacks the required data action"
    assert escape + FAKE_SUB in text and FAKE_SUB not in redact_text(text)


def test_publication_redaction_masks_live_endpoint_hostnames():
    from hapq.redact import redact_for_publication
    t = ("POST https://myacct-01.services.ai.azure.com/api/projects/p/toolboxes/t/mcp and "
         "https://app.env-123.westeurope.azurecontainerapps.io/mcp and crabc.azurecr.io and st1.blob.core.windows.net")
    out = redact_for_publication(t)
    assert "myacct-01" not in out and "app.env-123" not in out and "crabc" not in out and "st1." not in out
    assert "<host>.services.ai.azure.com" in out and "<host>.azurecr.io" in out
