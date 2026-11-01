"""
DistroNomad - Etkileşimli Modallar (Dosya Seçici, Parola, Diff Viewer, Rollback, Zamanlayıcı).
Çoklu dil (i18n) destekli.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional, List, Dict, Any

from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual import work
from textual.worker import get_current_worker
from textual.widgets import (
    DirectoryTree,
    Label,
    Button,
    Input,
    RichLog,
    RadioSet,
    RadioButton,
    OptionList,
)
from textual.widgets.option_list import Option
from textual.containers import Vertical, Horizontal

from distronomad import automation
from distronomad.automation import get_timer_status
from distronomad.i18n import t
from distronomad.diff_utils import compute_file_diff



class FilePickerModal(ModalScreen[Optional[Path]]):
    """Kullanıcının terminalde dosya veya klasör seçmesini sağlayan görsel modal."""

    def __init__(
        self,
        title: Optional[str] = None,
        start_path: Optional[str] = None,
        only_dirs: bool = False,
    ):
        super().__init__()
        self.modal_title = title or t("picker_default_title")
        self.only_dirs = only_dirs

        init_p = Path(start_path if start_path else Path.home()).resolve()
        if not init_p.exists():
            init_p = Path.home()
        if not init_p.is_dir():
            init_p = init_p.parent

        self.current_dir = init_p
        self.selected_path = init_p

    def compose(self) -> ComposeResult:
        with Vertical(id="file-picker-dialog"):
            yield Label(f"📂 {self.modal_title}", id="picker-title")

            with Horizontal(id="picker-shortcuts"):
                yield Button(t("picker_btn_home"), id="btn-sc-home")
                yield Button(t("picker_btn_docs"), id="btn-sc-docs")
                yield Button("💾 /media", id="btn-sc-media")
                yield Button("📁 /mnt", id="btn-sc-mnt")
                yield Button(t("picker_btn_up"), id="btn-sc-up")

            yield DirectoryTree(str(self.current_dir), id="picker-tree")

            prefix = t("picker_selected_path_prefix")
            yield Label(f"{prefix} {self.selected_path}", id="picker-selected-path")

            with Horizontal(id="picker-buttons"):
                yield Button(t("picker_btn_confirm"), id="btn-picker-confirm", classes="btn-success")
                yield Button(t("btn_cancel"), id="btn-picker-cancel", classes="btn-danger")

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.selected_path = event.path
        self._update_selected_label()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        if not self.only_dirs:
            self.selected_path = event.path
            self._update_selected_label()

    def _update_selected_label(self) -> None:
        try:
            prefix = t("picker_selected_path_prefix")
            self.query_one("#picker-selected-path", Label).update(f"{prefix} {self.selected_path}")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        tree = self.query_one("#picker-tree", DirectoryTree)

        if btn_id == "btn-sc-home":
            home = Path.home()
            tree.path = home
            self.selected_path = home
            self._update_selected_label()

        elif btn_id == "btn-sc-docs":
            docs = Path.home() / "Documents"
            if not docs.exists():
                docs = Path.home() / "Belgeler"
            if not docs.exists():
                docs = Path.home()
            tree.path = docs
            self.selected_path = docs
            self._update_selected_label()

        elif btn_id == "btn-sc-media":
            media = Path("/media")
            if media.exists():
                tree.path = media
                self.selected_path = media
                self._update_selected_label()

        elif btn_id == "btn-sc-mnt":
            mnt = Path("/mnt")
            if mnt.exists():
                tree.path = mnt
                self.selected_path = mnt
                self._update_selected_label()

        elif btn_id == "btn-sc-up":
            current = Path(tree.path)
            parent = current.parent
            if parent.exists() and parent != current:
                tree.path = parent
                self.selected_path = parent
                self._update_selected_label()

        elif btn_id == "btn-picker-confirm":
            self.dismiss(self.selected_path)

        elif btn_id == "btn-picker-cancel":
            self.dismiss(None)


class PasswordModal(ModalScreen[Optional[str]]):
    """AES-256 parola girişi veya onayı için modal."""

    def __init__(self, title: Optional[str] = None, prompt: Optional[str] = None):
        super().__init__()
        self.modal_title = title or t("pwd_modal_title")
        self.prompt_text = prompt or t("pwd_modal_prompt")

    def compose(self) -> ComposeResult:
        with Vertical(id="password-dialog"):
            yield Label(self.modal_title, id="pwd-modal-title")
            yield Label(self.prompt_text, id="pwd-modal-prompt")
            yield Input(password=True, placeholder=t("pwd_input_placeholder"), id="pwd-input")
            with Horizontal(id="pwd-buttons"):
                yield Button(t("btn_confirm"), id="btn-pwd-confirm", classes="btn-success")
                yield Button(t("btn_cancel"), id="btn-pwd-cancel", classes="btn-danger")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-pwd-confirm":
            pwd = self.query_one("#pwd-input", Input).value.strip()
            self.dismiss(pwd if pwd else None)
        elif event.button.id == "btn-pwd-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        pwd = event.value.strip()
        self.dismiss(pwd if pwd else None)


class DiffViewerModal(ModalScreen[None]):
    """Yedek ile sistem dosyaları arasındaki unified diff farklarını görüntüleyen modal (lazy/on-demand)."""

    def __init__(self, diff_list: List[Dict[str, Any]]):
        super().__init__()
        self.diff_list = diff_list
        self.current_index = 0

    def compose(self) -> ComposeResult:
        options = []
        for idx, d in enumerate(self.diff_list):
            stat_char = "✨" if d["status"] == "new_in_backup" else ("📝" if d["status"] == "modified" else ("💾" if d["status"] == "binary" else "📄"))
            options.append(Option(f"{stat_char} {d['rel_path']}", id=str(idx)))

        with Vertical(id="diff-dialog"):
            yield Label(t("diff_modal_title"), id="diff-title")
            with Horizontal(id="diff-container"):
                with Vertical(id="diff-file-list"):
                    yield Label(t("diff_file_list_lbl"), classes="diff-subheading")
                    yield OptionList(*options, id="diff-files-list")
                with Vertical(id="diff-content-panel"):
                    yield Label(t("diff_preview_lbl"), id="diff-file-header")
                    yield RichLog(highlight=True, markup=True, id="diff-log")
            with Horizontal(id="diff-bottom-bar"):
                yield Label(t("diff_total_analyzed", count=len(self.diff_list)), id="diff-count-label")
                yield Button(t("btn_close"), id="btn-diff-close", classes="btn-primary")

    def on_mount(self) -> None:
        log = self.query_one("#diff-log", RichLog)
        if not self.diff_list:
            log.write(t("diff_no_differences"))
        else:
            log.write(t("diff_select_prompt"))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if idx is not None and 0 <= idx < len(self.diff_list):
            self._request_diff(idx)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-diff-close":
            self.dismiss(None)
        elif btn_id and btn_id.startswith("btn-diff-file-"):
            try:
                idx = int(btn_id.replace("btn-diff-file-", ""))
                self._show_diff(idx)
            except Exception:
                pass

    def _request_diff(self, idx: int) -> None:
        """Kullanıcı bir dosyaya tıkladığında arka plan worker'ı başlatır."""
        if 0 <= idx < len(self.diff_list):
            self.current_index = idx
            item = self.diff_list[idx]
            try:
                self.query_one("#diff-file-header", Label).update(f"📄 {item['rel_path']} (...)")
            except Exception:
                pass
            self._load_diff_worker(idx)

    @work(thread=True, exclusive=True, group="diff_viewer")
    def _load_diff_worker(self, idx: int) -> None:
        """Ağır diff hesaplamasını UI iş parçacığını kilitlemeden arka planda yapar."""
        try:
            worker = get_current_worker()
        except Exception:
            worker = None

        if 0 <= idx < len(self.diff_list):
            item = self.diff_list[idx]
            diff_text, added, removed = compute_file_diff(item)
            if worker and worker.is_cancelled:
                return
            self.app.call_from_thread(self._render_diff_content, idx, diff_text, added, removed)

    def _show_diff(self, idx: int) -> None:
        """Senkron çağrılar ve testler için doğrudan diff üretip gösterir."""
        if 0 <= idx < len(self.diff_list):
            self.current_index = idx
            item = self.diff_list[idx]
            diff_text, added, removed = compute_file_diff(item)
            self._render_diff_content(idx, diff_text, added, removed)

    def _render_diff_content(self, idx: int, diff_text: str, added: int, removed: int) -> None:
        """Üretilen diff sonucunu UI üzerinde RichLog ve başlığa yansıtır."""
        if 0 <= idx < len(self.diff_list):
            item = self.diff_list[idx]
            header = f"📄 {item['rel_path']} (+{added} / -{removed})"
            try:
                self.query_one("#diff-file-header", Label).update(header)
                log = self.query_one("#diff-log", RichLog)
                log.clear()
                for line in diff_text.splitlines():
                    if line.startswith("+++") or line.startswith("---"):
                        log.write(f"[bold cyan]{line}[/bold cyan]")
                    elif line.startswith("@@"):
                        log.write(f"[bold magenta]{line}[/bold magenta]")
                    elif line.startswith("+"):
                        log.write(f"[green]{line}[/green]")
                    elif line.startswith("-"):
                        log.write(f"[red]{line}[/red]")
                    else:
                        log.write(f"[white]{line}[/white]")
            except Exception:
                pass


class RollbackModal(ModalScreen[bool]):
    """Son geri yükleme işlemini geri alma onay modalı."""

    def __init__(self, tx_data: Dict[str, Any]):
        super().__init__()
        self.tx_data = tx_data

    def compose(self) -> ComposeResult:
        with Vertical(id="rollback-dialog"):
            yield Label(t("rollback_modal_title"), id="rollback-title")
            ops_count = len(self.tx_data.get("operations", []))
            timestamp = self.tx_data.get("timestamp", "-")
            backup_src = Path(self.tx_data.get("backup_path", "")).name

            desc_text = t(
                "rollback_modal_desc",
                timestamp=timestamp,
                backup_src=backup_src,
                ops_count=ops_count,
            )

            yield Label(desc_text, id="rollback-desc")
            with Horizontal(id="rollback-buttons"):
                yield Button(t("btn_rollback_confirm"), id="btn-rb-confirm", classes="btn-warning")
                yield Button(t("btn_rollback_cancel"), id="btn-rb-cancel", classes="btn-primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-rb-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)


class AutomationModal(ModalScreen[Optional[str]]):
    """systemd user timer zamanlanmış yedekleme yapılandırma modalı."""

    def __init__(self):
        super().__init__()
        self.status = get_timer_status()

    def compose(self) -> ComposeResult:
        with Vertical(id="auto-dialog"):
            yield Label(t("auto_modal_title"), id="auto-title")
            st_text = t("auto_status_active") if self.status["is_active"] else t("auto_status_inactive")
            status_lbl = t("auto_lbl_status")
            interval_lbl = t("auto_lbl_interval")
            raw_interval = self.status.get("interval", "daily")
            interval_text = t(f"auto_{raw_interval}") if f"auto_{raw_interval}" in ("auto_daily", "auto_weekly", "auto_hourly") else raw_interval
            yield Label(f"{status_lbl} {st_text} | {interval_lbl} {interval_text}", id="auto-current-status")
            yield Label(t("auto_interval_lbl"))
            yield RadioSet(
                RadioButton(t("auto_daily"), id="radio-daily", value=True),
                RadioButton(t("auto_weekly"), id="radio-weekly"),
                RadioButton(t("auto_hourly"), id="radio-hourly"),
                id="auto-interval-radios"
            )
            with Horizontal(id="auto-buttons"):
                yield Button(t("btn_auto_enable"), id="btn-auto-enable", classes="btn-success")
                yield Button(t("btn_auto_disable"), id="btn-auto-disable", classes="btn-danger")
                yield Button(t("btn_close"), id="btn-auto-close", classes="btn-primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-auto-enable":
            radios = self.query_one("#auto-interval-radios", RadioSet)
            interval = "daily"
            if radios.pressed_button:
                rb_id = radios.pressed_button.id
                if rb_id == "radio-weekly":
                    interval = "weekly"
                elif rb_id == "radio-hourly":
                    interval = "hourly"
            res = automation.enable_timer(interval=interval)
            if res.get("success") and res.get("systemctl_enabled"):
                self.dismiss("enabled")
            else:
                if res.get("message"):
                    self.app.notify(res["message"], severity="error")
                self.dismiss("failed")
        elif btn_id == "btn-auto-disable":
            res = automation.disable_timer()
            if res.get("success"):
                self.dismiss("disabled")
            else:
                self.dismiss("failed")
        else:
            self.dismiss(None)


class Fat32WarningModal(ModalScreen[bool]):
    """FAT32 4 GB sınır uyarısı ve onay modalı."""

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="fat32-dialog"):
            yield Label(t("fat32_warning_title"), id="fat32-title")
            yield Label(self.message, id="fat32-desc")
            with Horizontal(id="fat32-buttons"):
                yield Button(t("btn_fat32_proceed"), id="btn-fat32-proceed", classes="btn-warning")
                yield Button(t("btn_fat32_cancel"), id="btn-fat32-cancel", classes="btn-primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fat32-proceed":
            self.dismiss(True)
        else:
            self.dismiss(False)


class ConfirmExitModal(ModalScreen[bool]):
    """Devam eden bir işlem varken uygulamadan çıkış onay modalı."""

    def __init__(self, message: Optional[str] = None):
        super().__init__()
        self.message = message or t("confirm_quit_prompt")

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-quit-dialog"):
            yield Label(t("confirm_quit_title"), id="confirm-quit-title")
            yield Label(self.message, id="confirm-quit-desc")
            with Horizontal(id="confirm-quit-buttons"):
                yield Button(t("btn_quit_confirm"), id="btn-quit-confirm", classes="btn-danger")
                yield Button(t("btn_quit_cancel"), id="btn-quit-cancel", classes="btn-primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-quit-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)


