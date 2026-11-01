"""
DistroNomad - Veri Modelleri Birim Testleri.
"""

import pytest
import json
from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    CustomPathItem,
    BackupManifest,
    RestoreOptions,
    format_bytes,
)


def test_system_info():
    info = SystemInfo(
        distro_id="cachyos",
        distro_name="CachyOS",
        kernel_version="6.12.0",
        available_package_managers=["pacman", "flatpak"]
    )
    assert info.distro_id == "cachyos"
    assert "pacman" in info.available_package_managers


def test_package_item():
    pkg = PackageItem(
        name="neovim",
        manager="pacman",
        version="0.10.0",
        category="Düzenleyici",
        app_id="neovim"
    )
    assert pkg.display_name == "neovim"
    assert pkg.is_selected is True

    pkg_flatpak = PackageItem(
        name="VLC",
        manager="flatpak",
        version="3.0.20",
        app_id="org.videolan.VLC"
    )
    assert "org.videolan.VLC" in pkg_flatpak.display_name


def test_config_item_formatted_size():
    cfg = ConfigItem(
        id="cfg_nvim",
        name="Neovim",
        category="Düzenleyici",
        source_path="/home/user/.config/nvim",
        rel_path=".config/nvim",
        size_bytes=1048576, # 1 MB
        item_count=12
    )
    assert "1.0 MB" in cfg.formatted_size


def test_format_bytes_helper():
    """format_bytes fonksiyonunun farklı boyutlarda doğru metin ürettiğini test eder."""
    assert format_bytes(0) == "0.0 B"
    assert format_bytes(500) == "500.0 B"
    assert format_bytes(20480) == "20.0 KB"
    assert format_bytes(1048576) == "1.0 MB"
    assert format_bytes(1073741824) == "1.0 GB"
    assert format_bytes(1099511627776) == "1.0 TB"


def test_manifest_serialization():
    sys_info = SystemInfo(distro_id="arch", distro_name="Arch Linux")
    pkg = PackageItem(name="git", manager="pacman")
    cfg = ConfigItem(
        id="dot_bashrc",
        name=".bashrc",
        category="Kabuk",
        source_path="/home/user/.bashrc",
        rel_path=".bashrc",
        size_bytes=500
    )
    custom = CustomPathItem(path="/home/user/Belgeler", label="Belgeler", size_bytes=2048)

    manifest = BackupManifest(
        created_at="2026-10-13T12:00:00",
        version="1.0.0",
        system_info=sys_info,
        packages=[pkg],
        configs=[cfg],
        custom_paths=[custom],
        archive_format="tar.zst"
    )

    data = manifest.to_dict()
    assert data["version"] == "1.0.0"
    assert len(data["packages"]) == 1
    assert data["packages"][0]["name"] == "git"

    # Geri yükleme testi
    manifest_loaded = BackupManifest.from_dict(data)
    assert manifest_loaded.system_info.distro_name == "Arch Linux"
    assert len(manifest_loaded.configs) == 1
    assert manifest_loaded.configs[0].rel_path == ".bashrc"
