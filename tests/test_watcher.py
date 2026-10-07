"""监控：首次不倒放历史、新消息触发一次、旧消息/自己的消息不触发、群也能触发。"""
import time

from make_demo_kb import GROUP, ZHANG, inject

from wxreply.config import ContactBinding
from wxreply.kb import KBReader
from wxreply.watcher import MessageWatcher


def _watcher(cfg, kb, fired):
    reader = KBReader(kb)
    w = MessageWatcher(cfg, lambda b, m, h: fired.append((b, m, h)), reader=reader)

    def on_event(binding, msg, history):
        fired.append((binding, msg, history))
    w.on_event = on_event
    return w


def test_first_pass_records_cursor_without_firing(cfg, demo_kb):
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    assert w.check_once() == []
    assert fired == []
    assert cfg.contacts[0].last_seen_ts == w.reader.latest_ts(ZHANG)


def test_new_incoming_message_fires_once(cfg, demo_kb):
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    w.check_once()                                  # 记录基线

    inject(demo_kb, ZHANG, "在吗，帮我看看这个材料")
    hits = w.check_once()
    assert len(hits) == 1
    binding, msg, history = hits[0]
    assert binding.label == "联系人 1"
    assert msg.content == "在吗，帮我看看这个材料"
    assert history == []                            # 本次没有我自己发的消息
    assert cfg.contacts[0].last_seen_ts == msg.create_time

    # 同一条不会重复触发
    assert w.check_once() == []
    assert len(fired) == 1


def test_my_own_message_does_not_fire(cfg, demo_kb):
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    w.check_once()
    inject(demo_kb, ZHANG, "我自己写的", sender=None, is_from_me=True)
    assert w.check_once() == []


def test_old_message_is_ignored(cfg, demo_kb):
    cfg.max_event_age_min = 15
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    w.check_once()
    inject(demo_kb, ZHANG, "很久以前的消息", when=int(time.time()) - 3600)
    assert w.check_once() == []
    # 但游标要往前走，避免下一轮又看到它
    assert w.reader.latest_ts(ZHANG) >= int(time.time()) - 3600


def test_group_binding_fires_with_sender_name(cfg, demo_kb):
    cfg.contacts = [ContactBinding(label="联系人 2", username=GROUP, enabled=True)]
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    w.check_once()
    inject(demo_kb, GROUP, "周六改成下午三点吧", sender=ZHANG)
    hits = w.check_once()
    assert len(hits) == 1
    assert hits[0][1].content == "周六改成下午三点吧"
    assert hits[0][1].sender_name == "张三"


def test_monitor_disabled_does_nothing(cfg, demo_kb):
    cfg.monitor_enabled = False
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    w.check_once()
    inject(demo_kb, ZHANG, "不该被看到")
    assert w.check_once() == []


def test_unknown_contact_is_reported_not_crashing(cfg, demo_kb):
    cfg.contacts = [ContactBinding(label="联系人 9", username="不存在的人", enabled=True)]
    fired = []
    w = _watcher(cfg, demo_kb, fired)
    assert w.check_once() == []
    assert w.status.errors
