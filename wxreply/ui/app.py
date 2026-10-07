"""应用装配：托盘 + 监控线程 + 生成线程 + 弹窗，用队列把跨线程结果搬回 Qt 主线程。"""
from __future__ import annotations

import queue
import threading
import time
import traceback

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..config import AppConfig, ContactBinding
from ..engine import ReplyEngine, ReplyEvent
from ..kb import KBReader
from ..llm import LLMError
from ..watcher import MessageWatcher, build_runtime
from .main_window import MainWindow
from .popup import PopupWindow


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2f6fed"))
    p.setPen(QColor("#2f6fed"))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("white"))
    f = p.font()
    f.setPointSize(30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), 0x84, "回")
    p.end()
    return QIcon(pm)


class Controller(QObject):
    def __init__(self, cfg: AppConfig, app: QApplication):
        super().__init__()
        self.cfg = cfg
        self.app = app
        self.reader: KBReader | None = None
        self.mirror = None
        self.engine = ReplyEngine(cfg, None)
        self.watcher: MessageWatcher | None = None
        self.runtime_note = ""
        self.popups: list[PopupWindow] = []
        self._inbox: queue.Queue = queue.Queue()
        self._outbox: queue.Queue = queue.Queue()
        self._inflight: set[str] = set()
        self.window = MainWindow(cfg, self)
        self._wire_window()
        self.tray = self._make_tray()
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    # ---------------------------------------------------------------- #
    def _wire_window(self) -> None:
        self.window.apply_requested.connect(self.apply_settings)
        self.window.check_requested.connect(self.check_now)
        self.window.reload_requested.connect(self.apply_settings)
        self.window.test_llm_requested.connect(self.test_llm)

    def _make_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(make_icon(), self.app)
        tray.setToolTip("微信辅助回复助手")
        menu = QMenu()
        act_show = QAction("打开设置", self.app)
        act_show.triggered.connect(self.show_window)
        menu.addAction(act_show)
        act_check = QAction("立即检查一次", self.app)
        act_check.triggered.connect(self.check_now)
        menu.addAction(act_check)
        self.act_pause = QAction("暂停监控", self.app, checkable=True)
        self.act_pause.toggled.connect(self._toggle_monitor)
        menu.addAction(self.act_pause)
        menu.addSeparator()
        act_quit = QAction("退出", self.app)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self.show_window()
                               if reason == QSystemTrayIcon.Trigger else None)
        tray.show()
        return tray

    # ---------------------------------------------------------------- #
    # 生命周期
    # ---------------------------------------------------------------- #
    def start(self) -> None:
        self.apply_settings()
        if self.watcher:
            self.watcher.start()
        self.window.show()
        self._set_status(self.runtime_note)

    def apply_settings(self) -> None:
        """保存后重建 reader / mirror / engine / watcher，并重启监控线程。"""
        self.cfg.save()
        if self.watcher:
            self.watcher.stop()
        reader, mirror, note = build_runtime(self.cfg)
        self.reader = reader
        self.mirror = mirror
        self.runtime_note = note
        self.engine = ReplyEngine(self.cfg, reader)
        self.watcher = MessageWatcher(self.cfg, self._on_incoming, reader=reader, mirror=mirror)
        if self.cfg.monitor_enabled:
            self.watcher.start()
        self._set_status(f"{note}　|　监控：{'运行中' if self.cfg.monitor_enabled else '已暂停'}")

    def quit(self) -> None:
        try:
            if self.watcher:
                self.watcher.stop()
        finally:
            self.app.quit()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _toggle_monitor(self, paused: bool) -> None:
        self.cfg.monitor_enabled = not paused
        self.window.monitor_enabled.setChecked(self.cfg.monitor_enabled)
        if self.watcher:
            if paused:
                self.watcher.stop()
            else:
                self.watcher.start()
        self._set_status(f"监控：{'已暂停' if paused else '运行中'}")

    def check_now(self) -> None:
        if not self.watcher:
            self._set_status("还没配置知识库目录")
            return
        fired = self.watcher.check_once()
        self._set_status(f"检查完成（{time.strftime('%H:%M:%S')}）：本轮触发 {len(fired)} 条")

    def test_llm(self) -> None:
        if self.cfg.llm.provider == "offline":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self.window, "离线模式",
                                    "当前是离线模板模式：不联网、不调用模型。\n"
                                    "所有界面交互都能跑通，候选内容为固定模板。\n"
                                    "填入 Base URL / API Key / 模型 ID 并选 openai 后即可真实生成。")
            return
        from PySide6.QtWidgets import QMessageBox
        try:
            text = self.engine.llm.chat("你是测试助手。", "只回两个字：可用", temperature=0)
            QMessageBox.information(self.window, "连接成功", f"模型返回：{text[:80]}")
        except LLMError as e:
            QMessageBox.warning(self.window, "连接失败", str(e))

    # ---------------------------------------------------------------- #
    # 监控线程 → 队列 → 主线程
    # ---------------------------------------------------------------- #
    def _on_incoming(self, binding: ContactBinding, msg, history) -> None:
        self._inbox.put((binding, msg))

    def _drain(self) -> None:
        while True:
            try:
                binding, msg = self._inbox.get_nowait()
            except queue.Empty:
                break
            key = f"{binding.username}:{getattr(msg, 'local_id', 0)}"
            if key in self._inflight:
                continue
            self._inflight.add(key)
            display = self.reader.display_name(binding.username) if self.reader else binding.username
            placeholder = ReplyEvent(contact_label=binding.label, chat_username=binding.username,
                                     display_name=display, incoming=msg)
            popup = self._show_popup(placeholder, pending=True)
            threading.Thread(target=self._generate, args=(binding, msg, popup),
                             name="wxreply-generate", daemon=True).start()

        while True:
            try:
                kind, payload = self._outbox.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "event":
                    binding, ev = payload
                    key = f"{binding.username}:{getattr(ev.incoming, 'local_id', 0)}"
                    self._inflight.discard(key)
                    for popup in list(self.popups):
                        if popup.reply_event.incoming is ev.incoming:
                            popup.reset_from_event(ev)
                    self._notify(ev)
                elif kind == "refine":
                    ev, index, cand = payload
                    for popup in self.popups:
                        if popup.reply_event is ev:
                            popup.apply_refine(index, cand)
                elif kind == "more":
                    binding, ev, new = payload
                    for popup in self.popups:
                        if popup.reply_event is ev:
                            popup.add_candidates(new)
                            popup.set_busy_more(False)
                elif kind == "status":
                    self._set_status(str(payload))
            except Exception:
                traceback.print_exc()

    def _generate(self, binding: ContactBinding, msg, popup: PopupWindow) -> None:
        ev = self.engine.generate(binding, msg)
        self._outbox.put(("event", (binding, ev)))

    def _show_popup(self, ev: ReplyEvent, pending: bool = False) -> PopupWindow:
        if pending:
            ev.error = ""
            ev.intent = ""
            p = PopupWindow(ev, self.cfg)
            p.status.setText("已收到新消息，正在生成候选回复…")
            p.set_busy_more(True)
        else:
            p = PopupWindow(ev, self.cfg)
        p.refine_requested.connect(self._on_refine)
        p.more_requested.connect(self._on_more)
        p.closed.connect(self._on_popup_closed)
        self.popups.append(p)
        if len(self.popups) > 4:                      # 别让窗口堆满屏幕
            self.popups[0].close()
        p.show()
        return p

    def _on_popup_closed(self, ev: ReplyEvent) -> None:
        self.popups = [p for p in self.popups if p.reply_event is not ev]

    def _on_refine(self, ev: ReplyEvent, index: int, comment: str) -> None:
        binding = self.cfg.find_contact(ev.chat_username) or ContactBinding(
            label=ev.contact_label, username=ev.chat_username)
        threading.Thread(target=self._refine, args=(binding, ev, index, comment),
                         name="wxreply-refine", daemon=True).start()

    def _refine(self, binding: ContactBinding, ev: ReplyEvent, index: int, comment: str) -> None:
        cand = self.engine.refine(binding, ev, index, comment)
        self._outbox.put(("refine", (ev, index, cand)))

    def _on_more(self, ev: ReplyEvent) -> None:
        for popup in self.popups:
            if popup.reply_event is ev:
                popup.set_busy_more(True)
        binding = self.cfg.find_contact(ev.chat_username) or ContactBinding(
            label=ev.contact_label, username=ev.chat_username)
        threading.Thread(target=self._more, args=(binding, ev), name="wxreply-more", daemon=True).start()

    def _more(self, binding: ContactBinding, ev: ReplyEvent) -> None:
        before = len(ev.candidates)
        self.engine.more(binding, ev)
        self._outbox.put(("more", (binding, ev, ev.candidates[before:])))

    # ---------------------------------------------------------------- #
    def _notify(self, ev: ReplyEvent) -> None:
        if not (self.tray and self.cfg.notify_system):
            return
        n = len(ev.candidates)
        body = f"{ev.display_name}: {(ev.incoming.content if ev.incoming else '')[:60]}\n已生成 {n} 条候选"
        self.tray.showMessage(f"📩 {ev.contact_label} 新消息", body, make_icon(), 6000)

    def _set_status(self, text: str) -> None:
        self.window.set_status(text)


def _fake_msg():
    from .kb.models import Message
    return Message(create_time=int(time.time()), local_type=1, content="在吗？想问你个事")


def run_gui(cfg: AppConfig) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("WxReply")
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)
    ctrl = Controller(cfg, app)
    app._wxreply_controller = ctrl       # 防 GC
    ctrl.start()
    return app.exec()
