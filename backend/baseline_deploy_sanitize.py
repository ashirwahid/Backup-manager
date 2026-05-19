"""
Strip or resolve Simeon Cloud baseline placeholders before Microsoft Graph create/deploy.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}")

ROLE_DEF_URN_RE = re.compile(
    r"\$\{urn:resource:MSGraph:RoleManagement:Directory:RoleDefinitions/([^?}]+)\?id\}",
    re.IGNORECASE,
)
USER_URN_RE = re.compile(
    r"\$\{urn:resource:MSGraph:Users/([^@?}]+)@",
    re.IGNORECASE,
)
USER_URN_FULL_RE = re.compile(
    r"\$\{urn:resource:MSGraph:Users/([^?}]+)\?id\}",
    re.IGNORECASE,
)
GROUP_URN_RE = re.compile(
    r"\$\{urn:resource:MSGraph:Groups/([^?}]+)\?id\}",
    re.IGNORECASE,
)
SERVICE_PRINCIPAL_URN_RE = re.compile(
    r"\$\{urn:resource:MSGraph:ServicePrincipals/([^?}]+)\?id\}",
    re.IGNORECASE,
)
AUTH_STRENGTH_URN_RE = re.compile(
    r"\$\{urn:resource:MSGraph:Identity:ConditionalAccess:AuthenticationStrength:Policies/([^?}]+)\?id\}",
    re.IGNORECASE,
)

VALID_CA_BUILTIN_CONTROLS = frozenset({
    "block",
    "mfa",
    "compliantDevice",
    "domainJoinedDevice",
    "approvedApplication",
    "compliantApplication",
    "passwordChange",
    "riskRemediation",
    "unknownFutureValue",
})

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

CA_USER_RESERVED = frozenset({
    "All",
    "None",
    "GuestsOrExternalUsers",
    "ExternalUsers",
})

METADATA_KEYS = frozenset({
    "_cisBaselineSourcePath",
    "_devOpsBackupPath",
    "_backupKind",
})

CA_CREATE_STRIP_KEYS = frozenset({
    "templateId",
    "deletedDateTime",
    "modifiedDateTime",
    "partialEnablementStrategy",
})

SKIP_KEYS = frozenset({
    "id",
    "@odata.type",
    "@odata.context",
    "createdDateTime",
    "lastModifiedDateTime",
    "version",
    "createdBy",
    "lastModifiedBy",
    "settingCount",
    "isAssigned",
    "priorityMetaData",
})


def is_baseline_placeholder(value: Any) -> bool:
    return isinstance(value, str) and "${" in value


def is_graph_guid(value: Any) -> bool:
    return isinstance(value, str) and bool(GUID_RE.match(value.strip()))


def is_probable_upn(value: Any) -> bool:
    return (
        isinstance(value, str)
        and "@" in value
        and "${" not in value
        and not is_graph_guid(value)
    )


def extract_role_display_name(placeholder: str) -> Optional[str]:
    m = ROLE_DEF_URN_RE.search(placeholder)
    return m.group(1).strip() if m else None


def extract_user_local_part(placeholder: str) -> Optional[str]:
    m = USER_URN_RE.search(placeholder)
    return m.group(1).strip() if m else None


def extract_user_upn_from_placeholder(placeholder: str) -> Optional[str]:
    """Full UPN from Simeon user URN after variables.json expansion."""
    m = USER_URN_FULL_RE.search(placeholder)
    if not m:
        return None
    upn = m.group(1).strip()
    if "@" not in upn or "${" in upn:
        return None
    return upn


def extract_group_display_name(placeholder: str) -> Optional[str]:
    m = GROUP_URN_RE.search(placeholder)
    return m.group(1).strip() if m else None


def extract_auth_strength_display_name(placeholder: str) -> Optional[str]:
    m = AUTH_STRENGTH_URN_RE.search(placeholder)
    return m.group(1).strip() if m else None


def _filter_string_list(
    items: Any,
    *,
    keep: Callable[[str], bool],
) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for raw in items:
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if not val or val in seen:
            continue
        if keep(val):
            seen.add(val)
            out.append(val)
        elif is_baseline_placeholder(val):
            logger.info("Removed unresolved baseline placeholder from deploy payload: %s", val[:120])
    return out


def _map_ca_user_list(
    items: Any, resolved_users: Dict[str, str]
) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in items or []:
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if not val:
            continue
        mapped: Optional[str] = None
        if val in CA_USER_RESERVED:
            mapped = val
        elif is_graph_guid(val):
            mapped = val
        elif is_baseline_placeholder(val):
            upn = extract_user_upn_from_placeholder(val)
            if upn and upn in resolved_users:
                mapped = resolved_users[upn]
            else:
                local = extract_user_local_part(val)
                if local and local in resolved_users:
                    mapped = resolved_users[local]
                else:
                    logger.info(
                        "Removed unresolved user placeholder from deploy: %s",
                        val[:120],
                    )
        elif is_probable_upn(val):
            if val in resolved_users:
                mapped = resolved_users[val]
            else:
                logger.info(
                    "Removed unresolved user UPN from deploy: %s", val[:120]
                )
        else:
            mapped = val
        if mapped and mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return out


def _map_ca_group_list(
    items: Any, resolved_groups: Dict[str, str]
) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in items or []:
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if not val:
            continue
        mapped: Optional[str] = None
        if is_graph_guid(val):
            mapped = val
        elif is_baseline_placeholder(val):
            name = extract_group_display_name(val)
            if name and name in resolved_groups:
                mapped = resolved_groups[name]
            else:
                logger.info(
                    "Removed unresolved group placeholder from deploy: %s", val[:120]
                )
        else:
            mapped = val
        if mapped and mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return out


def _map_ca_role_list(
    items: Any, resolved_roles: Dict[str, str]
) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in items or []:
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if not val:
            continue
        mapped: Optional[str] = None
        if is_graph_guid(val):
            mapped = val
        elif is_baseline_placeholder(val):
            name = extract_role_display_name(val)
            if name and name in resolved_roles:
                mapped = resolved_roles[name]
            else:
                logger.info(
                    "Removed unresolved role placeholder from deploy: %s", val[:120]
                )
        else:
            mapped = val
        if mapped and mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return out


def sanitize_ca_users_block(
    users: Dict[str, Any],
    *,
    resolved_users: Dict[str, str],
    resolved_groups: Dict[str, str],
    resolved_roles: Dict[str, str],
) -> Dict[str, Any]:
    if not isinstance(users, dict):
        return {}

    out: Dict[str, Any] = {}
    for key, val in users.items():
        if key in ("includeUsers", "excludeUsers"):
            mapped = _map_ca_user_list(val, resolved_users)
            if mapped:
                out[key] = mapped
        elif key in ("includeGroups", "excludeGroups"):
            mapped = _map_ca_group_list(val, resolved_groups)
            if mapped:
                out[key] = mapped
        elif key in ("includeRoles", "excludeRoles"):
            mapped = _map_ca_role_list(val, resolved_roles)
            if mapped:
                out[key] = mapped
        elif isinstance(val, str) and is_baseline_placeholder(val):
            continue
        elif val not in (None, "", [], {}):
            out[key] = val
    return out


def sanitize_ca_applications_block(apps: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(apps, dict):
        return {}
    out: Dict[str, Any] = {}
    list_keys = (
        "includeApplications",
        "excludeApplications",
        "includeAuthenticationContextClassReferences",
        "includeUserActions",
    )
    for key, val in apps.items():
        if key in list_keys and isinstance(val, list):
            cleaned = _filter_string_list(
                val, keep=lambda s: not is_baseline_placeholder(s)
            )
            if cleaned:
                out[key] = cleaned
        elif key == "includeServicePrincipals" and isinstance(val, list):
            cleaned = []
            for entry in val:
                if isinstance(entry, dict):
                    e = {
                        k: v
                        for k, v in entry.items()
                        if not (isinstance(v, str) and is_baseline_placeholder(v))
                    }
                    if e:
                        cleaned.append(e)
                elif isinstance(entry, str) and not is_baseline_placeholder(entry):
                    cleaned.append(entry)
            if cleaned:
                out[key] = cleaned
        elif isinstance(val, str) and is_baseline_placeholder(val):
            continue
        else:
            out[key] = val
    return out


def collect_ca_placeholder_refs(policy: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Gather role display names, user local parts, and UPNs from Simeon placeholders."""
    roles: Set[str] = set()
    users: Set[str] = set()
    user_upns: Set[str] = set()
    groups: Set[str] = set()

    def scan_list(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, str):
                continue
            if is_probable_upn(item):
                user_upns.add(item.strip())
                continue
            if not is_baseline_placeholder(item):
                continue
            rn = extract_role_display_name(item)
            if rn:
                roles.add(rn)
            upn = extract_user_upn_from_placeholder(item)
            if upn:
                user_upns.add(upn)
            else:
                ul = extract_user_local_part(item)
                if ul:
                    users.add(ul)
            gn = extract_group_display_name(item)
            if gn:
                groups.add(gn)

    block = (policy.get("conditions") or {}).get("users") or {}
    if isinstance(block, dict):
        for key in (
            "includeUsers",
            "excludeUsers",
            "includeGroups",
            "excludeGroups",
            "includeRoles",
            "excludeRoles",
        ):
            scan_list(block.get(key))

    auth_strengths: Set[str] = set()
    grant = policy.get("grantControls")
    if isinstance(grant, dict):
        auth = grant.get("authenticationStrength")
        if isinstance(auth, dict):
            aid = auth.get("id")
            if isinstance(aid, str):
                if is_baseline_placeholder(aid):
                    name = extract_auth_strength_display_name(aid)
                    if name:
                        auth_strengths.add(name)
                elif not is_graph_guid(aid):
                    auth_strengths.add(aid.strip())

    return {
        "roles": roles,
        "users": users,
        "user_upns": user_upns,
        "groups": groups,
        "auth_strengths": auth_strengths,
    }


def normalize_built_in_controls(controls: Any) -> List[str]:
    if not isinstance(controls, list):
        return []
    cleaned: List[str] = []
    for raw in controls:
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if val in VALID_CA_BUILTIN_CONTROLS and val not in cleaned:
            cleaned.append(val)
    if "block" in cleaned:
        return ["block"]
    return cleaned


def sanitize_ca_grant_controls_block(
    grant: Dict[str, Any],
    *,
    resolved_auth_strengths: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(grant, dict):
        return None

    resolved = resolved_auth_strengths or {}
    built_in = normalize_built_in_controls(grant.get("builtInControls"))

    auth_strength_out: Optional[Dict[str, str]] = None
    auth = grant.get("authenticationStrength")
    if isinstance(auth, dict):
        aid = auth.get("id")
        if isinstance(aid, str):
            val = aid.strip()
            if is_graph_guid(val):
                auth_strength_out = {"id": val}
            elif is_baseline_placeholder(val):
                name = extract_auth_strength_display_name(val)
                if name and name in resolved:
                    auth_strength_out = {"id": resolved[name]}
                else:
                    logger.info(
                        "Removed unresolved authenticationStrength placeholder: %s",
                        val[:120],
                    )
            elif val in resolved:
                auth_strength_out = {"id": resolved[val]}

    if not built_in and not auth_strength_out:
        return None

    out: Dict[str, Any] = {}
    if built_in:
        out["builtInControls"] = built_in
    if auth_strength_out:
        out["authenticationStrength"] = auth_strength_out

    operator = grant.get("operator")
    if operator in ("AND", "OR"):
        out["operator"] = operator
    else:
        out["operator"] = "OR"

    if built_in == ["block"]:
        out["operator"] = "OR"
        out.pop("authenticationStrength", None)

    return out


def deep_scrub_unresolved_placeholders(obj: Any) -> Any:
    """Remove list entries and drop string fields that still contain ${...}."""
    if isinstance(obj, str):
        return None if is_baseline_placeholder(obj) else obj
    if isinstance(obj, list):
        out: List[Any] = []
        for item in obj:
            if isinstance(item, str) and is_baseline_placeholder(item):
                logger.info(
                    "Removed unresolved baseline placeholder from list: %s",
                    item[:120],
                )
                continue
            scrubbed = deep_scrub_unresolved_placeholders(item)
            if scrubbed is not None:
                out.append(scrubbed)
        return out
    if isinstance(obj, dict):
        out_dict: Dict[str, Any] = {}
        for key, val in obj.items():
            if isinstance(val, str) and is_baseline_placeholder(val):
                logger.info("Dropped unresolved placeholder field %s", key)
                continue
            scrubbed = deep_scrub_unresolved_placeholders(val)
            if scrubbed is not None and scrubbed != [] and scrubbed != {}:
                out_dict[key] = scrubbed
        return out_dict
    return obj


def sanitize_conditional_access_policy(
    policy: Dict[str, Any],
    *,
    resolved_users: Optional[Dict[str, str]] = None,
    resolved_groups: Optional[Dict[str, str]] = None,
    resolved_roles: Optional[Dict[str, str]] = None,
    resolved_auth_strengths: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Remove Simeon placeholders; optional maps: display/local name -> Graph object id."""
    out = copy.deepcopy(policy)
    if not out.get("displayName"):
        friendly = policy.get("$friendlyName") or out.get("$friendlyName")
        if isinstance(friendly, str) and friendly.strip():
            out["displayName"] = friendly.strip()
    for meta in list(out.keys()):
        if meta.startswith("$") or meta in METADATA_KEYS:
            out.pop(meta, None)

    conditions = out.get("conditions")
    if isinstance(conditions, dict):
        users = conditions.get("users")
        if isinstance(users, dict):
            conditions["users"] = sanitize_ca_users_block(
                users,
                resolved_users=resolved_users or {},
                resolved_groups=resolved_groups or {},
                resolved_roles=resolved_roles or {},
            )
        apps = conditions.get("applications")
        if isinstance(apps, dict):
            conditions["applications"] = sanitize_ca_applications_block(apps)
        out["conditions"] = conditions

    grant = out.get("grantControls")
    if isinstance(grant, dict):
        normalized_grant = sanitize_ca_grant_controls_block(
            grant, resolved_auth_strengths=resolved_auth_strengths
        )
        if normalized_grant:
            out["grantControls"] = normalized_grant
        else:
            out.pop("grantControls", None)

    if isinstance(out.get("roleScopeTagIds"), list):
        tags = _filter_string_list(
            out["roleScopeTagIds"], keep=lambda s: not is_baseline_placeholder(s)
        )
        if tags:
            out["roleScopeTagIds"] = tags
        else:
            out.pop("roleScopeTagIds", None)

    scrubbed = deep_scrub_unresolved_placeholders(out)
    base = scrubbed if isinstance(scrubbed, dict) else out
    return finalize_conditional_access_for_graph_create(base)


def prune_empty_values(obj: Any) -> Any:
    """Recursively remove None, empty lists, and empty dicts (Graph rejects many empty arrays)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for key, val in obj.items():
            pruned = prune_empty_values(val)
            if pruned is None or pruned == [] or pruned == {}:
                continue
            out[key] = pruned
        return out if out else None
    if isinstance(obj, list):
        items: List[Any] = []
        for item in obj:
            pruned = prune_empty_values(item)
            if pruned is None or pruned == [] or pruned == {}:
                continue
            items.append(pruned)
        return items if items else None
    return obj


def finalize_conditional_access_for_graph_create(policy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Shape a Simeon baseline CA export into a valid Microsoft Graph create body.
    Graph error 1007 is commonly caused by empty arrays/objects left in baseline JSON.
    """
    body: Dict[str, Any] = {}
    for key, val in policy.items():
        if key.startswith("$") or key in METADATA_KEYS or key in CA_CREATE_STRIP_KEYS:
            continue
        if key in SKIP_KEYS:
            continue
        if val is not None:
            body[key] = val

    pruned = prune_empty_values(body)
    if not isinstance(pruned, dict):
        pruned = {}

    display = pruned.get("displayName")
    if isinstance(display, str) and display.strip():
        pruned["displayName"] = display.strip()

    state = pruned.get("state")
    if not state:
        pruned["state"] = "disabled"

    conditions = pruned.get("conditions")
    if not isinstance(conditions, dict):
        conditions = {}
    users = conditions.get("users")
    if not isinstance(users, dict):
        users = {}
    if not any(
        users.get(k)
        for k in (
            "includeUsers",
            "excludeUsers",
            "includeGroups",
            "excludeGroups",
            "includeRoles",
            "excludeRoles",
        )
    ):
        users["includeUsers"] = ["All"]
    conditions["users"] = users

    apps = conditions.get("applications")
    if not isinstance(apps, dict):
        apps = {}
    if not apps.get("includeApplications") and not apps.get("excludeApplications"):
        apps["includeApplications"] = ["All"]
    conditions["applications"] = apps

    if not conditions.get("clientAppTypes"):
        conditions["clientAppTypes"] = ["all"]
    pruned["conditions"] = conditions

    grant = pruned.get("grantControls")
    if isinstance(grant, dict):
        normalized_grant = sanitize_ca_grant_controls_block(grant)
        if normalized_grant:
            pruned["grantControls"] = normalized_grant
        else:
            pruned.pop("grantControls", None)

    session = pruned.get("sessionControls")
    if isinstance(session, dict):
        session_pruned = prune_empty_values(session)
        if isinstance(session_pruned, dict) and session_pruned:
            pruned["sessionControls"] = session_pruned
        else:
            pruned.pop("sessionControls", None)

    return pruned


def _sanitize_generic_value(value: Any) -> Any:
    if isinstance(value, str):
        if is_baseline_placeholder(value):
            return None
        return value
    if isinstance(value, list):
        cleaned = []
        for item in value:
            if isinstance(item, str):
                if is_baseline_placeholder(item):
                    logger.info(
                        "Removed baseline placeholder from list: %s", item[:120]
                    )
                    continue
                cleaned.append(item)
            else:
                sub = _sanitize_generic_value(item)
                if sub is not None:
                    cleaned.append(sub)
        return cleaned
    if isinstance(value, dict):
        return _sanitize_generic_dict(value)
    return value


def _sanitize_generic_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in data.items():
        if key.startswith("$") or key in METADATA_KEYS:
            continue
        if key in SKIP_KEYS:
            continue
        if isinstance(val, str) and is_baseline_placeholder(val):
            logger.info("Dropped baseline placeholder field %s", key)
            continue
        if isinstance(val, (dict, list)):
            sanitized = _sanitize_generic_value(val)
            if sanitized is not None and sanitized != [] and sanitized != {}:
                out[key] = sanitized
        elif val is not None:
            out[key] = val
    return out


def sanitize_policy_for_graph_deploy(
    policy: Dict[str, Any],
    policy_type: str,
    *,
    resolved_users: Optional[Dict[str, str]] = None,
    resolved_groups: Optional[Dict[str, str]] = None,
    resolved_roles: Optional[Dict[str, str]] = None,
    resolved_auth_strengths: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if policy_type == "conditional_access":
        return sanitize_conditional_access_policy(
            policy,
            resolved_users=resolved_users,
            resolved_groups=resolved_groups,
            resolved_roles=resolved_roles,
            resolved_auth_strengths=resolved_auth_strengths,
        )
    return _sanitize_generic_dict(copy.deepcopy(policy))
