"""
DistroNomad - Geri Yüklemede Donanım Sürücüsü Kurulum Seçeneği Testleri.
"""

import json
import pytest
import tarfile
from pathlib import Path
from distronomad.models import (
    SystemInfo,
    RestoreOptions,
    PackageItem,
    ConfigItem,
)
from distronomad.discovery import SystemScanner
from distronomad.hardware import HardwareProfile, is_hardware_profile_different
from distronomad.restore import RestoreEngine
from distronomad.ui.app import DistroNomadApp
from distronomad.cli import build_parser, cmd_restore
from distronomad.i18n import t, set_current_language, get_current_language
from textual.widgets import Checkbox, Label, RichLog


@pytest.fixture(autouse=True)
def arch_target_system(monkeypatch):
    """Sürücü önerileri hedef dağıtım ailesine göre seçilir; testlerin CI'daki Ubuntu gibi farklı
    dağıtımlarda da aynı sonucu vermesi için hedef sistem Arch (pacman) olarak sabitlenir."""
    def as_arch(info: SystemInfo) -> SystemInfo:
        info.distro_id = "arch"
        info.distro_name = "Arch Linux"
        info.distro_like = ""
        info.available_package_managers = ["pacman"]
        return info

    orig_scan = SystemScanner.scan_system_info
    orig_current = SystemInfo.current
    monkeypatch.setattr(SystemScanner, "scan_system_info", lambda self: as_arch(orig_scan(self)))
    monkeypatch.setattr(SystemInfo, "current", classmethod(lambda cls: as_arch(orig_current())))


def _make_dummy_backup(dest_path: Path, hardware_info=None, packages=None) -> Path:
    """Testler için geçerli bir minimal distronomad arşiv dosyası oluşturur."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "version": "1.0.0",
        "created_at": "2026-10-29T12:00:00",
        "system_info": {
            "distro_name": "Arch Linux",
            "distro_id": "arch",
            "distro_like": [],
            "kernel_version": "6.12.0",
            "desktop_environment": "KDE",
            "shell": "/bin/bash",
            "home_dir": str(dest_path.parent / "mock_home"),
            "available_package_managers": ["pacman"]
        },
        "packages": packages or [],
        "configs": [
            {
                "id": "cfg_bashrc",
                "name": "bashrc",
                "category": "Kabuk",
                "source_path": "/dummy/.bashrc",
                "rel_path": ".bashrc",
                "is_selected": True,
                "size_bytes": 100
            }
        ],
        "custom_paths": [],
        "archive_format": "tar.gz",
        "total_bytes": 100,
        "file_hashes": {},
        "backup_type": "full",
        "hardware_info": hardware_info or {}
    }

    dummy_bashrc = dest_path.parent / "temp_bashrc"
    dummy_bashrc.write_text("alias l='ls'\n", encoding="utf-8")

    manifest_json = dest_path.parent / "manifest.json"
    manifest_json.write_text(json.dumps(manifest_data), encoding="utf-8")

    with tarfile.open(dest_path, "w:gz") as tar:
        tar.add(manifest_json, arcname="manifest.json")
        tar.add(dummy_bashrc, arcname="configs/.bashrc")

    manifest_json.unlink(missing_ok=True)
    dummy_bashrc.unlink(missing_ok=True)
    return dest_path


def test_is_hardware_profile_different_helper():
    """is_hardware_profile_different fonksiyonunun eşleşme ve fark analizini test eder."""
    live = HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen 7 7800X3D",
        gpus=["NVIDIA GeForce RTX 4080"],
        gpu_vendor="nvidia",
        recommended_packages={"arch": ["nvidia-dkms"]}
    )

    # 1. Boş manifest donanım bilgisi
    is_diff, desc = is_hardware_profile_different(live, {})
    assert is_diff is False
    assert desc == ""

    # 2. Aynı donanım
    same_manifest = {
        "cpu_vendor": "AMD",
        "cpu_model": "AMD Ryzen 7 7800X3D",
        "gpus": ["NVIDIA GeForce RTX 4080"],
        "gpu_vendor": "nvidia"
    }
    is_diff, desc = is_hardware_profile_different(live, same_manifest)
    assert is_diff is False

    # 3. Farklı GPU
    diff_gpu_manifest = {
        "cpu_vendor": "AMD",
        "cpu_model": "AMD Ryzen 7 7800X3D",
        "gpus": ["AMD Radeon RX 7900 XTX"],
        "gpu_vendor": "amd"
    }
    is_diff, desc = is_hardware_profile_different(live, diff_gpu_manifest)
    assert is_diff is True
    assert "GPU" in desc

    # 4. Farklı CPU
    diff_cpu_manifest = {
        "cpu_vendor": "Intel",
        "cpu_model": "Intel Core i7-13700K",
        "gpus": ["NVIDIA GeForce RTX 4080"],
        "gpu_vendor": "nvidia"
    }
    is_diff, desc = is_hardware_profile_different(live, diff_cpu_manifest)
    assert is_diff is True
    assert "CPU" in desc


@pytest.mark.asyncio
async def test_ui_restore_hardware_checkbox_default_unchecked_and_enabled(tmp_path, monkeypatch):
    """TUI Geri Yükle sekmesinde onay kutusunun varsayılan olarak işaretsiz olduğunu ve öneri varsa etkin olduğunu doğrular."""
    live_profile = HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen 5 5600X",
        gpus=["NVIDIA GeForce RTX 3060"],
        gpu_vendor="nvidia",
        recommended_packages={"arch": ["nvidia-dkms", "nvidia-utils"]}
    )
    monkeypatch.setattr("distronomad.ui.app.scan_hardware_profile", lambda: live_profile)
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: live_profile)

    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()

        chk = app.query_one("#chk-restore-drivers", Checkbox)
        lbl_pkgs = app.query_one("#lbl-restore-drivers-pkgs", Label)

        # Varsayılan olarak İŞARETSİZ olmalı
        assert chk.value is False
        # Öneri bulunduğu için kutu etkin olmalı (disabled = False)
        assert chk.disabled is False
        # Önerilen paketler etiket veya kutuda listelenmeli
        assert "nvidia-dkms" in str(lbl_pkgs.render())


@pytest.mark.asyncio
async def test_ui_restore_hardware_checkbox_disabled_when_no_recommendations(tmp_path, monkeypatch):
    """Önerilen sürücü paketi bulunmadığında onay kutusunun devre dışı olduğunu doğrular."""
    live_no_recs = HardwareProfile(
        cpu_vendor="Intel",
        cpu_model="Intel Core i3-8100",
        gpus=["Intel HD Graphics 630"],
        gpu_vendor="intel",
        recommended_packages={"arch": [], "debian": [], "fedora": [], "suse": []}
    )
    monkeypatch.setattr("distronomad.ui.app.scan_hardware_profile", lambda: live_no_recs)
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: live_no_recs)

    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()

        chk = app.query_one("#chk-restore-drivers", Checkbox)
        lbl_pkgs = app.query_one("#lbl-restore-drivers-pkgs", Label)

        # Öneri yoksa kutu devre dışı olmalı ve işaretlenememeli
        assert chk.disabled is True
        assert chk.value is False
        assert "Özel sürücü gerekmiyor" in str(lbl_pkgs.render())


@pytest.mark.asyncio
async def test_recommendations_come_from_live_scan_not_manifest(tmp_path, monkeypatch):
    """Önerilerin manifestteki eski donanıma göre değil, canlı donanım taramasına göre yapıldığını ve profil farkının gösterildiğini doğrular."""
    # Manifestte Intel UHD / i7 var
    manifest_hw = {
        "cpu_vendor": "Intel",
        "cpu_model": "Intel Core i7-10700K",
        "gpus": ["Intel UHD Graphics 630"],
        "gpu_vendor": "intel",
        "recommended_packages": {"arch": ["vulkan-intel", "intel-media-driver"]}
    }
    backup_file = _make_dummy_backup(tmp_path / "intel_backup.tar.gz", hardware_info=manifest_hw)

    # Canlı makinede AMD / NVIDIA RTX 4070 var
    live_profile = HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen 7 7700X",
        gpus=["NVIDIA GeForce RTX 4070"],
        gpu_vendor="nvidia",
        recommended_packages={"arch": ["nvidia-dkms", "nvidia-utils"]}
    )
    monkeypatch.setattr("distronomad.ui.app.scan_hardware_profile", lambda: live_profile)
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: live_profile)

    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()

        # Yedeği incele
        app._inspect_restore_with_password(backup_file, None)
        await pilot.pause()

        chk = app.query_one("#chk-restore-drivers", Checkbox)
        lbl_pkgs = app.query_one("#lbl-restore-drivers-pkgs", Label)
        hw_text = app.query_one("#hw-rec-text", Label)

        # Önerilen paketler manifestteki (vulkan-intel) değil, canlı taramadaki (nvidia-dkms) olmalı!
        assert "nvidia-dkms" in str(lbl_pkgs.render())
        assert "vulkan-intel" not in str(lbl_pkgs.render())

        # Yedekteki profil farklıysa bu fark bilgi olarak gösterilmeli
        rendered_hw = str(hw_text.render())
        assert "Yedekteki donanım profili" in rendered_hw
        assert "Intel UHD Graphics 630" in rendered_hw or "Intel Core i7" in rendered_hw


@pytest.mark.asyncio
async def test_ui_restore_execution_with_drivers_checked_adds_to_dry_run(tmp_path, monkeypatch):
    """Kutu işaretlendiğinde sürücü paketlerinin dry-run kurulum adımına eklendiğini doğrular."""
    backup_file = _make_dummy_backup(tmp_path / "test_backup.tar.gz")

    live_profile = HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen 9",
        gpus=["NVIDIA GeForce RTX 4090"],
        gpu_vendor="nvidia",
        recommended_packages={"arch": ["nvidia-dkms", "nvidia-utils"]}
    )
    monkeypatch.setattr("distronomad.ui.app.scan_hardware_profile", lambda: live_profile)
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: live_profile)

    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()

        from textual.widgets import Input
        app.query_one("#input-restore-src", Input).value = str(backup_file)

        # Yedeği yükle
        app._inspect_restore_with_password(backup_file, None)
        await pilot.pause()

        # Sürücü kutusunu ve dry-run'ı işaretle
        chk_drivers = app.query_one("#chk-restore-drivers", Checkbox)
        chk_dry = app.query_one("#chk-dry-run", Checkbox)
        chk_drivers.value = True
        chk_dry.value = True

        finished_results = []
        orig_finished = app._restore_finished
        def fake_finished(res):
            finished_results.append(res)
            orig_finished(res)
        app._restore_finished = fake_finished

        app._handle_start_restore()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(finished_results) == 1
        res = finished_results[0]
        installed_str = " ".join(res.get("installed_packages", []))
        assert "nvidia-dkms" in installed_str
        assert "nvidia-utils" in installed_str


def test_restore_engine_install_drivers_dry_run(tmp_path, monkeypatch):
    """RestoreEngine doğrudan çalıştırıldığında install_drivers=True seçeneğinin dry-run kurulumuna sürücü paketlerini eklediğini doğrular."""
    backup_file = _make_dummy_backup(tmp_path / "engine_backup.tar.gz")

    live_profile = HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen 7",
        gpus=["NVIDIA RTX 3050"],
        gpu_vendor="nvidia",
        recommended_packages={"arch": ["nvidia-dkms", "nvidia-utils"]}
    )
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: live_profile)

    engine = RestoreEngine(backup_path=backup_file)
    engine.load_manifest()

    target_sys = SystemInfo(
        distro_name="Arch Linux",
        distro_id="arch",
        available_package_managers=["pacman"],
        home_dir=str(tmp_path)
    )

    # 1. install_drivers = False iken sürücü kurulmamalı
    opts_no_drivers = RestoreOptions(
        target_home=str(tmp_path),
        selected_packages=[],
        selected_configs=[],
        install_packages=False,
        dry_run=True,
        install_drivers=False
    )
    res_no = engine.execute_restore(opts_no_drivers, target_system_info=target_sys)
    assert not any("nvidia-dkms" in str(p) for p in res_no["installed_packages"])

    # 2. install_drivers = True iken sürücü paketleri dry-run kurulumuna eklenmeli
    opts_with_drivers = RestoreOptions(
        target_home=str(tmp_path),
        selected_packages=[],
        selected_configs=[],
        install_packages=False,
        dry_run=True,
        install_drivers=True
    )
    res_with = engine.execute_restore(opts_with_drivers, target_system_info=target_sys)
    installed_str = " ".join(res_with["installed_packages"])
    assert "[DRY-RUN]" in installed_str
    assert "nvidia-dkms" in installed_str
    assert "nvidia-utils" in installed_str


def test_cli_restore_install_drivers_flag(tmp_path, monkeypatch, capsys):
    """CLI restore komutunda --install-drivers bayrağının parse edildiğini ve çalıştırıldığını doğrular."""
    parser = build_parser()

    # 1. Bayrak verilmediğinde varsayılan False
    args_default = parser.parse_args(["restore", "--src", str(tmp_path / "backup.tar.gz")])
    assert args_default.install_drivers is False

    # 2. --install-drivers bayrağı verildiğinde True
    args_flag = parser.parse_args(["restore", "--src", str(tmp_path / "backup.tar.gz"), "--install-drivers"])
    assert args_flag.install_drivers is True

    # 3. cmd_restore çağrısı ile dry-run testi
    backup_file = _make_dummy_backup(tmp_path / "cli_backup.tar.gz")
    live_profile = HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen",
        gpus=["NVIDIA RTX 4060"],
        gpu_vendor="nvidia",
        recommended_packages={"arch": ["nvidia-dkms"]}
    )
    monkeypatch.setattr("distronomad.cli.scan_hardware_profile", lambda: live_profile)
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: live_profile)

    args_run = parser.parse_args([
        "restore",
        "--src", str(backup_file),
        "--target-home", str(tmp_path),
        "--dry-run",
        "--install-drivers"
    ])

    cmd_restore(args_run)
    captured = capsys.readouterr()
    assert "nvidia-dkms" in captured.out
    assert "Planlanan/Kurulan Paketler: 1" in captured.out


def test_i18n_hardware_driver_strings():
    """Tüm yeni donanım sürücüsü metinlerinin hem TR hem EN çevirilerinin eksiksiz olduğunu doğrular."""
    keys = [
        "chk_install_drivers_lbl",
        "hw_rec_none",
        "hw_manifest_diff_notice",
        "hw_from_backup",
        "hw_rec_cpu",
        "hw_rec_gpu",
        "hw_rec_audio",
        "hw_rec_target_distro",
    ]

    for key in keys:
        tr_val = t(key, lang="tr", distro="Arch", family="ARCH")
        en_val = t(key, lang="en", distro="Arch", family="ARCH")
        assert tr_val, f"Eksik TR çeviri: {key}"
        assert en_val, f"Eksik EN çeviri: {key}"
        assert tr_val != en_val, f"TR ve EN metinleri aynı kalmış: {key}"
