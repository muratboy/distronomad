"""
DistroNomad - Çoklu Dil Desteği ve Yerelleştirme Motoru (i18n / Localization).
Desteklenen Diller: Türkçe (tr), İngilizce (en).
"""

from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional


_CURRENT_LANGUAGE = "tr"
_DEFAULT_SETTINGS_DIR = Path.home() / ".config" / "distronomad"
_DEFAULT_SETTINGS_FILE = _DEFAULT_SETTINGS_DIR / "settings.json"
SETTINGS_DIR = _DEFAULT_SETTINGS_DIR
SETTINGS_FILE = _DEFAULT_SETTINGS_FILE


def get_runtime_settings_dir() -> Path:
    """Çalışma anındaki ev dizini ve XDG konfigürasyon yolunu hesaplar."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "distronomad"
    home = Path(os.environ.get("HOME") or Path.home())
    return home / ".config" / "distronomad"


def get_runtime_settings_file() -> Path:
    """Çalışma anındaki ayar dosyası yolunu hesaplar."""
    return get_runtime_settings_dir() / "settings.json"


def load_saved_settings() -> Dict[str, Any]:
    """Kullanıcının kayıtlı ayarlarını (~/.config/distronomad/settings.json) yükler."""
    target_file = SETTINGS_FILE
    if target_file == _DEFAULT_SETTINGS_FILE:
        target_file = get_runtime_settings_file()

    if target_file.exists():
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_setting(key: str, value: Any) -> None:
    """Belirtilen ayarı (~/.config/distronomad/settings.json) dosyasına kaydeder."""
    try:
        target_dir = SETTINGS_DIR
        target_file = SETTINGS_FILE
        if target_dir == _DEFAULT_SETTINGS_DIR:
            target_dir = get_runtime_settings_dir()
        if target_file == _DEFAULT_SETTINGS_FILE:
            target_file = get_runtime_settings_file()

        target_dir.mkdir(parents=True, exist_ok=True)
        settings = load_saved_settings()
        settings[key] = value
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def normalize_language(lang: Any) -> str:
    """Verilen dil kodunu 'tr' veya 'en' olarak normalize eder."""
    if not lang:
        return "en"
    l = str(lang).strip().lower()
    if l.startswith("tr"):
        return "tr"
    return "en"


def detect_default_language() -> str:
    """Varsayılan dili belirler:
    1. Kayıtlı ayar (settings.json -> language) varsa o.
    2. Yoksa ortam değişkenlerinde (LC_ALL, LC_MESSAGES, LANG) 'tr' ile başlıyorsa 'tr'.
    3. Aksi hâlde (C, POSIX, de_DE vb. dahil) 'en'.
    """
    settings = load_saved_settings()
    saved = settings.get("language")
    if saved:
        return normalize_language(saved)

    # Standart yerel önceliği: LC_ALL -> LC_MESSAGES -> LANG
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_var)
        if val and val.strip():
            return "tr" if val.strip().lower().startswith("tr") else "en"
    return "en"


def get_current_language() -> str:
    """Mevcut aktif dili döndürür ('tr' veya 'en')."""
    global _CURRENT_LANGUAGE
    return _CURRENT_LANGUAGE


def set_current_language(lang: str, persist: bool = True) -> str:
    """Aktif dili ayarlar ve istenirse diske kaydeder."""
    global _CURRENT_LANGUAGE
    normalized = normalize_language(lang)
    _CURRENT_LANGUAGE = normalized
    if persist:
        save_setting("language", normalized)
    return normalized


# Başlangıçta kayıtlı dili veya ortam dilini belirle
_CURRENT_LANGUAGE = detect_default_language()


HELP_MARKDOWN_TR = """
# 🐧 DistroNomad (Sürüm 0.9.0) - Kullanım ve Dağıtım Taşıma Rehberi

DistroNomad, mevcut Linux dağıtımınızdaki tüm uygulamaları, masaüstü/terminal konfigürasyonlarını, tarayıcı profillerini, Flatpak uygulama verilerini ve kişisel dosyalarınızı kayıpsız bir şekilde yedekleyip, format attıktan sonra kurduğunuz **yeni Linux dağıtımına** tek tıkla aktarmanızı sağlar.

Komut satırından `distronomad` komutu ile ister modern TUI arayüzünü açabilir, isterseniz doğrudan alt komutları (`backup`, `restore`, `verify`, `append`, `diff`, `rollback`, `schedule`) çalıştırabilirsiniz. `--lang tr|en` seçeneği hem TUI hem de tüm CLI komutlarında kullanılabilir.

---

### 🚀 Temel Özellikler
1. **Evrensel Paket, Uygulama & Eklenti Taşıma:**
   - **Pacman (Arch/CachyOS), Apt (Debian/Ubuntu), DNF (Fedora), Zypper (openSUSE)** ve **Flatpak, Snap, Pipx, Cargo, NPM, VS Code / VSCodium Eklentileri** destekler.
   - Farklı bir dağıtıma geçtiğinizde (örn: Arch'tan Fedora'ya veya Ubuntu'ya), paket adlarını otomatik olarak hedef dağıtımın depolarına göre çevirir!
   - Flatpak uygulamaları doğrudan evrensel Flathub ID'leri ile taşınır.

2. **Eksiksiz Konfigürasyon, Masaüstü (GNOME/KDE/WM) ve Profil Koruması:**
   - `~/.config` altındaki tüm masaüstü kompozitörleri (Hyprland, Sway, i3), terminaller (Kitty, Alacritty), düzenleyiciler (Neovim, VS Code) ve dotfile'lar (`.bashrc`, `.zshrc`, `.tmux.conf`).
   - **Kabuk Komut Geçmişleri:** `.bash_history`, `.zsh_history`, `fish_history`, `atuin` (hassas veri olarak varsayılan seçimsiz gelir).
   - **GNOME & KDE Plasma:** `dconf` masaüstü veritabanı yedeği, GNOME Shell eklentileri, KDE Plasma widget'ları, Konsole profilleri ve renk şemaları.
   - **Tarayıcı & E-posta Profilleri:** Firefox (`~/.mozilla/firefox`), Thunderbird (`~/.thunderbird`), Zen Browser, LibreWolf (önbellekler hariç tutularak).
   - **Flatpak Uygulama Verileri & İzinleri:** `~/.var/app/*` uygulama ayarları ve Flatseal izin özelleştirmeleri (`~/.local/share/flatpak/overrides`).
   - **Hassas Veriler & Bulut Anahtarları:** `~/.ssh`, `~/.gnupg`, `~/.kube`, `~/.aws`, `~/.password-store`, GNOME Keyring ve KWallet.
   - **Sistem Durumu & `/etc` Referansları:** Kullanıcı grupları (`groups`), `crontab`, etkin `systemd --user` servisleri ve `/etc/fstab`, `/etc/hosts`, `/etc/udev/rules.d/` referans kopyaları.

3. **Seçici Geri Yükleme (Granular Restore) & Güvenlik Korumaları:**
   - Hangi uygulamaların kurulacağını, hangi ayarların ve dosyaların yükleneceğini tek tek seçebilirsiniz.
   - **Ev Dışı Yollar Koruması:** Ev dizini dışındaki özel yollar (`custom/root`) güvenlik amacıyla varsayılan olarak atlanır; yalnızca açık onayla (`--allow-external-paths` veya TUI onay kutusu) geri yüklenir. Hedef ev dizini gerçek ev dizini değilse köke asla yazılmaz.

4. **Yedek Doğrulama (Verify) & Mevcut Yedeğe Ekleme (Append):**
   - **Bütünlük Doğrulama (Verify):** `distronomad verify` komutu veya TUI'deki [Yedeği Doğrula] butonu ile SHA-256 bütünlüğünü, eksik/bozuk dosyaları ve artımlı yedek zincirlerini test edebilirsiniz.
   - **Mevcut Yedeğe Ekleme (Append):** Önceden alınmış bir tam yedeğe yeni dosyaları (`--path`), ayarları (`--config`) veya paketleri (`--package`) bütünlüğü bozmadan ekleyebilirsiniz.

5. **Görsel Dosya & Klasör Gezgini (Kolay Seçim):**
   - Dahili `[📂 Gözat]` modalı ile USB belleğinizi, harici disklerinizi veya ev dizininizi tıklayarak seçebilirsiniz.

6. **Yedekleme Sepeti & Hızlı Çıkarma:**
   - Hangi uygulamaların, ayarların ve klasörlerin arşive ekleneceğini "Yedekle" sekmesindeki canlı tabloda görebilirsiniz.
   - İstemediğiniz ögeleri sepetten anında çıkarmak için üzerine gelip `[Enter]`, `[Delete]` veya `[🗑️ Seçiliyi Sepetten Çıkar]` butonuna basabilirsiniz.

7. **İleri Seviye Güvenlik & Fark Analizi:**
   - **AES-256 Şifreleme:** İsteğe bağlı olarak yedeklerinizi parola ile şifreleyin.
   - **Fark Analizi (Diff):** Geri yüklemeden önce mevcut dosyalar ile yedek arasındaki farkları satır satır inceleyin (`distronomad diff`).
   - **1-Tıkla Geri Alma (Rollback):** Beklenmedik durumlarda geri yüklemeyi tek tıkla geri alın (`distronomad rollback`).
   - **Zamanlayıcı (systemd User Timer):** İsteğe bağlı otomatik periyodik yedekleme (`distronomad schedule`).

---

### ⌨️ Klavye Kısayolları
- **Sekmeler:** `F1` (Genel Bakış), `F2` (Uygulamalar), `F3` (Ayarlar), `F4` (Dosyalar), `F5` (Yedekle), `F6` (Geri Yükle), `F7` (Rehber)
- **Dil Değiştir:** `l` (Türkçe / English geçişi yap)
- **Tema Değiştir:** `t` (Cyberpunk, Tokyo Night, Dracula, Nord, CachyOS, Monokai)
- **Sepetten Çıkar (F5'te):** `Enter` (Seçiliyi Çıkar), `Delete` / `Backspace` / `x` (Seçiliyi Sil)
- **Boşluk (Space):** Seçili ögeyi işaretle / kaldır
- **Tab / Shift+Tab:** Ögeler ve butonlar arasında gezin
- **q / Ctrl+C:** Çıkış
"""

HELP_MARKDOWN_EN = """
# 🐧 DistroNomad (Version 0.9.0) - Migration & Configuration Guide

DistroNomad allows you to seamlessly back up all installed applications, desktop/terminal configurations, browser profiles, Flatpak app data, and personal files from your current Linux distribution, and restore them with one click onto a **new Linux distribution** after reformatting or switching distros.

Run `distronomad` from the command line to launch the modern TUI interface, or run subcommands directly (`backup`, `restore`, `verify`, `append`, `diff`, `rollback`, `schedule`). The `--lang tr|en` flag is supported across both TUI and CLI subcommands.

---

### 🚀 Key Features
1. **Universal Package & App Migration (plus Extensions):**
   - Supports **Pacman (Arch/CachyOS), Apt (Debian/Ubuntu), DNF (Fedora), Zypper (openSUSE)** as well as **Flatpak, Snap, Pipx, Cargo, NPM, and VS Code / VSCodium Extensions**.
   - When hopping to a different distribution family (e.g., from Arch to Fedora or Ubuntu), package names are automatically translated to target repositories!
   - Flatpak apps are backed up using universal Flathub IDs.

2. **Full Configuration, Desktop (GNOME/KDE/WM) & Profile Preservation:**
   - Protects compositors and window managers (Hyprland, Sway, i3), terminal emulators (Kitty, Alacritty), editors (Neovim, VS Code), and shell dotfiles (`.bashrc`, `.zshrc`, `.tmux.conf`) in `~/.config`.
   - **Shell Command Histories:** `.bash_history`, `.zsh_history`, `fish_history`, `atuin` (marked sensitive and unselected by default).
   - **GNOME & KDE Plasma:** `dconf` settings dump, GNOME Shell extensions, KDE Plasma widgets, Konsole profiles, and color schemes.
   - **Browser & Mail Profiles:** Firefox (`~/.mozilla/firefox`), Thunderbird (`~/.thunderbird`), Zen Browser, LibreWolf (with caches automatically excluded).
   - **Flatpak App Data & Overrides:** `~/.var/app/*` app data and Flatseal permission overrides (`~/.local/share/flatpak/overrides`).
   - **Sensitive Keys & Cloud Credentials:** `~/.ssh`, `~/.gnupg`, `~/.kube`, `~/.aws`, `~/.password-store`, GNOME Keyring, and KWallet.
   - **System State & `/etc` References:** User groups (`groups`), `crontab`, enabled `systemd --user` services, and safe reference copies of `/etc/fstab`, `/etc/hosts`, and `/etc/udev/rules.d/`.

3. **Granular Selective Restore & Safety Boundaries:**
   - Choose exactly which applications, config files, and personal folders to restore.
   - **External Root Paths Protection:** Custom paths outside the home directory (`custom/root`) are skipped by default for safety; they are only restored with explicit confirmation (`--allow-external-paths` or TUI checkbox). If target home is not the real home, root paths are never written.

4. **Backup Verification (Verify) & Appending to Backups (Append):**
   - **Integrity Verification (Verify):** Verify SHA-256 checksums, missing/corrupted files, and incremental backup chains via `distronomad verify` or the TUI [Verify Backup] button.
   - **Append Mode (Append):** Add specific paths (`--path`), configurations (`--config`), or packages (`--package`) to an existing full backup archive without recreating it.

5. **Visual File & Folder Browser:**
   - Use the built-in `[📂 Browse]` modal to pick external drives, USB keys, or home directories with ease.

6. **Backup Cart & Quick Item Removal:**
   - Inspect all items selected for backup in the live table under the "Backup" tab.
   - Quickly remove items from your cart using `[Enter]`, `[Delete]`, or the `[🗑️ Remove Selected]` button.

7. **Advanced Security & Diff Tools:**
   - **AES-256 Encryption:** Protect sensitive backup archives with password encryption.
   - **Diff Viewer:** Review file differences line-by-line before restoring (`distronomad diff`).
   - **One-Click Rollback:** Revert restore transactions completely in case of issues (`distronomad rollback`).
   - **systemd User Timer:** Optional scheduled periodic background backups (`distronomad schedule`).

---

### ⌨️ Keyboard Shortcuts
- **Tabs:** `F1` (Overview), `F2` (Apps), `F3` (Configs), `F4` (Files), `F5` (Backup), `F6` (Restore), `F7` (Guide)
- **Switch Language:** `l` (Toggle between Türkçe and English)
- **Switch Theme:** `t` (Cycle through Cyberpunk, Tokyo Night, Dracula, Nord, CachyOS, Monokai)
- **Remove from Cart (F5):** `Enter` (Remove selected), `Delete` / `Backspace` / `x` (Delete item)
- **Space:** Toggle checkbox / selection
- **Tab / Shift+Tab:** Navigate between widgets and buttons
- **q / Ctrl+C:** Quit application
"""


TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # Üst Çubuk & Uygulama Başlığı
    "app_title": {
        "tr": "DistroNomad",
        "en": "DistroNomad"
    },
    "btn_lang_tr": {
        "tr": "🌐 Dil: TR",
        "en": "🌐 Lang: TR"
    },
    "btn_lang_en": {
        "tr": "🌐 Dil: EN",
        "en": "🌐 Lang: EN"
    },
    "scheduler_btn_off": {
        "tr": "⏰ Zamanlayıcı (Kapalı)",
        "en": "⏰ Scheduler (Off)"
    },
    "scheduler_btn_on": {
        "tr": "⏰ Zamanlayıcı (Aktif)",
        "en": "⏰ Scheduler (Active)"
    },

    # Sekme İsimleri
    "tab_overview": {
        "tr": "📊 Genel Bakış",
        "en": "📊 Overview"
    },
    "tab_apps": {
        "tr": "📦 Uygulamalar",
        "en": "📦 Applications"
    },
    "tab_configs": {
        "tr": "⚙️ Ayarlar & Dotfiles",
        "en": "⚙️ Configs & Dotfiles"
    },
    "tab_custom": {
        "tr": "📁 Özel Dosyalar",
        "en": "📁 Custom Files"
    },
    "tab_backup": {
        "tr": "💾 Yedekle",
        "en": "💾 Backup"
    },
    "tab_restore": {
        "tr": "🔄 Geri Yükle",
        "en": "🔄 Restore"
    },
    "tab_help": {
        "tr": "❓ Rehber & İpuçları",
        "en": "❓ Guide & Tips"
    },

    # Genel Bakış Sekmesi
    "card_sys_info": {
        "tr": "SİSTEM BİLGİSİ VE DAĞITIM DETAYLARI",
        "en": "SYSTEM INFO & DISTRIBUTION DETAILS"
    },
    "info_distro": {
        "tr": "• Dağıtım:",
        "en": "• Distribution:"
    },
    "info_kernel": {
        "tr": "• Çekirdek (Kernel):",
        "en": "• Kernel:"
    },
    "info_de": {
        "tr": "• Masaüstü Ortamı (DE/WM):",
        "en": "• Desktop Environment (DE/WM):"
    },
    "info_shell": {
        "tr": "• Varsayılan Kabuk:",
        "en": "• Default Shell:"
    },
    "info_pms": {
        "tr": "• Paket Yöneticileri:",
        "en": "• Package Managers:"
    },
    "stat_box_apps": {
        "tr": "📦 UYGULAMALAR",
        "en": "📦 APPLICATIONS"
    },
    "stat_box_apps_sub": {
        "tr": "Seçili / Toplam Paket",
        "en": "Selected / Total Packages"
    },
    "stat_box_configs": {
        "tr": "⚙️ AYARLAR & DOTFILES",
        "en": "⚙️ CONFIGS & DOTFILES"
    },
    "stat_box_configs_sub": {
        "tr": "~/.config Dizinleri",
        "en": "~/.config Directories"
    },
    "stat_box_files": {
        "tr": "📁 ÖZEL KLASÖRLER",
        "en": "📁 CUSTOM FOLDERS"
    },
    "stat_box_files_sub": {
        "tr": "Belgeler, Kodlar vb.",
        "en": "Documents, Code, etc."
    },
    "stat_box_size": {
        "tr": "💾 TAHMİNİ BOYUT",
        "en": "💾 ESTIMATED SIZE"
    },
    "stat_box_size_sub": {
        "tr": "Yedeklenecek Veri",
        "en": "Data to Backup"
    },
    "card_stat_guide": {
        "tr": "ℹ️ BU SAYILAR NE ANLAMA GELİYOR? (İSTATİSTİK REHBERİ)",
        "en": "ℹ️ WHAT DO THESE NUMBERS MEAN? (STATISTICS GUIDE)"
    },
    "info_guide_apps": {
        "tr": "• 📦 Uygulamalar: Sisteminizde tespit edilen ve yeni dağıtıma taşınacak program sayısıdır (Pacman, Flatpak, Snap vb.).",
        "en": "• 📦 Applications: Number of installed packages detected and to be migrated (Pacman, Flatpak, Snap, etc.)."
    },
    "info_guide_configs": {
        "tr": "• ⚙️ Ayarlar & Dotfiles: ~/.config altındaki masaüstü/terminal konfigürasyonlarınız ile .bashrc, .zshrc gibi dotfile dosyalarınızdır.",
        "en": "• ⚙️ Configs & Dotfiles: Desktop/terminal configs under ~/.config and dotfiles like .bashrc, .zshrc."
    },
    "info_guide_files": {
        "tr": "• 📁 Özel Klasörler: Belgeler, Resimler, Projeler gibi yedeklemek üzere işaretlediğiniz kişisel klasörlerin sayısıdır.",
        "en": "• 📁 Custom Folders: Personal directories selected for backup (Documents, Pictures, Projects, etc.)."
    },
    "info_guide_size": {
        "tr": "• 💾 Tahmini Boyut: Sıkıştırma öncesinde yedeğe dahil edilecek ayar ve kişisel dosyaların disk boyutudur (Uygulamalar yeni sistemde depolardan kurulacağı için yedek arşivinde yer kaplamaz).",
        "en": "• 💾 Estimated Size: Raw disk size of configs and personal files to be archived (Apps take 0 B in backup archive as they are installed directly from repositories)."
    },
    "btn_refresh_scan": {
        "tr": "🔄 Taramayı Yenile",
        "en": "🔄 Refresh Scan"
    },
    "btn_select_all_apps": {
        "tr": "Tüm Uygulamaları Seç",
        "en": "Select All Apps"
    },
    "btn_select_all_configs": {
        "tr": "Tüm Ayarları Seç",
        "en": "Select All Configs"
    },
    "btn_goto_backup": {
        "tr": "🚀 Doğrudan Yedekle",
        "en": "🚀 Go to Backup"
    },

    # Araç Çubuğu Butonları & Arama
    "search_apps_ph": {
        "tr": "🔍 Uygulama veya paket ara (örn: steam, vlc, git)...",
        "en": "🔍 Search applications or packages (e.g. steam, vlc, git)..."
    },
    "search_configs_ph": {
        "tr": "🔍 Ayar veya dotfile ara (örn: hypr, nvim, zsh)...",
        "en": "🔍 Search configs or dotfiles (e.g. hypr, nvim, zsh)..."
    },
    "btn_select_all": {
        "tr": "Tümünü Seç",
        "en": "Select All"
    },
    "btn_select_none": {
        "tr": "Temizle",
        "en": "Clear"
    },
    "btn_only_gui": {
        "tr": "Yalnızca Masaüstü",
        "en": "Desktop Only"
    },
    "btn_toggle_ssh": {
        "tr": "Hassas/SSH Dahil Et",
        "en": "Include Sensitive/SSH"
    },

    # Özel Dosyalar Sekmesi
    "card_add_custom": {
        "tr": "Yedeklenecek Özel Dizin veya Dosya Ekle:",
        "en": "Add Custom Directory or File to Backup:"
    },
    "input_custom_ph": {
        "tr": "/home/kullanici/Belgeler veya /projeler yolu...",
        "en": "/home/user/Documents or /projects path..."
    },
    "btn_browse": {
        "tr": "📂 Gözat",
        "en": "📂 Browse"
    },
    "btn_add": {
        "tr": "➕ Ekle",
        "en": "➕ Add"
    },
    "card_custom_list": {
        "tr": "Önerilen ve Eklenen Kişisel Dizinler:",
        "en": "Recommended & Added Personal Directories:"
    },
    "search_custom_ph": {
        "tr": "🔍 Özel klasör veya dosya ara...",
        "en": "🔍 Search custom folders or files..."
    },
    "btn_custom_remove_selected": {
        "tr": "🗑️ Listeden Kaldır",
        "en": "🗑️ Remove from List"
    },
    "btn_custom_clear_all": {
        "tr": "🧹 Listeyi Temizle",
        "en": "🧹 Clear List"
    },
    "custom_summary_label": {
        "tr": "Seçilen: {sel} / {total} Klasör/Dosya (~{size})",
        "en": "Selected: {sel} / {total} Folders/Files (~{size})"
    },
    "custom_tip": {
        "tr": "💡 İpucu: Listeden çıkarmak istediğiniz ögeyi seçip [🗑️ Listeden Kaldır] butonuna basabilir veya [Delete] tuşunu kullanabilirsiniz. İşaretini kaldırdığınız ögeler yedeğe dahil edilmez.",
        "en": "💡 Tip: Select an item and click [🗑️ Remove from List] or press [Delete] to remove it from this list. Unchecking an item excludes it from the backup cart."
    },
    "custom_item_removed": {
        "tr": "🗑️ '{name}' özel dosyalar listesinden kaldırıldı.",
        "en": "🗑️ '{name}' removed from custom files list."
    },
    "custom_cleared": {
        "tr": "🧹 Özel dosyalar listesi temizlendi.",
        "en": "🧹 Custom files list cleared."
    },
    "custom_already_added": {
        "tr": "Bu öge zaten listede mevcut: {path}",
        "en": "This item is already in the list: {path}"
    },

    # Yedekle Sekmesi
    "card_cart_title": {
        "tr": "📋 YEDEKLENECEK SEÇİLEN ÖGELERİN LİSTESİ (YEDEKLEME SEPETİ)",
        "en": "📋 SELECTED ITEMS FOR BACKUP (BACKUP CART)"
    },
    "search_cart_ph": {
        "tr": "🔍 Sepette ara...",
        "en": "🔍 Search in cart..."
    },
    "filter_all": {
        "tr": "Tümü",
        "en": "All"
    },
    "filter_apps": {
        "tr": "Uygulamalar",
        "en": "Apps"
    },
    "filter_configs": {
        "tr": "Ayarlar",
        "en": "Configs"
    },
    "filter_custom": {
        "tr": "Klasörler",
        "en": "Folders"
    },
    "cart_selected_label": {
        "tr": "Seçilen: {count} Öge",
        "en": "Selected: {count} Items"
    },
    "col_action": {
        "tr": "İşlem",
        "en": "Action"
    },
    "col_type": {
        "tr": "Tür",
        "en": "Type"
    },
    "col_name": {
        "tr": "Öğe / Paket Adı",
        "en": "Item / Package Name"
    },
    "col_category": {
        "tr": "Kategori / Konum",
        "en": "Category / Path"
    },
    "col_version_content": {
        "tr": "Sürüm / İçerik",
        "en": "Version / Content"
    },
    "col_backup_size": {
        "tr": "Yedek Boyutu",
        "en": "Backup Size"
    },
    "cart_size_pkg": {
        "tr": "0 B (Depodan)",
        "en": "0 B (From Repo)"
    },
    "cart_folder_category": {
        "tr": "Kişisel Klasör",
        "en": "Personal Folder"
    },
    "version_not_specified": {
        "tr": "Belirtilmemiş",
        "en": "Not specified"
    },
    "app_version_label": {
        "tr": "Sürüm: {ver}",
        "en": "Ver: {ver}"
    },
    "cart_row_remove": {
        "tr": "✕ Çıkar",
        "en": "✕ Remove"
    },
    "cart_row_app": {
        "tr": "📦 Uygulama",
        "en": "📦 Application"
    },
    "cart_row_config": {
        "tr": "⚙️ Ayar",
        "en": "⚙️ Config"
    },
    "cart_row_folder": {
        "tr": "📁 Özel Klasör",
        "en": "📁 Custom Folder"
    },
    "cart_row_files_count": {
        "tr": "{count} dosya",
        "en": "{count} files"
    },
    "btn_cart_remove": {
        "tr": "🗑️ Seçiliyi Sepetten Çıkar",
        "en": "🗑️ Remove Selected from Cart"
    },
    "btn_cart_remove_filtered": {
        "tr": "🧹 Filtrelenenleri Çıkar",
        "en": "🧹 Remove Filtered"
    },
    "btn_cart_clear_all": {
        "tr": "⚠️ Tüm Sepeti Boşalt",
        "en": "⚠️ Clear Entire Cart"
    },
    "cart_tip": {
        "tr": "💡 İpucu: Çıkarmak için ögeye çift tıklayabilir veya [Delete] tuşuna basabilirsiniz. Uygulamalar yeni sistemde depolardan kurulacağı için yedek boyutları 0 B görünür.",
        "en": "💡 Tip: Double click or press [Delete] to remove an item. Applications will be installed from repositories on the target system, so their archive size is 0 B."
    },

    "card_backup_dest_title": {
        "tr": "💾 YEDEKLEME HEDEFİ VE SEÇENEKLERİ",
        "en": "💾 BACKUP DESTINATION & OPTIONS"
    },
    "lbl_backup_mode": {
        "tr": "Yedekleme Modu:",
        "en": "Backup Mode:"
    },
    "mode_full": {
        "tr": "📦 Sıfırdan Yeni Yedek Oluştur (Seçilen tüm uygulama, ayar ve dosyaları yeni bir arşive kaydet)",
        "en": "📦 Create New Full Backup (Save all selected apps, configs and files into a new archive)"
    },
    "mode_inc": {
        "tr": "🔄 Sadece Değişen Dosyaları Yedekle (Önceki yedeğe göre sadece içeriği değişen dosyaların fark yedeğini al)",
        "en": "🔄 Backup Only Changed Files (Create a delta backup of files modified since base backup)"
    },
    "mode_append": {
        "tr": "➕ Var Olan Yedeğin İçine Ekle (Önceden aldığınız bir yedeğin içine sonradan uygulama, ayar veya dosya ekle)",
        "en": "➕ Add Into Existing Backup (Insert newly selected apps, configs or files into a previous backup)"
    },
    "card_cart_title_append": {
        "tr": "➕ MEVCUT YEDEĞE EKLENECEK YENİ ÖGELER (Sadece Yeni Seçtikleriniz)",
        "en": "➕ NEW ITEMS TO ADD INTO EXISTING BACKUP (Only Newly Selected)"
    },
    "lbl_backup_dest_append": {
        "tr": "📂 İçine Ekleme Yapılacak Mevcut Yedek Dosyası:",
        "en": "📂 Existing Backup File to Add Into:"
    },
    "btn_browse_dest_append": {
        "tr": "📂 Mevcut Yedeği Seç (Gözat)",
        "en": "📂 Select Existing Backup (Browse)"
    },
    "input_backup_append_ph": {
        "tr": "Gözat butonuna basarak içine ekleme yapılacak mevcut yedek dosyasını (.tar.zst) seçin...",
        "en": "Click Browse to select the existing backup file (.tar.zst) to add items into..."
    },
    "btn_start_backup_append": {
        "tr": "➕ SEÇİLEN YENİ ÖGELERİ MEVCUT YEDEĞE EKLE",
        "en": "➕ ADD SELECTED NEW ITEMS INTO EXISTING BACKUP"
    },
    "picker_append_title": {
        "tr": "İçine Ekleme Yapılacak Mevcut Yedek Dosyasını Seç",
        "en": "Select Existing Backup File to Add Into"
    },
    "append_info_loaded": {
        "tr": "✅ Seçili Yedek: {name} (İçinde zaten {pkgs} Uygulama, {cfgs} Ayar, {paths} Özel Dosya var — bunlar korunur).\n💡 F2 (Uygulamalar), F3 (Ayarlar) veya F4 (Özel Dosyalar) sekmesinden SADECE bu yedeğe yeni eklemek istediğiniz ögeleri seçin.",
        "en": "✅ Selected Backup: {name} (Already contains {pkgs} Apps, {cfgs} Configs, {paths} Custom Paths — preserved).\n💡 From F2 (Apps), F3 (Configs) or F4 (Files), select ONLY the new items you want to add into this backup."
    },
    "append_info_selected": {
        "tr": "✅ Seçili Yedek: {name}\n💡 F2 (Uygulamalar), F3 (Ayarlar) veya F4 (Özel Dosyalar) sekmesinden SADECE bu yedeğe yeni eklemek istediğiniz ögeleri seçin.",
        "en": "✅ Selected Backup: {name}\n💡 From F2 (Apps), F3 (Configs) or F4 (Files), select ONLY the new items you want to add into this backup."
    },
    "append_info_empty": {
        "tr": "💡 1) 'Mevcut Yedeği Seç (Gözat)' ile eski yedeğinizi seçin.  2) F2 / F3 / F4 sekmelerinden eklemek istediğiniz yeni ögeleri seçin.  3) Aşağıdaki butona basın.",
        "en": "💡 1) Select your existing backup via 'Browse'.  2) Pick new items to add from F2 / F3 / F4 tabs.  3) Click the button below."
    },
    "append_no_items_warning": {
        "tr": "Mevcut yedeğe eklenecek hiçbir yeni öge seçmediniz! Lütfen F2 (Uygulamalar), F3 (Ayarlar) veya F4 (Özel Dosyalar) sekmesinden eklenecek ögeleri işaretleyin.",
        "en": "No new items selected to add! Please select items from F2 (Apps), F3 (Configs) or F4 (Custom Files) first."
    },
    "backup_append_prompt": {
        "tr": "Mevcut yedeğe ekleme modu açıldı. Sadece eklemek istediğiniz yeni ögeleri (F2/F3/F4) seçip işlemi başlatabilirsiniz.",
        "en": "Add-to-backup mode enabled. Select only the new items (F2/F3/F4) you wish to add and start."
    },
    "lbl_base_backup": {
        "tr": "Karşılaştırılacak Önceki Yedek Dosyası (Temel Yedek):",
        "en": "Previous Base Backup to Compare Against:"
    },
    "input_backup_base_ph": {
        "tr": "Temel yedek arşivi veya dizini yolu (örn: distronomad_backup_....tar.zst)...",
        "en": "Base backup archive or directory path (e.g. distronomad_backup_....tar.zst)..."
    },
    "cart_summary_status": {
        "tr": "Seçilen: {apps} Uygulama | {cfgs} Ayar | {paths} Klasör | ~{size}",
        "en": "Selected: {apps} Apps | {cfgs} Configs | {paths} Folders | ~{size}"
    },
    "btn_browse_base": {
        "tr": "📂 Temel Yedeği Seç (Gözat)",
        "en": "📂 Select Base Backup (Browse)"
    },
    "btn_preview_changes": {
        "tr": "🔍 Değişenleri Tara & Önizle",
        "en": "🔍 Scan & Preview Changes"
    },
    "lbl_backup_dest": {
        "tr": "Yeni Yedek Dosyası Kayıt Yolu (Hedef):",
        "en": "New Backup File Destination Path:"
    },
    "btn_browse_dest": {
        "tr": "📂 Hedef Dizin Seç (Gözat)",
        "en": "📂 Select Destination (Browse)"
    },
    "lbl_compression": {
        "tr": "Arşiv Sıkıştırma Formatı:",
        "en": "Archive Compression Format:"
    },
    "format_zst_lbl": {
        "tr": "Zstandard (.tar.zst) - Ultra Hızlı & Yüksek Sıkıştırma (Önerilen)",
        "en": "Zstandard (.tar.zst) - Ultra Fast & High Compression (Recommended)"
    },
    "format_gz_lbl": {
        "tr": "Gzip (.tar.gz) - Evrensel Standart",
        "en": "Gzip (.tar.gz) - Universal Standard"
    },
    "format_dir_lbl": {
        "tr": "Düz Klasör (Sıkıştırmasız - Harici USB İçin Hızlı Senkronizasyon)",
        "en": "Plain Directory (Uncompressed - Fast Sync for External USB)"
    },
    "lbl_advanced_opts": {
        "tr": "Gelişmiş Seçenekler ve Filtreleme:",
        "en": "Advanced Options & Filtering:"
    },
    "chk_smart_ignore_lbl": {
        "tr": "🧹 Akıllı Filtreleme (.distronomadignore, Cache, GPUCache, node_modules atla)",
        "en": "🧹 Smart Filtering (Skip Cache, GPUCache, node_modules, etc.)"
    },
    "chk_encrypt_lbl": {
        "tr": "🔐 Parola ile Şifrele (AES-256 PBKDF2)",
        "en": "🔐 Encrypt with Password (AES-256 PBKDF2)"
    },
    "input_pwd_ph": {
        "tr": "🔐 Şifreleme parolası girin...",
        "en": "🔐 Enter encryption password..."
    },
    "input_pwd_confirm_ph": {
        "tr": "🔐 Parolayı tekrar girin (onay)...",
        "en": "🔐 Confirm encryption password..."
    },
    "err_pwd_empty": {
        "tr": "Şifreleme seçildi fakat parola girilmedi!",
        "en": "Encryption selected but no password entered!"
    },
    "err_pwd_mismatch": {
        "tr": "Girilen şifreleme parolaları birbiriyle eşleşmiyor!",
        "en": "Encryption passwords do not match!"
    },
    "backup_syncing_data": {
        "tr": "Veriler diske zorla yazılıyor (sync)...",
        "en": "Flushing data to disk (sync)..."
    },
    "backup_synced_usb_safe": {
        "tr": "Veriler diske yazıldı, USB sürücüyü güvenle çıkarabilirsiniz.",
        "en": "Data has been synced to disk; you can safely remove the USB drive."
    },
    "btn_start_backup": {
        "tr": "🚀 YEDEKLEMEYİ BAŞLAT",
        "en": "🚀 START BACKUP"
    },
    "card_backup_log": {
        "tr": "YEDEKLEME DURUMU VE GÜNLÜK (LOG)",
        "en": "BACKUP STATUS & LOG"
    },

    # Geri Yükle Sekmesi
    "card_restore_select_title": {
        "tr": "1. YEDEK ARŞİVİNİ SEÇİN VE İNCELEYİN",
        "en": "1. SELECT & INSPECT BACKUP ARCHIVE"
    },
    "input_restore_src_ph": {
        "tr": "Yedek arşivi yolu (örn: /media/usb/backup.tar.zst)...",
        "en": "Backup archive path (e.g. /media/usb/backup.tar.zst)..."
    },
    "btn_inspect_restore": {
        "tr": "🔍 Yedeği İncele",
        "en": "🔍 Inspect Backup"
    },
    "btn_restore_diff": {
        "tr": "🔍 Farkları İncele (Diff)",
        "en": "🔍 Inspect Diffs"
    },
    "btn_restore_rollback": {
        "tr": "↩️ Geri Al (Rollback)",
        "en": "↩️ Undo (Rollback)"
    },
    "btn_restore_append_to_this": {
        "tr": "➕ Bu Yedeğe Yeni Ögeler Ekle",
        "en": "➕ Append Items to this Backup"
    },
    "target_system_lbl": {
        "tr": "Hedef Sistem:",
        "en": "Target System:"
    },
    "restore_no_manifest": {
        "tr": "Henüz bir yedek yüklenmedi. Yukarıdan yedeğinizi seçip 'Yedeği İncele' butonuna basınız.",
        "en": "No backup loaded yet. Please select your backup above and click 'Inspect Backup'."
    },
    "card_hw_rec_title": {
        "tr": "🖥️ DONANIM VE SÜRÜCÜ ENVANTERİ / TAVSİYELERİ",
        "en": "🖥️ HARDWARE & DRIVER INVENTORY / RECOMMENDATIONS"
    },
    "hw_rec_init_text": {
        "tr": "Canlı sistem donanımı ve hedef dağıtım için sürücü paketleri burada listelenir.",
        "en": "Live system hardware and driver recommendations for the target distribution appear here."
    },
    "chk_install_drivers_lbl": {
        "tr": "🖥️ Donanım Sürücülerini Kur",
        "en": "🖥️ Install Hardware Drivers"
    },
    "hw_rec_none": {
        "tr": "Özel sürücü gerekmiyor (Çekirdek dahili sürücüleri yeterli)",
        "en": "No special drivers required (In-kernel drivers sufficient)"
    },
    "hw_manifest_diff_notice": {
        "tr": "Yedekteki donanım profili bu makineden farklı",
        "en": "Hardware profile in backup differs from this machine"
    },
    "hw_from_backup": {
        "tr": "yedekteki",
        "en": "in backup"
    },
    "hw_rec_cpu": {
        "tr": "İşlemci",
        "en": "Processor"
    },
    "hw_rec_gpu": {
        "tr": "Ekran Kartı",
        "en": "Graphics Card"
    },
    "hw_rec_audio": {
        "tr": "Ses Alt Yapısı",
        "en": "Audio System"
    },
    "hw_rec_target_distro": {
        "tr": "Hedef Dağıtımınız ({distro} / {family}) İçin Önerilen Sürücüler:",
        "en": "Recommended Drivers For Your Target Distribution ({distro} / {family}):"
    },
    "card_restore_apps_title": {
        "tr": "2. GERİ YÜKLENECEK UYGULAMALAR & PAKETLER (TEK TEK SEÇİN)",
        "en": "2. APPS & PACKAGES TO RESTORE (SELECT INDIVIDUALLY)"
    },
    "search_restore_apps_ph": {
        "tr": "🔍 Yedekteki uygulamalarda ara...",
        "en": "🔍 Search apps in backup..."
    },
    "card_restore_configs_title": {
        "tr": "3. GERİ YÜKLENECEK KONFİGÜRASYONLAR & AYARLAR (TEK TEK SEÇİN)",
        "en": "3. CONFIGS & SETTINGS TO RESTORE (SELECT INDIVIDUALLY)"
    },
    "search_restore_configs_ph": {
        "tr": "🔍 Yedekteki ayarlarda ara...",
        "en": "🔍 Search configs in backup..."
    },
    "card_restore_custom_title": {
        "tr": "4. GERİ YÜKLENECEK ÖZEL DOSYALAR & KLASÖRLER (TEK TEK SEÇİN)",
        "en": "4. CUSTOM FILES & FOLDERS TO RESTORE (SELECT INDIVIDUALLY)"
    },
    "card_restore_safety_title": {
        "tr": "5. ÇAKIŞMA GÜVENLİĞİ VE İŞLEM SEÇENEKLERİ",
        "en": "5. CONFLICT SAFETY & RESTORE OPTIONS"
    },
    "lbl_conflict_strategy": {
        "tr": "Mevcut Dosya Çakışma Stratejisi:",
        "en": "Conflict Strategy for Existing Files:"
    },
    "conflict_backup_lbl": {
        "tr": "Mevcut dosyaları .bak olarak yedekle ve değiştir (En Güvenli)",
        "en": "Backup existing files as .bak and replace (Safest)"
    },
    "conflict_overwrite_lbl": {
        "tr": "Üzerine Yaz",
        "en": "Overwrite Existing Files"
    },
    "conflict_skip_lbl": {
        "tr": "Mevcut Olanları Atla",
        "en": "Skip Existing Files"
    },
    "chk_dry_run_lbl": {
        "tr": "🛡️ Yalnızca Simüle Et (Dry-Run Testi - Dosyalara Dokunmaz)",
        "en": "🛡️ Simulate Only (Dry-Run Test - Does not modify files)"
    },
    "chk_restore_system_state_lbl": {
        "tr": "⚙️ Sistem Durumunu Geri Yükle (dconf, crontab)",
        "en": "⚙️ Restore System State (dconf, crontab)"
    },
    "lbl_restore_ready": {
        "tr": "Seçilen ögeler geri yüklenmeye hazır.",
        "en": "Selected items are ready to restore."
    },
    "restore_count_format": {
        "tr": "Seçili: {sel} / {total}",
        "en": "Selected: {sel} / {total}"
    },
    "restore_summary_format": {
        "tr": "Seçilen: {apps} Uygulama, {cfgs} Konfigürasyon, {custom} Özel Klasör geri yüklenecek.",
        "en": "Selected: {apps} Applications, {cfgs} Configurations, {custom} Custom Folders to restore."
    },
    "btn_start_restore": {
        "tr": "⚡ SEÇİLENLERİ GERİ YÜKLE",
        "en": "⚡ RESTORE SELECTED"
    },
    "card_restore_log": {
        "tr": "6. GERİ YÜKLEME DURUMU VE GÜNLÜK (LOG)",
        "en": "6. RESTORE STATUS & LOG"
    },

    # Modallar
    "pwd_modal_title": {
        "tr": "🔐 AES-256 Parola Girişi",
        "en": "🔐 AES-256 Password Entry"
    },
    "pwd_modal_prompt": {
        "tr": "Lütfen arşiv parolasını girin:",
        "en": "Please enter archive password:"
    },
    "btn_confirm": {
        "tr": "✔ Onayla",
        "en": "✔ Confirm"
    },
    "btn_cancel": {
        "tr": "✖ İptal",
        "en": "✖ Cancel"
    },
    "btn_close": {
        "tr": "✖ Kapat",
        "en": "✖ Close"
    },
    "diff_modal_title": {
        "tr": "🔍 Yapılandırma ve Dosya Farkları (Diff Viewer)",
        "en": "🔍 Config & File Differences (Diff Viewer)"
    },
    "diff_file_list_lbl": {
        "tr": "📁 Değişen Dosyalar:",
        "en": "📁 Modified Files:"
    },
    "diff_preview_lbl": {
        "tr": "Fark Önizlemesi:",
        "en": "Diff Preview:"
    },
    "diff_total_analyzed": {
        "tr": "Toplam {count} dosya incelendi.",
        "en": "Total {count} files analyzed."
    },
    "diff_file_too_large": {
        "tr": "[Dosya çok büyük ({size_mb:.1f} MB > {limit_mb:.1f} MB) - Metin farkı görüntülenemiyor]",
        "en": "[File too large ({size_mb:.1f} MB > {limit_mb:.1f} MB) - Text diff not displayed]"
    },
    "diff_select_prompt": {
        "tr": "[cyan]Farkları görüntülemek için sol taraftaki listeden bir dosya seçin.[/cyan]",
        "en": "[cyan]Select a file from the list on the left to view differences.[/cyan]"
    },
    "diff_no_diff": {
        "tr": "[green]Hiçbir fark bulunamadı. Yedek ile sistem dosyaları birebir aynı![/green]",
        "en": "[green]No differences found. Backup and system files are identical![/green]"
    },
    "rollback_modal_title": {
        "tr": "↩️ Geri Yüklemeyi Geri Al (Rollback / Undo)",
        "en": "↩️ Undo Restore (Rollback / Undo)"
    },
    "btn_rollback_confirm": {
        "tr": "↩️ Evet, Geri Al",
        "en": "↩️ Yes, Revert"
    },
    "btn_rollback_cancel": {
        "tr": "✖ Vazgeç",
        "en": "✖ Cancel"
    },
    "auto_modal_title": {
        "tr": "⏰ Zamanlanmış Otomatik Yedekleme (systemd User Timer)",
        "en": "⏰ Scheduled Automatic Backup (systemd User Timer)"
    },
    "auto_interval_lbl": {
        "tr": "Çalışma Aralığı Seçin:",
        "en": "Select Interval:"
    },
    "auto_daily": {
        "tr": "Günlük (Her gün arka planda artımlı yedek)",
        "en": "Daily (Incremental backup in background every day)"
    },
    "auto_weekly": {
        "tr": "Haftalık (Her pazartesi artımlı yedek)",
        "en": "Weekly (Incremental backup every Monday)"
    },
    "auto_hourly": {
        "tr": "Saatlik (Geliştiriciler ve yoğun çalışanlar için)",
        "en": "Hourly (For developers & heavy users)"
    },
    "btn_auto_enable": {
        "tr": "🚀 Zamanlayıcıyı Etkinleştir",
        "en": "🚀 Enable Scheduler"
    },
    "btn_auto_disable": {
        "tr": "⏹ Zamanlayıcıyı Kapat",
        "en": "⏹ Disable Scheduler"
    },
    "fat32_warning_title": {
        "tr": "⚠️ FAT32 Dosya Boyutu Sınırı Uyarısı",
        "en": "⚠️ FAT32 File Size Limit Warning"
    },
    "fat32_archive_limit_warning": {
        "tr": "Hedef sürücü FAT32 dosya sistemine sahip. Seçilen ögelerin tahmini toplam boyutu ({size}) FAT32 tek dosya sınırına (4 GB) çok yakın veya bu sınırı aşıyor (önerilen azami arşiv boyutu: 3.5 GB).\n\nYedekleme yarıda kesilebilir ve arşiv bozulabilir.\n\nÖneri: USB belleğinizi 4 GB'dan büyük dosyaları destekleyen exFAT veya ext4 olarak biçimlendirin.\n\nYine de devam etmek istiyor musunuz?",
        "en": "Target drive has a FAT32 filesystem. The estimated total size of selected items ({size}) is close to or exceeds the FAT32 single-file limit of 4 GB (recommended archive limit: 3.5 GB).\n\nBackup may fail midway and result in a corrupted archive.\n\nRecommendation: Format your USB drive as exFAT or ext4, which support files larger than 4 GB.\n\nDo you still want to proceed?"
    },
    "fat32_single_file_limit_warning": {
        "tr": "Hedef sürücü FAT32 dosya sistemine sahip. Seçili ögeler içinde 4 GB veya üstü tek bir dosya tespit edildi:\n{file} ({size})\n\nFAT32 dosya sistemi 4 GB veya daha büyük tek bir dosyayı depolayamaz.\n\nÖneri: USB belleğinizi 4 GB'dan büyük dosyaları destekleyen exFAT veya ext4 olarak biçimlendirin.\n\nYine de devam etmek istiyor musunuz?",
        "en": "Target drive has a FAT32 filesystem. A single file equal to or exceeding 4 GB was found in selected items:\n{file} ({size})\n\nFAT32 filesystem cannot store single files that are 4 GB or larger.\n\nRecommendation: Format your USB drive as exFAT or ext4, which support files larger than 4 GB.\n\nDo you still want to proceed?"
    },
    "fat32_cli_warning": {
        "tr": "FAT32 dosya boyutu sınırı aşıldı! Yedekleme iptal edildi. USB belleğinizi exFAT veya ext4 olarak biçimlendirmeniz önerilir. Yine de denemek istiyorsanız '--force' parametresini kullanın.",
        "en": "FAT32 file size limit exceeded! Backup aborted. Recommended to format USB drive as exFAT or ext4. If you still want to proceed, use the '--force' flag."
    },
    "btn_fat32_proceed": {
        "tr": "Yine de Devam Et",
        "en": "Proceed Anyway"
    },
    "btn_fat32_cancel": {
        "tr": "İptal Et",
        "en": "Cancel"
    },
    "err_efbig_fat32_limit": {
        "tr": "Dosya boyutu sınırı aşıldı (FAT32 tek dosya sınırı 4 GB). Yarım kalan çıktı temizlendi. USB belleğinizi exFAT veya ext4 olarak biçimlendirmeniz önerilir.",
        "en": "File size limit exceeded (FAT32 single file limit is 4 GB). Incomplete output cleaned up. Recommended to format USB drive as exFAT or ext4."
    },
    "backup_aborted_by_user": {
        "tr": "Yedekleme işlemi kullanıcı tarafından iptal edildi.",
        "en": "Backup operation cancelled by user."
    },
    "btn_verify_backup": {
        "tr": "🔍 Yedeği Doğrula",
        "en": "🔍 Verify Backup"
    },
    "chk_verify_after_backup_lbl": {
        "tr": "Yedek bitince bütünlüğü doğrula",
        "en": "Verify integrity after backup"
    },
    "verify_title": {
        "tr": "Yedek Doğrulama",
        "en": "Backup Verification"
    },
    "verify_started": {
        "tr": "Yedek bütünlük doğrulaması başlatıldı: {path}",
        "en": "Backup integrity verification started: {path}"
    },
    "verify_progress": {
        "tr": "Doğrulanıyor ({processed}/{total}): {item}",
        "en": "Verifying ({processed}/{total}): {item}"
    },
    "verify_success": {
        "tr": "✔ Yedek sağlam! ({matched} dosya doğrulandı)",
        "en": "✔ Backup healthy! ({matched} files verified)"
    },
    "verify_corrupted": {
        "tr": "✖ Yedek bozuk veya hatalı! {details}",
        "en": "✖ Backup corrupted or invalid! {details}"
    },
    "verify_summary_matched": {
        "tr": "Eşleşen (Sağlam)",
        "en": "Matched (Healthy)"
    },
    "verify_summary_mismatched": {
        "tr": "Uyuşmayan (Bozuk)",
        "en": "Mismatched (Corrupted)"
    },
    "verify_summary_missing": {
        "tr": "Arşivde Eksik",
        "en": "Missing in Archive"
    },
    "verify_summary_untracked": {
        "tr": "Manifestte Olmayan",
        "en": "Untracked (Extra)"
    },
    "verify_summary_skipped": {
        "tr": "Atlanan (Sembolik Bağ)",
        "en": "Skipped (Symlink)"
    },
    "verify_chain_base_missing": {
        "tr": "Taban yedek bulunamadı: '{name}'. Taban katmandaki dosyaların bütünlüğü doğrulanamadı.",
        "en": "Base backup not found: '{name}'. Integrity of base layer files could not be verified."
    },
    "verify_chain_verified": {
        "tr": "Taban yedek zinciri doğrulandı: {name}",
        "en": "Base backup chain verified: {name}"
    },
    "verify_btn_in_progress": {
        "tr": "⏳ Doğrulanıyor...",
        "en": "⏳ Verifying..."
    },
    "confirm_quit_title": {
        "tr": "⚠️ Aktif İşlem Uyarısı",
        "en": "⚠️ Active Operation Warning"
    },
    "confirm_quit_prompt": {
        "tr": "Aktif bir işlem (yedekleme, geri yükleme, doğrulama veya geri alma) halen devam ediyor. Çıkmak istediğinize emin misiniz? İşlemin yarıda kesilmesi veri kaybına veya bozulmaya yol açabilir.",
        "en": "An active operation (backup, restore, verify, or rollback) is currently in progress. Are you sure you want to quit? Interrupting may cause data loss or corruption."
    },
    "btn_quit_confirm": {
        "tr": "Evet, Çık",
        "en": "Yes, Quit"
    },
    "btn_quit_cancel": {
        "tr": "İptal (Devam Et)",
        "en": "Cancel (Continue)"
    },
    "diff_btn_in_progress": {
        "tr": "⏳ Hesaplanıyor...",
        "en": "⏳ Analyzing..."
    },
    "diff_in_progress_notify": {
        "tr": "Yedek ile sistem dosyaları arasındaki farklar hesaplanıyor...",
        "en": "Calculating differences between backup and host files..."
    },
    "diff_failed_notify": {
        "tr": "Fark analizi başarısız",
        "en": "Diff analysis failed"
    },
    "chk_allow_external_paths_lbl": {
        "tr": "Ev dizini dışındaki yolları da geri yükle (custom/root)",
        "en": "Also restore paths outside home directory (custom/root)"
    },
    "restore_skipped_external_paths": {
        "tr": "Atlanan ev dışı yollar ({count} adet):",
        "en": "Skipped external paths ({count} items):"
    },
    "backup_completed_with_skipped": {
        "tr": "Yedek tamamlandı ancak {count} dosya okunamadı",
        "en": "Backup completed but {count} files could not be read"
    },
    "backup_skipped_files_header": {
        "tr": "Atlanan Dosyalar ({count} adet):",
        "en": "Skipped Files ({count} items):"
    },
    "sudo_terminal_prompt": {
        "tr": "DistroNomad: Paket kurulumu için sudo yetkisi gerekiyor. Lütfen parolanızı girin:",
        "en": "DistroNomad: Sudo privileges required for package installation. Please enter password:"
    },
    "sudo_failed_skip_pkgs": {
        "tr": "Sudo yetkisi alınamadı veya askıya alma desteklenmiyor; paket kurulumu atlandı, dosya geri yüklemesi devam ediyor.",
        "en": "Sudo privileges could not be obtained or suspend unsupported; skipping package installation, continuing file restore."
    },
    "notify_no_items_left_to_restore": {
        "tr": "Paket kurulumu atlandıktan sonra geri yüklenecek başka öge kalmadı.",
        "en": "No remaining items to restore after skipping package installation."
    },
    "verify_reason_label": {
        "tr": "Başarısızlık Nedeni",
        "en": "Failure Reason"
    },

    # CLI - Genel & Ayrıştırıcı
    "cli_app_desc": {
        "tr": "DistroNomad - Linux Dağıtım Taşıma ve Konfigürasyon Yöneticisi",
        "en": "DistroNomad - Linux Distribution Migration and Configuration Manager"
    },
    "cli_epilog_main": {
        "tr": "Çıkış Kodları:\n  0: İşlem başarıyla tamamlandı\n  1: Hata veya başarısız işlem\n  2: Geçersiz komut satırı argümanları\n  3: Yedekleme tamamlandı ancak bazı dosyalar okunamadı / atlandı",
        "en": "Exit Codes:\n  0: Operation completed successfully\n  1: Error or failed operation\n  2: Invalid command line arguments\n  3: Backup completed but some files could not be read / were skipped"
    },
    "cli_epilog_backup": {
        "tr": "Çıkış Kodları:\n  0: Yedekleme eksiksiz tamamlandı\n  1: Hata veya başarısız işlem\n  3: Yedekleme tamamlandı ancak bazı dosyalar okunamadı / atlandı (eksik)",
        "en": "Exit Codes:\n  0: Backup completed successfully\n  1: Error or failed operation\n  3: Backup completed but some files could not be read / were skipped (incomplete)"
    },
    "cli_epilog_append": {
        "tr": "Çıkış Kodları:\n  0: Ekleme eksiksiz tamamlandı\n  1: Hata veya başarısız işlem\n  3: Ekleme tamamlandı ancak bazı dosyalar okunamadı / atlandı (eksik)",
        "en": "Exit Codes:\n  0: Append completed successfully\n  1: Error or failed operation\n  3: Append completed but some files could not be read / were skipped (incomplete)"
    },
    "cli_opt_home": {
        "tr": "Özel ev dizini (test için)",
        "en": "Custom home directory (for testing)"
    },
    "cli_opt_lang": {
        "tr": "Arayüz dili (tr: Türkçe, en: English)",
        "en": "Interface language (tr: Türkçe, en: English)"
    },
    "cli_cmd_tui_help": {
        "tr": "Görsel Terminal Arayüzünü (TUI) başlat",
        "en": "Launch Graphical Terminal User Interface (TUI)"
    },
    "cli_cmd_scan_help": {
        "tr": "Sistemdeki paketleri ve ayarları tara ve raporla",
        "en": "Scan and report system packages and configurations"
    },
    "cli_cmd_backup_help": {
        "tr": "Komut satırından yedek al",
        "en": "Create backup from command line"
    },
    "cli_cmd_append_help": {
        "tr": "Var olan bir yedek arşivine yeni uygulama, ayar veya dosya ekle",
        "en": "Append new applications, configurations or files to an existing backup"
    },
    "cli_cmd_restore_help": {
        "tr": "Komut satırından yedeği geri yükle",
        "en": "Restore backup from command line"
    },
    "cli_cmd_diff_help": {
        "tr": "Yedek ile mevcut sistem arasındaki ayar farklarını incele",
        "en": "Inspect configuration differences between backup and host system"
    },
    "cli_cmd_rollback_help": {
        "tr": "Son geri yükleme işlemini geri al (Rollback / Undo)",
        "en": "Roll back last restore operation (Undo)"
    },
    "cli_cmd_schedule_help": {
        "tr": "Zamanlanmış otomatik yedekleme ayarlarını yönet (systemd user timer)",
        "en": "Manage scheduled automatic backup settings (systemd user timer)"
    },
    "cli_cmd_verify_help": {
        "tr": "Yedek arşivinin veya dizininin bütünlüğünü doğrula",
        "en": "Verify integrity of backup archive or directory"
    },

    # CLI - Ortak Hata & Sütunlar
    "cli_err_prefix": {
        "tr": "HATA: {error}",
        "en": "ERROR: {error}"
    },
    "cli_col_name": {
        "tr": "Ad",
        "en": "Name"
    },
    "cli_col_category": {
        "tr": "Kategori",
        "en": "Category"
    },
    "cli_col_rel_path": {
        "tr": "Göreli Yol",
        "en": "Relative Path"
    },
    "cli_col_size": {
        "tr": "Boyut",
        "en": "Size"
    },
    "cli_col_item_count": {
        "tr": "Öğe Sayısı",
        "en": "Item Count"
    },
    "cli_col_pkg_name": {
        "tr": "Paket / Uygulama Adı",
        "en": "Package / Application Name"
    },
    "cli_col_manager": {
        "tr": "Yönetici",
        "en": "Manager"
    },
    "cli_col_version": {
        "tr": "Sürüm",
        "en": "Version"
    },
    "cli_col_label": {
        "tr": "Etiket",
        "en": "Label"
    },
    "cli_col_path": {
        "tr": "Yol",
        "en": "Path"
    },
    "cli_col_file_count": {
        "tr": "Dosya Sayısı",
        "en": "File Count"
    },
    "cli_col_status": {
        "tr": "Durum",
        "en": "Status"
    },
    "cli_col_added": {
        "tr": "Eklenen (+)",
        "en": "Added (+)"
    },
    "cli_col_removed": {
        "tr": "Silinen (-)",
        "en": "Removed (-)"
    },
    "cli_col_prop": {
        "tr": "Özellik / Kategori",
        "en": "Property / Category"
    },
    "cli_col_result": {
        "tr": "Sonuç / Değer",
        "en": "Result / Value"
    },

    # CLI - Scan
    "cli_scan_report_title": {
        "tr": "Sistem Tarama Raporu",
        "en": "System Scan Report"
    },
    "cli_scan_distro": {
        "tr": "Dağıtım:",
        "en": "Distribution:"
    },
    "cli_scan_kernel": {
        "tr": "Çekirdek:",
        "en": "Kernel:"
    },
    "cli_scan_desktop": {
        "tr": "Masaüstü:",
        "en": "Desktop:"
    },
    "cli_scan_home": {
        "tr": "Ev Dizini:",
        "en": "Home Directory:"
    },
    "cli_scan_pms": {
        "tr": "Tespit Edilen Paket Yöneticileri:",
        "en": "Detected Package Managers:"
    },
    "cli_scan_pms_none": {
        "tr": "Yok",
        "en": "None"
    },
    "cli_scan_panel_title": {
        "tr": "SİSTEM BİLGİSİ",
        "en": "SYSTEM INFORMATION"
    },
    "cli_scan_configs_title": {
        "tr": "Tespit Edilen Konfigürasyonlar ve Dotfile'lar",
        "en": "Detected Configurations and Dotfiles"
    },
    "cli_scan_configs_more": {
        "tr": "... ve {count} ek konfigürasyon daha tespit edildi.",
        "en": "... and {count} more configurations detected."
    },
    "cli_scan_packages_title": {
        "tr": "Tespit Edilen Kurulu Uygulamalar ve Paketler",
        "en": "Detected Installed Applications and Packages"
    },
    "cli_scan_packages_more": {
        "tr": "... ve {count} ek paket daha tespit edildi.",
        "en": "... and {count} more packages detected."
    },
    "cli_scan_custom_title": {
        "tr": "Önerilen Kişisel Dizinler",
        "en": "Suggested Personal Directories"
    },

    # CLI - Backup
    "cli_opt_backup_dest": {
        "tr": "Yedek dosyasının yazılacağı yol (örn: /media/usb/backup.tar.zst)",
        "en": "Target path for backup file (e.g. /media/usb/backup.tar.zst)"
    },
    "cli_opt_backup_format": {
        "tr": "Yedek formatı",
        "en": "Backup format"
    },
    "cli_opt_configs_only": {
        "tr": "Yalnızca konfigürasyonları yedekle",
        "en": "Back up configurations only"
    },
    "cli_opt_no_sensitive": {
        "tr": "SSH ve GPG gibi hassas anahtarları dahil etme",
        "en": "Exclude sensitive keys such as SSH and GPG"
    },
    "cli_opt_incremental": {
        "tr": "Artımlı yedek al (yalnızca değişenleri yedekle)",
        "en": "Create incremental backup (back up changed files only)"
    },
    "cli_opt_append": {
        "tr": "Var olan bir yedek arşivine yeni uygulama, ayar veya dosya ekle / güncelle",
        "en": "Append/update new applications, settings or files to existing backup"
    },
    "cli_opt_backup_base": {
        "tr": "Karşılaştırılacak temel yedek arşivi veya dizini",
        "en": "Base backup archive or directory for comparison"
    },
    "cli_opt_password": {
        "tr": "AES-256 şifreleme parolası (değersiz verilirse sorulur; DISTRONOMAD_PASSWORD desteklenir)",
        "en": "AES-256 encryption password (prompts if value omitted; supports DISTRONOMAD_PASSWORD)"
    },
    "cli_password_cli_warning": {
        "tr": "Uyarı: Parola komut satırında iletildi; işlem listesi (ps) ve kabuk geçmişinde görünebilir. Etkileşimli giriş için parametreyi değersiz kullanın veya DISTRONOMAD_PASSWORD ortam değişkenini tercih edin.",
        "en": "Warning: Password was provided on the command line; it may be visible in process listings (ps) and shell history. Use the option without a value for interactive prompt or set the DISTRONOMAD_PASSWORD environment variable."
    },
    "cli_password_prompt": {
        "tr": "Parola: ",
        "en": "Password: "
    },
    "cli_password_confirm_prompt": {
        "tr": "Parolayı tekrar girin: ",
        "en": "Confirm password: "
    },
    "cli_password_err_mismatch": {
        "tr": "Parolalar eşleşmiyor.",
        "en": "Passwords do not match."
    },
    "cli_password_err_empty": {
        "tr": "Parola boş olamaz.",
        "en": "Password cannot be empty."
    },
    "cli_password_err_non_terminal": {
        "tr": "Şifreli işlem için parola gerekli ancak stdin bir terminal değil. DISTRONOMAD_PASSWORD ortam değişkenini kullanın veya terminalden çalıştırın.",
        "en": "Password required for encrypted operation but stdin is not a terminal. Use the DISTRONOMAD_PASSWORD environment variable or run from a terminal."
    },
    "cli_password_err_aborted": {
        "tr": "İşlem kullanıcı tarafından iptal edildi.",
        "en": "Operation aborted by user."
    },
    "cli_opt_no_ignore": {
        "tr": "Akıllı önbellek ve çöp filtrelerini kapat",
        "en": "Disable smart cache and junk file filters"
    },
    "cli_opt_force": {
        "tr": "FAT32 dosya boyutu sınırı uyarılarını yoksay ve yedeklemeye zorla",
        "en": "Ignore FAT32 file size limit warnings and force backup"
    },
    "cli_fat32_dir_unsupported": {
        "tr": "FAT32 dosya sisteminde dizin formatı desteklenmez. Lütfen arşiv formatını (--format tar.zst) kullanın veya --force ile devam edin.",
        "en": "Directory format is not supported on FAT32 filesystems. Please use archive format (--format tar.zst) or continue with --force."
    },
    "cli_fat32_force_dir_proceed": {
        "tr": "--force bayrağı belirtildiği için işleme devam ediliyor (sembolik bağlar atlanabilir)...",
        "en": "--force flag specified, continuing operation (symlinks may be skipped)..."
    },
    "cli_fat32_force_proceed": {
        "tr": "--force bayrağı belirtildiği için işleme devam ediliyor...",
        "en": "--force flag specified, continuing operation..."
    },
    "cli_backup_starting": {
        "tr": "Yedekleme başlatılıyor: {dest} ({format})",
        "en": "Starting backup: {dest} ({format})"
    },
    "cli_backup_completed": {
        "tr": "Yedekleme tamamlandı: {dest}",
        "en": "Backup completed: {dest}"
    },
    "cli_backup_skipped_warning": {
        "tr": "Uyarı: {count} dosya okunamadığı veya atlandığı için yedek eksik tamamlandı:",
        "en": "Warning: Backup completed incompletely as {count} files could not be read or were skipped:"
    },
    "cli_skipped_files_more": {
        "tr": "... ve {count} dosya daha.",
        "en": "... and {count} more files."
    },

    # CLI - Append
    "cli_opt_append_backup": {
        "tr": "Güncellenecek mevcut yedek arşivi veya dizini yolu",
        "en": "Path to existing backup archive or directory to update"
    },
    "cli_opt_append_path": {
        "tr": "Yedek arşivine eklenecek özel dosya veya klasör yolu (yinelenebilir)",
        "en": "Custom file or folder path to add to backup archive (repeatable)"
    },
    "cli_opt_append_config": {
        "tr": "Yedek arşivine eklenecek konfigürasyon yolu (ev dizinine göre veya mutlak, yinelenebilir)",
        "en": "Configuration path to add to backup archive (relative to home or absolute, repeatable)"
    },
    "cli_opt_append_package": {
        "tr": "Yedek arşivine eklenecek paket adı veya YÖNETİCİ:AD (yinelenebilir)",
        "en": "Package name or MANAGER:NAME to add to backup archive (repeatable)"
    },
    "cli_opt_append_configs_only": {
        "tr": "Yalnızca konfigürasyonları ekle",
        "en": "Add configurations only"
    },
    "cli_opt_append_no_sensitive": {
        "tr": "Hassas anahtarları dahil etme",
        "en": "Exclude sensitive keys"
    },
    "cli_opt_append_password": {
        "tr": "Şifrelenmiş yedeğin parolası",
        "en": "Password for encrypted backup"
    },
    "cli_opt_append_force": {
        "tr": "FAT32 dosya boyutu sınırı uyarılarını yoksay ve güncellemeye zorla",
        "en": "Ignore FAT32 file size limit warnings and force update"
    },
    "cli_append_err_no_selection": {
        "tr": "HATA: Eklenecek en az bir --path, --config veya --package belirtilmelidir.",
        "en": "ERROR: At least one --path, --config or --package must be specified."
    },
    "cli_append_example_title": {
        "tr": "Örnek kullanım:",
        "en": "Example usage:"
    },
    "cli_append_err_backup_not_found": {
        "tr": "HATA: Güncellenecek yedek arşivi veya dizini bulunamadı: {path}",
        "en": "ERROR: Target backup archive or directory not found: {path}"
    },
    "cli_append_warn_unmatched_config": {
        "tr": "Eşleşmeyen konfigürasyon yolu: {query}",
        "en": "Unmatched configuration path: {query}"
    },
    "cli_append_warn_unmatched_package": {
        "tr": "Eşleşmeyen paket adı: {query}",
        "en": "Unmatched package name: {query}"
    },
    "cli_append_err_no_items_matched": {
        "tr": "HATA: Eklenecek hiçbir geçerli veya eşleşen öge bulunamadı.",
        "en": "ERROR: No valid or matching items found to append."
    },
    "cli_append_starting": {
        "tr": "Yedek arşivine ekleme başlatılıyor: {dest}",
        "en": "Starting append to backup archive: {dest}"
    },
    "cli_append_completed": {
        "tr": "Ekleme tamamlandı: {dest}",
        "en": "Append completed: {dest}"
    },
    "cli_append_skipped_warning": {
        "tr": "Uyarı: {count} dosya okunamadığı veya atlandığı için ekleme eksik tamamlandı:",
        "en": "Warning: Append completed incompletely as {count} files could not be read or were skipped:"
    },

    # CLI - Restore
    "cli_opt_restore_src": {
        "tr": "Yedek dosyası veya dizini yolu",
        "en": "Path to backup file or directory"
    },
    "cli_opt_restore_base": {
        "tr": "Artımlı yedek için temel yedek arşivi veya dizini",
        "en": "Base backup archive or directory for incremental backup"
    },
    "cli_opt_restore_target_home": {
        "tr": "Hedef kullanıcı ev dizini",
        "en": "Target user home directory"
    },
    "cli_opt_restore_conflict": {
        "tr": "Çakışma stratejisi",
        "en": "Conflict resolution strategy"
    },
    "cli_opt_restore_dry_run": {
        "tr": "Gerçek değişiklik yapmadan test et",
        "en": "Test without making real modifications"
    },
    "cli_opt_restore_configs_only": {
        "tr": "Yalnızca konfigürasyonları yükle",
        "en": "Restore configurations only"
    },
    "cli_opt_restore_packages_only": {
        "tr": "Yalnızca paketleri kur",
        "en": "Install packages only"
    },
    "cli_opt_restore_system_state": {
        "tr": "Sistem durumunu (dconf, crontab vb.) geri yükle (varsayılan: kapalı)",
        "en": "Restore system state (dconf, crontab, etc.) (default: off)"
    },
    "cli_opt_restore_install_drivers": {
        "tr": "Canlı donanım taramasına göre önerilen sürücüleri kur (varsayılan: kapalı)",
        "en": "Install recommended drivers based on live hardware scan (default: off)"
    },
    "cli_opt_restore_password": {
        "tr": "Şifrelenmiş yedeği açmak için parola",
        "en": "Password to decrypt backup"
    },
    "cli_opt_restore_allow_external": {
        "tr": "Ev dizini dışındaki özel yolları da sisteme geri yükle (açık onay gerektirir)",
        "en": "Restore custom paths outside home directory to system as well (requires explicit confirmation)"
    },
    "cli_restore_err_src_not_found": {
        "tr": "HATA: Yedek dosyası bulunamadı: {src}",
        "en": "ERROR: Backup file not found: {src}"
    },
    "cli_restore_inspecting": {
        "tr": "Yedek inceleniyor: {src}",
        "en": "Inspecting backup: {src}"
    },
    "cli_restore_manifest_loaded": {
        "tr": "Manifest yüklendi. Kaynak: {distro} ({created})",
        "en": "Manifest loaded. Source: {distro} ({created})"
    },
    "cli_restore_completed": {
        "tr": "Geri yükleme tamamlandı!",
        "en": "Restore completed!"
    },
    "cli_restore_stat_configs": {
        "tr": "Yüklenen Ayarlar: {count}",
        "en": "Restored Configurations: {count}"
    },
    "cli_restore_stat_files": {
        "tr": "Yüklenen Dosyalar: {count}",
        "en": "Restored Files: {count}"
    },
    "cli_restore_stat_packages": {
        "tr": "Planlanan/Kurulan Paketler: {count}",
        "en": "Planned/Installed Packages: {count}"
    },
    "cli_restore_failed_packages": {
        "tr": "Başarısız Paketler ({count}):",
        "en": "Failed Packages ({count}):"
    },
    "cli_restore_untranslated_packages": {
        "tr": "Çevrilemeyen Paketler ({count}):",
        "en": "Untranslated Packages ({count}):"
    },
    "cli_restore_skipped_external_paths": {
        "tr": "Atlanan Ev Dışı Özel Yollar ({count}):",
        "en": "Skipped External Paths ({count}):"
    },
    "cli_restore_tx_log": {
        "tr": "Geri Alma (Rollback) Kaydı:",
        "en": "Rollback Transaction Log:"
    },
    "cli_restore_sudo_warning": {
        "tr": "Paket kurulumu için yetki sağlanamadı. Lütfen önce terminalde 'sudo -v' çalıştırarak yetkiyi yenileyin veya yalnızca ayarları geri yüklemek için '--configs-only' seçeneğini kullanın.",
        "en": "Privileges could not be obtained for package installation. Please run 'sudo -v' in terminal to refresh authorization, or use '--configs-only' to restore only configurations."
    },

    # CLI - Rollback
    "cli_opt_rollback_tx": {
        "tr": "Özel işlem kaydı JSON dosyası yolu",
        "en": "Path to custom transaction log JSON file"
    },
    "cli_opt_rollback_target_home": {
        "tr": "Geri almanın uygulanacağı hedef kullanıcı ev dizini",
        "en": "Target user home directory where rollback is applied"
    },
    "cli_rollback_err_no_tx": {
        "tr": "Rollback Hatası: Geri alma işlemi için işlem kaydı (transaction log) bulunamadı: {path}",
        "en": "Rollback Error: Transaction log not found for rollback operation: {path}"
    },
    "cli_rollback_err_read_tx": {
        "tr": "Rollback Hatası: İşlem kaydı okunamadı: {error}",
        "en": "Rollback Error: Could not read transaction log: {error}"
    },
    "cli_rollback_err_home_mismatch": {
        "tr": "Rollback Hatası: İşlem günlüğündeki hedef ev dizini ({log_home}) ile verilen/mevcut hedef ev dizini ({current_home}) uyuşmuyor!",
        "en": "Rollback Error: Target home directory in transaction log ({log_home}) does not match specified/current target home ({current_home})!"
    },
    "cli_rollback_completed": {
        "tr": "Geri alma (Rollback) işlemi tamamlandı!",
        "en": "Rollback operation completed!"
    },
    "cli_rollback_stat_reverted": {
        "tr": "Eski Haline Getirilen Dosyalar: {count}",
        "en": "Reverted Files: {count}"
    },
    "cli_rollback_stat_removed": {
        "tr": "Kaldırılan Yeni Dosyalar: {count}",
        "en": "Removed Created Files: {count}"
    },
    "cli_rollback_unrecoverable": {
        "tr": "Geri Alınamayan Dosyalar ({count}):",
        "en": "Unrecoverable Files ({count}):"
    },
    "cli_rollback_errors": {
        "tr": "Hatalar ({count}):",
        "en": "Errors ({count}):"
    },
    "cli_rollback_err_prefix": {
        "tr": "Rollback Hatası: {error}",
        "en": "Rollback Error: {error}"
    },

    # CLI - Diff
    "cli_opt_diff_src": {
        "tr": "Yedek dosyası veya dizini yolu",
        "en": "Path to backup file or directory"
    },
    "cli_opt_diff_target_home": {
        "tr": "Karşılaştırılacak hedef ev dizini",
        "en": "Target home directory for comparison"
    },
    "cli_opt_diff_password": {
        "tr": "Şifrelenmiş yedeğin parolası",
        "en": "Password for encrypted backup"
    },
    "cli_opt_diff_show_content": {
        "tr": "Değişen dosyaların tam diff içeriğini göster",
        "en": "Show full diff content for modified files"
    },
    "cli_diff_err_src_not_found": {
        "tr": "HATA: Yedek dosyası bulunamadı: {src}",
        "en": "ERROR: Backup file not found: {src}"
    },
    "cli_diff_report_title": {
        "tr": "Dosya Karşılaştırma Raporu ({name})",
        "en": "File Comparison Report ({name})"
    },
    "cli_diff_status_new": {
        "tr": "Yeni",
        "en": "New"
    },
    "cli_diff_status_modified": {
        "tr": "Değişmiş",
        "en": "Modified"
    },
    "cli_diff_status_identical": {
        "tr": "Aynı",
        "en": "Identical"
    },
    "cli_diff_status_binary": {
        "tr": "İkili",
        "en": "Binary"
    },

    # CLI - Schedule
    "cli_opt_schedule_enable": {
        "tr": "Otomatik zamanlayıcıyı etkinleştir",
        "en": "Enable automatic timer"
    },
    "cli_opt_schedule_disable": {
        "tr": "Otomatik zamanlayıcıyı kapat",
        "en": "Disable automatic timer"
    },
    "cli_opt_schedule_interval": {
        "tr": "Çalışma periyodu",
        "en": "Execution interval"
    },
    "cli_opt_schedule_dest": {
        "tr": "Otomatik yedek hedef dizini",
        "en": "Target directory for automatic backups"
    },
    "cli_schedule_err_disable": {
        "tr": "HATA: {message}",
        "en": "ERROR: {message}"
    },
    "cli_schedule_fail_disable_default": {
        "tr": "Zamanlayıcı devre dışı bırakılamadı.",
        "en": "Timer could not be disabled."
    },
    "cli_schedule_err_enable": {
        "tr": "HATA: Otomatik zamanlayıcı etkinleştirilemedi.",
        "en": "ERROR: Automatic timer could not be enabled."
    },
    "cli_schedule_enabled": {
        "tr": "Zamanlayıcı kuruldu ve etkinleştirildi!",
        "en": "Timer installed and enabled!"
    },
    "cli_schedule_stat_interval": {
        "tr": "Aralık: {interval} ({calendar})",
        "en": "Interval: {interval} ({calendar})"
    },
    "cli_schedule_stat_dest": {
        "tr": "Hedef Dizin: {dest}",
        "en": "Target Directory: {dest}"
    },
    "cli_schedule_stat_service": {
        "tr": "systemd Servis: {path}",
        "en": "systemd Service: {path}"
    },
    "cli_schedule_stat_timer": {
        "tr": "systemd Timer: {path}",
        "en": "systemd Timer: {path}"
    },
    "cli_schedule_active": {
        "tr": "Aktif",
        "en": "Active"
    },
    "cli_schedule_disabled": {
        "tr": "Devre Dışı (Kapalı)",
        "en": "Disabled (Off)"
    },
    "cli_schedule_installed": {
        "tr": "Kurulu",
        "en": "Installed"
    },
    "cli_schedule_not_installed": {
        "tr": "Kurulu Değil",
        "en": "Not Installed"
    },
    "cli_schedule_panel_title": {
        "tr": "Otomatik Yedekleme Durumu",
        "en": "Automatic Backup Status"
    },
    "cli_schedule_lbl_status": {
        "tr": "Durum:",
        "en": "Status:"
    },
    "cli_schedule_lbl_installed": {
        "tr": "Kurulum:",
        "en": "Installation:"
    },
    "cli_schedule_lbl_interval": {
        "tr": "Çalışma Aralığı:",
        "en": "Interval:"
    },
    "cli_schedule_lbl_timer_file": {
        "tr": "Timer Dosyası:",
        "en": "Timer File:"
    },
    "cli_schedule_hint_enable": {
        "tr": "Etkinleştirmek için: distronomad schedule --enable --interval daily",
        "en": "To enable: distronomad schedule --enable --interval daily"
    },
    "cli_schedule_hint_disable": {
        "tr": "Kapatmak için:       distronomad schedule --disable",
        "en": "To disable: distronomad schedule --disable"
    },

    # CLI - Verify
    "cli_opt_verify_src": {
        "tr": "Doğrulanacak yedek dosyası veya dizini yolu",
        "en": "Path to backup file or directory to verify"
    },
    "cli_opt_verify_base": {
        "tr": "Artımlı yedek için temel yedek arşivi veya dizini",
        "en": "Base backup archive or directory for incremental backup"
    },
    "cli_opt_verify_password": {
        "tr": "Şifrelenmiş yedeği açmak için parola",
        "en": "Password to decrypt backup"
    },
    "cli_verify_err_not_found": {
        "tr": "HATA: Yedek dosyası veya dizini bulunamadı: {src}",
        "en": "ERROR: Backup file or directory not found: {src}"
    },
    "cli_verify_starting": {
        "tr": "Yedek bütünlüğü doğrulanıyor: {src}",
        "en": "Verifying backup integrity: {src}"
    },
    "cli_verify_err_prefix": {
        "tr": "HATA: {error}",
        "en": "ERROR: {error}"
    },
    "cli_verify_err_verify_failed": {
        "tr": "Doğrulama Hatası: {error}",
        "en": "Verification Error: {error}"
    },
    "cli_verify_table_title": {
        "tr": "Yedek Bütünlük Raporu: {name}",
        "en": "Backup Integrity Report: {name}"
    },
    "cli_verify_row_location": {
        "tr": "Yedek Konumu",
        "en": "Backup Location"
    },
    "cli_verify_row_archive_status": {
        "tr": "Arşiv Durumu",
        "en": "Archive Status"
    },
    "cli_verify_status_intact": {
        "tr": "Sağlam (Akış Hatasız)",
        "en": "Intact (Stream Without Error)"
    },
    "cli_verify_status_corrupted": {
        "tr": "Bozuk / Kesilmiş ({details})",
        "en": "Corrupted / Truncated ({details})"
    },
    "cli_verify_status_stream_error": {
        "tr": "Akış hatası",
        "en": "Stream error"
    },
    "cli_verify_row_total_checked": {
        "tr": "İncelenen Dosya Sayısı",
        "en": "Total Files Checked"
    },
    "cli_verify_row_matched": {
        "tr": "Eşleşen (Sağlam)",
        "en": "Matched (Intact)"
    },
    "cli_verify_row_mismatched": {
        "tr": "Uyuşmayan (Bozulmuş)",
        "en": "Mismatched (Corrupted)"
    },
    "cli_verify_row_missing": {
        "tr": "Arşivde Eksik",
        "en": "Missing in Archive"
    },
    "cli_verify_row_untracked": {
        "tr": "Manifestte Olmayan (Fazlalık)",
        "en": "Untracked in Manifest (Extra)"
    },
    "cli_verify_row_skipped": {
        "tr": "Atlanan (Sembolik Bağ vb.)",
        "en": "Skipped (Symlink etc.)"
    },
    "cli_verify_row_chain_layer": {
        "tr": "Taban Zinciri Katmanı #{idx}",
        "en": "Base Chain Layer #{idx}"
    },
    "cli_verify_chain_status_ok": {
        "tr": "Sağlam",
        "en": "Intact"
    },
    "cli_verify_chain_status_bad": {
        "tr": "Sorunlu",
        "en": "Problematic"
    },
    "cli_verify_chain_matched_files": {
        "tr": "({count} dosya)",
        "en": "({count} files)"
    },
    "cli_verify_warning_prefix": {
        "tr": "Uyarı:",
        "en": "Warning:"
    },
    "cli_verify_hdr_mismatched": {
        "tr": "Uyuşmayan Dosyalar (Hash Değeri Değişmiş):",
        "en": "Mismatched Files (Hash Changed):"
    },
    "cli_verify_hdr_missing": {
        "tr": "Arşivde Eksik Dosyalar:",
        "en": "Missing Files in Archive:"
    },
    "cli_verify_hdr_untracked": {
        "tr": "Manifestte Olmayan Dosyalar:",
        "en": "Files Not in Manifest:"
    },
    "cli_verify_success": {
        "tr": "Yedek bütünlüğü doğrulandı! Arşivdeki tüm dosyalar sağlam.",
        "en": "Backup integrity verified! All files in archive are intact."
    },
    "cli_verify_fail_mismatched": {
        "tr": "{count} dosyanın hash değeri uyuşmuyor",
        "en": "Hash values of {count} files do not match"
    },
    "cli_verify_fail_missing": {
        "tr": "{count} dosya arşivde eksik",
        "en": "{count} files are missing in archive"
    },
    "cli_verify_fail_untracked": {
        "tr": "{count} dosya manifestte yer almıyor",
        "en": "{count} files are not in manifest"
    },
    "cli_verify_fail_chain_invalid": {
        "tr": "Taban zinciri #{idx} ({name}) geçersiz: {reason}",
        "en": "Base chain #{idx} ({name}) invalid: {reason}"
    },
    "cli_verify_fail_chain_layer_invalid": {
        "tr": "zincir katmanı geçersiz",
        "en": "chain layer is invalid"
    },
    "cli_verify_fail_default": {
        "tr": "Bütünlük doğrulaması başarısız",
        "en": "Integrity verification failed"
    },
    "cli_verify_fail_summary": {
        "tr": "Yedek bütünlük doğrulaması BAŞARISIZ oldu: {reasons}!",
        "en": "Backup integrity verification FAILED: {reasons}!"
    },

    # --- Motor Modülleri (W3a-2) ---
    # archive.py
    "archive_err_unknown_format": {
        "tr": "Tanınmayan veya desteklenmeyen yedek formatı: {path}",
        "en": "Unrecognized or unsupported backup format: {path}"
    },
    "archive_err_zstd_required": {
        "tr": "Zstandard arşivi açmak için zstandard kütüphanesi veya tar/zstd gereklidir.",
        "en": "The zstandard library or tar/zstd is required to extract a Zstandard archive."
    },
    "archive_err_unsupported_format": {
        "tr": "Desteklenmeyen arşiv formatı: {path}",
        "en": "Unsupported archive format: {path}"
    },
    "archive_err_file_not_found": {
        "tr": "Arşiv dosyası bulunamadı: {path}",
        "en": "Archive file not found: {path}"
    },
    "archive_err_password_required": {
        "tr": "Bu yedek dosyası AES-256 ile şifrelenmiştir. Lütfen parola belirtin (--password).",
        "en": "This backup file is encrypted with AES-256. Please specify a password (--password)."
    },
    "archive_err_openssl_required": {
        "tr": "Şifre çözme işlemi için sistemde 'openssl' komutu bulunmalıdır.",
        "en": "The 'openssl' command must be installed on the system for decryption."
    },
    "archive_err_decrypt_init_failed": {
        "tr": "Şifre çözme başlatılamadı: {error}",
        "en": "Failed to initialize decryption: {error}"
    },
    "archive_err_bad_password_or_corrupt": {
        "tr": "Hatalı parola veya bozulmuş şifreli arşiv!",
        "en": "Incorrect password or corrupted encrypted archive!"
    },
    "archive_err_empty_decrypt_output": {
        "tr": "Şifre çözme çıktısı boş: {error}",
        "en": "Decryption output is empty: {error}"
    },
    "archive_err_zstd_not_installed": {
        "tr": "zstandard kütüphanesi yüklü değil, .tar.zst arşivi açılamıyor.",
        "en": "The zstandard library is not installed, unable to open .tar.zst archive."
    },

    # crypto.py
    "crypto_err_password_required_for_encryption": {
        "tr": "Şifreleme için parola belirtilmelidir.",
        "en": "A password must be specified for encryption."
    },
    "crypto_err_source_not_found": {
        "tr": "Şifrelenecek dosya bulunamadı: {path}",
        "en": "File to encrypt not found: {path}"
    },
    "crypto_err_openssl_required_for_encryption": {
        "tr": "Şifreleme işlemi için sistemde 'openssl' komutu bulunmalıdır.",
        "en": "The 'openssl' command must be installed on the system for encryption."
    },
    "crypto_err_encryption_failed": {
        "tr": "Şifreleme başarısız: {error}",
        "en": "Encryption failed: {error}"
    },
    "crypto_err_password_required_for_decryption": {
        "tr": "Şifre çözme için parola girilmelidir.",
        "en": "A password must be entered for decryption."
    },
    "crypto_err_encrypted_not_found": {
        "tr": "Şifreli dosya bulunamadı: {path}",
        "en": "Encrypted file not found: {path}"
    },
    "crypto_err_openssl_required_for_decryption": {
        "tr": "Şifre çözme işlemi için sistemde 'openssl' komutu bulunmalıdır.",
        "en": "The 'openssl' command must be installed on the system for decryption."
    },
    "crypto_err_decryption_failed": {
        "tr": "Şifre çözme başarısız: {error}",
        "en": "Decryption failed: {error}"
    },

    # verify.py
    "verify_err_backup_not_found": {
        "tr": "Doğrulanacak yedek bulunamadı: {path}",
        "en": "Backup to verify not found: {path}"
    },
    "verify_warn_base_error": {
        "tr": "Taban yedek ({name}) doğrulanırken hata oluştu: {error}",
        "en": "Error occurred while verifying base backup ({name}): {error}"
    },
    "verify_warn_base_missing": {
        "tr": "Taban yedek bulunamadı: '{base}'. Zincir eksik; taban katmandaki dosyaların bütünlüğü doğrulanamadı.",
        "en": "Base backup not found: '{base}'. Chain is missing; integrity of files in base layer could not be verified."
    },
    "verify_err_chain_missing": {
        "tr": "Zincir eksik: Taban yedek bulunamadı ('{base}').",
        "en": "Chain missing: Base backup not found ('{base}')."
    },
    "verify_skipped_default": {
        "tr": "atlandı",
        "en": "skipped"
    },
    "verify_err_manifest_not_found_in_dir": {
        "tr": "Dizinde 'manifest.json' bulunamadı!",
        "en": "'manifest.json' not found in directory!"
    },
    "verify_err_manifest_read_error": {
        "tr": "manifest.json okunamadı veya bozuk: {error}",
        "en": "manifest.json could not be read or is corrupted: {error}"
    },
    "verify_progress_verifying": {
        "tr": "Doğrulanıyor: {item}",
        "en": "Verifying: {item}"
    },
    "verify_err_stream_corrupted": {
        "tr": "Arşiv akışı bozuk veya kesilmiş: {error}",
        "en": "Archive stream corrupted or truncated: {error}"
    },
    "verify_err_manifest_not_found_in_archive": {
        "tr": "Yedek arşivinde 'manifest.json' bulunamadı!",
        "en": "'manifest.json' not found in backup archive!"
    },

    # diff_utils.py
    "diff_label_host": {
        "tr": "Mevcut Sistem (Host)",
        "en": "Current System (Host)"
    },
    "diff_label_backup": {
        "tr": "Yedeklenen Versiyon (Backup)",
        "en": "Backed Up Version (Backup)"
    },
    "diff_file_not_found": {
        "tr": "Dosya bulunamadı.",
        "en": "File not found."
    },
    "diff_binary_file": {
        "tr": "[İkili (Binary) Dosya - Metin farkı görüntülenemiyor]",
        "en": "[Binary File - Text diff cannot be displayed]"
    },
    "diff_host_file_read_error": {
        "tr": "Mevcut sistem dosyası okunamadı: {error}",
        "en": "Host file could not be read: {error}"
    },
    "diff_backup_file_read_error": {
        "tr": "Yedek dosyası okunamadı: {error}",
        "en": "Backup file could not be read: {error}"
    },
    "diff_files_identical": {
        "tr": "Dosyalar tamamen aynı (Fark yok).",
        "en": "Files are identical (No difference)."
    },

    # automation.py
    "timer_interval_disabled": {
        "tr": "Kapalı (Devre Dışı)",
        "en": "Disabled (Off)"
    },
    "automation_service_desc_inc": {
        "tr": "DistroNomad Otomatik Artımlı Yedekleme",
        "en": "DistroNomad Automatic Incremental Backup"
    },
    "automation_timer_desc_inc": {
        "tr": "DistroNomad Zamanlanmış Artımlı Yedekleme Zamanlayıcısı",
        "en": "DistroNomad Scheduled Incremental Backup Timer"
    },
    "automation_service_desc_full": {
        "tr": "DistroNomad Otomatik Yedekleme",
        "en": "DistroNomad Automatic Backup"
    },
    "automation_timer_desc_full": {
        "tr": "DistroNomad Zamanlanmış Yedekleme Zamanlayıcısı",
        "en": "DistroNomad Scheduled Backup Timer"
    },
    "automation_disabled_success": {
        "tr": "Otomatik zamanlayıcı başarıyla devre dışı bırakıldı.",
        "en": "Automatic scheduler successfully disabled."
    },
    "automation_err_portable_unsupported": {
        "tr": "Zamanlanmış yedekleme taşınabilir (USB) sürümde desteklenmez; DistroNomad'i sisteme kurup (pipx veya venv) oradan etkinleştirin.",
        "en": "Scheduled backups are not supported in the portable (USB) build; install DistroNomad on the system (pipx or venv) and enable it from there."
    },

    # hardware.py
    "hardware_diff_gpu": {
        "tr": "GPU: {value}",
        "en": "GPU: {value}"
    },
    "hardware_diff_cpu": {
        "tr": "CPU: {value}",
        "en": "CPU: {value}"
    },

    # packagemap.py
    "packagemap_warn_builtin_importlib": {
        "tr": "Dahili package_map.json importlib.resources ile okunamadı: {error}",
        "en": "Built-in package_map.json could not be read with importlib.resources: {error}"
    },
    "packagemap_warn_builtin_path": {
        "tr": "Dahili package_map.json doğrudan yoldan okunamadı: {error}",
        "en": "Built-in package_map.json could not be read from direct path: {error}"
    },
    "packagemap_warn_builtin_json": {
        "tr": "Dahili package_map.json JSON parse hatası: {error}",
        "en": "Built-in package_map.json JSON parse error: {error}"
    },
    "packagemap_warn_user_exists_check": {
        "tr": "Kullanıcı eşleme dosyası varlığı denetlenemedi ({path}): {error}",
        "en": "User mapping file existence check failed ({path}): {error}"
    },
    "packagemap_warn_user_json": {
        "tr": "Kullanıcı eşleme dosyası ({path}) bozuk veya geçersiz JSON içeriyor: {error}",
        "en": "User mapping file ({path}) is corrupted or contains invalid JSON: {error}"
    },
    "packagemap_warn_user_schema_root": {
        "tr": "Kullanıcı eşleme dosyası ({path}) geçersiz şema: kök öge bir JSON nesnesi olmalıdır.",
        "en": "User mapping file ({path}) invalid schema: root item must be a JSON object."
    },
    "packagemap_warn_user_packages_object": {
        "tr": "Kullanıcı eşleme dosyası ({path}) 'packages' alanı bir nesne olmalıdır.",
        "en": "User mapping file ({path}) 'packages' field must be an object."
    },
    "packagemap_warn_user_pkg_schema": {
        "tr": "Kullanıcı eşleme dosyası ({path}) '{pkg}' için geçersiz şema; dağıtım sözlüğü bekleniyor.",
        "en": "User mapping file ({path}) invalid schema for '{pkg}'; distribution dictionary expected."
    },
    "packagemap_warn_user_fam_invalid": {
        "tr": "Kullanıcı eşleme dosyası ({path}) '{pkg}' altındaki '{fam}' geçersiz; dize bekleniyor.",
        "en": "User mapping file ({path}) '{fam}' under '{pkg}' is invalid; string expected."
    },
    "packagemap_warn_user_flatpak_object": {
        "tr": "Kullanıcı eşleme dosyası ({path}) 'flatpak' alanı bir nesne olmalıdır.",
        "en": "User mapping file ({path}) 'flatpak' field must be an object."
    },
    "packagemap_warn_user_flatpak_invalid": {
        "tr": "Kullanıcı eşleme dosyası ({path}) flatpak '{app}' geçersiz; dize bekleniyor.",
        "en": "User mapping file ({path}) flatpak '{app}' is invalid; string expected."
    },
    "pm_desc_pacman": {
        "tr": "Pacman kurulumu",
        "en": "Pacman installation"
    },
    "pm_desc_aur": {
        "tr": "{mgr} (AUR) kurulumu",
        "en": "{mgr} (AUR) installation"
    },
    "pm_desc_apt": {
        "tr": "Apt kurulumu",
        "en": "Apt installation"
    },
    "pm_desc_dnf": {
        "tr": "DNF kurulumu",
        "en": "DNF installation"
    },
    "pm_desc_zypper": {
        "tr": "Zypper kurulumu",
        "en": "Zypper installation"
    },
    "pm_desc_flatpak": {
        "tr": "Flatpak kurulumu",
        "en": "Flatpak installation"
    },
    "pm_desc_snap": {
        "tr": "Snap kurulumu",
        "en": "Snap installation"
    },
    "pm_desc_pipx": {
        "tr": "Pipx kurulumu",
        "en": "Pipx installation"
    },
    "pm_desc_cargo": {
        "tr": "Cargo kurulumu",
        "en": "Cargo installation"
    },
    "pm_desc_npm": {
        "tr": "NPM genel kurulumu",
        "en": "NPM global installation"
    },
    "pm_desc_vscode": {
        "tr": "VS Code eklenti kurulumu",
        "en": "VS Code extension installation"
    },
    "pm_desc_codium": {
        "tr": "VSCodium eklenti kurulumu",
        "en": "VSCodium extension installation"
    },
    "pm_desc_generic": {
        "tr": "{mgr} kurulumu",
        "en": "{mgr} installation"
    },

    # restore.py
    "restore_warn_repo_query_error": {
        "tr": "Depo paket sorgulama hatası ({pm}): {error}",
        "en": "Repository package query error ({pm}): {error}"
    },
    "restore_err_backup_not_found": {
        "tr": "Yedek dosyası veya dizini bulunamadı: {path}",
        "en": "Backup file or directory not found: {path}"
    },
    "restore_err_password_required": {
        "tr": "Bu yedek AES-256 ile şifrelenmiştir. Lütfen şifre çözme parolasını girin.",
        "en": "This backup is encrypted with AES-256. Please enter the decryption password."
    },
    "restore_notify_decrypting": {
        "tr": "Yedek arşivinin şifresi çözülüyor...",
        "en": "Decrypting backup archive..."
    },
    "restore_err_manifest_not_found": {
        "tr": "Geçersiz yedek arşivi: manifest.json bulunamadı ({path})",
        "en": "Invalid backup archive: manifest.json not found ({path})"
    },
    "restore_notify_extracting": {
        "tr": "Yedek arşivi açılıyor: {name}",
        "en": "Extracting backup archive: {name}"
    },
    "restore_err_base_not_found": {
        "tr": "Artımlı yedek için taban yedek bulunamadı: {base}. Lütfen geçerli bir temel yedek yolu belirtin (--base).",
        "en": "Base backup not found for incremental backup: {base}. Please specify a valid base backup path (--base)."
    },
    "restore_err_chain_loop": {
        "tr": "Artımlı yedek zincirinde döngü tespit edildi: {name}",
        "en": "Loop detected in incremental backup chain: {name}"
    },
    "restore_err_base_encrypted": {
        "tr": "Taban yedek AES-256 ile şifrelenmiştir, lütfen parola belirtin: {name}",
        "en": "Base backup is encrypted with AES-256, please specify password: {name}"
    },
    "restore_err_base_manifest_corrupted": {
        "tr": "Taban yedeğin manifest dosyası okunamadı veya arşiv bozuk: {name}",
        "en": "Base backup manifest file could not be read or archive is corrupted: {name}"
    },
    "restore_notify_integrating_base": {
        "tr": "Temel yedek entegre ediliyor: {name}...",
        "en": "Integrating base backup: {name}..."
    },
    "restore_notify_decrypting_base": {
        "tr": "Taban arşivin şifresi çözülüyor: {name}...",
        "en": "Decrypting base archive: {name}..."
    },
    "restore_notify_integrating_inc": {
        "tr": "Artımlı yedek entegre ediliyor: {name}...",
        "en": "Integrating incremental backup: {name}..."
    },
    "restore_warn_traversal_blocked": {
        "tr": "Güvenlik uyarısı: Hedef ev dizini dışına çıkma engellendi, atlandı: {src} -> {dst}",
        "en": "Security warning: Traversal outside target home blocked, skipped: {src} -> {dst}"
    },
    "restore_notify_restoring_config": {
        "tr": "Ayar geri yükleniyor: {name}",
        "en": "Restoring configuration: {name}"
    },
    "restore_warn_not_found_in_backup": {
        "tr": "Yedek içinde bulunamadı: {path}",
        "en": "Not found in backup: {path}"
    },
    "restore_warn_conflict_skipped": {
        "tr": "Atlandı (mevcut dosya korundu): {path}",
        "en": "Skipped (existing file preserved): {path}"
    },
    "restore_warn_root_write_blocked_custom_home": {
        "tr": "Hedef ev dizini özel bir dizin olarak belirtildiğinden mutlak kök yoluna yazma engellendi, atlandı: {path}",
        "en": "Writing to absolute root path blocked because target home is a custom directory, skipped: {path}"
    },
    "restore_warn_external_path_skipped_no_confirm": {
        "tr": "Ev dışı özel yol atlandı (onay verilmedi): {path}",
        "en": "External custom path skipped (not confirmed): {path}"
    },
    "restore_warn_external_path_invalid": {
        "tr": "Güvenlik uyarısı: Ev dışı özel yol mutlak değil veya hedef kökün dışına çıkıyor, atlandı: {path}",
        "en": "Security warning: External custom path is not absolute or escapes the target root, skipped: {path}"
    },
    "restore_notify_restoring_file": {
        "tr": "Özel dosya/klasör geri yükleniyor: {name}",
        "en": "Restoring custom file/folder: {name}"
    },
    "restore_warn_target_not_writable": {
        "tr": "Hedef yazılabilir değil, atlandı (sudo kullanılmadı): {path}",
        "en": "Target is not writable, skipped (sudo not used): {path}"
    },
    "restore_notify_preparing_packages": {
        "tr": "Uygulama ve paket kurulumları hazırlanıyor...",
        "en": "Preparing application and package installations..."
    },
    "restore_warn_invalid_package_name": {
        "tr": "Geçersiz veya güvensiz paket adı atlandı: {name}",
        "en": "Invalid or unsafe package name skipped: {name}"
    },
    "restore_invalid_pkg_tag": {
        "tr": "geçersiz paket adı",
        "en": "invalid package name"
    },
    "restore_warn_sudo_no_terminal": {
        "tr": "{pm} kurulumu atlandı: Parolasız sudo yetkisi yok ve etkileşimli terminal bulunmuyor.",
        "en": "{pm} installation skipped: No passwordless sudo privilege and no interactive terminal."
    },
    "restore_sudo_failed_tag": {
        "tr": "yetki hatası: sudo -n başarısız",
        "en": "permission error: sudo -n failed"
    },
    "restore_dry_run_apt_update": {
        "tr": "[DRY-RUN] Apt güncellemesi: sudo apt-get update",
        "en": "[DRY-RUN] Apt update: sudo apt-get update"
    },
    "restore_notify_apt_update": {
        "tr": "Apt paket listesi güncelleniyor: sudo apt-get update...",
        "en": "Updating Apt package list: sudo apt-get update..."
    },
    "restore_err_update_error": {
        "tr": "{pm} update hatası: {error}",
        "en": "{pm} update error: {error}"
    },
    "restore_notify_installing_pkgs": {
        "tr": "{desc} başlatılıyor: {count} paket...",
        "en": "Starting {desc}: {count} packages..."
    },
    "restore_notify_batch_failed_trying_single": {
        "tr": "{desc} toplu kurulum başarısız oldu, paketler tek tek deneniyor...",
        "en": "{desc} batch installation failed, trying packages individually..."
    },
    "restore_err_exit_code": {
        "tr": "çıkış kodu {code}",
        "en": "exit code {code}"
    },
    "restore_err_single_pkg_timeout": {
        "tr": "{pm}:{pkg} (zaman aşımı {sec}s)",
        "en": "{pm}:{pkg} (timeout {sec}s)"
    },
    "restore_warn_consecutive_failures_exit": {
        "tr": "{pm} paket kurulumunda art arda yetki/zaman aşımı hatası alındığı için döngüden erken çıkıldı.",
        "en": "Early exit from loop due to consecutive auth/timeout errors during {pm} package installation."
    },
    "restore_err_pkg_timeout": {
        "tr": "{pm} zaman aşımı ({sec}s)",
        "en": "{pm} timeout ({sec}s)"
    },
    "restore_err_pkg_error": {
        "tr": "{pm} hatası: {error}",
        "en": "{pm} error: {error}"
    },
    "restore_warn_dconf_backup_code": {
        "tr": "Mevcut dconf yedeklenemedi (çıkış kodu {code})",
        "en": "Could not back up existing dconf (exit code {code})"
    },
    "restore_warn_dconf_backup_error": {
        "tr": "Mevcut dconf yedeklenemedi: {error}",
        "en": "Could not back up existing dconf: {error}"
    },
    "restore_warn_dconf_snapshot_failed": {
        "tr": "Geri yükleme öncesi dconf anlık görüntüsü alınamadığı için dconf ayarları geri yüklenmedi.",
        "en": "dconf settings not restored because dconf snapshot could not be taken prior to restore."
    },
    "restore_tag_dconf_settings": {
        "tr": "dconf (GNOME/Cinnamon ayarları)",
        "en": "dconf (GNOME/Cinnamon settings)"
    },
    "restore_warn_dconf_load_failed": {
        "tr": "dconf yüklenemedi: {error}",
        "en": "dconf could not be loaded: {error}"
    },
    "restore_warn_crontab_backup_stderr": {
        "tr": "Mevcut crontab yedeklenemedi: {error}",
        "en": "Could not back up existing crontab: {error}"
    },
    "restore_warn_crontab_backup_error": {
        "tr": "Mevcut crontab yedeklenemedi: {error}",
        "en": "Could not back up existing crontab: {error}"
    },
    "restore_warn_crontab_snapshot_failed": {
        "tr": "Geri yükleme öncesi crontab anlık görüntüsü alınamadığı için crontab geri yüklenmedi.",
        "en": "crontab not restored because crontab snapshot could not be taken prior to restore."
    },
    "restore_tag_crontab_settings": {
        "tr": "crontab (Kullanıcı zamanlanmış görevleri)",
        "en": "crontab (User scheduled jobs)"
    },
    "restore_warn_crontab_load_failed": {
        "tr": "crontab yüklenemedi: {error}",
        "en": "crontab could not be loaded: {error}"
    },
    "restore_notify_completed": {
        "tr": "Geri yükleme işlemi başarıyla tamamlandı!",
        "en": "Restore operation completed successfully!"
    },
    "restore_err_tx_log_not_found": {
        "tr": "Geri alma işlemi için işlem kaydı (transaction log) bulunamadı: {path}",
        "en": "Transaction log not found for rollback operation: {path}"
    },
    "restore_err_already_rolled_back": {
        "tr": "Bu geri yükleme zaten geri alınmış ({time}). Tekrar geri almak, o zamandan beri oluşturulan dosyaları silebileceği için engellendi.",
        "en": "This restore was already rolled back ({time}). Rolling it back again is blocked because it could delete files created since then."
    },
    "restore_notify_rollback_item": {
        "tr": "Geri alınıyor: {name}",
        "en": "Rolling back: {name}"
    },
    "restore_err_rollback_overwritten": {
        "tr": "{path}: Dosya üzerine yazma ('overwrite') stratejisiyle geri yüklendiğinden yedeği bulunmuyor ve geri alınamadı.",
        "en": "{path}: No backup exists because file was restored with 'overwrite' strategy, unable to revert."
    },
    "restore_warn_rollback_dconf_no_snapshot": {
        "tr": "dconf: Önceki anlık görüntü bulunamadığı için masaüstü ayarları geri alınmadı (güvenlik koruması).",
        "en": "dconf: Desktop settings not reverted because previous snapshot was not found (safety protection)."
    },
    "restore_warn_rollback_crontab_no_snapshot": {
        "tr": "crontab: Önceki anlık görüntü yedeği bulunamadığından ve ilk durum doğrulanamadığından silme yapılmadı (güvenlik koruması).",
        "en": "crontab: Deletion not performed because previous snapshot was not found and initial state could not be verified (safety protection)."
    },

    # backup.py & FAT32
    "backup_err_target_exists": {
        "tr": "Hedef zaten mevcut: {path}",
        "en": "Target already exists: {path}"
    },
    "backup_warn_special_file_skipped": {
        "tr": "Özel dosya atlandı (soket/FIFO/cihaz): {path}",
        "en": "Special file skipped (socket/FIFO/device): {path}"
    },
    "backup_warn_symlink_failed": {
        "tr": "Sembolik bağ oluşturulamadı ve atlandı: {src} -> {dst} ({error})",
        "en": "Symlink creation failed and skipped: {src} -> {dst} ({error})"
    },
    "backup_reason_symlink_failed": {
        "tr": "Sembolik bağ oluşturulamadı: {error}",
        "en": "Symlink could not be created: {error}"
    },
    "backup_reason_special_file": {
        "tr": "Özel dosya: {error}",
        "en": "Special file: {error}"
    },
    "backup_err_hash_failed": {
        "tr": "Hedef dosya hash hesaplanamadı: {path}",
        "en": "Could not compute hash for target file: {path}"
    },
    "backup_warn_read_failed": {
        "tr": "Dosya okunamadı ve atlandı: {path} ({error})",
        "en": "File could not be read and skipped: {path} ({error})"
    },
    "backup_warn_copy_failed": {
        "tr": "Dosya kopyalanamadı ve atlandı: {path} ({error})",
        "en": "File could not be copied and skipped: {path} ({error})"
    },
    "fat32_dir_unsupported_warning": {
        "tr": "Hedef FAT32 dosya sistemi sembolik bağları ve Linux izinlerini desteklemez; arşiv formatı (.tar.zst) önerilir.",
        "en": "Target FAT32 filesystem does not support symlinks and Linux permissions; archive format (.tar.zst) is recommended."
    },
    "backup_err_enc_archive_only": {
        "tr": "Şifreleme (AES-256) işlemi yalnızca arşiv formatlarında (.tar.zst / .tar.gz) desteklenmektedir.",
        "en": "Encryption (AES-256) is only supported for archive formats (.tar.zst / .tar.gz)."
    },
    "backup_notify_examining_base": {
        "tr": "Temel yedek inceleniyor: {name}",
        "en": "Examining base backup: {name}"
    },
    "backup_notify_base_read_failed": {
        "tr": "Temel yedek okunamadı ({name}), tam yedekleme yapılacak.",
        "en": "Base backup could not be read ({name}), full backup will be performed."
    },
    "backup_notify_scanning": {
        "tr": "Dosyalar taranıyor ve değişiklikler analiz ediliyor...",
        "en": "Scanning files and analyzing changes..."
    },
    "backup_notify_changes_summary": {
        "tr": "Değişen/Yeni: {changed} dosya, Silinen: {deleted} dosya",
        "en": "Changed/New: {changed} files, Deleted: {deleted} files"
    },
    "backup_err_inc_dir_same_as_base": {
        "tr": "Artımlı dizin yedeği taban yedekle aynı dizine yazılamaz: {path}",
        "en": "Incremental directory backup cannot be written to the same directory as base backup: {path}"
    },
    "backup_err_dir_exists_not_empty": {
        "tr": "Hedef dizin zaten mevcut ve boş değil: {path}",
        "en": "Target directory already exists and is not empty: {path}"
    },
    "backup_notify_copying_file": {
        "tr": "Yedekleniyor ({idx}/{total}): {dest}",
        "en": "Backing up ({idx}/{total}): {dest}"
    },
    "backup_notify_syncing": {
        "tr": "Veriler diske zorla yazılıyor (sync)...",
        "en": "Flushing data to disk (sync)..."
    },
    "backup_skip_note": {
        "tr": " ({count} dosya atlandı)",
        "en": " ({count} files skipped)"
    },
    "backup_notify_inc_analysis": {
        "tr": "⚡ Artımlı Analiz: {total} ögeden {changed} tanesi değişti/yeni, {unchanged} öge güncel (atlandı), {deleted} öge silindi. Tasarruf: ~{saved}",
        "en": "⚡ Incremental Analysis: {changed} of {total} items changed/new, {unchanged} items up to date (skipped), {deleted} items deleted. Savings: ~{saved}"
    },
    "backup_success_inc": {
        "tr": "✔ Artımlı yedek başarıyla oluşturuldu: {name} ({count} dosya, {saved} tasarruf){skip_note}",
        "en": "✔ Incremental backup successfully created: {name} ({count} files, {saved} saved){skip_note}"
    },
    "backup_success_full": {
        "tr": "✔ Tam yedekleme başarıyla tamamlandı: {name}{skip_note}",
        "en": "✔ Full backup completed successfully: {name}{skip_note}"
    },
    "backup_notify_packing_archive": {
        "tr": "Yedek arşivi paketleniyor ({format})...",
        "en": "Packaging backup archive ({format})..."
    },
    "backup_notify_adding_to_archive": {
        "tr": "Arşive ekleniyor ({idx}/{total}): {dest}",
        "en": "Adding to archive ({idx}/{total}): {dest}"
    },
    "backup_warn_archive_add_failed": {
        "tr": "Dosya arşive eklenemedi: {path} ({error})",
        "en": "File could not be added to archive: {path} ({error})"
    },
    "backup_warn_empty_dir_add_failed": {
        "tr": "Boş dizin arşive eklenemedi: {path} ({error})",
        "en": "Empty directory could not be added to archive: {path} ({error})"
    },
    "backup_notify_encrypting_archive": {
        "tr": "Yedek arşivi AES-256 PBKDF2 ile şifreleniyor...",
        "en": "Encrypting backup archive with AES-256 PBKDF2..."
    },
    "backup_notify_network_secrets_skipped": {
        "tr": "Ağ sırları (NetworkManager, WireGuard) şifresiz yedeklerde güvenlik nedeniyle atlandı.",
        "en": "Network secrets (NetworkManager, WireGuard) were skipped in unencrypted backup for security."
    },
    "backup_system_state_network_secrets_skipped_note": {
        "tr": "Ağ sırları (/etc/NetworkManager/system-connections, /etc/wireguard) şifresiz yedekleme nedeniyle dahil edilmedi.",
        "en": "Network secrets (/etc/NetworkManager/system-connections, /etc/wireguard) were excluded because backup is unencrypted."
    },
    "backup_err_append_target_not_found": {
        "tr": "Güncellenecek mevcut yedek arşivi veya klasörü bulunamadı: {path}",
        "en": "Existing backup archive or directory to update not found: {path}"
    },
    "backup_err_append_password_required": {
        "tr": "Şifrelenmiş yedeğe yeni öge eklemek için şifreleme parolası gereklidir.",
        "en": "Encryption password is required to append new items to an encrypted backup."
    },
    "backup_notify_opening_existing": {
        "tr": "Var olan yedek açılıyor: {name}",
        "en": "Opening existing backup: {name}"
    },
    "backup_err_append_manifest_not_found": {
        "tr": "Geçersiz yedek arşivi: manifest.json bulunamadı ({path})",
        "en": "Invalid backup archive: manifest.json not found ({path})"
    },
    "backup_notify_append_config": {
        "tr": "Ayar ekleniyor/güncelleniyor ({idx}/{total}): {name}",
        "en": "Adding/updating configuration ({idx}/{total}): {name}"
    },
    "backup_notify_append_custom": {
        "tr": "Özel dosya/klasör ekleniyor ({idx}/{total}): {name}",
        "en": "Adding custom file/folder ({idx}/{total}): {name}"
    },
    "backup_notify_updating_archive": {
        "tr": "Yedek arşivi güncelleniyor ({format})...",
        "en": "Updating backup archive ({format})..."
    },
    "backup_notify_reencrypting_archive": {
        "tr": "Yedek arşivi AES-256 ile yeniden şifreleniyor...",
        "en": "Re-encrypting backup archive with AES-256..."
    },
    "backup_success_append": {
        "tr": "✔ Var olan yedeğe başarıyla eklendi (+{pkgs} paket, +{cfgs} ayar, +{files} dosya/klasör): {name}{skip_note}",
        "en": "✔ Successfully appended to existing backup (+{pkgs} packages, +{cfgs} configs, +{files} files/folders): {name}{skip_note}"
    },

    # ==================== W3b KATEGORİLER & ETİKETLER ====================
    "cat_general": {
        "tr": "Genel",
        "en": "General"
    },
    "cat_desktop_wm": {
        "tr": "Masaüstü / WM",
        "en": "Desktop / WM"
    },
    "cat_terminal_shell": {
        "tr": "Terminal & Kabuk",
        "en": "Terminal & Shell"
    },
    "cat_editor_ide": {
        "tr": "Düzenleyici & IDE",
        "en": "Editor & IDE"
    },
    "cat_dev_build": {
        "tr": "Geliştirme & Derleme",
        "en": "Development & Build"
    },
    "cat_desktop_app": {
        "tr": "Masaüstü Uygulaması",
        "en": "Desktop Application"
    },
    "cat_cli_tool": {
        "tr": "CLI / Yardımcı Araç",
        "en": "CLI / Utility Tool"
    },
    "cat_system_general": {
        "tr": "Sistem / Genel",
        "en": "System / General"
    },
    "cat_desktop_flatpak": {
        "tr": "Masaüstü (Flatpak)",
        "en": "Desktop (Flatpak)"
    },
    "cat_snap": {
        "tr": "Snap Uygulaması",
        "en": "Snap Application"
    },
    "cat_cli_python": {
        "tr": "Python CLI Aracı",
        "en": "Python CLI Tool"
    },
    "cat_cli_rust": {
        "tr": "Rust CLI Aracı",
        "en": "Rust CLI Tool"
    },
    "cat_npm_global": {
        "tr": "NPM Global Paketi",
        "en": "NPM Global Package"
    },
    "cat_editor_ext": {
        "tr": "Editör Eklentisi",
        "en": "Editor Extension"
    },
    "cat_vscode_ext": {
        "tr": "VS Code Eklentisi",
        "en": "VS Code Extension"
    },
    "cat_codium_ext": {
        "tr": "VSCodium Eklentisi",
        "en": "VSCodium Extension"
    },
    "cat_editor": {
        "tr": "Düzenleyici",
        "en": "Editor"
    },
    "cat_terminal": {
        "tr": "Terminal",
        "en": "Terminal"
    },
    "cat_system_monitor": {
        "tr": "Sistem / İzleme",
        "en": "System / Monitoring"
    },
    "cat_shell": {
        "tr": "Kabuk",
        "en": "Shell"
    },
    "cat_media": {
        "tr": "Medya",
        "en": "Media"
    },
    "cat_media_audio": {
        "tr": "Medya / Ses",
        "en": "Media / Audio"
    },
    "cat_file_manager": {
        "tr": "Dosya Yöneticisi",
        "en": "File Manager"
    },
    "cat_communication": {
        "tr": "İletişim",
        "en": "Communication"
    },
    "cat_dev": {
        "tr": "Geliştirme",
        "en": "Development"
    },
    "cat_gaming": {
        "tr": "Oyun (Gaming)",
        "en": "Gaming"
    },
    "cat_system": {
        "tr": "Sistem",
        "en": "System"
    },
    "cat_app_config": {
        "tr": "Uygulama Ayarı (~/.config)",
        "en": "Application Config (~/.config)"
    },
    "cat_shell_history": {
        "tr": "Kabuk Geçmişi",
        "en": "Shell History"
    },
    "cat_dev_identity": {
        "tr": "Geliştirme & Kimlik",
        "en": "Development & Credentials"
    },
    "cat_security_keys": {
        "tr": "Güvenlik & Anahtarlar",
        "en": "Security & Keys"
    },
    "cat_cli_util": {
        "tr": "CLI / Yardımcı Araç",
        "en": "CLI / Utility Tool"
    },
    "cat_dev_cloud": {
        "tr": "Geliştirme & Bulut",
        "en": "Development & Cloud"
    },
    "cat_appearance_themes": {
        "tr": "Görünüm & Temalar",
        "en": "Appearance & Themes"
    },
    "cat_desktop_integration": {
        "tr": "Masaüstü Entegrasyonu",
        "en": "Desktop Integration"
    },
    "cat_desktop_gnome": {
        "tr": "Masaüstü / GNOME",
        "en": "Desktop / GNOME"
    },
    "cat_desktop_kde": {
        "tr": "Masaüstü / KDE Plasma",
        "en": "Desktop / KDE Plasma"
    },
    "cat_browser_comm": {
        "tr": "Tarayıcı & İletişim",
        "en": "Browser & Communication"
    },
    "cat_flatpak_data": {
        "tr": "Flatpak Verileri",
        "en": "Flatpak Data"
    },
    "cat_flatpak_app_data": {
        "tr": "Flatpak Verileri (~/.var/app)",
        "en": "Flatpak Data (~/.var/app)"
    },
    "cat_other": {
        "tr": "Diğer",
        "en": "Other"
    },

    # Bilinen Konfigürasyon Açıklamaları
    "cfg_desc_hypr": {
        "tr": "Hyprland Wayland Kompozitörü",
        "en": "Hyprland Wayland Compositor"
    },
    "cfg_desc_sway": {
        "tr": "Sway i3-uyumlu Wayland Masaüstü",
        "en": "Sway i3-compatible Wayland Desktop"
    },
    "cfg_desc_i3": {
        "tr": "i3 Döşemeli Pencere Yöneticisi",
        "en": "i3 Tiling Window Manager"
    },
    "cfg_desc_waybar": {
        "tr": "Wayland Durum Çubuğu",
        "en": "Wayland Status Bar"
    },
    "cfg_desc_rofi": {
        "tr": "Uygulama Başlatıcı ve Arama",
        "en": "Application Launcher and Search"
    },
    "cfg_desc_wofi": {
        "tr": "Wayland Uygulama Başlatıcı",
        "en": "Wayland Application Launcher"
    },
    "cfg_desc_dunst": {
        "tr": "Bildirim Servisi",
        "en": "Notification Daemon"
    },
    "cfg_desc_mako": {
        "tr": "Wayland Bildirim Servisi",
        "en": "Wayland Notification Daemon"
    },
    "cfg_desc_nvim": {
        "tr": "Neovim Metin Düzenleyicisi",
        "en": "Neovim Text Editor"
    },
    "cfg_desc_vim": {
        "tr": "Vim Metin Düzenleyicisi",
        "en": "Vim Text Editor"
    },
    "cfg_desc_helix": {
        "tr": "Helix Modern Terminal Editörü",
        "en": "Helix Modern Terminal Editor"
    },
    "cfg_desc_zed": {
        "tr": "Zed Hızlı Kod Düzenleyicisi",
        "en": "Zed Fast Code Editor"
    },
    "cfg_desc_code": {
        "tr": "Visual Studio Code",
        "en": "Visual Studio Code"
    },
    "cfg_desc_cursor": {
        "tr": "Cursor AI Kod Düzenleyicisi",
        "en": "Cursor AI Code Editor"
    },
    "cfg_desc_vscodium": {
        "tr": "VSCodium Açık Kaynak Düzenleyici",
        "en": "VSCodium Open Source Editor"
    },
    "cfg_desc_kitty": {
        "tr": "Kitty GPU Hızlandırmalı Terminal",
        "en": "Kitty GPU Accelerated Terminal"
    },
    "cfg_desc_alacritty": {
        "tr": "Alacritty Hızlı Terminal",
        "en": "Alacritty Fast Terminal"
    },
    "cfg_desc_wezterm": {
        "tr": "WezTerm Terminal Emülatörü",
        "en": "WezTerm Terminal Emulator"
    },
    "cfg_desc_foot": {
        "tr": "Foot Hafif Wayland Terminali",
        "en": "Foot Lightweight Wayland Terminal"
    },
    "cfg_desc_zellij": {
        "tr": "Zellij Modern Terminal Çoklayıcı",
        "en": "Zellij Modern Terminal Multiplexer"
    },
    "cfg_desc_btop": {
        "tr": "btop++ Kaynak Monitörü",
        "en": "btop++ Resource Monitor"
    },
    "cfg_desc_htop": {
        "tr": "htop Süreç Yöneticisi",
        "en": "htop Process Manager"
    },
    "cfg_desc_fastfetch": {
        "tr": "Fastfetch Sistem Bilgi Aracı",
        "en": "Fastfetch System Info Tool"
    },
    "cfg_desc_neofetch": {
        "tr": "Neofetch Bilgi Ekranı",
        "en": "Neofetch Info Tool"
    },
    "cfg_desc_fish": {
        "tr": "Fish Etkileşimli Kabuk",
        "en": "Fish Interactive Shell"
    },
    "cfg_desc_starship": {
        "tr": "Starship Kabuk İstemcisi (Prompt)",
        "en": "Starship Cross-Shell Prompt"
    },
    "cfg_desc_mpv": {
        "tr": "MPV Medya Oynatıcısı",
        "en": "MPV Media Player"
    },
    "cfg_desc_pipewire": {
        "tr": "PipeWire Ses Sunucusu Ayarları",
        "en": "PipeWire Audio Server Settings"
    },
    "cfg_desc_wireplumber": {
        "tr": "WirePlumber Ses Oturum Yöneticisi",
        "en": "WirePlumber Audio Session Manager"
    },
    "cfg_desc_easyeffects": {
        "tr": "EasyEffects Ses Efekt ve Ekolayzer Profilleri",
        "en": "EasyEffects Audio Effects and Equalizer"
    },
    "cfg_desc_ranger": {
        "tr": "Ranger Terminal Dosya Yöneticisi",
        "en": "Ranger Terminal File Manager"
    },
    "cfg_desc_yazi": {
        "tr": "Yazi Modern Terminal Dosya Yöneticisi",
        "en": "Yazi Modern Terminal File Manager"
    },
    "cfg_desc_thunar": {
        "tr": "Thunar XFCE Dosya Yöneticisi",
        "en": "Thunar XFCE File Manager"
    },
    "cfg_desc_obs": {
        "tr": "OBS Studio Yayın ve Kayıt",
        "en": "OBS Studio Streaming and Recording"
    },
    "cfg_desc_discord": {
        "tr": "Discord İletişim Uygulaması",
        "en": "Discord Communication App"
    },
    "cfg_desc_spotify": {
        "tr": "Spotify Müzik İstemcisi",
        "en": "Spotify Music Client"
    },
    "cfg_desc_lazygit": {
        "tr": "Lazygit Terminal Git Arayüzü",
        "en": "Lazygit Terminal Git UI"
    },
    "cfg_desc_gh": {
        "tr": "GitHub CLI Yapılandırması",
        "en": "GitHub CLI Configuration"
    },
    "cfg_desc_mangohud": {
        "tr": "MangoHud FPS & Sistem Katmanı",
        "en": "MangoHud FPS & System Overlay"
    },
    "cfg_desc_heroic": {
        "tr": "Heroic Games Launcher (Epic/GOG)",
        "en": "Heroic Games Launcher (Epic/GOG)"
    },
    "cfg_desc_lutris": {
        "tr": "Lutris Oyun Yöneticisi",
        "en": "Lutris Game Manager"
    },
    "cfg_desc_autostart": {
        "tr": "Başlangıçta Otomatik Çalışan Uygulamalar",
        "en": "Autostart Applications"
    },
    "cfg_desc_systemd": {
        "tr": "Kullanıcı systemd Servis ve Zamanlayıcıları",
        "en": "User systemd Services and Timers"
    },
    "cfg_desc_mimeapps": {
        "tr": "Varsayılan Uygulama İlişkilendirmeleri",
        "en": "Default Application Associations"
    },
    "cfg_desc_userdirs": {
        "tr": "Kullanıcı Dizin Tanımları",
        "en": "User Directory Definitions"
    },

    # Dotfile Açıklamaları
    "dot_desc_bashrc": {
        "tr": "Bash Kabuk Yapılandırması",
        "en": "Bash Shell Configuration"
    },
    "dot_desc_bash_profile": {
        "tr": "Bash Profil Başlatma",
        "en": "Bash Profile Startup"
    },
    "dot_desc_profile": {
        "tr": "Kullanıcı Ortam Profili",
        "en": "User Environment Profile"
    },
    "dot_desc_zshrc": {
        "tr": "Zsh Kabuk Yapılandırması",
        "en": "Zsh Shell Configuration"
    },
    "dot_desc_zshenv": {
        "tr": "Zsh Ortam Değişkenleri",
        "en": "Zsh Environment Variables"
    },
    "dot_desc_zprofile": {
        "tr": "Zsh Profil Başlatma",
        "en": "Zsh Profile Startup"
    },
    "dot_desc_p10k": {
        "tr": "Powerlevel10k Zsh Teması",
        "en": "Powerlevel10k Zsh Theme"
    },
    "dot_desc_inputrc": {
        "tr": "Readline Klavye Kısayol Yapılandırması",
        "en": "Readline Keybindings Configuration"
    },
    "dot_desc_bash_history": {
        "tr": "Bash Komut Geçmişi",
        "en": "Bash Command History"
    },
    "dot_desc_zsh_history": {
        "tr": "Zsh Komut Geçmişi",
        "en": "Zsh Command History"
    },
    "dot_desc_python_history": {
        "tr": "Python Komut Geçmişi",
        "en": "Python Command History"
    },
    "dot_desc_psql_history": {
        "tr": "PostgreSQL Komut Geçmişi",
        "en": "PostgreSQL Command History"
    },
    "dot_desc_mysql_history": {
        "tr": "MySQL Komut Geçmişi",
        "en": "MySQL Command History"
    },
    "dot_desc_sqlite_history": {
        "tr": "SQLite Komut Geçmişi",
        "en": "SQLite Command History"
    },
    "dot_desc_gitconfig": {
        "tr": "Git Kullanıcı Yapılandırması",
        "en": "Git User Configuration"
    },
    "dot_desc_gitignore_global": {
        "tr": "Genel Git Yoksayma Listesi",
        "en": "Global Git Ignore List"
    },
    "dot_desc_git_credentials": {
        "tr": "Git Kimlik Bilgileri",
        "en": "Git Credentials"
    },
    "dot_desc_gdbinit": {
        "tr": "GDB Hata Ayıklayıcı Ayarları",
        "en": "GDB Debugger Settings"
    },
    "dot_desc_cargo": {
        "tr": "Rust Cargo Yapılandırması",
        "en": "Rust Cargo Configuration"
    },
    "dot_desc_maven": {
        "tr": "Maven Yapılandırması",
        "en": "Maven Configuration"
    },
    "dot_desc_tmux": {
        "tr": "Tmux Çoklayıcı Yapılandırması",
        "en": "Tmux Multiplexer Configuration"
    },
    "dot_desc_vimrc": {
        "tr": "Vim Temel Yapılandırması",
        "en": "Vim Base Configuration"
    },
    "dot_desc_xresources": {
        "tr": "X11 Renk ve Yazı Tipi Tanımları",
        "en": "X11 Color and Font Resources"
    },
    "dot_desc_xprofile": {
        "tr": "X11 Başlatma Komutları",
        "en": "X11 Startup Commands"
    },
    "dot_desc_xinitrc": {
        "tr": "X11 Masaüstü Başlatıcı",
        "en": "X11 Desktop Starter"
    },
    "dot_desc_npmrc": {
        "tr": "NPM Paket Yapılandırması ve Kimlik Belirteçleri",
        "en": "NPM Package Config and Tokens"
    },
    "dot_desc_netrc": {
        "tr": "Ağ Kimlik Bilgileri (.netrc)",
        "en": "Network Credentials (.netrc)"
    },
    "dot_desc_wgetrc": {
        "tr": "Wget Yapılandırması",
        "en": "Wget Configuration"
    },
    "dot_desc_curlrc": {
        "tr": "Curl Yapılandırması",
        "en": "Curl Configuration"
    },

    # Hassas ve Güvenlik Açıklamaları
    "sec_desc_ssh": {
        "tr": "SSH Anahtarları & Yapılandırma",
        "en": "SSH Keys & Configuration"
    },
    "sec_desc_gnupg": {
        "tr": "GPG Anahtarları",
        "en": "GPG Keys"
    },
    "sec_desc_pass": {
        "tr": "Pass Şifre Yöneticisi Kasası",
        "en": "Pass Password Manager Store"
    },
    "sec_desc_keyrings": {
        "tr": "GNOME Keyring Anahtarlığı",
        "en": "GNOME Keyring"
    },
    "sec_desc_kwallet": {
        "tr": "KDE KWallet Şifre Cüzdanı",
        "en": "KDE KWallet"
    },
    "sec_desc_pki": {
        "tr": "Kullanıcı SSL/TLS Sertifikaları",
        "en": "User SSL/TLS Certificates"
    },
    "sec_desc_cert": {
        "tr": "VPN ve Ağ Sertifikaları",
        "en": "VPN and Network Certificates"
    },
    "sec_desc_kube": {
        "tr": "Kubernetes Küme Erişim Anahtarları",
        "en": "Kubernetes Cluster Access Keys"
    },
    "sec_desc_aws": {
        "tr": "AWS CLI Kimlik ve Profilleri",
        "en": "AWS CLI Credentials and Profiles"
    },
    "sec_desc_docker": {
        "tr": "Docker Kimlik Yapılandırması",
        "en": "Docker Credentials Config"
    },
    "sec_desc_pypirc": {
        "tr": "PyPI Paket Yayınlama Anahtarı",
        "en": "PyPI Package Publish Credentials"
    },
    "sec_desc_dbeaver": {
        "tr": "DBeaver Veritabanı Bağlantıları",
        "en": "DBeaver Database Connections"
    },
    "sec_desc_fish_history": {
        "tr": "Fish Kabuk Komut Geçmişi",
        "en": "Fish Shell Command History"
    },
    "sec_desc_atuin": {
        "tr": "Atuin Kabuk Geçmişi Veritabanı",
        "en": "Atuin Shell History Database"
    },

    # Görünüm, Masaüstü, Tarayıcı ve Oyun
    "theme_desc_fonts_share": {
        "tr": "Yazı Tipleri (Fonts)",
        "en": "Fonts (~/.local/share/fonts)"
    },
    "theme_desc_fonts_home": {
        "tr": "Kullanıcı Yazı Tipleri",
        "en": "User Fonts (~/.fonts)"
    },
    "theme_desc_themes_share": {
        "tr": "Masaüstü Temaları",
        "en": "Desktop Themes (~/.local/share/themes)"
    },
    "theme_desc_themes_home": {
        "tr": "Kullanıcı Temaları (~/.themes)",
        "en": "User Themes (~/.themes)"
    },
    "theme_desc_icons_share": {
        "tr": "Simge Setleri (Icons)",
        "en": "Icon Sets (~/.local/share/icons)"
    },
    "theme_desc_icons_home": {
        "tr": "Kullanıcı Simgeleri (~/.icons)",
        "en": "User Icons (~/.icons)"
    },
    "theme_desc_wallpapers": {
        "tr": "Kullanıcı Duvar Kağıtları",
        "en": "User Wallpapers"
    },
    "theme_desc_desktop_entries": {
        "tr": "Özel Masaüstü Kısayolları (.desktop)",
        "en": "Custom Desktop Entries (.desktop)"
    },
    "theme_desc_gnome_ext": {
        "tr": "GNOME Shell Eklentileri (Extensions)",
        "en": "GNOME Shell Extensions"
    },
    "theme_desc_nautilus_scripts": {
        "tr": "Nautilus Sağ Tık Betikleri",
        "en": "Nautilus Right-Click Scripts"
    },
    "theme_desc_konsole": {
        "tr": "KDE Konsole Terminal Profilleri",
        "en": "KDE Konsole Profiles"
    },
    "theme_desc_plasma": {
        "tr": "KDE Plasma Widget ve Temaları",
        "en": "KDE Plasma Widgets and Themes"
    },
    "theme_desc_colors": {
        "tr": "KDE Renk Şemaları",
        "en": "KDE Color Schemes"
    },
    "theme_desc_kxmlgui": {
        "tr": "KDE Uygulama Kısayol Şemaları",
        "en": "KDE Application Shortcut Schemes"
    },
    "theme_desc_aurorae": {
        "tr": "KDE Pencere Dekorasyonları",
        "en": "KDE Window Decorations"
    },
    "browser_desc_firefox": {
        "tr": "Mozilla Firefox Profilleri & Yer İmleri",
        "en": "Mozilla Firefox Profiles & Bookmarks"
    },
    "browser_desc_thunderbird": {
        "tr": "Mozilla Thunderbird E-posta Profilleri",
        "en": "Mozilla Thunderbird Email Profiles"
    },
    "browser_desc_zen": {
        "tr": "Zen Browser Profilleri",
        "en": "Zen Browser Profiles"
    },
    "browser_desc_librewolf": {
        "tr": "LibreWolf Tarayıcı Profilleri",
        "en": "LibreWolf Browser Profiles"
    },
    "browser_desc_waterfox": {
        "tr": "Waterfox Tarayıcı Profilleri",
        "en": "Waterfox Browser Profiles"
    },
    "gaming_desc_lutris": {
        "tr": "Lutris Oyun Profilleri ve Verileri",
        "en": "Lutris Gaming Profiles & Data"
    },
    "gaming_desc_steam_compat": {
        "tr": "Özel Steam Proton-GE Sürümleri",
        "en": "Custom Steam Proton-GE Versions"
    },
    "gaming_desc_steam_root_compat": {
        "tr": "Steam Proton-GE Sürümleri (~/.steam)",
        "en": "Steam Proton-GE Versions (~/.steam)"
    },
    "flatpak_desc_overrides": {
        "tr": "Flatpak İzin Özelleştirmeleri",
        "en": "Flatpak Permission Overrides"
    },
    "flatpak_desc_app_data": {
        "tr": "Flatpak Uygulama Ayar & Verisi",
        "en": "Flatpak App Settings & Data"
    },

    # Custom Paths
    "custom_lbl_documents": {
        "tr": "Belgeler (Documents)",
        "en": "Documents"
    },
    "custom_lbl_documents_tr": {
        "tr": "Belgeler",
        "en": "Documents"
    },
    "custom_lbl_pictures": {
        "tr": "Resimler (Pictures)",
        "en": "Pictures"
    },
    "custom_lbl_pictures_tr": {
        "tr": "Resimler",
        "en": "Pictures"
    },
    "custom_lbl_projects": {
        "tr": "Projeler (Projects)",
        "en": "Projects"
    },
    "custom_lbl_src": {
        "tr": "Kaynak Kodlar (src)",
        "en": "Source Code (src)"
    },
    "custom_lbl_code": {
        "tr": "Kodlar (code)",
        "en": "Code (code)"
    },
    "custom_lbl_applications": {
        "tr": "Taşınabilir Uygulamalar / AppImages (~/Applications)",
        "en": "Portable Apps / AppImages (~/Applications)"
    },
    "custom_lbl_appimages": {
        "tr": "AppImage Uygulamaları (~/AppImages)",
        "en": "AppImage Applications (~/AppImages)"
    },
    "custom_lbl_games": {
        "tr": "Oyun Klasörü (~/Games)",
        "en": "Games Folder (~/Games)"
    },
    "custom_lbl_music": {
        "tr": "Müzikler (Music)",
        "en": "Music"
    },
    "custom_lbl_music_tr": {
        "tr": "Müzikler",
        "en": "Music"
    },
    "custom_lbl_videos": {
        "tr": "Videolar (Videos)",
        "en": "Videos"
    },
    "custom_lbl_videos_tr": {
        "tr": "Videolar",
        "en": "Videos"
    },
    "custom_lbl_desktop": {
        "tr": "Masaüstü Dosyaları",
        "en": "Desktop Files"
    },
    "custom_lbl_local_bin": {
        "tr": "Kullanıcı Betikleri (~/.local/bin)",
        "en": "User Scripts (~/.local/bin)"
    },
    "custom_lbl_bin": {
        "tr": "Kullanıcı Betikleri (~/bin)",
        "en": "User Scripts (~/bin)"
    },
    "custom_lbl_downloads": {
        "tr": "İndirilenler (Downloads)",
        "en": "Downloads"
    },

    # UI Metinleri ve Kısayollar
    "app_subtitle": {
        "tr": "Linux Dağıtım & Ayar Taşıyıcı",
        "en": "Linux Distro & Settings Migrator"
    },
    "binding_quit": {
        "tr": "Çıkış / Quit",
        "en": "Quit"
    },
    "binding_lang": {
        "tr": "Dil / Lang",
        "en": "Lang / Dil"
    },
    "binding_theme": {
        "tr": "Tema / Theme",
        "en": "Theme"
    },
    "binding_tab_overview": {
        "tr": "Genel Bakış",
        "en": "Overview"
    },
    "binding_tab_apps": {
        "tr": "Uygulamalar",
        "en": "Applications"
    },
    "binding_tab_configs": {
        "tr": "Ayarlar",
        "en": "Configurations"
    },
    "binding_tab_custom": {
        "tr": "Dosyalar",
        "en": "Files"
    },
    "binding_tab_backup": {
        "tr": "Yedekle",
        "en": "Backup"
    },
    "binding_tab_restore": {
        "tr": "Geri Yükle",
        "en": "Restore"
    },
    "binding_tab_help": {
        "tr": "Rehber",
        "en": "Guide"
    },
    "theme_prefix": {
        "tr": "Tema",
        "en": "Theme"
    },
    "theme_switched": {
        "tr": "Tema değiştirildi: {name}",
        "en": "Theme changed: {name}"
    },
    "lang_switched_tr": {
        "tr": "Dil değiştirildi: Türkçe",
        "en": "Language switched to Turkish"
    },
    "lang_switched_en": {
        "tr": "Language switched to English",
        "en": "Language switched to English"
    },
    "item_count_label": {
        "tr": "{count} öge",
        "en": "{count} items"
    },
    "file_count_label": {
        "tr": "{count} dosya",
        "en": "{count} files"
    },
    "target_system_unknown": {
        "tr": "Bilinmiyor",
        "en": "Unknown"
    },

    # Sepet ve Liste Bildirimleri
    "cart_notify_removed": {
        "tr": "🗑️ '{name}' sepetten çıkarıldı.",
        "en": "🗑️ '{name}' removed from cart."
    },
    "cart_notify_empty": {
        "tr": "Sepetiniz şu anda boş.",
        "en": "Your cart is currently empty."
    },
    "cart_notify_select_row": {
        "tr": "Lütfen sepetten çıkarmak için bir satır seçin.",
        "en": "Please select a row to remove from cart."
    },
    "cart_notify_failed": {
        "tr": "Seçili öge sepetten çıkarılamadı.",
        "en": "Failed to remove selected item."
    },
    "cart_notify_no_filtered": {
        "tr": "Sepette filtrelenen öge yok.",
        "en": "No filtered items in cart."
    },
    "cart_notify_filtered_removed": {
        "tr": "🧹 Filtrelenen {count} öge sepetten çıkarıldı.",
        "en": "🧹 Removed {count} filtered items from cart."
    },
    "cart_notify_cleared": {
        "tr": "⚠️ Tüm yedekleme sepeti boşaltıldı.",
        "en": "⚠️ Entire backup cart cleared."
    },
    "custom_notify_list_empty": {
        "tr": "Özel dosyalar listesi boş.",
        "en": "Custom paths list is empty."
    },
    "custom_notify_added": {
        "tr": "Eklendi: {name}",
        "en": "Added: {name}"
    },
    "custom_notify_not_found": {
        "tr": "Yol bulunamadı: {path}",
        "en": "Path not found: {path}"
    },
    "picker_notify_existing_backup_selected": {
        "tr": "Mevcut yedek seçildi: {name}",
        "en": "Existing backup selected: {name}"
    },
    "picker_notify_target_dir_selected": {
        "tr": "Hedef klasör seçildi: {name}",
        "en": "Target directory selected: {name}"
    },
    "picker_notify_base_backup_selected": {
        "tr": "Temel yedek seçildi: {name}",
        "en": "Base backup selected: {name}"
    },
    "scan_refreshed_notify": {
        "tr": "Sistem taraması yenilendi.",
        "en": "System scan refreshed."
    },

    # Geri Yükleme, İnceleme, Yedekleme Bildirimleri
    "restore_notify_select_or_inspect_first": {
        "tr": "Lütfen önce bir yedek dosyası seçin veya inceleyin!",
        "en": "Please select or inspect a backup file first!"
    },
    "restore_notify_backup_not_found": {
        "tr": "Yedek dosyası bulunamadı: {path}",
        "en": "Backup file not found: {path}"
    },
    "restore_notify_inspect_must_inspect_first": {
        "tr": "Önce geçerli bir yedek seçip 'Yedeği İncele' butonuna basmalısınız!",
        "en": "You must select a valid backup and click 'Inspect Backup' first!"
    },
    "restore_notify_no_items_selected": {
        "tr": "Geri yüklenecek hiçbir öge seçilmedi!",
        "en": "No items selected to restore!"
    },
    "restore_notify_in_progress_busy": {
        "tr": "Geri yükleme işlemi zaten devam ediyor!",
        "en": "Restore operation is already in progress!"
    },
    "restore_notify_inspect_while_restoring": {
        "tr": "Geri yükleme işlemi devam ederken yeni bir yedek incelenemez!",
        "en": "Cannot inspect a new backup while restore is in progress!"
    },
    "restore_notify_inspect_success": {
        "tr": "Yedek incelendi: {apps} uygulama, {configs} ayar bulundu.",
        "en": "Backup inspected: {apps} apps, {configs} configs found."
    },
    "restore_notify_success": {
        "tr": "Geri yükleme başarıyla tamamlandı!",
        "en": "Restore completed successfully!"
    },
    "restore_notify_inspect_first_warning": {
        "tr": "Önce bir yedek arşivini inceleyiniz!",
        "en": "Please inspect a backup archive first!"
    },
    "restore_notify_pwd_empty": {
        "tr": "Parola girilmedi, şifreli yedek incelenemedi.",
        "en": "Password not entered; encrypted backup could not be inspected."
    },
    "restore_notify_path_not_found": {
        "tr": "Belirtilen yol bulunamadı: {path}",
        "en": "Specified path not found: {path}"
    },
    "restore_notify_enter_path": {
        "tr": "Lütfen bir yedek arşivi veya dizin yolu girin!",
        "en": "Please enter a backup archive or directory path!"
    },
    "restore_notify_error": {
        "tr": "Hata: {error}",
        "en": "Error: {error}"
    },

    # Zamanlayıcı (Automation)
    "auto_notify_enabled": {
        "tr": "✔ Zamanlanmış otomatik yedekleme etkinleştirildi!",
        "en": "✔ Scheduled automatic backup enabled!"
    },
    "auto_notify_disabled": {
        "tr": "Zamanlanmış otomatik yedekleme kapatıldı.",
        "en": "Scheduled automatic backup disabled."
    },
    "auto_notify_failed": {
        "tr": "Zamanlanmış otomatik yedekleme işlemi başarısız veya eksik!",
        "en": "Scheduled automatic backup operation failed or incomplete!"
    },
    "auto_status_active": {
        "tr": "🟢 Aktif",
        "en": "🟢 Active"
    },
    "auto_status_inactive": {
        "tr": "🔴 Devre Dışı (Kapalı)",
        "en": "🔴 Inactive (Off)"
    },
    "auto_lbl_status": {
        "tr": "Mevcut Durum:",
        "en": "Current Status:"
    },
    "auto_lbl_interval": {
        "tr": "Periyot:",
        "en": "Interval:"
    },

    # Artımlı Önizleme
    "inc_preview_select_base": {
        "tr": "Önizleme için lütfen bir temel yedek yolu seçin!",
        "en": "Please select a base backup path for preview!"
    },
    "inc_preview_base_not_found": {
        "tr": "Temel yedek bulunamadı: {name}",
        "en": "Base backup not found: {name}"
    },
    "inc_preview_manifest_failed": {
        "tr": "Temel yedek manifesti okunamadı!",
        "en": "Could not read base backup manifest!"
    },
    "inc_preview_notify_found": {
        "tr": "Önizleme: {count} değişen öge bulundu (~{saved} tasarruf).",
        "en": "Preview: {count} changed items found (~{saved} saved)."
    },
    "inc_log_title": {
        "tr": "🔍 Artımlı Önizleme Analizi ({name}):",
        "en": "🔍 Incremental Preview Analysis ({name}):"
    },
    "inc_log_total_scanned": {
        "tr": "  • Toplam İncelenen: {count} dosya/öge",
        "en": "  • Total Scanned: {count} files/items"
    },
    "inc_log_changed_new": {
        "tr": "  • [bold yellow]Değişen / Yeni: {count} dosya[/bold yellow]",
        "en": "  • [bold yellow]Changed / New: {count} files[/bold yellow]"
    },
    "inc_log_unchanged": {
        "tr": "  • [bold green]Zaten Güncel (Atlanacak): {count} dosya[/bold green]",
        "en": "  • [bold green]Already Up-to-Date (Skipped): {count} files[/bold green]"
    },
    "inc_log_deleted": {
        "tr": "  • [bold red]Silinen (Temel Yedekten Çıkarılacak): {count} dosya[/bold red]",
        "en": "  • [bold red]Deleted (To Remove from Base): {count} files[/bold red]"
    },
    "inc_log_saved": {
        "tr": "  • [bold green]Tahmini Veri Tasarrufu: ~{saved}[/bold green]",
        "en": "  • [bold green]Estimated Data Savings: ~{saved}[/bold green]"
    },
    "inc_log_some_changed": {
        "tr": "[dim]Değişen dosyalardan bazıları:[/dim]",
        "en": "[dim]Some of the changed files:[/dim]"
    },

    # Yedekleme Bildirim ve Logları
    "backup_notify_already_running": {
        "tr": "Zaten devam eden bir yedekleme işlemi var!",
        "en": "A backup operation is already in progress!"
    },
    "backup_notify_enter_dest": {
        "tr": "Lütfen bir yedekleme hedef yolu girin!",
        "en": "Please enter a backup target path!"
    },
    "backup_notify_base_not_specified": {
        "tr": "Artımlı yedekleme için temel yedek yolu belirtilmedi!",
        "en": "Base backup path not specified for incremental backup!"
    },
    "backup_notify_append_dest_not_found": {
        "tr": "Güncellenecek mevcut yedek arşivi bulunamadı: {path}",
        "en": "Existing backup archive to update not found: {path}"
    },
    "backup_notify_failed": {
        "tr": "Yedekleme başarısız: {error}",
        "en": "Backup failed: {error}"
    },
    "backup_log_starting": {
        "tr": "[bold cyan]{mode} yedekleme süreci başlatılıyor...{enc}{ign}[/bold cyan]",
        "en": "[bold cyan]Starting {mode} backup process...{enc}{ign}[/bold cyan]"
    },
    "backup_log_completed": {
        "tr": "[bold green]✔ Yedekleme tamamlandı! Dosya: {path}[/bold green]",
        "en": "[bold green]✔ Backup completed! File: {path}[/bold green]"
    },
    "backup_log_error": {
        "tr": "[bold red]✖ Yedekleme hatası: {error}[/bold red]",
        "en": "[bold red]✖ Backup error: {error}[/bold red]"
    },

    # Geri Yükleme Logları
    "restore_log_inspecting": {
        "tr": "[cyan]Yedek inceleniyor: {name}...[/cyan]",
        "en": "[cyan]Inspecting backup: {name}...[/cyan]"
    },
    "restore_log_manifest_success": {
        "tr": "[green]✔ Manifest başarıyla okundu![/green]",
        "en": "[green]✔ Manifest read successfully![/green]"
    },
    "restore_log_source_distro": {
        "tr": "  • Kaynak Dağıtım: [bold]{distro}[/bold]",
        "en": "  • Source Distribution: [bold]{distro}[/bold]"
    },
    "restore_log_app_count": {
        "tr": "  • Kayıtlı Uygulama Sayısı: {count}",
        "en": "  • Recorded Applications: {count}"
    },
    "restore_log_config_count": {
        "tr": "  • Kayıtlı Konfigürasyon Sayısı: {count}",
        "en": "  • Recorded Configurations: {count}"
    },
    "restore_log_custom_count": {
        "tr": "  • Kayıtlı Özel Klasör Sayısı: {count}",
        "en": "  • Recorded Custom Folders: {count}"
    },
    "restore_log_diff_distro_warn": {
        "tr": "[bold yellow]⚠ DİKKAT: Yedek '{src}' dağıtımından alınmış. Şu anki sisteminiz ise '{dest}'. Paketler otomatik olarak akıllı paket eşleştirme tablosuyla dönüştürülecektir.[/bold yellow]",
        "en": "[bold yellow]⚠ WARNING: Backup was taken from '{src}'. Current system is '{dest}'. Packages will be automatically translated via package mapping table.[/bold yellow]"
    },
    "restore_log_select_prompt": {
        "tr": "[bold yellow]Aşağıdaki listelerden geri yüklemek istediklerinizi tek tek seçebilirsiniz.[/bold yellow]",
        "en": "[bold yellow]You can individually select what to restore from the lists below.[/bold yellow]"
    },
    "restore_log_inspect_error": {
        "tr": "[bold red]İnceleme hatası: {error}[/bold red]",
        "en": "[bold red]Inspection error: {error}[/bold red]"
    },
    "restore_log_starting": {
        "tr": "[bold cyan]Geri yükleme işlemi başlatılıyor...[/bold cyan]",
        "en": "[bold cyan]Starting restore process...[/bold cyan]"
    },
    "restore_log_selection_summary": {
        "tr": "Seçilenler: {pkgs} paket, {cfgs} ayar, {custom} klasör.",
        "en": "Selected: {pkgs} packages, {cfgs} configs, {custom} folders."
    },
    "restore_log_dry_run_warn": {
        "tr": "[yellow]UYARI: DRY-RUN Modunda çalışılıyor. Gerçek dosya veya paket değişikliği yapılmayacak.[/yellow]",
        "en": "[yellow]WARNING: Running in DRY-RUN mode. No real files or packages will be modified.[/yellow]"
    },
    "restore_log_completed": {
        "tr": "[bold green]✔ Geri yükleme süreci tamamlandı![/bold green]",
        "en": "[bold green]✔ Restore process completed![/bold green]"
    },
    "restore_log_restored_configs": {
        "tr": "  • Yüklenen Konfigürasyonlar: {count}",
        "en": "  • Restored Configurations: {count}"
    },
    "restore_log_restored_files": {
        "tr": "  • Yüklenen Dosyalar: {count}",
        "en": "  • Restored Files: {count}"
    },
    "restore_log_installed_pkgs": {
        "tr": "  • Kurulan/Planlanan Paketler: {count}",
        "en": "  • Installed/Scheduled Packages: {count}"
    },
    "restore_log_failed_pkgs_header": {
        "tr": "  • [bold red]Başarısız Paketler ({count}):[/bold red]",
        "en": "  • [bold red]Failed Packages ({count}):[/bold red]"
    },
    "restore_log_untranslated_pkgs_header": {
        "tr": "  • [bold yellow]Çevrilemeyen Paketler ({count}):[/bold yellow]",
        "en": "  • [bold yellow]Untranslated Packages ({count}):[/bold yellow]"
    },
    "restore_log_error": {
        "tr": "[bold red]✖ Geri yükleme hatası: {error}[/bold red]",
        "en": "[bold red]✖ Restore error: {error}[/bold red]"
    },

    # Rollback
    "rb_notify_no_tx": {
        "tr": "Geri alınabilecek kayıtlı bir işlem günlüğü bulunamadı!",
        "en": "No recorded transaction log found to roll back!"
    },
    "rb_notify_tx_read_failed": {
        "tr": "İşlem kaydı okunamadı: {error}",
        "en": "Could not read transaction log: {error}"
    },
    "rb_notify_completed": {
        "tr": "Rollback tamamlandı: {count} dosya geri yüklendi.",
        "en": "Rollback completed: {count} files restored."
    },
    "rb_notify_failed": {
        "tr": "Rollback hatası: {error}",
        "en": "Rollback error: {error}"
    },
    "rb_log_starting": {
        "tr": "[bold yellow]↩️ Geri alma işlemi başlatılıyor (Rollback)...[/bold yellow]",
        "en": "[bold yellow]↩️ Starting rollback process...[/bold yellow]"
    },
    "rb_log_success": {
        "tr": "[bold green]✔ Rollback başarıyla tamamlandı![/bold green]",
        "en": "[bold green]✔ Rollback completed successfully![/bold green]"
    },
    "rb_log_reverted_files": {
        "tr": "  • Eski haline getirilen dosyalar: {count}",
        "en": "  • Restored original files: {count}"
    },
    "rb_log_removed_created": {
        "tr": "  • Temizlenen yeni dosyalar: {count}",
        "en": "  • Cleaned new files: {count}"
    },
    "rb_log_error_item": {
        "tr": "[bold red]Hata: {error}[/bold red]",
        "en": "[bold red]Error: {error}[/bold red]"
    },
    "rb_log_overwrite_failed": {
        "tr": "[bold yellow]Geri alınamadı (overwrite): {path}[/bold yellow]",
        "en": "[bold yellow]Could not revert (overwrite): {path}[/bold yellow]"
    },

    # Doğrulama (Verify)
    "verify_notify_enter_path": {
        "tr": "Lütfen doğrulanacak bir yedek arşivi veya dizin yolu girin!",
        "en": "Please enter a backup archive or directory path to verify!"
    },
    "verify_notify_pwd_empty": {
        "tr": "Parola girilmedi, şifreli yedek doğrulanamadı.",
        "en": "Password not entered; encrypted backup could not be verified."
    },
    "verify_notify_error": {
        "tr": "Doğrulama hatası: {error}",
        "en": "Verification error: {error}"
    },
    "verify_log_error": {
        "tr": "[bold red]✖ Doğrulama hatası: {error}[/bold red]",
        "en": "[bold red]✖ Verification error: {error}[/bold red]"
    },

    # Modal Metinleri
    "picker_default_title": {
        "tr": "Dosya veya Klasör Seç",
        "en": "Select File or Folder"
    },
    "picker_btn_home": {
        "tr": "🏠 Ev (~)",
        "en": "🏠 Home (~)"
    },
    "picker_btn_docs": {
        "tr": "📄 Belgeler",
        "en": "📄 Documents"
    },
    "picker_btn_up": {
        "tr": "⬆ Üst Dizin",
        "en": "⬆ Parent"
    },
    "picker_btn_confirm": {
        "tr": "✔ Bu Konumu Seç",
        "en": "✔ Select This Location"
    },
    "picker_selected_path_prefix": {
        "tr": "Seçilen Konum:",
        "en": "Selected Path:"
    },
    "pwd_input_placeholder": {
        "tr": "Parola...",
        "en": "Password..."
    },
    "diff_no_differences": {
        "tr": "[green]Hiçbir fark bulunamadı. Yedek ile sistem dosyaları birebir aynı![/green]",
        "en": "[green]No differences found. Backup and system files are identical![/green]"
    },
    "diff_select_prompt": {
        "tr": "[cyan]Farkları görüntülemek için sol taraftaki listeden bir dosya seçin.[/cyan]",
        "en": "[cyan]Select a file from the list on the left to view differences.[/cyan]"
    },
    "rollback_modal_desc": {
        "tr": "Son geri yükleme kaydı:\n\n• Zaman Damgası: {timestamp}\n• Kaynak Yedek: {backup_src}\n• Değiştirilen/Oluşturulan Öge: {ops_count} adet\n\nBu işlem, yedeğe alınmış (.bak) dosyaları orijinal yerlerine taşıyacak ve sonradan kopyalanan yeni dosyaları silecektir.\n\nİşlemi geri almak istediğinize emin misiniz?",
        "en": "Last restore transaction details:\n\n• Timestamp: {timestamp}\n• Source Backup: {backup_src}\n• Modified/Created Items: {ops_count}\n\nThis action will move .bak backup files back to their original locations and remove newly restored files.\n\nAre you sure you want to revert this restore?"
    },
    "modal_password_title": {
        "tr": "Şifreli Yedek Arşivi",
        "en": "Encrypted Backup Archive"
    },
    "restore_pwd_prompt": {
        "tr": "'{name}' şifrelenmiştir. Lütfen parolanızı girin:",
        "en": "'{name}' is encrypted. Please enter your password:"
    },
    "verify_pwd_prompt": {
        "tr": "'{name}' şifrelenmiştir. Doğrulama için parolanızı girin:",
        "en": "'{name}' is encrypted. Please enter your password for verification:"
    },
    "picker_dest_dir_title": {
        "tr": "Yedek Kayıt Hedefi (Klasör) Seç",
        "en": "Select Backup Destination (Directory)"
    },
    "picker_base_backup_title": {
        "tr": "Karşılaştırılacak Temel Yedek Arşivini (.tar.zst, .tar.gz veya Dizin) Seç",
        "en": "Select Base Backup Archive (.tar.zst, .tar.gz, or Directory) to Compare"
    },
    "picker_restore_src_title": {
        "tr": "Geri Yüklenecek Yedek Arşivini (.tar.zst, .tar.gz veya Klasör) Seç",
        "en": "Select Backup Archive (.tar.zst, .tar.gz, or Directory) to Restore"
    },
    "picker_custom_title": {
        "tr": "Yedeklenecek Klasör veya Dosya Seç",
        "en": "Select Folder or File to Backup"
    },
    "backup_notify_append_selected": {
        "tr": "Mevcut yedek seçildi: {name}",
        "en": "Existing backup selected: {name}"
    },
    "backup_notify_dest_selected": {
        "tr": "Hedef klasör seçildi: {name}",
        "en": "Target directory selected: {name}"
    },
    "backup_notify_base_selected": {
        "tr": "Temel yedek seçildi: {name}",
        "en": "Base backup selected: {name}"
    },
    "backup_notify_append_not_found": {
        "tr": "Güncellenecek mevcut yedek arşivi bulunamadı: {path}",
        "en": "Existing backup archive to update not found: {path}"
    },
    "backup_mode_label_full": {
        "tr": "Tam (Full)",
        "en": "Full"
    },
    "backup_mode_label_incremental": {
        "tr": "Artımlı (Incremental)",
        "en": "Incremental"
    },
    "backup_mode_label_append": {
        "tr": "Mevcut Yedeğe Ekleme (Append)",
        "en": "Append to Existing Backup"
    },
    "backup_log_encrypted": {
        "tr": "🔐 AES-256 Şifreli",
        "en": "🔐 AES-256 Encrypted"
    },
    "backup_log_smart_filtered": {
        "tr": "🧹 Akıllı Filtreli",
        "en": "🧹 Smart Filtered"
    },
    "backup_log_failed": {
        "tr": "✖ Yedekleme hatası: {error}",
        "en": "✖ Backup error: {error}"
    },
    "hw_unknown": {
        "tr": "Bilinmiyor",
        "en": "Unknown"
    },
    "cart_notify_remove_failed": {
        "tr": "Seçili öge sepetten çıkarılamadı.",
        "en": "Could not remove selected item from cart."
    },
    "custom_notify_empty": {
        "tr": "Özel dosyalar listesi boş.",
        "en": "Custom files list is empty."
    },
    "notify_scan_refreshed": {
        "tr": "Sistem taraması yenilendi.",
        "en": "System scan refreshed."
    },
    "automation_notify_enabled": {
        "tr": "✔ Zamanlanmış otomatik yedekleme etkinleştirildi!",
        "en": "✔ Scheduled automatic backup enabled!"
    },
    "automation_notify_disabled": {
        "tr": "Zamanlanmış otomatik yedekleme kapatıldı.",
        "en": "Scheduled automatic backup disabled."
    },
    "automation_notify_failed": {
        "tr": "Zamanlanmış otomatik yedekleme işlemi başarısız veya eksik!",
        "en": "Scheduled automatic backup operation failed or incomplete!"
    },
    "inc_preview_manifest_error": {
        "tr": "Temel yedek manifesti okunamadı!",
        "en": "Could not read base backup manifest!"
    },
    "inc_preview_summary": {
        "tr": "Önizleme: {count} değişen öge bulundu (~{size} tasarruf).",
        "en": "Preview: {count} changed items found (~{size} saved)."
    },
    "inc_log_total": {
        "tr": "Toplam İncelenen: {count} dosya/öge",
        "en": "Total Inspected: {count} files/items"
    },
    "inc_log_changed": {
        "tr": "Değişen / Yeni: {count} dosya",
        "en": "Changed / New: {count} files"
    },
    "restore_notify_enter_src": {
        "tr": "Lütfen bir yedek arşivi veya dizin yolu girin!",
        "en": "Please enter a backup archive or directory path!"
    },
    "restore_notify_src_not_found": {
        "tr": "Belirtilen yol bulunamadı: {path}",
        "en": "Specified path not found: {path}"
    },
    "restore_notify_already_restoring": {
        "tr": "Geri yükleme işlemi devam ederken yeni bir işlem başlatılamaz!",
        "en": "Cannot start another operation while restore is in progress!"
    },
    "restore_summary_card": {
        "tr": "✔ Kaynak Dağıtım: [bold]{distro}[/bold] | Tarih: {date} | 📦 {pkgs} Uygulama | ⚙️ {cfgs} Konfigürasyon | 📁 {paths} Klasör",
        "en": "✔ Source Distro: [bold]{distro}[/bold] | Date: {date} | 📦 {pkgs} Apps | ⚙️ {cfgs} Configs | 📁 {paths} Folders"
    },
    "restore_log_saved_apps": {
        "tr": "Kayıtlı Uygulama Sayısı: {count}",
        "en": "Recorded Application Count: {count}"
    },
    "restore_log_saved_configs": {
        "tr": "Kayıtlı Konfigürasyon Sayısı: {count}",
        "en": "Recorded Configuration Count: {count}"
    },
    "restore_log_saved_paths": {
        "tr": "Kayıtlı Özel Klasör Sayısı: {count}",
        "en": "Recorded Custom Folder Count: {count}"
    },
    "restore_log_system_state": {
        "tr": "Sistem Durumu: dconf={dconf} | crontab={crontab} | Gruplar: {groups} | /etc Referansları: {etc}",
        "en": "System State: dconf={dconf} | crontab={crontab} | Groups: {groups} | /etc References: {etc}"
    },
    "restore_notify_inspected": {
        "tr": "Yedek incelendi: {pkgs} uygulama, {cfgs} ayar bulundu.",
        "en": "Backup inspected: {pkgs} apps, {cfgs} configs found."
    },
    "restore_notify_inspect_first": {
        "tr": "Önce bir yedek arşivini inceleyiniz!",
        "en": "Please inspect a backup archive first!"
    },
    "rb_notify_no_log": {
        "tr": "Geri alınabilecek kayıtlı bir işlem günlüğü bulunamadı!",
        "en": "No recorded transaction log found to roll back!"
    },
    "rb_notify_read_err": {
        "tr": "İşlem kaydı okunamadı: {error}",
        "en": "Could not read transaction log: {error}"
    },
    "rb_log_reverted": {
        "tr": "Eski haline getirilen dosyalar: {count}",
        "en": "Restored original files: {count}"
    },
    "rb_log_removed": {
        "tr": "Temizlenen yeni dosyalar: {count}",
        "en": "Cleaned new files: {count}"
    },
    "rb_log_error": {
        "tr": "Hata: {error}",
        "en": "Error: {error}"
    },
    "rb_log_failed_overwrite": {
        "tr": "Geri alınamadı (overwrite): {path}",
        "en": "Could not revert (overwrite): {path}"
    },
    "rb_notify_success": {
        "tr": "Rollback tamamlandı: {count} dosya geri yüklendi.",
        "en": "Rollback completed: {count} files restored."
    },
    "rb_log_fatal": {
        "tr": "Rollback hatası: {error}",
        "en": "Rollback error: {error}"
    },
    "rb_notify_fatal": {
        "tr": "Rollback hatası: {error}",
        "en": "Rollback error: {error}"
    },
    "restore_notify_inspect_required": {
        "tr": "Önce geçerli bir yedek seçip 'Yedeği İncele' butonuna basmalısınız!",
        "en": "You must select a valid backup and click 'Inspect Backup' first!"
    },
    "restore_notify_select_backup": {
        "tr": "Lütfen önce bir yedek dosyası seçin veya inceleyin!",
        "en": "Please select or inspect a backup file first!"
    },
    "restore_log_selection_count": {
        "tr": "Seçilenler: {pkgs} paket, {cfgs} ayar, {custom} klasör.",
        "en": "Selected: {pkgs} packages, {cfgs} configs, {custom} folders."
    },
    "restore_log_restored_cfgs": {
        "tr": "Yüklenen Konfigürasyonlar: {count}",
        "en": "Restored Configurations: {count}"
    },
    "restore_log_restored_pkgs": {
        "tr": "Kurulan/Planlanan Paketler: {count}",
        "en": "Installed/Scheduled Packages: {count}"
    },
    "restore_log_failed_pkgs": {
        "tr": "Başarısız Paketler ({count})",
        "en": "Failed Packages ({count})"
    },
    "restore_log_untranslated_pkgs": {
        "tr": "Çevrilemeyen Paketler ({count})",
        "en": "Untranslated Packages ({count})"
    },
    "verify_notify_path_not_found": {
        "tr": "Belirtilen yol bulunamadı: {path}",
        "en": "Specified path not found: {path}"
    },
    "verify_more_files": {
        "tr": "+{count} dosya daha",
        "en": "+{count} more files"
    },
    "verify_log_fatal": {
        "tr": "Doğrulama hatası: {error}",
        "en": "Verification error: {error}"
    },
    "verify_notify_fatal": {
        "tr": "Doğrulama hatası: {error}",
        "en": "Verification error: {error}"
    },
}


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Verilen anahtar için geçerli dildeki çeviriyi döndürür."""
    active_lang = normalize_language(lang) if lang else _CURRENT_LANGUAGE
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key

    val = entry.get(active_lang) or entry.get("tr") or key
    if kwargs:
        try:
            val = val.format(**kwargs)
        except Exception:
            pass
    return val
