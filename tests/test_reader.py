"""知识库读取：联系人解析、方向判断、群消息、增量查询。"""
from make_demo_kb import GROUP, LI, ME, ZHANG, inject


def test_stats_and_self(reader):
    st = reader.stats()
    assert st["self_username"] == ME
    assert st["chats"] == 3
    assert st["contacts"] >= 4


def test_resolve_by_remark_nickname_and_group(reader):
    assert reader.resolve("张三").username == ZHANG
    assert reader.resolve("李四").username == LI
    assert reader.resolve("大学同学群").username == GROUP
    assert reader.resolve("wxid_demo_zhang").username == ZHANG
    assert reader.resolve("不存在的人") is None


def test_direction_and_names(reader):
    msgs = reader.messages(ZHANG, limit=20)
    assert len(msgs) == 9
    assert msgs[-1].is_from_me is False
    assert msgs[-1].sender_name == "张三"
    assert msgs[-1].is_text
    mine = [m for m in msgs if m.is_from_me]
    assert mine and all(m.sender_name == "我" for m in mine)
    # 时间升序
    assert [m.create_time for m in msgs] == sorted(m.create_time for m in msgs)


def test_group_sender_prefix_is_stripped(reader):
    msgs = reader.messages(GROUP, limit=20)
    assert len(msgs) == 4
    named = {m.sender_name for m in msgs if not m.is_from_me}
    assert named == {"张三", "李四"}
    assert all(":\n" not in m.content for m in msgs)          # 前缀已剥掉
    assert [m.content for m in msgs][0] == "周五晚上聚一下？"


def test_new_messages_and_latest(reader):
    latest = reader.latest_ts(ZHANG)
    assert latest > 0
    assert reader.new_messages(ZHANG, latest) == []
    older = reader.messages(ZHANG, limit=20)[0].create_time
    got = reader.new_messages(ZHANG, older)
    assert len(got) == 8
    assert all(m.create_time > older for m in got)


def test_messages_before_ts_for_context(reader, demo_kb):
    msgs = reader.messages(ZHANG, limit=5)
    incoming = msgs[-1]
    ctx = reader.messages(ZHANG, limit=20, before_ts=incoming.create_time - 1)
    assert ctx
    assert all(m.create_time < incoming.create_time for m in ctx)
    assert ctx[-1].content == "嗯嗯，谢啦"


def test_text_only_filter(reader, demo_kb):
    import sqlite3
    from pathlib import Path
    db = Path(demo_kb) / "message/message_0.db"
    conn = sqlite3.connect(db)
    conn.execute("insert into Msg_" + __import__("hashlib").md5(ZHANG.encode()).hexdigest() +
                 "(local_type,create_time,real_sender_id,message_content) values(3,1,2,'[图片]')")
    conn.commit()
    conn.close()
    from wxreply.kb import KBReader
    r = KBReader(demo_kb)
    assert all(m.is_text for m in r.messages(ZHANG, limit=30, text_only=True))


def test_inject_then_visible(reader, demo_kb):
    inject(demo_kb, ZHANG, "刚看到你消息，我这就确认一下")
    msgs = reader.messages(ZHANG, limit=20)
    assert msgs[-1].content == "刚看到你消息，我这就确认一下"
    assert msgs[-1].is_from_me is False
