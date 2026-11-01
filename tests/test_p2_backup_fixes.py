"""
DistroNomad - P2 Yedekleme Bulguları Regresyon Testleri.
(1) Append modunda gerçek format tespiti (dir, tar, tar.zst, şifreli)
(2) Artımlı değişiklik tespiti (symlink hedefi, mtime geri gidişi, zincir metadata)
(3) Sembolik bağ olan dizinlerin hedefsiz yedeklenmesi ve doğru restore edilmesi
(4) Yok sayma kurallarının yalnızca seçilen kök altındaki yollara uygulanması
"""

import os
import time
from pathlib import Path
from distronomad.models import SystemInfo, ConfigItem, CustomPathItem, RestoreOptions
from distronomad.backup import BackupEngine, is_file_changed, detect_backup_format
from distronomad.restore import RestoreEngine
from distronomad.ignore import SmartIgnoreFilter


def _make_dummy_sysinfo(home_dir: Path) -> SystemInfo:
    return SystemInfo(
        distro_id="arch",
        distro_name="Arch Linux",
        distro_version="rolling",
        kernel_version="6.12.0",
        home_dir=str(home_dir),
        username="testuser"
    )


# ==================== (1) APPEND FORMAT DETECTION & COMPATIBILITY ====================

def test_detect_backup_format(tmp_path):
    """dir, .tar, .tar.zst formatlarının hedefin gerçek durumundan doğru tespit edildiğini doğrular."""
    # 1. Dizin
    dir_backup = tmp_path / "test_dir_backup"
    dir_backup.mkdir()
    assert detect_backup_format(dir_backup) == "dir"

    # 2. Düz .tar
    import tarfile
    tar_backup = tmp_path / "test_plain.tar"
    with tarfile.open(tar_backup, "w") as tar:
        pass
    assert detect_backup_format(tar_backup) == "tar"

    # 3. .tar.zst
    zst_backup = tmp_path / "test_zst.tar.zst"
    zst_backup.write_bytes(b"\x28\xb5\x2f\xfd\x00\x00")
    assert detect_backup_format(zst_backup) == "tar.zst"


def test_append_mode_all_formats(tmp_path):
    """Append modunun dir, .tar, .tar.zst ve şifreli arşivlerin hepsinde çalıştığını doğrular."""
    home = tmp_path / "home"
    home.mkdir()
    sysinfo = _make_dummy_sysinfo(home)

    data_dir = home / "data"
    data_dir.mkdir()
    f1 = data_dir / "file1.txt"
    f1.write_text("content1")
    f2 = data_dir / "file2.txt"
    f2.write_text("content2")

    formats_and_targets = [
        ("dir", tmp_path / "backups" / "backup_dir"),
        ("tar", tmp_path / "backups" / "backup.tar"),
        ("tar.zst", tmp_path / "backups" / "backup.tar.zst"),
    ]

    for fmt, out_path in formats_and_targets:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 1. İlk yedek (sadece f1)
        cp1 = CustomPathItem(path=str(f1), label="f1", is_selected=True)
        engine1 = BackupEngine(
            system_info=sysinfo,
            packages=[],
            configs=[],
            custom_paths=[cp1],
            output_path=out_path,
            archive_format=fmt,
            backup_mode="full"
        )
        res1 = engine1.run()
        assert res1.exists()

        # 2. Append modunda f2'yi ekle (TUI varsayılan adı out_path iken)
        cp2 = CustomPathItem(path=str(f2), label="f2", is_selected=True)
        # TUI varsayılan adı .tar.zst iken bile dir veya tar hedefe append başarılı olmalıdır
        dummy_output = tmp_path / "backups" / "backup.tar.zst" if fmt != "tar.zst" else out_path
        engine_append = BackupEngine(
            system_info=sysinfo,
            packages=[],
            configs=[],
            custom_paths=[cp2],
            output_path=out_path,
            archive_format="tar.zst",  # TUI varsayılan formatı tar.zst kalsa bile gerçek hedef algılanmalı
            backup_mode="append",
            base_backup_path=out_path
        )
        res_app = engine_append.run()
        assert res_app.exists()

        # Doğrulama: RestoreEngine ile içeriği aç ve her iki dosyanın da olduğunu gör
        re = RestoreEngine(res_app)
        manifest = re.load_manifest(extract_files=True)
        assert len(manifest.custom_paths) == 2
        restored = re.ensure_extracted()
        assert (restored / "custom" / "home" / "data" / "file1.txt").exists()
        assert (restored / "custom" / "home" / "data" / "file2.txt").exists()


def test_append_mode_encrypted_archive(tmp_path):
    """Şifreli arşive append işleminin doğru çalıştığını doğrular."""
    home = tmp_path / "home"
    home.mkdir()
    sysinfo = _make_dummy_sysinfo(home)

    f1 = home / "secret1.txt"
    f1.write_text("secret1")
    f2 = home / "secret2.txt"
    f2.write_text("secret2")

    enc_out = tmp_path / "enc_backup.tar.zst"
    pwd = "TestPassword123!"

    # 1. Şifreli ilk yedek
    engine1 = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(f1), label="s1", is_selected=True)],
        output_path=enc_out,
        archive_format="tar.zst",
        backup_mode="full",
        password=pwd
    )
    res_enc = engine1.run()
    assert res_enc.exists()
    assert str(res_enc).endswith(".enc")

    # 2. Şifreli yedeğe append
    engine_app = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[CustomPathItem(path=str(f2), label="s2", is_selected=True)],
        output_path=res_enc,
        archive_format="tar.zst",
        backup_mode="append",
        password=pwd
    )
    final_enc = engine_app.run()
    assert final_enc.exists()

    # Restore ile doğrula
    re = RestoreEngine(final_enc, password=pwd)
    manifest = re.load_manifest(extract_files=True)
    assert len(manifest.custom_paths) == 2
    restored = re.ensure_extracted()
    assert (restored / "custom" / "home" / "secret1.txt").exists()
    assert (restored / "custom" / "home" / "secret2.txt").exists()


# ==================== (2) INCREMENTAL CHANGE DETECTION ====================

def test_incremental_symlink_change_detection(tmp_path):
    """Sembolik bağların her seferinde değişmiş sayılmadığını, readlink hedefiyle karşılaştırıldığını doğrular."""
    home = tmp_path / "home"
    home.mkdir()
    sysinfo = _make_dummy_sysinfo(home)

    target_a = home / "target_a.txt"
    target_a.write_text("target a")
    target_b = home / "target_b.txt"
    target_b.write_text("target b")

    link_file = home / "my_link"
    os.symlink(str(target_a), link_file)

    # 1. Tam yedek
    backup_full = tmp_path / "full.tar.zst"
    cp = CustomPathItem(path=str(link_file), label="link", is_selected=True)
    e_full = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=backup_full,
        archive_format="tar.zst",
        backup_mode="full"
    )
    e_full.run()

    # 2. Hedef değişmediğinde artımlı yedek: dosya "unmodified" olmalı
    e_inc1 = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=tmp_path / "inc1.tar.zst",
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=backup_full
    )
    e_inc1.run()
    assert e_inc1.incremental_stats["changed_count"] == 0
    assert e_inc1.incremental_stats["unchanged_count"] == 1

    # 3. Sembolik bağın hedefi değiştirildiğinde: "modified" olmalı
    link_file.unlink()
    os.symlink(str(target_b), link_file)

    e_inc2 = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=tmp_path / "inc2.tar.zst",
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=backup_full
    )
    e_inc2.run()
    assert e_inc2.incremental_stats["changed_count"] == 1


def test_incremental_mtime_backward_detection(tmp_path):
    """mtime geriye gittiğinde (boyut aynı kalsa dahi) değişikliğin kaçırılmadığını doğrular."""
    home = tmp_path / "home"
    home.mkdir()
    sysinfo = _make_dummy_sysinfo(home)

    test_file = home / "time_travel.txt"
    test_file.write_text("constant_length_data_12345")

    backup_full = tmp_path / "full_mtime.tar.zst"
    cp = CustomPathItem(path=str(test_file), label="tt", is_selected=True)
    e_full = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=backup_full,
        archive_format="tar.zst",
        backup_mode="full"
    )
    e_full.run()

    # mtime'ı 1 saat geriye çek (boyut aynı)
    current_stat = test_file.stat()
    os.utime(test_file, (current_stat.st_atime - 3600, current_stat.st_mtime - 3600))

    e_inc = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=tmp_path / "inc_mtime.tar.zst",
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=backup_full
    )
    e_inc.run()
    assert e_inc.incremental_stats["changed_count"] == 1
    assert e_inc.incremental_stats["unchanged_count"] == 0


def test_incremental_chain_metadata_preservation(tmp_path):
    """Zincirdeki dosyaların (full -> inc1 -> inc2) gereksiz yere yeniden depolanmadığını doğrular."""
    home = tmp_path / "home"
    home.mkdir()
    sysinfo = _make_dummy_sysinfo(home)

    f_a = home / "file_a.txt"
    f_a.write_text("file A")
    f_b = home / "file_b.txt"
    f_b.write_text("file B")

    cp_a = CustomPathItem(path=str(f_a), label="a", is_selected=True)
    cp_b = CustomPathItem(path=str(f_b), label="b", is_selected=True)

    # 1. Full: file_a ve file_b
    full_path = tmp_path / "chain_full.tar.zst"
    e_full = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp_a, cp_b],
        output_path=full_path,
        archive_format="tar.zst",
        backup_mode="full"
    )
    e_full.run()

    # 2. Inc1: sadece file_a değişti
    time.sleep(0.05)
    f_a.write_text("file A modified!")
    inc1_path = tmp_path / "chain_inc1.tar.zst"
    e_inc1 = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp_a, cp_b],
        output_path=inc1_path,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=full_path
    )
    e_inc1.run()
    assert e_inc1.incremental_stats["changed_count"] == 1
    assert e_inc1.incremental_stats["unchanged_count"] == 1

    # 3. Inc2: hiçbir dosya değişmedi; base olarak inc1 verildiğinde file_b gereksiz depolanmamalı
    inc2_path = tmp_path / "chain_inc2.tar.zst"
    e_inc2 = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp_a, cp_b],
        output_path=inc2_path,
        archive_format="tar.zst",
        backup_mode="incremental",
        base_backup_path=inc1_path
    )
    e_inc2.run()
    # file_b inc1'de kopyalanmamış olsa bile zincirden (full'dan) en son hali bilinir ve unmodified sayılır
    assert e_inc2.incremental_stats["changed_count"] == 0
    assert e_inc2.incremental_stats["unchanged_count"] == 2


# ==================== (3) DIRECTORY SYMLINKS BACKUP & RESTORE ====================

def test_directory_symlink_backup_and_restore(tmp_path):
    """Sembolik bağ olan DİZİNLERİN hedefsiz sembolik bağ olarak arşive girdiğini ve doğru restore edildiğini doğrular."""
    home = tmp_path / "home"
    home.mkdir()
    sysinfo = _make_dummy_sysinfo(home)

    # Gerçek hedef dizin
    real_target_dir = home / "real_target_dir"
    real_target_dir.mkdir()
    (real_target_dir / "target_file.txt").write_text("hello from target")

    # Proje dizini (içinde sembolik bağ dizin barındıran)
    project_dir = home / "my_project"
    project_dir.mkdir()
    (project_dir / "code.py").write_text("print(1)")

    # my_project/sym_subdir -> real_target_dir
    sym_subdir = project_dir / "sym_subdir"
    os.symlink(str(real_target_dir), sym_subdir)

    assert sym_subdir.is_symlink()
    assert sym_subdir.is_dir()

    # Yedek al
    backup_path = tmp_path / "symdir_backup.tar.zst"
    cp = CustomPathItem(path=str(project_dir), label="project", is_selected=True)
    engine = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=backup_path,
        archive_format="tar.zst",
        backup_mode="full"
    )
    res = engine.run()
    assert res.exists()

    # Arşiv içindeki yapıyı kontrol et
    re = RestoreEngine(res)
    extracted = re.ensure_extracted()
    extracted_sym = extracted / "custom" / "home" / "my_project" / "sym_subdir"
    assert extracted_sym.is_symlink(), "Arşivdeki dizin sembolik bağı sembolik bağ olarak saklanmamış!"
    assert os.readlink(extracted_sym) == str(real_target_dir)

    # Geri yükleme testi
    restore_dest = tmp_path / "restore_home"
    restore_dest.mkdir()
    opts = RestoreOptions(
        selected_custom_paths=[cp],
        target_home=str(restore_dest)
    )
    re.execute_restore(opts)

    restored_sym = restore_dest / "my_project" / "sym_subdir"
    assert restored_sym.is_symlink(), "Geri yüklenen dizin sembolik bağı gerçek dizine dönüştürülmüş veya kaybolmuş!"
    assert os.readlink(restored_sym) == str(real_target_dir)


# ==================== (4) SMART IGNORE ON CHOSEN ROOT ====================

def test_smart_ignore_under_chosen_root(tmp_path):
    """Üst dizininde Cache geçen seçili kökün yok sayılmadığını, yalnızca kök altındaki kuralların işletildiğini doğrular."""
    # Üst dizin adında "Cache" ve "cache" geçsin
    parent_with_cache = tmp_path / "MainCacheDir" / "local_cache"
    parent_with_cache.mkdir(parents=True)

    # Kullanıcının yedeklemek istediği proje kökü
    chosen_root = parent_with_cache / "special_project"
    chosen_root.mkdir()

    valid_file = chosen_root / "main.py"
    valid_file.write_text("print('ok')")

    # Kökün ALTINDAKİ önbellek ve kesin çöp klasörleri
    inner_cache = chosen_root / "Cache" / "cache_data.bin"
    inner_cache.parent.mkdir()
    inner_cache.write_bytes(b"temp")

    inner_trash = chosen_root / "__pycache__" / "temp.cpython-312.pyc"
    inner_trash.parent.mkdir()
    inner_trash.write_bytes(b"trash")

    flt = SmartIgnoreFilter(use_smart_defaults=True)

    # 1. Filtre doğrudan denetimi:
    # Seçilen kök ve altındaki meşru dosya ignore edilmemeli
    assert not flt.should_ignore(chosen_root, base_path=chosen_root)
    assert not flt.should_ignore(valid_file, base_path=chosen_root)

    # Konfigürasyon modunda Cache dosyası ignore edilir
    assert flt.should_ignore(inner_cache, base_path=chosen_root, is_custom_path=False)
    # A-P1-1: Kullanıcının özel yollarında (custom paths) Cache elenmez, meşru kod korunur
    assert not flt.should_ignore(inner_cache, base_path=chosen_root, is_custom_path=True)
    # Ancak kesin çöp (__pycache__, node_modules vb.) özel yollarda da elenir
    assert flt.should_ignore(inner_trash, base_path=chosen_root, is_custom_path=True)

    # 2. BackupEngine ile uçtan uca doğrulama
    sysinfo = _make_dummy_sysinfo(tmp_path)
    cp = CustomPathItem(path=str(chosen_root), label="chosen", is_selected=True)
    out = tmp_path / "chosen_backup.tar.zst"
    engine = BackupEngine(
        system_info=sysinfo,
        packages=[],
        configs=[],
        custom_paths=[cp],
        output_path=out,
        archive_format="tar.zst",
        backup_mode="full",
        use_smart_ignore=True
    )
    engine.run()

    re = RestoreEngine(out)
    extracted = re.ensure_extracted()
    # chosen_root altındaki main.py ve Cache/cache_data.bin korunmalı, __pycache__ elenmeli (A-P1-1)
    custom_tree = list(extracted.rglob("main.py"))
    assert len(custom_tree) == 1
    cache_tree = list(extracted.rglob("cache_data.bin"))
    assert len(cache_tree) == 1
    trash_tree = list(extracted.rglob("*.pyc"))
    assert len(trash_tree) == 0
