"""
DistroNomad - Paket Haritalama ve Çapraz Dağıtım Çeviri Testleri.
"""

import json
from pathlib import Path
import pytest
from distronomad.packagemap import (
    COMMON_PACKAGE_MAP,
    FLATPAK_EQUIVALENTS,
    detect_distro_family,
    translate_package,
    get_flatpak_id,
    get_install_command,
    load_package_mappings,
    get_user_mappings_path,
)

ORIGINAL_PACKAGE_MAP = {
    "fd": {"arch": "fd", "debian": "fd-find", "fedora": "fd-find", "suse": "fd", "alpine": "fd"},
    "bat": {"arch": "bat", "debian": "batcat", "fedora": "bat", "suse": "bat", "alpine": "bat"},
    "build-essential": {"arch": "base-devel", "debian": "build-essential", "fedora": "@development-tools", "suse": "patterns-devel-base-devel_basis", "alpine": "build-base"},
    "base-devel": {"arch": "base-devel", "debian": "build-essential", "fedora": "@development-tools", "suse": "patterns-devel-base-devel_basis", "alpine": "build-base"},
    "python-pip": {"arch": "python-pip", "debian": "python3-pip", "fedora": "python3-pip", "suse": "python3-pip", "alpine": "py3-pip"},
    "python3-pip": {"arch": "python-pip", "debian": "python3-pip", "fedora": "python3-pip", "suse": "python3-pip", "alpine": "py3-pip"},
    "docker": {"arch": "docker", "debian": "docker.io", "fedora": "moby-engine", "suse": "docker", "alpine": "docker"},
    "docker-compose": {"arch": "docker-compose", "debian": "docker-compose-v2", "fedora": "docker-compose", "suse": "docker-compose", "alpine": "docker-cli-compose"},
    "neovim": {"arch": "neovim", "debian": "neovim", "fedora": "neovim", "suse": "neovim", "alpine": "neovim"},
    "ripgrep": {"arch": "ripgrep", "debian": "ripgrep", "fedora": "ripgrep", "suse": "ripgrep", "alpine": "ripgrep"},
    "btop": {"arch": "btop", "debian": "btop", "fedora": "btop", "suse": "btop", "alpine": "btop"},
    "htop": {"arch": "htop", "debian": "htop", "fedora": "htop", "suse": "htop", "alpine": "htop"},
    "alacritty": {"arch": "alacritty", "debian": "alacritty", "fedora": "alacritty", "suse": "alacritty", "alpine": "alacritty"},
    "kitty": {"arch": "kitty", "debian": "kitty", "fedora": "kitty", "suse": "kitty", "alpine": "kitty"},
    "zsh": {"arch": "zsh", "debian": "zsh", "fedora": "zsh", "suse": "zsh", "alpine": "zsh"},
    "fish": {"arch": "fish", "debian": "fish", "fedora": "fish", "suse": "fish", "alpine": "fish"},
    "tmux": {"arch": "tmux", "debian": "tmux", "fedora": "tmux", "suse": "tmux", "alpine": "tmux"},
    "git": {"arch": "git", "debian": "git", "fedora": "git", "suse": "git", "alpine": "git"},
    "curl": {"arch": "curl", "debian": "curl", "fedora": "curl", "suse": "curl", "alpine": "curl"},
    "wget": {"arch": "wget", "debian": "wget", "fedora": "wget", "suse": "wget", "alpine": "wget"},
    "jq": {"arch": "jq", "debian": "jq", "fedora": "jq", "suse": "jq", "alpine": "jq"},
    "fzf": {"arch": "fzf", "debian": "fzf", "fedora": "fzf", "suse": "fzf", "alpine": "fzf"},
    "eza": {"arch": "eza", "debian": "eza", "fedora": "eza", "suse": "eza", "alpine": "eza"},
    "fastfetch": {"arch": "fastfetch", "debian": "fastfetch", "fedora": "fastfetch", "suse": "fastfetch", "alpine": "fastfetch"}
}

ORIGINAL_FLATPAK_EQUIVALENTS = {
    "firefox": "org.mozilla.firefox",
    "chromium": "org.chromium.Chromium",
    "google-chrome": "com.google.Chrome",
    "spotify": "com.spotify.Client",
    "spotify-launcher": "com.spotify.Client",
    "discord": "com.discordapp.Discord",
    "vlc": "org.videolan.VLC",
    "gimp": "org.gimp.GIMP",
    "inkscape": "org.inkscape.Inkscape",
    "blender": "org.blender.Blender",
    "obs": "com.obsproject.Studio",
    "obs-studio": "com.obsproject.Studio",
    "code": "com.visualstudio.code",
    "vscode": "com.visualstudio.code",
    "visual-studio-code": "com.visualstudio.code",
    "vscodium": "com.vscodium.codium",
    "telegram": "org.telegram.desktop",
    "telegram-desktop": "org.telegram.desktop",
    "steam": "com.valvesoftware.Steam",
    "libreoffice": "org.libreoffice.LibreOffice",
    "libreoffice-fresh": "org.libreoffice.LibreOffice",
    "libreoffice-still": "org.libreoffice.LibreOffice",
    "audacity": "org.audacityteam.Audacity",
    "thunderbird": "org.mozilla.Thunderbird",
    "kdenlive": "org.kde.kdenlive",
    "qbittorrent": "org.qbittorrent.qBittorrent",
    "transmission": "com.transmissionbt.Transmission",
    "mpv": "io.mpv.Mpv",
    "slack": "com.slack.Slack",
    "signal-desktop": "org.signal.Signal",
    "calibre": "com.calibre_ebook.calibre"
}


def test_detect_distro_family():
    assert detect_distro_family("arch") == "arch"
    assert detect_distro_family("cachyos", "arch") == "arch"
    assert detect_distro_family("manjaro") == "arch"

    assert detect_distro_family("ubuntu", "debian") == "debian"
    assert detect_distro_family("pop", "ubuntu") == "debian"
    assert detect_distro_family("linuxmint") == "debian"

    assert detect_distro_family("fedora") == "fedora"
    assert detect_distro_family("rhel", "fedora") == "fedora"

    assert detect_distro_family("opensuse-tumbleweed") == "suse"
    assert detect_distro_family("alpine") == "alpine"


def test_translate_package_arch_to_debian():
    # fd -> fd-find
    assert translate_package("fd", "arch", "debian") == "fd-find"
    # bat -> batcat
    assert translate_package("bat", "arch", "debian") == "batcat"
    # base-devel -> build-essential
    assert translate_package("base-devel", "arch", "debian") == "build-essential"
    # docker -> docker.io
    assert translate_package("docker", "arch", "debian") == "docker.io"
    # aynı kalanlar
    assert translate_package("neovim", "arch", "debian") == "neovim"
    assert translate_package("git", "arch", "debian") == "git"


def test_translate_package_debian_to_arch():
    assert translate_package("fd-find", "debian", "arch") == "fd"
    assert translate_package("batcat", "debian", "arch") == "bat"
    assert translate_package("build-essential", "debian", "arch") == "base-devel"


def test_get_flatpak_id():
    assert get_flatpak_id("firefox") == "org.mozilla.firefox"
    assert get_flatpak_id("spotify") == "com.spotify.Client"
    assert get_flatpak_id("discord") == "com.discordapp.Discord"
    assert get_flatpak_id("vlc") == "org.videolan.VLC"
    assert get_flatpak_id("bilinmeyen-uygulama") is None


def test_get_install_command():
    pacman_cmd, _ = get_install_command("pacman", ["git", "neovim"])
    assert "pacman" in pacman_cmd
    assert "--needed" in pacman_cmd
    assert "git" in pacman_cmd

    apt_cmd, _ = get_install_command("apt", ["git", "neovim"])
    assert "apt-get" in apt_cmd
    assert "install" in apt_cmd

    flatpak_cmd, _ = get_install_command("flatpak", ["org.videolan.VLC"])
    assert "flatpak" in flatpak_cmd
    assert "flathub" in flatpak_cmd


def test_all_mappings_match_frozen_original(monkeypatch, tmp_path):
    """Taşımadan önceki tüm eşlemelerin birebir aynı sonucu verdiğini doğrular."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    load_package_mappings(reload=True)

    assert COMMON_PACKAGE_MAP == ORIGINAL_PACKAGE_MAP
    assert FLATPAK_EQUIVALENTS == ORIGINAL_FLATPAK_EQUIVALENTS

    families = ["arch", "debian", "fedora", "suse", "alpine"]
    for canonical, fmap in ORIGINAL_PACKAGE_MAP.items():
        for src_fam in families:
            for dst_fam in families:
                src_pkg = fmap.get(src_fam, canonical)
                expected = fmap.get(dst_fam, src_pkg)
                assert translate_package(src_pkg, src_fam, dst_fam) == expected

    for app_name, flatpak_id in ORIGINAL_FLATPAK_EQUIVALENTS.items():
        assert get_flatpak_id(app_name) == flatpak_id


def test_user_mappings_override_and_extend(monkeypatch, tmp_path):
    """Kullanıcı tanımlı kuralların varsayılanları ezdiğini ve yeni eşleme eklediğini doğrular."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    config_dir = xdg / "distronomad"
    config_dir.mkdir(parents=True)
    mapping_file = config_dir / "mappings.json"

    user_rules = {
        "packages": {
            "bat": {
                "debian": "my-custom-batcat"
            },
            "mytool": {
                "arch": "mytool-git",
                "debian": "mytool-bin",
                "fedora": "mytool"
            }
        },
        "flatpak": {
            "firefox": "org.mozilla.FirefoxNightly",
            "mycustomapp": "com.example.MyCustomApp"
        }
    }
    mapping_file.write_text(json.dumps(user_rules), encoding="utf-8")

    # Kullanıcı kuralı debian karşılığını ezdi
    assert translate_package("bat", "arch", "debian") == "my-custom-batcat"
    # Ezilmeyen diğer dağıtım aileleri korundu
    assert translate_package("bat", "arch", "fedora") == "bat"

    # Yeni paket eşlemesi
    assert translate_package("mytool-git", "arch", "debian") == "mytool-bin"
    assert translate_package("mytool", "arch", "fedora") == "mytool"

    # Flatpak override ve yeni ekleme
    assert get_flatpak_id("firefox") == "org.mozilla.FirefoxNightly"
    assert get_flatpak_id("mycustomapp") == "com.example.MyCustomApp"
    # Değişmeyen varsayılan flatpak korundu
    assert get_flatpak_id("spotify") == "com.spotify.Client"


def test_user_mappings_fallback_to_home_config(monkeypatch, tmp_path):
    """XDG_CONFIG_HOME tanımlı değilken ~/.config/distronomad/mappings.json kullanıldığını doğrular."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    config_dir = home / ".config" / "distronomad"
    config_dir.mkdir(parents=True)
    mapping_file = config_dir / "mappings.json"

    mapping_file.write_text(json.dumps({
        "flatpak": {"testapp": "org.test.TestApp"}
    }), encoding="utf-8")

    assert get_flatpak_id("testapp") == "org.test.TestApp"


def test_corrupted_and_invalid_user_mappings_ignored(monkeypatch, tmp_path):
    """Bozuk JSON ve geçersiz şemalı dosyaların programı çökertmeyip yok sayıldığını doğrular."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    config_dir = xdg / "distronomad"
    config_dir.mkdir(parents=True)
    mapping_file = config_dir / "mappings.json"

    # 1. Sözdizimsel olarak bozuk JSON
    mapping_file.write_text("{ bu geçerli bir json değil :::", encoding="utf-8")
    assert translate_package("bat", "arch", "debian") == "batcat"
    assert get_flatpak_id("firefox") == "org.mozilla.firefox"

    # 2. Kök öğe nesne değil (liste)
    mapping_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert translate_package("bat", "arch", "debian") == "batcat"

    # 3. packages nesne değil (sayı veya metin)
    mapping_file.write_text(json.dumps({"packages": 12345}), encoding="utf-8")
    assert translate_package("bat", "arch", "debian") == "batcat"

    # 4. Paket içi eşleme nesne değil (string)
    mapping_file.write_text(json.dumps({"packages": {"bat": "invalid"}}), encoding="utf-8")
    assert translate_package("bat", "arch", "debian") == "batcat"


def test_packagemap_isolation_does_not_touch_real_home(monkeypatch, tmp_path):
    """Testlerin izole tmp_path kullandığını ve gerçek sistem ev dizinine dokunmadığını doğrular."""
    fake_home = tmp_path / "fake_home"
    fake_xdg = tmp_path / "fake_xdg"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))

    user_path = get_user_mappings_path()
    assert str(fake_xdg) in str(user_path)
    assert not user_path.exists()
