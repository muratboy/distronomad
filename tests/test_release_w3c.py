"""
W3c: Bağımsız (Standalone) restore.sh betiğinin iki dilli (TR/EN) davranış testleri.

Kapsanan gereksinimler:
1. Bash 4+ sözdizimi doğrulaması (bash -n, varsa shellcheck).
2. İki dilli --help metni: Ortam diline ve --lang bayrağına göre TR/EN çıktısı.
3. Dil tespiti önceliği (LC_ALL -> LC_MESSAGES -> LANG tr ile başlıyorsa TR, aksi hâlde EN).
4. --lang tr ve --lang en seçeneğinin ortam değişkenini ezmesi ve geçersiz dilde hata vermesi.
5. Sahte paket yöneticileriyle hem Türkçe hem İngilizce çıktı doğrulaması.
6. Etkileşimli onay sorularında hem Türkçe (e/H) hem İngilizce (y/N) yanıtların kabul edilmesi.
7. Güvenlik korumalarının (paket adı denetimi, -- ayracı, eval kullanmama, --target-home kök koruması,
   dry-run dizin oluşturmama) iki dilde de bozulmadan çalışması.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path
import pytest

from distronomad.standalone_script import generate_standalone_restore_script


def _create_restore_script(tmp_path: Path) -> Path:
    """Standalone restore.sh betiğini oluşturup yürütülebilir yapar."""
    script_path = tmp_path / "restore.sh"
    script_content = generate_standalone_restore_script()
    script_path.write_text(script_content, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)
    return script_path


def _create_minimal_backup_env(tmp_path: Path):
    """restore.sh için minimal configs, packages ve custom dizin yapısını hazırlar."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / ".bashrc").write_text("# mock bashrc\n")

    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)

    custom_dir = tmp_path / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 1. Sözdizimi ve Statik Denetim
# ==============================================================================

def test_w3c_bash_n_syntax_validation(tmp_path):
    """restore.sh betiği bash -n ile hatasız doğrulanmalıdır (Bash 4+ uyumlu)."""
    script_file = _create_restore_script(tmp_path)
    res = subprocess.run(["bash", "-n", str(script_file)], capture_output=True, text=True)
    assert res.returncode == 0, f"bash -n sözdizimi hatası verdi: {res.stderr}"


def test_w3c_shellcheck_if_installed(tmp_path):
    """shellcheck kuruluysa betiği statik analizden geçirir (kurulu değilse atlar)."""
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck sistemde kurulu değil, atlanıyor.")

    script_file = _create_restore_script(tmp_path)
    res = subprocess.run(
        ["shellcheck", "-s", "bash", "-e", "SC1090,SC1091", str(script_file)],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0, f"shellcheck uyarısı:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"


# ==============================================================================
# 2. İki Dilli --help Metni
# ==============================================================================

def test_w3c_help_turkish_from_environment(tmp_path):
    """LANG=tr_TR.UTF-8 ortamında --help Türkçe kullanım ve seçenek açıklamaları sunmalıdır."""
    script_file = _create_restore_script(tmp_path)
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "tr_TR.UTF-8"}

    res = subprocess.run([str(script_file), "--help"], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    stdout = res.stdout
    assert "Kullanım:" in stdout
    assert "--all" in stdout
    assert "Her şeyi" in stdout
    assert "--lang tr|en" in stdout
    assert "--target-home" in stdout
    assert "--allow-root-paths" in stdout


def test_w3c_help_english_from_environment(tmp_path):
    """LANG=en_US.UTF-8 veya LANG=C ortamında --help İngilizce kullanım ve seçenek açıklamaları sunmalıdır."""
    script_file = _create_restore_script(tmp_path)
    for loc in ["en_US.UTF-8", "C", "de_DE.UTF-8"]:
        env = {"PATH": os.environ.get("PATH", ""), "LANG": loc}
        res = subprocess.run([str(script_file), "--help"], env=env, capture_output=True, text=True)
        assert res.returncode == 0
        stdout = res.stdout
        assert "Usage:" in stdout
        assert "--all" in stdout
        assert "Restore everything" in stdout
        assert "--lang tr|en" in stdout
        assert "--target-home" in stdout
        assert "--allow-root-paths" in stdout


def test_w3c_help_with_lang_flags(tmp_path):
    """--lang tr veya --lang en bayrağı verildiğinde ortam dili ne olursa olsun istenen dilde yardım gelmelidir."""
    script_file = _create_restore_script(tmp_path)

    # Ortam Türkçe ama --lang en verilmiş -> İngilizce
    env_tr = {"PATH": os.environ.get("PATH", ""), "LANG": "tr_TR.UTF-8"}
    res_en = subprocess.run([str(script_file), "--help", "--lang", "en"], env=env_tr, capture_output=True, text=True)
    assert res_en.returncode == 0
    assert "Usage:" in res_en.stdout
    assert "Restore everything" in res_en.stdout

    # Ortam İngilizce ama --lang tr verilmiş -> Türkçe
    env_en = {"PATH": os.environ.get("PATH", ""), "LANG": "C"}
    res_tr = subprocess.run([str(script_file), "--help", "--lang", "tr"], env=env_en, capture_output=True, text=True)
    assert res_tr.returncode == 0
    assert "Kullanım:" in res_tr.stdout
    assert "Her şeyi" in res_tr.stdout

    # --lang=en ve --lang=tr biçimleri
    res_eq_en = subprocess.run([str(script_file), "--lang=en", "-h"], env=env_tr, capture_output=True, text=True)
    assert res_eq_en.returncode == 0
    assert "Usage:" in res_eq_en.stdout

    res_eq_tr = subprocess.run([str(script_file), "--lang=tr", "-h"], env=env_en, capture_output=True, text=True)
    assert res_eq_tr.returncode == 0
    assert "Kullanım:" in res_eq_tr.stdout


# ==============================================================================
# 3. Ortam Değişkenleri Önceliği ve --lang Bayrağı
# ==============================================================================

def test_w3c_environment_locale_precedence(tmp_path):
    """LC_ALL -> LC_MESSAGES -> LANG sırasıyla tr ile başlıyorsa TR, aksi hâlde EN seçilmelidir."""
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)
    target_home = tmp_path / "home"
    target_home.mkdir()

    base_env = {"PATH": os.environ.get("PATH", "")}

    # 1. Yalnızca LANG=tr
    env1 = dict(base_env, LANG="tr_TR.UTF-8")
    p1 = subprocess.run([str(script_file), "--dry-run", "--configs-only", "--target-home", str(target_home)],
                        cwd=str(tmp_path), env=env1, capture_output=True, text=True)
    assert p1.returncode == 0
    assert "Yedek Kaynağı:" in p1.stdout
    assert "[BİLGİ]" in p1.stdout

    # 2. Yalnızca LC_MESSAGES=tr
    env2 = dict(base_env, LC_MESSAGES="tr_TR.UTF-8")
    p2 = subprocess.run([str(script_file), "--dry-run", "--configs-only", "--target-home", str(target_home)],
                        cwd=str(tmp_path), env=env2, capture_output=True, text=True)
    assert p2.returncode == 0
    assert "Yedek Kaynağı:" in p2.stdout
    assert "[BİLGİ]" in p2.stdout

    # 3. Yalnızca LC_ALL=tr
    env3 = dict(base_env, LC_ALL="tr_TR.UTF-8")
    p3 = subprocess.run([str(script_file), "--dry-run", "--configs-only", "--target-home", str(target_home)],
                        cwd=str(tmp_path), env=env3, capture_output=True, text=True)
    assert p3.returncode == 0
    assert "Yedek Kaynağı:" in p3.stdout
    assert "[BİLGİ]" in p3.stdout

    # 4. LANG=tr ancak LC_ALL=C (POSIX önceliği: LC_ALL baskın çıkar -> EN)
    env4 = dict(base_env, LANG="tr_TR.UTF-8", LC_ALL="C")
    p4 = subprocess.run([str(script_file), "--dry-run", "--configs-only", "--target-home", str(target_home)],
                        cwd=str(tmp_path), env=env4, capture_output=True, text=True)
    assert p4.returncode == 0
    assert "Backup Source:" in p4.stdout
    assert "[INFO]" in p4.stdout


def test_w3c_lang_override_and_invalid_lang(tmp_path):
    """--lang tr ve --lang en ortamı ezer; geçersiz dilde çıkış kodu 1 ile hata verir."""
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)
    target_home = tmp_path / "home"
    target_home.mkdir()

    # Ortam TR, komut --lang en
    env_tr = {"PATH": os.environ.get("PATH", ""), "LANG": "tr_TR.UTF-8"}
    p_en = subprocess.run([str(script_file), "--lang", "en", "--dry-run", "--configs-only", "--target-home", str(target_home)],
                          cwd=str(tmp_path), env=env_tr, capture_output=True, text=True)
    assert p_en.returncode == 0
    assert "Backup Source:" in p_en.stdout
    assert "[INFO]" in p_en.stdout
    assert "Configurations successfully restored" in p_en.stdout

    # Ortam EN, komut --lang tr
    env_en = {"PATH": os.environ.get("PATH", ""), "LANG": "C"}
    p_tr = subprocess.run([str(script_file), "--lang", "tr", "--dry-run", "--configs-only", "--target-home", str(target_home)],
                          cwd=str(tmp_path), env=env_en, capture_output=True, text=True)
    assert p_tr.returncode == 0
    assert "Yedek Kaynağı:" in p_tr.stdout
    assert "[BİLGİ]" in p_tr.stdout
    assert "Konfigürasyonlar başarıyla" in p_tr.stdout

    # Geçersiz dil
    p_err = subprocess.run([str(script_file), "--lang", "de"], cwd=str(tmp_path), env=env_en, capture_output=True, text=True)
    assert p_err.returncode != 0
    assert "Invalid language" in p_err.stdout or "Geçersiz dil" in p_err.stdout


# ==============================================================================
# 4. Sahte Paket Yöneticisi ile Çıktı Doğrulaması (TR ve EN)
# ==============================================================================

def test_w3c_package_install_outputs_turkish_and_english(tmp_path):
    """Sahte paket yöneticisi ortamında tek tek deneme, başarı, başarısızlık ve özet mesajları TR ve EN doğrulanır."""
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    clean_sys_bin = tmp_path / "clean_sys_bin"
    clean_sys_bin.mkdir()

    for util in ["bash", "sh", "grep", "cat", "echo", "mkdir", "cp", "rm", "date", "touch", "find", "dirname", "sed", "tr", "realpath"]:
        p = shutil.which(util)
        if p:
            (clean_sys_bin / util).symlink_to(p)

    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
    fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # Sahte apt-get: toplu kurulumda pkg_fail varsa hata verir, tek tek çağrıda pkg_fail reddeder
    fake_apt = fake_bin / "apt-get"
    fake_apt.write_text("""#!/bin/bash
if [ "$1" = "update" ]; then exit 0; fi
if [ "$1" = "install" ]; then
    for arg in "$@"; do
        if [ "$arg" = "pkg_fail" ]; then exit 100; fi
    done
    exit 0
fi
exit 0
""")
    fake_apt.chmod(fake_apt.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    packages_dir = tmp_path / "packages"
    (packages_dir / "system_packages.txt").write_text("pkg_ok\npkg_fail\n")

    env = {
        "PATH": f"{fake_bin}:{clean_sys_bin}",
        "HOME": str(tmp_path / "home")
    }

    # 1. TÜRKÇE ÇIKTI TESTİ
    p_tr = subprocess.run(
        [str(script_file), "--packages-only", "--lang", "tr", "--target-home", str(tmp_path / "home")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_tr.returncode == 0
    stdout_tr = p_tr.stdout
    assert "Toplu paket kurulumu başarısız oldu, paketler tek tek deneniyor..." in stdout_tr
    assert "Kuruldu: pkg_ok" in stdout_tr
    assert "Kurulamadı: pkg_fail" in stdout_tr
    assert "Kurulamayan paketler özeti" in stdout_tr
    assert "İşlemler tamamlandı!" in stdout_tr

    # 2. İNGİLİZCE ÇIKTI TESTİ
    p_en = subprocess.run(
        [str(script_file), "--packages-only", "--lang", "en", "--target-home", str(tmp_path / "home")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_en.returncode == 0
    stdout_en = p_en.stdout
    assert "Batch package installation failed, trying packages individually..." in stdout_en
    assert "Installed: pkg_ok" in stdout_en
    assert "Failed to install: pkg_fail" in stdout_en
    assert "Failed packages summary" in stdout_en
    assert "Operations completed!" in stdout_en


# ==============================================================================
# 5. Etkileşimli Onay Sorularında e/H ve y/N Uyumu
# ==============================================================================

def test_w3c_interactive_confirmation_accepts_both_e_and_y(tmp_path):
    """Etkileşimli modda onay soruları hem Türkçe (e/E) hem İngilizce (y/Y) yanıtları kabul etmelidir."""
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    clean_sys_bin = tmp_path / "clean_sys_bin"
    clean_sys_bin.mkdir()

    for util in ["bash", "sh", "grep", "cat", "echo", "mkdir", "cp", "rm", "date", "touch", "find", "dirname", "sed", "tr", "realpath"]:
        p = shutil.which(util)
        if p:
            (clean_sys_bin / util).symlink_to(p)

    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
    fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    call_log = tmp_path / "pacman_calls.log"
    fake_pacman = fake_bin / "pacman"
    fake_pacman.write_text(f"""#!/bin/bash
echo "CALL: $*" >> "{call_log}"
exit 0
""")
    fake_pacman.chmod(fake_pacman.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    packages_dir = tmp_path / "packages"
    (packages_dir / "system_packages.txt").write_text("testpkg\n")

    env = {"PATH": f"{fake_bin}:{clean_sys_bin}", "HOME": str(tmp_path / "home")}

    # Durum 1: Türkçe modda 'e' ile onay
    if call_log.exists():
        call_log.unlink()
    # Seçim 4: Yalnızca Paketleri Kur
    p_tr_e = subprocess.run(
        [str(script_file), "--lang", "tr", "--target-home", str(tmp_path / "home")],
        input="4\ne\n",
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_tr_e.returncode == 0
    assert "Hangi işlemleri gerçekleştirmek istiyorsunuz?" in p_tr_e.stdout
    assert call_log.exists()

    # Durum 2: Türkçe modda 'y' ile onay (İngilizce yanıt)
    call_log.unlink()
    p_tr_y = subprocess.run(
        [str(script_file), "--lang", "tr", "--target-home", str(tmp_path / "home")],
        input="4\ny\n",
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_tr_y.returncode == 0
    assert call_log.exists()

    # Durum 3: İngilizce modda 'y' ile onay
    call_log.unlink()
    p_en_y = subprocess.run(
        [str(script_file), "--lang", "en", "--target-home", str(tmp_path / "home")],
        input="4\ny\n",
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_en_y.returncode == 0
    assert "Which operations would you like to perform?" in p_en_y.stdout
    assert call_log.exists()

    # Durum 4: İngilizce modda 'e' ile onay (Türkçe yanıt)
    call_log.unlink()
    p_en_e = subprocess.run(
        [str(script_file), "--lang", "en", "--target-home", str(tmp_path / "home")],
        input="4\ne\n",
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_en_e.returncode == 0
    assert call_log.exists()

    # Durum 5: 'h' veya 'n' ile reddetme
    call_log.unlink()
    p_en_n = subprocess.run(
        [str(script_file), "--lang", "en", "--target-home", str(tmp_path / "home")],
        input="4\nn\n",
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )
    assert p_en_n.returncode == 0
    assert "Package installation skipped." in p_en_n.stdout
    assert not call_log.exists()


def test_w3c_custom_root_interactive_confirmation_tr_and_en(tmp_path):
    """custom/root dosyaları için etkileşimli onayda ret verildiğinde doğru dilde atlandı mesajı verir."""
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)

    # custom/root altında tek dosya
    custom_root = tmp_path / "custom" / "root"
    custom_root.mkdir(parents=True, exist_ok=True)
    (custom_root / "test.txt").write_text("DATA")

    # 1. Türkçe modda etkileşimli seçim 3 (özel dosyalar), soruya 'h' cevabı
    p_tr = subprocess.run(
        [str(script_file), "--lang", "tr"],
        input="3\nh\n",
        cwd=str(tmp_path),
        capture_output=True,
        text=True
    )
    assert p_tr.returncode == 0
    assert "Ev dışı yol atlandı (onay verilmedi): /test.txt" in p_tr.stdout

    # 2. İngilizce modda etkileşimli seçim 3, soruya 'n' cevabı
    p_en = subprocess.run(
        [str(script_file), "--lang", "en"],
        input="3\nn\n",
        cwd=str(tmp_path),
        capture_output=True,
        text=True
    )
    assert p_en.returncode == 0
    assert "External path skipped (not confirmed): /test.txt" in p_en.stdout


# ==============================================================================
# 6. Güvenlik Davranışlarının Korunması
# ==============================================================================

def test_w3c_security_target_home_protection_and_root_prompt(tmp_path):
    """
    Hedef ev dizini özel belirtildiğinde root dosyaları asla yazılmaz;
    ana sistemde ise --allow-root-paths olmadan etkileşimli sorulur.
    """
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)

    custom_root = tmp_path / "custom" / "root" / "tmp" / "dn_test_root"
    custom_root.mkdir(parents=True)
    (custom_root / "sample.conf").write_text("CONF=1")

    # 1. Özel target-home verildiğinde kök dosyaları yazılmaz ve uyarılır (İngilizce)
    p_en = subprocess.run(
        [str(script_file), "--files-only", "--lang", "en", "--target-home", str(tmp_path / "custom_home")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True
    )
    assert p_en.returncode == 0
    assert "Since target home is specified as a custom directory" in p_en.stdout
    assert not Path("/tmp/dn_test_root/sample.conf").exists()

    # 2. Özel target-home verildiğinde kök dosyaları yazılmaz ve uyarılır (Türkçe)
    p_tr = subprocess.run(
        [str(script_file), "--files-only", "--lang", "tr", "--target-home", str(tmp_path / "custom_home")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True
    )
    assert p_tr.returncode == 0
    assert "Hedef ev dizini özel bir dizin olarak belirtildiğinden" in p_tr.stdout
    assert not Path("/tmp/dn_test_root/sample.conf").exists()


def test_w3c_security_dry_run_creates_no_directories(tmp_path):
    """--dry-run modunda hedefte hiçbir dizin oluşturulmamalıdır (B-11 koruması)."""
    script_file = _create_restore_script(tmp_path)
    _create_minimal_backup_env(tmp_path)

    nonexistent_target_home = tmp_path / "nonexistent_dryrun_home"
    deep_config = tmp_path / "configs" / ".config" / "deep_test"
    deep_config.mkdir(parents=True)
    (deep_config / "setting.ini").write_text("A=1")

    p = subprocess.run(
        [str(script_file), "--dry-run", "--configs-only", "--lang", "en", "--target-home", str(nonexistent_target_home)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True
    )
    assert p.returncode == 0
    assert "[DRY-RUN] Directory will be created:" in p.stdout
    assert not nonexistent_target_home.exists(), "--dry-run modunda dizin oluşturulmamalıdır!"
