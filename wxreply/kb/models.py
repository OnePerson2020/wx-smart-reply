"""知识库数据模型（对齐微信 4.x 本地库的实际结构）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# 4.x 消息类型（local_type 低 16 位）
MESSAGE_TYPES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情",
    48: "位置", 49: "链接/文件/小程序", 50: "音视频通话",
    10000: "系统消息", 10002: "撤回消息",
}


@dataclass
class Contact:
    username: str = ""
    remark: str = ""
    nickname: str = ""
    is_group: bool = False
    row_id: int = 0

    @property
    def display_name(self) -> str:
        return self.remark or self.nickname or self.username

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{'群' if self.is_group else '联系人'} {self.display_name} {self.username}>"


@dataclass
class Message:
    local_id: int = 0
    create_time: int = 0
    local_type: int = 0
    content: str = ""
    is_from_me: bool = False
    sender_id: str = ""
    sender_name: str = ""
    chat_username: str = ""
    server_id: int = 0

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.create_time)

    @property
    def type_name(self) -> str:
        base = self.local_type & 0xFFFF
        return MESSAGE_TYPES.get(base, f"未知({base})")

    @property
    def is_text(self) -> bool:
        return (self.local_type & 0xFFFF) == 1

    @property
    def is_system(self) -> bool:
        base = self.local_type & 0xFFFF
        return base in (10000, 10002)

    def line(self, me_name: str = "我") -> str:
        """给模型看的单行格式。"""
        import time
        ts = time.strftime("%m-%d %H:%M", time.localtime(self.create_time))
        who = me_name if self.is_from_me else (self.sender_name or self.sender_id or "对方")
        body = self.content if self.is_text else f"[{self.type_name}]"
        return f"[{ts}] {who}: {body}"

    def to_dict(self) -> dict:
        return {
            "local_id": self.local_id,
            "time": self.datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": self.create_time,
            "type": self.type_name,
            "content": self.content,
            "is_from_me": self.is_from_me,
            "sender": self.sender_name or self.sender_id,
            "chat": self.chat_username,
        }
