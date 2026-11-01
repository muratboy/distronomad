"""
DistroNomad - Tembel (On-Demand / Lazy) Diff Motoru ve Görüntüleyici Testleri (D-4).
"""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Label, RichLog

from distronomad.diff_utils import (
    compare_backup_with_host,
    determine_file_status,
    generate_unified_diff,
    compute_file_diff,
    get_file_diff,
    is_binary_file,
    DEFAULT_MAX_DIFF_SIZE,
)
from distronomad.ui.modal import DiffViewerModal
from distronomad.i18n import set_current_language, get_current_language


def test_compare_backup_summary_no_full_diff_called(tmp_path, monkeypatch):
    """
    Özet oluşturulurken generate_unified_diff'in ASLA çağrılmadığını (monkeypatch)
    ve durumların (identical, modified, new_in_backup, binary) doğru belirlendiğini doğrular.
    """
    host_home = tmp_path / "host_home"
    host_home.mkdir()

    # 1. Aynı dosya (identical)
    (host_home / "same.conf").write_text("setting=1\nvalue=true\n", encoding="utf-8")

    # 2. Değişmiş dosya (modified)
    (host_home / "mod.conf").write_text("color=blue\n", encoding="utf-8")

    # 3. İkili dosya (host tarafında)
    (host_home / "host_bin.bin").write_bytes(b"\x00\x01\x02\x03")

    # Yedek dizini hazırla
    backup_extracted = tmp_path / "backup_extracted"
    configs_dir = backup_extracted / "configs"
    configs_dir.mkdir(parents=True)

    (configs_dir / "same.conf").write_text("setting=1\nvalue=true\n", encoding="utf-8")
    (configs_dir / "mod.conf").write_text("color=red\nfont=fira\n", encoding="utf-8")
    (configs_dir / "new.conf").write_text("new_entry=yes\n", encoding="utf-8")
    (configs_dir / "backup_bin.bin").write_bytes(b"DATA\x00BIN")

    # generate_unified_diff fonksiyonuna monkeypatch koy: çağrılırsa test patlasın!
    def fail_if_diff_called(*args, **kwargs):
        pytest.fail("Özet listesi oluşturulurken generate_unified_diff çağrılmamalıdır!")

    monkeypatch.setattr("distronomad.diff_utils.generate_unified_diff", fail_if_diff_called)

    # Özet fonksiyonunu çağır
    summaries = compare_backup_with_host(backup_extracted, target_home=host_home)

    assert len(summaries) == 4
    by_rel = {s["rel_path"]: s for s in summaries}

    # Aynı dosya
    assert by_rel["same.conf"]["status"] == "identical"
    assert by_rel["same.conf"]["diff_text"] is None
    assert by_rel["same.conf"]["added"] is None
    assert by_rel["same.conf"]["removed"] is None

    # Değişmiş dosya
    assert by_rel["mod.conf"]["status"] == "modified"
    assert by_rel["mod.conf"]["diff_text"] is None

    # Yeni dosya
    assert by_rel["new.conf"]["status"] == "new_in_backup"
    assert by_rel["new.conf"]["diff_text"] is None

    # İkili dosya
    assert by_rel["backup_bin.bin"]["status"] == "binary"
    assert by_rel["backup_bin.bin"]["diff_text"] is None


def test_determine_file_status_cases(tmp_path):
    """
    Boyut ve içerik karşılaştırmasıyla durum tespitini doğrular:
    - Yeni dosya (new_in_backup)
    - İkili dosya (binary)
    - Boyut farkından değişmiş (modified - hızlı yol)
    - Boyut aynı, bayt farkından değişmiş (modified - içerik yolu)
    - Birebir aynı (identical)
    """
    host_dir = tmp_path / "host"
    backup_dir = tmp_path / "backup"
    host_dir.mkdir()
    backup_dir.mkdir()

    # 1. Yeni dosya
    bf_new = backup_dir / "new.txt"
    bf_new.write_text("hello", encoding="utf-8")
    assert determine_file_status(host_dir / "nonexistent.txt", bf_new) == "new_in_backup"

    # 2. İkili dosya
    bf_bin = backup_dir / "sample.bin"
    bf_bin.write_bytes(b"ABC\x00DEF")
    hf_bin = host_dir / "sample.bin"
    hf_bin.write_bytes(b"ABC\x00XYZ")
    assert determine_file_status(hf_bin, bf_bin) == "binary"

    # 3. Boyut farkından değişmiş
    hf_sz = host_dir / "size_diff.txt"
    bf_sz = backup_dir / "size_diff.txt"
    hf_sz.write_text("short", encoding="utf-8")
    bf_sz.write_text("much longer content here", encoding="utf-8")
    assert determine_file_status(hf_sz, bf_sz) == "modified"

    # 4. Boyut aynı, içerik farklı
    hf_cnt = host_dir / "same_size.txt"
    bf_cnt = backup_dir / "same_size.txt"
    hf_cnt.write_text("hello 1", encoding="utf-8")
    bf_cnt.write_text("hello 2", encoding="utf-8")
    assert hf_cnt.stat().st_size == bf_cnt.stat().st_size
    assert determine_file_status(hf_cnt, bf_cnt) == "modified"

    # 5. Boyut ve içerik birebir aynı
    hf_id = host_dir / "identical.txt"
    bf_id = backup_dir / "identical.txt"
    content = "exact match line 1\nexact match line 2\n"
    hf_id.write_text(content, encoding="utf-8")
    bf_id.write_text(content, encoding="utf-8")
    assert determine_file_status(hf_id, bf_id) == "identical"


def test_on_demand_compute_file_diff(tmp_path):
    """İstendiğinde tek dosya için diff metni ve satır sayılarının doğru üretildiğini doğrular."""
    host_dir = tmp_path / "host"
    backup_dir = tmp_path / "backup"
    host_dir.mkdir()
    backup_dir.mkdir()

    # 1. Değişmiş dosya
    hf_mod = host_dir / "test.conf"
    bf_mod = backup_dir / "test.conf"
    hf_mod.write_text("line 1\nline 2 (old)\nline 3\n", encoding="utf-8")
    bf_mod.write_text("line 1\nline 2 (new)\nline 3\nline 4 (added)\n", encoding="utf-8")

    mod_item = {
        "category": "config",
        "rel_path": "test.conf",
        "host_path": str(hf_mod),
        "backup_path": str(bf_mod),
        "status": "modified",
        "added": None,
        "removed": None,
        "diff_text": None,
    }

    diff_text, added, removed = compute_file_diff(mod_item)
    assert added == 2
    assert removed == 1
    assert "+line 2 (new)" in diff_text
    assert "-line 2 (old)" in diff_text
    # item içine önbelleğe alınmış olmalı
    assert mod_item["diff_text"] == diff_text
    assert mod_item["added"] == 2
    assert mod_item["removed"] == 1

    # İkinci çağrıda önbellekten gelmeli
    diff_text2, added2, removed2 = compute_file_diff(mod_item)
    assert diff_text2 == diff_text
    assert added2 == 2

    # 2. Aynı dosya
    hf_same = host_dir / "same.txt"
    bf_same = backup_dir / "same.txt"
    hf_same.write_text("same\n", encoding="utf-8")
    bf_same.write_text("same\n", encoding="utf-8")
    same_item = {
        "status": "identical",
        "host_path": str(hf_same),
        "backup_path": str(bf_same),
    }
    diff_s, add_s, rem_s = compute_file_diff(same_item)
    assert add_s == 0
    assert rem_s == 0
    assert "aynı" in diff_s.lower() or "identical" in diff_s.lower()

    # 3. İkili dosya
    bf_bin = backup_dir / "icon.png"
    bf_bin.write_bytes(b"\x89PNG\r\n\x00")
    bin_item = {
        "status": "binary",
        "host_path": None,
        "backup_path": str(bf_bin),
    }
    diff_b, add_b, rem_b = compute_file_diff(bin_item)
    assert add_b == 0
    assert rem_b == 0
    assert "[İkili" in diff_b or "[Binary" in diff_b

    # 4. get_file_diff doğrudan çağrı testi
    d_direct, a_dir, r_dir = get_file_diff(hf_mod, bf_mod)
    assert a_dir == 2
    assert r_dir == 1


def test_diff_file_size_threshold_i18n(tmp_path):
    """
    Belirli bir eşiğin (max_file_size) üstündeki metin dosyalarında tam diff üretilmediğini,
    TR ve EN dillerinde açıklayıcı mesaj verildiğini doğrular.
    """
    host_file = tmp_path / "big_host.txt"
    backup_file = tmp_path / "big_backup.txt"

    # 100 baytlık dosya oluşturup eşiği 50 bayt verelim
    host_file.write_text("A" * 100, encoding="utf-8")
    backup_file.write_text("B" * 100, encoding="utf-8")

    # Türkçe testi
    set_current_language("tr", persist=False)
    diff_text_tr, added_tr, removed_tr = generate_unified_diff(
        host_file, backup_file, max_file_size=50
    )
    assert added_tr == 0
    assert removed_tr == 0
    assert "[Dosya çok büyük" in diff_text_tr
    assert "Metin farkı görüntülenemiyor" in diff_text_tr

    # İngilizce testi
    set_current_language("en", persist=False)
    diff_text_en, added_en, removed_en = generate_unified_diff(
        host_file, backup_file, max_file_size=50
    )
    assert added_en == 0
    assert removed_en == 0
    assert "[File too large" in diff_text_en
    assert "Text diff not displayed" in diff_text_en

    # Dili geri Türkçeye al
    set_current_language("tr", persist=False)


def test_cli_diff_output_and_show_content(tmp_path, capsys):
    """
    CLI diff komutunun çıktısının (durum, eklenen/silinen sütunları ve --show-content)
    kullanıcı açısından eksiksiz korunduğunu test eder.
    """
    from distronomad.cli import cmd_diff
    import argparse

    host_dir = tmp_path / "cli_host"
    host_dir.mkdir()
    (host_dir / ".bashrc").write_text("export FOO=1\n", encoding="utf-8")
    (host_dir / ".vimrc").write_text("set number\n", encoding="utf-8")

    # Sahte arşiv dizini ve manifest hazırla
    backup_dir = tmp_path / "cli_backup"
    backup_dir.mkdir()
    cfg_dir = backup_dir / "configs"
    cfg_dir.mkdir()

    # .bashrc değişmiş
    (cfg_dir / ".bashrc").write_text("export FOO=2\nexport BAR=3\n", encoding="utf-8")
    # .vimrc aynı
    (cfg_dir / ".vimrc").write_text("set number\n", encoding="utf-8")
    # .zshrc yeni
    (cfg_dir / ".zshrc").write_text("alias ll='ls -la'\n", encoding="utf-8")

    manifest_data = {
        "created_at": "2026-10-29",
        "version": "1.0",
        "archive_format": "dir",
        "total_bytes": 100,
        "system_info": {
            "distro_name": "Arch Linux",
            "distro_id": "arch",
            "distro_like": [],
            "kernel_version": "6",
            "desktop_environment": "KDE",
            "home_dir": str(host_dir),
            "shell": "zsh",
            "hostname": "h",
            "available_package_managers": []
        },
        "packages": [],
        "configs": [],
        "custom_paths": []
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    args = argparse.Namespace(
        src=str(backup_dir),
        target_home=str(host_dir),
        password=None,
        show_content=True
    )

    cmd_diff(args)
    captured = capsys.readouterr().out

    # Tablo başlıkları
    assert "Dosya Karşılaştırma Raporu" in captured
    assert "Kategori" in captured
    assert "Göreli Yol" in captured
    assert "Durum" in captured
    assert "Eklenen (+)" in captured
    assert "Silinen (-)" in captured

    # Satırlar
    assert ".bashrc" in captured
    assert "Değişmiş" in captured
    assert ".vimrc" in captured
    assert "Aynı" in captured
    assert ".zshrc" in captured
    assert "Yeni" in captured

    # --show-content paneli
    assert "export FOO=2" in captured


@pytest.mark.asyncio
async def test_tui_diff_viewer_modal_click(tmp_path):
    """
    TUI DiffViewerModal'ın açılışta yalnızca özetle geldiğini ve
    kullanıcı bir dosyaya tıkladığında diff'in üretilip gösterildiğini test eder.
    """
    host_file = tmp_path / "host.txt"
    backup_file = tmp_path / "backup.txt"

    host_file.write_text("line 1\nline 2 (old)\n", encoding="utf-8")
    backup_file.write_text("line 1\nline 2 (new)\nline 3 (added)\n", encoding="utf-8")

    diff_list = [
        {
            "category": "config",
            "rel_path": "host.txt",
            "host_path": str(host_file),
            "backup_path": str(backup_file),
            "status": "modified",
            "added": None,
            "removed": None,
            "diff_text": None,
        }
    ]

    class ModalTestApp(App):
        def compose(self) -> ComposeResult:
            yield Label("Main App Screen")

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = DiffViewerModal(diff_list)
        app.push_screen(modal)
        await pilot.pause()

        # 1. Açılışta henüz diff üretilmemiş olmalı (özet / karşılama mesajı olmalı)
        assert diff_list[0]["diff_text"] is None
        log_widget = modal.query_one("#diff-log", RichLog)
        assert log_widget is not None

        # 2. OptionList bileşeninin tek widget olarak mevcut olduğunu doğrula
        opt_list = modal.query_one("#diff-files-list", OptionList)
        assert opt_list is not None
        assert opt_list.option_count == 1

        # 3. Listeden dosyayı tıkla (offset ile tıklama)
        await pilot.click(OptionList, offset=(2, 1))
        await pilot.pause()

        # Worker'ın diff hesaplayıp log'a yazmasını bekle
        for _ in range(10):
            if diff_list[0]["diff_text"] is not None:
                break
            await pilot.pause(0.05)

        # 4. Diff metninin ve eklenen/silinen sayılarının yüklendiğini doğrula
        assert diff_list[0]["diff_text"] is not None
        assert diff_list[0]["added"] == 2
        assert diff_list[0]["removed"] == 1

        header_lbl = modal.query_one("#diff-file-header", Label)
        assert "(+2 / -1)" in str(header_lbl.render())
