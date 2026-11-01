"""
DistroNomad - Bağımsız (Standalone) Bash Geri Yükleme Betiği Üreteci.
"""

RESTORE_SCRIPT_TEMPLATE = r'''#!/usr/bin/env bash
# ==============================================================================
#  DistroNomad - Bağımsız Sistem Geri Yükleme Betiği / Standalone Restore Tool
# ==============================================================================
# Bu betik, yeni kurduğunuz Linux dağıtımında bağımsız olarak çalışabilir.
# Hiçbir ek Python kütüphanesine veya harici bağımlılığa ihtiyaç duymaz.
#
# This script can run standalone on your newly installed Linux distribution.
# It requires no additional Python libraries or external dependencies.
# ==============================================================================

set -eo pipefail

# ANSI Renk Kodları / ANSI Color Codes
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_CYAN="\033[1;36m"
C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"
C_RED="\033[1;31m"
C_MAGENTA="\033[1;35m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIGS_DIR="${SCRIPT_DIR}/configs"
CUSTOM_DIR="${SCRIPT_DIR}/custom"
PACKAGES_DIR="${SCRIPT_DIR}/packages"

TARGET_HOME="${HOME}"
DRY_RUN=false
DO_BACKUP=true
ALLOW_ROOT_PATHS=false
MODE="interactive" # interactive, all, configs, packages, files
FAILED_SYSTEM_PACKAGES=()

# Dil tespiti: LC_ALL -> LC_MESSAGES -> LANG 'tr' ile başlıyorsa Türkçe, aksi hâlde İngilizce
SCRIPT_LANG="en"
raw_env_locale=""
if [[ -n "${LC_ALL:-}" ]]; then
    raw_env_locale="${LC_ALL}"
elif [[ -n "${LC_MESSAGES:-}" ]]; then
    raw_env_locale="${LC_MESSAGES}"
elif [[ -n "${LANG:-}" ]]; then
    raw_env_locale="${LANG}"
fi

lower_env_locale="$(echo "${raw_env_locale}" | tr '[:upper:]' '[:lower:]')"
if [[ "${lower_env_locale}" =~ ^tr ]]; then
    SCRIPT_LANG="tr"
fi

# Komut satırındaki --lang seçeneğini önceden tara (help ve erken çıktılar için)
for ((i=1; i<=$#; i++)); do
    arg="${!i}"
    if [[ "$arg" == "--lang" ]]; then
        next_i=$((i+1))
        next_val="${!next_i:-}"
        if [[ "$next_val" == "tr" || "$next_val" == "en" ]]; then
            SCRIPT_LANG="$next_val"
        fi
    elif [[ "$arg" =~ ^--lang=(tr|en)$ ]]; then
        SCRIPT_LANG="${arg#--lang=}"
    fi
done

print_banner() {
    clear || true
    echo -e "${C_CYAN}"
    echo "  ╔══════════════════════════════════════════════════════════════════╗"
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo "  ║             DistroNomad - Sistem Geri Yükleme Aracı              ║"
    else
        echo "  ║             DistroNomad - System Restore Tool                    ║"
    fi
    echo "  ╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${C_RESET}"
}

log_info() {
    local msg="$1"
    if [[ $# -ge 2 && "$SCRIPT_LANG" != "tr" ]]; then
        msg="$2"
    fi
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e " ${C_CYAN}ℹ [BİLGİ]${C_RESET} ${msg}"
    else
        echo -e " ${C_CYAN}ℹ [INFO]${C_RESET} ${msg}"
    fi
}

log_success() {
    local msg="$1"
    if [[ $# -ge 2 && "$SCRIPT_LANG" != "tr" ]]; then
        msg="$2"
    fi
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e " ${C_GREEN}✔ [BAŞARILI]${C_RESET} ${msg}"
    else
        echo -e " ${C_GREEN}✔ [SUCCESS]${C_RESET} ${msg}"
    fi
}

log_warn() {
    local msg="$1"
    if [[ $# -ge 2 && "$SCRIPT_LANG" != "tr" ]]; then
        msg="$2"
    fi
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e " ${C_YELLOW}⚠ [UYARI]${C_RESET} ${msg}"
    else
        echo -e " ${C_YELLOW}⚠ [WARNING]${C_RESET} ${msg}"
    fi
}

log_error() {
    local msg="$1"
    if [[ $# -ge 2 && "$SCRIPT_LANG" != "tr" ]]; then
        msg="$2"
    fi
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e " ${C_RED}✖ [HATA]${C_RESET} ${msg}"
    else
        echo -e " ${C_RED}✖ [ERROR]${C_RESET} ${msg}"
    fi
}

# Parametreleri işle
while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            MODE="all"
            shift
            ;;
        --configs-only)
            MODE="configs"
            shift
            ;;
        --packages-only)
            MODE="packages"
            shift
            ;;
        --files-only)
            MODE="files"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-backup)
            DO_BACKUP=false
            shift
            ;;
        --target-home)
            TARGET_HOME="$2"
            shift 2
            ;;
        --allow-root-paths|--allow-external-paths)
            ALLOW_ROOT_PATHS=true
            shift
            ;;
        --lang)
            if [[ "${2:-}" == "tr" || "${2:-}" == "en" ]]; then
                SCRIPT_LANG="$2"
                shift 2
            else
                log_error "Geçersiz dil seçeneği: ${2:-} (Desteklenen: tr, en)" "Invalid language option: ${2:-} (Supported: tr, en)"
                exit 1
            fi
            ;;
        --lang=*)
            val="${1#--lang=}"
            if [[ "$val" == "tr" || "$val" == "en" ]]; then
                SCRIPT_LANG="$val"
                shift
            else
                log_error "Geçersiz dil seçeneği: $val (Desteklenen: tr, en)" "Invalid language option: $val (Supported: tr, en)"
                exit 1
            fi
            ;;
        -h|--help)
            if [[ "$SCRIPT_LANG" = "tr" ]]; then
                echo "Kullanım: $0 [SEÇENEKLER]"
                echo "  --all             Her şeyi (konfigürasyonlar, dosyalar, paketler) geri yükle"
                echo "  --configs-only    Sadece ~/.config ve dotfile yapılandırmalarını yükle"
                echo "  --packages-only   Sadece paketleri/uygulamaları kur"
                echo "  --files-only      Sadece özel dosyaları/klasörleri yükle"
                echo "  --dry-run         Değişiklik yapmadan test et (simülasyon)"
                echo "  --no-backup       Mevcut dosyaları .bak olarak yedekleme"
                echo "  --target-home DIR Hedef kullanıcı ev dizini (varsayılan: $HOME)"
                echo "  --allow-root-paths Ev dizini dışındaki özel yolları (custom/root) geri yükle"
                echo "  --lang tr|en      Arayüz dili (varsayılan: sistem dili / tr)"
                echo "  -h, --help        Bu yardım mesajını göster ve çık"
            else
                echo "Usage: $0 [OPTIONS]"
                echo "  --all             Restore everything (configurations, files, packages)"
                echo "  --configs-only    Restore only ~/.config and dotfile configurations"
                echo "  --packages-only   Install only packages/applications"
                echo "  --files-only      Restore only custom files/folders"
                echo "  --dry-run         Test without making changes (simulation)"
                echo "  --no-backup       Do not backup existing files as .bak"
                echo "  --target-home DIR Target user home directory (default: $HOME)"
                echo "  --allow-root-paths Restore custom paths outside home directory (custom/root)"
                echo "  --lang tr|en      Interface language (default: system language / en)"
                echo "  -h, --help        Show this help message and exit"
            fi
            exit 0
            ;;
        *)
            log_error "Bilinmeyen seçenek: $1" "Unknown option: $1"
            exit 1
            ;;
    esac
done

detect_current_os() {
    DETECTED_PM=""
    DETECTED_DISTRO="Linux"
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        DETECTED_DISTRO="${NAME:-Linux}"
    fi

    if command -v pacman &>/dev/null; then
        DETECTED_PM="pacman"
    elif command -v apt-get &>/dev/null; then
        DETECTED_PM="apt"
    elif command -v dnf &>/dev/null; then
        DETECTED_PM="dnf"
    elif command -v zypper &>/dev/null; then
        DETECTED_PM="zypper"
    elif command -v apk &>/dev/null; then
        DETECTED_PM="apk"
    fi
}

restore_configs() {
    echo ""
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e "${C_BOLD}${C_MAGENTA}=== 1. KONFİGÜRASYONLAR VE AYARLAR (DOTFILES) ===${C_RESET}"
    else
        echo -e "${C_BOLD}${C_MAGENTA}=== 1. CONFIGURATIONS AND SETTINGS (DOTFILES) ===${C_RESET}"
    fi

    if [[ ! -d "${CONFIGS_DIR}" ]]; then
        log_warn "Yedek içinde konfigürasyon dizini bulunamadı." "Configuration directory not found in backup."
        return
    fi

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local resolved_home=""
    if command -v realpath &>/dev/null; then
        resolved_home="$(realpath -m -- "${TARGET_HOME}")"
    fi

    # Dizinleri ve dosyaları hedef HOME dizinine kopyala
    find "${CONFIGS_DIR}" -mindepth 1 | while read -r src_item; do
        rel_path="${src_item#"${CONFIGS_DIR}"/}"
        target_item="${TARGET_HOME}/${rel_path}"

        if [[ -n "${resolved_home}" ]]; then
            resolved_target="$(realpath -m -- "${target_item}")"
            if [[ "${resolved_target}" != "${resolved_home}"/* ]]; then
                log_warn "Güvenlik uyarısı: Hedef ev dizini dışına çıkma engellendi, atlandı: ${target_item}" "Security warning: Directory traversal outside target home prevented, skipped: ${target_item}"
                continue
            fi
        fi

        if [[ -d "${src_item}" ]]; then
            if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                if [[ ! -d "${target_item}" ]]; then
                    if [[ "$DO_BACKUP" = true ]]; then
                        if [[ "$DRY_RUN" = true ]]; then
                            log_info "[DRY-RUN] Mevcut dosya/bağ yedeklenecek: ${target_item} -> ${target_item}.bak.${timestamp}" "[DRY-RUN] Existing file/symlink will be backed up: ${target_item} -> ${target_item}.bak.${timestamp}"
                        else
                            cp -a "${target_item}" "${target_item}.bak.${timestamp}"
                            rm -f "${target_item}"
                        fi
                    else
                        [[ "$DRY_RUN" = true ]] || rm -f "${target_item}"
                    fi
                fi
            fi
            if [[ "$DRY_RUN" = true ]]; then
                log_info "[DRY-RUN] Dizin oluşturulacak: ${target_item}" "[DRY-RUN] Directory will be created: ${target_item}"
            else
                mkdir -p "${target_item}"
            fi
        elif [[ -f "${src_item}" || -L "${src_item}" ]]; then
            if [[ "$DRY_RUN" != true ]]; then
                mkdir -p "$(dirname "${target_item}")"
            fi
            if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                if [[ "$DO_BACKUP" = true ]]; then
                    if [[ "$DRY_RUN" = true ]]; then
                        log_info "[DRY-RUN] Mevcut dosya/dizin yedeklenecek: ${target_item} -> ${target_item}.bak.${timestamp}" "[DRY-RUN] Existing file/directory will be backed up: ${target_item} -> ${target_item}.bak.${timestamp}"
                    else
                        cp -a "${target_item}" "${target_item}.bak.${timestamp}"
                        rm -rf "${target_item}"
                    fi
                else
                    [[ "$DRY_RUN" = true ]] || rm -rf "${target_item}"
                fi
            fi

            if [[ "$DRY_RUN" = true ]]; then
                log_info "[DRY-RUN] Dosya yüklenecek: ${rel_path} -> ${target_item}" "[DRY-RUN] File will be restored: ${rel_path} -> ${target_item}"
            else
                cp -a "${src_item}" "${target_item}"
            fi
        fi
    done

    log_success "Konfigürasyonlar başarıyla ${TARGET_HOME} dizinine aktarıldı!" "Configurations successfully restored to ${TARGET_HOME}!"
}

restore_custom_files() {
    echo ""
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e "${C_BOLD}${C_MAGENTA}=== 2. ÖZEL DOSYALAR VE KLASÖRLER ===${C_RESET}"
    else
        echo -e "${C_BOLD}${C_MAGENTA}=== 2. CUSTOM FILES AND FOLDERS ===${C_RESET}"
    fi

    if [[ ! -d "${CUSTOM_DIR}" ]]; then
        log_info "Yedek içinde özel dosya dizini bulunamadı veya boş." "Custom files directory not found in backup or empty."
        return
    fi

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)

    # 1. Ev dizinine göreli dosyalar (custom/home)
    if [[ -d "${CUSTOM_DIR}/home" ]]; then
        local resolved_home=""
        if command -v realpath &>/dev/null; then
            resolved_home="$(realpath -m -- "${TARGET_HOME}")"
        fi

        find "${CUSTOM_DIR}/home" -mindepth 1 | while read -r src_item; do
            rel_path="${src_item#"${CUSTOM_DIR}"/home/}"
            target_item="${TARGET_HOME}/${rel_path}"

            if [[ -n "${resolved_home}" ]]; then
                resolved_target="$(realpath -m -- "${target_item}")"
                if [[ "${resolved_target}" != "${resolved_home}"/* ]]; then
                    log_warn "Güvenlik uyarısı: Hedef ev dizini dışına çıkma engellendi, atlandı: ${target_item}" "Security warning: Directory traversal outside target home prevented, skipped: ${target_item}"
                    continue
                fi
            fi

            if [[ -d "${src_item}" ]]; then
                if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                    if [[ ! -d "${target_item}" ]]; then
                        if [[ "$DO_BACKUP" = true ]]; then
                            if [[ "$DRY_RUN" = true ]]; then
                                log_info "[DRY-RUN] Mevcut dosya/bağ yedeklenecek: ${target_item} -> ${target_item}.bak.${timestamp}" "[DRY-RUN] Existing file/symlink will be backed up: ${target_item} -> ${target_item}.bak.${timestamp}"
                            else
                                cp -a "${target_item}" "${target_item}.bak.${timestamp}"
                                rm -f "${target_item}"
                            fi
                        else
                            [[ "$DRY_RUN" = true ]] || rm -f "${target_item}"
                        fi
                    fi
                fi
                if [[ "$DRY_RUN" = true ]]; then
                    log_info "[DRY-RUN] Dizin oluşturulacak: ${target_item}" "[DRY-RUN] Directory will be created: ${target_item}"
                else
                    mkdir -p "${target_item}"
                fi
            elif [[ -f "${src_item}" || -L "${src_item}" ]]; then
                if [[ "$DRY_RUN" != true ]]; then
                    mkdir -p "$(dirname "${target_item}")"
                fi
                if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                    if [[ "$DO_BACKUP" = true ]]; then
                        if [[ "$DRY_RUN" = true ]]; then
                            log_info "[DRY-RUN] Mevcut dosya/dizin yedeklenecek: ${target_item} -> ${target_item}.bak.${timestamp}" "[DRY-RUN] Existing file/directory will be backed up: ${target_item} -> ${target_item}.bak.${timestamp}"
                        else
                            cp -a "${target_item}" "${target_item}.bak.${timestamp}"
                            rm -rf "${target_item}"
                        fi
                    else
                        [[ "$DRY_RUN" = true ]] || rm -rf "${target_item}"
                    fi
                fi

                if [[ "$DRY_RUN" = true ]]; then
                    log_info "[DRY-RUN] Dosya yüklenecek: ${rel_path} -> ${target_item}" "[DRY-RUN] File will be restored: ${rel_path} -> ${target_item}"
                else
                    cp -a "${src_item}" "${target_item}"
                fi
            fi
        done
    fi

    # 2. Mutlak sistem yolları (custom/root)
    if [[ -d "${CUSTOM_DIR}/root" ]]; then
        local is_custom_target=false
        local resolved_target_home=""
        if command -v realpath &>/dev/null; then
            resolved_target_home="$(realpath -m -- "${TARGET_HOME}")"
            local real_home_resolved
            real_home_resolved="$(realpath -m -- "${HOME}")"
            if [[ -n "${resolved_target_home}" && "${resolved_target_home}" != "${real_home_resolved}" ]]; then
                is_custom_target=true
            fi
        fi
        if [[ "${TARGET_HOME}" != "${HOME}" ]]; then
            is_custom_target=true
        fi

        find "${CUSTOM_DIR}/root" -mindepth 1 | while read -r src_item; do
            rel_path="${src_item#"${CUSTOM_DIR}"/root/}"
            target_item="/${rel_path}"

            # Güvenlik ve Karar 3: Hedef ev dizini özel bir dizin olarak belirtildiğinden (--target-home vb.) asla köke yazma
            if [[ "$is_custom_target" = true ]]; then
                log_warn "Hedef ev dizini özel bir dizin olarak belirtildiğinden (${TARGET_HOME}), mutlak kök yolu (/...) ana sisteme yazılamaz, atlandı: ${rel_path}" "Since target home is specified as a custom directory (${TARGET_HOME}), absolute root path (/...) cannot be written to host system, skipped: ${rel_path}"
                continue
            fi

            # Karar 3: Ev dizini dışındaki özel yollar varsayılan atlanır, yalnızca açık onayla yazılır
            if [[ "$ALLOW_ROOT_PATHS" != true ]]; then
                if [[ "$MODE" = "interactive" ]]; then
                    local confirm_root=""
                    if [[ "$SCRIPT_LANG" = "tr" ]]; then
                        read -rp "Ev dışı sistem yolu (/${rel_path}) geri yüklensin mi? [e/H]: " confirm_root || true
                    else
                        read -rp "Restore external system path (/${rel_path})? [y/N]: " confirm_root || true
                    fi
                    if [[ ! "$confirm_root" =~ ^[eEyY]$ ]]; then
                        log_info "Ev dışı yol atlandı (onay verilmedi): /${rel_path}" "External path skipped (not confirmed): /${rel_path}"
                        continue
                    fi
                else
                    log_warn "Ev dışı yol varsayılan olarak atlandı (--allow-root-paths gereklidir): /${rel_path}" "External path skipped by default (--allow-root-paths required): /${rel_path}"
                    continue
                fi
            fi

            # Yazılabilirlik kontrolü (sudo KULLANMA, atla ve uyarı ver)
            is_writable=false
            if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                if [[ -w "${target_item}" ]]; then
                    is_writable=true
                fi
            else
                parent_dir="$(dirname "${target_item}")"
                while [[ ! -d "$parent_dir" && "$parent_dir" != "/" ]]; do
                    parent_dir="$(dirname "$parent_dir")"
                done
                if [[ -w "$parent_dir" ]]; then
                    is_writable=true
                fi
            fi

            if [[ "$is_writable" != true ]]; then
                log_warn "Hedef yazılabilir değil, atlandı (sudo kullanılmadı): ${target_item}" "Target is not writable, skipped (sudo was not used): ${target_item}"
                continue
            fi

            if [[ -d "${src_item}" ]]; then
                if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                    if [[ ! -d "${target_item}" ]]; then
                        if [[ "$DO_BACKUP" = true ]]; then
                            if [[ "$DRY_RUN" = true ]]; then
                                log_info "[DRY-RUN] Mevcut dosya/bağ yedeklenecek: ${target_item} -> ${target_item}.bak.${timestamp}" "[DRY-RUN] Existing file/symlink will be backed up: ${target_item} -> ${target_item}.bak.${timestamp}"
                            else
                                cp -a "${target_item}" "${target_item}.bak.${timestamp}"
                                rm -f "${target_item}"
                            fi
                        else
                            [[ "$DRY_RUN" = true ]] || rm -f "${target_item}"
                        fi
                    fi
                fi
                if [[ "$DRY_RUN" = true ]]; then
                    log_info "[DRY-RUN] Dizin oluşturulacak: ${target_item}" "[DRY-RUN] Directory will be created: ${target_item}"
                else
                    mkdir -p "${target_item}"
                fi
            elif [[ -f "${src_item}" || -L "${src_item}" ]]; then
                if [[ "$DRY_RUN" != true ]]; then
                    mkdir -p "$(dirname "${target_item}")"
                fi
                if [[ -e "${target_item}" || -L "${target_item}" ]]; then
                    if [[ "$DO_BACKUP" = true ]]; then
                        if [[ "$DRY_RUN" = true ]]; then
                            log_info "[DRY-RUN] Mevcut dosya/dizin yedeklenecek: ${target_item} -> ${target_item}.bak.${timestamp}" "[DRY-RUN] Existing file/directory will be backed up: ${target_item} -> ${target_item}.bak.${timestamp}"
                        else
                            cp -a "${target_item}" "${target_item}.bak.${timestamp}"
                            rm -rf "${target_item}"
                        fi
                    else
                        [[ "$DRY_RUN" = true ]] || rm -rf "${target_item}"
                    fi
                fi

                if [[ "$DRY_RUN" = true ]]; then
                    log_info "[DRY-RUN] Dosya yüklenecek: /${rel_path} -> ${target_item}" "[DRY-RUN] File will be restored: /${rel_path} -> ${target_item}"
                else
                    cp -a "${src_item}" "${target_item}"
                fi
            fi
        done
    fi

    log_success "Özel dosyalar ve klasörler aktarıldı!" "Custom files and folders restored!"
}

install_packages() {
    echo ""
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e "${C_BOLD}${C_MAGENTA}=== 3. UYGULAMALAR VE PAKETLER ===${C_RESET}"
    else
        echo -e "${C_BOLD}${C_MAGENTA}=== 3. APPLICATIONS AND PACKAGES ===${C_RESET}"
    fi

    detect_current_os
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        log_info "Mevcut Dağıtım: ${DETECTED_DISTRO} (Paket Yöneticisi: ${DETECTED_PM:-Bilinmiyor})"
    else
        log_info "Current Distribution: ${DETECTED_DISTRO} (Package Manager: ${DETECTED_PM:-Unknown})"
    fi

    local LC_ALL=C
    # 1. Native Paketler
    local pkg_file="${PACKAGES_DIR}/system_packages.txt"
    if [[ -f "${pkg_file}" && -n "${DETECTED_PM}" ]]; then
        mapfile -t raw_pkgs < <(grep -v '^[[:space:]]*#' "${pkg_file}" | grep -v '^[[:space:]]*$' || true)
        if [[ ${#raw_pkgs[@]} -gt 0 ]]; then
            local valid_pkgs=()
            for p in "${raw_pkgs[@]}"; do
                if [[ "$p" =~ ^- ]]; then
                    log_warn "Geçersiz paket adı (eksi ile başlayamaz), atlandı: $p" "Invalid package name (cannot start with a dash), skipped: $p"
                    continue
                fi
                if [[ ! "$p" =~ ^[a-zA-Z0-9._+@:-]+$ ]]; then
                    log_warn "Geçersiz karakter içeren paket adı atlandı: $p" "Package name with invalid characters skipped: $p"
                    continue
                fi
                valid_pkgs+=("$p")
            done

            if [[ ${#valid_pkgs[@]} -eq 0 ]]; then
                log_info "Kurulacak geçerli paket bulunamadı." "No valid packages found to install."
            else
                if [[ "$SCRIPT_LANG" = "tr" ]]; then
                    echo -e "${C_CYAN}Kurulacak paket sayısı: ${#valid_pkgs[@]}${C_RESET}"
                else
                    echo -e "${C_CYAN}Number of packages to install: ${#valid_pkgs[@]}${C_RESET}"
                fi

                local cmd=()
                case "${DETECTED_PM}" in
                    pacman)
                        cmd=(sudo pacman -S --needed --noconfirm -- "${valid_pkgs[@]}")
                        ;;
                    apt)
                        cmd=(sudo apt-get install -y -- "${valid_pkgs[@]}")
                        ;;
                    dnf)
                        cmd=(sudo dnf install -y -- "${valid_pkgs[@]}")
                        ;;
                    zypper)
                        cmd=(sudo zypper install -y -- "${valid_pkgs[@]}")
                        ;;
                esac

                if [[ ${#cmd[@]} -gt 0 ]]; then
                    if [[ "$SCRIPT_LANG" = "tr" ]]; then
                        echo -e "${C_YELLOW}Çalıştırılacak komut:${C_RESET} ${cmd[*]}"
                    else
                        echo -e "${C_YELLOW}Command to run:${C_RESET} ${cmd[*]}"
                    fi

                    if [[ "$DRY_RUN" = true ]]; then
                        log_info "[DRY-RUN] Paket kurulum komutu çalıştırılmadı." "[DRY-RUN] Package installation command not executed."
                    else
                        local run_install=false
                        if [[ "$MODE" = "interactive" ]]; then
                            local confirm=""
                            if [[ "$SCRIPT_LANG" = "tr" ]]; then
                                read -rp "Bu paketleri şimdi kurmak istiyor musunuz? [e/H]: " confirm || true
                            else
                                read -rp "Do you want to install these packages now? [y/N]: " confirm || true
                            fi
                            if [[ "$confirm" =~ ^[eEyY]$ ]]; then
                                run_install=true
                            else
                                log_info "Paket kurulumu atlandı." "Package installation skipped."
                            fi
                        else
                            run_install=true
                        fi

                        if [[ "$run_install" = true ]]; then
                            if [[ "${DETECTED_PM}" = "apt" ]]; then
                                if [[ "$SCRIPT_LANG" = "tr" ]]; then
                                    sudo apt-get update || log_warn "apt-get update başarısız oldu."
                                else
                                    sudo apt-get update || log_warn "apt-get update failed."
                                fi
                            fi

                            local failed_pkgs=()
                            set +e
                            "${cmd[@]}"
                            local batch_rc=$?
                            if [[ $batch_rc -eq 0 ]]; then
                                log_success "Tüm sistem paketleri başarıyla kuruldu." "All system packages installed successfully."
                            else
                                log_warn "Toplu paket kurulumu başarısız oldu, paketler tek tek deneniyor..." "Batch package installation failed, trying packages individually..."
                                for p in "${valid_pkgs[@]}"; do
                                    local single_cmd=()
                                    case "${DETECTED_PM}" in
                                        pacman)
                                            single_cmd=(sudo pacman -S --needed --noconfirm -- "$p")
                                            ;;
                                        apt)
                                            single_cmd=(sudo apt-get install -y -- "$p")
                                            ;;
                                        dnf)
                                            single_cmd=(sudo dnf install -y -- "$p")
                                            ;;
                                        zypper)
                                            single_cmd=(sudo zypper install -y -- "$p")
                                            ;;
                                    esac

                                    if [[ ${#single_cmd[@]} -gt 0 ]]; then
                                        log_info "Paket kuruluyor: $p" "Installing package: $p"
                                        if "${single_cmd[@]}"; then
                                            log_success "Kuruldu: $p" "Installed: $p"
                                        else
                                            log_warn "Kurulamadı: $p" "Failed to install: $p"
                                            failed_pkgs+=("$p")
                                        fi
                                    fi
                                done
                            fi
                            set -e

                            if [[ ${#failed_pkgs[@]} -gt 0 ]]; then
                                FAILED_SYSTEM_PACKAGES+=("${failed_pkgs[@]}")
                                echo ""
                                if [[ "$SCRIPT_LANG" = "tr" ]]; then
                                    log_warn "Kurulamayan paketler özeti (${#failed_pkgs[@]} paket):"
                                else
                                    log_warn "Failed packages summary (${#failed_pkgs[@]} packages):"
                                fi
                                for fp in "${failed_pkgs[@]}"; do
                                    echo -e "  ${C_RED}✖${C_RESET} ${fp}"
                                done
                            fi
                        fi
                    fi
                fi
            fi
        fi
    fi

    # 2. Flatpak Uygulamaları
    local flatpak_file="${PACKAGES_DIR}/flatpaks.txt"
    if [[ -f "${flatpak_file}" ]] && command -v flatpak &>/dev/null; then
        mapfile -t flatpaks < <(grep -v '^[[:space:]]*#' "${flatpak_file}" | grep -v '^[[:space:]]*$' || true)
        if [[ ${#flatpaks[@]} -gt 0 ]]; then
            if [[ "$SCRIPT_LANG" = "tr" ]]; then
                echo -e "${C_CYAN}Kurulacak Flatpak uygulama sayısı: ${#flatpaks[@]}${C_RESET}"
            else
                echo -e "${C_CYAN}Number of Flatpak applications to install: ${#flatpaks[@]}${C_RESET}"
            fi

            if [[ "$DRY_RUN" = true ]]; then
                log_info "[DRY-RUN] Flatpak kurulumları simüle edildi." "[DRY-RUN] Flatpak installations simulated."
            else
                local run_fp=true
                if [[ "$MODE" = "interactive" ]]; then
                    local confirm_fp=""
                    if [[ "$SCRIPT_LANG" = "tr" ]]; then
                        read -rp "Flatpak uygulamalarını şimdi kurmak istiyor musunuz? [e/H]: " confirm_fp || true
                    else
                        read -rp "Do you want to install Flatpak applications now? [y/N]: " confirm_fp || true
                    fi
                    [[ "$confirm_fp" =~ ^[eEyY]$ ]] || run_fp=false
                fi
                if [[ "$run_fp" = true ]]; then
                    for fp in "${flatpaks[@]}"; do
                        if [[ "$fp" =~ ^- || ! "$fp" =~ ^[a-zA-Z0-9._-]+$ ]]; then
                            log_warn "Geçersiz Flatpak uygulama adı atlandı: $fp" "Invalid Flatpak application name skipped: $fp"
                            continue
                        fi
                        log_info "Flatpak kuruluyor: $fp" "Installing Flatpak: $fp"
                        if [[ "$SCRIPT_LANG" = "tr" ]]; then
                            flatpak install -y flathub -- "$fp" || log_warn "$fp kurulamadı."
                        else
                            flatpak install -y flathub -- "$fp" || log_warn "Failed to install $fp."
                        fi
                    done
                fi
            fi
        fi
    fi

    # 3. VS Code / VSCodium Eklentileri
    local vscode_file="${PACKAGES_DIR}/vscode_extensions.txt"
    local code_bin=""
    if command -v code &>/dev/null; then
        code_bin="code"
    elif command -v codium &>/dev/null; then
        code_bin="codium"
    fi
    if [[ -f "${vscode_file}" && -n "${code_bin}" ]]; then
        mapfile -t vsexts < <(grep -v '^[[:space:]]*#' "${vscode_file}" | grep -v '^[[:space:]]*$' || true)
        if [[ ${#vsexts[@]} -gt 0 ]]; then
            if [[ "$SCRIPT_LANG" = "tr" ]]; then
                echo -e "${C_CYAN}Kurulacak VS Code eklenti sayısı: ${#vsexts[@]}${C_RESET}"
            else
                echo -e "${C_CYAN}Number of VS Code extensions to install: ${#vsexts[@]}${C_RESET}"
            fi

            if [[ "$DRY_RUN" = true ]]; then
                log_info "[DRY-RUN] VS Code eklenti kurulumu simüle edildi." "[DRY-RUN] VS Code extension installation simulated."
            else
                for ext in "${vsexts[@]}"; do
                    if [[ "$ext" =~ ^- || ! "$ext" =~ ^[a-zA-Z0-9._-]+$ ]]; then
                        log_warn "Geçersiz eklenti adı atlandı: $ext" "Invalid extension name skipped: $ext"
                        continue
                    fi
                    if [[ "$SCRIPT_LANG" = "tr" ]]; then
                        "${code_bin}" --install-extension "$ext" || log_warn "Eklenti kurulamadı: $ext"
                    else
                        "${code_bin}" --install-extension "$ext" || log_warn "Failed to install extension: $ext"
                    fi
                done
            fi
        fi
    fi
}

restore_system_state() {
    local state_dir="${SCRIPT_DIR}/system_state"
    if [[ ! -d "${state_dir}" ]]; then
        return
    fi

    echo ""
    if [[ "$SCRIPT_LANG" = "tr" ]]; then
        echo -e "${C_BOLD}${C_MAGENTA}=== 4. SİSTEM DURUMU VE REFERANS DOSYALARI ===${C_RESET}"
    else
        echo -e "${C_BOLD}${C_MAGENTA}=== 4. SYSTEM STATE AND REFERENCE FILES ===${C_RESET}"
    fi
    local ref_dest="${TARGET_HOME}/.local/share/distronomad/system_state_reference"

    if [[ "$DRY_RUN" = true ]]; then
        log_info "[DRY-RUN] Sistem referans dosyaları kopyalanacak: ${ref_dest}" "[DRY-RUN] System reference files will be copied: ${ref_dest}"
    else
        mkdir -p "${ref_dest}"
        cp -a "${state_dir}/." "${ref_dest}/"
        log_success "Sistem durum ve /etc referans dosyaları kaydedildi: ${ref_dest}" "System state and /etc reference files saved: ${ref_dest}"
    fi

    if [[ -f "${state_dir}/user_groups.txt" ]]; then
        local grp_list
        grp_list=$(tr '\n' ',' < "${state_dir}/user_groups.txt" | sed 's/,$//')
        log_info "Önceki sistemdeki kullanıcı gruplarınız: ${grp_list}" "Your user groups on the previous system: ${grp_list}"
    fi
}

main() {
    print_banner
    log_info "Yedek Kaynağı: ${SCRIPT_DIR}" "Backup Source: ${SCRIPT_DIR}"
    log_info "Hedef Ev Dizini: ${TARGET_HOME}" "Target Home Directory: ${TARGET_HOME}"
    if [[ "$DRY_RUN" = true ]]; then
        log_warn "DİKKAT: DRY-RUN (Simülasyon) modunda çalışılıyor. Hiçbir dosya değiştirilmeyecek." "ATTENTION: Running in DRY-RUN (Simulation) mode. No files will be modified."
    fi

    case "$MODE" in
        all)
            restore_configs
            restore_custom_files
            install_packages
            restore_system_state
            ;;
        configs)
            restore_configs
            restore_system_state
            ;;
        files)
            restore_custom_files
            ;;
        packages)
            install_packages
            ;;
        interactive)
            echo ""
            local choice=""
            if [[ "$SCRIPT_LANG" = "tr" ]]; then
                echo "Hangi işlemleri gerçekleştirmek istiyorsunuz?"
                echo "  1) Tam Geri Yükleme (Ayarlar + Dosyalar + Uygulama Kurulumu + Sistem Durumu)"
                echo "  2) Yalnızca Ayarları ve Dotfile'ları Yükle"
                echo "  3) Yalnızca Özel Dosyaları ve Klasörleri Yükle"
                echo "  4) Yalnızca Uygulamaları ve Paketleri Kur"
                echo "  5) Çıkış"
                echo ""
                read -rp "Seçiminiz [1-5]: " choice || true
            else
                echo "Which operations would you like to perform?"
                echo "  1) Full Restore (Settings + Files + Application Installation + System State)"
                echo "  2) Restore Only Settings and Dotfiles"
                echo "  3) Restore Only Custom Files and Folders"
                echo "  4) Install Only Applications and Packages"
                echo "  5) Exit"
                echo ""
                read -rp "Your choice [1-5]: " choice || true
            fi

            case "$choice" in
                1)
                    restore_configs
                    restore_custom_files
                    install_packages
                    restore_system_state
                    ;;
                2)
                    restore_configs
                    restore_system_state
                    ;;
                3)
                    restore_custom_files
                    ;;
                4)
                    install_packages
                    ;;
                *)
                    log_info "İşlem iptal edildi." "Operation cancelled."
                    exit 0
                    ;;
            esac
            ;;
    esac

    echo ""
    if [[ ${#FAILED_SYSTEM_PACKAGES[@]} -gt 0 ]]; then
        if [[ "$SCRIPT_LANG" = "tr" ]]; then
            log_warn "Genel Özet: ${#FAILED_SYSTEM_PACKAGES[@]} paket kurulamadı:"
        else
            log_warn "General Summary: ${#FAILED_SYSTEM_PACKAGES[@]} packages failed to install:"
        fi
        for p in "${FAILED_SYSTEM_PACKAGES[@]}"; do
            echo -e "  ${C_RED}✖${C_RESET} ${p}"
        done
        echo ""
    fi
    log_success "İşlemler tamamlandı! Yeni Linux sisteminizin tadını çıkarın. 🎉" "Operations completed! Enjoy your new Linux system. 🎉"
}

main
'''


def generate_standalone_restore_script() -> str:
    """Yedeğin içine konulacak bağımsız bash restore scriptini döndürür."""
    return RESTORE_SCRIPT_TEMPLATE
