"""
DistroNomad - Ortak Arşiv Yönetimi ve Akış Motoru (Archive & Stream Engine).
.tar.zst, .tar.gz, .tar ve AES-256 şifreli (.enc) arşivlerin tespiti, güvenli
çıkarılması, akış olarak taranması ve manifest okunmasını tek merkezde yönetir.
"""

from __future__ import annotations
import os
import io
import json
import gzip
import shutil
import tarfile
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union, Any, Iterator

from distronomad.models import BackupManifest
from distronomad.crypto import is_encrypted_file
from distronomad.i18n import t


RUNTIME_SOCKET_NAMES = frozenset({
    "SingletonSocket",
    "SingletonLock",
    "SingletonCookie",
    "RunningChromeVersion",
})


class PrefixReader(io.RawIOBase):
    """Önceden okunmuş bir ön eki ve ardından gelen ham akışı birleştiren okuyucu."""

    def __init__(self, prefix: bytes, stream: Any):
        self.prefix = prefix
        self.stream = stream
        self.pos = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:
        n = 0
        if self.pos < len(self.prefix):
            chunk = self.prefix[self.pos : self.pos + len(b)]
            b[: len(chunk)] = chunk
            self.pos += len(chunk)
            n += len(chunk)
            if n == len(b):
                return n
        m = self.stream.readinto(memoryview(b)[n:])
        return n + (m or 0)


def is_safe_tar_path(dest_path: Union[Path, str], member_name: str) -> bool:
    """Hedef dizin dışına çıkış (path traversal) girişimlerini denetler."""
    dest_dir = Path(dest_path).resolve()
    target = (dest_dir / member_name).resolve()
    try:
        return target == dest_dir or target.is_relative_to(dest_dir)
    except (ValueError, AttributeError):
        try:
            return os.path.commonpath([str(dest_dir), str(target)]) == str(dest_dir)
        except ValueError:
            return False


def tar_extract_filter(member: tarfile.TarInfo, dest_path: str) -> Optional[tarfile.TarInfo]:
    """
    Python 3.12+/3.14+ tarfile filtresi: kırık çalışma zamanı soketlerini ve aygıt
    dosyalarını atlar, mutlak sembolik bağları korurken hedef dışına çıkışları
    engeller, sembolik bağları ve üzerine yazmaları güvenle yönetir.
    """
    base_name = os.path.basename(member.name)
    if base_name in RUNTIME_SOCKET_NAMES:
        return None
    if member.isfifo() or member.ischr() or member.isblk():
        return None
    if hasattr(tarfile, "tar_filter"):
        filtered = tarfile.tar_filter(member, dest_path)
    else:
        if not is_safe_tar_path(dest_path, member.name):
            return None
        filtered = member

    if filtered is not None:
        target = Path(dest_path) / filtered.name
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.exists():
            if target.is_dir() and not filtered.isdir():
                shutil.rmtree(target, ignore_errors=True)
            elif not target.is_dir() and (filtered.isdir() or filtered.issym() or filtered.islnk()):
                target.unlink(missing_ok=True)

    return filtered


def safe_tar_extract(tar: tarfile.TarFile, target_dir: Union[Path, str]) -> None:
    """Arşivi Python 3.9 - 3.14+ sürümleriyle tam uyumlu ve güvenli şekilde hedefe açar."""
    target_path = Path(target_dir)
    if hasattr(tarfile, "tar_filter"):
        tar.extractall(path=target_path, filter=tar_extract_filter)
    else:
        dest_dir = target_path.resolve()
        for m in tar:
            if os.path.basename(m.name) in RUNTIME_SOCKET_NAMES:
                continue
            if m.isfifo() or m.ischr() or m.isblk():
                continue
            if not is_safe_tar_path(dest_dir, m.name):
                continue
            target = target_path / m.name
            if target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.exists():
                if target.is_dir() and not m.isdir():
                    shutil.rmtree(target, ignore_errors=True)
                elif not target.is_dir() and (m.isdir() or m.issym() or m.islnk()):
                    target.unlink(missing_ok=True)
            try:
                tar.extract(m, path=target_path, filter=lambda member, path: member)
            except TypeError:
                tar.extract(m, path=target_path)


def detect_backup_format(path: Union[Path, str]) -> str:
    """
    Hedef yolun gerçek durumundan veya uzantısından formatını tespit eder.
    Döndürür: 'dir', 'tar.zst', 'tar.gz', 'tar', 'enc'
    """
    p = Path(path)
    if p.exists() and p.is_dir():
        return "dir"

    if p.exists() and p.is_file():
        try:
            with open(p, "rb") as f:
                header = f.read(512)
            if header.startswith(b"\x28\xb5\x2f\xfd"):
                return "tar.zst"
            if header.startswith(b"\x1f\x8b"):
                return "tar.gz"
            if tarfile.is_tarfile(p):
                return "tar"
        except Exception:
            pass

    str_p = str(p).lower()
    if str_p.endswith(".enc"):
        inner = str_p[:-4]
        if inner.endswith(".tar.zst"):
            return "tar.zst"
        if inner.endswith(".tar.gz"):
            return "tar.gz"
        if inner.endswith(".tar"):
            return "tar"
        return "enc"

    if str_p.endswith(".tar.zst"):
        return "tar.zst"
    if str_p.endswith(".tar.gz"):
        return "tar.gz"
    if str_p.endswith(".tar"):
        return "tar"

    raise ValueError(t("archive_err_unknown_format", path=str(path)))


def extract_tar_archive(
    archive_file: Union[Path, str],
    target_dir: Union[Path, str],
    archive_format: Optional[str] = None
) -> None:
    """
    tar.zst, tar.gz veya tar arşivini ara dosya oluşturmadan hedef dizine güvenli şekilde açar.
    zstandard paketi yoksa tar/zstd CLI fallback'i kullanılır.
    """
    p = Path(archive_file)
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    str_path = str(p).lower()
    fmt = (archive_format or "").lower()

    is_zstd = False
    try:
        with open(p, "rb") as f:
            if f.read(4) == b"\x28\xb5\x2f\xfd":
                is_zstd = True
    except Exception:
        pass

    if fmt == "tar.zst" or str_path.endswith(".tar.zst") or is_zstd:
        try:
            import zstandard as zstd
            dctx = zstd.ZstdDecompressor()
            with open(p, "rb") as f_in:
                with dctx.stream_reader(f_in) as reader:
                    with tarfile.open(fileobj=reader, mode="r|*") as tar:
                        safe_tar_extract(tar, target)
            return
        except ImportError:
            if shutil.which("tar") and shutil.which("zstd"):
                subprocess.run(
                    ["tar", "--zstd", "--overwrite", "-xf", str(p), "-C", str(target)],
                    check=True
                )
                return
            raise RuntimeError(t("archive_err_zstd_required"))

    elif fmt in ("tar.gz", "tar") or str_path.endswith((".tar.gz", ".tar")) or tarfile.is_tarfile(p):
        mode = "r:gz" if (fmt == "tar.gz" or str_path.endswith(".tar.gz")) else "r:*"
        with tarfile.open(p, mode) as tar:
            safe_tar_extract(tar, target)
    else:
        raise ValueError(t("archive_err_unsupported_format", path=str(p)))


@contextmanager
def open_archive_stream(
    archive_path: Union[Path, str],
    password: Optional[str] = None
) -> Iterator[Any]:
    """
    Arşiv dosyasını (.tar.zst, .tar.gz, .tar veya şifreli .enc) akış (stream) olarak açar.
    Şifreli arşivlerde parola stdin üzerinden aktarılır (argv'de görünmez) ve
    düz metin diske yazılmaz.
    """
    p = Path(archive_path)
    if not p.exists():
        raise FileNotFoundError(t("archive_err_file_not_found", path=str(p)))

    proc: Optional[subprocess.Popen] = None
    file_handle: Optional[io.BufferedReader] = None
    decomp_stream: Any = None
    raw_stream: Any = None

    try:
        if is_encrypted_file(p):
            if not password:
                raise ValueError(t("archive_err_password_required"))
            if not shutil.which("openssl"):
                raise RuntimeError(t("archive_err_openssl_required"))

            cmd = [
                "openssl", "enc", "-d", "-aes-256-cbc",
                "-pbkdf2", "-iter", "100000",
                "-in", str(p),
                "-pass", "stdin"
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            try:
                if proc.stdin:
                    proc.stdin.write(password.encode("utf-8") + b"\n")
                    proc.stdin.close()
            except Exception as e:
                raise ValueError(t("archive_err_decrypt_init_failed", error=str(e)))

            magic = proc.stdout.read(4) if proc.stdout else b""
            if not magic:
                proc.wait()
                err_msg = ""
                if proc.stderr:
                    err_msg = proc.stderr.read().decode("utf-8", errors="replace")
                if "bad decrypt" in err_msg.lower() or "error" in err_msg.lower():
                    raise ValueError(t("archive_err_bad_password_or_corrupt"))
                raise RuntimeError(t("archive_err_empty_decrypt_output", error=err_msg.strip()))

            raw_stream = io.BufferedReader(PrefixReader(magic, proc.stdout))
        else:
            file_handle = open(p, "rb")
            magic = file_handle.read(4)
            file_handle.seek(0)
            raw_stream = file_handle

        str_path = str(p).lower()
        is_zstd = (magic == b"\x28\xb5\x2f\xfd" or ".tar.zst" in str_path or ".zst" in str_path)
        is_gzip = (magic.startswith(b"\x1f\x8b") or ".tar.gz" in str_path or ".tgz" in str_path)

        if is_zstd:
            try:
                import zstandard as zstd
                dctx = zstd.ZstdDecompressor()
                decomp_stream = dctx.stream_reader(raw_stream)
            except ImportError:
                raise RuntimeError(t("archive_err_zstd_not_installed"))
        elif is_gzip:
            decomp_stream = gzip.GzipFile(fileobj=raw_stream, mode="rb")
        else:
            decomp_stream = raw_stream

        yield decomp_stream

    finally:
        if decomp_stream is not None and hasattr(decomp_stream, "close") and decomp_stream is not raw_stream:
            try:
                decomp_stream.close()
            except Exception:
                pass
        if file_handle:
            try:
                file_handle.close()
            except Exception:
                pass
        if proc:
            try:
                if proc.stdout:
                    proc.stdout.close()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            if proc.returncode is not None and proc.returncode != 0:
                err_text = ""
                try:
                    if proc.stderr:
                        err_text = proc.stderr.read().decode("utf-8", errors="replace").lower()
                except Exception:
                    pass
                if "bad decrypt" in err_text or "error" in err_text:
                    raise ValueError(t("archive_err_bad_password_or_corrupt"))


def read_manifest_from_backup(
    backup_path: Optional[Union[Path, str]],
    password: Optional[str] = None
) -> Optional[BackupManifest]:
    """Herhangi bir yedek arşivinden (.tar.zst, .tar.gz, .tar, şifreli .enc) veya dizininden manifest.json dosyasını okur."""
    if not backup_path:
        return None
    p = Path(backup_path)
    if not p.exists():
        return None

    if p.is_dir():
        manifest_file = p / "manifest.json"
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8", errors="surrogateescape") as f:
                    return BackupManifest.from_dict(json.load(f))
            except Exception:
                return None
        return None

    try:
        with open_archive_stream(p, password=password) as decomp_stream:
            with tarfile.open(fileobj=decomp_stream, mode="r|*") as tar:
                for member in tar:
                    norm_name = member.name.replace("\\", "/").lstrip("./").lstrip("/")
                    # Yalnızca kökteki manifest yedeğe aittir (yedeklenen uygulamaların kendi manifest.json'ları değil).
                    # Arşivin başındaki bu manifest hızlı inceleme içindir; tüm arşivi okumamak için ilk eşleşmede durulur.
                    if norm_name == "manifest.json":
                        f = tar.extractfile(member)
                        if f:
                            data = json.loads(f.read().decode("utf-8", errors="surrogateescape"))
                            return BackupManifest.from_dict(data)
    except Exception:
        return None

    return None
