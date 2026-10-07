"""发布前扫描工具：仓库本身必须干净；各类泄漏必须真的能被抓到。

注意：本文件里所有"泄漏样例"都在运行时拼出来，不写成字面量，
否则它自己就会被扫描器命中（扫描器不会特例豁免任何文件，除了它自己）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from preflight import main, scan          # noqa: E402

JOIN = "".join


def test_repo_is_clean():
    """守住这条：仓库里不允许出现本机路径、真实 wxid、密钥、数据库文件。"""
    findings = scan(ROOT)
    assert findings == [], "仓库里有不该提交的内容：\n" + "\n".join(str(f) for f in findings)
    assert main(["--path", str(ROOT)]) == 0


def _leak_cases() -> dict[str, str]:
    hex64 = JOIN(["7f2a9c4e", "1b6d8f0a", "3c5e7b9d", "1f3a5c7e",
                  "9b1d3f5a", "7c9e1b3d", "5f7a9c1e", "3b5d7f9a"])
    return {
        "leak_path_unix.py": f'P = "{JOIN(["/Users/", "somebody", "/work/kb"])}"\n',
        "leak_path_win.md": f"path = {JOIN(['D:', chr(92), 'Users', chr(92), 'somebody'])}{chr(92)}Documents\n",
        "leak_wxid.txt": f"chat = {JOIN(['wxid_', '8k3jf9s0d2m4'])}\n",
        "leak_key.json": '{' + f'"{JOIN(["enc", "_key"])}": "{hex64}"' + '}\n',
        "leak_literal.txt": f"{JOIN(['x', chr(39)])}{'ab12' * 24}{chr(39)}\n",
        "leak_token.py": f'apikey": "{JOIN(["sk-live-", "abcdefghijklmnopqrstuvwxyz012345"])}"\n',
        "leak_pem.md": f"-----BEGIN {JOIN(['RSA ', 'PRIVATE KEY'])}-----\n",
        "leak_internal.md": f"control plane: {JOIN(['ark_', 'controlplane'])} / {JOIN(['node', '_acls'])}\n",
        "leak.db": "SQLite format 3\x00",
        "keys.json": "{}",
        "history.jsonl": '{"kind":"generate"}\n',
    }


def test_scan_catches_each_leak_kind(tmp_path: Path):
    """反向自测：每种泄漏都要抓得到（否则这个门就是摆设）。"""
    cases = _leak_cases()
    for name, content in cases.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    findings = scan(tmp_path)
    hit = {f.path for f in findings}
    for name in cases:
        assert name in hit, f"{name} 没被抓到；现有命中：{sorted(hit)}"
    assert main(["--path", str(tmp_path)]) == 1


def test_placeholders_are_allowed(tmp_path: Path):
    """文档里合法的占位符不能被误报。"""
    home = JOIN(["C:", chr(92), "Users", chr(92), "<你>"])
    (tmp_path / "guide.md").write_text(
        f'python -m wxreply check --kb "{home}{chr(92)}Documents{chr(92)}xwechat_files"\n'
        f"联系人 {JOIN(['wxid_', 'xxx'])} / {JOIN(['wxid_', 'demo_zhang'])}\n"
        f'{{"message/message_0.db": {{"{JOIN(["enc", "_key"])}": "<64hex>"}}}}\n'
        "python -m wxreply --config ~/.wxreply/config.json\n",
        encoding="utf-8")
    assert scan(tmp_path) == []


def test_binary_and_cache_dirs_are_skipped(tmp_path: Path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "leak.py").write_text(
        f'"{JOIN([chr(47) + "Users" + chr(47), "nobody", chr(47)])}x"\n', encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert scan(tmp_path) == []
