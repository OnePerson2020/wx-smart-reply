"""微信数据目录发现：定位已解密目录 / 加密源目录 / keys.json。"""
from __future__ import annotations

import sys
from pathlib import Path

def candidate_xwechat_roots() -> list[Path]:
    """常见 xwechat_files 根目录（Windows / macOS）。"""
    home = Path.home()
    out: list[Path] = []
    if sys.platform == "win32":
        # 微信「文件管理」自定义目录写在注册表里
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat") as k:
                val, _ = winreg.QueryValueEx(k, "FileSavePath")
                if val and val != "MyDocument:":
                    out.append(Path(val) / "xwechat_files")
        except Exception:
            pass
        out.append(home / "Documents" / "xwechat_files")
        out.append(home / "Documents" / "WeChat Files")
    else:
        out.append(home / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files")
    return out


def find_account_dirs(root: str | Path | None = None) -> list[Path]:
    """返回所有含 db_storage 的账号目录（按最近修改排序，最近登录的在前）。"""
    roots = [Path(root).expanduser()] if root else []
    roots += candidate_xwechat_roots()
    found: list[Path] = []
    for r in roots:
        if not r or not r.exists():
            continue
        if (r / "db_storage").is_dir():
            found.append(r / "db_storage")
            continue
        if (r / "contact").is_dir() and (r / "message").is_dir():
            found.append(r)
            continue
        try:
            for d in r.iterdir():
                if d.is_dir() and (d / "db_storage").is_dir():
                    found.append(d / "db_storage")
        except OSError:
            continue
    uniq: list[Path] = []
    for p in found:
        if p not in uniq:
            uniq.append(p)
    uniq.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return uniq


def find_keys_file(explicit: str | Path | None = None, search_dirs: list[Path] | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    for d in (search_dirs or []) + [Path.cwd(), Path.home()]:
        for name in ("keys.json", "wxecho_keys.json"):
            p = Path(d) / name
            if p.exists():
                return p
    return None
