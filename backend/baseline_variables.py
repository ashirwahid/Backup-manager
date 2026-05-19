"""
Load Simeon-style baseline variables and expand ${Key} placeholders in policy JSON before deploy.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_VARIABLES_PATH = "Source/Resources/variables.json"
# Innermost ${Key} only (no nested ${ inside) — required for URNs like
# ${urn:...admin@${ResourceContext:TenantDomainName}?id}
INNERMOST_VARIABLE_RE = re.compile(r"\$\{([^{}]+)\}")

Scalar = Union[str, int, float, bool]


def normalize_variables_dict(raw: Any) -> Dict[str, str]:
    """Flatten a variables.json object to string values."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key, val in raw.items():
        if val is None:
            continue
        k = str(key).strip()
        if not k:
            continue
        if isinstance(val, (str, int, float, bool)):
            out[k] = str(val) if not isinstance(val, str) else val
        elif isinstance(val, dict):
            # Nested objects are uncommon; skip rather than stringify blindly.
            logger.debug("Skipping nested variables key %s", k)
    return out


def parse_variables_json_text(text: str) -> Dict[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Invalid variables.json: %s", e)
        return {}
    return normalize_variables_dict(data)


def expand_string_variables(
    value: str,
    variables: Dict[str, str],
    *,
    max_passes: int = 16,
) -> str:
    if not value or "${" not in value or not variables:
        return value
    out = value
    for _ in range(max_passes):
        changed = False

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            key = match.group(1)
            if key in variables:
                changed = True
                return variables[key]
            return match.group(0)

        new = INNERMOST_VARIABLE_RE.sub(repl, out)
        if not changed or new == out:
            return new
        out = new
    return out


def expand_variables_deep(obj: Any, variables: Dict[str, str]) -> Any:
    if not variables:
        return obj
    if isinstance(obj, str):
        return expand_string_variables(obj, variables)
    if isinstance(obj, list):
        return [expand_variables_deep(item, variables) for item in obj]
    if isinstance(obj, dict):
        return {k: expand_variables_deep(v, variables) for k, v in obj.items()}
    return obj


def tenant_domain_from_variables(variables: Dict[str, str]) -> Optional[str]:
    for key in (
        "ResourceContext:TenantDomainName",
        "ResourceContext:OnMicrosoftDomainName",
    ):
        val = (variables.get(key) or "").strip()
        if val and "@" not in val:
            return val
    return None


GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def admin_user_id_from_variables(variables: Dict[str, str]) -> Optional[str]:
    for key in (
        "ResourceContext:MSGraph:Users:admin",
        "Baseline:AdminUserId",
        "Baseline:BreakGlassUserId",
    ):
        val = (variables.get(key) or "").strip()
        if val and GUID_RE.match(val):
            return val
    return None


def find_unresolved_placeholders(obj: Any, *, path: str = "") -> List[str]:
    """Return dotted paths of string values that still contain ${...}."""
    found: List[str] = []
    if isinstance(obj, str) and "${" in obj:
        found.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{path}.{k}" if path else str(k)
            found.extend(find_unresolved_placeholders(v, path=child))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            child = f"{path}[{i}]"
            found.extend(find_unresolved_placeholders(v, path=child))
    return found
