"""
DistroNomad - Donanım ve Sürücü Profili Algılayıcı (Hardware & Driver Profiler).
Dağıtım değişiminde GPU, Wi-Fi, Ses ve mikro kod sürücü gereksinimlerini belirler.
"""

from __future__ import annotations
import os
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Tuple

from distronomad.i18n import t


@dataclass
class HardwareProfile:
    """Sistemin donanım ve sürücü envanteri."""
    cpu_vendor: str = "Unknown"        # Intel, AMD, ARM
    cpu_model: str = ""
    gpus: List[str] = field(default_factory=list)          # ["NVIDIA GeForce RTX 4060", "Intel UHD Graphics"]
    gpu_vendor: str = "generic"        # nvidia, amd, intel, hybrid, generic
    wifi_devices: List[str] = field(default_factory=list)  # ["Intel Wi-Fi 6 AX200", "Broadcom BCM4360"]
    audio_system: str = "pipewire"     # pipewire, pulseaudio, alsa
    recommended_packages: Dict[str, List[str]] = field(default_factory=dict) # {"arch": [...], "debian": [...], "fedora": [...]}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HardwareProfile":
        return cls(**data)


def scan_hardware_profile() -> HardwareProfile:
    """Mevcut sistemin donanım profilini güvenli ve salt-okunur şekilde analiz eder."""
    cpu_vendor = "Unknown"
    cpu_model = ""

    # 1. CPU Tespiti (/proc/cpuinfo)
    if os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if "vendor_id" in line and cpu_vendor == "Unknown":
                        val = line.split(":", 1)[1].strip()
                        if "AuthenticAMD" in val:
                            cpu_vendor = "AMD"
                        elif "GenuineIntel" in val:
                            cpu_vendor = "Intel"
                    elif "model name" in line and not cpu_model:
                        cpu_model = line.split(":", 1)[1].strip()
        except Exception:
            pass

    # 2. GPU ve PCI Cihazları Tespiti
    gpus: List[str] = []
    wifi_devices: List[str] = []

    if shutil.which("lspci"):
        try:
            res = subprocess.run(["lspci"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    lower_line = line.lower()
                    # VGA veya 3D Denetleyici
                    if "vga compatible controller" in lower_line or "3d controller" in lower_line or "display controller" in lower_line:
                        parts = line.split(":", 2)
                        gpu_desc = parts[-1].strip() if len(parts) >= 3 else line
                        gpus.append(gpu_desc)
                    # Ağ ve Wi-Fi
                    elif "network controller" in lower_line or "wireless" in lower_line or "wi-fi" in lower_line:
                        parts = line.split(":", 2)
                        wifi_desc = parts[-1].strip() if len(parts) >= 3 else line
                        wifi_devices.append(wifi_desc)
        except Exception:
            pass

    # lspci yoksa veya GPU boşsa /sys/class/drm fallback
    if not gpus and os.path.exists("/sys/class/drm"):
        try:
            for card in os.listdir("/sys/class/drm"):
                if card.startswith("card") and "-" not in card:
                    dev_path = f"/sys/class/drm/{card}/device/uevent"
                    if os.path.exists(dev_path):
                        with open(dev_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            if "nvidia" in content.lower():
                                gpus.append("NVIDIA GPU (DRM)")
                            elif "amdgpu" in content.lower() or "radeon" in content.lower():
                                gpus.append("AMD Radeon GPU (DRM)")
                            elif "i915" in content.lower() or "xe" in content.lower():
                                gpus.append("Intel Graphics (DRM)")
        except Exception:
            pass

    # GPU Satıcısı Belirleme
    gpu_vendor = "generic"
    has_nvidia = any("nvidia" in g.lower() for g in gpus)
    has_amd = any("amd" in g.lower() or "radeon" in g.lower() for g in gpus)
    has_intel = any("intel" in g.lower() for g in gpus)

    if has_nvidia and (has_amd or has_intel):
        gpu_vendor = "hybrid"
    elif has_nvidia:
        gpu_vendor = "nvidia"
    elif has_amd:
        gpu_vendor = "amd"
    elif has_intel:
        gpu_vendor = "intel"

    # 3. Ses Sunucusu Tespiti
    audio_system = "pipewire"
    try:
        # pgrep ile çalışan ses sürecini kontrol et
        if shutil.which("pgrep"):
            p_pw = subprocess.run(["pgrep", "-x", "pipewire"], stdout=subprocess.PIPE)
            if p_pw.returncode == 0:
                audio_system = "pipewire"
            else:
                p_pa = subprocess.run(["pgrep", "-x", "pulseaudio"], stdout=subprocess.PIPE)
                if p_pa.returncode == 0:
                    audio_system = "pulseaudio"
    except Exception:
        pass

    # 4. Hedef Dağıtımlara Göre Paket Önerileri Oluştur
    recommendations: Dict[str, List[str]] = {
        "arch": [],
        "debian": [],
        "fedora": [],
        "suse": []
    }

    # CPU Mikro Kod Önerileri
    if cpu_vendor == "AMD":
        recommendations["arch"].append("amd-ucode")
        recommendations["debian"].append("amd64-microcode")
        recommendations["fedora"].append("microcode_ctl")
    elif cpu_vendor == "Intel":
        recommendations["arch"].append("intel-ucode")
        recommendations["debian"].append("intel-microcode")
        recommendations["fedora"].append("microcode_ctl")

    # GPU Sürücü Önerileri
    if gpu_vendor in ["nvidia", "hybrid"]:
        recommendations["arch"].extend(["nvidia-dkms", "nvidia-utils"])
        recommendations["debian"].extend(["nvidia-driver", "firmware-misc-nonfree"])
        recommendations["fedora"].extend(["akmod-nvidia", "xorg-x11-drv-nvidia"])
        recommendations["suse"].extend(["nvidia-driver-G06-kmp-default"])
    elif gpu_vendor == "amd":
        recommendations["arch"].extend(["vulkan-radeon", "mesa"])
        recommendations["debian"].extend(["mesa-vulkan-drivers", "libgl1-mesa-dri"])
        recommendations["fedora"].extend(["mesa-vulkan-drivers"])
    elif gpu_vendor == "intel":
        recommendations["arch"].extend(["vulkan-intel", "intel-media-driver"])
        recommendations["debian"].extend(["intel-media-va-driver", "mesa-vulkan-drivers"])

    # Broadcom Wi-Fi Önerileri
    if any("broadcom" in w.lower() for w in wifi_devices):
        recommendations["arch"].append("broadcom-wl-dkms")
        recommendations["debian"].append("broadcom-sta-dkms")
        recommendations["fedora"].append("broadcom-wl")

    return HardwareProfile(
        cpu_vendor=cpu_vendor,
        cpu_model=cpu_model,
        gpus=gpus,
        gpu_vendor=gpu_vendor,
        wifi_devices=wifi_devices,
        audio_system=audio_system,
        recommended_packages=recommendations
    )


def is_hardware_profile_different(live_hw: HardwareProfile, manifest_hw: Dict[str, Any]) -> Tuple[bool, str]:
    """Canlı donanım profili ile yedekteki profilin farklı olup olmadığını kontrol eder."""
    if not manifest_hw:
        return False, ""

    m_gpus = [str(g).strip().lower() for g in manifest_hw.get("gpus", []) if g]
    l_gpus = [str(g).strip().lower() for g in live_hw.gpus if g]

    m_gpu_vendor = str(manifest_hw.get("gpu_vendor", "")).strip().lower()
    l_gpu_vendor = str(live_hw.gpu_vendor or "").strip().lower()

    m_cpu = str(manifest_hw.get("cpu_model") or manifest_hw.get("cpu_vendor") or "").strip().lower()
    l_cpu = str(live_hw.cpu_model or live_hw.cpu_vendor or "").strip().lower()

    is_diff = False
    diff_parts = []

    if m_gpus and l_gpus and set(m_gpus) != set(l_gpus):
        is_diff = True
        orig_m_gpus = ", ".join(manifest_hw.get("gpus", []))
        diff_parts.append(t("hardware_diff_gpu", value=orig_m_gpus))
    elif m_gpu_vendor and l_gpu_vendor and m_gpu_vendor != l_gpu_vendor:
        is_diff = True
        diff_parts.append(t("hardware_diff_gpu", value=m_gpu_vendor))

    if m_cpu and l_cpu and m_cpu != l_cpu:
        is_diff = True
        orig_m_cpu = manifest_hw.get("cpu_model") or manifest_hw.get("cpu_vendor")
        diff_parts.append(t("hardware_diff_cpu", value=orig_m_cpu))

    return is_diff, " | ".join(diff_parts) if diff_parts else ""

