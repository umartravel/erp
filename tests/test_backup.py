"""
Integration test: CLI backup + restore.

Isolasi PENUH -- semua path (DB, uploads, backup root) di-monkeypatch ke
tmp_path pytest. Production DB dan uploads_private/ TIDAK pernah disentuh.

Skenario:
- create -> hasilkan folder dgn umar_crm.db + uploads_private.zip + manifest.
- verify -> checksum manifest match dgn file aktual.
- verify -> corrupt file -> raise SystemExit (safety).
- restore -> replace DB+uploads, sekaligus bikin pre-restore-* utk rollback.
"""
import json
import sqlite3

import pytest

import backup


@pytest.fixture
def isolated_backup(tmp_path, monkeypatch):
    """Redirect semua path modul backup ke tmp_path pytest -- production
    DB/uploads/backup dir TIDAK pernah tersentuh."""
    db_file = tmp_path / "umar_crm.db"
    uploads_dir = tmp_path / "uploads_private"
    backup_root = tmp_path / ".backup"
    uploads_dir.mkdir()

    # Bikin DB dummy dgn 1 tabel supaya VACUUM INTO tidak protes empty.
    con = sqlite3.connect(str(db_file))
    con.executescript(
        "CREATE TABLE dummy (id INTEGER PRIMARY KEY, val TEXT);"
        "INSERT INTO dummy (val) VALUES ('hello');"
    )
    con.commit()
    con.close()

    # Bikin 2 file dummy di uploads utk cek zip round-trip.
    (uploads_dir / "ktp_001.jpg").write_bytes(b"fake-ktp-bytes-1234")
    (uploads_dir / "sub").mkdir()
    (uploads_dir / "sub" / "kk_002.pdf").write_bytes(b"fake-kk-pdf-5678")

    monkeypatch.setattr(backup, "DB_FILE", db_file)
    monkeypatch.setattr(backup, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(backup, "BACKUP_ROOT", backup_root)

    return {"db": db_file, "uploads": uploads_dir, "root": backup_root}


def test_create_produces_valid_backup(isolated_backup):
    out = backup.cmd_create()
    assert out.is_dir()
    mf = json.loads((out / backup.MANIFEST_NAME).read_text(encoding="utf-8"))

    # File hadir + ukuran match manifest.
    db_bak = out / backup.DB_BACKUP_NAME
    zip_bak = out / backup.UPLOADS_BACKUP_NAME
    assert db_bak.stat().st_size == mf["db_bytes"]
    assert zip_bak.stat().st_size == mf["uploads_bytes"]

    # Ada 2 file di uploads dummy -> manifest ikut catat.
    assert mf["uploads_file_count"] == 2
    # SHA256 valid (64 hex chars).
    assert len(mf["db_sha256"]) == 64
    assert len(mf["uploads_sha256"]) == 64


def test_verify_passes_for_clean_backup(isolated_backup):
    out = backup.cmd_create()
    assert backup.cmd_verify(out.name) is True


def test_verify_fails_on_corruption(isolated_backup):
    out = backup.cmd_create()
    # Rusak DB file supaya sha256 mismatch.
    db_bak = out / backup.DB_BACKUP_NAME
    db_bak.write_bytes(b"corrupted")
    with pytest.raises(SystemExit):
        backup.cmd_verify(out.name)


def test_restore_replaces_state_and_saves_safety(isolated_backup):
    # Bikin backup dari state awal.
    out = backup.cmd_create()

    # Modifikasi state produksi setelah backup: tambah row + file baru.
    con = sqlite3.connect(str(isolated_backup["db"]))
    con.execute("INSERT INTO dummy (val) VALUES ('after-backup')")
    con.commit()
    con.close()
    (isolated_backup["uploads"] / "extra.txt").write_bytes(b"extra")

    # Restore -> harus balik ke state sebelum modifikasi + bikin safety dir.
    backup.cmd_restore(out.name)

    con = sqlite3.connect(str(isolated_backup["db"]))
    rows = con.execute("SELECT val FROM dummy ORDER BY id").fetchall()
    con.close()
    assert rows == [("hello",)], f"restore tidak revert DB: {rows}"

    # File 'extra.txt' tambahan setelah backup TIDAK boleh ada lagi.
    assert not (isolated_backup["uploads"] / "extra.txt").exists()
    # File asli dari backup HARUS ada.
    assert (isolated_backup["uploads"] / "ktp_001.jpg").exists()
    assert (isolated_backup["uploads"] / "sub" / "kk_002.pdf").exists()

    # Safety net: harus ada folder pre-restore_* yg simpan state pre-restore.
    safeties = [p for p in isolated_backup["root"].iterdir()
                if p.is_dir() and p.name.startswith("pre-restore_")]
    assert len(safeties) == 1, f"pre-restore backup hilang: {safeties}"
    # Safety DB harusnya masih punya row 'after-backup' (state sebelum restore).
    con = sqlite3.connect(str(safeties[0] / backup.DB_BACKUP_NAME))
    saved = con.execute("SELECT val FROM dummy ORDER BY id").fetchall()
    con.close()
    assert ("after-backup",) in saved, f"safety net tidak capture state: {saved}"
