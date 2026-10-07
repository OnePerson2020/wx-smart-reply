"""配置：数据目录、模型接入、联系人白名单、监控参数。全部落到一个 JSON 文件，便于手工查看。"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def default_data_dir() -> Path:
    """Windows: %APPDATA%\\wxreply；其他平台: ~/.wxreply"""
    env = os.environ.get("WXREPLY_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "wxreply"
    return Path.home() / ".wxreply"


# 默认语气清单：不同语气 + 不同倾向，覆盖"稳 / 巧 / 直 / 简"
DEFAULT_TONES = [
    {"name": "稳妥", "hint": "安全、不踩雷、不留把柄，偏礼貌克制"},
    {"name": "轻松", "hint": "幽默或随和，化解尴尬、拉近距离"},
    {"name": "直球", "hint": "态度明确，把事情往前推进，该拒绝就拒绝"},
    {"name": "简短", "hint": "一两句话，随手回复，不用长篇大论"},
]


@dataclass
class LLMConfig:
    """OpenAI 兼容的 /chat/completions 接口。provider=offline 时不联网，用本地模板兜底。"""
    provider: str = "offline"                 # offline | openai
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    api_key: str = ""
    model: str = ""
    temperature: float = 0.8
    max_tokens: int = 2048          # 带思考的模型要给足预算，否则正文可能是空的
    timeout_sec: int = 60

    @property
    def ready(self) -> bool:
        if self.provider == "offline":
            return True
        return bool(self.api_key and self.model and self.base_url)

    @property
    def needs_api_key(self) -> bool:
        """本地网关（localhost / 127.0.0.1）通常不用 key，别卡住用户。"""
        base = (self.base_url or "").lower()
        return not any(h in base for h in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]"))


@dataclass
class ContactBinding:
    """一个被监控的联系人／群。label 就是界面上的"联系人 1/2/3/4"。"""
    label: str = "联系人 1"
    username: str = ""            # wxid_xxx / xxx@chatroom / 自定义微信号
    enabled: bool = True
    note: str = ""                # 额外指令，例如"对方是老板，要客气"
    tones: list[dict] = field(default_factory=list)   # 空 = 用全局默认语气
    is_group: bool = False
    last_seen_ts: int = 0         # 已提醒到的时间戳（避免重复弹窗 / 重启后重放）

    @property
    def tones_or_default(self) -> list[dict]:
        return self.tones or DEFAULT_TONES


@dataclass
class AppConfig:
    data_dir: str = ""
    llm: LLMConfig = field(default_factory=LLMConfig)
    contacts: list[ContactBinding] = field(default_factory=lambda: [ContactBinding()])

    # 监控
    monitor_enabled: bool = True
    poll_interval_sec: float = 3.0
    source_mode: str = "dir"          # dir = 直接轮询已解密目录；decrypt = 先增量解密再轮询
    decrypted_dir: str = ""           # 已解密数据库目录（dir 模式必填；decrypt 模式的输出目录）
    wechat_dir: str = ""              # decrypt 模式：xwechat_files 根目录或 <wxid>_xxx/db_storage
    keys_file: str = ""               # decrypt 模式：keys.json
    decrypt_wal: bool = True          # 是否尝试解密 -wal（尽力而为，失败自动忽略）

    # 生成
    context_messages: int = 20        # 送给模型的历史条数（双向）
    style_samples: int = 8            # 取多少条"我"的历史消息做语气模仿
    max_candidates: int = 4
    replay_on_start: bool = False     # 启动时是否为历史消息补弹窗（默认关闭，只提醒新的）
    max_event_age_min: int = 15       # 只提醒 N 分钟内出现的新消息，防止重放旧记录

    # 界面
    popup_corner: str = "bottom-right"   # bottom-right / bottom-left / top-right / top-left
    auto_copy_on_final: bool = True
    notify_system: bool = True

    # ---------------------------------------------------------------- #
    def contacts_path(self) -> Path:
        return self.data_dir_path() / "config.json"

    def data_dir_path(self) -> Path:
        return Path(self.data_dir or default_data_dir()).expanduser()

    def history_path(self) -> Path:
        return self.data_dir_path() / "history.jsonl"

    def log_path(self) -> Path:
        return self.data_dir_path() / "wxreply.log"

    def ensure_dirs(self) -> None:
        self.data_dir_path().mkdir(parents=True, exist_ok=True)

    def enabled_contacts(self) -> list[ContactBinding]:
        """按 wxid 去重，避免两个名称指向同一个人时弹两次窗。"""
        out: list[ContactBinding] = []
        seen: set[str] = set()
        for c in self.contacts:
            username = (c.username or "").strip()
            if not (c.enabled and username) or username in seen:
                continue
            seen.add(username)
            out.append(c)
        return out

    def find_contact(self, username: str) -> ContactBinding | None:
        for c in self.contacts:
            if c.username == username:
                return c
        return None

    # ---------------------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        raw = dict(raw or {})
        llm = LLMConfig(**{k: v for k, v in (raw.pop("llm", {}) or {}).items()
                           if k in LLMConfig.__dataclass_fields__})
        contacts = []
        for c in raw.pop("contacts", []) or []:
            contacts.append(ContactBinding(**{k: v for k, v in c.items()
                                              if k in ContactBinding.__dataclass_fields__}))
        cfg = cls(llm=llm, contacts=contacts or [ContactBinding()],
                  **{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        cfg.ensure_dirs()
        return cfg

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        p = Path(path).expanduser() if path else default_data_dir() / "config.json"
        if p.exists():
            try:
                cfg = cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
                if not cfg.data_dir:            # 用了 --config，就把它旁边的目录当数据目录
                    cfg.data_dir = str(p.parent)
                    cfg.ensure_dirs()
                return cfg
            except Exception:
                pass
        cfg = cls(data_dir=str(p.parent))
        cfg.ensure_dirs()
        return cfg

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path).expanduser() if path else self.contacts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p
