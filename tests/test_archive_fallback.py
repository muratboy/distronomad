"""
Python 3.9 - 3.11 eski Python yolu (tarfile.tar_filter özniteliğinin bulunmadığı durum)
için safe_tar_extract ve arşiv güvenlik testleri.
"""

import io
import errno
import tarfile
from pathlib import Path
import pytest
import zstandard as zstd

from distronomad.archive import safe_tar_extract, extract_tar_archive


def test_safe_tar_extract_legacy_fallback_stream(tmp_path, monkeypatch):
    """
    tarfile.tar_filter bulunmayan eski Python fallback yolunda:
    zstd ile sıkıştırılmış ve akış kipinde (r|*) açılan arşivdeki:
    - Normal dosyaların başarıyla açıldığını,
    - ../ ile dizin dışına çıkan girdilerin atlandığını,
    - Mutlak yollu girdilerin atlandığını,
    - Hedef dışını gösteren sembolik bağın altına yazmaya çalışan girdilerin atlandığını,
    - Hedef dizin dışına hiçbir dosya yazılmadığını
    uçtan uca doğrular.
    """
    # 1. monkeypatch ile tarfile.tar_filter özniteliğini kaldır
    monkeypatch.delattr(tarfile, "tar_filter", raising=False)
    assert not hasattr(tarfile, "tar_filter"), "tarfile.tar_filter özniteliği kaldırılmış olmalıdır"

    target_dir = tmp_path / "extract_target"
    target_dir.mkdir()

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    outside_traversal_dest = tmp_path / "outside_file.txt"
    evil_abs_dest = outside_dir / "evil_abs.txt"
    pwned_dest = outside_dir / "under_symlink.txt"

    # 2. Test arşivini zstd ile sıkıştırılmış olarak oluştur
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        # Normal dosya 1
        ti_norm1 = tarfile.TarInfo("normal_file1.txt")
        d_norm1 = b"Hello from normal file 1"
        ti_norm1.size = len(d_norm1)
        tar.addfile(ti_norm1, io.BytesIO(d_norm1))

        # ../ ile hedef dışına çıkmaya çalışan girdi
        ti_trav = tarfile.TarInfo("../outside_file.txt")
        d_trav = b"Malicious traversal attempt"
        ti_trav.size = len(d_trav)
        tar.addfile(ti_trav, io.BytesIO(d_trav))

        # Mutlak yollu girdi (hedef dışındaki outside_dir içine yazmaya çalışır)
        ti_abs = tarfile.TarInfo(str(evil_abs_dest))
        d_abs = b"Malicious absolute path attempt"
        ti_abs.size = len(d_abs)
        tar.addfile(ti_abs, io.BytesIO(d_abs))

        # Hedef dışını gösteren bir sembolik bağ
        ti_sym = tarfile.TarInfo("symlink_to_outside")
        ti_sym.type = tarfile.SYMTYPE
        ti_sym.linkname = str(outside_dir)
        tar.addfile(ti_sym)

        # O sembolik bağın altına dosya yazmaya çalışan kötü niyetli girdi
        ti_under = tarfile.TarInfo("symlink_to_outside/under_symlink.txt")
        d_under = b"Malicious write under symlink attempt"
        ti_under.size = len(d_under)
        tar.addfile(ti_under, io.BytesIO(d_under))

        # Normal dosya 2 (alt dizinde)
        ti_norm2 = tarfile.TarInfo("subdir/normal_file2.txt")
        d_norm2 = b"Hello from normal file 2 in subdir"
        ti_norm2.size = len(d_norm2)
        tar.addfile(ti_norm2, io.BytesIO(d_norm2))

    raw_tar_bytes = tar_buf.getvalue()

    # zstd ile sıkıştır
    cctx = zstd.ZstdCompressor(level=3)
    compressed_bytes = cctx.compress(raw_tar_bytes)

    archive_path = tmp_path / "test_fallback_backup.tar.zst"
    archive_path.write_bytes(compressed_bytes)

    # 3. Arşivi akış kipinde (r|*) aç ve safe_tar_extract ile test et
    dctx = zstd.ZstdDecompressor()
    with open(archive_path, "rb") as f_in:
        with dctx.stream_reader(f_in) as reader:
            with tarfile.open(fileobj=reader, mode="r|*") as tar_stream:
                safe_tar_extract(tar_stream, target_dir)

    # 4. Doğrulamalar:
    # A) Normal dosyalar eksiksiz ve doğru içerikle açılmalı
    assert (target_dir / "normal_file1.txt").exists(), "normal_file1.txt çıkarılmış olmalıdır"
    assert (target_dir / "normal_file1.txt").read_bytes() == b"Hello from normal file 1"

    assert (target_dir / "subdir" / "normal_file2.txt").exists(), "subdir/normal_file2.txt çıkarılmış olmalıdır"
    assert (target_dir / "subdir" / "normal_file2.txt").read_bytes() == b"Hello from normal file 2 in subdir"

    # B) Kötü niyetli dosyalar hedef dışına HİÇBİR ŞEY yazamamış olmalı
    assert not outside_traversal_dest.exists(), "../ traversal dosyası hedef dışına yazılmamış olmalıdır"
    assert not evil_abs_dest.exists(), "Mutlak yollu dosya hedef dışına yazılmamış olmalıdır"
    assert not pwned_dest.exists(), "Sembolik bağ altındaki dosya hedef dışına yazılmamış olmalıdır"
    assert list(outside_dir.iterdir()) == [], "Hedef dışındaki dizin tamamen boş kalmalıdır"

    # C) extract_tar_archive yardımcı fonksiyonu ile de uçtan uca çalışabilmeli
    target_dir2 = tmp_path / "extract_target_2"
    extract_tar_archive(archive_path, target_dir2)
    assert (target_dir2 / "normal_file1.txt").read_bytes() == b"Hello from normal file 1"
    assert (target_dir2 / "subdir" / "normal_file2.txt").read_bytes() == b"Hello from normal file 2 in subdir"
    assert not (outside_dir / "evil_abs.txt").exists()
    assert not (outside_dir / "under_symlink.txt").exists()


def test_safe_tar_extract_legacy_fallback_raises_oserror_enospc(tmp_path, monkeypatch):
    """
    tarfile.tar_filter bulunmayan eski Python fallback yolunda:
    tar.extract sırasında disk dolu (errno.ENOSPC) gibi bir OSError oluştuğunda
    hatanın yutulmadan yukarı iletildiğini doğrular.
    """
    monkeypatch.delattr(tarfile, "tar_filter", raising=False)
    assert not hasattr(tarfile, "tar_filter")

    target_dir = tmp_path / "extract_target"
    target_dir.mkdir()

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        ti = tarfile.TarInfo("test_file.txt")
        data = b"Sample content"
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    tar_buf.seek(0)

    def mock_extract(self, *args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(tarfile.TarFile, "extract", mock_extract)

    with tarfile.open(fileobj=tar_buf, mode="r:*") as tar:
        with pytest.raises(OSError) as exc_info:
            safe_tar_extract(tar, target_dir)
        assert exc_info.value.errno == errno.ENOSPC

