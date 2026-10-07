"""读取已解密的微信 4.x 数据库（知识库）。

关键事实（已用真实 4.4.x 库验证）：
  * 每个会话一张表：Msg_<md5(username)>；表可能在 message_0..N.db 任意一个里。
  * 消息表 real_sender_id = 该库 Name2Id 表的 rowid（不是 contact.id）。
      - 自己的 rowid 通过 Name2Id.user_name == 自己的 wxid 定位。
      - 群消息正文形如 "wxid_xxx:\\n内容"（自己发的没有前缀）。
  * contact.db 的 contact 表提供 remark / nick_name。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from .models import Contact, Message

try:
    import zstandard as zstd
except Exception:  # pragma: no cover
    zstd = None

_GROUP_PREFIX = re.compile(r"^([^\s:\n]{4,64}):\n")
_SKIP_DB_PREFIX = ("message_fts", "message_resource", "media_", "weclaw")


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _open_ro(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("select 1").fetchone()
        return conn
    except sqlite3.Error:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn


class KBReader:
    """只读访问已解密目录。任何一步失败都降级为空结果，不抛给 UI。"""

    def __init__(self, decrypted_dir: str | Path, self_username: str = ""):
        self.root = Path(decrypted_dir).expanduser()
        if not self.root.exists():
            raise FileNotFoundError(f"解密目录不存在: {self.root}")
        self._self_username = self_username.strip()
        self._contacts: dict[str, Contact] = {}
        self._name2id_in_contact: dict[str, str] = {}     # 4.4 contact.name2id 的 rowid→username（备用）
        self._msg_dbs: list[Path] = []
        self._table_db: dict[str, Path] = {}              # Msg_xxx -> 所在 db
        self._columns: dict[str, set[str]] = {}
        self._name2id: dict[Path, dict[int, str]] = {}
        self._self_rowid: dict[Path, int | None] = {}
        self._zstd = zstd.ZstdDecompressor() if zstd else None
        self._load()

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        self._load_contacts()
        msg_dir = self.root / "message"
        if msg_dir.is_dir():
            self._msg_dbs = sorted(
                p for p in msg_dir.glob("*.db")
                if p.stat().st_size >= 4096 and not p.name.startswith(_SKIP_DB_PREFIX)
            )
        self._index_tables()
        if not self._self_username:
            self._self_username = self._detect_self_username()

    def _load_contacts(self) -> None:
        db = self.root / "contact" / "contact.db"
        if not db.exists():
            return
        try:
            conn = _open_ro(db)
        except sqlite3.Error:
            return
        try:
            tables = {r[0] for r in conn.execute(
                "select name from sqlite_master where type='table'")}
            for table, c_id, c_user, c_remark, c_nick in (
                ("contact", "id", "username", "remark", "nick_name"),
                ("contact", "id", "username", "remark", "nickname"),
                ("Friend", "rowid", "userName", "remark", "nickName"),
                ("WCContact", "rowid", "userName", "remark", "nickName"),
            ):
                if table not in tables:
                    continue
                try:
                    rows = conn.execute(
                        f"select {c_id},{c_user},{c_remark},{c_nick} from {table}").fetchall()
                except sqlite3.Error:
                    continue
                for row_id, username, remark, nickname in rows:
                    if not username:
                        continue
                    self._contacts[username] = Contact(
                        username=username, remark=remark or "", nickname=nickname or "",
                        is_group=username.endswith("@chatroom"), row_id=row_id or 0)
                if self._contacts:
                    break
            if "name2id" in tables:                       # 4.4：全量 username 列表
                try:
                    for row_id, username in conn.execute("select rowid,username from name2id"):
                        if username:
                            self._name2id_in_contact.setdefault(username, "")
                            self._contacts.setdefault(username, Contact(
                                username=username, remark="", nickname="",
                                is_group=username.endswith("@chatroom"), row_id=row_id or 0))
                except sqlite3.Error:
                    pass
        finally:
            conn.close()

    def _index_tables(self) -> None:
        for db in self._msg_dbs:
            try:
                conn = _open_ro(db)
            except sqlite3.Error:
                continue
            try:
                for (name,) in conn.execute(
                        "select name from sqlite_master where type='table' and name like 'Msg\\_%' escape '\\'"):
                    self._table_db.setdefault(name, db)
            except sqlite3.Error:
                pass
            finally:
                conn.close()

    def _db_for_table(self, table: str) -> Path | None:
        if table in self._table_db:
            return self._table_db[table]
        # 表可能在别的 db 里（4.x 消息分库）——懒加载扫一次
        for db in self._msg_dbs:
            try:
                conn = _open_ro(db)
                try:
                    hit = conn.execute(
                        "select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone()
                finally:
                    conn.close()
                if hit:
                    self._table_db[table] = db
                    return db
            except sqlite3.Error:
                continue
        return None

    def _cols(self, db: Path, table: str) -> set[str]:
        key = f"{db}::{table}"
        if key not in self._columns:
            try:
                conn = _open_ro(db)
                try:
                    self._columns[key] = {r[1] for r in conn.execute(f"pragma table_info({table})")}
                finally:
                    conn.close()
            except sqlite3.Error:
                self._columns[key] = set()
        return self._columns[key]

    # ------------------------------------------------------------------ #
    # Name2Id：rowid -> username（real_sender_id 的取值空间）
    # ------------------------------------------------------------------ #
    def _name2id_map(self, db: Path) -> dict[int, str]:
        if db not in self._name2id:
            out: dict[int, str] = {}
            try:
                conn = _open_ro(db)
                try:
                    col = "user_name"
                    cols = {r[1] for r in conn.execute("pragma table_info(Name2Id)")}
                    if cols and col not in cols:
                        col = "userName" if "userName" in cols else next(iter(cols))
                    for row_id, username in conn.execute(f"select rowid,{col} from Name2Id"):
                        out[int(row_id)] = username
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
            self._name2id[db] = out
        return self._name2id[db]

    def _self_rowid_in(self, db: Path) -> int | None:
        if db not in self._self_rowid:
            found = None
            if self._self_username:
                for row_id, username in self._name2id_map(db).items():
                    if username == self._self_username:
                        found = row_id
                        break
            self._self_rowid[db] = found
        return self._self_rowid[db]

    # ------------------------------------------------------------------ #
    # 联系人
    # ------------------------------------------------------------------ #
    def contacts(self, keyword: str = "", include_groups: bool = True) -> list[Contact]:
        kw = (keyword or "").lower()
        out = []
        for c in self._contacts.values():
            if not include_groups and c.is_group:
                continue
            if kw and not (kw in c.username.lower() or kw in c.remark.lower()
                           or kw in c.nickname.lower()):
                continue
            out.append(c)
        out.sort(key=lambda c: (c.is_group, c.display_name.lower()))
        return out

    def contact(self, username: str) -> Contact | None:
        c = self._contacts.get(username)
        if c:
            return c
        return Contact(username=username, is_group=username.endswith("@chatroom"))

    def resolve(self, name_or_id: str) -> Contact | None:
        """按 wxid / 备注名 / 昵称 / 群名查找（精确优先，其次唯一子串匹配）。"""
        key = (name_or_id or "").strip()
        if not key:
            return None
        if key in self._contacts:
            return self._contacts[key]
        low = key.lower()
        loose = [c for c in self._contacts.values()
                 if low in c.username.lower() or low in c.remark.lower() or low in c.nickname.lower()]
        for c in loose:
            if low in (c.remark.lower(), c.nickname.lower(), c.username.lower()):
                return c
        return loose[0] if len(loose) == 1 else (loose[0] if loose else None)

    def display_name(self, username: str) -> str:
        c = self._contacts.get(username)
        return c.display_name if c else username

    # ------------------------------------------------------------------ #
    # 自己
    # ------------------------------------------------------------------ #
    @property
    def self_username(self) -> str:
        return self._self_username

    def _detect_self_username(self) -> str:
        """账号目录名一般是 <wxid>_<后缀>；失败则用「出现在最多单聊里」的 sender 反查。"""
        head = self.root.parent.name or self.root.name
        m = re.match(r"^(wxid_[A-Za-z0-9]+)_", head)
        if m:
            return m.group(1)
        if head.startswith("wxid_"):
            return head
        seen: dict[int, int] = {}
        checked = 0
        for table, db in self._table_db.items():
            username = self._username_for_table(table, db)
            if not username or username.endswith(("@chatroom", "@openim")):
                continue
            checked += 1
            if checked > 60:
                break
            try:
                conn = _open_ro(db)
                try:
                    for (sid,) in conn.execute(f"select distinct real_sender_id from {table}"):
                        seen[int(sid or 0)] = seen.get(int(sid or 0), 0) + 1
                finally:
                    conn.close()
            except sqlite3.Error:
                continue
        if seen:
            best = max(seen.items(), key=lambda kv: kv[1])[0]
            return self._name2id_map(self._msg_dbs[0]).get(best, "") if self._msg_dbs else ""
        return ""

    def _username_for_table(self, table: str, db: Path) -> str:
        """Msg_<md5> -> username：反向查 Name2Id（md5 命中）。"""
        md5part = table[4:]
        for username in self._name2id_map(db).values():
            if md5_hex(username) == md5part:
                return username
        for username in self._contacts:
            if md5_hex(username) == md5part:
                return username
        return ""

    def table_for(self, username: str) -> str:
        return "Msg_" + md5_hex(username)

    # ------------------------------------------------------------------ #
    # 消息
    # ------------------------------------------------------------------ #
    def _content(self, row: sqlite3.Row | tuple, cols: set[str]) -> str:
        def g(name, default=None):
            try:
                return row[name]
            except (IndexError, KeyError, TypeError):
                return default

        raw = g("message_content")
        if raw is None:
            raw = g("compress_content")
        if raw is None:
            return ""
        if isinstance(raw, memoryview):
            raw = bytes(raw)
        if isinstance(raw, (bytes, bytearray)):
            flag = g("WCDB_CT_message_content") or 0
            data = bytes(raw)
            if flag == 4 and self._zstd is not None:
                try:
                    data = self._zstd.decompress(data)
                except Exception:
                    pass
            return data.decode("utf-8", errors="replace")
        return str(raw)

    _SELECT = ("local_id, server_id, local_type, real_sender_id, create_time, "
               "message_content, WCDB_CT_message_content, origin_source")

    def _rows_to_messages(self, rows, db: Path, chat_username: str) -> list[Message]:
        name2id = self._name2id_map(db)
        my_row = self._self_rowid_in(db)
        is_group = chat_username.endswith("@chatroom")
        out: list[Message] = []
        for row in rows:
            local_id = row["local_id"]
            local_type = row["local_type"] or 0
            real_sender = row["real_sender_id"]
            content = self._content(row, set())
            sender_row = None
            try:
                sender_row = int(real_sender) if real_sender is not None else None
            except (TypeError, ValueError):
                sender_row = None

            if my_row is not None and sender_row is not None:
                is_from_me = sender_row == my_row
            else:                        # 兜底：origin_source 1/3 视为自己
                is_from_me = (row["origin_source"] or 0) in (1, 3)

            sender_username = ""
            if is_group:
                m = _GROUP_PREFIX.match(content or "")
                if m and m.group(1) in name2id.values():
                    sender_username = m.group(1)
                    content = content[len(m.group(1)) + 2:]
                    is_from_me = False
            if not sender_username and sender_row is not None:
                sender_username = name2id.get(sender_row, "") or self._self_username
            if local_type & 0xFFFF in (10000, 10002):
                sender_username = "[系统消息]"
                is_from_me = False

            sender_name = ""
            if is_from_me:
                sender_name = "我"
            elif sender_username:
                sender_name = self.display_name(sender_username)

            out.append(Message(
                local_id=local_id or 0,
                create_time=row["create_time"] or 0,
                local_type=local_type,
                content=content,
                is_from_me=is_from_me,
                sender_id=sender_username,
                sender_name=sender_name,
                chat_username=chat_username,
                server_id=row["server_id"] or 0,
            ))
        return out

    def _query(self, chat_username: str, where: str = "", params: tuple = (),
               order: str = "asc", limit: int = 20) -> list[Message]:
        table = self.table_for(chat_username)
        db = self._db_for_table(table)
        if db is None:
            return []
        cols = self._cols(db, table)
        select = self._SELECT if {"message_content"} <= cols else "*"
        sql = (f"select {select} from {table} "
               f"{('where ' + where) if where else ''} order by create_time {order}, local_id {order} limit ?")
        try:
            conn = _open_ro(db)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, tuple(params) + (int(limit),)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        msgs = self._rows_to_messages(rows, db, chat_username)
        if order == "desc":
            msgs.reverse()
        return msgs

    def messages(self, chat_username: str, limit: int = 20, before_ts: int | None = None,
                 text_only: bool = False) -> list[Message]:
        """取最近 limit 条（升序返回）。"""
        where, params = [], []
        if before_ts:
            where.append("create_time <= ?")
            params.append(int(before_ts))
        if text_only:
            where.append("(local_type & 65535) = 1")
        msgs = self._query(chat_username, " and ".join(where), tuple(params), "desc", limit)
        return msgs

    def new_messages(self, chat_username: str, since_ts: int, limit: int = 50) -> list[Message]:
        """create_time > since_ts（升序）。"""
        return self._query(chat_username, "create_time > ?", (int(since_ts),), "asc", limit)

    def latest_ts(self, chat_username: str) -> int:
        for db in [self._db_for_table(self.table_for(chat_username))]:
            if db is None:
                return 0
            table = self.table_for(chat_username)
            try:
                conn = _open_ro(db)
                try:
                    row = conn.execute(f"select max(create_time) from {table}").fetchone()
                    return int(row[0] or 0)
                finally:
                    conn.close()
            except sqlite3.Error:
                return 0
        return 0

    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        return {
            "root": str(self.root),
            "contacts": len(self._contacts),
            "chats": len(self._table_db),
            "msg_dbs": [p.name for p in self._msg_dbs],
            "self_username": self._self_username,
        }
