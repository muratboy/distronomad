import typing
import pytest
from distronomad.restore import RestoreEngine, rollback_restore


def test_restore_type_hints_any():
    """P0 (1): restore.py typing.get_type_hints evaluation for Any."""
    hints_exec = typing.get_type_hints(RestoreEngine.execute_restore)
    assert hints_exec["return"] is not None
    hints_rb = typing.get_type_hints(rollback_restore)
    assert hints_rb["return"] is not None


def test_ui_app_json_imported(monkeypatch, tmp_path):
    """P0 (2): ui/app.py json import and rollback handler."""
    import distronomad.ui.app as app_mod
    assert hasattr(app_mod, "json"), "json is not imported in distronomad.ui.app"


@pytest.mark.asyncio
async def test_ui_app_cart_duplicate_key(tmp_path):
    """P0 (3): Duplicate key in cart must not crash with DuplicateKey error."""
    from distronomad.ui.app import DistroNomadApp
    from distronomad.models import PackageItem

    home = tmp_path / "mock_home"
    home.mkdir()

    app = DistroNomadApp(home_dir=str(home))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.all_packages = [
            PackageItem(name="git", manager="pacman", is_selected=True),
            PackageItem(name="git", manager="pacman", is_selected=True),
        ]
        # This will call table.add_row with key="pkg:pacman:git" twice
        app._populate_backup_cart_table()
        cart_table = app.query_one("#table-backup-cart")
        assert cart_table.row_count == 1


def test_discovery_scan_npm_ignores_npm_submodules(monkeypatch):
    """P0 (3): discovery._scan_npm must not return npm or npm's own submodules."""
    import subprocess
    from distronomad.discovery import SystemScanner

    class MockProc:
        returncode = 0
        stdout = '{"dependencies": {"npm": {"version": "10.8.2"}, "corepack": {"version": "0.28.0"}, "typescript": {"version": "5.4.5"}}}'
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: MockProc())
    scanner = SystemScanner()
    pkgs = scanner._scan_npm()
    pkg_names = [p.name for p in pkgs]
    assert "npm" not in pkg_names
    assert "corepack" not in pkg_names
    assert "typescript" in pkg_names


def test_automation_exec_cmd_cli_arguments(tmp_path):
    """P0 (4): automation.py ExecStart must match cli.py args (--dest, no --output, no --base)."""
    import shlex
    from distronomad.automation import enable_timer
    from distronomad.cli import build_parser

    mock_systemd = tmp_path / "systemd_user"
    out_dir = tmp_path / "backup_out"
    enable_timer(interval="daily", backup_output_dir=out_dir, systemd_dir=mock_systemd)

    service_file = mock_systemd / "distronomad-backup.service"
    assert service_file.exists()
    content = service_file.read_text(encoding="utf-8")

    exec_line = ""
    for line in content.splitlines():
        if line.startswith("ExecStart="):
            exec_line = line.split("=", 1)[1].strip()
            break

    assert "--output" not in exec_line, "--output should not be used in ExecStart"
    assert "--base" not in exec_line, "--base should not be used in ExecStart"
    assert "--dest" in exec_line, "--dest is required in cli backup"

    # Verify that cli parser accepts these exact args
    tokens = shlex.split(exec_line)
    # The command is python -m distronomad.cli backup --dest ...
    cli_args_idx = tokens.index("backup")
    cli_args = tokens[cli_args_idx:]
    parser = build_parser()
    parsed = parser.parse_args(cli_args)
    assert parsed.command == "backup"
    assert parsed.dest == str(out_dir)


@pytest.mark.asyncio
async def test_automation_modal_reports_failure_when_incomplete(monkeypatch):
    """P0 (4): Automation modal must not report success when timer enabling fails or is incomplete."""
    from distronomad.ui.modal import AutomationModal
    from textual.app import App, ComposeResult
    from distronomad import automation

    # Mock enable_timer returning systemctl failure
    monkeypatch.setattr(
        automation,
        "enable_timer",
        lambda **kwargs: {"success": False, "systemctl_enabled": False}
    )

    modal = AutomationModal()

    dismissed_result = None
    def mock_dismiss(result=None):
        nonlocal dismissed_result
        dismissed_result = result

    modal.dismiss = mock_dismiss

    class DummyButton:
        id = "btn-auto-enable"

    class DummyEvent:
        button = DummyButton()

    class DummyRadioSet:
        pressed_button = None

    monkeypatch.setattr(modal, "query_one", lambda *args, **kwargs: DummyRadioSet())
    modal.on_button_pressed(DummyEvent())

    assert dismissed_result != "enabled", "Modal must not report 'enabled' when enable_timer failed!"
    assert dismissed_result == "failed"


def test_packagemap_apt_argv_no_shell_operator():
    """P0 (5): packagemap.py:281 apt argv must not contain '&&'."""
    from distronomad.packagemap import get_install_command

    cmd, desc = get_install_command("apt", ["curl", "git"])
    assert "&&" not in cmd, "Shell operator '&&' must be removed from apt argv"
    assert cmd == ["sudo", "apt-get", "install", "-y", "--", "curl", "git"]


def test_restore_executes_apt_update_and_install_separately(monkeypatch, tmp_path):
    """P0 (5): restore.py caller must run apt update and install as separate argv without shell."""
    import subprocess
    from distronomad.restore import RestoreEngine
    from distronomad.models import RestoreOptions, BackupManifest, SystemInfo, PackageItem

    executed_cmds = []

    class MockCompletedProcess:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def mock_subprocess_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        return MockCompletedProcess()

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # Prepare dummy backup dir with manifest
    backup_dir = tmp_path / "mock_backup"
    backup_dir.mkdir()

    manifest = BackupManifest(
        created_at="2026-10-20T00:00:00",
        version="1.0.0",
        system_info=SystemInfo.current(),
        packages=[PackageItem(name="curl", manager="apt", is_selected=True)],
        configs=[],
        custom_paths=[],
        archive_format="dir"
    )

    import json
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    engine = RestoreEngine(backup_path=backup_dir)
    engine.load_manifest(extract_files=False)

    target_sys = SystemInfo(
        distro_id="ubuntu",
        distro_name="Ubuntu",
        distro_like="debian",
        distro_version="24.04",
        available_package_managers=["apt"],
        home_dir=str(tmp_path / "target_home")
    )

    options = RestoreOptions(
        install_packages=True,
        selected_packages=[PackageItem(name="curl", manager="apt", is_selected=True)],
        selected_configs=[],
        selected_custom_paths=[],
        restore_system_state=False,
        target_home=str(tmp_path / "target_home")
    )

    results = engine.execute_restore(options, target_system_info=target_sys)
    assert "curl" in results["installed_packages"]

    # Verify executed commands: sudo precheck, update, install
    assert len(executed_cmds) == 3
    assert executed_cmds[0] == ["sudo", "-n", "true"]
    assert executed_cmds[1] == ["sudo", "apt-get", "update"]
    assert executed_cmds[2] == ["sudo", "apt-get", "install", "-y", "--", "curl"]
    for c in executed_cmds:
        assert "&&" not in c




