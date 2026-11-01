"""
DistroNomad - Ortak Arşiv Modülü (distronomad.archive) Testleri.
"""

import os
import tarfile
import pytest
from pathlib import Path

from distronomad.archive import (
    RUNTIME_SOCKET_NAMES,
    PrefixReader,
    is_safe_tar_path,
    tar_extract_filter,
    safe_tar_extract,
    detect_backup_format,
    extract_tar_archive,
    open_archive_stream,
    read_manifest_from_backup,
)
from distronomad.crypto import encrypt_archive


def test_runtime_sockets_and_filter(tmp_path):
    """SingletonSocket, FIFO ve aygıt dosyalarının elendiğini doğrular."""
    dest = tmp_path / "dest"
    dest.mkdir()

    # Soket ve aygıt TarInfo simülasyonu
    for sock_name in RUNTIME_SOCKET_NAMES:
        ti = tarfile.TarInfo(f".config/myapp/{sock_name}")
        assert tar_extract_filter(ti, str(dest)) is None

    ti_fifo = tarfile.TarInfo("myfifo")
    ti_fifo.type = tarfile.FIFOTYPE
    assert tar_extract_filter(ti_fifo, str(dest)) is None

    ti_chr = tarfile.TarInfo("mychr")
    ti_chr.type = tarfile.CHRTYPE
    assert tar_extract_filter(ti_chr, str(dest)) is None


def test_tar_path_traversal_protection(tmp_path):
    """Hedef dışına çıkan dizin/dosya yollarının elendiğini doğrular."""
    dest = tmp_path / "sandbox"
    dest.mkdir()

    assert is_safe_tar_path(dest, "normal/file.txt") is True
    assert is_safe_tar_path(dest, "../../etc/passwd") is False
    assert is_safe_tar_path(dest, "/etc/passwd") is False


def test_detect_backup_format(tmp_path):
    """Format tespit fonksiyonunun tüm formatları ve .enc türevlerini doğru tanıdığını doğrular."""
    d = tmp_path / "some_dir"
    d.mkdir()
    assert detect_backup_format(d) == "dir"

    assert detect_backup_format("backup.tar.zst") == "tar.zst"
    assert detect_backup_format("backup.tar.gz") == "tar.gz"
    assert detect_backup_format("backup.tar") == "tar"
    assert detect_backup_format("backup.tar.zst.enc") == "tar.zst"
    assert detect_backup_format("backup.tar.gz.enc") == "tar.gz"
    assert detect_backup_format("backup.tar.enc") == "tar"
    assert detect_backup_format("backup.enc") == "enc"

    with pytest.raises(ValueError):
        detect_backup_format("unknown.xyz")


def test_stream_reading_and_manifest(tmp_path):
    """Arşiv akış açma ve manifest okumanın tar.gz ve şifreli .enc ile çalıştığını doğrular."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manifest_file = data_dir / "manifest.json"
    manifest_file.write_text('{"version": "1.0.0", "backup_type": "full", "system_info": {"distro_id": "test", "distro_name": "Test"}}', encoding="utf-8")

    archive_tar = tmp_path / "test.tar.gz"
    with tarfile.open(archive_tar, "w:gz") as tar:
        tar.add(str(manifest_file), arcname="manifest.json")

    # Düz tar.gz manifest okuma
    m1 = read_manifest_from_backup(archive_tar)
    assert m1 is not None
    assert m1.version == "1.0.0"
    assert m1.backup_type == "full"

    # Şifreli .enc manifest okuma
    enc_path = encrypt_archive(archive_tar, tmp_path / "test.tar.gz.enc", "TestPass123!")
    m2 = read_manifest_from_backup(enc_path, password="TestPass123!")
    assert m2 is not None
    assert m2.version == "1.0.0"

    # Yanlış parola None dönmeli
    assert read_manifest_from_backup(enc_path, password="WrongPassword") is None

    # open_archive_stream yanlış parolada ValueError fırlatmalı
    with pytest.raises(ValueError, match="Hatalı parola"):
        with open_archive_stream(enc_path, password="WrongPassword") as _:
            pass


def test_extract_tar_archive_absolute_symlink(tmp_path):
    """Mutlak sembolik bağ içeren arşivin AbsoluteLinkError vermeden çıkarıldığını doğrular."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("content", encoding="utf-8")
    link_path = src / "abs_link"
    try:
        os.symlink("/usr/bin/env", link_path)
    except OSError:
        pytest.skip("Symlink oluşturulamadı")

    archive_path = tmp_path / "archive_with_link.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(str(src / "file.txt"), arcname="file.txt")
        tar.add(str(link_path), arcname="abs_link")

    out_dir = tmp_path / "extracted"
    extract_tar_archive(archive_path, out_dir)

    assert (out_dir / "file.txt").exists()
    assert (out_dir / "abs_link").is_symlink()
    assert os.readlink(out_dir / "abs_link") == "/usr/bin/env"
