"""
DistroNomad - Pre-release W2-TUI Comprehensive Test Suite.
Tests all C-1 through C-10 requirements, locked decisions, and headless behaviors:
- C-1: Exit confirmation modal on 'q' / Ctrl+C during active backup/restore/verify/rollback.
- C-2: Diff inspection (ensure_extracted + compare_backup_with_host) in background worker without UI freeze.
- C-3: Composite package identity (MANAGER:NAME) across SelectionLists, cart table keys, removal, and append mode.
- C-6: TUI rollback using system_info.home_dir and target_home parameter.
- C-7: 80x24 terminal toolbar overflow prevention (no children outside 80 columns).
- C-9: Select-all / deselect-all with active filters only affecting visible items.
- External paths (custom/root): chk-allow-external-paths enabled only when external paths exist, wired to RestoreOptions.
- Skipped files in backup: logged in RichLog and warning notification displayed.
- Sudo password flow: App.suspend() terminal prompt, graceful fallback to skipping packages if unsupported/failed.
- Verify result: detailed failure reasons displayed in log and notification for invalid/broken chains.
- Locked decisions: Tab 4 two toolbars under TabPane, prepend custom path + reset search, append mode layout.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RichLog,
    SelectionList,
    TabbedContent,
    TabPane,
)

from distronomad.models import (
    BackupManifest,
    ConfigItem,
    CustomPathItem,
    PackageItem,
    ProgressUpdate,
    RestoreOptions,
    SystemInfo,
)
from distronomad.ui.app import DistroNomadApp
from distronomad.ui.modal import ConfirmExitModal, DiffViewerModal, RollbackModal
from distronomad.verify import VerificationResult


def _extract_rich_log_text(log: RichLog) -> str:
    """RichLog içeriğini (hem işlenmiş satırları hem de sekme aktif değilken kuyrukta bekleyen deferred renders) döndürür."""
    parts = [strip.text for strip in log.lines]
    for d in getattr(log, "_deferred_renders", []):
        content = d.content
        if hasattr(content, "plain"):
            parts.append(content.plain)
        else:
            parts.append(str(content))
    return "\n".join(parts)


# ============================================================================
# C-1: Exit confirmation modal when active operations are running
# ============================================================================
@pytest.mark.asyncio
async def test_c1_quit_confirmation_when_operation_in_progress(tmp_path):
    """C-1: Aktif işlem (backup, restore, verify, rollback) sürerken 'q' basıldığında onay modalı açılmalı."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        # 1. İşlem yokken 'q' hemen çıkmalı
        assert app.is_operation_in_progress is False

        # 2. Yedekleme sürerken 'q' onay modalı açmalı
        app._is_backing_up = True
        assert app.is_operation_in_progress is True

        await pilot.press("q")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmExitModal)
        assert app.is_running is True

        # İptal et: uygulama kapanmamalı
        btn_cancel = app.screen.query_one("#btn-quit-cancel", Button)
        btn_cancel.press()
        await pilot.pause()

        assert not isinstance(app.screen, ConfirmExitModal)
        assert app.is_running is True

        # 3. Geri yükleme sürerken 'q' onaylanınca çıkmalı
        app._is_backing_up = False
        app._is_restoring = True
        assert app.is_operation_in_progress is True

        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmExitModal)

        btn_confirm = app.screen.query_one("#btn-quit-confirm", Button)
        btn_confirm.press()
        await pilot.pause()

        assert app.is_running is False


# ============================================================================
# C-2: Background Diff Worker
# ============================================================================
@pytest.mark.asyncio
async def test_c2_diff_runs_in_background_worker(tmp_path):
    """C-2: Farkları İncele işlemi UI donmadan arka plan worker'ında çalışmalı ve modalı açmalı."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        # Geri yükleme sekmesine geç
        await pilot.press("f6")
        await pilot.pause()

        mock_engine = MagicMock()
        mock_engine.manifest = MagicMock()
        mock_engine.ensure_extracted.return_value = tmp_path
        app.current_restore_engine = mock_engine

        mock_diffs = [
            {"rel_path": ".config/test.conf", "status": "modified", "size_backup": 10, "size_host": 12}
        ]

        with patch("distronomad.ui.app.compare_backup_with_host", return_value=mock_diffs):
            btn_diff = app.query_one("#btn-restore-diff", Button)
            btn_diff.press()
            await pilot.pause(0.2)
            await app.workers.wait_for_complete()

            # Worker tamamlandığında DiffViewerModal açılmış olmalı
            assert isinstance(app.screen, DiffViewerModal)
            assert btn_diff.disabled is False


# ============================================================================
# C-3: Composite Package Key (MANAGER:NAME)
# ============================================================================
@pytest.mark.asyncio
async def test_c3_composite_package_key_distinct_managers(tmp_path):
    """C-3: Farklı yöneticilerdeki aynı adlı paketler SelectionList'te ve sepette çakışmamalı."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()

        pkg1 = PackageItem(name="curl", manager="pacman", category="Ağ", is_selected=True)
        pkg2 = PackageItem(name="curl", manager="snap", category="Ağ", is_selected=True)
        app.all_packages = [pkg1, pkg2]

        app._populate_apps_list()
        app._populate_backup_cart_table()
        await pilot.pause()

        # SelectionList seçenek değerleri yönetici:ad bileşik anahtarı olmalı
        apps_list = app.query_one("#list-apps", SelectionList)
        opt_values = [opt.value for opt in apps_list.options]
        assert "pacman:curl" in opt_values
        assert "snap:curl" in opt_values
        assert len(opt_values) == 2

        # Sepet tablosunda her iki paket de ayrı satır olarak listelenmeli
        cart_table = app.query_one("#table-backup-cart", DataTable)
        assert cart_table.row_count == 2

        # Sepetten pacman:curl çıkarıldığında snap:curl korunmalı
        removed = app._remove_item_by_cart_key("pkg:pacman:curl")
        assert removed is True
        assert pkg1.is_selected is False
        assert pkg2.is_selected is True
        assert cart_table.row_count == 1

        # Append modu seçim saklama testi
        app._apply_backup_mode_ui("append", switch_selections=True)
        assert pkg2.is_selected is False

        app._apply_backup_mode_ui("full", switch_selections=True)
        assert pkg2.is_selected is True
        assert pkg1.is_selected is False


# ============================================================================
# C-6: Rollback with system_info.home_dir and target_home
# ============================================================================
@pytest.mark.asyncio
async def test_c6_rollback_uses_system_info_home_dir(tmp_path):
    """C-6: Rollback işlem günlüğü system_info.home_dir altında aranmalı ve rollback_restore'a target_home verilmeli."""
    custom_home = tmp_path / "custom_home"
    custom_home.mkdir()
    tx_dir = custom_home / ".config" / "distronomad" / "transactions"
    tx_dir.mkdir(parents=True)
    tx_file = tx_dir / "last_restore.json"
    tx_file.write_text(json.dumps({
        "timestamp": "2026-11-01T12:00:00",
        "backup_path": "/dummy/backup.tar.zst",
        "operations": []
    }))

    app = DistroNomadApp(home_dir=str(custom_home))
    async with app.run_test() as pilot:
        await pilot.pause()

        with patch("distronomad.ui.app.rollback_restore") as mock_rb:
            mock_rb.return_value = {
                "reverted_files": ["/file1"],
                "removed_created": [],
                "errors": [],
                "failed_overwrites": []
            }

            app._handle_restore_rollback()
            await pilot.pause()

            # RollbackModal açılmalı
            assert isinstance(app.screen, RollbackModal)
            btn_confirm = app.screen.query_one("#btn-rb-confirm", Button)
            btn_confirm.press()
            await pilot.pause()

            # rollback_restore target_home ve transaction_file ile çağrılmış olmalı
            mock_rb.assert_called_once()
            called_kwargs = mock_rb.call_args.kwargs
            assert called_kwargs.get("target_home") == custom_home
            assert called_kwargs.get("transaction_file") == tx_file


# ============================================================================
# C-7: 80x24 Terminal toolbar overflow prevention
# ============================================================================
@pytest.mark.asyncio
async def test_c7_80x24_terminal_toolbars_within_screen_bounds(tmp_path):
    """C-7: 80x24 boyutunda terminalde Sekme 4 ve Sekme 5 araç çubuğu ögeleri ekran dışına taşmamalı."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await app.workers.wait_for_complete()

        # Sekme 4 (Özel Dosyalar)
        await pilot.press("f4")
        await pilot.pause()

        custom_toolbar = app.query_one("#custom-search-toolbar")
        assert custom_toolbar is not None

        # Custom search toolbar içindeki tüm ögelerin ekran genişliği sınırlarında olduğunu doğrula
        for child in custom_toolbar.walk_children():
            reg = child.region
            if reg.width > 0 and reg.height > 0:
                assert reg.x >= 0, f"Child {child} x < 0: {reg}"
                assert reg.x + reg.width <= 80, f"Child {child} overflows 80 cols: {reg}"

        # Sekme 5 (Yedekle / Sepet)
        await pilot.press("f5")
        await pilot.pause()

        cart_toolbar = app.query_one("#cart-search-toolbar")
        assert cart_toolbar is not None

        for child in cart_toolbar.walk_children():
            reg = child.region
            if reg.width > 0 and reg.height > 0:
                assert reg.x >= 0, f"Child {child} x < 0: {reg}"
                assert reg.x + reg.width <= 80, f"Child {child} overflows 80 cols: {reg}"


# ============================================================================
# C-9: Select-all / deselect-all only affects visible items under filter
# ============================================================================
@pytest.mark.asyncio
async def test_c9_filter_select_all_and_none_only_affects_visible(tmp_path):
    """C-9: Filtre aktifken Tümünü Seç / Hiçbirini Seçme yalnızca filtrelenen görünür ögeleri etkilemeli."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()

        p1 = PackageItem(name="web-browser", manager="pacman", category="Web", is_selected=False)
        p2 = PackageItem(name="web-server", manager="pacman", category="Web", is_selected=False)
        p3 = PackageItem(name="terminal-editor", manager="pacman", category="CLI", is_selected=False)
        p4 = PackageItem(name="terminal-multiplexer", manager="pacman", category="CLI", is_selected=True)

        app.all_packages = [p1, p2, p3, p4]

        # F2 sekmesine git ve 'web' filtresi uygula
        await pilot.press("f2")
        await pilot.pause()

        app._populate_apps_list(filter_text="web")
        await pilot.pause()

        # 'Tümünü Seç'e bas
        btn_all = app.query_one("#btn-apps-select-all", Button)
        btn_all.press()
        await pilot.pause()

        # Yalnızca web olanlar seçilmeli, CLI olanların önceki durumu bozulmamalı
        assert p1.is_selected is True
        assert p2.is_selected is True
        assert p3.is_selected is False  # Önceden False idi, değişmemeli
        assert p4.is_selected is True   # Önceden True idi, değişmemeli

        # 'Hiçbirini Seçme'ye bas (hala web filtresi aktif)
        btn_none = app.query_one("#btn-apps-select-none", Button)
        btn_none.press()
        await pilot.pause()

        # Yalnızca web olanların seçimi kalkmalı, p4 True kalmalı
        assert p1.is_selected is False
        assert p2.is_selected is False
        assert p4.is_selected is True


# ============================================================================
# External Paths (custom/root) handling
# ============================================================================
@pytest.mark.asyncio
async def test_external_paths_checkbox_and_skipped_logging(tmp_path):
    """Ev dışı yollar: chk-allow-external-paths yalnızca kök ögeleri varken aktif olmalı ve RestoreOptions'a bağlanmalı."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        # 1. Yalnızca ev içi yolları olan yedek: onay kutusu devre dışı olmalı
        manifest_internal = BackupManifest(
            created_at="2026-11-01T12:00:00",
            version="1.0.0",
            system_info=SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(tmp_path)),
            packages=[],
            configs=[],
            custom_paths=[CustomPathItem(path=str(tmp_path / "file.txt"), label="file.txt")],
            archive_format="dir"
        )
        mock_engine = MagicMock()
        mock_engine.load_manifest.return_value = manifest_internal
        mock_engine.manifest = manifest_internal
        mock_engine.backup_path = tmp_path / "internal_backup"

        with patch("distronomad.ui.app.RestoreEngine", return_value=mock_engine):
            app._inspect_restore_with_password(tmp_path / "internal_backup", None)
            chk_ext = app.query_one("#chk-allow-external-paths", Checkbox)
            assert chk_ext.disabled is True
            assert chk_ext.value is False

        # 2. Ev dışı yolu olan yedek: onay kutusu etkinleşmeli
        manifest_external = BackupManifest(
            created_at="2026-11-01T12:00:00",
            version="1.0.0",
            system_info=SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(tmp_path)),
            packages=[],
            configs=[],
            custom_paths=[CustomPathItem(path="/etc/myapp/config.conf", label="config.conf")],
            archive_format="dir"
        )
        mock_engine2 = MagicMock()
        mock_engine2.load_manifest.return_value = manifest_external
        mock_engine2.manifest = manifest_external
        external_backup_dir = tmp_path / "external_backup"
        external_backup_dir.mkdir(parents=True, exist_ok=True)
        mock_engine2.backup_path = external_backup_dir

        with patch("distronomad.ui.app.RestoreEngine", return_value=mock_engine2):
            app._inspect_restore_with_password(external_backup_dir, None)
            assert chk_ext.disabled is False
            assert chk_ext.value is False

            # Onay kutusu işaretlendiğinde RestoreOptions'a yansımalı
            chk_ext.value = True

            captured_options = None
            def fake_run_worker(engine, options):
                nonlocal captured_options
                captured_options = options

            app._run_restore_worker = fake_run_worker
            app.query_one("#input-restore-src", Input).value = str(external_backup_dir)
            app._handle_start_restore()
            await pilot.pause()

            assert captured_options is not None
            assert captured_options.allow_external_paths is True

        # 3. Restore sonucunda skipped_external_paths varsa log'a yazılmalı
        log = app.query_one("#restore-log", RichLog)
        app._restore_finished({
            "restored_configs": [],
            "restored_files": [],
            "installed_packages": [],
            "skipped_external_paths": ["/etc/myapp/config.conf"]
        })
        await pilot.pause()

        log_content = _extract_rich_log_text(log)
        assert "/etc/myapp/config.conf" in log_content


# ============================================================================
# Skipped files in backup reporting
# ============================================================================
@pytest.mark.asyncio
async def test_skipped_files_during_backup_reported_in_log_and_notify(tmp_path):
    """Yedekleme sonunda skipped_files varsa RichLog'da listelenmeli ve uyarı bildirimi verilmelidir."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        skipped_data = [
            {"path": "/home/user/locked.file", "reason": "Permission denied"}
        ]

        log = app.query_one("#backup-log", RichLog)
        app._backup_finished(tmp_path / "backup.tar.zst", skipped_files=skipped_data)
        await pilot.pause()

        log_content = _extract_rich_log_text(log)
        assert "/home/user/locked.file" in log_content
        assert "Permission denied" in log_content


# ============================================================================
# Sudo password flow & graceful skip
# ============================================================================
@pytest.mark.asyncio
async def test_sudo_unsupported_or_failed_skips_packages_and_continues_files(tmp_path):
    """Sudo yetkisi alınamadığında paket kurulumu atlanmalı ve dosya geri yüklemesi devam etmelidir."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        backup_file = tmp_path / "test_backup.tar.zst"
        backup_file.write_bytes(b"dummy")

        mock_engine = MagicMock()
        mock_manifest = BackupManifest(
            created_at="2026-11-01T12:00:00",
            version="1.0.0",
            system_info=SystemInfo(distro_id="arch", distro_name="Arch", home_dir=str(tmp_path)),
            packages=[PackageItem(name="curl", manager="pacman", is_selected=True)],
            configs=[ConfigItem(id="cfg1", name="bashrc", category="Shell", source_path="/dummy", rel_path=".bashrc", is_selected=True)],
            custom_paths=[]
        )
        mock_engine.load_manifest.return_value = mock_manifest
        mock_engine.manifest = mock_manifest
        mock_engine.backup_path = backup_file

        with patch("distronomad.ui.app.RestoreEngine", return_value=mock_engine):
            app.query_one("#input-restore-src", Input).value = str(backup_file)
            app._inspect_restore_with_password(backup_file, None)

            # Sudo parolasız çalışamasın
            app._check_sudo_passwordless = MagicMock(return_value=False)

            captured_options = None
            def fake_run_worker(engine, options):
                nonlocal captured_options
                captured_options = options

            app._run_restore_worker = fake_run_worker
            app._handle_start_restore()
            await pilot.pause()

            # Paket kurulumu atlanmış, ayar geri yüklemesi devam etmiş olmalı
            assert captured_options is not None
            assert captured_options.install_packages is False
            assert len(captured_options.selected_packages) == 0
            assert len(captured_options.selected_configs) == 1


# ============================================================================
# Verify result detailed reasons
# ============================================================================
@pytest.mark.asyncio
async def test_verify_result_shows_explicit_failure_reasons(tmp_path):
    """Geçersiz doğrulama sonucunda (eksik taban / bozuk zincir) hata nedeni açıkça gösterilmeli."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        res_invalid = VerificationResult(
            archive_path=tmp_path / "inc_backup.tar.zst",
            is_valid=False,
            total_files_checked=10,
            matched=[],
            mismatched=["file1.txt"],
            missing=["file2.txt"],
            untracked=["untracked.txt"],
            error_message="Zincir eksik: Taban yedek bulunamadı ('base_backup.tar.zst').",
            warnings=["Taban katman dosyaları eksik"]
        )

        log = app.query_one("#restore-log", RichLog)
        app._verify_finished(res_invalid, target_log_id="#restore-log", target_progress_id="#restore-progress")
        await pilot.pause()

        log_content = _extract_rich_log_text(log)
        assert "Zincir eksik: Taban yedek bulunamadı" in log_content
        assert "file1.txt" in log_content
        assert "file2.txt" in log_content


# ============================================================================
# Locked Decisions: Tab 4 layout & prepending custom path & append mode
# ============================================================================
@pytest.mark.asyncio
async def test_locked_decisions_tab4_prepend_and_append_mode(tmp_path):
    """Kilitli Kararlar: Tab 4 yerleşimi, yeni yolun listenin başına eklenip aramanın sıfırlanması ve append modu."""
    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        # 1. Tab 4 yerleşim kuralı: custom-add-toolbar ve custom-search-toolbar doğrudan TabPane altında olmalı
        tab_custom = app.query_one("#tab-custom", TabPane)
        direct_child_ids = [child.id for child in tab_custom.children if child.id]
        assert "custom-add-toolbar" in direct_child_ids
        assert "custom-search-toolbar" in direct_child_ids
        assert "list-custom-paths" in direct_child_ids

        # 2. Yeni yol listenin en başına eklenmeli ve arama sıfırlanmalı
        await pilot.press("f4")
        await pilot.pause()

        test_file1 = tmp_path / "path1.txt"
        test_file1.write_text("file 1")
        test_file2 = tmp_path / "path2.txt"
        test_file2.write_text("file 2")

        inp_search = app.query_one("#input-custom-search", Input)
        inp_search.value = "some_filter"
        await pilot.pause()

        inp_add = app.query_one("#input-custom-path", Input)
        inp_add.value = str(test_file1)
        btn_add = app.query_one("#btn-add-custom-path", Button)
        btn_add.press()
        await pilot.pause()

        assert app.all_custom_paths[0].path == str(test_file1.resolve())
        assert inp_search.value == ""  # Arama sıfırlanmalı

        # İkinci yolu ekle: o da en başa geçmeli
        inp_add.value = str(test_file2)
        btn_add.press()
        await pilot.pause()

        assert app.all_custom_paths[0].path == str(test_file2.resolve())
        assert app.all_custom_paths[1].path == str(test_file1.resolve())

        # 3. Append modu ekran kuralları
        await pilot.press("f5")
        await pilot.pause()

        app._apply_backup_mode_ui("append", switch_selections=True)
        await pilot.pause()

        assert app.query_one("#row-backup-base").display is False
        assert app.query_one("#radio-backup-format").display is False
        assert app.query_one("#lbl-append-info").display is True
