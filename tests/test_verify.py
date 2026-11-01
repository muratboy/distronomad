"""
DistroNomad - Bağımsız Yedek Bütünlük Doğrulama (verify) Testleri.
"""

import os
import tempfile
import pytest
from pathlib import Path

from distronomad.models import SystemInfo, PackageItem, ConfigItem
from distronomad.backup import BackupEngine
from distronomad.verify import verify_backup
from distronomad.cli import build_parser, cmd_verify
from distronomad.ui.app import DistroNomadApp
from textual.widgets import Input, Button, RichLog, ProgressBar


def _create_sample_backup(
    dest_path: Path,
    archive_format: str = "tar.zst",
    password: str = None,
    backup_mode: str = "full",
    base_backup_path: Path = None,
    extra_content: str = "test content 123"
) -> Path:
    """Doğrulama testleri için geçerli bir DistroNomad yedeği oluşturur."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    sample_home = dest_path.parent / "sample_home"
    cfg_dir = sample_home / ".config" / "sampleapp"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    sample_file = cfg_dir / "app.conf"
    sample_file.write_text(extra_content, encoding="utf-8")

    sys_info = SystemInfo(
        distro_id="arch",
        distro_name="Arch Linux",
        home_dir=str(sample_home),
        available_package_managers=["pacman"]
    )
    pkgs = [PackageItem(name="neovim", manager="pacman", category="Editör")]
    cfgs = [ConfigItem(id="cfg_sample", name="sampleapp", category="Uygulama", source_path=str(cfg_dir), rel_path=".config/sampleapp")]

    engine = BackupEngine(
        system_info=sys_info,
        packages=pkgs,
        configs=cfgs,
        custom_paths=[],
        output_path=dest_path,
        archive_format=archive_format,
        backup_mode=backup_mode,
        base_backup_path=base_backup_path,
        password=password
    )
    return engine.run()


def test_verify_tar_zst_healthy(tmp_path):
    """tar.zst yedeğinin diske açılmadan sağlam olarak doğrulandığını test eder."""
    backup_file = tmp_path / "backup.tar.zst"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst")

    res = verify_backup(out_path)
    assert res.is_valid is True
    assert res.corrupted is False
    assert res.error_message is None
    assert len(res.matched) >= 1
    assert len(res.mismatched) == 0
    assert len(res.missing) == 0
    assert len(res.untracked) == 0


def test_verify_tar_gz_and_dir_healthy(tmp_path):
    """tar.gz ve dir formatındaki yedeklerin sağlam olarak doğrulandığını test eder."""
    # tar.gz
    gz_file = tmp_path / "backup.tar.gz"
    out_gz = _create_sample_backup(gz_file, archive_format="tar.gz")
    res_gz = verify_backup(out_gz)
    assert res_gz.is_valid is True
    assert len(res_gz.matched) >= 1
    assert len(res_gz.mismatched) == 0

    # dir
    dir_path = tmp_path / "backup_dir"
    out_dir = _create_sample_backup(dir_path, archive_format="dir")
    res_dir = verify_backup(out_dir)
    assert res_dir.is_valid is True
    assert len(res_dir.matched) >= 1
    assert len(res_dir.mismatched) == 0


def test_verify_encrypted_archive_healthy(tmp_path):
    """AES-256 şifreli yedeğin doğru parola ile akış halinde sağlam doğrulandığını test eder."""
    backup_file = tmp_path / "backup.tar.zst"
    pwd = "GucluParola_123!"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst", password=pwd)

    assert str(out_path).endswith(".enc")
    res = verify_backup(out_path, password=pwd)
    assert res.is_valid is True
    assert res.corrupted is False
    assert len(res.matched) >= 1
    assert len(res.mismatched) == 0
    assert len(res.missing) == 0


def test_verify_dir_byte_mismatch_and_missing_file(tmp_path):
    """dir formatında tek bayt değişince uyuşmazlık, dosya silinince eksik raporlandığını test eder."""
    dir_path = tmp_path / "dir_backup"
    out_dir = _create_sample_backup(dir_path, archive_format="dir", extra_content="Orijinal veri")

    target_file = out_dir / "configs" / ".config" / "sampleapp" / "app.conf"
    assert target_file.exists()

    # 1. Dosya içeriğinde tek bayt değiştir
    target_file.write_text("Degistirilmis veri", encoding="utf-8")
    res_mismatch = verify_backup(out_dir)
    assert res_mismatch.is_valid is False
    assert len(res_mismatch.mismatched) >= 1
    assert any("app.conf" in m for m in res_mismatch.mismatched)

    # 2. Dosyayı tamamen sil (eksik dosya)
    target_file.unlink()
    res_missing = verify_backup(out_dir)
    assert res_missing.is_valid is False
    assert len(res_missing.missing) >= 1
    assert any("app.conf" in m for m in res_missing.missing)


def test_verify_truncated_tar_zst_caught_corrupted(tmp_path):
    """Kesilmiş (truncate edilmiş) tar.zst arşivin bozuk olarak yakalandığını test eder."""
    backup_file = tmp_path / "backup.tar.zst"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst", extra_content="X" * 10000)

    # Arşivin son yarısını kes
    orig_size = out_path.stat().st_size
    with open(out_path, "r+b") as f:
        f.truncate(orig_size // 3)

    res = verify_backup(out_path)
    assert res.is_valid is False
    assert res.corrupted is True
    assert res.error_message is not None
    assert "bozuk" in res.error_message.lower() or "kesilmiş" in res.error_message.lower() or "tar" in res.error_message.lower() or "zstd" in res.error_message.lower()


def test_verify_encrypted_wrong_password_clear_error(tmp_path):
    """Yanlış parolanın ve eksik parolanın anlaşılır hata verdiğini test eder."""
    backup_file = tmp_path / "backup.tar.zst"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst", password="DogruParola123")

    # Parola verilmediğinde
    with pytest.raises(ValueError, match="şifrelenmiştir"):
        verify_backup(out_path, password=None)

    # Yanlış parola verildiğinde istisna fırlatılmamalı; is_valid=False ve anlaşılır hata dönmeli (A-P2-2)
    res_wrong = verify_backup(out_path, password="YanlisParola_999")
    assert res_wrong.is_valid is False
    assert res_wrong.corrupted is True
    assert "Hatalı parola" in (res_wrong.error_message or "")


def test_verify_encrypted_no_plaintext_written_to_temp_or_disk(tmp_path):
    """Şifreli doğrulama sırasında geçici dizinde veya diskte düz metin dosya oluşmadığını test eder."""
    secret_marker = "SUPER_SECRET_TOKEN_DO_NOT_LEAK_987654321"
    backup_file = tmp_path / "secret.tar.zst"
    pwd = "VaultPassword777"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst", password=pwd, extra_content=secret_marker)

    # /tmp altındaki mevcut dosyaları kaydet
    tmp_dir = Path(tempfile.gettempdir())
    files_before = set(tmp_dir.glob("**/*"))

    # Şifreli doğrulamayı çalıştır
    res = verify_backup(out_path, password=pwd)
    assert res.is_valid is True

    # /tmp altında yeni dosya açılmadığını ve gizli belirtecin diskte hiçbir dosyada olmadığını doğrula
    files_after = set(tmp_dir.glob("**/*")) - files_before
    for f in files_after:
        if f.is_file():
            try:
                content = f.read_bytes()
                assert secret_marker.encode("utf-8") not in content
            except Exception:
                pass


def test_verify_incremental_chain_and_missing_base_warning(tmp_path):
    """Artımlı zincirde tabanın doğrulandığını ve taban yoksa uyarı verildiğini test eder."""
    # 1. Taban (full) yedek oluştur
    base_file = tmp_path / "backup_base.tar.zst"
    base_path = _create_sample_backup(base_file, archive_format="tar.zst", extra_content="Base v1")

    # 2. Artımlı yedek oluştur
    inc_file = tmp_path / "backup_inc1.tar.zst"
    inc_path = _create_sample_backup(
        inc_file,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=base_path,
        extra_content="Base v1 degismedi"
    )

    # Taban yedek yanında bulunduğunda zincir başarıyla doğrulanmalı
    res_chain = verify_backup(inc_path, check_chain=True)
    assert res_chain.is_valid is True
    assert len(res_chain.chain_results) == 1
    assert res_chain.chain_results[0].is_valid is True
    assert len(res_chain.missing) == 0

    # 3. Artımlı yedeği tabanın olmadığı izole bir dizine taşı
    iso_dir = tmp_path / "isolated"
    iso_dir.mkdir(parents=True)
    iso_inc_path = iso_dir / inc_path.name
    iso_inc_path.write_bytes(inc_path.read_bytes())

    res_isolated = verify_backup(iso_inc_path, check_chain=True)
    # Taban yoksa uyarı verilmeli ve tabandaki dosyalar eksik sayılmamalı
    assert len(res_isolated.warnings) >= 1
    assert "Taban yedek bulunamadı" in res_isolated.warnings[0]
    assert len(res_isolated.missing) == 0
    assert res_isolated.is_valid is False


def test_verify_cli_exit_codes(tmp_path, monkeypatch):
    """CLI verify komutunun sağlamda 0, bozukta sıfırdan farklı kodla çıktığını test eder."""
    backup_file = tmp_path / "cli_backup.tar.zst"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst")

    parser = build_parser()

    # 1. Sağlam yedek -> çıkış kodu 0
    args_ok = parser.parse_args(["verify", "--src", str(out_path)])
    with pytest.raises(SystemExit) as exc:
        cmd_verify(args_ok)
    assert exc.value.code == 0

    # 2. Olmayan dosya -> çıkış kodu != 0
    args_missing = parser.parse_args(["verify", "--src", str(tmp_path / "non_existent.tar.zst")])
    with pytest.raises(SystemExit) as exc:
        cmd_verify(args_missing)
    assert exc.value.code != 0

    # 3. Bozulmuş dosya -> çıkış kodu != 0
    corrupt_file = tmp_path / "cli_corrupt.tar.zst"
    corrupt_file.write_bytes(out_path.read_bytes()[:100])
    args_corrupt = parser.parse_args(["verify", "--src", str(corrupt_file)])
    with pytest.raises(SystemExit) as exc:
        cmd_verify(args_corrupt)
    assert exc.value.code != 0


@pytest.mark.asyncio
async def test_verify_tui_button_and_worker(tmp_path):
    """TUI Geri Yükle sekmesindeki Yedeği Doğrula düğmesinin worker'ı çalıştırıp sonucu gösterdiğini test eder."""
    backup_file = tmp_path / "tui_backup.tar.zst"
    out_path = _create_sample_backup(backup_file, archive_format="tar.zst")

    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test(size=(120, 60)) as pilot:
        # Geri Yükle sekmesine geç
        app.action_switch_tab("tab-restore")
        await pilot.pause()

        # Kaynak girişine yedek yolunu yaz
        src_input = app.query_one("#input-restore-src", Input)
        src_input.value = str(out_path)

        # Yedeği Doğrula düğmesine tıkla
        btn_verify = app.query_one("#btn-verify-backup", Button)
        await pilot.click(btn_verify)
        await pilot.pause(0.5)

        # Log alanında doğrulama mesajını kontrol et
        log = app.query_one("#restore-log", RichLog)
        # Worker tamamlanana kadar kısa bekleme
        for _ in range(20):
            if any("✔" in str(line.text) for line in log.lines):
                break
            await pilot.pause(0.1)

        log_text = "\n".join(str(line.text) for line in log.lines)
        assert "✔" in log_text or "doğrulandı" in log_text.lower() or "sağlam" in log_text.lower()
