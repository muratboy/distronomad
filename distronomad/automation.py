"""
DistroNomad - Zamanlanmış ve Otomatik Yedekleme Motoru (systemd User Timer & Automation).
Kullanıcının tercihine göre günlük veya haftalık artımlı yedeklemeleri otomatikleştirir.
ÖNEMLİ: Varsayılan olarak KAPALIDIR (Opt-in).
"""

from __future__ import annotations
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from distronomad.i18n import t


SERVICE_TEMPLATE = """[Unit]
Description={description}
Documentation=https://github.com/muratboy/distronomad
After=default.target

[Service]
Type=oneshot
ExecStart={exec_cmd}
StandardOutput=journal
StandardError=journal
"""

TIMER_TEMPLATE = """[Unit]
Description={description}
PartOf=distronomad-backup.service

[Timer]
OnCalendar={calendar_spec}
Persistent=true

[Install]
WantedBy=timers.target
"""

CALENDAR_MAP = {
    "hourly": "hourly",
    "daily": "daily",
    "weekly": "weekly",
    "monthly": "monthly"
}


def get_user_systemd_dir() -> Path:
    """Kullanıcı systemd birim dosya dizinini döndürür (~/.config/systemd/user)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "systemd" / "user"
    home = Path(os.environ.get("HOME") or Path.home())
    return home / ".config" / "systemd" / "user"


def get_timer_status(systemd_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    systemd user timer'ın kurulu ve aktif olup olmadığını kontrol eder.
    Varsayılan: Kapalı (active: False)
    """
    s_dir = systemd_dir or get_user_systemd_dir()
    timer_path = s_dir / "distronomad-backup.timer"
    service_path = s_dir / "distronomad-backup.service"

    installed = timer_path.exists() and service_path.exists()
    is_active = False
    interval = t("timer_interval_disabled")

    if installed:
        # Timer dosyasından OnCalendar ayarını oku
        try:
            with open(timer_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("OnCalendar="):
                        interval = line.strip().split("=", 1)[1]
                        break
        except Exception:
            pass

        # systemctl ile aktiflik kontrolü
        if shutil.which("systemctl"):
            try:
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", timer_path.name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=3
                )
                is_active = (res.returncode == 0 and res.stdout.strip() == "active")
            except Exception:
                pass

    return {
        "installed": installed,
        "is_active": is_active,
        "interval": interval,
        "timer_path": str(timer_path) if installed else None,
        "service_path": str(service_path) if installed else None
    }


def _systemd_quote_arg(arg: Any) -> str:
    """systemd ExecStart kurallarına göre argümanı tırnaklar ve % karakterini %% olarak kaçırır."""
    s = str(arg).replace("%", "%%")
    if any(c in s for c in " \t\"'\n"):
        import shlex
        return shlex.quote(s)
    return s


def enable_timer(
    interval: str = "daily",
    backup_output_dir: Optional[Path] = None,
    systemd_dir: Optional[Path] = None,
    incremental: bool = False
) -> Dict[str, Any]:
    """
    systemd user timer ve service birimlerini oluşturup etkinleştirir.
    interval: 'hourly', 'daily', 'weekly', 'monthly'
    incremental: Artımlı yedek alınıp alınmayacağını belirtir
    """
    calendar_spec = CALENDAR_MAP.get(interval.lower(), "daily")

    # Taşınabilir (PyInstaller) sürümde sys.executable USB'deki ikili dosyadır: '-m distronomad.cli' ile
    # çağrılamaz ve USB çıkarıldığında yol kaybolur; bozuk bir birim yazmak yerine kurulumu reddet.
    if getattr(sys, "frozen", False):
        return {
            "success": False,
            "systemctl_enabled": False,
            "message": t("automation_err_portable_unsupported"),
            "interval": interval,
            "calendar_spec": calendar_spec,
            "service_path": "",
            "timer_path": "",
            "output_dir": ""
        }

    s_dir = systemd_dir or get_user_systemd_dir()
    s_dir.mkdir(parents=True, exist_ok=True)

    home_path = Path(os.environ.get("HOME") or Path.home())
    out_dir = Path(backup_output_dir or (home_path / "Yedekler" / "distronomad_otomatik"))
    py_exec = sys.executable

    quoted_py = _systemd_quote_arg(py_exec)
    quoted_out = _systemd_quote_arg(out_dir)

    # ExecStart komutu: distronomad modülünü çalıştır
    if incremental:
        exec_cmd = f"{quoted_py} -m distronomad.cli backup --dest {quoted_out} --incremental"
        service_desc = t("automation_service_desc_inc")
        timer_desc = t("automation_timer_desc_inc")
    else:
        exec_cmd = f"{quoted_py} -m distronomad.cli backup --dest {quoted_out}"
        service_desc = t("automation_service_desc_full")
        timer_desc = t("automation_timer_desc_full")

    service_content = SERVICE_TEMPLATE.format(description=service_desc, exec_cmd=exec_cmd)
    timer_content = TIMER_TEMPLATE.format(description=timer_desc, calendar_spec=calendar_spec)

    service_path = s_dir / "distronomad-backup.service"
    timer_path = s_dir / "distronomad-backup.timer"

    with open(service_path, "w", encoding="utf-8") as f:
        f.write(service_content)

    with open(timer_path, "w", encoding="utf-8") as f:
        f.write(timer_content)


    # systemctl reload ve enable
    systemctl_success = False
    if shutil.which("systemctl"):
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=5)
            res = subprocess.run(
                ["systemctl", "--user", "enable", "--now", "distronomad-backup.timer"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )
            systemctl_success = (res.returncode == 0)
        except Exception:
            pass

    return {
        "success": bool(service_path.exists() and timer_path.exists() and (systemctl_success if shutil.which("systemctl") and systemd_dir is None else True)),
        "systemctl_enabled": systemctl_success if shutil.which("systemctl") else False,
        "interval": interval,
        "calendar_spec": calendar_spec,
        "service_path": str(service_path),
        "timer_path": str(timer_path),
        "output_dir": str(out_dir)
    }


def disable_timer(systemd_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    systemd user timer'ı durdurur ve devre dışı bırakır.
    """
    s_dir = systemd_dir or get_user_systemd_dir()
    timer_path = s_dir / "distronomad-backup.timer"
    service_path = s_dir / "distronomad-backup.service"


    if shutil.which("systemctl"):
        try:
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "distronomad-backup.timer"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5
            )
        except Exception:
            pass

    timer_path.unlink(missing_ok=True)
    service_path.unlink(missing_ok=True)

    if shutil.which("systemctl"):
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=5)
        except Exception:
            pass

    return {
        "success": True,
        "message": t("automation_disabled_success")
    }
