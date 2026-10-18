"""Translation file consistency tests."""

import json
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "esb_smart_meter"


def _load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def test_en_json_matches_strings_json():
    """Home Assistant reads translations/en.json for custom integrations; keep it in sync."""
    assert _load("translations/en.json") == _load("strings.json")


def test_options_menu_entries_have_labels():
    menu = _load("translations/en.json")["options"]["step"]["init"]["menu_options"]
    assert set(menu) == {"update_interval", "manual_cookies"}
    assert all(label.strip() for label in menu.values())


def test_update_interval_step_is_translated():
    step = _load("translations/en.json")["options"]["step"]["update_interval"]
    assert step["data"]["update_interval"].strip()


def test_captcha_repair_issue_is_translated():
    issue = _load("translations/en.json")["issues"]["captcha_required"]
    assert issue["title"].strip()
    assert "{mprn}" in issue["description"] and "{esb_url}" in issue["description"]


def test_reauth_flow_is_translated():
    config = _load("translations/en.json")["config"]
    step = config["step"]["reauth_confirm"]
    assert "{username}" in step["description"]
    assert step["data"]["password"].strip()
    assert config["abort"]["reauth_successful"].strip()
    assert config["error"]["empty_password"].strip()
