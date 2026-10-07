"""生成一份「微信 4.x 已解密库」形状的演示数据（全部为虚构内容）。

    python tools/make_demo_kb.py /tmp/wxreply_demo

结构（与真实解密目录一致，可被 KBReader 直接读取）：
    contact/contact.db   -- contact 表 + name2id 表
    session/session.db   -- SessionTable
    message/message_0.db -- Name2Id(rowid->username) + Msg_<md5(username)> 表
"""
from __future__ import annotations

import hashlib
import sqlite3
import struct
import sys
import time
from pathlib import Path

ME = "wxid_demo_me"
ZHANG = "wxid_demo_zhang"
LI = "wxid_demo_li"
GROUP = "12345678901@chatroom"
GROUP_NAME = "大学同学群"

CONTACTS = [
    (ME, "", "我"),
    (ZHANG, "张三", "张三"),
    (LI, "", "李四"),
    (GROUP, "", GROUP_NAME),
]


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def make_empty_reserve_db(path: Path, reserve: int = 80, page_size: int = 4096) -> None:
    """先写好一个「每页保留 80 字节」的空 SQLite 头，再让 sqlite3 往里建表。

    真实的微信解密库就是这样（页头第 21 个字节 = 80）：这样演示库才和线上库同形，
    才能用来验证「加密 → 解密」的无损往返。
    """
    p = bytearray(page_size)
    p[0:16] = b"SQLite format 3\x00"
    p[16:18] = struct.pack(">H", page_size)
    p[18] = 1
    p[19] = 1
    p[20] = reserve
    p[21] = 64
    p[22] = 32
    p[23] = 32
    p[24:28] = struct.pack(">I", 1)
    p[28:32] = struct.pack(">I", 1)
    p[40:44] = struct.pack(">I", 1)
    p[44:48] = struct.pack(">I", 4)
    p[56:60] = struct.pack(">I", 1)
    p[92:96] = struct.pack(">I", 1)
    p[96:100] = struct.pack(">I", 3045000)
    p[100] = 0x0D
    p[105:107] = struct.pack(">H", page_size - reserve)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(p))


def _connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        make_empty_reserve_db(path)
    conn = sqlite3.connect(path)
    # 真实微信 4.x 库是 WAL 模式（文件头 18/19 字节 = 2），这里保持一致
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _checkpoint(conn: sqlite3.Connection) -> None:
    """把已提交数据落回主库文件，并清掉 -wal/-shm（demo/测试不需要 WAL 残留）。"""
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.Error:
        pass


def _msg_schema(table: str) -> str:
    return (f"CREATE TABLE {table}(local_id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER, "
            "local_type INTEGER, sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, "
            "status INTEGER, upload_status INTEGER, download_status INTEGER, server_seq INTEGER, "
            "origin_source INTEGER, source TEXT, message_content TEXT, compress_content TEXT, "
            "packed_info_data BLOB, WCDB_CT_message_content INTEGER DEFAULT NULL, "
            "WCDB_CT_source INTEGER DEFAULT NULL)")


def build(root: str | Path, days_ago: float = 0.0) -> Path:
    """days_ago: 把「最后一条消息」放到多少天前，用来演示"只提醒新消息"。"""
    root = Path(root)
    for sub in ("contact", "session", "message"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    now = int(time.time() - days_ago * 86400)

    # ---------------- contact.db ---------------- #
    conn = _connect(root / "contact" / "contact.db")
    conn.execute("CREATE TABLE contact(id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, "
                 "alias TEXT, encrypt_username TEXT, flag INTEGER, delete_flag INTEGER, "
                 "verify_flag INTEGER, remark TEXT, remark_quan_pin TEXT, remark_pin_yin_initial TEXT,"
                 " nick_name TEXT, pin_yin_initial TEXT, quan_pin TEXT, big_head_url TEXT,"
                 " small_head_url TEXT, head_img_md5 TEXT, chat_room_notify INTEGER,"
                 " is_in_chat_room INTEGER, description TEXT, extra_buffer BLOB, chat_room_type INTEGER)")
    conn.execute("CREATE TABLE name2id(username TEXT PRIMARY KEY)")
    for i, (username, remark, nick) in enumerate(CONTACTS, start=1):
        conn.execute("INSERT INTO contact(id,username,local_type,remark,nick_name) VALUES(?,?,?,?,?)",
                     (i, username, 1, remark, nick))
        conn.execute("INSERT INTO name2id(username) VALUES(?)", (username,))
    conn.commit()
    _checkpoint(conn)
    conn.close()

    # ---------------- session.db ---------------- #
    conn = _connect(root / "session" / "session.db")
    conn.execute("CREATE TABLE SessionTable(username TEXT PRIMARY KEY, type INTEGER, unread_count INTEGER,"
                 " summary TEXT, last_timestamp INTEGER, sort_timestamp INTEGER, last_msg_type INTEGER,"
                 " last_msg_sender TEXT, last_sender_display_name TEXT)")
    conn.commit()
    _checkpoint(conn)
    conn.close()

    # ---------------- message_0.db ---------------- #
    conn = _connect(root / "message" / "message_0.db")
    conn.execute("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY, is_session INTEGER)")
    ids: dict[str, int] = {}
    for username, _, _ in CONTACTS:
        cur = conn.execute("INSERT INTO Name2Id(user_name,is_session) VALUES(?,1)", (username,))
        ids[username] = int(cur.lastrowid)
    conn.execute("CREATE TABLE TimeStamp(timestamp INTEGER)")

    script: dict[str, list[tuple[str, str, int]]] = {
        # (sender_username, text, seconds_ago)
        ZHANG: [
            (ME, "张三，明天下午的会你去吗", 3 * 86400),
            (ZHANG, "去啊，我一点半到", 3 * 86400 - 120),
            (ME, "好，那我带电脑，你带转接头", 3 * 86400 - 240),
            (ZHANG, "行，没问题", 3 * 86400 - 300),
            (ME, "对了我把上次的文档发你了", 2 * 86400),
            (ZHANG, "收到，我看看", 2 * 86400 - 60),
            (ME, "有问题随时说，我这两天都在", 2 * 86400 - 120),
            (ZHANG, "嗯嗯，谢啦", 2 * 86400 - 180),
            (ZHANG, "对了，你上次说的那个报名的材料，我先帮你交上去还是等你确认一下？我怕截止了", 120),
        ],
        LI: [
            (LI, "在吗，想问你个事", 5 * 86400),
            (ME, "在的，你说", 5 * 86400 - 30),
            (LI, "上次那个比赛你还参加吗", 5 * 86400 - 60),
            (ME, "可能不参加了，时间有点紧", 5 * 86400 - 90),
            (LI, "好吧，那我拉别人组队", 5 * 86400 - 120),
            (ME, "好，祝你们拿奖", 5 * 86400 - 150),
        ],
        GROUP: [
            (ZHANG, "周五晚上聚一下？", 6 * 86400),
            (LI, "我可以，地点定了吗", 6 * 86400 - 60),
            (ME, "我都行，看你们", 6 * 86400 - 120),
            (ZHANG, "那就老地方，七点", 6 * 86400 - 180),
        ],
    }

    session_rows: list[tuple[str, int]] = []
    for chat, rows in script.items():
        table = "Msg_" + md5(chat)
        conn.execute(_msg_schema(table))
        last_ts = 0
        for sender, text, seconds_ago in rows:
            ts = now - seconds_ago
            # 群聊里别人发的内容带 "wxid:\n" 前缀；自己发的不带
            content = f"{sender}:\n{text}" if (chat == GROUP and sender != ME) else text
            conn.execute(
                f"INSERT INTO {table}(server_id,local_type,sort_seq,real_sender_id,create_time,status,"
                "origin_source,message_content) VALUES(?,?,?,?,?,?,?,?)",
                (1000 + ts % 1000, 1, ts, ids[sender], ts,
                 2 if sender == ME else 3, 1 if sender == ME else 2, content))
            last_ts = max(last_ts, ts)
        session_rows.append((chat, last_ts))

    conn.commit()
    _checkpoint(conn)
    conn.close()

    conn = _connect(root / "session" / "session.db")
    for chat, ts in session_rows:
        conn.execute("INSERT INTO SessionTable(username,type,unread_count,summary,last_timestamp,"
                     "sort_timestamp,last_msg_type) VALUES(?,?,?,?,?,?,?)",
                     (chat, 1 if chat.endswith("@chatroom") else 0, 0, "演示会话", ts, ts, 1))
    conn.commit()
    _checkpoint(conn)
    conn.close()
    return root


def inject(root: str | Path, chat: str, text: str, sender: str | None = None,
           when: int | None = None, is_from_me: bool = False) -> int:
    """往演示库里塞一条新消息（模拟"对方刚发来"）。返回 create_time。"""
    root = Path(root)
    conn = sqlite3.connect(root / "message" / "message_0.db")
    conn.execute("PRAGMA journal_mode=WAL")
    table = "Msg_" + md5(chat)
    ids = {r[0]: r[1] for r in conn.execute("select user_name, rowid from Name2Id")}
    sender = sender or (ME if is_from_me else chat)
    ts = when or int(time.time())
    content = text
    if chat.endswith("@chatroom") and not is_from_me:
        content = f"{sender}:\n{text}"
    conn.execute(f"INSERT INTO {table}(server_id,local_type,sort_seq,real_sender_id,create_time,status,"
                 "origin_source,message_content) VALUES(?,?,?,?,?,?,?,?)",
                 (999, 1, ts, ids[sender], ts, 2 if is_from_me else 3,
                  1 if is_from_me else 2, content))
    conn.commit()
    _checkpoint(conn)
    conn.close()
    return ts


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/wxreply_demo")
    build(out)
    print(f"演示知识库已生成：{out}")
    print("  联系人 1 = 张三（wxid_demo_zhang）")
    print("  联系人 2 = 李四（wxid_demo_li）")
    print(f"  群      = {GROUP_NAME}（{GROUP}）")
    print("  最后一条对方消息：张三 关于报名材料（用来测试「只提醒新消息」）")
