"""OpenAI 兼容的 chat/completions 客户端（可对接火山方舟 / OpenAI / DeepSeek / Ollama 等）。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    raw: dict[str, Any] | None = None


class ChatLLM:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    # ---------------------------------------------------------------- #
    def _url(self) -> str:
        base = (self.cfg.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def chat(self, system: str, user: str, temperature: float | None = None,
             max_tokens: int | None = None) -> str:
        if self.cfg.provider == "offline":
            raise LLMError("offline 模式不联网")
        if not (self.cfg.model and self.cfg.base_url):
            raise LLMError("模型配置不完整：需要 base_url 和 model")
        if self.cfg.needs_api_key and not self.cfg.api_key:
            raise LLMError("模型配置不完整：非本地地址需要 API Key")

        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        last_err: Exception | None = None
        for attempt in range(4):
            try:
                with httpx.Client(timeout=self.cfg.timeout_sec) as client:
                    resp = client.post(self._url(), json=payload, headers=headers)
            except httpx.HTTPError as e:
                last_err = e
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                raise LLMError(f"请求失败：{e}") from e
            if resp.status_code < 400:
                try:
                    return _extract_text(resp.json())
                except ValueError as e:
                    raise LLMError(f"返回体不是 JSON：{resp.text[:160]}") from e

            body = resp.text[:300]
            # 兼容性降级：部分新模型不接受 temperature / max_tokens 这两个参数名
            if resp.status_code == 400 and "temperature" in body and "temperature" in payload:
                payload.pop("temperature")
                continue
            if resp.status_code == 400 and "max_tokens" in body and "max_tokens" in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
                continue
            last_err = LLMError(f"HTTP {resp.status_code}: {body}")
            if resp.status_code in (408, 409, 429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err
        raise LLMError(f"请求失败：{last_err}")


def _extract_text(data: dict) -> str:
    try:
        choices = data.get("choices") or []
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):          # 某些实现返回分片数组
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if content:
            return str(content)
        if choices[0].get("text"):
            return str(choices[0]["text"])
    except (AttributeError, IndexError, TypeError):
        pass
    raise LLMError(f"返回体无法解析：{json.dumps(data, ensure_ascii=False)[:200]}")


def extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON 对象（容忍代码块、前后废话、尾逗号）。"""
    if not text:
        raise LLMError("模型返回为空")
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if len(s.split("```")) > 1 else s
        s = s[4:] if s.lower().startswith("json") else s
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"模型没有返回 JSON：{s[:160]}")
    raw = s[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        return json.loads(cleaned)
