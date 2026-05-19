import json
from pathlib import Path

from configuration_settings_deploy import clean_configuration_settings_for_graph_create


def test_choice_setting_value_gets_odata_type():
    settings = [
        {
            "settingInstance": {
                "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
                "choiceSettingValue": {
                    "children": [],
                    "value": "device_vendor_msft_policy_config_test_0",
                },
                "settingDefinitionId": "device_vendor_msft_policy_config_test",
            }
        }
    ]
    out = clean_configuration_settings_for_graph_create(settings)
    assert out[0]["@odata.type"] == "#microsoft.graph.deviceManagementConfigurationSetting"
    csv = out[0]["settingInstance"]["choiceSettingValue"]
    assert csv["@odata.type"] == (
        "#microsoft.graph.deviceManagementConfigurationChoiceSettingValue"
    )


def test_macros_disabled_sample_normalizes():
    path = (
        Path(__file__).resolve().parents[1]
        / "Source/Resources/Content/MSGraph/DeviceManagement/ConfigurationPolicies"
        / "All Macros Disabled.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = clean_configuration_settings_for_graph_create(raw["settings"][:3])
    assert len(out) == 3
    for row in out:
        assert row["@odata.type"].endswith("ConfigurationSetting")
        csv = row["settingInstance"].get("choiceSettingValue")
        assert csv and "@odata.type" in csv
