"""测试用：最简单的 SQLCipher-4 写入器（只实现本程序读取所需的布局）。

页 1：salt(16) + AES-CBC(明文[16:4016]) + iv(16) + hmac(64, 这里填 0)
页 N：AES-CBC(明文[0:4016]) + iv(16) + hmac(64, 这里填 0)
WAL：标准 32 字节头 + (24 字节帧头 + 页) * N，帧头校验和按 SQLite 算法计算。
本程序不校验 HMAC，所以这里填 0 也能被正确读回。
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from Crypto.Cipher import AES

PAGE = 4096
IVN = 16
HMAC = 64
RESERVE = IVN + HMAC


def _enc(key: bytes, iv: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(data)


def encrypt_page(plain: bytes, key: bytes, pgno: int) -> bytes:
    assert len(plain) == PAGE
    iv = os.urandom(IVN)
    if pgno == 1:
        body = _enc(key, iv, plain[16:PAGE - RESERVE])
        return plain[:16] + body + iv + b"\x00" * HMAC   # 前 16 字节位置放 salt 由调用方覆盖
    body = _enc(key, iv, plain[:PAGE - RESERVE])
    return body + iv + b"\x00" * HMAC


def encrypt_db(src_plain: str | Path, dst: str | Path, key: bytes, salt: bytes) -> None:
    data = Path(src_plain).read_bytes()
    total = len(data) // PAGE
    out = bytearray()
    for i in range(total):
        page = encrypt_page(data[i * PAGE:(i + 1) * PAGE], key, i + 1)
        if i == 0:
            page = salt + page[16:]
        out += page
    out += data[total * PAGE:]
    Path(dst).write_bytes(bytes(out))


# --------------------------------------------------------------------------- #
def _checksum(data: bytes, s0: int, s1: int) -> tuple[int, int]:
    for i in range(0, len(data), 8):
        x0 = int.from_bytes(data[i:i + 4], "big")
        x1 = int.from_bytes(data[i + 4:i + 8], "big")
        s0 = (s0 + x0 + s1) & 0xFFFFFFFF
        s1 = (s1 + x1 + s0) & 0xFFFFFFFF
    return s0, s1


def encrypt_wal(frames: list[tuple[int, bytes]], v1: str | Path, v2: str | Path,
                dst_wal: str | Path, key: bytes) -> int:
    """frames: [(pgno, 明文页)]；v2 的总页数会写进最后一个 commit 帧。"""
    salt1 = int.from_bytes(os.urandom(4), "big")
    salt2 = int.from_bytes(os.urandom(4), "big")
    header24 = struct.pack(">IIIIII", 0x377F0682, 3007000, PAGE, 0, salt1, salt2)
    s0, s1 = _checksum(header24, 0, 0)
    out = bytearray(header24 + struct.pack(">II", s0, s1))
    total_v2 = Path(v2).stat().st_size // PAGE
    for idx, (pgno, plain) in enumerate(frames):
        dbsize = total_v2 if idx == len(frames) - 1 else 0
        head8 = struct.pack(">II", pgno, dbsize)
        s0, s1 = _checksum(head8, s0, s1)
        out += head8 + struct.pack(">IIII", salt1, salt2, s0, s1)
        out += encrypt_page(plain, key, pgno)
    Path(dst_wal).write_bytes(bytes(out))
    return total_v2

