"""回复生成引擎：候选生成、换一批、按用户意见多轮改写。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from . import prompts
from .config import AppConfig, ContactBinding, DEFAULT_TONES
from .kb import KBReader, Message
from .llm import ChatLLM, LLMError, extract_json


@dataclass
class Candidate:
    tone: str = "稳妥"
    text: str = ""
    reason: str = ""
    risk: str = "低"
    rounds: list[dict] = field(default_factory=list)   # [{comment, revised, ts}]

    @property
    def current(self) -> str:
        return self.rounds[-1]["revised"] if self.rounds else self.text

    def to_dict(self) -> dict:
        return {"tone": self.tone, "text": self.current, "original": self.text,
                "reason": self.reason, "risk": self.risk, "rounds": self.rounds}


@dataclass
class ReplyEvent:
    contact_label: str = ""
    chat_username: str = ""
    display_name: str = ""
    incoming: Message | None = None
    context: list[Message] = field(default_factory=list)
    style_samples: list[str] = field(default_factory=list)
    intent: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ts": self.created_at,
            "contact": self.contact_label,
            "chat": self.chat_username,
            "from": self.display_name,
            "incoming": self.incoming.to_dict() if self.incoming else None,
            "intent": self.intent,
            "error": self.error,
            "candidates": [c.to_dict() for c in self.candidates],
        }


class ReplyEngine:
    def __init__(self, cfg: AppConfig, reader: KBReader | None = None):
        self.cfg = cfg
        self.reader = reader
        self.llm = ChatLLM(cfg.llm)

    # ------------------------------------------------------------------ #
    # 上下文
    # ------------------------------------------------------------------ #
    def build_context(self, binding: ContactBinding, incoming: Message) -> tuple[list[Message], list[str]]:
        if not self.reader:
            return [], []
        before = incoming.create_time - 1
        context = self.reader.messages(binding.username, limit=self.cfg.context_messages,
                                       before_ts=before)
        context.append(incoming)
        mine = [m.content for m in context if m.is_from_me and m.is_text and m.content.strip()]
        style = mine[-self.cfg.style_samples:] if self.cfg.style_samples else []
        return context, style

    def _tones(self, binding: ContactBinding) -> list[dict]:
        return binding.tones_or_default or DEFAULT_TONES

    # ------------------------------------------------------------------ #
    # 生成
    # ------------------------------------------------------------------ #
    def generate(self, binding: ContactBinding, incoming: Message) -> ReplyEvent:
        display = self.reader.display_name(binding.username) if self.reader else binding.username
        context, style = self.build_context(binding, incoming)
        ev = ReplyEvent(contact_label=binding.label, chat_username=binding.username,
                        display_name=display, incoming=incoming, context=context,
                        style_samples=style)
        if self.cfg.llm.provider == "offline":
            ev.candidates = self._offline_candidates(incoming)
            ev.intent = "（离线模板模式：未接入模型，仅演示交互流程）"
            ev.error = "当前是离线模板模式，候选不贴合语境。在「模型」页配置后即可真实生成。"
            self.log(ev)
            return ev

        system, user = prompts.build_generate_prompt(
            binding.label, display, binding.note, incoming, context, style,
            self._tones(binding), self.cfg.max_candidates)
        try:
            raw = self.llm.chat(system, user)
            data = extract_json(raw)
            ev.intent = str(data.get("intent", ""))[:120]
            ev.candidates = self._parse_candidates(data)
            if not ev.candidates:
                raise LLMError("模型没有给出可用候选")
        except (LLMError, ValueError, KeyError) as e:
            ev.error = f"生成失败：{e}"
            ev.candidates = self._offline_candidates(incoming)
        self.log(ev)
        return ev

    def more(self, binding: ContactBinding, event: ReplyEvent) -> ReplyEvent:
        """换一批：避开已有候选，再要几条。"""
        if self.cfg.llm.provider == "offline":
            extra = self._offline_candidates(event.incoming or Message())[:2]
            for c in extra:
                c.tone = c.tone + "·备选"
            event.candidates.extend(extra)
            return event
        system, user = prompts.build_tone_prompt(
            event.incoming or Message(), [c.current for c in event.candidates],
            self._tones(binding), self.cfg.max_candidates)
        try:
            data = extract_json(self.llm.chat(system, user))
            new = self._parse_candidates(data)
            if not new:
                raise LLMError("没有新候选")
            event.intent = str(data.get("intent") or event.intent)[:120]
            event.candidates.extend(new)
            event.error = ""
        except (LLMError, ValueError, KeyError) as e:
            event.error = f"换一批失败：{e}"
        self.log(event, kind="more")
        return event

    def refine(self, binding: ContactBinding, event: ReplyEvent, index: int, comment: str) -> Candidate:
        """给某个候选提意见 → 改写。多轮累积在 candidate.rounds 上。"""
        cand = event.candidates[index]
        cand.rounds.append({"comment": comment, "revised": cand.current, "ts": time.time()})
        if self.cfg.llm.provider == "offline":
            cand.reason = "离线模板模式无法真正改写；请在「模型」页配置后使用多轮修改。"
            self.log(event, kind="refine")
            return cand
        system, user = prompts.build_refine_prompt(
            binding.label, event.display_name, binding.note, event.incoming or Message(),
            event.context, cand.tone, cand.current, cand.rounds)
        try:
            revised = self.llm.chat(system, user, temperature=min(0.9, self.cfg.llm.temperature)).strip()
            revised = revised.strip('"“”').strip()
            if revised:
                cand.rounds[-1]["revised"] = revised
                cand.reason = f"已按你的意见修改（第 {len(cand.rounds)} 轮）"
                event.error = ""
        except LLMError as e:
            cand.rounds.pop()
            event.error = f"修改失败：{e}"
        self.log(event, kind="refine")
        return cand

    # ------------------------------------------------------------------ #
    def _parse_candidates(self, data: dict[str, Any]) -> list[Candidate]:
        out: list[Candidate] = []
        for item in (data.get("candidates") or [])[: max(1, self.cfg.max_candidates + 2)]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            risk = str(item.get("risk") or "低").strip()
            if risk not in ("低", "中", "高"):
                risk = "低" if risk in ("low", "LOW", "") else "中"
            out.append(Candidate(tone=str(item.get("tone") or "候选").strip()[:12],
                                 text=text, reason=str(item.get("reason") or "").strip(),
                                 risk=risk))
        return out

    @staticmethod
    def _offline_candidates(incoming: Message) -> list[Candidate]:
        quote = " ".join((incoming.content or "").split())
        quote = quote[:12] + ("…" if len(quote) > 12 else "")
        quote = quote or "你刚才说的"
        return [
            Candidate("稳妥", f"好的，关于「{quote}」我想一下，晚点回你。",
                      "不马上表态，给自己留时间；对方一般不会觉得被晾着。", "低"),
            Candidate("轻松", f"哈哈收到，{quote} 这个我得先琢磨琢磨～",
                      "用轻松口吻接住话题，避免显得有压力。", "低"),
            Candidate("直球", f"关于「{quote}」，我现在没法立刻答复，今天内给你准话。",
                      "直接给出答复时间，减少来回试探。", "中"),
            Candidate("简短", "收到，我看下。", "最省事的回应，适合暂时不想展开聊。", "低"),
        ]

    # ------------------------------------------------------------------ #
    def log(self, event: ReplyEvent, kind: str = "generate") -> None:
        try:
            path = self.cfg.history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": kind, **event.to_dict()}, ensure_ascii=False) + "\n")
        except OSError:
            pass
