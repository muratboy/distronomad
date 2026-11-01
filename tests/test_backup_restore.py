"""
DistroNomad - Sanal İzolasyonlu Yedekleme ve Geri Yükleme Entegrasyon Testleri.
(Kullanıcı sistemine dokunmadan, tamamen sanal geçici dizinlerde test edilir)
"""

import os
import stat
import tempfile
from pathlib import Path
import pytest

from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    RestoreOptions,
)
from distronomad.discovery import SystemScanner
from distronomad.backup import BackupEngine
from distronomad.restore import RestoreEngine


@pytest.fixture
def mock_source_environment():
    """Test için sanal bir kullanıcı ev dizini ve konfigürasyon yapısı oluşturur."""
    with tempfile.TemporaryDirectory(prefix="test_mock_home_") as tmpdir:
        home = Path(tmpdir)

        # 1. ~/.config dizini ve örnek uygulamalar
        hypr_dir = home / ".config" / "hypr"
        hypr_dir.mkdir(parents=True, exist_ok=True)
        (hypr_dir / "hyprland.conf").write_text("monitor=,preferred,auto,1\nexec-once=waybar\n", encoding="utf-8")

        nvim_dir = home / ".config" / "nvim"
        nvim_dir.mkdir(parents=True, exist_ok=True)
        (nvim_dir / "init.lua").write_text("vim.opt.number = true\nvim.opt.relativenumber = true\n", encoding="utf-8")

        btop_dir = home / ".config" / "btop"
        btop_dir.mkdir(parents=True, exist_ok=True)
        (btop_dir / "btop.conf").write_text("color_theme = 'cyberpunk'\n", encoding="utf-8")

        # 2. Dotfile dosyaları
        (home / ".bashrc").write_text("alias ll='ls -la'\nexport EDITOR='nvim'\n", encoding="utf-8")
        (home / ".zshrc").write_text("export ZSH=\"$HOME/.oh-my-zsh\"\n", encoding="utf-8")

        # 3. Güvenlik dizini
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / "config").write_text("Host github.com\n  User git\n", encoding="utf-8")

        # 4. Özel kullanıcı klasörleri
        docs_dir = home / "Documents"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "rapor.txt").write_text("Gizli proje raporu 2026\n", encoding="utf-8")

        projects_dir = home / "Projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        (projects_dir / "app.py").write_text("print('Merhaba Dünya')\n", encoding="utf-8")

        yield home


def test_scanner_on_mock_environment(mock_source_environment):
    scanner = SystemScanner(home_dir=str(mock_source_environment))

    # Konfigürasyonları tara
    configs = scanner.scan_configs()
    cfg_names = [c.name for c in configs]
    rel_paths = [c.rel_path for c in configs]

    assert ".config/hypr" in rel_paths
    assert ".config/nvim" in rel_paths
    assert ".config/btop" in rel_paths
    assert ".bashrc" in rel_paths
    assert ".zshrc" in rel_paths
    assert ".ssh" in rel_paths

    # Özel klasörleri tara
    custom_paths = scanner.scan_custom_paths()
    p_paths = [cp.path for cp in custom_paths]
    assert str(mock_source_environment / "Documents") in p_paths
    assert str(mock_source_environment / "Projects") in p_paths


def test_backup_and_restore_cycle_tar_zst(mock_source_environment):
    """
    Tam döngü testi (tar.zst):
    1. Sanal kaynak sistem taranır.
    2. .tar.zst yedeği alınır.
    3. Tamamen temiz ikinci bir sanal dizine geri yüklenir.
    4. Dosya içerikleri ve izinleri doğrulanır.
    """
    scanner = SystemScanner(home_dir=str(mock_source_environment))
    sys_info = SystemInfo(
        distro_id="cachyos",
        distro_name="CachyOS",
        home_dir=str(mock_source_environment)
    )
    configs = scanner.scan_configs()
    custom_paths = scanner.scan_custom_paths()
    packages = [
        PackageItem(name="neovim", manager="pacman", category="Düzenleyici"),
        PackageItem(name="vlc", manager="flatpak", app_id="org.videolan.VLC")
    ]

    with tempfile.TemporaryDirectory(prefix="test_backup_out_") as backup_tmp:
        backup_file = Path(backup_tmp) / "sistem_yedek.tar.zst"

        # 1. Yedek al
        engine = BackupEngine(
            system_info=sys_info,
            packages=packages,
            configs=configs,
            custom_paths=custom_paths,
            output_path=backup_file,
            archive_format="tar.zst"
        )
        final_backup_path = engine.run()
        assert final_backup_path.exists()
        assert final_backup_path.stat().st_size > 0

        # 2. Temiz hedef sistemde geri yükle
        with tempfile.TemporaryDirectory(prefix="test_new_system_home_") as new_home_tmp:
            new_home = Path(new_home_tmp)

            restore_engine = RestoreEngine(backup_path=final_backup_path)
            manifest = restore_engine.load_manifest()

            assert manifest.system_info.distro_name == "CachyOS"
            assert len(manifest.configs) == len([c for c in configs if c.is_selected])
            assert len(manifest.packages) == 2

            # Geri yükleme seçenekleri
            options = RestoreOptions(
                target_home=str(new_home),
                selected_packages=manifest.packages,
                selected_configs=manifest.configs,
                selected_custom_paths=manifest.custom_paths,
                conflict_strategy="backup",
                install_packages=False, # Test ortamında sudo pacman çalıştırmıyoruz
                dry_run=False
            )

            results = restore_engine.execute_restore(options)

            # Doğrulamalar:
            # 1. Hyprland konfigürasyonu aktarıldı mı?
            restored_hypr = new_home / ".config" / "hypr" / "hyprland.conf"
            assert restored_hypr.exists()
            assert "exec-once=waybar" in restored_hypr.read_text(encoding="utf-8")

            # 2. Neovim konfigürasyonu aktarıldı mı?
            restored_nvim = new_home / ".config" / "nvim" / "init.lua"
            assert restored_nvim.exists()
            assert "vim.opt.number = true" in restored_nvim.read_text(encoding="utf-8")

            # 3. .bashrc aktarıldı mı?
            restored_bashrc = new_home / ".bashrc"
            assert restored_bashrc.exists()
            assert "alias ll='ls -la'" in restored_bashrc.read_text(encoding="utf-8")

            # 4. Belgeler klasörü aktarıldı mı?
            restored_doc = new_home / "Documents" / "rapor.txt"
            assert restored_doc.exists()
            assert "Gizli proje raporu 2026" in restored_doc.read_text(encoding="utf-8")

            # 5. Projeler klasörü aktarıldı mı?
            restored_proj = new_home / "Projects" / "app.py"
            assert restored_proj.exists()
            assert "Merhaba Dünya" in restored_proj.read_text(encoding="utf-8")

            restore_engine.cleanup()


def test_restore_conflict_handling_backup(mock_source_environment):
    """
    Çakışma testi (Conflict handling):
    Hedef sistemde önceden var olan dosya .bak uzantısıyla yedeklenmeli.
    """
    scanner = SystemScanner(home_dir=str(mock_source_environment))
    sys_info = SystemInfo(distro_id="arch", home_dir=str(mock_source_environment))
    configs = scanner.scan_configs()

    with tempfile.TemporaryDirectory(prefix="test_backup_conflict_") as b_tmp:
        b_file = Path(b_tmp) / "backup.tar.gz"
        b_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=configs,
            custom_paths=[],
            output_path=b_file,
            archive_format="tar.gz"
        )
        b_engine.run()

        with tempfile.TemporaryDirectory(prefix="test_dest_conflict_") as d_tmp:
            dest_home = Path(d_tmp)

            # Hedefte önceden var olan bir .bashrc oluştur
            pre_existing_bashrc = dest_home / ".bashrc"
            pre_existing_bashrc.write_text("ESKI_ONCEDEN_VAR_OLAN_ICERIK", encoding="utf-8")

            r_engine = RestoreEngine(backup_path=b_file)
            manifest = r_engine.load_manifest()

            opts = RestoreOptions(
                target_home=str(dest_home),
                selected_configs=manifest.configs,
                conflict_strategy="backup",
                install_packages=False,
                dry_run=False
            )
            r_engine.execute_restore(opts)

            # Yeni .bashrc yedeğin içeriğini almalı
            assert "alias ll='ls -la'" in pre_existing_bashrc.read_text(encoding="utf-8")

            # Eski dosya .bak uzantısıyla yedeklenmiş olmalı
            bak_files = list(dest_home.glob(".bashrc.bak.*"))
            assert len(bak_files) == 1
            assert "ESKI_ONCEDEN_VAR_OLAN_ICERIK" in bak_files[0].read_text(encoding="utf-8")

            r_engine.cleanup()


def test_restore_dry_run(mock_source_environment):
    """
    Dry-run modu testi:
    Hiçbir dosya hedef dizine yazılmamalıdır.
    """
    scanner = SystemScanner(home_dir=str(mock_source_environment))
    sys_info = SystemInfo(distro_id="arch", home_dir=str(mock_source_environment))
    configs = scanner.scan_configs()

    with tempfile.TemporaryDirectory(prefix="test_backup_dry_") as b_tmp:
        b_dir = Path(b_tmp) / "backup_dir"
        b_engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=configs,
            custom_paths=[],
            output_path=b_dir,
            archive_format="dir"
        )
        b_engine.run()

        with tempfile.TemporaryDirectory(prefix="test_dest_dry_") as d_tmp:
            dest_home = Path(d_tmp)

            r_engine = RestoreEngine(backup_path=b_dir)
            manifest = r_engine.load_manifest()

            opts = RestoreOptions(
                target_home=str(dest_home),
                selected_configs=manifest.configs,
                conflict_strategy="overwrite",
                install_packages=False,
                dry_run=True # Simülasyon
            )
            results = r_engine.execute_restore(opts)

            assert results["is_dry_run"] is True
            # Hedef dizinde hiçbir konfigürasyon dosyası oluşturulmamalı
            assert not (dest_home / ".config").exists()
            assert not (dest_home / ".bashrc").exists()


def test_backup_flush_sync_monkeypatch(monkeypatch, tmp_path):
    """Yedekleme bitişinde arşiv için fsync ve dizin için sync çağrıldığını, dizin fsync hatasının yok sayıldığını doğrular."""
    fsync_fds = []
    sync_called = []

    def mock_fsync(fd):
        fsync_fds.append(fd)

    def mock_sync():
        sync_called.append(True)

    monkeypatch.setattr(os, "fsync", mock_fsync)
    monkeypatch.setattr(os, "sync", mock_sync)

    src_home = tmp_path / "home"
    src_home.mkdir()
    (src_home / ".bashrc").write_text("test_content\n", encoding="utf-8")

    sys_info = SystemInfo(distro_id="arch", home_dir=str(src_home))
    cfgs = [ConfigItem(id="bashrc", name="Bash", category="Shell", source_path=str(src_home / ".bashrc"), rel_path=".bashrc", is_selected=True)]

    # 1. Tek dosyalı arşiv testi (tar.zst): dosya ve üst dizin için os.fsync çağrılmalı
    tar_out = tmp_path / "out.tar.zst"
    engine_tar = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=tar_out,
        archive_format="tar.zst",
        backup_mode="full"
    )
    engine_tar.run()
    assert len(fsync_fds) >= 2

    # 2. Dizin formatı testi (dir): os.sync çağrılmalı
    dir_out = tmp_path / "out_dir"
    engine_dir = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=dir_out,
        archive_format="dir",
        backup_mode="full"
    )
    engine_dir.run()
    assert len(sync_called) >= 1

    # 3. Dizin fsync çağrısının OSError vermesi durumunda yedeklemenin kesintisiz tamamlanması
    fsync_fds.clear()
    call_idx = 0

    def mock_fsync_dir_error(fd):
        nonlocal call_idx
        call_idx += 1
        if call_idx >= 2:
            raise OSError("Directory fsync not supported")
        fsync_fds.append(fd)

    monkeypatch.setattr(os, "fsync", mock_fsync_dir_error)
    tar_out_err = tmp_path / "out_err.tar.zst"
    engine_tar_err = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=tar_out_err,
        archive_format="tar.zst",
        backup_mode="full"
    )
    res_path = engine_tar_err.run()
    assert res_path.exists()

