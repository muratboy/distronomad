"""
DistroNomad - Veri Modelleri ve Veri Yapıları.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
import os
from datetime import datetime


@dataclass
class SystemInfo:
    """Sistem bilgileri ve donanım/dağıtım detayları."""
    distro_id: str = "unknown"
    distro_name: str = "Linux"
    distro_like: str = ""
    distro_version: str = ""
    kernel_version: str = ""
    hostname: str = ""
    username: str = ""
    home_dir: str = ""
    available_package_managers: List[str] = field(default_factory=list)
    desktop_environment: str = "unknown"
    shell: str = "sh"

    @classmethod
    def current(cls) -> "SystemInfo":
        """Mevcut sistem bilgilerini toplar."""
        import platform
        import os

        distro_id = "linux"
        distro_name = "Linux"
        distro_like = ""
        distro_version = ""

        # /etc/os-release dosyasını oku
        if os.path.exists("/etc/os-release"):
            try:
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        v = v.strip('"\'')
                        if k == "ID":
                            distro_id = v.lower()
                        elif k == "NAME":
                            distro_name = v
                        elif k == "ID_LIKE":
                            distro_like = v.lower()
                        elif k == "VERSION_ID":
                            distro_version = v
            except Exception:
                pass

        # Masaüstü ortamı tespiti
        de = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "Headless / CLI"

        # Kullanılan kabuk (Shell)
        shell = os.path.basename(os.environ.get("SHELL", "bash"))

        # Paket yöneticisi kontrolü
        pms = []
        for pm in ["pacman", "apt", "dnf", "zypper", "flatpak", "snap", "pipx", "cargo", "npm"]:
            import shutil
            if shutil.which(pm):
                pms.append(pm)

        return cls(
            distro_id=distro_id,
            distro_name=distro_name,
            distro_like=distro_like,
            distro_version=distro_version,
            kernel_version=platform.release(),
            hostname=platform.node(),
            username=os.environ.get("USER", "user"),
            home_dir=os.path.expanduser("~"),
            available_package_managers=pms,
            desktop_environment=de,
            shell=shell
        )


@dataclass
class PackageItem:
    """Tespit edilen bir paket veya uygulama."""
    name: str
    manager: str              # pacman, apt, dnf, flatpak, snap, pipx, cargo, npm, appimage
    version: str = ""
    description: str = ""
    category: str = "Genel"   # Desktop, CLI, Geliştirme, Sistem, Medya, vb.
    is_selected: bool = True
    app_id: str = ""          # Flatpak app-id veya tam tanımlayıcı (örn: org.videolan.VLC)
    category_id: str = "general"

    @property
    def display_name(self) -> str:
        if self.app_id and self.app_id != self.name:
            return f"{self.name} ({self.app_id})"
        return self.name

    @property
    def is_gui_app(self) -> bool:
        """Paketin masaüstü/GUI uygulaması olup olmadığını dile bağlı olmadan belirler."""
        if self.category_id in {"desktop_wm", "desktop_flatpak", "desktop_app", "editor_ide"}:
            return True
        if self.manager == "flatpak":
            return True
        cat = (self.category or "").lower()
        return any(k in cat for k in ["masaüstü", "flatpak", "medya", "düzenleyici", "desktop", "media", "editor"])


def format_bytes(size: int | float) -> str:
    """Byte cinsinden boyutu insan tarafından okunabilir birime dönüştürür (örn. 20.0 KB, 1.5 MB)."""
    s = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if s < 1024.0:
            return f"{s:.1f} {unit}"
        s /= 1024.0
    return f"{s:.1f} TB"


@dataclass
class ConfigItem:
    """Tespit edilen bir konfigürasyon dizini veya dotfile."""
    id: str                   # benzersiz tanımlayıcı
    name: str                 # gösterim adı (örn: Neovim, Hyprland, .bashrc)
    category: str             # Kabuk, Masaüstü, Terminal, Düzenleyici, Güvenlik, Temalar, Diğer
    source_path: str          # tam kaynak yolu (/home/.../.config/nvim)
    rel_path: str             # HOME'a göre göreli yol (.config/nvim veya .bashrc)
    size_bytes: int = 0
    item_count: int = 1
    is_directory: bool = True
    is_sensitive: bool = False # SSH anahtarları, GPG vb.
    is_selected: bool = True
    category_id: str = "other"

    @property
    def formatted_size(self) -> str:
        return format_bytes(self.size_bytes)


@dataclass
class CustomPathItem:
    """Kullanıcının yedeklemek istediği özel dosya veya klasör."""
    path: str
    label: str = ""
    exists: bool = True
    is_dir: bool = True
    size_bytes: int = 0
    file_count: int = 0
    is_selected: bool = True
    label_key: str = ""

    @property
    def formatted_size(self) -> str:
        return format_bytes(self.size_bytes)


@dataclass
class BackupManifest:
    """Yedek arşivinin içindeki meta veri ve manifest."""
    created_at: str
    version: str
    system_info: SystemInfo
    packages: List[PackageItem] = field(default_factory=list)
    configs: List[ConfigItem] = field(default_factory=list)
    custom_paths: List[CustomPathItem] = field(default_factory=list)
    archive_format: str = "tar.zst"
    total_bytes: int = 0
    file_hashes: Dict[str, str] = field(default_factory=dict)
    backup_type: str = "full"                 # "full" veya "incremental"
    base_backup_name: str = ""                # Temel yedeğin dosya adı veya dizini
    base_backup_created_at: str = ""          # Temel yedeğin tarihi
    file_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict) # {rel_key: {"size": int, "mtime": float, "sha256": str}}
    incremental_stats: Dict[str, int] = field(default_factory=dict) # {"scanned": X, "changed": Y, "unchanged": Z, "bytes_saved": W}
    hardware_info: Dict[str, Any] = field(default_factory=dict) # Donanım profili
    system_state_info: Dict[str, Any] = field(default_factory=dict) # dconf, crontab, gruplar, udev/fstab referansları
    deleted_files: List[str] = field(default_factory=list) # Artımlı yedekte silinen dosyalar
    skipped_files: List[Dict[str, str]] = field(default_factory=list) # Okunamayan veya atlanan dosyalar [{"path": ..., "reason": ...}]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackupManifest":
        sys_info_data = data.get("system_info", {})
        sys_info = SystemInfo(**sys_info_data)

        packages = []
        for pkg in data.get("packages", []):
            if isinstance(pkg, dict):
                p_copy = dict(pkg)
                if "category_id" not in p_copy:
                    cat = p_copy.get("category", "").lower()
                    if any(k in cat for k in ["flatpak"]):
                        p_copy["category_id"] = "desktop_flatpak"
                    elif any(k in cat for k in ["masaüstü", "desktop"]):
                        p_copy["category_id"] = "desktop_app"
                    elif any(k in cat for k in ["düzenleyici", "editor"]):
                        p_copy["category_id"] = "editor_ide"
                    elif any(k in cat for k in ["terminal"]):
                        p_copy["category_id"] = "terminal_shell"
                    elif any(k in cat for k in ["snap"]):
                        p_copy["category_id"] = "snap"
                packages.append(PackageItem(**p_copy))

        configs = []
        for cfg in data.get("configs", []):
            if isinstance(cfg, dict):
                c_copy = dict(cfg)
                configs.append(ConfigItem(**c_copy))

        custom_paths = []
        for cp in data.get("custom_paths", []):
            if isinstance(cp, dict):
                cp_copy = dict(cp)
                custom_paths.append(CustomPathItem(**cp_copy))

        return cls(
            created_at=data.get("created_at", datetime.now().isoformat()),
            version=data.get("version", "1.0.0"),
            system_info=sys_info,
            packages=packages,
            configs=configs,
            custom_paths=custom_paths,
            archive_format=data.get("archive_format", "tar.zst"),
            total_bytes=data.get("total_bytes", 0),
            file_hashes=data.get("file_hashes", {}),
            backup_type=data.get("backup_type", "full"),
            base_backup_name=data.get("base_backup_name", ""),
            base_backup_created_at=data.get("base_backup_created_at", ""),
            file_metadata=data.get("file_metadata", {}),
            incremental_stats=data.get("incremental_stats", {}),
            hardware_info=data.get("hardware_info", {}),
            system_state_info=data.get("system_state_info", {}),
            deleted_files=data.get("deleted_files", []),
            skipped_files=data.get("skipped_files", [])
        )


@dataclass
class RestoreOptions:
    """Geri yükleme seçenekleri."""
    target_home: str = ""
    target_root: str = "/"
    selected_packages: List[PackageItem] = field(default_factory=list)
    selected_configs: List[ConfigItem] = field(default_factory=list)
    selected_custom_paths: List[CustomPathItem] = field(default_factory=list)
    conflict_strategy: str = "backup"   # "backup" (rename to .bak), "overwrite", "skip"
    install_packages: bool = True
    restore_system_state: bool = False
    dry_run: bool = False
    base_backup_path: str = ""
    install_drivers: bool = False
    allow_external_paths: bool = False
    allow_root_paths: bool = False


@dataclass
class ProgressUpdate:
    """İlerleme güncelleme olayı."""
    phase: str           # "tarama", "paketleme", "sıkıştırma", "kurulum", "tamamlandı", "hata"
    message: str
    current_item: str = ""
    processed_count: int = 0
    total_count: int = 0
    processed_bytes: int = 0
    total_bytes: int = 0
    percent: float = 0.0
