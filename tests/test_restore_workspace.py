"""
DistroNomad - Geri Yükleme Çalışma Dizini ve Hata Gösterimi Testleri.

Arşiv /tmp yerine (birçok dağıtımda RAM'de tutulan, belleğin yarısıyla sınırlı bir tmpfs) ev dizininin
diskindeki önbellek dizininde açılır; yer yetmiyorsa açmaya başlamadan anlaşılır bir hata verilir ve
TUI hatayı çökmeden gösterir.
"""

import errno
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path

import pytest
from textual.widgets import Button, RichLog, TabbedContent

from distronomad import restore as restore_mod
from distronomad.cli import build_parser, cmd_restore
from distronomad.i18n import t
from distronomad.models import RestoreOptions, SystemInfo, format_bytes
from distronomad.restore import RestoreEngine
from distronomad.ui.app import DistroNomadApp


def _make_backup(dest: Path, total_bytes: int = 10) -> Path:
    """Yalnızca manifest ve bir ayar dosyası içeren en küçük geçerli yedek."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1.0.0",
        "created_at": "2026-11-23T10:00:00",
        "system_info": {
            "distro_name": "Pardus",
            "distro_id": "pardus",
            "distro_like": "debian",
            "kernel_version": "6.0.0",
            "desktop_environment": "KDE",
            "shell": "/bin/bash",
            "home_dir": "/home/kaynak",
            "available_package_managers": ["apt"],
        },
        "packages": [],
        "configs": [{
            "id": "cfg_bashrc",
            "name": "bashrc",
            "category": "Kabuk",
            "source_path": "/home/kaynak/.bashrc",
            "rel_path": ".bashrc",
            "is_selected": True,
            "size_bytes": 10,
        }],
        "custom_paths": [],
        "archive_format": "tar.gz",
        "total_bytes": total_bytes,
        "file_hashes": {},
        "backup_type": "full",
        "hardware_info": {},
    }
    work = dest.parent / f"{dest.name}.work"
    work.mkdir()
    (work / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (work / "bashrc").write_text("alias l='ls'\n", encoding="utf-8")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(work / "manifest.json", arcname="manifest.json")
        tar.add(work / "bashrc", arcname="configs/.bashrc")
    return dest


def _engine(tmp_path, total_bytes=10):
    engine = RestoreEngine(backup_path=_make_backup(tmp_path / "yedek.tar.gz", total_bytes), transaction_dir=tmp_path / "tx")
    engine.load_manifest(extract_files=False)
    return engine


def _restore(engine, tmp_path):
    target = SystemInfo(distro_id="pardus", distro_like="debian", available_package_managers=["apt"], home_dir=str(tmp_path / "hedef"))
    options = RestoreOptions(
        target_home=str(tmp_path / "hedef"),
        selected_configs=list(engine.manifest.configs),
        install_packages=False,
    )
    return engine.execute_restore(options, target_system_info=target)


def _cache_base() -> Path:
    return Path(os.environ["XDG_CACHE_HOME"]) / "distronomad" / "tmp"


def test_restore_unpacks_in_cache_dir_and_cleans_up(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    used = []
    real_extract = RestoreEngine._extract_archive

    def recording_extract(self, archive_file, target_dir):
        used.append(Path(target_dir))
        return real_extract(self, archive_file, target_dir)

    monkeypatch.setattr(RestoreEngine, "_extract_archive", recording_extract)
    res = _restore(engine, tmp_path)
    engine.cleanup()

    assert used and used[0].parent == _cache_base()
    assert (tmp_path / "hedef" / ".bashrc").read_text(encoding="utf-8") == "alias l='ls'\n"
    assert res["restored_configs"]
    # Geçici dizin silindi
    assert not any(p.name.startswith("distronomad_restore_") for p in _cache_base().iterdir())


def test_restore_refuses_to_unpack_without_free_space(tmp_path, monkeypatch):
    big = 10 * 1024 ** 3
    engine = _engine(tmp_path, total_bytes=big)
    monkeypatch.setattr(restore_mod, "_free_bytes", lambda path: 50 * 1024 ** 2)
    monkeypatch.setattr(RestoreEngine, "_extract_archive", lambda *a, **k: pytest.fail("yer yokken açmaya başlanmamalı"))

    with pytest.raises(OSError) as exc:
        _restore(engine, tmp_path)

    assert exc.value.errno == errno.ENOSPC
    msg = str(exc.value)
    assert format_bytes(big) in msg and format_bytes(50 * 1024 ** 2) in msg
    assert not (tmp_path / "hedef" / ".bashrc").exists()


def test_restore_falls_back_to_system_temp_when_home_disk_is_full(tmp_path, monkeypatch):
    engine = _engine(tmp_path, total_bytes=1024 ** 3)
    cache = _cache_base()
    monkeypatch.setattr(restore_mod, "_free_bytes", lambda path: 1024 if Path(path) == cache else 100 * 1024 ** 3)
    used = []
    real_extract = RestoreEngine._extract_archive

    def recording_extract(self, archive_file, target_dir):
        used.append(Path(target_dir))
        return real_extract(self, archive_file, target_dir)

    monkeypatch.setattr(RestoreEngine, "_extract_archive", recording_extract)
    _restore(engine, tmp_path)
    engine.cleanup()

    assert used and used[0].parent == Path(tempfile.gettempdir())


def test_disk_full_during_unpack_gives_clear_message_and_removes_partial_files(tmp_path, monkeypatch):
    engine = _engine(tmp_path)

    def full_disk(self, archive_file, target_dir):
        (Path(target_dir) / "yarim").write_bytes(b"x")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(RestoreEngine, "_extract_archive", full_disk)
    with pytest.raises(OSError) as exc:
        _restore(engine, tmp_path)

    assert exc.value.errno == errno.ENOSPC
    assert str(exc.value).endswith(t("restore_err_no_space_during_extract", path=str(_cache_base())))
    assert not any(p.name.startswith("distronomad_restore_") for p in _cache_base().iterdir())


def test_stale_work_dirs_from_crashed_runs_are_removed():
    base = _cache_base()
    base.mkdir(parents=True, exist_ok=True)
    old = base / "distronomad_restore_eski"
    fresh = base / "distronomad_restore_yeni"
    foreign = base / "baska_dizin"
    for d in (old, fresh, foreign):
        d.mkdir()
        (d / "dosya").write_bytes(b"x")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))
    os.utime(foreign, (two_days_ago, two_days_ago))

    new_dir = restore_mod._make_work_dir("distronomad_restore_")

    assert not old.exists()
    assert fresh.exists() and foreign.exists()
    assert new_dir.parent == base and new_dir.exists()


def test_cli_restore_unpacks_only_in_restore_step(tmp_path, monkeypatch):
    """CLI arşivi manifest okunurken değil, geri yükleme adımında (yer denetimiyle) açar."""
    backup = _make_backup(tmp_path / "cli_yedek.tar.gz")
    seen = {}

    def fake_execute(self, options, target_system_info=None):
        seen["source_dir"] = self.source_dir
        return {"restored_configs": [], "restored_files": [], "installed_packages": [], "failed_packages": [], "warnings": []}

    monkeypatch.setattr(RestoreEngine, "execute_restore", fake_execute)
    args = build_parser().parse_args(["restore", "--src", str(backup), "--target-home", str(tmp_path / "hedef")])
    cmd_restore(args)

    assert seen["source_dir"] is None


class _FailingEngine:
    progress_callback = None

    def execute_restore(self, options, target_system_info=None):
        raise OSError(errno.ENOSPC, "disk dolu")

    def cleanup(self):
        pass


@pytest.mark.asyncio
async def test_tui_shows_restore_error_instead_of_crashing(tmp_path):
    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(140, 45)) as pilot:
        await app.workers.wait_for_complete()
        # RichLog görünmeyen sekmede yazılanları ertelediği için geri yükleme sekmesi açılır
        app.query_one("#main-tabs", TabbedContent).active = "tab-restore"
        await pilot.pause()
        app._is_restoring = True
        app.query_one("#btn-start-restore", Button).disabled = True

        app._run_restore_worker(_FailingEngine(), RestoreOptions())
        await app.workers.wait_for_complete()
        await pilot.pause()

        log = app.query_one("#restore-log", RichLog)
        text = " ".join(" ".join(strip.text for strip in log.lines).split())
        assert "disk dolu" in text
        assert t("restore_log_failed", error="").strip() in text
        assert app._is_restoring is False
        assert app.query_one("#btn-start-restore", Button).disabled is False
