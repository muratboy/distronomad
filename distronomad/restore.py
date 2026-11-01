"""
DistroNomad - Geri Yükleme Motoru (Restore Engine).
"""

import os
import shutil
import tarfile
import json
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple, Any, Union
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

from distronomad.models import (
    SystemInfo,
    PackageItem,
    BackupManifest,
    RestoreOptions,
    ProgressUpdate,
)
from distronomad.packagemap import (
    detect_distro_family,
    translate_package,
    get_install_command,
    is_valid_package_name,
)
from distronomad.archive import (
    RUNTIME_SOCKET_NAMES,
    tar_extract_filter,
    safe_tar_extract,
    extract_tar_archive,
)
from distronomad.crypto import decrypt_archive, is_encrypted_file
from distronomad.backup import read_manifest_from_backup, safe_copytree
from distronomad.hardware import scan_hardware_profile
from distronomad.i18n import t


_RUNTIME_SOCKET_NAMES = RUNTIME_SOCKET_NAMES
_tar_extract_filter = tar_extract_filter
_safe_tar_extract = safe_tar_extract


_SUDO_AUTH_FAILURE_MARKERS = (
    "a password is required",
    "a terminal is required",
    "no tty present",
    "incorrect password attempt",
    "authentication failure",
    "is not in the sudoers file",
    "may not run sudo",
)


def _is_sudo_auth_failure(stderr_text: Optional[str]) -> bool:
    """Komut çıktısının sudo yetki/parola hatasına işaret edip etmediğini döndürür."""
    low = (stderr_text or "").lower()
    return any(marker in low for marker in _SUDO_AUTH_FAILURE_MARKERS)


class RestoreEngine:
    """Yedekten yeni sisteme geri yükleme işlemlerini yürüten sınıf."""

    def __init__(
        self,
        backup_path: Path,
        password: Optional[str] = None,
        progress_callback: Optional[Callable[[ProgressUpdate], None]] = None,
        transaction_dir: Optional[Path] = None
    ):
        self.backup_path = Path(backup_path)
        self.password = password
        self.progress_callback = progress_callback
        self.transaction_dir = Path(transaction_dir) if transaction_dir else None
        self.temp_extracted_dir: Optional[Path] = None
        self.merged_extracted_dir: Optional[Path] = None
        self.decrypted_temp_file: Optional[Path] = None
        self.base_backup_path: Optional[Path] = None
        self.manifest: Optional[BackupManifest] = None
        self.source_dir: Optional[Path] = None

    def _notify(
        self,
        phase: str,
        message: str,
        current_item: str = "",
        processed_count: int = 0,
        total_count: int = 0,
        percent: float = 0.0
    ):
        if self.progress_callback:
            self.progress_callback(ProgressUpdate(
                phase=phase,
                message=message,
                current_item=current_item,
                processed_count=processed_count,
                total_count=total_count,
                percent=percent
            ))

    def _query_repo_packages(self, target_pm: str, package_names: List[str]) -> Optional[set]:
        """
        Hedef depoda aday paketlerin mevcut olup olmadığını sudo'suz sorgu komutuyla kontrol eder.
        Toplu sorgulama yapar. Sorgu komutu yoksa veya başarısız olursa None döner.
        """
        if not package_names:
            return set()

        mgr = target_pm.lower()
        cmd_prefix = None
        if mgr in ["apt", "apt-get"]:
            cmd_prefix = ["apt-cache", "show"]
        elif mgr in ["pacman", "yay", "paru"]:
            cmd_prefix = ["pacman", "-Si"]
        elif mgr in ["dnf"]:
            cmd_prefix = ["dnf", "info"]
        elif mgr in ["zypper"]:
            cmd_prefix = ["zypper", "info"]

        if not cmd_prefix:
            return None

        try:
            env = dict(os.environ, LC_ALL="C")
            cmd = cmd_prefix + list(package_names)
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                env=env
            )
            found = set()
            for match in re.finditer(r"^(?:Name|Package|İsim)\s*:\s*(\S+)", proc.stdout, re.MULTILINE | re.IGNORECASE):
                pkg_name = match.group(1).strip()
                found.add(pkg_name)
                if ":" in pkg_name:
                    found.add(pkg_name.split(":", 1)[0])

            # pacman -Si çıktısındaki Provides/Sağlananlar bilgisini de ekle
            for match in re.finditer(r"^(?:Provides|Sağlananlar)\s*:\s*(.+)$", proc.stdout, re.MULTILINE | re.IGNORECASE):
                for prov in match.group(1).split():
                    prov_clean = re.split(r"[=<>]", prov)[0].strip()
                    if prov_clean:
                        found.add(prov_clean)

            # Arch / pacman için: -Si sanal veya provides adlarını bulamadıysa, pacman -Sp ile provides çözümlemesi yap
            if mgr in ["pacman", "yay", "paru"]:
                unresolved = [p for p in package_names if p not in found and p.lower() not in {f.lower() for f in found}]
                for cand in unresolved:
                    try:
                        p_sp = subprocess.run(
                            ["pacman", "-Sp", "--", cand],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=30,
                            env=env
                        )
                        if p_sp.returncode == 0:
                            found.add(cand)
                    except Exception:
                        pass

            # Komut başarısız olduysa ve hiçbir paket bulunamadıysa (sorgu hatası) None dön
            if proc.returncode != 0 and not found:
                stderr_lower = proc.stderr.lower() if proc.stderr else ""
                if "target not found" in stderr_lower or "hedef bulunamadı" in stderr_lower:
                    return found
                return None

            return found
        except Exception as e:
            logger.warning(t("restore_warn_repo_query_error", pm=target_pm, error=str(e)))
            return None

    def ensure_extracted(self) -> Path:
        """Arşiv henüz geçici dizine açılmadıysa açar ve source_dir yolunu döndürür."""
        if self.source_dir and self.source_dir.exists():
            return self.source_dir

        if not self.backup_path.exists():
            raise FileNotFoundError(t("restore_err_backup_not_found", path=str(self.backup_path)))

        actual_path = self.backup_path
        if is_encrypted_file(self.backup_path):
            if not self.password:
                raise ValueError(t("restore_err_password_required"))
            if not self.decrypted_temp_file or not self.decrypted_temp_file.exists():
                temp_dec_dir = Path(tempfile.mkdtemp(prefix="distronomad_restore_dec_"))
                stem = self.backup_path.name
                if stem.endswith(".enc"):
                    stem = stem[:-4]
                self.decrypted_temp_file = temp_dec_dir / stem
                self._notify("şifre_çözme", t("restore_notify_decrypting"))
                decrypt_archive(self.backup_path, self.decrypted_temp_file, self.password)
            actual_path = self.decrypted_temp_file

        if actual_path.is_dir():
            self.source_dir = actual_path
        else:
            self.temp_extracted_dir = Path(tempfile.mkdtemp(prefix="distronomad_restore_"))
            self.source_dir = self.temp_extracted_dir
            try:
                self._extract_archive(actual_path, self.temp_extracted_dir)
            except Exception:
                shutil.rmtree(self.temp_extracted_dir, ignore_errors=True)
                self.temp_extracted_dir = None
                self.source_dir = None
                raise

        return self.source_dir

    def load_manifest(self, password: Optional[str] = None, extract_files: bool = True) -> BackupManifest:
        """Yedek arşivini veya dizinini açarak manifest.json dosyasını yükler."""
        if password is not None:
            self.password = password

        if not self.backup_path.exists():
            raise FileNotFoundError(t("restore_err_backup_not_found", path=str(self.backup_path)))

        # Hızlı inceleme modu: Tüm arşivi diske açmadan doğrudan akıştan manifest.json oku
        if not extract_files and not self.backup_path.is_dir():
            manifest = read_manifest_from_backup(self.backup_path, password=self.password)
            if manifest is not None:
                self.manifest = manifest
                return self.manifest

        self.ensure_extracted()

        manifest_file = self.source_dir / "manifest.json"
        if not manifest_file.exists():
            raise ValueError(t("restore_err_manifest_not_found", path=str(self.source_dir)))

        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.manifest = BackupManifest.from_dict(data)
        return self.manifest

    def _extract_archive(self, archive_file: Path, target_dir: Path):
        """tar.zst veya tar.gz/tar arşivini ara dosya oluşturmadan akış (stream) ile açar."""
        self._notify("açma", t("restore_notify_extracting", name=archive_file.name))
        extract_tar_archive(archive_file, target_dir)

    @staticmethod
    def _is_path_writable(dst: Path) -> bool:
        """Hedef yolun yazılabilir olup olmadığını kontrol eder (sudo kullanılmaz)."""
        try:
            if dst.exists() or dst.is_symlink():
                return os.access(dst, os.W_OK)
            parent = dst.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            return os.access(parent, os.W_OK)
        except Exception:
            return False

    def execute_restore(self, options: RestoreOptions, target_system_info: Optional[SystemInfo] = None) -> Dict[str, Any]:
        """
        Seçilen konfigürasyonları, özel dosyaları ve uygulamaları geri yükler.
        """
        if not self.manifest:
            self.load_manifest(extract_files=True)
        elif not self.source_dir or not self.source_dir.exists():
            self.ensure_extracted()

        current_sys = target_system_info or SystemInfo.current()
        target_home = Path(options.target_home or current_sys.home_dir)
        if not options.dry_run:
            target_home.mkdir(parents=True, exist_ok=True)

        # Eğer bu yedek bir artımlı (incremental) yedekse zinciri tam yedeğe kadar geriye izle ve birleştir
        if self.manifest.backup_type == "incremental":
            # 1. Zinciri en yeniden en eskiye doğru geriye doğru topla
            chain_backwards: List[Tuple[Path, BackupManifest]] = []
            seen_resolved_paths = {self.backup_path.resolve()}
            curr_path = self.backup_path
            curr_manifest = self.manifest
            first_hop = True

            while curr_manifest and curr_manifest.backup_type == "incremental":
                cand = None
                if first_hop and options.base_backup_path:
                    cand = Path(options.base_backup_path)
                elif curr_manifest.base_backup_name:
                    cand1 = curr_path.parent / curr_manifest.base_backup_name
                    if cand1.exists():
                        cand = cand1
                    else:
                        cand2 = self.backup_path.parent / curr_manifest.base_backup_name
                        if cand2.exists():
                            cand = cand2
                        else:
                            cand = cand1

                if not cand or not cand.exists():
                    base_desc = str(cand) if cand else (options.base_backup_path if first_hop else curr_manifest.base_backup_name) or "belirtilmemiş"
                    raise FileNotFoundError(t("restore_err_base_not_found", base=base_desc))

                cand_res = cand.resolve()
                if cand_res in seen_resolved_paths:
                    raise ValueError(t("restore_err_chain_loop", name=cand.name))
                seen_resolved_paths.add(cand_res)

                cand_manifest = read_manifest_from_backup(cand, password=self.password)
                if cand_manifest is None:
                    if is_encrypted_file(cand) and not self.password:
                        raise ValueError(t("restore_err_base_encrypted", name=cand.name))
                    raise ValueError(t("restore_err_base_manifest_corrupted", name=cand.name))

                chain_backwards.append((cand, cand_manifest))
                curr_path = cand
                curr_manifest = cand_manifest
                first_hop = False

            # 2. Zinciri en eskiden en yeniye doğru (reversed) birleştir
            self.merged_extracted_dir = Path(tempfile.mkdtemp(prefix="distronomad_merged_"))
            temp_chain_cleanups: List[Path] = []

            def _apply_deletions(m_manifest: Optional[BackupManifest]):
                if not m_manifest or not self.merged_extracted_dir:
                    return
                merged_base = Path(os.path.abspath(self.merged_extracted_dir))

                def _safe_del(rel_k: str):
                    clean_rel = str(rel_k).lstrip("/")
                    try:
                        # B-7: resolve() sembolik bağları takip ettiği için os.path.abspath kullanılır
                        del_p = Path(os.path.abspath(self.merged_extracted_dir / clean_rel))
                        if os.path.commonpath([str(merged_base), str(del_p)]) != str(merged_base) or str(del_p) == str(merged_base):
                            return
                    except Exception:
                        return
                    if del_p.is_symlink():
                        del_p.unlink(missing_ok=True)
                    elif del_p.is_dir():
                        shutil.rmtree(del_p, ignore_errors=True)
                    elif del_p.exists():
                        del_p.unlink(missing_ok=True)

                for del_rel in getattr(m_manifest, "deleted_files", []) or []:
                    _safe_del(del_rel)
                if m_manifest.file_metadata:
                    for rel_k, meta_entry in m_manifest.file_metadata.items():
                        if isinstance(meta_entry, dict) and (meta_entry.get("deleted") is True or meta_entry.get("status") == "deleted"):
                            _safe_del(rel_k)
                if m_manifest.file_hashes:
                    for rel_k, hash_val in m_manifest.file_hashes.items():
                        if hash_val in ("__DELETED__", "deleted"):
                            _safe_del(rel_k)

            try:
                for member_path, member_manifest in reversed(chain_backwards):
                    self._notify("birleştirme", t("restore_notify_integrating_base", name=member_path.name))
                    _apply_deletions(member_manifest)
                    if member_path.is_dir():
                        safe_copytree(member_path, self.merged_extracted_dir, symlinks=True, dirs_exist_ok=True)
                    else:
                        archive_to_extract = member_path
                        if is_encrypted_file(member_path):
                            if not self.password:
                                raise ValueError(t("restore_err_base_encrypted", name=member_path.name))
                            temp_dec_dir = Path(tempfile.mkdtemp(prefix="distronomad_chain_dec_"))
                            temp_chain_cleanups.append(temp_dec_dir)
                            stem = member_path.name[:-4] if member_path.name.endswith(".enc") else member_path.name
                            dec_file = temp_dec_dir / stem
                            self._notify("şifre_çözme", t("restore_notify_decrypting_base", name=member_path.name))
                            decrypt_archive(member_path, dec_file, self.password)
                            archive_to_extract = dec_file

                        self._extract_archive(archive_to_extract, self.merged_extracted_dir)

                # Son olarak bu artımlı yedeğin (en güncel) dosyalarını üzerine yaz
                self._notify("birleştirme", t("restore_notify_integrating_inc", name=self.backup_path.name))
                _apply_deletions(self.manifest)
                safe_copytree(self.source_dir, self.merged_extracted_dir, symlinks=True, dirs_exist_ok=True)
                self.source_dir = self.merged_extracted_dir

            except Exception:
                if self.merged_extracted_dir and self.merged_extracted_dir.exists():
                    shutil.rmtree(self.merged_extracted_dir, ignore_errors=True)
                    self.merged_extracted_dir = None
                raise
            finally:
                for tmp in temp_chain_cleanups:
                    if tmp.exists():
                        shutil.rmtree(tmp, ignore_errors=True)

        results = {
            "restored_configs": [],
            "restored_files": [],
            "installed_packages": [],
            "failed_packages": [],
            "untranslated_packages": [],
            "skipped_external_paths": [],
            "restored_system_state": [],
            "warnings": [],
            "is_dry_run": options.dry_run,
            "transaction_file": "",
            "operations_count": 0
        }

        total_steps = len(options.selected_configs) + len(options.selected_custom_paths)
        has_pkg_install = (options.install_packages and bool(options.selected_packages)) or options.install_drivers
        if has_pkg_install:
            total_steps += 1

        current_step = 0
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        transaction_ops: List[Dict[str, Any]] = []

        def _save_transaction_log() -> None:
            if not options.dry_run and transaction_ops:
                try:
                    if self.transaction_dir:
                        tx_dir = self.transaction_dir
                    elif options.target_home:
                        tx_dir = Path(options.target_home) / ".config" / "distronomad" / "transactions"
                    else:
                        home_dir = Path(os.environ.get("HOME") or Path.home())
                        tx_dir = home_dir / ".config" / "distronomad" / "transactions"

                    tx_dir.mkdir(parents=True, exist_ok=True)
                    tx_file = tx_dir / f"restore_{timestamp}.json"
                    last_tx_file = tx_dir / "last_restore.json"
                    tx_record = {
                        "timestamp": timestamp,
                        "created_at": datetime.now().isoformat(),
                        "backup_path": str(self.backup_path),
                        "target_home": str(target_home),
                        "operations": transaction_ops
                    }
                    with open(tx_file, "w", encoding="utf-8") as f:
                        json.dump(tx_record, f, indent=2, ensure_ascii=False)
                    with open(last_tx_file, "w", encoding="utf-8") as f:
                        json.dump(tx_record, f, indent=2, ensure_ascii=False)

                    results["transaction_file"] = str(tx_file)
                    results["operations_count"] = len(transaction_ops)
                except Exception:
                    pass

        try:
            # 1. Aşama: Konfigürasyonları Geri Yükle
            configs_src = self.source_dir / "configs"
            if configs_src.exists():
                for cfg in options.selected_configs:
                    current_step += 1
                    src_item = configs_src / cfg.rel_path
                    dst_item = target_home / cfg.rel_path

                    # Güvenlik doğrulaması: target_home dışına çıkışı engelle (Path traversal ve symlink kaçışı)
                    try:
                        resolved_home = str(target_home.resolve())
                        resolved_dst = str(dst_item.resolve())
                        is_safe = (
                            os.path.commonpath([resolved_home, resolved_dst]) == resolved_home
                            and resolved_dst != resolved_home
                        )
                    except Exception:
                        is_safe = False

                    if not is_safe:
                        warn_msg = t("restore_warn_traversal_blocked", src=str(cfg.rel_path), dst=str(dst_item))
                        logger.warning(warn_msg)
                        results["warnings"].append(warn_msg)
                        continue

                    self._notify(
                        "konfigürasyon",
                        t("restore_notify_restoring_config", name=cfg.name),
                        current_item=cfg.name,
                        processed_count=current_step,
                        total_count=total_steps
                    )
    
                    if not src_item.exists() and not src_item.is_symlink():
                        results["warnings"].append(t("restore_warn_not_found_in_backup", path=str(cfg.rel_path)))
                        continue
    
                    if options.dry_run:
                        results["restored_configs"].append(f"[DRY-RUN] {cfg.rel_path} -> {dst_item}")
                        continue
    
                    # Çakışma stratejisi ve İşlem Günlüğü (Transaction Log)
                    was_existing = dst_item.exists() or dst_item.is_symlink()
                    if was_existing:
                        if options.conflict_strategy == "skip":
                            results["warnings"].append(t("restore_warn_conflict_skipped", path=str(dst_item)))
                            continue
                        elif options.conflict_strategy == "backup":
                            bak_name = dst_item.parent / f"{dst_item.name}.bak.{timestamp}"
                            shutil.move(dst_item, bak_name)
                            transaction_ops.append({
                                "type": "replaced",
                                "path": str(dst_item),
                                "backup_path": str(bak_name),
                                "is_dir": bak_name.is_dir()
                            })
                        else:  # overwrite
                            transaction_ops.append({
                                "type": "overwritten",
                                "path": str(dst_item),
                                "backup_path": None,
                                "is_dir": dst_item.is_dir()
                            })
                    else:
                        transaction_ops.append({
                            "type": "created",
                            "path": str(dst_item),
                            "backup_path": None,
                            "is_dir": src_item.is_dir()
                        })
    
                    # Kopyala
                    dst_item.parent.mkdir(parents=True, exist_ok=True)
                    if src_item.is_dir() and not src_item.is_symlink():
                        # B-6: Hedefte dosya veya sembolik bağ varsa dizin kopyalamadan önce kaldır
                        if dst_item.is_symlink() or (dst_item.exists() and not dst_item.is_dir()):
                            dst_item.unlink(missing_ok=True)
                        safe_copytree(src_item, dst_item, symlinks=True, dirs_exist_ok=True)
                    else:
                        if dst_item.is_symlink() or dst_item.exists():
                            if dst_item.is_dir() and not dst_item.is_symlink():
                                shutil.rmtree(dst_item, ignore_errors=True)
                            else:
                                dst_item.unlink(missing_ok=True)
                        if src_item.is_symlink():
                            link_target = os.readlink(src_item)
                            os.symlink(link_target, dst_item)
                        else:
                            shutil.copy2(src_item, dst_item, follow_symlinks=False)
    
                    results["restored_configs"].append(str(dst_item))
    
            # 2. Aşama: Özel Dosyaları ve Klasörleri Geri Yükle
            custom_src = self.source_dir / "custom"
            if custom_src.exists():
                source_home = Path(self.manifest.system_info.home_dir) if self.manifest and self.manifest.system_info and self.manifest.system_info.home_dir else None

                for cp in options.selected_custom_paths:
                    current_step += 1
                    cp_path = Path(cp.path)
                    item_name = cp_path.name

                    # Ev dizinine göreli mi yoksa köke (root) göreli mi tespit et
                    is_home_rel = False
                    rel_path = None
                    if source_home:
                        try:
                            rel_path = cp_path.resolve().relative_to(source_home.resolve())
                            is_home_rel = True
                        except ValueError:
                            pass
                        if not is_home_rel:
                            try:
                                rel_path = cp_path.relative_to(source_home)
                                is_home_rel = True
                            except ValueError:
                                pass

                    if not is_home_rel:
                        try:
                            rel_path = cp_path.relative_to(Path.home())
                            is_home_rel = True
                        except ValueError:
                            pass

                    root_rel = Path(str(cp_path).lstrip("/"))
                    if is_home_rel and not (custom_src / "home" / rel_path).exists() and (custom_src / "root" / root_rel).exists():
                        is_home_rel = False
                        rel_path = root_rel
                    elif not is_home_rel:
                        if (custom_src / "home" / root_rel).exists():
                            is_home_rel = True
                            rel_path = root_rel
                        else:
                            rel_path = root_rel

                    if is_home_rel:
                        src_item = custom_src / "home" / rel_path
                        dst_item = target_home / rel_path

                        # Güvenlik doğrulaması: target_home dışına çıkışı engelle (Path traversal ve symlink kaçışı)
                        try:
                            resolved_home = str(target_home.resolve())
                            resolved_dst = str(dst_item.resolve())
                            is_safe = (
                                os.path.commonpath([resolved_home, resolved_dst]) == resolved_home
                                and resolved_dst != resolved_home
                            )
                        except Exception:
                            is_safe = False

                        if not is_safe:
                            warn_msg = t("restore_warn_traversal_blocked", src=str(cp.path), dst=str(dst_item))
                            logger.warning(warn_msg)
                            results["warnings"].append(warn_msg)
                            continue
                    else:
                        # B-2 ve Karar 3: Ev dizini dışındaki özel yollar (custom/root)
                        # Hedef ev dizini gerçek ev dizini değilse (--target-home vb.) asla köke/mutlak yola yazılmaz.
                        is_custom_target_home = False
                        try:
                            is_custom_target_home = (target_home.resolve() != Path.home().resolve())
                        except Exception:
                            is_custom_target_home = True

                        if is_custom_target_home:
                            if options.target_root and options.target_root != "/":
                                root_base = Path(options.target_root)
                                dst_item = root_base / rel_path
                                try:
                                    resolved_root = str(root_base.resolve())
                                    resolved_root_dst = str(dst_item.resolve())
                                    inside_root = (
                                        os.path.commonpath([resolved_root, resolved_root_dst]) == resolved_root
                                        and resolved_root_dst != resolved_root
                                    )
                                except Exception:
                                    inside_root = False
                                if not cp_path.is_absolute() or not inside_root:
                                    warn_msg = t("restore_warn_external_path_invalid", path=str(cp.path))
                                    logger.warning(warn_msg)
                                    results["warnings"].append(warn_msg)
                                    results["skipped_external_paths"].append(str(cp.path))
                                    continue
                            else:
                                warn_msg = t("restore_warn_root_write_blocked_custom_home", path=str(cp.path))
                                logger.warning(warn_msg)
                                results["warnings"].append(warn_msg)
                                results["skipped_external_paths"].append(str(cp.path))
                                continue
                        else:
                            # Karar 3: Ev dizini dışındaki özel yollar varsayılan ATLANIR ve listelenir; yalnızca açık onayla yazılır
                            can_restore_root = bool(getattr(options, "allow_external_paths", False) or getattr(options, "allow_root_paths", False))
                            if not can_restore_root:
                                warn_msg = t("restore_warn_external_path_skipped_no_confirm", path=str(cp.path))
                                logger.info(warn_msg)
                                results["warnings"].append(warn_msg)
                                results["skipped_external_paths"].append(str(cp.path))
                                continue

                            # Arşivden gelen yol güvenilmezdir: göreli yol çalışma dizinine, '..' içeren yol beklenmedik yere yazar
                            if not cp_path.is_absolute() or ".." in cp_path.parts:
                                warn_msg = t("restore_warn_external_path_invalid", path=str(cp.path))
                                logger.warning(warn_msg)
                                results["warnings"].append(warn_msg)
                                results["skipped_external_paths"].append(str(cp.path))
                                continue

                            dst_item = cp_path

                        src_item = custom_src / "root" / rel_path

                    self._notify(
                        "dosyalar",
                        t("restore_notify_restoring_file", name=item_name),
                        current_item=item_name,
                        processed_count=current_step,
                        total_count=total_steps
                    )

                    if not src_item.exists() and not src_item.is_symlink():
                        results["warnings"].append(t("restore_warn_not_found_in_backup", path=str(rel_path)))
                        continue

                    if not self._is_path_writable(dst_item):
                        warn_msg = t("restore_warn_target_not_writable", path=str(dst_item))
                        logger.warning(warn_msg)
                        results["warnings"].append(warn_msg)
                        continue

                    if options.dry_run:
                        results["restored_files"].append(f"[DRY-RUN] {item_name} -> {dst_item}")
                        continue

                    was_existing = dst_item.exists() or dst_item.is_symlink()
                    if was_existing:
                        if options.conflict_strategy == "skip":
                            results["warnings"].append(t("restore_warn_conflict_skipped", path=str(dst_item)))
                            continue
                        elif options.conflict_strategy == "backup":
                            bak_name = dst_item.parent / f"{dst_item.name}.bak.{timestamp}"
                            shutil.move(dst_item, bak_name)
                            transaction_ops.append({
                                "type": "replaced",
                                "path": str(dst_item),
                                "backup_path": str(bak_name),
                                "is_dir": bak_name.is_dir()
                            })
                        else:  # overwrite
                            transaction_ops.append({
                                "type": "overwritten",
                                "path": str(dst_item),
                                "backup_path": None,
                                "is_dir": dst_item.is_dir()
                            })
                    else:
                        transaction_ops.append({
                            "type": "created",
                            "path": str(dst_item),
                            "backup_path": None,
                            "is_dir": src_item.is_dir()
                        })

                    dst_item.parent.mkdir(parents=True, exist_ok=True)
                    if src_item.is_dir() and not src_item.is_symlink():
                        # B-6: Hedefte dosya veya sembolik bağ varsa dizin kopyalamadan önce kaldır
                        if dst_item.is_symlink() or (dst_item.exists() and not dst_item.is_dir()):
                            dst_item.unlink(missing_ok=True)
                        safe_copytree(src_item, dst_item, symlinks=True, dirs_exist_ok=True)
                    else:
                        if dst_item.is_symlink() or dst_item.exists():
                            if dst_item.is_dir() and not dst_item.is_symlink():
                                shutil.rmtree(dst_item, ignore_errors=True)
                            else:
                                dst_item.unlink(missing_ok=True)
                        if src_item.is_symlink():
                            link_target = os.readlink(src_item)
                            os.symlink(link_target, dst_item)
                        else:
                            shutil.copy2(src_item, dst_item, follow_symlinks=False)

                    results["restored_files"].append(str(dst_item))
                # 3. Aşama: Uygulamaları ve Paketleri Kur
            if has_pkg_install:
                current_step += 1
                self._notify(
                    "paketler",
                    t("restore_notify_preparing_packages"),
                    processed_count=current_step,
                    total_count=total_steps
                )
    
                src_family = detect_distro_family(self.manifest.system_info.distro_id, self.manifest.system_info.distro_like)
                target_family = detect_distro_family(current_sys.distro_id, current_sys.distro_like)

                # Hedef sistemde mevcut ana sistem paket yöneticisini bul
                sys_pms = ["pacman", "yay", "paru", "apt", "apt-get", "dnf", "zypper"]
                target_sys_pm = None
                for pm_cand in (current_sys.available_package_managers or []):
                    if pm_cand in sys_pms:
                        target_sys_pm = pm_cand
                        break

                # Paketleri yöneticilere göre grupla
                pkgs_by_pm: Dict[str, List[str]] = {}
                cross_candidates_by_pm: Dict[str, List[Tuple[PackageItem, str]]] = {}
                target_fam_eff = target_family

                if options.install_packages and options.selected_packages:
                    for pkg in options.selected_packages:
                        # B-5: Katı izin listesi regex'iyle paket adı doğrulaması
                        target_name = pkg.app_id if pkg.manager == "flatpak" and pkg.app_id else pkg.name
                        if not is_valid_package_name(target_name, manager=pkg.manager):
                            warn_msg = t("restore_warn_invalid_package_name", name=target_name)
                            logger.warning(warn_msg)
                            results["warnings"].append(warn_msg)
                            results["failed_packages"].append(f"{pkg.manager}:{target_name} ({t('restore_invalid_pkg_tag')})")
                            continue

                        pm = pkg.manager
                        if pm in ["pacman", "yay", "paru", "apt", "apt-get", "dnf", "zypper"]:
                            target_pm = target_sys_pm or pm

                            # Dağıtım ailesini paket yöneticisine göre de tahmin et (distro_id bilinmiyorsa)
                            src_fam_eff = src_family
                            if src_fam_eff == "unknown":
                                if pm in ["pacman", "yay", "paru"]:
                                    src_fam_eff = "arch"
                                elif pm in ["apt", "apt-get"]:
                                    src_fam_eff = "debian"
                                elif pm in ["dnf"]:
                                    src_fam_eff = "fedora"
                                elif pm in ["zypper"]:
                                    src_fam_eff = "suse"

                            target_fam_eff = target_family
                            if target_fam_eff == "unknown":
                                if target_pm in ["pacman", "yay", "paru"]:
                                    target_fam_eff = "arch"
                                elif target_pm in ["apt", "apt-get"]:
                                    target_fam_eff = "debian"
                                elif target_pm in ["dnf"]:
                                    target_fam_eff = "fedora"
                                elif target_pm in ["zypper"]:
                                    target_fam_eff = "suse"

                            # Aynı dağıtım ailesi veya aynı paket yöneticisi ise paket adı olduğu gibi geçer
                            if src_fam_eff == target_fam_eff or pm == target_pm:
                                pkgs_by_pm.setdefault(target_pm, []).append(pkg.name)
                            else:
                                # Farklı dağıtım ailesi: eşleme varsa onu, yoksa kaynak adı aday al
                                translated_name = translate_package(pkg.name, src_fam_eff, target_fam_eff, fallback_to_source=False)
                                cand_name = translated_name if translated_name else pkg.name
                                cross_candidates_by_pm.setdefault(target_pm, []).append((pkg, cand_name))
                        elif pm == "flatpak":
                            flatpak_id = pkg.app_id or pkg.name
                            pkgs_by_pm.setdefault("flatpak", []).append(flatpak_id)
                        else:
                            pkgs_by_pm.setdefault(pm, []).append(pkg.name)

                    # Farklı dağıtım ailesinden gelen aday paketleri hedef depoda toplu sorgula
                    for target_pm, cand_list in cross_candidates_by_pm.items():
                        cand_names = [cand for _, cand in cand_list]
                        avail_set = self._query_repo_packages(target_pm, cand_names)
                        if avail_set is None:
                            # Sorgu komutu yoksa/başarısız olursa adayları elemeden kuruluma gönder
                            for _, cand_name in cand_list:
                                pkgs_by_pm.setdefault(target_pm, []).append(cand_name)
                        else:
                            avail_lower = {name.lower() for name in avail_set}
                            for pkg, cand_name in cand_list:
                                if cand_name in avail_set or cand_name.lower() in avail_lower:
                                    pkgs_by_pm.setdefault(target_pm, []).append(cand_name)
                                else:
                                    # Depoda olmayanlar kurulumdan çıkarılıp raporlansın
                                    results["untranslated_packages"].append(pkg.name)

                # Canlı donanım taramasına göre sürücü paketlerini ekle
                if options.install_drivers:
                    live_hw = scan_hardware_profile()
                    fam_key = target_family
                    if fam_key == "unknown":
                        fam_key = target_fam_eff if target_fam_eff != "unknown" else "arch"
                    driver_pkgs = live_hw.recommended_packages.get(fam_key, [])
                    if not driver_pkgs and fam_key in ["arch", "debian", "fedora", "suse"]:
                        driver_pkgs = live_hw.recommended_packages.get(fam_key, [])
                    if driver_pkgs:
                        eff_pm = target_sys_pm or (
                            "pacman" if fam_key == "arch" else
                            "apt-get" if fam_key == "debian" else
                            "dnf" if fam_key == "fedora" else
                            "zypper" if fam_key == "suse" else "pacman"
                        )
                        for dpkg in driver_pkgs:
                            if dpkg not in pkgs_by_pm.setdefault(eff_pm, []):
                                pkgs_by_pm[eff_pm].append(dpkg)

                for pm, pkg_list in pkgs_by_pm.items():
                    if not pkg_list:
                        continue
                    cmd, desc = get_install_command(pm, pkg_list)
                    if not cmd:
                        continue

                    # B-9: sudo gerektiren kurulumlardan önce 'sudo -n true' ile ön kontrol yap
                    if pm in ["pacman", "apt", "apt-get", "dnf", "zypper", "snap", "npm"]:
                        sudo_ok = False
                        try:
                            s_chk = subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                            sudo_ok = (s_chk.returncode == 0)
                        except Exception:
                            sudo_ok = False

                        import sys
                        is_interactive = bool(sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty())

                        if not sudo_ok and not is_interactive and not options.dry_run:
                            warn_msg = t("restore_warn_sudo_no_terminal", pm=pm)
                            logger.warning(warn_msg)
                            results["warnings"].append(warn_msg)
                            results["failed_packages"].append(f"{pm} ({t('restore_sudo_failed_tag')})")
                            continue

                    if pm in ["apt", "apt-get"]:
                        if options.dry_run:
                            results["installed_packages"].append(t("restore_dry_run_apt_update"))
                        else:
                            self._notify("kurulum", t("restore_notify_apt_update"))
                            try:
                                proc_up = subprocess.run(
                                    ["sudo", "apt-get", "update"],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    timeout=120
                                )
                                if proc_up.returncode != 0:
                                    results["failed_packages"].append(f"{pm} update: {proc_up.stderr[:200]}")
                            except Exception as e:
                                results["failed_packages"].append(t("restore_err_update_error", pm=pm, error=str(e)))

                    cmd_str = " ".join(cmd)
                    if options.dry_run:
                        results["installed_packages"].append(f"[DRY-RUN] {desc}: {cmd_str}")
                    else:
                        self._notify("kurulum", t("restore_notify_installing_pkgs", desc=desc, count=len(pkg_list)))
                        timeout_sec = max(300, len(pkg_list) * 60)
                        try:
                            # 1. Adım: Toplu paket kurulumu dene
                            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec)
                            if proc.returncode == 0:
                                results["installed_packages"].extend(pkg_list)
                            else:
                                # Toplu kurulum başarısız olduysa, hatalı paketleri ayıklamak için paket paket yeniden dene
                                self._notify("kurulum", t("restore_notify_batch_failed_trying_single", desc=desc))
                                consecutive_auth_or_timeout_failures = 0
                                for single_pkg in pkg_list:
                                    s_cmd, _ = get_install_command(pm, [single_pkg])
                                    try:
                                        s_proc = subprocess.run(s_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
                                        if s_proc.returncode == 0:
                                            results["installed_packages"].append(single_pkg)
                                            consecutive_auth_or_timeout_failures = 0
                                        else:
                                            err_detail = s_proc.stderr[:200].strip() or t("restore_err_exit_code", code=s_proc.returncode)
                                            results["failed_packages"].append(f"{pm}:{single_pkg} ({err_detail})")
                                            # Yalnızca gerçek sudo yetki hataları sayılır; pacman/dnf "paket bulunamadı"
                                            # durumunda da 1 döndürdüğü için çıkış kodu tek başına ölçüt değildir.
                                            if _is_sudo_auth_failure(s_proc.stderr):
                                                consecutive_auth_or_timeout_failures += 1
                                            else:
                                                consecutive_auth_or_timeout_failures = 0
                                    except subprocess.TimeoutExpired:
                                        results["failed_packages"].append(t("restore_err_single_pkg_timeout", pm=pm, pkg=single_pkg, sec=120))
                                        consecutive_auth_or_timeout_failures += 1
                                    except Exception as s_ex:
                                        results["failed_packages"].append(f"{pm}:{single_pkg} ({str(s_ex)})")

                                    # B-9: Art arda yetki / zaman aşımı hatalarında erken çıkış (circuit breaker)
                                    if consecutive_auth_or_timeout_failures >= 3:
                                        cb_msg = t("restore_warn_consecutive_failures_exit", pm=pm)
                                        logger.warning(cb_msg)
                                        results["warnings"].append(cb_msg)
                                        break
                        except subprocess.TimeoutExpired:
                            results["failed_packages"].append(t("restore_err_pkg_timeout", pm=pm, sec=timeout_sec))
                        except Exception as e:
                            results["failed_packages"].append(t("restore_err_pkg_error", pm=pm, error=str(e)))

            # 4. Aşama: Sistem Durumu (dconf, crontab ve /etc referansları) Geri Yükleme
            state_src = self.source_dir / "system_state"
            if options.restore_system_state and state_src.exists():
                is_real_home = False
                try:
                    current_home = Path.home().resolve()
                    is_real_home = (target_home.resolve() == current_home)
                except Exception:
                    pass

                ref_dest = target_home / ".local" / "share" / "distronomad" / "system_state_reference"
                if options.dry_run:
                    results["restored_system_state"].append(f"[DRY-RUN] system_state -> {ref_dest}")
                else:
                    try:
                        ref_dest.parent.mkdir(parents=True, exist_ok=True)
                        safe_copytree(state_src, ref_dest, dirs_exist_ok=True)
                        results["restored_system_state"].append(str(ref_dest))
                    except Exception:
                        pass

                    # Gerçek kullanıcı ev dizinine yükleniyorsa dconf ve crontab'ı da uygula
                    if is_real_home:
                        if self.transaction_dir:
                            tx_dir = self.transaction_dir
                        else:
                            tx_dir = target_home / ".config" / "distronomad" / "transactions"
                        tx_dir.mkdir(parents=True, exist_ok=True)

                        dconf_file = state_src / "dconf-settings.ini"
                        if dconf_file.exists() and shutil.which("dconf"):
                            bak_dconf_path = None
                            dconf_dump_ok = False
                            try:
                                proc_dump = subprocess.run(["dconf", "dump", "/"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
                                if proc_dump.returncode == 0:
                                    dconf_dump_ok = True
                                    dconf_bak_file = tx_dir / f"dconf_backup_{timestamp}.ini"
                                    dconf_bak_file.write_text(proc_dump.stdout, encoding="utf-8")
                                    bak_dconf_path = str(dconf_bak_file)
                                else:
                                    logger.warning(t("restore_warn_dconf_backup_code", code=proc_dump.returncode))
                            except Exception as ex:
                                logger.warning(t("restore_warn_dconf_backup_error", error=str(ex)))

                            # B-1: Geri yükleme öncesi anlık görüntü alınamazsa sistem durumu geri yüklemesini hiç yapma ve uyar
                            if not dconf_dump_ok:
                                warn_msg = t("restore_warn_dconf_snapshot_failed")
                                logger.warning(warn_msg)
                                results["warnings"].append(warn_msg)
                            else:
                                transaction_ops.append({
                                    "type": "system_state_dconf",
                                    "path": "dconf",
                                    "backup_path": bak_dconf_path,
                                    "is_dir": False
                                })

                                try:
                                    with open(dconf_file, "r", encoding="utf-8") as df:
                                        subprocess.run(["dconf", "load", "/"], stdin=df, timeout=10)
                                    results["restored_system_state"].append(t("restore_tag_dconf_settings"))
                                except Exception as e:
                                    results["warnings"].append(t("restore_warn_dconf_load_failed", error=str(e)))

                        cron_file = state_src / "crontab.txt"
                        if cron_file.exists() and shutil.which("crontab"):
                            bak_cron_path = None
                            cron_snapshot_ok = False
                            had_no_crontab = False
                            try:
                                proc_cron = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
                                if proc_cron.returncode == 0:
                                    cron_snapshot_ok = True
                                    cron_bak_file = tx_dir / f"crontab_backup_{timestamp}.txt"
                                    cron_bak_file.write_text(proc_cron.stdout, encoding="utf-8")
                                    bak_cron_path = str(cron_bak_file)
                                else:
                                    stderr_cron = ((proc_cron.stderr or "") + (proc_cron.stdout or "")).lower()
                                    if "no crontab" in stderr_cron:
                                        cron_snapshot_ok = True
                                        had_no_crontab = True
                                    else:
                                        logger.warning(t("restore_warn_crontab_backup_stderr", error=stderr_cron))
                            except Exception as ex:
                                logger.warning(t("restore_warn_crontab_backup_error", error=str(ex)))

                            # B-1: Geri yükleme öncesi anlık görüntü alınamazsa sistem durumu geri yüklemesini hiç yapma ve uyar
                            if not cron_snapshot_ok:
                                warn_msg = t("restore_warn_crontab_snapshot_failed")
                                logger.warning(warn_msg)
                                results["warnings"].append(warn_msg)
                            else:
                                transaction_ops.append({
                                    "type": "system_state_crontab",
                                    "path": "crontab",
                                    "backup_path": bak_cron_path,
                                    "had_no_crontab": had_no_crontab,
                                    "is_dir": False
                                })

                                try:
                                    subprocess.run(["crontab", str(cron_file)], timeout=5)
                                    results["restored_system_state"].append(t("restore_tag_crontab_settings"))
                                except Exception as e:
                                    results["warnings"].append(t("restore_warn_crontab_load_failed", error=str(e)))
    
            self._notify("tamamlandı", t("restore_notify_completed"), percent=100.0)
            return results
        finally:
            _save_transaction_log()

    def cleanup(self):
        """Geçici açılan dizini ve şifresi çözülmüş geçici dosyaları temizler."""
        if self.temp_extracted_dir and self.temp_extracted_dir.exists():
            shutil.rmtree(self.temp_extracted_dir, ignore_errors=True)
            self.temp_extracted_dir = None
        if self.merged_extracted_dir and self.merged_extracted_dir.exists():
            shutil.rmtree(self.merged_extracted_dir, ignore_errors=True)
            self.merged_extracted_dir = None
        if self.decrypted_temp_file and self.decrypted_temp_file.parent.exists():
            shutil.rmtree(self.decrypted_temp_file.parent, ignore_errors=True)
            self.decrypted_temp_file = None


def rollback_restore(
    transaction_file: Optional[Path] = None,
    progress_callback: Optional[Callable[[ProgressUpdate], None]] = None,
    transaction_dir: Optional[Path] = None,
    target_home: Optional[Union[Path, str]] = None
) -> Dict[str, Any]:
    """
    En son yapılan veya belirtilen geri yükleme işlemini geri alır (Rollback / Undo Restore).
    Yedeğe alınmış (.bak) dosyaları orijinal yerlerine taşır ve yeni oluşturulan dosyaları kaldırır.
    """
    if transaction_file is None:
        if transaction_dir:
            target_tx = Path(transaction_dir) / "last_restore.json"
        elif target_home:
            target_tx = Path(target_home) / ".config" / "distronomad" / "transactions" / "last_restore.json"
        else:
            home = Path(os.environ.get("HOME") or Path.home())
            target_tx = home / ".config" / "distronomad" / "transactions" / "last_restore.json"
    else:
        target_tx = Path(transaction_file)

    if not target_tx.exists():
        raise FileNotFoundError(t("restore_err_tx_log_not_found", path=str(target_tx)))

    with open(target_tx, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Aynı işlem ikinci kez geri alınırsa, ilk geri almadan sonra aynı yollarda oluşturulan
    # kullanıcı dosyaları ("created" kayıtları) silinir ve crontab yeniden kaldırılır; bu yüzden engellenir.
    if data.get("rolled_back_at"):
        raise RuntimeError(t("restore_err_already_rolled_back", time=data.get("rolled_back_at")))

    operations = data.get("operations", [])
    reverted_files = []
    removed_created = []
    unrecoverable_files = []
    warnings = []
    errors = []

    total_ops = len(operations)
    for idx, op in enumerate(reversed(operations), 1):
        op_type = op.get("type")
        orig_path = Path(op["path"])
        bak_path = Path(op["backup_path"]) if op.get("backup_path") else None

        if progress_callback:
            progress_callback(ProgressUpdate(
                phase="geri_alma",
                message=t("restore_notify_rollback_item", name=orig_path.name),
                current_item=orig_path.name,
                processed_count=idx,
                total_count=total_ops,
                percent=(idx / total_ops) * 100 if total_ops > 0 else 100.0
            ))

        try:
            if op_type == "replaced" and bak_path and bak_path.exists():
                if orig_path.is_dir() and not orig_path.is_symlink():
                    shutil.rmtree(orig_path, ignore_errors=True)
                elif orig_path.exists() or orig_path.is_symlink():
                    orig_path.unlink(missing_ok=True)
                shutil.move(bak_path, orig_path)
                reverted_files.append(str(orig_path))

            elif op_type == "created":
                if orig_path.is_dir() and not orig_path.is_symlink():
                    shutil.rmtree(orig_path, ignore_errors=True)
                elif orig_path.exists() or orig_path.is_symlink():
                    orig_path.unlink(missing_ok=True)
                removed_created.append(str(orig_path))

            elif op_type == "overwritten":
                # B-8: 'overwrite' stratejisinde ezilen dosyalar rollback sonucunda 'geri alınamadı' olarak raporlanır
                msg = t("restore_err_rollback_overwritten", path=str(orig_path))
                errors.append(msg)
                unrecoverable_files.append(str(orig_path))
                logger.warning(msg)

            elif op_type == "system_state_dconf":
                if bak_path and Path(bak_path).exists() and shutil.which("dconf"):
                    with open(bak_path, "r", encoding="utf-8") as bf:
                        subprocess.run(["dconf", "load", "/"], stdin=bf, timeout=10)
                    Path(bak_path).unlink(missing_ok=True)
                    reverted_files.append("dconf")
                else:
                    # B-1: Rollback hiçbir koşulda dconf reset -f / çalıştırmaz, yalnızca uyarır
                    warn_msg = t("restore_warn_rollback_dconf_no_snapshot")
                    logger.warning(warn_msg)
                    warnings.append(warn_msg)

            elif op_type == "system_state_crontab":
                if bak_path and Path(bak_path).exists() and shutil.which("crontab"):
                    subprocess.run(["crontab", str(bak_path)], timeout=5)
                    Path(bak_path).unlink(missing_ok=True)
                    reverted_files.append("crontab")
                elif op.get("had_no_crontab") is True and shutil.which("crontab"):
                    # B-1: Yalnızca geri yükleme öncesinde gerçekten hiç crontab olmadığı kesin olarak kaydedildiyse crontab -r çalıştırılır
                    subprocess.run(["crontab", "-r"], timeout=5)
                    removed_created.append("crontab")
                else:
                    warn_msg = t("restore_warn_rollback_crontab_no_snapshot")
                    logger.warning(warn_msg)
                    warnings.append(warn_msg)

        except Exception as e:
            errors.append(f"{orig_path}: {str(e)}")

    data["rolled_back_at"] = datetime.now().isoformat()
    try:
        with open(target_tx, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    return {
        "success": len(errors) == 0,
        "reverted_files": reverted_files,
        "removed_created": removed_created,
        "unrecoverable_files": unrecoverable_files,
        "errors": errors,
        "warnings": warnings,
        "transaction_file": str(target_tx)
    }
