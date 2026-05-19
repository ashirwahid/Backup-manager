"""
Simeon-style resource paths under Source/Resources/Content and portal breadcrumbs.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

# Tenant backup + CIS baseline alignment (simeoncloud/Baseline)
DEVOPS_RESOURCE_CONTENT_ROOT = "/Source/Resources/Content"
DEVOPS_LEGACY_BACKUP_PREFIX = "/policies/backup"

DEVOPS_DEPENDENCIES_USERS_SUFFIX = "dependencies/users"
DEVOPS_DEPENDENCIES_GROUPS_SUFFIX = "dependencies/groups"

POLICY_TYPE_KEYS = (
    "conditional_access",
    "device_configuration",
    "configuration",
    "compliance",
)

POLICY_RESOURCE_LAYOUT: Dict[str, Dict[str, Any]] = {
    "device_configuration": {
        "devops_scope": "MSGraph/DeviceManagement/DeviceConfigurations",
        "legacy_read_scopes": [],
        "ui_breadcrumb": ["Intune", "Devices", "Configuration Profiles"],
        "filter_label": "Intune · Devices · Configuration Profiles",
    },
    "conditional_access": {
        "devops_scope": "MSGraph/Identity/ConditionalAccess/Policies",
        "legacy_read_scopes": [],
        "ui_breadcrumb": ["Entra ID", "Security", "Conditional Access", "Policies"],
        "filter_label": "Entra ID · Security · Conditional Access",
    },
    "configuration": {
        "devops_scope": "MSGraph/DeviceAppManagement/MobileApps",
        "legacy_read_scopes": [
            "MSGraph/DeviceManagement/ConfigurationPolicies",
        ],
        "ui_breadcrumb": ["Intune", "Apps"],
        "filter_label": "Intune · Apps (Settings catalog)",
    },
    "compliance": {
        "devops_scope": "MSGraph/DeviceManagement/DeviceCompliancePolicies",
        "legacy_read_scopes": [],
        "ui_breadcrumb": ["Intune", "Devices", "Compliance", "Policies"],
        "filter_label": "Intune · Devices · Compliance",
    },
}

ONEDRIVE_BACKUP_FOLDER = "onedrive"
ONEDRIVE_DEVOPS_SCOPE = "MSGraph/Users/OneDriveSnapshots"


def onedrive_read_scopes() -> List[str]:
    scopes: List[str] = []
    seen: set[str] = set()

    def add(scope: str) -> None:
        norm = normalize_repo_path(scope)
        key = norm.lower()
        if key not in seen:
            seen.add(key)
            scopes.append(norm)

    add(f"{DEVOPS_RESOURCE_CONTENT_ROOT}/{ONEDRIVE_DEVOPS_SCOPE}")
    if os.environ.get("DEVOPS_READ_LEGACY_BACKUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        add(f"{DEVOPS_LEGACY_BACKUP_PREFIX}/{ONEDRIVE_BACKUP_FOLDER}")
    return scopes


def normalize_repo_path(p: str) -> str:
    return "/" + p.strip().lstrip("/").replace("\\", "/")


def policy_content_scope(policy_type: str) -> str:
    layout = POLICY_RESOURCE_LAYOUT[policy_type]
    return normalize_repo_path(
        f"{DEVOPS_RESOURCE_CONTENT_ROOT}/{layout['devops_scope']}"
    )


def policy_read_scopes(policy_type: str) -> List[str]:
    """All DevOps folder prefixes to scan when loading policies (new + legacy)."""
    scopes: List[str] = []
    seen: set[str] = set()

    def add(scope: str) -> None:
        norm = normalize_repo_path(scope)
        key = norm.lower()
        if key not in seen:
            seen.add(key)
            scopes.append(norm)

    add(policy_content_scope(policy_type))
    layout = POLICY_RESOURCE_LAYOUT[policy_type]
    for rel in layout.get("legacy_read_scopes") or []:
        add(f"{DEVOPS_RESOURCE_CONTENT_ROOT}/{rel}")
    # Old layout: /policies/backup/<type>/ — optional; 404s are normal if you only use
    # Source/Resources/Content. Set DEVOPS_READ_LEGACY_BACKUP=1 to scan legacy paths too.
    if os.environ.get("DEVOPS_READ_LEGACY_BACKUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        add(f"{DEVOPS_LEGACY_BACKUP_PREFIX}/{policy_type}")
    return scopes


def ui_breadcrumb_label(policy_type: str) -> str:
    parts = POLICY_RESOURCE_LAYOUT.get(policy_type, {}).get("ui_breadcrumb") or [
        policy_type
    ]
    return " > ".join(parts)


def policy_filter_label(policy_type: str) -> str:
    return POLICY_RESOURCE_LAYOUT.get(policy_type, {}).get("filter_label") or policy_type


def infer_policy_type_from_resource_path(path: Optional[str]) -> Optional[str]:
    if not path or not str(path).strip():
        return None
    p = normalize_repo_path(str(path)).lower()

    for ptype, layout in POLICY_RESOURCE_LAYOUT.items():
        needle = layout["devops_scope"].lower()
        if f"/{needle}/" in p or p.endswith(f"/{needle}"):
            return ptype
        for leg in layout.get("legacy_read_scopes") or []:
            leg_l = leg.lower()
            if f"/{leg_l}/" in p or p.endswith(f"/{leg_l}"):
                return ptype

    legacy_needle = DEVOPS_LEGACY_BACKUP_PREFIX.lower()
    if legacy_needle in p:
        rest = p.split(legacy_needle, 1)[-1].strip("/")
        seg = rest.split("/", 1)[0] if rest else ""
        if seg in POLICY_RESOURCE_LAYOUT:
            return seg

    if "/devicecompliancepolicies" in p:
        return "compliance"
    if "/deviceconfigurations" in p:
        return "device_configuration"
    if "/conditionalaccess/" in p and "/policies" in p:
        return "conditional_access"
    if "/configurationpolicies" in p or "/mobileapps" in p:
        return "configuration"
    return None


def devops_backup_commit_root() -> str:
    return DEVOPS_RESOURCE_CONTENT_ROOT


def dependencies_users_prefix() -> str:
    return normalize_repo_path(
        f"{DEVOPS_RESOURCE_CONTENT_ROOT}/{DEVOPS_DEPENDENCIES_USERS_SUFFIX}"
    )


def dependencies_groups_prefix() -> str:
    return normalize_repo_path(
        f"{DEVOPS_RESOURCE_CONTENT_ROOT}/{DEVOPS_DEPENDENCIES_GROUPS_SUFFIX}"
    )


def display_name_from_baseline_filename(path: str) -> Optional[str]:
    """Derive a display label from Simeon/GitHub backup filenames."""
    if not path:
        return None
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    if base.lower().endswith(".json"):
        base = base[: -len(".json")]
    if "--" in base:
        return base.split("--", 1)[1].strip() or None
    sep = base.find("__")
    if sep > 0:
        return base[sep + 2 :].strip() or None
    return base.strip() or None


def resolve_policy_display_name(
    policy: Dict[str, Any], *, source_path: Optional[str] = None
) -> Optional[str]:
    for key in ("displayName", "name", "$friendlyName"):
        val = policy.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    path = (
        source_path
        or policy.get("_cisBaselineSourcePath")
        or policy.get("_devOpsBackupPath")
        or ""
    )
    from_name = display_name_from_baseline_filename(path)
    return from_name.strip() if from_name else None


def normalize_policy_compare_name(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def policy_compare_key(
    policy: Dict[str, Any], *, match_by_id: bool = False
) -> str:
    """Stable key for tenant↔baseline comparison (name-first for CIS; id-first for snapshots)."""
    if policy.get("_backupKind") == "assignments":
        pid = policy.get("id")
        suffix = str(pid) if pid else "unknown"
        return f"assignments:{suffix}".lower()

    pid = policy.get("id")
    if isinstance(pid, str) and pid.strip():
        pid_key = pid.strip().lower()
        if match_by_id:
            return f"id:{pid_key}"

    label = resolve_policy_display_name(policy)
    if label:
        return f"name:{normalize_policy_compare_name(label)}"

    if isinstance(pid, str) and pid.strip():
        return f"id:{pid.strip().lower()}"

    blob = json.dumps(policy, sort_keys=True, default=str)[:120]
    return f"hash:{blob}"
