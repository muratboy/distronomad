"""
DistroNomad - Çoklu Dil (i18n) ve Dil Değiştirme Testleri.
"""

import os
from pathlib import Path
import pytest
from textual.widgets import Label, Button, TabbedContent

from distronomad.i18n import (
    TRANSLATIONS,
    t,
    get_current_language,
    set_current_language,
    load_saved_settings,
    save_setting,
    HELP_MARKDOWN_TR,
    HELP_MARKDOWN_EN,
)
import distronomad.i18n as i18n_module
from distronomad.ui.app import DistroNomadApp, YeniLinuxApp


def test_i18n_translation_keys_completeness():
    """Her çeviri anahtarının hem Türkçe hem de İngilizce karşılığının bulunduğunu doğrular."""
    assert len(TRANSLATIONS) > 50

    for key, val in TRANSLATIONS.items():
        assert "tr" in val, f"Anahtar eksik 'tr': {key}"
        assert "en" in val, f"Anahtar eksik 'en': {key}"
        assert isinstance(val["tr"], str) and len(val["tr"]) > 0, f"Boş 'tr' metni: {key}"
        assert isinstance(val["en"], str) and len(val["en"]) > 0, f"Boş 'en' metni: {key}"


def test_i18n_t_function():
    """t() fonksiyonunun dil seçimi ve parametre biçimlendirmesini test eder."""
    # TR kontrolü
    set_current_language("tr", persist=False)
    assert get_current_language() == "tr"
    assert t("tab_overview") == "📊 Genel Bakış"
    assert t("btn_browse") == "📂 Gözat"

    # EN kontrolü
    set_current_language("en", persist=False)
    assert get_current_language() == "en"
    assert t("tab_overview") == "📊 Overview"
    assert t("btn_browse") == "📂 Browse"

    # Parametre formatlama
    res_tr = t("cart_selected_label", lang="tr", count=5)
    assert res_tr == "Seçilen: 5 Öge"

    res_en = t("cart_selected_label", lang="en", count=5)
    assert res_en == "Selected: 5 Items"

    # Bilinmeyen anahtar fallback
    assert t("non_existent_key_xyz") == "non_existent_key_xyz"

    # Temizle
    set_current_language("tr", persist=False)


def test_i18n_settings_persistence(tmp_path, monkeypatch):
    """Ayarların JSON dosyasına yazılıp okunmasını izole ortamda test eder."""
    mock_dir = tmp_path / "config" / "distronomad"
    mock_file = mock_dir / "settings.json"

    monkeypatch.setattr(i18n_module, "SETTINGS_DIR", mock_dir)
    monkeypatch.setattr(i18n_module, "SETTINGS_FILE", mock_file)

    # Başlangıçta boş
    assert load_saved_settings() == {}

    # Ayar kaydet
    save_setting("theme", "dracula")
    settings = load_saved_settings()
    assert settings.get("theme") == "dracula"

    # Dil ayarını diske kaydet
    set_current_language("en", persist=True)
    settings_after = load_saved_settings()
    assert settings_after.get("language") == "en"

    # Geri al
    set_current_language("tr", persist=True)
    assert load_saved_settings().get("language") == "tr"


def test_help_markdown_content():
    """Yardım rehberi markdown belgelerinin doluluğunu kontrol eder."""
    assert "# 🐧 DistroNomad" in HELP_MARKDOWN_TR
    assert "Dağıtım Taşıma Rehberi" in HELP_MARKDOWN_TR
    assert "# 🐧 DistroNomad" in HELP_MARKDOWN_EN
    assert "Migration & Configuration Guide" in HELP_MARKDOWN_EN
    assert "Universal Package & App Migration" in HELP_MARKDOWN_EN


@pytest.mark.asyncio
async def test_tui_language_cycle_and_apply(tmp_path):
    """TUI üzerinde klavye kısayolu / buton ile dil geçişini ve dinamik güncellemeyi test eder."""
    # Başlangıçta TR
    set_current_language("tr", persist=False)
    app = DistroNomadApp(home_dir=str(tmp_path), lang="tr")

    async with app.run_test() as pilot:
        await pilot.pause()

        # 1. Başlangıçta TR metinlerini doğrula
        btn_lang = app.query_one("#btn-top-lang", Button)
        assert btn_lang.label == "🌐 Dil: TR"

        card_title = app.query_one("#lbl-card-sys-info", Label)
        assert "SİSTEM BİLGİSİ VE DAĞITIM DETAYLARI" in str(card_title.render())

        tabs = app.query_one("#main-tabs", TabbedContent)
        tab_overview = tabs.get_tab("tab-overview")
        assert tab_overview.label == "📊 Genel Bakış"

        # 2. Dili İngilizceye çevir (cycle_language tetikle)
        app.action_cycle_language()
        await pilot.pause()

        assert get_current_language() == "en"
        assert btn_lang.label == "🌐 Lang: EN"
        assert "SYSTEM INFO & DISTRIBUTION DETAILS" in str(card_title.render())
        assert tab_overview.label == "📊 Overview"

        # 3. Tekrar Türkçe'ye çevir
        app.action_cycle_language()
        await pilot.pause()

        assert get_current_language() == "tr"
        assert btn_lang.label == "🌐 Dil: TR"
        assert "SİSTEM BİLGİSİ VE DAĞITIM DETAYLARI" in str(card_title.render())
        assert tab_overview.label == "📊 Genel Bakış"


@pytest.mark.asyncio
async def test_cart_table_distinct_version_and_size_columns(tmp_path):
    """Sepet tablosunda Sürüm ve Boyut sütunlarının ayrıştırıldığını ve açıkça gösterildiğini doğrular."""
    from textual.widgets import DataTable
    from distronomad.models import PackageItem, ConfigItem

    # YeniLinuxApp geriye dönük takma adının da çalıştığını doğrula
    app = YeniLinuxApp(home_dir=str(tmp_path), lang="tr")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # Örnek paket ve konfigürasyon ekle
        app.all_packages = [
            PackageItem(name="neovim", manager="pacman", category="Editör", version="0.10.1", is_selected=True)
        ]
        app.all_configs = [
            ConfigItem(id="cfg-hypr", name="hyprland", category="Masaüstü", source_path=str(tmp_path), rel_path="hypr", size_bytes=20480, item_count=5, is_selected=True)
        ]
        app._populate_backup_cart_table()
        await pilot.pause()

        table = app.query_one("#table-backup-cart", DataTable)
        col_labels = [c.label.plain for c in table.columns.values()]
        assert "Sürüm / İçerik" in col_labels
        assert "Yedek Boyutu" in col_labels

        # Satır hücrelerini doğrula
        rows_by_key = {k.value: table.get_row(k) for k in table.rows.keys()}
        row_pkg = rows_by_key.get("pkg:pacman:neovim", rows_by_key.get("pkg:neovim"))
        assert "v0.10.1" in row_pkg[4]
        assert "0 B (Depodan)" in row_pkg[5]

        row_cfg = rows_by_key["cfg:cfg-hypr"]
        assert "5 dosya" in row_cfg[4]
        assert "20.0 KB" in row_cfg[5]
