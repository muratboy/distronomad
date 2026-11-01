import os
import stat
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

from distronomad.models import SystemInfo, ConfigItem, CustomPathItem, RestoreOptions, BackupManifest
from distronomad.backup import BackupEngine
from distronomad.restore import RestoreEngine
from distronomad.crypto import is_encrypted_file, encrypt_archive, decrypt_archive


@pytest.fixture
def mock_env(tmp_path):
    home_dir = tmp_path / "mock_home"
    home_dir.mkdir(parents=True, exist_ok=True)
    sys_info = SystemInfo(
        distro_name="Arch Linux",
        distro_id="arch",
        distro_like=["arch"],
        kernel_version="6.10.0-arch",
        desktop_environment="Hyprland",
        home_dir=str(home_dir),
        shell="/bin/bash",
        hostname="test-box",
        available_package_managers=["pacman"]
    )
    return {
        "home": home_dir,
        "sys_info": sys_info,
        "tmp_path": tmp_path
    }


def test_multilevel_incremental_restore_preserves_full_backup_files(mock_env):
    """
    (1) P0 restore.py ~312-330:
    Çok seviyeli artımlı zincirde (Full -> Inc1 -> Inc2) geri yükleme
    yalnızca Full yedekte bulunan dosyaları da korumalıdır.
    Mevcut kodda Full yedekteki dosyalar sessizce kaybolur.
    """
    home = mock_env["home"]
    tmp_path = mock_env["tmp_path"]
    sys_info = mock_env["sys_info"]

    data_dir = home / ".config" / "myapp"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Aşama 1: Full yedek
    file_full = data_dir / "file_full.txt"
    file_full.write_text("FULL_CONTENT")
    common_file = data_dir / "common.txt"
    common_file.write_text("COMMON_V1")

    cfg = ConfigItem(
        id="cfg_myapp",
        name="MyApp",
        source_path=str(data_dir),
        rel_path=".config/myapp",
        category="Uygulama",
        is_directory=True,
        is_selected=True
    )

    full_archive = tmp_path / "backup_full.tar.zst"
    eng_full = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=full_archive,
        archive_format="tar.zst",
        backup_mode="full"
    )
    res_full = eng_full.run()
    assert res_full.exists()

    # Aşama 2: Inc1 yedek
    file_inc1 = data_dir / "file_inc1.txt"
    file_inc1.write_text("INC1_CONTENT")
    common_file.write_text("COMMON_V2")

    inc1_archive = tmp_path / "backup_inc1.tar.zst"
    eng_inc1 = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=inc1_archive,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=full_archive
    )
    res_inc1 = eng_inc1.run()
    assert res_inc1.exists()

    # Aşama 3: Inc2 yedek
    file_inc2 = data_dir / "file_inc2.txt"
    file_inc2.write_text("INC2_CONTENT")

    inc2_archive = tmp_path / "backup_inc2.tar.zst"
    eng_inc2 = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=inc2_archive,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=inc1_archive
    )
    res_inc2 = eng_inc2.run()
    assert res_inc2.exists()

    # Geri yükleme testi (Inc2 geri yükleniyor)
    restore_target = tmp_path / "restored_home"
    restore_target.mkdir(parents=True, exist_ok=True)

    restore_engine = RestoreEngine(backup_path=res_inc2)
    manifest = restore_engine.load_manifest()

    opts = RestoreOptions(
        target_home=str(restore_target),
        selected_configs=manifest.configs,
        selected_packages=[],
        selected_custom_paths=[]
    )
    restore_engine.execute_restore(opts)
    restore_engine.cleanup()

    # KONTROLLER:
    # Full'deki dosya korunmalı (mevcut kodda KAYBOLUYORDU)
    restored_full = restore_target / ".config" / "myapp" / "file_full.txt"
    assert restored_full.exists(), "P0 Hatası: Full yedekteki dosya geri yüklenemedi, kayboldu!"
    assert restored_full.read_text() == "FULL_CONTENT"

    restored_inc1 = restore_target / ".config" / "myapp" / "file_inc1.txt"
    assert restored_inc1.exists()
    assert restored_inc1.read_text() == "INC1_CONTENT"

    restored_inc2 = restore_target / ".config" / "myapp" / "file_inc2.txt"
    assert restored_inc2.exists()
    assert restored_inc2.read_text() == "INC2_CONTENT"

    restored_common = restore_target / ".config" / "myapp" / "common.txt"
    assert restored_common.exists()
    assert restored_common.read_text() == "COMMON_V2"


def test_multilevel_incremental_chain_loop_and_missing_link(mock_env):
    """
    (1) P0 restore.py: Zincirde döngü veya eksik halka varsa anlaşılır hata verilmelidir.
    """
    tmp_path = mock_env["tmp_path"]
    sys_info = mock_env["sys_info"]
    home = mock_env["home"]

    data_dir = home / ".config" / "loopapp"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "a.txt").write_text("A")

    cfg = ConfigItem(
        id="cfg_loop",
        name="LoopApp",
        source_path=str(data_dir),
        rel_path="loopapp",
        category="Uygulama",
        is_directory=True,
        is_selected=True
    )

    inc_archive = tmp_path / "backup_orphan.tar.zst"
    eng = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=inc_archive,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=tmp_path / "non_existent_base.tar.zst"
    )
    # base_backup_path yoksa uyarıyla full fallback yapabilir, o yüzden manifest'i doğrudan simüle ediyoruz
    res = eng.run()

    # Manifest'i manipüle ederek olmayan taban verelim
    re = RestoreEngine(backup_path=res)
    re.load_manifest()
    re.manifest.backup_type = "incremental"
    re.manifest.base_backup_name = "missing_base_backup.tar.zst"

    opts = RestoreOptions(target_home=str(tmp_path / "restored"))
    with pytest.raises(FileNotFoundError) as exc_info:
        re.execute_restore(opts)
    assert "bulunamadı" in str(exc_info.value).lower()
    re.cleanup()

    # Döngü testi simülasyonu: A -> B -> A
    # İki artımlı arşiv manifestinde karşılıklı referans oluşturulması
    b_archive = tmp_path / "backup_loop_b.tar.zst"
    shutil.copy2(res, b_archive)

    re_loop = RestoreEngine(backup_path=res)
    re_loop.load_manifest()
    re_loop.manifest.backup_type = "incremental"
    re_loop.manifest.base_backup_name = b_archive.name

    # b_archive'in manifest'ini res'e yönlendirelim
    with patch("distronomad.restore.read_manifest_from_backup") as mock_read:
        loop_manifest = BackupManifest(
            created_at="2026-01-01T00:00:00",
            version="1.0.0",
            system_info=sys_info,
            backup_type="incremental",
            base_backup_name=res.name
        )
        mock_read.return_value = loop_manifest
        with pytest.raises(ValueError) as exc_info:
            re_loop.execute_restore(opts)
        assert "döngü" in str(exc_info.value).lower() or "loop" in str(exc_info.value).lower()
    re_loop.cleanup()


def test_incremental_restore_with_encrypted_base(mock_env):
    """
    (2) P1 restore.py ~327:
    Zincirdeki taban yedek şifreliyse (_extract_archive çökmeden) self.password ile
    çözülüp başarıyla birleştirilmelidir.
    """
    home = mock_env["home"]
    tmp_path = mock_env["tmp_path"]
    sys_info = mock_env["sys_info"]
    pwd = "BaseSecretPassword789!"

    data_dir = home / ".config" / "encapp"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "base_secret.txt").write_text("BASE_ENC_SECRET")

    cfg = ConfigItem(
        id="cfg_encapp",
        name="EncApp",
        source_path=str(data_dir),
        rel_path=".config/encapp",
        category="Uygulama",
        is_directory=True,
        is_selected=True
    )

    # Şifreli Tam Taban Yedek
    full_archive = tmp_path / "base_enc.tar.zst.enc"
    eng_full = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=full_archive,
        archive_format="tar.zst",
        backup_mode="full",
        password=pwd
    )
    res_full = eng_full.run()
    assert res_full.exists()
    assert is_encrypted_file(res_full)

    # Şifresiz veya şifreli artımlı yedek (taban olarak şifreli yedeği gösteriyor)
    (data_dir / "inc_file.txt").write_text("INC_NEW_DATA")
    inc_archive = tmp_path / "inc_over_enc.tar.zst"
    eng_inc = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=inc_archive,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=res_full,
        password=pwd
    )
    res_inc = eng_inc.run()
    assert res_inc.exists()

    # Artımlı yedeği geri yükle (taban şifreli olduğu için self.password ile açılmalı)
    restore_target = tmp_path / "restored_enc_home"
    restore_target.mkdir(parents=True, exist_ok=True)

    restore_engine = RestoreEngine(backup_path=res_inc, password=pwd)
    manifest = restore_engine.load_manifest()

    opts = RestoreOptions(
        target_home=str(restore_target),
        selected_configs=manifest.configs,
        selected_packages=[],
        selected_custom_paths=[]
    )
    # Mevcut kodda: ValueError: Desteklenmeyen arşiv formatı (çünkü .enc doğrudan tar açmaya veriliyor)
    restore_engine.execute_restore(opts)
    restore_engine.cleanup()

    restored_base = restore_target / ".config" / "encapp" / "base_secret.txt"
    assert restored_base.exists()
    assert restored_base.read_text() == "BASE_ENC_SECRET"

    restored_inc = restore_target / ".config" / "encapp" / "inc_file.txt"
    assert restored_inc.exists()
    assert restored_inc.read_text() == "INC_NEW_DATA"


def test_encrypted_backup_output_path_no_tmp_prefix(mock_env):
    """
    (3) P1 backup.py ~991 ve ~1148-1151:
    Şifreli yedek kullanıcının seçtiği yol yerine gizli .tmp_ önekli dosya olarak kalmamalıdır.
    """
    home = mock_env["home"]
    tmp_path = mock_env["tmp_path"]
    sys_info = mock_env["sys_info"]
    pwd = "SafePassword321!"

    data_dir = home / ".config" / "simple"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample.txt").write_text("SAMPLE")

    cfg = ConfigItem(
        id="cfg_simple",
        name="Simple",
        source_path=str(data_dir),
        rel_path="simple",
        category="Uygulama",
        is_directory=True,
        is_selected=True
    )

    out_file = tmp_path / "my_secret_backup.tar.zst.enc"
    eng = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=out_file,
        archive_format="tar.zst",
        backup_mode="full",
        password=pwd
    )
    final_res = eng.run()

    # KONTROLLER:
    # 1. Dönen yol .tmp_ ile başlamamalı
    assert not final_res.name.startswith(".tmp_"), f"Hata: nihai dosya .tmp_ öneki içeriyor: {final_res.name}"
    # 2. Tam olarak kullanıcının/arayüzün belirttiği yolda oluşmalı
    assert final_res == out_file, f"Beklenen {out_file}, oluşan: {final_res}"
    assert final_res.exists()
    # 3. Geride hiçbir .tmp_ dosyası kalmamalı
    tmp_leftovers = [f.name for f in tmp_path.iterdir() if f.name.startswith(".tmp_")]
    assert len(tmp_leftovers) == 0, f"Geride geçici dosya kaldı: {tmp_leftovers}"


def test_backup_failure_cleans_up_partial_archive_and_preserves_intact(mock_env):
    """
    (4) P1 backup.py ~991, ~1119, ~1168-1172:
    Yedekleme hata verirse hedefte yarım arşiv kalmamalı, geçici dosya silinmeli;
    append modunda hata olursa mevcut arşiv bozulmamalı;
    dir biçiminde de yarım kalan çıktı temizlenmeli;
    arşiv izinleri 0600 ve dizin izinleri 0700 korunmalı.
    """
    home = mock_env["home"]
    tmp_path = mock_env["tmp_path"]
    sys_info = mock_env["sys_info"]

    data_dir = home / ".config" / "failapp"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "bigfile.txt").write_text("DATA" * 1000)

    cfg = ConfigItem(
        id="cfg_fail",
        name="FailApp",
        source_path=str(data_dir),
        rel_path="failapp",
        category="Uygulama",
        is_directory=True,
        is_selected=True
    )

    # 1. Normal şifresiz arşivde hata durumu
    target_archive = tmp_path / "should_not_exist.tar.zst"
    eng = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=target_archive,
        archive_format="tar.zst",
        backup_mode="full"
    )

    with patch("distronomad.backup.compute_sha256", side_effect=RuntimeError("Simulated disk read failure")):
        with pytest.raises(RuntimeError):
            eng.run()

    # Hedefte yarım dosya kalmamalı
    assert not target_archive.exists(), "Hata: Başarısız yedekleme hedefte yarım arşiv bıraktı!"
    tmp_leftovers = [f.name for f in tmp_path.iterdir() if f.name.startswith(".tmp_")]
    assert len(tmp_leftovers) == 0, f"Geçici artıklar kaldı: {tmp_leftovers}"

    # 2. Dir biçiminde hata durumu
    target_dir = tmp_path / "should_not_exist_dir"
    eng_dir = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=target_dir,
        archive_format="dir",
        backup_mode="full"
    )
    with patch("distronomad.backup.copy_single_file", side_effect=RuntimeError("Simulated copy failure")):
        with pytest.raises(RuntimeError):
            eng_dir.run()

    assert not target_dir.exists(), "Hata: Başarısız dir yedekleme hedefte yarım dizin bıraktı!"

    # 3. Başarılı arşiv ve dizin izinleri (0600 ve 0700)
    ok_archive = tmp_path / "success_perm.tar.zst"
    eng_ok = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=ok_archive,
        archive_format="tar.zst",
        backup_mode="full"
    )
    res_ok = eng_ok.run()
    assert res_ok.exists()
    mode = stat.S_IMODE(os.stat(res_ok).st_mode)
    assert mode == 0o600, f"Arşiv izinleri 0600 olmalı, bulunan: {oct(mode)}"

    ok_dir = tmp_path / "success_perm_dir"
    eng_ok_dir = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=ok_dir,
        archive_format="dir",
        backup_mode="full"
    )
    res_ok_dir = eng_ok_dir.run()
    assert res_ok_dir.exists()
    mode_dir = stat.S_IMODE(os.stat(res_ok_dir).st_mode)
    assert mode_dir == 0o700, f"Dizin izinleri 0700 olmalı, bulunan: {oct(mode_dir)}"

    # 4. Append modunda hata olursa mevcut arşiv bozulmamalı
    # res_ok arşivini append için kullanalım
    orig_size = res_ok.stat().st_size
    orig_content = res_ok.read_bytes()

    (data_dir / "new_append.txt").write_text("NEW_DATA")
    eng_append = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=res_ok,
        archive_format="tar.zst",
        backup_mode="append",
        base_backup_path=res_ok
    )

    with patch.object(BackupEngine, "_finalize_archive", side_effect=RuntimeError("Simulated repack failure")):
        with pytest.raises(RuntimeError):
            eng_append.run()

    assert res_ok.exists(), "Mevcut arşiv silinmiş olmamalı!"
    assert res_ok.stat().st_size == orig_size, "Mevcut arşiv boyutu bozulmuş!"
    assert res_ok.read_bytes() == orig_content, "Mevcut arşiv içeriği bozulmuş!"


def test_multilevel_incremental_restore_applies_deleted_files(mock_env):
    """
    (1) P0 restore.py: Zincirdeki silinen dosyalar (deleted_files veya file_metadata deleted/status=deleted)
    sırayla uygulanmalı ve geri yükleme çıktısından kaldırılmalıdır.
    """
    home = mock_env["home"]
    tmp_path = mock_env["tmp_path"]
    sys_info = mock_env["sys_info"]

    data_dir = home / ".config" / "delapp"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "keep.txt").write_text("KEEP_ME")
    (data_dir / "to_delete1.txt").write_text("DELETE_ME_1")
    (data_dir / "to_delete2.txt").write_text("DELETE_ME_2")

    cfg = ConfigItem(
        id="cfg_delapp",
        name="DelApp",
        source_path=str(data_dir),
        rel_path=".config/delapp",
        category="Uygulama",
        is_directory=True,
        is_selected=True
    )

    full_archive = tmp_path / "base_del_full.tar.zst"
    eng_full = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=full_archive,
        archive_format="tar.zst",
        backup_mode="full"
    )
    res_full = eng_full.run()

    # Inc1 yedeği: to_delete1 ve to_delete2 silinmiş olarak işaretli
    (data_dir / "to_delete1.txt").unlink()
    (data_dir / "to_delete2.txt").unlink()
    (data_dir / "new_inc.txt").write_text("NEW_INC")

    inc_archive = tmp_path / "inc_del.tar.zst"
    eng_inc = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=[cfg],
        custom_paths=[],
        output_path=inc_archive,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=res_full
    )
    res_inc = eng_inc.run()

    # Manifestte silinen dosyaları belirtelim:
    # 1. file_metadata içinde status="deleted"
    # 2. deleted_files listesinde
    re_inspect = RestoreEngine(backup_path=res_inc)
    manifest = re_inspect.load_manifest()
    manifest.file_metadata["configs/.config/delapp/to_delete1.txt"] = {"status": "deleted"}
    manifest.file_metadata["configs/.config/delapp/to_delete2.txt"] = {"deleted": True}
    # Manifest'i geçici açılan alana yeniden yaz
    import json
    with open(re_inspect.source_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f)

    restore_target = tmp_path / "restored_del_home"
    restore_target.mkdir(parents=True, exist_ok=True)

    opts = RestoreOptions(
        target_home=str(restore_target),
        selected_configs=manifest.configs,
        selected_packages=[],
        selected_custom_paths=[]
    )
    re_inspect.execute_restore(opts)
    re_inspect.cleanup()

    assert (restore_target / ".config" / "delapp" / "keep.txt").exists()
    assert (restore_target / ".config" / "delapp" / "new_inc.txt").exists()
    assert not (restore_target / ".config" / "delapp" / "to_delete1.txt").exists()
    assert not (restore_target / ".config" / "delapp" / "to_delete2.txt").exists()


def test_standalone_script_rejects_eval_injection_and_invalid_pkgs(tmp_path):
    """
    (1) P0 standalone_script.py ~362 ve ~367:
    restore.sh eval ile paket kurulum komutlarını çalıştırmamalı;
    kabuk metakarakterleri komut olarak icra edilmemeli, geçersiz paket adları
    ve '-' ile başlayan adlar reddedilmeli, '--' argüman sonlandırıcı kullanılmalı.
    """
    import subprocess
    from distronomad.standalone_script import generate_standalone_restore_script

    script_content = generate_standalone_restore_script()
    script_file = tmp_path / "restore.sh"
    script_file.write_text(script_content)
    script_file.chmod(script_file.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # 1. bash -n ile sözdizimi doğrulaması
    proc_syntax = subprocess.run(["bash", "-n", str(script_file)], capture_output=True, text=True)
    assert proc_syntax.returncode == 0, f"restore.sh sözdizimi hatası: {proc_syntax.stderr}"

    # 2. Sahte PATH ortamı hazırlama
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()

    # Sahte sudo (argümanları doğrudan çalıştırır)
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
    fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # Sahte pacman (çağrı argümanlarını kaydeder, gerçek işlem yapmaz)
    pacman_log = tmp_path / "pacman_calls.log"
    fake_pacman = fake_bin / "pacman"
    fake_pacman.write_text(f"#!/bin/sh\necho \"ARGS: $*\" >> \"{pacman_log}\"\nexit 0\n")
    fake_pacman.chmod(fake_pacman.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # Dizin yapısını kur
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    pwned_marker = tmp_path / "pwned_marker.txt"
    pwned_marker2 = tmp_path / "pwned_marker2.txt"

    pkg_list = [
        "valid_pkg1",
        "valid-pkg2",
        "pkg:with+symbols@1.0_2",
        "--invalid_dash_flag",
        f"malicious; touch {pwned_marker};",
        f"malicious2 && touch {pwned_marker2}",
    ]
    (packages_dir / "system_packages.txt").write_text("\n".join(pkg_list) + "\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    # restore.sh'i --packages-only ile çalıştır
    proc_run = subprocess.run(
        [str(script_file), "--packages-only", "--target-home", str(tmp_path / "home")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )

    # Kötü niyetli komutlar icra edilmemeli
    assert not pwned_marker.exists(), "GÜVENLİK AÇIĞI: restore.sh eval ile enjekte edilen komutu çalıştırdı!"
    assert not pwned_marker2.exists(), "GÜVENLİK AÇIĞI: restore.sh eval ile komut zincirini çalıştırdı!"

    # Log dosyasını kontrol et
    assert pacman_log.exists(), f"Sahte pacman çağrılmadı! stderr: {proc_run.stderr}, stdout: {proc_run.stdout}"
    log_content = pacman_log.read_text()

    # Paket yöneticisi çağrısında '--' argüman sonlandırıcı bulunmalı
    assert "-- " in log_content, f"Paket yöneticisi çağrısında '--' bulunamadı: {log_content}"
    # Geçerli paketler iletilmiş olmalı
    assert "valid_pkg1" in log_content
    assert "valid-pkg2" in log_content
    assert "pkg:with+symbols@1.0_2" in log_content
    # Geçersiz/zararlı paket adları pacman'e verilmemeli
    assert "--invalid_dash_flag" not in log_content
    assert "touch" not in log_content


def test_restore_engine_prevents_path_traversal_and_symlink_escape(tmp_path):
    """
    (2) P1 restore.py ~396, ~508, ~511:
    rel_path veya özel yol .. içerdiğinde veya daha önce açılmış
    bir sembolik bağ üzerinden target_home dışına yazma engellenmelidir.
    """
    import json
    target_home = tmp_path / "target_home"
    target_home.mkdir()

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    # 1. Önceden oluşturulmuş (veya yedeğin ilk parçasından gelen) bir sembolik bağ dizini
    symlink_dir = target_home / "symlink_dir"
    os.symlink(str(outside_dir), str(symlink_dir))

    # Sahte yedek dizini oluştur
    backup_dir = tmp_path / "fake_backup"
    configs_dir = backup_dir / "configs"
    configs_dir.mkdir(parents=True)

    # a) .. ile dizin dışına kaçış denemesi
    pwned_outside_traversal = outside_dir / "traversal_pwn.txt"
    evil_traversal_cfg_src = configs_dir / "evil_traversal"
    evil_traversal_cfg_src.mkdir(parents=True)
    (evil_traversal_cfg_src / "evil.txt").write_text("TRAVERSAL_DATA")

    # b) Sembolik bağ üzerinden dışarı yazma denemesi
    pwned_outside_symlink = outside_dir / "symlink_pwn.txt"
    evil_symlink_cfg_src = configs_dir / "symlink_dir"
    evil_symlink_cfg_src.mkdir(parents=True)
    (evil_symlink_cfg_src / "symlink_pwn.txt").write_text("SYMLINK_DATA")

    # Manifest oluştur
    manifest_data = {
        "version": "1.0",
        "created_at": "2026-10-20T12:00:00",
        "backup_mode": "full",
        "archive_format": "dir",
        "system_info": {"home_dir": "/mock/source_home", "distro_id": "arch"},
        "packages": [],
        "configs": [
            {
                "id": "cfg_traversal",
                "name": "Traversal Config",
                "category": "app",
                "source_path": "",
                "rel_path": f"../outside_dir/{pwned_outside_traversal.name}",
                "is_selected": True
            },
            {
                "id": "cfg_symlink",
                "name": "Symlink Escape Config",
                "category": "app",
                "source_path": "",
                "rel_path": "symlink_dir/symlink_pwn.txt",
                "is_selected": True
            }
        ],
        "custom_paths": []
    }
    with open(backup_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)

    engine = RestoreEngine(backup_path=backup_dir)
    manifest = engine.load_manifest()

    opts = RestoreOptions(
        target_home=str(target_home),
        selected_configs=manifest.configs,
        selected_packages=[],
        selected_custom_paths=[]
    )

    results = engine.execute_restore(opts)
    engine.cleanup()

    # Dışarıdaki hiçbir dosya yazılmamış olmalı
    assert not pwned_outside_traversal.exists(), "GÜVENLİK AÇIĞI: target_home dışına .. ile dosya yazıldı!"
    assert not pwned_outside_symlink.exists(), "GÜVENLİK AÇIĞI: target_home dışına sembolik bağ üzerinden dosya yazıldı!"

    # Güvenlik uyarılarının günlüğe yazıldığını doğrula
    assert any("engellendi" in w.lower() or "dışına" in w.lower() for w in results["warnings"])

    # 2. restore.sh betiğinde de sembolik bağ üzerinden target_home dışına yazma koruması
    import subprocess
    from distronomad.standalone_script import generate_standalone_restore_script
    restore_sh_home = tmp_path / "restore_sh_home"
    restore_sh_home.mkdir()
    sh_outside = tmp_path / "sh_outside"
    sh_outside.mkdir()
    os.symlink(str(sh_outside), str(restore_sh_home / "symlink_dir"))

    sh_script = backup_dir / "restore.sh"
    sh_script.write_text(generate_standalone_restore_script())
    sh_script.chmod(sh_script.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    subprocess.run(
        [str(sh_script), "--configs-only", "--target-home", str(restore_sh_home)],
        cwd=str(backup_dir),
        capture_output=True,
        text=True
    )
    assert not (sh_outside / "symlink_pwn.txt").exists(), "GÜVENLİK AÇIĞI: restore.sh sembolik bağ üzerinden dışarı dosya yazdı!"



@pytest.mark.asyncio
async def test_ui_restore_in_progress_prevents_new_restore_and_protects_engine(tmp_path):
    """
    (3) P1 ui/app.py ~2280-2285:
    Geri yükleme sürerken yeni geri yükleme başlatılması engellenmeli,
    old_engine.cleanup() çalışan restore motorunun geçici dizinlerini silmemeli.
    """
    from distronomad.ui.app import DistroNomadApp
    from distronomad.restore import RestoreEngine
    from distronomad.models import ConfigItem, BackupManifest, SystemInfo
    from textual.widgets import Button, Input

    home = tmp_path / "mock_home"
    home.mkdir()

    app = DistroNomadApp(home_dir=str(home))
    async with app.run_test() as pilot:
        await pilot.pause()

        # Çalışmakta olan bir motor ve geçici dizin simüle et
        mock_backup = tmp_path / "mock_backup.tar.zst"
        mock_backup.write_bytes(b"dummy")

        engine = RestoreEngine(backup_path=mock_backup)
        temp_extracted = tmp_path / "running_worker_extracted"
        temp_extracted.mkdir()
        (temp_extracted / "important_extracted_file.txt").write_text("DO_NOT_DELETE")
        engine.temp_extracted_dir = temp_extracted
        engine.source_dir = temp_extracted

        manifest = BackupManifest(
            version="1.0",
            created_at="2026-10-20T12:00:00",
            backup_type="full",
            archive_format="tar.zst",
            system_info=SystemInfo(distro_id="arch", distro_name="Arch Linux", home_dir=str(home)),
            packages=[],
            configs=[ConfigItem(id="c1", name="C1", category="app", source_path="", rel_path="c1.txt", is_selected=True)],
            custom_paths=[]
        )
        engine.manifest = manifest

        app.current_restore_engine = engine
        app.restore_manifest_configs = manifest.configs
        app.query_one("#input-restore-src", Input).value = str(mock_backup)
        app._is_restoring = True
        btn = app.query_one("#btn-start-restore", Button)
        btn.disabled = True

        # Geri yükleme devam ederken _handle_start_restore tetiklensin
        app._handle_start_restore()

        # 1. Çalışan motorun geçici dizini silinmemeli
        assert temp_extracted.exists(), "HATA: old_engine.cleanup() çalışan restore motorunun dizinini sildi!"
        assert (temp_extracted / "important_extracted_file.txt").exists()

        # 2. Buton devre dışı kalmaya devam etmeli
        assert btn.disabled is True


def test_detect_backup_format_encrypted_tar_gz():
    """
    (1) P2 backup.py ~101-131:
    detect_backup_format .tar.gz.enc gibi şifreli dosyaları tar.zst değil,
    iç biçim olan tar.gz olarak tespit etmeli; tanınmayan biçimlerde ValueError vermeli.
    """
    from distronomad.backup import detect_backup_format

    assert detect_backup_format("mybackup.tar.gz.enc") == "tar.gz"
    assert detect_backup_format("mybackup.tar.zst.enc") == "tar.zst"
    assert detect_backup_format("mybackup.tar.enc") == "tar"
    with pytest.raises(ValueError):
        detect_backup_format("mybackup.unknown_extension")


def test_restore_pacman_sp_uses_double_dash_argument_terminator(tmp_path):
    """
    (2) P2 restore.py ~155:
    pacman -Sp çağrısında paket adından önce '--' argüman sonlandırıcısı bulunmalıdır.
    """
    import subprocess
    from distronomad.restore import RestoreEngine

    engine = RestoreEngine(backup_path=tmp_path)
    sp_calls = []

    def mock_sub_run(cmd, *args, **kwargs):
        if len(cmd) >= 2 and cmd[0] == "pacman" and cmd[1] == "-Sp":
            sp_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")
        elif len(cmd) >= 2 and cmd[0] == "pacman" and cmd[1] == "-Si":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: package not found")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("distronomad.restore.subprocess.run", side_effect=mock_sub_run):
        engine._query_repo_packages("pacman", ["--evil-flag"])

    assert len(sp_calls) > 0
    assert sp_calls[0] == ["pacman", "-Sp", "--", "--evil-flag"]


def test_copy_single_file_uses_rel_key_for_file_hashes(tmp_path):
    """
    (3) P2 backup.py ~670, 677:
    copy_single_file file_hashes sözlüğünü mutlak dosya yolu ile değil,
    manifest göreli anahtarı (rel_key) ile güncellemelidir.
    """
    from distronomad.backup import copy_single_file

    src = tmp_path / "source.txt"
    src.write_text("hello world")
    dst = tmp_path / "staging" / "configs" / "source.txt"
    rel_key = "configs/source.txt"
    file_hashes = {}
    file_metadata = {}

    copy_single_file(src, dst, rel_key, file_hashes, file_metadata)

    assert rel_key in file_hashes, f"HATA: rel_key '{rel_key}' file_hashes içinde bulunamadı!"
    assert str(dst) not in file_hashes, f"HATA: mutlak staging yolu '{dst}' file_hashes anahtarı yapılmış!"
    assert file_hashes[rel_key] == file_metadata[rel_key]["sha256"]


def test_ui_backup_and_scan_worker_groups_and_concurrency(tmp_path):
    """
    (4) P2 ui/app.py ~821, 2026-2027:
    Worker'lar ayrı gruplara sahip olmalı, scan exclusive=True olmalı,
    backup ve restore birbirini iptal etmemeli ve backup sürerken yenisi başlatılmamalı.
    """
    import inspect
    from distronomad.ui.app import DistroNomadApp

    # 1. Metod dekoratör parametreleri kontrolü
    # Textual @work decorated callable has _work_decorator attribute or closure
    app = DistroNomadApp(home_dir=str(tmp_path))

    # _run_scan_worker exclusive olmalı, group='scan' olmalı
    # _run_backup_worker exclusive OLMAMALI, group='backup' olmalı
    # _run_restore_worker exclusive OLMAMALI, group='restore' olmalı
    scan_worker_func = DistroNomadApp._run_scan_worker
    backup_worker_func = DistroNomadApp._run_backup_worker
    restore_worker_func = DistroNomadApp._run_restore_worker

    # Worker fonksiyon kaynak kodunda query_one kontrolü
    backup_src = inspect.getsource(backup_worker_func)
    assert "self.query_one" not in backup_src, "HATA: _run_backup_worker thread içinden query_one çağırıyor!"

    # Eşzamanlı başlatma engeli: _is_backing_up True iken _handle_start_backup engellenmeli
    app._is_backing_up = True
    with patch.object(app, "_run_backup_worker") as mock_run:
        app._handle_start_backup()
        mock_run.assert_not_called()

    # Worker group ve exclusive kontrolleri
    scan_closures = {name: cell.cell_contents for name, cell in zip(scan_worker_func.__code__.co_freevars, scan_worker_func.__closure__)}
    backup_closures = {name: cell.cell_contents for name, cell in zip(backup_worker_func.__code__.co_freevars, backup_worker_func.__closure__)}
    restore_closures = {name: cell.cell_contents for name, cell in zip(restore_worker_func.__code__.co_freevars, restore_worker_func.__closure__)}

    assert scan_closures["exclusive"] is True
    assert scan_closures["group"] == "scan"
    assert backup_closures["exclusive"] is False
    assert backup_closures["group"] == "backup"
    assert restore_closures["exclusive"] is False
    assert restore_closures["group"] == "restore"

    # Worker iptal edildiğinde (is_cancelled) sonuçların uygulanmaması kontrolü
    with patch("distronomad.ui.app.get_current_worker") as mock_get_worker, \
         patch.object(app, "_apply_scan_results") as mock_apply, \
         patch.object(app, "call_from_thread") as mock_call_from_thread:
        mock_worker_inst = mock_get_worker.return_value
        mock_worker_inst.is_cancelled = True
        scan_worker_func.__wrapped__(app)
        mock_call_from_thread.assert_not_called()
        mock_apply.assert_not_called()


def test_conftest_mocks_popen_and_controlled_pacman_sp():
    """
    (5) P2 tests/conftest.py ~97, 117:
    subprocess.Popen ve os.system sahtelenmeli; pacman -Sp her zaman başarı dönmemeli,
    olmayan paketler için hata dönmeli.
    """
    import subprocess
    import os

    # 1. Popen systemctl için sahte olmalı ve sisteme dokunmamalı
    proc = subprocess.Popen(["systemctl", "--user", "status", "test"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate()
    assert proc.returncode in (0, 3)

    # 2. pacman -Sp var olmayan paket için 1 dönmeli (her zaman 0 dönmemeli!)
    res_fail = subprocess.run(["pacman", "-Sp", "--", "definitely-nonexistent-package-xyz123"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert res_fail.returncode != 0, "HATA: pacman -Sp sahtesi var olmayan paket için her zaman 0 (başarı) dönüyor!"

    # 3. pacman -Sp bilinen paket için 0 dönmeli
    res_ok = subprocess.run(["pacman", "-Sp", "--", "git"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert res_ok.returncode == 0


def test_restore_sh_package_fallback_on_batch_failure_apt(tmp_path):
    """
    [KRİTİK-3] restore.sh çapraz dağıtım geçişinde tek bir bulunamayan paket
    yüzünden tüm toplu paket kurulumunun iptal olmasını engelleme testi (Apt).
    1. Toplu kurulum başarısız olduğunda tek tek fallback döngüsüne geçildiğini,
    2. apt-get update'in yalnızca bir kez çağrıldığını,
    3. Başarısız paketin özette raporlandığını ve diğerlerinin kurulduğunu,
    4. set -e nedeniyle betiğin yarıda kesilmediğini,
    5. Gerçek sudo veya paket yöneticisinin asla çalışmadığını doğrular.
    """
    import subprocess
    import shutil
    import stat
    import os
    from distronomad.standalone_script import generate_standalone_restore_script

    script_content = generate_standalone_restore_script()
    script_file = tmp_path / "restore.sh"
    script_file.write_text(script_content)
    script_file.chmod(script_file.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # 1. bash -n sözdizimi kontrolü
    syntax_res = subprocess.run(["bash", "-n", str(script_file)], capture_output=True, text=True)
    assert syntax_res.returncode == 0, f"restore.sh sözdizimi hatası: {syntax_res.stderr}"

    # 2. Sahte izole PATH ortamı (gerçek sudo/pacman/apt çağrılamaz)
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    clean_sys_bin = tmp_path / "clean_sys_bin"
    clean_sys_bin.mkdir()

    for util in ["bash", "sh", "grep", "cat", "echo", "mkdir", "cp", "rm", "date", "touch", "find", "dirname", "sed", "tr", "realpath"]:
        p = shutil.which(util)
        if p:
            (clean_sys_bin / util).symlink_to(p)

    # Sahte sudo
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
    fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # Sahte apt-get
    apt_log = tmp_path / "apt_calls.log"
    fake_apt = fake_bin / "apt-get"
    fake_apt.write_text(f"""#!/bin/bash
log_file="{apt_log}"
echo "CALL: $*" >> "$log_file"

if [ "$1" = "update" ]; then
    echo "UPDATE_CALLED" >> "$log_file"
    exit 0
fi

if [ "$1" = "install" ]; then
    for arg in "$@"; do
        if [ "$arg" = "pkg_unavailable" ]; then
            echo "E: Unable to locate package pkg_unavailable" >&2
            exit 100
        fi
    done
    exit 0
fi
exit 0
""")
    fake_apt.chmod(fake_apt.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # 3. Paket listesi hazırla
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    (packages_dir / "system_packages.txt").write_text("pkg_good1\npkg_unavailable\npkg_good2\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{clean_sys_bin}"

    # 4. restore.sh betiğini çalıştır
    proc = subprocess.run(
        [str(script_file), "--packages-only", "--lang", "tr", "--target-home", str(tmp_path / "home")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )

    # Betik set -e nedeniyle ilk hatada ölmemeli, başarıyla (0) tamamlanmalı
    assert proc.returncode == 0, f"restore.sh başarısız bitti (kod {proc.returncode}): {proc.stderr}\nSTDOUT:\n{proc.stdout}"

    # 5. Apt update yalnızca bir kez çalışmalı
    log_content = apt_log.read_text()
    assert log_content.count("UPDATE_CALLED") == 1, f"apt-get update tam olarak 1 kez çağrılmalıydı! Çağrılar:\n{log_content}"

    # 6. Toplu çağrı denenmeli ve başarısız olmalı
    assert "CALL: install -y -- pkg_good1 pkg_unavailable pkg_good2" in log_content

    # 7. Paketler tek tek denenmeli
    assert "CALL: install -y -- pkg_good1" in log_content
    assert "CALL: install -y -- pkg_unavailable" in log_content
    assert "CALL: install -y -- pkg_good2" in log_content

    # 8. Çıktıdaki mesajlar ve özeti doğrula
    stdout = proc.stdout
    assert "Toplu paket kurulumu başarısız oldu, paketler tek tek deneniyor..." in stdout
    assert "Kuruldu: pkg_good1" in stdout
    assert "Kurulamadı: pkg_unavailable" in stdout
    assert "Kuruldu: pkg_good2" in stdout
    assert "Kurulamayan paketler özeti (1 paket):" in stdout or "Kurulamayan paketler özeti" in stdout
    assert "pkg_unavailable" in stdout


def test_restore_sh_package_fallback_on_batch_failure_pacman(tmp_path):
    """
    [KRİTİK-3] restore.sh Pacman ortamında tek paket uyuşmazlığında fallback testi.
    """
    import subprocess
    import shutil
    import stat
    import os
    from distronomad.standalone_script import generate_standalone_restore_script

    script_content = generate_standalone_restore_script()
    script_file = tmp_path / "restore.sh"
    script_file.write_text(script_content)
    script_file.chmod(script_file.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()

    # Sahte sudo
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
    fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    # Sahte pacman
    pacman_log = tmp_path / "pacman_calls.log"
    fake_pacman = fake_bin / "pacman"
    fake_pacman.write_text(f"""#!/bin/bash
log_file="{pacman_log}"
echo "CALL: $*" >> "$log_file"

for arg in "$@"; do
    if [ "$arg" = "broken_aur_or_missing_pkg" ]; then
        echo "error: target not found: broken_aur_or_missing_pkg" >&2
        exit 1
    fi
done
exit 0
""")
    fake_pacman.chmod(fake_pacman.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR)

    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    (packages_dir / "system_packages.txt").write_text("arch_pkg1\nbroken_aur_or_missing_pkg\narch_pkg2\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    proc = subprocess.run(
        [str(script_file), "--packages-only", "--lang", "tr", "--target-home", str(tmp_path / "home")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True
    )

    assert proc.returncode == 0, f"restore.sh başarısız bitti: {proc.stderr}\nSTDOUT:\n{proc.stdout}"

    log_content = pacman_log.read_text()
    # Toplu çağrı denenmiş olmalı
    assert "CALL: -S --needed --noconfirm -- arch_pkg1 broken_aur_or_missing_pkg arch_pkg2" in log_content
    # Tek tek çağrılar denenmiş olmalı
    assert "CALL: -S --needed --noconfirm -- arch_pkg1" in log_content
    assert "CALL: -S --needed --noconfirm -- broken_aur_or_missing_pkg" in log_content
    assert "CALL: -S --needed --noconfirm -- arch_pkg2" in log_content

    stdout = proc.stdout
    assert "Toplu paket kurulumu başarısız oldu, paketler tek tek deneniyor..." in stdout
    assert "Kuruldu: arch_pkg1" in stdout
    assert "Kurulamadı: broken_aur_or_missing_pkg" in stdout
    assert "Kuruldu: arch_pkg2" in stdout
    assert "broken_aur_or_missing_pkg" in stdout


# =========================================================================
# [KRİTİK-4] FAT32 Dosya Sistemi 4 GB Sınırı Koruması Testleri
# =========================================================================

def test_filesystem_type_detection_fake_mountinfo(tmp_path):
    """
    [KRİTİK-4] /proc/self/mountinfo üzerinden en uzun bağlama noktası eşleşmesiyle
    dosya sistemi türü tespitini doğrular.
    """
    from distronomad.backup import get_filesystem_type, is_fat_filesystem

    mountinfo_content = """41 1 0:35 /@ / rw,noatime shared:1 - btrfs /dev/nvme0n1p2 rw
100 41 8:1 / /media/fat_drive rw,nosuid,nodev shared:2 - vfat /dev/sdb1 rw
101 41 8:2 / /media/msdos_drive rw,nosuid,nodev shared:3 - msdos /dev/sdb2 rw
102 41 8:3 / /media/fat_drive/fat_sub rw,nosuid,nodev shared:4 - fat /dev/sdb3 rw
103 41 8:4 / /media/exfat_drive rw,nosuid,nodev shared:5 - exfat /dev/sdb4 rw
104 41 8:5 / /media/ext4_drive rw,nosuid,nodev shared:6 - ext4 /dev/sdb5 rw
105 41 8:6 / /media/usb\\040drive rw,nosuid,nodev shared:7 - vfat /dev/sdb6 rw
"""
    mountinfo_file = tmp_path / "fake_mountinfo"
    mountinfo_file.write_text(mountinfo_content, encoding="utf-8")

    # 1. vfat, msdos ve fat türleri FAT sayılmalı
    assert get_filesystem_type("/media/fat_drive/backup.tar.zst", mountinfo_path=mountinfo_file) == "vfat"
    assert is_fat_filesystem("/media/fat_drive/backup.tar.zst", mountinfo_path=mountinfo_file) is True

    assert get_filesystem_type("/media/msdos_drive/backup.tar.zst", mountinfo_path=mountinfo_file) == "msdos"
    assert is_fat_filesystem("/media/msdos_drive/backup.tar.zst", mountinfo_path=mountinfo_file) is True

    # 2. En uzun bağlama noktası eşleşmesi (fat_sub > fat_drive)
    assert get_filesystem_type("/media/fat_drive/fat_sub/backup.tar.zst", mountinfo_path=mountinfo_file) == "fat"
    assert is_fat_filesystem("/media/fat_drive/fat_sub/backup.tar.zst", mountinfo_path=mountinfo_file) is True

    # 3. exfat ve diğerleri sınırsız (FAT sayılmaz)
    assert get_filesystem_type("/media/exfat_drive/backup.tar.zst", mountinfo_path=mountinfo_file) == "exfat"
    assert is_fat_filesystem("/media/exfat_drive/backup.tar.zst", mountinfo_path=mountinfo_file) is False

    assert get_filesystem_type("/media/ext4_drive/backup.tar.zst", mountinfo_path=mountinfo_file) == "ext4"
    assert is_fat_filesystem("/media/ext4_drive/backup.tar.zst", mountinfo_path=mountinfo_file) is False

    # 4. Sekizlik kaçış (\040 -> boşluk) çözümü
    assert get_filesystem_type("/media/usb drive/backup.tar.zst", mountinfo_path=mountinfo_file) == "vfat"
    assert is_fat_filesystem("/media/usb drive/backup.tar.zst", mountinfo_path=mountinfo_file) is True

    # 5. Dosya bulunamadığında veya tespit edilemediğinde korumayı atla (None / False)
    assert get_filesystem_type("/media/unknown/backup.tar.zst", mountinfo_path=tmp_path / "nonexistent") is None
    assert is_fat_filesystem("/media/unknown/backup.tar.zst", mountinfo_path=tmp_path / "nonexistent") is False


def test_fat_safeguard_archive_large_and_small_size(tmp_path, monkeypatch):
    """
    [KRİTİK-4] FAT hedefte büyük tahmini boyutta (>3.5 GiB) korumanın devreye girmesi,
    küçük boyutta (<3.5 GiB) devreye girmemesi ve append modunda mevcut boyutun eklenmesi testi.
    """
    import os
    import pytest
    from distronomad.models import SystemInfo, ConfigItem, CustomPathItem
    from distronomad.backup import (
        BackupEngine,
        check_fat32_risk,
        Fat32LimitError,
    )

    fake_mountinfo = tmp_path / "mountinfo_fat"
    fake_mountinfo.write_text(f"100 1 8:1 / {tmp_path} rw - vfat /dev/sdb1 rw\n", encoding="utf-8")

    sys_info = SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    out_archive = tmp_path / "my_backup.tar.zst"

    # 1. Küçük boyut (ör. 50 MB < 3.5 GiB) -> has_risk=False, koruma devreye girmez
    small_cfgs = [ConfigItem(id="c1", name="app", category="Genel", source_path=str(tmp_path), rel_path=".config/app", size_bytes=50 * 1024 * 1024)]
    risk_small = check_fat32_risk(
        target_path=out_archive,
        archive_format="tar.zst",
        configs=small_cfgs,
        mountinfo_path=fake_mountinfo
    )
    assert risk_small.has_risk is False

    # 2. Büyük boyut (ör. 3.8 GiB > 3.5 GiB) -> has_risk=True, koruma devreye girer
    large_size = int(3.8 * 1024 * 1024 * 1024)
    large_cfgs = [ConfigItem(id="c1", name="app", category="Genel", source_path=str(tmp_path), rel_path=".config/app", size_bytes=large_size)]
    risk_large = check_fat32_risk(
        target_path=out_archive,
        archive_format="tar.zst",
        configs=large_cfgs,
        mountinfo_path=fake_mountinfo
    )
    assert risk_large.has_risk is True
    assert risk_large.reason == "archive_size_exceeded"
    assert "exFAT veya ext4" in risk_large.message or "exFAT or ext4" in risk_large.message

    # BackupEngine.run() doğrudan çağrıldığında Fat32LimitError fırlatmalı
    engine = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=large_cfgs,
        custom_paths=[],
        output_path=out_archive,
        mountinfo_path=fake_mountinfo
    )
    with pytest.raises(Fat32LimitError) as exc_info:
        engine.run()
    assert exc_info.value.risk_assessment.has_risk is True

    # force=True verildiğinde koruma hatası fırlatılmamalı
    out_archive.write_bytes(b"dummy")
    engine_force = BackupEngine(
        system_info=sys_info,
        packages=[],
        configs=large_cfgs,
        custom_paths=[],
        output_path=out_archive,
        mountinfo_path=fake_mountinfo,
        force=True
    )
    monkeypatch.setattr(BackupEngine, "_finalize_archive", lambda self, s: out_archive)
    monkeypatch.setattr("os.replace", lambda src, dst: None)
    # force=True run() çağrıldığında check_fat32_risk baypas edilir
    res = engine_force.run()
    assert res == out_archive

    # 3. Append modu: Mevcut arşiv boyutu (2 GiB) + Yeni seçilenler (2 GiB) = 4 GiB > 3.5 GiB
    existing_archive = tmp_path / "existing.tar.zst"
    existing_archive.write_bytes(b"x")
    orig_stat = Path.stat
    # Python 3.10-3.11'de Path.lstat() içeride self.stat(follow_symlinks=False) çağırır; argümanlar aktarılmalıdır
    def fake_stat_append(self, *args, **kwargs):
        if str(self) == str(existing_archive):
            return os.stat_result((0o100644, 0, 0, 1, 1000, 1000, 2 * 1024 * 1024 * 1024, 0, 0, 0))
        return orig_stat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", fake_stat_append)

    append_cfgs = [ConfigItem(id="c2", name="app2", category="Genel", source_path=str(tmp_path), rel_path=".config/app2", size_bytes=2 * 1024 * 1024 * 1024)]
    risk_append = check_fat32_risk(
        target_path=existing_archive,
        archive_format="tar.zst",
        backup_mode="append",
        configs=append_cfgs,
        mountinfo_path=fake_mountinfo
    )
    assert risk_append.has_risk is True
    assert risk_append.reason == "archive_size_exceeded"

    # 4. Dizin (dir) formatında kontrol (A-P2-4: FAT32'de dir formatı sembolik bağ ve izinleri koruyamaz, risk üretir)
    dir_target = tmp_path / "backup_dir"
    test_file_ok = tmp_path / "small_file.bin"
    test_file_ok.write_bytes(b"small")
    cp_ok = [CustomPathItem(path=str(test_file_ok), size_bytes=1024)]
    risk_dir_ok = check_fat32_risk(
        target_path=dir_target,
        archive_format="dir",
        custom_paths=cp_ok,
        mountinfo_path=fake_mountinfo
    )
    assert risk_dir_ok.has_risk is True
    assert risk_dir_ok.reason == "fat32_dir_unsupported"

    # 4.2. Tek dosya >= 4 GiB -> has_risk=True
    test_file_big = tmp_path / "huge_file.iso"
    test_file_big.write_bytes(b"iso")
    def fake_stat_big(self, *args, **kwargs):
        if str(self).endswith("huge_file.iso"):
            return os.stat_result((0o100644, 0, 0, 1, 1000, 1000, int(4.5 * 1024 * 1024 * 1024), 0, 0, 0))
        return orig_stat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", fake_stat_big)

    cp_big = [CustomPathItem(path=str(test_file_big), size_bytes=int(4.5 * 1024 * 1024 * 1024))]
    risk_dir_big = check_fat32_risk(
        target_path=dir_target,
        archive_format="dir",
        custom_paths=cp_big,
        mountinfo_path=fake_mountinfo
    )
    assert risk_dir_big.has_risk is True
    assert risk_dir_big.reason == "single_file_exceeded"
    assert risk_dir_big.offending_file == test_file_big


def test_exfat_safeguard_not_triggered_even_for_large_size(tmp_path):
    """
    [KRİTİK-4] exFAT dosya sisteminde 4 GB sınırı olmadığı için büyük boyutta bile
    korumanın devreye girmediğini doğrular.
    """
    from distronomad.models import ConfigItem
    from distronomad.backup import check_fat32_risk

    fake_mountinfo = tmp_path / "mountinfo_exfat"
    fake_mountinfo.write_text(f"100 1 8:1 / {tmp_path} rw - exfat /dev/sdb1 rw\n", encoding="utf-8")

    out_archive = tmp_path / "large_backup.tar.zst"
    large_size = int(10 * 1024 * 1024 * 1024)
    cfgs = [ConfigItem(id="c1", name="large", category="Genel", source_path=str(tmp_path), rel_path=".config/large", size_bytes=large_size)]

    risk = check_fat32_risk(
        target_path=out_archive,
        archive_format="tar.zst",
        configs=cfgs,
        mountinfo_path=fake_mountinfo
    )
    assert risk.has_risk is False
    assert risk.target_fs == "exfat"


def test_cli_fat_safeguard_exit_code_and_force(tmp_path, monkeypatch):
    """
    [KRİTİK-4] CLI'da FAT hedef ve büyük boyutta --force bayrağı olmadan sıfırdan farklı
    çıkış kodu (sys.exit(1)) verildiğini, --force ile başarılı devam edildiğini doğrular.
    """
    import argparse
    import pytest
    from distronomad.cli import cmd_backup
    from distronomad.backup import Fat32RiskAssessment

    out_archive = tmp_path / "backup.tar.zst"
    args_no_force = argparse.Namespace(
        dest=str(out_archive),
        format="tar.zst",
        configs_only=False,
        no_sensitive=False,
        incremental=False,
        append=False,
        base=None,
        password=None,
        no_ignore=False,
        force=False,
        home=str(tmp_path)
    )

    monkeypatch.setattr(
        "distronomad.cli.check_fat32_risk",
        lambda **kwargs: Fat32RiskAssessment(
            has_risk=True,
            reason="archive_size_exceeded",
            message="FAT32 4 GB Sınırı Uyarısı"
        )
    )

    # 1. --force olmadan çağrıldığında sys.exit(1) fırlatılmalı
    with pytest.raises(SystemExit) as exc_info:
        cmd_backup(args_no_force)
    assert exc_info.value.code != 0

    # 2. --force ile çağrıldığında sys.exit fırlatılmamalı
    args_with_force = argparse.Namespace(
        dest=str(out_archive),
        format="tar.zst",
        configs_only=False,
        no_sensitive=False,
        incremental=False,
        append=False,
        base=None,
        password=None,
        no_ignore=False,
        force=True,
        home=str(tmp_path)
    )
    monkeypatch.setattr("distronomad.cli.BackupEngine.run", lambda self: out_archive)
    cmd_backup(args_with_force)


def test_efbig_error_handling_and_cleanup(tmp_path, monkeypatch):
    """
    [KRİTİK-4] Yazma sırasında errno EFBIG (File too large) hatası oluştuğunda anlaşılır
    bir mesaj verildiğini ve yarım kalan geçici/çıktı dosyalarının temizlendiğini doğrular.
    """
    import errno
    import pytest
    from distronomad.models import SystemInfo, PackageItem
    from distronomad.backup import BackupEngine, Fat32LimitError

    sys_info = SystemInfo(distro_id="arch", home_dir=str(tmp_path))
    out_archive = tmp_path / "efbig_backup.tar.zst"

    def fake_stream_writer(*args, **kwargs):
        raise OSError(errno.EFBIG, "File too large")

    import zstandard
    monkeypatch.setattr(zstandard.ZstdCompressor, "stream_writer", fake_stream_writer)

    engine = BackupEngine(
        system_info=sys_info,
        packages=[PackageItem(name="pkg1", manager="pacman")],
        configs=[],
        custom_paths=[],
        output_path=out_archive,
        archive_format="tar.zst",
        force=True
    )

    with pytest.raises(Fat32LimitError) as exc_info:
        engine.run()

    err_text = str(exc_info.value)
    assert "4 GB" in err_text or "exFAT" in err_text or "ext4" in err_text

    assert not out_archive.exists(), "Yarım kalan ana arşiv dosyası diskte kalmamalı!"
    leftover_tmps = list(tmp_path.glob(".tmp_*")) + list(tmp_path.glob("distronomad_meta_*"))
    assert len(leftover_tmps) == 0, f"Diskte temizlenmemiş geçici artıklar var: {leftover_tmps}"


@pytest.mark.asyncio
async def test_tui_fat_safeguard_cancel_modal_does_not_start_backup(tmp_path, monkeypatch):
    """
    [KRİTİK-4] TUI onay penceresinde 'İptal Et' tıklandığında yedeğin başlamadığını doğrular.
    """
    from textual.widgets import Input
    from distronomad.ui.app import DistroNomadApp
    from distronomad.ui.modal import Fat32WarningModal
    from distronomad.backup import Fat32RiskAssessment

    app = DistroNomadApp(home_dir=str(tmp_path))

    monkeypatch.setattr(
        "distronomad.ui.app.check_fat32_risk",
        lambda **kwargs: Fat32RiskAssessment(
            has_risk=True,
            reason="archive_size_exceeded",
            message="FAT32 4 GB Sınırı Uyarısı: USB belleğinizi exFAT veya ext4 formatına dönüştürün."
        )
    )

    worker_started = False
    def fake_backup_worker(self, *args, **kwargs):
        nonlocal worker_started
        worker_started = True

    monkeypatch.setattr(DistroNomadApp, "_run_backup_worker", fake_backup_worker)

    async with app.run_test(size=(120, 60)) as pilot:
        app.query_one("#input-backup-dest", Input).value = str(tmp_path / "usb_backup.tar.zst")

        app._handle_start_backup()
        await pilot.pause()

        assert isinstance(app.screen, Fat32WarningModal)

        await pilot.click("#btn-fat32-cancel")
        await pilot.pause()

        assert not isinstance(app.screen, Fat32WarningModal)
        assert app._is_backing_up is False
        assert worker_started is False


def test_d3_scan_installed_packages_preserves_order_with_varying_delays(monkeypatch):
    """
    [D-3] Farklı gecikmelere rağmen sonuç paketlerinin yönetici sırasını birebir koruduğunu doğrular.
    """
    import time
    from distronomad.discovery import SystemScanner
    from distronomad.models import PackageItem

    scanner = SystemScanner()
    pms = ["pacman", "apt", "flatpak", "snap", "pipx", "cargo", "npm", "code", "codium"]

    # pacman en yavaş (0.04s), sonrakiler daha hızlı biter ama sonuç sırası pacman -> apt -> ... olmalıdır
    delays = {
        "pacman": 0.04,
        "apt": 0.01,
        "flatpak": 0.03,
        "snap": 0.01,
        "pipx": 0.02,
        "cargo": 0.01,
        "npm": 0.02,
    }

    for pm in ["pacman", "apt", "flatpak", "snap", "pipx", "cargo", "npm"]:
        def _make_scan(pm_name, delay):
            def fake():
                time.sleep(delay)
                return [PackageItem(name=f"pkg-{pm_name}", manager=pm_name)]
            return fake
        monkeypatch.setattr(scanner, f"_scan_{pm}", _make_scan(pm, delays[pm]))

    def fake_scan_vscode(binary, manager_name, category):
        time.sleep(0.01)
        return [PackageItem(name=f"ext-{manager_name}", manager=manager_name, category=category)]

    monkeypatch.setattr(scanner, "_scan_vscode", fake_scan_vscode)

    res = scanner.scan_installed_packages(pms_to_scan=pms)
    expected_order = [
        "pkg-pacman",
        "pkg-apt",
        "pkg-flatpak",
        "pkg-snap",
        "pkg-pipx",
        "pkg-cargo",
        "pkg-npm",
        "ext-vscode",
        "ext-codium",
    ]
    actual_order = [p.name for p in res]
    assert actual_order == expected_order


def test_d3_scan_installed_packages_exception_and_timeout_isolation(monkeypatch):
    """
    [D-3] Bir veya birden fazla tarayıcının istisna fırlatması veya zaman aşımına uğramasının
    diğer tarayıcıların sonuçlarını etkilemediğini doğrular.
    """
    from distronomad.discovery import SystemScanner
    from distronomad.models import PackageItem

    scanner = SystemScanner()
    pms = ["pacman", "apt", "flatpak", "snap"]

    monkeypatch.setattr(scanner, "_scan_pacman", lambda: [PackageItem(name="pkg-pacman", manager="pacman")])

    def failing_apt():
        raise RuntimeError("Apt veritabanı kilitli")

    def timeout_snap():
        raise TimeoutError("Snap servisi yanıt vermedi")

    monkeypatch.setattr(scanner, "_scan_apt", failing_apt)
    monkeypatch.setattr(scanner, "_scan_flatpak", lambda: [PackageItem(name="pkg-flatpak", manager="flatpak")])
    monkeypatch.setattr(scanner, "_scan_snap", timeout_snap)

    res = scanner.scan_installed_packages(pms_to_scan=pms)
    names = [p.name for p in res]
    assert "pkg-pacman" in names
    assert "pkg-flatpak" in names
    assert len(res) == 2


def test_d3_scan_installed_packages_truly_concurrent(monkeypatch):
    """
    [D-3] Taramaların paralel çalıştığını (toplam sürenin sıralı toplam yerine en yavaş taramaya yakın olduğunu) doğrular.
    """
    import time
    from distronomad.discovery import SystemScanner
    from distronomad.models import PackageItem

    scanner = SystemScanner()
    pms = ["pacman", "apt", "flatpak", "snap"]

    # 4 tarayıcı, her biri 0.08 saniye bekler.
    # Sıralı çalışsaydı en az 0.32 saniye sürerdi.
    # 4 iş parçacıklı paralel havuzda toplam süre ~0.08 - 0.18 saniye arasında olmalıdır.
    for pm in pms:
        def _make_scan(pm_name):
            def fake():
                time.sleep(0.08)
                return [PackageItem(name=f"pkg-{pm_name}", manager=pm_name)]
            return fake
        monkeypatch.setattr(scanner, f"_scan_{pm}", _make_scan(pm))

    t_start = time.perf_counter()
    res = scanner.scan_installed_packages(pms_to_scan=pms)
    t_elapsed = time.perf_counter() - t_start

    assert len(res) == 4
    assert t_elapsed < 0.22, f"Paralel süre ({t_elapsed:.4f}s) sıralı süreye (>=0.32s) çok yakın, paralellik sağlanamadı!"


def test_d3_scan_installed_packages_pms_to_scan_filter(monkeypatch):
    """
    [D-3] pms_to_scan parametresinin boş liste ve filtreleme davranışını doğrular.
    """
    from distronomad.discovery import SystemScanner
    from distronomad.models import PackageItem

    scanner = SystemScanner()

    # 1. Boş liste verildiğinde hiç tarama yapılmamalı ve anında boş liste dönmeli
    assert scanner.scan_installed_packages(pms_to_scan=[]) == []

    # 2. Sadece belirli bir yönetici verildiğinde yalnızca o çalışmalı
    called = []
    monkeypatch.setattr(scanner, "_scan_flatpak", lambda: called.append("flatpak") or [PackageItem(name="fp", manager="flatpak")])
    monkeypatch.setattr(scanner, "_scan_pacman", lambda: called.append("pacman") or [PackageItem(name="pac", manager="pacman")])

    res = scanner.scan_installed_packages(pms_to_scan=["flatpak"])
    assert called == ["flatpak"]
    assert len(res) == 1
    assert res[0].name == "fp"





