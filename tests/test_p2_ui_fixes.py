"""
Regression tests for P2 UI fixes:
1. Refresh preserves user-added custom paths and user selections (including in append mode).
2. Restore uses updated RestoreEngine with latest options and cleans up previous engine.
3. Refresh uses background worker and single-pass cart population without O(N^2) storm.
"""

import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from textual.widgets import SelectionList, DataTable, Label, Input

from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem, BackupManifest
from distronomad.ui.app import DistroNomadApp
from distronomad.restore import RestoreEngine


@pytest.mark.asyncio
async def test_refresh_preserves_custom_paths_and_selections():
    """Item 1: Yenileme sırasında özel yollar ve seçimler korunmalı (Full mod)."""
    with tempfile.TemporaryDirectory() as tmp_home:
        home = Path(tmp_home)
        cfg_dir = home / ".config" / "testapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.ini").write_text("setting=1\n")

        custom_dir = home / "MyCustomDir"
        custom_dir.mkdir()
        (custom_dir / "note.txt").write_text("important data\n")

        app = DistroNomadApp(home_dir=str(home))
        async with app.run_test() as pilot:
            # Senkron uygula
            app._do_scan_sync()
            await pilot.pause()

            # Kullanıcı özel yol ekliyor
            app.user_added_paths.add(str(custom_dir.resolve()))
            user_item = CustomPathItem(
                path=str(custom_dir.resolve()),
                label="MyCustomDir",
                exists=True,
                is_dir=True,
                size_bytes=100,
                file_count=1,
                is_selected=True
            )
            app.all_custom_paths.insert(0, user_item)

            # Bir paketi seç, bir ayarın seçimini kaldır
            if app.all_packages:
                app.all_packages[0].is_selected = True
                pkg_name = app.all_packages[0].name
            else:
                pkg_name = None

            if app.all_configs:
                app.all_configs[0].is_selected = False
                cfg_id = app.all_configs[0].id
            else:
                cfg_id = None

            # Yenileme tetikle
            app._do_scan_sync()
            await pilot.pause()

            # 1. Özel yol korunmalı ve en başta olmalı
            custom_paths = [cp.path for cp in app.all_custom_paths]
            assert str(custom_dir.resolve()) in custom_paths
            assert app.all_custom_paths[0].path == str(custom_dir.resolve())
            assert app.all_custom_paths[0].is_selected is True

            # 2. Seçimler korunmalı
            if pkg_name:
                p = next(p for p in app.all_packages if p.name == pkg_name)
                assert p.is_selected is True

            if cfg_id:
                c = next(c for c in app.all_configs if c.id == cfg_id)
                assert c.is_selected is False


@pytest.mark.asyncio
async def test_refresh_in_append_mode_preserves_append_selections():
    """Item 1: Append modundayken yenileme append seçim durumunu bozmamalı (R5)."""
    with tempfile.TemporaryDirectory() as tmp_home:
        home = Path(tmp_home)
        cfg_dir = home / ".config" / "testapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.ini").write_text("setting=1\n")

        custom_file = home / "custom.txt"
        custom_file.write_text("custom\n")

        app = DistroNomadApp(home_dir=str(home))
        async with app.run_test() as pilot:
            app._do_scan_sync()
            await pilot.pause()

            # Kullanıcı özel yol ekliyor
            app.user_added_paths.add(str(custom_file.resolve()))
            app.all_custom_paths.insert(0, CustomPathItem(
                path=str(custom_file.resolve()),
                label="custom.txt",
                exists=True,
                is_dir=False,
                size_bytes=10,
                file_count=1,
                is_selected=True
            ))

            # Append moduna geç
            app._apply_backup_mode_ui("append", switch_selections=True)
            await pilot.pause()

            # Append modunda standart ayarlar False olmalı
            assert all(not c.is_selected for c in app.all_configs)
            # Yalnızca user_added_paths seçili olmalı
            assert any(cp.is_selected for cp in app.all_custom_paths if cp.path == str(custom_file.resolve()))

            # Append modundayken yenile
            app._do_scan_sync()
            await pilot.pause()

            # Yenileme sonrasında standart ayarlar otomatik True OLMAMALI (append durumu bozulmamalı)
            assert all(not c.is_selected for c in app.all_configs)
            # Kullanıcının özel yolu halen korunmalı ve seçili olmalı
            assert str(custom_file.resolve()) in [cp.path for cp in app.all_custom_paths]
            assert any(cp.is_selected for cp in app.all_custom_paths if cp.path == str(custom_file.resolve()))


@pytest.mark.asyncio
async def test_restore_uses_updated_engine_and_cleans_up_old(tmp_path):
    """Item 2: Geri yükleme en son seçilen yedekle güncel RestoreEngine kullanmalı ve eski motoru temizlemeli."""
    backup_dir = tmp_path / "mock_backup"
    backup_dir.mkdir()

    manifest = BackupManifest(
        created_at="2026-10-20T00:00:00",
        version="1.0.0",
        system_info=SystemInfo.current(),
        packages=[PackageItem(name="htop", manager="pacman", is_selected=True)],
        configs=[],
        custom_paths=[],
        archive_format="dir"
    )
    (backup_dir / "manifest.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    app = DistroNomadApp(home_dir=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        # İlk yedek incelemesi
        app.query_one("#input-restore-src", Input).value = str(backup_dir)
        app._inspect_restore_with_password(backup_dir, None)

        old_engine = app.current_restore_engine
        assert old_engine is not None
        assert old_engine.manifest is not None

        old_cleanup_mock = MagicMock(side_effect=old_engine.cleanup)
        old_engine.cleanup = old_cleanup_mock

        executed_engine = None

        def mock_execute_restore(options, target_system_info=None):
            return {
                "restored_configs": [],
                "restored_files": [],
                "installed_packages": ["htop"],
                "failed_packages": [],
                "untranslated_packages": [],
                "warnings": []
            }

        # _run_restore_worker çağrısını yakala
        original_run_worker = app._run_restore_worker
        def spy_run_restore_worker(engine, options):
            nonlocal executed_engine
            executed_engine = engine
            engine.execute_restore = mock_execute_restore
            original_run_worker(engine, options)

        app._run_restore_worker = spy_run_restore_worker

        # Geri yüklemeyi başlat
        app._handle_start_restore()
        await pilot.pause(0.1)

        # 1. Başlatma anında eski motorun cleanup() işlemi çalışan worker'ı çökertmemek için çağrılmamalı
        assert old_cleanup_mock.called is False

        # 2. Yeni güncel bir RestoreEngine örneği oluşturulup çalıştırılmış olmalı
        assert executed_engine is not None
        assert executed_engine is not old_engine
        assert executed_engine.backup_path.resolve() == backup_dir.resolve()


@pytest.mark.asyncio
async def test_cart_population_single_pass_and_no_event_storm():
    """Item 3: Sepet yenileme tek geçişte çalışmalı ve liste doldurma sırasında event storm olmamalı."""
    with tempfile.TemporaryDirectory() as tmp_home:
        home = Path(tmp_home)
        cfg_dir = home / ".config" / "demo"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "demo.conf").write_text("x=1\n")

        app = DistroNomadApp(home_dir=str(home))
        async with app.run_test() as pilot:
            # İlk açılış worker'ının tamamlanmasını bekle
            await app.workers.wait_for_complete()

            # 50 adet paket oluştur
            app.all_packages = [
                PackageItem(name=f"pkg-{i}", manager="pacman", category="Sistem", is_selected=(i % 2 == 0))
                for i in range(50)
            ]
            app.all_configs = [
                ConfigItem(id=f"cfg-{i}", name=f"Config {i}", category="Ayar", source_path=f"/dummy/{i}", rel_path=f".config/cfg{i}", is_selected=(i % 2 == 0), size_bytes=100)
                for i in range(10)
            ]

            # Listeleri doldururken event storm olmamalı ve sepet tek geçişte kurulmalı
            cart_rebuild_count = 0
            original_populate_cart = app._populate_backup_cart_table

            def counted_cart_populate(*args, **kwargs):
                nonlocal cart_rebuild_count
                cart_rebuild_count += 1
                original_populate_cart(*args, **kwargs)

            app._populate_backup_cart_table = counted_cart_populate

            # _apply_scan_results çağrısı
            app._apply_scan_results(app.all_packages, app.all_configs, [])
            await pilot.pause()

            # 50 paket ve 10 ayar için 60 kez değil, tam 1 kez sepet tablosu kurulmalı
            assert cart_rebuild_count == 1

            # Sepet tablosundaki ve özetindeki değerleri doğrula
            cart_table = app.query_one("#table-backup-cart", DataTable)
            assert cart_table.row_count == 25 + 5  # Seçili olanlar (50/2 + 10/2)


@pytest.mark.asyncio
async def test_action_cycle_theme_preserves_footer_and_styles_content():
    """Tema değiştiğinde Footer (tail) yazıları kaybolmamalı ve sayfa içeriği temayı yansıtmalı."""
    with tempfile.TemporaryDirectory() as tmp_home:
        app = DistroNomadApp(home_dir=tmp_home)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            footer = app.query_one("Footer")
            tabs = app.query_one("Tabs")
            tabbed_content = app.query_one("TabbedContent")
            summary_box = app.query_one(".summary-box")

            from distronomad.ui.app import AVAILABLE_THEMES

            # Başlangıç durumu (Cyberpunk)
            assert footer.styles.border.top[0] == ""
            assert footer.styles.border.bottom[0] == ""
            assert footer.region.height == 1
            assert tabbed_content.styles.background.a == 0

            # Tüm temalardan sırayla geç
            for _ in range(len(AVAILABLE_THEMES)):
                app.action_cycle_theme()
                await pilot.pause()

                # Footer asla border: round almamalı (tail yazıları görünür kalmalı)
                assert footer.styles.border.top[0] == ""
                assert footer.styles.border.bottom[0] == ""
                assert footer.region.height == 1
                assert len(footer.children) > 0
                assert footer.children[0].region.y == 29

                # Tabs sadece alt kenarlık almalı, dört tarafı kapatan border almamalı
                assert tabs.styles.border.top[0] == ""
                assert tabs.styles.border.left[0] == ""
                assert tabs.styles.border.right[0] == ""
                assert tabs.styles.border.bottom[0] != ""

                # TabbedContent sayfa zeminini engellememeli (şeffaf olmalı)
                assert tabbed_content.styles.background.a == 0

                # SummaryBox temaya göre stil almalı
                assert summary_box.styles.border.top[0] == "round"

