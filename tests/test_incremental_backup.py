"""
DistroNomad - Artımlı (Incremental) Yedekleme ve Eşitleme Testleri.
"""

import os
import time
import tempfile
import pytest
from pathlib import Path

from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    CustomPathItem,
    RestoreOptions,
    BackupManifest,
)
from distronomad.backup import (
    BackupEngine,
    read_manifest_from_backup,
    is_file_changed,
    scan_and_compare_entries,
)
from distronomad.restore import RestoreEngine


def test_incremental_change_detection():
    """Dosya değişiklik tespiti ve zaman damgası analizi testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f_unmodified = root / "unchanged.conf"
        f_unmodified.write_text("setting = 1\n")

        f_modified = root / "modified.conf"
        f_modified.write_text("color = blue\n")

        f_new = root / "brand_new.conf"
        f_new.write_text("new = true\n")

        st_u = f_unmodified.stat()
        st_m = f_modified.stat()

        base_manifest = BackupManifest(
            created_at="2026-10-13T12:00:00",
            version="1.0.0",
            system_info=SystemInfo(),
            file_metadata={
                "configs/test/unchanged.conf": {"size": st_u.st_size, "mtime": st_u.st_mtime},
                "configs/test/modified.conf": {"size": st_m.st_size, "mtime": st_m.st_mtime},
            }
        )

        # Değişiklik yok kontrolü
        is_chg, reason = is_file_changed(f_unmodified, "configs/test/unchanged.conf", base_manifest)
        assert is_chg is False
        assert reason == "unmodified"

        # Dosyayı güncelle
        time.sleep(0.02)
        f_modified.write_text("color = red_updated\n")

        is_chg_m, reason_m = is_file_changed(f_modified, "configs/test/modified.conf", base_manifest)
        assert is_chg_m is True
        assert reason_m == "modified"

        # Yeni dosya kontrolü
        is_chg_n, reason_n = is_file_changed(f_new, "configs/test/brand_new.conf", base_manifest)
        assert is_chg_n is True
        assert reason_n == "new"


def test_incremental_backup_cycle_and_stats():
    """Tam yedek -> dosya güncelleme -> artımlı yedek döngüsü testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / ".config" / "editor"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "init.lua").write_text("print('v1')\n")
        (cfg_dir / "theme.lua").write_text("return 'nord'\n")

        sys_info = SystemInfo(distro_id="cachyos", distro_name="CachyOS", home_dir=str(root))
        pkgs = [PackageItem(name="neovim", manager="pacman")]
        cfgs = [ConfigItem(id="c_ed", name="editor", category="Düzenleyici", source_path=str(cfg_dir), rel_path=".config/editor")]

        base_backup_path = root / "base_backup.tar.zst"
        engine_base = BackupEngine(
            system_info=sys_info,
            packages=pkgs,
            configs=cfgs,
            custom_paths=[],
            output_path=base_backup_path,
            archive_format="tar.zst",
            backup_mode="full"
        )
        base_out = engine_base.run()
        assert base_out.exists()

        # Temel yedekten manifesti oku
        base_manifest = read_manifest_from_backup(base_out)
        assert base_manifest is not None
        assert base_manifest.backup_type == "full"
        assert len(base_manifest.file_metadata) >= 2

        # Dosyayı değiştir ve yeni bir dosya ekle
        time.sleep(0.02)
        (cfg_dir / "init.lua").write_text("print('v2_updated_feature')\n")
        (cfg_dir / "plugin.lua").write_text("return { 'telescope' }\n")

        # Artımlı yedekleme al
        inc_backup_path = root / "inc_backup.tar.zst"
        engine_inc = BackupEngine(
            system_info=sys_info,
            packages=pkgs,
            configs=cfgs,
            custom_paths=[],
            output_path=inc_backup_path,
            archive_format="tar.zst",
            backup_mode="incremental",
            base_backup_path=base_out
        )
        inc_out = engine_inc.run()
        assert inc_out.exists()

        # Artımlı manifesti oku ve doğrula
        inc_manifest = read_manifest_from_backup(inc_out)
        assert inc_manifest is not None
        assert inc_manifest.backup_type == "incremental"
        assert inc_manifest.base_backup_name == base_out.name

        # İstatistikleri doğrula: theme.lua değişmedi (1 adet), init.lua ve plugin.lua değişti (2 adet)
        stats = inc_manifest.incremental_stats
        assert stats["total_scanned"] == 3
        assert stats["changed_count"] == 2
        assert stats["unchanged_count"] == 1
        assert stats["bytes_saved"] > 0


def test_incremental_directory_sync():
    """Düz dizin hedefinde anlık yerinde artımlı senkronizasyon (In-Place Sync) testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / ".config" / "terminal"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "alacritty.toml").write_text("font_size = 12\n")

        dest_dir = root / "usb_backup_mirror"

        sys_info = SystemInfo(distro_id="cachyos", distro_name="CachyOS", home_dir=str(root))
        cfgs = [ConfigItem(id="c_term", name="terminal", category="Terminal", source_path=str(cfg_dir), rel_path=".config/terminal")]

        # 1. İlk tam yedek (dizin modunda)
        engine_1 = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=dest_dir,
            archive_format="dir",
            backup_mode="full"
        )
        engine_1.run()

        copied_file = dest_dir / "configs" / ".config" / "terminal" / "alacritty.toml"
        assert copied_file.exists()
        assert "font_size = 12" in copied_file.read_text()

        # 2. Kaynak dosyayı güncelle ve 2. dosyayı ekle
        time.sleep(0.02)
        (cfg_dir / "alacritty.toml").write_text("font_size = 16\n")
        (cfg_dir / "colors.toml").write_text("theme = 'dracula'\n")

        # 3. Aynı dizine artımlı eşitleme çalıştırıldığında hata vermeli (A-P0-2: Yerinde sync kaldırıldı)
        engine_same = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=dest_dir,
            archive_format="dir",
            backup_mode="incremental",
            base_backup_path=dest_dir
        )
        with pytest.raises((ValueError, FileExistsError)):
            engine_same.run()

        # 4. Yeni bir dizine artımlı yedek oluştur
        inc_dir = root / "usb_backup_mirror_inc"
        engine_2 = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=inc_dir,
            archive_format="dir",
            backup_mode="incremental",
            base_backup_path=dest_dir
        )
        engine_2.run()

        # Orijinal taban yedeğin bozulmadığını doğrula
        assert "font_size = 12" in (dest_dir / "configs" / ".config" / "terminal" / "alacritty.toml").read_text()

        # Yeni artımlı dizin içindeki dosyanın güncellendiğini ve yeni dosyanın eklendiğini doğrula
        assert "font_size = 16" in (inc_dir / "configs" / ".config" / "terminal" / "alacritty.toml").read_text()
        assert (inc_dir / "configs" / ".config" / "terminal" / "colors.toml").exists()

        # Manifestin doğruluğunu kontrol et
        m = read_manifest_from_backup(inc_dir)
        assert m.backup_type == "incremental"
        assert m.base_backup_name == dest_dir.name
        assert m.incremental_stats["changed_count"] == 2


def test_restore_layered_incremental_backup():
    """Temel yedek + Artımlı yedek birleştirilerek geri yükleme testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / "source_home" / ".config" / "shell"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.sh").write_text("export OLD=1\n")
        (cfg_dir / "aliases.sh").write_text("alias ll='ls -la'\n")

        sys_info = SystemInfo(distro_id="cachyos", distro_name="CachyOS", home_dir=str(root / "source_home"))
        cfgs = [ConfigItem(id="c_sh", name="shell", category="Kabuk", source_path=str(cfg_dir), rel_path=".config/shell")]

        # 1. Temel yedeği al
        base_backup = root / "base.tar.zst"
        b_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=base_backup,
            archive_format="tar.zst",
            backup_mode="full"
        )
        b_engine.run()

        # 2. config.sh'ı güncelle, yeni bir dosya ekle
        time.sleep(0.02)
        (cfg_dir / "config.sh").write_text("export NEW=2\n")
        (cfg_dir / "prompt.sh").write_text("PS1='\\$ '\n")

        # 3. Artımlı yedeği al
        inc_backup = root / "inc.tar.zst"
        inc_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=inc_backup,
            archive_format="tar.zst",
            backup_mode="incremental",
            base_backup_path=base_backup
        )
        inc_engine.run()

        # 4. Yeni boş bir hedef sisteme artımlı yedekten geri yükleme yap (Temel yedek katmanı ile)
        target_home = root / "target_home"
        target_home.mkdir()

        restore_engine = RestoreEngine(backup_path=inc_backup)
        manifest = restore_engine.load_manifest()

        options = RestoreOptions(
            target_home=str(target_home),
            selected_configs=cfgs,
            selected_packages=[],
            selected_custom_paths=[],
            install_packages=False,
            conflict_strategy="overwrite",
            base_backup_path=str(base_backup)
        )

        results = restore_engine.execute_restore(options)
        restore_engine.cleanup()

        # Doğrulamalar:
        # 1. aliases.sh temel yedekten gelmeli (artımlıda yoktu ama temelden birleşti)
        assert (target_home / ".config" / "shell" / "aliases.sh").exists()
        assert "alias ll" in (target_home / ".config" / "shell" / "aliases.sh").read_text()

        # 2. config.sh en güncel haliyle (NEW=2) yüklenmeli
        assert (target_home / ".config" / "shell" / "config.sh").exists()
        assert "export NEW=2" in (target_home / ".config" / "shell" / "config.sh").read_text()

        # 3. prompt.sh artımlı yedekten gelmeli
        assert (target_home / ".config" / "shell" / "prompt.sh").exists()
        assert "PS1" in (target_home / ".config" / "shell" / "prompt.sh").read_text()


def test_multilevel_incremental_chain_common_symlinks_and_overwritten_files():
    """Çok seviyeli zincir, ortak yoldaki sembolik bağ ve ezilen dosyaların doğrudan açılmasını test eder."""
    with tempfile.TemporaryDirectory(prefix="test_d2_chain_") as td:
        root = Path(td)
        src_home = root / "src_home"
        src_home.mkdir()
        sys_info = SystemInfo(home_dir=str(src_home))

        cfg_dir = src_home / ".config" / "app"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "persistent.txt").write_text("base_persistent_data\n")
        (cfg_dir / "version.txt").write_text("v1_base\n")
        (cfg_dir / "target_v1.txt").write_text("target_v1_content\n")
        link_path = cfg_dir / "current_symlink"
        os.symlink("target_v1.txt", link_path)

        cfgs = [ConfigItem(id="app_cfg", name="App", category="Tools", source_path=str(cfg_dir), rel_path=".config/app", is_selected=True)]

        # 1. Base backup
        base_backup = root / "base.tar.zst"
        b_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=base_backup,
            archive_format="tar.zst",
            backup_mode="full"
        )
        b_engine.run()

        # 2. Inc1 backup (değişen version, değişen symlink)
        time.sleep(0.02)
        (cfg_dir / "version.txt").write_text("v2_inc1\n")
        (cfg_dir / "target_v2.txt").write_text("target_v2_content\n")
        link_path.unlink()
        os.symlink("target_v2.txt", link_path)

        inc1_backup = root / "inc1.tar.zst"
        inc1_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=inc1_backup,
            archive_format="tar.zst",
            backup_mode="incremental",
            base_backup_path=base_backup
        )
        inc1_engine.run()

        # 3. Inc2 backup (değişen version v3, yine aynı yolda değişen symlink)
        time.sleep(0.02)
        (cfg_dir / "version.txt").write_text("v3_inc2\n")
        (cfg_dir / "target_v3.txt").write_text("target_v3_content\n")
        link_path.unlink()
        os.symlink("target_v3.txt", link_path)

        inc2_backup = root / "inc2.tar.zst"
        inc2_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=inc2_backup,
            archive_format="tar.zst",
            backup_mode="incremental",
            base_backup_path=inc1_backup
        )
        inc2_engine.run()

        # 4. Temiz hedef dizine çok seviyeli zinciri geri yükle
        target_home = root / "target_home"
        target_home.mkdir()

        restore_engine = RestoreEngine(backup_path=inc2_backup)
        restore_engine.load_manifest()

        options = RestoreOptions(
            target_home=str(target_home),
            selected_configs=cfgs,
            selected_packages=[],
            selected_custom_paths=[],
            install_packages=False,
            conflict_strategy="overwrite",
            base_backup_path=str(inc1_backup)
        )

        results = restore_engine.execute_restore(options)
        restore_engine.cleanup()

        restored_dir = target_home / ".config" / "app"
        # persistent.txt temel yedekten başarıyla gelmeli
        assert (restored_dir / "persistent.txt").read_text() == "base_persistent_data\n"
        # version.txt üst katman tarafından ezilmeli (v3)
        assert (restored_dir / "version.txt").read_text() == "v3_inc2\n"
        # Sembolik bağ en üst katmandaki hedefi göstermeli ve FileExistsError vermemeli
        restored_link = restored_dir / "current_symlink"
        assert restored_link.is_symlink()
        assert os.readlink(restored_link) == "target_v3.txt"
        assert (restored_dir / "target_v3.txt").read_text() == "target_v3_content\n"


def test_incremental_deleted_file_detection_and_no_resurrection():
    """Tam yedekten sonra silinen dosyanın artımlı yedekte deleted_files'a yazılması ve geri yüklenmemesi testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / ".config" / "testapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "keep.txt").write_text("keep_content\n")
        (cfg_dir / "removed.txt").write_text("to_be_removed\n")

        sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(root))
        cfgs = [ConfigItem(id="c_test", name="testapp", category="Diğer", source_path=str(cfg_dir), rel_path=".config/testapp")]

        # 1. Tam yedek
        base_backup = root / "base.tar.zst"
        b_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=base_backup,
            archive_format="tar.zst",
            backup_mode="full"
        )
        b_engine.run()

        # 2. removed.txt dosyasını diskten sil
        (cfg_dir / "removed.txt").unlink()
        assert not (cfg_dir / "removed.txt").exists()

        # 3. Artımlı yedek al
        inc_backup = root / "inc1.tar.zst"
        inc_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=inc_backup,
            archive_format="tar.zst",
            backup_mode="incremental",
            base_backup_path=base_backup
        )
        inc_engine.run()

        inc_manifest = read_manifest_from_backup(inc_backup)
        assert inc_manifest is not None
        # deleted_files listesinde configs/.config/testapp/removed.txt bulunmalı
        rel_key = "configs/.config/testapp/removed.txt"
        assert rel_key in inc_manifest.deleted_files
        assert inc_manifest.incremental_stats.get("deleted_count") == 1
        assert rel_key not in inc_manifest.file_metadata

        # 4. Geri yükle ve silinen dosyanın diriltilmediğini doğrula
        target_home = root / "target_home"
        target_home.mkdir()

        restore_engine = RestoreEngine(backup_path=inc_backup)
        restore_engine.load_manifest()
        options = RestoreOptions(
            target_home=str(target_home),
            selected_configs=cfgs,
            conflict_strategy="overwrite",
            base_backup_path=str(base_backup)
        )
        restore_engine.execute_restore(options)
        restore_engine.cleanup()

        restored_app = target_home / ".config" / "testapp"
        assert (restored_app / "keep.txt").exists()
        assert (restored_app / "keep.txt").read_text() == "keep_content\n"
        assert not (restored_app / "removed.txt").exists()


def test_multi_level_chain_deleted_and_recreated_files():
    """Çok seviyeli zincirde silinen dosyanın silinmiş kalması ve silinip yeniden oluşturulanın geri gelmesi testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / ".config" / "chainapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "f1.txt").write_text("f1_v1\n")
        (cfg_dir / "f2.txt").write_text("f2_v1\n")
        (cfg_dir / "f3.txt").write_text("f3_v1\n")

        sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(root))
        cfgs = [ConfigItem(id="c_chain", name="chainapp", category="Diğer", source_path=str(cfg_dir), rel_path=".config/chainapp")]

        # 1. Full backup
        base_backup = root / "base.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=base_backup, archive_format="tar.zst", backup_mode="full"
        ).run()

        # 2. f1.txt ve f2.txt silinsin -> Inc1
        (cfg_dir / "f1.txt").unlink()
        (cfg_dir / "f2.txt").unlink()
        time.sleep(0.02)

        inc1_backup = root / "inc1.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=inc1_backup, archive_format="tar.zst", backup_mode="incremental",
            base_backup_path=base_backup
        ).run()

        inc1_manifest = read_manifest_from_backup(inc1_backup)
        assert "configs/.config/chainapp/f1.txt" in inc1_manifest.deleted_files
        assert "configs/.config/chainapp/f2.txt" in inc1_manifest.deleted_files
        assert inc1_manifest.incremental_stats.get("deleted_count") == 2

        # 3. f1.txt yeniden oluşturulsun (f2.txt silinmiş kalsın) -> Inc2
        time.sleep(0.02)
        (cfg_dir / "f1.txt").write_text("f1_recreated_v2\n")

        inc2_backup = root / "inc2.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=inc2_backup, archive_format="tar.zst", backup_mode="incremental",
            base_backup_path=inc1_backup
        ).run()

        inc2_manifest = read_manifest_from_backup(inc2_backup)
        # f1 yeniden oluşturulduğu için inc2 deleted_files içinde OLMAMALI
        assert "configs/.config/chainapp/f1.txt" not in inc2_manifest.deleted_files
        # f2 zaten inc1'de silindiği için kümülatif listeden düşmüştü, inc2'de tekrar silinmiş olarak listelenmez
        assert "configs/.config/chainapp/f2.txt" not in inc2_manifest.deleted_files

        # 4. Restore Inc2:
        # f1.txt geri GELMELİ (yeniden oluşturuldu)
        # f2.txt geri GELMEMELİ (inc1'de silindi ve inc2'de silinmişliği korundu)
        # f3.txt GELMELİ (asla silinmedi)
        target_home = root / "target_home"
        target_home.mkdir()

        restore_engine = RestoreEngine(backup_path=inc2_backup)
        restore_engine.load_manifest()
        options = RestoreOptions(
            target_home=str(target_home),
            selected_configs=cfgs,
            conflict_strategy="overwrite",
            base_backup_path=str(inc1_backup)
        )
        restore_engine.execute_restore(options)
        restore_engine.cleanup()

        restored_app = target_home / ".config" / "chainapp"
        assert (restored_app / "f1.txt").exists()
        assert (restored_app / "f1.txt").read_text() == "f1_recreated_v2\n"
        assert not (restored_app / "f2.txt").exists()
        assert (restored_app / "f3.txt").exists()
        assert (restored_app / "f3.txt").read_text() == "f3_v1\n"


def test_unselected_item_files_not_marked_deleted():
    """Seçimden çıkarılmış bir ögenin dosyalarının silinmiş sayılmadığı testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_a = root / ".config" / "app_a"
        cfg_b = root / ".config" / "app_b"
        cfg_a.mkdir(parents=True)
        cfg_b.mkdir(parents=True)
        (cfg_a / "file_a.txt").write_text("a\n")
        (cfg_b / "file_b.txt").write_text("b\n")

        sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(root))
        c_a = ConfigItem(id="c_a", name="app_a", category="Diğer", source_path=str(cfg_a), rel_path=".config/app_a", is_selected=True)
        c_b = ConfigItem(id="c_b", name="app_b", category="Diğer", source_path=str(cfg_b), rel_path=".config/app_b", is_selected=True)

        base_backup = root / "base.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=[c_a, c_b], custom_paths=[],
            output_path=base_backup, archive_format="tar.zst", backup_mode="full"
        ).run()

        # file_b.txt silinsin ve c_b seçimden çıkarılsın
        (cfg_b / "file_b.txt").unlink()
        c_b.is_selected = False

        inc_backup = root / "inc.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=[c_a, c_b], custom_paths=[],
            output_path=inc_backup, archive_format="tar.zst", backup_mode="incremental",
            base_backup_path=base_backup
        ).run()

        inc_manifest = read_manifest_from_backup(inc_backup)
        # file_b seçili olmadığı için silinmiş sayılmamalı
        assert "configs/.config/app_b/file_b.txt" not in inc_manifest.deleted_files
        assert len(inc_manifest.deleted_files) == 0


def test_target_home_existing_file_never_deleted_security():
    """GÜVENLİK: deleted_files hedef sistemdeki (target_home) mevcut dosyaları asla silmemeli."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / ".config" / "secapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "target_only_file.txt").write_text("backup_version\n")

        sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(root))
        cfgs = [ConfigItem(id="c_sec", name="secapp", category="Diğer", source_path=str(cfg_dir), rel_path=".config/secapp")]

        base_backup = root / "base.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=base_backup, archive_format="tar.zst", backup_mode="full"
        ).run()

        # Kaynakta sil ve artımlı yedek al
        (cfg_dir / "target_only_file.txt").unlink()
        inc_backup = root / "inc.tar.zst"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=inc_backup, archive_format="tar.zst", backup_mode="incremental",
            base_backup_path=base_backup
        ).run()

        # Hedef sistemde kullanıcı tarafından oluşturulmuş önemli bir dosya var
        target_home = root / "target_home"
        target_file = target_home / ".config" / "secapp" / "target_only_file.txt"
        target_file.parent.mkdir(parents=True)
        target_file.write_text("precious_local_target_content_do_not_delete\n")

        restore_engine = RestoreEngine(backup_path=inc_backup)
        restore_engine.load_manifest()
        options = RestoreOptions(
            target_home=str(target_home),
            selected_configs=cfgs,
            conflict_strategy="overwrite",
            base_backup_path=str(base_backup)
        )
        restore_engine.execute_restore(options)
        restore_engine.cleanup()

        # Hedefteki dosya ASLA silinmemeli
        assert target_file.exists()
        assert target_file.read_text() == "precious_local_target_content_do_not_delete\n"


def test_dir_format_deleted_files_and_sync():
    """dir formatında yerinde senkronizasyonda deleted_files tespiti ve hedeften silinmesi testi."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_dir = root / ".config" / "dirapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "stay.txt").write_text("stay\n")
        (cfg_dir / "delete_me.txt").write_text("delete_me\n")

        sys_info = SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(root))
        cfgs = [ConfigItem(id="c_dir", name="dirapp", category="Diğer", source_path=str(cfg_dir), rel_path=".config/dirapp")]

        backup_dir = root / "dir_backup"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=backup_dir, archive_format="dir", backup_mode="full"
        ).run()

        assert (backup_dir / "configs" / ".config" / "dirapp" / "delete_me.txt").exists()

        # Kaynakta delete_me.txt sil
        (cfg_dir / "delete_me.txt").unlink()

        # Aynı dizine yerinde sync denenirse hata vermeli (A-P0-2)
        with pytest.raises((ValueError, FileExistsError)):
            BackupEngine(
                system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
                output_path=backup_dir, archive_format="dir", backup_mode="incremental",
                base_backup_path=backup_dir
            ).run()

        # Yeni dizine artımlı yedek
        inc_backup_dir = root / "dir_backup_inc"
        BackupEngine(
            system_info=sys_info, packages=[], configs=cfgs, custom_paths=[],
            output_path=inc_backup_dir, archive_format="dir", backup_mode="incremental",
            base_backup_path=backup_dir
        ).run()

        manifest = read_manifest_from_backup(inc_backup_dir)
        assert manifest is not None
        assert "configs/.config/dirapp/delete_me.txt" in manifest.deleted_files
        assert not (inc_backup_dir / "configs" / ".config" / "dirapp" / "delete_me.txt").exists()
        # Taban yedek bozulmamalı
        assert (backup_dir / "configs" / ".config" / "dirapp" / "delete_me.txt").exists()
        assert (backup_dir / "configs" / ".config" / "dirapp" / "stay.txt").exists()
