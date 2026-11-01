#!/usr/bin/env bash
# ==============================================================================
# DistroNomad - Taşınabilir USB Paketi Derleme Scripti (Portable USB Packager)
# ==============================================================================
# Bu betik, DistroNomad'i herhangi bir modern 64-bit Linux dağıtımında (Ubuntu,
# Debian, Fedora, Arch, openSUSE, Mint vb.) host sistemde Python veya bağımlılık
# kurulu olmasa dahi doğrudan USB bellekten çalışabilecek taşınabilir (portable)
# bir paket haline getirir.
#
# Çıktılar:
#   dist/distronomad-portable/                (USB belleğe doğrudan kopyalanacak klasör)
#   dist/distronomad-portable-linux-x86_64.tar.gz  (Arşiv paketi)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"

BUILD_DIR="${SCRIPT_DIR}/build/portable"
DIST_DIR="${SCRIPT_DIR}/dist"
PORTABLE_OUT="${DIST_DIR}/distronomad-portable"
STANDALONE_PY_DIR="${SCRIPT_DIR}/build/python-standalone"

PYTHON_STANDALONE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
# Güvenlik gereği SHA-256 sağlama toplamı doğrulanmadan harici ikili indirilmez.
# Resmi sağlama toplamı biliniyorsa buraya yazılmalıdır; boş bırakılırsa betik indirmeyi reddeder.
PYTHON_STANDALONE_SHA256="ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20"

echo "=================================================================="
echo "🚀 DistroNomad Taşınabilir USB Paketi Hazırlanıyor..."
echo "=================================================================="

# 1. Gerekli dizinleri hazırla
mkdir -p "${BUILD_DIR}"
mkdir -p "${DIST_DIR}"
rm -rf "${PORTABLE_OUT}"
mkdir -p "${PORTABLE_OUT}"

# 2. Standalone Python çalışma ortamını sağla (glibc 2.17+ uyumlu)
if [ ! -x "${STANDALONE_PY_DIR}/bin/python3" ]; then
    if [ -z "${PYTHON_STANDALONE_SHA256:-}" ]; then
        echo "❌ HATA: PYTHON_STANDALONE_SHA256 tanımlanmamış!" >&2
        echo "Güvenlik nedeniyle resmi SHA-256 sağlama toplamı belirtilmeden indirme yapılamaz." >&2
        echo "Lütfen betik başındaki PYTHON_STANDALONE_SHA256 değişkenini doldurun." >&2
        exit 1
    fi
    echo "🌐 Bağımsız Python çalışma ortamı indiriliyor (glibc 2.17+ manylinux)..."
    mkdir -p "${STANDALONE_PY_DIR}"
    TMP_ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/python_standalone_XXXXXX.tar.gz")"
    trap 'rm -f "${TMP_ARCHIVE}"' EXIT INT TERM
    curl -sSL "${PYTHON_STANDALONE_URL}" -o "${TMP_ARCHIVE}"
    echo "${PYTHON_STANDALONE_SHA256}  ${TMP_ARCHIVE}" | sha256sum -c -
    tar -xzf "${TMP_ARCHIVE}" -C "${STANDALONE_PY_DIR}" --strip-components=1
    rm -f "${TMP_ARCHIVE}"
    trap - EXIT INT TERM
fi

PY_BIN="${STANDALONE_PY_DIR}/bin/python3"
PIP_BIN="${STANDALONE_PY_DIR}/bin/pip3"

echo "🔍 Derleme ortamı Python: $("${PY_BIN}" --version)"

# 3. Bağımlılıkları ve PyInstaller'ı bağımsız ortama kur
echo "📦 Bağımlılıklar (Textual, Rich, Zstandard, PyInstaller) doğrulanıyor..."
"${PIP_BIN}" install -q --upgrade pip
"${PIP_BIN}" install -q "textual==8.2.8" "rich==15.0.0" "zstandard==0.25.0" "pyinstaller==6.22.3"

echo "📦 DistroNomad paketi kuruluyor..."
"${PIP_BIN}" install -q "${SCRIPT_DIR}"

# 4. PyInstaller ile tek parça çalıştırılabilir dosya derle
echo "🔨 Bağımsız ikili dosya (single-binary) derleniyor..."
PYINSTALLER_BIN="${STANDALONE_PY_DIR}/bin/pyinstaller"

"${PYINSTALLER_BIN}" \
    --onefile \
    --name distronomad \
    --clean \
    --noconfirm \
    --add-data "${SCRIPT_DIR}/distronomad/ui/styles.tcss:distronomad/ui" \
    --add-data "${SCRIPT_DIR}/distronomad/data/package_map.json:distronomad/data" \
    --collect-all textual \
    --collect-all rich \
    --collect-all zstandard \
    --collect-all distronomad \
    "${SCRIPT_DIR}/distronomad/main.py" \
    --distpath "${BUILD_DIR}/dist" \
    --workpath "${BUILD_DIR}/work" \
    --specpath "${BUILD_DIR}/spec"

# 5. Dosyaları distronomad-portable klasörüne yerleştir
echo "📂 Taşınabilir USB paketi bileşenleri oluşturuluyor..."
cp "${BUILD_DIR}/dist/distronomad" "${PORTABLE_OUT}/distronomad"
chmod +x "${PORTABLE_OUT}/distronomad"

# 5.1. Akıllı Başlatıcı (run.sh)
cat << 'EOF' > "${PORTABLE_OUT}/run.sh"
#!/usr/bin/env bash
# ==============================================================================
# DistroNomad - Taşınabilir USB Başlatıcı (Smart Portable Launcher)
# ==============================================================================

set -e

# UTF-8 ve terminal desteğini garantiye al
export PYTHONIOENCODING=utf-8
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-${LANG}}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${DIR}/distronomad"

# GUI dosya yöneticisinden çift tıklanıp tıklanmadığını kontrol et
# Terminal yoksa ve parametre verilmemişse otomatik terminal penceresi aç
if [ ! -t 0 ] && [ $# -eq 0 ]; then
    for TERM_CMD in xdg-terminal-exec gnome-terminal konsole alacritty kitty xfce4-terminal mate-terminal xterm; do
        if command -v "$TERM_CMD" >/dev/null 2>&1; then
            case "$TERM_CMD" in
                gnome-terminal|mate-terminal|xfce4-terminal)
                    exec "$TERM_CMD" -- bash -c "cd '$DIR' && ./run.sh; echo ''; read -n 1 -s -r -p 'Kapatmak için bir tuşa basın...'"
                    ;;
                konsole)
                    exec "$TERM_CMD" -e bash -c "cd '$DIR' && ./run.sh; echo ''; read -n 1 -s -r -p 'Kapatmak için bir tuşa basın...'"
                    ;;
                alacritty|kitty)
                    exec "$TERM_CMD" -e bash -c "cd '$DIR' && ./run.sh; echo ''; read -n 1 -s -r -p 'Kapatmak için bir tuşa basın...'"
                    ;;
                *)
                    exec "$TERM_CMD" -e "bash" "-c" "cd '$DIR' && ./run.sh; read -n 1 -s -r -p 'Kapatmak için bir tuşa basın...'"
                    ;;
            esac
        fi
    done
fi

# FAT32 / exFAT USB belleklerdeki 'noexec' korumasını kontrol et
# Eğer doğrudan çalıştırma başarısız olursa binary'yi /tmp'ye kopyalayıp oradan çalıştır
RUN_BIN="${BIN}"
if [ ! -x "${BIN}" ] || ! "${BIN}" --version >/dev/null 2>&1; then
    CACHE_DIR="${XDG_RUNTIME_DIR:-/tmp}/distronomad-${UID:-0}"
    mkdir -p "${CACHE_DIR}"
    RUN_BIN="${CACHE_DIR}/distronomad"
    
    # Binary değiştiyse veya henüz yoksa kopyala
    if [ ! -f "${RUN_BIN}" ] || [ "${BIN}" -nt "${RUN_BIN}" ]; then
        cp -f "${BIN}" "${RUN_BIN}"
        chmod +x "${RUN_BIN}"
    fi
fi

# Argüman verilmemişse varsayılan olarak TUI (Görsel Arayüz) başlat
if [ $# -eq 0 ]; then
    exec "${RUN_BIN}" tui
else
    exec "${RUN_BIN}" "$@"
fi
EOF
chmod +x "${PORTABLE_OUT}/run.sh"

# 5.2. Masaüstü Kısayolu (distronomad.desktop)
cat << 'EOF' > "${PORTABLE_OUT}/distronomad.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=DistroNomad (Portable)
Comment=Linux Dağıtım Taşıma ve Konfigürasyon Yöneticisi
Exec=bash -c 'cd "$(dirname "%k")" && ./run.sh'
Icon=utilities-terminal
Terminal=true
Categories=System;Utility;Archiving;
EOF
chmod +x "${PORTABLE_OUT}/distronomad.desktop"

# 5.3. Türkçe Kullanım Kılavuzu (BENIOKU.txt)
cat << 'EOF' > "${PORTABLE_OUT}/BENIOKU.txt"
==============================================================================
DistroNomad - Taşınabilir USB Sürümü (Portable Linux Edition)
==============================================================================

Bu paket, DistroNomad'i herhangi bir Linux bilgisayarda (Ubuntu, Debian, Fedora,
Arch Linux, openSUSE, Linux Mint vb.) kurulum yapmadan ve internet bağlantısına
ihtiyaç duymadan doğrudan USB bellek üzerinden çalıştırmanızı sağlar.

İçerideki ikili dosya glibc 2.17+ uyumlu bağımsız Python motoru ve tüm grafiksel
TUI bileşenleri ile birlikte tek parça olarak paketlenmiştir.

------------------------------------------------------------------------------
NASIL KULLANILIR?
------------------------------------------------------------------------------

1. USB BELLEĞE KOPYALAMA:
   Bu klasörün ("distronomad-portable") tamamını USB belleğinize kopyalayın.

2. ÇALIŞTIRMA (Görsel Arayüz - TUI):
   USB bellek takılıyken terminali bu klasörde açın ve şu komutu çalıştırın:

       ./run.sh
       veya
       bash run.sh

   (Eğer masaüstü ortamındaysanız "run.sh" veya "distronomad.desktop"
   dosyasına çift tıklayarak da başlatabilirsiniz.)

3. KOMUT SATIRI (CLI) KULLANIMI:
   Tüm CLI komutları "run.sh" üzerinden doğrudan çalıştırılabilir:

   - Sistem ve dotfile taraması:
       ./run.sh scan

   - Yedekleme alma:
       ./run.sh backup --dest /media/usb/yedek.tar.zst

   - Yedeği yeni sisteme geri yükleme:
       ./run.sh restore --src /media/usb/yedek.tar.zst

   - Yedek ile sistem farklarını görme:
       ./run.sh diff --src /media/usb/yedek.tar.zst

   - Yardım menüsü:
       ./run.sh --help

------------------------------------------------------------------------------
ÖNEMLİ İPUÇLARI VE SORUN GİDERME
------------------------------------------------------------------------------
* FAT32 / exFAT USB Bellekler (noexec sorunu):
  Bazı Linux dağıtımları FAT32/exFAT bellekleri güvenlik nedeniyle "noexec"
  (çalıştırma izni kapalı) olarak bağlar. "run.sh" betiği bunu otomatik olarak
  tespit eder ve programı geçici olarak /tmp dizininden güvenle çalıştırır.
  Bu nedenle her zaman doğrudan "./distronomad" yerine "./run.sh" kullanmanız
  önerilir.

* Yetkili Kullanıcı (sudo) Gereksinimi:
  Dotfile ve kullanıcı ayarlarını yedeklemek veya geri yüklemek için normal
  kullanıcı hakları yeterlidir. Ancak sistem genelindeki paketleri kurmak
  istediğinizde dağıtımınızın paket yöneticisi parola soracaktır.
==============================================================================
EOF

# 5.4. İngilizce Kullanım Kılavuzu (README.txt)
cat << 'EOF' > "${PORTABLE_OUT}/README.txt"
==============================================================================
DistroNomad - Portable USB Edition (Stand-alone Linux Package)
==============================================================================

This package allows you to run DistroNomad on any Linux computer (Ubuntu,
Debian, Fedora, Arch Linux, openSUSE, Linux Mint, etc.) directly from a USB
drive without any installation, internet connection, or pre-installed Python.

The bundled binary is compiled against glibc 2.17+ and contains all required
runtime engines, Textual TUI, and dependencies in a self-contained package.

------------------------------------------------------------------------------
QUICK START
------------------------------------------------------------------------------

1. COPY TO USB DRIVE:
   Copy the entire "distronomad-portable" folder to your USB drive.

2. RUN (Visual TUI):
   Open a terminal in this directory on the target machine and run:

       ./run.sh
       or
       bash run.sh

   (In desktop environments, you can also double-click "run.sh" or
   "distronomad.desktop".)

3. COMMAND LINE (CLI) USAGE:
   Pass any command-line options directly to "run.sh":

   - Scan system packages and dotfiles:
       ./run.sh scan

   - Create a full backup:
       ./run.sh backup --dest /media/usb/backup.tar.zst

   - Restore backup to this machine:
       ./run.sh restore --src /media/usb/backup.tar.zst

   - Compare backup with host:
       ./run.sh diff --src /media/usb/backup.tar.zst

   - View help:
       ./run.sh --help

------------------------------------------------------------------------------
NOTES & TROUBLESHOOTING
------------------------------------------------------------------------------
* FAT32 / exFAT USB Drives (noexec mount option):
  Many Linux distros mount FAT32/exFAT drives with the "noexec" flag.
  "run.sh" detects this automatically and safely executes from /tmp in RAM.
  Therefore, always use "./run.sh" instead of running "./distronomad" directly.
==============================================================================
EOF

# 6. Tar.gz arşivi oluştur
echo "📦 Arşiv oluşturuluyor: dist/distronomad-portable-linux-x86_64.tar.gz..."
tar -czf "${DIST_DIR}/distronomad-portable-linux-x86_64.tar.gz" -C "${DIST_DIR}" distronomad-portable

echo ""
echo "=================================================================="
echo "✅ BAŞARILI: Taşınabilir paket hazırlandı!"
echo "📂 Klasör : ${PORTABLE_OUT}"
echo "📦 Arşiv  : ${DIST_DIR}/distronomad-portable-linux-x86_64.tar.gz"
echo "Boyut     : $(du -sh "${PORTABLE_OUT}" | cut -f1)"
echo "=================================================================="
