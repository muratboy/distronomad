"""
DistroNomad - Yayın Öncesi Son İnceleme Düzeltmeleri Testleri.

Kapsam:
1. Rollback: Aynı geri yükleme ikinci kez geri alınamaz; ilk geri almadan sonra oluşturulan kullanıcı dosyaları korunur.
2. Paket kurulumu: Tek tek kurulum döngüsü "paket bulunamadı" hatalarında durmaz; yalnızca art arda 3 sudo yetki hatasında durur.
3. Boş dizinler: tar.zst, şifreli, dir ve append yedeklerinde korunur (yalnızca FIFO içeren dizin dahil).
4. EFBIG: Arşiv yazımı EFBIG ile kesilirse aynı hedefteki önceki sağlam yedek silinmez.
5. Zamanlayıcı: Taşınabilir (PyInstaller) sürümde birim dosyası yazılmaz, nedeni kullanıcıya gösterilir.
6. Parola: main() DISTRONOMAD_PASSWORD'ü ortamdan çıkarır; komutlar parolayı yine de kullanır.
7. Ev dışı yollar: Göreli veya '..' ile kaçan yollar yazılmaz; geçerli mutlak yollar yazılmaya devam eder.
8. Append: Özel yollardaki 'cache' dizinleri tam yedekte olduğu gibi korunur.
9. i18n: Her t() çağrısı şablonun beklediği tüm yer tutucuları verir (aksi halde ekranda ham '{alan}' görünür).
"""

from __future__ import annotations
import ast
import errno
import hashlib
import json
import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Optional
import pytest

from distronomad import cli
from distronomad.automation import enable_timer
from distronomad.backup import BackupEngine, Fat32LimitError
from distronomad.i18n import TRANSLATIONS, t
from distronomad.models import ConfigItem, CustomPathItem, PackageItem, RestoreOptions, SystemInfo
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.verify import verify_backup


EMPTY_DIRS = (".ssh/sockets", "Projects/empty_sub", "Projects/only_fifo")


def _write(path: Path, data: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def _empty_etc(tmp_path: Path) -> Path:
    etc_dir = tmp_path / "mock_etc"
    etc_dir.mkdir(exist_ok=True)
    return etc_dir


def _make_source_home(tmp_path: Path) -> Path:
    """Boş dizin, yalnızca FIFO içeren dizin ve 'cache' dizini barındıran kaynak ev dizini kurar."""
    home = tmp_path / "src_home"
    _write(home / ".bashrc", "export A=1\n")
    _write(home / ".ssh" / "config", "Host *\n  ControlPath ~/.ssh/sockets/%r@%h-%p\n")
    (home / ".ssh" / "sockets").mkdir(parents=True)
    _write(home / "Projects" / "notes.txt", "notlar\n")
    (home / "Projects" / "empty_sub").mkdir(parents=True)
    (home / "Projects" / "only_fifo").mkdir(parents=True)
    os.mkfifo(home / "Projects" / "only_fifo" / "pipe")
    _write(home / "Projects" / "myapp" / "src" / "cache" / "helper.py", "def f():\n    pass\n")
    return home


def _system_info(home: Path) -> SystemInfo:
    return SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home))


def _configs(home: Path, names=(".bashrc", ".ssh")) -> list:
    return [
        ConfigItem(
            id=f"cfg{name}",
            name=name,
            category="Test",
            source_path=str(home / name),
            rel_path=name,
            is_directory=(home / name).is_dir(),
            is_selected=True
        )
        for name in names
    ]


def _projects(home: Path) -> list:
    return [CustomPathItem(path=str(home / "Projects"), label="Projects")]


def _restore_with(archive: Path, target_home: Path, password: Optional[str] = None, **extra_options) -> dict:
    """Yedekteki tüm konfigürasyon ve özel yolları hedef ev dizinine geri yükler."""
    engine = RestoreEngine(backup_path=archive, password=password)
    try:
        manifest = engine.load_manifest(extract_files=True)
        options = RestoreOptions(
            target_home=str(target_home),
            selected_configs=manifest.configs,
            selected_custom_paths=manifest.custom_paths,
            install_packages=False,
            restore_system_state=False,
            **extra_options
        )
        return engine.execute_restore(options, target_system_info=SystemInfo(distro_id="arch", home_dir=str(target_home)))
    finally:
        engine.cleanup()


# ============================================================================
# 1. ROLLBACK İKİNCİ KEZ ÇALIŞTIRILAMAZ
# ============================================================================

def test_second_rollback_is_refused_and_keeps_new_user_files(tmp_path):
    """İlk geri almadan sonra aynı yollarda oluşturulan kullanıcı dosyaları ikinci geri almada silinmemelidir."""
    home = _make_source_home(tmp_path)
    archive = BackupEngine(
        _system_info(home), [], _configs(home, (".bashrc",)), _projects(home),
        tmp_path / "rb.tar.zst", etc_dir=_empty_etc(tmp_path)
    ).run()
    target = tmp_path / "target"
    _restore_with(archive, target)
    assert (target / "Projects" / "notes.txt").exists()

    first = rollback_restore(target_home=target)
    assert first["success"]
    assert not (target / "Projects").exists()
    assert not (target / ".bashrc").exists()

    # Kullanıcı geri almadan sonra aynı yollarda yeni veri oluşturur
    _write(target / "Projects" / "new_work.txt", "yeni çalışma\n")
    _write(target / ".bashrc", "kullanıcının yeni ayarı\n")

    with pytest.raises(RuntimeError) as exc:
        rollback_restore(target_home=target)
    assert "geri alınmış" in str(exc.value)

    assert (target / "Projects" / "new_work.txt").read_text(encoding="utf-8") == "yeni çalışma\n"
    assert (target / ".bashrc").read_text(encoding="utf-8") == "kullanıcının yeni ayarı\n"


# ============================================================================
# 2. PAKET KURULUMU: TEK TEK DENEME DÖNGÜSÜ
# ============================================================================

def _fake_pacman(monkeypatch, single_result):
    """'sudo pacman -S ... -- <paketler>' çağrılarını yakalar; toplu kurulum hep başarısız olur,
    tekil kurulumun sonucu single_result(paket) -> (returncode, stderr) ile belirlenir.
    Diğer komutlar conftest'in sahte subprocess.run'ına bırakılır."""
    prev_run = subprocess.run
    calls = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)):
            cmd_list = [str(c) for c in cmd]
            if cmd_list[:3] == ["sudo", "-n", "true"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd_list[:2] == ["sudo", "pacman"] and "--" in cmd_list:
                pkgs = cmd_list[cmd_list.index("--") + 1:]
                calls.append(pkgs)
                if len(pkgs) > 1:
                    return subprocess.CompletedProcess(cmd, 1, "", "error: failed to prepare transaction\n")
                rc, err = single_result(pkgs[0])
                return subprocess.CompletedProcess(cmd, rc, "", err)
        return prev_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _restore_packages(tmp_path: Path, names: list) -> dict:
    """Yalnızca paket listesi içeren bir yedek alıp paketleri pacman ile kurmayı dener."""
    home = tmp_path / "pkg_home"
    home.mkdir()
    archive = BackupEngine(
        _system_info(home), [PackageItem(name=n, manager="pacman") for n in names], [], [],
        tmp_path / "pkgs.tar.zst", etc_dir=_empty_etc(tmp_path)
    ).run()
    target = tmp_path / "pkg_target"
    engine = RestoreEngine(backup_path=archive)
    try:
        manifest = engine.load_manifest(extract_files=True)
        options = RestoreOptions(
            target_home=str(target),
            selected_packages=manifest.packages,
            install_packages=True,
            restore_system_state=False
        )
        target_info = SystemInfo(distro_id="arch", home_dir=str(target), available_package_managers=["pacman"])
        return engine.execute_restore(options, target_system_info=target_info)
    finally:
        engine.cleanup()


def test_package_fallback_continues_past_not_found_errors(tmp_path, monkeypatch):
    """Art arda bulunamayan paketler döngüyü durdurmamalı; sonraki paketler yine kurulmalıdır."""
    names = ["bash", "cachy-update", "cachyos-hello", "cachyos-hooks", "cachyos-kde-settings", "firefox", "git", "vim", "zsh"]
    calls = _fake_pacman(
        monkeypatch,
        lambda pkg: (1, f"error: target not found: {pkg}\n") if pkg.startswith("cachy") else (0, "")
    )

    res = _restore_packages(tmp_path, names)

    assert [c[0] for c in calls if len(c) == 1] == names, "Her paket tek tek denenmelidir."
    assert res["installed_packages"] == ["bash", "firefox", "git", "vim", "zsh"]
    assert t("restore_warn_consecutive_failures_exit", pm="pacman") not in res["warnings"]


def test_package_fallback_stops_after_three_sudo_auth_failures(tmp_path, monkeypatch):
    """Art arda 3 sudo yetki hatasında döngü erken durmalı ve uyarı vermelidir."""
    names = ["bash", "firefox", "git", "vim", "zsh"]
    calls = _fake_pacman(monkeypatch, lambda pkg: (1, "sudo: a password is required\n"))

    res = _restore_packages(tmp_path, names)

    assert [c[0] for c in calls if len(c) == 1] == ["bash", "firefox", "git"]
    assert res["installed_packages"] == []
    assert t("restore_warn_consecutive_failures_exit", pm="pacman") in res["warnings"]


# ============================================================================
# 3. BOŞ DİZİNLER VE ÖZEL YOLLARDAKİ CACHE DİZİNLERİ
# ============================================================================

@pytest.mark.parametrize("archive_format,password,out_name", [
    ("tar.zst", None, "empty_dirs.tar.zst"),
    ("tar.zst", "Bos-Dizin-Parola", "empty_dirs.tar.zst.enc"),
    ("dir", None, "empty_dirs_dir"),
])
def test_empty_dirs_survive_backup_and_restore(tmp_path, archive_format, password, out_name):
    """Boş dizinler (yalnızca FIFO içeren dahil) yedeğe girmeli ve geri yüklenmelidir."""
    home = _make_source_home(tmp_path)
    archive = BackupEngine(
        _system_info(home), [], _configs(home), _projects(home), tmp_path / out_name,
        archive_format=archive_format, password=password, etc_dir=_empty_etc(tmp_path)
    ).run()
    assert verify_backup(archive, password=password).is_valid

    target = tmp_path / "target"
    _restore_with(archive, target, password=password)

    for rel in EMPTY_DIRS:
        assert (target / rel).is_dir(), f"Boş dizin geri yüklenmedi: {rel}"
    assert not (target / "Projects" / "only_fifo" / "pipe").exists(), "FIFO yedeğe girmemelidir."
    assert (target / ".ssh" / "config").exists()
    assert (target / "Projects" / "myapp" / "src" / "cache" / "helper.py").exists()


def _append_round_trip(tmp_path: Path, password: Optional[str]) -> Path:
    """Taban yedeğe append ile .ssh ve Projects ekler, sonucu doğrulayıp geri yükler; hedef ev dizinini döndürür."""
    home = _make_source_home(tmp_path)
    si = _system_info(home)
    out_name = "base.tar.zst.enc" if password else "base.tar.zst"
    base = BackupEngine(
        si, [], _configs(home, (".bashrc",)), [], tmp_path / out_name,
        password=password, etc_dir=_empty_etc(tmp_path)
    ).run()
    appended = BackupEngine(
        si, [], _configs(home, (".ssh",)), _projects(home), base,
        backup_mode="append", password=password, etc_dir=_empty_etc(tmp_path)
    ).run()
    assert verify_backup(appended, password=password).is_valid

    target = tmp_path / "target"
    _restore_with(appended, target, password=password)
    assert (target / ".bashrc").exists()
    return target


@pytest.mark.parametrize("password", [None, "Append-Parola-1"])
def test_append_keeps_empty_dirs(tmp_path, password):
    """append ile eklenen konfigürasyon ve özel yollardaki boş dizinler korunmalıdır."""
    target = _append_round_trip(tmp_path, password)
    for rel in EMPTY_DIRS:
        assert (target / rel).is_dir(), f"append sonrası boş dizin kayboldu: {rel}"


def test_append_keeps_custom_path_cache_dirs(tmp_path):
    """Özel yollardaki 'cache' dizini tam yedekte olduğu gibi append'de de korunmalıdır."""
    target = _append_round_trip(tmp_path, None)
    assert (target / "Projects" / "myapp" / "src" / "cache" / "helper.py").exists()


# ============================================================================
# 4. EFBIG: ÖNCEKİ SAĞLAM YEDEK KORUNUR
# ============================================================================

def test_efbig_does_not_delete_previous_backup_at_same_destination(tmp_path, monkeypatch):
    """Aynı hedefe alınan yeni yedek EFBIG ile kesilirse önceki yedek değişmeden kalmalıdır."""
    import zstandard

    home = _make_source_home(tmp_path)
    out = tmp_path / "scheduled.tar.zst"

    def run_backup():
        return BackupEngine(
            _system_info(home), [], _configs(home, (".bashrc",)), [], out,
            force=True, etc_dir=_empty_etc(tmp_path)
        ).run()

    run_backup()
    before = hashlib.sha256(out.read_bytes()).hexdigest()

    def efbig_writer(*args, **kwargs):
        raise OSError(errno.EFBIG, "File too large")

    with monkeypatch.context() as m:
        m.setattr(zstandard.ZstdCompressor, "stream_writer", efbig_writer)
        with pytest.raises(Fat32LimitError):
            run_backup()

    assert out.exists(), "Önceki sağlam yedek silinmemelidir."
    assert hashlib.sha256(out.read_bytes()).hexdigest() == before
    assert verify_backup(out).is_valid
    leftovers = list(tmp_path.glob(".tmp_*")) + list(tmp_path.glob("distronomad_meta_*"))
    assert leftovers == []


# ============================================================================
# 5. ZAMANLAYICI: TAŞINABİLİR SÜRÜM
# ============================================================================

def test_enable_timer_refuses_portable_build_and_writes_no_units(tmp_path, monkeypatch):
    """PyInstaller sürümünde (sys.frozen) birim dosyaları yazılmamalı ve neden döndürülmelidir."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    systemd_dir = tmp_path / "systemd_user"

    res = enable_timer(interval="daily", backup_output_dir=tmp_path / "out", systemd_dir=systemd_dir)

    assert res["success"] is False
    assert res["message"] == t("automation_err_portable_unsupported")
    assert not (systemd_dir / "distronomad-backup.service").exists()
    assert not (systemd_dir / "distronomad-backup.timer").exists()


def test_cli_schedule_enable_shows_portable_reason(monkeypatch, capsys):
    """CLI 'schedule --enable' taşınabilir sürümde nedeni yazdırıp 1 ile çıkmalıdır."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli, "_ENV_PASSWORD", None, raising=False)
    monkeypatch.setattr(cli, "setup_cli_logging", lambda: None)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--lang", "tr", "schedule", "--enable"])

    assert exc.value.code == 1
    assert "taşınabilir (USB)" in capsys.readouterr().out
    systemd_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "systemd" / "user"
    assert not (systemd_dir / "distronomad-backup.service").exists()


# ============================================================================
# 6. PAROLA ORTAM DEĞİŞKENİ ALT SÜREÇLERE AKTARILMAZ
# ============================================================================

def test_main_removes_env_password_but_backup_still_uses_it(tmp_path, monkeypatch):
    """main() parolayı ortamdan çıkarmalı; yedekleme motoru parolayı yine almalıdır."""
    # main() parolayı modül düzeyinde saklar; test sonunda eski değere dönülsün ki diğer testler etkilenmesin
    monkeypatch.setattr(cli, "_ENV_PASSWORD", None, raising=False)
    monkeypatch.setattr(cli, "setup_cli_logging", lambda: None)
    monkeypatch.setenv("DISTRONOMAD_PASSWORD", "EnvOnly-Parola-42")
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("getpass çağrılmamalıydı!"))
    seen = {}

    def fake_run(self):
        seen["env_during_run"] = os.environ.get("DISTRONOMAD_PASSWORD")
        seen["password"] = self.password
        return self.output_path

    monkeypatch.setattr(cli.BackupEngine, "run", fake_run)
    home = tmp_path / "src_home"
    _write(home / ".bashrc", "export A=1\n")

    cli.main(["--home", str(home), "backup", "--dest", str(tmp_path / "env.tar.zst.enc"), "--password"])

    assert seen["password"] == "EnvOnly-Parola-42"
    assert seen["env_during_run"] is None, "Alt süreçler parolayı ortamdan devralmamalıdır."
    assert "DISTRONOMAD_PASSWORD" not in os.environ


# ============================================================================
# 7. EV DIŞI YOLLAR: GÖRELİ VE '..' İÇEREN YOLLAR
# ============================================================================

def _external_path_backup(tmp_path: Path):
    """Ev dışı geçerli bir özel yol içeren dizin biçiminde yedek alır ve manifeste kötü niyetli girdiler ekler."""
    home = tmp_path / "src_home"
    _write(home / ".bashrc", "export A=1\n")
    ext_file = _write(tmp_path / "ext_src" / "app.conf", "geçerli\n")
    backup_dir = BackupEngine(
        _system_info(home), [], [], [CustomPathItem(path=str(ext_file), label="ext", is_dir=False)],
        tmp_path / "ext_backup", archive_format="dir", etc_dir=_empty_etc(tmp_path)
    ).run()

    # Göreli yol: gerçek ev dizinine geri yüklemede çalışma dizinine yazılırdı
    _write(backup_dir / "custom" / "root" / "rel_evil" / "payload.txt", "EVIL\n")
    # '..' içeren yol: hedef kök dizinin dışına (tmp_path/escape_root) çıkardı
    (backup_dir / "custom" / "root" / "opt").mkdir(parents=True, exist_ok=True)
    _write(backup_dir / "custom" / "escape_root" / "evil.txt", "EVIL\n")

    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["custom_paths"] += [
        {"path": "rel_evil/payload.txt", "label": "rel", "is_dir": False},
        {"path": "/opt/../../escape_root/evil.txt", "label": "dotdot", "is_dir": False},
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return backup_dir, ext_file


def test_external_paths_real_home_skip_relative_and_dotdot(tmp_path, monkeypatch):
    """Gerçek ev dizinine onaylı geri yüklemede geçerli mutlak yol yazılmalı, göreli ve '..' içeren yollar atlanmalıdır."""
    backup_dir, ext_file = _external_path_backup(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    ext_file.unlink()

    res = _restore_with(backup_dir, Path.home(), allow_external_paths=True)

    assert ext_file.read_text(encoding="utf-8") == "geçerli\n"
    assert not (cwd / "rel_evil").exists(), "Göreli yol çalışma dizinine yazılmamalıdır."
    assert "rel_evil/payload.txt" in res["skipped_external_paths"]
    assert "/opt/../../escape_root/evil.txt" in res["skipped_external_paths"]


def test_cli_append_normalizes_dotdot_in_absolute_paths(tmp_path, monkeypatch):
    """append --path ile verilen '..' içeren mutlak yol sadeleştirilmelidir; aksi halde geri yükleme onu atlar."""
    home = tmp_path / "src_home"
    _write(home / ".bashrc", "export A=1\n")
    data_dir = tmp_path / "srv" / "data"
    _write(data_dir / "report.txt", "rapor\n")
    base = BackupEngine(
        _system_info(home), [], _configs(home, (".bashrc",)), [], tmp_path / "base.tar.zst",
        etc_dir=_empty_etc(tmp_path)
    ).run()
    seen = {}

    def fake_run(self):
        seen["paths"] = [cp.path for cp in self.custom_paths]
        return self.output_path

    monkeypatch.setattr(cli.BackupEngine, "run", fake_run)
    args = cli.build_parser().parse_args([
        "--home", str(home), "append", "--backup", str(base), "--path", str(tmp_path / "opt" / ".." / "srv" / "data")
    ])
    cli.cmd_append(args)

    assert seen["paths"] == [str(data_dir)]


def test_external_paths_target_root_blocks_escape(tmp_path):
    """Özel hedef kök dizinde geçerli yol kökün altına yazılmalı, kökten kaçan ve göreli yollar atlanmalıdır."""
    backup_dir, ext_file = _external_path_backup(tmp_path)
    target_home = tmp_path / "new_home"
    target_root = tmp_path / "new_root"

    res = _restore_with(backup_dir, target_home, target_root=str(target_root))

    assert (target_root / str(ext_file).lstrip("/")).read_text(encoding="utf-8") == "geçerli\n"
    assert not (tmp_path / "escape_root").exists(), "Hedef kökün dışına yazılmamalıdır."
    assert not (target_root / "rel_evil").exists()
    assert "/opt/../../escape_root/evil.txt" in res["skipped_external_paths"]
    assert "rel_evil/payload.txt" in res["skipped_external_paths"]


# ============================================================================
# 9. i18n: t() ÇAĞRILARI ŞABLON YER TUTUCULARIYLA UYUMLU
# ============================================================================

def test_t_calls_pass_exactly_the_template_fields():
    """t() eksik argümanla çağrılırsa biçimlendirme sessizce atlanır ve kullanıcı ham şablonu görür;
    her çağrının şablondaki yer tutucuların tamamını (ve yalnızca onları) verdiğini doğrular."""
    formatter = string.Formatter()

    def fields(text: str) -> set:
        return {f.split(".")[0].split("[")[0] for _, f, _, _ in formatter.parse(text) if f}

    package_dir = Path(__file__).resolve().parent.parent / "distronomad"
    mismatches = []
    for py_file in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "t"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in TRANSLATIONS):
                continue
            if any(kw.arg is None for kw in node.keywords):
                continue  # **kwargs ile çağrılar statik olarak denetlenemez
            key = node.args[0].value
            given = {kw.arg for kw in node.keywords if kw.arg != "lang"}
            needed = fields(TRANSLATIONS[key]["tr"]) | fields(TRANSLATIONS[key]["en"])
            if needed - given or (given and given - needed):
                mismatches.append(f"{py_file.name}:{node.lineno} {key} gerekli={sorted(needed)} verilen={sorted(given)}")

    assert not mismatches, "Şablonla uyumsuz t() çağrıları:\n" + "\n".join(mismatches)


@pytest.mark.asyncio
async def test_tui_backup_log_lines_show_no_raw_placeholders(tmp_path, monkeypatch):
    """TUI artımlı önizleme ve yedek başlangıç satırlarında ham '{alan}', çift emoji veya çift madde işareti görünmemelidir."""
    from textual.widgets import Input, RichLog, TabbedContent
    from distronomad.ui.app import DistroNomadApp

    home = tmp_path / "home"
    _write(home / ".config" / "app" / "a.conf", "v1\n")
    cfg = [ConfigItem(id="c", name="app", category="Test", source_path=str(home / ".config" / "app"), rel_path=".config/app")]
    base = BackupEngine(_system_info(home), [], cfg, [], tmp_path / "base.tar.zst", etc_dir=_empty_etc(tmp_path)).run()
    _write(home / ".config" / "app" / "b.conf", "yeni\n")
    monkeypatch.setattr(DistroNomadApp, "_run_backup_worker", lambda self, *args, **kwargs: None)

    app = DistroNomadApp(home_dir=str(home))
    async with app.run_test(size=(120, 40)) as pilot:
        # RichLog görünmeyen sekmede yazılanları ertelediği için yedekleme sekmesi açılır
        app.query_one("#main-tabs", TabbedContent).active = "tab-backup"
        await pilot.pause()
        app.all_configs = cfg
        app.query_one("#input-backup-base", Input).value = str(base)
        app._handle_preview_changes()
        await pilot.pause()
        log = app.query_one("#backup-log", RichLog)
        lines = [strip.text for strip in log.lines]
        app._proceed_with_backup(tmp_path / "out.tar.zst", "tar.zst", is_encrypt=True, mode_label="Tam (Full)")
        await pilot.pause()
        lines += [strip.text for strip in log.lines]

    text = "\n".join(lines)
    assert "Tahmini Veri Tasarrufu" in text and "AES-256" in text
    assert "{" not in text and "}" not in text, text
    assert "🔍 🔍" not in text and "•   •" not in text, text
