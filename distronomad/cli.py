"""
DistroNomad - Komut Satırı Arayüzü (CLI) ve Başlatıcı.
"""

from __future__ import annotations
import sys
import os
import json
import argparse
import getpass
from pathlib import Path
from typing import Optional, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from distronomad import __version__, __app_name__
from distronomad.discovery import SystemScanner, find_running_browsers
from distronomad.models import RestoreOptions, CustomPathItem
from distronomad.backup import BackupEngine, check_fat32_risk, Fat32LimitError
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.diff_utils import compare_backup_with_host, compute_file_diff
from distronomad.automation import get_timer_status, enable_timer, disable_timer
from distronomad.hardware import scan_hardware_profile, is_hardware_profile_different
from distronomad.crypto import is_encrypted_file
from distronomad.i18n import (
    t,
    get_current_language,
    set_current_language,
    detect_default_language,
    normalize_language,
)


console = Console()
err_console = Console(stderr=True)
CLI_PASSWORD_SENTINEL = object()
# main() DISTRONOMAD_PASSWORD'ü ortamdan çıkarıp burada saklar; böylece paket yöneticileri, npm, code gibi alt süreçlere aktarılmaz.
_ENV_PASSWORD: Optional[str] = None


def _env_password() -> Optional[str]:
    """DISTRONOMAD_PASSWORD değerini ortamdan veya main()'in sakladığı kopyadan döndürür."""
    return os.environ.get("DISTRONOMAD_PASSWORD") or _ENV_PASSWORD


def resolve_cli_password(
    cli_password: Any,
    *,
    target_path: Optional[Path] = None,
    base_path: Optional[Path] = None,
    is_backup: bool = False,
    confirm_twice: bool = False,
) -> Optional[str]:
    """CLI parola parametresini (--password / -p), ortam değişkenini (DISTRONOMAD_PASSWORD)
    ve etkileşimli getpass sormasını güvenli biçimde çözümler.
    """
    # 1. Komut satırında doğrudan değer olarak verilmişse
    if cli_password is not None and cli_password is not CLI_PASSWORD_SENTINEL:
        val_str = str(cli_password)
        if not val_str:
            err_console.print(f"[bold red]✖ {t('cli_password_err_empty')}[/bold red]")
            sys.exit(1)
        err_console.print(f"[bold yellow]⚠ {t('cli_password_cli_warning')}[/bold yellow]")
        return val_str

    # 2. Komut satırında bayrak olarak değersiz verilmişse (--password / -p)
    if cli_password is CLI_PASSWORD_SENTINEL:
        env_pwd = _env_password()
        if env_pwd:
            return env_pwd

        if not sys.stdin.isatty():
            err_console.print(f"[bold red]✖ {t('cli_password_err_non_terminal')}[/bold red]")
            sys.exit(1)

        try:
            p1 = getpass.getpass(t("cli_password_prompt"))
            if not p1:
                err_console.print(f"[bold red]✖ {t('cli_password_err_empty')}[/bold red]")
                sys.exit(1)
            if confirm_twice:
                p2 = getpass.getpass(t("cli_password_confirm_prompt"))
                if p1 != p2:
                    err_console.print(f"[bold red]✖ {t('cli_password_err_mismatch')}[/bold red]")
                    sys.exit(1)
            return p1
        except (KeyboardInterrupt, EOFError):
            err_console.print(f"\n[bold red]✖ {t('cli_password_err_aborted')}[/bold red]")
            sys.exit(1)

    # 3. Komut satırında bayrak hiç verilmemişse (cli_password is None)
    is_target_encrypted = False
    if target_path and target_path.exists():
        if is_encrypted_file(target_path):
            is_target_encrypted = True
        elif base_path and base_path.exists() and is_encrypted_file(base_path):
            is_target_encrypted = True

    if is_target_encrypted:
        env_pwd = _env_password()
        if env_pwd:
            return env_pwd

        if not sys.stdin.isatty():
            err_console.print(f"[bold red]✖ {t('cli_password_err_non_terminal')}[/bold red]")
            sys.exit(1)

        try:
            p = getpass.getpass(t("cli_password_prompt"))
            if not p:
                err_console.print(f"[bold red]✖ {t('cli_password_err_empty')}[/bold red]")
                sys.exit(1)
            return p
        except (KeyboardInterrupt, EOFError):
            err_console.print(f"\n[bold red]✖ {t('cli_password_err_aborted')}[/bold red]")
            sys.exit(1)

    env_pwd = _env_password()
    if is_backup and env_pwd:
        return env_pwd

    return None


def preparse_language(argv: list[str] | None = None) -> Optional[str]:
    """Argparse çalıştırmadan önce sys.argv / argv içinden --lang parametresini ön-okur."""
    if argv is None:
        argv = sys.argv[1:]
    found_lang = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--lang":
            if i + 1 < len(argv):
                val = argv[i + 1].strip().lower()
                if val.startswith("tr"):
                    found_lang = "tr"
                elif val.startswith("en"):
                    found_lang = "en"
                i += 1
        elif arg.startswith("--lang="):
            _, _, val = arg.partition("=")
            val = val.strip().lower()
            if val.startswith("tr"):
                found_lang = "tr"
            elif val.startswith("en"):
                found_lang = "en"
        i += 1
    return found_lang


def run_tui(home_dir: str = None, lang: str = None):
    """Modern Textual TUI arayüzünü başlatır."""
    if lang:
        set_current_language(lang, persist=False)
    from distronomad.ui.app import DistroNomadApp
    app = DistroNomadApp(home_dir=home_dir, lang=lang)
    app.run()


def cmd_scan(args):
    """Sistemdeki uygulamaları ve konfigürasyonları konsola yazdırır."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    try:
        scanner = SystemScanner(home_dir=args.home)
        sys_info = scanner.scan_system_info()

        pms_str = ", ".join(sys_info.available_package_managers) or t("cli_scan_pms_none")
        console.print(Panel.fit(
            f"[bold cyan]{__app_name__} v{__version__}[/bold cyan] - {t('cli_scan_report_title')}\n"
            f"[bold]{t('cli_scan_distro')}[/bold] {sys_info.distro_name} ({sys_info.distro_id})\n"
            f"[bold]{t('cli_scan_kernel')}[/bold] {sys_info.kernel_version} | [bold]{t('cli_scan_desktop')}[/bold] {sys_info.desktop_environment}\n"
            f"[bold]{t('cli_scan_home')}[/bold] {sys_info.home_dir}\n"
            f"[bold]{t('cli_scan_pms')}[/bold] {pms_str}",
            title=f"[bold green]{t('cli_scan_panel_title')}[/bold green]",
            border_style="cyan"
        ))

        # Konfigürasyonlar
        configs = scanner.scan_configs()
        cfg_table = Table(title=t("cli_scan_configs_title"), border_style="cyan")
        cfg_table.add_column(t("cli_col_name"), style="cyan")
        cfg_table.add_column(t("cli_col_category"), style="green")
        cfg_table.add_column(t("cli_col_rel_path"), style="white")
        cfg_table.add_column(t("cli_col_size"), style="yellow")
        cfg_table.add_column(t("cli_col_item_count"), style="magenta")

        for c in configs[:25]:
            cfg_table.add_row(c.name, c.category, c.rel_path, c.formatted_size, str(c.item_count))

        console.print(cfg_table)
        if len(configs) > 25:
            console.print(f"[dim]{t('cli_scan_configs_more', count=len(configs) - 25)}[/dim]")

        # Paketler
        packages = scanner.scan_installed_packages()
        pkg_table = Table(title=t("cli_scan_packages_title"), border_style="magenta")
        pkg_table.add_column(t("cli_col_pkg_name"), style="bold white")
        pkg_table.add_column(t("cli_col_manager"), style="bold cyan")
        pkg_table.add_column(t("cli_col_category"), style="green")
        pkg_table.add_column(t("cli_col_version"), style="yellow")

        for p in packages[:25]:
            pkg_table.add_row(p.display_name, p.manager.upper(), p.category, p.version or "-")

        console.print(pkg_table)
        if len(packages) > 25:
            console.print(f"[dim]{t('cli_scan_packages_more', count=len(packages) - 25)}[/dim]")

        # Özel Dizinler
        custom_paths = scanner.scan_custom_paths()
        if custom_paths:
            custom_table = Table(title=t("cli_scan_custom_title"), border_style="green")
            custom_table.add_column(t("cli_col_label"), style="bold white")
            custom_table.add_column(t("cli_col_path"), style="cyan")
            custom_table.add_column(t("cli_col_size"), style="yellow")
            custom_table.add_column(t("cli_col_file_count"), style="magenta")

            for cp in custom_paths:
                custom_table.add_row(cp.label, cp.path, cp.formatted_size, str(cp.file_count))

            console.print(custom_table)
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_backup(args):
    """Komut satırından otomatik yedek alır."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    try:
        scanner = SystemScanner(home_dir=args.home)
        sys_info = scanner.scan_system_info()
        packages = scanner.scan_installed_packages()
        configs = scanner.scan_configs()
        custom_paths = scanner.scan_custom_paths()

        # Hassas verileri filtrele
        if args.no_sensitive:
            for c in configs:
                if c.is_sensitive:
                    c.is_selected = False

        output_path = Path(args.dest)
        fmt = args.format

        backup_mode = "full"
        if getattr(args, "append", False):
            backup_mode = "append"
        elif getattr(args, "incremental", False):
            backup_mode = "incremental"

        base_path = Path(args.base) if getattr(args, "base", None) else None
        force = getattr(args, "force", False)

        # FAT32 dosya boyutu sınırı koruması
        target_custom = custom_paths if not args.configs_only else []
        risk = check_fat32_risk(
            target_path=output_path,
            archive_format=fmt,
            backup_mode=backup_mode,
            configs=configs,
            custom_paths=target_custom,
            base_backup_path=base_path
        )
        if risk.has_risk:
            if risk.reason == "fat32_dir_unsupported":
                if not force:
                    console.print(f"[bold yellow]⚠️  {risk.message}[/bold yellow]")
                    console.print(f"[bold red]✖ {t('cli_fat32_dir_unsupported')}[/bold red]")
                    sys.exit(1)
                else:
                    console.print(f"[bold yellow]⚠️  {risk.message}[/bold yellow]")
                    console.print(f"[yellow]{t('cli_fat32_force_dir_proceed')}[/yellow]")
            else:
                if not force:
                    console.print(f"[bold yellow]{risk.message}[/bold yellow]")
                    console.print(f"[bold red]✖ {t('fat32_cli_warning')}[/bold red]")
                    sys.exit(1)
                else:
                    console.print(f"[bold yellow]⚠️  {risk.message}[/bold yellow]")
                    console.print(f"[yellow]{t('cli_fat32_force_proceed')}[/yellow]")

        # Açık tarayıcılar profil dosyalarına yazmaya devam eder (zamanlanmış yedekte soru sorulamayacağı için yalnızca uyarılır)
        running_browsers = find_running_browsers(
            [c.source_path for c in configs if c.is_selected] + [cp.path for cp in target_custom if cp.is_selected],
            home_dir=sys_info.home_dir
        )
        if running_browsers:
            console.print(f"[bold yellow]⚠️  {t('backup_warn_browsers_running', names=', '.join(running_browsers))}[/bold yellow]")

        console.print(f"[bold cyan]{t('cli_backup_starting', dest=output_path, format=fmt)}[/bold cyan]")

        def progress_callback(update):
            console.print(f"[{update.phase.upper()}] {update.message}")

        password = resolve_cli_password(
            getattr(args, "password", None),
            is_backup=True,
            confirm_twice=True,
        )

        engine = BackupEngine(
            system_info=sys_info,
            packages=packages,
            configs=configs,
            custom_paths=target_custom,
            output_path=output_path,
            archive_format=fmt,
            backup_mode=backup_mode,
            base_backup_path=base_path,
            use_smart_ignore=not getattr(args, "no_ignore", False),
            password=password,
            progress_callback=progress_callback,
            force=force
        )

        try:
            final_path = engine.run()
        except Fat32LimitError as e:
            console.print(f"[bold red]✖ {e.message}[/bold red]")
            sys.exit(1)

        console.print(f"[bold green]✔ {t('cli_backup_completed', dest=final_path)}[/bold green]")
        console.print(f"[bold cyan]💾 {t('backup_synced_usb_safe')}[/bold cyan]")

        if engine.skipped_files:
            console.print(f"[bold yellow]⚠ {t('cli_backup_skipped_warning', count=len(engine.skipped_files))}[/bold yellow]")
            for sf in engine.skipped_files[:5]:
                reason_str = f" ({sf['reason']})" if sf.get("reason") else ""
                console.print(f"  [yellow]- {sf.get('path', '')}{reason_str}[/yellow]")
            if len(engine.skipped_files) > 5:
                console.print(f"  [dim]{t('cli_skipped_files_more', count=len(engine.skipped_files) - 5)}[/dim]")
            sys.exit(3)
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_append(args):
    """Var olan bir yedek arşivine yeni uygulama, ayar veya dosya ekler."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    paths = getattr(args, "path", None) or []
    cfgs = getattr(args, "config", None) or []
    pkgs = getattr(args, "package", None) or []

    # C-5: Hiçbir seçim belirtilmemişse açık hata mesajı ve örnek kullanım gösterip çık
    if not paths and not cfgs and not pkgs:
        console.print(f"[bold red]✖ {t('cli_append_err_no_selection')}[/bold red]")
        console.print(f"[cyan]{t('cli_append_example_title')}[/cyan]")
        console.print("  distronomad append --backup backup.tar.zst --path /home/kullanici/Belgeler")
        console.print("  distronomad append --backup backup.tar.zst --config .config/nvim")
        console.print("  distronomad append --backup backup.tar.zst --package pacman:ripgrep")
        sys.exit(1)

    backup_path = Path(args.backup)
    if not backup_path.exists():
        console.print(f"[bold red]✖ {t('cli_append_err_backup_not_found', path=backup_path)}[/bold red]")
        sys.exit(1)

    try:
        scanner = SystemScanner(home_dir=getattr(args, "home", None))
        sys_info = scanner.scan_system_info()
        all_configs = scanner.scan_configs()
        all_packages = scanner.scan_installed_packages()

        effective_home = Path(sys_info.home_dir).resolve() if sys_info.home_dir else Path.home().resolve()

        # Konfigürasyon eşleştirme (taramadan seç)
        selected_configs = []
        for c_query in cfgs:
            matched = False
            q_norm = str(c_query).strip("/\\")
            q_p = Path(c_query).expanduser()
            for c in all_configs:
                c_rel_norm = str(c.rel_path).strip("/\\")
                c_src = Path(c.source_path).resolve() if c.source_path else None
                if (
                    q_norm == c_rel_norm
                    or (q_p.is_absolute() and c_src and q_p.resolve() == c_src)
                    or (not q_p.is_absolute() and c_src and (effective_home / q_p).resolve() == c_src)
                    or c_query == c.name
                    or c_query == c.id
                ):
                    c.is_selected = True
                    if c not in selected_configs:
                        selected_configs.append(c)
                    matched = True
            if not matched:
                console.print(f"[bold yellow]⚠ {t('cli_append_warn_unmatched_config', query=c_query)}[/bold yellow]")

        # Paket eşleştirme (taramadan seç)
        selected_packages = []
        for p_query in pkgs:
            matched = False
            if ":" in p_query:
                pm, pname = p_query.split(":", 1)
                pm = pm.strip().lower()
                pname = pname.strip().lower()
                for p in all_packages:
                    if p.manager.lower() == pm and (p.name.lower() == pname or p.app_id.lower() == pname):
                        p.is_selected = True
                        if p not in selected_packages:
                            selected_packages.append(p)
                        matched = True
            else:
                pname = p_query.strip().lower()
                for p in all_packages:
                    if p.name.lower() == pname or p.app_id.lower() == pname:
                        p.is_selected = True
                        if p not in selected_packages:
                            selected_packages.append(p)
                        matched = True
            if not matched:
                console.print(f"[bold yellow]⚠ {t('cli_append_warn_unmatched_package', query=p_query)}[/bold yellow]")

        # Özel yollar (--path)
        selected_custom = []
        for pt_query in paths:
            pt = Path(pt_query).expanduser()
            if not pt.is_absolute():
                pt = pt.resolve()
            elif ".." in pt.parts:
                # Geri yükleme '..' içeren ev dışı yolları güvenlik gereği atladığı için yol burada sadeleştirilir
                pt = Path(os.path.normpath(pt))
            selected_custom.append(CustomPathItem(
                path=str(pt),
                label=pt.name,
                exists=pt.exists() or pt.is_symlink(),
                is_dir=pt.is_dir() if (pt.exists() and not pt.is_symlink()) else False,
                is_selected=True
            ))

        # Hassas verileri filtrele
        if getattr(args, "no_sensitive", False):
            selected_configs = [c for c in selected_configs if not c.is_sensitive]

        if not selected_configs and not selected_packages and not selected_custom:
            console.print(f"[bold red]✖ {t('cli_append_err_no_items_matched')}[/bold red]")
            sys.exit(1)

        force = getattr(args, "force", False)
        risk = check_fat32_risk(
            target_path=backup_path,
            archive_format="tar.zst",
            backup_mode="append",
            configs=selected_configs,
            custom_paths=selected_custom,
            base_backup_path=None
        )
        if risk.has_risk:
            if risk.reason == "fat32_dir_unsupported":
                if not force:
                    console.print(f"[bold yellow]⚠️  {risk.message}[/bold yellow]")
                    console.print(f"[bold red]✖ {t('cli_fat32_dir_unsupported')}[/bold red]")
                    sys.exit(1)
                else:
                    console.print(f"[bold yellow]⚠️  {risk.message}[/bold yellow]")
                    console.print(f"[yellow]{t('cli_fat32_force_dir_proceed')}[/yellow]")
            else:
                if not force:
                    console.print(f"[bold yellow]{risk.message}[/bold yellow]")
                    console.print(f"[bold red]✖ {t('fat32_cli_warning')}[/bold red]")
                    sys.exit(1)
                else:
                    console.print(f"[bold yellow]⚠️  {risk.message}[/bold yellow]")
                    console.print(f"[yellow]{t('cli_fat32_force_proceed')}[/yellow]")

        running_browsers = find_running_browsers(
            [c.source_path for c in selected_configs] + [cp.path for cp in selected_custom],
            home_dir=sys_info.home_dir
        )
        if running_browsers:
            console.print(f"[bold yellow]⚠️  {t('backup_warn_browsers_running', names=', '.join(running_browsers))}[/bold yellow]")

        console.print(f"[bold cyan]{t('cli_append_starting', dest=backup_path)}[/bold cyan]")

        def progress_callback(update):
            console.print(f"[{update.phase.upper()}] {update.message}")

        password = resolve_cli_password(
            getattr(args, "password", None),
            target_path=backup_path,
            confirm_twice=True,
        )

        engine = BackupEngine(
            system_info=sys_info,
            packages=selected_packages,
            configs=selected_configs,
            custom_paths=selected_custom,
            output_path=backup_path,
            archive_format="tar.zst",
            backup_mode="append",
            base_backup_path=None,
            use_smart_ignore=True,
            password=password,
            progress_callback=progress_callback,
            force=force
        )

        try:
            final_path = engine.run()
        except Fat32LimitError as e:
            console.print(f"[bold red]✖ {e.message}[/bold red]")
            sys.exit(1)

        console.print(f"[bold green]✔ {t('cli_append_completed', dest=final_path)}[/bold green]")
        console.print(f"[bold cyan]💾 {t('backup_synced_usb_safe')}[/bold cyan]")

        if engine.skipped_files:
            console.print(f"[bold yellow]⚠ {t('cli_append_skipped_warning', count=len(engine.skipped_files))}[/bold yellow]")
            for sf in engine.skipped_files[:5]:
                reason_str = f" ({sf['reason']})" if sf.get("reason") else ""
                console.print(f"  [yellow]- {sf.get('path', '')}{reason_str}[/yellow]")
            if len(engine.skipped_files) > 5:
                console.print(f"  [dim]{t('cli_skipped_files_more', count=len(engine.skipped_files) - 5)}[/dim]")
            sys.exit(3)
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_restore(args):
    """Komut satırından yedek geri yükler."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    src = Path(args.src)
    if not src.exists():
        console.print(f"[bold red]✖ {t('cli_restore_err_src_not_found', src=src)}[/bold red]")
        sys.exit(1)

    try:
        password = resolve_cli_password(
            getattr(args, "password", None),
            target_path=src,
            confirm_twice=False,
        )
        console.print(f"[bold cyan]{t('cli_restore_inspecting', src=src)}[/bold cyan]")
        engine = RestoreEngine(backup_path=src, password=password)
        manifest = engine.load_manifest()

        console.print(f"[green]✔ {t('cli_restore_manifest_loaded', distro=manifest.system_info.distro_name, created=manifest.created_at)}[/green]")

        if manifest.hardware_info:
            live_hw = scan_hardware_profile()
            is_diff, diff_desc = is_hardware_profile_different(live_hw, manifest.hardware_info)
            if is_diff:
                console.print(f"[yellow]ℹ {t('hw_manifest_diff_notice')}: {diff_desc} ({t('hw_from_backup')})[/yellow]")

        install_drivers = getattr(args, "install_drivers", False)
        if install_drivers:
            live_hw = scan_hardware_profile()
            scanner = SystemScanner(home_dir=args.target_home)
            current_sys = scanner.scan_system_info()
            from distronomad.packagemap import detect_distro_family
            target_fam = detect_distro_family(current_sys.distro_id, current_sys.distro_like)
            recs = live_hw.recommended_packages.get(target_fam, [])
            recs_str = ", ".join(recs) if recs else t("hw_rec_none")
            console.print(f"[cyan]🖥 {t('chk_install_drivers_lbl')}:[/cyan] {recs_str}")

        allow_ext = getattr(args, "allow_external_paths", False)
        options = RestoreOptions(
            target_home=args.target_home or str(Path.home()),
            selected_packages=manifest.packages if not args.configs_only else [],
            selected_configs=manifest.configs if not args.packages_only else [],
            selected_custom_paths=manifest.custom_paths if not (args.configs_only or args.packages_only) else [],
            conflict_strategy=args.conflict,
            install_packages=not args.configs_only,
            restore_system_state=getattr(args, "restore_system_state", False),
            dry_run=args.dry_run,
            base_backup_path=getattr(args, "base", "") or "",
            install_drivers=install_drivers,
            allow_external_paths=allow_ext,
            allow_root_paths=allow_ext
        )

        def progress_callback(update):
            console.print(f"[{update.phase.upper()}] {update.message}")

        engine.progress_callback = progress_callback
        try:
            results = engine.execute_restore(options)
        finally:
            engine.cleanup()

        console.print(f"[bold green]✔ {t('cli_restore_completed')}[/bold green]")
        console.print(f"  • {t('cli_restore_stat_configs', count=len(results['restored_configs']))}")
        console.print(f"  • {t('cli_restore_stat_files', count=len(results['restored_files']))}")
        console.print(f"  • {t('cli_restore_stat_packages', count=len(results['installed_packages']))}")
        if results.get("failed_packages"):
            console.print(f"  • [bold red]{t('cli_restore_failed_packages', count=len(results['failed_packages']))}[/bold red]")
            for fp in results["failed_packages"]:
                console.print(f"    [red]- {fp}[/red]")
        if results.get("untranslated_packages"):
            console.print(f"  • [bold yellow]{t('cli_restore_untranslated_packages', count=len(results['untranslated_packages']))}[/bold yellow]")
            for up in results["untranslated_packages"]:
                console.print(f"    [yellow]- {up}[/yellow]")
        if results.get("skipped_external_paths"):
            console.print(f"  • [bold yellow]{t('cli_restore_skipped_external_paths', count=len(results['skipped_external_paths']))}[/bold yellow]")
            for ep in results["skipped_external_paths"]:
                console.print(f"    [yellow]- {ep}[/yellow]")
        if results.get("transaction_file"):
            console.print(f"  • [cyan]{t('cli_restore_tx_log')}[/cyan] {results['transaction_file']}")

        # Sudo yetki uyarısı (B-9)
        sudo_failed = any("sudo -n başarısız" in str(fp) or "yetki hatası" in str(fp) for fp in results.get("failed_packages", []))
        if not sudo_failed:
            for w in results.get("warnings", []):
                if "Parolasız sudo yetkisi yok" in w:
                    sudo_failed = True
                    break
        if sudo_failed:
            console.print(f"[bold yellow]⚠ {t('cli_restore_sudo_warning')}[/bold yellow]")

    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_rollback(args):
    """En son veya belirtilen geri yükleme işlemini geri alır."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    tx_arg = getattr(args, "tx", None)
    target_home_arg = getattr(args, "target_home", None)

    if tx_arg:
        tx_path = Path(tx_arg)
    elif target_home_arg:
        tx_path = Path(target_home_arg) / ".config" / "distronomad" / "transactions" / "last_restore.json"
    else:
        home_path = Path(os.environ.get("HOME") or Path.home())
        tx_path = home_path / ".config" / "distronomad" / "transactions" / "last_restore.json"

    if not tx_path.exists():
        console.print(f"[bold red]{t('cli_rollback_err_no_tx', path=tx_path)}[/bold red]")
        sys.exit(1)

    effective_target_home = Path(target_home_arg).resolve() if target_home_arg else Path(os.environ.get("HOME") or Path.home()).resolve()

    try:
        with open(tx_path, "r", encoding="utf-8") as f:
            tx_data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]{t('cli_rollback_err_read_tx', error=str(e))}[/bold red]")
        sys.exit(1)

    log_target_home_str = tx_data.get("target_home")
    if log_target_home_str:
        try:
            log_target_home = Path(log_target_home_str).resolve()
            if effective_target_home != log_target_home:
                console.print(
                    f"[bold red]✖ {t('cli_rollback_err_home_mismatch', log_home=log_target_home_str, current_home=effective_target_home)}[/bold red]"
                )
                sys.exit(1)
        except Exception:
            pass

    try:
        res = rollback_restore(transaction_file=tx_path, target_home=effective_target_home)
        console.print(f"[bold green]✔ {t('cli_rollback_completed')}[/bold green]")
        console.print(f"  • {t('cli_rollback_stat_reverted', count=len(res.get('reverted_files', [])))}")
        console.print(f"  • {t('cli_rollback_stat_removed', count=len(res.get('removed_created', [])))}")
        if res.get("unrecoverable_files"):
            console.print(f"  • [bold yellow]{t('cli_rollback_unrecoverable', count=len(res['unrecoverable_files']))}[/bold yellow]")
            for unrec in res["unrecoverable_files"]:
                console.print(f"    [yellow]- {unrec}[/yellow]")
        if res.get("errors"):
            console.print(f"  • [bold red]{t('cli_rollback_errors', count=len(res['errors']))}[/bold red]")
            for err in res["errors"]:
                console.print(f"    [red]- {err}[/red]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]{t('cli_rollback_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_diff(args):
    """Yedek ile mevcut sistem arasındaki dosya farklarını listeler."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    src = Path(args.src)
    if not src.exists():
        console.print(f"[bold red]✖ {t('cli_diff_err_src_not_found', src=src)}[/bold red]")
        sys.exit(1)

    try:
        password = resolve_cli_password(
            getattr(args, "password", None),
            target_path=src,
            confirm_twice=False,
        )
        engine = RestoreEngine(backup_path=src, password=password)
        try:
            engine.load_manifest()
            target_home = Path(args.target_home) if getattr(args, "target_home", None) else None
            diffs = compare_backup_with_host(engine.source_dir, target_home=target_home)

            # Değişen veya yeni dosyaların satır sayılarını tembel fonksiyonla hesapla
            for d in diffs:
                if d.get("status") in ("modified", "new_in_backup"):
                    compute_file_diff(d)
                else:
                    if d.get("added") is None:
                        d["added"] = 0
                    if d.get("removed") is None:
                        d["removed"] = 0

            table = Table(title=t("cli_diff_report_title", name=src.name), border_style="cyan")
            table.add_column(t("cli_col_category"), style="cyan")
            table.add_column(t("cli_col_rel_path"), style="white")
            table.add_column(t("cli_col_status"), style="bold")
            table.add_column(t("cli_col_added"), style="green")
            table.add_column(t("cli_col_removed"), style="red")

            for d in diffs:
                status_style = "green" if d["status"] == "new_in_backup" else ("yellow" if d["status"] == "modified" else "dim")
                status_text = (
                    t("cli_diff_status_new") if d["status"] == "new_in_backup"
                    else (t("cli_diff_status_modified") if d["status"] == "modified"
                          else (t("cli_diff_status_identical") if d["status"] == "identical" else t("cli_diff_status_binary")))
                )
                table.add_row(
                    d["category"],
                    d["rel_path"],
                    f"[{status_style}]{status_text}[/{status_style}]",
                    str(d["added"]),
                    str(d["removed"])
                )

            console.print(table)

            if getattr(args, "show_content", False):
                for d in diffs:
                    if d["status"] == "modified":
                        console.print(Panel(d["diff_text"], title=f"[bold yellow]{d['rel_path']}[/bold yellow]"))
        finally:
            engine.cleanup()
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_schedule(args):
    """Zamanlanmış otomatik yedekleme ayarlarını yönetir."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    try:
        if getattr(args, "disable", False):
            res = disable_timer()
            if not res.get("success"):
                default_fail = t("cli_schedule_fail_disable_default")
                console.print(f"[bold red]✖ {t('cli_schedule_err_disable', message=res.get('message', default_fail))}[/bold red]")
                sys.exit(1)
            console.print(f"[bold yellow]✔ {res['message']}[/bold yellow]")
        elif getattr(args, "enable", False):
            res = enable_timer(interval=args.interval, backup_output_dir=Path(args.dest) if args.dest else None)
            if not res.get("success"):
                console.print(f"[bold red]✖ {t('cli_schedule_err_enable')}[/bold red]")
                if res.get("message"):
                    console.print(f"[red]{res['message']}[/red]")
                sys.exit(1)
            console.print(f"[bold green]✔ {t('cli_schedule_enabled')}[/bold green]")
            console.print(f"  • {t('cli_schedule_stat_interval', interval=res['interval'], calendar=res['calendar_spec'])}")
            console.print(f"  • {t('cli_schedule_stat_dest', dest=res['output_dir'])}")
            console.print(f"  • {t('cli_schedule_stat_service', path=res['service_path'])}")
            console.print(f"  • {t('cli_schedule_stat_timer', path=res['timer_path'])}")
        else:
            st = get_timer_status()
            status_str = f"[bold green]{t('cli_schedule_active')}[/bold green]" if st.get("is_active") else f"[bold red]{t('cli_schedule_disabled')}[/bold red]"
            installed_str = t("cli_schedule_installed") if st.get("installed") else t("cli_schedule_not_installed")
            panel_content = (
                f"[bold]{t('cli_schedule_lbl_status')}[/bold] {status_str}\n"
                f"[bold]{t('cli_schedule_lbl_installed')}[/bold] {installed_str}\n"
                f"[bold]{t('cli_schedule_lbl_interval')}[/bold] {st.get('interval', '-')}\n"
                f"[bold]{t('cli_schedule_lbl_timer_file')}[/bold] {st.get('timer_path') or '-'}\n\n"
                f"[dim]{t('cli_schedule_hint_enable')}[/dim]\n"
                f"[dim]{t('cli_schedule_hint_disable')}[/dim]"
            )
            console.print(Panel.fit(
                panel_content,
                title=f"[bold cyan]{t('cli_schedule_panel_title')}[/bold cyan]",
                border_style="cyan"
            ))
    except (OSError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)


def cmd_verify(args):
    """Komut satırından yedek bütünlüğünü doğrular."""
    if getattr(args, "lang", None):
        set_current_language(args.lang, persist=False)
    src = Path(args.src)
    if not src.exists():
        console.print(f"[bold red]✖ {t('cli_verify_err_not_found', src=src)}[/bold red]")
        sys.exit(1)

    base = getattr(args, "base", None)
    base_path = Path(base) if base else None

    password = resolve_cli_password(
        getattr(args, "password", None),
        target_path=src,
        base_path=base_path,
        confirm_twice=False,
    )

    console.print(f"[bold cyan]🔍 {t('cli_verify_starting', src=src)}[/bold cyan]")

    from distronomad.verify import verify_backup
    try:
        result = verify_backup(
            backup_path=src,
            password=password,
            base_path=base_path,
            check_chain=True
        )
    except ValueError as ve:
        console.print(f"[bold red]{t('cli_verify_err_prefix', error=str(ve))}[/bold red]")
        sys.exit(1)
    except (OSError, RuntimeError) as e:
        console.print(f"[bold red]✖ {t('cli_err_prefix', error=str(e))}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]{t('cli_verify_err_verify_failed', error=str(e))}[/bold red]")
        sys.exit(1)

    table = Table(title=t("cli_verify_table_title", name=src.name), show_header=True, header_style="bold magenta")
    table.add_column(t("cli_col_prop"), style="bold")
    table.add_column(t("cli_col_result"))

    table.add_row(t("cli_verify_row_location"), str(result.archive_path))
    err_detail = result.error_message or t("cli_verify_status_stream_error")
    status_str = f"[green]{t('cli_verify_status_intact')}[/green]" if not result.corrupted else f"[bold red]{t('cli_verify_status_corrupted', details=err_detail)}[/bold red]"
    table.add_row(t("cli_verify_row_archive_status"), status_str)
    table.add_row(t("cli_verify_row_total_checked"), str(result.total_files_checked))
    table.add_row(t("cli_verify_row_matched"), f"[green]{len(result.matched)}[/green]")
    table.add_row(t("cli_verify_row_mismatched"), f"[bold red]{len(result.mismatched)}[/bold red]" if result.mismatched else "[green]0[/green]")
    table.add_row(t("cli_verify_row_missing"), f"[bold red]{len(result.missing)}[/bold red]" if result.missing else "[green]0[/green]")
    table.add_row(t("cli_verify_row_untracked"), f"[yellow]{len(result.untracked)}[/yellow]" if result.untracked else "[green]0[/green]")
    table.add_row(t("cli_verify_row_skipped"), f"[cyan]{len(result.skipped)}[/cyan]")

    for idx, chain_res in enumerate(result.chain_results, 1):
        chain_status = f"[green]{t('cli_verify_chain_status_ok')}[/green]" if chain_res.is_valid else f"[bold red]{t('cli_verify_chain_status_bad')}[/bold red]"
        files_str = t("cli_verify_chain_matched_files", count=len(chain_res.matched))
        layer_label = t("cli_verify_row_chain_layer", idx=idx)
        table.add_row(layer_label, f"{chain_res.archive_path.name} -> {chain_status} {files_str}")

    console.print(table)

    if result.warnings:
        for w in result.warnings:
            console.print(f"[bold yellow]⚠ {t('cli_verify_warning_prefix')}[/bold yellow] {w}")

    if result.mismatched:
        console.print(f"[bold red]{t('cli_verify_hdr_mismatched')}[/bold red]")
        for m in result.mismatched:
            console.print(f"  [red]✖ {m}[/red]")

    if result.missing:
        console.print(f"[bold red]{t('cli_verify_hdr_missing')}[/bold red]")
        for mis in result.missing:
            console.print(f"  [red]✖ {mis}[/red]")

    if result.untracked:
        console.print(f"[bold yellow]{t('cli_verify_hdr_untracked')}[/bold yellow]")
        for unt in result.untracked:
            console.print(f"  [yellow]ℹ {unt}[/yellow]")

    if result.is_valid:
        console.print(f"[bold green]✔ {t('cli_verify_success')}[/bold green]")
        sys.exit(0)
    else:
        reasons = []
        if result.error_message:
            reasons.append(result.error_message)
        if result.mismatched:
            reasons.append(t("cli_verify_fail_mismatched", count=len(result.mismatched)))
        if result.missing:
            reasons.append(t("cli_verify_fail_missing", count=len(result.missing)))
        if result.untracked:
            reasons.append(t("cli_verify_fail_untracked", count=len(result.untracked)))
        for idx, chain_res in enumerate(result.chain_results, 1):
            if not chain_res.is_valid:
                cr_reason = chain_res.error_message or t("cli_verify_fail_chain_layer_invalid")
                reasons.append(t("cli_verify_fail_chain_invalid", idx=idx, name=chain_res.archive_path.name, reason=cr_reason))
        if not reasons:
            reasons.append(t("cli_verify_fail_default"))

        reasons_str = "; ".join(reasons)
        console.print(f"[bold red]✖ {t('cli_verify_fail_summary', reasons=reasons_str)}[/bold red]")
        sys.exit(1)


def build_parser(lang: Optional[str] = None) -> argparse.ArgumentParser:
    active_lang = normalize_language(lang) if lang else get_current_language()

    exit_codes_help = t("cli_epilog_main", lang=active_lang)
    backup_epilog = t("cli_epilog_backup", lang=active_lang)
    append_epilog = t("cli_epilog_append", lang=active_lang)

    lang_parent = argparse.ArgumentParser(add_help=False)
    lang_parent.add_argument(
        "--lang",
        choices=["tr", "en"],
        default=argparse.SUPPRESS,
        help=t("cli_opt_lang", lang=active_lang)
    )

    parser = argparse.ArgumentParser(
        prog="distronomad",
        description=t("cli_app_desc", lang=active_lang),
        epilog=exit_codes_help,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--home", type=str, default=None, help=t("cli_opt_home", lang=active_lang))
    parser.add_argument(
        "--lang",
        choices=["tr", "en"],
        default=None,
        help=t("cli_opt_lang", lang=active_lang)
    )

    subparsers = parser.add_subparsers(dest="command")

    # tui komutu
    p_tui = subparsers.add_parser(
        "tui",
        parents=[lang_parent],
        help=t("cli_cmd_tui_help", lang=active_lang)
    )

    # scan komutu
    subparsers.add_parser(
        "scan",
        parents=[lang_parent],
        help=t("cli_cmd_scan_help", lang=active_lang)
    )

    # backup komutu
    p_backup = subparsers.add_parser(
        "backup",
        parents=[lang_parent],
        help=t("cli_cmd_backup_help", lang=active_lang),
        epilog=backup_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p_backup.add_argument("--dest", required=True, help=t("cli_opt_backup_dest", lang=active_lang))
    p_backup.add_argument("--format", choices=["tar.zst", "tar.gz", "dir"], default="tar.zst", help=t("cli_opt_backup_format", lang=active_lang))
    p_backup.add_argument("--configs-only", action="store_true", help=t("cli_opt_configs_only", lang=active_lang))
    p_backup.add_argument("--no-sensitive", action="store_true", help=t("cli_opt_no_sensitive", lang=active_lang))
    p_backup.add_argument("--incremental", "--inc", action="store_true", help=t("cli_opt_incremental", lang=active_lang))
    p_backup.add_argument("--append", action="store_true", help=t("cli_opt_append", lang=active_lang))
    p_backup.add_argument("--base", type=str, default=None, help=t("cli_opt_backup_base", lang=active_lang))
    p_backup.add_argument("--password", "-p", nargs="?", const=CLI_PASSWORD_SENTINEL, default=None, help=t("cli_opt_password", lang=active_lang))
    p_backup.add_argument("--no-ignore", action="store_true", help=t("cli_opt_no_ignore", lang=active_lang))
    p_backup.add_argument("--force", action="store_true", help=t("cli_opt_force", lang=active_lang))

    # append komutu
    p_append = subparsers.add_parser(
        "append",
        parents=[lang_parent],
        help=t("cli_cmd_append_help", lang=active_lang),
        epilog=append_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p_append.add_argument("--backup", required=True, help=t("cli_opt_append_backup", lang=active_lang))
    p_append.add_argument("--path", action="append", default=[], help=t("cli_opt_append_path", lang=active_lang))
    p_append.add_argument("--config", action="append", default=[], help=t("cli_opt_append_config", lang=active_lang))
    p_append.add_argument("--package", action="append", default=[], help=t("cli_opt_append_package", lang=active_lang))
    p_append.add_argument("--configs-only", action="store_true", help=t("cli_opt_append_configs_only", lang=active_lang))
    p_append.add_argument("--no-sensitive", action="store_true", help=t("cli_opt_append_no_sensitive", lang=active_lang))
    p_append.add_argument("--password", "-p", nargs="?", const=CLI_PASSWORD_SENTINEL, default=None, help=t("cli_opt_append_password", lang=active_lang))
    p_append.add_argument("--force", action="store_true", help=t("cli_opt_append_force", lang=active_lang))

    # restore komutu
    p_restore = subparsers.add_parser(
        "restore",
        parents=[lang_parent],
        help=t("cli_cmd_restore_help", lang=active_lang)
    )
    p_restore.add_argument("--src", required=True, help=t("cli_opt_restore_src", lang=active_lang))
    p_restore.add_argument("--base", type=str, default=None, help=t("cli_opt_restore_base", lang=active_lang))
    p_restore.add_argument("--target-home", type=str, default=None, help=t("cli_opt_restore_target_home", lang=active_lang))
    p_restore.add_argument("--conflict", choices=["backup", "overwrite", "skip"], default="backup", help=t("cli_opt_restore_conflict", lang=active_lang))
    p_restore.add_argument("--dry-run", action="store_true", help=t("cli_opt_restore_dry_run", lang=active_lang))
    p_restore.add_argument("--configs-only", action="store_true", help=t("cli_opt_restore_configs_only", lang=active_lang))
    p_restore.add_argument("--packages-only", action="store_true", help=t("cli_opt_restore_packages_only", lang=active_lang))
    p_restore.add_argument("--restore-system-state", action="store_true", default=False, help=t("cli_opt_restore_system_state", lang=active_lang))
    p_restore.add_argument("--install-drivers", action="store_true", default=False, help=t("cli_opt_restore_install_drivers", lang=active_lang))
    p_restore.add_argument("--password", "-p", nargs="?", const=CLI_PASSWORD_SENTINEL, default=None, help=t("cli_opt_restore_password", lang=active_lang))
    p_restore.add_argument("--allow-external-paths", "--allow-root-paths", action="store_true", default=False, dest="allow_external_paths", help=t("cli_opt_restore_allow_external", lang=active_lang))

    # diff komutu
    p_diff = subparsers.add_parser(
        "diff",
        parents=[lang_parent],
        help=t("cli_cmd_diff_help", lang=active_lang)
    )
    p_diff.add_argument("--src", required=True, help=t("cli_opt_diff_src", lang=active_lang))
    p_diff.add_argument("--target-home", type=str, default=None, help=t("cli_opt_diff_target_home", lang=active_lang))
    p_diff.add_argument("--password", "-p", nargs="?", const=CLI_PASSWORD_SENTINEL, default=None, help=t("cli_opt_diff_password", lang=active_lang))
    p_diff.add_argument("--show-content", action="store_true", help=t("cli_opt_diff_show_content", lang=active_lang))

    # rollback komutu
    p_rollback = subparsers.add_parser(
        "rollback",
        parents=[lang_parent],
        help=t("cli_cmd_rollback_help", lang=active_lang)
    )
    p_rollback.add_argument("--tx", type=str, default=None, help=t("cli_opt_rollback_tx", lang=active_lang))
    p_rollback.add_argument("--target-home", type=str, default=None, help=t("cli_opt_rollback_target_home", lang=active_lang))

    # schedule komutu
    p_schedule = subparsers.add_parser(
        "schedule",
        parents=[lang_parent],
        help=t("cli_cmd_schedule_help", lang=active_lang)
    )
    p_schedule.add_argument("--enable", action="store_true", help=t("cli_opt_schedule_enable", lang=active_lang))
    p_schedule.add_argument("--disable", action="store_true", help=t("cli_opt_schedule_disable", lang=active_lang))
    p_schedule.add_argument("--interval", choices=["hourly", "daily", "weekly", "monthly"], default="daily", help=t("cli_opt_schedule_interval", lang=active_lang))
    p_schedule.add_argument("--dest", type=str, default=None, help=t("cli_opt_schedule_dest", lang=active_lang))

    # verify komutu
    p_verify = subparsers.add_parser(
        "verify",
        parents=[lang_parent],
        help=t("cli_cmd_verify_help", lang=active_lang)
    )
    p_verify.add_argument("--src", required=True, help=t("cli_opt_verify_src", lang=active_lang))
    p_verify.add_argument("--base", type=str, default=None, help=t("cli_opt_verify_base", lang=active_lang))
    p_verify.add_argument("--password", "-p", nargs="?", const=CLI_PASSWORD_SENTINEL, default=None, help=t("cli_opt_verify_password", lang=active_lang))

    return parser


def setup_cli_logging() -> None:
    """TUI dışındaki alt komutlar için logging'i WARNING seviyesinde stderr'e yönlendirir."""
    import logging
    pkg_logger = logging.getLogger("distronomad")
    pkg_logger.setLevel(logging.WARNING)
    for h in pkg_logger.handlers:
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr:
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    formatter = logging.Formatter("%(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    pkg_logger.addHandler(handler)


def main(argv: list[str] | None = None):
    global _ENV_PASSWORD
    # Parolayı ortamdan çıkar: alt süreçler (paket yöneticileri, npm, code vb.) devralmasın.
    _ENV_PASSWORD = os.environ.pop("DISTRONOMAD_PASSWORD", None)

    if argv is None:
        argv = sys.argv[1:]

    cli_lang = preparse_language(argv)
    if cli_lang:
        active_lang = cli_lang
    else:
        active_lang = detect_default_language()

    set_current_language(active_lang, persist=False)
    parser = build_parser(lang=active_lang)
    args = parser.parse_args(argv)

    if getattr(args, "lang", None):
        active_lang = normalize_language(args.lang)
        set_current_language(active_lang, persist=False)

    if args.command not in (None, "tui"):
        setup_cli_logging()

    if args.command == "tui":
        run_tui(home_dir=args.home, lang=getattr(args, "lang", None) or active_lang)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "backup":
        cmd_backup(args)
    elif args.command == "append":
        cmd_append(args)
    elif args.command == "restore":
        cmd_restore(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "rollback":
        cmd_rollback(args)
    elif args.command == "schedule":
        cmd_schedule(args)
    else:
        # Varsayılan: TUI
        run_tui(home_dir=args.home, lang=getattr(args, "lang", None) or active_lang)


if __name__ == "__main__":
    main()
