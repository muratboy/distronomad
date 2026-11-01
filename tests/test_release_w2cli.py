"""
DistroNomad Yayın Öncesi Testleri: W2-CLI Paketi.
Bulgular: C-4, C-10, C-5, B-3, Ev dışı yollar, Atlanan dosyalar (Exit Code 3), verify, FAT, sudo.
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
import pytest

from distronomad.cli import (
    build_parser,
    cmd_backup,
    cmd_append,
    cmd_restore,
    cmd_rollback,
    cmd_schedule,
    cmd_verify,
    cmd_diff,
    cmd_scan
)
from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    CustomPathItem,
    BackupManifest,
    RestoreOptions
)
from distronomad.backup import Fat32RiskAssessment, BackupEngine
from distronomad.verify import VerificationResult


def test_c4_rollback_missing_tx_log_exits_nonzero(tmp_path, capsys):
    """C-4: rollback işlem günlüğü bulunamadığında sıfır olmayan kodla (1) çıkmalı."""
    parser = build_parser()
    fake_tx = tmp_path / "nonexistent_tx.json"
    args = parser.parse_args(["rollback", "--tx", str(fake_tx)])

    with pytest.raises(SystemExit) as exc:
        cmd_rollback(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Geri alma işlemi için işlem kaydı (transaction log) bulunamadı" in out


def test_c4_rollback_errors_in_result_exits_nonzero(tmp_path, monkeypatch, capsys):
    """C-4: rollback sırasında dosya geri alma hataları oluştuğunda sıfır olmayan kodla (1) çıkmalı."""
    tx_file = tmp_path / "last_restore.json"
    tx_file.write_text(json.dumps({
        "timestamp": "20261101_120000",
        "target_home": str(tmp_path),
        "operations": []
    }), encoding="utf-8")

    monkeypatch.setattr(
        "distronomad.cli.rollback_restore",
        lambda transaction_file, target_home: {
            "reverted_files": [],
            "removed_created": [],
            "unrecoverable_files": [],
            "errors": ["Erişim engellendi: /etc/test.conf"],
            "warnings": []
        }
    )

    parser = build_parser()
    args = parser.parse_args(["rollback", "--tx", str(tx_file), "--target-home", str(tmp_path)])

    with pytest.raises(SystemExit) as exc:
        cmd_rollback(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Erişim engellendi: /etc/test.conf" in out


def test_c4_schedule_enable_disable_failure_exits_nonzero(monkeypatch, capsys):
    """C-4: schedule timer etkinleştirme veya kapatma başarısız olduğunda sıfır olmayan kodla (1) çıkmalı."""
    parser = build_parser()

    # 1. enable başarısızlığı
    monkeypatch.setattr("distronomad.cli.enable_timer", lambda **kwargs: {"success": False})
    args_enable = parser.parse_args(["schedule", "--enable"])
    with pytest.raises(SystemExit) as exc_enable:
        cmd_schedule(args_enable)
    assert exc_enable.value.code == 1

    # 2. disable başarısızlığı
    monkeypatch.setattr("distronomad.cli.disable_timer", lambda **kwargs: {"success": False, "message": "Durdurulamadı"})
    args_disable = parser.parse_args(["schedule", "--disable"])
    with pytest.raises(SystemExit) as exc_disable:
        cmd_schedule(args_disable)
    assert exc_disable.value.code == 1


def test_c10_backup_oserror_clean_exit(tmp_path, monkeypatch, capsys):
    """C-10: backup komutunda OSError fırlatıldığında traceback basılmadan temiz mesajla çıkılmalı."""
    out_archive = tmp_path / "backup.tar.zst"
    parser = build_parser()
    args = parser.parse_args(["backup", "--dest", str(out_archive)])

    def fake_run(self):
        raise OSError("[Errno 30] Salt okunur dosya sistemi: '/target'")

    monkeypatch.setattr(BackupEngine, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        cmd_backup(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "✖ HATA: [Errno 30] Salt okunur dosya sistemi" in out
    assert "Traceback" not in out


def test_c10_restore_clean_exit_on_value_error(tmp_path, monkeypatch, capsys):
    """C-10: restore komutunda ValueError durumunda temiz hata mesajıyla çıkılmalı."""
    fake_archive = tmp_path / "corrupt.tar.zst"
    fake_archive.write_bytes(b"invalid data")

    parser = build_parser()
    args = parser.parse_args(["restore", "--src", str(fake_archive)])

    def fake_load_manifest(self, **kwargs):
        raise ValueError("Geçersiz yedek arşivi: manifest.json ayrıştırılamadı")

    monkeypatch.setattr("distronomad.cli.RestoreEngine.load_manifest", fake_load_manifest)

    with pytest.raises(SystemExit) as exc:
        cmd_restore(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "✖ HATA: Geçersiz yedek arşivi" in out
    assert "Traceback" not in out


def test_c10_diff_runtime_error_clean_exit(tmp_path, monkeypatch, capsys):
    """C-10: diff komutunda RuntimeError durumunda temiz hata mesajıyla çıkılmalı."""
    fake_archive = tmp_path / "backup.tar.zst"
    fake_archive.write_bytes(b"dummy")

    parser = build_parser()
    args = parser.parse_args(["diff", "--src", str(fake_archive)])

    def fake_load_manifest(self, **kwargs):
        raise RuntimeError("Arşiv karşılaştırması sırasında zstd kütüphanesi çöktü")

    monkeypatch.setattr("distronomad.cli.RestoreEngine.load_manifest", fake_load_manifest)

    with pytest.raises(SystemExit) as exc:
        cmd_diff(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "✖ HATA: Arşiv karşılaştırması sırasında zstd kütüphanesi çöktü" in out


def test_c5_append_requires_at_least_one_selection(tmp_path, capsys):
    """C-5: append alt komutuna --path, --config veya --package verilmezse hata basıp örnek kullanım göstermeli."""
    parser = build_parser()
    args = parser.parse_args(["append", "--backup", str(tmp_path / "backup.tar.zst")])

    with pytest.raises(SystemExit) as exc:
        cmd_append(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "✖ HATA: Eklenecek en az bir --path, --config veya --package belirtilmelidir." in out
    assert "Örnek kullanım:" in out
    assert "--path" in out
    assert "--config" in out
    assert "--package" in out


def test_c5_append_missing_backup_target_exits_nonzero(tmp_path, capsys):
    """C-5: append alt komutunda hedef yedek dosyası mevcut değilse temiz hata basmalı."""
    parser = build_parser()
    missing_backup = tmp_path / "nonexistent.tar.zst"
    test_path = tmp_path / "file.txt"
    test_path.write_text("test")

    args = parser.parse_args(["append", "--backup", str(missing_backup), "--path", str(test_path)])

    with pytest.raises(SystemExit) as exc:
        cmd_append(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Güncellenecek yedek arşivi veya dizini bulunamadı" in out


def test_c5_append_selective_path_config_package(tmp_path, monkeypatch):
    """C-5: append alt komutu tüm sistemi eklememeli, sadece belirtilen path/config/package ögelerini eklemeli."""
    existing_backup = tmp_path / "existing_backup.tar.zst"
    existing_backup.write_bytes(b"dummy_backup")

    custom_file = tmp_path / "extra_file.txt"
    custom_file.write_text("my custom content")

    dummy_sys = SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    dummy_pkgs = [
        PackageItem(name="curl", manager="pacman", is_selected=True),
        PackageItem(name="neovim", manager="pacman", is_selected=True),
        PackageItem(name="htop", manager="pacman", is_selected=True)
    ]
    dummy_cfgs = [
        ConfigItem(id="cfg_nvim", name="Neovim", category="Editor", source_path=str(tmp_path / ".config/nvim"), rel_path=".config/nvim", is_selected=True),
        ConfigItem(id="cfg_bashrc", name=".bashrc", category="Shell", source_path=str(tmp_path / ".bashrc"), rel_path=".bashrc", is_selected=True)
    ]

    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_system_info", lambda self: dummy_sys)
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_installed_packages", lambda self: dummy_pkgs)
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_configs", lambda self: dummy_cfgs)

    captured_init = {}
    original_init = BackupEngine.__init__

    def mock_engine_init(self, *args, **kwargs):
        captured_init.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(BackupEngine, "__init__", mock_engine_init)
    monkeypatch.setattr(BackupEngine, "run", lambda self: existing_backup)

    parser = build_parser()
    args = parser.parse_args([
        "append",
        "--backup", str(existing_backup),
        "--path", str(custom_file),
        "--config", ".config/nvim",
        "--package", "pacman:neovim"
    ])

    cmd_append(args)

    assert "packages" in captured_init
    selected_pkg_names = [p.name for p in captured_init["packages"]]
    assert selected_pkg_names == ["neovim"]
    assert "curl" not in selected_pkg_names
    assert "htop" not in selected_pkg_names

    selected_cfg_ids = [c.id for c in captured_init["configs"]]
    assert selected_cfg_ids == ["cfg_nvim"]
    assert "cfg_bashrc" not in selected_cfg_ids

    selected_custom_paths = [cp.path for cp in captured_init["custom_paths"]]
    assert str(custom_file.resolve()) in selected_custom_paths


def test_c5_append_reports_unmatched_items(tmp_path, capsys, monkeypatch):
    """C-5: append eşleşmeyen konfigürasyon veya paket girdilerini kullanıcıya sarı uyarıyla raporlamalı."""
    existing_backup = tmp_path / "existing.tar.zst"
    existing_backup.write_bytes(b"dummy")

    dummy_sys = SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_system_info", lambda self: dummy_sys)
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_installed_packages", lambda self: [])
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_configs", lambda self: [])

    parser = build_parser()
    args = parser.parse_args([
        "append",
        "--backup", str(existing_backup),
        "--config", ".config/nonexistent_cfg",
        "--package", "pacman:unknown_pkg",
        "--path", str(tmp_path)
    ])

    monkeypatch.setattr(BackupEngine, "run", lambda self: existing_backup)

    cmd_append(args)
    out = capsys.readouterr().out
    assert "⚠ Eşleşmeyen konfigürasyon yolu: .config/nonexistent_cfg" in out
    assert "⚠ Eşleşmeyen paket adı: pacman:unknown_pkg" in out


def test_b3_rollback_target_home_support(tmp_path, monkeypatch):
    """B-3: rollback alt komutu --target-home parametresini desteklemeli ve rollback_restore'a geçirmeli."""
    custom_target = tmp_path / "target_home"
    tx_dir = custom_target / ".config" / "distronomad" / "transactions"
    tx_dir.mkdir(parents=True)
    tx_file = tx_dir / "last_restore.json"
    tx_file.write_text(json.dumps({
        "timestamp": "20261101_130000",
        "target_home": str(custom_target.resolve()),
        "operations": []
    }), encoding="utf-8")

    captured_call = {}

    def mock_rollback_restore(transaction_file=None, target_home=None):
        captured_call["transaction_file"] = transaction_file
        captured_call["target_home"] = target_home
        return {"reverted_files": ["a"], "removed_created": ["b"], "errors": []}

    monkeypatch.setattr("distronomad.cli.rollback_restore", mock_rollback_restore)

    parser = build_parser()
    args = parser.parse_args(["rollback", "--target-home", str(custom_target)])

    cmd_rollback(args)

    assert captured_call["transaction_file"] == tx_file
    assert captured_call["target_home"] == custom_target.resolve()


def test_b3_rollback_target_home_mismatch_aborts(tmp_path, capsys):
    """B-3: işlem günlüğündeki target_home ile verilen hedef uyuşmuyorsa rollback yapılmamalı ve hata verilmelidir."""
    custom_target = tmp_path / "target_home"
    tx_dir = custom_target / ".config" / "distronomad" / "transactions"
    tx_dir.mkdir(parents=True)
    tx_file = tx_dir / "last_restore.json"
    tx_file.write_text(json.dumps({
        "timestamp": "20261101_130000",
        "target_home": "/different/user/home",
        "operations": []
    }), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["rollback", "--target-home", str(custom_target)])

    with pytest.raises(SystemExit) as exc:
        cmd_rollback(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "hedef ev dizini (/different/user/home)" in out
    assert "uyuşmuyor" in out


def test_restore_allow_external_paths_flag_and_reporting(tmp_path, monkeypatch, capsys):
    """Ev dışı yollar: restore alt komutu --allow-external-paths ve takma adı --allow-root-paths bayrağını desteklemeli ve atlananları listelemelidir."""
    fake_backup = tmp_path / "backup.tar.zst"
    fake_backup.write_bytes(b"dummy")

    parser = build_parser()

    # 1. Bayrak kontrolü (--allow-external-paths)
    args1 = parser.parse_args(["restore", "--src", str(fake_backup), "--allow-external-paths"])
    assert args1.allow_external_paths is True

    # 2. Takma ad kontrolü (--allow-root-paths)
    args2 = parser.parse_args(["restore", "--src", str(fake_backup), "--allow-root-paths"])
    assert args2.allow_external_paths is True

    # 3. cmd_restore içinde RestoreOptions'a aktarılması ve sonuç raporlaması
    dummy_manifest = BackupManifest(
        created_at="2026-11-01",
        version="0.9.0",
        system_info=SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    )

    captured_opts = []

    def fake_execute(self, options, **kwargs):
        captured_opts.append(options)
        return {
            "restored_configs": [],
            "restored_files": [],
            "installed_packages": [],
            "failed_packages": [],
            "untranslated_packages": [],
            "skipped_external_paths": ["/etc/environment", "/opt/app/config.ini"]
        }

    monkeypatch.setattr("distronomad.cli.RestoreEngine.load_manifest", lambda self, **kwargs: dummy_manifest)
    monkeypatch.setattr("distronomad.cli.RestoreEngine.execute_restore", fake_execute)
    monkeypatch.setattr("distronomad.cli.RestoreEngine.cleanup", lambda self: None)

    cmd_restore(args1)

    assert len(captured_opts) == 1
    assert captured_opts[0].allow_external_paths is True
    assert captured_opts[0].allow_root_paths is True

    out = capsys.readouterr().out
    assert "Atlanan Ev Dışı Özel Yollar (2):" in out
    assert "/etc/environment" in out
    assert "/opt/app/config.ini" in out


def test_backup_and_append_exit_code_3_when_skipped_files(tmp_path, monkeypatch, capsys):
    """Atlanan dosyalar: backup/append tamamlandığında skipped_files varsa çıkış kodu 3 olmalıdır."""
    out_archive = tmp_path / "backup.tar.zst"
    out_archive.write_bytes(b"content")

    parser = build_parser()

    # 1. backup komutu
    def fake_run_with_skips(self):
        self.skipped_files = [
            {"path": "/home/user/.secret_key", "reason": "Permission denied"},
            {"path": "/home/user/broken_link", "reason": "Sembolik bağ hedefi yok"}
        ]
        return out_archive

    monkeypatch.setattr(BackupEngine, "run", fake_run_with_skips)

    args_backup = parser.parse_args(["backup", "--dest", str(out_archive)])
    with pytest.raises(SystemExit) as exc_b:
        cmd_backup(args_backup)
    assert exc_b.value.code == 3

    out_b = capsys.readouterr().out
    assert "2 dosya okunamadığı veya atlandığı için yedek eksik tamamlandı" in out_b
    assert "/home/user/.secret_key (Permission denied)" in out_b

    # 2. append komutu
    dummy_sys = SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_system_info", lambda self: dummy_sys)
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_installed_packages", lambda self: [])
    monkeypatch.setattr("distronomad.cli.SystemScanner.scan_configs", lambda self: [])

    args_append = parser.parse_args(["append", "--backup", str(out_archive), "--path", str(tmp_path)])
    with pytest.raises(SystemExit) as exc_a:
        cmd_append(args_append)
    assert exc_a.value.code == 3

    out_a = capsys.readouterr().out
    assert "2 dosya okunamadığı veya atlandığı için ekleme eksik tamamlandı" in out_a


def test_verify_reports_clear_reason_on_missing_chain(tmp_path, monkeypatch, capsys):
    """verify: tabanı eksik zincir veya bozuk yedek durumunda nedeni açıkça yazdırıp sıfır olmayan kodla çıkmalı."""
    sample_backup = tmp_path / "inc.tar.zst"
    sample_backup.write_bytes(b"dummy")

    fake_result = VerificationResult(
        archive_path=sample_backup,
        is_valid=False,
        error_message="Zincir eksik: Taban yedek bulunamadı ('full_v1.tar.zst').",
        corrupted=False
    )

    monkeypatch.setattr("distronomad.verify.verify_backup", lambda **kwargs: fake_result)

    parser = build_parser()
    args = parser.parse_args(["verify", "--src", str(sample_backup)])

    with pytest.raises(SystemExit) as exc:
        cmd_verify(args)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Yedek bütünlük doğrulaması BAŞARISIZ oldu" in out
    assert "Zincir eksik: Taban yedek" in out
    assert "full_v1.tar.zst" in out


def test_fat32_dir_unsupported_clear_message(tmp_path, monkeypatch, capsys):
    """FAT: fat32_dir_unsupported durumunda arşiv biçimini önerip --force seçeneğini bildirmeli."""
    out_dir = tmp_path / "backup_dir"
    parser = build_parser()

    risk = Fat32RiskAssessment(
        has_risk=True,
        reason="fat32_dir_unsupported",
        target_fs="vfat",
        message="Hedef FAT32 dosya sistemi sembolik bağları ve Linux izinlerini desteklemez; arşiv formatı (.tar.zst) önerilir."
    )
    monkeypatch.setattr("distronomad.cli.check_fat32_risk", lambda **kwargs: risk)

    # 1. force olmadan -> exit code 1
    args_no_force = parser.parse_args(["backup", "--dest", str(out_dir), "--format", "dir"])
    with pytest.raises(SystemExit) as exc:
        cmd_backup(args_no_force)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "FAT32 dosya sisteminde dizin formatı desteklenmez" in out
    assert "--format tar.zst" in out
    assert "--force ile devam edin" in out

    # 2. force ile -> devam eder
    args_force = parser.parse_args(["backup", "--dest", str(out_dir), "--format", "dir", "--force"])
    monkeypatch.setattr(BackupEngine, "run", lambda self: out_dir)
    cmd_backup(args_force)
    out_force = capsys.readouterr().out
    assert "--force bayrağı belirtildiği için işleme devam ediliyor" in out_force


def test_restore_sudo_precheck_guidance(tmp_path, monkeypatch, capsys):
    """sudo: yetki ön kontrolü veya izin hatası durumunda sudo -v veya --configs-only önerisi basılmalı."""
    fake_backup = tmp_path / "backup.tar.zst"
    fake_backup.write_bytes(b"dummy")

    dummy_manifest = BackupManifest(
        created_at="2026-11-01",
        version="0.9.0",
        system_info=SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    )

    def fake_execute(self, options, **kwargs):
        return {
            "restored_configs": [],
            "restored_files": [],
            "installed_packages": [],
            "failed_packages": ["pacman (yetki hatası: sudo -n başarısız)"],
            "untranslated_packages": [],
            "warnings": ["pacman kurulumu atlandı: Parolasız sudo yetkisi yok ve etkileşimli terminal bulunmuyor."]
        }

    monkeypatch.setattr("distronomad.cli.RestoreEngine.load_manifest", lambda self, **kwargs: dummy_manifest)
    monkeypatch.setattr("distronomad.cli.RestoreEngine.execute_restore", fake_execute)
    monkeypatch.setattr("distronomad.cli.RestoreEngine.cleanup", lambda self: None)

    parser = build_parser()
    args = parser.parse_args(["restore", "--src", str(fake_backup), "--dry-run"])

    cmd_restore(args)

    out = capsys.readouterr().out
    assert "Paket kurulumu için yetki sağlanamadı" in out
    assert "sudo -v" in out
    assert "--configs-only" in out


def test_help_epilog_documents_exit_codes():
    """--help epilog'unda çıkış kodları (0, 1, 2, 3) belgelenmiş olmalı."""
    import argparse
    parser = build_parser()
    help_text = parser.format_help()
    assert "Çıkış Kodları:" in help_text
    assert "3: Yedekleme tamamlandı ancak bazı dosyalar okunamadı / atlandı" in help_text

    subparsers_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    subparser_choices = subparsers_action.choices

    backup_help = subparser_choices["backup"].format_help()
    assert "3: Yedekleme tamamlandı ancak bazı dosyalar okunamadı / atlandı (eksik)" in backup_help

    append_help = subparser_choices["append"].format_help()
    assert "3: Ekleme tamamlandı ancak bazı dosyalar okunamadı / atlandı (eksik)" in append_help
