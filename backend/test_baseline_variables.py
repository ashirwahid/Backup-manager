import json
from pathlib import Path

from baseline_deploy_sanitize import (
    _map_ca_user_list,
    collect_ca_placeholder_refs,
    extract_user_upn_from_placeholder,
    finalize_conditional_access_for_graph_create,
    sanitize_conditional_access_policy,
)
from baseline_variables import expand_string_variables, tenant_domain_from_variables


def test_nested_admin_placeholder():
    variables = {"ResourceContext:TenantDomainName": "simeontesttenant13.onmicrosoft.com"}
    raw = "${urn:resource:MSGraph:Users/admin@${ResourceContext:TenantDomainName}?id}"
    expanded = expand_string_variables(raw, variables)
    assert expanded == "${urn:resource:MSGraph:Users/admin@simeontesttenant13.onmicrosoft.com?id}"
    upn = extract_user_upn_from_placeholder(expanded)
    assert upn == "admin@simeontesttenant13.onmicrosoft.com"
    refs = collect_ca_placeholder_refs(
        {"conditions": {"users": {"includeUsers": [expanded]}}}
    )
    assert upn in refs["user_upns"]


def test_map_exclude_admin_with_resolved_guid():
    admin_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    variables = {"ResourceContext:TenantDomainName": "contoso.onmicrosoft.com"}
    domain = tenant_domain_from_variables(variables)
    resolved = {f"admin@{domain}": admin_guid, "admin": admin_guid}
    raw = "${urn:resource:MSGraph:Users/admin@${ResourceContext:TenantDomainName}?id}"
    expanded = expand_string_variables(raw, variables)
    mapped = _map_ca_user_list([expanded], resolved)
    assert mapped == [admin_guid]


def test_finalize_compliant_device_policy_schema():
    sample_path = (
        Path(__file__).resolve().parents[1]
        / "Source/Resources/Content/MSGraph/Identity/ConditionalAccess/Policies"
        / "Baseline - Requrie Compliant Device.json"
    )
    raw = json.loads(sample_path.read_text(encoding="utf-8"))
    sanitized = sanitize_conditional_access_policy(raw)
    body = finalize_conditional_access_for_graph_create(sanitized)

    assert body.get("displayName") == "Baseline - Requrie Compliant Device"
    assert body["conditions"]["users"]["includeUsers"] == ["All"]
    assert "excludeApplications" not in body["conditions"]["applications"]
    assert body["conditions"]["applications"]["includeApplications"] == ["All"]
    assert "signInRiskLevels" not in body["conditions"]
    assert body["grantControls"]["builtInControls"] == [
        "compliantDevice",
        "domainJoinedDevice",
    ]
    assert "termsOfUse" not in body["grantControls"]


def test_auth_strength_grant_controls_resolved():
    raw = {
        "$friendlyName": "ACSC Essential Eight MFA - Maturity Level 1",
        "conditions": {
            "applications": {"includeApplications": ["All"]},
            "clientAppTypes": ["all"],
            "users": {"includeUsers": ["All"]},
        },
        "grantControls": {
            "authenticationStrength": {
                "id": "${urn:resource:MSGraph:Identity:ConditionalAccess:AuthenticationStrength:Policies/Essential 8 Maturity level 1?id}"
            },
            "builtInControls": [],
            "operator": "OR",
        },
        "state": "disabled",
    }
    strength_id = "11111111-2222-3333-4444-555555555555"
    sanitized = sanitize_conditional_access_policy(
        raw,
        resolved_auth_strengths={"Essential 8 Maturity level 1": strength_id},
    )
    grant = sanitized["grantControls"]
    assert grant["authenticationStrength"]["id"] == strength_id
    assert "builtInControls" not in grant
    assert grant["operator"] == "OR"
