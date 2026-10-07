"""已解密/加密数据库的处理。

- 加密格式：微信 4.x 使用 SQLCipher 4（WCDB 封装）：页 4096，保留区 80 = IV(16) + HMAC(64)，
  AES-256-CBC，raw-key 模式（enc_key 直接当 AES key 用）。
- 已用真实 4.4.x 库验证：页 1 = salt(16) + enc(4000) + iv + hmac；页 N = enc(4016) + iv + hmac。
- WAL 帧与普通页同构（页 1 同样是 salt+enc(4000)），因此可复用同一解密函数。

不支持 HMAC 校验密钥正确性之外的作用；解密后的文件交给 sqlite 读取。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

try:  # 仅解密模式需要；dir 模式可以没有 pycryptodome
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover
    AES = None  # type: ignore

PAGE_SIZE = 4096
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = IV_SIZE + HMAC_SIZE          # 80
SQLITE_HDR = b"SQLite format 3\x00"
WAL_HEADER_SIZE = 32
WAL_FRAME_HEADER_SIZE = 24

# 只解密这三类库，media/favorite/biz 等与本应用无关
DEFAULT_GROUPS = ("contact", "session", "message")

# 每个目录里实际要解密的文件名（其余如 media_*.db、biz_message_*.db、*_fts.db 都不需要）
_WANTED: dict[str, tuple[str, ...]] = {
    "contact": ("contact.db",),
    "session": ("session.db",),
    "message": ("message_*.db",),
}
_SKIP_IN_MESSAGE = ("message_fts", "message_resource", "weclaw")


def is_wanted(group: str, name: str) -> bool:
    patterns = _WANTED.get(group)
    if not patterns:
        return False
    if group == "message":
        if any(name.startswith(p) for p in _SKIP_IN_MESSAGE):
            return False
        return bool(re.fullmatch(r"message_\d+\.db", name))
    return name in patterns


# --------------------------------------------------------------------------- #
# keys.json 读取（兼容两种格式）
# --------------------------------------------------------------------------- #
def load_keys(path: str | Path) -> dict[str, bytes]:
    """返回 {相对路径(posix): 32 字节 AES key}。

    支持：
      {"message/message_0.db": {"enc_key": "<64hex>"}}         —— 只记密钥
      {"message/message_0.db": "x'<64hex_key><32hex_salt>'"}   —— 密钥 + salt
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    keys: dict[str, bytes] = {}
    for rel, val in raw.items():
        if isinstance(val, dict):
            hex_key = val.get("enc_key") or val.get("key") or ""
        else:
            m = re.search(r"x'([0-9a-fA-F]+)'", str(val))
            hex_key = m.group(1) if m else str(val)
        hex_key = re.sub(r"[^0-9a-fA-F]", "", hex_key)
        if len(hex_key) >= 64:
            keys[rel.lstrip("./")] = bytes.fromhex(hex_key[:64])
    return keys


def resolve_key(db_path: Path, keys: dict[str, bytes], rel: str = "") -> bytes | None:
    """优先按相对路径取 key（key 与文件一一对应，最可靠），再退回同名匹配。"""
    db_path = Path(db_path)
    rel = rel or db_path.name
    candidates = [rel.replace("\\", "/"), db_path.name, str(db_path).replace("\\", "/")]
    for c in candidates:
        if c in keys:
            return keys[c]
        for k, v in keys.items():                       # 后缀匹配：允许 keys.json 只写后缀
            if k.endswith("/" + c) or c.endswith("/" + k):
                return v
    return None


# --------------------------------------------------------------------------- #
# 单页解密
# --------------------------------------------------------------------------- #
def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if AES is None:  # pragma: no cover
        raise RuntimeError("缺少 pycryptodome，无法解密：pip install pycryptodome")
    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)


def decrypt_page(page: bytes, key: bytes, pgno: int) -> bytes:
    """解密一个 4096 字节页（pgno 从 1 开始）。页 1 需要补回 SQLite 头。"""
    if len(page) < PAGE_SIZE:
        return page
    if pgno == 1:
        iv = page[PAGE_SIZE - RESERVE_SIZE: PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
        body = _aes_cbc_decrypt(key, iv, page[SALT_SIZE: PAGE_SIZE - RESERVE_SIZE])
        return SQLITE_HDR + body + b"\x00" * RESERVE_SIZE
    iv = page[PAGE_SIZE - RESERVE_SIZE: PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    body = _aes_cbc_decrypt(key, iv, page[: PAGE_SIZE - RESERVE_SIZE])
    return body + b"\x00" * RESERVE_SIZE


def _page_type_ok(page: bytes, pgno: int = 0) -> bool:
    """解密结果看起来是否合理。页 1 会补回文件头，所以还要看头里的字段。"""
    if len(page) < PAGE_SIZE:
        return False
    if pgno == 1:
        return (page[:16] == SQLITE_HDR
                and int.from_bytes(page[16:18], "big") == PAGE_SIZE
                and page[18] in (1, 2) and page[19] in (1, 2))
    return page[0] in (2, 5, 10, 13)


# --------------------------------------------------------------------------- #
# 整库 / WAL 解密
# --------------------------------------------------------------------------- #
def decrypt_file(src: str | Path, dst: str | Path, key: bytes) -> bool:
    src, dst = Path(src), Path(dst)
    data = src.read_bytes()
    if len(data) < PAGE_SIZE:
        return False
    if data.startswith(SQLITE_HDR):                     # 已经是明文
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return True
    first = decrypt_page(data[:PAGE_SIZE], key, 1)
    if not _page_type_ok(first, 1):                     # 密钥/格式不对，不要写出垃圾文件
        return False
    total = len(data) // PAGE_SIZE
    out = bytearray(first)
    for i in range(1, total):
        out += decrypt_page(data[i * PAGE_SIZE:(i + 1) * PAGE_SIZE], key, i + 1)
    out += data[total * PAGE_SIZE:]                     # 尾部不足一页原样保留
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(out))
    return _db_ok(dst)


def decrypt_wal(src_wal: str | Path, dst_wal: str | Path, key: bytes) -> bool:
    """解密 -wal（尽力而为）。返回 False 表示没有可用帧或校验不通过，调用方应删掉输出。"""
    src_wal, dst_wal = Path(src_wal), Path(dst_wal)
    if not src_wal.exists():
        dst_wal.unlink(missing_ok=True)
        return False
    raw = src_wal.read_bytes()
    if len(raw) < WAL_HEADER_SIZE + WAL_FRAME_HEADER_SIZE + PAGE_SIZE:
        dst_wal.unlink(missing_ok=True)
        return False
    page_size = int.from_bytes(raw[8:12], "big") or PAGE_SIZE
    if page_size != PAGE_SIZE:
        dst_wal.unlink(missing_ok=True)
        return False
    frame_size = WAL_FRAME_HEADER_SIZE + page_size
    out = bytearray(raw[:WAL_HEADER_SIZE])
    frames = 0
    for i in range((len(raw) - WAL_HEADER_SIZE) // frame_size):
        off = WAL_HEADER_SIZE + i * frame_size
        hdr = raw[off:off + WAL_FRAME_HEADER_SIZE]
        page = raw[off + WAL_FRAME_HEADER_SIZE: off + frame_size]
        pgno = int.from_bytes(hdr[0:4], "big")
        if pgno == 0:
            out += hdr + page
            continue
        try:
            dec = decrypt_page(page, key, pgno)
        except Exception:
            dst_wal.unlink(missing_ok=True)
            return False
        if not _page_type_ok(dec, pgno):     # 布局不符就别喂给 sqlite
            dst_wal.unlink(missing_ok=True)
            return False
        out += hdr + dec
        frames += 1
    if not frames:
        dst_wal.unlink(missing_ok=True)
        return False
    dst_wal.write_bytes(bytes(out))
    return True


# --------------------------------------------------------------------------- #
# 密钥校验（拿到 keys.json 后先跑这个，再谈解密）
# --------------------------------------------------------------------------- #
def verify_key_hmac(db_path: str | Path, key: bytes) -> bool:
    """用页 1 的 HMAC-SHA512 强校验密钥对不对（SQLCipher raw-key 模式）：

        mac_key = PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3a, 2 轮, 32 字节)
        HMAC-SHA512(mac_key, 页1[16:4032] + 小端页码1) == 页1[4032:4096]

    这是判定密钥有效的决定性证据。测试用的假库没有真 HMAC，会返回 False。
    """
    import hmac as _hmac
    import struct as _struct

    try:
        with open(db_path, "rb") as f:
            page = f.read(PAGE_SIZE)
    except OSError:
        return False
    if len(page) < PAGE_SIZE or AES is None:
        return False
    salt = page[:SALT_SIZE]
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    h = _hmac.new(mac_key, page[SALT_SIZE: PAGE_SIZE - RESERVE_SIZE + IV_SIZE], hashlib.sha512)
    h.update(_struct.pack("<I", 1))
    return h.digest() == page[PAGE_SIZE - HMAC_SIZE: PAGE_SIZE]


@dataclass
class KeyStatus:
    rel: str
    size_mb: float
    has_key: bool
    hmac_ok: bool
    structural_ok: bool

    @property
    def status(self) -> str:
        if not self.has_key:
            return "missing"
        if self.hmac_ok:
            return "ok"
        return "ok-structural" if self.structural_ok else "fail"

    @property
    def hint(self) -> str:
        return {
            "ok": "密钥正确（HMAC 强校验通过）",
            "ok-structural": "能解出合法页结构，但没有可校验的 HMAC（测试库/非常规库）",
            "fail": "密钥与这个库不匹配：可能取的是另一台机器/另一批库的密钥，或微信升级后换了密钥",
            "missing": "keys.json 里没有这个库的条目（检查相对路径写法）",
        }[self.status]


def check_keys(src_root: str | Path, keys_file: str | Path,
               groups: tuple[str, ...] = DEFAULT_GROUPS) -> list[KeyStatus]:
    """逐个库校验密钥，返回状态列表（不解密、不写盘）。"""
    root = _resolve_src_root(Path(src_root).expanduser())
    keys = load_keys(keys_file) if keys_file and Path(keys_file).exists() else {}
    out: list[KeyStatus] = []
    for group in groups:
        gdir = root / group
        if not gdir.is_dir():
            continue
        for f in sorted(gdir.iterdir()):
            if not (f.is_file() and f.suffix == ".db" and is_wanted(group, f.name)):
                continue
            rel = f"{group}/{f.name}"
            key = resolve_key(f, keys, rel)
            size_mb = f.stat().st_size / 1e6
            if key is None:
                out.append(KeyStatus(rel, size_mb, False, False, False))
                continue
            data = f.read_bytes()
            first = decrypt_page(data[:PAGE_SIZE], key, 1) if len(data) >= PAGE_SIZE else b""
            out.append(KeyStatus(rel, size_mb, True, verify_key_hmac(f, key),
                                 _page_type_ok(first, 1)))
    return out


def _db_ok(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.execute("select count(*) from sqlite_master").fetchone()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def table_count(path: Path) -> int:
    """能读则返回表数量（用于确认 WAL 没有污染主库），否则 -1。"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return conn.execute("select count(*) from sqlite_master").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return -1


# --------------------------------------------------------------------------- #
# 增量刷新（decrypt 模式）
# --------------------------------------------------------------------------- #
def _resolve_src_root(root: Path) -> Path:
    """允许直接传 xwechat_files 根目录：自动落到最近使用的账号 db_storage。"""
    if (root / "message").is_dir() or (root / "contact").is_dir():
        return root
    from .paths import find_account_dirs
    found = find_account_dirs(root)
    return found[0] if found else root


@dataclass
class RefreshResult:
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.updated)


class DecryptedMirror:
    """把加密的微信库镜像成可读的 sqlite 文件。

    只在源文件 (mtime, size) 变化时才重新解密；解密很快（约 100MB/s 级），
    所以不做页级增量，避免状态不一致的隐蔽 bug。
    """

    def __init__(self, src_root: str | Path, out_root: str | Path, keys_file: str | Path,
                 groups: tuple[str, ...] = DEFAULT_GROUPS, decrypt_wal: bool = True):
        self.src_root = _resolve_src_root(Path(src_root).expanduser())
        self.out_root = Path(out_root).expanduser()
        self.groups = groups
        self.decrypt_wal = decrypt_wal
        self.keys_file = Path(keys_file).expanduser()
        self.keys = load_keys(self.keys_file) if self.keys_file and self.keys_file.exists() else {}
        self.state_path = self.out_root / ".sync_state.json"
        self._state: dict[str, list] = {}
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                self._state = {}

    # ---------------------------------------------------------------- #
    def _sources(self) -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        for group in self.groups:
            gdir = self.src_root / group
            if not gdir.is_dir():
                continue
            for f in sorted(gdir.iterdir()):
                if not f.is_file() or f.suffix != ".db":
                    continue
                if not is_wanted(group, f.name):
                    continue
                out.append((f"{group}/{f.name}", f))
        return out

    def _stamp(self, p: Path) -> list:
        st = p.stat()
        return [st.st_mtime_ns, st.st_size]

    def sync(self, force: bool = False) -> RefreshResult:
        res = RefreshResult()
        self.out_root.mkdir(parents=True, exist_ok=True)
        if not self.keys:
            res.errors.append(f"未读到密钥文件: {self.keys_file}")
            return res

        for rel, src in self._sources():
            try:
                stamp = self._stamp(src)
            except OSError as e:
                res.errors.append(f"{rel}: {e}")
                continue
            wal_src = src.with_name(src.name + "-wal")
            wal_stamp = self._stamp(wal_src) if wal_src.exists() else [0, 0]
            prev = self._state.get(rel)
            if not force and prev == [stamp, wal_stamp]:
                res.skipped.append(rel)
                continue

            key = resolve_key(src, self.keys, rel)
            if key is None:
                res.errors.append(f"{rel}: keys.json 里没有对应密钥")
                continue

            dst = self.out_root / rel
            dst_wal = dst.with_name(dst.name + "-wal")
            dst_wal.unlink(missing_ok=True)
            try:
                ok = decrypt_file(src, dst, key)
            except Exception as e:                              # pragma: no cover
                ok = False
                res.errors.append(f"{rel}: 解密异常 {e}")
            if not ok:
                res.errors.append(f"{rel}: 解密后无法用 sqlite 打开（密钥或格式不匹配）")
                continue

            want_wal = self.decrypt_wal and wal_stamp[1] > 0
            if want_wal:
                try:
                    wrote = decrypt_wal(wal_src, dst_wal, key)
                except Exception:
                    wrote = False
                if wrote:
                    # 校验：带 WAL 打开后表数量不能少于主库，否则丢弃 WAL
                    base = table_count(dst)
                    with_wal = table_count(dst)
                    if with_wal < base:
                        dst_wal.unlink(missing_ok=True)
                dst_shm = Path(str(dst) + "-shm")
                dst_shm.unlink(missing_ok=True)

            self._state[rel] = [stamp, wal_stamp]
            res.updated.append(rel)

        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        return res

