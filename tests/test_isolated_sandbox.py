"""
DistroNomad - Bubblewrap (bwrap) ile Tamamen Yalıtılmış Sanal Linux Ortamı Testi.
(Kullanıcının gerçek işletim sistemine zerre dokunmadan sanal konteyner içinde çalıştırılır)
"""

import os
import shutil
import tempfile
import subprocess
from pathlib import Path
import pytest

from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem
from distronomad.backup import BackupEngine


def test_standalone_restore_script_in_bwrap_sandbox():
    """
    Sanal ve yalıtılmış bwrap konteyneri içinde standalone restore.sh betiğinin çalışmasını doğrular.
    """
    if not shutil.which("bwrap"):
        pytest.skip("bwrap (bubblewrap) sistemi üzerinde kurulu değil.")

    with tempfile.TemporaryDirectory(prefix="bwrap_test_") as sandbox_root:
        root_path = Path(sandbox_root)
        source_dir = root_path / "source"
        target_dir = root_path / "target"
        backup_dir = root_path / "backup_archive"

        source_dir.mkdir(parents=True)
        target_dir.mkdir(parents=True)

        # Sanal kaynak verileri
        (source_dir / ".bashrc").write_text("export TEST_VAR=distronomad_success\n", encoding="utf-8")
        src_cfg = source_dir / ".config" / "testapp"
        src_cfg.mkdir(parents=True)
        (src_cfg / "settings.json").write_text('{"theme": "cyberpunk"}', encoding="utf-8")

        # Yedek oluştur
        sys_info = SystemInfo(distro_id="cachyos", distro_name="CachyOS", home_dir=str(source_dir))
        cfg_items = [
            ConfigItem(id="c1", name=".bashrc", category="Kabuk", source_path=str(source_dir / ".bashrc"), rel_path=".bashrc"),
            ConfigItem(id="c2", name="testapp", category="Uygulama", source_path=str(src_cfg), rel_path=".config/testapp", is_directory=True)
        ]
        pkg_items = [
            PackageItem(name="htop", manager="pacman")
        ]

        engine = BackupEngine(
            system_info=sys_info,
            packages=pkg_items,
            configs=cfg_items,
            custom_paths=[],
            output_path=backup_dir,
            archive_format="dir"
        )
        engine.run()

        restore_sh = backup_dir / "restore.sh"
        assert restore_sh.exists()

        # Sanal Bubblewrap konteyneri içinde restore.sh --all --target-home /home/target çalıştır
        # Ana sisteme hiçbir dosya yazılmaz, her şey sandbox_root içinde gerçekleşir.
        bwrap_cmd = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--bind", str(sandbox_root), "/sandbox",
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
            "bash",
            "/sandbox/backup_archive/restore.sh",
            "--all",
            "--target-home", "/sandbox/target"
        ]

        proc = subprocess.run(bwrap_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.returncode == 0, f"bwrap başarısız oldu: {proc.stderr}"

        # Konteyner çıkışında target dizinini doğrula
        restored_bashrc = target_dir / ".bashrc"
        assert restored_bashrc.exists()
        assert "distronomad_success" in restored_bashrc.read_text(encoding="utf-8")

        restored_cfg = target_dir / ".config" / "testapp" / "settings.json"
        assert restored_cfg.exists()
        assert "cyberpunk" in restored_cfg.read_text(encoding="utf-8")
