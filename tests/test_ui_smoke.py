"""界面联调：离屏跑真实弹窗，点真实按钮（复制 / 提意见改写 / 换一批），并留下截图。

这是"UI 真的能点"的证据，不是 mock：走的是 Controller → 弹窗 → 剪贴板 的完整链路。
"""
import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication                                   # noqa: E402

from make_demo_kb import LI, ZHANG, inject                                    # noqa: E402
from wxreply.config import AppConfig, ContactBinding, LLMConfig               # noqa: E402
from wxreply.ui.app import Controller                                          # noqa: E402

SHOT_DIR = Path(__file__).resolve().parents[1] / "docs" / "evidence"

REVISED = "先别交哈，我看完就跟你说～\n对了，你周末有空没？"
GOOD_JSON = ('{"intent": "对方怕材料误事", "candidates": ['
             '{"tone": "稳妥", "text": "等我确认下，没问题你就帮我交。", "reason": "先确认再交，避免出错。", "risk": "低"},'
             '{"tone": "轻松", "text": "哈哈别慌，我马上瞅一眼。", "reason": "安抚对方焦虑。", "risk": "低"},'
             '{"tone": "直球", "text": "先别交，我半小时内回你。", "reason": "给明确时间。", "risk": "中"}]}')


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def ui(app, tmp_path, demo_kb, monkeypatch):
    cfg = AppConfig(data_dir=str(tmp_path / "data"), decrypted_dir=str(demo_kb),
                    monitor_enabled=False)
    cfg.llm = LLMConfig(provider="openai", base_url="http://x/v1", api_key="k", model="m")
    cfg.contacts = [ContactBinding(label="联系人 1", username=ZHANG, enabled=True, note="同学")]
    ctrl = Controller(cfg, app)
    ctrl.apply_settings()                      # 正常启动时这一步在 Controller.start() 里做
    calls = {"n": 0}

    def fake_chat(system, user, **kw):
        calls["n"] += 1
        # 改写请求返回正文；生成/换一批请求返回 JSON
        return REVISED if "请输出修改后的回复正文" in user else GOOD_JSON
    monkeypatch.setattr(ctrl.engine.llm, "chat", fake_chat)
    yield ctrl, calls
    ctrl.watcher.stop() if ctrl.watcher else None


def _pump(ctrl, app, seconds=5.0, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        ctrl._drain()
        if until and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def _show_popup(ctrl, app, text="在吗，那个报名的材料我先帮你交上去还是等你确认一下？"):
    binding = ctrl.cfg.contacts[0]
    inject(ctrl.cfg.decrypted_dir, ZHANG, text)
    msg = [m for m in ctrl.reader.messages(ZHANG, limit=3) if not m.is_from_me][-1]
    ctrl._inbox.put((binding, msg))
    assert _pump(ctrl, app, 8.0, lambda: bool(ctrl.popups and ctrl.popups[-1].cards)), "弹窗没有出候选"
    return ctrl.popups[-1]


def test_incoming_message_pops_up_with_candidates(ui, app):
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    tones = [cand.tone for cand in popup.reply_event.candidates]
    assert tones[:3] == ["稳妥", "轻松", "直球"]
    assert popup.cards[0].text_label.text() == "等我确认下，没问题你就帮我交。"
    assert "理由" in popup.cards[0].reason_label.text()
    assert not popup.isHidden()


def test_copy_button_puts_text_in_clipboard(ui, app):
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    popup.cards[1].copy_btn.click()
    app.processEvents()
    assert QApplication.clipboard().text() == "哈哈别慌，我马上瞅一眼。"
    assert "已复制" in popup.toast.text()


def test_comment_round_refines_text_and_keeps_history(ui, app):
    ctrl, calls = ui
    popup = _show_popup(ctrl, app)
    card = popup.cards[0]
    card.revise_btn.click()                                # 展开意见框
    assert not card.comment_row.isHidden()
    card.comment_edit.setText("别催她，轻松一点，顺便问下她周末有没有空")
    card._send_comment()
    assert _pump(ctrl, app, 8.0, lambda: card.text_label.text() == REVISED), card.text_label.text()
    assert "第1轮" in card.rounds_label.text()
    assert "第 1 轮" in card.reason_label.text() or "第 1 轮" in card.reason_label.text()

    # 第二轮：历史要保留
    card.comment_edit.setText("再短一点")
    card._send_comment()
    assert _pump(ctrl, app, 8.0, lambda: len(card._cand.rounds) == 2)
    assert len(card._cand.rounds) == 2
    assert calls["n"] >= 3


def test_final_copy_uses_refined_text(ui, app):
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    card = popup.cards[0]
    card.revise_btn.click()
    card.comment_edit.setText("轻松一点")
    card._send_comment()
    _pump(ctrl, app, 8.0, lambda: card.text_label.text() == REVISED)
    popup.final_btn.click()
    app.processEvents()
    assert QApplication.clipboard().text() == REVISED
    assert "最终回复已复制" in popup.toast.text()


def test_more_button_appends_candidates(ui, app):
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    before = len(popup.cards)
    popup.more_btn.click()
    assert _pump(ctrl, app, 8.0, lambda: len(popup.cards) > before)
    assert len(popup.cards) > before


def test_offline_mode_popup_still_works(app, tmp_path, demo_kb):
    cfg = AppConfig(data_dir=str(tmp_path / "d2"), decrypted_dir=str(demo_kb), monitor_enabled=False)
    cfg.llm = LLMConfig(provider="offline")
    cfg.contacts = [ContactBinding(label="联系人 1", username=ZHANG, enabled=True)]
    ctrl = Controller(cfg, app)
    ctrl.apply_settings()
    popup = _show_popup(ctrl, app, "有空帮我看一下这个吗")
    assert len(popup.cards) == 4
    assert "离线模板模式" in popup.status.text()
    # 离线模式下提意见不能崩
    popup.cards[0].revise_btn.click()
    popup.cards[0].comment_edit.setText("短一点")
    popup.cards[0]._send_comment()
    _pump(ctrl, app, 5.0, lambda: "离线模板模式无法真正改写" in popup.cards[0].reason_label.text())
    assert "离线模板模式无法真正改写" in popup.cards[0].reason_label.text()


def test_screenshots_are_saved(ui, app):
    """留下截图，便于人工确认界面长什么样。"""
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    card = popup.cards[0]
    card.revise_btn.click()
    card.comment_edit.setText("别催她，轻松一点，顺便问下周末")
    card._send_comment()
    _pump(ctrl, app, 8.0, lambda: card.text_label.text() == REVISED)
    card.comment_edit.setText("再短一点")
    card._send_comment()
    _pump(ctrl, app, 8.0, lambda: len(card._cand.rounds) == 2)
    _pump(ctrl, app, 1.0)
    # 理由和改写历史必须真的在界面上，不是只在内存里
    assert not card.reason_label.isHidden() and card.reason_label.text().startswith("理由：")
    assert not card.rounds_label.isHidden() and "第1轮" in card.rounds_label.text()
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    popup.adjustSize()
    app.processEvents()
    assert popup.grab().save(str(SHOT_DIR / "popup.png"))
    ctrl.window.show()
    app.processEvents()
    assert ctrl.window.grab().save(str(SHOT_DIR / "main_window.png"))
    assert (SHOT_DIR / "popup.png").stat().st_size > 5000
    assert (SHOT_DIR / "main_window.png").stat().st_size > 5000


# --------------------------------------------------------------------------- #
# 主窗口：四个页签、联系人收集、从知识库选人、记录页
# --------------------------------------------------------------------------- #
def test_main_window_tabs_and_contact_collection(ui, app):
    ctrl, _ = ui
    win = ctrl.window
    win.show()
    app.processEvents()
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["联系人", "模型", "监控", "记录"]

    # 四个页签都切一遍，不应抛异常
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()

    # 表格里勾选状态/名称/备注能被正确收集成绑定
    bindings = win._collect_contacts()
    assert len(bindings) == 1
    assert bindings[0].username == ZHANG
    assert bindings[0].label == "联系人 1"
    assert bindings[0].note == "同学"
    assert bindings[0].enabled


def test_main_window_history_tab_reads_records(ui, app):
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    assert popup.reply_event.candidates
    ctrl.window.load_history()
    app.processEvents()
    assert ctrl.window.history_table.rowCount() >= 1
    assert ctrl.window.history_table.item(0, 1).text() == "联系人 1"


def test_contact_picker_lists_kb_contacts(ui, app):
    from wxreply.ui.main_window import ContactPickerDialog
    ctrl, _ = ui
    dlg = ContactPickerDialog(ctrl.reader, ctrl.window)
    assert dlg.list.count() >= 4
    texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert any("张三" in t for t in texts)
    dlg.search.setText("李四")
    app.processEvents()
    assert dlg.list.count() == 1
    dlg.list.setCurrentRow(0)
    dlg.accept()
    assert dlg.picked == LI


def test_full_threaded_pipeline_pops_up_without_manual_injection(app, tmp_path, demo_kb, monkeypatch):
    """真跑监控线程 + QTimer：注入一条新消息后，不需要任何手动喂数据也要弹窗。"""
    from wxreply.config import AppConfig as Cfg

    cfg = Cfg(data_dir=str(tmp_path / "d3"), decrypted_dir=str(demo_kb),
              monitor_enabled=True, poll_interval_sec=0.5, max_event_age_min=15)
    cfg.llm = LLMConfig(provider="openai", base_url="http://x/v1", api_key="k", model="m")
    cfg.contacts = [ContactBinding(label="联系人 1", username=ZHANG, enabled=True)]
    ctrl = Controller(cfg, app)
    ctrl.apply_settings()                       # monitor_enabled=True → 监控线程真的起来了
    # 注意：apply_settings 会重建 Engine，所以补丁要打在重建之后
    monkeypatch.setattr(ctrl.engine.llm, "chat",
                        lambda *a, **k: '{"intent":"催你拍板","candidates":[{"tone":"稳妥","text":"我确认下再回你。","reason":"稳","risk":"低"}]}')
    try:
        # 等第一轮把游标垫好（不弹历史消息）
        end = time.time() + 8
        while time.time() < end and cfg.contacts[0].last_seen_ts == 0:
            app.processEvents()
            ctrl._drain()
            time.sleep(0.02)
        assert cfg.contacts[0].last_seen_ts > 0, "监控线程没有跑起来"
        assert ctrl.popups == [], "首次不应该为历史消息弹窗"

        assert ctrl.watcher.status.running
        inject(demo_kb, ZHANG, "这个材料到底先交还是等确认呀")
        end = time.time() + 15
        while time.time() < end and not (ctrl.popups and ctrl.popups[-1].cards):
            app.processEvents()                 # QTimer 在 processEvents 里触发
            time.sleep(0.02)
        assert ctrl.popups, "监控线程发现新消息后应该自动弹窗"
        popup = ctrl.popups[-1]
        assert popup.reply_event.incoming.content == "这个材料到底先交还是等确认呀"
        assert popup.cards and popup.cards[0].text_label.text() == "我确认下再回你。"
        # 游标已推进，落盘后重启不会重复提醒
        assert cfg.contacts[0].last_seen_ts == popup.reply_event.incoming.create_time
        assert json.loads(cfg.contacts_path().read_text(encoding="utf-8"))["contacts"][0]["last_seen_ts"] \
            == cfg.contacts[0].last_seen_ts
    finally:
        ctrl.watcher.stop()


def test_closing_popup_is_clean(ui, app):
    """关掉弹窗不能抛异常，也不能把它留在 popups 里（否则会一直占内存）。"""
    ctrl, _ = ui
    popup = _show_popup(ctrl, app)
    assert popup in ctrl.popups
    popup.close()
    app.processEvents()
    ctrl._on_popup_closed(popup.reply_event)        # 直接调用，异常会真的冒出来
    assert ctrl.popups == []
