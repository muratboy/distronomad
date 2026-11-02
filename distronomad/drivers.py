"""
DistroNomad - Denetimli Sürücü Kurulumu (Checked Driver Installation).

NVIDIA ve Broadcom wl gibi sürücüler çekirdek modülü kurar ve kurulunca açık kaynak sürücüyü
(nouveau, b43) devre dışı bırakır. Modül açılacak çekirdek için oluşmazsa (başlık paketi eksik,
Secure Boot imzasız modülü reddediyor) bilgisayar siyah ekranla ya da ağsız açılır.
Bu modül kurulumdan önceki denetimleri, kurulumdan sonraki doğrulamayı ve başarısız kurulumun
tam olarak geri alınması için gereken paket listesi karşılaştırmasını sağlar.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

from distronomad.i18n import t
from distronomad.packagemap import detect_distro_family, get_install_command, is_valid_package_name


# Testlerde sahte dizinlere yönlendirilebilen sistem yolları
MODULES_DIR = Path("/lib/modules")
BOOT_DIR = Path("/boot")
EFI_DIR = Path("/sys/firmware/efi")
LOCKDOWN_FILE = Path("/sys/kernel/security/lockdown")
SIG_ENFORCE_FILE = Path("/sys/module/module/parameters/sig_enforce")
SECURE_BOOT_VAR_NAME = "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"

SYSTEM_PACKAGE_MANAGERS = ("pacman", "yay", "paru", "apt", "apt-get", "dnf", "zypper")

# Kurulum ve modül derlemesi (DKMS/akmods) için üst süre
DRIVER_INSTALL_TIMEOUT = 1800

_SUPPORTED_FAMILIES = ("debian", "arch", "fedora", "suse")
# Modülü yerelde derleyen (DKMS/akmods) aileler başlık paketi ister; openSUSE KMP paketleri hazır derlenmiş gelir
_HEADER_FAMILIES = ("debian", "arch", "fedora")
_FAMILY_BY_PM = {
    "pacman": "arch", "yay": "arch", "paru": "arch",
    "apt": "debian", "apt-get": "debian",
    "dnf": "fedora", "zypper": "suse",
}
_PM_BY_FAMILY = {"arch": "pacman", "debian": "apt-get", "fedora": "dnf", "suse": "zypper"}

# Dağıtımların kendi sürücü araçları (yalnızca sistemde bulunursa önerilir)
_DISTRO_DRIVER_TOOLS = ("ubuntu-drivers", "mintdrivers", "chwd", "mhwd", "pardus-nvidia-installer")


def driver_module_names(package: str) -> Tuple[str, ...]:
    """Paket açık kaynak sürücünün yerini alan bir çekirdek sürücüsü kuruyorsa doğrulanacak modül
    adlarını, kurmuyorsa boş demet döndürür. NVIDIA paketleri (nvidia-utils dahil) nouveau'yu,
    Broadcom wl paketleri b43/brcmsmac sürücülerini devre dışı bırakır."""
    low = (package or "").lower()
    if "nvidia" in low:
        return ("nvidia", "nvidia-current")
    if low.startswith(("broadcom-wl", "broadcom-sta")):
        return ("wl",)
    return ()


def is_kernel_driver_package(package: str) -> bool:
    """Paket kurulunca açık kaynak sürücüyü devre dışı bırakan bir çekirdek sürücüsü paketi mi?"""
    return bool(driver_module_names(package))


def resolve_driver_target(system_info: Any) -> Tuple[str, str]:
    """Sürücülerin kurulacağı dağıtım ailesini ve sistem paket yöneticisini belirler.
    Aile belirlenemezse "unknown" döner; sürücü önerisi yapılmaz."""
    family = detect_distro_family(getattr(system_info, "distro_id", ""), getattr(system_info, "distro_like", ""))
    managers = getattr(system_info, "available_package_managers", None) or []
    pm = next((m for m in managers if m in SYSTEM_PACKAGE_MANAGERS), "")
    if family == "unknown":
        family = _FAMILY_BY_PM.get(pm, "unknown")
    if not pm:
        pm = _PM_BY_FAMILY.get(family, "")
    if pm in ("yay", "paru"):
        # Sürücüler resmi depodan kurulur; AUR yardımcısına gerek yok
        pm = "pacman"
    return family, pm


def running_kernel() -> str:
    """Çalışan çekirdeğin sürümü (uname -r)."""
    return os.uname().release


def _kernel_sort_key(kver: str) -> List[Tuple[int, int, str]]:
    """Çekirdek sürümlerini doğal sırayla (6.1.0-9 < 6.1.0-28) karşılaştırmak için anahtar."""
    return [(0, int(tok), "") if tok.isdigit() else (1, 0, tok) for tok in re.split(r"(\d+)", kver) if tok]


def installed_kernels() -> List[str]:
    """Açılabilir (vmlinuz dosyası olan) çekirdekleri eskiden yeniye sıralı döndürür.
    Kaldırılmış çekirdeklerden kalan modül dizinleri sayılmaz."""
    try:
        entries = [p for p in MODULES_DIR.iterdir() if p.is_dir()]
    except OSError:
        return []
    kernels = [
        p.name for p in entries
        if (p / "vmlinuz").exists() or (BOOT_DIR / f"vmlinuz-{p.name}").exists()
    ]
    return sorted(kernels, key=_kernel_sort_key)


def kernels_to_cover() -> List[str]:
    """Sürücü modülünün bulunması gereken çekirdekler: çalışan çekirdek (hâlâ kuruluysa) ve
    bir sonraki açılışta varsayılan olarak seçilen, kurulu en yeni çekirdek."""
    installed = installed_kernels()
    running = running_kernel()
    if not installed:
        return [running]
    kernels = [running] if running in installed else []
    if installed[-1] not in kernels:
        kernels.append(installed[-1])
    return kernels


def headers_installed(kver: str) -> bool:
    """Çekirdeğin başlık dosyaları (DKMS/akmods modülü bunlarla derler) kurulu mu?"""
    return (MODULES_DIR / kver / "build").is_dir()


def header_package(family: str, kver: str) -> Optional[str]:
    """Çekirdeğin başlık paketinin adını döndürür; belirlenemezse None."""
    if family == "debian":
        return f"linux-headers-{kver}"
    if family == "fedora":
        return f"kernel-devel-{kver}"
    if family == "arch":
        # Arch tabanlı dağıtımlarda çekirdek paketinin adı (linux, linux-lts, linux-cachyos...) pkgbase dosyasındadır
        try:
            pkgbase = (MODULES_DIR / kver / "pkgbase").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return f"{pkgbase}-headers" if pkgbase else None
    return None


def _debian_header_metapackage(kver: str) -> Optional[str]:
    """Çekirdek türünün başlık üst paketi (6.1.0-18-amd64 -> linux-headers-amd64). Kurulu olursa
    sonraki çekirdek güncellemeleriyle başlıklar da gelir ve DKMS modülü yeni çekirdek için de derler."""
    parts = kver.split("-", 2)
    if len(parts) == 3 and parts[2]:
        return f"linux-headers-{parts[2]}"
    return None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def secure_boot_enabled() -> Optional[bool]:
    """Secure Boot açık mı? UEFI olmayan açılışta False, belirlenemezse None."""
    if not EFI_DIR.exists():
        return False
    efivars = EFI_DIR / "efivars"
    try:
        data = (efivars / SECURE_BOOT_VAR_NAME).read_bytes()
        # İlk 4 bayt değişken öznitelikleri, ardından 1 baytlık değer gelir
        if len(data) >= 5:
            return data[4] == 1
    except FileNotFoundError:
        # efivarfs bağlı ama değişken yoksa ürün yazılımı Secure Boot desteklemiyor
        try:
            if any(efivars.iterdir()):
                return False
        except OSError:
            pass
    except OSError:
        pass

    if shutil.which("mokutil"):
        try:
            proc = subprocess.run(
                ["mokutil", "--sb-state"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                env=dict(os.environ, LC_ALL="C"),
            )
            out = f"{proc.stdout}\n{proc.stderr}".lower()
            if "secureboot enabled" in out:
                return True
            if "secureboot disabled" in out or "doesn't support secure boot" in out:
                return False
        except Exception:
            pass
    return None


def unsigned_modules_blocked(secure_boot: Optional[bool]) -> Optional[bool]:
    """Çekirdek imzasız (yerelde DKMS/akmods ile derlenen) modülleri reddediyor mu?
    module.sig_enforce veya kernel lockdown etkinse True, lockdown kapalıysa False döner;
    ikisi de okunamazsa Secure Boot durumuna göre karar verilir (açıksa reddettiği varsayılır)."""
    sig = _read_text(SIG_ENFORCE_FILE)
    if sig is not None and sig.strip().upper().startswith("Y"):
        return True
    lock = _read_text(LOCKDOWN_FILE)
    if lock is not None:
        match = re.search(r"\[(\w+)\]", lock)
        if match:
            return match.group(1) != "none"
    return secure_boot


def module_present(kver: str, names: Tuple[str, ...]) -> bool:
    """Çekirdeğin modül dizininde verilen adlardan biriyle bir modül (.ko, .ko.xz, .ko.zst...) var mı?
    Başlık ağacına giden build/source bağlantıları izlenmez; kırık bağlantılar sayılmaz."""
    wanted = {n.replace("-", "_") for n in names}
    for root, _dirs, files in os.walk(MODULES_DIR / kver):
        for fname in files:
            if ".ko" not in fname:
                continue
            stem = fname.split(".ko", 1)[0].replace("-", "_")
            if stem in wanted and os.path.exists(os.path.join(root, fname)):
                return True
    return False


def find_distro_driver_tool() -> str:
    """Sistemde dağıtımın kendi sürücü aracı varsa adını döndürür."""
    for tool in _DISTRO_DRIVER_TOOLS:
        if shutil.which(tool):
            return tool
    return ""


def sudo_ready() -> bool:
    """sudo komutları çalıştırılabilir mi? Parolasız sudo ya da parola sorulabilecek bir terminal gerekir."""
    try:
        proc = subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    return bool(sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty())


def package_available(pm: str, name: str) -> Optional[bool]:
    """Paket depoda (ya da kurulu olarak) var mı? Sorgulanamazsa None döner."""
    if not is_valid_package_name(name, manager=pm):
        return False
    mgr = (pm or "").lower()
    if mgr in ("apt", "apt-get"):
        cmd = ["apt-cache", "show", name]
    elif mgr in ("pacman", "yay", "paru"):
        cmd = ["pacman", "-Si", name]
    elif mgr == "dnf":
        cmd = ["dnf", "info", name]
    elif mgr == "zypper":
        cmd = ["zypper", "--non-interactive", "--quiet", "search", "--match-exact", name]
    else:
        return None
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            env=dict(os.environ, LC_ALL="C"),
        )
    except Exception:
        return None
    return proc.returncode == 0


def installed_package_set(pm: str) -> Optional[Set[str]]:
    """Kurulu paketlerin kümesini döndürür; başarısız bir kurulumda yalnızca o adımda gelen
    paketleri kaldırabilmek için kurulumdan önce ve sonra alınır. Okunamazsa None döner."""
    mgr = (pm or "").lower()
    if mgr in ("apt", "apt-get"):
        cmd = ["dpkg-query", "-W", "-f", "${db:Status-Status} ${binary:Package}\\n"]
    elif mgr in ("pacman", "yay", "paru"):
        cmd = ["pacman", "-Qq"]
    elif mgr == "dnf":
        # Aynı adın farklı sürümleri (ör. kernel-devel) ayırt edilsin diye tam sürümle
        cmd = ["rpm", "-qa", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n"]
    elif mgr == "zypper":
        cmd = ["rpm", "-qa", "--qf", "%{NAME}\\n"]
    else:
        return None
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=dict(os.environ, LC_ALL="C"),
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None

    packages: Set[str] = set()
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if mgr in ("apt", "apt-get"):
            status, _, name = line.partition(" ")
            # Kaldırılmış (yalnızca ayar dosyası kalan) paketler kurulu sayılmaz
            if not name or status in ("not-installed", "config-files"):
                continue
            packages.add(name)
        elif not line.startswith("gpg-pubkey"):
            packages.add(line)
    return packages


def driver_install_command(pm: str, packages: List[str]) -> Tuple[List[str], str]:
    """Sürücü kurulum komutu. apt'de debconf soruları (ör. nouveau uyarısı) görünmeyen bir
    terminalde kurulumu kilitlemesin diye etkileşimsiz arayüz kullanılır."""
    cmd, desc = get_install_command(pm, packages)
    if cmd and (pm or "").lower() in ("apt", "apt-get") and cmd[0] == "sudo":
        cmd = ["sudo", "env", "DEBIAN_FRONTEND=noninteractive"] + cmd[1:]
    return cmd, desc


def removal_command(pm: str, packages: List[str]) -> List[str]:
    """Verilen paketleri (ve yalnızca onları) kaldıran komut."""
    valid = [p for p in packages if is_valid_package_name(p, manager=pm)]
    if not valid:
        return []
    mgr = (pm or "").lower()
    if mgr in ("apt", "apt-get"):
        return ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "purge", "-y", "--"] + valid
    if mgr in ("pacman", "yay", "paru"):
        return ["sudo", "pacman", "-Rn", "--noconfirm", "--"] + valid
    if mgr == "dnf":
        return ["sudo", "dnf", "remove", "-y", "--"] + valid
    if mgr == "zypper":
        return ["sudo", "zypper", "--non-interactive", "remove", "--"] + valid
    return []


@dataclass
class DriverPlan:
    """Sürücü kurulum planı ve kurulum öncesi denetim sonuçları."""
    family: str = "unknown"
    package_manager: str = ""
    plain_packages: List[str] = field(default_factory=list)       # mikro kod, mesa, ürün yazılımı: çekirdek modülü kurmaz
    module_packages: List[str] = field(default_factory=list)      # çekirdek modülü kuran, açık kaynak sürücüyü kapatan paketler
    module_groups: List[Tuple[str, ...]] = field(default_factory=list)  # her grup için doğrulanacak modül adları
    header_packages: List[str] = field(default_factory=list)      # eksik ve mutlaka kurulması gereken çekirdek başlıkları
    optional_packages: List[str] = field(default_factory=list)    # depoda varsa eklenen paketler (Debian başlık üst paketi)
    kernels: List[str] = field(default_factory=list)              # modülün bulunması gereken çekirdekler
    secure_boot: Optional[bool] = None
    already_present: bool = False
    blockers: List[str] = field(default_factory=list)             # modül kurulumunu engelleyen nedenler
    distro_tool: str = ""


def build_driver_plan(recommended: List[str], family: str, package_manager: str) -> DriverPlan:
    """Önerilen sürücü paketlerini ayırır ve çekirdek modülü kuranlar için kurulum öncesi
    denetimleri yapar (Secure Boot / lockdown, açılacak çekirdeklerin başlık paketleri).
    Depoda bulunma denetimi kurulumdan hemen önce restore motorunda yapılır."""
    pm = "pacman" if package_manager in ("yay", "paru") else package_manager
    plan = DriverPlan(family=family, package_manager=pm)
    for pkg in recommended or []:
        names = driver_module_names(pkg)
        if names:
            plan.module_packages.append(pkg)
            if names not in plan.module_groups:
                plan.module_groups.append(names)
        else:
            plan.plain_packages.append(pkg)
    if not plan.module_packages:
        return plan

    plan.kernels = kernels_to_cover()
    plan.secure_boot = secure_boot_enabled()
    plan.distro_tool = find_distro_driver_tool()

    if all(module_present(k, grp) for k in plan.kernels for grp in plan.module_groups):
        plan.already_present = True
        return plan

    if family not in _SUPPORTED_FAMILIES or not pm:
        plan.blockers.append(t("driver_block_family"))
        return plan

    blocked = unsigned_modules_blocked(plan.secure_boot)
    if blocked is True:
        plan.blockers.append(t("driver_block_secure_boot"))
    elif blocked is None:
        plan.blockers.append(t("driver_block_secure_boot_unknown"))

    if family in _HEADER_FAMILIES:
        for kver in plan.kernels:
            if headers_installed(kver):
                continue
            hp = header_package(family, kver)
            if not hp or not is_valid_package_name(hp, manager=pm):
                plan.blockers.append(t("driver_block_headers_unknown", kernel=kver))
            elif hp not in plan.header_packages:
                plan.header_packages.append(hp)

    if family == "debian":
        for kver in plan.kernels:
            meta = _debian_header_metapackage(kver)
            if meta and meta not in plan.optional_packages and is_valid_package_name(meta, manager=pm):
                plan.optional_packages.append(meta)

    return plan


def describe_driver_plan(plan: DriverPlan) -> List[str]:
    """Onay ekranında ve CLI'da gösterilecek plan satırları: kurulacak paketler, çalıştırılacak
    komut, çekirdekler, Secure Boot durumu, engeller ve riskler."""
    if plan.already_present:
        return [t("driver_status_already", kernels=", ".join(plan.kernels))]

    sb_key = {True: "driver_sb_on", False: "driver_sb_off"}.get(plan.secure_boot, "driver_sb_unknown")
    lines: List[str] = []
    if plan.blockers:
        lines.append(t("driver_plan_blocked", pkgs=", ".join(plan.module_packages)))
        lines.extend(f"• {reason}" for reason in plan.blockers)
    else:
        required = plan.module_packages + plan.header_packages
        cmd, _ = driver_install_command(plan.package_manager, required + plan.optional_packages)
        lines.append(t("driver_plan_packages", pkgs=", ".join(required)))
        if plan.optional_packages:
            lines.append(t("driver_plan_optional", pkgs=", ".join(plan.optional_packages)))
        if cmd:
            lines.append(t("driver_plan_command", cmd=" ".join(cmd)))
        lines.append(t("driver_plan_kernels", kernels=", ".join(plan.kernels)))
    lines.append(t("driver_plan_secure_boot", state=t(sb_key)))
    if plan.distro_tool:
        lines.append(t("driver_plan_distro_tool", tool=plan.distro_tool))
    if not plan.blockers:
        lines.append(t("driver_plan_risk"))
    return lines
