"""新消息弹窗：提示 + 候选卡片 + 复制 + 多轮「提意见改写」+ 最终复制。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..config import AppConfig
from ..engine import Candidate, ReplyEvent
from ..ui.theme import QSS


def copy_to_clipboard(text: str) -> None:
    QGuiApplication.clipboard().setText(text or "")


class CandidateCard(QFrame):
    refine_requested = Signal(int, str)     # index, comment
    copy_requested = Signal(int)
    focus_requested = Signal(int)

    def __init__(self, index: int, cand: Candidate, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.index = index
        self._cand = cand
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        badge = QLabel(cand.tone)
        badge.setObjectName("ToneBadge")
        badge.setFixedHeight(20)
        head.addWidget(badge)
        risk = QLabel(f"踩雷风险：{cand.risk}")
        risk.setObjectName("RiskHigh" if cand.risk == "高" else
                           "RiskMid" if cand.risk == "中" else "RiskLow")
        head.addWidget(risk)
        head.addStretch(1)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setToolTip("复制这条回复到剪贴板")
        self.copy_btn.clicked.connect(lambda: self.copy_requested.emit(self.index))
        head.addWidget(self.copy_btn)
        self.revise_btn = QPushButton("提意见改写")
        self.revise_btn.clicked.connect(self._toggle_comment)
        head.addWidget(self.revise_btn)
        lay.addLayout(head)

        self.text_label = QLabel(cand.current)
        self.text_label.setObjectName("CardText")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.text_label)

        self.reason_label = QLabel(f"理由：{cand.reason or '（无）'}")
        self.reason_label.setObjectName("Muted")
        self.reason_label.setWordWrap(True)
        lay.addWidget(self.reason_label)

        self.rounds_label = QLabel("")
        self.rounds_label.setObjectName("Muted")
        self.rounds_label.setWordWrap(True)
        self.rounds_label.hide()
        lay.addWidget(self.rounds_label)

        self.comment_row = QWidget()
        row = QHBoxLayout(self.comment_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("写你觉得哪里不好 / 想怎么改，例如：太硬了，软一点；加一句约时间")
        self.comment_edit.returnPressed.connect(self._send_comment)
        row.addWidget(self.comment_edit, 1)
        send = QPushButton("发送意见")
        send.setObjectName("Primary")
        send.clicked.connect(self._send_comment)
        row.addWidget(send)
        self.comment_row.hide()
        self._comment_open = False
        lay.addWidget(self.comment_row)

    # ---------------------------------------------------------------- #
    def _toggle_comment(self) -> None:
        show = not self._comment_open
        self._comment_open = show
        self.comment_row.setVisible(show)
        self.revise_btn.setText("收起" if show else "提意见改写")
        if show:
            self.comment_edit.setFocus()
            self.focus_requested.emit(self.index)

    def _send_comment(self) -> None:
        text = self.comment_edit.text().strip()
        if not text:
            return
        self.comment_edit.clear()
        self.refine_requested.emit(self.index, text)

    # ---------------------------------------------------------------- #
    def set_busy(self, busy: bool) -> None:
        self.copy_btn.setEnabled(not busy)
        self.revise_btn.setEnabled(not busy)

    def refresh(self, cand: Candidate) -> None:
        self._cand = cand
        self.text_label.setText(cand.current)
        self.reason_label.setText(f"理由：{cand.reason or '（无）'}")
        if cand.rounds:
            lines = [f"第{i + 1}轮 你的意见：{r['comment']}" for i, r in enumerate(cand.rounds)]
            self.rounds_label.setText("\n".join(lines))
            self.rounds_label.show()
            self.setVisible(True)


class PopupWindow(QWidget):
    """收到消息时弹出的半自动回复面板。"""

    refine_requested = Signal(object, int, str)     # event, index, comment
    more_requested = Signal(object)                 # event
    closed = Signal(object)

    def __init__(self, event: ReplyEvent, cfg: AppConfig, parent=None):
        super().__init__(None)
        self.reply_event = event
        self.cfg = cfg
        self.cards: list[CandidateCard] = []
        self._selected = 0

        self.setWindowTitle(f"{event.contact_label} 新消息")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setStyleSheet(QSS)
        self.setMinimumWidth(430)
        self.setMaximumWidth(560)
        self._build()
        self._place()

    # ---------------------------------------------------------------- #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel(f"📩 {self.reply_event.contact_label} 发来消息")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        close = QPushButton("✕")
        close.setFixedWidth(28)
        close.clicked.connect(self.close)
        head.addWidget(close)
        root.addLayout(head)

        incoming = QLabel(f"{self.reply_event.display_name}：{_brief(self.reply_event.incoming.content if self.reply_event.incoming else '', 160)}")
        incoming.setObjectName("Incoming")
        incoming.setWordWrap(True)
        self.incoming_label = incoming
        root.addWidget(incoming)

        # 状态行：意图 / 错误提示 / 进度
        self.status = QLabel(self.reply_event.error or (f"对方意图：{self.reply_event.intent}" if self.reply_event.intent else ""))
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(140)      # 不写死上限，理由/改写历史都要能完整看到
        container = QWidget()
        self.cards_layout = QVBoxLayout(container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll)
        self._scroll = scroll
        self._container = container

        for i, cand in enumerate(self.reply_event.candidates):
            self._add_card(i, cand)

        foot = QHBoxLayout()
        self.more_btn = QPushButton("换一批")
        self.more_btn.clicked.connect(lambda: self.more_requested.emit(self.reply_event))
        foot.addWidget(self.more_btn)
        foot.addStretch(1)
        self.final_btn = QPushButton("复制我给对方发的最终回复")
        self.final_btn.setObjectName("Primary")
        self.final_btn.clicked.connect(self._copy_final)
        foot.addWidget(self.final_btn)
        root.addLayout(foot)

        self.toast = QLabel("")
        self.toast.setObjectName("Muted")
        root.addWidget(self.toast)

    def _add_card(self, index: int, cand: Candidate) -> None:
        card = CandidateCard(index, cand)
        card.copy_requested.connect(self._copy_candidate)
        card.refine_requested.connect(self._on_refine)
        card.focus_requested.connect(lambda i: setattr(self, "_selected", i))
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.cards.append(card)

    # ---------------------------------------------------------------- #
    def _copy_candidate(self, index: int) -> None:
        self._selected = index
        copy_to_clipboard(self.reply_event.candidates[index].current)
        self._flash(f"已复制第 {index + 1} 条（{self.reply_event.candidates[index].tone}），粘贴即可发送")

    def _copy_final(self) -> None:
        if not self.reply_event.candidates:
            return
        text = self.reply_event.candidates[self._selected].current
        copy_to_clipboard(text)
        self._flash("最终回复已复制到剪贴板，去微信里粘贴发送吧")

    def _on_refine(self, index: int, comment: str) -> None:
        self._selected = index
        card = self.cards[index]
        card.set_busy(True)
        self._flash(f"正在按你的意见改写第 {index + 1} 条…")
        self.refine_requested.emit(self.reply_event, index, comment)
        QTimer.singleShot(30000, lambda: card.set_busy(False))   # 兜底解锁

    def apply_refine(self, index: int, cand: Candidate) -> None:
        if 0 <= index < len(self.cards):
            self.cards[index].refresh(cand)
            self.cards[index].set_busy(False)
        self.status.setText(self.reply_event.error or f"对方意图：{self.reply_event.intent}")
        self._flash("已更新，可以继续提意见或复制")
        self._fit()

    def reset_from_event(self, event: ReplyEvent) -> None:
        """占位弹窗 → 生成完成后填充候选。"""
        self.reply_event = event
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        self._selected = 0
        if event.incoming is not None:
            self.incoming_label.setText(
                f"{event.display_name}：{_brief(event.incoming.content, 160)}")
        self.status.setText(event.error or (f"对方意图：{event.intent}" if event.intent else
                                            "没有生成候选，可以点「换一批」重试"))
        for i, cand in enumerate(event.candidates):
            self._add_card(i, cand)
        self.set_busy_more(False)
        self._flash("复制任意一条即可去微信粘贴发送")
        self._fit()

    def add_candidates(self, cands: list[Candidate]) -> None:
        start = len(self.cards)
        for i, cand in enumerate(cands):
            self._add_card(start + i, cand)
        self._flash(f"又补了 {len(cands)} 条新角度")
        self._fit()

    def set_busy_more(self, busy: bool) -> None:
        self.more_btn.setEnabled(not busy)
        self.toast.setText("正在换一批…" if busy else self.toast.text())

    def _flash(self, text: str) -> None:
        self.toast.setText(text)

    # ---------------------------------------------------------------- #
    def _fit(self) -> None:
        """内容变化后重新贴合尺寸：候选变多 / 文本变长也要能看全。"""
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen is not None:
            cap = int(screen.availableGeometry().height() * 0.85)
            if self.height() > cap:
                self.resize(self.width(), cap)
        self._place()

    def _place(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        if self.height() < 200:
            self.adjustSize()
        w, h = self.width(), self.height()
        margin = 16
        corner = (self.cfg.popup_corner or "bottom-right").lower()
        x = geo.left() + (margin if "left" in corner else geo.width() - w - margin)
        y = geo.top() + (margin if "top" in corner else geo.height() - h - margin)
        self.move(max(geo.left(), x), max(geo.top(), y))

    def closeEvent(self, e):                                   # noqa: N802
        self.closed.emit(self.reply_event)
        super().closeEvent(e)

    def keyPressEvent(self, e):                                # noqa: N802
        if e.key() == Qt.Key_Escape:
            self.close()


def _brief(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
