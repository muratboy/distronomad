"""
DistroNomad - Yayın Öncesi W3a-2 Testleri: Motor Modülleri i18n & Append Düzeltmesi.
Kapsam:
  - backup.py, restore.py, verify.py, archive.py, crypto.py, automation.py, hardware.py,
    diff_utils.py ve packagemap.py içindeki kullanıcıya dönük metinlerin i18n.t() üzerinden TR ve EN olması.
  - Metinlerin çağrı anındaki aktif dile göre dinamik üretilmesi.
  - EK MOTOR DÜZELTMESİ: BackupEngine append modunda okunamayan/atlanan dosyaların
    skipped_files listesine girmesi ve manifest'e kaydedilmesi.
"""

from __future__ import annotations
import os
import stat
import json
from pathlib import Path
import pytest

from distronomad.i18n import (
    t,
    set_current_language,
    get_current_language,
    TRANSLATIONS,
)
from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    CustomPathItem,
    RestoreOptions,
    BackupManifest,
)
from distronomad.backup import BackupEngine, safe_copytree
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.verify import verify_backup
from distronomad.archive import (
    detect_backup_format,
    extract_tar_archive,
    open_archive_stream,
)
from distronomad.crypto import encrypt_archive, decrypt_archive
from distronomad.diff_utils import generate_unified_diff, compute_file_diff
from distronomad.automation import enable_timer, disable_timer, get_timer_status
from distronomad.hardware import HardwareProfile, is_hardware_profile_different
from distronomad.packagemap import get_install_command


# ===========================================================================
# 1. EK MOTOR DÜZELTMESİ: Append Modunda Atlanan / Okunamayan Dosyalar
# ===========================================================================
def test_append_mode_records_skipped_unreadable_files(tmp_path):
    """Var olan bir yedeğe append yapılırken okunamayan dosyaların engine.skipped_files
    listesine ve manifest.json dosyasına eksiksiz yazıldığını doğrular."""
    backup_dir = tmp_path / "initial_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Başlangıç manifesti
    sys_info = SystemInfo(distro_id="arch", home_dir=str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    manifest = BackupManifest(
        created_at="2026-11-01T10:00:00",
        version="1.0.0",
        system_info=sys_info,
        packages=[PackageItem(name="git", manager="pacman")],
        configs=[],
        custom_paths=[],
        archive_format="dir",
        total_bytes=100,
        skipped_files=[]
    )
    with open(backup_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f)

    # Okunabilir dosya ve okunamayan dosya hazırla
    readable_file = tmp_path / "home" / "readable.txt"
    readable_file.write_text("okunabilir veri", encoding="utf-8")

    unreadable_file = tmp_path / "home" / "unreadable.txt"
    unreadable_file.write_text("gizli veri", encoding="utf-8")
    unreadable_file.chmod(0o000)

    try:
        custom_items = [
            CustomPathItem(path=str(readable_file)),
            CustomPathItem(path=str(unreadable_file)),
        ]

        engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=[],
            custom_paths=custom_items,
            output_path=backup_dir,
            archive_format="dir",
            backup_mode="append"
        )
        engine.run()

        # Okunamayan dosya engine.skipped_files listesine girmiş olmalı
        skipped_paths = [sf["path"] for sf in engine.skipped_files]
        assert str(unreadable_file) in skipped_paths, f"Okunamayan dosya skipped_files içinde yok: {skipped_paths}"

        # Manifest içinde de skipped_files alanında yer almalı
        with open(backup_dir / "manifest.json", "r", encoding="utf-8") as f:
            updated_manifest = json.load(f)
        manifest_skipped = [sf["path"] for sf in updated_manifest.get("skipped_files", [])]
        assert str(unreadable_file) in manifest_skipped, f"Okunamayan dosya manifest içinde yok: {manifest_skipped}"

    finally:
        # Temizlik için izinleri geri ver
        unreadable_file.chmod(0o644)


# ===========================================================================
# 2. Dinamik Dil Değişimi: Çağrı Anındaki Dile Göre Üretim
# ===========================================================================
def test_dynamic_runtime_language_switching():
    """Motor hata ve ilerleme mesajlarının modül yüklenirken sabitlenmediğini,
    çağrı anındaki set_current_language() seçimine göre dinamik üretildiğini doğrular."""
    set_current_language("tr", persist=False)
    msg_tr = t("archive_err_zstd_required")
    assert "Zstandard arşivi açmak için" in msg_tr

    set_current_language("en", persist=False)
    msg_en = t("archive_err_zstd_required")
    assert "The zstandard library or tar/zstd is required" in msg_en

    # diff_utils etiketleri
    set_current_language("tr", persist=False)
    diff_text_tr, _, _ = generate_unified_diff(None, None)
    assert "Dosya bulunamadı." in diff_text_tr

    set_current_language("en", persist=False)
    diff_text_en, _, _ = generate_unified_diff(None, None)
    assert "File not found." in diff_text_en

    # conftest sabitlemesi Türkçe'ye geri döndürür
    set_current_language("tr", persist=False)


# ===========================================================================
# 3. archive.py ve crypto.py İngilizce Çıktı Testleri
# ===========================================================================
def test_archive_and_crypto_english_messages(tmp_path):
    """archive.py ve crypto.py modüllerinin İngilizce modunda beklenen hata mesajlarını ürettiğini doğrular."""
    set_current_language("en", persist=False)
    try:
        # archive.py: Tanınmayan format
        with pytest.raises(ValueError) as excinfo:
            detect_backup_format(tmp_path / "invalid.unknown_ext_xyz")
        assert "Unrecognized or unsupported backup format" in str(excinfo.value)

        # archive.py: Eksik parola
        nonexistent = tmp_path / "test.tar.zst"
        with pytest.raises(FileNotFoundError) as excinfo:
            with open_archive_stream(nonexistent):
                pass
        assert "Archive file not found" in str(excinfo.value)

        # crypto.py: Eksik parola
        with pytest.raises(ValueError) as excinfo:
            encrypt_archive(tmp_path / "some.tar", None, "")
        assert "A password must be specified for encryption." in str(excinfo.value)

        with pytest.raises(ValueError) as excinfo:
            decrypt_archive(tmp_path / "some.enc", None, "")
        assert "A password must be entered for decryption." in str(excinfo.value)

        # crypto.py: Bulunamayan dosya
        with pytest.raises(FileNotFoundError) as excinfo:
            encrypt_archive(tmp_path / "nonexistent.tar", None, "pass123")
        assert "File to encrypt not found" in str(excinfo.value)
    finally:
        set_current_language("tr", persist=False)


# ===========================================================================
# 4. verify.py İngilizce Çıktı Testleri
# ===========================================================================
def test_verify_english_messages(tmp_path):
    """verify.py modülünün İngilizce modunda doğrulama ve hata mesajlarını doğru ürettiğini test eder."""
    set_current_language("en", persist=False)
    try:
        # Bulunamayan yedek
        with pytest.raises(FileNotFoundError) as excinfo:
            verify_backup(tmp_path / "does_not_exist.tar.zst")
        assert "Backup to verify not found" in str(excinfo.value)

        # Manifest içermeyen dizin
        empty_dir = tmp_path / "empty_backup"
        empty_dir.mkdir()
        res = verify_backup(empty_dir)
        assert res.is_valid is False
        assert "'manifest.json' not found in directory!" in (res.error_message or "")

        # İlerleme callback mesajı İngilizce olmalı
        progress_msgs = []
        dummy_file = empty_dir / "file.txt"
        dummy_file.write_text("hello", encoding="utf-8")
        manifest = BackupManifest(
            created_at="2026-11-01T12:00:00",
            version="1.0.0",
            system_info=SystemInfo(distro_id="arch", home_dir=str(tmp_path / "home")),
            file_hashes={"file.txt": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"},
        )
        with open(empty_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f)

        res_ok = verify_backup(empty_dir, progress_callback=lambda p: progress_msgs.append(p.message))
        assert any("Verifying:" in m for m in progress_msgs)
    finally:
        set_current_language("tr", persist=False)


# ===========================================================================
# 5. diff_utils.py İngilizce Çıktı Testleri
# ===========================================================================
def test_diff_utils_english_messages(tmp_path):
    """diff_utils.py modülünün İngilizce modunda diff metinlerini ve etiketlerini test eder."""
    set_current_language("en", persist=False)
    try:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("line1\n", encoding="utf-8")
        f2.write_text("line1\n", encoding="utf-8")

        diff_identical, _, _ = generate_unified_diff(f1, f2)
        assert "Files are identical (No difference)." in diff_identical

        # İkili dosya
        b1 = tmp_path / "bin.dat"
        b1.write_bytes(b"\x00\x01\x02\x03")
        diff_bin, _, _ = generate_unified_diff(b1, f2)
        assert "[Binary File - Text diff cannot be displayed]" in diff_bin

        # compute_file_diff fonksiyonu ile test
        item = {
            "status": "identical",
            "host_path": str(f1),
            "backup_path": str(f2),
        }
        res_text, _, _ = compute_file_diff(item)
        assert "Files are identical (No difference)." in res_text
    finally:
        set_current_language("tr", persist=False)


# ===========================================================================
# 6. automation.py & hardware.py İngilizce Çıktı Testleri
# ===========================================================================
def test_automation_and_hardware_english_messages(tmp_path):
    """automation.py ve hardware.py modüllerinin İngilizce modundaki çıktılarını test eder."""
    set_current_language("en", persist=False)
    try:
        # automation.py
        status = get_timer_status(systemd_dir=tmp_path / "systemd")
        assert status["interval"] == "Disabled (Off)"

        dis = disable_timer(systemd_dir=tmp_path / "systemd")
        assert "Automatic scheduler successfully disabled." in dis["message"]

        en_res = enable_timer(systemd_dir=tmp_path / "systemd", backup_output_dir=tmp_path / "out", incremental=True)
        service_text = Path(en_res["service_path"]).read_text(encoding="utf-8")
        timer_text = Path(en_res["timer_path"]).read_text(encoding="utf-8")
        assert "Description=DistroNomad Automatic Incremental Backup" in service_text
        assert "Description=DistroNomad Scheduled Incremental Backup Timer" in timer_text

        # hardware.py
        live_hw = HardwareProfile(gpus=["NVIDIA RTX 4060"], cpu_model="Intel Core i7")
        old_hw = {"gpus": ["AMD Radeon RX 6700"], "cpu_model": "AMD Ryzen 7"}
        is_diff, diff_str = is_hardware_profile_different(live_hw, old_hw)
        assert is_diff is True
        assert "GPU:" in diff_str and "CPU:" in diff_str
    finally:
        set_current_language("tr", persist=False)


# ===========================================================================
# 7. packagemap.py İngilizce Açıklama Testleri
# ===========================================================================
def test_packagemap_install_command_descriptions_english():
    """get_install_command() açıklamalarının İngilizce modunda yerelleştirildiğini doğrular."""
    set_current_language("en", persist=False)
    try:
        _, desc_pacman = get_install_command("pacman", ["git"])
        assert desc_pacman == "Pacman installation"

        _, desc_apt = get_install_command("apt", ["git"])
        assert desc_apt == "Apt installation"

        _, desc_dnf = get_install_command("dnf", ["git"])
        assert desc_dnf == "DNF installation"

        _, desc_zypper = get_install_command("zypper", ["git"])
        assert desc_zypper == "Zypper installation"

        _, desc_flatpak = get_install_command("flatpak", ["org.videolan.VLC"])
        assert desc_flatpak == "Flatpak installation"

        _, desc_aur = get_install_command("yay", ["yay-git"])
        assert desc_aur == "yay (AUR) installation"
    finally:
        set_current_language("tr", persist=False)


# ===========================================================================
# 8. restore.py & backup.py İngilizce Bildirim ve Hata Testleri
# ===========================================================================
def test_restore_and_backup_english_messages(tmp_path):
    """restore.py ve backup.py modüllerinin İngilizce modundaki hata ve bildirimlerini test eder."""
    set_current_language("en", persist=False)
    try:
        # restore.py: Bulunamayan işlem günlüğü
        with pytest.raises(FileNotFoundError) as excinfo:
            rollback_restore(transaction_file=tmp_path / "nonexistent_tx.json")
        assert "Transaction log not found for rollback operation" in str(excinfo.value)

        # restore.py: Bulunamayan yedek arşivi
        sys_info = SystemInfo(distro_id="arch", home_dir=str(tmp_path / "home"))
        restorer = RestoreEngine(
            backup_path=tmp_path / "nonexistent_backup.tar.zst"
        )
        with pytest.raises(FileNotFoundError) as excinfo:
            restorer.ensure_extracted()
        assert "Backup file or directory not found" in str(excinfo.value)

        # backup.py: dir modunda şifreleme hatası
        engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=[],
            custom_paths=[],
            output_path=tmp_path / "out_dir",
            archive_format="dir",
            password="secret_password"
        )
        with pytest.raises(ValueError) as excinfo:
            engine.run()
        assert "Encryption (AES-256) is only supported for archive formats" in str(excinfo.value)
    finally:
        set_current_language("tr", persist=False)
