"""
DistroNomad - Yayın Sonrası Düzeltme Testleri.

Kapsam:
1. Doğrulama: Yedeklenen uygulamaların kendi manifest.json dosyaları (tarayıcı eklentileri, Widevine)
   yedeğin manifesti sanılmaz; yedek sağlam görünür ve bu dosyaların hash'leri de doğrulanır.
2. Manifest okuyucu: Yalnızca kökteki manifest.json kabul edilir; kök manifest yoksa iç içe olan kullanılmaz.
3. Açık tarayıcı uyarısı: Profili yedeğe seçili ve açık olan tarayıcılar algılanır; TUI başlamadan onay sorar,
   CLI uyarı yazar.
4. Arşiv tutarlılığı: SHA-256 arşive yazılan baytlardan aynı okumada alınır ve kesin manifest arşivin sonuna eklenir;
   yedekleme sırasında değişen, kısalan veya sabit bağlantılı dosyalar arşivi ya da doğrulamayı bozmaz.
"""

from __future__ import annotations
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
import pytest
import zstandard as zstd

from distronomad import cli, discovery
from distronomad.archive import read_manifest_from_backup
from distronomad.backup import BackupEngine
from distronomad.discovery import find_running_browsers
from distronomad.i18n import t
from distronomad.models import ConfigItem, RestoreOptions, SystemInfo
from distronomad.restore import RestoreEngine
from distronomad.verify import verify_backup


NESTED_MANIFESTS = (
    ".mozilla/firefox/abcd.default-release/gmp-widevinecdm/4.10.0.0/manifest.json",
    ".config/BraveSoftware/Brave-Browser/aaaabbbbccccddddeeeeffffgggghhhh/1.0.0/manifest.json",
)


def _write(path: Path, data: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def _empty_etc(tmp_path: Path) -> Path:
    etc_dir = tmp_path / "mock_etc"
    etc_dir.mkdir(exist_ok=True)
    return etc_dir


def _home_with_nested_manifests(tmp_path: Path) -> Path:
    """İçinde eklenti manifestleri bulunan Firefox ve Brave profilleri olan bir ev dizini kurar."""
    home = tmp_path / "src_home"
    _write(home / ".bashrc", "export A=1\n")
    for rel in NESTED_MANIFESTS:
        _write(home / rel, json.dumps({"name": "Eklenti", "manifest_version": 3, "version": "1.0"}))
    _write(home / ".mozilla/firefox/abcd.default-release/prefs.js", 'user_pref("a", 1);\n')
    return home


def _configs(home: Path) -> list:
    return [
        ConfigItem(
            id=f"cfg_{rel}",
            name=rel,
            category="Test",
            source_path=str(home / rel),
            rel_path=rel,
            is_directory=(home / rel).is_dir(),
            is_selected=True
        )
        for rel in (".bashrc", ".mozilla/firefox", ".config/BraveSoftware")
    ]


def _tar_gz(path: Path, members: list) -> Path:
    """Verilen (ad, içerik) sırasıyla elle bir tar.gz arşivi oluşturur."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


# ============================================================================
# 1. DOĞRULAMA: İÇ İÇE manifest.json DOSYALARI
# ============================================================================

@pytest.mark.parametrize("archive_format,password,out_name", [
    ("tar.zst", None, "b.tar.zst"),
    ("tar.gz", None, "b.tar.gz"),
    ("tar.zst", "Parola-1", "b.tar.zst.enc"),
])
def test_verify_ignores_nested_manifest_files(tmp_path, archive_format, password, out_name):
    """Tarayıcı eklentilerinin manifest.json dosyaları yedeğin manifesti sanılmamalı, yedek sağlam görünmelidir."""
    home = _home_with_nested_manifests(tmp_path)
    archive = BackupEngine(
        SystemInfo(distro_id="arch", home_dir=str(home)), [], _configs(home), [], tmp_path / out_name,
        archive_format=archive_format, password=password, etc_dir=_empty_etc(tmp_path)
    ).run()

    res = verify_backup(archive, password=password)

    assert res.is_valid, (res.error_message, res.untracked[:5], res.missing[:5], res.mismatched[:5])
    for rel in NESTED_MANIFESTS:
        assert f"configs/{rel}" in res.matched, "Eklenti manifestleri sıradan dosya olarak doğrulanmalıdır."
    manifest = read_manifest_from_backup(archive, password=password)
    assert manifest is not None and manifest.file_hashes


def test_verify_after_append_with_nested_manifests(tmp_path):
    """append ile eklenen tarayıcı profillerindeki manifest.json dosyaları doğrulamayı bozmamalıdır."""
    home = _home_with_nested_manifests(tmp_path)
    si = SystemInfo(distro_id="arch", home_dir=str(home))
    configs = _configs(home)
    base = BackupEngine(si, [], configs[:1], [], tmp_path / "base.tar.zst", etc_dir=_empty_etc(tmp_path)).run()
    appended = BackupEngine(si, [], configs[1:], [], base, backup_mode="append", etc_dir=_empty_etc(tmp_path)).run()

    res = verify_backup(appended)

    assert res.is_valid, (res.error_message, res.untracked[:5], res.missing[:5], res.mismatched[:5])
    for rel in NESTED_MANIFESTS:
        assert f"configs/{rel}" in res.matched


# ============================================================================
# 2. MANİFEST OKUYUCU: YALNIZCA KÖKTEKİ manifest.json
# ============================================================================

def test_read_manifest_uses_only_root_manifest(tmp_path):
    """İç içe manifest.json arşivde önce gelse de kökteki okunmalı; kök yoksa iç içe olan kullanılmamalıdır."""
    root = json.dumps({"version": "0.9.0", "backup_type": "full", "file_hashes": {"configs/a": "0" * 64}}).encode()
    nested = json.dumps({"name": "Eklenti", "manifest_version": 3, "version": "1.0"}).encode()

    nested_first = _tar_gz(tmp_path / "nested_first.tar.gz", [("configs/ext/manifest.json", nested), ("manifest.json", root)])
    manifest = read_manifest_from_backup(nested_first)
    assert manifest is not None and list(manifest.file_hashes) == ["configs/a"]

    only_nested = _tar_gz(tmp_path / "only_nested.tar.gz", [("configs/ext/manifest.json", nested)])
    assert read_manifest_from_backup(only_nested) is None
    res = verify_backup(only_nested)
    assert res.corrupted and not res.is_valid
    assert res.error_message == t("verify_err_manifest_not_found_in_archive")


# ============================================================================
# 3. AÇIK TARAYICI UYARISI
# ============================================================================

def _fake_pgrep(monkeypatch, stdout: str, returncode: int = 0) -> list:
    """pgrep çağrısını taklit eder ve verilen komutları kaydeder."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/bin/pgrep" if name == "pgrep" else None)
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    return calls


def test_find_running_browsers_matches_only_selected_profiles(tmp_path, monkeypatch):
    """Yalnızca profili seçili yollarla çakışan açık tarayıcılar bildirilmelidir."""
    home = tmp_path / "home"
    calls = _fake_pgrep(monkeypatch, "1234 firefox\n5678 brave\n")

    assert find_running_browsers([str(home / ".mozilla/firefox")], home_dir=str(home)) == ["Firefox"]
    assert find_running_browsers(
        [str(home / ".config/BraveSoftware"), str(home / ".mozilla/firefox")], home_dir=str(home)
    ) == ["Firefox", "Brave"]
    # Profilin üst dizini seçiliyse (ör. ~/.config/mozilla veya tüm ev dizini) de çakışır
    assert find_running_browsers([str(home / ".config/mozilla")], home_dir=str(home)) == ["Firefox"]
    assert find_running_browsers([str(home)], home_dir=str(home)) == ["Firefox", "Brave"]
    # Profili seçili olmayan açık tarayıcı uyarı gerektirmez
    assert find_running_browsers([str(home / "Documents"), str(home / ".bashrc")], home_dir=str(home)) == []

    cmd = calls[0]
    assert cmd[:3] == ["pgrep", "-l", "-x"] and "-u" in cmd
    assert {"firefox", "brave", "chrome", "thunderbird"} <= set(cmd[-1].split("|"))


def test_find_running_browsers_without_running_process_or_pgrep(tmp_path, monkeypatch):
    """Açık tarayıcı yoksa veya pgrep bulunamazsa uyarı üretilmemelidir."""
    home = tmp_path / "home"
    _fake_pgrep(monkeypatch, "", returncode=1)
    assert find_running_browsers([str(home / ".mozilla/firefox")], home_dir=str(home)) == []

    monkeypatch.setattr(discovery.shutil, "which", lambda name: None)
    assert find_running_browsers([str(home / ".mozilla/firefox")], home_dir=str(home)) == []


@pytest.mark.parametrize("running", [["Firefox"], []])
def test_cli_backup_warns_when_browser_running(tmp_path, monkeypatch, capsys, running):
    """CLI yedeklemesi seçili profili açık olan tarayıcı için uyarı yazmalı, yoksa yazmamalıdır."""
    home = _home_with_nested_manifests(tmp_path)
    seen = {}

    def fake_find(paths, home_dir=None):
        seen["paths"] = list(paths)
        return running

    monkeypatch.setattr(cli, "find_running_browsers", fake_find)
    monkeypatch.setattr(cli.BackupEngine, "run", lambda self: self.output_path)
    args = cli.build_parser().parse_args(["--home", str(home), "backup", "--dest", str(tmp_path / "out.tar.zst")])

    cli.cmd_backup(args)

    out = capsys.readouterr().out
    assert str(home / ".mozilla/firefox") in seen["paths"]
    assert ("Şu anda açık" in out and "Firefox" in out) is bool(running)


@pytest.mark.asyncio
@pytest.mark.parametrize("button,should_start", [("#btn-browser-cancel", False), ("#btn-browser-proceed", True)])
async def test_tui_asks_before_backup_when_browser_running(tmp_path, monkeypatch, button, should_start):
    """TUI açık tarayıcı varken yedeklemeden önce onay sormalı; iptalde başlamamalı, onayda başlamalıdır."""
    from textual.widgets import Input
    from distronomad.ui.app import DistroNomadApp
    from distronomad.ui.modal import BrowserRunningModal

    monkeypatch.setattr("distronomad.ui.app.find_running_browsers", lambda paths, home_dir=None: ["Firefox"])
    started = []
    monkeypatch.setattr(DistroNomadApp, "_run_backup_worker", lambda self, *args, **kwargs: started.append(args))

    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test(size=(120, 60)) as pilot:
        app.query_one("#input-backup-dest", Input).value = str(tmp_path / "b.tar.zst")

        app._handle_start_backup()
        await pilot.pause()
        assert isinstance(app.screen, BrowserRunningModal)

        await pilot.click(button)
        await pilot.pause()

        assert not isinstance(app.screen, BrowserRunningModal)
        assert bool(started) is should_start


# ============================================================================
# 4. ARŞİV TUTARLILIĞI: TEK OKUMADA HASH VE KESİN MANİFEST
# ============================================================================

def _dir_config(home: Path, rel: str) -> list:
    return [ConfigItem(id=f"cfg_{rel}", name=rel, category="Test", source_path=str(home / rel), rel_path=rel, is_directory=True, is_selected=True)]


def _archive_members(archive: Path) -> list:
    with open(archive, "rb") as f_in, zstd.ZstdDecompressor().stream_reader(f_in) as reader:
        with tarfile.open(fileobj=reader, mode="r|*") as tar:
            return [(m.name, m.type) for m in tar]


def test_file_changed_after_hashing_is_archived_consistently(tmp_path, monkeypatch):
    """Ön taramadan sonra değişen dosya (açık tarayıcının veritabanı gibi) arşivle tutarlı kaydedilmeli;
    doğrulama sağlam demeli ve dosyayı uyarıda göstermelidir."""
    home = tmp_path / "src_home"
    profile = home / ".mozilla/firefox/abcd.default-release"
    _write(profile / "places.sqlite", "v1 - ön tarama\n")
    _write(profile / "prefs.js", 'user_pref("a", 1);\n')
    orig_notify = BackupEngine._notify

    def notify_and_write(self, phase, message, *args, **kwargs):
        # Ön tarama ve baştaki manifest bitti; akış başlamadan tarayıcı dosyaya yazıyor
        if phase == "sıkıştırma":
            (profile / "places.sqlite").write_text("v2 - yedekleme sırasında yazıldı\n", encoding="utf-8")
        return orig_notify(self, phase, message, *args, **kwargs)

    monkeypatch.setattr(BackupEngine, "_notify", notify_and_write)
    archive = BackupEngine(
        SystemInfo(distro_id="arch", home_dir=str(home)), [], _dir_config(home, ".mozilla/firefox"), [],
        tmp_path / "ff.tar.zst", etc_dir=_empty_etc(tmp_path)
    ).run()

    res = verify_backup(archive)

    assert res.is_valid, (res.mismatched, res.missing, res.untracked)
    assert any("places.sqlite" in w for w in res.warnings)
    names = [name for name, _ in _archive_members(archive)]
    assert names[0] == "manifest.json" and names[-1] == "manifest.json", "Başta inceleme, sonda kesin manifest olmalıdır."

    target = tmp_path / "target"
    engine = RestoreEngine(backup_path=archive)
    try:
        manifest = engine.load_manifest(extract_files=True)
        options = RestoreOptions(target_home=str(target), selected_configs=manifest.configs, install_packages=False, restore_system_state=False)
        engine.execute_restore(options, target_system_info=SystemInfo(distro_id="arch", home_dir=str(target)))
    finally:
        engine.cleanup()
    restored = target / ".mozilla/firefox/abcd.default-release/places.sqlite"
    assert restored.read_text(encoding="utf-8") == "v2 - yedekleme sırasında yazıldı\n"


def test_file_shrinking_while_archived_keeps_archive_readable(tmp_path, monkeypatch):
    """Arşive eklenirken kısalan dosya (ör. sıfırlanan bir -wal dosyası) arşivin geri kalanını bozmamalıdır."""
    home = tmp_path / "src_home"
    app_dir = home / ".config/app"
    shrinking = _write(app_dir / "history.sqlite-wal", "w" * 200000)
    for i in range(5):
        _write(app_dir / f"settings_{i}.conf", f"ayar {i}\n")
    orig_gettarinfo = tarfile.TarFile.gettarinfo

    def gettarinfo_then_shrink(self, name=None, arcname=None, fileobj=None):
        tarinfo = orig_gettarinfo(self, name, arcname, fileobj)
        if arcname and arcname.endswith("history.sqlite-wal"):
            os.truncate(shrinking, 1000)  # boyut başlığa yazıldıktan sonra uygulama dosyayı sıfırlıyor
        return tarinfo

    monkeypatch.setattr(tarfile.TarFile, "gettarinfo", gettarinfo_then_shrink)
    archive = BackupEngine(
        SystemInfo(distro_id="arch", home_dir=str(home)), [], _dir_config(home, ".config/app"), [],
        tmp_path / "wal.tar.zst", etc_dir=_empty_etc(tmp_path)
    ).run()

    res = verify_backup(archive)

    assert res.is_valid and not res.corrupted, (res.error_message, res.mismatched, res.missing)
    for i in range(5):
        assert f"configs/.config/app/settings_{i}.conf" in res.matched
    assert any("history.sqlite-wal" in w for w in res.warnings)


def test_hard_linked_files_are_archived_with_content(tmp_path):
    """Sabit bağlantılı dosyalar içerik taşıyan normal girdi olarak yazılmalı ve doğrulanmalıdır."""
    home = tmp_path / "src_home"
    app_dir = home / ".config/app"
    first = _write(app_dir / "a.txt", "ortak içerik\n")
    os.link(first, app_dir / "b.txt")
    archive = BackupEngine(
        SystemInfo(distro_id="arch", home_dir=str(home)), [], _dir_config(home, ".config/app"), [],
        tmp_path / "links.tar.zst", etc_dir=_empty_etc(tmp_path)
    ).run()

    res = verify_backup(archive)

    assert res.is_valid, (res.missing, res.untracked)
    assert {"configs/.config/app/a.txt", "configs/.config/app/b.txt"} <= set(res.matched)
    types = {name: kind for name, kind in _archive_members(archive) if name.endswith((".config/app/a.txt", ".config/app/b.txt"))}
    assert set(types.values()) == {tarfile.REGTYPE}
