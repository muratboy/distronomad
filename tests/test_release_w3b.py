"""
DistroNomad - Pre-release W3b Test Suite: UI & Discovery i18n & Non-Turkish Character Audit.

Test Coverage:
1. TR & EN Key and Placeholder Parity across all TRANSLATIONS.
2. Codebase literal key definitions: 100% of static literal keys passed to t("...")
   in distronomad modules exist in TRANSLATIONS.
3. Category ID decoupling & desktop apps filter parity:
   PackageItem.is_gui_app and UI buttons ("btn-apps-only-gui", "btn-restore-apps-gui")
   select identical items in TR and EN.
4. Live language toggle dynamic update: pressing 'l' updates active tabs, labels,
   SelectionLists, and footer bindings without restarting.
5. Headless TUI English non-Turkish character audit:
   Opens the UI in English mode with mock ASCII data, traverses all 7 tabs and 6 modal
   dialogs, and ensures no Turkish-specific characters (ç, ğ, ı, ö, ş, ü, Ç, Ğ, İ, Ö, Ş, Ü)
   appear anywhere in the widget hierarchy, with intentional exceptions explicitly listed.
"""

from __future__ import annotations
import ast
import json
import string
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    OptionList,
    ProgressBar,
    RadioButton,
    RadioSet,
    RichLog,
    SelectionList,
    Tab,
    TabbedContent,
    TabPane,
)

from distronomad.models import (
    ConfigItem,
    CustomPathItem,
    PackageItem,
    SystemInfo,
)
from distronomad.i18n import (
    TRANSLATIONS,
    t,
    get_current_language,
    set_current_language,
)
from distronomad.ui.app import DistroNomadApp
from distronomad.ui.modal import (
    AutomationModal,
    ConfirmExitModal,
    DiffViewerModal,
    Fat32WarningModal,
    FilePickerModal,
    PasswordModal,
    RollbackModal,
)


TURKISH_SPECIFIC_CHARS = set("çğışöüÇĞİŞÖÜıİ")

# Explicit intentional exceptions permitted in English mode:
# 1. References to "Türkçe" in the in-app help guide ("Toggle between Türkçe and English")
# 2. Language switcher button / label displaying current or target language ("🌐 Lang: EN", "Türkçe")
INTENTIONAL_EXCEPTIONS = {
    "Türkçe",
}


# ============================================================================
# 1. TR & EN Key and Placeholder Parity
# ============================================================================
def test_w3b_i18n_keys_and_placeholders_parity():
    """Tüm çeviri anahtarlarının TR ve EN karşılıklarının ve yer tutucularının eksiksiz eşleştiğini doğrular."""
    formatter = string.Formatter()
    assert len(TRANSLATIONS) >= 350, f"Yetersiz anahtar sayısı: {len(TRANSLATIONS)}"

    missing_tr = []
    missing_en = []
    placeholder_mismatches = []

    for key, entry in TRANSLATIONS.items():
        if "tr" not in entry or not entry["tr"]:
            missing_tr.append(key)
        if "en" not in entry or not entry["en"]:
            missing_en.append(key)

        tr_val = entry.get("tr", "")
        en_val = entry.get("en", "")

        tr_fields = {field for _, field, _, _ in formatter.parse(tr_val) if field is not None}
        en_fields = {field for _, field, _, _ in formatter.parse(en_val) if field is not None}

        if tr_fields != en_fields:
            placeholder_mismatches.append((key, tr_fields, en_fields))

    assert not missing_tr, f"TR çevirisi eksik anahtarlar: {missing_tr}"
    assert not missing_en, f"EN çevirisi eksik anahtarlar: {missing_en}"
    assert not placeholder_mismatches, f"Yer tutucu uyuşmazlığı olan anahtarlar: {placeholder_mismatches}"


# ============================================================================
# 2. Codebase Literal Keys Defined in TRANSLATIONS
# ============================================================================
def test_w3b_code_literal_keys_defined():
    """Kodda t('...') fonksiyonuna sabit string argümanla geçilen her anahtarın TRANSLATIONS içinde tanımlı olduğunu doğrular."""
    source_files = [
        Path("distronomad/ui/app.py"),
        Path("distronomad/ui/modal.py"),
        Path("distronomad/discovery.py"),
        Path("distronomad/backup.py"),
        Path("distronomad/restore.py"),
        Path("distronomad/verify.py"),
        Path("distronomad/cli.py"),
        Path("distronomad/models.py"),
    ]

    missing_by_file = {}

    for src_path in source_files:
        if not src_path.exists():
            continue
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        file_missing = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name == "t" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    key = node.args[0].value
                    if key not in TRANSLATIONS:
                        file_missing.add(key)
        if file_missing:
            missing_by_file[str(src_path)] = sorted(list(file_missing))

    assert not missing_by_file, f"Tanımsız i18n anahtarları bulundu: {missing_by_file}"


# ============================================================================
# 3. Category ID Decoupling & Desktop Apps Filter Parity
# ============================================================================
def test_w3b_only_gui_filter_parity_between_languages():
    """'Yalnızca Masaüstü / Only Desktop' filtresinin iki dilde tamamen aynı ögeleri seçtiğini doğrular."""
    packages_data = [
        # GUI apps
        ("firefox", "pacman", "desktop_wm", "Web Browser"),
        ("vlc", "pacman", "desktop_app", "Media Player"),
        ("code", "pacman", "editor_ide", "Code Editor"),
        ("gimp", "flatpak", "desktop_flatpak", "Graphics Editor"),
        # Non-GUI apps
        ("ripgrep", "pacman", "general", "CLI search tool"),
        ("git", "pacman", "general", "Version control"),
        ("black", "pipx", "cli_python", "Python formatter"),
        ("cargo-watch", "cargo", "cli_rust", "Rust watcher"),
        ("serve", "npm", "npm_global", "HTTP server"),
    ]

    # TR modunda test
    set_current_language("tr", persist=False)
    pkgs_tr = [
        PackageItem(
            name=name,
            manager=mgr,
            category_id=cat_id,
            category=t(f"cat_{cat_id}"),
            description=desc,
        )
        for name, mgr, cat_id, desc in packages_data
    ]
    selected_tr = {p.name for p in pkgs_tr if p.is_gui_app}

    # EN modunda test
    set_current_language("en", persist=False)
    pkgs_en = [
        PackageItem(
            name=name,
            manager=mgr,
            category_id=cat_id,
            category=t(f"cat_{cat_id}"),
            description=desc,
        )
        for name, mgr, cat_id, desc in packages_data
    ]
    selected_en = {p.name for p in pkgs_en if p.is_gui_app}

    expected_gui = {"firefox", "vlc", "code", "gimp"}
    assert selected_tr == expected_gui
    assert selected_en == expected_gui
    assert selected_tr == selected_en, "GUI filtresi TR ve EN arasında farklı ögeler seçti!"


# ============================================================================
# 4. Live Language Toggle Dynamic Update in Headless TUI
# ============================================================================
@pytest.mark.asyncio
async def test_w3b_live_language_cycle_in_headless_ui(tmp_path):
    """'l' tuşuna basıldığında açık ekranlar, sekmeler, butonlar ve SelectionList listelerinin anında yeni dile güncellendiğini doğrular."""
    set_current_language("tr", persist=False)
    app = DistroNomadApp(home_dir=str(tmp_path))

    # ASCII mock verileri
    mock_pkgs = [
        PackageItem(name="vim", manager="pacman", category_id="editor_ide", category="Düzenleyici"),
        PackageItem(name="curl", manager="pacman", category_id="general", category="Genel"),
    ]
    mock_cfgs = [
        ConfigItem(id="config_test", name="testapp", category_id="other", category="Diğer", source_path=str(tmp_path), rel_path=".config/test"),
    ]
    mock_paths = [
        CustomPathItem(path=str(tmp_path / "Projects"), label="Projeler (Projects)", label_key="custom_lbl_projects", exists=True),
    ]

    with patch.object(app.scanner, "scan_installed_packages", return_value=mock_pkgs), \
         patch.object(app.scanner, "scan_configs", return_value=mock_cfgs), \
         patch.object(app.scanner, "scan_custom_paths", return_value=mock_paths):

        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()

            # 1. Başlangıçta Türkçe olmalı
            tabs = app.query_one("#main-tabs", TabbedContent)
            tab_apps = tabs.get_tab("tab-apps")
            assert "Uygulamalar" in str(tab_apps.label)

            btn_refresh = app.query_one("#btn-refresh-scan", Button)
            assert "Yenile" in str(btn_refresh.label)

            # 2. 'l' tuşuna basarak İngilizceye geçiş
            await pilot.press("l")
            await pilot.pause()

            assert get_current_language() == "en"
            assert "Applications" in str(tab_apps.label)
            assert "Refresh" in str(btn_refresh.label)

            btn_cart = app.query_one("#btn-cart-filter-all", Button)
            assert "All" in str(btn_cart.label)

            # 3. 'l' tuşuna tekrar basarak Türkçeye geri dönüş
            await pilot.press("l")
            await pilot.pause()

            assert get_current_language() == "tr"
            assert "Uygulamalar" in str(tab_apps.label)
            assert "Yenile" in str(btn_refresh.label)


# ============================================================================
# 5. Headless TUI English Non-Turkish Character Audit
# ============================================================================
def _collect_widget_texts(widget) -> list[str]:
    """Widget hiyerarşisindeki tüm kullanıcıya dönük metinleri toplar."""
    texts = []

    # Widget tipine göre metin çıkarımı
    if isinstance(widget, Label):
        try:
            rendered = str(widget.render())
            if rendered:
                texts.append(rendered)
        except Exception:
            pass
    elif isinstance(widget, Button):
        if widget.label:
            texts.append(str(widget.label))
    elif isinstance(widget, (Checkbox, RadioButton)):
        if widget.label:
            texts.append(str(widget.label))
    elif isinstance(widget, Tab):
        if widget.label:
            texts.append(str(widget.label))
    elif isinstance(widget, Input):
        if widget.placeholder:
            texts.append(str(widget.placeholder))
        if widget.value:
            texts.append(str(widget.value))
    elif isinstance(widget, SelectionList):
        for opt in widget.options:
            if hasattr(opt, "prompt"):
                texts.append(str(opt.prompt))
    elif isinstance(widget, OptionList):
        for i in range(widget.option_count):
            try:
                opt = widget.get_option_at_index(i)
                if hasattr(opt, "prompt"):
                    texts.append(str(opt.prompt))
            except Exception:
                pass
    elif isinstance(widget, Markdown):
        # Markdown ham metni
        doc = getattr(widget, "_markdown", "") or getattr(widget, "source", "")
        if doc:
            texts.append(str(doc))
    elif isinstance(widget, Footer):
        # Footer tuş açıklamaları
        for b in getattr(widget, "_bindings", []):
            if hasattr(b, "description") and b.description:
                texts.append(str(b.description))

    for child in widget.children:
        texts.extend(_collect_widget_texts(child))

    return texts


@pytest.mark.asyncio
async def test_w3b_headless_ui_english_no_turkish_characters(tmp_path):
    """Arayüz İngilizce modda açıldığında tüm sekmelerde ve açılabilen modallarda
    Türkçeye özgü harf bulunmadığını denetler (bilinçli istisnalar hariç)."""
    set_current_language("en", persist=False)
    app = DistroNomadApp(home_dir=str(tmp_path))

    # ASCII-only sahte donanım ve taranan veriler
    app.system_info = SystemInfo(
        distro_id="arch",
        distro_name="Arch Linux",
        kernel_version="6.12.0",
        desktop_environment="GNOME",
        shell="/bin/bash",
        hostname="testnode",
        home_dir=str(tmp_path),
        available_package_managers=["pacman"],
    )

    mock_pkgs = [
        PackageItem(name="mockapp", manager="pacman", category_id="desktop_app", category="Desktop Application"),
        PackageItem(name="testeditor", manager="pacman", category_id="editor_ide", category="Editor / IDE"),
    ]
    mock_cfgs = [
        ConfigItem(id="config_testapp", name="testapp (Application)", category_id="app_settings", category="Application Settings", source_path=str(tmp_path), rel_path=".config/testapp"),
    ]
    mock_paths = [
        CustomPathItem(path=str(tmp_path / "Projects"), label="Projects", label_key="custom_lbl_projects", exists=True),
    ]

    with patch.object(app.scanner, "scan_installed_packages", return_value=mock_pkgs), \
         patch.object(app.scanner, "scan_configs", return_value=mock_cfgs), \
         patch.object(app.scanner, "scan_custom_paths", return_value=mock_paths), \
         patch("distronomad.ui.app.scan_hardware_profile") as mock_hw:

        # ASCII donanım profili
        mock_hw_profile = MagicMock()
        mock_hw_profile.cpu_model = "Intel Core i7"
        mock_hw_profile.cpu_vendor = "Intel"
        mock_hw_profile.gpus = ["Intel UHD Graphics"]
        mock_hw_profile.gpu_vendor = "intel"
        mock_hw_profile.audio_system = "PipeWire"
        mock_hw_profile.recommended_packages = {"arch": ["mesa", "vulkan-intel"]}
        mock_hw.return_value = mock_hw_profile

        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()

            tabs_to_visit = [
                "tab-overview",
                "tab-apps",
                "tab-configs",
                "tab-custom",
                "tab-backup",
                "tab-restore",
                "tab-help",
            ]

            collected_violations = []

            for tab_id in tabs_to_visit:
                app.action_switch_tab(tab_id)
                await pilot.pause()

                texts = _collect_widget_texts(app.screen)
                for t_str in texts:
                    for line in t_str.splitlines():
                        # Bilinçli istisnaları temizle
                        clean_line = line
                        for exc in INTENTIONAL_EXCEPTIONS:
                            clean_line = clean_line.replace(exc, "")
                        # Türkçeye özgü karakter kontrolü
                        found = [ch for ch in clean_line if ch in TURKISH_SPECIFIC_CHARS]
                        if found:
                            collected_violations.append((tab_id, set(found), line.strip()))

            # Modalları tek tek kontrol et
            modals_to_test = [
                ("FilePickerModal", FilePickerModal(title=t("picker_default_title"), start_path=str(tmp_path))),
                ("PasswordModal", PasswordModal(title=t("pwd_modal_title"), prompt="Enter archive password:")),
                ("DiffViewerModal", DiffViewerModal([])),
                ("RollbackModal", RollbackModal({"timestamp": "2026-11-01", "operations": [], "backup_path": "test.tar.zst"})),
                ("AutomationModal", AutomationModal()),
                ("Fat32WarningModal", Fat32WarningModal("FAT32 filesystem does not support files larger than 4GB.")),
                ("ConfirmExitModal", ConfirmExitModal("Operations in progress. Exit?")),
            ]

            for modal_name, modal_inst in modals_to_test:
                app.push_screen(modal_inst)
                await pilot.pause()

                modal_texts = _collect_widget_texts(app.screen)
                for t_str in modal_texts:
                    for line in t_str.splitlines():
                        clean_line = line
                        for exc in INTENTIONAL_EXCEPTIONS:
                            clean_line = clean_line.replace(exc, "")
                        found = [ch for ch in clean_line if ch in TURKISH_SPECIFIC_CHARS]
                        if found:
                            collected_violations.append((modal_name, set(found), line.strip()))

                app.pop_screen()
                await pilot.pause()

            # Doğrulama: İhlal listesi tamamen boş olmalı
            if collected_violations:
                msg = "\n".join(f"[{loc}] Harfler {chars}: '{line}'" for loc, chars, line in collected_violations[:15])
                pytest.fail(f"İngilizce modda Türkçeye özgü harfler bulundu ({len(collected_violations)} adet):\n{msg}")
