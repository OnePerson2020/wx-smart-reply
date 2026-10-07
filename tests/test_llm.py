"""模型客户端：返回值解析、JSON 兜底、参数兼容降级、错误处理。"""
import httpx
import pytest

from wxreply.config import LLMConfig
from wxreply.llm import ChatLLM, LLMError, extract_json


class _Resp:
    def __init__(self, status, payload, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else str(payload)

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")


class _Client:
    """按顺序返回预设响应，并记录请求体。"""

    calls: list = []

    def __init__(self, **kw):          # httpx.Client(timeout=...) 只带关键字
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        import copy
        # 深拷贝：调用方会原地改 payload 做兼容降级，不能把后来的改动记到首次请求上
        _Client.calls.append({"url": url, "json": copy.deepcopy(json),
                              "headers": dict(headers or {})})
        return _Client._responses.pop(0)


def _patch_httpx(monkeypatch, responses):
    _Client.calls = []
    _Client._responses = responses
    monkeypatch.setattr(httpx, "Client", _Client)


def _cfg(**kw):
    base = dict(provider="openai", base_url="http://x/v1", api_key="k", model="m")
    base.update(kw)
    return LLMConfig(**base)


def test_url_join_and_bearer(monkeypatch):
    _patch_httpx(monkeypatch, [_Resp(200, {"choices": [{"message": {"content": "好"}}]})])
    assert ChatLLM(_cfg()).chat("s", "u") == "好"
    call = _Client.calls[0]
    assert call["url"] == "http://x/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["messages"][0]["role"] == "system"


def test_base_url_already_ends_with_endpoint(monkeypatch):
    _patch_httpx(monkeypatch, [_Resp(200, {"choices": [{"message": {"content": "ok"}}]})])
    ChatLLM(_cfg(base_url="http://x/v1/chat/completions")).chat("s", "u")
    assert _Client.calls[0]["url"] == "http://x/v1/chat/completions"


def test_local_gateway_does_not_need_api_key(monkeypatch):
    _patch_httpx(monkeypatch, [_Resp(200, {"choices": [{"message": {"content": "ok"}}]})])
    ChatLLM(_cfg(base_url="http://127.0.0.1:8321/v1", api_key="")).chat("s", "u")
    assert "Authorization" not in _Client.calls[0]["headers"]


def test_remote_gateway_requires_api_key():
    with pytest.raises(LLMError):
        ChatLLM(_cfg(api_key="")).chat("s", "u")


def test_drops_temperature_when_model_rejects_it(monkeypatch):
    _patch_httpx(monkeypatch, [
        _Resp(400, None, text="Unsupported parameter: 'temperature' is unsupported"),
        _Resp(200, {"choices": [{"message": {"content": "降级成功"}}]}),
    ])
    assert ChatLLM(_cfg()).chat("s", "u") == "降级成功"
    assert "temperature" not in _Client.calls[1]["json"]
    assert "temperature" in _Client.calls[0]["json"]


def test_renames_max_tokens_when_rejected(monkeypatch):
    _patch_httpx(monkeypatch, [
        _Resp(400, None, text="Unsupported parameter: max_tokens, use max_completion_tokens"),
        _Resp(200, {"choices": [{"message": {"content": "ok"}}]}),
    ])
    LLMClient = ChatLLM(_cfg())
    assert LLMClient.chat("s", "u") == "ok"
    assert "max_completion_tokens" in _Client.calls[1]["json"]
    assert "max_tokens" not in _Client.calls[1]["json"]


def test_error_message_is_surfaced(monkeypatch):
    _patch_httpx(monkeypatch, [_Resp(401, None, text="invalid api key")] + [_Resp(401, None, text="invalid api key")] * 3)
    with pytest.raises(LLMError) as e:
        ChatLLM(_cfg()).chat("s", "u")
    assert "401" in str(e.value) or "invalid api key" in str(e.value)


def test_empty_content_raises(monkeypatch):
    _patch_httpx(monkeypatch, [_Resp(200, {"choices": [{"message": {"content": ""}}]})])
    with pytest.raises(LLMError):
        ChatLLM(_cfg()).chat("s", "u")


def test_content_array_shape(monkeypatch):
    _patch_httpx(monkeypatch, [_Resp(200, {"choices": [{"message": {"content": [
        {"type": "text", "text": "分片"}, {"type": "text", "text": "内容"}]}}]})])
    assert ChatLLM(_cfg()).chat("s", "u") == "分片内容"


# ---------------------------------------------------------------- extract_json
def test_extract_json_plain():
    assert extract_json('{"a": 1}')["a"] == 1


def test_extract_json_fenced_and_noisy():
    text = "好的，这是结果：\n```json\n{\"intent\": \"x\", \"candidates\": [{\"text\": \"y\"},]}\n```\n希望有帮助"
    data = extract_json(text)
    assert data["intent"] == "x"
    assert data["candidates"][0]["text"] == "y"


def test_extract_json_without_json_raises():
    with pytest.raises(LLMError):
        extract_json("我没有 JSON")
