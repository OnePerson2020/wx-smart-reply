"""新消息监控：轮询知识库，发现白名单联系人的新消息就回调。"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig, ContactBinding
from .kb import KBReader
from .kb.crypto import DecryptedMirror, RefreshResult


@dataclass
class WatchStatus:
    running: bool = False
    last_poll: float = 0.0
    last_change: float = 0.0
    polls: int = 0
    errors: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)

    def note_error(self, msg: str) -> None:
        self.errors = ([msg] + self.errors)[:5]


class MessageWatcher:
    """可以线程跑（start/stop），也可以单次跑（check_once，方便测试与 CLI）。"""

    def __init__(self, cfg: AppConfig, on_event: Callable[[ContactBinding, object, list], None],
                 reader: KBReader | None = None, mirror: DecryptedMirror | None = None):
        self.cfg = cfg
        self.on_event = on_event
        self.reader = reader
        self.mirror = mirror
        self.status = WatchStatus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty = False

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wxreply-watcher", daemon=True)
        self._thread.start()
        self.status.running = True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.status.running = False
        if self._dirty:
            self.cfg.save()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception as e:                      # 监控线程绝不能静默死掉
                self.status.note_error(f"轮询异常：{e}")
            self._stop.wait(max(0.5, float(self.cfg.poll_interval_sec)))

    # ------------------------------------------------------------------ #
    def refresh_source(self) -> RefreshResult | None:
        if not self.mirror:
            return None
        res = self.mirror.sync()
        if res.updated:
            self.status.last_change = time.time()
            self.status.refreshed = res.updated
        for e in res.errors:
            self.status.note_error(e)
        return res

    def check_once(self) -> list[tuple[ContactBinding, object, list]]:
        """跑一轮检查，返回本轮触发的事件（同时也回调 on_event）。"""
        self.status.polls += 1
        self.status.last_poll = time.time()
        fired: list[tuple[ContactBinding, object, list]] = []
        if not self.cfg.monitor_enabled:
            return fired
        self.refresh_source()
        if self.reader is None:
            return fired

        now = int(time.time())
        max_age = max(60, int(self.cfg.max_event_age_min) * 60)
        for binding in self.cfg.enabled_contacts():
            username = self._resolve(binding)
            if not username:
                self.status.note_error(f"{binding.label}: 在知识库里找不到 {binding.username}")
                continue
            try:
                latest = self.reader.latest_ts(username)
            except Exception as e:
                self.status.note_error(f"{binding.label}: 读取失败 {e}")
                continue
            if latest <= 0:
                continue

            if not binding.last_seen_ts:
                # 首次绑定：只记住进度，不倒放历史
                binding.last_seen_ts = latest if not self.cfg.replay_on_start else max(0, latest - max_age)
                self._dirty = True
                continue

            try:
                new_msgs = self.reader.new_messages(username, binding.last_seen_ts)
            except Exception as e:
                self.status.note_error(f"{binding.label}: 查询新消息失败 {e}")
                continue
            if not new_msgs:
                continue

            binding.last_seen_ts = max(m.create_time for m in new_msgs)
            self._dirty = True
            incoming = [m for m in new_msgs
                        if not m.is_from_me and m.is_text and (m.content or "").strip()
                        and now - m.create_time <= max_age]
            if not incoming:
                continue
            msg = incoming[-1]
            history = [m for m in new_msgs if m.is_from_me or m is not msg]
            fired.append((binding, msg, history))
            try:
                self.on_event(binding, msg, history)
            except Exception as e:
                self.status.note_error(f"回调异常：{e}")
        if self._dirty:
            self._dirty = False
            self.cfg.save()
        return fired

    def _resolve(self, binding: ContactBinding) -> str:
        raw = (binding.username or "").strip()
        if not raw or self.reader is None:
            return raw
        if binding.is_group and raw.endswith("@chatroom"):
            return raw
        hit = self.reader.resolve(raw)
        return hit.username if hit else (raw if raw.endswith(("@chatroom", "@openim")) or raw.startswith("wxid_") else "")


def build_runtime(cfg: AppConfig) -> tuple[KBReader | None, DecryptedMirror | None, str]:
    """按配置准备知识库读取器（必要时先建解密镜像）。返回 (reader, mirror, 状态说明)。"""
    mirror: DecryptedMirror | None = None
    note = ""
    if cfg.source_mode == "decrypt":
        if not (cfg.wechat_dir and cfg.keys_file and cfg.decrypted_dir):
            return None, None, "decrypt 模式需要填：微信数据目录、keys.json、解密输出目录"
        mirror = DecryptedMirror(cfg.wechat_dir, cfg.decrypted_dir, cfg.keys_file,
                                 decrypt_wal=cfg.decrypt_wal)
        res = mirror.sync()
        if res.errors:
            note = "解密提示：" + "；".join(res.errors[:2])
        if not Path(cfg.decrypted_dir).exists():
            return None, mirror, note or "解密目录还没生成"
    target = cfg.decrypted_dir
    if not target or not Path(target).expanduser().exists():
        return None, mirror, note or "还没设置已解密数据库目录"
    try:
        reader = KBReader(target)
    except Exception as e:
        return None, mirror, note or f"读取知识库失败：{e}"
    if not note:
        st = reader.stats()
        note = f"知识库就绪：{st['chats']} 个会话 / 自己={st['self_username'] or '未识别'}"
    return reader, mirror, note
