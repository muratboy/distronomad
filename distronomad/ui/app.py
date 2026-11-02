"""
DistroNomad - Modern Terminal Kullanıcı Arayüzü (Textual TUI).
"""

from __future__ import annotations
import dataclasses
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    TabbedContent,
    TabPane,
    Label,
    Button,
    Input,
    SelectionList,
    ProgressBar,
    RichLog,
    RadioSet,
    RadioButton,
    Checkbox,
    Markdown,
    DataTable,
)
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual import work, events
from textual.worker import get_current_worker

from distronomad.models import (
    PackageItem,
    ConfigItem,
    CustomPathItem,
    ProgressUpdate,
    RestoreOptions,
    format_bytes,
)
from distronomad.discovery import SystemScanner, get_dir_size_and_count, KNOWN_CONFIG_CATEGORIES, find_running_browsers
from distronomad.backup import BackupEngine, read_manifest_from_backup, scan_and_compare_entries, check_fat32_risk
from distronomad.restore import RestoreEngine, rollback_restore
from distronomad.crypto import is_encrypted_file
from distronomad.diff_utils import compare_backup_with_host
from distronomad.automation import get_timer_status
from distronomad.hardware import scan_hardware_profile, is_hardware_profile_different
from distronomad.packagemap import detect_distro_family
from distronomad.ui.modal import (
    FilePickerModal,
    PasswordModal,
    DiffViewerModal,
    RollbackModal,
    AutomationModal,
    Fat32WarningModal,
    BrowserRunningModal,
    ConfirmExitModal,
)
from distronomad.i18n import (
    t,
    get_current_language,
    set_current_language,
    HELP_MARKDOWN_TR,
    HELP_MARKDOWN_EN,
)


AVAILABLE_THEMES = [
    ("cyberpunk", "Cyberpunk"),
    ("tokyonight", "Tokyo Night"),
    ("dracula", "Dracula"),
    ("nord", "Nord"),
    ("emerald", "CachyOS Emerald"),
    ("monokai", "Monokai"),
]

BINDING_KEYS_MAP = {
    "q": "binding_quit",
    "ctrl+c": "binding_quit",
    "l": "binding_lang",
    "t": "binding_theme",
    "f1": "binding_tab_overview",
    "f2": "binding_tab_apps",
    "f3": "binding_tab_configs",
    "f4": "binding_tab_custom",
    "f5": "binding_tab_backup",
    "f6": "binding_tab_restore",
    "f7": "binding_tab_help",
}


class DistroNomadApp(App):
    """DistroNomad TUI Uygulaması."""

    CSS_PATH = "styles.tcss"
    TITLE = "DistroNomad"
    SUB_TITLE = "Linux Dağıtım & Ayar Taşıyıcı"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        ("q", "quit", "Çıkış / Quit"),
        ("ctrl+c", "quit", "Çıkış / Quit"),
        ("l", "cycle_language", "Dil / Lang"),
        ("t", "cycle_theme", "Tema / Theme"),
        ("f1", "switch_tab('tab-overview')", "Genel Bakış"),
        ("f2", "switch_tab('tab-apps')", "Uygulamalar"),
        ("f3", "switch_tab('tab-configs')", "Ayarlar"),
        ("f4", "switch_tab('tab-custom')", "Dosyalar"),
        ("f5", "switch_tab('tab-backup')", "Yedekle"),
        ("f6", "switch_tab('tab-restore')", "Geri Yükle"),
        ("f7", "switch_tab('tab-help')", "Rehber"),
    ]

    def __init__(self, home_dir: Optional[str] = None, lang: Optional[str] = None):
        super().__init__()
        if lang:
            set_current_language(lang)
        self.sub_title = t("app_subtitle")
        self.scanner = SystemScanner(home_dir=home_dir)
        self.system_info = self.scanner.scan_system_info()
        self.all_packages: List[PackageItem] = []
        self.all_configs: List[ConfigItem] = []
        self.all_custom_paths: List[CustomPathItem] = []
        self.current_restore_engine: Optional[RestoreEngine] = None
        self.current_theme_idx: int = 0

        # Geri yükleme için yedekten okunan granular listeler
        self.restore_manifest_packages: List[PackageItem] = []
        self.restore_manifest_configs: List[ConfigItem] = []
        self.restore_manifest_custom_paths: List[CustomPathItem] = []

        # Yedekleme sepeti filtre değişkenleri
        self.cart_filter_type: str = "all"
        self.cart_filter_text: str = ""
        self.user_added_paths: set[str] = set()
        self._saved_full_selections: Optional[Dict[str, set[str]]] = None
        self._current_backup_mode: str = "full"
        self._is_populating: bool = False
        self._is_restoring: bool = False
        self._is_backing_up: bool = False
        self._is_verifying: bool = False
        self._is_rolling_back: bool = False

    @property
    def is_operation_in_progress(self) -> bool:
        """Herhangi bir kritik işlem (yedek, restore, verify, rollback) sürüyor mu?"""
        return bool(
            getattr(self, "_is_backing_up", False)
            or getattr(self, "_is_restoring", False)
            or getattr(self, "_is_verifying", False)
            or getattr(self, "_is_rolling_back", False)
        )

    def action_quit(self) -> None:
        """Kritik işlem devam ediyorsa onay penceresi gösterir; aksi halde çıkar."""
        if self.is_operation_in_progress:
            self.push_screen(ConfirmExitModal(), self._on_quit_confirmed)
        else:
            self.exit()

    def _on_quit_confirmed(self, confirmed: Optional[bool]) -> None:
        if confirmed:
            self.exit()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Üst Bilgi Çubuğu
        cur_lang = get_current_language()
        with Horizontal(id="top-bar"):
            yield Label(f"⚡ {t('app_title')}", id="brand-label")
            yield Label(
                f"🐧 {self.system_info.distro_name} ({self.system_info.kernel_version}) | 💻 {self.system_info.hostname}",
                id="system-badge",
            )
            yield Button(t("btn_lang_tr" if cur_lang == "tr" else "btn_lang_en"), id="btn-top-lang")
            yield Button(t("scheduler_btn_off"), id="btn-top-automation")
            yield Button(f"🎨 {t('theme_prefix')}: Cyberpunk", id="theme-badge-btn")

        # Sekmeli Ana İçerik
        with TabbedContent(id="main-tabs"):
            # Sekme 1: Genel Bakış
            with TabPane(t("tab_overview"), id="tab-overview"):
                with VerticalScroll(classes="scroll-panel"):
                    with Vertical(classes="card"):
                        yield Label(t("card_sys_info"), id="lbl-card-sys-info", classes="card-title")
                        yield Label(f"{t('info_distro')} {self.system_info.distro_name} ({self.system_info.distro_id})", id="lbl-info-distro", classes="info-line")
                        yield Label(f"{t('info_kernel')} {self.system_info.kernel_version}", id="lbl-info-kernel", classes="info-line")
                        yield Label(f"{t('info_de')} {self.system_info.desktop_environment}", id="lbl-info-de", classes="info-line")
                        yield Label(f"{t('info_shell')} {self.system_info.shell}", id="lbl-info-shell", classes="info-line")
                        none_text = "None" if cur_lang == "en" else "Yok"
                        yield Label(f"{t('info_pms')} {', '.join(self.system_info.available_package_managers) or none_text}", id="lbl-info-pms", classes="info-line")

                    with Horizontal(classes="summary-row"):
                        with Vertical(classes="summary-box"):
                            yield Label(t("stat_box_apps"), id="lbl-stat-apps-title", classes="summary-box-title")
                            yield Label("0 / 0", id="stat-apps-count", classes="summary-box-number")
                            yield Label(t("stat_box_apps_sub"), id="lbl-stat-apps-sub", classes="summary-box-label")

                        with Vertical(classes="summary-box"):
                            yield Label(t("stat_box_configs"), id="lbl-stat-configs-title", classes="summary-box-title")
                            yield Label("0 / 0", id="stat-configs-count", classes="summary-box-number")
                            yield Label(t("stat_box_configs_sub"), id="lbl-stat-configs-sub", classes="summary-box-label")

                        with Vertical(classes="summary-box"):
                            yield Label(t("stat_box_files"), id="lbl-stat-files-title", classes="summary-box-title")
                            yield Label("0 / 0", id="stat-files-count", classes="summary-box-number")
                            yield Label(t("stat_box_files_sub"), id="lbl-stat-files-sub", classes="summary-box-label")

                        with Vertical(classes="summary-box"):
                            yield Label(t("stat_box_size"), id="lbl-stat-size-title", classes="summary-box-title")
                            yield Label("0 B", id="stat-total-size", classes="summary-box-number")
                            yield Label(t("stat_box_size_sub"), id="lbl-stat-size-sub", classes="summary-box-label")

                    with Vertical(classes="card"):
                        yield Label(t("card_stat_guide"), id="lbl-card-stat-guide", classes="card-title")
                        yield Label(t("info_guide_apps"), id="lbl-info-guide-apps", classes="info-line")
                        yield Label(t("info_guide_configs"), id="lbl-info-guide-configs", classes="info-line")
                        yield Label(t("info_guide_files"), id="lbl-info-guide-files", classes="info-line")
                        yield Label(t("info_guide_size"), id="lbl-info-guide-size", classes="info-line")

                    with Horizontal(classes="card"):
                        yield Button(t("btn_refresh_scan"), id="btn-refresh-scan", classes="btn-primary")
                        yield Button(t("btn_select_all_apps"), id="btn-overview-select-all-apps")
                        yield Button(t("btn_select_all_configs"), id="btn-overview-select-all-configs")
                        yield Button(t("btn_goto_backup"), id="btn-overview-goto-backup", classes="btn-success")

            # Sekme 2: Uygulamalar
            with TabPane(t("tab_apps"), id="tab-apps"):
                with Horizontal(classes="toolbar"):
                    yield Input(placeholder=t("search_apps_ph"), id="input-apps-search", classes="search-input")
                    with Horizontal(classes="btn-group"):
                        yield Button(t("btn_select_all"), id="btn-apps-select-all")
                        yield Button(t("btn_select_none"), id="btn-apps-select-none")
                        yield Button(t("btn_only_gui"), id="btn-apps-only-gui")

                yield SelectionList[str](id="list-apps")

            # Sekme 3: Ayarlar & Dotfiles
            with TabPane(t("tab_configs"), id="tab-configs"):
                with Horizontal(classes="toolbar"):
                    yield Input(placeholder=t("search_configs_ph"), id="input-configs-search", classes="search-input")
                    with Horizontal(classes="btn-group"):
                        yield Button(t("btn_select_all"), id="btn-configs-select-all")
                        yield Button(t("btn_select_none"), id="btn-configs-select-none")
                        yield Button(t("btn_toggle_ssh"), id="btn-configs-toggle-ssh")

                yield SelectionList[str](id="list-configs")

            # Sekme 4: Özel Dosyalar
            with TabPane(t("tab_custom"), id="tab-custom"):
                with Horizontal(classes="toolbar", id="custom-add-toolbar"):
                    yield Input(placeholder=t("input_custom_ph"), id="input-custom-path", classes="search-input")
                    yield Button(t("btn_browse"), id="btn-browse-custom-path", classes="btn-primary")
                    yield Button(t("btn_add"), id="btn-add-custom-path", classes="btn-success")

                with Vertical(classes="toolbar-multiline", id="custom-search-toolbar"):
                    with Horizontal(classes="toolbar-row"):
                        yield Input(placeholder=t("search_custom_ph"), id="input-custom-search", classes="search-input")
                        yield Label(t("custom_summary_label", sel=0, total=0, size="0 B"), id="lbl-custom-summary", classes="counter-badge")
                    with Horizontal(classes="toolbar-row btn-group"):
                        yield Button(t("btn_select_all"), id="btn-custom-select-all")
                        yield Button(t("btn_select_none"), id="btn-custom-select-none")
                        yield Button(t("btn_custom_remove_selected"), id="btn-custom-remove-selected", classes="btn-danger")
                        yield Button(t("btn_custom_clear_all"), id="btn-custom-clear-all")

                yield SelectionList[str](id="list-custom-paths")
                yield Label(t("custom_tip"), id="lbl-custom-tip", classes="info-line")

            # Sekme 5: Yedekle (YEDEKLEME SEPETİ & HEDEF SEÇİMİ)
            with TabPane(t("tab_backup"), id="tab-backup"):
                with VerticalScroll(classes="scroll-panel", id="backup-scroll"):
                    # KART 1: YEDEKLENECEK SEÇİLEN ÖGELER (YEDEKLEME SEPETİ)
                    with Vertical(classes="card", id="card-backup-cart"):
                        yield Label(t("card_cart_title"), id="lbl-card-cart-title", classes="card-title")
                        with Vertical(classes="toolbar-multiline", id="cart-search-toolbar"):
                            with Horizontal(classes="toolbar-row"):
                                yield Input(placeholder=t("search_cart_ph"), id="input-cart-search", classes="search-input")
                                yield Label(t("cart_selected_label", count=0), id="lbl-cart-summary", classes="counter-badge")
                            with Horizontal(classes="toolbar-row btn-group"):
                                yield Button(t("filter_all"), id="btn-cart-filter-all")
                                yield Button(t("filter_apps"), id="btn-cart-filter-apps")
                                yield Button(t("filter_configs"), id="btn-cart-filter-configs")
                                yield Button(t("filter_custom"), id="btn-cart-filter-custom")

                        yield DataTable(id="table-backup-cart", cursor_type="row")
                        with Horizontal(classes="toolbar", id="cart-actions-toolbar"):
                            yield Button(t("btn_cart_remove"), id="btn-cart-remove-selected", classes="btn-danger")
                            yield Button(t("btn_cart_remove_filtered"), id="btn-cart-remove-filtered", classes="btn-danger")
                            yield Button(t("btn_cart_clear_all"), id="btn-cart-clear-all")
                        yield Label(t("cart_tip"), id="lbl-cart-tip", classes="info-line")

                    # KART 2: YEDEKLEME HEDEFİ VE AYARLARI
                    with Vertical(classes="card"):
                        yield Label(t("card_backup_dest_title"), id="lbl-card-backup-dest-title", classes="card-title")

                        yield Label(t("lbl_backup_mode"), id="lbl-backup-mode", classes="field-label")
                        with RadioSet(id="radio-backup-mode"):
                            yield RadioButton(t("mode_full"), value=True, id="mode-backup-full")
                            yield RadioButton(t("mode_append"), id="mode-backup-append")
                            yield RadioButton(t("mode_inc"), id="mode-backup-inc")

                        yield Label(t("lbl_base_backup"), classes="field-label", id="lbl-backup-base")
                        with Horizontal(id="row-backup-base"):
                            yield Input(placeholder=t("input_backup_base_ph"), id="input-backup-base", classes="search-input")
                            yield Button(t("btn_browse_base"), id="btn-browse-backup-base", classes="btn-primary")
                            yield Button(t("btn_preview_changes"), id="btn-preview-changes")

                        yield Label(t("lbl_backup_dest"), id="lbl-backup-dest", classes="field-label")
                        with Horizontal(id="row-backup-dest"):
                            yield Input(id="input-backup-dest", classes="search-input")
                            yield Button(t("btn_browse_dest"), id="btn-browse-backup-dest", classes="btn-primary")
                        yield Label(t("append_info_empty"), id="lbl-append-info", classes="info-line")

                        yield Label(t("lbl_compression"), id="lbl-compression", classes="field-label")
                        with RadioSet(id="radio-backup-format"):
                            yield RadioButton(t("format_zst_lbl"), value=True, id="format-zst")
                            yield RadioButton(t("format_gz_lbl"), id="format-gz")
                            yield RadioButton(t("format_dir_lbl"), id="format-dir")

                        yield Label(t("lbl_advanced_opts"), id="lbl-advanced-opts", classes="field-label")
                        yield Checkbox(t("chk_smart_ignore_lbl"), value=True, id="chk-smart-ignore")
                        yield Checkbox(t("chk_encrypt_lbl"), value=False, id="chk-encrypt-backup")
                        yield Checkbox(t("chk_verify_after_backup_lbl"), value=False, id="chk-verify-after-backup")
                        with Horizontal(id="row-backup-pwd"):
                            yield Input(password=True, placeholder=t("input_pwd_ph"), id="input-backup-password", classes="search-input")
                        with Horizontal(id="row-backup-pwd-confirm"):
                            yield Input(password=True, placeholder=t("input_pwd_confirm_ph"), id="input-backup-password-confirm", classes="search-input")

                        yield Button(t("btn_start_backup"), id="btn-start-backup", classes="btn-action-large")

                    # KART 3: İLERLEME VE GÜNLÜK
                    with Vertical(classes="card", id="backup-progress-card"):
                        yield Label(t("card_backup_log"), id="lbl-card-backup-log", classes="card-title")
                        yield ProgressBar(id="backup-progress", show_eta=True)
                        yield RichLog(id="backup-log", wrap=True, highlight=True, markup=True)

            # Sekme 6: Geri Yükle (SEÇİCİ / GRANULAR GERİ YÜKLEME)
            with TabPane(t("tab_restore"), id="tab-restore"):
                with VerticalScroll(classes="scroll-panel", id="restore-scroll"):
                    # Kart 1: Yedek Seçimi ve İnceleme
                    with Vertical(classes="card"):
                        yield Label(t("card_restore_select_title"), id="lbl-card-restore-select-title", classes="card-title")
                        with Horizontal():
                            yield Input(placeholder=t("input_restore_src_ph"), id="input-restore-src", classes="search-input")
                            yield Button(t("btn_browse"), id="btn-browse-restore-src", classes="btn-primary")
                            yield Button(t("btn_inspect_restore"), id="btn-inspect-restore")
                            yield Button(t("btn_verify_backup"), id="btn-verify-backup")
                            yield Button(t("btn_restore_append_to_this"), id="btn-restore-append-to-this", classes="btn-success")
                            yield Button(t("btn_restore_diff"), id="btn-restore-diff")
                            yield Button(t("btn_restore_rollback"), id="btn-restore-rollback", classes="btn-danger")

                        pms_text = self.system_info.available_package_managers[0] if self.system_info.available_package_managers else t("target_system_unknown")
                        yield Label(f"{t('target_system_lbl')} {self.system_info.distro_name} ({pms_text})", id="restore-target-info", classes="field-label")
                        yield Label(t("restore_no_manifest"), id="restore-manifest-summary", classes="info-line")

                    # Kart 1.5: Donanım ve Sürücü Tavsiyeleri
                    with Vertical(classes="card", id="hw-rec-card"):
                        yield Label(t("card_hw_rec_title"), id="hw-rec-title")
                        yield Label(t("hw_rec_init_text"), id="hw-rec-text")
                        with Horizontal(id="row-restore-drivers"):
                            yield Checkbox(t("chk_install_drivers_lbl"), value=False, id="chk-restore-drivers")
                            yield Label("", id="lbl-restore-drivers-pkgs")

                    # Kart 2: Yedekten Yüklenecek Uygulamaları Tek Tek Seç
                    with Vertical(classes="card", id="card-restore-apps"):
                        yield Label(t("card_restore_apps_title"), id="lbl-card-restore-apps-title", classes="card-title")
                        with Horizontal(classes="toolbar"):
                            yield Input(placeholder=t("search_restore_apps_ph"), id="input-restore-apps-search", classes="search-input")
                            with Horizontal(classes="btn-group"):
                                yield Button(t("btn_select_all"), id="btn-restore-apps-all")
                                yield Button(t("btn_select_none"), id="btn-restore-apps-none")
                                yield Button(t("btn_only_gui"), id="btn-restore-apps-gui")
                            yield Label(t("restore_count_format", sel=0, total=0), id="lbl-restore-apps-count", classes="counter-badge")

                        yield SelectionList[str](id="list-restore-apps", classes="restore-selection-list")

                    # Kart 3: Yedekten Yüklenecek Ayarları Tek Tek Seç
                    with Vertical(classes="card", id="card-restore-configs"):
                        yield Label(t("card_restore_configs_title"), id="lbl-card-restore-configs-title", classes="card-title")
                        with Horizontal(classes="toolbar"):
                            yield Input(placeholder=t("search_restore_configs_ph"), id="input-restore-configs-search", classes="search-input")
                            with Horizontal(classes="btn-group"):
                                yield Button(t("btn_select_all"), id="btn-restore-configs-all")
                                yield Button(t("btn_select_none"), id="btn-restore-configs-none")
                            yield Label(t("restore_count_format", sel=0, total=0), id="lbl-restore-configs-count", classes="counter-badge")

                        yield SelectionList[str](id="list-restore-configs", classes="restore-selection-list")

                    # Kart 4: Yedekten Yüklenecek Özel Dosyaları Tek Tek Seç
                    with Vertical(classes="card", id="card-restore-custom"):
                        yield Label(t("card_restore_custom_title"), id="lbl-card-restore-custom-title", classes="card-title")
                        with Horizontal(classes="toolbar"):
                            with Horizontal(classes="btn-group"):
                                yield Button(t("btn_select_all"), id="btn-restore-custom-all")
                                yield Button(t("btn_select_none"), id="btn-restore-custom-none")
                            yield Label(t("restore_count_format", sel=0, total=0), id="lbl-restore-custom-count", classes="counter-badge")

                        yield SelectionList[str](id="list-restore-custom", classes="restore-selection-list")

                    # Kart 5: Çakışma Güvenliği ve Başlatma
                    with Vertical(classes="card", id="restore-safety-card"):
                        yield Label(t("card_restore_safety_title"), id="lbl-card-restore-safety-title", classes="card-title")
                        yield Label(t("lbl_conflict_strategy"), id="lbl-conflict-strategy", classes="field-label")
                        with RadioSet(id="radio-conflict"):
                            yield RadioButton(t("conflict_backup_lbl"), value=True, id="conflict-backup")
                            yield RadioButton(t("conflict_overwrite_lbl"), id="conflict-overwrite")
                            yield RadioButton(t("conflict_skip_lbl"), id="conflict-skip")

                        yield Checkbox(t("chk_dry_run_lbl"), value=False, id="chk-dry-run")
                        yield Checkbox(t("chk_restore_system_state_lbl"), value=False, id="chk-restore-system-state")
                        yield Checkbox(t("chk_allow_external_paths_lbl"), value=False, id="chk-allow-external-paths", disabled=True)
                        yield Label(t("lbl_restore_ready"), id="lbl-restore-total-summary", classes="info-line")
                        yield Button(t("btn_start_restore"), id="btn-start-restore", classes="btn-action-large")

                    # Kart 6: İlerleme ve Log Konsolu
                    with Vertical(classes="card", id="restore-progress-card"):
                        yield Label(t("card_restore_log"), id="lbl-card-restore-log", classes="card-title")
                        yield ProgressBar(id="restore-progress", show_eta=True)
                        yield RichLog(id="restore-log", wrap=True, highlight=True, markup=True)

            # Sekme 7: Rehber
            with TabPane(t("tab_help"), id="tab-help"):
                with VerticalScroll(classes="scroll-panel"):
                    yield Markdown(HELP_MARKDOWN_EN if cur_lang == "en" else HELP_MARKDOWN_TR, id="help-markdown")

        yield Footer()

    def on_unmount(self) -> None:
        """Uygulama kapanırken geçici açılmış arşivleri ve deşifre edilmiş dosyaları temizler."""
        if getattr(self, "current_restore_engine", None):
            try:
                self.current_restore_engine.cleanup()
            except Exception:
                pass

    def on_mount(self) -> None:
        """Uygulama açıldığında ilk taramayı yap ve listeleri doldur."""
        default_backup_name = f"distronomad_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.zst"
        default_backup_path = str(Path(self.system_info.home_dir) / default_backup_name)
        self.query_one("#input-backup-dest", Input).value = default_backup_path

        # Eğer ev dizininde var olan bir yedek varsa geri yükleme ve artımlı yedek alanına da öner
        existing_backups = list(Path(self.system_info.home_dir).glob("distronomad_backup_*.tar.zst"))
        if existing_backups:
            latest = max(existing_backups, key=lambda p: p.stat().st_mtime)
            self.query_one("#input-restore-src", Input).value = str(latest)
            try:
                self.query_one("#input-backup-base", Input).value = str(latest)
            except Exception:
                pass

        # DataTable sütunlarını başlat
        try:
            table = self.query_one("#table-backup-cart", DataTable)
            table.cursor_type = "row"
            table.add_column(t("col_action"), width=10)
            table.add_column(t("col_type"), width=14)
            table.add_column(t("col_name"), width=28)
            table.add_column(t("col_category"), width=22)
            table.add_column(t("col_version_content"), width=16)
            table.add_column(t("col_backup_size"), width=16)
        except Exception:
            pass

        self._update_hardware_driver_ui()
        self._update_bindings_language()
        self.action_refresh_all()
        self._apply_backup_mode_ui("full", switch_selections=False)
        self._update_automation_badge()

    def _update_bindings_language(self) -> None:
        """Klavye kısayol açıklamalarını ve alt başlığı aktif dile günceller."""
        self.sub_title = t("app_subtitle")
        if hasattr(self, "_bindings") and hasattr(self._bindings, "key_to_bindings"):
            for k, blist in self._bindings.key_to_bindings.items():
                if k in BINDING_KEYS_MAP:
                    new_desc = t(BINDING_KEYS_MAP[k])
                    self._bindings.key_to_bindings[k] = [
                        dataclasses.replace(b, description=new_desc) for b in blist
                    ]
            try:
                self.query_one(Footer).refresh_bindings()
            except Exception:
                pass

    def action_switch_tab(self, tab_id: str) -> None:
        """Klavye kısayolu ile sekmeler arası geçiş."""
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = tab_id
        if tab_id == "tab-backup":
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

    def action_cycle_theme(self) -> None:
        """Canlı tema değiştirici."""
        self.current_theme_idx = (self.current_theme_idx + 1) % len(AVAILABLE_THEMES)
        theme_id, theme_name = AVAILABLE_THEMES[self.current_theme_idx]

        for tid, _ in AVAILABLE_THEMES:
            self.remove_class(f"theme-{tid}")
            self.screen.remove_class(f"theme-{tid}")

        if theme_id != "cyberpunk":
            self.add_class(f"theme-{theme_id}")
            self.screen.add_class(f"theme-{theme_id}")

        btn = self.query_one("#theme-badge-btn", Button)
        btn.label = f"🎨 {t('theme_prefix')}: {theme_name}"
        self.notify(t("theme_switched", name=theme_name))

    def action_cycle_language(self) -> None:
        """Kullanıcının dil tercihini (TR <-> EN) anında değiştirir."""
        current_lang = get_current_language()
        new_lang = "en" if current_lang == "tr" else "tr"
        set_current_language(new_lang, persist=True)
        self._apply_language()
        msg = t("lang_switched_tr" if new_lang == "tr" else "lang_switched_en")
        self.notify(msg)

    def _apply_language(self) -> None:
        """Tüm arayüz metinlerini seçili dile göre dinamik olarak günceller."""
        cur = get_current_language()
        self._update_bindings_language()

        # Top Bar
        try:
            self.query_one("#brand-label", Label).update(f"⚡ {t('app_title')}")
            self.query_one("#btn-top-lang", Button).label = t("btn_lang_tr" if cur == "tr" else "btn_lang_en")
            self.query_one("#theme-badge-btn", Button).label = f"🎨 {t('theme_prefix')}: {AVAILABLE_THEMES[self.current_theme_idx][1]}"
            self._update_automation_badge()
        except Exception:
            pass

        # Sekme başlıkları (TabbedContent tabs)
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            tab_mappings = [
                ("tab-overview", "tab_overview"),
                ("tab-apps", "tab_apps"),
                ("tab-configs", "tab_configs"),
                ("tab-custom", "tab_custom"),
                ("tab-backup", "tab_backup"),
                ("tab-restore", "tab_restore"),
                ("tab-help", "tab_help"),
            ]
            for pane_id, key in tab_mappings:
                tab_widget = tabs.get_tab(pane_id)
                if tab_widget:
                    tab_widget.label = t(key)
        except Exception:
            pass

        def safe_label_update(selector: str, text: str):
            try:
                self.query_one(selector, Label).update(text)
            except Exception:
                pass

        def safe_btn_update(selector: str, text: str):
            try:
                self.query_one(selector, Button).label = text
            except Exception:
                pass

        def safe_input_ph(selector: str, text: str):
            try:
                self.query_one(selector, Input).placeholder = text
            except Exception:
                pass

        def safe_radio_update(selector: str, text: str):
            try:
                self.query_one(selector, RadioButton).label = text
            except Exception:
                pass

        def safe_chk_update(selector: str, text: str):
            try:
                self.query_one(selector, Checkbox).label = text
            except Exception:
                pass

        # Genel Bakış Sekmesi
        safe_label_update("#lbl-card-sys-info", t("card_sys_info"))
        safe_label_update("#lbl-info-distro", f"{t('info_distro')} {self.system_info.distro_name} ({self.system_info.distro_id})")
        safe_label_update("#lbl-info-kernel", f"{t('info_kernel')} {self.system_info.kernel_version}")
        safe_label_update("#lbl-info-de", f"{t('info_de')} {self.system_info.desktop_environment}")
        safe_label_update("#lbl-info-shell", f"{t('info_shell')} {self.system_info.shell}")
        none_text = "None" if cur == "en" else "Yok"
        safe_label_update("#lbl-info-pms", f"{t('info_pms')} {', '.join(self.system_info.available_package_managers) or none_text}")

        safe_label_update("#lbl-stat-apps-title", t("stat_box_apps"))
        safe_label_update("#lbl-stat-apps-sub", t("stat_box_apps_sub"))
        safe_label_update("#lbl-stat-configs-title", t("stat_box_configs"))
        safe_label_update("#lbl-stat-configs-sub", t("stat_box_configs_sub"))
        safe_label_update("#lbl-stat-files-title", t("stat_box_files"))
        safe_label_update("#lbl-stat-files-sub", t("stat_box_files_sub"))
        safe_label_update("#lbl-stat-size-title", t("stat_box_size"))
        safe_label_update("#lbl-stat-size-sub", t("stat_box_size_sub"))

        safe_label_update("#lbl-card-stat-guide", t("card_stat_guide"))
        safe_label_update("#lbl-info-guide-apps", t("info_guide_apps"))
        safe_label_update("#lbl-info-guide-configs", t("info_guide_configs"))
        safe_label_update("#lbl-info-guide-files", t("info_guide_files"))
        safe_label_update("#lbl-info-guide-size", t("info_guide_size"))

        safe_btn_update("#btn-refresh-scan", t("btn_refresh_scan"))
        safe_btn_update("#btn-overview-select-all-apps", t("btn_select_all_apps"))
        safe_btn_update("#btn-overview-select-all-configs", t("btn_select_all_configs"))
        safe_btn_update("#btn-overview-goto-backup", t("btn_goto_backup"))

        # Uygulamalar Sekmesi
        safe_input_ph("#input-apps-search", t("search_apps_ph"))
        safe_btn_update("#btn-apps-select-all", t("btn_select_all"))
        safe_btn_update("#btn-apps-select-none", t("btn_select_none"))
        safe_btn_update("#btn-apps-only-gui", t("btn_only_gui"))

        # Ayarlar Sekmesi
        safe_input_ph("#input-configs-search", t("search_configs_ph"))
        safe_btn_update("#btn-configs-select-all", t("btn_select_all"))
        safe_btn_update("#btn-configs-select-none", t("btn_select_none"))
        safe_btn_update("#btn-configs-toggle-ssh", t("btn_toggle_ssh"))

        # Özel Dosyalar Sekmesi
        safe_label_update("#lbl-card-add-custom", t("card_add_custom"))
        safe_input_ph("#input-custom-path", t("input_custom_ph"))
        safe_btn_update("#btn-browse-custom-path", t("btn_browse"))
        safe_btn_update("#btn-add-custom-path", t("btn_add"))
        safe_label_update("#lbl-card-custom-list", t("card_custom_list"))
        safe_input_ph("#input-custom-search", t("search_custom_ph"))
        safe_btn_update("#btn-custom-select-all", t("btn_select_all"))
        safe_btn_update("#btn-custom-select-none", t("btn_select_none"))
        safe_btn_update("#btn-custom-remove-selected", t("btn_custom_remove_selected"))
        safe_btn_update("#btn-custom-clear-all", t("btn_custom_clear_all"))
        safe_label_update("#lbl-custom-tip", t("custom_tip"))
        self._update_custom_summary_label()

        # Yedekle Sekmesi (Sepet & Hedef)
        safe_label_update("#lbl-card-cart-title", t("card_cart_title"))
        safe_input_ph("#input-cart-search", t("search_cart_ph"))
        safe_btn_update("#btn-cart-filter-all", t("filter_all"))
        safe_btn_update("#btn-cart-filter-apps", t("filter_apps"))
        safe_btn_update("#btn-cart-filter-configs", t("filter_configs"))
        safe_btn_update("#btn-cart-filter-custom", t("filter_custom"))
        safe_btn_update("#btn-cart-remove-selected", t("btn_cart_remove"))
        safe_btn_update("#btn-cart-remove-filtered", t("btn_cart_remove_filtered"))
        safe_btn_update("#btn-cart-clear-all", t("btn_cart_clear_all"))
        safe_label_update("#lbl-cart-tip", t("cart_tip"))

        safe_label_update("#lbl-card-backup-dest-title", t("card_backup_dest_title"))
        safe_label_update("#lbl-backup-mode", t("lbl_backup_mode"))
        safe_radio_update("#mode-backup-full", t("mode_full"))
        safe_radio_update("#mode-backup-inc", t("mode_inc"))
        safe_radio_update("#mode-backup-append", t("mode_append"))
        safe_label_update("#lbl-backup-base", t("lbl_base_backup"))
        safe_input_ph("#input-backup-base", t("input_backup_base_ph"))
        safe_btn_update("#btn-browse-backup-base", t("btn_browse_base"))
        safe_btn_update("#btn-preview-changes", t("btn_preview_changes"))
        safe_label_update("#lbl-backup-dest", t("lbl_backup_dest"))
        safe_btn_update("#btn-browse-backup-dest", t("btn_browse_dest"))
        safe_label_update("#lbl-compression", t("lbl_compression"))
        safe_radio_update("#format-zst", t("format_zst_lbl"))
        safe_radio_update("#format-gz", t("format_gz_lbl"))
        safe_radio_update("#format-dir", t("format_dir_lbl"))
        safe_label_update("#lbl-advanced-opts", t("lbl_advanced_opts"))
        safe_chk_update("#chk-smart-ignore", t("chk_smart_ignore_lbl"))
        safe_chk_update("#chk-encrypt-backup", t("chk_encrypt_lbl"))
        safe_chk_update("#chk-verify-after-backup", t("chk_verify_after_backup_lbl"))
        safe_input_ph("#input-backup-password", t("input_pwd_ph"))
        safe_input_ph("#input-backup-password-confirm", t("input_pwd_confirm_ph"))
        safe_btn_update("#btn-start-backup", t("btn_start_backup"))
        safe_label_update("#lbl-card-backup-log", t("card_backup_log"))

        # Geri Yükle Sekmesi
        safe_label_update("#lbl-card-restore-select-title", t("card_restore_select_title"))
        safe_input_ph("#input-restore-src", t("input_restore_src_ph"))
        safe_btn_update("#btn-browse-restore-src", t("btn_browse"))
        safe_btn_update("#btn-inspect-restore", t("btn_inspect_restore"))
        safe_btn_update("#btn-verify-backup", t("btn_verify_backup"))
        safe_btn_update("#btn-restore-append-to-this", t("btn_restore_append_to_this"))
        safe_btn_update("#btn-restore-diff", t("btn_restore_diff"))
        safe_btn_update("#btn-restore-rollback", t("btn_restore_rollback"))

        safe_label_update("#lbl-card-restore-apps-title", t("card_restore_apps_title"))
        safe_input_ph("#input-restore-apps-search", t("search_restore_apps_ph"))
        safe_btn_update("#btn-restore-apps-all", t("btn_select_all"))
        safe_btn_update("#btn-restore-apps-none", t("btn_select_none"))
        safe_btn_update("#btn-restore-apps-gui", t("btn_only_gui"))

        safe_label_update("#lbl-card-restore-configs-title", t("card_restore_configs_title"))
        safe_input_ph("#input-restore-configs-search", t("search_restore_configs_ph"))
        safe_btn_update("#btn-restore-configs-all", t("btn_select_all"))
        safe_btn_update("#btn-restore-configs-none", t("btn_select_none"))

        safe_label_update("#lbl-card-restore-custom-title", t("card_restore_custom_title"))
        safe_btn_update("#btn-restore-custom-all", t("btn_select_all"))
        safe_btn_update("#btn-restore-custom-none", t("btn_select_none"))

        safe_label_update("#lbl-card-restore-safety-title", t("card_restore_safety_title"))
        safe_label_update("#lbl-conflict-strategy", t("lbl_conflict_strategy"))
        safe_radio_update("#conflict-backup", t("conflict_backup_lbl"))
        safe_radio_update("#conflict-overwrite", t("conflict_overwrite_lbl"))
        safe_radio_update("#conflict-skip", t("conflict_skip_lbl"))
        safe_chk_update("#chk-dry-run", t("chk_dry_run_lbl"))
        safe_chk_update("#chk-restore-system-state", t("chk_restore_system_state_lbl"))
        safe_chk_update("#chk-allow-external-paths", t("chk_allow_external_paths_lbl"))
        safe_label_update("#hw-rec-title", t("card_hw_rec_title"))
        safe_chk_update("#chk-restore-drivers", t("chk_install_drivers_lbl"))
        pms_text = self.system_info.available_package_managers[0] if self.system_info.available_package_managers else t("target_system_unknown")
        safe_label_update("#restore-target-info", f"{t('target_system_lbl')} {self.system_info.distro_name} ({pms_text})")
        manifest = self.current_restore_engine.manifest if self.current_restore_engine else None
        self._update_hardware_driver_ui(manifest=manifest)
        safe_btn_update("#btn-start-restore", t("btn_start_restore"))
        safe_label_update("#lbl-card-restore-log", t("card_restore_log"))

        # Rehber Markdown
        try:
            md = self.query_one("#help-markdown", Markdown)
            md.update(HELP_MARKDOWN_EN if cur == "en" else HELP_MARKDOWN_TR)
        except Exception:
            pass

        # Tablo sütun başlıklarını yeniden ayarla
        try:
            table = self.query_one("#table-backup-cart", DataTable)
            table.clear(columns=True)
            table.add_column(t("col_action"), width=10)
            table.add_column(t("col_type"), width=14)
            table.add_column(t("col_name"), width=28)
            table.add_column(t("col_category"), width=22)
            table.add_column(t("col_version_content"), width=16)
            table.add_column(t("col_backup_size"), width=16)
        except Exception:
            pass

        # Mevcut tespit edilmiş nesnelerin kategori ve etiketlerini yeni dile dönüştür
        for pkg in self.all_packages:
            pkg.category = t(f"cat_{pkg.category_id}")
        for cfg in self.all_configs:
            cfg.category = t(f"cat_{cfg.category_id}")
            base_id = cfg.id.replace("config_", "")
            if base_id in KNOWN_CONFIG_CATEGORIES:
                cfg.name = f"{base_id} ({t(KNOWN_CONFIG_CATEGORIES[base_id][1])})"
        for cp in self.all_custom_paths:
            if cp.label_key:
                cp.label = t(cp.label_key)

        for pkg in self.restore_manifest_packages:
            pkg.category = t(f"cat_{pkg.category_id}")
        for cfg in self.restore_manifest_configs:
            cfg.category = t(f"cat_{cfg.category_id}")
            base_id = cfg.id.replace("config_", "")
            if base_id in KNOWN_CONFIG_CATEGORIES:
                cfg.name = f"{base_id} ({t(KNOWN_CONFIG_CATEGORIES[base_id][1])})"
        for cp in self.restore_manifest_custom_paths:
            if cp.label_key:
                cp.label = t(cp.label_key)

        # Arama kutusu değerlerini koruyarak listeleri güncel dille yeniden render et
        try:
            apps_search = self.query_one("#input-apps-search", Input).value
        except Exception:
            apps_search = ""
        try:
            cfgs_search = self.query_one("#input-configs-search", Input).value
        except Exception:
            cfgs_search = ""
        try:
            restore_apps_search = self.query_one("#input-restore-apps-search", Input).value
        except Exception:
            restore_apps_search = ""
        try:
            restore_cfgs_search = self.query_one("#input-restore-configs-search", Input).value
        except Exception:
            restore_cfgs_search = ""

        self._populate_apps_list(filter_text=apps_search)
        self._populate_configs_list(filter_text=cfgs_search)
        self._populate_custom_paths_list()
        self._populate_restore_apps_list(filter_text=restore_apps_search)
        self._populate_restore_configs_list(filter_text=restore_cfgs_search)
        self._populate_restore_custom_list()

        # Sepet, mod görünümü ve listeleri yenile
        self._apply_backup_mode_ui(self._current_backup_mode, switch_selections=False)
        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
        self._update_restore_summary()
        self._update_summary_stats()
        self._update_custom_summary_label()
        self._update_append_info_banner()

    def _update_append_info_banner(self) -> None:
        """Mevcut yedeğe ekleme modunda seçilen yedeğin içeriğini bilgi satırında gösterir."""
        try:
            lbl = self.query_one("#lbl-append-info", Label)
            dest_val = self.query_one("#input-backup-dest", Input).value.strip()
        except Exception:
            return

        if dest_val:
            p = Path(os.path.expanduser(dest_val))
            if p.exists():
                manifest = read_manifest_from_backup(p)
                if manifest:
                    lbl.update(
                        t(
                            "append_info_loaded",
                            name=p.name,
                            pkgs=len(manifest.packages),
                            cfgs=len(manifest.configs),
                            paths=len(manifest.custom_paths),
                        )
                    )
                    return
                lbl.update(t("append_info_selected", name=p.name))
                return

        lbl.update(t("append_info_empty"))

    def _apply_backup_mode_ui(self, mode: str, switch_selections: bool = True) -> None:
        """Seçilen yedekleme moduna göre (full, append, incremental) ekranı sadeleştirir ve uyarlar."""
        prev_mode = self._current_backup_mode
        self._current_backup_mode = mode

        if switch_selections and mode != prev_mode:
            if mode == "append":
                # Tam yedek seçimlerini sakla ve sepeti sadece kullanıcının sonradan eklediklerine indirge
                if self._saved_full_selections is None:
                    self._saved_full_selections = {
                        "pkgs": {f"{p.manager}:{p.name}" for p in self.all_packages if p.is_selected},
                        "cfgs": {c.id for c in self.all_configs if c.is_selected},
                        "paths": {cp.path for cp in self.all_custom_paths if cp.is_selected},
                    }
                for p in self.all_packages:
                    p.is_selected = False
                for c in self.all_configs:
                    c.is_selected = False
                for cp in self.all_custom_paths:
                    cp.is_selected = cp.path in self.user_added_paths

                self._populate_apps_list()
                self._populate_configs_list()
                self._populate_custom_paths_list()
                self._update_summary_stats()
                self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

            elif prev_mode == "append" and self._saved_full_selections is not None:
                # Tam yedek veya artımlı moda geri dönüldüğünde önceki seçimleri geri yükle
                saved_pkgs = self._saved_full_selections.get("pkgs", set())
                saved_cfgs = self._saved_full_selections.get("cfgs", set())
                saved_paths = self._saved_full_selections.get("paths", set()) | self.user_added_paths
                for p in self.all_packages:
                    p.is_selected = (f"{p.manager}:{p.name}" in saved_pkgs) or (p.name in saved_pkgs)
                for c in self.all_configs:
                    c.is_selected = c.id in saved_cfgs
                for cp in self.all_custom_paths:
                    cp.is_selected = cp.path in saved_paths
                self._saved_full_selections = None

                self._populate_apps_list()
                self._populate_configs_list()
                self._populate_custom_paths_list()
                self._update_summary_stats()
                self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        try:
            lbl_base = self.query_one("#lbl-backup-base", Label)
            row_base = self.query_one("#row-backup-base", Horizontal)
            lbl_dest = self.query_one("#lbl-backup-dest", Label)
            inp_dest = self.query_one("#input-backup-dest", Input)
            btn_dest = self.query_one("#btn-browse-backup-dest", Button)
            lbl_info = self.query_one("#lbl-append-info", Label)
            lbl_comp = self.query_one("#lbl-compression", Label)
            rad_comp = self.query_one("#radio-backup-format", RadioSet)
            lbl_cart = self.query_one("#lbl-card-cart-title", Label)
            btn_start = self.query_one("#btn-start-backup", Button)
        except Exception:
            return

        if mode == "append":
            lbl_base.display = False
            row_base.display = False
            lbl_comp.display = False
            rad_comp.display = False
            lbl_info.display = True

            lbl_cart.update(t("card_cart_title_append"))
            lbl_dest.update(t("lbl_backup_dest_append"))
            btn_dest.label = t("btn_browse_dest_append")
            inp_dest.placeholder = t("input_backup_append_ph")
            btn_start.label = t("btn_start_backup_append")

            cur_dest = inp_dest.value.strip()
            if not cur_dest or not Path(os.path.expanduser(cur_dest)).exists():
                base_val = self.query_one("#input-backup-base", Input).value.strip()
                if base_val and Path(os.path.expanduser(base_val)).exists():
                    inp_dest.value = base_val
                else:
                    home_p = Path(self.system_info.home_dir)
                    existing_backups = (
                        list(home_p.glob("*backup*.tar.zst"))
                        + list(home_p.glob("*backup*.tar.gz"))
                        + list(home_p.glob("*backup*.enc"))
                    )
                    if existing_backups:
                        latest = max(existing_backups, key=lambda p: p.stat().st_mtime)
                        inp_dest.value = str(latest)
                    else:
                        inp_dest.value = ""

            self._update_append_info_banner()

        elif mode == "incremental":
            lbl_base.display = True
            row_base.display = True
            lbl_comp.display = True
            rad_comp.display = True
            lbl_info.display = False

            lbl_cart.update(t("card_cart_title"))
            lbl_dest.update(t("lbl_backup_dest"))
            btn_dest.label = t("btn_browse_dest")
            inp_dest.placeholder = ""
            btn_start.label = t("btn_start_backup")

            cur_dest = inp_dest.value.strip()
            if not cur_dest or Path(os.path.expanduser(cur_dest)).exists():
                default_name = f"distronomad_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.zst"
                inp_dest.value = str(Path(self.system_info.home_dir) / default_name)

        else:  # full
            lbl_base.display = False
            row_base.display = False
            lbl_comp.display = True
            rad_comp.display = True
            lbl_info.display = False

            lbl_cart.update(t("card_cart_title"))
            lbl_dest.update(t("lbl_backup_dest"))
            btn_dest.label = t("btn_browse_dest")
            inp_dest.placeholder = ""
            btn_start.label = t("btn_start_backup")

            cur_dest = inp_dest.value.strip()
            if not cur_dest or Path(os.path.expanduser(cur_dest)).exists():
                default_name = f"distronomad_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.zst"
                inp_dest.value = str(Path(self.system_info.home_dir) / default_name)

    def action_refresh_all(self, background: bool = True) -> None:
        """Sistemi yeniden tarar ve tüm arayüz listelerini günceller."""
        if background:
            self._run_scan_worker()
        else:
            self._do_scan_sync()

    @work(thread=True, exclusive=True, group="scan")
    def _run_scan_worker(self) -> None:
        """Ağır tarama işlerini Textual worker ile arka plana alır."""
        try:
            worker = get_current_worker()
        except Exception:
            worker = None

        packages = self.scanner.scan_installed_packages()
        if worker and worker.is_cancelled:
            return
        configs = self.scanner.scan_configs()
        if worker and worker.is_cancelled:
            return
        custom_paths = self.scanner.scan_custom_paths()
        if worker and worker.is_cancelled:
            return
        self.call_from_thread(self._apply_scan_results, packages, configs, custom_paths)

    def _do_scan_sync(self) -> None:
        """Senkron tarama (testler veya özel durumlar için)."""
        packages = self.scanner.scan_installed_packages()
        configs = self.scanner.scan_configs()
        custom_paths = self.scanner.scan_custom_paths()
        self._apply_scan_results(packages, configs, custom_paths)

    def _apply_scan_results(
        self,
        packages: List[PackageItem],
        configs: List[ConfigItem],
        custom_paths: List[CustomPathItem]
    ) -> None:
        """Tarama sonuçlarını arayüze ve veri modellerine aktarır; seçimleri ve özel yolları korur."""
        is_append = (self._current_backup_mode == "append")
        known_pkgs = {f"{p.manager}:{p.name}": p.is_selected for p in self.all_packages}
        known_pkgs_by_name = {p.name: p.is_selected for p in self.all_packages}
        known_cfgs = {c.id: c.is_selected for c in self.all_configs}
        known_custom = {cp.path: cp.is_selected for cp in self.all_custom_paths}

        # 1. Paket seçimlerini koru
        for p in packages:
            key = f"{p.manager}:{p.name}"
            default_val = False if is_append else p.is_selected
            if is_append:
                p.is_selected = known_pkgs.get(key, known_pkgs_by_name.get(p.name, False))
            else:
                p.is_selected = known_pkgs.get(key, known_pkgs_by_name.get(p.name, default_val))

        # 2. Konfigürasyon seçimlerini koru
        for c in configs:
            if is_append:
                c.is_selected = known_cfgs.get(c.id, False)
            else:
                c.is_selected = known_cfgs.get(c.id, c.is_selected)

        # 3. Kullanıcının eklediği özel yolları koru ve en başa ekle
        user_items = [cp for cp in self.all_custom_paths if cp.path in self.user_added_paths]
        existing_user_paths = {cp.path for cp in user_items}
        for u_path in self.user_added_paths:
            if u_path not in existing_user_paths:
                p_obj = Path(u_path)
                if p_obj.exists():
                    size, count = get_dir_size_and_count(p_obj)
                    user_items.append(CustomPathItem(
                        path=u_path,
                        label=p_obj.name,
                        exists=True,
                        is_dir=p_obj.is_dir(),
                        size_bytes=size,
                        file_count=count,
                        is_selected=True
                    ))

        # Taranan standart yollardan kullanıcı tarafından eklenmemiş olanları al
        scanned_items = [cp for cp in custom_paths if cp.path not in self.user_added_paths]
        merged_custom_paths = user_items + scanned_items

        for cp in merged_custom_paths:
            if is_append:
                cp.is_selected = known_custom.get(cp.path, cp.path in self.user_added_paths)
            else:
                cp.is_selected = known_custom.get(cp.path, cp.is_selected)

        self.all_packages = packages
        self.all_configs = configs
        self.all_custom_paths = merged_custom_paths

        self._is_populating = True
        try:
            self._populate_apps_list()
            self._populate_configs_list()
            self._populate_custom_paths_list()
        finally:
            self._is_populating = False

        self._update_summary_stats()
        self._populate_backup_cart_table()

    def _populate_apps_list(self, filter_text: str = "") -> None:
        try:
            list_widget = self.query_one("#list-apps", SelectionList)
        except Exception:
            return

        with list_widget.prevent(SelectionList.SelectedChanged):
            list_widget.clear_options()

            f_lower = filter_text.lower().strip()
            for idx, pkg in enumerate(self.all_packages):
                if f_lower and (f_lower not in pkg.name.lower() and f_lower not in pkg.category.lower()):
                    continue

                mgr_tag = f"[{pkg.manager.upper()}]"
                label_text = f"{mgr_tag:<10} {pkg.display_name:<30} | {pkg.category}"
                if pkg.version:
                    v_str = f"v{pkg.version}" if not pkg.version.startswith("v") else pkg.version
                    label_text += f" ({t('app_version_label', ver=v_str)})"

                pkg_key = f"{pkg.manager}:{pkg.name}"
                list_widget.add_option((label_text, pkg_key, pkg.is_selected))

    def _populate_configs_list(self, filter_text: str = "") -> None:
        try:
            list_widget = self.query_one("#list-configs", SelectionList)
        except Exception:
            return

        with list_widget.prevent(SelectionList.SelectedChanged):
            list_widget.clear_options()

            f_lower = filter_text.lower().strip()
            for idx, cfg in enumerate(self.all_configs):
                if f_lower and (f_lower not in cfg.name.lower() and f_lower not in cfg.rel_path.lower() and f_lower not in cfg.category.lower()):
                    continue

                sec_tag = "🔒 " if cfg.is_sensitive else ""
                label_text = f"{sec_tag}[{cfg.category}] {cfg.name} ({cfg.formatted_size}, {t('item_count_label', count=cfg.item_count)})"

                list_widget.add_option((label_text, cfg.id, cfg.is_selected))

    def _populate_custom_paths_list(self, filter_text: Optional[str] = None) -> None:
        try:
            list_widget = self.query_one("#list-custom-paths", SelectionList)
        except Exception:
            return

        with list_widget.prevent(SelectionList.SelectedChanged):
            list_widget.clear_options()

            if filter_text is None:
                try:
                    filter_text = self.query_one("#input-custom-search", Input).value
                except Exception:
                    filter_text = ""

            f_lower = filter_text.lower().strip()
            for idx, cp in enumerate(self.all_custom_paths):
                if f_lower and (f_lower not in cp.label.lower() and f_lower not in cp.path.lower()):
                    continue

                icon = "📁" if cp.is_dir else "📄"
                label_text = f"{icon} {cp.label or Path(cp.path).name} — {cp.path} ({cp.formatted_size}, {t('file_count_label', count=cp.file_count)})"
                list_widget.add_option((label_text, cp.path, cp.is_selected))

        self._update_custom_summary_label()

    def _update_custom_summary_label(self) -> None:
        try:
            lbl = self.query_one("#lbl-custom-summary", Label)
        except Exception:
            return
        total = len(self.all_custom_paths)
        sel = sum(1 for cp in self.all_custom_paths if cp.is_selected)
        total_bytes = sum(cp.size_bytes for cp in self.all_custom_paths if cp.is_selected)

        size_str = format_bytes(total_bytes)

        lbl.update(t("custom_summary_label", sel=sel, total=total, size=size_str))

    def _update_summary_stats(self) -> None:
        try:
            apps_lbl = self.query_one("#stat-apps-count", Label)
            cfgs_lbl = self.query_one("#stat-configs-count", Label)
            files_lbl = self.query_one("#stat-files-count", Label)
            size_lbl = self.query_one("#stat-total-size", Label)
        except Exception:
            return

        total_apps = len(self.all_packages)
        selected_apps = 0
        for p in self.all_packages:
            if p.is_selected:
                selected_apps += 1

        total_cfgs = len(self.all_configs)
        selected_cfgs = 0
        total_bytes = 0
        for c in self.all_configs:
            if c.is_selected:
                selected_cfgs += 1
                total_bytes += c.size_bytes

        total_paths = len(self.all_custom_paths)
        selected_paths = 0
        for cp in self.all_custom_paths:
            if cp.is_selected:
                selected_paths += 1
                total_bytes += cp.size_bytes

        size_str = format_bytes(total_bytes)

        apps_lbl.update(f"{selected_apps} / {total_apps}")
        cfgs_lbl.update(f"{selected_cfgs} / {total_cfgs}")
        files_lbl.update(f"{selected_paths} / {total_paths}")
        size_lbl.update(size_str)

    # ==================== YEDEKLEME SEPETİ (DATA TABLE) POPULATOR ====================
    def _populate_backup_cart_table(self, filter_text: str = "", filter_type: str = "all") -> None:
        try:
            table = self.query_one("#table-backup-cart", DataTable)
        except Exception:
            return

        table.clear()
        f_lower = filter_text.lower().strip()
        seen_keys = set()
        sel_apps = 0
        sel_cfgs = 0
        sel_paths = 0
        total_bytes = 0

        # Tek geçişte hem sepet satırlarını hem de istatistikleri topla
        # 1. Uygulamalar
        for p in self.all_packages:
            if p.is_selected:
                sel_apps += 1
                if filter_type in ["all", "apps"]:
                    if not f_lower or (f_lower in p.name.lower() or f_lower in p.category.lower()):
                        row_key = f"pkg:{p.manager}:{p.name}"
                        if row_key not in seen_keys:
                            seen_keys.add(row_key)
                            v_str = f"v{p.version}" if p.version and not p.version.startswith("v") else (p.version or t("version_not_specified"))
                            table.add_row(
                                t("cart_row_remove"),
                                t("cart_row_app"),
                                p.display_name,
                                f"[{p.manager.upper()}] {p.category}",
                                v_str,
                                t("cart_size_pkg"),
                                key=row_key
                            )

        # 2. Konfigürasyonlar
        for c in self.all_configs:
            if c.is_selected:
                sel_cfgs += 1
                total_bytes += c.size_bytes
                if filter_type in ["all", "configs"]:
                    if not f_lower or (f_lower in c.name.lower() or f_lower in c.rel_path.lower() or f_lower in c.category.lower()):
                        row_key = f"cfg:{c.id}"
                        if row_key not in seen_keys:
                            seen_keys.add(row_key)
                            table.add_row(
                                t("cart_row_remove"),
                                t("cart_row_config"),
                                c.name,
                                c.category,
                                t("cart_row_files_count", count=c.item_count),
                                c.formatted_size,
                                key=row_key
                            )

        # 3. Özel Klasörler
        for cp in self.all_custom_paths:
            if cp.is_selected:
                sel_paths += 1
                total_bytes += cp.size_bytes
                if filter_type in ["all", "custom"]:
                    if not f_lower or (f_lower in cp.label.lower() or f_lower in cp.path.lower()):
                        row_key = f"custom:{cp.path}"
                        if row_key not in seen_keys:
                            seen_keys.add(row_key)
                            table.add_row(
                                t("cart_row_remove"),
                                t("cart_row_folder"),
                                cp.label or cp.path,
                                t("cart_folder_category"),
                                t("cart_row_files_count", count=cp.file_count),
                                cp.formatted_size,
                                key=row_key
                            )

        # Sepet özet rozetini tek geçişte hesaplanan değerlerle güncelle (ekstra döngü çalıştırmadan)
        try:
            lbl = self.query_one("#lbl-cart-summary", Label)
            size_str = format_bytes(total_bytes)
            lbl.update(t("cart_summary_status", apps=sel_apps, cfgs=sel_cfgs, paths=sel_paths, size=size_str))
        except Exception:
            pass

    # ==================== YEDEKLEME SEPETİNDEN ÖGE ÇIKARMA YÖNETİMİ ====================
    def _remove_item_by_cart_key(self, key_str: str) -> bool:
        """Sepetten belirli bir anahtara (pkg:, cfg:, custom:) sahip ögeyi çıkarır."""
        if not key_str or ":" not in key_str:
            return False

        item_type, item_id = key_str.split(":", 1)
        removed_name = item_id

        if item_type == "pkg":
            found = False
            for p in self.all_packages:
                p_key = f"{p.manager}:{p.name}"
                if p_key == item_id or p.name == item_id:
                    p.is_selected = False
                    removed_name = p.display_name
                    found = True
                    item_id = p_key
                    break
            if not found:
                return False
            try:
                self.query_one("#list-apps", SelectionList).deselect(item_id)
            except Exception:
                pass

        elif item_type == "cfg":
            found = False
            for c in self.all_configs:
                if c.id == item_id:
                    c.is_selected = False
                    removed_name = c.name
                    found = True
                    break
            if not found:
                return False
            try:
                self.query_one("#list-configs", SelectionList).deselect(item_id)
            except Exception:
                pass

        elif item_type == "custom":
            found = False
            for cp in self.all_custom_paths:
                if cp.path == item_id:
                    cp.is_selected = False
                    removed_name = cp.label or cp.path
                    found = True
                    break
            if not found:
                return False
            try:
                self.query_one("#list-custom-paths", SelectionList).deselect(item_id)
            except Exception:
                pass
        else:
            return False

        self._update_summary_stats()
        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
        self.notify(t("cart_notify_removed", name=removed_name))
        return True

    def action_remove_selected_from_cart(self) -> None:
        """Sepette imlecin üzerinde durduğu seçili satırı sepetten çıkarır."""
        try:
            table = self.query_one("#table-backup-cart", DataTable)
        except Exception:
            return

        if table.row_count == 0:
            self.notify(t("cart_notify_empty"), severity="warning")
            return

        if table.cursor_row is None or table.cursor_row < 0:
            self.notify(t("cart_notify_select_row"), severity="warning")
            return

        try:
            prev_cursor = table.cursor_row
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            key_str = row_key.value if hasattr(row_key, "value") else str(row_key)
            if self._remove_item_by_cart_key(key_str):
                if table.row_count > 0:
                    table.move_cursor(row=min(prev_cursor, table.row_count - 1))
        except Exception:
            self.notify(t("cart_notify_remove_failed"), severity="warning")

    def action_remove_filtered_from_cart(self) -> None:
        """Arama veya kategori filtresine uyan tüm ögeleri sepetten çıkarır."""
        try:
            table = self.query_one("#table-backup-cart", DataTable)
        except Exception:
            return

        if table.row_count == 0:
            self.notify(t("cart_notify_no_filtered"), severity="warning")
            return

        f_lower = self.cart_filter_text.lower().strip()
        f_type = self.cart_filter_type
        removed_count = 0

        # Paketler
        if f_type in ["all", "apps"]:
            for p in self.all_packages:
                if p.is_selected:
                    if not f_lower or (f_lower in p.name.lower() or f_lower in p.category.lower()):
                        p.is_selected = False
                        removed_count += 1
                        try:
                            self.query_one("#list-apps", SelectionList).deselect(p.name)
                        except Exception:
                            pass

        # Ayarlar
        if f_type in ["all", "configs"]:
            for c in self.all_configs:
                if c.is_selected:
                    if not f_lower or (f_lower in c.name.lower() or f_lower in c.rel_path.lower() or f_lower in c.category.lower()):
                        c.is_selected = False
                        removed_count += 1
                        try:
                            self.query_one("#list-configs", SelectionList).deselect(c.id)
                        except Exception:
                            pass

        # Özel Klasörler
        if f_type in ["all", "custom"]:
            for cp in self.all_custom_paths:
                if cp.is_selected:
                    if not f_lower or (f_lower in cp.label.lower() or f_lower in cp.path.lower()):
                        cp.is_selected = False
                        removed_count += 1
                        try:
                            self.query_one("#list-custom-paths", SelectionList).deselect(cp.path)
                        except Exception:
                            pass

        self._update_summary_stats()
        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
        self.notify(t("cart_notify_filtered_removed", count=removed_count))

    def action_clear_all_cart(self) -> None:
        """Sepeti tamamen boşaltır."""
        for p in self.all_packages:
            p.is_selected = False
        try:
            self.query_one("#list-apps", SelectionList).deselect_all()
        except Exception:
            pass

        for c in self.all_configs:
            c.is_selected = False
        try:
            self.query_one("#list-configs", SelectionList).deselect_all()
        except Exception:
            pass

        for cp in self.all_custom_paths:
            cp.is_selected = False
        try:
            self.query_one("#list-custom-paths", SelectionList).deselect_all()
        except Exception:
            pass

        self._update_summary_stats()
        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
        self.notify(t("cart_notify_cleared"))

    def action_remove_custom_path(self, target_path: Optional[str] = None) -> None:
        """Özel dosyalar sekmesinden seçili veya odaklanılmış ögeyi kaldırır."""
        try:
            list_widget = self.query_one("#list-custom-paths", SelectionList)
        except Exception:
            return

        if not self.all_custom_paths:
            self.notify(t("custom_notify_empty"), severity="warning")
            return

        path_to_remove = target_path

        # Eğer doğrudan path verilmediyse:
        if not path_to_remove:
            # 1. Üzerinde durulan (highlighted) öge varsa öncelikle onu kaldır
            if list_widget.highlighted is not None and 0 <= list_widget.highlighted < list_widget.option_count:
                opt = list_widget.get_option_at_index(list_widget.highlighted)
                path_to_remove = str(opt.value)
            # 2. Odaklanmış öge yoksa işaretli olanlardan ilkini kaldır
            elif list_widget.selected:
                path_to_remove = str(list(list_widget.selected)[0])

        if path_to_remove:
            removed_name = ""
            for cp in list(self.all_custom_paths):
                if cp.path == path_to_remove:
                    removed_name = cp.label or Path(cp.path).name
                    self.all_custom_paths.remove(cp)
                    break
            self.user_added_paths.discard(path_to_remove)

            self._populate_custom_paths_list()
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
            self.notify(t("custom_item_removed", name=removed_name or path_to_remove))

    def action_clear_all_custom_paths(self) -> None:
        """Tüm özel dosyalar listesini boşaltır."""
        if not self.all_custom_paths:
            return
        self.all_custom_paths.clear()
        self.user_added_paths.clear()
        self._populate_custom_paths_list()
        self._update_summary_stats()
        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
        self.notify(t("custom_cleared"))

    # ==================== GERİ YÜKLEME LİSTELERİ POPULATOR VE GÜNCELLEMELER ====================
    def _populate_restore_apps_list(self, filter_text: str = "") -> None:
        try:
            list_widget = self.query_one("#list-restore-apps", SelectionList)
        except Exception:
            return

        with list_widget.prevent(SelectionList.SelectedChanged):
            list_widget.clear_options()
            f_lower = filter_text.lower().strip()

            for pkg in self.restore_manifest_packages:
                if f_lower and (f_lower not in pkg.name.lower() and f_lower not in pkg.category.lower()):
                    continue

                mgr_tag = f"[{pkg.manager.upper()}]"
                label_text = f"{mgr_tag:<10} {pkg.display_name:<30} | {pkg.category}"
                if pkg.version:
                    v_str = f"v{pkg.version}" if not pkg.version.startswith("v") else pkg.version
                    label_text += f" ({t('app_version_label', ver=v_str)})"

                pkg_key = f"{pkg.manager}:{pkg.name}"
                list_widget.add_option((label_text, pkg_key, pkg.is_selected))

    def _populate_restore_configs_list(self, filter_text: str = "") -> None:
        try:
            list_widget = self.query_one("#list-restore-configs", SelectionList)
        except Exception:
            return

        with list_widget.prevent(SelectionList.SelectedChanged):
            list_widget.clear_options()
            f_lower = filter_text.lower().strip()

            for cfg in self.restore_manifest_configs:
                if f_lower and (f_lower not in cfg.name.lower() and f_lower not in cfg.rel_path.lower() and f_lower not in cfg.category.lower()):
                    continue

                sec_tag = "🔒 " if cfg.is_sensitive else ""
                label_text = f"{sec_tag}[{cfg.category}] {cfg.name} ({cfg.formatted_size}, {t('item_count_label', count=cfg.item_count)})"

                list_widget.add_option((label_text, cfg.id, cfg.is_selected))

    def _populate_restore_custom_list(self) -> None:
        try:
            list_widget = self.query_one("#list-restore-custom", SelectionList)
        except Exception:
            return

        with list_widget.prevent(SelectionList.SelectedChanged):
            list_widget.clear_options()
            for cp in self.restore_manifest_custom_paths:
                label_text = f"📁 {cp.label or cp.path} ({cp.formatted_size}, {t('file_count_label', count=cp.file_count)})"
                list_widget.add_option((label_text, cp.path, cp.is_selected))

    def _update_restore_summary(self) -> None:
        try:
            apps_count_lbl = self.query_one("#lbl-restore-apps-count", Label)
            cfgs_count_lbl = self.query_one("#lbl-restore-configs-count", Label)
            custom_count_lbl = self.query_one("#lbl-restore-custom-count", Label)
            total_lbl = self.query_one("#lbl-restore-total-summary", Label)
        except Exception:
            return

        total_apps = len(self.restore_manifest_packages)
        sel_apps = sum(1 for p in self.restore_manifest_packages if p.is_selected)

        total_cfgs = len(self.restore_manifest_configs)
        sel_cfgs = sum(1 for c in self.restore_manifest_configs if c.is_selected)

        total_custom = len(self.restore_manifest_custom_paths)
        sel_custom = sum(1 for cp in self.restore_manifest_custom_paths if cp.is_selected)

        apps_count_lbl.update(t("restore_count_format", sel=sel_apps, total=total_apps))
        cfgs_count_lbl.update(t("restore_count_format", sel=sel_cfgs, total=total_cfgs))
        custom_count_lbl.update(t("restore_count_format", sel=sel_custom, total=total_custom))

        total_lbl.update(t("restore_summary_format", apps=sel_apps, cfgs=sel_cfgs, custom=sel_custom))

    # Arama Girişleri
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input-apps-search":
            self._populate_apps_list(filter_text=event.value)
        elif event.input.id == "input-configs-search":
            self._populate_configs_list(filter_text=event.value)
        elif event.input.id == "input-custom-search":
            self._populate_custom_paths_list(filter_text=event.value)
        elif event.input.id == "input-cart-search":
            self.cart_filter_text = event.value
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
        elif event.input.id == "input-restore-apps-search":
            self._populate_restore_apps_list(filter_text=event.value)
        elif event.input.id == "input-restore-configs-search":
            self._populate_restore_configs_list(filter_text=event.value)
        elif event.input.id == "input-backup-dest":
            if self._current_backup_mode == "append":
                self._update_append_info_banner()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Yedekleme modu değiştiğinde ekranı seçilen moda göre sadeleştirir."""
        if event.radio_set.id == "radio-backup-mode" and event.pressed:
            btn_id = event.pressed.id
            if btn_id == "mode-backup-append":
                self._apply_backup_mode_ui("append", switch_selections=True)
            elif btn_id == "mode-backup-inc":
                self._apply_backup_mode_ui("incremental", switch_selections=True)
            else:
                self._apply_backup_mode_ui("full", switch_selections=True)

    # Seçim Listeleri Değişimi
    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        if getattr(self, "_is_populating", False):
            return

        list_id = event.selection_list.id
        visible_values = {opt.value for opt in event.selection_list.options}
        selected_set = set(event.selection_list.selected)

        if list_id == "list-apps":
            pkg_map = {f"{p.manager}:{p.name}": p for p in self.all_packages}
            pkg_map_name = {p.name: p for p in self.all_packages}
            for v in visible_values:
                if v in pkg_map:
                    pkg_map[v].is_selected = v in selected_set
                elif v in pkg_map_name:
                    pkg_map_name[v].is_selected = v in selected_set
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif list_id == "list-configs":
            cfg_map = {c.id: c for c in self.all_configs}
            for v in visible_values:
                if v in cfg_map:
                    cfg_map[v].is_selected = v in selected_set
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif list_id == "list-custom-paths":
            cp_map = {cp.path: cp for cp in self.all_custom_paths}
            for v in visible_values:
                if v in cp_map:
                    cp_map[v].is_selected = v in selected_set
            self._update_summary_stats()
            self._update_custom_summary_label()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        # Geri yükleme listeleri değişimleri
        elif list_id == "list-restore-apps":
            pkg_map = {f"{p.manager}:{p.name}": p for p in self.restore_manifest_packages}
            pkg_map_name = {p.name: p for p in self.restore_manifest_packages}
            for v in visible_values:
                if v in pkg_map:
                    pkg_map[v].is_selected = v in selected_set
                elif v in pkg_map_name:
                    pkg_map_name[v].is_selected = v in selected_set
            self._update_restore_summary()

        elif list_id == "list-restore-configs":
            cfg_map = {c.id: c for c in self.restore_manifest_configs}
            for v in visible_values:
                if v in cfg_map:
                    cfg_map[v].is_selected = v in selected_set
            self._update_restore_summary()

        elif list_id == "list-restore-custom":
            cp_map = {cp.path: cp for cp in self.restore_manifest_custom_paths}
            for v in visible_values:
                if v in cp_map:
                    cp_map[v].is_selected = v in selected_set
            self._update_restore_summary()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Kullanıcı sepetteki bir satıra tıkladığında veya Enter bastığında o ögeyi sepetten çıkarır."""
        if getattr(event.data_table, "id", "") == "table-backup-cart":
            key_str = event.row_key.value if hasattr(event.row_key, "value") else str(event.row_key)
            self._remove_item_by_cart_key(key_str)

    def on_key(self, event: events.Key) -> None:
        """Sepet tablosu veya özel dosyalar listesi odaklıyken Delete / Backspace tuşu ile çıkarma."""
        if self.focused and getattr(self.focused, "id", "") == "table-backup-cart":
            if event.key in ("delete", "backspace", "d", "x"):
                self.action_remove_selected_from_cart()
        elif self.focused and getattr(self.focused, "id", "") == "list-custom-paths":
            if event.key in ("delete", "backspace", "d", "x"):
                self.action_remove_custom_path()

    # Buton Tıklamaları
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "theme-badge-btn":
            self.action_cycle_theme()

        elif btn_id == "btn-top-lang":
            self.action_cycle_language()

        elif btn_id == "btn-refresh-scan":
            self.action_refresh_all()
            self.notify(t("notify_scan_refreshed"))

        # ==================== DOSYA / KLASÖR SEÇİCİ MODAL TETİKLEYİCİLERİ ====================
        elif btn_id == "btn-browse-custom-path":
            def on_custom_picked(p: Optional[Path]):
                if p and p.exists():
                    p_res = str(p.resolve())
                    self.user_added_paths.add(p_res)
                    existing = next((cp for cp in self.all_custom_paths if cp.path == p_res), None)
                    if existing:
                        existing.is_selected = True
                        self.all_custom_paths.remove(existing)
                        self.all_custom_paths.insert(0, existing)
                        try:
                            self.query_one("#input-custom-search", Input).value = ""
                        except Exception:
                            pass
                        self._populate_custom_paths_list(filter_text="")
                        self._update_summary_stats()
                        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
                        self.notify(t("custom_already_added", path=p.name))
                        return

                    size, count = get_dir_size_and_count(p)
                    new_item = CustomPathItem(
                        path=p_res,
                        label=p.name,
                        exists=True,
                        is_dir=p.is_dir(),
                        size_bytes=size,
                        file_count=count,
                        is_selected=True
                    )
                    self.all_custom_paths.insert(0, new_item)
                    try:
                        self.query_one("#input-custom-search", Input).value = ""
                    except Exception:
                        pass
                    self._populate_custom_paths_list(filter_text="")
                    self._update_summary_stats()
                    self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
                    self.notify(t("custom_notify_added", name=p.name))
            self.push_screen(FilePickerModal(title=t("picker_custom_title"), start_path=self.system_info.home_dir), on_custom_picked)

        elif btn_id == "btn-browse-backup-dest":
            is_append_mode = (self._current_backup_mode == "append")
            try:
                if self.query_one("#mode-backup-append", RadioButton).value:
                    is_append_mode = True
            except Exception:
                pass

            if is_append_mode:
                def on_append_dest_picked(p: Optional[Path]):
                    if p and p.exists():
                        self.query_one("#input-backup-dest", Input).value = str(p.resolve())
                        self._update_append_info_banner()
                        self.notify(t("backup_notify_append_selected", name=p.name))
                self.push_screen(
                    FilePickerModal(
                        title=t("picker_append_title"),
                        start_path=self.system_info.home_dir,
                        only_dirs=False,
                    ),
                    on_append_dest_picked,
                )
            else:
                def on_dest_picked(p: Optional[Path]):
                    if p:
                        target_dir = p if p.is_dir() else p.parent
                        default_name = f"distronomad_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.zst"
                        self.query_one("#input-backup-dest", Input).value = str(target_dir / default_name)
                        self.notify(t("backup_notify_dest_selected", name=target_dir.name))
                self.push_screen(FilePickerModal(title=t("picker_dest_dir_title"), start_path=self.system_info.home_dir, only_dirs=True), on_dest_picked)

        elif btn_id == "btn-browse-backup-base":
            def on_base_picked(p: Optional[Path]):
                if p and p.exists():
                    self.query_one("#input-backup-base", Input).value = str(p)
                    self.notify(t("backup_notify_base_selected", name=p.name))
            self.push_screen(FilePickerModal(title=t("picker_base_backup_title"), start_path=self.system_info.home_dir), on_base_picked)

        elif btn_id == "btn-preview-changes":
            self._handle_preview_changes()

        elif btn_id == "btn-browse-restore-src":
            def on_src_picked(p: Optional[Path]):
                if p and p.exists():
                    self.query_one("#input-restore-src", Input).value = str(p)
                    self._handle_inspect_restore()
            self.push_screen(FilePickerModal(title=t("picker_restore_src_title"), start_path=self.system_info.home_dir), on_src_picked)

        # ==================== YEDEKLEME SEPETİ FİLTRE BUTONLARI ====================
        elif btn_id == "btn-cart-filter-all":
            self.cart_filter_type = "all"
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type="all")

        elif btn_id == "btn-cart-filter-apps":
            self.cart_filter_type = "apps"
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type="apps")

        elif btn_id == "btn-cart-filter-configs":
            self.cart_filter_type = "configs"
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type="configs")

        elif btn_id == "btn-cart-filter-custom":
            self.cart_filter_type = "custom"
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type="custom")

        # ==================== YEDEKLEME SEPETİNDEN ÇIKARMA BUTONLARI ====================
        elif btn_id == "btn-cart-remove-selected":
            self.action_remove_selected_from_cart()

        elif btn_id == "btn-cart-remove-filtered":
            self.action_remove_filtered_from_cart()

        elif btn_id == "btn-cart-clear-all":
            self.action_clear_all_cart()

        # ==================== YEDEKLEME LİSTELERİ BUTONLARI ====================
        elif btn_id == "btn-overview-select-all-apps" or btn_id == "btn-apps-select-all":
            try:
                list_w = self.query_one("#list-apps", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for pkg in self.all_packages:
                    if f"{pkg.manager}:{pkg.name}" in vis or pkg.name in vis:
                        pkg.is_selected = True
                list_w.select_all()
            except Exception:
                for pkg in self.all_packages:
                    pkg.is_selected = True
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-apps-select-none":
            try:
                list_w = self.query_one("#list-apps", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for pkg in self.all_packages:
                    if f"{pkg.manager}:{pkg.name}" in vis or pkg.name in vis:
                        pkg.is_selected = False
                list_w.deselect_all()
            except Exception:
                for pkg in self.all_packages:
                    pkg.is_selected = False
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-apps-only-gui":
            for pkg in self.all_packages:
                pkg.is_selected = pkg.is_gui_app
            self._populate_apps_list()
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-overview-select-all-configs" or btn_id == "btn-configs-select-all":
            try:
                list_w = self.query_one("#list-configs", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cfg in self.all_configs:
                    if cfg.id in vis:
                        cfg.is_selected = True
                list_w.select_all()
            except Exception:
                for cfg in self.all_configs:
                    cfg.is_selected = True
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-configs-select-none":
            try:
                list_w = self.query_one("#list-configs", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cfg in self.all_configs:
                    if cfg.id in vis:
                        cfg.is_selected = False
                list_w.deselect_all()
            except Exception:
                for cfg in self.all_configs:
                    cfg.is_selected = False
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-configs-toggle-ssh":
            for cfg in self.all_configs:
                if cfg.is_sensitive:
                    cfg.is_selected = not cfg.is_selected
            self._populate_configs_list()
            self._update_summary_stats()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-overview-goto-backup":
            self.action_switch_tab("tab-backup")

        elif btn_id == "btn-add-custom-path":
            input_w = self.query_one("#input-custom-path", Input)
            val = input_w.value.strip()
            if val:
                p = Path(os.path.expanduser(val))
                if p.exists():
                    p_res = str(p.resolve())
                    self.user_added_paths.add(p_res)
                    existing = next((cp for cp in self.all_custom_paths if cp.path == p_res), None)
                    if existing:
                        existing.is_selected = True
                        self.all_custom_paths.remove(existing)
                        self.all_custom_paths.insert(0, existing)
                        input_w.value = ""
                        try:
                            self.query_one("#input-custom-search", Input).value = ""
                        except Exception:
                            pass
                        self._populate_custom_paths_list(filter_text="")
                        self._update_summary_stats()
                        self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
                        self.notify(t("custom_already_added", path=p.name))
                        return

                    size, count = get_dir_size_and_count(p)
                    new_item = CustomPathItem(
                        path=p_res,
                        label=p.name,
                        exists=True,
                        is_dir=p.is_dir(),
                        size_bytes=size,
                        file_count=count,
                        is_selected=True
                    )
                    self.all_custom_paths.insert(0, new_item)
                    input_w.value = ""
                    try:
                        self.query_one("#input-custom-search", Input).value = ""
                    except Exception:
                        pass
                    self._populate_custom_paths_list(filter_text="")
                    self._update_summary_stats()
                    self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)
                    self.notify(t("custom_notify_added", name=p.name))
                else:
                    self.notify(t("custom_notify_not_found", path=val), severity="error")

        elif btn_id == "btn-custom-select-all":
            try:
                list_w = self.query_one("#list-custom-paths", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cp in self.all_custom_paths:
                    if cp.path in vis:
                        cp.is_selected = True
                list_w.select_all()
            except Exception:
                for cp in self.all_custom_paths:
                    cp.is_selected = True
            self._update_summary_stats()
            self._update_custom_summary_label()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-custom-select-none":
            try:
                list_w = self.query_one("#list-custom-paths", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cp in self.all_custom_paths:
                    if cp.path in vis:
                        cp.is_selected = False
                list_w.deselect_all()
            except Exception:
                for cp in self.all_custom_paths:
                    cp.is_selected = False
            self._update_summary_stats()
            self._update_custom_summary_label()
            self._populate_backup_cart_table(filter_text=self.cart_filter_text, filter_type=self.cart_filter_type)

        elif btn_id == "btn-custom-remove-selected":
            self.action_remove_custom_path()

        elif btn_id == "btn-custom-clear-all":
            self.action_clear_all_custom_paths()

        elif btn_id == "btn-restore-append-to-this":
            src_val = self.query_one("#input-restore-src", Input).value.strip()
            if not src_val:
                self.notify(t("restore_notify_select_backup"), severity="warning")
            else:
                p = Path(os.path.expanduser(src_val))
                if not p.exists():
                    self.notify(t("restore_notify_backup_not_found", path=src_val), severity="error")
                else:
                    self.query_one("#input-backup-dest", Input).value = str(p.resolve())
                    self.query_one("#input-backup-base", Input).value = str(p.resolve())
                    try:
                        self.query_one("#mode-backup-append", RadioButton).value = True
                    except Exception:
                        pass
                    self._apply_backup_mode_ui("append", switch_selections=True)
                    self.action_switch_tab("tab-backup")
                    self.notify(t("backup_append_prompt"))

        # ==================== GERİ YÜKLEME LİSTESİ SEÇİM BUTONLARI ====================
        elif btn_id == "btn-restore-apps-all":
            try:
                list_w = self.query_one("#list-restore-apps", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for pkg in self.restore_manifest_packages:
                    if f"{pkg.manager}:{pkg.name}" in vis or pkg.name in vis:
                        pkg.is_selected = True
                list_w.select_all()
            except Exception:
                for pkg in self.restore_manifest_packages:
                    pkg.is_selected = True
            self._update_restore_summary()

        elif btn_id == "btn-restore-apps-none":
            try:
                list_w = self.query_one("#list-restore-apps", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for pkg in self.restore_manifest_packages:
                    if f"{pkg.manager}:{pkg.name}" in vis or pkg.name in vis:
                        pkg.is_selected = False
                list_w.deselect_all()
            except Exception:
                for pkg in self.restore_manifest_packages:
                    pkg.is_selected = False
            self._update_restore_summary()

        elif btn_id == "btn-restore-apps-gui":
            for pkg in self.restore_manifest_packages:
                pkg.is_selected = pkg.is_gui_app
            self._populate_restore_apps_list()
            self._update_restore_summary()

        elif btn_id == "btn-restore-configs-all":
            try:
                list_w = self.query_one("#list-restore-configs", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cfg in self.restore_manifest_configs:
                    if cfg.id in vis:
                        cfg.is_selected = True
                list_w.select_all()
            except Exception:
                for cfg in self.restore_manifest_configs:
                    cfg.is_selected = True
            self._update_restore_summary()

        elif btn_id == "btn-restore-configs-none":
            try:
                list_w = self.query_one("#list-restore-configs", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cfg in self.restore_manifest_configs:
                    if cfg.id in vis:
                        cfg.is_selected = False
                list_w.deselect_all()
            except Exception:
                for cfg in self.restore_manifest_configs:
                    cfg.is_selected = False
            self._update_restore_summary()

        elif btn_id == "btn-restore-custom-all":
            try:
                list_w = self.query_one("#list-restore-custom", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cp in self.restore_manifest_custom_paths:
                    if cp.path in vis:
                        cp.is_selected = True
                list_w.select_all()
            except Exception:
                for cp in self.restore_manifest_custom_paths:
                    cp.is_selected = True
            self._update_restore_summary()

        elif btn_id == "btn-restore-custom-none":
            try:
                list_w = self.query_one("#list-restore-custom", SelectionList)
                vis = {opt.value for opt in list_w.options}
                for cp in self.restore_manifest_custom_paths:
                    if cp.path in vis:
                        cp.is_selected = False
                list_w.deselect_all()
            except Exception:
                for cp in self.restore_manifest_custom_paths:
                    cp.is_selected = False
            self._update_restore_summary()
            self._update_restore_summary()

        elif btn_id == "btn-start-backup":
            self._handle_start_backup()

        elif btn_id == "btn-inspect-restore":
            self._handle_inspect_restore()

        elif btn_id == "btn-verify-backup":
            self._handle_verify_backup()

        elif btn_id == "btn-restore-diff":
            self._handle_restore_diff()

        elif btn_id == "btn-restore-rollback":
            self._handle_restore_rollback()

        elif btn_id == "btn-top-automation":
            self.push_screen(AutomationModal(), self._on_automation_modal_closed)

        elif btn_id == "btn-start-restore":
            self._handle_start_restore()

    def _update_automation_badge(self) -> None:
        try:
            st = get_timer_status()
            btn = self.query_one("#btn-top-automation", Button)
            btn.label = t("scheduler_btn_on") if st["is_active"] else t("scheduler_btn_off")
        except Exception:
            pass

    def _on_automation_modal_closed(self, result: Optional[str]) -> None:
        if result == "enabled":
            self.notify(t("automation_notify_enabled"), severity="information")
        elif result == "disabled":
            self.notify(t("automation_notify_disabled"), severity="warning")
        elif result == "failed":
            self.notify(t("automation_notify_failed"), severity="error")
        self._update_automation_badge()

    def _handle_preview_changes(self) -> None:
        base_path_str = self.query_one("#input-backup-base", Input).value.strip()
        if not base_path_str:
            self.notify(t("inc_preview_select_base"), severity="warning")
            return
        base_path = Path(os.path.expanduser(base_path_str))
        if not base_path.exists():
            self.notify(t("inc_preview_base_not_found", name=base_path.name), severity="error")
            return

        pwd = self.query_one("#input-backup-password", Input).value.strip() or None
        base_manifest = read_manifest_from_backup(base_path, password=pwd)
        if not base_manifest:
            self.notify(t("inc_preview_manifest_error"), severity="error")
            return

        sel_cfgs = [c for c in self.all_configs if c.is_selected]
        sel_paths = [cp for cp in self.all_custom_paths if cp.is_selected]
        changed, unchanged, bytes_saved, deleted_files = scan_and_compare_entries(sel_cfgs, sel_paths, base_manifest)

        saved_str = format_bytes(bytes_saved)

        log = self.query_one("#backup-log", RichLog)
        log.write(f"[bold cyan]{t('inc_log_title', name=base_path.name)}[/bold cyan]")
        log.write(f"  • {t('inc_log_total', count=len(changed) + len(unchanged))}")
        log.write(f"  • [bold yellow]{t('inc_log_changed', count=len(changed))}[/bold yellow]")
        log.write(t("inc_log_unchanged", count=len(unchanged)))
        if deleted_files:
            log.write(t("inc_log_deleted", count=len(deleted_files)))
        log.write(t("inc_log_saved", saved=saved_str))
        if changed:
            log.write(t("inc_log_some_changed"))
            for item in changed[:5]:
                log.write(f"    - {item['name']} ({item['reason']})")
        self.notify(t("inc_preview_summary", count=len(changed), size=saved_str))

    def _handle_start_backup(self, browsers_confirmed: bool = False) -> None:
        if getattr(self, "_is_backing_up", False):
            self.notify(t("backup_notify_already_running"), severity="warning")
            return

        dest_input = self.query_one("#input-backup-dest", Input).value.strip()
        if not dest_input:
            self.notify(t("backup_notify_enter_dest"), severity="error")
            return

        fmt = "tar.zst"
        if self.query_one("#format-gz", RadioButton).value:
            fmt = "tar.gz"
        elif self.query_one("#format-dir", RadioButton).value:
            fmt = "dir"

        is_inc = self.query_one("#mode-backup-inc", RadioButton).value
        is_append = False
        try:
            is_append = self.query_one("#mode-backup-append", RadioButton).value
        except Exception:
            pass

        base_path = None
        if is_inc:
            base_str = self.query_one("#input-backup-base", Input).value.strip()
            if base_str:
                base_path = Path(os.path.expanduser(base_str))
            else:
                self.notify(t("backup_notify_base_not_specified"), severity="warning")
        elif is_append:
            dest_p = Path(os.path.expanduser(dest_input))
            if not dest_p.exists():
                base_str = self.query_one("#input-backup-base", Input).value.strip()
                if base_str and Path(os.path.expanduser(base_str)).exists():
                    dest_input = base_str
                else:
                    self.notify(t("backup_notify_append_not_found", path=dest_input), severity="error")
                    return
            has_any_selected = (
                any(p.is_selected for p in self.all_packages)
                or any(c.is_selected for c in self.all_configs)
                or any(cp.is_selected for cp in self.all_custom_paths)
            )
            if not has_any_selected:
                self.notify(t("append_no_items_warning"), severity="warning")
                return

        use_smart_ignore = self.query_one("#chk-smart-ignore", Checkbox).value
        is_encrypt = self.query_one("#chk-encrypt-backup", Checkbox).value
        password = self.query_one("#input-backup-password", Input).value.strip() if is_encrypt else None
        confirm_password = self.query_one("#input-backup-password-confirm", Input).value.strip() if is_encrypt else None

        if is_encrypt:
            if not password:
                self.notify(t("err_pwd_empty"), severity="error")
                return
            if password != confirm_password:
                self.notify(t("err_pwd_mismatch"), severity="error")
                return

        output_path = Path(os.path.expanduser(dest_input))
        backup_mode = "full"
        if is_append:
            backup_mode = "append"
            mode_label = t("backup_mode_label_append")
        elif is_inc:
            backup_mode = "incremental"
            mode_label = t("backup_mode_label_incremental")
        else:
            mode_label = t("backup_mode_label_full")

        # Açık tarayıcılar profil dosyalarına yazmaya devam eder; başlamadan önce kapatma fırsatı ver
        if not browsers_confirmed:
            running_browsers = find_running_browsers(
                [c.source_path for c in self.all_configs if c.is_selected]
                + [cp.path for cp in self.all_custom_paths if cp.is_selected],
                home_dir=self.system_info.home_dir
            )
            if running_browsers:
                def on_browsers_confirmed(confirmed: Optional[bool]) -> None:
                    if confirmed:
                        self._handle_start_backup(browsers_confirmed=True)
                    else:
                        self.notify(t("backup_aborted_by_user"), severity="information")

                self.push_screen(BrowserRunningModal(", ".join(running_browsers)), on_browsers_confirmed)
                return

        risk = check_fat32_risk(
            target_path=output_path,
            archive_format=fmt,
            backup_mode=backup_mode,
            configs=self.all_configs,
            custom_paths=self.all_custom_paths,
            base_backup_path=base_path
        )
        if risk.has_risk:
            def on_fat32_confirmed(confirmed: Optional[bool]) -> None:
                if confirmed:
                    self._proceed_with_backup(
                        output_path,
                        fmt,
                        backup_mode=backup_mode,
                        base_backup_path=base_path,
                        use_smart_ignore=use_smart_ignore,
                        password=password,
                        is_encrypt=is_encrypt,
                        mode_label=mode_label,
                        force=True
                    )
                else:
                    self.notify(t("backup_aborted_by_user"), severity="information")

            self.push_screen(Fat32WarningModal(risk.message), on_fat32_confirmed)
            return

        self._proceed_with_backup(
            output_path,
            fmt,
            backup_mode=backup_mode,
            base_backup_path=base_path,
            use_smart_ignore=use_smart_ignore,
            password=password,
            is_encrypt=is_encrypt,
            mode_label=mode_label,
            force=False
        )

    def _proceed_with_backup(
        self,
        output_path: Path,
        fmt: str,
        backup_mode: str = "full",
        base_backup_path: Optional[Path] = None,
        use_smart_ignore: bool = True,
        password: Optional[str] = None,
        is_encrypt: bool = False,
        mode_label: str = "Tam (Full)",
        force: bool = False
    ) -> None:
        self.query_one("#btn-start-backup", Button).disabled = True
        self.query_one("#backup-progress", ProgressBar).update(progress=0, total=100)
        log = self.query_one("#backup-log", RichLog)
        log.clear()

        enc_label = f" [{t('backup_log_encrypted')}]" if is_encrypt else ""
        ign_label = f" [{t('backup_log_smart_filtered')}]" if use_smart_ignore else ""
        log.write(t("backup_log_starting", mode=mode_label, enc=enc_label, ign=ign_label))

        self._is_backing_up = True
        self._run_backup_worker(
            output_path,
            fmt,
            backup_mode=backup_mode,
            base_backup_path=base_backup_path,
            use_smart_ignore=use_smart_ignore,
            password=password,
            force=force
        )

    @work(thread=True, group="backup")
    def _run_backup_worker(
        self,
        output_path: Path,
        archive_format: str,
        backup_mode: str = "full",
        base_backup_path: Optional[Path] = None,
        use_smart_ignore: bool = True,
        password: Optional[str] = None,
        force: bool = False
    ) -> None:
        def callback(update: ProgressUpdate):
            self.call_from_thread(self._backup_progress_callback, update)

        try:
            engine = BackupEngine(
                system_info=self.system_info,
                packages=self.all_packages,
                configs=self.all_configs,
                custom_paths=self.all_custom_paths,
                output_path=output_path,
                archive_format=archive_format,
                backup_mode=backup_mode,
                base_backup_path=base_backup_path,
                use_smart_ignore=use_smart_ignore,
                password=password,
                progress_callback=callback,
                force=force
            )
            final_path = engine.run()
            self.call_from_thread(self._backup_finished, final_path, getattr(engine, "skipped_files", []))
        except Exception as e:
            self.call_from_thread(self._backup_failed, str(e))
        finally:
            self._is_backing_up = False

    def _backup_progress_callback(self, update: ProgressUpdate) -> None:
        log = self.query_one("#backup-log", RichLog)
        progress = self.query_one("#backup-progress", ProgressBar)

        if update.total_count > 0:
            pct = (update.processed_count / update.total_count) * 100
            progress.update(progress=pct, total=100)

        log.write(f"[{update.phase.upper()}] {update.message}")

    def _backup_finished(self, final_path: Path, skipped_files: Optional[List[dict]] = None) -> None:
        self._is_backing_up = False
        self.query_one("#btn-start-backup", Button).disabled = False
        self.query_one("#backup-progress", ProgressBar).update(progress=100, total=100)
        log = self.query_one("#backup-log", RichLog)
        log.write(f"[bold green]✔ {t('backup_log_completed', path=final_path)}[/bold green]")
        log.write(f"[bold cyan]💾 {t('backup_synced_usb_safe')}[/bold cyan]")

        if skipped_files:
            log.write(f"[bold yellow]⚠ {t('backup_skipped_files_header', count=len(skipped_files))}[/bold yellow]")
            for sf in skipped_files:
                p_str = sf.get("path", "")
                r_str = sf.get("reason", "")
                log.write(f"  [yellow]• {p_str}: {r_str}[/yellow]")
            self.notify(
                t("backup_completed_with_skipped", count=len(skipped_files)),
                severity="warning",
                title=f"⚠ {final_path.name}"
            )
        else:
            self.notify(t("backup_synced_usb_safe"), title=f"✔ {final_path.name}")

        try:
            chk_verify = self.query_one("#chk-verify-after-backup", Checkbox)
            if chk_verify and chk_verify.value:
                self._is_verifying = True
                pwd = self.query_one("#input-backup-password", Input).value.strip() or None
                log.write(f"[bold cyan]🔍 {t('verify_started', path=final_path.name)}[/bold cyan]")
                self._run_verify_worker(final_path, pwd, target_log_id="#backup-log", target_progress_id="#backup-progress")
        except Exception:
            pass

    def _backup_failed(self, error_msg: str) -> None:
        self._is_backing_up = False
        self.query_one("#btn-start-backup", Button).disabled = False
        log = self.query_one("#backup-log", RichLog)
        log.write(f"[bold red]✖ {t('backup_log_failed', error=error_msg)}[/bold red]")
        self.notify(t("backup_notify_failed", error=error_msg), severity="error")

    # ==================== GERİ YÜKLEME İŞLEMLERİ ====================
    def _handle_inspect_restore(self) -> None:
        src_input = self.query_one("#input-restore-src", Input).value.strip()
        if not src_input:
            self.notify(t("restore_notify_enter_src"), severity="error")
            return

        p = Path(os.path.expanduser(src_input))
        if not p.exists():
            self.notify(t("restore_notify_src_not_found", path=src_input), severity="error")
            return

        # Şifreli dosya ise parola sor
        if is_encrypted_file(p):
            def on_pwd_entered(pwd: Optional[str]):
                if not pwd:
                    self.notify(t("restore_notify_pwd_empty"), severity="warning")
                    return
                self._inspect_restore_with_password(p, pwd)
            self.push_screen(PasswordModal(title="🔐 " + t("modal_password_title"), prompt=t("restore_pwd_prompt", name=p.name)), on_pwd_entered)
            return

        self._inspect_restore_with_password(p, None)

    def _update_hardware_driver_ui(self, manifest: Optional[Any] = None) -> None:
        """Canlı donanım taramasına ve hedef dağıtıma göre sürücü önerilerini ve onay kutusunu günceller."""
        try:
            hw_text_lbl = self.query_one("#hw-rec-text", Label)
            chk_drivers = self.query_one("#chk-restore-drivers", Checkbox)
            lbl_pkgs = self.query_one("#lbl-restore-drivers-pkgs", Label)
        except Exception:
            return

        live_hw = scan_hardware_profile()
        distro_fam = detect_distro_family(self.system_info.distro_id, self.system_info.distro_like)
        if distro_fam == "unknown" and self.system_info.available_package_managers:
            pm = self.system_info.available_package_managers[0]
            if pm in ["pacman", "yay", "paru"]:
                distro_fam = "arch"
            elif pm in ["apt", "apt-get"]:
                distro_fam = "debian"
            elif pm in ["dnf"]:
                distro_fam = "fedora"
            elif pm in ["zypper"]:
                distro_fam = "suse"

        recs = live_hw.recommended_packages.get(distro_fam, [])
        recs_str = ", ".join(recs) if recs else t("hw_rec_none")

        cpu = live_hw.cpu_model or live_hw.cpu_vendor or t("hw_unknown")
        gpus = ", ".join(live_hw.gpus) or t("hw_unknown")
        audio = live_hw.audio_system

        lines = [
            f"• {t('hw_rec_cpu')}: [bold cyan]{cpu}[/bold cyan]",
            f"• {t('hw_rec_gpu')}: [bold yellow]{gpus}[/bold yellow] ({live_hw.gpu_vendor.upper()})",
            f"• {t('hw_rec_audio')}: [bold magenta]{audio}[/bold magenta]",
            f"• [bold green]{t('hw_rec_target_distro', distro=self.system_info.distro_name, family=distro_fam.upper())}[/bold green] {recs_str}"
        ]

        if manifest and getattr(manifest, "hardware_info", None):
            is_diff, diff_desc = is_hardware_profile_different(live_hw, manifest.hardware_info)
            if is_diff:
                lines.append(f"• [bold yellow]ℹ {t('hw_manifest_diff_notice')}:[/bold yellow] {diff_desc} ({t('hw_from_backup')})")

        hw_text_lbl.update("\n".join(lines))

        # Checkbox ve etiket durumunu ayarla
        chk_drivers.label = t("chk_install_drivers_lbl")
        if recs:
            chk_drivers.disabled = False
            lbl_pkgs.update(f"({', '.join(recs)})")
        else:
            chk_drivers.disabled = True
            chk_drivers.value = False
            lbl_pkgs.update(f"({t('hw_rec_none')})")

    def _inspect_restore_with_password(self, p: Path, password: Optional[str]) -> None:
        if getattr(self, "_is_restoring", False):
            self.notify(t("restore_notify_already_restoring"), severity="warning")
            return

        log = self.query_one("#restore-log", RichLog)
        log.clear()
        log.write(f"[cyan]{t('restore_log_inspecting', name=p.name)}[/cyan]")

        if getattr(self, "current_restore_engine", None):
            try:
                self.current_restore_engine.cleanup()
            except Exception:
                pass

        try:
            self.current_restore_engine = RestoreEngine(backup_path=p, password=password)
            manifest = self.current_restore_engine.load_manifest(extract_files=False)

            # Granular listeleri sakla (varsayılan hepsi seçili)
            self.restore_manifest_packages = manifest.packages
            for pkg in self.restore_manifest_packages:
                pkg.is_selected = True

            self.restore_manifest_configs = manifest.configs
            for cfg in self.restore_manifest_configs:
                cfg.is_selected = True

            self.restore_manifest_custom_paths = manifest.custom_paths
            for cp in self.restore_manifest_custom_paths:
                cp.is_selected = True

            # Arayüz listelerini doldur
            self._populate_restore_apps_list()
            self._populate_restore_configs_list()
            self._populate_restore_custom_list()
            self._update_restore_summary()

            summary_text = t(
                "restore_summary_card",
                distro=manifest.system_info.distro_name,
                date=manifest.created_at[:10],
                pkgs=len(manifest.packages),
                cfgs=len(manifest.configs),
                paths=len(manifest.custom_paths),
            )
            self.query_one("#restore-manifest-summary", Label).update(summary_text)

            # Donanım Profilini ve Sürücü Önerilerini Güncelle (Canlı Makine Taraması)
            self._update_hardware_driver_ui(manifest=manifest)

            # Ev dışı yol kontrolü (custom/root)
            has_ext = False
            source_home = manifest.system_info.home_dir if manifest.system_info else None
            for cp in manifest.custom_paths:
                p_obj = Path(cp.path)
                if source_home:
                    try:
                        p_obj.relative_to(source_home)
                    except ValueError:
                        has_ext = True
                        break
                else:
                    has_ext = True
                    break

            try:
                chk_ext = self.query_one("#chk-allow-external-paths", Checkbox)
                chk_ext.disabled = not has_ext
                chk_ext.value = False
            except Exception:
                pass

            log.write(f"[green]✔ {t('restore_log_manifest_success')}[/green]")
            log.write(f"  • {t('restore_log_source_distro', distro=manifest.system_info.distro_name)}")
            log.write(f"  • {t('restore_log_saved_apps', count=len(manifest.packages))}")
            log.write(f"  • {t('restore_log_saved_configs', count=len(manifest.configs))}")
            log.write(f"  • {t('restore_log_saved_paths', count=len(manifest.custom_paths))}")
            if manifest.system_state_info:
                ss = manifest.system_state_info
                grps = ", ".join(ss.get("user_groups", [])) or "-"
                etc_refs = ", ".join(ss.get("etc_reference_files", [])) or "-"
                log.write(
                    f"  • {t('restore_log_system_state', dconf='✔' if ss.get('has_dconf') else '✖', crontab='✔' if ss.get('has_crontab') else '✖', groups=grps, etc=etc_refs)}"
                )
            log.write(f"[bold yellow]{t('restore_log_select_prompt')}[/bold yellow]")

            self.notify(t("restore_notify_inspected", pkgs=len(manifest.packages), cfgs=len(manifest.configs)))
        except Exception as e:
            log.write(f"[bold red]{t('restore_log_inspect_error', error=str(e))}[/bold red]")
            self.notify(t("restore_notify_error", error=str(e)), severity="error")

    def _handle_restore_diff(self) -> None:
        """Yedek ile mevcut sistem arasındaki ayar farklarını diff modalında gösterir."""
        if not self.current_restore_engine or not self.current_restore_engine.manifest:
            self.notify(t("restore_notify_inspect_first"), severity="warning")
            return

        try:
            btn = self.query_one("#btn-restore-diff", Button)
            btn.disabled = True
            btn.label = t("diff_btn_in_progress")
        except Exception:
            pass

        self.notify(t("diff_in_progress_notify"))
        self._run_diff_worker(self.current_restore_engine, Path(self.system_info.home_dir))

    @work(thread=True, exclusive=True, group="diff_calculation")
    def _run_diff_worker(self, engine: RestoreEngine, target_home: Path) -> None:
        try:
            src_dir = engine.ensure_extracted()
            diffs = compare_backup_with_host(src_dir, target_home=target_home)
            self.call_from_thread(self._diff_finished, diffs)
        except Exception as e:
            self.call_from_thread(self._diff_failed, str(e))

    def _diff_finished(self, diffs: list) -> None:
        try:
            btn = self.query_one("#btn-restore-diff", Button)
            btn.disabled = False
            btn.label = t("btn_restore_diff")
        except Exception:
            pass
        self.push_screen(DiffViewerModal(diffs))

    def _diff_failed(self, error_msg: str) -> None:
        try:
            btn = self.query_one("#btn-restore-diff", Button)
            btn.disabled = False
            btn.label = t("btn_restore_diff")
        except Exception:
            pass
        self.notify(f"{t('diff_failed_notify')}: {error_msg}", severity="error")

    def _handle_restore_rollback(self) -> None:
        """Son yapılan geri yükleme işlemini geri alır (Rollback / Undo)."""
        home = Path(self.system_info.home_dir)
        tx_path = home / ".config" / "distronomad" / "transactions" / "last_restore.json"
        if not tx_path.exists():
            self.notify(t("rb_notify_no_log"), severity="warning")
            return

        try:
            with open(tx_path, "r", encoding="utf-8") as f:
                tx_data = json.load(f)
        except Exception as e:
            self.notify(t("rb_notify_read_err", error=e), severity="error")
            return

        # Aynı işlemin ikinci kez geri alınması, arada oluşturulan dosyaları silebilir
        if tx_data.get("rolled_back_at"):
            self.notify(t("restore_err_already_rolled_back", time=tx_data.get("rolled_back_at")), severity="warning")
            return

        def on_rollback_confirmed(confirmed: bool):
            if confirmed:
                log = self.query_one("#restore-log", RichLog)
                log.write(f"[bold yellow]↩️ {t('rb_log_starting')}[/bold yellow]")
                self._is_rolling_back = True
                try:
                    res = rollback_restore(transaction_file=tx_path, target_home=home)
                    log.write(f"[bold green]✔ {t('rb_log_success')}[/bold green]")
                    log.write(f"  • {t('rb_log_reverted', count=len(res['reverted_files']))}")
                    log.write(f"  • {t('rb_log_removed', count=len(res['removed_created']))}")
                    for err in res.get("errors", []):
                        log.write(f"[bold red]{t('rb_log_error', error=err)}[/bold red]")
                    for f_ov in res.get("failed_overwrites", []):
                        log.write(f"[bold yellow]{t('rb_log_failed_overwrite', path=f_ov)}[/bold yellow]")
                    self.notify(t("rb_notify_success", count=len(res['reverted_files'])))
                except Exception as e:
                    log.write(f"[bold red]{t('rb_log_fatal', error=e)}[/bold red]")
                    self.notify(t("rb_notify_fatal", error=e), severity="error")
                finally:
                    self._is_rolling_back = False

        self.push_screen(RollbackModal(tx_data), on_rollback_confirmed)

    def _check_sudo_passwordless(self) -> bool:
        """Sudo'nun parolasız çalışıp çalışamayacağını kontrol eder."""
        try:
            proc = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=3)
            return proc.returncode == 0
        except Exception:
            return False

    def _handle_start_restore(self) -> None:
        if getattr(self, "_is_restoring", False):
            self.notify(t("restore_notify_already_restoring"), severity="warning")
            return

        src_input = self.query_one("#input-restore-src", Input).value.strip()
        if not src_input:
            self.notify(t("restore_notify_inspect_required"), severity="error")
            return

        p = Path(os.path.expanduser(src_input))
        if not p.exists():
            self.notify(t("restore_notify_src_not_found", path=src_input), severity="error")
            return

        if (
            not self.current_restore_engine
            or not self.current_restore_engine.manifest
            or self.current_restore_engine.backup_path.resolve() != p.resolve()
        ):
            self._handle_inspect_restore()
            if not self.current_restore_engine or not self.current_restore_engine.manifest:
                self.notify(t("restore_notify_inspect_required"), severity="error")
                return

        # Sadece KULLANICININ SEÇTİĞİ ögeleri al
        selected_pkgs = [p for p in self.restore_manifest_packages if p.is_selected]
        selected_cfgs = [c for c in self.restore_manifest_configs if c.is_selected]
        selected_custom = [cp for cp in self.restore_manifest_custom_paths if cp.is_selected]

        install_drivers = False
        try:
            chk_d = self.query_one("#chk-restore-drivers", Checkbox)
            install_drivers = bool(chk_d.value and not chk_d.disabled)
        except Exception:
            pass

        if not selected_pkgs and not selected_cfgs and not selected_custom and not install_drivers:
            self.notify(t("restore_notify_no_items_selected"), severity="error")
            return

        # Paket kurulumu için sudo kontrolü ve App.suspend() parola akışı
        if selected_pkgs:
            can_sudo = self._check_sudo_passwordless()
            if not can_sudo:
                sudo_ok = False
                try:
                    with self.suspend():
                        print(f"\n{t('sudo_terminal_prompt')}\n")
                        proc = subprocess.run(["sudo", "-v"])
                        if proc.returncode == 0:
                            sudo_ok = True
                except Exception:
                    sudo_ok = False

                if not sudo_ok:
                    self.notify(t("sudo_failed_skip_pkgs"), severity="warning")
                    try:
                        log_pre = self.query_one("#restore-log", RichLog)
                        log_pre.write(f"[bold yellow]⚠ {t('sudo_failed_skip_pkgs')}[/bold yellow]")
                    except Exception:
                        pass
                    selected_pkgs = []

        conflict = "backup"
        if self.query_one("#conflict-overwrite", RadioButton).value:
            conflict = "overwrite"
        elif self.query_one("#conflict-skip", RadioButton).value:
            conflict = "skip"

        dry_run = self.query_one("#chk-dry-run", Checkbox).value
        restore_sys_state = self.query_one("#chk-restore-system-state", Checkbox).value

        allow_external = False
        try:
            chk_ext = self.query_one("#chk-allow-external-paths", Checkbox)
            allow_external = bool(chk_ext.value and not chk_ext.disabled)
        except Exception:
            pass

        options = RestoreOptions(
            target_home=self.system_info.home_dir,
            selected_packages=selected_pkgs,
            selected_configs=selected_cfgs,
            selected_custom_paths=selected_custom,
            conflict_strategy=conflict,
            install_packages=len(selected_pkgs) > 0,
            restore_system_state=restore_sys_state,
            dry_run=dry_run,
            install_drivers=install_drivers,
            allow_external_paths=allow_external
        )

        old_engine = self.current_restore_engine
        pwd = old_engine.password if old_engine else None
        cached_manifest = old_engine.manifest if old_engine else None

        # Kullanıcının en son seçtiği yedek ve seçeneklerle oluşturulmuş güncel RestoreEngine
        engine = RestoreEngine(backup_path=p, password=pwd)
        engine.manifest = cached_manifest
        self.current_restore_engine = engine

        self._is_restoring = True
        self.query_one("#btn-start-restore", Button).disabled = True
        self.query_one("#restore-progress", ProgressBar).update(progress=0, total=100)
        log = self.query_one("#restore-log", RichLog)
        log.write(f"[bold cyan]{t('restore_log_starting')}[/bold cyan]")
        log.write(t("restore_log_selection_count", pkgs=len(selected_pkgs), cfgs=len(selected_cfgs), custom=len(selected_custom)))
        if dry_run:
            log.write(f"[yellow]{t('restore_log_dry_run_warn')}[/yellow]")

        self._run_restore_worker(engine, options)

    @work(thread=True, group="restore")
    def _run_restore_worker(self, engine: RestoreEngine, options: RestoreOptions) -> None:
        def callback(update: ProgressUpdate):
            self.call_from_thread(self._restore_progress_callback, update)

        try:
            engine.progress_callback = callback
            results = engine.execute_restore(options, target_system_info=self.system_info)
            self.call_from_thread(self._restore_finished, results)
        except Exception as e:
            self.call_from_thread(self._restore_failed, str(e))
        finally:
            self._is_restoring = False
            try:
                engine.cleanup()
            except Exception:
                pass

    def _restore_progress_callback(self, update: ProgressUpdate) -> None:
        log = self.query_one("#restore-log", RichLog)
        progress = self.query_one("#restore-progress", ProgressBar)

        if update.total_count > 0:
            pct = (update.processed_count / update.total_count) * 100
            progress.update(progress=pct, total=100)

        log.write(f"[{update.phase.upper()}] {update.message}")

    def _restore_finished(self, results: dict) -> None:
        self._is_restoring = False
        self.query_one("#btn-start-restore", Button).disabled = False
        self.query_one("#restore-progress", ProgressBar).update(progress=100, total=100)
        log = self.query_one("#restore-log", RichLog)

        log.write(f"[bold green]✔ {t('restore_log_completed')}[/bold green]")
        log.write(f"  • {t('restore_log_restored_cfgs', count=len(results.get('restored_configs', [])))}")
        log.write(f"  • {t('restore_log_restored_files', count=len(results.get('restored_files', [])))}")
        log.write(f"  • {t('restore_log_restored_pkgs', count=len(results.get('installed_packages', [])))}")
        if results.get("failed_packages"):
            log.write(f"  • [bold red]{t('restore_log_failed_pkgs', count=len(results['failed_packages']))}:[/bold red]")
            for fp in results["failed_packages"]:
                log.write(f"    [red]- {fp}[/red]")
        if results.get("untranslated_packages"):
            log.write(f"  • [bold yellow]{t('restore_log_untranslated_pkgs', count=len(results['untranslated_packages']))}:[/bold yellow]")
            for up in results["untranslated_packages"]:
                log.write(f"    [yellow]- {up}[/yellow]")

        for warn in results.get("warnings", []):
            log.write(f"[yellow]⚠ {warn}[/yellow]")

        skipped_ext = results.get("skipped_external_paths", [])
        if skipped_ext:
            log.write(f"[bold yellow]⚠ {t('restore_skipped_external_paths', count=len(skipped_ext))}[/bold yellow]")
            for sp in skipped_ext:
                log.write(f"  [yellow]• {sp}[/yellow]")

        self.notify(t("restore_notify_success"))

    def _handle_verify_backup(self) -> None:
        src_input = self.query_one("#input-restore-src", Input).value.strip()
        if not src_input:
            self.notify(t("verify_notify_enter_path"), severity="error")
            return

        p = Path(os.path.expanduser(src_input))
        if not p.exists():
            self.notify(t("verify_notify_path_not_found", path=src_input), severity="error")
            return

        existing_pwd = self.current_restore_engine.password if (self.current_restore_engine and self.current_restore_engine.backup_path == p) else None

        if is_encrypted_file(p) and not existing_pwd:
            def on_pwd_entered(pwd: Optional[str]):
                if not pwd:
                    self.notify(t("verify_notify_pwd_empty"), severity="warning")
                    return
                self._start_verify_worker(p, pwd)
            self.push_screen(PasswordModal(title="🔐 " + t("verify_title"), prompt=t("verify_pwd_prompt", name=p.name)), on_pwd_entered)
            return

        self._start_verify_worker(p, existing_pwd)

    def _start_verify_worker(self, p: Path, pwd: Optional[str]) -> None:
        self._is_verifying = True
        try:
            log = self.query_one("#restore-log", RichLog)
            log.write(f"[bold cyan]🔍 {t('verify_started', path=p.name)}[/bold cyan]")
        except Exception:
            pass
        self.notify(t("verify_started", path=p.name))
        try:
            btn = self.query_one("#btn-verify-backup", Button)
            btn.disabled = True
            btn.label = t("verify_btn_in_progress")
        except Exception:
            pass
        self._run_verify_worker(p, pwd, target_log_id="#restore-log", target_progress_id="#restore-progress")

    @work(thread=True, group="verify")
    def _run_verify_worker(
        self,
        backup_path: Path,
        password: Optional[str] = None,
        target_log_id: str = "#restore-log",
        target_progress_id: str = "#restore-progress"
    ) -> None:
        def callback(update: ProgressUpdate):
            self.call_from_thread(self._verify_progress_callback, update, target_log_id, target_progress_id)

        from distronomad.verify import verify_backup
        try:
            result = verify_backup(
                backup_path=backup_path,
                password=password,
                check_chain=True,
                progress_callback=callback
            )
            self.call_from_thread(self._verify_finished, result, target_log_id, target_progress_id)
        except Exception as e:
            self.call_from_thread(self._verify_failed, str(e), target_log_id, target_progress_id)

    def _verify_progress_callback(self, update: ProgressUpdate, target_log_id: str, target_progress_id: str) -> None:
        try:
            log = self.query_one(target_log_id, RichLog)
            progress = self.query_one(target_progress_id, ProgressBar)
            if update.total_count > 0:
                pct = (update.processed_count / update.total_count) * 100
                progress.update(progress=pct, total=100)
            log.write(f"[dim]{update.message}[/dim]")
        except Exception:
            pass

    def _verify_finished(self, result, target_log_id: str, target_progress_id: str) -> None:
        self._is_verifying = False
        try:
            btn = self.query_one("#btn-verify-backup", Button)
            btn.disabled = False
            btn.label = t("btn_verify_backup")
        except Exception:
            pass

        try:
            progress = self.query_one(target_progress_id, ProgressBar)
            progress.update(progress=100, total=100)
        except Exception:
            pass

        try:
            log = self.query_one(target_log_id, RichLog)
            if result.is_valid:
                log.write(f"[bold green]✔ {t('verify_success', matched=len(result.matched))}[/bold green]")
                log.write(f"  • {t('verify_summary_matched')}: [green]{len(result.matched)}[/green]")
                log.write(f"  • {t('verify_summary_skipped')}: [cyan]{len(result.skipped)}[/cyan]")
                for cr in result.chain_results:
                    log.write(f"  • {t('verify_chain_verified', name=cr.archive_path.name)} ([green]{len(cr.matched)}[/green])")
                for w in result.warnings:
                    log.write(f"  • [yellow]⚠ {w}[/yellow]")
                self.notify(t("verify_success", matched=len(result.matched)), title="✔ " + t("verify_title"))
            else:
                err_detail = result.error_message or ""
                log.write(f"[bold red]✖ {t('verify_corrupted', details=err_detail)}[/bold red]")
                if result.error_message:
                    log.write(f"  • [bold red]{t('verify_reason_label')}: {result.error_message}[/bold red]")
                log.write(f"  • {t('verify_summary_matched')}: {len(result.matched)}")
                if result.mismatched:
                    log.write(f"  • [bold red]{t('verify_summary_mismatched')} ({len(result.mismatched)}):[/bold red]")
                    for m in result.mismatched[:10]:
                        log.write(f"    [red]✖ {m}[/red]")
                    if len(result.mismatched) > 10:
                        log.write(f"    ... ({t('verify_more_files', count=len(result.mismatched)-10)})")
                if result.missing:
                    log.write(f"  • [bold red]{t('verify_summary_missing')} ({len(result.missing)}):[/bold red]")
                    for mis in result.missing[:10]:
                        log.write(f"    [red]✖ {mis}[/red]")
                    if len(result.missing) > 10:
                        log.write(f"    ... ({t('verify_more_files', count=len(result.missing)-10)})")
                if result.untracked:
                    log.write(f"  • [bold yellow]{t('verify_summary_untracked')} ({len(result.untracked)}):[/bold yellow]")
                    for unt in result.untracked[:10]:
                        log.write(f"    [yellow]ℹ {unt}[/yellow]")
                for w in result.warnings:
                    log.write(f"  • [yellow]⚠ {w}[/yellow]")
                notify_msg = f"{t('verify_reason_label')}: {err_detail}" if err_detail else t('verify_corrupted', details="")
                self.notify(notify_msg, severity="error", title="✖ " + t("verify_title"))
        except Exception:
            pass

    def _verify_failed(self, error_msg: str, target_log_id: str, target_progress_id: str) -> None:
        self._is_verifying = False
        try:
            btn = self.query_one("#btn-verify-backup", Button)
            btn.disabled = False
            btn.label = t("btn_verify_backup")
        except Exception:
            pass

        try:
            log = self.query_one(target_log_id, RichLog)
            log.write(f"[bold red]✖ {t('verify_log_fatal', error=error_msg)}[/bold red]")
            self.notify(t("verify_notify_fatal", error=error_msg), severity="error")
        except Exception:
            pass


# Geriye dönük uyumluluk takma adı (Backward compatibility alias)
YeniLinuxApp = DistroNomadApp
