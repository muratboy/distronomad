"""
DistroNomad - Bağımsız Yedek Bütünlük Doğrulama Motoru (Backup Integrity Verifier).
Arşivleri diske açmadan akış halinde SHA-256 sağlama toplamları ile doğrular.
"""

from __future__ import annotations
import os
import json
import tarfile
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any, Callable

from distronomad.models import BackupManifest, ProgressUpdate
from distronomad.archive import PrefixReader, open_archive_stream
from distronomad.i18n import t


@dataclass
class VerificationResult:
    """Yedek bütünlük doğrulama sonuç raporu."""
    archive_path: Path
    is_valid: bool
    total_files_checked: int = 0
    matched: List[str] = field(default_factory=list)
    mismatched: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    untracked: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    corrupted: bool = False
    error_message: Optional[str] = None
    chain_results: List["VerificationResult"] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _norm_path(p: str) -> str:
    """Yol dizesini karşılaştırma için standartlaştırır."""
    return p.replace("\\", "/").lstrip("./").lstrip("/")


def _is_distronomad_metadata(norm_name: str) -> bool:
    """DistroNomad iç meta veri dosyası olup olmadığını belirler."""
    if norm_name in ("manifest.json", "restore.sh"):
        return True
    if norm_name.startswith("packages/") or norm_name.startswith("system_state/"):
        return True
    return False


def verify_backup(
    backup_path: Path | str,
    password: Optional[str] = None,
    base_path: Optional[Path | str] = None,
    check_chain: bool = True,
    progress_callback: Optional[Callable[[ProgressUpdate], None]] = None
) -> VerificationResult:
    """
    Belirtilen yedek arşivinin (.tar.zst, .tar.gz, .tar, şifreli .enc) veya dizininin
    bütünlüğünü diske açmadan tek geçişte akış olarak doğrular.
    """
    p = Path(backup_path)
    if not p.exists():
        raise FileNotFoundError(t("verify_err_backup_not_found", path=str(p)))

    if p.is_dir():
        res = _verify_directory(p, progress_callback=progress_callback)
    else:
        res = _verify_archive_file(p, password=password, progress_callback=progress_callback)

    if res.corrupted or not res.manifest:
        return res

    manifest = res.manifest

    # Artımlı yedek zincir kontrolü
    if manifest.backup_type == "incremental" and check_chain:
        cand: Optional[Path] = None
        if base_path and Path(base_path).exists():
            cand = Path(base_path)
        elif manifest.base_backup_name:
            cand1 = p.parent / manifest.base_backup_name
            if cand1.exists():
                cand = cand1
            else:
                try:
                    cand2 = p.resolve().parent / manifest.base_backup_name
                    if cand2.exists():
                        cand = cand2
                except Exception:
                    pass

        if cand and cand.exists():
            try:
                base_res = verify_backup(
                    backup_path=cand,
                    password=password,
                    check_chain=True,
                    progress_callback=progress_callback
                )
                res.chain_results.append(base_res)

                chain_matched: set[str] = set()
                def _collect_matched(r: VerificationResult) -> None:
                    chain_matched.update(r.matched)
                    for cr in r.chain_results:
                        _collect_matched(cr)
                _collect_matched(base_res)

                # Taban zincirinde bulunamayan ve bu arşivde de olmayan dosyaları eksik olarak işaretle
                for norm_k in res.expected_files:
                    if norm_k not in res.seen_files and norm_k not in chain_matched:
                        res.missing.append(norm_k)
            except Exception as e:
                err_text = t("verify_warn_base_error", name=cand.name, error=str(e))
                res.warnings.append(err_text)
                res.error_message = err_text
        else:
            base_desc = manifest.base_backup_name or "belirtilmemiş"
            res.warnings.append(
                t("verify_warn_base_missing", base=base_desc)
            )
            res.error_message = t("verify_err_chain_missing", base=base_desc)

    if getattr(manifest, "skipped_files", None):
        for sf in manifest.skipped_files:
            if isinstance(sf, dict):
                res.skipped.append((sf.get("path", ""), sf.get("reason", t("verify_skipped_default"))))

    if getattr(manifest, "changed_during_backup", None):
        res.warnings.append(t(
            "verify_warn_changed_during_backup",
            count=len(manifest.changed_during_backup),
            examples=", ".join(manifest.changed_during_backup[:3])
        ))

    # Zincir kontrolü koşulu: Artımlı yedekte zincir kontrolü açıkken zincir doğrulanmış olmalı
    chain_ok = True
    if manifest.backup_type == "incremental" and check_chain:
        chain_ok = len(res.chain_results) > 0 and all(cr.is_valid for cr in res.chain_results)

    # Nihai geçerlilik kararı
    res.is_valid = (
        not res.corrupted
        and res.error_message is None
        and len(res.mismatched) == 0
        and len(res.missing) == 0
        and len(res.untracked) == 0
        and chain_ok
    )

    return res


class _InternalVerificationState(VerificationResult):
    """Doğrulama motorunun iç ara durumu."""
    manifest: Optional[BackupManifest] = None
    expected_files: Dict[str, str] = field(default_factory=dict)
    seen_files: Dict[str, str] = field(default_factory=dict)


def _verify_directory(
    dir_path: Path,
    progress_callback: Optional[Callable[[ProgressUpdate], None]] = None
) -> _InternalVerificationState:
    """Dizin formatındaki yedeği doğrular."""
    manifest_f = dir_path / "manifest.json"
    if not manifest_f.exists():
        return _InternalVerificationState(
            archive_path=dir_path,
            is_valid=False,
            corrupted=True,
            error_message=t("verify_err_manifest_not_found_in_dir")
        )

    try:
        with open(manifest_f, "r", encoding="utf-8", errors="surrogateescape") as f:
            manifest = BackupManifest.from_dict(json.load(f))
    except Exception as e:
        return _InternalVerificationState(
            archive_path=dir_path,
            is_valid=False,
            corrupted=True,
            error_message=t("verify_err_manifest_read_error", error=str(e))
        )

    expected_files: Dict[str, str] = {}
    for k, h in manifest.file_hashes.items():
        if h.startswith("symlink:") or h in ("__DELETED__", "deleted"):
            continue
        expected_files[_norm_path(k)] = h

    seen_files: Dict[str, str] = {}
    skipped: List[Tuple[str, str]] = []
    matched: List[str] = []
    mismatched: List[str] = []
    missing: List[str] = []
    untracked: List[str] = []

    for root, dirs, files in os.walk(dir_path):
        root_p = Path(root)
        for d in list(dirs):
            dp = root_p / d
            if dp.is_symlink():
                rel = _norm_path(dp.relative_to(dir_path).as_posix())
                try:
                    target = os.readlink(dp)
                    skipped.append((rel, f"symlink -> {target}"))
                except OSError:
                    skipped.append((rel, "symlink"))
                dirs.remove(d)

        for f in files:
            fp = root_p / f
            rel = _norm_path(fp.relative_to(dir_path).as_posix())
            if rel == "manifest.json":
                continue

            if fp.is_symlink():
                try:
                    target = os.readlink(fp)
                    skipped.append((rel, f"symlink -> {target}"))
                except OSError:
                    skipped.append((rel, "symlink"))
            elif fp.is_file():
                hasher = hashlib.sha256()
                try:
                    with open(fp, "rb") as fh:
                        while chunk := fh.read(65536):
                            hasher.update(chunk)
                    computed = hasher.hexdigest()
                    seen_files[rel] = computed

                    if progress_callback:
                        progress_callback(ProgressUpdate(
                            phase="dogrulama",
                            message=t("verify_progress_verifying", item=rel),
                            current_item=rel,
                            processed_count=len(seen_files),
                            total_count=len(expected_files) or len(seen_files)
                        ))
                except (OSError, PermissionError) as e:
                    mismatched.append(rel)

    # Karşılaştırma
    for norm_k, exp_h in expected_files.items():
        if norm_k in seen_files:
            act_h = seen_files[norm_k]
            if act_h.lower() == exp_h.lower():
                matched.append(norm_k)
            else:
                mismatched.append(norm_k)
        else:
            if manifest.backup_type != "incremental":
                missing.append(norm_k)

    for norm_name in seen_files:
        if norm_name not in expected_files:
            if not _is_distronomad_metadata(norm_name):
                untracked.append(norm_name)

    state = _InternalVerificationState(
        archive_path=dir_path,
        is_valid=False,
        total_files_checked=len(matched) + len(mismatched),
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        untracked=untracked,
        skipped=skipped,
        corrupted=False,
        error_message=None
    )
    state.manifest = manifest
    state.expected_files = expected_files
    state.seen_files = seen_files
    return state


def _verify_archive_file(
    archive_path: Path,
    password: Optional[str] = None,
    progress_callback: Optional[Callable[[ProgressUpdate], None]] = None
) -> _InternalVerificationState:
    """Arşiv dosyasını (.tar.zst, .tar.gz, .tar, .enc) diske açmadan tek geçişte akış olarak doğrular."""
    manifest: Optional[BackupManifest] = None
    seen_files: Dict[str, str] = {}
    skipped: List[Tuple[str, str]] = []
    matched: List[str] = []
    mismatched: List[str] = []
    missing: List[str] = []
    untracked: List[str] = []
    corrupted: bool = False
    error_message: Optional[str] = None

    try:
        with open_archive_stream(archive_path, password=password) as decomp_stream:
            try:
                with tarfile.open(fileobj=decomp_stream, mode="r|*") as tar:
                    for member in tar:
                        norm_name = _norm_path(member.name)
                        # Yalnızca kökteki manifest yedeğe aittir; tarayıcı eklentileri gibi uygulamaların
                        # kendi manifest.json dosyaları sıradan yedek dosyasıdır ve hash'leri doğrulanır.
                        # Arşiv sonundaki kesin manifest (arşive yazılan baytların hash'leri) baştakinin yerini alır.
                        if norm_name == "manifest.json":
                            f = tar.extractfile(member)
                            if f:
                                content = f.read()
                                manifest = BackupManifest.from_dict(json.loads(content.decode("utf-8", errors="surrogateescape")))
                        elif member.isdir():
                            continue
                        elif member.issym() or member.islnk():
                            skipped.append((norm_name, f"symlink -> {member.linkname}"))
                        elif member.isreg():
                            f = tar.extractfile(member)
                            if f:
                                hasher = hashlib.sha256()
                                while chunk := f.read(65536):
                                    hasher.update(chunk)
                                computed = hasher.hexdigest()
                                seen_files[norm_name] = computed

                                if progress_callback:
                                    exp_count = len(manifest.file_hashes) if manifest and manifest.file_hashes else len(seen_files)
                                    progress_callback(ProgressUpdate(
                                        phase="dogrulama",
                                        message=t("verify_progress_verifying", item=norm_name),
                                        current_item=norm_name,
                                        processed_count=len(seen_files),
                                        total_count=exp_count
                                    ))
                        else:
                            skipped.append((norm_name, "special"))
            except Exception as e:
                corrupted = True
                error_message = t("verify_err_stream_corrupted", error=f"{type(e).__name__}: {str(e)}")
    except ValueError as ve:
        if not password and ("şifrelenmiştir" in str(ve) or "encrypted" in str(ve)):
            raise
        corrupted = True
        error_message = str(ve)
    except Exception as e:
        corrupted = True
        error_message = t("verify_err_stream_corrupted", error=f"{type(e).__name__}: {str(e)}")

    if not corrupted and manifest is None:
        corrupted = True
        error_message = t("verify_err_manifest_not_found_in_archive")

    expected_files: Dict[str, str] = {}
    if manifest:
        for k, h in manifest.file_hashes.items():
            if h.startswith("symlink:") or h in ("__DELETED__", "deleted"):
                continue
            expected_files[_norm_path(k)] = h

        for norm_k, exp_h in expected_files.items():
            if norm_k in seen_files:
                act_h = seen_files[norm_k]
                if act_h.lower() == exp_h.lower():
                    matched.append(norm_k)
                else:
                    mismatched.append(norm_k)
            else:
                if manifest.backup_type != "incremental":
                    missing.append(norm_k)

        for norm_name in seen_files:
            if norm_name not in expected_files:
                if not _is_distronomad_metadata(norm_name):
                    untracked.append(norm_name)

    state = _InternalVerificationState(
        archive_path=archive_path,
        is_valid=False,
        total_files_checked=len(matched) + len(mismatched),
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        untracked=untracked,
        skipped=skipped,
        corrupted=corrupted,
        error_message=error_message
    )
    state.manifest = manifest
    state.expected_files = expected_files
    state.seen_files = seen_files
    return state
