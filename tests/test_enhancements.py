"""
DistroNomad - Yeni Yetenekler ve Geliştirmeler Test Paketi.
1. Akıllı Filtreleme (Smart Ignore)
2. AES-256 Şifreleme (Crypto)
3. Donanım ve Sürücü Analizi (Hardware Profiler)
4. Geri Alma / Geri Yüklemeyi Geri Çevirme (Rollback / Undo Restore)
5. Fark Analizi (Diff Engine)
6. Otomatik Zamanlayıcı (systemd User Timer Automation)
"""

import os
import shutil
import tempfile
from pathlib import Path
import pytest

from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem, RestoreOptions
from distronomad.ignore import SmartIgnoreFilter
from distronomad.crypto import encrypt_archive, decrypt_archive, is_encrypted_file
from distronomad.hardware import scan_hardware_profile, HardwareProfile
from distronomad.diff_utils import generate_unified_diff, compare_backup_with_host, is_binary_file
from distronomad.automation import get_timer_status, enable_timer, disable_timer
from distronomad.backup import BackupEngine
from distronomad.restore import RestoreEngine, rollback_restore


@pytest.fixture
def mock_env(tmp_path):
    """İzole sahte sistem dizini oluşturur."""
    home_dir = tmp_path / "mock_home"
    home_dir.mkdir(parents=True, exist_ok=True)

    # Konfigürasyon dizinleri ve sahte dosyalar
    hypr_dir = home_dir / ".config" / "hypr"
    hypr_dir.mkdir(parents=True, exist_ok=True)
    (hypr_dir / "hyprland.conf").write_text("monitor=,preferred,auto,1\nautostart=waybar\n", encoding="utf-8")

    # Çöp ve önbellek dosyaları (Akıllı filtre testi için)
    cache_dir = hypr_dir / "Cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "temp_cache.bin").write_text("trash cache data", encoding="utf-8")

    node_modules_dir = home_dir / "projects" / "my_app" / "node_modules"
    node_modules_dir.mkdir(parents=True, exist_ok=True)
    (node_modules_dir / "package.json").write_text('{"name": "lib"}', encoding="utf-8")

    # Kişisel proje dosyası
    (home_dir / "projects" / "my_app" / "main.py").write_text("print('hello')\n", encoding="utf-8")

    sys_info = SystemInfo(
        distro_name="Arch Linux",
        distro_id="arch",
        distro_like=["arch"],
        kernel_version="6.10.0-arch",
        desktop_environment="Hyprland",
        home_dir=str(home_dir),
        shell="/bin/zsh",
        hostname="arch-box",
        available_package_managers=["pacman"]
    )

    return {
        "home": home_dir,
        "sys_info": sys_info,
        "hypr_dir": hypr_dir,
        "tmp_path": tmp_path
    }


def test_smart_ignore_filter():
    """SmartIgnoreFilter kurallarının önbellek ve çöp dosyalarını doğru ayıkladığını test eder."""
    flt = SmartIgnoreFilter(use_smart_defaults=True)

    assert flt.should_ignore(Path("/home/user/.config/google-chrome/Default/Cache/data_0"))
    assert flt.should_ignore(Path("/home/user/.config/Code/GPUCache/data_1"))
    assert flt.should_ignore(Path("/home/user/project/node_modules/express/index.js"))
    assert flt.should_ignore(Path("/home/user/project/.venv/bin/python"))
    assert flt.should_ignore(Path("/home/user/app/__pycache__/app.cpython-312.pyc"))
    assert flt.should_ignore(Path("/home/user/.local/share/Trash/files/old.txt"))

    # Normal yapılandırma ve kod dosyaları ASLA elenmemeli
    assert not flt.should_ignore(Path("/home/user/.config/hypr/hyprland.conf"))
    assert not flt.should_ignore(Path("/home/user/.config/nvim/init.lua"))
    assert not flt.should_ignore(Path("/home/user/.bashrc"))
    assert not flt.should_ignore(Path("/home/user/documents/report.pdf"))


def test_backup_with_smart_ignore(mock_env):
    """BackupEngine'in Akıllı Filtreleme ile önbellekleri elediğini test eder."""
    home = mock_env["home"]
    sys_info = mock_env["sys_info"]
    out_dir = mock_env["tmp_path"] / "backup_smart"

    configs = [
        ConfigItem(
            id="cfg_hypr",
            name="Hyprland",
            source_path=str(home / ".config" / "hypr"),
            rel_path="hypr",
            category="Pencere Yöneticisi",
            size_bytes=100,
            item_count=2,
            is_selected=True
        )
    ]

    custom_paths = [
        CustomPathItem(
            path=str(home / "projects"),
            label="Projeler",
            exists=True,
            is_dir=True,
            size_bytes=200,
            file_count=2,
            is_selected=True
        )
    ]

    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=configs,
        custom_paths=custom_paths,
        output_path=out_dir,
        archive_format="dir",
        backup_mode="full",
        use_smart_ignore=True
    )
    final_path = engine.run()

    # hyprland.conf ve main.py yedeklenmeli
    assert (final_path / "configs" / "hypr" / "hyprland.conf").exists()
    assert (final_path / "custom" / "home" / "projects" / "my_app" / "main.py").exists()

    # Cache ve node_modules ATLANMIŞ olmalı
    assert not (final_path / "configs" / "hypr" / "Cache").exists()
    assert not (final_path / "custom" / "home" / "projects" / "my_app" / "node_modules").exists()


def test_aes256_encryption_and_decryption(tmp_path):
    """AES-256 PBKDF2 şifreleme ve çözme döngüsünü test eder."""
    secret_file = tmp_path / "confidential.tar.gz"
    secret_file.write_text("Çok gizli SSH ve cüzdan anahtarları!", encoding="utf-8")

    pwd = "UltraSecurePassword123!"
    enc_path = encrypt_archive(secret_file, None, password=pwd)

    assert enc_path.exists()
    assert enc_path.name.endswith(".enc")
    assert is_encrypted_file(enc_path)

    # Yanlış parola ile çözme hata vermeli
    dec_fail_path = tmp_path / "failed.tar.gz"
    with pytest.raises(ValueError):
        decrypt_archive(enc_path, dec_fail_path, password="WrongPassword!")

    # Doğru parola ile çözme başarılı olmalı
    dec_success_path = tmp_path / "decrypted.tar.gz"
    decrypt_archive(enc_path, dec_success_path, password=pwd)

    assert dec_success_path.exists()
    assert dec_success_path.read_text(encoding="utf-8") == "Çok gizli SSH ve cüzdan anahtarları!"


def test_encrypted_backup_and_restore_cycle(mock_env):
    """BackupEngine ve RestoreEngine ile şifreli yedekleme ve geri yükleme tam döngüsünü test eder."""
    home = mock_env["home"]
    sys_info = mock_env["sys_info"]
    out_archive = mock_env["tmp_path"] / "secure_backup.tar.zst"
    pwd = "SuperBackupPass456#"

    configs = [
        ConfigItem(
            id="cfg_hypr",
            name="Hyprland",
            source_path=str(home / ".config" / "hypr"),
            rel_path="hypr",
            category="Pencere Yöneticisi",
            size_bytes=100,
            item_count=1,
            is_selected=True
        )
    ]

    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=configs,
        custom_paths=[],
        output_path=out_archive,
        archive_format="tar.zst",
        backup_mode="full",
        password=pwd
    )
    final_enc_archive = engine.run()

    assert final_enc_archive.exists()
    assert str(final_enc_archive).endswith(".enc")
    assert is_encrypted_file(final_enc_archive)

    # Parolasız geri yükleme denemesi hata vermeli
    restore_eng = RestoreEngine(backup_path=final_enc_archive)
    with pytest.raises(ValueError):
        restore_eng.load_manifest()

    # Doğru parola ile geri yükleme
    restore_eng_auth = RestoreEngine(backup_path=final_enc_archive, password=pwd)
    manifest = restore_eng_auth.load_manifest()
    assert manifest.system_info.distro_id == "arch"

    # Başka bir hedef dizine geri yükle
    target_home = mock_env["tmp_path"] / "restored_home"
    opts = RestoreOptions(
        target_home=str(target_home),
        selected_configs=manifest.configs,
        selected_packages=[],
        selected_custom_paths=[]
    )
    res = restore_eng_auth.execute_restore(opts)
    restore_eng_auth.cleanup()

    assert (target_home / "hypr" / "hyprland.conf").exists()
    assert len(res["restored_configs"]) == 1


def test_hardware_profiler_and_manifest_integration():
    """Donanım profili algılayıcı ve dağıtım sürücü paket eşleme testleri."""
    profile = scan_hardware_profile()
    assert isinstance(profile, HardwareProfile)
    assert profile.cpu_vendor in ["Intel", "AMD", "Unknown", "ARM"]
    assert profile.audio_system in ["pipewire", "pulseaudio", "alsa"]
    assert "arch" in profile.recommended_packages
    assert "debian" in profile.recommended_packages
    assert "fedora" in profile.recommended_packages

    # Manifest sözlük dönüşümü
    d = profile.to_dict()
    assert "cpu_vendor" in d
    assert "recommended_packages" in d


def test_diff_engine_and_host_comparison(mock_env):
    """Diff motorunun eklenen/silinen satırları doğru raporladığını test eder."""
    file_a = mock_env["tmp_path"] / "file_a.txt"
    file_b = mock_env["tmp_path"] / "file_b.txt"

    file_a.write_text("satir 1\nsatir 2 (eski)\nsatir 3\n", encoding="utf-8")
    file_b.write_text("satir 1\nsatir 2 (yeni)\nsatir 3\nsatir 4 (ek)\n", encoding="utf-8")

    diff_text, added, removed = generate_unified_diff(file_a, file_b)
    assert added == 2
    assert removed == 1
    assert "+satir 2 (yeni)" in diff_text
    assert "-satir 2 (eski)" in diff_text

    # İkili dosya testi
    bin_file = mock_env["tmp_path"] / "image.png"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert is_binary_file(bin_file)

    diff_bin, _, _ = generate_unified_diff(bin_file, file_b)
    assert "[İkili" in diff_bin


def test_rollback_undo_restore(mock_env):
    """Geri yükleme işlemi sonrası rollback (1-tıkla geri alma) mekanizmasını test eder."""
    home = mock_env["home"]
    target_config = home / ".config" / "hypr" / "hyprland.conf"
    original_content = target_config.read_text(encoding="utf-8")

    # Sahte bir yedek dizini oluştur
    backup_dir = mock_env["tmp_path"] / "sample_backup"
    configs_src = backup_dir / "configs" / ".config" / "hypr"
    configs_src.mkdir(parents=True, exist_ok=True)
    (configs_src / "hyprland.conf").write_text("yeni hyprland ayarlari\n", encoding="utf-8")

    # Ayrıca daha önce var olmayan yeni bir dosya
    new_cfg_src = backup_dir / "configs" / ".config" / "alacritty"
    new_cfg_src.mkdir(parents=True, exist_ok=True)
    (new_cfg_src / "alacritty.toml").write_text("font = 'Fira Code'\n", encoding="utf-8")

    # Manifest yaz
    (backup_dir / "manifest.json").write_text('{"created_at": "2026-10-15", "version": "1.0", "system_info": {"distro_name": "Arch", "distro_id": "arch", "distro_like": [], "kernel_version": "6", "desktop_environment": "Hyprland", "home_dir": "' + str(home) + '", "shell": "zsh", "hostname": "h", "available_package_managers": []}, "packages": [], "configs": [{"id": "1", "name": "Hypr", "source_path": "", "rel_path": ".config/hypr/hyprland.conf", "category": "WM", "size_bytes": 10, "item_count": 1, "is_selected": true}, {"id": "2", "name": "Alacritty", "source_path": "", "rel_path": ".config/alacritty/alacritty.toml", "category": "Term", "size_bytes": 10, "item_count": 1, "is_selected": true}], "custom_paths": [], "archive_format": "dir", "total_bytes": 100}', encoding="utf-8")

    restore_eng = RestoreEngine(backup_path=backup_dir)
    manifest = restore_eng.load_manifest()

    opts = RestoreOptions(
        target_home=str(home),
        selected_configs=manifest.configs,
        selected_packages=[],
        selected_custom_paths=[],
        conflict_strategy="backup"
    )

    restore_res = restore_eng.execute_restore(opts)
    tx_file = Path(restore_res["transaction_file"])
    assert tx_file.exists()

    # hyprland.conf yeni içerikle değişmiş olmalı
    assert target_config.read_text(encoding="utf-8") == "yeni hyprland ayarlari\n"
    # alacritty.toml oluşturulmuş olmalı
    alacritty_file = home / ".config" / "alacritty" / "alacritty.toml"
    assert alacritty_file.exists()

    # ŞİMDİ ROLLBACK (GERİ ALMA) ÇALIŞTIR
    rb_result = rollback_restore(transaction_file=tx_file)
    assert rb_result["success"] is True
    assert len(rb_result["reverted_files"]) == 1
    assert len(rb_result["removed_created"]) == 1

    # Orijinal dosya içeriği aynen geri gelmiş olmalı!
    assert target_config.read_text(encoding="utf-8") == original_content
    # Yeni oluşturulan dosya silinmiş olmalı!
    assert not alacritty_file.exists()


def test_systemd_automation_timer(tmp_path):
    """systemd user timer ve service dosya üretimini ve durumunu test eder."""
    mock_systemd_dir = tmp_path / "systemd_user"

    # Başlangıçta kapalı ve kurulu değil olmalı
    status_init = get_timer_status(systemd_dir=mock_systemd_dir)
    assert status_init["installed"] is False
    assert status_init["is_active"] is False

    # Etkinleştir (mock dizine yaz)
    res_enable = enable_timer(interval="weekly", backup_output_dir=tmp_path / "auto_backups", systemd_dir=mock_systemd_dir)
    assert res_enable["success"] is True
    assert res_enable["interval"] == "weekly"

    # Dosyaların üretildiğini doğrula
    service_file = mock_systemd_dir / "distronomad-backup.service"
    timer_file = mock_systemd_dir / "distronomad-backup.timer"
    assert service_file.exists()
    assert timer_file.exists()

    timer_content = timer_file.read_text(encoding="utf-8")
    assert "OnCalendar=weekly" in timer_content
    assert "Persistent=true" in timer_content

    # Durumun kurulu olduğunu doğrula
    status_after = get_timer_status(systemd_dir=mock_systemd_dir)
    assert status_after["installed"] is True
    assert status_after["interval"] == "weekly"

    # Devre dışı bırak
    res_disable = disable_timer(systemd_dir=mock_systemd_dir)
    assert res_disable["success"] is True
    assert not timer_file.exists()
    assert not service_file.exists()


def test_expanded_discovery_coverage(tmp_path):
    """
    Kabuk geçmişleri, Firefox/Thunderbird profilleri, Flatpak uygulama verileri,
    GNOME/KDE masaüstü bileşenleri, bulut anahtarları ve AppImage klasörlerinin
    tespitini izole ortamda doğrular.
    """
    from distronomad.discovery import SystemScanner

    home = tmp_path / "expanded_home"
    home.mkdir()

    # 1. Kabuk geçmişleri
    (home / ".zsh_history").write_text(": 1700000000:0;git status\n", encoding="utf-8")
    fish_dir = home / ".local/share/fish"
    fish_dir.mkdir(parents=True)
    (fish_dir / "fish_history").write_text("- cmd: ls -la\n", encoding="utf-8")

    # 2. Tarayıcı ve E-posta profilleri
    ff_dir = home / ".mozilla/firefox/default.profile"
    ff_dir.mkdir(parents=True)
    (ff_dir / "places.sqlite").write_text("bookmarks_db", encoding="utf-8")
    tb_dir = home / ".thunderbird/profile1"
    tb_dir.mkdir(parents=True)
    (tb_dir / "prefs.js").write_text("user_pref();", encoding="utf-8")

    # 3. Flatpak uygulama verisi ve overrides
    fp_obs = home / ".var/app/com.obsproject.Studio/config"
    fp_obs.mkdir(parents=True)
    (fp_obs / "global.ini").write_text("[General]\n", encoding="utf-8")
    fp_over = home / ".local/share/flatpak/overrides"
    fp_over.mkdir(parents=True)
    (fp_over / "global").write_text("[Context]\nfilesystems=home\n", encoding="utf-8")

    # 4. GNOME eklentileri & KDE Konsole profilleri
    gnome_ext = home / ".local/share/gnome-shell/extensions/blur-my-shell"
    gnome_ext.mkdir(parents=True)
    (gnome_ext / "metadata.json").write_text("{}", encoding="utf-8")
    kde_konsole = home / ".local/share/konsole"
    kde_konsole.mkdir(parents=True)
    (kde_konsole / "Cyberpunk.profile").write_text("[Appearance]\n", encoding="utf-8")

    # 5. Hassas Bulut & Şifre Kasaları
    kube_dir = home / ".kube"
    kube_dir.mkdir(parents=True)
    (kube_dir / "config").write_text("apiVersion: v1\n", encoding="utf-8")
    aws_dir = home / ".aws"
    aws_dir.mkdir(parents=True)
    (aws_dir / "credentials").write_text("[default]\n", encoding="utf-8")
    pass_dir = home / ".password-store"
    pass_dir.mkdir(parents=True)
    (pass_dir / "email.gpg").write_bytes(b"encrypted")
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_ed25519").write_text("test-ssh-key", encoding="utf-8")

    # 6. AppImages özel klasörü
    appimg_dir = home / "AppImages"
    appimg_dir.mkdir(parents=True)
    (appimg_dir / "cursor.AppImage").write_bytes(b"\x7fELF")

    scanner = SystemScanner(home_dir=str(home))
    configs = scanner.scan_configs()
    cfg_by_rel = {c.rel_path: c for c in configs}

    # Doğrulamalar
    assert ".zsh_history" in cfg_by_rel
    assert ".local/share/fish/fish_history" in cfg_by_rel
    assert ".mozilla/firefox" in cfg_by_rel
    assert ".thunderbird" in cfg_by_rel
    assert ".var/app/com.obsproject.Studio" in cfg_by_rel
    assert ".local/share/flatpak/overrides" in cfg_by_rel
    assert ".local/share/gnome-shell/extensions" in cfg_by_rel
    assert ".local/share/konsole" in cfg_by_rel

    # Hassas bulut anahtarları tespit edilmeli fakat güvenlik için varsayılan seçili olmamalı
    assert ".kube" in cfg_by_rel
    assert cfg_by_rel[".kube"].is_sensitive is True
    assert cfg_by_rel[".kube"].is_selected is False

    assert ".aws" in cfg_by_rel
    assert cfg_by_rel[".aws"].is_sensitive is True
    assert cfg_by_rel[".aws"].is_selected is False

    assert ".password-store" in cfg_by_rel
    assert cfg_by_rel[".password-store"].is_sensitive is True
    assert cfg_by_rel[".password-store"].is_selected is False

    assert ".ssh" in cfg_by_rel
    assert cfg_by_rel[".ssh"].is_sensitive is True
    assert cfg_by_rel[".ssh"].is_selected is False

    # Özel klasörler (AppImages)
    custom_paths = scanner.scan_custom_paths()
    cp_paths = [cp.path for cp in custom_paths]
    assert str(appimg_dir) in cp_paths


def test_system_state_and_vscode_backup_restore(tmp_path):
    """
    Sistem durumu (/etc/fstab, hosts, udev kuralları, NetworkManager profilleri,
    kullanıcı grupları) ve VS Code eklenti listelerinin yedeklenip geri yüklenmesini doğrular.
    """
    from distronomad.packagemap import get_install_command

    # 1. VS Code eklenti kurulum komutu kontrolü
    cmd, desc = get_install_command("vscode", ["ms-python.python", "esbenp.prettier-vscode"])
    assert cmd == ["code", "--install-extension", "ms-python.python", "--install-extension", "esbenp.prettier-vscode"]

    # 2. Sahte /etc dizini hazırla
    mock_etc = tmp_path / "mock_etc"
    mock_etc.mkdir()
    (mock_etc / "fstab").write_text("UUID=1234-5678 /mnt/games ext4 defaults 0 2\n", encoding="utf-8")
    (mock_etc / "hosts").write_text("127.0.0.1 localhost dev.local\n", encoding="utf-8")

    udev_dir = mock_etc / "udev/rules.d"
    udev_dir.mkdir(parents=True)
    (udev_dir / "99-qmk-keyboard.rules").write_text('SUBSYSTEMS=="usb", ATTRS{idVendor}=="feed", MODE="0666"\n', encoding="utf-8")

    nm_dir = mock_etc / "NetworkManager/system-connections"
    nm_dir.mkdir(parents=True)
    (nm_dir / "EvWiFi.nmconnection").write_text("[connection]\nid=EvWiFi\n", encoding="utf-8")

    # 3. Kaynak ev dizini ve yedekleme
    src_home = tmp_path / "src_home"
    src_home.mkdir()
    (src_home / ".bashrc").write_text("export PATH=$PATH:~/.local/bin\n", encoding="utf-8")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(src_home))
    pkgs = [
        PackageItem(name="ms-python.python", manager="vscode", version="2026.1.0", category="VS Code Eklentisi")
    ]
    cfgs = [
        ConfigItem(id="dot_bashrc", name=".bashrc", category="Kabuk", source_path=str(src_home / ".bashrc"), rel_path=".bashrc", is_directory=False)
    ]

    archive_out = tmp_path / "sys_state_backup.tar.zst"
    b_engine = BackupEngine(
        system_info=sys_info,
        packages=pkgs,
        configs=cfgs,
        custom_paths=[],
        output_path=archive_out,
        archive_format="tar.zst",
        password="test_pw_123",
        etc_dir=mock_etc
    )
    final_archive = b_engine.run()
    assert final_archive.exists()

    # 4. Geri yükleme ve system_state doğrulaması
    dst_home = tmp_path / "dst_home"
    dst_home.mkdir()

    r_engine = RestoreEngine(backup_path=final_archive, password="test_pw_123")
    try:
        manifest = r_engine.load_manifest()
        assert "fstab" in manifest.system_state_info.get("etc_reference_files", [])
        assert "hosts" in manifest.system_state_info.get("etc_reference_files", [])
        assert "udev_rules.d/99-qmk-keyboard.rules" in manifest.system_state_info.get("etc_reference_files", [])
        assert "NetworkManager/EvWiFi.nmconnection" in manifest.system_state_info.get("etc_reference_files", [])

        # Paket listesinde vscode_extensions.txt üretildiğini doğrula
        vscode_txt = r_engine.source_dir / "packages" / "vscode_extensions.txt"
        assert vscode_txt.exists()
        assert "ms-python.python" in vscode_txt.read_text(encoding="utf-8")

        opts = RestoreOptions(
            target_home=str(dst_home),
            selected_packages=manifest.packages,
            selected_configs=manifest.configs,
            selected_custom_paths=[],
            install_packages=False,
            restore_system_state=True,
            dry_run=False
        )
        res = r_engine.execute_restore(opts)
        assert len(res["restored_system_state"]) > 0

        ref_dir = dst_home / ".local/share/distronomad/system_state_reference/etc_reference"
        assert (ref_dir / "fstab").exists()
        assert "UUID=1234-5678" in (ref_dir / "fstab").read_text(encoding="utf-8")
        assert (ref_dir / "udev_rules.d/99-qmk-keyboard.rules").exists()
        assert (ref_dir / "NetworkManager/EvWiFi.nmconnection").exists()
    finally:
        r_engine.cleanup()


def test_backup_append_mode_and_custom_files_management(tmp_path):
    """
    Önceden oluşturulmuş bir yedek arşivine sonradan yeni uygulama,
    ayar ve özel dosyaların eklenebilmesini (append mode) doğrular.
    """
    home = tmp_path / "user_home"
    home.mkdir()
    (home / ".bashrc").write_text("export FOO=1\n", encoding="utf-8")
    (home / ".vimrc").write_text("set number\n", encoding="utf-8")

    docs_dir = home / "Documents"
    docs_dir.mkdir()
    (docs_dir / "notes.txt").write_text("My project notes\n", encoding="utf-8")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))

    # 1. Başlangıç Yedeği (Yalnızca curl ve .bashrc)
    initial_pkgs = [PackageItem(name="curl", manager="pacman", category="Ağ")]
    initial_cfgs = [ConfigItem(id="cfg_bashrc", name=".bashrc", category="Kabuk", source_path=str(home / ".bashrc"), rel_path=".bashrc", is_directory=False)]

    backup_file = tmp_path / "my_backup.tar.zst"
    b_engine1 = BackupEngine(
        system_info=sys_info,
        packages=initial_pkgs,
        configs=initial_cfgs,
        custom_paths=[],
        output_path=backup_file,
        archive_format="tar.zst",
        backup_mode="full"
    )
    b_engine1.run()
    assert backup_file.exists()

    # 2. Sonradan Ekleme (Append Mode): htop paketi, .vimrc ayarı ve Documents klasörü
    new_pkgs = [PackageItem(name="htop", manager="pacman", category="Sistem")]
    new_cfgs = [ConfigItem(id="cfg_vimrc", name=".vimrc", category="Düzenleyici", source_path=str(home / ".vimrc"), rel_path=".vimrc", is_directory=False)]
    new_custom = [CustomPathItem(path=str(docs_dir), label="Documents", is_dir=True, exists=True, is_selected=True)]

    b_engine2 = BackupEngine(
        system_info=sys_info,
        packages=new_pkgs,
        configs=new_cfgs,
        custom_paths=new_custom,
        output_path=backup_file,
        archive_format="tar.zst",
        backup_mode="append"
    )
    final_backup = b_engine2.run()
    assert final_backup.exists()

    # 3. Güncellenen arşivi incele ve doğrula
    r_engine = RestoreEngine(backup_path=final_backup)
    try:
        manifest = r_engine.load_manifest()
        pkg_names = [p.name for p in manifest.packages]
        assert "curl" in pkg_names
        assert "htop" in pkg_names

        cfg_names = [c.name for c in manifest.configs]
        assert ".bashrc" in cfg_names
        assert ".vimrc" in cfg_names

        assert len(manifest.custom_paths) == 1
        assert manifest.custom_paths[0].label == "Documents"

        # Dosyaların arşiv içinde gerçekten bulunduğunu doğrula
        assert (r_engine.source_dir / "configs" / ".bashrc").exists()
        assert (r_engine.source_dir / "configs" / ".vimrc").exists()
        assert (r_engine.source_dir / "custom" / "home" / "Documents" / "notes.txt").exists()
        assert "My project notes" in (r_engine.source_dir / "custom" / "home" / "Documents" / "notes.txt").read_text(encoding="utf-8")

        sys_pkgs_txt = (r_engine.source_dir / "packages" / "system_packages.txt").read_text(encoding="utf-8")
        assert "curl" in sys_pkgs_txt
        assert "htop" in sys_pkgs_txt
    finally:
        r_engine.cleanup()


def test_encrypted_backup_append_mode(tmp_path):
    """Şifreli bir yedeğe append modu ile yeni ögeler eklenebilmesini test eder."""
    home = tmp_path / "enc_home"
    home.mkdir()
    (home / ".bashrc").write_text("export ENCRYPTED=1\n", encoding="utf-8")
    (home / ".zshrc").write_text("export ZSH=1\n", encoding="utf-8")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    archive_path = tmp_path / "enc_test.tar.zst"

    # 1. Başlangıç şifreli yedek
    b1 = BackupEngine(
        system_info=sys_info,
        packages=[PackageItem(name="git", manager="pacman")],
        configs=[ConfigItem(id="bashrc", name=".bashrc", category="Kabuk", source_path=str(home / ".bashrc"), rel_path=".bashrc", is_directory=False)],
        custom_paths=[],
        output_path=archive_path,
        password="MySecretPassword123",
        backup_mode="full"
    )
    enc_file = b1.run()
    assert is_encrypted_file(enc_file)

    # 2. Şifreli yedeğe yeni config ve paket ekle
    b2 = BackupEngine(
        system_info=sys_info,
        packages=[PackageItem(name="neovim", manager="pacman")],
        configs=[ConfigItem(id="zshrc", name=".zshrc", category="Kabuk", source_path=str(home / ".zshrc"), rel_path=".zshrc", is_directory=False)],
        custom_paths=[],
        output_path=enc_file,
        password="MySecretPassword123",
        backup_mode="append"
    )
    updated_enc = b2.run()
    assert is_encrypted_file(updated_enc)

    # 3. Şifreli yedeği oku ve her iki ögenin de var olduğunu doğrula
    r = RestoreEngine(backup_path=updated_enc, password="MySecretPassword123")
    try:
        manifest = r.load_manifest()
        pkg_names = [p.name for p in manifest.packages]
        assert "git" in pkg_names
        assert "neovim" in pkg_names

        cfg_names = [c.name for c in manifest.configs]
        assert ".bashrc" in cfg_names
        assert ".zshrc" in cfg_names
    finally:
        r.cleanup()


def test_absolute_symlink_and_runtime_socket_in_backup_archive(tmp_path):
    """
    Python 3.14 tarfile 'AbsoluteLinkError' hatasını önlemek için mutlak sembolik bağ
    (systemd servisleri vb.) ve kırık SingletonSocket içeren yedeklerin sorunsuz
    incelenip, ekleme (append) yapılıp geri yüklenebildiğini doğrular.
    """
    home = tmp_path / "sym_home"
    systemd_wants = home / ".config" / "systemd" / "user" / "default.target.wants"
    systemd_wants.mkdir(parents=True)
    # Mutlak yola işaret eden sembolik bağ (systemd user unit)
    os.symlink("/usr/lib/systemd/user/pipewire.service", systemd_wants / "pipewire.service")

    app_cfg = home / ".config" / "MyElectronApp"
    app_cfg.mkdir(parents=True)
    (app_cfg / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    # Kırık / mutlak SingletonSocket bağı
    os.symlink("/tmp/scoped_dir_nonexistent/SingletonSocket", app_cfg / "SingletonSocket")

    extra_file = home / ".vimrc"
    extra_file.write_text("set number\n", encoding="utf-8")

    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))
    archive_path = tmp_path / "sym_backup.tar.zst"

    # 1. Yedek oluştur (use_smart_ignore=False ile SingletonSocket de arşive girsin ki çıkarma filtresi de test edilsin)
    b1 = BackupEngine(
        system_info=sys_info,
        packages=[PackageItem(name="pipewire", manager="pacman")],
        configs=[
            ConfigItem(
                id="systemd_user",
                name="systemd",
                category="Sistem",
                source_path=str(home / ".config" / "systemd"),
                rel_path=".config/systemd",
                is_directory=True,
            ),
            ConfigItem(
                id="electron_app",
                name="MyElectronApp",
                category="Uygulama",
                source_path=str(app_cfg),
                rel_path=".config/MyElectronApp",
                is_directory=True,
            ),
        ],
        custom_paths=[],
        output_path=archive_path,
        use_smart_ignore=False,
        backup_mode="full",
    )
    out_archive = b1.run()
    assert out_archive.exists()

    # 2. Mevcut yedeğe Append modu ile yeni dosya ekle (AbsoluteLinkError vermemeli)
    b2 = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[
            ConfigItem(
                id="vimrc",
                name=".vimrc",
                category="Editör",
                source_path=str(extra_file),
                rel_path=".vimrc",
                is_directory=False,
            )
        ],
        custom_paths=[],
        output_path=out_archive,
        backup_mode="append",
    )
    b2.run()

    # 3. RestoreEngine ile hem hızlı manifest okumayı hem tam geri yüklemeyi doğrula
    target_home = tmp_path / "restored_sym_home"
    target_home.mkdir()
    r = RestoreEngine(backup_path=out_archive)
    try:
        manifest_fast = r.load_manifest(extract_files=False)
        assert len(manifest_fast.configs) == 3

        res = r.execute_restore(
            RestoreOptions(
                target_home=str(target_home),
                selected_packages=[],
                selected_configs=manifest_fast.configs,
                selected_custom_paths=[],
                install_packages=False,
            )
        )
        assert len(res["restored_configs"]) == 3
        restored_link = target_home / ".config" / "systemd" / "user" / "default.target.wants" / "pipewire.service"
        assert restored_link.is_symlink()
        assert os.readlink(restored_link) == "/usr/lib/systemd/user/pipewire.service"
        # Kırık SingletonSocket çıkarılırken filtrelenmiş olmalı
        assert not (target_home / ".config" / "MyElectronApp" / "SingletonSocket").exists()
        assert not (target_home / ".config" / "MyElectronApp" / "SingletonSocket").is_symlink()
    finally:
        r.cleanup()



