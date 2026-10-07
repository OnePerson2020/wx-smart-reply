"""解密镜像：整库解密、增量刷新、WAL 尽力而为。"""
import json
import os
import shutil
import sqlite3
from pathlib import Path

from sqlcipher_stub import PAGE, encrypt_db, encrypt_wal

from make_demo_kb import ZHANG, build, inject
from wxreply.kb.crypto import DecryptedMirror, decrypt_file, decrypt_wal, load_keys

KEY1 = bytes(range(32))
KEY2 = bytes(range(32, 64))


def _make_encrypted(tmp_path: Path):
    """把演示库加密成 enc/，并写 keys.json。"""
    plain = tmp_path / "plain"
    build(plain)
    enc = tmp_path / "enc"
    (enc / "message").mkdir(parents=True)
    (enc / "contact").mkdir(parents=True)
    (enc / "session").mkdir(parents=True)
    keys = {}
    for rel, key in (("contact/contact.db", KEY1), ("session/session.db", KEY1),
                     ("message/message_0.db", KEY2)):
        encrypt_db(plain / rel, enc / rel, key, salt=os.urandom(16))
        keys[rel] = {"enc_key": key.hex()}
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps(keys), encoding="utf-8")
    return plain, enc, keys_file


def test_load_keys_supports_both_formats(tmp_path: Path):
    f1 = tmp_path / "a.json"
    f1.write_text(json.dumps({"message/message_0.db": {"enc_key": KEY2.hex()}}))
    assert load_keys(f1)["message/message_0.db"] == KEY2

    f2 = tmp_path / "b.json"
    f2.write_text(json.dumps({"message/message_0.db": f"x'{KEY2.hex()}{'ab' * 16}'"}))
    assert load_keys(f2)["message/message_0.db"] == KEY2


def test_full_decrypt_and_incremental(tmp_path: Path):
    plain, enc, keys_file = _make_encrypted(tmp_path)
    out = tmp_path / "dec"
    mirror = DecryptedMirror(enc, out, keys_file)

    res = mirror.sync()
    assert len(res.updated) == 3, res.errors
    assert not res.errors

    conn = sqlite3.connect(out / "message" / "message_0.db")
    rows = conn.execute("select message_content from Msg_" + __import__("hashlib").md5(
        ZHANG.encode()).hexdigest()).fetchall()
    conn.close()
    assert any("报名的材料" in r[0] for r in rows)

    # 没变化就不重复解密
    assert mirror.sync().updated == []

    # 模拟微信写了新消息（重新加密覆盖源文件）
    inject(plain, ZHANG, "刚看到你消息，我这就确认一下")
    encrypt_db(plain / "message/message_0.db", enc / "message/message_0.db", KEY2, os.urandom(16))
    os.utime(enc / "message/message_0.db", (os.path.getmtime(enc / "message/message_0.db") + 5,) * 2)
    res2 = mirror.sync()
    assert res2.updated == ["message/message_0.db"]

    conn = sqlite3.connect(out / "message" / "message_0.db")
    texts = [r[0] for r in conn.execute(
        "select message_content from Msg_" + __import__("hashlib").md5(ZHANG.encode()).hexdigest())]
    conn.close()
    assert any("我这就确认一下" in t for t in texts)


def test_wrong_key_reports_error_and_writes_nothing(tmp_path: Path):
    _, enc, keys_file = _make_encrypted(tmp_path)
    bad = json.loads(Path(keys_file).read_text())
    bad["message/message_0.db"] = {"enc_key": (b"\x99" * 32).hex()}
    Path(keys_file).write_text(json.dumps(bad))

    out = tmp_path / "dec_bad"
    res = DecryptedMirror(enc, out, keys_file).sync()
    assert any("message/message_0.db" in e for e in res.errors)
    assert not (out / "message/message_0.db").exists()


def test_wal_frames_are_replayed(tmp_path: Path):
    """用真实 SQLite 写出的 -wal 做样本：只把页体换成密文，帧头/校验和保持原样。

    解密后 sqlite 必须能正常重放 WAL（看到 WAL 里的新消息）——这才是对 WAL 解密路径的真验证。
    """
    import hashlib
    import sqlite3
    import time

    from sqlcipher_stub import encrypt_page

    kb = tmp_path / "kb"
    build(kb)
    db = kb / "message" / "message_0.db"
    table = "Msg_" + hashlib.md5(ZHANG.encode()).hexdigest()

    # 1) 主库快照 = 当前状态（checkpoint 过）
    main_plain = tmp_path / "v1.db"
    
    shutil.copy(db, main_plain)

    # 2) 用 sqlite 自己产生一个未 checkpoint 的 WAL
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    sender_id = conn.execute("select rowid from Name2Id where user_name=?", (ZHANG,)).fetchone()[0]
    ts = int(time.time())
    conn.execute(
        f"insert into {table}(server_id,local_type,sort_seq,real_sender_id,create_time,status,"
        "origin_source,message_content) values(?,?,?,?,?,?,?,?)",
        (1, 1, ts, sender_id, ts, 3, 2, "WAL 里的这条新消息"))
    conn.commit()
    real_wal = Path(str(db) + "-wal")
    assert real_wal.exists() and real_wal.stat().st_size > 32
    raw = real_wal.read_bytes()          # sqlite 关闭时会 checkpoint 并删掉 -wal，必须先取走
    conn.close()

    # 3) 加密主库 + 加密（保留帧头的）WAL
    key = KEY2
    enc_main = tmp_path / "m.db"
    enc_wal = tmp_path / "m.db-wal"
    encrypt_db(main_plain, enc_main, key, os.urandom(16))
    page_size = int.from_bytes(raw[8:12], "big") or PAGE
    frame_size = 24 + page_size
    out = bytearray(raw[:32])
    frames = 0
    for i in range((len(raw) - 32) // frame_size):
        off = 32 + i * frame_size
        hdr = raw[off:off + 24]
        page = raw[off + 24:off + frame_size]
        pgno = int.from_bytes(hdr[0:4], "big")
        out += hdr + encrypt_page(page, key, pgno if pgno else 1)
        frames += 1
    enc_wal.write_bytes(bytes(out))
    assert frames >= 1

    # 4) 解密后交给 sqlite：WAL 必须被重放
    out_main = tmp_path / "out.db"
    out_wal = tmp_path / "out.db-wal"
    assert decrypt_file(enc_main, out_main, key)
    assert decrypt_wal(enc_wal, out_wal, key)

    conn = sqlite3.connect(f"file:{out_main}?mode=ro", uri=True)
    texts = [r[0] for r in conn.execute(f"select message_content from {table}")]
    conn.close()
    assert any("WAL 里的这条新消息" in t for t in texts), texts


def test_wal_garbage_is_rejected(tmp_path: Path):
    """WAL 布局不符（页类型不像 btree 页）时必须返回 False，不能把脏数据喂给 sqlite。"""
    enc_main = tmp_path / "m.db"
    v1 = tmp_path / "v1"
    build(v1)
    encrypt_db(v1 / "message/message_0.db", enc_main, KEY2, os.urandom(16))

    bogus = tmp_path / "m.db-wal"
    encrypt_wal([(1, os.urandom(PAGE))], v1 / "message/message_0.db",
                v1 / "message/message_0.db", bogus, KEY1)   # 错误密钥 = 解出来是噪声
    out_wal = tmp_path / "out.db-wal"
    assert decrypt_wal(bogus, out_wal, KEY2) is False
    assert not out_wal.exists()


def test_only_relevant_dbs_are_decrypted(tmp_path: Path):
    """message/ 下还有 media_*.db / biz_message_*.db / *_fts.db，不该去解密它们。"""
    from wxreply.kb.crypto import DecryptedMirror, is_wanted

    assert is_wanted("contact", "contact.db")
    assert not is_wanted("contact", "contact_fts.db")
    assert is_wanted("session", "session.db")
    assert is_wanted("message", "message_0.db")
    assert is_wanted("message", "message_12.db")
    for name in ("media_0.db", "biz_message_0.db", "message_fts.db", "message_resource.db",
                 "weclaw.db", "message_0.kvdb"):
        assert not is_wanted("message", name), name

    plain, enc, keys_file = _make_encrypted(tmp_path)
    for junk in ("message/media_0.db", "message/biz_message_0.db", "message/message_fts.db",
                 "contact/contact_fts.db"):
        (enc / junk).write_bytes(b"not really encrypted")
    out = tmp_path / "dec2"
    res = DecryptedMirror(enc, out, keys_file).sync()
    assert sorted(res.updated) == ["contact/contact.db", "message/message_0.db", "session/session.db"]
    assert not (out / "message/media_0.db").exists()
    assert not res.errors


def test_src_root_can_be_xwechat_files_root(tmp_path: Path):
    """传 xwechat_files 根目录（而不是 db_storage）时自动找到账号目录。"""
    from make_demo_kb import build

    whole = tmp_path / "xwechat_files"
    account = whole / "wxid_demo_me_abcd1234" / "db_storage"
    account.parent.mkdir(parents=True)
    build(account)

    from wxreply.kb.crypto import _resolve_src_root
    assert _resolve_src_root(whole) == account
    assert _resolve_src_root(account) == account
    assert _resolve_src_root(account.parent) == account
