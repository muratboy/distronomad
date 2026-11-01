"""
DistroNomad - P2 İzolasyon ve restore.py İşlem Günlüğü Düzeltme Testleri.
"""

import os
import subprocess
import json
from pathlib import Path
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.models import RestoreOptions, ConfigItem


def test_conftest_environment_isolation(tmp_path):
    """conftest autouse fixture'ının HOME ve XDG değişkenlerini geçici dizine yönlendirdiğini doğrular."""
    home = os.environ.get("HOME")
    assert home is not None
    assert str(tmp_path) in home or "isolated_home" in home

    for var in ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"]:
        val = os.environ.get(var)
        assert val is not None
        assert str(tmp_path) in val or "isolated_home" in val


def test_conftest_fakes_system_commands():
    """conftest autouse fixture'ının systemctl, npm, code komutlarını güvenle sahtelediğini doğrular."""
    proc_sys = subprocess.run(["systemctl", "--user", "is-active", "distronomad-backup.timer"], text=True)
    assert proc_sys.returncode == 3
    assert proc_sys.stdout == "inactive\n"

    proc_npm = subprocess.run(["npm", "list", "-g", "--depth=0", "--json"], text=True)
    assert proc_npm.returncode == 0
    assert json.loads(proc_npm.stdout) == {"dependencies": {}}

    proc_code = subprocess.run(["code", "--list-extensions"], text=True)
    assert proc_code.returncode == 0
    assert proc_code.stdout == ""


def test_restore_engine_custom_transaction_dir(tmp_path):
    """RestoreEngine'in işlem günlüğünü (last_restore.json) motor parametresiyle verilen dizine yazdığını doğrular."""
    backup_dir = tmp_path / "fake_backup"
    backup_dir.mkdir()
    target_home = tmp_path / "target_home"
    target_home.mkdir()
    custom_tx_dir = tmp_path / "custom_transactions"

    cfg_src = backup_dir / "configs" / ".config" / "app"
    cfg_src.mkdir(parents=True)
    (cfg_src / "config.ini").write_text("setting=1\n")

    # manifest.json
    manifest_data = {
        "created_at": "2026-10-20T12:00:00",
        "version": "1.0.0",
        "archive_format": "dir",
        "packages": [],
        "configs": [
            {
                "id": "c1",
                "name": "app",
                "category": "Test",
                "source_path": "/home/user/.config/app",
                "rel_path": ".config/app",
                "is_directory": True
            }
        ],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(
        backup_path=backup_dir,
        transaction_dir=custom_tx_dir
    )

    options = RestoreOptions(
        selected_configs=[
            ConfigItem(id="c1", name="app", category="Test", source_path="/home/user/.config/app", rel_path=".config/app", is_directory=True)
        ],
        target_home=str(target_home)
    )

    results = engine.execute_restore(options)
    assert (custom_tx_dir / "last_restore.json").exists()
    assert results.get("transaction_file") is not None
    assert str(custom_tx_dir) in results["transaction_file"]

    # rollback_restore ile transaction_dir parametresinin çalıştığını doğrula
    rb_res = rollback_restore(transaction_dir=custom_tx_dir)
    assert rb_res["success"] is True
