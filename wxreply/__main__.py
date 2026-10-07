"""命令行入口：默认开图形界面，另有若干无界面命令便于排查。

  python -m wxreply                      # 打开图形界面
  python -m wxreply check  --kb DIR      # 检查知识库/联系人
  python -m wxreply once   --kb DIR --chat 备注名 [--text 对方发来的消息]
  python -m wxreply demo   --kb DIR --chat 备注名   # 用离线模板跑一次生成（无需模型）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import AppConfig, ContactBinding
from .engine import ReplyEngine
from .kb import KBReader
from .kb.crypto import DecryptedMirror
from .kb.models import Message
from .kb.paths import find_account_dirs, find_keys_file


def _load_config(args) -> AppConfig:
    cfg = AppConfig.load(getattr(args, "config", None))
    if getattr(args, "kb", None):
        cfg.decrypted_dir = args.kb
    return cfg


def _reader(cfg: AppConfig, self_username: str = "") -> KBReader:
    return KBReader(cfg.decrypted_dir, self_username=self_username)


def cmd_check(args) -> int:
    cfg = _load_config(args)
    if not cfg.decrypted_dir:
        auto = find_account_dirs()
        print("候选微信数据目录：")
        for d in auto:
            print("  -", d)
        if not auto:
            print("  未找到。用 --kb 指定已解密目录。")
        print("keys.json 候选：", find_keys_file(None) or "未找到")
        return 1
    r = _reader(cfg, getattr(args, "self_id", "") or "")
    print(json.dumps(r.stats(), ensure_ascii=False, indent=2))
    print("\n监控中的联系人：")
    for b in cfg.contacts:
        if not b.username:
            continue
        hit = r.resolve(b.username)
        if not hit:
            print(f"  ❌ {b.label}: {b.username} 找不到")
            continue
        ts = r.latest_ts(hit.username)
        print(f"  ✅ {b.label}: {hit.display_name} ({hit.username}) "
              f"最近消息 {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts)) if ts else '无'}")
    print("\n最近 20 条消息（供选择联系人）：")
    for m in r.messages(args.chat, limit=20) if args.chat else []:
        print("  ", m.line())
    return 0


def cmd_once(args, cfg=None) -> int:
    cfg = cfg or _load_config(args)
    if not (cfg.decrypted_dir and args.chat):
        print("需要 --kb 和 --chat", file=sys.stderr)
        return 1
    r = _reader(cfg, getattr(args, "self_id", "") or "")
    hit = r.resolve(args.chat)
    if not hit:
        print(f"知识库里找不到：{args.chat}", file=sys.stderr)
        return 1
    from .kb.models import Message
    if args.text:
        incoming = Message(create_time=int(time.time()), local_type=1, content=args.text,
                           is_from_me=False, chat_username=hit.username,
                           sender_name=hit.display_name)
    else:
        msgs = [m for m in r.messages(hit.username, limit=30) if not m.is_from_me and m.is_text]
        if not msgs:
            print("这个会话没有对方发的文本消息", file=sys.stderr)
            return 1
        incoming = msgs[-1]
    binding = ContactBinding(label=args.label or "联系人 1", username=hit.username,
                             note=args.note or "", enabled=True)
    engine = ReplyEngine(cfg, r)
    ev = engine.generate(binding, incoming)
    print(f"对象：{ev.contact_label} · {ev.display_name}")
    print(f"对方：{incoming.line()}")
    if ev.intent:
        print(f"意图：{ev.intent}")
    if ev.error:
        print(f"提示：{ev.error}")
    for i, c in enumerate(ev.candidates, 1):
        print(f"\n[{i}] {c.tone}（风险 {c.risk}）\n    {c.current}\n    理由：{c.reason}")
    refine = getattr(args, "refine", None)
    if refine and ev.candidates:
        cand = engine.refine(binding, ev, 0, refine)
        print(f"\n按意见「{refine}」改写后：\n    {cand.current}")
    return 0


def cmd_demo(args) -> int:
    """无模型也能跑：离线模板 + 可选真实知识库。"""
    cfg = _load_config(args)
    cfg.llm.provider = "offline"
    if not cfg.decrypted_dir:
        print("需要 --kb（或用 tools/make_demo_kb.py 生成一份演示数据）", file=sys.stderr)
        return 1
    return cmd_once(args, cfg)


def cmd_refresh(args) -> int:
    """按 keys.json 增量解密一次，然后报告状态。"""
    src = Path(args.src).expanduser() if args.src else None
    if not src:
        dirs = find_account_dirs()
        if not dirs:
            print("没找到微信数据目录，用 --src 指定", file=sys.stderr)
            return 1
        src = dirs[0]
    keys = find_keys_file(args.keys)
    if not keys:
        print("没找到 keys.json，用 --keys 指定", file=sys.stderr)
        return 1
    out = Path(args.out).expanduser()
    mirror = DecryptedMirror(src, out, keys, decrypt_wal=not args.no_wal)
    res = mirror.sync(force=args.force)
    print(f"源：{src}\n输出：{out}\n密钥：{keys}")
    print(f"更新 {len(res.updated)} 个：{res.updated}")
    if res.errors:
        print("错误：" + "；".join(res.errors))
    return 0


def cmd_selftest(args) -> int:
    """一键自检：依赖、知识库、生成引擎、界面——打包后/新机器上先跑这个。"""
    import platform
    import tempfile

    ok = True
    report: list[str] = []

    def emit(line: str) -> None:
        report.append(line)
        print(line)

    def check(name: str, fn):
        nonlocal ok
        try:
            detail = fn() or "ok"
            emit(f"[PASS] {name}: {detail}")
        except Exception as e:                                  # noqa: BLE001
            ok = False
            emit(f"[FAIL] {name}: {type(e).__name__}: {e}")

    emit(f"python {platform.python_version()} / {platform.system()} {platform.release()}")

    def deps():
        import Crypto  # noqa: F401
        import httpx  # noqa: F401
        import PySide6
        return f"PySide6 {PySide6.__version__} / pycryptodome / httpx 已就绪"

    def kb_roundtrip():
        tools = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "tools"
        sys.path.insert(0, str(tools))
        try:
            from make_demo_kb import build as demo_build                 # type: ignore
        except ImportError:
            print("[SKIP] 知识库: 未内置演示数据生成器（用 selftest --kb <目录> 检查真实库）")
            return "跳过"
        with tempfile.TemporaryDirectory() as tmp:
            root = demo_build(Path(tmp) / "kb")
            r = KBReader(root)
            st = r.stats()
            assert st["chats"] >= 3, st
            assert r.resolve("张三"), "联系人解析失败"
            return f"自建演示库可读：{st['chats']} 个会话，自己={st['self_username']}"

    def engine_offline():
        from .kb.models import Message
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(data_dir=str(Path(tmp) / "d"))
            cfg.llm.provider = "offline"
            binding = ContactBinding(label="联系人 1", username="demo", enabled=True)
            eng = ReplyEngine(cfg, None)
            ev = eng.generate(binding, Message(create_time=int(time.time()), local_type=1,
                                               content="在吗？", chat_username="demo"))
            assert len(ev.candidates) >= 3
            return f"离线生成 {len(ev.candidates)} 条候选"

    def ui():
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from .engine import Candidate, ReplyEvent
        from .ui.popup import PopupWindow
        app = QApplication.instance() or QApplication([])
        cfg = AppConfig(data_dir=str(Path(tempfile.gettempdir()) / "wxreply_selftest"))
        ev = ReplyEvent(contact_label="自检", display_name="自检对象",
                        incoming=Message(create_time=int(time.time()), local_type=1, content="测试"),
                        candidates=[Candidate("稳妥", "好", "理由", "低")])
        p = PopupWindow(ev, cfg)
        assert len(p.cards) == 1
        app.processEvents()
        p.close()
        return f"弹窗可构建（{len(p.cards)} 张候选卡）"

    check("依赖", deps)
    check("知识库", kb_roundtrip)
    check("生成引擎", engine_offline)
    check("界面", ui)

    if getattr(args, "kb", None):
        def real_kb():
            r = KBReader(args.kb)
            st = r.stats()
            assert st["chats"] > 0, "没有读到任何会话"
            return (f"{st['chats']} 个会话 / {st['contacts']} 个联系人 / "
                    f"自己={st['self_username'] or '未识别'}")
        check(f"真实知识库 {args.kb}", real_kb)

    emit("")
    emit("全部通过 ✅" if ok else "有失败项 ❌")
    # 打包成 GUI exe 后没有控制台，自检结果弹窗展示，否则用户会看到"什么都没发生"
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            QApplication.instance() or QApplication([])
            box = QMessageBox()
            box.setWindowTitle("微信辅助回复助手 · 自检")
            box.setText("全部通过 ✅" if ok else "有失败项 ❌")
            box.setDetailedText("\n".join(report))
            box.exec()
        except Exception:                                       # noqa: BLE001
            pass
    return 0 if ok else 1


def cmd_gui(args) -> int:
    from .ui import run_gui
    cfg = _load_config(args)
    if getattr(args, "offline", False):
        cfg.llm.provider = "offline"
    return run_gui(cfg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wxreply", description="微信辅助回复助手")
    p.add_argument("--config", help="配置文件路径（默认 ~/.wxreply/config.json）")
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="打开图形界面（默认）")
    g.add_argument("--kb", help="已解密数据库目录")
    g.add_argument("--offline", action="store_true", help="强制离线模板模式")
    g.set_defaults(func=cmd_gui)

    for name, fn, extra in (("check", cmd_check, False),
                            ("once", cmd_once, True),
                            ("demo", cmd_demo, True)):
        sp = sub.add_parser(name)
        sp.add_argument("--kb", help="已解密数据库目录")
        sp.add_argument("--chat", help="联系人备注/昵称/wxid")
        sp.add_argument("--self-id", help="自己的微信号（自动识别失败时手工指定）")
        if extra:
            sp.add_argument("--text", help="模拟对方刚发来的消息")
            sp.add_argument("--label", help="联系人名称（默认 联系人 1）")
            sp.add_argument("--note", help="给这个联系人的额外说明")
        if name == "once":
            sp.add_argument("--refine", help="对第 1 条候选提一个意见，验证多轮改写")
        sp.set_defaults(func=fn)

    r = sub.add_parser("refresh", help="按 keys.json 增量解密一次")
    r.add_argument("--src", help="微信 xwechat_files 目录或账号 db_storage 目录")
    r.add_argument("--out", required=True, help="解密输出目录")
    r.add_argument("--keys", help="keys.json / wxecho_keys.json")
    r.add_argument("--force", action="store_true", help="忽略 mtime 缓存，强制重新解密")
    r.add_argument("--no-wal", action="store_true", help="不解密 -wal")
    r.set_defaults(func=cmd_refresh)

    st = sub.add_parser("selftest", help="一键自检（依赖 / 知识库 / 生成 / 界面）")
    st.add_argument("--kb", help="顺便检查一个真实（或演示）已解密目录")
    st.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        args = p.parse_args(["gui"] + (argv or []))
        args.kb = None
        args.offline = False
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
