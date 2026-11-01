"""
DistroNomad - P2 Performans, Arch Provides ve Restore.sh .bak Regresyon Testleri.
1. backup.py: Verilerin /tmp'ye kopyalanmadan doğrudan arşive/hedefe akıtılması (streaming tar.add / direct dir).
2. restore.py: Arch Linux pacman -Si sonrası sanal/provides paketlerinin pacman -Sp ile çözümlenmesi.
3. standalone_script.py: restore.sh betiğinin mevcut özel dosyaların üzerine yazmadan önce .bak yedeği alması.
"""

import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from distronomad.backup import BackupEngine, read_manifest_from_backup
from distronomad.models import BackupManifest, ConfigItem, CustomPathItem, PackageItem, RestoreOptions, SystemInfo
from distronomad.restore import RestoreEngine


def test_backup_direct_streaming_no_tmp_data_copy(tmp_path, monkeypatch):
    """
    (1) backup.py: Veriler /tmp altına (tmpfs) kopyalanmadan doğrudan arşive akıtılmalı;
    küçük meta dizini hedef dizin altında oluşturulmalı ve iş bitince temizlenmelidir.
    """
    home = tmp_path / "home"
    home.mkdir()
    data_file = home / "large_data.bin"
    data_file.write_bytes(b"A" * 1024 * 50)  # 50 KB veri

    backup_dest_dir = tmp_path / "target_backup_dir"
    backup_dest_dir.mkdir()
    archive_out = backup_dest_dir / "my_backup.tar.gz"

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[
            CustomPathItem(path=str(data_file), label="large_data.bin", is_dir=False, is_selected=True)
        ],
        output_path=archive_out,
        archive_format="tar.gz",
        backup_mode="full"
    )

    created_meta_dirs = []
    orig_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        d = orig_mkdtemp(*args, **kwargs)
        created_meta_dirs.append(Path(d))
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", tracking_mkdtemp)

    final_path = engine.run()
    assert final_path.exists()
    assert final_path == archive_out

    # Oluşturulan geçici meta dizinleri hedef dizinde olmalı, /tmp altında olmamalı
    for md in created_meta_dirs:
        assert str(backup_dest_dir) in str(md), f"Geçici dizin hedef dışında oluşturuldu: {md}"
        assert not md.exists(), f"Geçici dizin işlem bitince temizlenmedi: {md}"

    # Arşiv iç düzeni ve manifest formatı doğrulanmalı
    manifest = read_manifest_from_backup(final_path)
    assert manifest is not None
    assert manifest.version == "1.0.0"
    assert manifest.archive_format == "tar.gz"
    assert any("large_data.bin" in k for k in manifest.file_metadata.keys())

    # Arşiv içeriğinde manifest, restore.sh ve veri doğrudan bulunmalı
    with tarfile.open(final_path, "r:gz") as tar:
        names = tar.getnames()
        assert "manifest.json" in names
        assert "restore.sh" in names
        assert any("large_data.bin" in n for n in names)


def test_backup_dir_format_direct_writes(tmp_path):
    """
    (1) backup.py: archive_format='dir' durumunda veriler /tmp'ye gitmeden doğrudan hedefe yazılmalı.
    """
    home = tmp_path / "home"
    home.mkdir()
    my_file = home / "config.txt"
    my_file.write_text("SOME_CONFIG=1")

    target_dir = tmp_path / "dir_backup"
    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[
            CustomPathItem(path=str(my_file), label="config.txt", is_dir=False, is_selected=True)
        ],
        output_path=target_dir,
        archive_format="dir",
        backup_mode="full"
    )

    res_path = engine.run()
    assert res_path == target_dir
    assert (target_dir / "manifest.json").exists()
    assert (target_dir / "restore.sh").exists()
    assert (target_dir / "custom" / "home" / "config.txt").read_text() == "SOME_CONFIG=1"


def test_arch_pacman_provides_resolution(tmp_path):
    """
    (2) restore.py ~114: Arch hedefinde pacman -Si ile bulunamayan sanal/provides paketleri
    pacman -Sp ile çözülerek kuruluma dahil edilmeli; gerçekten bulunamayanlar elenmelidir.
    """
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "debian", "distro_like": "debian", "distro_name": "Debian", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="git", manager="apt", category="Geliştirme"),
        PackageItem(name="sh", manager="apt", category="Kabuk"),
        PackageItem(name="nonexistent-pkg", manager="apt", category="Diğer")
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
        if cmd[:2] == ["pacman", "-Si"]:
            # git depoda doğrudan bulunur; sh ve nonexistent-pkg bulunamaz (exit 1)
            stdout = "Repository     : core\nName           : git\nVersion        : 2.45.0\n\n"
            stderr = "error: target not found: sh\nerror: target not found: nonexistent-pkg\n"
            return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr=stderr)
        elif cmd[:2] == ["pacman", "-Sp"]:
            # sh sanal paketi bash tarafından sağlanır (exit 0)
            target = cmd[-1]
            if target == "sh":
                return subprocess.CompletedProcess(cmd, 0, stdout="file:///var/cache/pacman/pkg/bash.pkg.tar.zst\n", stderr="")
            else:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"error: target not found: {target}\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.translate_package", return_value=None), \
         patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    assert "git" in results["installed_packages"]
    assert "sh" in results["installed_packages"]
    assert "nonexistent-pkg" in results["untranslated_packages"]
    assert "nonexistent-pkg" not in results["installed_packages"]

    # Kurulum komutunda git ve sh yer almalı, nonexistent-pkg yer almamalı
    install_calls = [cmd for cmd in installed_cmds if cmd[0] == "sudo" and "pacman" in cmd]
    assert len(install_calls) > 0
    assert "git" in install_calls[0]
    assert "sh" in install_calls[0]
    assert "nonexistent-pkg" not in install_calls[0]


def test_arch_pacman_all_virtual_packages(tmp_path):
    """
    (2) Sadece sanal paket sorgulandığında da pacman -Sp ile çözümlenip başarıyla kurulmalıdır.
    """
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest_data = {
        "created_at": "2026-10-20",
        "version": "1.0",
        "system_info": {"distro_id": "debian", "distro_like": "debian", "distro_name": "Debian", "home_dir": str(tmp_path / "home")},
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.manifest = BackupManifest.from_dict(manifest_data)
    engine.source_dir = backup_dir

    pkgs = [
        PackageItem(name="cron", manager="apt", category="Sistem")
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
        if cmd[:2] == ["pacman", "-Si"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: target not found: cron\n")
        elif cmd[:2] == ["pacman", "-Sp"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="cronie\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.translate_package", return_value=None), \
         patch("distronomad.restore.subprocess.run", side_effect=fake_sub_run):
        results = engine.execute_restore(options, target_system_info=target_sys)

    assert "cron" in results["installed_packages"]
    assert "cron" not in results["untranslated_packages"]


def test_standalone_restore_sh_creates_bak_for_custom_files(tmp_path):
    """
    (3) standalone_script.py ~194: restore.sh betiği mevcut özel dosyaları ezmeden önce
    .bak yedeği almalı ve yeni dosyayı başarıyla yüklemelidir.
    """
    home = tmp_path / "home"
    home.mkdir()
    doc_dir = home / "documents"
    doc_dir.mkdir()
    doc_file = doc_dir / "notes.txt"
    doc_file.write_text("NEW_BACKUP_CONTENT")

    backup_dir = tmp_path / "backup_dir"
    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[
            CustomPathItem(path=str(doc_dir), label="documents", is_dir=True, is_selected=True)
        ],
        output_path=backup_dir,
        archive_format="dir",
        backup_mode="full"
    )
    engine.run()

    restore_sh = backup_dir / "restore.sh"
    assert restore_sh.exists()

    # Hedef sistemde aynı dosyanın eski bir sürümü var
    target_home = tmp_path / "target_home"
    target_doc_dir = target_home / "documents"
    target_doc_dir.mkdir(parents=True)
    target_file = target_doc_dir / "notes.txt"
    target_file.write_text("PRE_EXISTING_ORIGINAL_CONTENT")

    # restore.sh çalıştır
    proc = subprocess.run(
        ["bash", str(restore_sh), "--all", "--target-home", str(target_home)],
        cwd=str(backup_dir),
        capture_output=True,
        text=True
    )
    assert proc.returncode == 0, f"restore.sh başarısız:\n{proc.stdout}\n{proc.stderr}"

    # 1. Dosya yeni içerikle güncellenmeli
    assert target_file.read_text() == "NEW_BACKUP_CONTENT"

    # 2. Var olan dosyanın .bak yedeği alınmış olmalı ve orijinal içeriği korumalı
    bak_files = list(target_doc_dir.glob("notes.txt.bak.*"))
    assert len(bak_files) == 1, f"Yedek .bak dosyası bulunamadı: {list(target_doc_dir.iterdir())}"
    assert bak_files[0].read_text() == "PRE_EXISTING_ORIGINAL_CONTENT"
