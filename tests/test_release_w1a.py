"""
Tests for Release Readiness Plan Wave 1-A (W1-A):
Backup engine, data integrity, smart ignore, permissions, sensitive items, verify.
"""

from __future__ import annotations
import os
import stat
import json
import time
import shutil
import tarfile
import tempfile
from pathlib import Path
import pytest

from distronomad.models import (
    SystemInfo,
    PackageItem,
    ConfigItem,
    CustomPathItem,
    BackupManifest,
    ProgressUpdate,
    RestoreOptions,
)
from distronomad.backup import (
    BackupEngine,
    check_fat32_risk,
    Fat32LimitError,
)
from distronomad.verify import verify_backup
from distronomad.archive import read_manifest_from_backup
from distronomad.discovery import SystemScanner, get_dir_size_and_count
from distronomad.ignore import SmartIgnoreFilter
from distronomad.restore import RestoreEngine


def _make_sysinfo(home: Path) -> SystemInfo:
    return SystemInfo(
        distro_id="arch",
        distro_name="Arch Linux",
        home_dir=str(home),
        available_package_managers=["pacman"]
    )


# ============================================================================
# A-P0-1: Missing base incremental backup must never be reported as valid
# ============================================================================

def test_a_p0_1_missing_base_incremental_never_valid(tmp_path):
    """Tabanı bulunamayan artımlı yedek ASLA geçerli sayılmamalı (is_valid=False)."""
    home = tmp_path / "home"
    home.mkdir()
    cfg_dir = home / ".config" / "app"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "app.conf").write_text("v1")

    sys_info = _make_sysinfo(home)
    cfgs = [ConfigItem(id="c_app", name="app", category="Genel", source_path=str(cfg_dir), rel_path=".config/app")]

    # 1. Taban yedeği oluştur
    base_file = tmp_path / "base.tar.zst"
    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=base_file,
        archive_format="tar.zst",
        backup_mode="full"
    ).run()

    # 2. Artımlı yedek oluştur
    (cfg_dir / "app.conf").write_text("v2")
    inc_file = tmp_path / "inc.tar.zst"
    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=inc_file,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=base_file
    ).run()

    # Taban mevcutken doğrulama geçerli olmalı
    res_valid = verify_backup(inc_file, check_chain=True)
    assert res_valid.is_valid is True
    assert len(res_valid.chain_results) == 1

    # 3. Artımlı yedeği tabandan izole bir dizine taşı
    iso_dir = tmp_path / "isolated"
    iso_dir.mkdir()
    iso_inc = iso_dir / inc_file.name
    shutil.copy2(inc_file, iso_inc)

    # Taban bulunamadığında ASLA is_valid=True olmamalı
    res_orphan = verify_backup(iso_inc, check_chain=True)
    assert res_orphan.is_valid is False
    assert len(res_orphan.chain_results) == 0
    assert res_orphan.error_message is not None
    assert "Zincir eksik" in res_orphan.error_message or "bulunamadı" in res_orphan.error_message
    assert any("bulunamadı" in w for w in res_orphan.warnings)


# ============================================================================
# A-P0-2: Directory format incremental backup must never corrupt base
# ============================================================================

def test_a_p0_2_dir_format_incremental_to_same_path_fails_and_new_path_works(tmp_path):
    """Dizin formatı artımlı yedek yerinde sync yapmamalı; yeni dizine yazmalı, hata durumunda tabanı bozmamalı."""
    home = tmp_path / "home"
    home.mkdir()
    cfg_dir = home / ".config" / "my_app"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "f1.txt").write_text("f1_v1")
    (cfg_dir / "delete_later.txt").write_text("delete_me")

    sys_info = _make_sysinfo(home)
    cfgs = [ConfigItem(id="c_app", name="my_app", category="Genel", source_path=str(cfg_dir), rel_path=".config/my_app")]

    base_dir = tmp_path / "base_dir"
    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=base_dir,
        archive_format="dir",
        backup_mode="full"
    ).run()

    # Taban dosyasını kontrol et
    base_f1 = base_dir / "configs" / ".config" / "my_app" / "f1.txt"
    assert base_f1.exists()
    assert base_f1.read_text() == "f1_v1"

    # Kaynakta değişiklik yap
    (cfg_dir / "f1.txt").write_text("f1_v2")
    (cfg_dir / "delete_later.txt").unlink()

    # Tabanla aynı çıktı yolu verildiğinde hata fırlatılmalı
    with pytest.raises((ValueError, FileExistsError)):
        BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=base_dir,
            archive_format="dir",
            backup_mode="incremental",
            base_backup_path=base_dir
        ).run()

    # Tabanın bozulmadığını kontrol et
    assert base_f1.read_text() == "f1_v1"
    assert (base_dir / "configs" / ".config" / "my_app" / "delete_later.txt").exists()

    # Yeni dizine artımlı yedek al
    inc_dir = tmp_path / "inc_dir"
    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=inc_dir,
        archive_format="dir",
        backup_mode="incremental",
        base_backup_path=base_dir
    ).run()

    # inc_dir içinde yeni dosya olmalı, silinen dosya olmamalı
    inc_f1 = inc_dir / "configs" / ".config" / "my_app" / "f1.txt"
    assert inc_f1.exists()
    assert inc_f1.read_text() == "f1_v2"
    assert not (inc_dir / "configs" / ".config" / "my_app" / "delete_later.txt").exists()

    # Orijinal taban yedek hala sağlam olmalı
    assert (base_dir / "configs" / ".config" / "my_app" / "delete_later.txt").exists()

    # verify_backup ile doğrula
    v_res = verify_backup(inc_dir, base_path=base_dir, check_chain=True)
    assert v_res.is_valid is True


# ============================================================================
# A-P0-3: Invalid UTF-8 (surrogateescape) filenames must not crash backup/verify
# ============================================================================

def test_a_p0_3_surrogateescape_filenames_end_to_end(tmp_path):
    """Geçersiz UTF-8 surrogateescape karakterli dosya adları motoru çökertmemeli."""
    home = tmp_path / "home"
    home.mkdir()
    docs = home / "Documents"
    docs.mkdir()

    bad_name_bytes = b"bad_\xff\xfe_file.txt"
    bad_name_str = os.fsdecode(bad_name_bytes)
    bad_file = docs / bad_name_str
    bad_file.write_bytes(b"surrogate_content_test_123")

    sys_info = _make_sysinfo(home)
    cp = [CustomPathItem(path=str(docs), label="Docs", is_dir=True, is_selected=True)]
    out_archive = tmp_path / "surrogate_test.tar.zst"

    # Yedekleme çökmemeli (UnicodeEncodeError vermemeli)
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=cp,
        output_path=out_archive,
        archive_format="tar.zst",
        backup_mode="full"
    )
    engine.run()
    assert out_archive.exists()

    # Manifest okunabilmeli
    manifest = read_manifest_from_backup(out_archive)
    assert manifest is not None

    # verify_backup akışı çökmemeli ve sağlam raporlamalı
    v_res = verify_backup(out_archive)
    assert v_res.is_valid is True

    # restore tarafı sandbox'ta salt okunur olarak doğrula
    re = RestoreEngine(out_archive)
    extracted = re.ensure_extracted()
    restored_files = list(extracted.rglob("bad_*"))
    assert len(restored_files) == 1
    assert os.fsencode(restored_files[0].name) == bad_name_bytes
    assert restored_files[0].read_bytes() == b"surrogate_content_test_123"


# ============================================================================
# A-P0-4: Symlink loops and unreadable entries must not crash scanning
# ============================================================================

def test_a_p0_4_symlink_loop_does_not_crash_scanner(tmp_path):
    """Döngüsel sembolik bağlar (symlink loops) get_dir_size_and_count'u ve tarayıcıyı çökertmemeli."""
    test_dir = tmp_path / "loop_dir"
    test_dir.mkdir()
    (test_dir / "valid.txt").write_text("valid content")

    # Kendi içine döngü oluşturan sembolik bağ
    loop_symlink = test_dir / "infinite_loop"
    try:
        os.symlink(str(test_dir), str(loop_symlink))
    except OSError:
        pass

    # Mutual loop
    loop1 = test_dir / "loop1"
    loop2 = test_dir / "loop2"
    try:
        os.symlink("loop2", str(loop1))
        os.symlink("loop1", str(loop2))
    except OSError:
        pass

    # get_dir_size_and_count Errno 40 veya RecursionError ile çökmemeli
    size, count = get_dir_size_and_count(test_dir)
    assert count >= 1
    assert size >= len("valid content")

    # SystemScanner da çökmemeli
    scanner = SystemScanner(home_dir=str(tmp_path))
    cfgs = scanner.scan_configs()
    assert isinstance(cfgs, list)


# ============================================================================
# A-P1-1: SmartIgnoreFilter distinguishes configs vs custom paths
# ============================================================================

def test_a_p1_1_smart_ignore_custom_paths_vs_configs():
    """Custom path'lerde meşru cache dizinleri korunmalı; yalnızca kesin çöp elenmeli."""
    flt = SmartIgnoreFilter(use_smart_defaults=True)

    # 1. Kullanıcının projesindeki meşru cache dizini:
    p_code_cache = Path("/home/user/Projects/web_app/src/cache/store.py")
    p_data_cache = Path("/home/user/Data/Cache/results.csv")

    # is_custom_path=True olduğunda ASLA elenmemeli
    assert not flt.should_ignore(p_code_cache, is_custom_path=True)
    assert not flt.should_ignore(p_data_cache, is_custom_path=True)

    # is_custom_path=False olduğunda (konfigürasyon/tarayıcı) elenmeli
    assert flt.should_ignore(p_code_cache, is_custom_path=False)
    assert flt.should_ignore(p_data_cache, is_custom_path=False)

    # 2. Kesin çöp klasörleri her iki durumda da elenmeli
    trash_items = [
        Path("/home/user/Projects/web_app/node_modules/express/index.js"),
        Path("/home/user/Projects/web_app/__pycache__/app.cpython-312.pyc"),
        Path("/home/user/Projects/web_app/.venv/bin/python"),
        Path("/home/user/Projects/web_app/venv/bin/activate"),
        Path("/home/user/Projects/web_app/.pytest_cache/v/cache"),
        Path("/home/user/Projects/web_app/.mypy_cache/3.12/main.data.json"),
        Path("/home/user/Projects/web_app/.ruff_cache/cache"),
    ]
    for trash in trash_items:
        assert flt.should_ignore(trash, is_custom_path=True)
        assert flt.should_ignore(trash, is_custom_path=False)


def test_a_p1_1_backup_engine_reports_ignored_files_count(tmp_path):
    """BackupEngine akıllı filtreleme nedeniyle atlanan dosya sayısını raporlamalı."""
    home = tmp_path / "home"
    home.mkdir()
    proj = home / "Projects" / "app"
    proj.mkdir(parents=True)
    (proj / "main.py").write_text("print('hello')")
    (proj / "src_cache").mkdir()
    (proj / "src_cache" / "cache.py").write_text("# code cache")

    # Kesin çöp oluştur
    pycache = proj / "__pycache__"
    pycache.mkdir()
    (pycache / "main.cpython-312.pyc").write_bytes(b"bytecode")
    node_modules = proj / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg.js").write_text("module.exports={}")

    sys_info = _make_sysinfo(home)
    cp = [CustomPathItem(path=str(proj), label="app", is_dir=True, is_selected=True)]
    out = tmp_path / "ignore_test.tar.zst"

    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=cp,
        output_path=out,
        archive_format="tar.zst",
        use_smart_ignore=True
    )
    engine.run()

    # Kesin çöpler atlanmış ve sayılmış olmalı
    assert engine.ignored_by_filter_count >= 2


# ============================================================================
# A-P1-2: Decision 2 - Shell histories and token files sensitive & unselected
# ============================================================================

def test_a_p1_2_sensitive_history_and_tokens_default_unselected(tmp_path):
    """Komut geçmişleri ve kimlik/token dosyaları is_sensitive=True ve is_selected=False olmalı."""
    home = tmp_path / "home"
    home.mkdir()

    # Komut geçmişleri oluştur
    (home / ".bash_history").write_text("export SECRET=123\n")
    (home / ".zsh_history").write_text(": 1600000000:0;ls\n")
    (home / ".python_history").write_text("import os\n")
    (home / ".npmrc").write_text("//registry.npmjs.org/:_authToken=npm_xyz123\n")
    (home / ".netrc").write_text("machine github.com login foo password bar\n")
    (home / ".git-credentials").write_text("https://user:token@github.com\n")

    fish_dir = home / ".local" / "share" / "fish"
    fish_dir.mkdir(parents=True)
    (fish_dir / "fish_history").write_text("- cmd: secret_command\n")

    atuin_dir = home / ".local" / "share" / "atuin"
    atuin_dir.mkdir(parents=True)
    (atuin_dir / "history.db").write_bytes(b"sqlite_data")

    gh_dir = home / ".config" / "gh"
    gh_dir.mkdir(parents=True)
    (gh_dir / "hosts.yml").write_text("github.com:\n  oauth_token: gho_secret\n")

    scanner = SystemScanner(home_dir=str(home))
    configs = scanner.scan_configs()
    cfg_map = {c.rel_path: c for c in configs}

    expected_sensitive = [
        ".bash_history",
        ".zsh_history",
        ".python_history",
        ".npmrc",
        ".netrc",
        ".git-credentials",
        ".local/share/fish/fish_history",
        ".local/share/atuin",
        ".config/gh",
    ]

    for item_rel in expected_sensitive:
        assert item_rel in cfg_map, f"{item_rel} taranmadı!"
        cfg = cfg_map[item_rel]
        assert cfg.is_sensitive is True, f"{item_rel} is_sensitive=True olmalıydı!"
        assert cfg.is_selected is False, f"{item_rel} varsayılan is_selected=False olmalıydı!"


# ============================================================================
# A-P1-3: Temporary plaintext files created with 0600 / 0700
# ============================================================================

def test_a_p1_3_raw_archive_created_with_0600_umask_independent(tmp_path):
    """Geçici ham arşiv dosyası umask'tan bağımsız olarak 0600 ile açılmalı."""
    home = tmp_path / "home"
    home.mkdir()
    cfg_dir = home / ".config" / "app"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "secret.conf").write_text("super_secret_token")

    sys_info = _make_sysinfo(home)
    cfgs = [ConfigItem(id="c_app", name="app", category="Genel", source_path=str(cfg_dir), rel_path=".config/app")]
    out = tmp_path / "umask_test.tar.zst"

    # Umask 0000 veya 0022 simülasyonu altında BackupEngine çalıştır
    orig_umask = os.umask(0o000)
    try:
        engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=cfgs,
            custom_paths=[],
            output_path=out,
            archive_format="tar.zst"
        )
        engine.run()
    finally:
        os.umask(orig_umask)

    # Nihai dosya ve geçici dosyaların izin kontrolü (world readable olmamalı)
    mode = stat.S_IMODE(out.stat().st_mode)
    assert mode == 0o600


# ============================================================================
# A-P1-4: Encrypted append staging in local private dir, not target parent
# ============================================================================

def test_a_p1_4_encrypted_append_uses_local_cache_tmp(tmp_path, monkeypatch):
    """Şifreli append sırasında deşifre edilmiş ara dosyalar hedef dizine değil yerel XDG_CACHE_HOME'a yazılmalı."""
    home = tmp_path / "home"
    home.mkdir()
    local_cache = tmp_path / "custom_cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(local_cache))

    cfg_dir = home / ".config" / "app"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "c1.conf").write_text("v1")

    # Hedef olarak simüle edilmiş harici USB disk
    usb_dir = tmp_path / "external_usb"
    usb_dir.mkdir()
    backup_file = usb_dir / "vault.tar.zst.enc"
    pwd = "StrongPassword456"

    sys_info = _make_sysinfo(home)
    cfgs = [ConfigItem(id="c1", name="app", category="Genel", source_path=str(cfg_dir), rel_path=".config/app")]

    # 1. İlk şifreli yedek
    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs,
        custom_paths=[],
        output_path=backup_file,
        archive_format="tar.zst",
        password=pwd
    ).run()

    # 2. Append modunda yeni bir dosya ekle
    (cfg_dir / "c2.conf").write_text("v2")
    cfgs_new = [ConfigItem(id="c2", name="app2", category="Genel", source_path=str(cfg_dir / "c2.conf"), rel_path=".config/app/c2.conf")]

    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=cfgs_new,
        custom_paths=[],
        output_path=backup_file,
        archive_format="tar.zst",
        backup_mode="append",
        password=pwd
    ).run()

    # USB dizininde düz metin sızıntısı olmadığını kontrol et
    usb_files = list(usb_dir.iterdir())
    assert len(usb_files) == 1
    assert usb_files[0].name == "vault.tar.zst.enc"

    # Yerel geçici dizinin temizlendiğini kontrol et
    tmp_staging_leftover = list(local_cache.glob("**/distronomad_dec_*"))
    assert len(tmp_staging_leftover) == 0


# ============================================================================
# A-P1-5: Unreadable / skipped files tracked and reported
# ============================================================================

def test_a_p1_5_unreadable_files_in_skipped_list_and_not_empty_hash(tmp_path):
    """Okunamayan dosyalar skipped_files listesine girmeli, manifest'e boş hash ile girmemeli."""
    home = tmp_path / "home"
    home.mkdir()
    docs = home / "Documents"
    docs.mkdir()

    readable = docs / "readable.txt"
    readable.write_text("content ok")

    unreadable = docs / "unreadable.txt"
    unreadable.write_text("secret root")
    try:
        os.chmod(unreadable, 0o000)
    except OSError:
        pass

    sys_info = _make_sysinfo(home)
    cp = [CustomPathItem(path=str(docs), label="Docs", is_dir=True, is_selected=True)]
    out = tmp_path / "skip_test.tar.zst"

    last_notification = []
    def _cb(up: ProgressUpdate):
        if up.phase == "tamamlandı":
            last_notification.append(up.message)

    try:
        engine = BackupEngine(
            system_info=sys_info,
            packages=[],
            configs=[],
            custom_paths=cp,
            output_path=out,
            archive_format="tar.zst",
            progress_callback=_cb
        )
        engine.run()
    finally:
        try:
            os.chmod(unreadable, 0o600)
        except OSError:
            pass

    assert out.exists()

    manifest = read_manifest_from_backup(out)
    assert manifest is not None

    # Okunamayan dosya manifest.file_hashes içinde boş string olmamalı
    for k, h in manifest.file_hashes.items():
        assert h != "", f"{k} boş hash ile manifestte yer aldı!"

    # skipped_files içinde yer almalı
    assert len(manifest.skipped_files) >= 1
    assert any("unreadable.txt" in sf.get("path", "") for sf in manifest.skipped_files)

    # verify_backup okunamayan dosya yüzünden bozuk ('missing') çıkmamalı
    v_res = verify_backup(out)
    assert v_res.is_valid is True

    # Tamamlanma bildiriminde dosya atlandığı belirtilmeli
    assert len(last_notification) >= 1
    assert "atlandı" in last_notification[0]


# ============================================================================
# A-P2-2: verify with wrong password returns is_valid=False without crashing
# ============================================================================

def test_a_p2_2_verify_wrong_password_returns_invalid_without_exception(tmp_path):
    """Yanlış parola verildiğinde verify_backup unhandled ValueError fırlatmamalı."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "f.txt").write_text("data")

    sys_info = _make_sysinfo(home)
    cp = [CustomPathItem(path=str(home / "f.txt"), is_dir=False, is_selected=True)]
    out = tmp_path / "pwd_test.tar.zst.enc"
    pwd = "CorrectSecretPass123"

    BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=cp,
        output_path=out,
        archive_format="tar.zst",
        password=pwd
    ).run()

    # Yanlış parola
    res = verify_backup(out, password="WrongPassword999")
    assert res.is_valid is False
    assert res.corrupted is True
    assert res.error_message is not None
    assert "Hatalı parola" in res.error_message or "bad decrypt" in res.error_message.lower()


# ============================================================================
# A-P2-4: FAT filesystem dir format warning and symlink fallback
# ============================================================================

def test_a_p2_4_fat32_dir_format_risk_and_symlink_skip(tmp_path):
    """FAT32 üzerinde dir formatı risk üretmeli, force=True ile symlink çökmeden atlanmalı."""
    fake_mountinfo = tmp_path / "mountinfo_fat"
    fake_mountinfo.write_text(f"100 1 8:1 / {tmp_path} rw - vfat /dev/sdb1 rw\n", encoding="utf-8")

    out_dir = tmp_path / "fat_backup_dir"

    # 1. check_fat32_risk dir formatında risk üretmeli
    risk = check_fat32_risk(
        target_path=out_dir,
        archive_format="dir",
        mountinfo_path=fake_mountinfo
    )
    assert risk.has_risk is True
    assert risk.reason == "fat32_dir_unsupported"
    assert "tar.zst" in risk.message

    # 2. force=False iken BackupEngine.run Fat32LimitError fırlatmalı
    home = tmp_path / "home"
    home.mkdir()
    (home / "f.txt").write_text("hello")
    sys_info = _make_sysinfo(home)
    cp = [CustomPathItem(path=str(home / "f.txt"), is_dir=False, is_selected=True)]

    engine_noforce = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[],
        custom_paths=cp,
        output_path=out_dir,
        archive_format="dir",
        mountinfo_path=fake_mountinfo,
        force=False
    )
    with pytest.raises(Fat32LimitError):
        engine_noforce.run()
