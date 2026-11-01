"""
DistroNomad - Farklı Linux Dağıtımları Arası Paket Eşleme ve Çeviri Modülü.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any

from distronomad.i18n import t

logger = logging.getLogger(__name__)

# Modül seviyesinde eşleme sözlükleri (geriye uyumluluk ve dinamik önbellek için)
COMMON_PACKAGE_MAP: Dict[str, Dict[str, str]] = {}
FLATPAK_EQUIVALENTS: Dict[str, str] = {}

_last_loaded_user_path: Optional[Path] = None
_last_user_mtime: Optional[float] = None


def get_user_mappings_path() -> Path:
    """Kullanıcı tanımlı eşleme dosyasının yolunu döndürür (XDG uyumlu)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "distronomad" / "mappings.json"
    home = Path(os.environ.get("HOME") or Path.home())
    return home / ".config" / "distronomad" / "mappings.json"


def _load_builtin_mappings() -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """Paket içindeki varsayılan package_map.json dosyasını importlib.resources ile yükler."""
    raw_text: Optional[str] = None
    try:
        from importlib import resources
        if hasattr(resources, "files"):
            raw_text = resources.files("distronomad.data").joinpath("package_map.json").read_text(encoding="utf-8")
        else:
            raw_text = resources.read_text("distronomad.data", "package_map.json", encoding="utf-8")
    except Exception as e:
        logger.warning(t("packagemap_warn_builtin_importlib", error=str(e)))
        fallback_path = Path(__file__).resolve().parent / "data" / "package_map.json"
        if fallback_path.is_file():
            try:
                raw_text = fallback_path.read_text(encoding="utf-8")
            except Exception as fe:
                logger.warning(t("packagemap_warn_builtin_path", error=str(fe)))

    if not raw_text:
        return {}, {}

    try:
        data = json.loads(raw_text)
    except Exception as e:
        logger.warning(t("packagemap_warn_builtin_json", error=str(e)))
        return {}, {}

    if not isinstance(data, dict):
        return {}, {}

    pkgs = data.get("packages")
    flatpaks = data.get("flatpak")
    return (pkgs if isinstance(pkgs, dict) else {},
            flatpaks if isinstance(flatpaks, dict) else {})


def _load_user_mappings() -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """Kullanıcının özel mappings.json dosyasını yükler ve doğrular. Hata durumunda uyarır ve boş döner."""
    user_path = get_user_mappings_path()
    try:
        if not user_path.is_file():
            return {}, {}
    except Exception as e:
        logger.warning(t("packagemap_warn_user_exists_check", path=str(user_path), error=str(e)))
        return {}, {}

    try:
        content = user_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        msg = t("packagemap_warn_user_json", path=str(user_path), error=str(e))
        logger.warning(msg)
        return {}, {}

    if not isinstance(data, dict):
        msg = t("packagemap_warn_user_schema_root", path=str(user_path))
        logger.warning(msg)
        return {}, {}

    user_pkgs: Dict[str, Dict[str, str]] = {}
    user_flatpaks: Dict[str, str] = {}

    # packages doğrulaması (doğrudan format veya 'packages' alt anahtarı)
    is_direct = "packages" not in data and "flatpak" not in data
    raw_pkgs = data if is_direct else data.get("packages")

    if raw_pkgs is not None:
        if not isinstance(raw_pkgs, dict):
            msg = t("packagemap_warn_user_packages_object", path=str(user_path))
            logger.warning(msg)
        else:
            for pkg_name, family_map in raw_pkgs.items():
                if not isinstance(pkg_name, str) or not isinstance(family_map, dict):
                    msg = t("packagemap_warn_user_pkg_schema", path=str(user_path), pkg=str(pkg_name))
                    logger.warning(msg)
                    continue
                valid_family_map: Dict[str, str] = {}
                for fam, mapped_name in family_map.items():
                    if isinstance(fam, str) and isinstance(mapped_name, str):
                        valid_family_map[fam.strip().lower()] = mapped_name.strip()
                    else:
                        msg = t("packagemap_warn_user_fam_invalid", path=str(user_path), pkg=str(pkg_name), fam=str(fam))
                        logger.warning(msg)
                if valid_family_map:
                    user_pkgs[pkg_name.strip().lower()] = valid_family_map

    raw_flatpaks = data.get("flatpak")
    if raw_flatpaks is not None:
        if not isinstance(raw_flatpaks, dict):
            msg = t("packagemap_warn_user_flatpak_object", path=str(user_path))
            logger.warning(msg)
        else:
            for app_name, app_id in raw_flatpaks.items():
                if isinstance(app_name, str) and isinstance(app_id, str):
                    user_flatpaks[app_name.strip().lower()] = app_id.strip()
                else:
                    msg = t("packagemap_warn_user_flatpak_invalid", path=str(user_path), app=str(app_name))
                    logger.warning(msg)

    return user_pkgs, user_flatpaks


def load_package_mappings(reload: bool = False) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """
    Dahili paket haritası ile kullanıcı tanımlı kuralları birleştirir.
    Kullanıcı tanımlı eşlemeler varsayılanların üzerine yazılır.
    """
    global COMMON_PACKAGE_MAP, FLATPAK_EQUIVALENTS, _last_loaded_user_path, _last_user_mtime

    base_pkgs, base_flatpaks = _load_builtin_mappings()
    user_pkgs, user_flatpaks = _load_user_mappings()

    merged_pkgs: Dict[str, Dict[str, str]] = {}
    for k, v in base_pkgs.items():
        merged_pkgs[k] = dict(v)

    for pkg_name, family_map in user_pkgs.items():
        if pkg_name in merged_pkgs:
            merged_pkgs[pkg_name].update(family_map)
        else:
            merged_pkgs[pkg_name] = dict(family_map)

    merged_flatpaks: Dict[str, str] = dict(base_flatpaks)
    merged_flatpaks.update(user_flatpaks)

    COMMON_PACKAGE_MAP.clear()
    COMMON_PACKAGE_MAP.update(merged_pkgs)

    FLATPAK_EQUIVALENTS.clear()
    FLATPAK_EQUIVALENTS.update(merged_flatpaks)

    user_path = get_user_mappings_path()
    _last_loaded_user_path = user_path
    try:
        _last_user_mtime = user_path.stat().st_mtime if user_path.is_file() else None
    except Exception:
        _last_user_mtime = None

    return COMMON_PACKAGE_MAP, FLATPAK_EQUIVALENTS


def _ensure_mappings_loaded() -> None:
    """Eşlemelerin yüklü ve güncel olduğundan emin olur."""
    current_user_path = get_user_mappings_path()
    current_mtime: Optional[float] = None
    try:
        if current_user_path.is_file():
            current_mtime = current_user_path.stat().st_mtime
    except Exception:
        pass

    if (not COMMON_PACKAGE_MAP or
        current_user_path != _last_loaded_user_path or
        current_mtime != _last_user_mtime):
        load_package_mappings(reload=True)


# Modül ilk yüklendiğinde eşlemeleri başlat
load_package_mappings()


def detect_distro_family(distro_id: str, distro_like: Any = "") -> str:
    """Dağıtım kimliğini ana Linux ailesine (arch, debian, fedora, suse, alpine) indirger."""
    d_id = str(distro_id or "").lower()
    if isinstance(distro_like, list):
        d_like = " ".join(str(x) for x in distro_like).lower()
    else:
        d_like = str(distro_like or "").lower()

    combined = f"{d_id} {d_like}"

    if any(k in combined for k in ["arch", "cachyos", "manjaro", "endeavouros", "artix", "garuda"]):
        return "arch"
    if any(k in combined for k in ["debian", "ubuntu", "pop", "mint", "elementary", "kali", "raspbian"]):
        return "debian"
    if any(k in combined for k in ["fedora", "rhel", "centos", "rocky", "almalinux", "nobara"]):
        return "fedora"
    if any(k in combined for k in ["suse", "opensuse", "tumbleweed", "leap"]):
        return "suse"
    if "alpine" in combined:
        return "alpine"

    return "unknown"


def translate_package(source_pkg: str, source_family: str, target_family: str, fallback_to_source: bool = True) -> Optional[str]:
    """
    Kaynak dağıtımdaki paket adını hedef dağıtımdaki ada dönüştürür.
    Eğer özel bir eşleme yoksa fallback_to_source True ise orijinal adı, False ise None döndürür.
    """
    _ensure_mappings_loaded()
    clean_pkg = source_pkg.strip().lower()

    if source_family == target_family:
        return source_pkg

    # Doğrudan haritada arama yap
    for canonical_name, family_map in COMMON_PACKAGE_MAP.items():
        # Kaynak ailedeki adı kontrol et
        src_match = family_map.get(source_family)
        if (src_match and src_match.lower() == clean_pkg) or canonical_name.lower() == clean_pkg:
            target_match = family_map.get(target_family)
            if target_match:
                return target_match

    # Özel bir çeviri bulunamadıysa
    if fallback_to_source:
        return source_pkg
    return None


def get_flatpak_id(pkg_name: str) -> Optional[str]:
    """Popüler bir masaüstü uygulamasının Flatpak ID'sini döndürür."""
    _ensure_mappings_loaded()
    clean = pkg_name.strip().lower()
    return FLATPAK_EQUIVALENTS.get(clean)


SAFE_PACKAGE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9._+@:-]+$")
NPM_SCOPED_PACKAGE_REGEX = re.compile(r"^@[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")


def is_valid_package_name(pkg_name: str, manager: Optional[str] = None) -> bool:
    """Paket adının güvenli ve geçerli formatta olup olmadığını denetler (komut ve bayrak enjeksiyonu koruması)."""
    if not pkg_name or not isinstance(pkg_name, str):
        return False
    clean = pkg_name.strip()
    if clean.startswith("-") or "=" in clean:
        return False

    mgr = (manager or "").strip().lower()

    if "/" in clean:
        if mgr != "npm":
            return False
        if clean.count("/") != 1 or not clean.startswith("@"):
            return False
        scope, name = clean.split("/", 1)
        scope_body = scope[1:]
        if not scope_body or scope_body.startswith("-") or not name or name.startswith("-"):
            return False
        return bool(NPM_SCOPED_PACKAGE_REGEX.match(clean))

    return bool(SAFE_PACKAGE_NAME_REGEX.match(clean))


def get_install_command(manager: str, packages: List[str]) -> Tuple[List[str], str]:
    """
    Paket yöneticisine göre kurulum komutunu hazırlar.
    (cmd_list, description) tuple'ı döndürür.
    Tüm paket adları izin listesiyle doğrulanır ve destekleyen araçlarda '--' kullanılır.
    """
    if not packages:
        return ([], "")

    valid_pkgs = [p for p in packages if is_valid_package_name(p, manager=manager)]
    if not valid_pkgs:
        return ([], "")

    mgr = manager.lower()
    if mgr in ["pacman"]:
        return (["sudo", "pacman", "-S", "--needed", "--noconfirm", "--"] + valid_pkgs, t("pm_desc_pacman"))
    elif mgr in ["yay", "paru"]:
        return ([mgr, "-S", "--needed", "--noconfirm", "--"] + valid_pkgs, t("pm_desc_aur", mgr=mgr))
    elif mgr in ["apt", "apt-get"]:
        return (["sudo", "apt-get", "install", "-y", "--"] + valid_pkgs, t("pm_desc_apt"))
    elif mgr in ["dnf"]:
        return (["sudo", "dnf", "install", "-y", "--"] + valid_pkgs, t("pm_desc_dnf"))
    elif mgr in ["zypper"]:
        return (["sudo", "zypper", "install", "-y", "--"] + valid_pkgs, t("pm_desc_zypper"))
    elif mgr in ["flatpak"]:
        return (["flatpak", "install", "-y", "flathub", "--"] + valid_pkgs, t("pm_desc_flatpak"))
    elif mgr in ["snap"]:
        return (["sudo", "snap", "install", "--"] + valid_pkgs, t("pm_desc_snap"))
    elif mgr in ["pipx"]:
        return (["pipx", "install", "--"] + valid_pkgs, t("pm_desc_pipx"))
    elif mgr in ["cargo"]:
        return (["cargo", "install", "--"] + valid_pkgs, t("pm_desc_cargo"))
    elif mgr in ["npm"]:
        return (["sudo", "npm", "install", "-g", "--"] + valid_pkgs, t("pm_desc_npm"))
    elif mgr in ["vscode", "code"]:
        ext_args = []
        for ext in valid_pkgs:
            ext_args.extend(["--install-extension", ext])
        return (["code"] + ext_args, t("pm_desc_vscode"))
    elif mgr in ["codium", "vscodium"]:
        ext_args = []
        for ext in valid_pkgs:
            ext_args.extend(["--install-extension", ext])
        return (["codium"] + ext_args, t("pm_desc_codium"))
    else:
        return ([mgr, "install", "--"] + valid_pkgs, t("pm_desc_generic", mgr=mgr))
