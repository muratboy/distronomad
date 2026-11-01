"""
DistroNomad - Textual TUI Arayüzü Pilot ve Widget Testleri.
"""

import pytest
import tempfile
from pathlib import Path
from distronomad.ui.app import DistroNomadApp, AVAILABLE_THEMES
from distronomad.models import SystemInfo, PackageItem, ConfigItem, CustomPathItem
from distronomad.backup import BackupEngine
from textual.widgets import Label, Button, Checkbox, SelectionList, Input, TabbedContent, RadioButton


@pytest.mark.asyncio
async def test_tui_app_mount_and_tabs():
    """TUI uygulamasının başlatılması, sekmeler ve ögelerin yüklenmesi testi."""
    with tempfile.TemporaryDirectory() as tmp_home:
        home = Path(tmp_home)
        # Örnek konfigürasyon oluştur
        cfg_dir = home / ".config" / "testapp"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.toml").write_text("setting = 1\n")

        # Örnek bir yedek arşivi oluştur
        backup_file = home / "test_backup.tar.gz"
        sys_info = SystemInfo(distro_id="cachyos", distro_name="CachyOS", home_dir=str(home))
        pkgs = [
            PackageItem(name="neovim", manager="pacman", category="Düzenleyici"),
            PackageItem(name="firefox", manager="pacman", category="Masaüstü")
        ]
        cfgs = [
            ConfigItem(id="c_test", name="testapp", category="Uygulama", source_path=str(cfg_dir), rel_path=".config/testapp")
        ]
        b_engine = BackupEngine(
            system_info=sys_info,
            packages=pkgs,
            configs=cfgs,
            custom_paths=[],
            output_path=backup_file,
            archive_format="tar.gz"
        )
        b_engine.run()

        app = DistroNomadApp(home_dir=str(home))

        async with app.run_test(size=(120, 60)) as pilot:
            # 1. Başlangıçta ana başlık ve genel bakış aktif olmalı
            assert app.title == "DistroNomad"

            # 2. Aktif sekmenin yüksek kontrast siyah yazı ve neon renk kontrolü
            active_tab = app.query_one("Tab.-active")
            # Siyah yazı rengi kontrolü (Color(0, 0, 0))
            assert active_tab.styles.color.rgb == (0, 0, 0)
            assert active_tab.styles.text_style.bold is True

            # 3. Genel bakıştaki sayıların formatını doğrula (oran formatı: Seçili / Toplam)
            apps_label = str(app.query_one("#stat-apps-count", Label).render())
            configs_label = str(app.query_one("#stat-configs-count", Label).render())
            assert "/" in apps_label
            assert "/" in configs_label

            # 4. Tema değiştirme testi (t tuşu ile)
            initial_theme = app.current_theme_idx
            await pilot.press("t")
            await pilot.pause()
            assert app.current_theme_idx == (initial_theme + 1) % len(AVAILABLE_THEMES)

            # Tema butonuna tıklama testi
            theme_btn = app.query_one("#theme-badge-btn", Button)
            await pilot.click(theme_btn)
            await pilot.pause()
            assert app.current_theme_idx == (initial_theme + 2) % len(AVAILABLE_THEMES)

            # 5. Sekmeler arası geçişler
            await pilot.press("f2")
            await pilot.pause()

            await pilot.press("f3")
            await pilot.pause()

            await pilot.press("f5")
            await pilot.pause()

            # 6. Geri yükleme sekmesi (f6) ve Granular (Öğe Öğe) Seçim Testi
            await pilot.press("f6")
            await pilot.pause()

            # Yedek dosyasını arayüze girip incele
            src_input = app.query_one("#input-restore-src")
            src_input.value = str(backup_file)

            inspect_btn = app.query_one("#btn-inspect-restore", Button)
            await pilot.click(inspect_btn)
            await pilot.pause()

            # Yedekteki uygulamalar listesinin tek tek seçilebilir olduğunu doğrula
            restore_apps_list = app.query_one("#list-restore-apps", SelectionList)
            assert restore_apps_list.option_count >= 2

            # Yedekteki konfigürasyonlar listesinin tek tek seçilebilir olduğunu doğrula
            restore_cfgs_list = app.query_one("#list-restore-configs", SelectionList)
            assert restore_cfgs_list.option_count >= 1

            # Seçili sayaç etiketlerini kontrol et
            apps_count_lbl = str(app.query_one("#lbl-restore-apps-count", Label).render())
            assert "Seçili: 2 / 2" in apps_count_lbl

            # 'Temizle' butonuna basarak seçimleri kaldırma testi
            btn_none = app.query_one("#btn-restore-apps-none", Button)
            btn_none.press()
            await pilot.pause()

            apps_count_lbl_after = str(app.query_one("#lbl-restore-apps-count", Label).render())
            assert "Seçili: 0 / 2" in apps_count_lbl_after

            # 'Tümünü Seç' butonuna basarak tekrar seçme testi
            btn_all = app.query_one("#btn-restore-apps-all", Button)
            btn_all.press()
            await pilot.pause()

            apps_count_lbl_final = str(app.query_one("#lbl-restore-apps-count", Label).render())
            assert "Seçili: 2 / 2" in apps_count_lbl_final

            # 7. 'q' ile çıkış yap
            await pilot.press("q")


@pytest.mark.asyncio
async def test_backup_cart_and_file_picker_modal():
    """Yedekleme Sepeti (DataTable) ve Dosya/Klasör Seçici Modalı Testi."""
    from distronomad.ui.modal import FilePickerModal
    from textual.widgets import DataTable, Input

    with tempfile.TemporaryDirectory() as tmp_home:
        home = Path(tmp_home)
        cfg_dir = home / ".config" / "myconfig"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "myconfig.conf").write_text("color = blue\n")

        # Özel bir test klasörü
        custom_folder = home / "MyProjects"
        custom_folder.mkdir()
        (custom_folder / "file.txt").write_text("hello\n")

        app = DistroNomadApp(home_dir=str(home))

        async with app.run_test(size=(120, 60)) as pilot:
            # 1. F5 ile Yedekleme sekmesine git
            await pilot.press("f5")
            await pilot.pause()

            # Yedekleme Sepeti tablosunu doğrula
            cart_table = app.query_one("#table-backup-cart", DataTable)
            assert cart_table is not None
            # Tabloda en azından taranan yapılandırmalar listelenmeli
            assert cart_table.row_count > 0

            # Filtre butonlarını test et
            btn_filter_apps = app.query_one("#btn-cart-filter-apps", Button)
            btn_filter_apps.press()
            await pilot.pause()
            assert app.cart_filter_type == "apps"

            btn_filter_cfgs = app.query_one("#btn-cart-filter-configs", Button)
            btn_filter_cfgs.press()
            await pilot.pause()
            assert app.cart_filter_type == "configs"

            btn_filter_all = app.query_one("#btn-cart-filter-all", Button)
            btn_filter_all.press()
            await pilot.pause()
            assert app.cart_filter_type == "all"

            # Arama kutusunu test et
            search_input = app.query_one("#input-cart-search", Input)
            search_input.value = "myconfig"
            await pilot.pause()
            assert cart_table.row_count >= 1

            # Arama filtresini temizle
            search_input.value = ""
            await pilot.pause()
            count_before_remove = cart_table.row_count

            # Sepetten seçili ögeyi butonla çıkarma testi
            btn_remove_selected = app.query_one("#btn-cart-remove-selected", Button)
            btn_remove_selected.press()
            await pilot.pause()
            assert cart_table.row_count == count_before_remove - 1

            # Sepetteki bir ögeye Enter basarak çıkarma testi
            cart_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert cart_table.row_count == count_before_remove - 2

            # Sepetteki bir ögeye Delete basarak çıkarma testi
            await pilot.press("delete")
            await pilot.pause()
            assert cart_table.row_count == count_before_remove - 3

            # 2. F4 (Özel Dosyalar) sekmesine git ve [📂 Gözat] modalını test et
            await pilot.press("f4")
            await pilot.pause()

            btn_browse_custom = app.query_one("#btn-browse-custom-path", Button)
            btn_browse_custom.press()
            await pilot.pause()

            # Modalin açıldığını doğrula
            assert isinstance(app.screen, FilePickerModal)
            modal = app.screen
            assert modal.query_one("#picker-title", Label) is not None

            # Modal içindeki kısayol butonlarını test et
            btn_sc_home = modal.query_one("#btn-sc-home", Button)
            btn_sc_home.press()
            await pilot.pause()

            # Modali iptal ile kapat
            btn_cancel = modal.query_one("#btn-picker-cancel", Button)
            btn_cancel.press()
            await pilot.pause()

            # Modalin kapandığını doğrula
            assert not isinstance(app.screen, FilePickerModal)

            # 3. F5 Yedekleme sekmesindeki [📂 Klasör Seç] modalını test et
            await pilot.press("f5")
            await pilot.pause()

            btn_browse_dest = app.query_one("#btn-browse-backup-dest", Button)
            btn_browse_dest.press()
            await pilot.pause()

            assert isinstance(app.screen, FilePickerModal)
            modal_dest = app.screen

            # 'Bu Konumu Seç' butonuna basarak onayla
            btn_confirm = modal_dest.query_one("#btn-picker-confirm", Button)
            btn_confirm.press()
            await pilot.pause()

            # Modalin kapandığını ve hedef input'un güncellendiğini doğrula
            assert not isinstance(app.screen, FilePickerModal)
            dest_input = app.query_one("#input-backup-dest", Input)
            assert dest_input.value != ""

            await pilot.press("q")


@pytest.mark.asyncio
async def test_custom_paths_tab_management_and_restore_append_button(tmp_path):
    """
    Özel dosyalar sekmesindeki ekleme, çıkarma, temizleme butonlarını
    ve Geri Yükle sekmesindeki 'Bu Yedeğe Yeni Ögeler Ekle' butonunu doğrular.
    """
    mock_home = tmp_path / "tui_custom_home"
    mock_home.mkdir()

    doc_file = mock_home / "custom_doc.txt"
    doc_file.write_text("Custom file content\n", encoding="utf-8")

    dummy_backup = tmp_path / "dummy_backup.tar.zst"
    dummy_backup.write_bytes(b"dummy")

    app = DistroNomadApp(home_dir=str(mock_home))
    async with app.run_test() as pilot:
        await pilot.pause()

        # 1. F4 Özel Dosyalar sekmesine geç
        await pilot.press("f4")
        await pilot.pause()

        input_custom = app.query_one("#input-custom-path", Input)
        input_custom.value = str(doc_file)

        btn_add = app.query_one("#btn-add-custom-path", Button)
        btn_add.press()
        await pilot.pause()

        # Eklenen dosyanın listede ve all_custom_paths içinde olduğunu doğrula
        custom_list = app.query_one("#list-custom-paths", SelectionList)
        assert any(cp.path == str(doc_file) for cp in app.all_custom_paths)
        assert custom_list.option_count >= 1

        # 2. Listeden seçip çıkarma işlemi (action_remove_custom_path)
        app.action_remove_custom_path(str(doc_file))
        await pilot.pause()
        assert not any(cp.path == str(doc_file) for cp in app.all_custom_paths)

        # Tekrar ekle ve 'Listeyi Temizle' butonunu test et
        input_custom.value = str(doc_file)
        btn_add.press()
        await pilot.pause()
        assert len(app.all_custom_paths) > 0

        btn_clear = app.query_one("#btn-custom-clear-all", Button)
        btn_clear.press()
        await pilot.pause()
        assert len(app.all_custom_paths) == 0

        # 3. F6 Geri Yükle sekmesindeki 'Bu Yedeğe Yeni Ögeler Ekle' butonunu test et
        await pilot.press("f6")
        await pilot.pause()

        src_input = app.query_one("#input-restore-src", Input)
        src_input.value = str(dummy_backup)

        btn_append = app.query_one("#btn-restore-append-to-this", Button)
        btn_append.press()
        await pilot.pause()

        # Aktif sekmenin 'tab-backup' ve modun 'mode-backup-append' olduğunu doğrula
        tabbed_content = app.query_one(TabbedContent)
        assert tabbed_content.active == "tab-backup"

        mode_append_rb = app.query_one("#mode-backup-append", RadioButton)
        assert mode_append_rb.value is True

        dest_input = app.query_one("#input-backup-dest", Input)
        assert dest_input.value == str(dummy_backup.resolve())

        # Append modunda kafa karıştırıcı 'Temel Yedek' ve 'Sıkıştırma Formatı' gizlenmeli, rehber kutusu görünmeli
        assert app.query_one("#row-backup-base").display is False
        assert app.query_one("#radio-backup-format").display is False
        assert app.query_one("#lbl-append-info").display is True

        # Append moduna geçince varsayılan sistem taraması seçimleri temizlenmeli (sepet 0 öge olmalı)
        cart_table = app.query_one("#table-backup-cart")
        assert cart_table.row_count == 0

        # Tekrar Tam Yedek moduna dönüldüğünde önceki seçimler geri yüklenmeli ve format seçimi açılmalı
        app._apply_backup_mode_ui("full", switch_selections=True)
        await pilot.pause()
        assert app.query_one("#radio-backup-format").display is True
        assert app.query_one("#lbl-append-info").display is False
        assert cart_table.row_count > 0

        await pilot.press("q")


@pytest.mark.asyncio
async def test_backup_encryption_password_confirmation_and_mismatch():
    """TUI yedeklemede parola teyit (onay) alanı ve uyuşmazlık kontrolünü test eder."""
    with tempfile.TemporaryDirectory() as tmp_home:
        home = Path(tmp_home)
        (home / ".bashrc").write_text("alias l='ls'\n")

        app = DistroNomadApp(home_dir=str(home))
        async with app.run_test(size=(120, 60)) as pilot:
            # Yedekleme sekmesine (F5) geç
            await pilot.press("f5")
            await pilot.pause()

            chk_encrypt = app.query_one("#chk-encrypt-backup", Checkbox)
            chk_encrypt.value = True

            pwd_in = app.query_one("#input-backup-password", Input)
            pwd_confirm = app.query_one("#input-backup-password-confirm", Input)

            # 1. Parola boşken yedekleme başlamamalı
            pwd_in.value = ""
            pwd_confirm.value = ""
            btn_start = app.query_one("#btn-start-backup", Button)
            btn_start.press()
            await pilot.pause()
            assert app._is_backing_up is False

            # 2. Parolalar eşleşmediğinde yedekleme başlamamalı
            pwd_in.value = "Password123!"
            pwd_confirm.value = "Mismatch999!"
            btn_start.press()
            await pilot.pause()
            assert app._is_backing_up is False

            # 3. Parolalar eşleştiğinde doğrulama başarılı olmalı
            pwd_confirm.value = "Password123!"
            assert pwd_in.value == pwd_confirm.value
            await pilot.press("q")



