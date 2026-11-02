"""
DistroNomad - Sistem, Paket, Konfigürasyon ve Dosya Tarama Modülü.
"""

import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable, Any
from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem
from distronomad.packagemap import get_flatpak_id
from distronomad.i18n import t

logger = logging.getLogger(__name__)


# Bilinen konfigürasyon uygulamaları: Ad -> (category_id, desc_key)
KNOWN_CONFIG_CATEGORIES: Dict[str, Tuple[str, str]] = {
    "hypr": ("desktop_wm", "cfg_desc_hypr"),
    "sway": ("desktop_wm", "cfg_desc_sway"),
    "i3": ("desktop_wm", "cfg_desc_i3"),
    "waybar": ("desktop_wm", "cfg_desc_waybar"),
    "rofi": ("desktop_wm", "cfg_desc_rofi"),
    "wofi": ("desktop_wm", "cfg_desc_wofi"),
    "dunst": ("desktop_wm", "cfg_desc_dunst"),
    "mako": ("desktop_wm", "cfg_desc_mako"),
    "nvim": ("editor", "cfg_desc_nvim"),
    "vim": ("editor", "cfg_desc_vim"),
    "helix": ("editor", "cfg_desc_helix"),
    "zed": ("editor", "cfg_desc_zed"),
    "Code": ("editor", "cfg_desc_code"),
    "Cursor": ("editor", "cfg_desc_cursor"),
    "VSCodium": ("editor", "cfg_desc_vscodium"),
    "kitty": ("terminal", "cfg_desc_kitty"),
    "alacritty": ("terminal", "cfg_desc_alacritty"),
    "wezterm": ("terminal", "cfg_desc_wezterm"),
    "foot": ("terminal", "cfg_desc_foot"),
    "zellij": ("terminal", "cfg_desc_zellij"),
    "btop": ("system_monitor", "cfg_desc_btop"),
    "htop": ("system_monitor", "cfg_desc_htop"),
    "fastfetch": ("system_monitor", "cfg_desc_fastfetch"),
    "neofetch": ("system_monitor", "cfg_desc_neofetch"),
    "fish": ("shell", "cfg_desc_fish"),
    "starship.toml": ("shell", "cfg_desc_starship"),
    "mpv": ("media", "cfg_desc_mpv"),
    "pipewire": ("media_audio", "cfg_desc_pipewire"),
    "wireplumber": ("media_audio", "cfg_desc_wireplumber"),
    "easyeffects": ("media_audio", "cfg_desc_easyeffects"),
    "ranger": ("file_manager", "cfg_desc_ranger"),
    "yazi": ("file_manager", "cfg_desc_yazi"),
    "thunar": ("file_manager", "cfg_desc_thunar"),
    "obs-studio": ("media", "cfg_desc_obs"),
    "discord": ("communication", "cfg_desc_discord"),
    "spotify": ("media", "cfg_desc_spotify"),
    "lazygit": ("dev", "cfg_desc_lazygit"),
    "gh": ("dev", "cfg_desc_gh"),
    "MangoHud": ("gaming", "cfg_desc_mangohud"),
    "heroic": ("gaming", "cfg_desc_heroic"),
    "lutris": ("gaming", "cfg_desc_lutris"),
    "autostart": ("system", "cfg_desc_autostart"),
    "systemd": ("system", "cfg_desc_systemd"),
    "mimeapps.list": ("system", "cfg_desc_mimeapps"),
    "user-dirs.dirs": ("system", "cfg_desc_userdirs"),
}

# Standart kullanıcı ana dizin dotfile dosyaları: (dosya_adı, category_id, desc_key)
COMMON_DOTFILES: List[Tuple[str, str, str]] = [
    (".bashrc", "shell", "dot_desc_bashrc"),
    (".bash_profile", "shell", "dot_desc_bash_profile"),
    (".profile", "shell", "dot_desc_profile"),
    (".zshrc", "shell", "dot_desc_zshrc"),
    (".zshenv", "shell", "dot_desc_zshenv"),
    (".zprofile", "shell", "dot_desc_zprofile"),
    (".p10k.zsh", "shell", "dot_desc_p10k"),
    (".inputrc", "shell", "dot_desc_inputrc"),
    (".bash_history", "shell_history", "dot_desc_bash_history"),
    (".zsh_history", "shell_history", "dot_desc_zsh_history"),
    (".python_history", "shell_history", "dot_desc_python_history"),
    (".psql_history", "shell_history", "dot_desc_psql_history"),
    (".mysql_history", "shell_history", "dot_desc_mysql_history"),
    (".sqlite_history", "shell_history", "dot_desc_sqlite_history"),
    (".gitconfig", "dev", "dot_desc_gitconfig"),
    (".gitignore_global", "dev", "dot_desc_gitignore_global"),
    (".git-credentials", "dev_identity", "dot_desc_git_credentials"),
    (".gdbinit", "dev", "dot_desc_gdbinit"),
    (".cargo/config.toml", "dev", "dot_desc_cargo"),
    (".m2/settings.xml", "dev", "dot_desc_maven"),
    (".tmux.conf", "terminal", "dot_desc_tmux"),
    (".vimrc", "editor", "dot_desc_vimrc"),
    (".Xresources", "system", "dot_desc_xresources"),
    (".xprofile", "system", "dot_desc_xprofile"),
    (".xinitrc", "system", "dot_desc_xinitrc"),
    (".npmrc", "dev_identity", "dot_desc_npmrc"),
    (".netrc", "security_keys", "dot_desc_netrc"),
    (".wgetrc", "cli_util", "dot_desc_wgetrc"),
    (".curlrc", "cli_util", "dot_desc_curlrc"),
]


def get_dir_size_and_count(path: Path) -> Tuple[int, int]:
    """Bir dizinin veya dosyanın toplam boyutunu ve dosya sayısını hesaplar."""
    try:
        if not path.exists() and not path.is_symlink():
            return 0, 0
    except OSError:
        return 0, 0

    if path.is_symlink():
        try:
            return path.lstat().st_size, 1
        except OSError:
            return 0, 1

    try:
        if path.is_file():
            return path.stat().st_size, 1
    except OSError:
        return 0, 1

    total_size = 0
    total_count = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_symlink():
                    total_size += entry.stat(follow_symlinks=False).st_size
                    total_count += 1
                elif entry.is_file(follow_symlinks=False):
                    total_size += entry.stat(follow_symlinks=False).st_size
                    total_count += 1
                elif entry.is_dir(follow_symlinks=False):
                    sub_size, sub_count = get_dir_size_and_count(Path(entry.path))
                    total_size += sub_size
                    total_count += sub_count
            except OSError:
                continue
    except OSError:
        pass

    return total_size, total_count


# Açıkken profil dosyalarına (çerez, geçmiş, oturum veritabanları) sürekli yazan tarayıcı ve e-posta istemcileri:
# (görünen ad, süreç adları, ev dizinine göre profil dizinleri)
PROFILE_WRITING_APPS = [
    ("Firefox", ("firefox", "firefox-esr", "firefox-bin"), (".mozilla/firefox", ".config/mozilla/firefox", ".var/app/org.mozilla.firefox")),
    ("Thunderbird", ("thunderbird", "thunderbird-bin"), (".thunderbird", ".var/app/org.mozilla.Thunderbird")),
    ("Zen", ("zen", "zen-bin"), (".zen", ".var/app/app.zen_browser.zen")),
    ("LibreWolf", ("librewolf", "librewolf-bin"), (".librewolf", ".var/app/io.gitlab.librewolf-community")),
    ("Waterfox", ("waterfox", "waterfox-bin"), (".waterfox",)),
    ("Brave", ("brave",), (".config/BraveSoftware", ".var/app/com.brave.Browser")),
    ("Google Chrome", ("chrome",), (".config/google-chrome", ".var/app/com.google.Chrome")),
    ("Chromium", ("chromium",), (".config/chromium", ".var/app/org.chromium.Chromium")),
    ("Vivaldi", ("vivaldi-bin",), (".config/vivaldi",)),
    ("Opera", ("opera",), (".config/opera",)),
    ("Microsoft Edge", ("msedge",), (".config/microsoft-edge",)),
]


def find_running_browsers(selected_paths: List[str], home_dir: Optional[str] = None) -> List[str]:
    """Profil dizini seçili yedek yollarıyla çakışan ve şu anda açık olan tarayıcı / e-posta istemcilerinin adlarını döndürür.
    Bu uygulamalar yedekleme sırasında profil dosyalarına yazmaya devam ettiği için o dosyalar tutarsız yedeklenebilir."""
    if not selected_paths or not shutil.which("pgrep"):
        return []
    process_names = sorted({name for _, names, _ in PROFILE_WRITING_APPS for name in names})
    try:
        res = subprocess.run(
            ["pgrep", "-l", "-x", "-u", str(os.getuid()), "|".join(process_names)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5
        )
    except Exception:
        return []
    if res.returncode != 0:
        return []
    running = {parts[1].strip() for parts in (line.split(None, 1) for line in res.stdout.splitlines()) if len(parts) == 2}

    home = Path(home_dir) if home_dir else Path.home()
    selected = [Path(os.path.abspath(p)) for p in selected_paths]
    found = []
    for display_name, names, profile_dirs in PROFILE_WRITING_APPS:
        if not running.intersection(names):
            continue
        for rel in profile_dirs:
            profile = Path(os.path.abspath(home / rel))
            # Profil seçili yolun içindeyse (ör. tüm ~/.config) veya seçili yol profilin içindeyse çakışır
            if any(s == profile or s in profile.parents or profile in s.parents for s in selected):
                found.append(display_name)
                break
    return found


class SystemScanner:
    """Sistem, uygulama, ayar ve dosya tarayıcısı."""

    def __init__(self, home_dir: Optional[str] = None, os_release_file: Optional[str] = None):
        self.home_dir = Path(home_dir if home_dir else os.path.expanduser("~"))
        self.os_release_file = os_release_file or "/etc/os-release"

    def scan_system_info(self) -> SystemInfo:
        """Sistem temel bilgilerini toplar."""
        info = SystemInfo.current()
        info.home_dir = str(self.home_dir)

        if os.path.exists(self.os_release_file):
            try:
                with open(self.os_release_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        v = v.strip('"\'')
                        if k == "ID":
                            info.distro_id = v.lower()
                        elif k == "NAME":
                            info.distro_name = v
                        elif k == "ID_LIKE":
                            info.distro_like = v.lower()
                        elif k == "VERSION_ID":
                            info.distro_version = v
            except Exception:
                pass

        return info

    def scan_installed_packages(self, pms_to_scan: Optional[List[str]] = None) -> List[PackageItem]:
        """Sistemde kurulu olan paketleri ve editör eklentilerini tespit eder."""
        packages: List[PackageItem] = []

        # Taranacak paket yöneticileri
        available = []
        for pm in ["pacman", "apt", "dnf", "zypper", "flatpak", "snap", "pipx", "cargo", "npm", "code", "codium"]:
            if shutil.which(pm):
                available.append(pm)

        target_pms = pms_to_scan if pms_to_scan is not None else available

        task_defs: List[Tuple[Callable, Tuple[Any, ...]]] = []

        # 1. Pacman (Arch / CachyOS / Manjaro)
        if "pacman" in target_pms:
            task_defs.append((self._scan_pacman, ()))

        # 2. Apt (Debian / Ubuntu / Mint / Pop)
        if "apt" in target_pms:
            task_defs.append((self._scan_apt, ()))

        # 3. DNF (Fedora / RHEL)
        if "dnf" in target_pms:
            task_defs.append((self._scan_dnf, ()))

        # 4. Flatpak (Evrensel)
        if "flatpak" in target_pms:
            task_defs.append((self._scan_flatpak, ()))

        # 5. Snap (Evrensel)
        if "snap" in target_pms:
            task_defs.append((self._scan_snap, ()))

        # 6. Pipx (Python CLI araçları)
        if "pipx" in target_pms:
            task_defs.append((self._scan_pipx, ()))

        # 7. Cargo (Rust araçları)
        if "cargo" in target_pms:
            task_defs.append((self._scan_cargo, ()))

        # 8. NPM Global
        if "npm" in target_pms:
            task_defs.append((self._scan_npm, ()))

        # 9. VS Code / VSCodium Eklentileri
        if "code" in target_pms or "vscode" in target_pms:
            task_defs.append((self._scan_vscode, ("code", "vscode", "VS Code Eklentisi")))
        if "codium" in target_pms:
            task_defs.append((self._scan_vscode, ("codium", "codium", "VSCodium Eklentisi")))

        if not task_defs:
            return packages

        max_workers = min(len(task_defs), 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_list = [executor.submit(func, *args) for func, args in task_defs]
            for future in future_list:
                try:
                    res = future.result()
                    if res:
                        packages.extend(res)
                except Exception as e:
                    logger.warning("Paket tarayıcı iş parçacığında beklenmeyen hata: %s", e)

        return packages

    def _run_scan_cmd(
        self,
        cmd: List[str],
        manager: str,
        timeout: int = 15,
        action_noun: str = "paket"
    ) -> Optional[str]:
        """Paket yöneticisi veya eklenti komutunu çalıştırır ve ortak hata/loglama/zaman aşımı kalıbını yönetir."""
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
            if proc.returncode == 0:
                return proc.stdout
            logger.debug("%s %s taraması sıfırdan farklı çıkış kodu döndürdü (%d): %s", manager, action_noun, proc.returncode, proc.stderr.strip())
        except FileNotFoundError as e:
            logger.debug("%s komutu bulunamadı: %s", manager, e)
        except subprocess.TimeoutExpired as e:
            logger.warning("%s %s taraması zaman aşımına uğradı: %s", manager, action_noun, e)
        except Exception as e:
            logger.warning("%s %s taramasında beklenmeyen hata: %s", manager, action_noun, e)
        return None

    def _scan_pacman(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["pacman", "-Qet"], manager="pacman", timeout=15)
        if stdout is None:
            return pkgs
        for line in stdout.strip().splitlines():
            parts = line.split(maxsplit=1)
            if parts:
                name = parts[0]
                ver = parts[1] if len(parts) > 1 else ""
                flatpak_eq = get_flatpak_id(name)
                cat_id = self._get_package_category_id(name)
                cat = t(f"cat_{cat_id}")
                pkgs.append(PackageItem(
                    name=name,
                    manager="pacman",
                    version=ver,
                    category=cat,
                    category_id=cat_id,
                    app_id=flatpak_eq or name,
                    is_selected=True
                ))
        return pkgs

    def _scan_apt(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["apt-mark", "showmanual"], manager="apt", timeout=15)
        if stdout is None:
            return pkgs
        for line in stdout.strip().splitlines():
            name = line.strip()
            if name:
                flatpak_eq = get_flatpak_id(name)
                cat_id = self._get_package_category_id(name)
                cat = t(f"cat_{cat_id}")
                pkgs.append(PackageItem(
                    name=name,
                    manager="apt",
                    category=cat,
                    category_id=cat_id,
                    app_id=flatpak_eq or name,
                    is_selected=True
                ))
        return pkgs

    def _scan_dnf(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["dnf", "repoquery", "--userinstalled", "--qf", "%{name} %{version}"], manager="dnf", timeout=15)
        if stdout is None:
            return pkgs
        for line in stdout.strip().splitlines():
            parts = line.split(maxsplit=1)
            if parts:
                name = parts[0]
                ver = parts[1] if len(parts) > 1 else ""
                flatpak_eq = get_flatpak_id(name)
                cat_id = self._get_package_category_id(name)
                cat = t(f"cat_{cat_id}")
                pkgs.append(PackageItem(
                    name=name,
                    manager="dnf",
                    version=ver,
                    category=cat,
                    category_id=cat_id,
                    app_id=flatpak_eq or name,
                    is_selected=True
                ))
        return pkgs

    def _scan_flatpak(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["flatpak", "list", "--app", "--columns=application,name,version"], manager="flatpak", timeout=10)
        if stdout is None:
            return pkgs
        for line in stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                app_id = parts[0].strip()
                name = parts[1].strip()
                ver = parts[2].strip() if len(parts) > 2 else ""
                pkgs.append(PackageItem(
                    name=name,
                    manager="flatpak",
                    version=ver,
                    category=t("cat_desktop_flatpak"),
                    category_id="desktop_flatpak",
                    app_id=app_id,
                    is_selected=True
                ))
        return pkgs

    def _scan_snap(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["snap", "list"], manager="snap", timeout=10)
        if stdout is None:
            return pkgs
        lines = stdout.strip().splitlines()
        # İlk başlık satırını atla
        for line in lines[1:]:
            parts = line.split()
            if parts:
                name = parts[0]
                ver = parts[1] if len(parts) > 1 else ""
                if name not in ["bare", "core", "core20", "core22", "snapd"]:
                    pkgs.append(PackageItem(
                        name=name,
                        manager="snap",
                        version=ver,
                        category=t("cat_snap"),
                        category_id="snap",
                        is_selected=True
                    ))
        return pkgs

    def _scan_pipx(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["pipx", "list", "--short"], manager="pipx", timeout=10)
        if stdout is None:
            return pkgs
        for line in stdout.strip().splitlines():
            parts = line.split()
            if parts:
                name = parts[0]
                ver = parts[1] if len(parts) > 1 else ""
                pkgs.append(PackageItem(
                    name=name,
                    manager="pipx",
                    version=ver,
                    category=t("cat_cli_python"),
                    category_id="cli_python",
                    is_selected=True
                ))
        return pkgs

    def _scan_cargo(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["cargo", "install", "--list"], manager="cargo", timeout=10)
        if stdout is None:
            return pkgs
        for line in stdout.strip().splitlines():
            if line and not line.startswith(" ") and ":" in line:
                name_ver = line.split(":")[0].strip()
                parts = name_ver.split()
                if parts:
                    name = parts[0]
                    ver = parts[1] if len(parts) > 1 else ""
                    pkgs.append(PackageItem(
                        name=name,
                        manager="cargo",
                        version=ver,
                        category=t("cat_cli_rust"),
                        category_id="cli_rust",
                        is_selected=True
                    ))
        return pkgs

    def _scan_npm(self) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd(["npm", "list", "-g", "--depth=0", "--json"], manager="npm", timeout=10)
        if stdout is None:
            return pkgs
        try:
            data = json.loads(stdout)
            deps = data.get("dependencies", {})
            for name, details in deps.items():
                if name in ("npm", "corepack"):
                    continue
                ver = details.get("version", "") if isinstance(details, dict) else ""
                pkgs.append(PackageItem(
                    name=name,
                    manager="npm",
                    version=ver,
                    category=t("cat_npm_global"),
                    category_id="npm_global",
                    is_selected=True
                ))
        except Exception:
            pass
        return pkgs

    def _scan_vscode(self, binary: str = "code", manager_name: str = "vscode", category: Optional[str] = None) -> List[PackageItem]:
        pkgs = []
        stdout = self._run_scan_cmd([binary, "--list-extensions", "--show-versions"], manager=manager_name, timeout=10, action_noun="eklenti")
        if stdout is None:
            return pkgs
        cat_id = "vscode_ext" if manager_name == "vscode" else "codium_ext"
        cat_label = category or t(f"cat_{cat_id}")
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if "@" in line:
                ext_id, ver = line.rsplit("@", 1)
            else:
                ext_id, ver = line, ""
            pkgs.append(PackageItem(
                name=ext_id,
                manager=manager_name,
                version=ver,
                category=cat_label,
                category_id=cat_id,
                is_selected=True
            ))
        return pkgs

    def _get_package_category_id(self, name: str) -> str:
        """Paket adına göre dilden bağımsız kategori kimliği belirler."""
        n = name.lower()
        if any(x in n for x in ["hypr", "sway", "waybar", "rofi", "dunst", "polybar", "i3", "kwin", "plasma"]):
            return "desktop_wm"
        if any(x in n for x in ["kitty", "alacritty", "foot", "terminal", "wezterm", "tmux", "zsh", "fish", "bash"]):
            return "terminal_shell"
        if any(x in n for x in ["nvim", "vim", "code", "emacs", "nano", "helix", "vscodium"]):
            return "editor_ide"
        if any(x in n for x in ["git", "gcc", "clang", "cmake", "make", "rust", "python", "node", "docker", "podman"]):
            return "dev_build"
        if any(x in n for x in ["firefox", "chromium", "chrome", "brave", "vlc", "mpv", "spotify", "discord", "gimp", "obs"]):
            return "desktop_app"
        if any(x in n for x in ["ripgrep", "fd", "bat", "fzf", "btop", "htop", "fastfetch", "curl", "wget", "jq", "eza"]):
            return "cli_tool"
        return "system_general"

    def _categorize_package(self, name: str) -> str:
        """Paket adına göre akıllı kategori etiketi (i18n) belirler."""
        cat_id = self._get_package_category_id(name)
        return t(f"cat_{cat_id}")

    def scan_configs(self) -> List[ConfigItem]:
        """
        Tüm ~/.config dizinlerini, ana dizin dotfile'larını, kabuk geçmişlerini,
        GNOME/KDE masaüstü bileşenlerini, tarayıcı profillerini, Flatpak verilerini
        ve hassas anahtarları tarar.
        """
        configs: List[ConfigItem] = []

        # 1. ~/.config dizini içindekiler
        config_dir = self.home_dir / ".config"
        if config_dir.exists() and config_dir.is_dir():
            try:
                for entry in sorted(os.scandir(config_dir), key=lambda e: e.name.lower()):
                    try:
                        entry_path = Path(entry.path)
                        is_dir = entry.is_dir(follow_symlinks=False)
                        size, count = get_dir_size_and_count(entry_path)

                        # Bilinen kategoriyi ve açıklamayı bul
                        known = KNOWN_CONFIG_CATEGORIES.get(entry.name)
                        if known:
                            cat_id = known[0]
                            cat = t(f"cat_{cat_id}")
                            desc = t(known[1])
                            name = f"{entry.name} ({desc})"
                        else:
                            cat_id = "app_config"
                            cat = t("cat_app_config")
                            name = entry.name

                        # GitHub CLI (~/.config/gh) token içerir: is_sensitive=True, varsayılan seçimsiz
                        is_sensitive = (entry.name == "gh")
                        is_selected = not is_sensitive

                        configs.append(ConfigItem(
                            id=f"config_{entry.name}",
                            name=name,
                            category=cat,
                            source_path=str(entry_path),
                            rel_path=f".config/{entry.name}",
                            size_bytes=size,
                            item_count=count,
                            is_directory=is_dir,
                            is_sensitive=is_sensitive,
                            is_selected=is_selected,
                            category_id=cat_id
                        ))
                    except OSError:
                        continue
            except OSError:
                pass

        # 2. $HOME içindeki standart dotfile dosyaları ve komut geçmişleri
        sensitive_history_and_tokens = {
            ".bash_history", ".zsh_history", ".python_history", ".psql_history",
            ".mysql_history", ".sqlite_history", ".npmrc", ".netrc", ".git-credentials"
        }
        for dotfile, cat_id, desc_key in COMMON_DOTFILES:
            dot_path = self.home_dir / dotfile
            if dot_path.exists():
                size, count = get_dir_size_and_count(dot_path)
                safe_id = dotfile.replace(".", "").replace("/", "_")
                is_sens = dotfile in sensitive_history_and_tokens
                cat = t(f"cat_{cat_id}")
                desc = t(desc_key)
                configs.append(ConfigItem(
                    id=f"dot_{safe_id}",
                    name=f"{dotfile} ({desc})",
                    category=cat,
                    source_path=str(dot_path),
                    rel_path=dotfile,
                    size_bytes=size,
                    item_count=count,
                    is_directory=dot_path.is_dir(),
                    is_sensitive=is_sens,
                    is_selected=not is_sens,
                    category_id=cat_id
                ))

        # 3. Güvenlik, Anahtarlıklar, Bulut ve DevOps Kimlikleri (Hassas Veriler)
        sensitive_items = [
            (self.home_dir / ".ssh", ".ssh", "sec_ssh", "sec_desc_ssh", "security_keys", False),
            (self.home_dir / ".gnupg", ".gnupg", "sec_gnupg", "sec_desc_gnupg", "security_keys", False),
            (self.home_dir / ".password-store", ".password-store", "sec_pass", "sec_desc_pass", "security_keys", False),
            (self.home_dir / ".local/share/keyrings", ".local/share/keyrings", "sec_keyrings", "sec_desc_keyrings", "security_keys", False),
            (self.home_dir / ".local/share/kwalletd", ".local/share/kwalletd", "sec_kwallet", "sec_desc_kwallet", "security_keys", False),
            (self.home_dir / ".pki", ".pki", "sec_pki", "sec_desc_pki", "security_keys", False),
            (self.home_dir / ".cert", ".cert", "sec_cert", "sec_desc_cert", "security_keys", False),
            (self.home_dir / ".kube", ".kube", "sec_kube", "sec_desc_kube", "dev_cloud", False),
            (self.home_dir / ".aws", ".aws", "sec_aws", "sec_desc_aws", "dev_cloud", False),
            (self.home_dir / ".docker/config.json", ".docker/config.json", "sec_docker", "sec_desc_docker", "dev_cloud", False),
            (self.home_dir / ".pypirc", ".pypirc", "sec_pypirc", "sec_desc_pypirc", "dev_cloud", False),
            (self.home_dir / ".local/share/DBeaverData", ".local/share/DBeaverData", "sec_dbeaver", "sec_desc_dbeaver", "dev_cloud", False),
            (self.home_dir / ".local/share/fish/fish_history", ".local/share/fish/fish_history", "sec_fish_history", "sec_desc_fish_history", "shell_history", False),
            (self.home_dir / ".local/share/atuin", ".local/share/atuin", "sec_atuin", "sec_desc_atuin", "shell_history", False),
        ]
        for s_path, rel_p, item_id, desc_key, cat_id, default_sel in sensitive_items:
            if s_path.exists():
                size, count = get_dir_size_and_count(s_path)
                desc = t(desc_key)
                configs.append(ConfigItem(
                    id=item_id,
                    name=f"~/{rel_p} ({desc})",
                    category=t(f"cat_{cat_id}"),
                    source_path=str(s_path),
                    rel_path=rel_p,
                    size_bytes=size,
                    item_count=count,
                    is_directory=s_path.is_dir(),
                    is_sensitive=True,
                    is_selected=default_sel,
                    category_id=cat_id
                ))

        # 4. Yazı Tipleri, Temalar, Masaüstü Ortamı (GNOME/KDE) ve Kabuk Verileri
        desktop_and_theme_paths = [
            (self.home_dir / ".local/share/fonts", ".local/share/fonts", "theme_desc_fonts_share", "appearance_themes", True),
            (self.home_dir / ".fonts", ".fonts", "theme_desc_fonts_home", "appearance_themes", True),
            (self.home_dir / ".local/share/themes", ".local/share/themes", "theme_desc_themes_share", "appearance_themes", True),
            (self.home_dir / ".themes", ".themes", "theme_desc_themes_home", "appearance_themes", True),
            (self.home_dir / ".local/share/icons", ".local/share/icons", "theme_desc_icons_share", "appearance_themes", True),
            (self.home_dir / ".icons", ".icons", "theme_desc_icons_home", "appearance_themes", True),
            (self.home_dir / ".local/share/wallpapers", ".local/share/wallpapers", "theme_desc_wallpapers", "appearance_themes", True),
            (self.home_dir / ".local/share/applications", ".local/share/applications", "theme_desc_desktop_entries", "desktop_integration", True),
            (self.home_dir / ".local/share/gnome-shell/extensions", ".local/share/gnome-shell/extensions", "theme_desc_gnome_ext", "desktop_gnome", True),
            (self.home_dir / ".local/share/nautilus/scripts", ".local/share/nautilus/scripts", "theme_desc_nautilus_scripts", "desktop_gnome", True),
            (self.home_dir / ".local/share/konsole", ".local/share/konsole", "theme_desc_konsole", "desktop_kde", True),
            (self.home_dir / ".local/share/plasma", ".local/share/plasma", "theme_desc_plasma", "desktop_kde", True),
            (self.home_dir / ".local/share/color-schemes", ".local/share/color-schemes", "theme_desc_colors", "desktop_kde", True),
            (self.home_dir / ".local/share/kxmlgui5", ".local/share/kxmlgui5", "theme_desc_kxmlgui", "desktop_kde", True),
            (self.home_dir / ".local/share/aurorae", ".local/share/aurorae", "theme_desc_aurorae", "desktop_kde", True),
        ]
        for p, rel, desc_key, cat_id, default_sel in desktop_and_theme_paths:
            if p.exists():
                size, count = get_dir_size_and_count(p)
                desc = t(desc_key)
                configs.append(ConfigItem(
                    id=f"theme_{rel.replace('/', '_').replace('.', '')}",
                    name=f"~/{rel} ({desc})",
                    category=t(f"cat_{cat_id}"),
                    source_path=str(p),
                    rel_path=rel,
                    size_bytes=size,
                    item_count=count,
                    is_directory=p.is_dir(),
                    is_sensitive=False,
                    is_selected=default_sel,
                    category_id=cat_id
                ))

        # 5. Tarayıcı ve E-posta İstemcisi Profilleri (~/.config dışında kalanlar)
        browser_paths = [
            (self.home_dir / ".mozilla/firefox", ".mozilla/firefox", "browser_desc_firefox", True),
            (self.home_dir / ".thunderbird", ".thunderbird", "browser_desc_thunderbird", True),
            (self.home_dir / ".zen", ".zen", "browser_desc_zen", True),
            (self.home_dir / ".librewolf", ".librewolf", "browser_desc_librewolf", True),
            (self.home_dir / ".waterfox", ".waterfox", "browser_desc_waterfox", True),
        ]
        for p, rel, desc_key, default_sel in browser_paths:
            if p.exists():
                size, count = get_dir_size_and_count(p)
                desc = t(desc_key)
                configs.append(ConfigItem(
                    id=f"browser_{rel.replace('/', '_').replace('.', '')}",
                    name=f"~/{rel} ({desc})",
                    category=t("cat_browser_comm"),
                    source_path=str(p),
                    rel_path=rel,
                    size_bytes=size,
                    item_count=count,
                    is_directory=p.is_dir(),
                    is_sensitive=False,
                    is_selected=default_sel,
                    category_id="browser_comm"
                ))

        # 6. Flatpak İzinleri ve Uygulama Verileri (~/.local/share/flatpak/overrides & ~/.var/app/*)
        flatpak_overrides = self.home_dir / ".local/share/flatpak/overrides"
        if flatpak_overrides.exists():
            size, count = get_dir_size_and_count(flatpak_overrides)
            configs.append(ConfigItem(
                id="flatpak_overrides",
                name=f"~/.local/share/flatpak/overrides ({t('flatpak_desc_overrides')})",
                category=t("cat_flatpak_data"),
                source_path=str(flatpak_overrides),
                rel_path=".local/share/flatpak/overrides",
                size_bytes=size,
                item_count=count,
                is_directory=True,
                is_sensitive=False,
                is_selected=True,
                category_id="flatpak_data"
            ))

        flatpak_var_app = self.home_dir / ".var/app"
        if flatpak_var_app.exists() and flatpak_var_app.is_dir():
            try:
                for entry in sorted(os.scandir(flatpak_var_app), key=lambda e: e.name.lower()):
                    if entry.is_dir(follow_symlinks=False):
                        entry_path = Path(entry.path)
                        size, count = get_dir_size_and_count(entry_path)
                        configs.append(ConfigItem(
                            id=f"flatpak_app_{entry.name}",
                            name=f"{entry.name} ({t('flatpak_desc_app_data')})",
                            category=t("cat_flatpak_app_data"),
                            source_path=str(entry_path),
                            rel_path=f".var/app/{entry.name}",
                            size_bytes=size,
                            item_count=count,
                            is_directory=True,
                            is_sensitive=False,
                            is_selected=True,
                            category_id="flatpak_app_data"
                        ))
            except OSError:
                pass

        # 7. Linux Oyun Verileri ve Özel Proton-GE Sürümleri
        gaming_paths = [
            (self.home_dir / ".local/share/lutris", ".local/share/lutris", "gaming_desc_lutris", True),
            (self.home_dir / ".local/share/Steam/compatibilitytools.d", ".local/share/Steam/compatibilitytools.d", "gaming_desc_steam_compat", False),
            (self.home_dir / ".steam/root/compatibilitytools.d", ".steam/root/compatibilitytools.d", "gaming_desc_steam_root_compat", False),
        ]
        seen_real_gaming = set()
        for p, rel, desc_key, default_sel in gaming_paths:
            if p.exists():
                try:
                    real_p = str(p.resolve())
                except Exception:
                    real_p = str(p)
                if real_p in seen_real_gaming:
                    continue
                seen_real_gaming.add(real_p)
                size, count = get_dir_size_and_count(p)
                desc = t(desc_key)
                configs.append(ConfigItem(
                    id=f"gaming_{rel.replace('/', '_').replace('.', '')}",
                    name=f"~/{rel} ({desc})",
                    category=t("cat_gaming"),
                    source_path=str(p),
                    rel_path=rel,
                    size_bytes=size,
                    item_count=count,
                    is_directory=p.is_dir(),
                    is_sensitive=False,
                    is_selected=default_sel,
                    category_id="gaming"
                ))

        return configs

    def scan_custom_paths(self) -> List[CustomPathItem]:
        """Kullanıcının ana dizinindeki standart klasörleri ve önerileri listeler."""
        candidates = [
            (self.home_dir / "Documents", "custom_lbl_documents", True),
            (self.home_dir / "Belgeler", "custom_lbl_documents_tr", True),
            (self.home_dir / "Pictures", "custom_lbl_pictures", True),
            (self.home_dir / "Resimler", "custom_lbl_pictures_tr", True),
            (self.home_dir / "Projects", "custom_lbl_projects", True),
            (self.home_dir / "src", "custom_lbl_src", True),
            (self.home_dir / "code", "custom_lbl_code", True),
            (self.home_dir / "Applications", "custom_lbl_applications", True),
            (self.home_dir / "AppImages", "custom_lbl_appimages", True),
            (self.home_dir / "Games", "custom_lbl_games", False),
            (self.home_dir / "Music", "custom_lbl_music", False),
            (self.home_dir / "Müzik", "custom_lbl_music_tr", False),
            (self.home_dir / "Videos", "custom_lbl_videos", False),
            (self.home_dir / "Videolar", "custom_lbl_videos_tr", False),
            (self.home_dir / "Desktop", "custom_lbl_desktop", True),
            (self.home_dir / "Masaüstü", "custom_lbl_desktop", True),
            (self.home_dir / ".local/bin", "custom_lbl_local_bin", True),
            (self.home_dir / "bin", "custom_lbl_bin", True),
            (self.home_dir / "Downloads", "custom_lbl_downloads", False),
        ]

        results: List[CustomPathItem] = []
        seen_paths = set()

        for p, label_key, default_selected in candidates:
            str_path = str(p)
            if str_path in seen_paths:
                continue

            if p.exists():
                seen_paths.add(str_path)
                size, count = get_dir_size_and_count(p)
                results.append(CustomPathItem(
                    path=str_path,
                    label=t(label_key),
                    exists=True,
                    is_dir=p.is_dir(),
                    size_bytes=size,
                    file_count=count,
                    is_selected=default_selected,
                    label_key=label_key
                ))

        return results
