"""发布前扫描：本仓库里不该出现的东西（本机路径、真实 wxid、密钥、解密产物、虚拟环境）。

    python tools/preflight.py            # 扫全仓库
    python tools/preflight.py --path X   # 只扫某个目录（测试用）

退出码 0 = 干净（PASS）；1 = 有命中（FAIL，逐条打印 文件:行: 规则）。
设计约束：这条命令要能在 Windows PowerShell 里直接跑，所以用 Python 而不是 shell 正则；
扫描时跳过 .git / .venv / 缓存目录，并跳过本文件自己（否则规则文本会自我命中）。
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", "node_modules"}
SKIP_FILES = {"preflight.py"}
TEXT_EXTS = {".py", ".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".ps1", ".bat",
             ".spec", ".html", ".css", ".js", ".sh", ".log"}

# 规则：(名字, 正则, 说明)。注意：不要在这些模式里写入会被自己命中的示例。
RULES: list[tuple[str, str, str]] = [
    ("machine-path-unix", r"/(?:Users|home)/[A-Za-z0-9._-]+/", "写了本机用户目录的绝对路径"),
    ("machine-path-win", r"[A-Za-z]:\\Users\\[^<\s\\]", "写了本机 Windows 用户目录的绝对路径"),
    ("real-wxid", r"wxid_(?!demo\b|xxx\b|xxxx\b)[a-z0-9]{8,}", "疑似真实微信号（应用占位符 wxid_xxx / wxid_demo_*）"),
    ("raw-key-hex", r"enc_key\"?\s*[:=]\s*\"?[0-9a-fA-F]{64}", "疑似真实 AES 密钥"),
    ("sqlcipher-keyliteral", r"x'[0-9a-fA-F]{96,}'", "疑似真实 keys.json 条目"),
    ("bearer-secret", r"(?:api[_-]?key|apikey|token)\"\s*:\s*\"[A-Za-z0-9_\-]{24,}", "疑似真实 API Key/Token"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    ("internal-marker", r"ark_controlplane|node_acls|bytedance\.com|larkoffice\.com",
     "内网/公司内部标识（发到公开仓库前去掉）"),
]

FORBIDDEN_FILES = {
    "keys.json": "密钥文件（应放在仓库外的用户目录）",
    "wxecho_keys.json": "密钥文件",
    "history.jsonl": "运行记录（含真实聊天内容）",
}
FORBIDDEN_SUFFIXES = {".db": "微信数据库/解密产物", ".db-wal": "数据库 WAL", ".db-shm": "数据库 SHM"}


@dataclass
class Finding:
    rule: str
    path: str
    line: int
    text: str
    note: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.note} → {self.text[:120]}"


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file() or p.name in SKIP_FILES:
            continue
        yield p


def scan(root: str | Path) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    compiled = [(name, re.compile(pattern), note) for name, pattern, note in RULES]

    for p in _iter_files(root):
        rel = str(p.relative_to(root)) if p != root else p.name

        if p.name in FORBIDDEN_FILES:
            findings.append(Finding("forbidden-file", rel, 1, p.name, FORBIDDEN_FILES[p.name]))
        suffix = "".join(p.suffixes[-2:]) if len(p.suffixes) >= 2 else p.suffix
        if p.suffix in FORBIDDEN_SUFFIXES or suffix in FORBIDDEN_SUFFIXES:
            findings.append(Finding("forbidden-file", rel, 1, p.name,
                                    FORBIDDEN_SUFFIXES.get(suffix) or FORBIDDEN_SUFFIXES.get(p.suffix, "数据文件")))

        if p.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for name, rx, note in compiled:
                m = rx.search(line)
                if m:
                    findings.append(Finding(name, rel, i, m.group(0), note))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布前扫描：本机路径 / 真实 wxid / 密钥 / 解密产物 / 虚拟环境")
    ap.add_argument("--path", default=str(Path(__file__).resolve().parents[1]), help="要扫描的根目录")
    args = ap.parse_args(argv)

    root = Path(args.path).expanduser().resolve()
    findings = scan(root)
    if findings:
        print(f"FAIL：{len(findings)} 处需要处理（{root}）\n")
        for f in findings:
            print(f"  {f}")
        print("\n处理：真实数据/密钥只放在用户目录（如 %APPDATA%\\wxreply 或仓库外的解密目录）；"
              "文档里用 <你>、wxid_xxx、wxid_demo_* 这类占位符。")
        return 1
    print(f"PASS：{root} 没有本机路径 / 真实 wxid / 密钥 / 数据库文件 / 虚拟环境")
    return 0


if __name__ == "__main__":
    sys.exit(main())
