"""
DistroNomad - W4b Yayın Öncesi Güvenlik Düzeltmeleri Testleri.

Kapsam:
1. CLI Parola: İsteğe bağlı parola argümanı, getpass ile etkileşimli sorma, eşleşmeme durumunda hata,
   DISTRONOMAD_PASSWORD ortam değişkeni desteği, komut satırı açık parola stderr görünürlük uyarısı,
   ve terminal olmayan (non-tty) girişte anlaşılır sıfır olmayan hata koduyla çıkış.
2. Ağ Sırları Güvenliği: /etc/NetworkManager/system-connections ve /etc/wireguard profillerinin
   şifresiz yedeklerde atlanması (bilgi mesajı ve system_state_info notu ile), şifreli yedeklerde ise dahil edilmesi.
3. Geriye Uyumluluk: Mevcut komut satırı --password <değer> kullanımının sorunsuz çalışması.
"""

from __future__ import annotations
import io
import os
import sys
import tarfile
from pathlib import Path
import pytest

from distronomad.cli import (
    build_parser,
    cmd_backup,
    cmd_append,
    cmd_restore,
    cmd_verify,
    cmd_diff,
)
from distronomad.models import SystemInfo, ConfigItem, PackageItem, CustomPathItem
from distronomad.backup import BackupEngine
from distronomad.restore import RestoreEngine
from distronomad.crypto import is_encrypted_file, decrypt_archive


def _create_mock_etc(tmp_path: Path) -> Path:
    """Ağ sırları ve genel sistem dosyalarını içeren sahte bir /etc dizini oluşturur."""
    fake_etc = tmp_path / "mock_etc"
    fake_etc.mkdir(parents=True, exist_ok=True)

    # Genel sistem dosyaları
    (fake_etc / "fstab").write_text("UUID=1111-2222 / ext4 defaults 0 1\n", encoding="utf-8")
    (fake_etc / "hosts").write_text("127.0.0.1 localhost\n", encoding="utf-8")

    udev_dir = fake_etc / "udev" / "rules.d"
    udev_dir.mkdir(parents=True, exist_ok=True)
    (udev_dir / "99-test-device.rules").write_text('KERNEL=="sd*", MODE="0666"\n', encoding="utf-8")

    modprobe_dir = fake_etc / "modprobe.d"
    modprobe_dir.mkdir(parents=True, exist_ok=True)
    (modprobe_dir / "blacklist.conf").write_text("blacklist pcspkr\n", encoding="utf-8")

    # Ağ sırları (Wi-Fi parolaları ve WireGuard özel anahtarları)
    nm_dir = fake_etc / "NetworkManager" / "system-connections"
    nm_dir.mkdir(parents=True, exist_ok=True)
    (nm_dir / "OfficeWifi.nmconnection").write_text(
        "[connection]\nid=OfficeWifi\n\n[wifi-security]\npsk=SuperSecretWifiPassword123\n",
        encoding="utf-8"
    )

    wg_dir = fake_etc / "wireguard"
    wg_dir.mkdir(parents=True, exist_ok=True)
    (wg_dir / "wg0.conf").write_text(
        "[Interface]\nPrivateKey = aabbccddeeff00112233445566778899\nAddress = 10.0.0.2/24\n",
        encoding="utf-8"
    )

    return fake_etc


def _setup_test_env(tmp_path: Path):
    """Testler için kaynak ev dizini ve temel yapıları kurar."""
    src_home = tmp_path / "src_home"
    src_home.mkdir(parents=True, exist_ok=True)
    (src_home / ".bashrc").write_text("export TEST=1\n", encoding="utf-8")

    sys_info = SystemInfo(
        distro_id="arch",
        distro_name="Arch Linux",
        home_dir=str(src_home)
    )
    cfgs = [
        ConfigItem(
            id="cfg_bashrc",
            name=".bashrc",
            category="Kabuk",
            source_path=str(src_home / ".bashrc"),
            rel_path=".bashrc",
            is_directory=False,
            is_selected=True
        )
    ]
    return src_home, sys_info, cfgs


# ============================================================================
# 1. CLI PAROLA: ETKİLEŞİMLİ GETPASS SORMA VE ONAY MEKANİZMASI
# ============================================================================

def test_cli_password_prompt_backup_interactive_match(tmp_path, monkeypatch):
    """backup --password değersiz verildiğinde etkileşimli sorup eşleştiğinde şifreli yedek oluşturmalıdır."""
    src_home, _, _ = _setup_test_env(tmp_path)
    dest_archive = tmp_path / "secure_backup.tar.zst.enc"

    # Terminal simülasyonu
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DISTRONOMAD_PASSWORD", raising=False)

    # getpass iki kez soracak: parola ve doğrulama
    prompts = []
    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return "InteractiveSecret123"

    monkeypatch.setattr("getpass.getpass", fake_getpass)

    parser = build_parser()
    args = parser.parse_args(["--home", str(src_home), "backup", "--dest", str(dest_archive), "--password"])
    cmd_backup(args)

    assert len(prompts) == 2, "backup için parola iki kez sorulmalıdır (doğrulama ile)."
    assert dest_archive.exists()
    assert is_encrypted_file(dest_archive), "Oluşturulan arşiv şifreli olmalıdır."

    # Parolayla açılabilirliğini doğrula
    engine = RestoreEngine(backup_path=dest_archive, password="InteractiveSecret123")
    manifest = engine.load_manifest()
    assert manifest is not None
    engine.cleanup()


def test_cli_password_prompt_backup_interactive_mismatch_exits_nonzero(tmp_path, monkeypatch, capsys):
    """backup --password onay parolası eşleşmediğinde sıfır olmayan kodla çıkmalı ve arşivi oluşturmamalıdır."""
    src_home, _, _ = _setup_test_env(tmp_path)
    dest_archive = tmp_path / "mismatch_backup.tar.zst.enc"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DISTRONOMAD_PASSWORD", raising=False)

    calls = ["FirstPass123", "DifferentPass456"]
    def fake_getpass(prompt=""):
        return calls.pop(0)

    monkeypatch.setattr("getpass.getpass", fake_getpass)

    parser = build_parser()
    args = parser.parse_args(["--home", str(src_home), "backup", "--dest", str(dest_archive), "--password"])

    with pytest.raises(SystemExit) as exc:
        cmd_backup(args)

    assert exc.value.code != 0, "Parola eşleşmediğinde sıfır olmayan çıkış kodu üretilmelidir."
    assert not dest_archive.exists(), "Eşleşmeme durumunda yedek dosyası oluşturulmamalıdır."

    err = capsys.readouterr().err
    assert "eşleşmiyor" in err.lower() or "mismatch" in err.lower() or "not match" in err.lower()


def test_cli_password_prompt_append_interactive_mismatch_exits_nonzero(tmp_path, monkeypatch):
    """append --password onay parolası eşleşmediğinde sıfır olmayan kodla çıkmalıdır."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    target_archive = tmp_path / "base_backup.tar.zst.enc"

    # Önce şifreli bir yedek oluştur
    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=target_archive,
        archive_format="tar.zst",
        password="OriginalPassword"
    )
    b_engine.run()
    assert target_archive.exists()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DISTRONOMAD_PASSWORD", raising=False)

    calls = ["PassA", "PassB"]
    monkeypatch.setattr("getpass.getpass", lambda prompt="": calls.pop(0))

    extra_file = tmp_path / "extra.txt"
    extra_file.write_text("append test\n", encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["--home", str(src_home), "append", "--backup", str(target_archive), "--path", str(extra_file), "--password"])

    with pytest.raises(SystemExit) as exc:
        cmd_append(args)

    assert exc.value.code != 0


# ============================================================================
# 2. CLI PAROLA: DISTRONOMAD_PASSWORD ORTAM DEĞİŞKENİ
# ============================================================================

def test_cli_password_environment_variable_backup(tmp_path, monkeypatch):
    """DISTRONOMAD_PASSWORD ortam değişkeni ayarlandığında sormadan şifreleme yapılmalıdır."""
    src_home, _, _ = _setup_test_env(tmp_path)
    dest_archive = tmp_path / "env_backup.tar.zst.enc"

    monkeypatch.setenv("DISTRONOMAD_PASSWORD", "EnvSecretPass999")
    # getpass çağrılmamalıdır
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("getpass çağrılmamalıydı!"))

    parser = build_parser()
    args = parser.parse_args(["--home", str(src_home), "backup", "--dest", str(dest_archive), "--password"])
    cmd_backup(args)

    assert dest_archive.exists()
    assert is_encrypted_file(dest_archive)

    # Parolayı doğrula
    r_engine = RestoreEngine(backup_path=dest_archive, password="EnvSecretPass999")
    manifest = r_engine.load_manifest()
    assert manifest is not None
    r_engine.cleanup()


def test_cli_password_environment_variable_restore(tmp_path, monkeypatch):
    """restore şifreli bir arşive parolasız çağrıldığında DISTRONOMAD_PASSWORD'ü otomatik kullanmalıdır."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    enc_archive = tmp_path / "enc_archive.tar.zst.enc"

    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=enc_archive,
        archive_format="tar.zst",
        password="EnvAutoUnlock"
    )
    b_engine.run()
    assert enc_archive.exists()

    monkeypatch.setenv("DISTRONOMAD_PASSWORD", "EnvAutoUnlock")
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("getpass çağrılmamalıydı!"))

    # --password verilmeden restore
    parser = build_parser()
    args = parser.parse_args(["restore", "--src", str(enc_archive), "--dry-run"])
    cmd_restore(args)


# ============================================================================
# 3. CLI PAROLA: KOMUT SATIRI AÇIK PAROLA GÖRÜNÜRLÜK UYARISI
# ============================================================================

def test_cli_password_cli_value_warning_on_stderr(tmp_path, monkeypatch, capsys):
    """Parola komut satırında açık metin olarak iletildiğinde stderr'e güvenlik uyarısı yazmalıdır."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    enc_archive = tmp_path / "cli_warn.tar.zst.enc"

    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=enc_archive,
        archive_format="tar.zst",
        password="CliPlainPassword"
    )
    b_engine.run()

    parser = build_parser()
    args = parser.parse_args(["verify", "--src", str(enc_archive), "--password", "CliPlainPassword"])
    with pytest.raises(SystemExit) as exc:
        cmd_verify(args)
    assert exc.value.code == 0

    err = capsys.readouterr().err
    assert ("uyarı" in err.lower() or "warning" in err.lower())
    assert ("ps" in err.lower() or "kabuk" in err.lower() or "shell" in err.lower() or "distronomad_password" in err.lower())


# ============================================================================
# 4. CLI PAROLA: TERMİNAL OLMAYAN (NON-TTY) GİRİŞTE HATA DURUMLARI
# ============================================================================

def test_cli_password_non_terminal_encrypted_restore_fails(tmp_path, monkeypatch, capsys):
    """Şifreli arşiv terminal olmayan ortamda parolasız çağrılırsa hata mesajıyla sıfır olmayan kodla çıkmalıdır."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    enc_archive = tmp_path / "enc_target.tar.zst.enc"

    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=enc_archive,
        archive_format="tar.zst",
        password="LockedArchive"
    )
    b_engine.run()

    monkeypatch.delenv("DISTRONOMAD_PASSWORD", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    parser = build_parser()
    args = parser.parse_args(["restore", "--src", str(enc_archive), "--dry-run"])

    with pytest.raises(SystemExit) as exc:
        cmd_restore(args)

    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert ("terminal" in err.lower() or "distronomad_password" in err.lower())


def test_cli_password_non_terminal_backup_flag_fails(tmp_path, monkeypatch, capsys):
    """backup --password terminal olmayan ortamda ortam değişkeni yoksa sıfır olmayan kodla çıkmalıdır."""
    src_home, _, _ = _setup_test_env(tmp_path)
    dest_archive = tmp_path / "wont_be_created.tar.zst.enc"

    monkeypatch.delenv("DISTRONOMAD_PASSWORD", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    parser = build_parser()
    args = parser.parse_args(["--home", str(src_home), "backup", "--dest", str(dest_archive), "--password"])

    with pytest.raises(SystemExit) as exc:
        cmd_backup(args)

    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert ("terminal" in err.lower() or "distronomad_password" in err.lower())


def test_cli_password_restore_diff_verify_auto_prompt_in_terminal(tmp_path, monkeypatch):
    """restore, diff ve verify şifreli arşive terminalde parolasız çağrıldığında getpass ile tek parola sormalıdır."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    enc_archive = tmp_path / "enc_auto_prompt.tar.zst.enc"

    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=enc_archive,
        archive_format="tar.zst",
        password="TargetArchiveSecret"
    )
    b_engine.run()

    monkeypatch.delenv("DISTRONOMAD_PASSWORD", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def fake_getpass(prompt=""):
        return "TargetArchiveSecret"

    monkeypatch.setattr("getpass.getpass", fake_getpass)

    parser = build_parser()

    # verify testi
    args_v = parser.parse_args(["verify", "--src", str(enc_archive)])
    with pytest.raises(SystemExit) as exc_v:
        cmd_verify(args_v)
    assert exc_v.value.code == 0

    # diff testi
    args_d = parser.parse_args(["diff", "--src", str(enc_archive), "--target-home", str(src_home)])
    cmd_diff(args_d)


# ============================================================================
# 5. AĞ SIRLARI GÜVENLİĞİ: ŞİFRESİZ YEDEKTE ATLANMA, ŞİFRELİDE DAHİL EDİLME
# ============================================================================

def test_network_secrets_skipped_in_unencrypted_backup(tmp_path):
    """Şifresiz yedeklerde NetworkManager ve wireguard profilleri arşive girmemeli, bilgi notu bırakılmalıdır."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    mock_etc = _create_mock_etc(tmp_path)
    out_archive = tmp_path / "plain_backup.tar.zst"

    notifies = []
    def progress_cb(update):
        notifies.append((update.phase, update.message))

    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=out_archive,
        archive_format="tar.zst",
        password=None,
        etc_dir=mock_etc,
        progress_callback=progress_cb
    )
    final_path = b_engine.run()
    assert final_path.exists()
    assert not is_encrypted_file(final_path)

    # Bilgi mesajı üretildi mi?
    assert any("Ağ sırları" in msg or "Network secrets" in msg for _, msg in notifies), \
        "Kullanıcıya ağ sırlarının atlandığına dair bilgi mesajı gösterilmelidir."

    r_engine = RestoreEngine(backup_path=final_path)
    try:
        manifest = r_engine.load_manifest()
        etc_files = manifest.system_state_info.get("etc_reference_files", [])

        # Genel dosyalar yedeklenmeli
        assert "fstab" in etc_files
        assert "hosts" in etc_files
        assert "udev_rules.d/99-test-device.rules" in etc_files
        assert "modprobe.d/blacklist.conf" in etc_files

        # Ağ sırları ASLA yedeklenmemeli
        assert not any("NetworkManager" in f for f in etc_files), "NetworkManager profilleri şifresiz yedekte bulunmamalıdır."
        assert not any("wireguard" in f for f in etc_files), "WireGuard profilleri şifresiz yedekte bulunmamalıdır."

        # system_state_info içindeki not
        assert manifest.system_state_info.get("network_secrets_skipped") is True
        note = manifest.system_state_info.get("network_secrets_note")
        assert note is not None
        assert ("ağ sırları" in note.lower() or "network secrets" in note.lower())

        # Fiziksel arşiv içeriğini de tara
        with tarfile.open(final_path, "r") as tar:
            names = tar.getnames()
            assert not any("system-connections" in n or "OfficeWifi" in n for n in names)
            assert not any("wireguard" in n or "wg0.conf" in n for n in names)
    finally:
        r_engine.cleanup()


def test_network_secrets_included_in_encrypted_backup(tmp_path):
    """Şifreli yedeklerde NetworkManager ve wireguard profilleri güvenle arşive dahil edilmelidir."""
    src_home, sys_info, cfgs = _setup_test_env(tmp_path)
    mock_etc = _create_mock_etc(tmp_path)
    out_archive = tmp_path / "enc_network_backup.tar.zst.enc"

    b_engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=out_archive,
        archive_format="tar.zst",
        password="EncryptedBackupSecretKey",
        etc_dir=mock_etc
    )
    final_path = b_engine.run()
    assert final_path.exists()
    assert is_encrypted_file(final_path)

    r_engine = RestoreEngine(backup_path=final_path, password="EncryptedBackupSecretKey")
    try:
        manifest = r_engine.load_manifest()
        etc_files = manifest.system_state_info.get("etc_reference_files", [])

        # Genel dosyalar
        assert "fstab" in etc_files
        assert "hosts" in etc_files

        # Ağ sırları şifreli yedekte mevcut olmalıdır
        assert any("NetworkManager/OfficeWifi.nmconnection" in f for f in etc_files)
        assert any("wireguard/wg0.conf" in f for f in etc_files)
        assert manifest.system_state_info.get("network_secrets_skipped") is False

        # Çözülmüş arşivde dosyaların fiziken var olduğunu kontrol et
        assert (r_engine.source_dir / "system_state" / "etc_reference" / "NetworkManager" / "OfficeWifi.nmconnection").exists()
        assert (r_engine.source_dir / "system_state" / "etc_reference" / "wireguard" / "wg0.conf").exists()
    finally:
        r_engine.cleanup()


# ============================================================================
# 6. GERİYE UYUMLULUK: MEVCUT --password DEĞER KULLANIMI
# ============================================================================

def test_existing_password_cli_usage_backward_compatible(tmp_path, capsys):
    """Mevcut testlerin ve kullanıcıların --password <değer> kullanımı çalışmaya devam etmelidir."""
    src_home, _, _ = _setup_test_env(tmp_path)
    dest_archive = tmp_path / "legacy_backup.tar.zst.enc"

    parser = build_parser()
    args = parser.parse_args(["--home", str(src_home), "backup", "--dest", str(dest_archive), "--password", "LegacyKey987"])
    assert args.password == "LegacyKey987"

    cmd_backup(args)
    assert dest_archive.exists()
    assert is_encrypted_file(dest_archive)

    # verify ile doğrula
    args_v = parser.parse_args(["verify", "--src", str(dest_archive), "--password", "LegacyKey987"])
    with pytest.raises(SystemExit) as exc_v:
        cmd_verify(args_v)
    assert exc_v.value.code == 0
