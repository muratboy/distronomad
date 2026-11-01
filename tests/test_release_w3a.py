"""
DistroNomad - Yayın Öncesi W3a Testleri: CLI i18n & Dil Sistemi.
Kapsam:
  - CLI kullanıcıya dönük tüm metinlerin i18n.t() üzerinden TR ve EN olması
  - --lang parametresinin alt komuttan önce ve sonra kabul edilmesi ve kalıcı ayara yazılmaması
  - Varsayılan dil tespit kuralı (kayıtlı ayar -> LC_ALL/LC_MESSAGES/LANG -> C/POSIX/vb. için en)
  - conftest.py izolasyonu ve test dil sabitlemesi
  - TR ve EN anahtar ve yer tutucu ({ad}) eşliği
"""

from __future__ import annotations
import io
import json
import os
import string
import sys
import argparse
from pathlib import Path
import pytest

from distronomad import __version__, __app_name__
from distronomad.i18n import (
    TRANSLATIONS,
    t,
    get_current_language,
    set_current_language,
    detect_default_language,
    normalize_language,
    load_saved_settings,
)
import distronomad.i18n as i18n_module
from distronomad.cli import (
    build_parser,
    preparse_language,
    cmd_scan,
    cmd_backup,
    cmd_append,
    cmd_restore,
    cmd_rollback,
    cmd_diff,
    cmd_schedule,
    cmd_verify,
    main,
)
from distronomad.models import SystemInfo


# ---------------------------------------------------------------------------
# 1. TR ve EN Anahtar ve Yer Tutucu ({yer_tutucu}) Eşliği
# ---------------------------------------------------------------------------
def test_i18n_keys_and_placeholders_parity():
    """Tüm çeviri anahtarlarının TR ve EN karşılıklarının ve yer tutucularının eksiksiz eşleştiğini doğrular."""
    formatter = string.Formatter()
    assert len(TRANSLATIONS) >= 300, f"Yetersiz anahtar sayısı: {len(TRANSLATIONS)}"

    missing_tr = []
    missing_en = []
    placeholder_mismatches = []

    for key, entry in TRANSLATIONS.items():
        if "tr" not in entry or not entry["tr"]:
            missing_tr.append(key)
        if "en" not in entry or not entry["en"]:
            missing_en.append(key)

        tr_val = entry.get("tr", "")
        en_val = entry.get("en", "")

        tr_fields = {field for _, field, _, _ in formatter.parse(tr_val) if field is not None}
        en_fields = {field for _, field, _, _ in formatter.parse(en_val) if field is not None}

        if tr_fields != en_fields:
            placeholder_mismatches.append((key, tr_fields, en_fields))

    assert not missing_tr, f"TR çevirisi eksik anahtarlar: {missing_tr}"
    assert not missing_en, f"EN çevirisi eksik anahtarlar: {missing_en}"
    assert not placeholder_mismatches, f"Yer tutucu uyuşmazlığı olan anahtarlar: {placeholder_mismatches}"


# ---------------------------------------------------------------------------
# 2. Varsayılan Dil Tespit Kuralı
# ---------------------------------------------------------------------------
def test_detect_default_language_rules(tmp_path, monkeypatch):
    """Varsayılan dil tespit kurallarını izole ortamda test eder:
    - Kayıtlı ayar varsa o kullanılır.
    - Yoksa LC_ALL -> LC_MESSAGES -> LANG önceliğine göre 'tr' ile başlayanlar 'tr',
      C, POSIX, de_DE, en_US vb. aksi tüm durumlar 'en' döner.
    """
    settings_dir = tmp_path / "cfg"
    settings_file = settings_dir / "settings.json"
    settings_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(i18n_module, "SETTINGS_DIR", settings_dir)
    monkeypatch.setattr(i18n_module, "SETTINGS_FILE", settings_file)

    # Ortam değişkenlerini temizle
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)

    # 1. Ortamda hiçbir değişken yokken varsayılan 'en'
    assert detect_default_language() == "en"

    # 2. C ve POSIX ortamında 'en' (GitHub Actions simülasyonu)
    monkeypatch.setenv("LANG", "C.UTF-8")
    assert detect_default_language() == "en"

    monkeypatch.setenv("LANG", "POSIX")
    assert detect_default_language() == "en"

    # 3. Almanca vb. üçüncü dillerde 'en'
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert detect_default_language() == "en"

    # 4. Türkçe ortamında 'tr'
    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    assert detect_default_language() == "tr"

    monkeypatch.setenv("LANG", "tr")
    assert detect_default_language() == "tr"

    # 5. LC_ALL önceliği (LC_ALL=C iken LANG=tr_TR ise 'en')
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    assert detect_default_language() == "en"

    # 6. LC_ALL=tr_TR iken LANG=en_US ise 'tr'
    monkeypatch.setenv("LC_ALL", "tr_TR.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert detect_default_language() == "tr"
    monkeypatch.delenv("LC_ALL", raising=False)

    # 7. LC_MESSAGES önceliği
    monkeypatch.setenv("LC_MESSAGES", "tr_TR.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert detect_default_language() == "tr"

    monkeypatch.setenv("LC_MESSAGES", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    assert detect_default_language() == "en"
    monkeypatch.delenv("LC_MESSAGES", raising=False)

    # 8. Kayıtlı ayar önceliği (ortam değişkenini ezer)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    settings_file.write_text(json.dumps({"language": "tr"}), encoding="utf-8")
    assert detect_default_language() == "tr"

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    settings_file.write_text(json.dumps({"language": "en"}), encoding="utf-8")
    assert detect_default_language() == "en"


# ---------------------------------------------------------------------------
# 3. --lang Ön-Okuma (Preparse) ve Kalıcı Ayara Yazmama Güvencesi
# ---------------------------------------------------------------------------
def test_preparse_language_argv():
    """sys.argv'den --lang parametresinin doğru ön-okunduğunu test eder."""
    assert preparse_language(["--lang", "en", "scan"]) == "en"
    assert preparse_language(["scan", "--lang", "en"]) == "en"
    assert preparse_language(["--lang=en", "backup"]) == "en"
    assert preparse_language(["backup", "--lang=tr"]) == "tr"
    assert preparse_language(["--lang", "TR", "verify"]) == "tr"
    assert preparse_language(["--lang=EN", "diff"]) == "en"
    assert preparse_language(["scan", "--home", "/tmp"]) is None
    assert preparse_language([]) is None


def test_cli_lang_does_not_persist_to_settings(tmp_path, monkeypatch):
    """CLI üzerinden --lang kullanımının settings.json dosyasına yazılmadığını garanti eder."""
    settings_dir = tmp_path / "cfg"
    settings_file = settings_dir / "settings.json"
    settings_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(i18n_module, "SETTINGS_DIR", settings_dir)
    monkeypatch.setattr(i18n_module, "SETTINGS_FILE", settings_file)

    # Başlangıçta ayar dosyası yok veya boş
    assert not settings_file.exists() or load_saved_settings() == {}

    # CLI main() simülasyonu: --lang en ile çağrı (--help SystemExit fırlatır)
    with pytest.raises(SystemExit):
        main(["--lang", "en", "scan", "--help"])

    # settings.json dosyasına 'language' kaydedilmemiş olmalı
    saved = load_saved_settings()
    assert "language" not in saved


# ---------------------------------------------------------------------------
# 4. --lang'ın Alt Komuttan Önce ve Sonra Kabul Edilmesi
# ---------------------------------------------------------------------------
def test_lang_flag_before_and_after_subcommands():
    """--lang seçeneğinin alt komuttan hem önce hem sonra çalıştığını doğrular."""
    parser = build_parser()

    # 1. Alt komuttan önce
    args1 = parser.parse_args(["--lang", "en", "scan"])
    assert args1.command == "scan"
    assert args1.lang == "en"

    # 2. Alt komuttan sonra
    args2 = parser.parse_args(["scan", "--lang", "en"])
    assert args2.command == "scan"
    assert args2.lang == "en"

    # 3. backup komutunda önce
    args3 = parser.parse_args(["--lang", "en", "backup", "--dest", "/tmp/test.tar.zst"])
    assert args3.command == "backup"
    assert args3.lang == "en"

    # 4. backup komutunda sonra
    args4 = parser.parse_args(["backup", "--dest", "/tmp/test.tar.zst", "--lang", "tr"])
    assert args4.command == "backup"
    assert args4.lang == "tr"

    # 5. verify komutunda sonra
    args5 = parser.parse_args(["verify", "--src", "/tmp/test.tar.zst", "--lang", "en"])
    assert args5.command == "verify"
    assert args5.lang == "en"

    # 6. Alt komuttaki dil ana seçeneği ezer
    args6 = parser.parse_args(["--lang", "tr", "verify", "--src", "/tmp/test.tar.zst", "--lang", "en"])
    assert args6.lang == "en"


# ---------------------------------------------------------------------------
# 5. Argparse Yardım Metinlerinin Seçili Dile Göre Üretilmesi
# ---------------------------------------------------------------------------
def test_argparse_help_tr_and_en():
    """Ana yardım metinlerinin TR ve EN dillerinde doğru üretildiğini doğrular."""
    # TR yardımı
    parser_tr = build_parser(lang="tr")
    help_tr = parser_tr.format_help()
    assert "Linux Dağıtım Taşıma ve Konfigürasyon Yöneticisi" in help_tr
    assert "Çıkış Kodları:" in help_tr
    assert "Görsel Terminal Arayüzünü (TUI) başlat" in help_tr
    assert "Sistemdeki paketleri ve ayarları tara ve raporla" in help_tr
    assert "Komut satırından yedek al" in help_tr
    assert "Yedek arşivinin veya dizininin bütünlüğünü doğrula" in help_tr

    # EN yardımı
    parser_en = build_parser(lang="en")
    help_en = parser_en.format_help()
    assert "Linux Distribution Migration and Configuration Manager" in help_en
    assert "Exit Codes:" in help_en
    assert "Launch Graphical Terminal User Interface (TUI)" in help_en
    assert "Scan and report system packages and configurations" in help_en
    assert "Create backup from command line" in help_en
    assert "Verify integrity of backup archive or directory" in help_en


def test_argparse_subcommand_help_tr_and_en():
    """Alt komut yardım ve epilog metinlerinin TR ve EN dillerinde doğru üretildiğini doğrular."""
    # backup alt komutu yardımı
    parser_tr = build_parser(lang="tr")
    parser_en = build_parser(lang="en")

    sub_action_tr = next(a for a in parser_tr._actions if isinstance(a, argparse._SubParsersAction))
    sub_action_en = next(a for a in parser_en._actions if isinstance(a, argparse._SubParsersAction))

    sub_tr = sub_action_tr.choices["backup"].format_help()
    assert "Target path for backup file" not in sub_tr
    assert "Yedek dosyasının yazılacağı yol" in sub_tr
    assert "Yedek formatı" in sub_tr
    assert "Yedekleme eksiksiz tamamlandı" in sub_tr

    sub_en = sub_action_en.choices["backup"].format_help()
    assert "Target path for backup file" in sub_en
    assert "Backup format" in sub_en
    assert "Backup completed successfully" in sub_en


# ---------------------------------------------------------------------------
# 6. CLI Çıktılarının (Scan, Verify, Rollback, Append, Restore) İki Dilde Doğrulanması
# ---------------------------------------------------------------------------
def test_cmd_scan_output_tr_and_en(capsys):
    """cmd_scan çıktısının TR ve EN dillerinde doğru etiketlerle üretildiğini test eder."""
    parser = build_parser()

    # TR
    args_tr = parser.parse_args(["scan", "--lang", "tr"])
    cmd_scan(args_tr)
    out_tr = capsys.readouterr().out
    assert "SİSTEM BİLGİSİ" in out_tr
    assert "Sistem Tarama Raporu" in out_tr
    assert "Dağıtım:" in out_tr
    assert "Çekirdek:" in out_tr
    assert "Masaüstü:" in out_tr
    assert "Tespit Edilen Konfigürasyonlar ve Dotfile'lar" in out_tr
    assert "Tespit Edilen Kurulu Uygulamalar ve Paketler" in out_tr

    # EN
    args_en = parser.parse_args(["scan", "--lang", "en"])
    cmd_scan(args_en)
    out_en = capsys.readouterr().out
    assert "SYSTEM INFORMATION" in out_en
    assert "System Scan Report" in out_en
    assert "Distribution:" in out_en
    assert "Kernel:" in out_en
    assert "Desktop:" in out_en
    assert "Detected Configurations and Dotfiles" in out_en
    assert "Detected Installed Applications and Packages" in out_en


def test_cmd_rollback_errors_tr_and_en(tmp_path, capsys):
    """cmd_rollback hata mesajlarının TR ve EN dillerinde yerelleştirildiğini doğrular."""
    parser = build_parser()
    nonexistent_tx = tmp_path / "no_tx.json"

    # 1. Bulunamayan dosya hatası - TR
    args_tr = parser.parse_args(["rollback", "--tx", str(nonexistent_tx), "--lang", "tr"])
    with pytest.raises(SystemExit) as exc_tr:
        cmd_rollback(args_tr)
    assert exc_tr.value.code == 1
    out_tr = capsys.readouterr().out
    assert "Rollback Hatası: Geri alma işlemi için işlem kaydı (transaction log) bulunamadı" in out_tr

    # 1. Bulunamayan dosya hatası - EN
    args_en = parser.parse_args(["rollback", "--tx", str(nonexistent_tx), "--lang", "en"])
    with pytest.raises(SystemExit) as exc_en:
        cmd_rollback(args_en)
    assert exc_en.value.code == 1
    out_en = capsys.readouterr().out
    assert "Rollback Error: Transaction log not found for rollback operation" in out_en

    # 2. Hedef ev dizini uyuşmazlığı hatası - TR
    tx_file = tmp_path / "last_restore.json"
    tx_file.write_text(json.dumps({
        "timestamp": "20261101_120000",
        "target_home": "/expected/home/user",
        "operations": []
    }), encoding="utf-8")

    args_mismatch_tr = parser.parse_args(["rollback", "--tx", str(tx_file), "--target-home", "/other/home", "--lang", "tr"])
    with pytest.raises(SystemExit) as exc_mm_tr:
        cmd_rollback(args_mismatch_tr)
    assert exc_mm_tr.value.code == 1
    out_mm_tr = capsys.readouterr().out
    assert "hedef ev dizini (/expected/home/user)" in out_mm_tr
    assert "uyuşmuyor" in out_mm_tr

    # 2. Hedef ev dizini uyuşmazlığı hatası - EN
    args_mismatch_en = parser.parse_args(["rollback", "--tx", str(tx_file), "--target-home", "/other/home", "--lang", "en"])
    with pytest.raises(SystemExit) as exc_mm_en:
        cmd_rollback(args_mismatch_en)
    assert exc_mm_en.value.code == 1
    out_mm_en = capsys.readouterr().out
    assert "Target home directory in transaction log (/expected/home/user)" in out_mm_en
    assert "does not match" in out_mm_en


def test_cmd_verify_output_tr_and_en(tmp_path, capsys):
    """cmd_verify hata ve tablo çıktılarının TR ve EN dillerinde yerelleştirildiğini doğrular."""
    parser = build_parser()
    nonexistent_backup = tmp_path / "missing_backup.tar.zst"

    # Bulunamayan yedek - TR
    args_tr = parser.parse_args(["verify", "--src", str(nonexistent_backup), "--lang", "tr"])
    with pytest.raises(SystemExit) as exc_tr:
        cmd_verify(args_tr)
    assert exc_tr.value.code == 1
    out_tr = capsys.readouterr().out
    assert "HATA: Yedek dosyası veya dizini bulunamadı" in out_tr

    # Bulunamayan yedek - EN
    args_en = parser.parse_args(["verify", "--src", str(nonexistent_backup), "--lang", "en"])
    with pytest.raises(SystemExit) as exc_en:
        cmd_verify(args_en)
    assert exc_en.value.code == 1
    out_en = capsys.readouterr().out
    assert "ERROR: Backup file or directory not found" in out_en


def test_cmd_append_errors_tr_and_en(capsys):
    """cmd_append hata mesajlarının TR ve EN dillerinde yerelleştirildiğini doğrular."""
    parser = build_parser()

    # Hiçbir seçim yokken - TR
    args_tr = parser.parse_args(["append", "--backup", "/tmp/any.tar.zst", "--lang", "tr"])
    with pytest.raises(SystemExit) as exc_tr:
        cmd_append(args_tr)
    assert exc_tr.value.code == 1
    out_tr = capsys.readouterr().out
    assert "HATA: Eklenecek en az bir --path, --config veya --package belirtilmelidir." in out_tr
    assert "Örnek kullanım:" in out_tr

    # Hiçbir seçim yokken - EN
    args_en = parser.parse_args(["append", "--backup", "/tmp/any.tar.zst", "--lang", "en"])
    with pytest.raises(SystemExit) as exc_en:
        cmd_append(args_en)
    assert exc_en.value.code == 1
    out_en = capsys.readouterr().out
    assert "ERROR: At least one --path, --config or --package must be specified." in out_en
    assert "Example usage:" in out_en


def test_cmd_restore_and_diff_errors_tr_and_en(tmp_path, capsys):
    """cmd_restore ve cmd_diff bulunamayan kaynak hata mesajlarının iki dilde doğrulanması."""
    parser = build_parser()
    missing_file = tmp_path / "missing.tar.zst"

    # restore - TR
    args_res_tr = parser.parse_args(["restore", "--src", str(missing_file), "--lang", "tr"])
    with pytest.raises(SystemExit):
        cmd_restore(args_res_tr)
    assert "HATA: Yedek dosyası bulunamadı" in capsys.readouterr().out

    # restore - EN
    args_res_en = parser.parse_args(["restore", "--src", str(missing_file), "--lang", "en"])
    with pytest.raises(SystemExit):
        cmd_restore(args_res_en)
    assert "ERROR: Backup file not found" in capsys.readouterr().out

    # diff - TR
    args_diff_tr = parser.parse_args(["diff", "--src", str(missing_file), "--lang", "tr"])
    with pytest.raises(SystemExit):
        cmd_diff(args_diff_tr)
    assert "HATA: Yedek dosyası bulunamadı" in capsys.readouterr().out

    # diff - EN
    args_diff_en = parser.parse_args(["diff", "--src", str(missing_file), "--lang", "en"])
    with pytest.raises(SystemExit):
        cmd_diff(args_diff_en)
    assert "ERROR: Backup file not found" in capsys.readouterr().out
