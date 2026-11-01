import os
import stat
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem
from distronomad.crypto import encrypt_archive, decrypt_archive, is_encrypted_file
from distronomad.backup import BackupEngine, read_manifest_from_backup
from distronomad.restore import RestoreEngine


@pytest.fixture
def dummy_system_info(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return SystemInfo(
        distro_id="arch",
        distro_like="arch",
        distro_name="Arch Linux",
        distro_version="rolling",
        kernel_version="6.1.0",
        hostname="testhost",
        username="testuser",
        home_dir=str(home)
    )


def test_crypto_password_not_in_argv(tmp_path):
    """(1) encrypt_archive ve decrypt_archive subprocess argv içinde parolayı içermemelidir."""
    src_file = tmp_path / "secret.txt"
    src_file.write_text("gizli veri", encoding="utf-8")
    enc_file = tmp_path / "secret.txt.enc"
    dec_file = tmp_path / "secret_dec.txt"
    pwd = "MySuperSecretPassword_12345!"

    captured_cmds = []
    real_run = subprocess.run

    def spy_run(cmd, *args, **kwargs):
        captured_cmds.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    with patch("distronomad.crypto.subprocess.run", side_effect=spy_run):
        encrypt_archive(src_file, enc_file, password=pwd)
        decrypt_archive(enc_file, dec_file, password=pwd)

    assert len(captured_cmds) >= 2
    for cmd in captured_cmds:
        for arg in cmd:
            assert pwd not in arg, f"Parola argv içinde bulundu: {arg}"

    assert dec_file.read_text(encoding="utf-8") == "gizli veri"


def test_encrypted_manifest_read_streaming_and_base_password(dummy_system_info, tmp_path):
    """(2) Şifreli yedekten manifest akışla (None dönmeden) okunmalı; taban yedek parola almalı."""
    sample_file = tmp_path / "home" / "test.txt"
    sample_file.write_text("veri", encoding="utf-8")

    enc_backup = tmp_path / "backup.tar.zst"
    engine = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=enc_backup,
        archive_format="tar.zst",
        backup_mode="full",
        password="secretpassword"
    )
    final_enc = engine.run()
    assert is_encrypted_file(final_enc)

    # 1. read_manifest_from_backup şifreli dosyada None dönmemeli
    manifest = read_manifest_from_backup(final_enc, password="secretpassword")
    assert manifest is not None
    assert manifest.system_info.distro_id == "arch"

    # 2. RestoreEngine(extract_files=False) tüm arşivi açmadan manifest okumalı
    restore_eng = RestoreEngine(backup_path=final_enc, password="secretpassword")
    loaded_manifest = restore_eng.load_manifest(extract_files=False)
    assert loaded_manifest is not None
    assert restore_eng.temp_extracted_dir is None  # diske açılmamış olmalı

    # 3. Artımlı yedeklemede taban şifreli yedek okunabilmeli
    inc_backup = tmp_path / "backup_inc.tar.zst"
    inc_engine = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=inc_backup,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=final_enc,
        password="secretpassword"
    )
    final_inc = inc_engine.run()
    assert inc_engine.backup_mode == "incremental"  # Şifreli taban okunamayınca 'full'e düşerdi


def test_cleanup_on_success_error_and_app_exit(dummy_system_info, tmp_path, monkeypatch):
    """(3) ui/app.py cleanup() çağrısı ve backup.py append hata/başarı durumlarında /tmp temizliği."""
    from distronomad.ui.app import DistroNomadApp

    # UI cleanup testi
    app = DistroNomadApp()
    mock_restore_engine = MagicMock()
    app.current_restore_engine = mock_restore_engine

    # on_unmount sırasında cleanup çağrılmalı
    app.on_unmount()
    mock_restore_engine.cleanup.assert_called()

    # backup.py append hatasında temp_dec_path kalmamalı
    sample_file = tmp_path / "home" / "test.txt"
    sample_file.write_text("veri", encoding="utf-8")
    base_backup = tmp_path / "base.tar.zst"
    engine = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=base_backup,
        archive_format="tar.zst",
        backup_mode="full",
        password="pwd"
    )
    final_enc = engine.run()

    created_dec_files = []
    real_decrypt = __import__("distronomad.backup", fromlist=["decrypt_archive"]).decrypt_archive

    def spy_decrypt(src, dst, pwd):
        created_dec_files.append(Path(dst))
        return real_decrypt(src, dst, pwd)

    monkeypatch.setattr("distronomad.backup.decrypt_archive", spy_decrypt)

    # _extract_existing_archive sırasında hata fırlat
    def failing_extract(*args, **kwargs):
        raise RuntimeError("Simule edilmiş açma hatası!")

    monkeypatch.setattr(BackupEngine, "_extract_existing_archive", failing_extract)

    append_engine = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=final_enc,
        archive_format="tar.zst",
        backup_mode="append",
        password="pwd"
    )

    with pytest.raises(RuntimeError, match="Simule edilmiş açma hatası!"):
        append_engine.run()

    # Oluşan deşifre edilmiş geçici dosyanın silindiğini doğrula
    assert len(created_dec_files) == 1
    assert not created_dec_files[0].exists(), "Deşifre edilmiş geçici dosya hata durumunda silinmedi!"


def test_backup_and_dir_permissions(dummy_system_info, tmp_path):
    """(4) Oluşturulan arşiv dosyaları 0600, dir kök dizini 0700 izinli olmalıdır."""
    sample_file = tmp_path / "home" / "id_rsa"
    sample_file.write_text("ssh-rsa AAAAB3NzaC1yc2E...", encoding="utf-8")

    # 1. tar.zst arşivi
    out_zst = tmp_path / "backup.tar.zst"
    eng_zst = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=out_zst,
        archive_format="tar.zst"
    )
    res_zst = eng_zst.run()
    assert (res_zst.stat().st_mode & 0o777) == 0o600

    # 2. Şifreli arşiv (.enc)
    out_enc = tmp_path / "backup_enc.tar.zst"
    eng_enc = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=out_enc,
        archive_format="tar.zst",
        password="testpassword"
    )
    res_enc = eng_enc.run()
    assert (res_enc.stat().st_mode & 0o777) == 0o600

    # 3. dir formatı kök dizini
    out_dir = tmp_path / "backup_dir"
    eng_dir = BackupEngine(
        system_info=dummy_system_info,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(sample_file), is_selected=True)],
        output_path=out_dir,
        archive_format="dir"
    )
    res_dir = eng_dir.run()
    assert (res_dir.stat().st_mode & 0o777) == 0o700
