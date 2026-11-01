"""
DistroNomad - Yapılandırma Karşılaştırma ve Fark Görüntüleyici (Diff Engine).
Geri yükleme öncesinde mevcut sistem dosyaları ile yedeklenen dosyalar arasındaki farkları analiz eder.
Tembel (on-demand / lazy) yükleme mimarisiyle binlerce dosyayı UI'ı dondurmadan işler.
"""

from __future__ import annotations
import os
import difflib
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from distronomad.i18n import t, get_current_language

DEFAULT_MAX_DIFF_SIZE = 2 * 1024 * 1024  # 2 MB eşiği


def is_binary_file(file_path: Path) -> bool:
    """Dosyanın ikili (binary) olup olmadığını tespit eder."""
    try:
        if not file_path.exists():
            return False
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            if b"\0" in chunk:
                return True
        return False
    except Exception:
        return True


def determine_file_status(host_file: Path, backup_file: Path) -> str:
    """
    Dosya durumunu tam diff üretmeden (önce boyut, sonra içerik karşılaştırmasıyla) belirler.
    Döndürür: 'new_in_backup', 'binary', 'identical', 'modified'
    """
    if is_binary_file(backup_file):
        return "binary"

    if not host_file.exists():
        return "new_in_backup"

    if is_binary_file(host_file):
        return "binary"

    # Önce boyut karşılaştırması
    try:
        host_stat = host_file.stat()
        backup_stat = backup_file.stat()
        if host_stat.st_size != backup_stat.st_size:
            return "modified"
    except Exception:
        return "modified"

    # Boyutlar eşitse içerik karşılaştırması (64 KB bloklar)
    try:
        with open(host_file, "rb") as fh, open(backup_file, "rb") as fb:
            while True:
                b1 = fh.read(65536)
                b2 = fb.read(65536)
                if b1 != b2:
                    return "modified"
                if not b1:
                    break
        return "identical"
    except Exception:
        return "modified"


def generate_unified_diff(
    host_file: Optional[Path],
    backup_file: Optional[Path],
    host_label: Optional[str] = None,
    backup_label: Optional[str] = None,
    max_file_size: int = DEFAULT_MAX_DIFF_SIZE,
) -> Tuple[str, int, int]:
    """
    İki dosya arasındaki farkı unified diff formatında üretir.
    Döndürür: (diff_text, lines_added, lines_removed)
    Belirli bir eşiğin (max_file_size) üstündeki dosyalarda tam diff yerine açıklayıcı mesaj döner.
    """
    if host_label is None:
        host_label = t("diff_label_host")
    if backup_label is None:
        backup_label = t("diff_label_backup")

    if host_file and not host_file.exists():
        host_file = None
    if backup_file and not backup_file.exists():
        backup_file = None

    if host_file is None and backup_file is None:
        return (t("diff_file_not_found"), 0, 0)

    # İkili dosya kontrolü
    if (host_file and is_binary_file(host_file)) or (backup_file and is_binary_file(backup_file)):
        return (t("diff_binary_file"), 0, 0)

    # Boyut eşiği kontrolü (örn. 2 MB)
    for fpath in (host_file, backup_file):
        if fpath and fpath.exists():
            try:
                sz = fpath.stat().st_size
                if sz > max_file_size:
                    size_mb = sz / (1024 * 1024)
                    limit_mb = max_file_size / (1024 * 1024)
                    msg = t("diff_file_too_large", size_mb=size_mb, limit_mb=limit_mb)
                    return (msg, 0, 0)
            except Exception:
                pass

    lines_host: List[str] = []
    if host_file:
        try:
            with open(host_file, "r", encoding="utf-8", errors="replace") as f:
                lines_host = f.read().splitlines()
        except Exception as e:
            return (t("diff_host_file_read_error", error=str(e)), 0, 0)

    lines_backup: List[str] = []
    if backup_file:
        try:
            with open(backup_file, "r", encoding="utf-8", errors="replace") as f:
                lines_backup = f.read().splitlines()
        except Exception as e:
            return (t("diff_backup_file_read_error", error=str(e)), 0, 0)

    diff = list(difflib.unified_diff(
        lines_host,
        lines_backup,
        fromfile=host_label,
        tofile=backup_label,
        lineterm=""
    ))

    if not diff:
        return (t("diff_files_identical"), 0, 0)

    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    diff_text = "\n".join(diff)
    return (diff_text, added, removed)


def get_file_diff(
    host_file: Optional[Path | str],
    backup_file: Optional[Path | str],
    max_file_size: int = DEFAULT_MAX_DIFF_SIZE,
    host_label: Optional[str] = None,
    backup_label: Optional[str] = None,
) -> Tuple[str, int, int]:
    """Tek bir dosya için diff metni ve eklenen/silinen satır sayılarını üretir."""
    h = Path(host_file) if host_file else None
    b = Path(backup_file) if backup_file else None
    return generate_unified_diff(
        h,
        b,
        host_label=host_label,
        backup_label=backup_label,
        max_file_size=max_file_size,
    )


def compute_file_diff(
    item: Dict[str, Any],
    max_file_size: int = DEFAULT_MAX_DIFF_SIZE,
) -> Tuple[str, int, int]:
    """
    Tek bir dosya için istek üzerine (on-demand / lazy) unified diff metnini
    ve eklenen/silinen satır sayılarını üretir.
    Sonuçları verilen item sözlüğüne ('diff_text', 'added', 'removed') kaydeder.
    """
    if item.get("diff_text") is not None and item.get("added") is not None and item.get("removed") is not None:
        return (item["diff_text"], item["added"], item["removed"])

    status = item.get("status")
    host_p = Path(item["host_path"]) if item.get("host_path") else None
    backup_p = Path(item["backup_path"]) if item.get("backup_path") else None

    if status == "identical":
        diff_text, added, removed = (t("diff_files_identical"), 0, 0)
    elif status == "binary":
        diff_text, added, removed = (t("diff_binary_file"), 0, 0)
    else:
        diff_text, added, removed = generate_unified_diff(
            host_p,
            backup_p,
            max_file_size=max_file_size,
        )

    item["diff_text"] = diff_text
    item["added"] = added
    item["removed"] = removed
    return (diff_text, added, removed)


def compare_backup_with_host(
    backup_extracted_dir: Path,
    target_home: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Açılmış bir yedek dizinindeki tüm ayarları mevcut sistemin hedef diziniyle karşılaştırır.
    Tam diff metni üretmeden hızlı bir özet listesi döndürür (lazy/on-demand).
    Döndürür: Her bir dosya için fark özeti listesi.
    """
    home = Path(target_home or Path.home())
    summaries: List[Dict[str, Any]] = []

    def _scan_dir(source_base: Path, host_base: Path, category: str) -> None:
        if not source_base.exists():
            return
        for root, _, files in os.walk(source_base):
            root_path = Path(root)
            for file_name in files:
                backup_file = root_path / file_name
                rel_path = backup_file.relative_to(source_base)
                host_file = host_base / rel_path

                status = determine_file_status(host_file, backup_file)

                summaries.append({
                    "category": category,
                    "rel_path": str(rel_path),
                    "host_path": str(host_file),
                    "backup_path": str(backup_file),
                    "status": status,
                    "added": None,
                    "removed": None,
                    "diff_text": None,
                })

    # 1. Yapılandırma dosyaları (configs)
    _scan_dir(backup_extracted_dir / "configs", home, "config")

    # 2. Özel dosyalar (custom/home ve custom/root)
    custom_dir = backup_extracted_dir / "custom"
    if custom_dir.exists():
        _scan_dir(custom_dir / "home", home, "custom")
        _scan_dir(custom_dir / "root", Path("/"), "custom")

    return summaries
