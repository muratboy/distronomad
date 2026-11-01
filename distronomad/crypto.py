"""
DistroNomad - Güvenli AES-256 PBKDF2 Şifreleme ve Çözme Motoru (Crypto Engine).
Hassas SSH, GPG, kimlik bilgileri ve kişisel dosyalar için endüstri standardı şifreleme sağlar.
"""

from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from distronomad.i18n import t


# OpenSSL Salted Başlık Sabiti (Magic Header)
OPENSSL_SALTED_MAGIC = b"Salted__"


def is_encrypted_file(file_path: Path | str) -> bool:
    """Verilen dosyanın AES-256 şifreli olup olmadığını kontrol eder."""
    p = Path(file_path)
    if not p.exists() or p.is_dir():
        return False

    if str(p).endswith(".enc"):
        return True

    # Dosya başlığını (Magic Bytes) kontrol et
    try:
        with open(p, "rb") as f:
            header = f.read(8)
            return header == OPENSSL_SALTED_MAGIC
    except Exception:
        return False


def encrypt_archive(source_file: Path, output_file: Optional[Path], password: str) -> Path:
    """
    Belirtilen arşivi veya dosyayı AES-256-CBC ve PBKDF2 (100.000 iterasyon) ile şifreler.
    Çıktı uzantısı varsayılan olarak .enc olur.
    """
    if not password:
        raise ValueError(t("crypto_err_password_required_for_encryption"))

    src = Path(source_file)
    if not src.exists():
        raise FileNotFoundError(t("crypto_err_source_not_found", path=str(src)))

    if output_file is None:
        target = Path(f"{src}.enc")
    else:
        target = Path(output_file)

    target.parent.mkdir(parents=True, exist_ok=True)
    # 0o600 ile önceden oluştur (umask'tan bağımsız)
    fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.close(fd)

    if not shutil.which("openssl"):
        raise RuntimeError(t("crypto_err_openssl_required_for_encryption"))

    cmd = [
        "openssl", "enc", "-aes-256-cbc",
        "-pbkdf2", "-iter", "100000",
        "-salt",
        "-in", str(src),
        "-out", str(target),
        "-pass", "stdin"
    ]

    res = subprocess.run(cmd, input=password, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(t("crypto_err_encryption_failed", error=res.stderr.strip()))

    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return target


def decrypt_archive(encrypted_file: Path, output_file: Optional[Path], password: str) -> Path:
    """
    AES-256 ile şifrelenmiş arşivi çözer.
    Parola yanlışsa ValueError fırlatır.
    """
    if not password:
        raise ValueError(t("crypto_err_password_required_for_decryption"))

    enc = Path(encrypted_file)
    if not enc.exists():
        raise FileNotFoundError(t("crypto_err_encrypted_not_found", path=str(enc)))

    if output_file is None:
        # .enc uzantısını kaldır
        name = enc.name[:-4] if enc.name.endswith(".enc") else f"{enc.name}.dec"
        target = enc.parent / name
    else:
        target = Path(output_file)

    target.parent.mkdir(parents=True, exist_ok=True)
    # 0o600 ile önceden oluştur (umask'tan bağımsız)
    fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.close(fd)

    if not shutil.which("openssl"):
        raise RuntimeError(t("crypto_err_openssl_required_for_decryption"))

    cmd = [
        "openssl", "enc", "-d", "-aes-256-cbc",
        "-pbkdf2", "-iter", "100000",
        "-in", str(enc),
        "-out", str(target),
        "-pass", "stdin"
    ]

    res = subprocess.run(cmd, input=password, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        if target.exists():
            target.unlink(missing_ok=True)
        err = res.stderr.strip()
        if "bad decrypt" in err.lower() or "error" in err.lower():
            raise ValueError(t("archive_err_bad_password_or_corrupt"))
        raise RuntimeError(t("crypto_err_decryption_failed", error=err))

    return target
