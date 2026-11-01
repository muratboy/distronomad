import os
import shutil
import stat
import socket
import pytest
from pathlib import Path
from unittest.mock import patch

from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem, RestoreOptions, BackupManifest
from distronomad.backup import BackupEngine
from distronomad.restore import RestoreEngine, rollback_restore


@pytest.mark.asyncio
async def test_ui_filter_active_selection_preserved(tmp_path):
    """(1) ui/app.py:1336 arama filtresi aktifken gizli ögelerin seçimi silinmemeli."""
    from distronomad.ui.app import DistroNomadApp
    from textual.widgets import SelectionList

    home = tmp_path / "mock_home"
    home.mkdir()

    app = DistroNomadApp(home_dir=str(home))
    async with app.run_test() as pilot:
        await pilot.pause()
        pkg1 = PackageItem(name="vim", manager="pacman", is_selected=True)
        pkg2 = PackageItem(name="nano", manager="pacman", is_selected=True)
        app.all_packages = [pkg1, pkg2]

        # Filtre uygula: Sadece "vim" listelenir
        app._populate_apps_list(filter_text="vim")
        await pilot.pause()

        list_apps = app.query_one("#list-apps", SelectionList)
        # Seçim değişimi olayını tetikle
        app.on_selection_list_selected_changed(SelectionList.SelectedChanged(list_apps))
        await pilot.pause()

        assert pkg2.is_selected is True, "Filtre dışında kalan 'nano' paketinin seçimi silindi!"


def test_backup_dir_format_does_not_rmtree_nonempty_dir(tmp_path):
    """(2) backup.py:810 'dir' formatı mevcut hedef dizini silmemeli, hedef var ve boş değilse hata vermeli."""
    target_dir = tmp_path / "existing_backup_dir"
    target_dir.mkdir()
    canary = target_dir / "precious_data.txt"
    canary.write_text("IMPORTANT_DATA")

    staging = tmp_path / "staging_dir"
    staging.mkdir()
    (staging / "manifest.json").write_text("{}")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(tmp_path))
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[],
        output_path=target_dir,
        archive_format="dir"
    )

    with pytest.raises(FileExistsError):
        engine._finalize_archive(staging)

    assert canary.exists(), "Mevcut hedef dizin rmtree ile silindi!"
    assert canary.read_text() == "IMPORTANT_DATA"


def test_encrypted_append_atomic_failure_preserves_original(tmp_path, monkeypatch):
    """(3) backup.py:1103 Şifreli append hata durumunda özgün .enc dosyasını doğrudan ezmemeli."""
    import distronomad.backup as backup_mod
    from distronomad.crypto import encrypt_archive

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(tmp_path))

    # Önce geçerli bir temel yedek oluştur
    base_file = tmp_path / "base_backup.tar.gz"
    engine_base = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[],
        output_path=base_file,
        archive_format="tar.gz"
    )
    created_base = engine_base.run()

    enc_file = tmp_path / "base_backup.tar.gz.enc"
    encrypt_archive(created_base, enc_file, "password123")
    orig_bytes = enc_file.read_bytes()

    # Append motoru
    new_custom_dir = tmp_path / "extra"
    new_custom_dir.mkdir()
    (new_custom_dir / "file.txt").write_text("extra")

    engine_append = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(new_custom_dir), is_dir=True, label="extra", is_selected=True)],
        output_path=enc_file,
        backup_mode="append",
        password="password123"
    )

    # encrypt_archive'ı hedef dosyayı bozup hata fırlatacak şekilde monkeypatch et
    def failing_encrypt(src, dst, pwd):
        Path(dst).write_text("CORRUPTED")
        raise IOError("Disk full or crypto crash")

    monkeypatch.setattr(backup_mod, "encrypt_archive", failing_encrypt)

    with pytest.raises(Exception):
        engine_append.run()

    assert enc_file.read_bytes() == orig_bytes, "Hata anında özgün .enc dosyası ezildi/bozuldu!"
    tmp_files = list(tmp_path.glob("*.tmp*"))
    assert len(tmp_files) == 0, f"Geçici artık dosyalar temizlenmedi: {tmp_files}"


def test_copytree_symlink_overwrite_and_rollback_on_error(tmp_path):
    """(4) restore.py:225,305,366 ve backup.py:1089 symlink üzerine kopyalama ve işlem günlüğü (rollback)."""
    home = tmp_path / "target_home"
    home.mkdir()
    cfg_dir = home / ".config" / "testapp"
    cfg_dir.mkdir(parents=True)
    # Hedefte var olan symlink
    existing_link = cfg_dir / "link_item"
    existing_link.symlink_to("old_target")

    # Yedek hazırla
    backup_dir = tmp_path / "backup_src"
    b_cfg = backup_dir / "configs" / ".config" / "testapp"
    b_cfg.mkdir(parents=True)
    (b_cfg / "link_item").symlink_to("new_target")
    (b_cfg / "file_item.txt").write_text("hello world")

    cfg_item = ConfigItem(
        id="cfg1",
        name="testapp",
        category="app",
        source_path=str(cfg_dir),
        rel_path=".config/testapp",
        is_selected=True
    )
    manifest = BackupManifest(
        created_at="2026-10-20T00:00:00",
        version="1.0.0",
        system_info=SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(home)),
        packages=[],
        configs=[cfg_item],
        custom_paths=[]
    )
    import json
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()))

    restore_engine = RestoreEngine(backup_path=backup_dir)
    options = RestoreOptions(
        selected_configs=[cfg_item],
        selected_custom_paths=[],
        install_packages=False,
        target_home=home,
        conflict_strategy="overwrite"
    )

    # 1. Aşama: Mevcut symlink'e sahip hedefe kopyalama çökmemeli
    res = restore_engine.execute_restore(options)
    assert os.readlink(existing_link) == "new_target", "Mevcut symlink üzerine güvenli kopyalanamadı!"

    # 2. Aşama: Hata durumunda da transaction log yazılmalı ve geri alınabilmeli
    shutil.rmtree(home / ".config" / "testapp", ignore_errors=True)
    cfg_item2 = ConfigItem(
        id="cfg2",
        name="testapp2",
        category="app",
        source_path=str(cfg_dir),
        rel_path=".config/testapp2",
        is_selected=True
    )
    b_cfg2 = backup_dir / "configs" / ".config" / "testapp2"
    b_cfg2.mkdir(parents=True)
    (b_cfg2 / "file2.txt").write_text("file2")

    options_error = RestoreOptions(
        selected_configs=[cfg_item, cfg_item2],
        selected_custom_paths=[],
        install_packages=False,
        target_home=home,
        conflict_strategy="overwrite"
    )

    tx_file = home / ".config" / "distronomad" / "transactions" / "last_restore.json"
    if tx_file.exists():
        tx_file.unlink()

    # İlk ayardan sonra hata fırlat (ikinci ayar bildirimi sırasında)
    with patch.object(RestoreEngine, "_notify", side_effect=[None, Exception("Midway crash"), None]):
        with pytest.raises(Exception, match="Midway crash"):
            restore_engine.execute_restore(options_error)

    assert tx_file.exists(), "Hata anında restore işlem günlüğü (last_restore.json) yazılmadı!"
    # Rollback testi: Yarım kalan restore geri alınabilmeli
    rb_res = rollback_restore(transaction_file=tx_file)
    assert not (home / ".config" / "testapp" / "file_item.txt").exists(), "Rollback işlemi yarım kalan dosyayı kaldıramadı!"


def test_custom_files_skip_conflict_strategy(tmp_path):
    """(5) restore.py:349 Özel dosyalarda 'skip' stratejisi seçilince dosya ezilmemeli."""
    home = tmp_path / "target_home"
    home.mkdir()
    existing_custom = home / "my_script.sh"
    existing_custom.write_text("LOCAL_VERSION")

    backup_dir = tmp_path / "backup_src"
    b_custom = backup_dir / "custom" / "home"
    b_custom.mkdir(parents=True)
    (b_custom / "my_script.sh").write_text("BACKUP_VERSION")

    manifest_file = backup_dir / "manifest.json"
    manifest_file.write_text('{"configs": [], "system_info": {"home_dir": "' + str(home) + '"}, "custom_paths": [{"path": "' + str(existing_custom) + '", "label": "my_script.sh", "is_dir": false}], "packages": []}')

    restore_engine = RestoreEngine(backup_path=backup_dir)
    options = RestoreOptions(
        selected_configs=[],
        selected_custom_paths=[CustomPathItem(path=str(existing_custom), label="my_script.sh", is_selected=True)],
        install_packages=False,
        target_home=home,
        conflict_strategy="skip"
    )

    restore_engine.execute_restore(options)
    assert existing_custom.read_text() == "LOCAL_VERSION", "Özel dosya 'skip' seçilmesine rağmen ezildi!"


def test_special_files_skipped_without_crashing_backup(tmp_path):
    """(6) backup.py:314 Soket/FIFO gibi özel dosyalar yedeği düşürmemeli, atlanıp devam etmeli."""
    src_dir = tmp_path / "src_with_socket"
    src_dir.mkdir()
    normal_file = src_dir / "normal.txt"
    normal_file.write_text("normal data")

    # Unix domain socket
    sock_file = src_dir / "test.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(str(sock_file))
    except Exception:
        pass

    # FIFO
    fifo_file = src_dir / "test.fifo"
    try:
        os.mkfifo(str(fifo_file))
    except Exception:
        pass

    out_file = tmp_path / "backup.tar.gz"
    sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(tmp_path))
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(src_dir), is_dir=True, label="socket_test", is_selected=True)],
        output_path=out_file,
        archive_format="tar.gz"
    )

    # Soket/FIFO varlığında çökmemeli
    res = engine.run()
    assert res.exists(), "Özel dosya içeren dizin yedeklenirken çöktü!"
