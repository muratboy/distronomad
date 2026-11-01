"""
DistroNomad - Akıllı Hariç Tutma ve Çöp Filtresi (Smart Ignore Filter).
Yedek boyutunu şişiren geçici önbellek, derleme artıkları ve çöp dosyaları eler.
"""

from __future__ import annotations
import os
import fnmatch
from pathlib import Path
from typing import List, Optional


# Kesin çöp ve derleme artığı klasörleri (Özel yollarda ve konfigürasyonlarda ortak elenir)
CUSTOM_PATH_TRASH_DIRS = {
    "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache"
}

# Yalnızca konfigürasyon ve uygulama/tarayıcı ayarlarında elenen önbellek klasörleri
APP_CACHE_DIRS = {
    "GPUCache", "GrShaderCache", "Cache", "cache", "cache2",
    "startupCache", "Code Cache", "CachedData", "Crash Reports", "minidumps"
}

# Kesin çöp kalıpları
CUSTOM_PATH_TRASH_PATTERNS = [
    "*/node_modules/*",
    "*/__pycache__/*",
    "*.pyc",
    "*.pyo",
    "*/.venv/*",
    "*/venv/*",
    "*/.pytest_cache/*",
    "*/.mypy_cache/*",
    "*/.ruff_cache/*",
    "*.tmp",
    "*.swp",
    "*.swo",
    "*~",
    ".DS_Store",
    "Thumbs.db",
]

# Varsayılan Akıllı Hariç Tutma Kuralları (Smart Defaults - Konfigürasyonlar için)
DEFAULT_IGNORE_PATTERNS = [
    # Tarayıcı, Firefox, Flatpak ve Electron Önbellekleri
    "*/Cache/*",
    "*/cache/*",
    "*/cache2/*",
    "*/startupCache/*",
    "*/GPUCache/*",
    "*/GrShaderCache/*",
    "*/Code Cache/*",
    "*/CachedData/*",
    "*/CachedExtensionVSIXs/*",
    "*/DawnCache/*",
    "*/ShaderCache/*",
    "*/Service Worker/CacheStorage/*",
    "*/blob_storage/*",
    "*/Crash Reports/*",
    "*/minidumps/*",
    "*/datareporting/*",

    # Programlama Dili ve Derleme Artıkları
    "*/node_modules/*",
    "*/__pycache__/*",
    "*.pyc",
    "*.pyo",
    "*/.venv/*",
    "*/venv/*",
    "*/.pytest_cache/*",
    "*/.mypy_cache/*",
    "*/.ruff_cache/*",
    "*/.cargo/registry/*",
    "*/.cargo/git/*",
    "*/target/debug/*",
    "*/target/release/build/*",
    "*/.gradle/caches/*",

    # Sistem ve Uygulama Geçici Dosyaları
    "*/.cache/*",
    "*/.local/share/Trash/*",
    "*.tmp",
    "*.swp",
    "*.swo",
    "*~",
    ".DS_Store",
    "Thumbs.db",

    # Socket ve Kilit Dosyaları
    "*.sock",
    "*.lock.db",
    "SingletonSocket",
    "SingletonLock",
    "SingletonCookie",
    "RunningChromeVersion",
]


class SmartIgnoreFilter:
    """Dosya yollarını akıllı ve özel hariç tutma kurallarına göre filtreler."""

    def __init__(
        self,
        use_smart_defaults: bool = True,
        custom_patterns: Optional[List[str]] = None,
        ignore_file_path: Optional[Path] = None
    ):
        self.use_smart_defaults = use_smart_defaults
        self.patterns: List[str] = []
        self.user_patterns: List[str] = []

        if use_smart_defaults:
            self.patterns.extend(DEFAULT_IGNORE_PATTERNS)

        if custom_patterns:
            self.patterns.extend(custom_patterns)
            self.user_patterns.extend(custom_patterns)

        # .distronomadignore dosyasından oku
        if ignore_file_path and Path(ignore_file_path).exists():
            self._load_from_file(Path(ignore_file_path))
        else:
            default_user_file = Path.home() / ".distronomadignore"
            if default_user_file.exists():
                self._load_from_file(default_user_file)

    def _load_from_file(self, file_path: Path):
        """Kullanıcının ignore dosyasından kuralları yükler."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.patterns.append(line)
                        self.user_patterns.append(line)
        except Exception:
            pass

    def should_ignore(
        self,
        path: Path | str,
        base_path: Optional[Path | str] = None,
        is_custom_path: bool = False
    ) -> bool:
        """Verilen dosya yolunun hariç tutulması gerekip gerekmediğini denetler.
        Eğer base_path belirtilmişse kurallar yalnızca seçilen kökün ALTINDAKİ göreli yola uygulanır.
        is_custom_path=True olduğunda (özel klasörler), cache/Cache dizinleri elenmez;
        yalnızca kesin çöp kalıpları (node_modules, __pycache__, .venv, venv vb.) ve kullanıcı kuralları elenir.
        """
        path_obj = Path(path)
        if base_path is not None:
            try:
                base_obj = Path(base_path)
                if path_obj.resolve() == base_obj.resolve() or path_obj == base_obj:
                    return False
                rel_path = path_obj.relative_to(base_obj)
                path_str = str(rel_path).replace("\\", "/")
                if path_str in ("", "."):
                    return False
            except ValueError:
                path_str = str(path).replace("\\", "/")
        else:
            path_str = str(path).replace("\\", "/")

        path_name = os.path.basename(path_str)

        # Doğrudan tam eşleşen klasör isimleri (hızlı kontrol)
        parts = [p for p in path_str.split("/") if p and p != "."]

        if is_custom_path:
            # Özel yollarda: yalnızca kesin çöp klasörleri
            if any(part in CUSTOM_PATH_TRASH_DIRS for part in parts):
                return True
            # Yalnızca kesin çöp kalıpları ve kullanıcının özel kuralları
            active_patterns = (CUSTOM_PATH_TRASH_PATTERNS if self.use_smart_defaults else []) + self.user_patterns
        else:
            # Konfigürasyon ve uygulama/tarayıcı yollarında: tüm önbellek ve çöp kuralları
            fast_dirs = CUSTOM_PATH_TRASH_DIRS | APP_CACHE_DIRS
            if any(part in fast_dirs for part in parts):
                return True
            active_patterns = self.patterns

        for pattern in active_patterns:
            # Hem tüm yola hem de dosya adına göre kontrol et
            if fnmatch.fnmatch(path_str, pattern) or fnmatch.fnmatch(path_name, pattern):
                return True
            # Klasör eşleşmesi: örn pattern '*/node_modules/*' ise içinde geçiyor mu
            if pattern.startswith("*/") and pattern.endswith("/*"):
                core_dir = pattern[2:-2]
                if f"/{core_dir}/" in f"/{path_str}/":
                    return True

        return False
