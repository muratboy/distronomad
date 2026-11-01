"""
DistroNomad - Dalga 1 Görev B (W1-B) Doğrulama Testleri.
Kapsam: Geri yükleme motoru, paketler, zamanlayıcı, bağımsız betik ve rollback.
Maddeler: B-1, B-2, B-4, B-5, B-6, B-7, B-8, B-9, B-10, B-11, B-12 ve B-3 hazırlığı.
"""

import json
import os
import shlex
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from distronomad.automation import enable_timer, _systemd_quote_arg
from distronomad.cli import build_parser
from distronomad.models import (
    BackupManifest,
    ConfigItem,
    CustomPathItem,
    PackageItem,
    RestoreOptions,
    SystemInfo,
)
from distronomad.packagemap import get_install_command, is_valid_package_name
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.standalone_script import generate_standalone_restore_script


# ==============================================================================
# B-1: Rollback dconf / crontab toptan silme yapmaması ve anlık görüntü koruması
# ==============================================================================

def test_b1_rollback_never_resets_dconf_when_no_backup(tmp_path):
    """B-1: dconf yedeği yoksa (bak_path is None) rollback ASLA 'dconf reset -f /' çalıştırmaz."""
    tx_file = tmp_path / "last_restore.json"
    tx_data = {
        "timestamp": "20261101_120000",
        "operations": [
            {
                "type": "system_state_dconf",
                "path": "dconf",
                "backup_path": None,
                "is_dir": False
            }
        ]
    }
    tx_file.write_text(json.dumps(tx_data), encoding="utf-8")

    executed_cmds = []

    def mock_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    with patch("distronomad.restore.subprocess.run", side_effect=mock_run), \
         patch("distronomad.restore.shutil.which", return_value="/usr/bin/dconf"):
        res = rollback_restore(transaction_file=tx_file)

    for cmd in executed_cmds:
        assert "reset" not in cmd, f"dconf reset -f / çağrılmamalıdır: {cmd}"
    assert any("dconf" in w for w in res["warnings"])
    assert "dconf" not in res["removed_created"]


def test_b1_rollback_crontab_only_removed_when_genuinely_had_no_crontab(tmp_path):
    """B-1: crontab yalnızca geri yükleme öncesinde hiç crontab olmadığı teyit edildiyse kaldırılır."""
    # Durum 1: had_no_crontab=False ve bak_path=None -> ASLA 'crontab -r' çağrılmaz
    tx_file1 = tmp_path / "tx_cron_unsafe.json"
    tx_file1.write_text(json.dumps({
        "operations": [
            {"type": "system_state_crontab", "path": "crontab", "backup_path": None, "had_no_crontab": False}
        ]
    }), encoding="utf-8")

    executed_cmds = []

    def mock_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    with patch("distronomad.restore.subprocess.run", side_effect=mock_run), \
         patch("distronomad.restore.shutil.which", return_value="/usr/bin/crontab"):
        res1 = rollback_restore(transaction_file=tx_file1)

    for cmd in executed_cmds:
        assert "-r" not in cmd, "had_no_crontab False iken crontab -r çağrılmamalıdır"
    assert any("crontab" in w for w in res1["warnings"])

    # Durum 2: had_no_crontab=True -> crontab -r çağrılabilir
    tx_file2 = tmp_path / "tx_cron_safe.json"
    tx_file2.write_text(json.dumps({
        "operations": [
            {"type": "system_state_crontab", "path": "crontab", "backup_path": None, "had_no_crontab": True}
        ]
    }), encoding="utf-8")

    executed_cmds.clear()
    with patch("distronomad.restore.subprocess.run", side_effect=mock_run), \
         patch("distronomad.restore.shutil.which", return_value="/usr/bin/crontab"):
        res2 = rollback_restore(transaction_file=tx_file2)

    assert any(cmd == ["crontab", "-r"] for cmd in executed_cmds)
    assert "crontab" in res2["removed_created"]


def test_b1_restore_skips_system_state_when_snapshot_fails(tmp_path):
    """B-1: Geri yükleme öncesi anlık görüntü alınamazsa sistem durumu geri yüklemesi atlanır ve uyarılır."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    state_dir = backup_dir / "system_state"
    state_dir.mkdir()
    (state_dir / "dconf-settings.ini").write_text("[test/key]\nval=1\n")
    (state_dir / "crontab.txt").write_text("* * * * * /bin/true\n")

    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo.current(),
        packages=[],
        configs=[],
        custom_paths=[],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)

    def mock_sub_run(cmd, *args, **kwargs):
        # dump/crontab -l başarısız olsun (anlık görüntü alınamasın)
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="Permission denied / DBus error")

    with patch("distronomad.restore.subprocess.run", side_effect=mock_sub_run), \
         patch("distronomad.restore.shutil.which", return_value="/usr/bin/mock"), \
         patch("distronomad.restore.Path.home", return_value=tmp_path / "real_home"):
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        opts = RestoreOptions(
            restore_system_state=True,
            target_home=str(real_home)
        )
        res = engine.execute_restore(opts)

    assert any("dconf anlık görüntüsü alınamadığı için" in w for w in res["warnings"])
    assert any("crontab anlık görüntüsü alınamadığı için" in w for w in res["warnings"])
    assert "dconf (GNOME/Cinnamon ayarları)" not in res["restored_system_state"]
    assert "crontab (Kullanıcı zamanlanmış görevleri)" not in res["restored_system_state"]


# ==============================================================================
# B-5 ve B-10: Paket adları doğrulaması ve '--' argüman sonlandırıcı
# ==============================================================================

def test_b5_b10_package_name_validation_and_dash_injection():
    """B-5 & B-10: Paket adları izin listesiyle doğrulanır, '-' ile başlayanlar ve komut enjeksiyonları elenir."""
    # Geçerli paket adları
    assert is_valid_package_name("neovim") is True
    assert is_valid_package_name("python-pip") is True
    assert is_valid_package_name("gcc-12") is True
    assert is_valid_package_name("org.videolan.VLC") is True
    assert is_valid_package_name("ms-python.python") is True
    assert is_valid_package_name("libstdc++6") is True
    assert is_valid_package_name("pkg:amd64") is True

    # Geçersiz ve enjeksiyon amaçlı girdiler
    assert is_valid_package_name("-o=Acquire::http::Proxy=http://evil") is False
    assert is_valid_package_name("--purge") is False
    assert is_valid_package_name("-S") is False
    assert is_valid_package_name("curl; rm -rf /") is False
    assert is_valid_package_name("pkg`whoami`") is False
    assert is_valid_package_name("pkg$(id)") is False
    assert is_valid_package_name("pkg | cat") is False
    assert is_valid_package_name("") is False
    assert is_valid_package_name(None) is False


def test_b5_get_install_command_uses_double_dash():
    """B-5: Destekleyen tüm paket yöneticilerinde komut satırına '--' eklenir."""
    # pacman
    cmd, _ = get_install_command("pacman", ["git", "vim"])
    assert cmd == ["sudo", "pacman", "-S", "--needed", "--noconfirm", "--", "git", "vim"]

    # apt
    cmd, _ = get_install_command("apt", ["git", "vim"])
    assert cmd == ["sudo", "apt-get", "install", "-y", "--", "git", "vim"]

    # dnf
    cmd, _ = get_install_command("dnf", ["git"])
    assert cmd == ["sudo", "dnf", "install", "-y", "--", "git"]

    # zypper
    cmd, _ = get_install_command("zypper", ["git"])
    assert cmd == ["sudo", "zypper", "install", "-y", "--", "git"]

    # flatpak
    cmd, _ = get_install_command("flatpak", ["org.videolan.VLC"])
    assert cmd == ["flatpak", "install", "-y", "flathub", "--", "org.videolan.VLC"]

    # Zararlı parametreler filtrelenir ve boş kalırsa komut dönülmez
    cmd, _ = get_install_command("apt", ["-o=Evil", "--force-yes"])
    assert cmd == []


def test_npm_scoped_package_validation_and_rejections():
    """B-5: npm kapsamlı paketleri (@kapsam/ad) npm yöneticisinde kabul edilir; diğer yöneticilerde / içeren ve npm'de - ile başlayan adlar reddedilir."""
    # 1. npm kapsamlı paketleri (@kapsam/ad) npm yöneticisinde geçerlidir
    assert is_valid_package_name("@vue/cli", manager="npm") is True
    assert is_valid_package_name("@typescript-eslint/eslint-plugin", manager="npm") is True
    assert is_valid_package_name("@babel/core", manager="npm") is True
    assert is_valid_package_name("lodash", manager="npm") is True

    # 2. Diğer tüm paket yöneticilerinde / içeren adlar reddedilmeye devam eder
    for pm in ["apt", "pacman", "dnf", "zypper", "flatpak", "snap", "pipx", "cargo", "vscode"]:
        assert is_valid_package_name("@vue/cli", manager=pm) is False
        assert is_valid_package_name("my/pkg", manager=pm) is False
    assert is_valid_package_name("@vue/cli") is False
    assert is_valid_package_name("my/pkg") is False

    # 3. npm yöneticisinde de kapsam @ ile başlamıyorsa veya birden fazla / varsa reddedilir
    assert is_valid_package_name("vue/cli", manager="npm") is False
    assert is_valid_package_name("@vue/cli/extra", manager="npm") is False
    assert is_valid_package_name("@/cli", manager="npm") is False
    assert is_valid_package_name("@vue/", manager="npm") is False

    # 4. npm ve her yöneticide '-' ile başlayan veya '=' içeren adlar reddedilir
    assert is_valid_package_name("-vue", manager="npm") is False
    assert is_valid_package_name("-@vue/cli", manager="npm") is False
    assert is_valid_package_name("@vue/-cli", manager="npm") is False
    assert is_valid_package_name("--save-dev", manager="npm") is False
    assert is_valid_package_name("lodash=4.17.21", manager="npm") is False
    assert is_valid_package_name("@vue/cli=5.0.0", manager="npm") is False
    assert is_valid_package_name("-o=evil", manager="apt") is False

    # 5. get_install_command npm kapsamlı paketleri kabul eder ve zararlıları eler
    cmd, desc = get_install_command("npm", ["@vue/cli", "@typescript-eslint/eslint-plugin", "-bad", "evil=1", "other/pkg"])
    assert cmd == ["sudo", "npm", "install", "-g", "--", "@vue/cli", "@typescript-eslint/eslint-plugin"]
    assert "NPM" in desc


def test_restore_engine_accepts_npm_scoped_packages(tmp_path):
    """RestoreEngine npm kapsamlı paketlerini geçerli sayarak kurulum listesine alır ve reddetmez."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest = BackupManifest(
        created_at="2026-11-01T12:00:00",
        version="0.9.0",
        system_info=SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(tmp_path / "home")),
        packages=[
            PackageItem(name="@vue/cli", manager="npm", category="NPM Global Paketi", is_selected=True),
            PackageItem(name="@typescript-eslint/eslint-plugin", manager="npm", category="NPM Global Paketi", is_selected=True),
            PackageItem(name="-bad-pkg", manager="npm", category="NPM Global Paketi", is_selected=True),
            PackageItem(name="bad/slash", manager="apt", category="Sistem Paketi", is_selected=True),
        ],
        configs=[],
        custom_paths=[],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    target_home = tmp_path / "home"
    target_home.mkdir()

    opts = RestoreOptions(
        install_packages=True,
        selected_packages=manifest.packages,
        target_home=str(target_home),
        dry_run=True
    )
    res = engine.execute_restore(opts)
    assert "npm:@vue/cli (geçersiz paket adı)" not in res["failed_packages"]
    assert "npm:@typescript-eslint/eslint-plugin (geçersiz paket adı)" not in res["failed_packages"]
    assert "npm:-bad-pkg (geçersiz paket adı)" in res["failed_packages"]
    assert "apt:bad/slash (geçersiz paket adı)" in res["failed_packages"]


# ==============================================================================
# B-2: Karar 3 Ev dışı özel yollar (custom/root) koruması
# ==============================================================================

def test_b2_restore_options_default_skips_root_paths(monkeypatch, tmp_path):
    """B-2: RestoreOptions'ta allow_external_paths varsayılan kapalıdır ve custom/root ögeleri atlanır."""
    opts = RestoreOptions()
    assert opts.allow_external_paths is False
    assert opts.allow_root_paths is False

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    out_file = tmp_path / "ext" / "ext.conf"
    rel_root = Path(str(out_file).lstrip("/"))
    c_root = backup_dir / "custom" / "root" / rel_root.parent
    c_root.mkdir(parents=True)
    (backup_dir / "custom" / "root" / rel_root).write_text("DATA=1")
    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo.current(),
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(out_file), label="ext.conf", is_dir=False, is_selected=True)],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)

    # 1. Varsayılan (allow_external_paths=False) -> Atlanmalı
    res_def = engine.execute_restore(RestoreOptions(target_home=str(home), selected_custom_paths=manifest.custom_paths))
    assert not out_file.exists()
    assert str(out_file) in res_def["skipped_external_paths"]

    # 2. allow_external_paths=True -> Yazılmalı
    res_ok = engine.execute_restore(RestoreOptions(target_home=str(home), selected_custom_paths=manifest.custom_paths, allow_external_paths=True))
    assert out_file.exists()
    assert out_file.read_text() == "DATA=1"


def test_b2_custom_target_home_never_writes_to_root(monkeypatch, tmp_path):
    """B-2: Hedef ev dizini gerçek ev dizini değilse (--target-home vb.) asla köke/mutlak yola yazılmaz."""
    real_home = tmp_path / "real_user_home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))

    custom_target = tmp_path / "mounted_target_home"
    custom_target.mkdir()

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    c_root = backup_dir / "custom" / "root" / "tmp" / "ext_safe"
    c_root.mkdir(parents=True)
    (c_root / "safe.conf").write_text("SAFE=1")

    out_file = tmp_path / "ext_safe" / "safe.conf"
    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo.current(),
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(out_file), label="safe.conf", is_dir=False, is_selected=True)],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)

    # allow_external_paths=True verilse dahi hedef ev dizini gerçek ev dizini olmadığı için köke yazılmaz!
    opts = RestoreOptions(
        target_home=str(custom_target),
        selected_custom_paths=manifest.custom_paths,
        allow_external_paths=True
    )
    res = engine.execute_restore(opts)
    assert not out_file.exists()
    assert str(out_file) in res["skipped_external_paths"]
    assert any("özel bir dizin olarak belirtildiğinden" in w for w in res["warnings"])


# ==============================================================================
# B-4: systemd Zamanlayıcı ExecStart tırnaklama, % kaçırma ve açıklama
# ==============================================================================

def test_b4_automation_quoting_spaces_and_percent(tmp_path):
    """B-4: ExecStart argümanları boşluk ve % karakteri içerdiğinde doğru tırnaklanır ve kaçırılır."""
    mock_systemd = tmp_path / "systemd_user"
    space_and_pct_dir = tmp_path / "My Backups%20Label" / "dir"

    # 1. Tam yedekleme (incremental=False)
    res_full = enable_timer(
        interval="daily",
        backup_output_dir=space_and_pct_dir,
        systemd_dir=mock_systemd,
        incremental=False
    )
    assert res_full["success"] is True

    service_file = mock_systemd / "distronomad-backup.service"
    content = service_file.read_text(encoding="utf-8")

    # Açıklama artımlı değilse 'artımlı' dememeli
    assert "Description=DistroNomad Otomatik Yedekleme" in content
    assert "Otomatik Artımlı" not in content

    # % karakteri %% olarak kaçırılmış olmalı
    assert "%%20" in content

    # ExecStart satırını bul ve argparse ile çözümle
    exec_line = ""
    for line in content.splitlines():
        if line.startswith("ExecStart="):
            exec_line = line.split("=", 1)[1].strip()
            break

    # systemd % unescaping simülasyonu
    unescaped_cmd = exec_line.replace("%%", "%")
    tokens = shlex.split(unescaped_cmd)
    cli_args = tokens[tokens.index("backup"):]
    parser = build_parser()
    parsed = parser.parse_args(cli_args)
    assert parsed.command == "backup"
    assert parsed.dest == str(space_and_pct_dir)
    assert parsed.incremental is False

    # 2. Artımlı zamanlayıcı (incremental=True)
    enable_timer(
        interval="weekly",
        backup_output_dir=space_and_pct_dir,
        systemd_dir=mock_systemd,
        incremental=True
    )
    content_inc = service_file.read_text(encoding="utf-8")
    assert "Description=DistroNomad Otomatik Artımlı Yedekleme" in content_inc

    exec_line_inc = [l.split("=", 1)[1].strip() for l in content_inc.splitlines() if l.startswith("ExecStart=")][0]
    tokens_inc = shlex.split(exec_line_inc.replace("%%", "%"))
    parsed_inc = parser.parse_args(tokens_inc[tokens_inc.index("backup"):])
    assert parsed_inc.incremental is True


# ==============================================================================
# B-6: Hedefte dosya varken yedekte dizin (ve tersi) çakışması
# ==============================================================================

def test_b6_file_to_directory_conflict_handled_gracefully(tmp_path):
    """B-6: Hedefte dosya varken yedekten aynı isimde bir dizin geldiğinde çökmeden ezebilmeli."""
    home = tmp_path / "home"
    home.mkdir()
    # Hedefte ~/.config/myapp bir DOSYADIR
    app_target = home / ".config" / "myapp"
    app_target.parent.mkdir(parents=True)
    app_target.write_text("I AM A FILE")

    # Yedekte ise configs/.config/myapp bir DİZİNDİR
    backup_dir = tmp_path / "backup"
    app_src = backup_dir / "configs" / ".config" / "myapp"
    app_src.mkdir(parents=True)
    (app_src / "subfile.conf").write_text("I AM IN A DIR")

    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo(home_dir=str(home)),
        configs=[ConfigItem(id="myapp", name="myapp", category="Desktop", source_path=str(app_target), rel_path=".config/myapp", is_directory=True)],
        packages=[],
        custom_paths=[],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    opts = RestoreOptions(
        target_home=str(home),
        conflict_strategy="overwrite",
        selected_configs=manifest.configs
    )

    # safe_copytree FileExistsError fırlatmamalı, hedef dosya temizlenip dizin oluşturulmalı
    res = engine.execute_restore(opts)
    assert app_target.is_dir()
    assert (app_target / "subfile.conf").read_text() == "I AM IN A DIR"


def test_b6_directory_to_file_conflict_handled_gracefully(tmp_path):
    """B-6: Hedefte dizin varken yedekten aynı isimde bir dosya geldiğinde çökmeden ezebilmeli."""
    home = tmp_path / "home"
    home.mkdir()
    # Hedefte ~/.config/single_file bir DİZİNDİR
    app_target = home / ".config" / "single_file"
    app_target.mkdir(parents=True)
    (app_target / "old.txt").write_text("OLD")

    # Yedekte ise single_file bir DOSYADIR
    backup_dir = tmp_path / "backup"
    app_src = backup_dir / "configs" / ".config" / "single_file"
    app_src.parent.mkdir(parents=True)
    app_src.write_text("I AM NOW A SINGLE FILE")

    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo(home_dir=str(home)),
        configs=[ConfigItem(id="single_file", name="single_file", category="Desktop", source_path=str(app_target), rel_path=".config/single_file", is_directory=False)],
        packages=[],
        custom_paths=[],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    opts = RestoreOptions(
        target_home=str(home),
        conflict_strategy="overwrite",
        selected_configs=manifest.configs
    )

    res = engine.execute_restore(opts)
    assert app_target.is_file()
    assert app_target.read_text() == "I AM NOW A SINGLE FILE"


# ==============================================================================
# B-7: Silinen sembolik bağların resolve() nedeniyle hedefte kalmaması
# ==============================================================================

def test_b7_incremental_deleted_symlink_removed_correctly(tmp_path):
    """B-7: _apply_deletions sembolik bağın hedefini izlemez, sembolik bağın kendisini siler."""
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_victim = tmp_path / "outside_victim.txt"
    outside_victim.write_text("DO NOT DELETE ME")

    # Taban yedekte harici bir dosyayı gösteren sembolik bağ var
    link_in_base = base_dir / "my_symlink"
    link_in_base.symlink_to(outside_victim)

    base_manifest = BackupManifest(
        created_at="2026-11-01T10:00:00",
        version="1.0.0",
        system_info=SystemInfo.current(),
        archive_format="dir"
    )
    (base_dir / "manifest.json").write_text(json.dumps(base_manifest.to_dict()), encoding="utf-8")

    # Artımlı yedekte bu sembolik bağ silinmiş
    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()
    inc_manifest = BackupManifest(
        created_at="2026-11-01T11:00:00",
        version="1.0.0",
        system_info=SystemInfo.current(),
        backup_type="incremental",
        base_backup_name="base",
        deleted_files=["my_symlink"],
        archive_format="dir"
    )
    (inc_dir / "manifest.json").write_text(json.dumps(inc_manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=inc_dir)
    target_home = tmp_path / "merged_target"

    opts = RestoreOptions(
        target_home=str(target_home),
        base_backup_path=str(base_dir)
    )
    engine.execute_restore(opts)

    # 1. Dışarıdaki kurban dosya silinmemiş olmalı!
    assert outside_victim.exists()
    assert outside_victim.read_text() == "DO_NOT_DELETE_ME".replace("_", " ")

    # 2. Sembolik bağ zincir birleştirmede silinmiş olmalı (hedefte var olmamalı)
    assert not (target_home / "my_symlink").exists()


# ==============================================================================
# B-8: 'overwrite' stratejisinde ezilen dosyaların rollback raporu
# ==============================================================================

def test_b8_rollback_reports_unrecoverable_overwritten_files(tmp_path):
    """B-8: overwrite stratejisinde ezilen dosyalar rollback'te unrecoverable olarak raporlanır."""
    tx_file = tmp_path / "last_restore.json"
    overwritten_file = tmp_path / "my_config.conf"
    overwritten_file.write_text("NEW_CONTENT")

    tx_data = {
        "timestamp": "20261101_130000",
        "operations": [
            {
                "type": "overwritten",
                "path": str(overwritten_file),
                "backup_path": None,
                "is_dir": False
            }
        ]
    }
    tx_file.write_text(json.dumps(tx_data), encoding="utf-8")

    res = rollback_restore(transaction_file=tx_file)
    assert res["success"] is False
    assert any("üzerine yazma ('overwrite') stratejisiyle" in err for err in res["errors"])
    assert str(overwritten_file) in res.get("unrecoverable_files", [])


# ==============================================================================
# B-9: sudo ön kontrolü ve circuit breaker
# ==============================================================================

def test_b9_sudo_precheck_skips_when_no_sudo_and_no_tty(tmp_path):
    """B-9: Parolasız sudo yoksa ve etkileşimli terminal yoksa paket kurulumu başlatılmadan atlanır."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo(distro_id="arch", available_package_managers=["pacman"]),
        packages=[PackageItem(name="htop", manager="pacman", is_selected=True)],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)

    def mock_sub_run(cmd, *args, **kwargs):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, returncode=1, stderr="sudo: a password is required")
        return subprocess.CompletedProcess(cmd, returncode=0)

    with patch("distronomad.restore.subprocess.run", side_effect=mock_sub_run), \
         patch("sys.stdin.isatty", return_value=False):
        res = engine.execute_restore(RestoreOptions(install_packages=True, selected_packages=manifest.packages))

    assert any("Parolasız sudo yetkisi yok" in w for w in res["warnings"])
    assert "htop" not in res["installed_packages"]


# ==============================================================================
# B-11 ve B-12: Dry-run modunda dizin oluşturmama
# ==============================================================================

def test_b12_restore_engine_dry_run_does_not_create_target_home(tmp_path):
    """B-12: RestoreEngine execute_restore dry_run=True iken target_home oluşturmaz."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo.current(),
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    nonexistent_home = tmp_path / "nonexistent_dryrun_home"
    assert not nonexistent_home.exists()

    engine = RestoreEngine(backup_path=backup_dir)
    res = engine.execute_restore(RestoreOptions(target_home=str(nonexistent_home), dry_run=True))
    assert res["is_dry_run"] is True
    assert not nonexistent_home.exists(), "Dry-run modunda hedef ev dizini diskte oluşturulmamalıdır!"


def test_b11_standalone_restore_script_dry_run_no_mkdir(tmp_path):
    """B-11: restore.sh betiği --dry-run modunda hedefte hiçbir dizin oluşturmaz."""
    script_content = generate_standalone_restore_script()
    script_file = tmp_path / "restore.sh"
    script_file.write_text(script_content, encoding="utf-8")
    script_file.chmod(0o755)

    nonexistent_target_home = tmp_path / "dryrun_standalone_home"
    configs_dir = tmp_path / "configs" / ".config" / "deep_nested_dir"
    configs_dir.mkdir(parents=True)
    (configs_dir / "app.conf").write_text("CONF=1")

    # dry-run ile çalıştır
    proc = subprocess.run(
        ["bash", str(script_file), "--dry-run", "--configs-only", "--target-home", str(nonexistent_target_home)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True
    )
    assert proc.returncode == 0
    assert not nonexistent_target_home.exists(), "restore.sh --dry-run hedefte dizin oluşturmamalıdır!"


# ==============================================================================
# B-3 Hazırlığı: İşlem günlüğü target_home eşleşmesi
# ==============================================================================

def test_b3_transaction_log_records_target_home_and_rollback_supports_target_home(tmp_path):
    """B-3: İşlem günlüğüne target_home yazılır ve rollback_restore(target_home=...) ile okunabilir."""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    conf_dir = backup_dir / "configs" / ".config" / "testapp"
    conf_dir.mkdir(parents=True)
    (conf_dir / "app.conf").write_text("RESTORED_DATA")

    custom_home = tmp_path / "custom_home"
    custom_home.mkdir()

    manifest = BackupManifest(
        created_at="2026-11-01",
        version="1.0.0",
        system_info=SystemInfo(home_dir=str(custom_home)),
        configs=[ConfigItem(id="testapp", name="testapp", category="Desktop", source_path="", rel_path=".config/testapp/app.conf", is_directory=False)],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    res = engine.execute_restore(RestoreOptions(target_home=str(custom_home), selected_configs=manifest.configs))

    # Transaction dosyası custom_home altında oluşturulmuş olmalı
    tx_file = custom_home / ".config" / "distronomad" / "transactions" / "last_restore.json"
    assert tx_file.exists()
    tx_data = json.loads(tx_file.read_text(encoding="utf-8"))
    assert tx_data.get("target_home") == str(custom_home)

    # rollback_restore target_home argümanıyla o dizindeki last_restore.json dosyasını bulmalı
    rb_res = rollback_restore(target_home=custom_home)
    assert rb_res["success"] is True
    assert not (custom_home / ".config" / "testapp" / "app.conf").exists()
