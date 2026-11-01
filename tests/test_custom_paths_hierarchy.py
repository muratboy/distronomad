import os
import stat
import subprocess
from pathlib import Path
import pytest

from distronomad.models import SystemInfo, CustomPathItem, RestoreOptions
from distronomad.backup import BackupEngine
from distronomad.restore import RestoreEngine


def test_custom_paths_basename_collision_under_home(tmp_path):
    """
    ~/.local/bin ve ~/bin yolları aynı 'bin' basename'ine sahiptir.
    Yeni şemada ikisi de tam göreli yapısıyla saklanmalı ve çakışmamalıdır.
    """
    home = tmp_path / "mock_home"
    local_bin = home / ".local" / "bin"
    home_bin = home / "bin"
    local_bin.mkdir(parents=True)
    home_bin.mkdir(parents=True)

    (local_bin / "tool1.sh").write_text("#!/bin/sh\necho tool1\n")
    (home_bin / "tool2.sh").write_text("#!/bin/sh\necho tool2\n")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    backup_path = tmp_path / "backup_collision.tar.zst"

    custom_paths = [
        CustomPathItem(path=str(local_bin), label="local_bin", is_dir=True, is_selected=True),
        CustomPathItem(path=str(home_bin), label="home_bin", is_dir=True, is_selected=True),
    ]

    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=custom_paths,
        output_path=backup_path,
        archive_format="tar.zst",
        backup_mode="full"
    )
    engine.run()

    # Arşiv içeriğini kontrol et
    r_engine = RestoreEngine(backup_path=backup_path)
    try:
        manifest = r_engine.load_manifest()
        r_engine.ensure_extracted()

        # Yeni şemaya göre custom/home altında göreli tam yollar bulunmalı
        assert (r_engine.source_dir / "custom" / "home" / ".local" / "bin" / "tool1.sh").exists()
        assert (r_engine.source_dir / "custom" / "home" / "bin" / "tool2.sh").exists()

        # Restore: Başka bir target_home'a geri yükleme
        target_home = tmp_path / "restored_home"
        target_home.mkdir()
        opts = RestoreOptions(
            target_home=str(target_home),
            selected_configs=[],
            selected_packages=[],
            selected_custom_paths=manifest.custom_paths
        )
        res = r_engine.execute_restore(opts)

        assert (target_home / ".local" / "bin" / "tool1.sh").exists()
        assert (target_home / "bin" / "tool2.sh").exists()
        assert (target_home / ".local" / "bin" / "tool1.sh").read_text() == "#!/bin/sh\necho tool1\n"
        assert (target_home / "bin" / "tool2.sh").read_text() == "#!/bin/sh\necho tool2\n"
    finally:
        r_engine.cleanup()


def test_custom_paths_outside_home_and_unwritable(monkeypatch, tmp_path):
    """
    $HOME dışındaki yollar arşivde custom/root altında saklanmalı,
    manifest özgün mutlak yolu kaydetmeli ve restore'da özgün mutlak yola dönmelidir.
    Hedef yazılabilir değilse sudo KULLANILMAMALI, atlanmalı ve uyarı loglanmalıdır.
    """
    home = tmp_path / "mock_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    outside_dir = tmp_path / "outside_root" / "opt" / "myapp"
    outside_dir.mkdir(parents=True)
    outside_file = outside_dir / "app.conf"
    outside_file.write_text("CONF_KEY=123\n")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    backup_path = tmp_path / "backup_outside.tar.zst"

    custom_paths = [
        CustomPathItem(path=str(outside_file), label="app.conf", is_dir=False, is_selected=True)
    ]

    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=custom_paths,
        output_path=backup_path,
        archive_format="tar.zst",
        backup_mode="full"
    )
    engine.run()

    # Arşiv kontrolü
    r_engine = RestoreEngine(backup_path=backup_path)
    try:
        manifest = r_engine.load_manifest()
        r_engine.ensure_extracted()

        rel_root = Path(str(outside_file).lstrip("/"))
        assert (r_engine.source_dir / "custom" / "root" / rel_root).exists()

        # 1. Yazılabilir özgün yola geri yükleme (dosyayı önce silelim)
        outside_file.unlink()

        # Karar 3: Varsayılan olarak izin verilmediğinde atlanmalı
        opts_default = RestoreOptions(
            target_home=str(home),
            selected_configs=[],
            selected_packages=[],
            selected_custom_paths=manifest.custom_paths,
            allow_external_paths=False
        )
        res_def = r_engine.execute_restore(opts_default)
        assert not outside_file.exists()
        assert str(outside_file) in res_def["skipped_external_paths"]

        # Karar 3: allow_external_paths=True ile izin verildiğinde geri yüklenmeli
        opts = RestoreOptions(
            target_home=str(home),
            selected_configs=[],
            selected_packages=[],
            selected_custom_paths=manifest.custom_paths,
            allow_external_paths=True
        )
        res = r_engine.execute_restore(opts)
        assert outside_file.exists()
        assert outside_file.read_text() == "CONF_KEY=123\n"

        # 2. Yazılamayan hedef yol testi (örn. read-only sandbox /etc/fake_unwritable)
        unwritable_target = Path("/etc/distronomad_unwritable_test_never_create/test.conf")
        unwritable_item = CustomPathItem(path=str(unwritable_target), label="unwritable", is_dir=False, is_selected=True)
        # Sahte bir yedek dosyası ekleyelim
        fake_archive_src = r_engine.source_dir / "custom" / "root" / Path(str(unwritable_target).lstrip("/"))
        fake_archive_src.parent.mkdir(parents=True, exist_ok=True)
        fake_archive_src.write_text("SHOULD_FAIL")

        opts_unwritable = RestoreOptions(
            target_home=str(home),
            selected_configs=[],
            selected_packages=[],
            selected_custom_paths=[unwritable_item],
            allow_external_paths=True
        )
        res2 = r_engine.execute_restore(opts_unwritable)
        assert not unwritable_target.exists()
        assert any("yazılabilir değil" in w for w in res2["warnings"])
    finally:
        r_engine.cleanup()


def test_custom_paths_append_mode(monkeypatch, tmp_path):
    """
    Append modunda hem home-göreli hem de root-göreli özel yollar
    yeni şemaya göre eklenmeli ve çakışmamalıdır.
    """
    home = tmp_path / "mock_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    local_bin = home / ".local" / "bin"
    home_bin = home / "bin"
    local_bin.mkdir(parents=True)
    home_bin.mkdir(parents=True)

    (local_bin / "tool1.sh").write_text("tool1")
    (home_bin / "tool2.sh").write_text("tool2")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    backup_file = tmp_path / "append_backup.tar.zst"

    # İlk yedek: Yalnızca ~/.local/bin
    b1 = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(local_bin), label="local_bin", is_dir=True, is_selected=True)],
        output_path=backup_file,
        archive_format="tar.zst",
        backup_mode="full"
    )
    b1.run()

    # Append: ~/bin ve $HOME dışı bir dosya ekle
    outside_file = tmp_path / "outside_data.txt"
    outside_file.write_text("OUTSIDE_VAL")

    b2 = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[
            CustomPathItem(path=str(home_bin), label="home_bin", is_dir=True, is_selected=True),
            CustomPathItem(path=str(outside_file), label="outside", is_dir=False, is_selected=True)
        ],
        output_path=backup_file,
        archive_format="tar.zst",
        backup_mode="append"
    )
    b2.run()

    # Doğrulama
    r_engine = RestoreEngine(backup_path=backup_file)
    try:
        manifest = r_engine.load_manifest()
        r_engine.ensure_extracted()

        assert (r_engine.source_dir / "custom" / "home" / ".local" / "bin" / "tool1.sh").exists()
        assert (r_engine.source_dir / "custom" / "home" / "bin" / "tool2.sh").exists()
        rel_outside = Path(str(outside_file).lstrip("/"))
        assert (r_engine.source_dir / "custom" / "root" / rel_outside).exists()

        # Restore
        target_home = tmp_path / "restored_home"
        target_home.mkdir()
        monkeypatch.setenv("HOME", str(target_home))
        outside_file.unlink()

        opts = RestoreOptions(
            target_home=str(target_home),
            selected_configs=[],
            selected_packages=[],
            selected_custom_paths=manifest.custom_paths,
            allow_external_paths=True
        )
        r_engine.execute_restore(opts)

        assert (target_home / ".local" / "bin" / "tool1.sh").read_text() == "tool1"
        assert (target_home / "bin" / "tool2.sh").read_text() == "tool2"
        assert outside_file.exists()
        assert outside_file.read_text() == "OUTSIDE_VAL"
    finally:
        r_engine.cleanup()


def test_custom_paths_standalone_restore_script(tmp_path):
    """
    restore.sh bağımsız betiğinin custom/home ve custom/root yollarını
    doğru hedef konumlara aktardığını ve yazılamayan yollarda atladığını test eder.
    """
    home = tmp_path / "mock_home"
    home.mkdir()
    local_bin = home / ".local" / "bin"
    home_bin = home / "bin"
    local_bin.mkdir(parents=True)
    home_bin.mkdir(parents=True)

    (local_bin / "tool1.sh").write_text("tool1")
    (home_bin / "tool2.sh").write_text("tool2")

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(parents=True)
    outside_file = outside_dir / "global.cfg"
    outside_file.write_text("GLOBAL=1")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    backup_dir = tmp_path / "backup_dir_standalone"

    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[
            CustomPathItem(path=str(local_bin), label="local_bin", is_dir=True, is_selected=True),
            CustomPathItem(path=str(home_bin), label="home_bin", is_dir=True, is_selected=True),
            CustomPathItem(path=str(outside_file), label="global.cfg", is_dir=False, is_selected=True)
        ],
        output_path=backup_dir,
        archive_format="dir",
        backup_mode="full"
    )
    engine.run()

    restore_sh = backup_dir / "restore.sh"
    assert restore_sh.exists()

    # restore.sh çalıştır
    target_home = tmp_path / "restored_standalone_home"
    target_home.mkdir()
    outside_file.unlink()

    # 1. --target-home verildiğinde: Karar 3 ve Bulgu 2 gereği custom/root dosyaları köke ASLA yazılmaz
    proc = subprocess.run(
        ["bash", str(restore_sh), "--all", "--target-home", str(target_home)],
        cwd=str(backup_dir),
        capture_output=True,
        text=True
    )
    assert proc.returncode == 0, f"restore.sh başarısız oldu:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    assert (target_home / ".local" / "bin" / "tool1.sh").exists()
    assert (target_home / "bin" / "tool2.sh").exists()
    assert not outside_file.exists(), "Hedef ev dizini özel belirtildiğinde root dosyası köke yazılmamalıdır"

    # 2. --allow-root-paths verildiğinde ve target-home ana sistem home iken köke yazma başarılı olmalı
    proc2 = subprocess.run(
        ["bash", str(restore_sh), "--files-only", "--allow-root-paths"],
        cwd=str(backup_dir),
        capture_output=True,
        text=True
    )
    assert proc2.returncode == 0, f"restore.sh başarısız oldu:\nSTDOUT:\n{proc2.stdout}\nSTDERR:\n{proc2.stderr}"
    assert outside_file.exists()
    assert outside_file.read_text() == "GLOBAL=1"
