"""
[A-4] Paket tarayıcılarında loglama ve CLI/TUI log yapılandırma testleri.
"""

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import distronomad
from distronomad.cli import main, setup_cli_logging
from distronomad.discovery import SystemScanner


def test_distronomad_package_logger_has_null_handler():
    """distronomad kök paket logger'ında varsayılan olarak NullHandler bulunduğunu doğrular."""
    pkg_logger = logging.getLogger("distronomad")
    null_handlers = [h for h in pkg_logger.handlers if isinstance(h, logging.NullHandler)]
    assert len(null_handlers) >= 1, "distronomad logger'ında NullHandler bulunmalı"


def test_setup_cli_logging_attaches_warning_handler_to_stderr(monkeypatch):
    """setup_cli_logging çağrısının distronomad logger'ına WARNING seviyesinde stderr yönlendirdiğini doğrular."""
    pkg_logger = logging.getLogger("distronomad")
    # Mevcut stderr handler'ları temizle ve test et
    orig_handlers = list(pkg_logger.handlers)
    try:
        pkg_logger.handlers = [h for h in orig_handlers if not isinstance(h, logging.StreamHandler)]
        setup_cli_logging()
        stderr_handlers = [
            h for h in pkg_logger.handlers
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        ]
        assert len(stderr_handlers) == 1
        assert stderr_handlers[0].level == logging.WARNING
        assert pkg_logger.level == logging.WARNING

        # İkinci kez çağrıldığında yinelenen handler eklenmemeli
        setup_cli_logging()
        stderr_handlers2 = [
            h for h in pkg_logger.handlers
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        ]
        assert len(stderr_handlers2) == 1
    finally:
        pkg_logger.handlers = orig_handlers


def test_main_cli_subcommand_triggers_logging_setup(monkeypatch):
    """TUI dışındaki alt komutlar için logging'in kurulduğunu, TUI için kurulmadığını doğrular."""
    called = []
    monkeypatch.setattr("distronomad.cli.setup_cli_logging", lambda: called.append("setup"))
    monkeypatch.setattr("distronomad.cli.cmd_scan", lambda args: None)
    monkeypatch.setattr("distronomad.cli.run_tui", lambda **kwargs: None)

    # 1. 'scan' alt komutu -> setup çağrılmalı
    monkeypatch.setattr(sys, "argv", ["distronomad", "scan"])
    called.clear()
    main()
    assert "setup" in called, "CLI 'scan' alt komutunda setup_cli_logging çağrılmalı"

    # 2. 'tui' alt komutu -> setup çağrılmamalı
    monkeypatch.setattr(sys, "argv", ["distronomad", "tui"])
    called.clear()
    main()
    assert "setup" not in called, "CLI 'tui' komutunda setup_cli_logging çağrılmamalı"

    # 3. Varsayılan (argümansız) -> setup çağrılmamalı
    monkeypatch.setattr(sys, "argv", ["distronomad"])
    called.clear()
    main()
    assert "setup" not in called, "Varsayılan TUI başlatmasında setup_cli_logging çağrılmamalı"


def test_discovery_scanner_logs_and_returns_empty_on_errors(caplog):
    """
    discovery.py paket tarayıcılarının (A-4):
    - Zaman aşımında (TimeoutExpired) WARNING seviyesinde,
    - Beklenmeyen istisnalarda WARNING seviyesinde,
    - Komut bulunamadığında (FileNotFoundError) DEBUG seviyesinde,
    - Sıfırdan farklı çıkış kodunda DEBUG seviyesinde
    paket yöneticisi adıyla log kaydettiğini ve boş liste döndürdüğünü doğrular.
    """
    scanner = SystemScanner()
    caplog.set_level(logging.DEBUG, logger="distronomad.discovery")

    # 1. TimeoutExpired -> WARNING ("pacman" adıyla)
    caplog.clear()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["pacman"], timeout=15)):
        res = scanner._scan_pacman()
        assert res == []
        assert any(r.levelno == logging.WARNING and "pacman" in r.message and "zaman aşımı" in r.message for r in caplog.records)

    # 2. FileNotFoundError -> DEBUG ("apt" adıyla)
    caplog.clear()
    with patch("subprocess.run", side_effect=FileNotFoundError("No apt-mark")):
        res = scanner._scan_apt()
        assert res == []
        assert any(r.levelno == logging.DEBUG and "apt" in r.message and "bulunamadı" in r.message for r in caplog.records)

    # 3. Non-zero returncode -> DEBUG ("dnf" adıyla)
    caplog.clear()
    mock_proc = MagicMock(returncode=1, stderr="No repodata found")
    with patch("subprocess.run", return_value=mock_proc):
        res = scanner._scan_dnf()
        assert res == []
        assert any(r.levelno == logging.DEBUG and "dnf" in r.message and "sıfırdan farklı" in r.message for r in caplog.records)

    # 4. Beklenmeyen genel istisna -> WARNING ("flatpak" adıyla)
    caplog.clear()
    with patch("subprocess.run", side_effect=RuntimeError("Beklenmeyen d-bus çökmesi")):
        res = scanner._scan_flatpak()
        assert res == []
        assert any(r.levelno == logging.WARNING and "flatpak" in r.message and "beklenmeyen hata" in r.message for r in caplog.records)
