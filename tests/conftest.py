"""
DistroNomad - Test Yapılandırması ve Yalıtılmış Çalışma Ortamı (Test Fixtures & Sandbox Isolation).

R1 Kuralı:
- Testlerin gerçek ev dizinine (~), ~/.config, systemctl --user, VS Code ayarları,
  npm ve diğer sistem araçlarına doğrudan dokunmasını engeller.
- HOME ve XDG değişkenlerini her test için izole tmp_path altına yönlendirir.
- systemctl, sudo, pkexec, pacman, yay, paru, apt, apt-get, dpkg, dnf, rpm, zypper,
  flatpak, snap, npm, code, codium, dconf, gsettings gibi sistem komutlarını
  subprocess.run, Popen, check_output, check_call, call ve os.system yollarında engeller/sahteler.
- tar, openssl, zstd, bash gibi test araçlarını serbest bırakır.
"""

from __future__ import annotations
import io
import os
import subprocess
from pathlib import Path
from typing import Any
import pytest


FAKED_SYSTEM_COMMANDS = {
    "systemctl",
    "sudo",
    "pkexec",
    "pacman",
    "yay",
    "paru",
    "apt",
    "apt-get",
    "apt-cache",
    "apt-mark",
    "dpkg",
    "dnf",
    "rpm",
    "zypper",
    "flatpak",
    "snap",
    "npm",
    "code",
    "codium",
    "vscodium",
    "dconf",
    "gsettings",
    "crontab",
    "pipx",
    "cargo",
    "lspci",
    "pgrep",
}

# pacman -Sp için testlerin ayarlayabileceği kontrollü paket listesi
PACMAN_SP_VALID_PACKAGES: set[str] = {
    "bash", "git", "neovim", "htop", "curl", "pipewire", "nano", "vim", "bat", "cachyos-hello", "btop", "good-pkg"
}


def _extract_base_cmd(cmd: Any) -> str:
    """Komut dizisi veya dizesinden kök çalıştırılabilir adını ayıklar (sudo/pkexec desteğiyle)."""
    if isinstance(cmd, (list, tuple)):
        if not cmd:
            return ""
        raw_cmd = str(cmd[0])
        base = os.path.basename(raw_cmd)
        if base in ("sudo", "pkexec"):
            idx = 1
            while idx < len(cmd) and str(cmd[idx]).startswith("-"):
                idx += 1
            if idx < len(cmd):
                return os.path.basename(str(cmd[idx]))
            return base
        return base
    elif isinstance(cmd, str):
        parts = cmd.strip().split()
        if not parts:
            return ""
        base = os.path.basename(parts[0])
        if base in ("sudo", "pkexec"):
            idx = 1
            while idx < len(parts) and parts[idx].startswith("-"):
                idx += 1
            if idx < len(parts):
                return os.path.basename(parts[idx])
            return base
        return base
    return ""


def _get_fake_result(cmd: Any, **kwargs: Any) -> tuple[int, str, str]:
    """Sahte komutlar için (returncode, stdout, stderr) üretir."""
    base_cmd = _extract_base_cmd(cmd)
    cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    stdout = ""
    stderr = ""
    returncode = 0

    if base_cmd == "npm":
        if "list" in cmd_str:
            stdout = '{"dependencies": {}}'
    elif base_cmd == "systemctl":
        if "is-active" in cmd_str:
            stdout = "inactive\n"
            returncode = 3
        else:
            returncode = 0
    elif base_cmd in ("code", "codium", "vscodium"):
        stdout = ""
        returncode = 0
    elif base_cmd in ("pacman", "yay", "paru"):
        if "-Qet" in cmd_str:
            stdout = "bash 5.2.026-2\ngit 2.44.0-1\nneovim 0.10.0-1\nhtop 3.3.0-1\ncurl 8.6.0-1\n"
        elif "-Sp" in cmd_str:
            cand = None
            if isinstance(cmd, (list, tuple)):
                non_opts = [arg for arg in cmd[1:] if not str(arg).startswith("-")]
                if non_opts:
                    cand = str(non_opts[-1])
            elif isinstance(cmd, str):
                parts = [p for p in cmd.split() if not p.startswith("-")]
                if len(parts) > 1:
                    cand = parts[-1]

            env_allowed = os.environ.get("MOCK_PACMAN_SP_ALLOWED")
            allowed_set = set(env_allowed.split(",")) if env_allowed is not None else PACMAN_SP_VALID_PACKAGES

            if cand and cand in allowed_set:
                stdout = f"https://geo.mirror.pkgbuild.com/core/os/x86_64/{cand}-1.0-1-any.pkg.tar.zst\n"
                returncode = 0
            else:
                stderr = f"error: target not found: {cand}\n"
                returncode = 1
        else:
            stdout = ""
            returncode = 0
    elif base_cmd in ("apt", "apt-get", "apt-cache", "apt-mark"):
        if "showmanual" in cmd_str:
            stdout = "bash\ngit\nneovim\nhtop\ncurl\n"
        else:
            stdout = ""
        returncode = 0
    elif base_cmd == "dpkg":
        if "-l" in cmd_str or "--get-selections" in cmd_str:
            stdout = "bash\tinstall\n"
        else:
            stdout = ""
        returncode = 0
    elif base_cmd == "dnf":
        if "repoquery" in cmd_str:
            stdout = "bash 5.2.026-2\ngit 2.44.0-1\nneovim 0.10.0-1\nhtop 3.3.0-1\ncurl 8.6.0-1\n"
        else:
            stdout = ""
        returncode = 0
    elif base_cmd == "rpm":
        if "-qa" in cmd_str:
            stdout = "bash-5.2-1.x86_64\n"
        else:
            stdout = ""
        returncode = 0
    elif base_cmd == "flatpak":
        if "list" in cmd_str:
            stdout = "org.mozilla.firefox\tFirefox\t124.0\norg.videolan.VLC\tVLC\t3.0.20\n"
        else:
            stdout = ""
        returncode = 0
    elif base_cmd == "crontab":
        returncode = 1
        stderr = "no crontab for user"
    elif base_cmd == "lspci":
        stdout = (
            "00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 620 (rev 07)\n"
            "01:00.0 3D controller: NVIDIA Corporation GP108M [GeForce MX150] (rev a1)\n"
            "02:00.0 Network controller: Intel Corporation Wireless 8265 / 8275 (rev 78)\n"
        )
        returncode = 0
    elif base_cmd == "pgrep":
        if "pipewire" in cmd_str:
            stdout = "1001\n"
            returncode = 0
        elif "pulseaudio" in cmd_str:
            stdout = ""
            returncode = 1
        else:
            stdout = ""
            returncode = 1
    elif base_cmd in ("snap", "pipx", "cargo", "zypper", "dconf", "gsettings", "sudo", "pkexec"):
        stdout = ""
        returncode = 0

    return returncode, stdout, stderr


class FakePopen:
    """Sistem komutları için sahte subprocess.Popen nesnesi."""

    def __init__(self, cmd: Any, *args: Any, **kwargs: Any):
        self.args = cmd
        rc, out, err = _get_fake_result(cmd, **kwargs)
        self.returncode = rc
        is_text = kwargs.get("text", False) or kwargs.get("universal_newlines", False)
        self._is_text = is_text

        if is_text:
            self._stdout_data = out
            self._stderr_data = err
            self.stdout = io.StringIO(out) if kwargs.get("stdout") == subprocess.PIPE else None
            self.stderr = io.StringIO(err) if kwargs.get("stderr") == subprocess.PIPE else None
            self.stdin = io.StringIO() if kwargs.get("stdin") == subprocess.PIPE else None
        else:
            out_b = out.encode("utf-8") if isinstance(out, str) else out
            err_b = err.encode("utf-8") if isinstance(err, str) else err
            self._stdout_data = out_b
            self._stderr_data = err_b
            self.stdout = io.BytesIO(out_b) if kwargs.get("stdout") == subprocess.PIPE else None
            self.stderr = io.BytesIO(err_b) if kwargs.get("stderr") == subprocess.PIPE else None
            self.stdin = io.BytesIO() if kwargs.get("stdin") == subprocess.PIPE else None

    def communicate(self, input: Any = None, timeout: Any = None) -> tuple[Any, Any]:
        return self._stdout_data, self._stderr_data

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: Any = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def send_signal(self, sig: int) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.stdout:
            self.stdout.close()
        if self.stderr:
            self.stderr.close()
        if self.stdin:
            self.stdin.close()


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Tüm testler için otomatik çalışan izolasyon fixture'ı:
    1. HOME ve XDG değişkenlerini tmp_path içine yönlendirir.
    2. Sistem düzeyinde komutları sahteler (tar, openssl, zstd gibi araçlar hariç).
    """
    test_home = tmp_path / "isolated_home"
    xdg_config = test_home / ".config"
    xdg_data = test_home / ".local" / "share"
    xdg_cache = test_home / ".cache"
    xdg_state = test_home / ".local" / "state"

    for d in (test_home, xdg_config, xdg_data, xdg_cache, xdg_state):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))

    from distronomad import i18n
    monkeypatch.setattr(i18n, "SETTINGS_DIR", xdg_config / "distronomad")
    monkeypatch.setattr(i18n, "SETTINGS_FILE", xdg_config / "distronomad" / "settings.json")
    i18n.set_current_language("tr", persist=False)

    real_run = subprocess.run
    real_popen = subprocess.Popen
    real_check_output = subprocess.check_output
    real_check_call = subprocess.check_call
    real_call = subprocess.call
    real_os_system = os.system

    def fake_subprocess_run(cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        base_cmd = _extract_base_cmd(cmd)
        if base_cmd in FAKED_SYSTEM_COMMANDS:
            rc, stdout, stderr = _get_fake_result(cmd, **kwargs)
            is_text = kwargs.get("text", False) or kwargs.get("universal_newlines", False)
            if not is_text:
                stdout_bytes = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
                stderr_bytes = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
                return subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=stdout_bytes, stderr=stderr_bytes)
            return subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=stdout, stderr=stderr)
        return real_run(cmd, *args, **kwargs)

    def fake_subprocess_popen(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        base_cmd = _extract_base_cmd(cmd)
        if base_cmd in FAKED_SYSTEM_COMMANDS:
            return FakePopen(cmd, *args, **kwargs)
        return real_popen(cmd, *args, **kwargs)

    def fake_subprocess_check_output(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        base_cmd = _extract_base_cmd(cmd)
        if base_cmd in FAKED_SYSTEM_COMMANDS:
            rc, stdout, stderr = _get_fake_result(cmd, **kwargs)
            if rc != 0:
                raise subprocess.CalledProcessError(rc, cmd, output=stdout, stderr=stderr)
            is_text = kwargs.get("text", False) or kwargs.get("universal_newlines", False)
            return stdout if is_text else (stdout.encode("utf-8") if isinstance(stdout, str) else stdout)
        return real_check_output(cmd, *args, **kwargs)

    def fake_subprocess_check_call(cmd: Any, *args: Any, **kwargs: Any) -> int:
        base_cmd = _extract_base_cmd(cmd)
        if base_cmd in FAKED_SYSTEM_COMMANDS:
            rc, stdout, stderr = _get_fake_result(cmd, **kwargs)
            if rc != 0:
                raise subprocess.CalledProcessError(rc, cmd, output=stdout, stderr=stderr)
            return 0
        return real_check_call(cmd, *args, **kwargs)

    def fake_subprocess_call(cmd: Any, *args: Any, **kwargs: Any) -> int:
        base_cmd = _extract_base_cmd(cmd)
        if base_cmd in FAKED_SYSTEM_COMMANDS:
            rc, _, _ = _get_fake_result(cmd, **kwargs)
            return rc
        return real_call(cmd, *args, **kwargs)

    def fake_os_system(cmd: str) -> int:
        base_cmd = _extract_base_cmd(cmd)
        if base_cmd in FAKED_SYSTEM_COMMANDS:
            rc, _, _ = _get_fake_result(cmd)
            return rc
        return real_os_system(cmd)

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(subprocess, "Popen", fake_subprocess_popen)
    monkeypatch.setattr(subprocess, "check_output", fake_subprocess_check_output)
    monkeypatch.setattr(subprocess, "check_call", fake_subprocess_check_call)
    monkeypatch.setattr(subprocess, "call", fake_subprocess_call)
    monkeypatch.setattr(os, "system", fake_os_system)
