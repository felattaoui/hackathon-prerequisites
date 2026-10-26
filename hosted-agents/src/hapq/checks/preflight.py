"""Read-only preflight checks against the subscription in .env.

None of these functions create, modify or delete anything. Each returns CheckResult objects
with evidence=observed. They answer "is the lab ready and what does the subscription allow",
never "does the platform support feature X".
"""
from __future__ import annotations

import json
import re

from ..azcli import Az
from ..config import KitConfig
from ..results import CheckResult, Evidence, Status

OBS = Evidence.OBSERVED

# Built-in "Allowed locations" policy definitions
_ALLOWED_LOCATION_DEFS = ("e56962a6-4747-49cd-b67b-bf8b01975c4c", "e765b5de-1225-4ba3-bd56-1ac6695af988")
_PROVISION_ROLES = {"Owner", "Contributor"}
_RBAC_ADMIN_ROLES = {"Owner", "User Access Administrator", "Role Based Access Control Administrator"}
_DATA_PLANE_ROLES = {"Foundry User", "Azure AI User", "Foundry Owner", "Azure AI Owner",
                     "Foundry Project Manager", "Azure AI Project Manager"}


def _err(res) -> str:
    return (res.stderr or "").strip().splitlines()[-1][:300] if res.stderr else f"exit {res.returncode}"


def check_auth(cfg: KitConfig, az: Az) -> CheckResult:
    res = az.json("account", "show")
    if not res.ok:
        return CheckResult("PF-AUTH", "C00", "Azure CLI authenticated for the target subscription",
                           Status.BLOCKED, OBS, "az account show succeeds", _err(res),
                           details={"hint": "Run `az login` (the kit never changes the default subscription)."})
    acc = res.json()
    enabled = acc.get("state") == "Enabled"
    return CheckResult("PF-AUTH", "C00", "Azure CLI authenticated for the target subscription",
                       Status.PASS if enabled else Status.FAIL, OBS,
                       "Subscription reachable and Enabled",
                       f"state={acc.get('state')}, identity type={acc.get('user', {}).get('type')}",
                       details={"identity_type": acc.get("user", {}).get("type")})


def check_subscription_target(cfg: KitConfig, az: Az) -> CheckResult:
    scoped = az.json("account", "show")
    default = az.json("account", "show", scoped=False)
    if not scoped.ok:
        return CheckResult("PF-SUB", "C00", "Commands target the subscription from .env", Status.BLOCKED, OBS,
                           "Explicit --subscription resolves to the .env subscription", _err(scoped))
    matches = scoped.json().get("id", "").lower() == cfg.subscription_id.lower()
    default_is_target = default.ok and default.json().get("id", "").lower() == cfg.subscription_id.lower()
    return CheckResult("PF-SUB", "C00", "Commands target the subscription from .env",
                       Status.PASS if matches else Status.FAIL, OBS,
                       "Explicit --subscription resolves to the .env subscription",
                       f"match={matches}; global default context is the target={default_is_target} (left unchanged)",
                       details={"subscription_fingerprint": cfg.subscription_fingerprint,
                                "default_context_is_target": default_is_target})


def check_resource_group(cfg: KitConfig, az: Az) -> CheckResult:
    title = f"Resource group {cfg.resource_group} exists and is tagged by the kit"
    res = az.json("group", "show", "-n", cfg.resource_group)
    if not res.ok:
        return CheckResult("PF-RG", "C00", title, Status.FAIL, OBS, "Resource group exists", _err(res),
                           details={"hint": "scripts/create-rg.sh creates it (explicit action)."})
    rg = res.json()
    tags = rg.get("tags") or {}
    tagged = tags.get(cfg.kit_tag_key) == cfg.kit_tag_value
    inv = az.json("resource", "list", "-g", cfg.resource_group)
    resources = inv.json() if inv.ok else []
    inventory = [{"type": r.get("type"), "name": r.get("name"), "location": r.get("location"),
                  "kit_tagged": (r.get("tags") or {}).get(cfg.kit_tag_key) == cfg.kit_tag_value}
                 for r in resources]
    return CheckResult("PF-RG", "C00", title, Status.PASS if tagged else Status.FAIL, OBS,
                       f"exists, tag {cfg.kit_tag_key}={cfg.kit_tag_value}",
                       f"RG location={rg.get('location')} (metadata only), tagged={tagged}, "
                       f"{len(inventory)} resource(s) inside",
                       region=rg.get("location"),
                       details={"resources": inventory,
                                "note": "Resource-group location does not determine resource locations; see each resource."})


def check_providers(cfg: KitConfig, az: Az) -> CheckResult:
    res = az.json("provider", "list", "--query", "[].{n:namespace,s:registrationState}")
    if not res.ok:
        return CheckResult("PF-PROVIDERS", "C00", "Required resource providers registered", Status.BLOCKED, OBS,
                           "All registered", _err(res))
    states = {p["n"].lower(): p["s"] for p in res.json()}
    per = {p: states.get(p.lower(), "NotFound") for p in cfg.required_providers}
    missing = [p for p, s in per.items() if s != "Registered"]
    return CheckResult("PF-PROVIDERS", "C00", "Required resource providers registered",
                       Status.PASS if not missing else Status.FAIL, OBS, "All registered",
                       "all registered" if not missing else f"not registered: {', '.join(missing)}",
                       details={"providers": per,
                                "note": "Registering a provider is a subscription-level change: requires approval."})


def check_policies(cfg: KitConfig, az: Az) -> CheckResult:
    title = "No policy blocks the lab region"
    # Subscription level (inherited from management groups included) + resource-group level.
    sub_level = az.json("policy", "assignment", "list", "--disable-scope-strict-match")
    rg_level = az.json("policy", "assignment", "list", "-g", cfg.resource_group, "--disable-scope-strict-match")
    if not (sub_level.ok and rg_level.ok):
        return CheckResult("PF-POLICIES", "C00", title, Status.BLOCKED, OBS, "Policy assignments readable",
                           _err(sub_level if not sub_level.ok else rg_level))
    assignments = list({a["id"]: a for a in (sub_level.json() or []) + (rg_level.json() or [])}.values())
    restricting = []
    for a in assignments:
        params = a.get("parameters") or {}
        allowed = None
        for key in ("listOfAllowedLocations", "allowedLocations"):
            if key in params:
                allowed = [x.lower() for x in params[key].get("value", [])]
        if allowed is None and any(d in (a.get("policyDefinitionId") or "") for d in _ALLOWED_LOCATION_DEFS):
            allowed = []
        if allowed is not None:
            restricting.append({"assignment": a.get("displayName") or a.get("name"),
                                "enforcement": a.get("enforcementMode"),
                                "allows_lab_region": cfg.lab_region.lower() in allowed})
    blocked = [r for r in restricting if not r["allows_lab_region"] and r["enforcement"] != "DoNotEnforce"]
    return CheckResult("PF-POLICIES", "C00", title, Status.FAIL if blocked else Status.PASS, OBS,
                       f"No enforced allowed-locations policy excludes {cfg.lab_region}",
                       f"{len(assignments)} assignment(s) apply (subscription + RG); location restrictions: {len(restricting)}; "
                       f"blocking: {len(blocked)}",
                       region=cfg.lab_region,
                       details={"assignments": [a.get("displayName") or a.get("name") for a in assignments],
                                "location_restrictions": restricting,
                                "limit": "Only location restrictions are evaluated; deny effects on network settings "
                                         "surface at deployment time."})


def check_roles(cfg: KitConfig, az: Az) -> CheckResult:
    title = "Caller has provisioning and Foundry data-plane roles"
    me = az.json("ad", "signed-in-user", "show", "--query", "{id:id}", scoped=False)
    if not me.ok:
        return CheckResult("PF-ROLES", "C00", title, Status.BLOCKED, OBS, "Signed-in user object id readable",
                           _err(me), details={"hint": "Service principals: set the assignee manually."})
    oid = me.json()["id"]
    res = az.json("role", "assignment", "list", "--assignee", oid, "--all", "--include-inherited",
                  "--query", "[].{role:roleDefinitionName,scope:scope}")
    if not res.ok:
        return CheckResult("PF-ROLES", "C00", title, Status.BLOCKED, OBS, "Role assignments readable", _err(res))
    rg_scope = cfg.rg_scope.lower()
    effective = sorted({a["role"] for a in res.json()
                        if rg_scope.startswith(a["scope"].lower().rstrip("/")) or a["scope"] == "/"
                        or "/providers/microsoft.management/managementgroups/" in a["scope"].lower()})
    has_prov = bool(_PROVISION_ROLES & set(effective))
    has_rbac = bool(_RBAC_ADMIN_ROLES & set(effective))
    has_data = bool(_DATA_PLANE_ROLES & set(effective))
    missing = [n for n, ok in (("provisioning (Owner/Contributor)", has_prov),
                               ("role assignment (Owner/UAA/RBAC Admin)", has_rbac),
                               ("Foundry data plane (Foundry User or higher)", has_data)) if not ok]
    return CheckResult("PF-ROLES", "C00", title, Status.PASS if not missing else Status.FAIL, OBS,
                       "Provisioning, role-assignment and Foundry data-plane roles on the RG scope",
                       f"effective roles on RG scope: {', '.join(effective) or 'none'}"
                       + (f"; missing: {'; '.join(missing)}" if missing else ""),
                       details={"effective_roles": effective,
                                "note": "Role names were renamed (Azure AI User -> Foundry User); both are accepted."})


def check_preview_features(cfg: KitConfig, az: Az) -> CheckResult:
    title = "No hosted-agent preview feature registration in the subscription"
    res = az.json("feature", "list", "--namespace", "Microsoft.CognitiveServices",
                  "--query", "[].{n:name,s:properties.state}", timeout=180)
    if not res.ok:
        return CheckResult("PF-PREVIEW-FEATURES", "C01", title, Status.BLOCKED, OBS, "Feature list readable", _err(res))
    related = [f for f in res.json() if re.search(r"agent|hosted", f["n"], re.I)]
    registered = [f for f in related if f["s"] == "Registered"]
    if registered:
        return CheckResult("PF-PREVIEW-FEATURES", "C01", title, Status.NOT_TESTED, OBS,
                           "No agent-related preview feature registered",
                           f"{len(registered)} agent-related feature(s) registered: GA-without-enrollment cannot be "
                           "isolated in this subscription", details={"features": related})
    return CheckResult("PF-PREVIEW-FEATURES", "C01", title, Status.PASS, OBS,
                       "No agent-related preview feature registered",
                       f"{len(related)} agent-related feature flag(s) exist, none registered",
                       details={"features": related,
                                "limit": "Shows the subscription is not enrolled; GA itself is demonstrated only by "
                                         "S01-GA-CREATE succeeding under these conditions."})


def check_model_available(cfg: KitConfig, az: Az) -> CheckResult:
    m = cfg.model
    title = f"Model {m.name} {m.version} ({m.sku}) offered in {cfg.lab_region}"
    res = az.json("cognitiveservices", "model", "list", "-l", cfg.lab_region, timeout=180)
    if not res.ok:
        return CheckResult("PF-MODEL-AVAILABLE", "C05.a", title, Status.BLOCKED, OBS, "Model catalog readable",
                           _err(res), region=cfg.lab_region)
    found = [x["model"] for x in res.json()
             if x.get("model", {}).get("name") == m.name and x["model"].get("version") == m.version]
    skus = sorted({s["name"] for f in found for s in f.get("skus", [])})
    lifecycle = sorted({f.get("lifecycleStatus") for f in found if f.get("lifecycleStatus")})
    ok = m.sku in skus
    return CheckResult("PF-MODEL-AVAILABLE", "C05.a", title, Status.PASS if ok else Status.FAIL, OBS,
                       f"{m.sku} SKU listed", f"skus={skus or 'none'}, lifecycle={lifecycle or 'n/a'}",
                       region=cfg.lab_region, details={"skus": skus, "lifecycle": lifecycle,
                                                       "limit": "Catalog listing is not deployment capacity."})


def check_model_quota(cfg: KitConfig, az: Az) -> CheckResult:
    m = cfg.model
    name = f"OpenAI.{m.sku}.{m.name}"
    title = f"Quota for {m.capacity}K TPM of {m.name} ({m.sku}) in {cfg.lab_region}"
    res = az.json("cognitiveservices", "usage", "list", "-l", cfg.lab_region)
    if not res.ok:
        return CheckResult("PF-MODEL-QUOTA", "C05.a", title, Status.BLOCKED, OBS, "Usage readable", _err(res),
                           region=cfg.lab_region)
    row = next((u for u in res.json() if u["name"]["value"] == name), None)
    if row is None:
        return CheckResult("PF-MODEL-QUOTA", "C05.a", title, Status.FAIL, OBS, f"quota entry {name} exists",
                           "no quota entry", region=cfg.lab_region)
    free = row["limit"] - row["currentValue"]
    return CheckResult("PF-MODEL-QUOTA", "C05.a", title, Status.PASS if free >= m.capacity else Status.FAIL, OBS,
                       f"free >= {m.capacity}", f"used {row['currentValue']:g} / limit {row['limit']:g} (free {free:g})",
                       region=cfg.lab_region, details={"quota": name, "used": row["currentValue"], "limit": row["limit"],
                                                       "limit_note": "Quota is not guaranteed regional capacity."})


def check_arm_locations(cfg: KitConfig, az: Az) -> list[CheckResult]:
    """ARM accepts Foundry accounts/projects in a region. Says nothing about hosted agents."""
    # list-locations does not accept --subscription; region names are identical across subscriptions.
    locs = az.json("account", "list-locations", "--query", "[].{n:name,d:displayName}", scoped=False)
    prov = az.json("provider", "show", "-n", "Microsoft.CognitiveServices",
                   "--query", "resourceTypes[?resourceType=='accounts/projects'].locations | [0]")
    if not (locs.ok and prov.ok):
        return [CheckResult("PF-ARM-LOCATIONS", "C05.a", "ARM resource type availability", Status.BLOCKED, OBS,
                            "Provider metadata readable", _err(locs if not locs.ok else prov))]
    display = {l["n"]: l["d"] for l in locs.json()}
    available = {x.lower() for x in (prov.json() or [])}
    out = []
    for region, check_id, criterion in ((cfg.lab_region, "PF-ARM-LOCATIONS", "C05.a"),
                                        (cfg.required_region, "PF-ARM-LOCATIONS-REQUIRED", "C05.b")):
        present = display.get(region, region).lower() in available
        is_lab = region == cfg.lab_region
        status = (Status.PASS if present else Status.FAIL) if is_lab else Status.NOT_TESTED
        out.append(CheckResult(
            check_id, criterion, f"ARM offers Foundry projects in {region}", status, OBS,
            "accounts/projects resource type offered in the region",
            f"offered={present}" + ("" if is_lab else
                                    "; hosted-agent support is not observable without deploying there (out of scope)"),
            region=region,
            details={"limit": "ARM resource-type availability does not demonstrate hosted-agent availability."}))
    return out


_APP_ROLES = {"Application Developer", "Application Administrator", "Cloud Application Administrator",
              "Global Administrator"}


def check_entra_app_creation(cfg: KitConfig, az: Az) -> CheckResult:
    """Read-only: can the caller register the MCP audience application (needed by scenario S07)?"""
    title = "Caller may create an Entra app registration (MCP audience)"
    pol = az.json("rest", "--method", "GET", "--url", "https://graph.microsoft.com/v1.0/policies/authorizationPolicy",
                  scoped=False)
    roles = az.json("rest", "--method", "GET", "--url",
                    "https://graph.microsoft.com/v1.0/me/memberOf/microsoft.graph.directoryRole?$select=displayName",
                    scoped=False)
    if not (pol.ok or roles.ok):
        return CheckResult("PF-ENTRA-APP-CREATE", "C04.b", title, Status.BLOCKED, OBS, "policy or roles readable",
                           _err(pol))
    body = pol.json() if pol.ok else {}
    body = body.get("value", [body])[0] if isinstance(body, dict) and "value" in body else body
    allowed = bool((body or {}).get("defaultUserRolePermissions", {}).get("allowedToCreateApps"))
    held = sorted({r["displayName"] for r in (roles.json() or {}).get("value", [])} & _APP_ROLES) if roles.ok else []
    ok = allowed or bool(held)
    return CheckResult("PF-ENTRA-APP-CREATE", "C04.b", title, Status.PASS if ok else Status.FAIL, OBS,
                       "users may register apps, or caller holds an app-registration directory role",
                       f"tenant allows user app registration={allowed}; relevant directory roles={held or 'none'}",
                       details={"note": "Creating the app is a tenant-level change: requires explicit approval."})


def _first_line(res) -> str:
    return (res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr).strip() else ""


def collect_tool_versions(az: Az) -> dict[str, str]:
    tools: dict[str, str] = {}
    v = az.tool("az", "version", "-o", "json")
    if v.ok:
        tools["azure-cli"] = v.json().get("azure-cli", "?")
    d = az.tool("azd", "version")
    if d.ok:
        tools["azd"] = _first_line(d).replace("azd version ", "").split(" ")[0]
    b = az.tool("az", "bicep", "version")
    m = re.search(r"Bicep CLI version ([\d.]+)", (b.stdout or "") + (b.stderr or ""))
    if m:
        tools["bicep"] = m.group(1)
    return tools


def check_tooling(cfg: KitConfig, az: Az) -> CheckResult:
    tools = collect_tool_versions(az)
    ext = az.tool("azd", "extension", "list", "--output", "json")
    extensions = {}
    if ext.ok:
        for e in json.loads(ext.stdout or "[]"):
            if e.get("installedVersion"):
                extensions[e["id"]] = {"installed": e["installedVersion"], "latest": e.get("version")}
    docker = az.tool("docker", "info", "--format", "{{.ServerVersion}}")
    warnings = []
    try:
        from importlib.metadata import version as _v
        sdk = _v("azure-ai-projects")
        tools["azure-ai-projects"] = sdk
        if tuple(int(x) for x in sdk.split(".")[:2]) < (2, 5):
            warnings.append(f"azure-ai-projects {sdk} < 2.5 (A2A update_details needs 2.5): pip install -e .[lab]")
    except Exception:
        warnings.append("azure-ai-projects not installed: pip install -e .[lab] before running Phase A scenarios")
    if not docker.ok:
        warnings.append("Docker daemon not reachable (not required: source-code deployment builds remotely)")
    ok = "azure-cli" in tools and "bicep" in tools
    return CheckResult("PF-TOOLING", "C00", "Local tooling for the kit", Status.PASS if ok else Status.FAIL, OBS,
                       "az, bicep and the Phase A SDK available (azd and Docker are not used by the kit)",
                       ", ".join(f"{k} {v}" for k, v in tools.items()) + (f" | warnings: {'; '.join(warnings)}" if warnings else ""),
                       details={"tools": tools, "azd_extensions": extensions, "docker_daemon": docker.ok,
                                "warnings": warnings})


def run_all(cfg: KitConfig, az: Az) -> list[CheckResult]:
    results = [check_auth(cfg, az)]
    if results[0].status is not Status.PASS:
        return results  # nothing else is meaningful without an identity
    results += [check_subscription_target(cfg, az), check_resource_group(cfg, az), check_providers(cfg, az),
                check_policies(cfg, az), check_roles(cfg, az), check_preview_features(cfg, az),
                check_model_available(cfg, az), check_model_quota(cfg, az)]
    results += check_arm_locations(cfg, az)
    results.append(check_entra_app_creation(cfg, az))
    results.append(check_tooling(cfg, az))
    return results
