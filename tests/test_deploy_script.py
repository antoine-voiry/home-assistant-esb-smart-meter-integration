"""Tests for deploy.sh using a fake Home Assistant config directory."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "deploy.sh"

pytestmark = pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync required")


def _fake_ha(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    (config / "custom_components" / "esb_smart_meter" / "esb_smart_meter").mkdir(parents=True)
    (config / "configuration.yaml").write_text("homeassistant:\n")
    (config / ".HA_VERSION").write_text("2026.10.1")
    (config / "custom_components" / "esb_smart_meter" / "stale.py").write_text("# old\n")
    # Real-world layout: a stale nested copy with its own bytecode cache
    nested_cache = config / "custom_components" / "esb_smart_meter" / "esb_smart_meter" / "__pycache__"
    nested_cache.mkdir()
    (nested_cache / "old.cpython-314.pyc").write_bytes(b"\0")
    return config


def _run(config: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=REPO,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HA_CONFIG": str(config),
            "DEPLOY_ALLOW_DIRTY": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _source_version() -> str:
    return json.loads((REPO / "custom_components/esb_smart_meter/manifest.json").read_text())["version"]


def test_refuses_non_ha_directory(tmp_path):
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "not a Home Assistant config" in result.stderr


def test_deploys_mirror_with_backup(tmp_path):
    config = _fake_ha(tmp_path)
    result = _run(config)
    assert result.returncode == 0, result.stderr

    target = config / "custom_components" / "esb_smart_meter"
    assert not (target / "stale.py").exists()
    assert not (target / "esb_smart_meter").exists()
    assert not list(target.rglob("__pycache__"))
    assert json.loads((target / "manifest.json").read_text())["version"] == _source_version()

    backups = list((config / "esb_smart_meter_backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "stale.py").exists()


def test_first_deploy_without_existing_install(tmp_path):
    config = _fake_ha(tmp_path)
    shutil.rmtree(config / "custom_components" / "esb_smart_meter")
    result = _run(config)
    assert result.returncode == 0, result.stderr
    assert (config / "custom_components" / "esb_smart_meter" / "manifest.json").exists()
    assert not (config / "esb_smart_meter_backups").exists()


def test_keeps_only_newest_backups(tmp_path):
    config = _fake_ha(tmp_path)
    backup_root = config / "esb_smart_meter_backups"
    for i in range(7):
        (backup_root / f"20260101-00000{i}-old").mkdir(parents=True)
    result = _run(config)
    assert result.returncode == 0, result.stderr
    remaining = sorted(p.name for p in backup_root.iterdir())
    assert len(remaining) == 5
    assert "20260101-000000-old" not in remaining


def test_dry_run_changes_nothing(tmp_path):
    config = _fake_ha(tmp_path)
    result = _run(config, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert (config / "custom_components" / "esb_smart_meter" / "stale.py").exists()
    assert not (config / "esb_smart_meter_backups").exists()


def test_refuses_uncommitted_changes_without_override(tmp_path):
    config = _fake_ha(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HA_CONFIG": str(config), "GIT_DIR": "/nonexistent"},
        capture_output=True,
        text=True,
        check=False,
    )
    # Without git metadata the dirty check cannot pass silently: it must fail closed
    assert result.returncode != 0
    assert "Uncommitted changes" in result.stderr


def test_undeletable_stale_items_warn_but_deploy_succeeds(tmp_path):
    """Samba hides vetoed '._*' files, so stale folders can refuse deletion."""
    config = _fake_ha(tmp_path)
    stuck = config / "custom_components" / "esb_smart_meter" / "esb_smart_meter"
    (stuck / "hidden").write_text("vetoed\n")
    stuck.chmod(0o555)
    try:
        result = _run(config)
    finally:
        stuck.chmod(0o755)
    assert result.returncode == 0, result.stderr
    assert "could not remove some items" in result.stderr
    target = config / "custom_components" / "esb_smart_meter"
    assert json.loads((target / "manifest.json").read_text())["version"] == _source_version()
