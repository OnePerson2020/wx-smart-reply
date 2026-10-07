"""生成引擎：JSON 容错、多轮改写、换一批、失败兜底、记录落盘。"""
import json

import pytest

from make_demo_kb import ZHANG
from wxreply.engine import ReplyEngine
from wxreply.kb import KBReader
from wxreply.llm import LLMError

GOOD = """```json
{"intent": "对方怕材料误事，等你拍板",
 "candidates": [
  {"tone": "稳妥", "text": "等我确认下，没问题你就帮我交。", "reason": "先确认再交，避免出错。", "risk": "低"},
  {"tone": "轻松", "text": "哈哈别慌，我马上瞅一眼。", "reason": "语气轻松，安抚对方焦虑。", "risk": "低"},
  {"tone": "直球", "text": "先别交，我半小时内回你。", "reason": "给明确时间，对方不用猜。", "risk": "中"},
 ],}
```"""


@pytest.fixture()
def engine(cfg, demo_kb, monkeypatch):
    reader = KBReader(demo_kb)
    cfg.llm.provider = "openai"
    cfg.llm.base_url, cfg.llm.api_key, cfg.llm.model = "http://x/v1", "k", "m"
    eng = ReplyEngine(cfg, reader)
    return eng


def _last_incoming(reader):
    msgs = [m for m in reader.messages(ZHANG, limit=30) if not m.is_from_me]
    return msgs[-1]


def test_generate_parses_fenced_json_with_trailing_comma(engine, monkeypatch, cfg, demo_kb):
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: GOOD)
    binding = cfg.contacts[0]
    ev = engine.generate(binding, _last_incoming(engine.reader))
    assert ev.intent.startswith("对方怕材料")
    assert [c.tone for c in ev.candidates] == ["稳妥", "轻松", "直球"]
    assert all(c.reason for c in ev.candidates)
    assert ev.error == ""
    # 上下文与语气样例都进了提示词
    assert ev.context and ev.style_samples


def test_llm_failure_falls_back_to_offline_candidates(engine, cfg, monkeypatch):
    def boom(*a, **k):
        raise LLMError("429 too many requests")
    monkeypatch.setattr(engine.llm, "chat", boom)
    ev = engine.generate(cfg.contacts[0], _last_incoming(engine.reader))
    assert ev.candidates and "生成失败" in ev.error


def test_refine_keeps_rounds_and_updates_text(engine, cfg, monkeypatch):
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: GOOD)
    binding = cfg.contacts[0]
    ev = engine.generate(binding, _last_incoming(engine.reader))
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: "先别交哈，我看完就跟你说～\n对了，你周末有空没？")
    cand = engine.refine(binding, ev, 0, "别催她，轻松点，顺便问下周末")
    assert cand.rounds and len(cand.rounds) == 1
    assert cand.current.startswith("先别交哈")
    assert "第 1 轮" in cand.reason

    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: "先别交哈，看完回你！周末有空吗")
    engine.refine(binding, ev, 0, "再短一点")
    assert len(cand.rounds) == 2
    assert cand.current == "先别交哈，看完回你！周末有空吗"
    assert cand.rounds[0]["comment"] == "别催她，轻松点，顺便问下周末"     # 历史保留


def test_refine_failure_rolls_back_round(engine, cfg, monkeypatch):
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: GOOD)
    binding = cfg.contacts[0]
    ev = engine.generate(binding, _last_incoming(engine.reader))
    before = ev.candidates[0].current

    def boom(*a, **k):
        raise LLMError("502 bad gateway")
    monkeypatch.setattr(engine.llm, "chat", boom)
    cand = engine.refine(binding, ev, 0, "软一点")
    assert cand.rounds == []                     # 失败不留半截状态
    assert cand.current == before
    assert "修改失败" in ev.error


def test_more_appends_without_dropping_old(engine, cfg, monkeypatch):
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: GOOD)
    binding = cfg.contacts[0]
    ev = engine.generate(binding, _last_incoming(engine.reader))
    n = len(ev.candidates)
    engine.more(binding, ev)
    assert len(ev.candidates) == 2 * n


def test_offline_provider_is_honest(cfg, demo_kb, monkeypatch):
    cfg.llm.provider = "offline"
    eng = ReplyEngine(cfg, KBReader(demo_kb))
    ev = eng.generate(cfg.contacts[0], _last_incoming(eng.reader))

    def boom(*a, **k):
        raise AssertionError("离线模式不应该联网")
    monkeypatch.setattr(eng.llm, "chat", boom)
    assert len(ev.candidates) == 4
    assert "离线模板" in ev.error
    ev2 = ReplyEngine(cfg, KBReader(demo_kb)).generate(cfg.contacts[0], _last_incoming(eng.reader))
    assert ev2.candidates


def test_history_is_written(engine, cfg, monkeypatch):
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: GOOD)
    engine.generate(cfg.contacts[0], _last_incoming(engine.reader))
    lines = cfg.history_path().read_text(encoding="utf-8").strip().splitlines()
    assert lines
    ev = json.loads(lines[-1])
    assert ev["kind"] == "generate"
    assert ev["candidates"][0]["tone"] == "稳妥"
    assert ev["incoming"]["content"]


def test_context_includes_style_samples_from_me(engine, cfg, monkeypatch):
    monkeypatch.setattr(engine.llm, "chat", lambda *a, **k: GOOD)
    engine.generate(cfg.contacts[0], _last_incoming(engine.reader))
    _, style = engine.build_context(cfg.contacts[0], _last_incoming(engine.reader))
    assert style, "应该取到我自己历史消息作为语气样例"
    assert all(isinstance(s, str) and s.strip() for s in style)
