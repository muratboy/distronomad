"""
DistroNomad - W2-PKG Yayın Öncesi Paketleme ve Depo Dosyaları Doğrulama Testleri.
"""

from __future__ import annotations
import os
import re
import subprocess
import tarfile
from pathlib import Path
import pytest

REPO_DIR = Path(__file__).resolve().parent.parent


def test_sdist_manifest_contains_conftest_and_assets(tmp_path):
    """MANIFEST.in dosyasının conftest.py, tests ve UI varlıklarını kapsadığını doğrular."""
    manifest_file = REPO_DIR / "MANIFEST.in"
    assert manifest_file.exists(), "MANIFEST.in dosyası mevcut olmalıdır."

    content = manifest_file.read_text(encoding="utf-8")
    assert "include tests/conftest.py" in content
    assert "recursive-include tests *.py" in content
    assert "styles.tcss" in content
    assert "package_map.json" in content or "*.json" in content


def test_pyproject_metadata_and_optional_dependencies():
    """pyproject.toml dosyasındaki standart üstverileri, test bağımlılıklarını ve pythonpath'i doğrular."""
    pyproject_file = REPO_DIR / "pyproject.toml"
    assert pyproject_file.exists()

    content = pyproject_file.read_text(encoding="utf-8")

    # Üstveriler
    assert 'name = "Murat Boy"' in content
    assert 'license = "MIT"' in content
    assert 'license-files = ["LICENSE"]' in content
    assert "License :: OSI Approved :: MIT License" not in content
    assert 'requires = ["setuptools>=77"]' in content
    assert "Development Status :: 4 - Beta" in content
    assert "Topic :: System :: Archiving :: Backup" in content
    assert "Programming Language :: Python :: 3.9" in content
    assert "Programming Language :: Python :: 3.14" in content

    # Test bağımlılıkları ve pytest yapılandırması
    assert "[project.optional-dependencies]" in content
    assert "pytest>=" in content
    assert "pytest-asyncio>=" in content
    assert 'pythonpath = ["."]' in content

    # Depo adresleri yayın deposunu göstermelidir
    assert "[project.urls]" in content
    assert 'Homepage = "https://github.com/muratboy/distronomad"' in content
    assert 'Issues = "https://github.com/muratboy/distronomad/issues"' in content


def test_run_sh_pythonpath_expansion_and_syntax():
    """run.sh içindeki güvenli PYTHONPATH genişletmesini ve kabuk sözdizimini doğrular."""
    run_sh = REPO_DIR / "run.sh"
    assert run_sh.exists()

    content = run_sh.read_text(encoding="utf-8")
    # Güvensiz ":" eklemesi kaldırılmış olmalı
    assert 'export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"' not in content
    # Güvenli koşullu genişletme
    assert 'export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"' in content

    # Bash sözdizimi denetimi
    res = subprocess.run(["bash", "-n", str(run_sh)], capture_output=True, text=True)
    assert res.returncode == 0, f"run.sh sözdizim hatası: {res.stderr}"


def test_build_portable_security_and_flags():
    """build_portable.sh içindeki güvenlik kontrollerini, bağımlılık sabitlemelerini ve sözdizimini doğrular."""
    script = REPO_DIR / "scripts" / "build_portable.sh"
    assert script.exists()

    content = script.read_text(encoding="utf-8")

    # P0-3: /tmp/test_py_standalone güvensiz bloğu kaldırılmış olmalı
    assert "/tmp/test_py_standalone" not in content

    # P0-2: SHA-256 sağlama toplamı kontrolü ve boşken reddetme
    assert "PYTHON_STANDALONE_SHA256" in content
    assert 'if [ -z "${PYTHON_STANDALONE_SHA256:-}" ]; then' in content
    assert "sha256sum -c" in content

    # P2-4: Bağımlılıklar sabit sürümlerle kurulmalı
    assert 'textual==8.2.8' in content
    assert 'rich==15.0.0' in content
    assert 'zstandard==0.25.0' in content
    assert 'pyinstaller==6.22.3' in content

    # Bash sözdizimi denetimi
    res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert res.returncode == 0, f"build_portable.sh sözdizim hatası: {res.stderr}"


def test_portable_doc_cli_examples_valid_argparse():
    """build_portable.sh'in ürettiği dokümandaki komut örneklerinin CLI argparse ile uyumlu olduğunu doğrular."""
    from distronomad.cli import build_parser
    parser = build_parser()

    script = REPO_DIR / "scripts" / "build_portable.sh"
    content = script.read_text(encoding="utf-8")

    # BENIOKU.txt ve README.txt örnekleri:
    # ./run.sh backup --dest ...
    # ./run.sh restore --src ...
    # ./run.sh diff --src ...
    assert "./run.sh backup --dest /media/usb/yedek.tar.zst" in content
    assert "./run.sh restore --src /media/usb/yedek.tar.zst" in content
    assert "./run.sh diff --src /media/usb/yedek.tar.zst" in content

    assert "./run.sh backup --dest /media/usb/backup.tar.zst" in content
    assert "./run.sh restore --src /media/usb/backup.tar.zst" in content
    assert "./run.sh diff --src /media/usb/backup.tar.zst" in content

    # argparse çözümleme testleri
    args_backup = parser.parse_args(["backup", "--dest", "/tmp/yedek.tar.zst"])
    assert args_backup.dest == "/tmp/yedek.tar.zst"

    args_restore = parser.parse_args(["restore", "--src", "/tmp/yedek.tar.zst"])
    assert args_restore.src == "/tmp/yedek.tar.zst"

    args_diff = parser.parse_args(["diff", "--src", "/tmp/yedek.tar.zst"])
    assert args_diff.src == "/tmp/yedek.tar.zst"


def test_conftest_fakes_lspci_and_pgrep():
    """conftest.py'nin lspci ve pgrep komutlarını sahteleyip gerçek sisteme dokunmadığını doğrular."""
    from tests.conftest import FAKED_SYSTEM_COMMANDS
    assert "lspci" in FAKED_SYSTEM_COMMANDS
    assert "pgrep" in FAKED_SYSTEM_COMMANDS

    # lspci çağrısı
    res_lspci = subprocess.run(["lspci"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert res_lspci.returncode == 0
    assert "VGA compatible controller" in res_lspci.stdout

    # pgrep çağrısı
    res_pgrep_pw = subprocess.run(["pgrep", "-x", "pipewire"], stdout=subprocess.PIPE, text=True)
    assert res_pgrep_pw.returncode == 0

    res_pgrep_other = subprocess.run(["pgrep", "-x", "nonexistent_proc_xyz"], stdout=subprocess.PIPE, text=True)
    assert res_pgrep_other.returncode == 1


def test_gitignore_standard_entries():
    """.gitignore dosyasında standart Python ve işletim sistemi kalıntılarının bulunduğunu doğrular."""
    gitignore = REPO_DIR / ".gitignore"
    assert gitignore.exists()

    content = gitignore.read_text(encoding="utf-8")
    expected_entries = [
        ".coverage",
        "htmlcov/",
        ".mypy_cache/",
        ".ruff_cache/",
        "*.whl",
        "*.egg",
        ".DS_Store",
        "*~",
        "*.bak",
    ]
    for entry in expected_entries:
        assert entry in content, f".gitignore içinde {entry} eksik!"


def test_community_files_and_github_actions_workflow():
    """SECURITY.md, CONTRIBUTING.md, CHANGELOG.md ve .github/workflows/tests.yml dosyalarını doğrular."""
    # SECURITY.md
    sec_file = REPO_DIR / "SECURITY.md"
    assert sec_file.exists()
    sec_content = sec_file.read_text(encoding="utf-8")
    assert "Private Vulnerability Reporting" in sec_content
    # e-posta yazılmamalı
    assert "@" not in sec_content

    # CONTRIBUTING.md
    contrib_file = REPO_DIR / "CONTRIBUTING.md"
    assert contrib_file.exists()
    contrib_content = contrib_file.read_text(encoding="utf-8")
    assert 'pip install -e ".[test]"' in contrib_content or "pip install -e '.[test]'" in contrib_content
    assert "conftest.py" in contrib_content
    assert "bwrap" in contrib_content

    # CHANGELOG.md
    change_file = REPO_DIR / "CHANGELOG.md"
    assert change_file.exists()
    change_content = change_file.read_text(encoding="utf-8")
    assert "0.9.0" in change_content

    # .github/workflows/tests.yml
    workflow_file = REPO_DIR / ".github" / "workflows" / "tests.yml"
    assert workflow_file.exists()
    wf_content = workflow_file.read_text(encoding="utf-8")
    assert "ubuntu-latest" in wf_content
    assert '"3.9"' in wf_content
    assert '"3.14"' in wf_content
    assert 'pip install -e ".[test]"' in wf_content
    assert "pytest -q" in wf_content
