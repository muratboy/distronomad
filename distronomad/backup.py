"""
DistroNomad - Yedekleme Motoru (Backup Engine).
Tam ve Artımlı (Incremental / Delta & Sync) Yedekleme Desteği.
"""

from __future__ import annotations
import os
import shutil
import hashlib
import tarfile
import json
import stat
import tempfile
import logging
import subprocess
import errno
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple, Any, Union, Iterator
from datetime import datetime

from distronomad.archive import (
    detect_backup_format,
    read_manifest_from_backup,
    extract_tar_archive,
)
from distronomad.i18n import t

logger = logging.getLogger(__name__)

# Varsayılan Zstandard sıkıştırma seviyesi (D-1: Level 3, Level 10'a kıyasla ~5 kat daha hızlıdır)
DEFAULT_ZSTD_COMPRESSION_LEVEL = 3

# FAT32 Dosya Sistemi Sınırları ve Koruması
FAT_FILESYSTEM_TYPES = {"vfat", "msdos", "fat"}
FAT32_ARCHIVE_WARN_BYTES = int(3.5 * 1024 * 1024 * 1024)  # 3.5 GiB (3,758,096,384 bytes)
FAT32_SINGLE_FILE_WARN_BYTES = 4 * 1024 * 1024 * 1024      # 4 GiB (4,294,967,296 bytes)


class Fat32LimitError(OSError):
    """FAT32 dosya boyutu sınırı aşıldığında veya risk tespit edildiğinde fırlatılan hata."""

    def __init__(self, message: str, risk_assessment: Optional[Any] = None):
        super().__init__(errno.EFBIG, message)
        self.message = message
        self.risk_assessment = risk_assessment


@dataclass
class Fat32RiskAssessment:
    has_risk: bool = False
    reason: str = ""       # "archive_size_exceeded" | "single_file_exceeded"
    target_fs: Optional[str] = None
    estimated_size: int = 0
    limit_size: int = 0
    offending_file: Optional[Path] = None
    message: str = ""


def get_filesystem_type(
    path: Path | str,
    mountinfo_path: Path | str = "/proc/self/mountinfo"
) -> Optional[str]:
    """
    Hedef yolun dosya sistemi türünü /proc/self/mountinfo üzerinden,
    çözümlenmiş hedef dizinin en uzun bağlama noktası eşleşmesiyle tespit eder.
    Tespit edilemezse None döner.
    """
    mountinfo_p = Path(mountinfo_path)
    if not mountinfo_p.exists():
        return None

    p = Path(path)
    try:
        curr = p
        while not curr.exists() and curr != curr.parent:
            curr = curr.parent
        if curr.exists():
            curr_res = curr.resolve()
            try:
                rel = p.relative_to(curr)
                resolved_target = curr_res / rel
            except Exception:
                resolved_target = curr_res
        else:
            resolved_target = p.resolve()
    except Exception:
        try:
            resolved_target = p.resolve()
        except Exception:
            resolved_target = Path(path).absolute()

    best_match_parts_len = -1
    best_match_str_len = -1
    best_fs_type: Optional[str] = None

    try:
        with open(mountinfo_p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split()
                if "-" not in parts:
                    continue
                sep_idx = parts.index("-")
                if len(parts) <= sep_idx + 1 or len(parts) < 5:
                    continue
                raw_mp = parts[4]
                # Sekizlik kaçış dizilerini çöz (\040 -> boşluk vb.)
                mp_str = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), raw_mp)
                fs_type = parts[sep_idx + 1]

                mp_path = Path(mp_str)
                try:
                    if resolved_target.is_relative_to(mp_path):
                        parts_len = len(mp_path.parts)
                        str_len = len(mp_str)
                        if (parts_len, str_len) >= (best_match_parts_len, best_match_str_len):
                            best_match_parts_len = parts_len
                            best_match_str_len = str_len
                            best_fs_type = fs_type
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.debug(f"mountinfo okunamadı: {e}")
        return None

    return best_fs_type


def is_fat_filesystem(
    path: Path | str,
    mountinfo_path: Path | str = "/proc/self/mountinfo"
) -> bool:
    """
    Hedef yolun dosya sistemi türünü mountinfo üzerinden kontrol eder.
    vfat, msdos ve fat türlerini FAT (True) sayar; exfat ve diğerlerini sınırsız (False) sayar.
    Tespit edilemezse koruma atlanır (False döner).
    """
    fs_type = get_filesystem_type(path, mountinfo_path=mountinfo_path)
    if not fs_type:
        return False
    return fs_type.lower() in FAT_FILESYSTEM_TYPES


def estimate_backup_size(
    configs: Optional[List[ConfigItem]] = None,
    custom_paths: Optional[List[CustomPathItem]] = None,
    append_existing_size: int = 0
) -> int:
    """Seçili ögelerin tahmini toplam disk boyutunu (byte) hesaplar."""
    total = 0
    if configs:
        for c in configs:
            if not getattr(c, "is_selected", True):
                continue
            sz = getattr(c, "size_bytes", 0)
            if sz > 0:
                total += sz
            else:
                sp = getattr(c, "source_path", None)
                if sp:
                    p = Path(sp)
                    if p.is_file() and not p.is_symlink():
                        try:
                            total += p.stat().st_size
                        except OSError:
                            pass
                    elif p.is_dir() and not p.is_symlink():
                        try:
                            from distronomad.discovery import get_dir_size_and_count
                            total += get_dir_size_and_count(p)[0]
                        except Exception:
                            pass
    if custom_paths:
        for cp in custom_paths:
            if not getattr(cp, "is_selected", True):
                continue
            sz = getattr(cp, "size_bytes", 0)
            if sz > 0:
                total += sz
            else:
                cpp = getattr(cp, "path", None)
                if cpp:
                    p = Path(cpp)
                    if p.is_file() and not p.is_symlink():
                        try:
                            total += p.stat().st_size
                        except OSError:
                            pass
                    elif p.is_dir() and not p.is_symlink():
                        try:
                            from distronomad.discovery import get_dir_size_and_count
                            total += get_dir_size_and_count(p)[0]
                        except Exception:
                            pass

    total += append_existing_size
    return total


def find_large_file_in_items(
    configs: Optional[List[ConfigItem]] = None,
    custom_paths: Optional[List[CustomPathItem]] = None,
    threshold_bytes: int = FAT32_SINGLE_FILE_WARN_BYTES
) -> Optional[Tuple[Path, int]]:
    """Seçili ögelerde threshold_bytes (4 GiB) veya üstü tek bir dosya varsa (dosya_yolu, boyut) döner."""
    paths_to_check: List[Path] = []
    if configs:
        for c in configs:
            if getattr(c, "is_selected", True):
                sp = getattr(c, "source_path", None)
                if sp:
                    paths_to_check.append(Path(sp))
    if custom_paths:
        for cp in custom_paths:
            if getattr(cp, "is_selected", True):
                p = getattr(cp, "path", None)
                if p:
                    paths_to_check.append(Path(p))

    for p in paths_to_check:
        if not p.exists() and not p.is_symlink():
            continue
        if p.is_file() and not p.is_symlink():
            try:
                sz = p.stat().st_size
                if sz >= threshold_bytes:
                    return p, sz
            except OSError:
                pass
        elif p.is_dir() and not p.is_symlink():
            try:
                for root, dirs, files in os.walk(p):
                    for fn in files:
                        fp = Path(root) / fn
                        if not fp.is_symlink():
                            try:
                                sz = fp.stat().st_size
                                if sz >= threshold_bytes:
                                    return fp, sz
                            except OSError:
                                pass
            except OSError:
                pass
    return None


def check_fat32_risk(
    target_path: Path | str,
    archive_format: str = "tar.zst",
    backup_mode: str = "full",
    configs: Optional[List[ConfigItem]] = None,
    custom_paths: Optional[List[CustomPathItem]] = None,
    base_backup_path: Optional[Path] = None,
    mountinfo_path: Path | str = "/proc/self/mountinfo"
) -> Fat32RiskAssessment:
    """
    Hedef yolun FAT dosya sisteminde olup olmadığını ve FAT32 sınırlarını aşıp aşmadığını kontrol eder.
    Hedef FAT değilse veya tespit edilemezse has_risk=False döner.
    """
    target_p = Path(target_path)
    fs_type = get_filesystem_type(target_p, mountinfo_path=mountinfo_path)
    if not is_fat_filesystem(target_p, mountinfo_path=mountinfo_path):
        return Fat32RiskAssessment(has_risk=False, target_fs=fs_type)

    if archive_format == "dir":
        # dir formatında: seçili ögelerde 4 GiB veya üstü tek bir dosya varsa öncelikli olarak uyar
        large_file = find_large_file_in_items(configs, custom_paths, FAT32_SINGLE_FILE_WARN_BYTES)
        if large_file:
            offending_p, sz = large_file
            msg = t("fat32_single_file_limit_warning", file=str(offending_p), size=format_bytes(sz))
            return Fat32RiskAssessment(
                has_risk=True,
                reason="single_file_exceeded",
                target_fs=fs_type,
                estimated_size=sz,
                limit_size=FAT32_SINGLE_FILE_WARN_BYTES,
                offending_file=offending_p,
                message=msg
            )
        # 4 GiB aşılmasa bile FAT32 sembolik bağları ve POSIX izinlerini desteklemez (A-P2-4)
        msg = t("fat32_dir_unsupported_warning")
        return Fat32RiskAssessment(
            has_risk=True,
            reason="fat32_dir_unsupported",
            target_fs=fs_type,
            message=msg
        )
    else:
        # Arşiv formatlarında (tar.zst, tar.gz, tar, şifreli dahil):
        # Seçili ögelerin tahmini toplam boyutu 3.5 GiB değerini aşıyorsa uyar
        append_sz = 0
        if backup_mode == "append":
            existing_archive = target_p if target_p.exists() else (base_backup_path if base_backup_path and base_backup_path.exists() else None)
            if existing_archive and existing_archive.is_file():
                try:
                    append_sz = existing_archive.stat().st_size
                except OSError:
                    pass

        estimated_total = estimate_backup_size(configs=configs, custom_paths=custom_paths, append_existing_size=append_sz)
        if estimated_total > FAT32_ARCHIVE_WARN_BYTES:
            msg = t("fat32_archive_limit_warning", size=format_bytes(estimated_total))
            return Fat32RiskAssessment(
                has_risk=True,
                reason="archive_size_exceeded",
                target_fs=fs_type,
                estimated_size=estimated_total,
                limit_size=FAT32_ARCHIVE_WARN_BYTES,
                message=msg
            )
        return Fat32RiskAssessment(has_risk=False, target_fs=fs_type, estimated_size=estimated_total)


from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    CustomPathItem,
    BackupManifest,
    ProgressUpdate,
    format_bytes,
)
from distronomad.standalone_script import generate_standalone_restore_script
from distronomad.ignore import SmartIgnoreFilter
from distronomad.crypto import encrypt_archive, decrypt_archive, is_encrypted_file
from distronomad.hardware import scan_hardware_profile



def compute_sha256(file_path: Path) -> str:
    """Dosyanın SHA-256 sağlama toplamını hesaplar."""
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""


class _HashingReader:
    """tarfile.addfile'ın arşive yazdığı baytların SHA-256 özetini tutan okuyucu.
    Dosya okunurken kısalırsa (ör. SQLite -wal dosyasının sıfırlanması) GNU tar gibi eksik kısmı sıfırla doldurur;
    aksi hâlde başlıktaki boyut tutmaz ve tar akışının geri kalanı okunamaz hâle gelir."""

    def __init__(self, fobj):
        self._fobj = fobj
        self.hasher = hashlib.sha256()
        self.shrank = False

    def read(self, size: int = -1) -> bytes:
        data = self._fobj.read(size)
        if size is not None and size > 0 and len(data) < size:
            self.shrank = True
            data += b"\0" * (size - len(data))
        self.hasher.update(data)
        return data


def add_file_with_hash(tar_obj: tarfile.TarFile, src: Path, arcname: str) -> Tuple[str, bool]:
    """Normal bir dosyayı tek okumada arşive ekler; (arşive yazılan baytların SHA-256 özeti, dosya kısaldı mı) döndürür.
    Sabit bağlantılı (hard link) dosyalar da içerikleriyle normal dosya olarak yazılır; böylece her girdi
    kendi içeriğini taşır ve doğrulanabilir."""
    with open(src, "rb") as f_in:
        tarinfo = tar_obj.gettarinfo(arcname=arcname, fileobj=f_in)
        if tarinfo.islnk():
            tarinfo.type = tarfile.REGTYPE
            tarinfo.linkname = ""
            tarinfo.size = os.fstat(f_in.fileno()).st_size
        reader = _HashingReader(f_in)
        tar_obj.addfile(tarinfo, reader)
    return reader.hasher.hexdigest(), reader.shrank


def safe_copytree(src: Path, dst: Path, symlinks: bool = True, dirs_exist_ok: bool = True) -> None:
    """Mevcut symlink veya dosyaların üzerine güvenle yazabilen copytree alternatifi."""
    src_p = Path(src)
    dst_p = Path(dst)

    if dst_p.is_symlink():
        dst_p.unlink()
    elif not dirs_exist_ok and dst_p.exists():
        raise FileExistsError(t("backup_err_target_exists", path=str(dst_p)))

    dst_p.mkdir(parents=True, exist_ok=dirs_exist_ok)

    for entry in os.scandir(src_p):
        s = src_p / entry.name
        d = dst_p / entry.name

        if entry.is_symlink() and symlinks:
            link_to = os.readlink(s)
            if d.is_symlink() or d.exists():
                if d.is_dir() and not d.is_symlink():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
            os.symlink(link_to, d)
            try:
                shutil.copystat(s, d, follow_symlinks=False)
            except OSError:
                pass
        elif entry.is_dir(follow_symlinks=not symlinks):
            if d.is_symlink() or (d.is_file() and not d.is_dir()):
                d.unlink(missing_ok=True)
            safe_copytree(s, d, symlinks=symlinks, dirs_exist_ok=dirs_exist_ok)
            try:
                shutil.copystat(s, d)
            except OSError:
                pass
        else:
            if d.is_symlink() or d.is_dir():
                if d.is_dir() and not d.is_symlink():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
            try:
                shutil.copy2(s, d, follow_symlinks=not symlinks)
            except (shutil.SpecialFileError, OSError):
                pass
    try:
        shutil.copystat(src_p, dst_p)
    except OSError:
        pass


def _flush_to_disk(target_path: Path, is_dir: bool = False) -> None:
    """Yedekleme verilerini işletim sistemi tamponundan diske zorla yazar (sync/fsync)."""
    if is_dir:
        if hasattr(os, "sync"):
            os.sync()
    else:
        # Tek dosyalı arşiv: dosyayı ve üst dizini os.fsync ile zorla diske yaz
        try:
            fd = os.open(target_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

        try:
            dir_fd = os.open(target_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Dizin fsync bazı dosya sistemlerinde veya işletim sistemlerinde desteklenmeyebilir
            pass


def load_chain_metadata(
    base_backup_path: Optional[Path],
    password: Optional[str] = None
) -> Tuple[Optional[BackupManifest], Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    Temel yedekten başlayarak zincirdeki önceki yedeklerin manifestlerini geriye doğru
    tarar ve zincirdeki her dosyanın en son kaydedilmiş durumunu (metadata ve hash) birleştirir.
    """
    if not base_backup_path:
        return None, {}, {}
    p = Path(base_backup_path)
    if not p.exists():
        return None, {}, {}

    base_manifest = read_manifest_from_backup(p, password=password)
    if not base_manifest:
        return None, {}, {}

    manifests = [base_manifest]
    current_path = p
    seen_paths = {p.resolve()}

    curr_manifest = base_manifest
    while curr_manifest and curr_manifest.backup_type == "incremental" and curr_manifest.base_backup_name:
        parent_cand = current_path.parent / curr_manifest.base_backup_name
        if not parent_cand.exists() and p.parent != current_path.parent:
            cand2 = p.parent / curr_manifest.base_backup_name
            if cand2.exists():
                parent_cand = cand2
        if parent_cand.exists() and parent_cand.resolve() not in seen_paths:
            seen_paths.add(parent_cand.resolve())
            parent_manifest = read_manifest_from_backup(parent_cand, password=password)
            if parent_manifest:
                manifests.append(parent_manifest)
                curr_manifest = parent_manifest
                current_path = parent_cand
            else:
                break
        else:
            break

    chain_metadata: Dict[str, Dict[str, Any]] = {}
    chain_hashes: Dict[str, str] = {}
    for m in reversed(manifests):
        if m.file_metadata:
            chain_metadata.update(m.file_metadata)
        if m.file_hashes:
            chain_hashes.update(m.file_hashes)
        for del_path in (getattr(m, "deleted_files", []) or []):
            chain_metadata.pop(del_path, None)
            chain_hashes.pop(del_path, None)
        if m.file_metadata:
            for k, meta in m.file_metadata.items():
                if isinstance(meta, dict) and (meta.get("deleted") is True or meta.get("status") == "deleted"):
                    chain_metadata.pop(k, None)
                    chain_hashes.pop(k, None)
        if m.file_hashes:
            for k, h in m.file_hashes.items():
                if h in ("__DELETED__", "deleted"):
                    chain_metadata.pop(k, None)
                    chain_hashes.pop(k, None)

    return base_manifest, chain_metadata, chain_hashes


def is_file_changed(src_file: Path, rel_key: str, base_manifest: Optional[BackupManifest]) -> Tuple[bool, str]:
    """Dosyanın temel yedeğe göre değişip değişmediğini kontrol eder.
    Döndürür: (is_changed, reason) -> reason: 'new', 'modified', 'unmodified'
    """
    if base_manifest is None:
        return True, "new"

    if not src_file.exists() and not src_file.is_symlink():
        return False, "missing"

    # Sembolik bağlar: hedef yolu (readlink) ile karşılaştır
    if src_file.is_symlink():
        try:
            cur_target = os.readlink(src_file)
        except OSError:
            return True, "modified"

        if base_manifest.file_metadata and rel_key in base_manifest.file_metadata:
            base_info = base_manifest.file_metadata[rel_key]
            if base_info.get("type") == "symlink" or "link_target" in base_info:
                if cur_target == base_info.get("link_target"):
                    return False, "unmodified"
                return True, "modified"

        if base_manifest.file_hashes and rel_key in base_manifest.file_hashes:
            base_hash = base_manifest.file_hashes[rel_key]
            if base_hash == f"symlink:{cur_target}":
                return False, "unmodified"
            return True, "modified"

        return True, "new"

    # 1. file_metadata üzerinden mtime eşitsizliği (geri gidiş dahil) ve boyut kontrolü
    if base_manifest.file_metadata and rel_key in base_manifest.file_metadata:
        base_info = base_manifest.file_metadata[rel_key]
        if base_info.get("type") == "symlink":
            return True, "modified"
        try:
            st = src_file.stat()
            mtime_diff = abs(st.st_mtime - base_info.get("mtime", 0.0))
            size_diff = st.st_size != base_info.get("size", -1)
            if mtime_diff > 0.001 or size_diff:
                return True, "modified"
            return False, "unmodified"
        except Exception:
            return True, "modified"

    # 2. file_hashes üzerinden hash kontrolü (eski manifest uyumluluğu)
    if base_manifest.file_hashes and rel_key in base_manifest.file_hashes:
        base_hash = base_manifest.file_hashes[rel_key]
        try:
            cur_hash = compute_sha256(src_file)
            if cur_hash != base_hash:
                return True, "modified"
            return False, "unmodified"
        except Exception:
            return True, "modified"

    return True, "new"


def detect_deleted_files(
    configs: List[ConfigItem],
    custom_paths: List[CustomPathItem],
    base_manifest: Optional[BackupManifest],
    ignore_filter: Optional[SmartIgnoreFilter] = None,
    home_dir: Optional[Union[str, Path]] = None
) -> List[str]:
    """Taban manifestte kayıtlı olup bu yedekte seçili olan bir ögenin kökü altında bulunan
    ve artık diskte hiç bulunmayan (lexists false) dosyaları tespit eder.
    Seçimden çıkarılmış ögeleri ve akıllı filtreyle atlanan dosyaları silinmiş saymaz.
    """
    if base_manifest is None:
        return []

    effective_home = Path(home_dir).resolve() if home_dir else (
        Path(base_manifest.system_info.home_dir).resolve() if base_manifest and base_manifest.system_info and base_manifest.system_info.home_dir else Path.home().resolve()
    )

    candidates = set()
    if base_manifest.file_metadata:
        candidates.update(base_manifest.file_metadata.keys())
    if base_manifest.file_hashes:
        candidates.update(base_manifest.file_hashes.keys())

    for del_f in getattr(base_manifest, "deleted_files", []) or []:
        candidates.discard(del_f)
    if base_manifest.file_metadata:
        for k, meta in base_manifest.file_metadata.items():
            if isinstance(meta, dict) and (meta.get("deleted") is True or meta.get("status") == "deleted"):
                candidates.discard(k)
    if base_manifest.file_hashes:
        for k, h in base_manifest.file_hashes.items():
            if h in ("__DELETED__", "deleted"):
                candidates.discard(k)

    if not candidates:
        return []

    selected_configs = [c for c in configs if getattr(c, "is_selected", True)]
    selected_custom = [cp for cp in custom_paths if getattr(cp, "is_selected", True)]

    deleted_files: List[str] = []

    for rel_key in sorted(candidates):
        matched = False
        disk_path: Optional[Path] = None
        base_path: Optional[Path] = None

        # 1. Seçili konfigürasyonlar altında mı?
        for cfg in selected_configs:
            src_path = Path(cfg.source_path)
            cfg_prefix = f"configs/{cfg.rel_path.strip('/')}"
            if rel_key == cfg_prefix:
                disk_path = src_path
                base_path = src_path
                matched = True
                break
            elif rel_key.startswith(f"{cfg_prefix}/"):
                inner_rel = rel_key[len(cfg_prefix) + 1:]
                disk_path = src_path / inner_rel
                base_path = src_path
                matched = True
                break

        # 2. Seçili özel klasör/dosyalar altında mı?
        if not matched:
            for cp in selected_custom:
                src_path = Path(cp.path)
                is_home = False
                rel_to_home = None
                try:
                    rel_to_home = src_path.resolve().relative_to(effective_home)
                    is_home = True
                except (ValueError, OSError):
                    pass

                if not is_home:
                    try:
                        rel_to_home = src_path.relative_to(Path(home_dir) if home_dir else Path.home())
                        is_home = True
                    except ValueError:
                        pass

                if is_home:
                    base_dest_rel = Path("custom/home") / rel_to_home
                else:
                    rel_to_root = Path(str(src_path).lstrip("/"))
                    base_dest_rel = Path("custom/root") / rel_to_root

                cp_prefix = str(base_dest_rel).replace("\\", "/").strip("/")

                if rel_key == cp_prefix:
                    disk_path = src_path
                    base_path = src_path
                    matched = True
                    break
                elif rel_key.startswith(f"{cp_prefix}/"):
                    inner_rel = rel_key[len(cp_prefix) + 1:]
                    disk_path = src_path / inner_rel
                    base_path = src_path
                    matched = True
                    break

        if not matched or disk_path is None or base_path is None:
            continue

        if ignore_filter and ignore_filter.should_ignore(disk_path, base_path=base_path):
            continue

        if not os.path.lexists(disk_path):
            deleted_files.append(rel_key)

    return deleted_files


def walk_and_filter_files(
    src_path: Path,
    ignore_filter: Optional[SmartIgnoreFilter] = None,
    is_custom_path: bool = False,
    on_ignored: Optional[Callable[[], None]] = None
) -> Iterator[Tuple[Path, Optional[Path]]]:
    """
    Belirtilen kaynak yol (dosya, sembolik bağ veya dizin) altındaki tüm geçerli dosyaları
    ve sembolik bağları akıllı filtre kurallarını (SmartIgnoreFilter) ve özel dosya (soket/FIFO/cihaz)
    kontrollerini uygulayarak (entry_path, inner_relative_path) çiftleri halinde üretir.
    Tekil dosya veya kök sembolik bağ için inner_relative_path None döner.
    """
    if not src_path.exists() and not src_path.is_symlink():
        return

    if ignore_filter and ignore_filter.should_ignore(src_path, base_path=src_path, is_custom_path=is_custom_path):
        if on_ignored:
            on_ignored()
        return

    if src_path.is_file() or src_path.is_symlink():
        yield (src_path, None)
        return

    if src_path.is_dir():
        try:
            for root, dirs, files in os.walk(str(src_path)):
                root_path = Path(root)

                symlink_dirs = []
                regular_dirs = []
                for d in dirs:
                    dir_entry = root_path / d
                    if ignore_filter and ignore_filter.should_ignore(dir_entry, base_path=src_path, is_custom_path=is_custom_path):
                        if on_ignored:
                            on_ignored()
                        continue
                    if dir_entry.is_symlink():
                        symlink_dirs.append(d)
                    else:
                        regular_dirs.append(d)

                dirs[:] = regular_dirs

                for d in symlink_dirs:
                    symlink_path = root_path / d
                    try:
                        inner_rel = symlink_path.relative_to(src_path)
                        yield (symlink_path, inner_rel)
                    except Exception:
                        pass

                for f in files:
                    file_path = root_path / f
                    if ignore_filter and ignore_filter.should_ignore(file_path, base_path=src_path, is_custom_path=is_custom_path):
                        if on_ignored:
                            on_ignored()
                        continue
                    try:
                        st_mode = file_path.lstat().st_mode
                        if stat.S_ISSOCK(st_mode) or stat.S_ISFIFO(st_mode) or stat.S_ISCHR(st_mode) or stat.S_ISBLK(st_mode):
                            logger.warning(t("backup_warn_special_file_skipped", path=str(file_path)))
                            continue
                    except Exception:
                        continue
                    try:
                        inner_rel = file_path.relative_to(src_path)
                        yield (file_path, inner_rel)
                    except Exception:
                        pass
        except OSError:
            pass


def walk_empty_dirs(
    src_path: Path,
    ignore_filter: Optional[SmartIgnoreFilter] = None,
    is_custom_path: bool = False
) -> Iterator[Tuple[Path, Optional[Path]]]:
    """
    walk_and_filter_files ile aynı filtreler uygulandıktan sonra hiçbir dosya, sembolik bağ veya
    alt dizin içermeyen (boş) dizinleri (dizin_yolu, iç_göreli_yol) çiftleri halinde üretir.
    Kök dizinin kendisi boşsa iç göreli yol None döner. Örneğin yalnızca soket içeren ~/.ssh/sockets
    veya vim'in undodir dizini bu sayede yedekte dizin girdisi olarak korunur.
    """
    if src_path.is_symlink() or not src_path.is_dir():
        return
    if ignore_filter and ignore_filter.should_ignore(src_path, base_path=src_path, is_custom_path=is_custom_path):
        return
    try:
        for root, dirs, files in os.walk(str(src_path)):
            root_path = Path(root)
            kept_dirs = []
            has_entry = False
            for d in dirs:
                dir_entry = root_path / d
                if ignore_filter and ignore_filter.should_ignore(dir_entry, base_path=src_path, is_custom_path=is_custom_path):
                    continue
                if dir_entry.is_symlink():
                    has_entry = True
                else:
                    kept_dirs.append(d)
            dirs[:] = kept_dirs
            if kept_dirs:
                has_entry = True
            if not has_entry:
                for f in files:
                    file_path = root_path / f
                    if ignore_filter and ignore_filter.should_ignore(file_path, base_path=src_path, is_custom_path=is_custom_path):
                        continue
                    try:
                        st_mode = file_path.lstat().st_mode
                    except OSError:
                        continue
                    if stat.S_ISSOCK(st_mode) or stat.S_ISFIFO(st_mode) or stat.S_ISCHR(st_mode) or stat.S_ISBLK(st_mode):
                        continue
                    has_entry = True
                    break
            if not has_entry:
                yield (root_path, None if root_path == src_path else root_path.relative_to(src_path))
    except OSError:
        pass


def collect_empty_dir_entries(
    configs: List[ConfigItem],
    custom_paths: List[CustomPathItem],
    ignore_filter: Optional[SmartIgnoreFilter] = None,
    home_dir: Optional[Union[str, Path]] = None
) -> List[Tuple[Path, Path]]:
    """Seçili konfigürasyon ve özel yollardaki boş dizinleri (kaynak_dizin, arşiv_içi_hedef) listesi olarak döndürür.
    Arşiv içi hedef, scan_and_compare_entries'in dosyalar için kullandığı eşlemeyle aynıdır."""
    entries: List[Tuple[Path, Path]] = []
    effective_home = Path(home_dir).resolve() if home_dir else Path.home().resolve()

    for cfg in configs:
        base_dest_rel = Path("configs") / cfg.rel_path
        for dir_path, inner_rel in walk_empty_dirs(Path(cfg.source_path), ignore_filter=ignore_filter, is_custom_path=False):
            entries.append((dir_path, base_dest_rel / inner_rel if inner_rel is not None else base_dest_rel))

    for cp in custom_paths:
        src_path = Path(cp.path)
        if src_path.is_symlink() or not src_path.is_dir():
            continue
        try:
            base_dest_rel = Path("custom/home") / src_path.resolve().relative_to(effective_home)
        except ValueError:
            try:
                base_dest_rel = Path("custom/home") / src_path.relative_to(Path(home_dir) if home_dir else Path.home())
            except ValueError:
                base_dest_rel = Path("custom/root") / Path(str(src_path).lstrip("/"))
        for dir_path, inner_rel in walk_empty_dirs(src_path, ignore_filter=ignore_filter, is_custom_path=True):
            entries.append((dir_path, base_dest_rel / inner_rel if inner_rel is not None else base_dest_rel))

    return entries


def scan_and_compare_entries(
    configs: List[ConfigItem],
    custom_paths: List[CustomPathItem],
    base_manifest: Optional[BackupManifest],
    ignore_filter: Optional[SmartIgnoreFilter] = None,
    home_dir: Optional[Union[str, Path]] = None,
    on_ignored: Optional[Callable[[], None]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, List[str]]:
    """Tüm seçili konfigürasyon ve özel dosyaları tarayarak temel yedeğe göre değişenleri ve değişmeyenleri belirler.
    Döndürür: (changed_items, unchanged_items, bytes_saved, deleted_files)
    """
    changed_items: List[Dict[str, Any]] = []
    unchanged_items: List[Dict[str, Any]] = []
    bytes_saved = 0

    effective_home = Path(home_dir).resolve() if home_dir else (
        Path(base_manifest.system_info.home_dir).resolve() if base_manifest and base_manifest.system_info and base_manifest.system_info.home_dir else Path.home().resolve()
    )

    # 1. Konfigürasyonlar
    for cfg in configs:
        src_path = Path(cfg.source_path)
        base_dest_rel = Path("configs") / cfg.rel_path
        base_rel_key = f"configs/{cfg.rel_path}"

        for entry_path, inner_rel in walk_and_filter_files(src_path, ignore_filter=ignore_filter, is_custom_path=False, on_ignored=on_ignored):
            if inner_rel is not None:
                dest_rel = base_dest_rel / inner_rel
                rel_key = f"{base_rel_key}/{inner_rel}"
            else:
                dest_rel = base_dest_rel
                rel_key = base_rel_key

            is_chg, reason = is_file_changed(entry_path, rel_key, base_manifest)
            sz = entry_path.stat().st_size if entry_path.is_file() and not entry_path.is_symlink() else 0
            item = {
                "type": "config",
                "name": cfg.name,
                "src": entry_path,
                "rel_key": rel_key,
                "dest_rel": dest_rel,
                "reason": reason,
                "size": sz
            }
            if is_chg:
                changed_items.append(item)
            else:
                unchanged_items.append(item)
                bytes_saved += sz

    # 2. Özel Klasörler
    for cp in custom_paths:
        src_path = Path(cp.path)
        if not src_path.exists() and not src_path.is_symlink():
            continue

        if ignore_filter and ignore_filter.should_ignore(src_path, base_path=src_path, is_custom_path=True):
            if on_ignored:
                on_ignored()
            continue

        is_home = False
        rel_to_home = None
        try:
            rel_to_home = src_path.resolve().relative_to(effective_home)
            is_home = True
        except ValueError:
            pass

        if not is_home:
            try:
                rel_to_home = src_path.relative_to(Path(home_dir) if home_dir else Path.home())
                is_home = True
            except ValueError:
                pass

        if is_home:
            base_dest_rel = Path("custom/home") / rel_to_home
        else:
            rel_to_root = Path(str(src_path).lstrip("/"))
            base_dest_rel = Path("custom/root") / rel_to_root

        dir_name = src_path.name
        base_rel_key = str(base_dest_rel).replace("\\", "/")

        for entry_path, inner_rel in walk_and_filter_files(src_path, ignore_filter=ignore_filter, is_custom_path=True, on_ignored=on_ignored):
            if inner_rel is not None:
                dest_rel = base_dest_rel / inner_rel
                rel_key = str(dest_rel).replace("\\", "/")
            else:
                dest_rel = base_dest_rel
                rel_key = base_rel_key

            is_chg, reason = is_file_changed(entry_path, rel_key, base_manifest)
            sz = entry_path.stat().st_size if entry_path.is_file() and not entry_path.is_symlink() else 0
            item = {
                "type": "custom",
                "name": cp.label or dir_name,
                "src": entry_path,
                "rel_key": rel_key,
                "dest_rel": dest_rel,
                "reason": reason,
                "size": sz
            }
            if is_chg:
                changed_items.append(item)
            else:
                unchanged_items.append(item)
                bytes_saved += sz

    deleted_files = detect_deleted_files(
        configs=configs,
        custom_paths=custom_paths,
        base_manifest=base_manifest,
        ignore_filter=ignore_filter,
        home_dir=effective_home
    )

    return changed_items, unchanged_items, bytes_saved, deleted_files


def copy_single_file(
    src: Path,
    dst: Path,
    rel_key: str,
    file_hashes: Dict[str, str],
    file_metadata: Dict[str, Dict[str, Any]],
    skipped_files: Optional[List[Dict[str, str]]] = None
) -> int:
    """Tek bir dosyayı kopyalar ve metadata/hash bilgilerini günceller."""
    if not src.exists() and not src.is_symlink():
        return 0

    try:
        st_mode = src.lstat().st_mode
        if stat.S_ISSOCK(st_mode) or stat.S_ISFIFO(st_mode) or stat.S_ISCHR(st_mode) or stat.S_ISBLK(st_mode):
            logger.warning(t("backup_warn_special_file_skipped", path=str(src)))
            return 0
    except (OSError, PermissionError, FileNotFoundError):
        return 0

    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_symlink():
        try:
            link_target = os.readlink(src)
            if dst.is_symlink() or dst.exists():
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    dst.unlink()
            os.symlink(link_target, dst)
            file_metadata[rel_key] = {
                "type": "symlink",
                "link_target": link_target,
                "size": 0,
                "mtime": 0.0
            }
            file_hashes[rel_key] = f"symlink:{link_target}"
            return 0
        except OSError as e:
            logger.warning(t("backup_warn_symlink_failed", src=str(src), dst=str(dst), error=str(e)))
            if skipped_files is not None:
                skipped_files.append({"path": str(src), "reason": t("backup_reason_symlink_failed", error=str(e))})
            return 0

    try:
        shutil.copy2(src, dst)
        st = dst.stat()
        h = compute_sha256(dst)
        if not h:
            raise OSError(t("backup_err_hash_failed", path=str(dst)))
        file_hashes[rel_key] = h
        file_metadata[rel_key] = {
            "size": st.st_size,
            "mtime": st.st_mtime,
            "sha256": h
        }
        return st.st_size
    except shutil.SpecialFileError as e:
        logger.warning(t("backup_warn_special_file_skipped", path=str(src)))
        if skipped_files is not None:
            skipped_files.append({"path": str(src), "reason": t("backup_reason_special_file", error=str(e))})
        return 0
    except (PermissionError, FileNotFoundError) as e:
        logger.warning(t("backup_warn_read_failed", path=str(src), error=str(e)))
        if skipped_files is not None:
            skipped_files.append({"path": str(src), "reason": str(e)})
        return 0
    except OSError as e:
        if getattr(e, "errno", None) == errno.EFBIG or "File too large" in str(e):
            if dst.exists():
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    dst.unlink(missing_ok=True)
            raise
        logger.warning(t("backup_warn_copy_failed", path=str(src), error=str(e)))
        if skipped_files is not None:
            skipped_files.append({"path": str(src), "reason": str(e)})
        return 0


class BackupEngine:
    """Tam ve Artımlı (Incremental) Yedekleme işlemlerini yürüten ana sınıf."""

    def __init__(
        self,
        system_info: SystemInfo,
        packages: List[PackageItem],
        configs: List[ConfigItem],
        custom_paths: List[CustomPathItem],
        output_path: Path,
        archive_format: str = "tar.zst", # tar.zst, tar.gz, dir
        backup_mode: str = "full",       # full, incremental
        base_backup_path: Optional[Path] = None,
        use_smart_ignore: bool = True,
        custom_ignore_patterns: Optional[List[str]] = None,
        password: Optional[str] = None,
        etc_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[ProgressUpdate], None]] = None,
        force: bool = False,
        mountinfo_path: Path | str = "/proc/self/mountinfo"
    ):
        self.system_info = system_info
        self.packages = [p for p in packages if p.is_selected]
        self.configs = [c for c in configs if c.is_selected]
        self.custom_paths = [cp for cp in custom_paths if cp.is_selected]
        self.output_path = Path(output_path)
        self.archive_format = archive_format.lower()
        self.backup_mode = backup_mode.lower()
        self.base_backup_path = Path(base_backup_path) if base_backup_path else None
        self.use_smart_ignore = use_smart_ignore
        self.custom_ignore_patterns = custom_ignore_patterns
        self.password = password
        self.etc_dir = Path(etc_dir) if etc_dir else Path("/etc")
        self.progress_callback = progress_callback
        self.force = force
        self.mountinfo_path = mountinfo_path
        self.file_hashes: Dict[str, str] = {}
        self.file_metadata: Dict[str, Dict[str, Any]] = {}
        self.incremental_stats: Dict[str, int] = {}
        self.skipped_files: List[Dict[str, str]] = []
        self.ignored_by_filter_count: int = 0

    def _notify(
        self,
        phase: str,
        message: str,
        current_item: str = "",
        processed_count: int = 0,
        total_count: int = 0,
        processed_bytes: int = 0,
        total_bytes: int = 0,
        percent: float = 0.0
    ):
        if self.progress_callback:
            self.progress_callback(ProgressUpdate(
                phase=phase,
                message=message,
                current_item=current_item,
                processed_count=processed_count,
                total_count=total_count,
                processed_bytes=processed_bytes,
                total_bytes=total_bytes,
                percent=percent
            ))

    def run(self) -> Path:
        """Yedekleme sürecini başlatır ve oluşturulan arşivin/dizinin yolunu döndürür."""
        if not self.force:
            risk = check_fat32_risk(
                target_path=self.output_path,
                archive_format=self.archive_format,
                backup_mode=self.backup_mode,
                configs=self.configs,
                custom_paths=self.custom_paths,
                base_backup_path=self.base_backup_path,
                mountinfo_path=self.mountinfo_path
            )
            if risk.has_risk:
                raise Fat32LimitError(risk.message, risk_assessment=risk)

        # Hedef yolu düzenle
        if self.backup_mode == "append":
            return self._run_append_backup()

        if self.password:
            # Şifreli arşiv uzantısını düzenle
            if not str(self.output_path).endswith(".enc"):
                if self.archive_format == "tar.zst" and not str(self.output_path).endswith(".tar.zst"):
                    self.output_path = Path(f"{self.output_path}.tar.zst.enc")
                elif self.archive_format == "tar.gz" and not str(self.output_path).endswith(".tar.gz"):
                    self.output_path = Path(f"{self.output_path}.tar.gz.enc")
                else:
                    self.output_path = Path(f"{self.output_path}.enc")
            else:
                base_without_enc = str(self.output_path)[:-4]
                if self.archive_format == "tar.zst" and not base_without_enc.endswith(".tar.zst"):
                    self.output_path = Path(f"{base_without_enc}.tar.zst.enc")
                elif self.archive_format == "tar.gz" and not base_without_enc.endswith(".tar.gz"):
                    self.output_path = Path(f"{base_without_enc}.tar.gz.enc")
        else:
            if self.archive_format == "tar.zst" and not str(self.output_path).endswith(".tar.zst"):
                self.output_path = Path(f"{self.output_path}.tar.zst")
            elif self.archive_format == "tar.gz" and not str(self.output_path).endswith(".tar.gz"):
                self.output_path = Path(f"{self.output_path}.tar.gz")

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        if self.password and self.archive_format == "dir":
            raise ValueError(t("backup_err_enc_archive_only"))

        # Donanım Profilini Çıkar
        try:
            hw_info = scan_hardware_profile().to_dict()
        except Exception:
            hw_info = {}

        base_manifest: Optional[BackupManifest] = None
        chain_metadata: Dict[str, Dict[str, Any]] = {}
        chain_hashes: Dict[str, str] = {}
        if self.backup_mode == "incremental" and self.base_backup_path:
            self._notify("analiz", t("backup_notify_examining_base", name=self.base_backup_path.name))
            base_manifest, chain_metadata, chain_hashes = load_chain_metadata(
                self.base_backup_path, password=self.password
            )
            if not base_manifest:
                self._notify("uyarı", t("backup_notify_base_read_failed", name=self.base_backup_path.name))
                self.backup_mode = "full"
            else:
                base_manifest.file_metadata = chain_metadata
                base_manifest.file_hashes = chain_hashes

        # Akıllı Filtre Hazırlığı
        ignore_filter = None
        if self.use_smart_ignore:
            ignore_filter = SmartIgnoreFilter(
                use_smart_defaults=True,
                custom_patterns=self.custom_ignore_patterns
            )

        def _on_ignored() -> None:
            self.ignored_by_filter_count += 1

        # Dosyaları tara ve değişiklikleri karşılaştır
        self._notify("tarama", t("backup_notify_scanning"))
        changed_items, unchanged_items, bytes_saved, deleted_files = scan_and_compare_entries(
            self.configs,
            self.custom_paths,
            base_manifest if self.backup_mode == "incremental" else None,
            ignore_filter=ignore_filter,
            home_dir=self.system_info.home_dir if self.system_info else None,
            on_ignored=_on_ignored
        )
        empty_dirs = collect_empty_dir_entries(
            self.configs,
            self.custom_paths,
            ignore_filter=ignore_filter,
            home_dir=self.system_info.home_dir if self.system_info else None
        )

        total_scanned = len(changed_items) + len(unchanged_items)
        changed_count = len(changed_items)
        unchanged_count = len(unchanged_items)
        deleted_count = len(deleted_files)

        # Tasarruf miktarını biçimlendir
        saved_str = format_bytes(bytes_saved)

        if self.backup_mode == "incremental":
            self._notify(
                "analiz",
                t(
                    "backup_notify_inc_analysis",
                    total=total_scanned,
                    changed=changed_count,
                    unchanged=unchanged_count,
                    deleted=deleted_count,
                    saved=saved_str
                ),
                total_count=total_scanned,
                processed_count=unchanged_count
            )

        self.incremental_stats = {
            "total_scanned": total_scanned,
            "changed_count": changed_count,
            "unchanged_count": unchanged_count,
            "deleted_count": deleted_count,
            "deleted": deleted_count,
            "bytes_saved": bytes_saved
        }
        self.deleted_files = deleted_files

        # ----------------- HEDEF: DİZİN (DIRECTORY FORMAT) -----------------
        if self.archive_format == "dir":
            if self.backup_mode == "incremental":
                if self.base_backup_path and self.output_path.resolve() == self.base_backup_path.resolve():
                    raise ValueError(t("backup_err_inc_dir_same_as_base", path=str(self.output_path)))

            if self.output_path.exists():
                if not self.output_path.is_dir() or any(self.output_path.iterdir()):
                    raise FileExistsError(t("backup_err_dir_exists_not_empty", path=str(self.output_path)))

            staging_target_dir = self.output_path.parent / f".tmp_dir_{os.getpid()}_{datetime.now().strftime('%H%M%S%f')}_{self.output_path.name}"
            staging_target_dir.mkdir(parents=True, exist_ok=True)
            target_dir = staging_target_dir

            try:
                os.chmod(target_dir, 0o700)
            except OSError:
                pass

            dir_success = False
            try:
                items_to_copy = changed_items if self.backup_mode == "incremental" else (changed_items + unchanged_items)
                total_items = len(items_to_copy) + 2

                copied_bytes = 0
                for idx, item in enumerate(items_to_copy, 1):
                    dst = target_dir / item["dest_rel"]
                    self._notify("kopyalama", t("backup_notify_copying_file", idx=idx, total=len(items_to_copy), dest=str(item['dest_rel'])), current_item=item['name'], processed_count=idx, total_count=len(items_to_copy))
                    copied_bytes += copy_single_file(item["src"], dst, item["rel_key"], self.file_hashes, self.file_metadata, skipped_files=self.skipped_files)

                # Boş dizinleri koru (ör. ~/.ssh/sockets, vim undodir)
                for dir_src, dir_dest_rel in empty_dirs:
                    try:
                        (target_dir / dir_dest_rel).mkdir(parents=True, exist_ok=True)
                    except OSError as e:
                        logger.warning(t("backup_warn_empty_dir_add_failed", path=str(dir_src), error=str(e)))

                # Paket listelerini doğrudan hedefe yaz
                packages_dir = target_dir / "packages"
                packages_dir.mkdir(parents=True, exist_ok=True)
                self._write_package_lists(packages_dir)

                # Sistem durum anlık görüntüsünü doğrudan hedefe yaz
                sys_state_info = self._write_system_state(target_dir / "system_state")

                # restore.sh doğrudan hedefe yaz
                restore_script_path = target_dir / "restore.sh"
                with open(restore_script_path, "w", encoding="utf-8") as f:
                    f.write(generate_standalone_restore_script())
                os.chmod(restore_script_path, os.stat(restore_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                # manifest.json doğrudan hedefe yaz
                if self.backup_mode == "incremental":
                    merged_meta = dict(chain_metadata)
                    merged_meta.update(self.file_metadata)
                    merged_hashes = dict(chain_hashes)
                    merged_hashes.update(self.file_hashes)
                    for del_key in deleted_files:
                        merged_meta.pop(del_key, None)
                        merged_hashes.pop(del_key, None)
                else:
                    merged_meta = self.file_metadata
                    merged_hashes = self.file_hashes

                merged_hashes = {k: v for k, v in merged_hashes.items() if v}

                manifest = BackupManifest(
                    created_at=datetime.now().isoformat(),
                    version="1.0.0",
                    system_info=self.system_info,
                    packages=self.packages,
                    configs=self.configs,
                    custom_paths=self.custom_paths,
                    archive_format="dir",
                    total_bytes=copied_bytes,
                    file_hashes=merged_hashes,
                    backup_type=self.backup_mode,
                    base_backup_name=self.base_backup_path.name if self.base_backup_path else "",
                    base_backup_created_at=base_manifest.created_at if base_manifest else "",
                    file_metadata=merged_meta,
                    incremental_stats=self.incremental_stats,
                    hardware_info=hw_info,
                    system_state_info=sys_state_info,
                    deleted_files=deleted_files,
                    skipped_files=self.skipped_files
                )
                with open(target_dir / "manifest.json", "w", encoding="utf-8") as f:
                    json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=True)

                if self.output_path.exists():
                    self.output_path.rmdir()
                os.replace(staging_target_dir, self.output_path)
                try:
                    os.chmod(self.output_path, 0o700)
                except OSError:
                    pass
                final_dir = self.output_path

                self._notify("senkronizasyon", t("backup_notify_syncing"), current_item=str(final_dir))
                _flush_to_disk(final_dir, is_dir=True)
                dir_success = True

                skip_note = t("backup_skip_note", count=len(self.skipped_files)) if self.skipped_files else ""
                success_msg = (
                    t("backup_success_inc", name=final_dir.name, count=changed_count, saved=saved_str, skip_note=skip_note)
                    if self.backup_mode == "incremental"
                    else t("backup_success_full", name=final_dir.name, skip_note=skip_note)
                )
                self._notify("tamamlandı", success_msg, current_item=str(final_dir), total_count=total_items, processed_count=total_items, percent=100.0)
                return final_dir
            except OSError as e:
                if getattr(e, "errno", None) == errno.EFBIG or "File too large" in str(e):
                    if staging_target_dir and staging_target_dir.exists():
                        shutil.rmtree(staging_target_dir, ignore_errors=True)
                    if self.output_path.exists():
                        shutil.rmtree(self.output_path, ignore_errors=True)
                    err_msg = t("err_efbig_fat32_limit")
                    self._notify("hata", err_msg)
                    raise Fat32LimitError(err_msg) from e
                raise
            finally:
                if not dir_success and staging_target_dir and staging_target_dir.exists():
                    shutil.rmtree(staging_target_dir, ignore_errors=True)

        # ----------------- HEDEF: ARŞİV (.tar.zst / .tar.gz / .tar) -----------------
        # Dosyaları /tmp altına kopyalamadan doğrudan arşive akıt (streaming tar.add).
        # Yalnızca manifest/küçük meta dosyalar için hedef dizinde geçici meta alanı oluştur.
        meta_staging_dir = Path(tempfile.mkdtemp(prefix="distronomad_meta_", dir=self.output_path.parent))
        try:
            os.chmod(meta_staging_dir, 0o700)
        except OSError:
            pass
        temp_raw_archive = self.output_path.parent / f".tmp_raw_{os.getpid()}_{datetime.now().strftime('%H%M%S%f')}_{self.output_path.name}"
        temp_enc_archive: Optional[Path] = None
        archive_success = False
        try:
            items_to_copy = changed_items if self.backup_mode == "incremental" else (changed_items + unchanged_items)
            total_items = len(items_to_copy) + 2

            # 1. Aşama: Paket listelerini ve sistem durumunu (system_state) meta dizinine yaz
            packages_dir = meta_staging_dir / "packages"
            packages_dir.mkdir(parents=True, exist_ok=True)
            self._write_package_lists(packages_dir)
            sys_state_info = self._write_system_state(meta_staging_dir / "system_state")

            # 2. Aşama: Dosyaların hash ve boyut metadatasını kaynaktan doğrudan çıkar
            copied_bytes = 0
            for idx, item in enumerate(items_to_copy, 1):
                src = item["src"]
                rel_key = item["rel_key"]
                if not src.exists() and not src.is_symlink():
                    continue
                try:
                    st_mode = src.lstat().st_mode
                    if stat.S_ISSOCK(st_mode) or stat.S_ISFIFO(st_mode) or stat.S_ISCHR(st_mode) or stat.S_ISBLK(st_mode):
                        continue
                except (OSError, PermissionError, FileNotFoundError):
                    continue

                if src.is_symlink():
                    try:
                        link_target = os.readlink(src)
                        self.file_metadata[rel_key] = {
                            "type": "symlink",
                            "link_target": link_target,
                            "size": 0,
                            "mtime": 0.0
                        }
                        self.file_hashes[rel_key] = f"symlink:{link_target}"
                        self.file_hashes[str(item["dest_rel"]).replace("\\", "/")] = f"symlink:{link_target}"
                    except OSError:
                        pass
                elif src.is_file():
                    try:
                        st = src.stat()
                        # Dosya okunabilir mi kontrol et ve hash hesapla
                        with open(src, "rb") as _test_f:
                            _test_f.read(1)
                        h = compute_sha256(src)
                        if not h:
                            raise OSError(t("backup_err_hash_failed", path=str(src)))
                        self.file_hashes[rel_key] = h
                        self.file_hashes[str(item["dest_rel"]).replace("\\", "/")] = h
                        self.file_metadata[rel_key] = {
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "sha256": h
                        }
                        copied_bytes += st.st_size
                    except (OSError, PermissionError, FileNotFoundError) as e:
                        logger.warning(t("backup_warn_read_failed", path=str(src), error=str(e)))
                        self.file_hashes.pop(rel_key, None)
                        self.file_hashes.pop(str(item["dest_rel"]).replace("\\", "/"), None)
                        self.file_metadata.pop(rel_key, None)
                        if not any(sf["path"] == str(src) for sf in self.skipped_files):
                            self.skipped_files.append({"path": str(src), "reason": str(e)})

            # 3. Aşama: restore.sh
            restore_script_path = meta_staging_dir / "restore.sh"
            with open(restore_script_path, "w", encoding="utf-8") as f:
                f.write(generate_standalone_restore_script())
            os.chmod(restore_script_path, os.stat(restore_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            # 4. Aşama: manifest.json
            # Arşivin başına ön tarama hash'leriyle hızlı inceleme manifesti yazılır; akış bitince arşive yazılan
            # baytların hash'leriyle kesin manifest arşivin sonuna eklenir (doğrulama ve geri yükleme sondakini kullanır).
            created_at = datetime.now().isoformat()

            def _write_manifest(changed_during_backup: List[str]) -> None:
                if self.backup_mode == "incremental":
                    merged_meta = dict(chain_metadata)
                    merged_meta.update(self.file_metadata)
                    merged_hashes = dict(chain_hashes)
                    merged_hashes.update(self.file_hashes)
                    for del_key in deleted_files:
                        merged_meta.pop(del_key, None)
                        merged_hashes.pop(del_key, None)
                else:
                    merged_meta = self.file_metadata
                    merged_hashes = self.file_hashes

                merged_hashes = {k: v for k, v in merged_hashes.items() if v}

                manifest = BackupManifest(
                    created_at=created_at,
                    version="1.0.0",
                    system_info=self.system_info,
                    packages=self.packages,
                    configs=self.configs,
                    custom_paths=self.custom_paths,
                    archive_format=self.archive_format,
                    total_bytes=copied_bytes,
                    file_hashes=merged_hashes,
                    backup_type=self.backup_mode,
                    base_backup_name=self.base_backup_path.name if self.base_backup_path else "",
                    base_backup_created_at=base_manifest.created_at if base_manifest else "",
                    file_metadata=merged_meta,
                    incremental_stats=self.incremental_stats,
                    hardware_info=hw_info,
                    system_state_info=sys_state_info,
                    deleted_files=deleted_files,
                    skipped_files=self.skipped_files,
                    changed_during_backup=changed_during_backup
                )
                with open(meta_staging_dir / "manifest.json", "w", encoding="utf-8") as f:
                    json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=True)

            _write_manifest([])

            # 5. Aşama: Arşive doğrudan akıt (Streaming tar.add)
            self._notify("sıkıştırma", t("backup_notify_packing_archive", format=self.archive_format), total_count=total_items, processed_count=0)

            def _populate_tar(tar_obj):
                manifest_f = meta_staging_dir / "manifest.json"
                if manifest_f.exists():
                    tar_obj.add(manifest_f, arcname="manifest.json")
                restore_f = meta_staging_dir / "restore.sh"
                if restore_f.exists():
                    tar_obj.add(restore_f, arcname="restore.sh")
                pkg_d = meta_staging_dir / "packages"
                if pkg_d.exists():
                    for p in pkg_d.iterdir():
                        tar_obj.add(p, arcname=f"packages/{p.name}")
                sys_d = meta_staging_dir / "system_state"
                if sys_d.exists():
                    tar_obj.add(sys_d, arcname="system_state")

                changed_during_backup: List[str] = []
                skipped_paths = {sf["path"] for sf in self.skipped_files}
                for idx, item in enumerate(items_to_copy, 1):
                    src = item["src"]
                    if str(src) in skipped_paths:
                        continue
                    if not src.exists() and not src.is_symlink():
                        continue
                    try:
                        st_mode = src.lstat().st_mode
                        if stat.S_ISSOCK(st_mode) or stat.S_ISFIFO(st_mode) or stat.S_ISCHR(st_mode) or stat.S_ISBLK(st_mode):
                            continue
                    except (OSError, PermissionError, FileNotFoundError):
                        continue
                    self._notify("kopyalama", t("backup_notify_adding_to_archive", idx=idx, total=len(items_to_copy), dest=str(item['dest_rel'])), current_item=item['name'], processed_count=idx, total_count=len(items_to_copy))
                    arcname = str(item["dest_rel"]).replace("\\", "/")
                    try:
                        if src.is_file() and not src.is_symlink():
                            # Hash, arşive yazılan baytlardan aynı okumada alınır; dosya ön taramadan sonra
                            # değiştiyse (ör. açık tarayıcının veritabanı) arşivdeki anlık hâli kayda geçer.
                            streamed_hash, shrank = add_file_with_hash(tar_obj, src, arcname)
                            if shrank or self.file_hashes.get(item["rel_key"]) != streamed_hash:
                                changed_during_backup.append(arcname)
                            self.file_hashes[item["rel_key"]] = streamed_hash
                            self.file_hashes[arcname] = streamed_hash
                            if item["rel_key"] in self.file_metadata:
                                self.file_metadata[item["rel_key"]]["sha256"] = streamed_hash
                        else:
                            tar_obj.add(src, arcname=arcname, recursive=False)
                    except (OSError, PermissionError, FileNotFoundError) as e:
                        if getattr(e, "errno", None) == errno.EFBIG or "File too large" in str(e):
                            raise
                        logger.warning(t("backup_warn_archive_add_failed", path=str(src), error=str(e)))
                        self.file_hashes.pop(item["rel_key"], None)
                        self.file_hashes.pop(arcname, None)
                        self.file_metadata.pop(item["rel_key"], None)
                        if not any(sf["path"] == str(src) for sf in self.skipped_files):
                            self.skipped_files.append({"path": str(src), "reason": str(e)})

                # Boş dizinleri dizin girdisi olarak ekle (ör. ~/.ssh/sockets, vim undodir)
                for dir_src, dir_dest_rel in empty_dirs:
                    try:
                        tar_obj.add(dir_src, arcname=str(dir_dest_rel).replace("\\", "/"), recursive=False)
                    except (OSError, PermissionError, FileNotFoundError) as e:
                        if getattr(e, "errno", None) == errno.EFBIG or "File too large" in str(e):
                            raise
                        logger.warning(t("backup_warn_empty_dir_add_failed", path=str(dir_src), error=str(e)))

                if changed_during_backup:
                    warn_msg = t("backup_warn_changed_during_backup", count=len(changed_during_backup), examples=", ".join(changed_during_backup[:3]))
                    logger.warning(warn_msg)
                    self._notify("uyarı", warn_msg)

                # Kesin manifest arşivin sonuna; açılırken baştakinin üzerine yazılır ve doğrulama sondakini kullanır
                _write_manifest(changed_during_backup)
                tar_obj.add(meta_staging_dir / "manifest.json", arcname="manifest.json")

            raw_fd = os.open(temp_raw_archive, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            if self.archive_format == "tar.zst":
                try:
                    import zstandard as zstd
                    cctx = zstd.ZstdCompressor(level=DEFAULT_ZSTD_COMPRESSION_LEVEL)
                    with open(raw_fd, "wb") as f_out:
                        with cctx.stream_writer(f_out) as writer:
                            with tarfile.open(fileobj=writer, mode="w|") as tar_obj:
                                _populate_tar(tar_obj)
                except ImportError:
                    os.close(raw_fd)
                    temp_raw_archive.unlink(missing_ok=True)
                    out_gz = Path(str(temp_raw_archive).replace(".tar.zst", ".tar.gz"))
                    raw_fd = os.open(out_gz, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                    with open(raw_fd, "wb") as f_out:
                        with tarfile.open(fileobj=f_out, mode="w:gz") as tar_obj:
                            _populate_tar(tar_obj)
                    temp_raw_archive = out_gz
            elif self.archive_format == "tar.gz":
                with open(raw_fd, "wb") as f_out:
                    with tarfile.open(fileobj=f_out, mode="w:gz") as tar_obj:
                        _populate_tar(tar_obj)
            elif self.archive_format == "tar":
                with open(raw_fd, "wb") as f_out:
                    with tarfile.open(fileobj=f_out, mode="w") as tar_obj:
                        _populate_tar(tar_obj)
            else:
                os.close(raw_fd)
                raise ValueError(t("archive_err_unsupported_format", path=self.archive_format))

            # 6. Aşama: Şifreleme ve Atomik Yerleştirme
            if self.password:
                self._notify("şifreleme", t("backup_notify_encrypting_archive"))
                temp_enc_archive = self.output_path.parent / f".tmp_enc_{os.getpid()}_{datetime.now().strftime('%H%M%S%f')}_{self.output_path.name}"
                encrypt_archive(temp_raw_archive, temp_enc_archive, self.password)
                os.replace(temp_enc_archive, self.output_path)
            else:
                os.replace(temp_raw_archive, self.output_path)

            try:
                os.chmod(self.output_path, 0o600)
            except OSError:
                pass

            final_path = self.output_path
            self._notify("senkronizasyon", t("backup_notify_syncing"), current_item=str(final_path))
            _flush_to_disk(final_path, is_dir=False)
            archive_success = True

            skip_note = t("backup_skip_note", count=len(self.skipped_files)) if self.skipped_files else ""
            success_msg = (
                t("backup_success_inc", name=final_path.name, count=changed_count, saved=saved_str, skip_note=skip_note)
                if self.backup_mode == "incremental"
                else t("backup_success_full", name=final_path.name, skip_note=skip_note)
            )
            self._notify("tamamlandı", success_msg, current_item=str(final_path), total_count=total_items, processed_count=total_items, percent=100.0)
            return final_path

        except OSError as e:
            if getattr(e, "errno", None) == errno.EFBIG or "File too large" in str(e):
                if temp_raw_archive.exists():
                    temp_raw_archive.unlink(missing_ok=True)
                if temp_enc_archive and temp_enc_archive.exists():
                    temp_enc_archive.unlink(missing_ok=True)
                # self.output_path'e DOKUNMA: yeni arşiv yalnızca başarıyla tamamlanınca os.replace ile yerleşir;
                # bu noktada o yolda duran dosya aynı hedefe daha önce alınmış sağlam yedektir.
                shutil.rmtree(meta_staging_dir, ignore_errors=True)
                err_msg = t("err_efbig_fat32_limit")
                self._notify("hata", err_msg)
                raise Fat32LimitError(err_msg) from e
            raise

        finally:
            shutil.rmtree(meta_staging_dir, ignore_errors=True)
            if temp_raw_archive.exists():
                temp_raw_archive.unlink(missing_ok=True)
            if temp_enc_archive and temp_enc_archive.exists():
                temp_enc_archive.unlink(missing_ok=True)

    def _write_package_lists(self, target_dir: Path, packages: Optional[List[PackageItem]] = None):
        """Paket yöneticilerine göre ayrılmış düz metin listeleri yazar."""
        pkgs_source = packages if packages is not None else self.packages
        system_pkgs = []
        flatpaks = []
        snaps = []
        pipx_pkgs = []
        cargo_pkgs = []
        npm_pkgs = []
        vscode_exts = []

        for p in pkgs_source:
            if p.manager in ["pacman", "apt", "dnf", "zypper"]:
                system_pkgs.append(p.name)
            elif p.manager == "flatpak":
                flatpaks.append(p.app_id or p.name)
            elif p.manager == "snap":
                snaps.append(p.name)
            elif p.manager == "pipx":
                pipx_pkgs.append(p.name)
            elif p.manager == "cargo":
                cargo_pkgs.append(p.name)
            elif p.manager == "npm":
                npm_pkgs.append(p.name)
            elif p.manager in ["vscode", "code", "codium"]:
                vscode_exts.append(p.name)

        if system_pkgs:
            with open(target_dir / "system_packages.txt", "w", encoding="utf-8") as f:
                f.write(f"# {self.system_info.distro_name} Sistem Paketleri\n")
                f.write("\n".join(system_pkgs) + "\n")

        if flatpaks:
            with open(target_dir / "flatpaks.txt", "w", encoding="utf-8") as f:
                f.write("# Flatpak Uygulamaları (Flathub)\n")
                f.write("\n".join(flatpaks) + "\n")

        if snaps:
            with open(target_dir / "snaps.txt", "w", encoding="utf-8") as f:
                f.write("# Snap Uygulamaları\n")
                f.write("\n".join(snaps) + "\n")

        if pipx_pkgs:
            with open(target_dir / "pipx.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(pipx_pkgs) + "\n")

        if cargo_pkgs:
            with open(target_dir / "cargo.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(cargo_pkgs) + "\n")

        if npm_pkgs:
            with open(target_dir / "npm.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(npm_pkgs) + "\n")

        if vscode_exts:
            with open(target_dir / "vscode_extensions.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(vscode_exts) + "\n")

    def _write_system_state(self, state_dir: Path) -> Dict[str, Any]:
        """
        GNOME/Cinnamon dconf veritabanını, kullanıcı crontab görevlerini,
        sistem kullanıcı gruplarını, etkin systemd --user servislerini ve
        okunabilir /etc referans dosyalarını (fstab, hosts, udev vb.) kaydeder.
        """
        import subprocess
        state_dir.mkdir(parents=True, exist_ok=True)
        info: Dict[str, Any] = {
            "has_dconf": False,
            "has_crontab": False,
            "user_groups": [],
            "systemd_user_units": [],
            "etc_reference_files": [],
            "network_secrets_skipped": False,
            "network_secrets_note": None
        }

        # 1. dconf dump (GNOME / Cinnamon / MATE masaüstü ve eklenti ayarları)
        if shutil.which("dconf"):
            try:
                proc = subprocess.run(
                    ["dconf", "dump", "/"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    (state_dir / "dconf-settings.ini").write_text(proc.stdout, encoding="utf-8")
                    info["has_dconf"] = True
            except Exception:
                pass

        # 2. Kullanıcı Crontab görevleri (crontab -l)
        if shutil.which("crontab"):
            try:
                proc = subprocess.run(
                    ["crontab", "-l"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if proc.returncode == 0 and proc.stdout.strip() and "no crontab" not in proc.stderr.lower():
                    (state_dir / "crontab.txt").write_text(proc.stdout, encoding="utf-8")
                    info["has_crontab"] = True
            except Exception:
                pass

        # 3. Kullanıcının dahil olduğu sistem grupları (docker, wheel, dialout, input, libvirt vb.)
        try:
            import grp
            gids = os.getgroups()
            group_names = []
            for gid in gids:
                try:
                    group_names.append(grp.getgrgid(gid).gr_name)
                except KeyError:
                    continue
            if group_names:
                (state_dir / "user_groups.txt").write_text("\n".join(group_names) + "\n", encoding="utf-8")
                info["user_groups"] = group_names
        except Exception:
            pass

        # 4. Etkinleştirilmiş systemd --user birimleri
        if shutil.which("systemctl"):
            try:
                proc = subprocess.run(
                    ["systemctl", "--user", "list-unit-files", "--state=enabled", "--no-legend"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    units = []
                    for line in proc.stdout.strip().splitlines():
                        parts = line.split()
                        if parts:
                            units.append(parts[0])
                    if units:
                        (state_dir / "systemd_user_enabled.txt").write_text("\n".join(units) + "\n", encoding="utf-8")
                        info["systemd_user_units"] = units
            except Exception:
                pass

        # 5. Sistem /etc Referans Dosyaları (fstab, hosts, environment, udev rules, NetworkManager)
        etc_ref_dir = state_dir / "etc_reference"
        if self.etc_dir.exists():
            single_etc_files = ["fstab", "hosts", "environment", "locale.conf", "vconsole.conf"]
            for fname in single_etc_files:
                src_f = self.etc_dir / fname
                if src_f.exists() and src_f.is_file() and os.access(src_f, os.R_OK):
                    try:
                        etc_ref_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_f, etc_ref_dir / fname)
                        info["etc_reference_files"].append(fname)
                    except Exception:
                        pass

            # /etc/udev/rules.d/ ve /etc/NetworkManager/system-connections/ ve /etc/wireguard/
            general_subdirs = [
                ("udev/rules.d", "udev_rules.d"),
                ("modprobe.d", "modprobe.d"),
            ]
            secret_subdirs = [
                ("NetworkManager/system-connections", "NetworkManager"),
                ("wireguard", "wireguard"),
            ]

            if self.password:
                etc_subdirs = general_subdirs + secret_subdirs
                info["network_secrets_skipped"] = False
            else:
                etc_subdirs = general_subdirs
                info["network_secrets_skipped"] = True
                info["network_secrets_note"] = t("backup_system_state_network_secrets_skipped_note")
                self._notify("bilgi", t("backup_notify_network_secrets_skipped"))
            for rel_sub, dst_sub_name in etc_subdirs:
                src_sub = self.etc_dir / rel_sub
                if src_sub.exists() and src_sub.is_dir() and os.access(src_sub, os.R_OK):
                    try:
                        for entry in os.scandir(src_sub):
                            if entry.is_file() and os.access(entry.path, os.R_OK):
                                dst_sub = etc_ref_dir / dst_sub_name
                                dst_sub.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(entry.path, dst_sub / entry.name)
                                info["etc_reference_files"].append(f"{dst_sub_name}/{entry.name}")
                    except Exception:
                        pass

        return info

    def _finalize_archive(self, staging_dir: Path) -> Path:
        """Hazırlanan dizini istenen formata dönüştürür."""
        if self.archive_format == "dir":
            if self.output_path.exists():
                if not self.output_path.is_dir() or any(self.output_path.iterdir()):
                    raise FileExistsError(f"Hedef dizin zaten mevcut ve boş değil: {self.output_path}")
            safe_copytree(staging_dir, self.output_path, symlinks=True, dirs_exist_ok=True)
            try:
                os.chmod(self.output_path, 0o700)
            except OSError:
                pass
            return self.output_path

        elif self.archive_format == "tar":
            with tarfile.open(self.output_path, "w") as tar:
                manifest_entry = staging_dir / "manifest.json"
                if manifest_entry.exists():
                    tar.add(manifest_entry, arcname="manifest.json")
                for entry in staging_dir.iterdir():
                    if entry.name != "manifest.json":
                        tar.add(entry, arcname=entry.name)
            try:
                os.chmod(self.output_path, 0o600)
            except OSError:
                pass
            return self.output_path

        elif self.archive_format == "tar.gz":
            with tarfile.open(self.output_path, "w:gz") as tar:
                manifest_entry = staging_dir / "manifest.json"
                if manifest_entry.exists():
                    tar.add(manifest_entry, arcname="manifest.json")
                for entry in staging_dir.iterdir():
                    if entry.name != "manifest.json":
                        tar.add(entry, arcname=entry.name)
            try:
                os.chmod(self.output_path, 0o600)
            except OSError:
                pass
            return self.output_path

        elif self.archive_format == "tar.zst":
            try:
                import zstandard as zstd
                cctx = zstd.ZstdCompressor(level=DEFAULT_ZSTD_COMPRESSION_LEVEL)
                with open(self.output_path, "wb") as f_out:
                    with cctx.stream_writer(f_out) as writer:
                        with tarfile.open(fileobj=writer, mode="w|") as tar:
                            manifest_entry = staging_dir / "manifest.json"
                            if manifest_entry.exists():
                                tar.add(manifest_entry, arcname="manifest.json")
                            for entry in staging_dir.iterdir():
                                if entry.name not in ("archive.tar", "manifest.json"):
                                    tar.add(entry, arcname=entry.name)
                try:
                    os.chmod(self.output_path, 0o600)
                except OSError:
                    pass
                return self.output_path
            except ImportError:
                if shutil.which("tar") and shutil.which("zstd"):
                    import subprocess
                    subprocess.run(
                        ["tar", "--zstd", "-cf", str(self.output_path), "-C", str(staging_dir), "."],
                        check=True
                    )
                    try:
                        os.chmod(self.output_path, 0o600)
                    except OSError:
                        pass
                    return self.output_path
                else:
                    out_gz = Path(str(self.output_path).replace(".tar.zst", ".tar.gz"))
                    with tarfile.open(out_gz, "w:gz") as tar:
                        for entry in staging_dir.iterdir():
                            tar.add(entry, arcname=entry.name)
                    try:
                        os.chmod(out_gz, 0o600)
                    except OSError:
                        pass
                    return out_gz

        try:
            os.chmod(self.output_path, 0o600 if self.archive_format != "dir" else 0o700)
        except OSError:
            pass
        return self.output_path

    def _extract_existing_archive(self, archive_file: Path, target_dir: Path, archive_format: Optional[str] = None) -> None:
        """tar.zst, tar.gz veya tar arşivini ara dosya oluşturmadan hedef dizine güvenli şekilde açar."""
        extract_tar_archive(archive_file, target_dir, archive_format=archive_format)

    def _run_append_backup(self) -> Path:
        """
        Var olan bir yedek arşivine veya dizinine yeni seçilen uygulama,
        ayar veya özel dosyaları ekler / günceller.
        """
        target_path = self.output_path
        if not target_path.exists() and self.base_backup_path and self.base_backup_path.exists():
            target_path = self.base_backup_path

        if not target_path.exists():
            raise FileNotFoundError(t("backup_err_append_target_not_found", path=str(target_path)))

        is_dir = target_path.is_dir()
        is_enc = is_encrypted_file(target_path) if not is_dir else False
        temp_dec_path: Optional[Path] = None
        staging_dir: Optional[Path] = None
        temp_unenc: Optional[Path] = None
        actual_source_path = target_path

        local_tmp_base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "distronomad" / "tmp"
        if is_enc:
            local_tmp_base.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(local_tmp_base, 0o700)
            except OSError:
                pass

        staging_dir_parent = local_tmp_base if is_enc else target_path.parent

        # Orijinal iç arşiv formatını gerçek durumundan tespit et
        if is_dir:
            detected_format = "dir"
        elif is_enc:
            if not self.password:
                raise ValueError(t("backup_err_append_password_required"))
            fd, tmp_name = tempfile.mkstemp(prefix="distronomad_dec_", dir=local_tmp_base)
            os.close(fd)
            temp_dec_path = Path(tmp_name)
            try:
                os.chmod(temp_dec_path, 0o600)
            except OSError:
                pass
            decrypt_archive(target_path, temp_dec_path, self.password)
            actual_source_path = temp_dec_path
            detected_format = detect_backup_format(temp_dec_path)
        else:
            detected_format = detect_backup_format(target_path)

        try:
            staging_dir = Path(tempfile.mkdtemp(prefix="distronomad_append_staging_", dir=staging_dir_parent))
            try:
                os.chmod(staging_dir, 0o700)
            except OSError:
                pass
            self._notify("açma", t("backup_notify_opening_existing", name=target_path.name))
            if detected_format == "dir":
                safe_copytree(actual_source_path, staging_dir, symlinks=True, dirs_exist_ok=True)
            else:
                self._extract_existing_archive(actual_source_path, staging_dir, archive_format=detected_format)

            if temp_dec_path and temp_dec_path.exists():
                temp_dec_path.unlink(missing_ok=True)
                temp_dec_path = None

            manifest_file = staging_dir / "manifest.json"
            if not manifest_file.exists():
                raise ValueError(t("backup_err_append_manifest_not_found", path=str(target_path)))

            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            manifest = BackupManifest.from_dict(data)

            # Akıllı filtre
            ignore_filter = None
            if self.use_smart_ignore:
                ignore_filter = SmartIgnoreFilter(
                    use_smart_defaults=True,
                    custom_patterns=self.custom_ignore_patterns
                )

            # 1. Aşama: Paketleri Birleştir
            pkg_map = {(p.name, p.manager): p for p in manifest.packages}
            new_pkgs_count = 0
            for p in self.packages:
                if (p.name, p.manager) not in pkg_map:
                    new_pkgs_count += 1
                pkg_map[(p.name, p.manager)] = p
            merged_packages = list(pkg_map.values())

            packages_dir = staging_dir / "packages"
            packages_dir.mkdir(parents=True, exist_ok=True)
            self._write_package_lists(packages_dir, packages=merged_packages)

            # 2. Aşama: Konfigürasyonları Birleştir
            configs_dir = staging_dir / "configs"
            configs_dir.mkdir(parents=True, exist_ok=True)
            cfg_map = {c.id: c for c in manifest.configs}
            new_cfgs_count = 0

            for idx, cfg in enumerate(self.configs, 1):
                src_path = Path(cfg.source_path)
                if not src_path.exists() and not src_path.is_symlink():
                    continue

                self._notify("konfigürasyon", t("backup_notify_append_config", idx=idx, total=len(self.configs), name=cfg.name), current_item=cfg.name)
                dst_base = configs_dir / cfg.rel_path
                base_rel_key = f"configs/{cfg.rel_path}"

                if src_path.is_dir():
                    dst_base.mkdir(parents=True, exist_ok=True)

                for entry_path, inner_rel in walk_and_filter_files(src_path, ignore_filter=ignore_filter):
                    if inner_rel is not None:
                        dst_path = dst_base / inner_rel
                        rel_key = f"{base_rel_key}/{inner_rel}"
                    else:
                        dst_path = dst_base
                        rel_key = base_rel_key
                    copy_single_file(entry_path, dst_path, rel_key, self.file_hashes, self.file_metadata, skipped_files=self.skipped_files)

                for _dir_src, inner_rel in walk_empty_dirs(src_path, ignore_filter=ignore_filter, is_custom_path=False):
                    (dst_base / inner_rel if inner_rel is not None else dst_base).mkdir(parents=True, exist_ok=True)

                if cfg.id not in cfg_map:
                    new_cfgs_count += 1
                cfg_map[cfg.id] = cfg
            merged_configs = list(cfg_map.values())

            # 3. Aşama: Özel Dosyaları ve Klasörleri Birleştir
            custom_dir = staging_dir / "custom"
            custom_dir.mkdir(parents=True, exist_ok=True)
            custom_map = {cp.path: cp for cp in manifest.custom_paths}
            new_custom_count = 0

            effective_home = Path(self.system_info.home_dir).resolve() if self.system_info and self.system_info.home_dir else Path.home().resolve()

            for idx, cp in enumerate(self.custom_paths, 1):
                src_path = Path(cp.path)
                if not src_path.exists() and not src_path.is_symlink():
                    continue

                item_name = src_path.name
                self._notify("dosyalar", t("backup_notify_append_custom", idx=idx, total=len(self.custom_paths), name=item_name), current_item=item_name)

                is_home = False
                rel_to_home = None
                try:
                    rel_to_home = src_path.resolve().relative_to(effective_home)
                    is_home = True
                except ValueError:
                    pass

                if not is_home:
                    try:
                        rel_to_home = src_path.relative_to(Path(self.system_info.home_dir) if self.system_info and self.system_info.home_dir else Path.home())
                        is_home = True
                    except ValueError:
                        pass

                if is_home:
                    base_dest_rel = Path("custom/home") / rel_to_home
                else:
                    rel_to_root = Path(str(src_path).lstrip("/"))
                    base_dest_rel = Path("custom/root") / rel_to_root

                dst_path = staging_dir / base_dest_rel
                if src_path.is_dir():
                    dst_path.mkdir(parents=True, exist_ok=True)

                base_rel_key = str(base_dest_rel).replace("\\", "/")

                for entry_path, inner_rel in walk_and_filter_files(src_path, ignore_filter=ignore_filter, is_custom_path=True):
                    if inner_rel is not None:
                        item_dest_rel = base_dest_rel / inner_rel
                    else:
                        item_dest_rel = base_dest_rel
                    rel_key = str(item_dest_rel).replace("\\", "/")
                    copy_single_file(entry_path, staging_dir / item_dest_rel, rel_key, self.file_hashes, self.file_metadata, skipped_files=self.skipped_files)

                for _dir_src, inner_rel in walk_empty_dirs(src_path, ignore_filter=ignore_filter, is_custom_path=True):
                    (staging_dir / base_dest_rel / inner_rel if inner_rel is not None else staging_dir / base_dest_rel).mkdir(parents=True, exist_ok=True)

                if cp.path not in custom_map:
                    new_custom_count += 1
                custom_map[cp.path] = cp
            merged_custom = list(custom_map.values())

            # 4. Aşama: restore.sh Güncelle
            restore_script_path = staging_dir / "restore.sh"
            with open(restore_script_path, "w", encoding="utf-8") as f:
                f.write(generate_standalone_restore_script())
            os.chmod(restore_script_path, os.stat(restore_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            # 5. Aşama: manifest.json Güncelle
            manifest.packages = merged_packages
            manifest.configs = merged_configs
            manifest.custom_paths = merged_custom
            if manifest.file_metadata is None:
                manifest.file_metadata = {}
            manifest.file_metadata.update(self.file_metadata)
            if manifest.file_hashes is None:
                manifest.file_hashes = {}
            manifest.file_hashes.update(self.file_hashes)

            if manifest.skipped_files is None:
                manifest.skipped_files = []
            existing_skipped_paths = {sf["path"] for sf in manifest.skipped_files if isinstance(sf, dict) and "path" in sf}
            for sf in self.skipped_files:
                if sf["path"] not in existing_skipped_paths:
                    manifest.skipped_files.append(sf)
                    existing_skipped_paths.add(sf["path"])

            total_bytes = 0
            for f in staging_dir.rglob("*"):
                if not f.is_symlink() and f.is_file():
                    total_bytes += f.stat().st_size
            manifest.total_bytes = total_bytes

            with open(staging_dir / "manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=True)

            # 6. Aşama: Arşivi Yeniden Paketle
            self._notify("sıkıştırma", t("backup_notify_updating_archive", format=detected_format))

            if detected_format == "dir":
                safe_copytree(staging_dir, target_path, symlinks=True, dirs_exist_ok=True)
                final_target = target_path
            elif self.password or is_enc:
                enc_pwd = self.password
                final_enc_target = target_path if str(target_path).endswith(".enc") else Path(f"{target_path}.enc")
                temp_unenc = local_tmp_base / f"{target_path.stem}_unenc_{datetime.now().strftime('%H%M%S%f')}_{os.getpid()}.{detected_format}"
                temp_enc = target_path.parent / f"{final_enc_target.name}.tmp_{datetime.now().strftime('%H%M%S%f')}_{os.getpid()}"

                old_out = self.output_path
                old_fmt = self.archive_format
                self.output_path = temp_unenc
                self.archive_format = detected_format
                try:
                    self._finalize_archive(staging_dir)
                    self._notify("şifreleme", t("backup_notify_reencrypting_archive"))
                    encrypt_archive(temp_unenc, temp_enc, enc_pwd)
                    os.replace(temp_enc, final_enc_target)
                    final_target = final_enc_target
                finally:
                    self.output_path = old_out
                    self.archive_format = old_fmt
                    if temp_unenc.exists():
                        temp_unenc.unlink(missing_ok=True)
                    if temp_enc.exists():
                        temp_enc.unlink(missing_ok=True)
            else:
                temp_archive = target_path.parent / f"{target_path.stem}_tmp_{datetime.now().strftime('%H%M%S')}{target_path.suffix}"
                old_out = self.output_path
                old_fmt = self.archive_format
                self.output_path = temp_archive
                self.archive_format = detected_format
                try:
                    self._finalize_archive(staging_dir)
                    os.replace(temp_archive, target_path)
                    final_target = target_path
                finally:
                    self.output_path = old_out
                    self.archive_format = old_fmt
                    if temp_archive.exists():
                        temp_archive.unlink(missing_ok=True)

            try:
                os.chmod(final_target, 0o700 if final_target.is_dir() else 0o600)
            except OSError:
                pass

            self._notify("senkronizasyon", t("backup_notify_syncing"), current_item=str(final_target))
            _flush_to_disk(final_target, is_dir=final_target.is_dir())
            skip_note = t("backup_skip_note", count=len(self.skipped_files)) if self.skipped_files else ""
            success_msg = t(
                "backup_success_append",
                pkgs=new_pkgs_count,
                cfgs=new_cfgs_count,
                files=new_custom_count,
                name=final_target.name,
                skip_note=skip_note
            )
            self._notify("tamamlandı", success_msg, current_item=str(final_target), percent=100.0)
            return final_target
            return final_target

        except OSError as e:
            if getattr(e, "errno", None) == errno.EFBIG or "File too large" in str(e):
                if temp_dec_path and temp_dec_path.exists():
                    temp_dec_path.unlink(missing_ok=True)
                if staging_dir and staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                err_msg = t("err_efbig_fat32_limit")
                self._notify("hata", err_msg)
                raise Fat32LimitError(err_msg) from e
            raise

        finally:
            if temp_dec_path and temp_dec_path.exists():
                temp_dec_path.unlink(missing_ok=True)
            if staging_dir and staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if temp_unenc and temp_unenc.exists():
                temp_unenc.unlink(missing_ok=True)
