"""
DistroNomad - Denetimli Sürücü Kurulumu Testleri.

Çekirdek modülü kuran sürücüler (NVIDIA, Broadcom wl) kurulunca açık kaynak sürücüyü kapatır;
modül açılacak çekirdek için oluşmazsa bilgisayar siyah ekranla açılır. Bu testler kurulum öncesi
denetimleri (Secure Boot, çekirdek başlıkları, depo), kurulum sonrası doğrulamayı, başarısız
kurulumun geri alınmasını ve CLI/TUI onayını sahte bir sistem üzerinde doğrular.
Hiçbir test gerçek paket yöneticisini, sudo'yu ya da ana makinenin çekirdek dizinlerini kullanmaz.
"""

import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Checkbox, Input, Label, RichLog, TabbedContent

from distronomad import drivers
from distronomad.cli import build_parser, cmd_restore
from distronomad.discovery import SystemScanner
from distronomad.hardware import HardwareProfile
from distronomad.i18n import t
from distronomad.models import PackageItem, RestoreOptions, SystemInfo
from distronomad.restore import RestoreEngine
from distronomad.standalone_script import generate_standalone_restore_script
from distronomad.ui.app import DistroNomadApp
from distronomad.ui.modal import DriverRiskModal


DEBIAN_KERNEL = "6.1.0-18-amd64"
DEBIAN_ADDS = [
    "dkms",
    "glx-alternative-nvidia",
    "linux-headers-6.1.0-18-amd64",
    "linux-headers-amd64",
    "nvidia-driver",
    "nvidia-kernel-dkms",
    "nvidia-kernel-support",
]


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

@pytest.fixture
def host(tmp_path, monkeypatch):
    """Boş sahte sistem: çekirdekleri, Secure Boot durumunu ve modülleri testler kendisi kurar."""
    root = tmp_path / "host"
    modules = root / "modules"
    boot = root / "boot"
    modules.mkdir(parents=True)
    boot.mkdir()
    monkeypatch.setattr(drivers, "MODULES_DIR", modules)
    monkeypatch.setattr(drivers, "BOOT_DIR", boot)
    monkeypatch.setattr(drivers, "EFI_DIR", root / "efi")
    monkeypatch.setattr(drivers, "LOCKDOWN_FILE", root / "lockdown")
    monkeypatch.setattr(drivers, "SIG_ENFORCE_FILE", root / "sig_enforce")
    monkeypatch.setattr(drivers, "running_kernel", lambda: DEBIAN_KERNEL)
    monkeypatch.setattr(drivers, "find_distro_driver_tool", lambda: "")
    monkeypatch.setattr(drivers, "sudo_ready", lambda: True)
    monkeypatch.setattr(drivers, "package_available", lambda pm, name: True)
    return SimpleNamespace(root=root, modules=modules, boot=boot)


def _add_kernel(host, kver, *, boot_vmlinuz=True, module_vmlinuz=False, headers=False, pkgbase=None):
    kdir = host.modules / kver
    kdir.mkdir(parents=True, exist_ok=True)
    if boot_vmlinuz:
        (host.boot / f"vmlinuz-{kver}").write_bytes(b"")
    if module_vmlinuz:
        (kdir / "vmlinuz").write_bytes(b"")
    if headers:
        (kdir / "build").mkdir(exist_ok=True)
    if pkgbase:
        (kdir / "pkgbase").write_text(pkgbase + "\n", encoding="utf-8")
    return kdir


def _add_module(host, kver, rel="updates/dkms/nvidia-current.ko.xz"):
    path = host.modules / kver / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _set_secure_boot(host, enabled: bool):
    efivars = host.root / "efi" / "efivars"
    efivars.mkdir(parents=True, exist_ok=True)
    (efivars / drivers.SECURE_BOOT_VAR_NAME).write_bytes(bytes([0x06, 0, 0, 0, 1 if enabled else 0]))


def _make_backup(dest: Path, distro_id: str = "arch") -> Path:
    """Yalnızca manifest ve bir ayar dosyası içeren en küçük geçerli yedek."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1.0.0",
        "created_at": "2026-11-03T10:00:00",
        "system_info": {
            "distro_name": distro_id.capitalize(),
            "distro_id": distro_id,
            "distro_like": "",
            "kernel_version": "6.0.0",
            "desktop_environment": "KDE",
            "shell": "/bin/bash",
            "home_dir": "/home/kaynak",
            "available_package_managers": ["pacman"] if distro_id == "arch" else ["apt"],
        },
        "packages": [],
        "configs": [{
            "id": "cfg_bashrc",
            "name": "bashrc",
            "category": "Kabuk",
            "source_path": "/home/kaynak/.bashrc",
            "rel_path": ".bashrc",
            "is_selected": True,
            "size_bytes": 10,
        }],
        "custom_paths": [],
        "archive_format": "tar.gz",
        "total_bytes": 10,
        "file_hashes": {},
        "backup_type": "full",
        "hardware_info": {},
    }
    work = dest.parent / f"{dest.name}.work"
    work.mkdir()
    (work / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (work / "bashrc").write_text("alias l='ls'\n", encoding="utf-8")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(work / "manifest.json", arcname="manifest.json")
        tar.add(work / "bashrc", arcname="configs/.bashrc")
    return dest


class FakePackageSystem:
    """Paket yöneticisini ve modül derlemesini (DKMS/akmods) taklit eder, çalıştırılan komutları kaydeder.
    Tanımadığı komutları conftest'in sahte subprocess.run'ına bırakır."""

    def __init__(self, monkeypatch, host, *, installed, adds, trigger="nvidia-driver",
                 build_for=(), build_on="install", module_rel="updates/dkms/nvidia-current.ko.xz",
                 install_rc=0, remove_rc=0):
        self.host = host
        self.installed = set(installed)
        self.adds = list(adds)
        self.trigger = trigger
        self.build_for = list(build_for)
        self.build_on = build_on
        self.module_rel = module_rel
        self.install_rc = install_rc
        self.remove_rc = remove_rc
        self.calls = []
        self._inner = subprocess.run
        monkeypatch.setattr(subprocess, "run", self.run)
        monkeypatch.setattr(drivers, "installed_package_set", lambda pm: set(self.installed))

    def _build(self):
        for kver in self.build_for:
            _add_module(self.host, kver, self.module_rel)

    def run(self, cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)):
            cmd = [str(c) for c in cmd]
            self.calls.append(cmd)
            if cmd[:3] == ["sudo", "-n", "true"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if self.trigger in cmd and "install" in cmd:
                self.installed |= set(self.adds)
                if self.build_on == "install":
                    self._build()
                err = "E: Sub-process /usr/bin/dpkg returned an error code (1)" if self.install_rc else ""
                return subprocess.CompletedProcess(cmd, self.install_rc, "", err)
            if "akmods" in cmd:
                if self.build_on == "akmods":
                    self._build()
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "purge" in cmd or "-Rn" in cmd or "remove" in cmd:
                if self.remove_rc == 0:
                    self.installed -= set(cmd[cmd.index("--") + 1:])
                return subprocess.CompletedProcess(cmd, self.remove_rc, "", "")
        return self._inner(cmd, *args, **kwargs)

    def with_word(self, word):
        return [c for c in self.calls if word in c]


def _pardus_profile():
    return HardwareProfile(
        cpu_vendor="AMD",
        cpu_model="AMD Ryzen 5 5600",
        gpus=["NVIDIA GeForce RTX 3060"],
        gpu_vendor="nvidia",
        recommended_packages={"debian": ["nvidia-driver", "firmware-misc-nonfree"]},
    )


def _run_pardus_restore(tmp_path, monkeypatch, *, dry_run=False, profile=None, selected_packages=(), install_drivers=True):
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: profile or _pardus_profile())
    backup = _make_backup(tmp_path / "pardus_backup.tar.gz")
    engine = RestoreEngine(backup_path=backup, transaction_dir=tmp_path / "tx")
    engine.load_manifest()
    target = SystemInfo(
        distro_name="Pardus",
        distro_id="pardus",
        distro_like="debian",
        available_package_managers=["apt"],
        home_dir=str(tmp_path / "home"),
    )
    options = RestoreOptions(
        target_home=str(tmp_path / "home"),
        selected_packages=list(selected_packages),
        selected_configs=[],
        install_packages=bool(selected_packages),
        install_drivers=install_drivers,
        dry_run=dry_run,
    )
    try:
        return engine.execute_restore(options, target_system_info=target)
    finally:
        engine.cleanup()


def _pardus_host(host):
    """Yeni kurulmuş Pardus: tek çekirdek, başlıkları kurulu değil."""
    _add_kernel(host, DEBIAN_KERNEL, headers=False)


# ---------------------------------------------------------------------------
# Paket sınıflandırma ve hedef sistem
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "nvidia-driver", "nvidia-dkms", "nvidia-utils", "lib32-nvidia-utils", "akmod-nvidia",
    "xorg-x11-drv-nvidia", "nvidia-driver-G06-kmp-default", "linux-cachyos-nvidia-open",
    "broadcom-sta-dkms", "broadcom-wl-dkms", "broadcom-wl",
])
def test_kernel_driver_packages_are_detected(name):
    assert drivers.is_kernel_driver_package(name)


@pytest.mark.parametrize("name", [
    "intel-microcode", "amd-ucode", "mesa-vulkan-drivers", "firmware-misc-nonfree",
    "vulkan-radeon", "v4l2loopback-dkms", "bash",
])
def test_other_packages_are_not_kernel_drivers(name):
    assert not drivers.is_kernel_driver_package(name)


def test_resolve_driver_target_uses_family_and_system_package_manager():
    pardus = SystemInfo(distro_id="pardus", distro_like="debian", available_package_managers=["flatpak", "apt"])
    assert drivers.resolve_driver_target(pardus) == ("debian", "apt")

    cachy = SystemInfo(distro_id="cachyos", distro_like="arch", available_package_managers=["yay", "pacman"])
    assert drivers.resolve_driver_target(cachy) == ("arch", "pacman")

    unknown_dnf = SystemInfo(distro_id="bilinmeyen", available_package_managers=["dnf"])
    assert drivers.resolve_driver_target(unknown_dnf) == ("fedora", "dnf")

    # Aile belirlenemezse eskiden olduğu gibi Arch varsayılmaz
    unknown = SystemInfo(distro_id="bilinmeyen", available_package_managers=["flatpak"])
    assert drivers.resolve_driver_target(unknown) == ("unknown", "")


# ---------------------------------------------------------------------------
# Çekirdekler ve başlık paketleri
# ---------------------------------------------------------------------------

def test_kernels_to_cover_running_and_newest_installed(host):
    _add_kernel(host, "6.1.0-9-amd64")
    _add_kernel(host, DEBIAN_KERNEL)
    _add_kernel(host, "6.1.0-28-amd64")
    # Kaldırılmış çekirdekten kalan modül dizini (vmlinuz yok) sayılmaz
    _add_kernel(host, "6.1.0-30-amd64", boot_vmlinuz=False)

    assert drivers.installed_kernels() == ["6.1.0-9-amd64", DEBIAN_KERNEL, "6.1.0-28-amd64"]
    assert drivers.kernels_to_cover() == [DEBIAN_KERNEL, "6.1.0-28-amd64"]


def test_kernels_to_cover_after_upgrade_without_reboot(host, monkeypatch):
    # Arch: güncellemeden sonra çalışan çekirdeğin modülleri silinir, yalnızca yenisi açılabilir
    monkeypatch.setattr(drivers, "running_kernel", lambda: "6.12.9-arch1-1")
    _add_kernel(host, "6.12.10-arch1-1", boot_vmlinuz=False, module_vmlinuz=True, pkgbase="linux")
    assert drivers.kernels_to_cover() == ["6.12.10-arch1-1"]


def test_plan_debian_adds_headers_for_running_kernel(host):
    _pardus_host(host)
    plan = drivers.build_driver_plan(["nvidia-driver", "firmware-misc-nonfree"], "debian", "apt")

    assert plan.module_packages == ["nvidia-driver"]
    assert plan.plain_packages == ["firmware-misc-nonfree"]
    assert plan.kernels == [DEBIAN_KERNEL]
    assert plan.header_packages == ["linux-headers-6.1.0-18-amd64"]
    assert plan.optional_packages == ["linux-headers-amd64"]
    assert plan.blockers == []
    assert plan.already_present is False


def test_plan_arch_uses_pkgbase_header_package_for_each_kernel(host, monkeypatch):
    monkeypatch.setattr(drivers, "running_kernel", lambda: "6.6.70-1-lts")
    _add_kernel(host, "6.6.70-1-lts", boot_vmlinuz=False, module_vmlinuz=True, pkgbase="linux-lts")
    _add_kernel(host, "6.12.10-arch1-1", boot_vmlinuz=False, module_vmlinuz=True, pkgbase="linux")

    plan = drivers.build_driver_plan(["nvidia-dkms", "nvidia-utils"], "arch", "yay")

    assert plan.package_manager == "pacman"
    assert plan.module_packages == ["nvidia-dkms", "nvidia-utils"]
    assert plan.header_packages == ["linux-lts-headers", "linux-headers"]
    assert plan.blockers == []


def test_plan_arch_without_pkgbase_is_blocked(host, monkeypatch):
    monkeypatch.setattr(drivers, "running_kernel", lambda: "6.12.10-custom")
    _add_kernel(host, "6.12.10-custom", boot_vmlinuz=False, module_vmlinuz=True)

    plan = drivers.build_driver_plan(["nvidia-dkms"], "arch", "pacman")

    assert plan.blockers == [t("driver_block_headers_unknown", kernel="6.12.10-custom")]


def test_plan_fedora_uses_kernel_devel(host, monkeypatch):
    kver = "6.11.4-301.fc41.x86_64"
    monkeypatch.setattr(drivers, "running_kernel", lambda: kver)
    _add_kernel(host, kver, boot_vmlinuz=False, module_vmlinuz=True)

    plan = drivers.build_driver_plan(["akmod-nvidia", "xorg-x11-drv-nvidia"], "fedora", "dnf")

    assert plan.header_packages == [f"kernel-devel-{kver}"]
    assert plan.blockers == []


def test_plan_suse_needs_no_headers_and_installed_headers_are_not_added(host):
    _add_kernel(host, DEBIAN_KERNEL, headers=True)

    suse = drivers.build_driver_plan(["nvidia-driver-G06-kmp-default"], "suse", "zypper")
    assert suse.header_packages == [] and suse.blockers == []

    debian = drivers.build_driver_plan(["nvidia-driver"], "debian", "apt-get")
    assert debian.header_packages == []


def test_plan_without_kernel_module_packages_needs_no_checks(host):
    plan = drivers.build_driver_plan(["amd64-microcode", "mesa-vulkan-drivers"], "debian", "apt")
    assert plan.module_packages == [] and plan.kernels == [] and plan.blockers == []
    assert plan.plain_packages == ["amd64-microcode", "mesa-vulkan-drivers"]


def test_plan_unknown_family_is_blocked(host):
    _pardus_host(host)
    plan = drivers.build_driver_plan(["nvidia-driver"], "alpine", "apk")
    assert plan.blockers == [t("driver_block_family")]


# ---------------------------------------------------------------------------
# Secure Boot ve lockdown
# ---------------------------------------------------------------------------

def test_secure_boot_state_from_efi_variable(host, monkeypatch):
    # UEFI dizini yok: BIOS açılışı
    assert drivers.secure_boot_enabled() is False

    _set_secure_boot(host, True)
    assert drivers.secure_boot_enabled() is True

    _set_secure_boot(host, False)
    assert drivers.secure_boot_enabled() is False


def test_secure_boot_missing_variable_and_mokutil(host, monkeypatch):
    efivars = host.root / "efi" / "efivars"
    efivars.mkdir(parents=True)
    monkeypatch.setattr(drivers.shutil, "which", lambda name: None)

    # efivarfs boş ve mokutil yok: belirlenemez
    assert drivers.secure_boot_enabled() is None

    # Başka değişkenler var ama SecureBoot yok: ürün yazılımı desteklemiyor
    (efivars / "BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(b"\x07\x00\x00\x00\x01\x00")
    assert drivers.secure_boot_enabled() is False


def test_secure_boot_falls_back_to_mokutil(host, monkeypatch):
    (host.root / "efi").mkdir(parents=True)
    monkeypatch.setattr(drivers.shutil, "which", lambda name: "/usr/bin/mokutil" if name == "mokutil" else None)
    inner = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:1] == ["mokutil"]:
            return subprocess.CompletedProcess(cmd, 0, "SecureBoot enabled\n", "")
        return inner(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert drivers.secure_boot_enabled() is True


def test_unsigned_modules_blocked_by_sig_enforce_and_lockdown(host):
    assert drivers.unsigned_modules_blocked(False) is False
    assert drivers.unsigned_modules_blocked(True) is True
    assert drivers.unsigned_modules_blocked(None) is None

    # Lockdown kapalıysa Secure Boot açık olsa da imzasız modül yüklenir
    (host.root / "lockdown").write_text("[none] integrity confidentiality\n")
    assert drivers.unsigned_modules_blocked(True) is False

    (host.root / "lockdown").write_text("none [integrity] confidentiality\n")
    assert drivers.unsigned_modules_blocked(False) is True

    (host.root / "lockdown").write_text("[none] integrity confidentiality\n")
    (host.root / "sig_enforce").write_text("Y\n")
    assert drivers.unsigned_modules_blocked(False) is True


def test_plan_blocked_when_secure_boot_rejects_unsigned_modules(host):
    _pardus_host(host)
    _set_secure_boot(host, True)

    plan = drivers.build_driver_plan(["nvidia-driver"], "debian", "apt")

    assert plan.secure_boot is True
    assert t("driver_block_secure_boot") in plan.blockers


# ---------------------------------------------------------------------------
# Modül doğrulama
# ---------------------------------------------------------------------------

def test_module_present_variants(host, tmp_path):
    names = ("nvidia", "nvidia-current")
    _add_kernel(host, "k-debian")
    _add_module(host, "k-debian", "updates/dkms/nvidia-current.ko.xz")
    assert drivers.module_present("k-debian", names)

    _add_kernel(host, "k-fedora")
    _add_module(host, "k-fedora", "extra/nvidia/nvidia.ko.xz")
    assert drivers.module_present("k-fedora", names)

    # Yalnızca yardımcı modül varsa (nvidia-drm) ana modül yok sayılır
    _add_kernel(host, "k-drm")
    _add_module(host, "k-drm", "updates/dkms/nvidia-drm.ko")
    assert not drivers.module_present("k-drm", names)

    # Kırık bağlantı ve build/ üzerinden görünen başlık ağacı sayılmaz
    kdir = _add_kernel(host, "k-broken")
    (kdir / "updates").mkdir()
    os.symlink(tmp_path / "olmayan" / "nvidia.ko", kdir / "updates" / "nvidia.ko")
    outside = tmp_path / "headers_tree"
    outside.mkdir()
    (outside / "nvidia.ko").write_bytes(b"")
    os.symlink(outside, kdir / "build")
    assert not drivers.module_present("k-broken", names)


def test_plan_already_present_when_module_exists(host):
    _pardus_host(host)
    _add_module(host, DEBIAN_KERNEL)
    _set_secure_boot(host, True)

    plan = drivers.build_driver_plan(["nvidia-driver"], "debian", "apt")

    assert plan.already_present is True
    assert plan.blockers == []


# ---------------------------------------------------------------------------
# Paket listesi ve komutlar
# ---------------------------------------------------------------------------

def test_installed_package_set_parses_dpkg_and_rpm(monkeypatch):
    outputs = {
        "dpkg-query": (0, "installed bash\nconfig-files eski-paket\nnot-installed yok\n"
                          "half-configured nvidia-kernel-dkms\nunpacked libfoo:i386\n"),
        "rpm": (0, "kernel-devel-6.11.4-301.fc41.x86_64\ngpg-pubkey-abcd1234-5678\nbash-5.2-1.fc41.x86_64\n"),
        "pacman": (1, ""),
    }

    def fake_run(cmd, *args, **kwargs):
        rc, out = outputs[cmd[0]]
        return subprocess.CompletedProcess(cmd, rc, out, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert drivers.installed_package_set("apt") == {"bash", "nvidia-kernel-dkms", "libfoo:i386"}
    assert drivers.installed_package_set("dnf") == {"kernel-devel-6.11.4-301.fc41.x86_64", "bash-5.2-1.fc41.x86_64"}
    assert drivers.installed_package_set("pacman") is None
    assert drivers.installed_package_set("flatpak") is None


def test_driver_install_and_removal_commands():
    cmd, _ = drivers.driver_install_command("apt-get", ["nvidia-driver", "linux-headers-6.1.0-18-amd64"])
    assert cmd == ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "--",
                   "nvidia-driver", "linux-headers-6.1.0-18-amd64"]

    assert drivers.removal_command("apt", ["nvidia-driver", "-rf", "dkms"]) == [
        "sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "purge", "-y", "--", "nvidia-driver", "dkms"]
    assert drivers.removal_command("yay", ["nvidia-dkms"]) == ["sudo", "pacman", "-Rn", "--noconfirm", "--", "nvidia-dkms"]
    assert drivers.removal_command("dnf", ["akmod-nvidia"]) == ["sudo", "dnf", "remove", "-y", "--", "akmod-nvidia"]
    assert drivers.removal_command("zypper", ["x"]) == ["sudo", "zypper", "--non-interactive", "remove", "--", "x"]
    assert drivers.removal_command("apt", ["-y"]) == []


def test_describe_plan_lists_command_kernels_and_risks(host, monkeypatch):
    _pardus_host(host)
    monkeypatch.setattr(drivers, "find_distro_driver_tool", lambda: "pardus-nvidia-installer")
    lines = drivers.describe_driver_plan(drivers.build_driver_plan(["nvidia-driver"], "debian", "apt"))
    text = "\n".join(lines)

    assert "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -- nvidia-driver linux-headers-6.1.0-18-amd64 linux-headers-amd64" in text
    assert DEBIAN_KERNEL in text
    assert t("driver_plan_risk") in lines
    assert "pardus-nvidia-installer" in text

    _set_secure_boot(host, True)
    blocked = drivers.describe_driver_plan(drivers.build_driver_plan(["nvidia-driver"], "debian", "apt"))
    assert blocked[0] == t("driver_plan_blocked", pkgs="nvidia-driver")
    assert f"• {t('driver_block_secure_boot')}" in blocked
    assert t("driver_plan_risk") not in blocked


# ---------------------------------------------------------------------------
# Geri yükleme motoru: Pardus senaryosu
# ---------------------------------------------------------------------------

def test_engine_installs_driver_separately_and_verifies_module(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS, build_for=[DEBIAN_KERNEL])

    res = _run_pardus_restore(tmp_path, monkeypatch)

    report = res["driver_report"]
    assert report["status"] == "installed", report
    assert report["message"] == t("driver_status_installed", kernels=DEBIAN_KERNEL)

    driver_cmds = fake.with_word("nvidia-driver")
    assert driver_cmds == [[
        "sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "--",
        "nvidia-driver", "linux-headers-6.1.0-18-amd64", "linux-headers-amd64",
    ]]
    # Çekirdek modülü kurmayan paket diğer paketlerle, ayrı komutla kurulur
    firmware_cmds = fake.with_word("firmware-misc-nonfree")
    assert firmware_cmds and all("nvidia-driver" not in c for c in firmware_cmds)
    # Paket listesi bir kez güncellenir
    assert len([c for c in fake.calls if c[-1:] == ["update"]]) == 1
    assert not fake.with_word("purge")
    assert "nvidia-driver" in res["installed_packages"]


def test_engine_removes_new_packages_when_module_missing(tmp_path, monkeypatch, host):
    """Kullanıcının yaşadığı durum: paketler kuruldu ama modül çalışan çekirdek için oluşmadı."""
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash", "firmware-misc-nonfree"}, adds=DEBIAN_ADDS)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    report = res["driver_report"]
    assert report["status"] == "rolled_back", report
    purge = fake.with_word("purge")
    assert purge == [["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "purge", "-y", "--"] + sorted(DEBIAN_ADDS)]
    # Önceden kurulu paketlere dokunulmaz, sistem önceki paket listesine döner
    assert fake.installed == {"bash", "firmware-misc-nonfree"}
    assert report["removed"] == sorted(DEBIAN_ADDS)
    reason = t("driver_reason_module_missing", kernels=DEBIAN_KERNEL)
    assert report["message"] == t("driver_status_rolled_back", reason=reason, count=len(DEBIAN_ADDS))
    assert f"apt:nvidia-driver ({t('driver_failed_tag')})" in res["failed_packages"]


def test_engine_shows_manual_command_when_removal_fails(tmp_path, monkeypatch, host):
    _pardus_host(host)
    FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS, remove_rc=100)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    report = res["driver_report"]
    manual = " ".join(["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "purge", "-y", "--"] + sorted(DEBIAN_ADDS))
    assert report["status"] == "rollback_failed"
    assert report["manual_command"] == manual
    assert manual in report["message"]


def test_engine_rolls_back_failed_install_that_left_packages(tmp_path, monkeypatch, host):
    """DKMS derlemesi postinst'te düşünce dpkg paketleri yarım yapılandırılmış bırakır."""
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS, install_rc=100)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    assert res["driver_report"]["status"] == "rolled_back"
    assert t("driver_reason_install_failed", error="E: Sub-process /usr/bin/dpkg returned an error code (1)") in res["driver_report"]["message"]
    assert fake.installed == {"bash"}


def test_engine_failed_install_without_changes_removes_nothing(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=[], install_rc=100)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    assert res["driver_report"]["status"] == "failed"
    assert not fake.with_word("purge")


def test_engine_unknown_package_state_after_failure_shows_manual_command(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)
    snapshots = iter([{"bash"}, None])
    monkeypatch.setattr(drivers, "installed_package_set", lambda pm: next(snapshots))

    res = _run_pardus_restore(tmp_path, monkeypatch)

    report = res["driver_report"]
    assert report["status"] == "rollback_failed"
    assert "purge -y -- nvidia-driver linux-headers-6.1.0-18-amd64 linux-headers-amd64" in report["manual_command"]
    assert not fake.with_word("purge")


def test_engine_skips_module_drivers_when_secure_boot_blocks(tmp_path, monkeypatch, host):
    _pardus_host(host)
    _set_secure_boot(host, True)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    report = res["driver_report"]
    assert report["status"] == "skipped"
    assert t("driver_block_secure_boot") in report["message"]
    assert not fake.with_word("nvidia-driver")
    # Risksiz paketler yine kurulur
    assert fake.with_word("firmware-misc-nonfree")


def test_engine_skips_when_header_package_not_in_repo(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)
    monkeypatch.setattr(drivers, "package_available", lambda pm, name: name != "linux-headers-6.1.0-18-amd64")

    res = _run_pardus_restore(tmp_path, monkeypatch)

    report = res["driver_report"]
    assert report["status"] == "skipped"
    assert t("driver_block_not_in_repo", pkgs="linux-headers-6.1.0-18-amd64") in report["message"]
    assert not fake.with_word("nvidia-driver")


def test_engine_drops_unavailable_optional_header_metapackage(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS, build_for=[DEBIAN_KERNEL])
    monkeypatch.setattr(drivers, "package_available", lambda pm, name: name != "linux-headers-amd64")

    res = _run_pardus_restore(tmp_path, monkeypatch)

    assert res["driver_report"]["status"] == "installed"
    assert fake.with_word("nvidia-driver")[0][-2:] == ["nvidia-driver", "linux-headers-6.1.0-18-amd64"]


def test_engine_skips_without_package_snapshot(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)
    monkeypatch.setattr(drivers, "installed_package_set", lambda pm: None)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    assert res["driver_report"]["status"] == "skipped"
    assert t("driver_block_pkg_list") in res["driver_report"]["message"]
    assert not fake.with_word("nvidia-driver")


def test_engine_skips_without_sudo(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)
    monkeypatch.setattr(drivers, "sudo_ready", lambda: False)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    assert res["driver_report"]["status"] == "skipped"
    assert t("driver_block_sudo") in res["driver_report"]["message"]
    assert not fake.with_word("nvidia-driver")


def test_engine_already_present_does_not_touch_driver(tmp_path, monkeypatch, host):
    _pardus_host(host)
    _add_module(host, DEBIAN_KERNEL)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)

    res = _run_pardus_restore(tmp_path, monkeypatch)

    assert res["driver_report"]["status"] == "already_present"
    assert not fake.with_word("nvidia-driver")


def test_engine_dry_run_reports_plan_without_installing(tmp_path, monkeypatch, host):
    _pardus_host(host)
    fake = FakePackageSystem(monkeypatch, host, installed={"bash"}, adds=DEBIAN_ADDS)

    res = _run_pardus_restore(tmp_path, monkeypatch, dry_run=True)

    assert res["driver_report"]["status"] == "dry_run"
    dry_lines = [line for line in res["installed_packages"] if "nvidia-driver" in line]
    assert len(dry_lines) == 1 and dry_lines[0].startswith("[DRY-RUN]")
    assert "linux-headers-6.1.0-18-amd64" in dry_lines[0]
    assert not fake.with_word("nvidia-driver")
    assert not fake.with_word("purge")


def test_engine_fedora_waits_for_akmods_before_verifying(tmp_path, monkeypatch, host):
    kver = "6.11.4-301.fc41.x86_64"
    monkeypatch.setattr(drivers, "running_kernel", lambda: kver)
    _add_kernel(host, kver, boot_vmlinuz=False, module_vmlinuz=True)
    adds = ["akmod-nvidia", "akmods", f"kernel-devel-{kver}", "xorg-x11-drv-nvidia"]
    fake = FakePackageSystem(
        monkeypatch, host, installed={"bash-5.2-1.fc41.x86_64"}, adds=adds, trigger="akmod-nvidia",
        build_for=[kver], build_on="akmods", module_rel="extra/nvidia/nvidia.ko.xz",
    )
    monkeypatch.setattr("distronomad.restore.shutil.which", lambda name: "/usr/sbin/akmods" if name == "akmods" else None)
    profile = HardwareProfile(gpu_vendor="nvidia", recommended_packages={"fedora": ["akmod-nvidia", "xorg-x11-drv-nvidia"]})
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: profile)

    backup = _make_backup(tmp_path / "fedora_backup.tar.gz")
    engine = RestoreEngine(backup_path=backup, transaction_dir=tmp_path / "tx")
    engine.load_manifest()
    target = SystemInfo(distro_name="Fedora", distro_id="fedora", available_package_managers=["dnf"], home_dir=str(tmp_path / "home"))
    res = engine.execute_restore(
        RestoreOptions(target_home=str(tmp_path / "home"), selected_configs=[], install_packages=False, install_drivers=True),
        target_system_info=target,
    )
    engine.cleanup()

    assert res["driver_report"]["status"] == "installed", res["driver_report"]
    assert ["sudo", "akmods", "--force", "--kernels", kver] in fake.calls
    install_idx = fake.calls.index(fake.with_word("akmod-nvidia")[0])
    assert fake.calls.index(["sudo", "akmods", "--force", "--kernels", kver]) > install_idx


def test_backup_list_driver_packages_are_not_installed(tmp_path, monkeypatch, host):
    """Yedekteki listede NVIDIA paketleri olsa bile (kutu işaretli değilken de) denetimsiz kurulmaz."""
    pkgs = [
        PackageItem(name="nvidia-driver", manager="apt"),
        PackageItem(name="nvidia-settings", manager="apt"),
        PackageItem(name="curl", manager="apt"),
        PackageItem(name="nvidia-utils", manager="pacman"),
        PackageItem(name="Platform.GL.nvidia", manager="flatpak", app_id="org.freedesktop.Platform.GL.nvidia-550-120"),
    ]
    res = _run_pardus_restore(tmp_path, monkeypatch, dry_run=True, selected_packages=pkgs, install_drivers=False)

    assert res["skipped_driver_packages"] == ["nvidia-driver", "nvidia-settings", "nvidia-utils"]
    planned = " ".join(res["installed_packages"])
    assert "curl" in planned
    assert "nvidia-driver" not in planned and "nvidia-utils" not in planned and "nvidia-settings" not in planned
    # Flatpak GL eklentisi kullanıcı alanındadır, çekirdek sürücüsü kurmaz
    assert "org.freedesktop.Platform.GL.nvidia-550-120" in planned
    assert t("restore_warn_driver_pkgs_from_backup", count=3, pkgs="nvidia-driver, nvidia-settings, nvidia-utils") in res["warnings"]


# ---------------------------------------------------------------------------
# CLI onayı
# ---------------------------------------------------------------------------

class _FakeStdin:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self):
        return self._tty


def _cli_setup(tmp_path, monkeypatch):
    target = SystemInfo(distro_name="Arch Linux", distro_id="arch", available_package_managers=["pacman"], home_dir=str(tmp_path / "home"))
    monkeypatch.setattr(SystemScanner, "scan_system_info", lambda self: target)
    profile = HardwareProfile(gpu_vendor="nvidia", gpus=["NVIDIA GeForce RTX 4070"], recommended_packages={"arch": ["nvidia-dkms", "nvidia-utils"]})
    monkeypatch.setattr("distronomad.cli.scan_hardware_profile", lambda: profile)
    captured = {}

    def fake_execute(self, options, target_system_info=None):
        captured["options"] = options
        return {"restored_configs": [], "restored_files": [], "installed_packages": [], "failed_packages": [], "warnings": []}

    monkeypatch.setattr(RestoreEngine, "execute_restore", fake_execute)
    backup = _make_backup(tmp_path / "cli_backup.tar.gz")
    args = build_parser().parse_args(["restore", "--src", str(backup), "--target-home", str(tmp_path / "home"), "--install-drivers"])
    return args, captured


@pytest.mark.parametrize("tty, answer, expected, message_key", [
    (True, "e", True, None),
    (True, "Evet", True, None),
    (True, "y", True, None),
    (True, "", False, "cli_drivers_skipped_by_user"),
    (True, "h", False, "cli_drivers_skipped_by_user"),
    (False, "e", False, "cli_drivers_noninteractive"),
])
def test_cli_asks_before_installing_kernel_driver(tmp_path, monkeypatch, capsys, tty, answer, expected, message_key):
    args, captured = _cli_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty))
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or answer)

    cmd_restore(args)
    out = " ".join(capsys.readouterr().out.split())

    assert captured["options"].install_drivers is expected
    assert asked == ([t("cli_drivers_confirm")] if tty else [])
    # Plan, komut ve riskler onaydan önce gösterilir
    assert "sudo pacman -S --needed --noconfirm -- nvidia-dkms nvidia-utils" in out
    assert " ".join(t("driver_plan_risk").split()) in out
    if message_key:
        assert t(message_key) in out


def test_cli_does_not_ask_when_plan_is_blocked_or_dry_run(tmp_path, monkeypatch, capsys):
    args, captured = _cli_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("onay sorulmamalı"))

    efivars = tmp_path / "efi" / "efivars"
    efivars.mkdir(parents=True)
    (efivars / drivers.SECURE_BOOT_VAR_NAME).write_bytes(bytes([6, 0, 0, 0, 1]))
    monkeypatch.setattr(drivers, "EFI_DIR", tmp_path / "efi")

    cmd_restore(args)
    out = " ".join(capsys.readouterr().out.split())
    # Engel varsa kurulum denenmeyeceği için sorulmaz; motor sürücü modülünü atlar ve nedeni raporlar
    assert captured["options"].install_drivers is True
    assert t("driver_plan_blocked", pkgs="nvidia-dkms, nvidia-utils") in out

    monkeypatch.setattr(drivers, "EFI_DIR", tmp_path / "bios")
    args.dry_run = True
    cmd_restore(args)
    assert captured["options"].install_drivers is True


def test_cli_prints_driver_report_and_skipped_backup_drivers(tmp_path, monkeypatch, capsys):
    args, _ = _cli_setup(tmp_path, monkeypatch)
    args.install_drivers = False
    reason = t("driver_reason_module_missing", kernels=DEBIAN_KERNEL)
    message = t("driver_status_rolled_back", reason=reason, count=7)

    def fake_execute(self, options, target_system_info=None):
        return {
            "restored_configs": [], "restored_files": [], "installed_packages": [], "failed_packages": [],
            "warnings": [], "skipped_driver_packages": ["nvidia-utils"],
            "driver_report": {"status": "rolled_back", "message": message},
        }

    monkeypatch.setattr(RestoreEngine, "execute_restore", fake_execute)
    cmd_restore(args)
    out = " ".join(capsys.readouterr().out.split())

    assert " ".join(message.split()) in out
    assert " ".join(t("restore_warn_driver_pkgs_from_backup", count=1, pkgs="nvidia-utils").split()) in out


# ---------------------------------------------------------------------------
# TUI onayı
# ---------------------------------------------------------------------------

def _tui_setup(tmp_path, monkeypatch):
    target = SystemInfo(distro_name="Arch Linux", distro_id="arch", available_package_managers=["pacman"], home_dir=str(tmp_path))
    monkeypatch.setattr(SystemScanner, "scan_system_info", lambda self: target)
    profile = HardwareProfile(gpu_vendor="nvidia", gpus=["NVIDIA GeForce RTX 4070"], recommended_packages={"arch": ["nvidia-dkms", "nvidia-utils"]})
    monkeypatch.setattr("distronomad.ui.app.scan_hardware_profile", lambda: profile)
    monkeypatch.setattr("distronomad.restore.scan_hardware_profile", lambda: profile)
    return _make_backup(tmp_path / "tui_backup.tar.gz")


async def _start_restore_with_drivers(app, pilot, backup):
    await app.workers.wait_for_complete()
    app.query_one("#input-restore-src", Input).value = str(backup)
    app._inspect_restore_with_password(backup, None)
    await pilot.pause()
    app.query_one("#chk-restore-drivers", Checkbox).value = True
    app.query_one("#chk-dry-run", Checkbox).value = False
    app._check_sudo_passwordless = lambda: True
    captured = []
    app._run_restore_worker = lambda engine, options: captured.append(options)
    app._handle_start_restore()
    await pilot.pause()
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize("button, expected", [
    ("btn-driver-install", [True]),
    ("btn-driver-skip", [False]),
    ("btn-driver-cancel", []),
])
async def test_tui_driver_consent_modal(tmp_path, monkeypatch, button, expected):
    backup = _tui_setup(tmp_path, monkeypatch)
    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(140, 45)) as pilot:
        captured = await _start_restore_with_drivers(app, pilot, backup)

        # Onay alınmadan geri yükleme başlamaz; plan, komut ve riskler gösterilir
        assert captured == []
        assert isinstance(app.screen, DriverRiskModal)
        desc = str(app.screen.query_one("#driver-desc", Label).render())
        assert "sudo pacman -S --needed --noconfirm -- nvidia-dkms nvidia-utils" in desc
        assert "6.0.0-test" in desc

        app.screen.query_one(f"#{button}", Button).press()
        await pilot.pause()

        assert [opts.install_drivers for opts in captured] == expected
        if expected:
            # Ayarlar sürücü kararından bağımsız geri yüklenir
            assert len(captured[0].selected_configs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_tui_driver_modal_buttons_fit_on_screen(tmp_path, monkeypatch, size):
    """Açıklama uzun olsa da onay düğmeleri küçük terminalde ekranın içinde kalır."""
    backup = _tui_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(drivers, "find_distro_driver_tool", lambda: "chwd")
    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=size) as pilot:
        await _start_restore_with_drivers(app, pilot, backup)
        assert isinstance(app.screen, DriverRiskModal)
        width, height = size
        # Açıklama kesilmez, kapsayıcının genişliğinde alt satıra kaydırılır
        desc = app.screen.query_one("#driver-desc", Label)
        scroll = app.screen.query_one("#driver-desc-scroll")
        assert desc.size.width <= scroll.size.width
        for button_id in ("btn-driver-install", "btn-driver-skip", "btn-driver-cancel"):
            region = app.screen.query_one(f"#{button_id}", Button).region
            assert region.height > 0 and region.width > 0, button_id
            assert region.y >= 0 and region.bottom <= height and region.right <= width, (button_id, region)
        if os.environ.get("DISTRONOMAD_SCREENSHOT_DIR"):
            app.save_screenshot(f"driver_modal_{width}x{height}.svg", path=os.environ["DISTRONOMAD_SCREENSHOT_DIR"])


@pytest.mark.asyncio
async def test_tui_blocked_plan_starts_without_modal(tmp_path, monkeypatch):
    backup = _tui_setup(tmp_path, monkeypatch)
    efivars = tmp_path / "efi" / "efivars"
    efivars.mkdir(parents=True)
    (efivars / drivers.SECURE_BOOT_VAR_NAME).write_bytes(bytes([6, 0, 0, 0, 1]))
    monkeypatch.setattr(drivers, "EFI_DIR", tmp_path / "efi")

    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(140, 45)) as pilot:
        captured = await _start_restore_with_drivers(app, pilot, backup)

        assert not isinstance(app.screen, DriverRiskModal)
        assert [opts.install_drivers for opts in captured] == [True]


@pytest.mark.asyncio
async def test_tui_sudo_failure_also_skips_drivers(tmp_path, monkeypatch):
    backup = _tui_setup(tmp_path, monkeypatch)
    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(140, 45)) as pilot:
        captured = await _start_restore_with_drivers(app, pilot, backup)
        app._check_sudo_passwordless = lambda: False

        def no_suspend():
            raise RuntimeError("askıya alma desteklenmiyor")

        app.suspend = no_suspend
        app.screen.query_one("#btn-driver-install", Button).press()
        await pilot.pause()

        assert [opts.install_drivers for opts in captured] == [False]


@pytest.mark.asyncio
async def test_tui_shows_driver_report(tmp_path):
    manual = "sudo env DEBIAN_FRONTEND=noninteractive apt-get purge -y -- nvidia-driver dkms"
    message = t("driver_status_rollback_failed", reason=t("driver_reason_module_missing", kernels=DEBIAN_KERNEL), cmd=manual)
    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(140, 45)) as pilot:
        await app.workers.wait_for_complete()
        # RichLog görünmeyen sekmede yazılanları ertelediği için geri yükleme sekmesi açılır
        app.query_one("#main-tabs", TabbedContent).active = "tab-restore"
        await pilot.pause()

        app._restore_finished({
            "restored_configs": [], "restored_files": [], "installed_packages": [], "failed_packages": [],
            "warnings": [], "driver_report": {"status": "rollback_failed", "message": message},
        })
        await pilot.pause()

        log = app.query_one("#restore-log", RichLog)
        text = " ".join(" ".join(strip.text for strip in log.lines).split())
        assert "DİKKAT" in text
        assert "apt-get purge -y -- nvidia-driver dkms" in text


# ---------------------------------------------------------------------------
# Bağımsız restore.sh
# ---------------------------------------------------------------------------

def test_standalone_script_skips_driver_packages(tmp_path):
    script = tmp_path / "restore.sh"
    script.write_text(generate_standalone_restore_script())
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    (fake_bin / "sudo").write_text("#!/bin/sh\nexec \"$@\"\n")
    pacman_log = tmp_path / "pacman_calls.log"
    (fake_bin / "pacman").write_text(f"#!/bin/sh\necho \"ARGS: $*\" >> \"{pacman_log}\"\nexit 0\n")
    for tool in ("sudo", "pacman"):
        (fake_bin / tool).chmod(0o755)

    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    (packages_dir / "system_packages.txt").write_text(
        "bash\nnvidia-utils\nlib32-NVIDIA-utils\nbroadcom-wl-dkms\ngit\n", encoding="utf-8"
    )

    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ.get('PATH', '')}", LANG="C.UTF-8")
    proc = subprocess.run(
        [str(script), "--packages-only", "--lang", "en", "--target-home", str(tmp_path / "home")],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )

    calls = pacman_log.read_text()
    assert "bash" in calls and "git" in calls
    assert "nvidia" not in calls.lower() and "broadcom" not in calls
    assert "Driver package not installed automatically" in proc.stdout + proc.stderr
