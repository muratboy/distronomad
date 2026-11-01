import os
import json
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    RestoreOptions,
    BackupManifest
)
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.cli import cmd_restore


def test_incremental_restore_missing_base_raises_error(tmp_path):
    """(4) restore.py: Artımlı restore'da taban yedek bulunamazsa sessiz kalmamalı, FileNotFoundError fırlatmalı."""
    inc_dir = tmp_path / "inc_backup"
    inc_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "backup_type": "incremental",
        "base_backup_name": "nonexistent_base.tar.zst",
        "system_info": {"distro_id": "arch", "distro_name": "Arch", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (inc_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=inc_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = inc_dir

    options = RestoreOptions(
        target_home=str(tmp_path / "target_home"),
        base_backup_path=str(tmp_path / "nonexistent_base.tar.zst")
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        engine.execute_restore(options)
    assert "taban yedek bulunamadı" in str(exc_info.value).lower() or "temel yedek" in str(exc_info.value).lower()


def test_cli_restore_defaults_to_current_user_home(tmp_path):
    """(3) cli.py:167 CLI restore hedefi kaynak makinenin home'u yerine varsayılan olarak mevcut kullanıcının home'u olmalı."""
    backup_file = tmp_path / "backup.tar.zst"
    backup_file.write_text("dummy")

    manifest = BackupManifest(
        created_at="2026-10-20",
        version="1.0",
        system_info=SystemInfo(
            distro_id="arch",
            distro_name="Arch Linux",
            home_dir="/home/source_user"
        ),
        packages=[],
        configs=[],
        custom_paths=[]
    )

    args = MagicMock()
    args.src = str(backup_file)
    args.target_home = None
    args.password = None
    args.configs_only = False
    args.packages_only = False
    args.conflict = "backup"
    args.dry_run = True
    args.base = None
    args.restore_system_state = False

    captured_options = None

    def fake_execute(opts, target_system_info=None):
        nonlocal captured_options
        captured_options = opts
        return {
            "restored_configs": [],
            "restored_files": [],
            "installed_packages": [],
            "failed_packages": [],
            "untranslated_packages": [],
            "restored_system_state": [],
            "warnings": []
        }

    with patch("distronomad.cli.RestoreEngine") as mock_engine_cls:
        engine_instance = MagicMock()
        engine_instance.load_manifest.return_value = manifest
        engine_instance.execute_restore.side_effect = fake_execute
        mock_engine_cls.return_value = engine_instance

        cmd_restore(args)

    assert captured_options is not None
    assert captured_options.target_home == str(Path.home())
    assert captured_options.target_home != "/home/source_user"


def test_restore_system_state_default_is_false():
    """(2) restore.py / models.py: restore_system_state varsayılanı False olmalı."""
    opts = RestoreOptions()
    assert opts.restore_system_state is False


def test_restore_system_state_backs_up_and_records_dconf_and_crontab(tmp_path):
    """(2) restore.py: Sistem durumu yüklendiğinde mevcut dconf ve crontab ezilmeden önce yedeklenmeli ve rollback'e kaydedilmeli."""
    backup_dir = tmp_path / "backup"
    state_dir = backup_dir / "system_state"
    state_dir.mkdir(parents=True)
    (state_dir / "dconf-settings.ini").write_text("[test/key]\nvalue=1\n")
    (state_dir / "crontab.txt").write_text("* * * * * echo test\n")

    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "arch", "distro_name": "Arch", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)

    options = RestoreOptions(
        target_home=str(fake_home),
        install_packages=False,
        restore_system_state=True
    )

    # subprocess.run çağrılarını mock'la (dconf dump, dconf load, crontab -l, crontab file)
    def fake_subprocess_run(cmd, *args, **kwargs):
        if cmd[0] == "dconf" and cmd[1] == "dump":
            return subprocess.CompletedProcess(cmd, 0, stdout="[existing/key]\nvalue=0\n", stderr="")
        elif cmd[0] == "crontab" and cmd[1] == "-l":
            return subprocess.CompletedProcess(cmd, 0, stdout="0 0 * * * /old/job.sh\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.Path.home", return_value=fake_home), \
         patch("distronomad.restore.shutil.which", return_value="/usr/bin/mock"), \
         patch("distronomad.restore.subprocess.run", side_effect=fake_subprocess_run) as mock_run:
        results = engine.execute_restore(options)

    # İşlem kaydını oku
    tx_file = fake_home / ".config" / "distronomad" / "transactions" / "last_restore.json"
    assert tx_file.exists()
    tx_data = json.loads(tx_file.read_text(encoding="utf-8"))
    op_types = [op.get("type") for op in tx_data.get("operations", [])]

    assert "system_state_dconf" in op_types
    assert "system_state_crontab" in op_types

    # Yedek dosyalarının varlığını doğrula
    dconf_op = next(op for op in tx_data["operations"] if op.get("type") == "system_state_dconf")
    assert dconf_op["backup_path"] is not None
    assert Path(dconf_op["backup_path"]).exists()
    assert "[existing/key]" in Path(dconf_op["backup_path"]).read_text()

    cron_op = next(op for op in tx_data["operations"] if op.get("type") == "system_state_crontab")
    assert cron_op["backup_path"] is not None
    assert Path(cron_op["backup_path"]).exists()
    assert "/old/job.sh" in Path(cron_op["backup_path"]).read_text()

    # Rollback yapıldığında dconf ve crontab'ın eski durumuna yüklendiğini doğrula
    rollback_cmds = []
    def fake_rb_sub_run(cmd, *args, **kwargs):
        rollback_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.Path.home", return_value=fake_home), \
         patch("distronomad.restore.shutil.which", return_value="/usr/bin/mock"), \
         patch("distronomad.restore.subprocess.run", side_effect=fake_rb_sub_run):
        rb_res = rollback_restore(transaction_file=tx_file)

    assert rb_res["success"] is True
    assert "dconf" in rb_res["reverted_files"]
    assert "crontab" in rb_res["reverted_files"]


def test_package_restore_cross_distro_filters_untranslated(tmp_path):
    """(1) Farklı dağıtım ailesine geri yüklemede çevrilemeyen paketler kurulumdan çıkarılıp untranslated_packages olarak raporlanmalı."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "arch", "distro_like": "arch", "distro_name": "Arch", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="bat", manager="pacman", category="Geliştirme"),
        PackageItem(name="cachyos-hello", manager="pacman", category="Sistem")
    ]

    target_sys = SystemInfo(
        distro_id="debian",
        distro_like="debian",
        distro_name="Debian GNU/Linux",
        home_dir=str(tmp_path / "target_home"),
        available_package_managers=["apt"]
    )

    options = RestoreOptions(
        target_home=str(tmp_path / "target_home"),
        install_packages=True,
        selected_packages=pkgs
    )

    installed_cmds = []

    def fake_sub_run(cmd, *args, **kwargs):
        installed_cmds.append(cmd)
        if cmd[0] == "apt-cache":
            return subprocess.CompletedProcess(cmd, 100, stdout="Package: batcat\n", stderr="W: Unable to locate package cachyos-hello\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    assert "untranslated_packages" in results
    assert "cachyos-hello" in results["untranslated_packages"]
    assert "batcat" in results["installed_packages"]
    # cachyos-hello kurulum komutuna gitmemeli!
    for cmd in installed_cmds:
        if "install" in cmd:
            assert "cachyos-hello" not in cmd


def test_package_restore_same_distro_family_preserves_names(tmp_path):
    """(1) Aynı dağıtım ailesinde (örn: cachyos -> arch) paket adları olduğu gibi geçmeli, çevrilemeyen sayılmamalı."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "cachyos", "distro_like": "arch", "distro_name": "CachyOS", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="cachyos-hello", manager="pacman", category="Sistem"),
        PackageItem(name="btop", manager="pacman", category="İzleme")
    ]

    target_sys = SystemInfo(
        distro_id="arch",
        distro_like="arch",
        distro_name="Arch Linux",
        home_dir=str(tmp_path / "target_home"),
        available_package_managers=["pacman"]
    )

    options = RestoreOptions(
        target_home=str(tmp_path / "target_home"),
        install_packages=True,
        selected_packages=pkgs
    )

    installed_cmds = []

    def fake_sub_run(cmd, *args, **kwargs):
        installed_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    assert len(results["untranslated_packages"]) == 0
    assert "cachyos-hello" in results["installed_packages"]
    assert "btop" in results["installed_packages"]


def test_package_restore_batch_failure_retries_per_package(tmp_path):
    """(1) Toplu paket kurulumu başarısız olursa, hatalı paketleri ayıklamak için tek tek denenmeli."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "arch", "distro_like": "arch", "distro_name": "Arch", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="good-pkg", manager="pacman", category="CLI"),
        PackageItem(name="broken-pkg", manager="pacman", category="CLI")
    ]

    target_sys = SystemInfo(
        distro_id="arch",
        distro_like="arch",
        distro_name="Arch Linux",
        home_dir=str(tmp_path / "target_home"),
        available_package_managers=["pacman"]
    )

    options = RestoreOptions(
        target_home=str(tmp_path / "target_home"),
        install_packages=True,
        selected_packages=pkgs
    )

    def fake_sub_run(cmd, *args, **kwargs):
        # Toplu komut (her iki paket birden var) -> hata ver
        if "good-pkg" in cmd and "broken-pkg" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: target not found: broken-pkg")
        # Tek tek kurulum
        if "broken-pkg" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: target not found: broken-pkg")
        if "good-pkg" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    assert "good-pkg" in results["installed_packages"]
    assert any("broken-pkg" in f for f in results["failed_packages"])


def test_cross_distro_unmapped_package_installed_if_in_repo_and_missing_reported(tmp_path):
    """Arch->Debian restore: eşlemesiz 'git' depoda varsa kurulmalı; depoda olmayan ad raporlanmalı ve kurulmamalı."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "arch", "distro_like": "arch", "distro_name": "Arch", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="git", manager="pacman", category="Geliştirme"),
        PackageItem(name="nonexistent-pkg", manager="pacman", category="Diğer")
    ]

    target_sys = SystemInfo(
        distro_id="debian",
        distro_like="debian",
        distro_name="Debian GNU/Linux",
        home_dir=str(tmp_path / "target_home"),
        available_package_managers=["apt"]
    )

    options = RestoreOptions(
        target_home=str(tmp_path / "target_home"),
        install_packages=True,
        selected_packages=pkgs
    )

    installed_cmds = []

    def fake_sub_run(cmd, *args, **kwargs):
        installed_cmds.append(cmd)
        if cmd[0] == "apt-cache":
            # git depoda var, nonexistent-pkg depoda yok
            return subprocess.CompletedProcess(
                cmd,
                100,
                stdout="Package: git\nVersion: 1:2.39.5-0+deb12u1\nArchitecture: amd64\n\n",
                stderr="W: Unable to locate package nonexistent-pkg\n"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # git'in eşlemesiz olduğunu garantilemek için translate_package None döndürecek şekilde test et
    with patch("distronomad.restore.translate_package", return_value=None), \
         patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    assert "git" in results["installed_packages"]
    assert "nonexistent-pkg" in results["untranslated_packages"]
    assert "nonexistent-pkg" not in results["installed_packages"]
    # nonexistent-pkg kurulum komutuna gitmemeli!
    for cmd in installed_cmds:
        if cmd[0] == "sudo" and "install" in cmd:
            assert "git" in cmd
            assert "nonexistent-pkg" not in cmd


def test_cross_distro_query_failure_does_not_filter_candidates(tmp_path):
    """Arch->Debian restore: depo sorgusu başarısız olursa adaylar elenmeden kuruluma gönderilmeli."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "arch", "distro_like": "arch", "distro_name": "Arch", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="git", manager="pacman", category="Geliştirme"),
        PackageItem(name="nonexistent-pkg", manager="pacman", category="Diğer")
    ]

    target_sys = SystemInfo(
        distro_id="debian",
        distro_like="debian",
        distro_name="Debian GNU/Linux",
        home_dir=str(tmp_path / "target_home"),
        available_package_managers=["apt"]
    )

    options = RestoreOptions(
        target_home=str(tmp_path / "target_home"),
        install_packages=True,
        selected_packages=pkgs
    )

    installed_cmds = []

    def fake_sub_run(cmd, *args, **kwargs):
        installed_cmds.append(cmd)
        if cmd[0] == "apt-cache":
            # Sorgu başarısız: returncode=1, boş stdout
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="apt-cache: database error")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.translate_package", return_value=None), \
         patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    # Sorgu başarısız olduğu için adaylar elenmemeli, kuruluma gönderilmeli
    assert len(results["untranslated_packages"]) == 0
    assert "git" in results["installed_packages"]
    assert "nonexistent-pkg" in results["installed_packages"]
    install_calls = [cmd for cmd in installed_cmds if cmd[0] == "sudo" and "install" in cmd]
    assert len(install_calls) > 0
    assert "git" in install_calls[0]
    assert "nonexistent-pkg" in install_calls[0]
