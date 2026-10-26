"""Preflight decision logic with canned az responses (FakeRunner). Not Azure evidence."""
from hapq.checks import preflight
from hapq.results import Status

from ..conftest import FAKE_SUB, err, has, ok

ACCOUNT = {"id": FAKE_SUB, "state": "Enabled", "user": {"type": "user"}}


def test_auth_blocked_without_identity(cfg, make_az):
    az = make_az([(has("account", "show"), err("ERROR: Please run 'az login'"))])
    r = preflight.check_auth(cfg, az)
    assert r.status is Status.BLOCKED
    assert all("--subscription" in args for _, args in az.runner.calls)


def test_run_all_stops_when_not_authenticated(cfg, make_az):
    az = make_az([(has("account", "show"), err())])
    assert [r.check_id for r in preflight.run_all(cfg, az)] == ["PF-AUTH"]


def test_subscription_mismatch_fails(cfg, make_az):
    az = make_az([(has("account", "show"), ok({**ACCOUNT, "id": "99999999-2222-3333-4444-555555555555"}))])
    assert preflight.check_subscription_target(cfg, az).status is Status.FAIL


def test_missing_resource_group_fails(cfg, make_az):
    az = make_az([(has("group", "show"), err("ResourceGroupNotFound"))])
    assert preflight.check_resource_group(cfg, az).status is Status.FAIL


def test_untagged_resource_group_fails_and_inventory_has_regions(cfg, make_az):
    az = make_az([(has("group", "show"), ok({"location": "westeurope", "tags": {}})),
                  (has("resource", "list"), ok([{"type": "Microsoft.Storage/storageAccounts", "name": "s",
                                                  "location": "northeurope", "tags": None}]))])
    r = preflight.check_resource_group(cfg, az)
    assert r.status is Status.FAIL
    assert r.details["resources"][0]["location"] == "northeurope"


def test_unregistered_provider_fails(cfg, make_az):
    providers = [{"n": p, "s": "Registered"} for p in cfg.required_providers]
    providers[1]["s"] = "NotRegistered"
    r = preflight.check_providers(cfg, make_az([(has("provider", "list"), ok(providers))]))
    assert r.status is Status.FAIL and providers[1]["n"] in r.observed


def test_allowed_locations_policy_excluding_lab_region_fails(cfg, make_az):
    assignment = {"id": "a1", "displayName": "Allowed locations", "enforcementMode": "Default",
                  "parameters": {"listOfAllowedLocations": {"value": ["northeurope"]}}}
    az = make_az([(has("policy", "assignment", "list"), ok([assignment]))])
    assert preflight.check_policies(cfg, az).status is Status.FAIL


def test_allowed_locations_policy_including_lab_region_passes(cfg, make_az):
    assignment = {"id": "a1", "displayName": "Allowed locations", "enforcementMode": "Default",
                  "parameters": {"listOfAllowedLocations": {"value": ["westeurope"]}}}
    az = make_az([(has("policy", "assignment", "list"), ok([assignment]))])
    assert preflight.check_policies(cfg, az).status is Status.PASS


def test_roles_require_data_plane_role(cfg, make_az):
    az = make_az([(has("signed-in-user"), ok({"id": "u"})),
                  (has("role", "assignment", "list"), ok([{"role": "Owner", "scope": f"/subscriptions/{FAKE_SUB}"}]))])
    r = preflight.check_roles(cfg, az)
    assert r.status is Status.FAIL and "Foundry data plane" in r.observed


def test_roles_on_other_resource_group_are_ignored(cfg, make_az):
    az = make_az([(has("signed-in-user"), ok({"id": "u"})),
                  (has("role", "assignment", "list"), ok([
                      {"role": "Owner", "scope": f"/subscriptions/{FAKE_SUB}/resourceGroups/other"},
                      {"role": "Foundry User", "scope": f"/subscriptions/{FAKE_SUB}"}]))])
    assert preflight.check_roles(cfg, az).status is Status.FAIL


def test_registered_preview_feature_prevents_ga_isolation(cfg, make_az):
    az = make_az([(has("feature", "list"), ok([{"n": "HostedAgentsPreview", "s": "Registered"}]))])
    assert preflight.check_preview_features(cfg, az).status is Status.NOT_TESTED


def test_quota_insufficient_fails(cfg, make_az):
    name = f"OpenAI.{cfg.model.sku}.{cfg.model.name}"
    az = make_az([(has("usage", "list"), ok([{"name": {"value": name}, "currentValue": 295.0, "limit": 300.0}]))])
    assert preflight.check_model_quota(cfg, az).status is Status.FAIL


def test_model_without_requested_sku_fails(cfg, make_az):
    model = {"model": {"name": cfg.model.name, "version": cfg.model.version, "skus": [{"name": "GlobalStandard"}]}}
    az = make_az([(has("model", "list"), ok([model]))])
    assert preflight.check_model_available(cfg, az).status is Status.FAIL


def test_required_region_arm_availability_is_never_a_pass(cfg, make_az):
    az = make_az([(has("list-locations"), ok([{"n": "westeurope", "d": "West Europe"},
                                               {"n": "northeurope", "d": "North Europe"}])),
                  (has("provider", "show"), ok(["West Europe", "North Europe"]))])
    by_id = {r.check_id: r for r in preflight.check_arm_locations(cfg, az)}
    assert by_id["PF-ARM-LOCATIONS"].status is Status.PASS
    assert by_id["PF-ARM-LOCATIONS-REQUIRED"].status is Status.NOT_TESTED
