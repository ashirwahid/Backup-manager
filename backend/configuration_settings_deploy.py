"""
Normalize Settings Catalog (configurationPolicies) settings for Microsoft Graph create.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SETTING_ODATA = "#microsoft.graph.deviceManagementConfigurationSetting"
CHOICE_VALUE_ODATA = "#microsoft.graph.deviceManagementConfigurationChoiceSettingValue"

# Read-only / template metadata from export; not valid on create.
STRIP_INSTANCE_KEYS = frozenset({
    "settingInstanceTemplateReference",
    "auditRuleInformation",
    "settingVisibility",
    "accessTypes",
    "applicability",
})

STRIP_VALUE_KEYS = frozenset({
    "settingValueTemplateReference",
})


def _normalize_choice_setting_value(value: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "@odata.type": value.get("@odata.type") or CHOICE_VALUE_ODATA,
    }
    if "value" in value:
        out["value"] = value["value"]
    children = value.get("children")
    if isinstance(children, list) and children:
        out["children"] = [
            _normalize_setting_instance(child)
            for child in children
            if isinstance(child, dict)
        ]
    else:
        out["children"] = []
    return out


def _normalize_simple_setting_value(value: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in value.items():
        if key in STRIP_VALUE_KEYS:
            continue
        out[key] = val
    if "@odata.type" not in out:
        out["@odata.type"] = (
            "#microsoft.graph.deviceManagementConfigurationStringSettingValue"
        )
    return out


def _normalize_setting_instance(instance: Dict[str, Any]) -> Dict[str, Any]:
    inst = copy.deepcopy(instance)
    odata_type = inst.get("@odata.type") or ""
    out: Dict[str, Any] = {}

    if odata_type:
        out["@odata.type"] = odata_type

    if "settingDefinitionId" in inst:
        out["settingDefinitionId"] = inst["settingDefinitionId"]

    if "choiceSettingValue" in inst and isinstance(inst["choiceSettingValue"], dict):
        out["choiceSettingValue"] = _normalize_choice_setting_value(
            inst["choiceSettingValue"]
        )

    if "simpleSettingValue" in inst and isinstance(inst["simpleSettingValue"], dict):
        out["simpleSettingValue"] = _normalize_simple_setting_value(
            inst["simpleSettingValue"]
        )

    if "simpleSettingCollectionValue" in inst and isinstance(
        inst["simpleSettingCollectionValue"], list
    ):
        out["simpleSettingCollectionValue"] = [
            _normalize_simple_setting_value(v)
            if isinstance(v, dict)
            else v
            for v in inst["simpleSettingCollectionValue"]
        ]

    # Pass through other known instance shapes with minimal cleanup.
    for key in (
        "groupSettingCollectionValue",
        "choiceSettingCollectionValue",
        "secretSettingValue",
    ):
        if key in inst and key not in out:
            val = inst[key]
            if isinstance(val, dict):
                cleaned = {
                    k: v for k, v in val.items() if k not in STRIP_VALUE_KEYS
                }
                out[key] = cleaned
            elif isinstance(val, list):
                out[key] = [
                    (
                        {k: v for k, v in item.items() if k not in STRIP_VALUE_KEYS}
                        if isinstance(item, dict)
                        else item
                    )
                    for item in val
                ]

    if not out.get("@odata.type") and odata_type:
        out["@odata.type"] = odata_type

    return out


def clean_configuration_settings_for_graph_create(
    settings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Graph create rejects export-shaped settings unless each entry is wrapped as
    deviceManagementConfigurationSetting and choiceSettingValue includes @odata.type.
    """
    cleaned: List[Dict[str, Any]] = []
    for index, item in enumerate(settings):
        if not isinstance(item, dict):
            continue

        instance = item.get("settingInstance")
        if not isinstance(instance, dict):
            # Some exports put the instance at the root of the setting item.
            if item.get("@odata.type", "").endswith("SettingInstance"):
                instance = item
            else:
                logger.warning("Skipping settings entry without settingInstance")
                continue

        entry: Dict[str, Any] = {
            "@odata.type": SETTING_ODATA,
            "settingInstance": _normalize_setting_instance(instance),
        }
        setting_id = item.get("id")
        entry["id"] = str(setting_id) if setting_id is not None else str(index)
        cleaned.append(entry)

    return cleaned
