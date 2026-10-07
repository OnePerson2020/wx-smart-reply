"""主窗口：联系人设置 / 模型接入 / 监控设置 / 回复记录。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..config import AppConfig, ContactBinding, DEFAULT_TONES
from .theme import QSS


class ContactPickerDialog(QDialog):
    """从知识库里挑一个联系人 / 群。"""

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从知识库选择联系人")
        self.resize(460, 520)
        self.reader = reader
        self.picked: str = ""
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索备注 / 昵称 / wxid…")
        self.search.textChanged.connect(self._fill)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("确定")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        if not self.reader:
            return
        for c in self.reader.contacts(self.search.text(), include_groups=True)[:400]:
            tag = "群" if c.is_group else "人"
            item = QListWidgetItem(f"[{tag}] {c.display_name}    {c.username}")
            item.setData(Qt.UserRole, c.username)
            self.list.addItem(item)

    def accept(self) -> None:                                   # noqa: N802
        item = self.list.currentItem()
        if item:
            self.picked = item.data(Qt.UserRole)
        super().accept()


class MainWindow(QMainWindow):
    apply_requested = Signal()
    check_requested = Signal()
    reload_requested = Signal()
    test_llm_requested = Signal()

    def __init__(self, cfg: AppConfig, controller=None):
        super().__init__()
        self.cfg = cfg
        self.controller = controller
        self.setWindowTitle("微信辅助回复助手")
        self.resize(880, 620)
        self.setStyleSheet(QSS)

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_contacts_tab(), "联系人")
        self.tabs.addTab(self._build_model_tab(), "模型")
        self.tabs.addTab(self._build_monitor_tab(), "监控")
        self.tabs.addTab(self._build_history_tab(), "记录")
        lay.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        save = QPushButton("保存并应用")
        save.setObjectName("Primary")
        save.clicked.connect(self._apply)
        bottom.addWidget(save)
        lay.addLayout(bottom)

    # ================================================================ #
    # 联系人
    # ================================================================ #
    def _build_contacts_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        tip = QLabel("给每个联系人起个名字（联系人 1/2/3/4），填微信 ID 或备注名；"
                     "「额外说明」会写进提示词（例如：是我老板，要客气）。语气列留空＝用默认四档。")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.contact_table = QTableWidget(0, 5)
        self.contact_table.setHorizontalHeaderLabels(["启用", "名称", "微信ID/备注", "额外说明", "语气(逗号分隔)"])
        self.contact_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.contact_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.contact_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.contact_table, 1)

        row = QHBoxLayout()
        add = QPushButton("添加")
        add.clicked.connect(lambda: self._add_contact_row(ContactBinding(label=self._next_label())))
        row.addWidget(add)
        rm = QPushButton("删除选中")
        rm.clicked.connect(self._remove_contact_row)
        row.addWidget(rm)
        pick = QPushButton("从知识库选择…")
        pick.clicked.connect(self._pick_contact)
        row.addWidget(pick)
        test = QPushButton("测试：这些联系人能读到吗")
        test.clicked.connect(self._test_contacts)
        row.addWidget(test)
        row.addStretch(1)
        lay.addLayout(row)

        for c in self.cfg.contacts:
            self._add_contact_row(c)
        return w

    def _next_label(self) -> str:
        used = {c.label for c in self.cfg.contacts}
        i = 1
        while f"联系人 {i}" in used:
            i += 1
        return f"联系人 {i}"

    def _add_contact_row(self, binding: ContactBinding) -> None:
        r = self.contact_table.rowCount()
        self.contact_table.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chk.setCheckState(Qt.Checked if binding.enabled else Qt.Unchecked)
        self.contact_table.setItem(r, 0, chk)
        self.contact_table.setItem(r, 1, QTableWidgetItem(binding.label))
        self.contact_table.setItem(r, 2, QTableWidgetItem(binding.username))
        self.contact_table.setItem(r, 3, QTableWidgetItem(binding.note))
        tones = ", ".join(t.get("name", "") for t in binding.tones) if binding.tones else ""
        self.contact_table.setItem(r, 4, QTableWidgetItem(tones))

    def _remove_contact_row(self) -> None:
        rows = sorted({i.row() for i in self.contact_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.contact_table.removeRow(r)

    def _pick_contact(self) -> None:
        dlg = ContactPickerDialog(self.controller.reader if self.controller else None, self)
        if dlg.exec() == QDialog.Accepted and dlg.picked:
            r = self.contact_table.currentRow()
            if r < 0:
                self.contact_table.insertRow(0)
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Checked)
                self.contact_table.setItem(0, 0, chk)
                self.contact_table.setItem(0, 1, QTableWidgetItem(self._next_label()))
                r = 0
            self.contact_table.setItem(r, 2, QTableWidgetItem(dlg.picked))
            if self.controller and self.controller.reader:
                c = self.controller.reader.contact(dlg.picked)
                if c and not (self.contact_table.item(r, 3) or QTableWidgetItem("")).text():
                    self.contact_table.setItem(r, 3, QTableWidgetItem(""))

    def _collect_contacts(self) -> list[ContactBinding]:
        out: list[ContactBinding] = []
        for r in range(self.contact_table.rowCount()):
            def cell(col: int, row: int = r) -> str:     # row 绑默认参数，别闭包捕循环变量
                it = self.contact_table.item(row, col)
                return (it.text() if it else "").strip()

            enabled = bool(self.contact_table.item(r, 0)
                           and self.contact_table.item(r, 0).checkState() == Qt.Checked)
            username = cell(2)
            label = cell(1) or f"联系人 {r + 1}"
            if not username and not enabled:
                continue
            tones = [{"name": t.strip(), "hint": t.strip()}
                     for t in cell(4).replace("，", ",").split(",") if t.strip()]
            out.append(ContactBinding(label=label, username=username, enabled=enabled,
                                      note=cell(3), tones=tones,
                                      is_group=username.endswith("@chatroom")))
        # 保留已记住的进度
        old = {c.username: c for c in self.cfg.contacts}
        for c in out:
            if c.username in old:
                c.last_seen_ts = old[c.username].last_seen_ts
        return out or [ContactBinding()]

    def _test_contacts(self) -> None:
        if not (self.controller and self.controller.reader):
            QMessageBox.warning(self, "知识库未就绪", "先在「监控」页设置已解密数据库目录并保存。")
            return
        lines = []
        for b in self._collect_contacts():
            if not b.username:
                continue
            hit = self.controller.reader.resolve(b.username)
            if not hit:
                lines.append(f"❌ {b.label}: 找不到「{b.username}」")
            else:
                ts = self.controller.reader.latest_ts(hit.username)
                import time
                when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "无消息"
                lines.append(f"✅ {b.label}: {hit.display_name} ({hit.username}) 最近消息 {when}")
        QMessageBox.information(self, "联系人检查", "\n".join(lines) or "没有启用任何联系人")

    # ================================================================ #
    # 模型
    # ================================================================ #
    def _build_model_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.provider = QComboBox()
        self.provider.addItems(["offline（离线模板，不联网）", "openai（OpenAI 兼容接口）"])
        self.provider.setCurrentIndex(1 if self.cfg.llm.provider == "openai" else 0)
        form.addRow("提供方", self.provider)

        self.base_url = QLineEdit(self.cfg.llm.base_url)
        self.base_url.setPlaceholderText("https://ark.cn-beijing.volces.com/api/v3")
        form.addRow("Base URL", self.base_url)
        self.api_key = QLineEdit(self.cfg.llm.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        form.addRow("API Key", self.api_key)
        self.model = QLineEdit(self.cfg.llm.model)
        self.model.setPlaceholderText("例如 doubao-… / gpt-4o-mini / deepseek-chat / qwen2.5:7b")
        form.addRow("模型 ID", self.model)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 1.5)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(self.cfg.llm.temperature)
        form.addRow("温度", self.temperature)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(128, 16000)
        self.max_tokens.setSingleStep(256)
        self.max_tokens.setValue(self.cfg.llm.max_tokens)
        self.max_tokens.setToolTip("带思考过程的模型要给足预算，否则正文可能为空")
        form.addRow("最大输出 tokens", self.max_tokens)
        self.max_candidates = QSpinBox()
        self.max_candidates.setRange(1, 6)
        self.max_candidates.setValue(self.cfg.max_candidates)
        form.addRow("候选条数", self.max_candidates)
        self.context_messages = QSpinBox()
        self.context_messages.setRange(0, 200)
        self.context_messages.setValue(self.cfg.context_messages)
        form.addRow("历史上下文条数", self.context_messages)
        self.style_samples = QSpinBox()
        self.style_samples.setRange(0, 50)
        self.style_samples.setValue(self.cfg.style_samples)
        form.addRow("模仿语气样例条数", self.style_samples)

        test = QPushButton("测试连接（发一条中文测试消息）")
        test.clicked.connect(self.test_llm_requested.emit)
        form.addRow(test)
        hint = QLabel("离线模板模式：不联网也能跑通全部交互，但候选是固定模板；"
                      "接入任意 OpenAI 兼容接口（火山方舟 / OpenAI / DeepSeek / 本地 Ollama）即可真实生成。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    # ================================================================ #
    # 监控
    # ================================================================ #
    def _build_monitor_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.monitor_enabled = QCheckBox("开启实时监控（发现新消息自动弹窗）")
        self.monitor_enabled.setChecked(self.cfg.monitor_enabled)
        form.addRow(self.monitor_enabled)

        self.source_mode = QComboBox()
        self.source_mode.addItems(["dir（只用已解密目录，由你/其他工具负责刷新）",
                                   "decrypt（本程序用 keys.json 自动增量解密）"])
        self.source_mode.setCurrentIndex(1 if self.cfg.source_mode == "decrypt" else 0)
        self.source_mode.currentIndexChanged.connect(self._sync_mode_hint)
        form.addRow("数据来源", self.source_mode)

        self.decrypted_dir = self._path_row(form, "已解密数据库目录", self.cfg.decrypted_dir, pick_dir=True)
        self.wechat_dir = self._path_row(form, "微信数据目录(xwechat_files)", self.cfg.wechat_dir, pick_dir=True)
        self.keys_file = self._path_row(form, "keys.json", self.cfg.keys_file, pick_dir=False)

        self.poll_interval = QDoubleSpinBox()
        self.poll_interval.setRange(1.0, 120.0)
        self.poll_interval.setSingleStep(1.0)
        self.poll_interval.setValue(self.cfg.poll_interval_sec)
        form.addRow("轮询间隔（秒）", self.poll_interval)

        self.max_age = QSpinBox()
        self.max_age.setRange(1, 720)
        self.max_age.setValue(self.cfg.max_event_age_min)
        form.addRow("只提醒 N 分钟内的新消息", self.max_age)

        self.replay = QCheckBox("启动时为历史消息也弹窗（默认只提醒新的）")
        self.replay.setChecked(self.cfg.replay_on_start)
        form.addRow(self.replay)

        self.decrypt_wal = QCheckBox("尝试解密 -wal（更实时，失败会自动忽略）")
        self.decrypt_wal.setChecked(self.cfg.decrypt_wal)
        form.addRow(self.decrypt_wal)

        self.popup_corner = QComboBox()
        self.popup_corner.addItems(["bottom-right", "bottom-left", "top-right", "top-left"])
        self.popup_corner.setCurrentText(self.cfg.popup_corner)
        form.addRow("弹窗位置", self.popup_corner)

        row = QHBoxLayout()
        reload_btn = QPushButton("重新加载知识库")
        reload_btn.clicked.connect(self.reload_requested.emit)
        row.addWidget(reload_btn)
        check = QPushButton("立即检查一次")
        check.clicked.connect(self.check_requested.emit)
        row.addWidget(check)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(holder)
        self._sync_mode_hint()
        return w

    def _path_row(self, form: QFormLayout, label: str, value: str, pick_dir: bool) -> QLineEdit:
        edit = QLineEdit(value)
        btn = QPushButton("浏览…")

        def browse() -> None:
            if pick_dir:
                p = QFileDialog.getExistingDirectory(self, label, edit.text() or str(Path.home()))
            else:
                p, _ = QFileDialog.getOpenFileName(self, label, edit.text() or str(Path.home()),
                                                   "JSON (*.json)")
            if p:
                edit.setText(p)
        btn.clicked.connect(browse)
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(btn)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(label, holder)
        return edit

    def _sync_mode_hint(self) -> None:
        decrypt_mode = self.source_mode.currentIndex() == 1
        for widget in (self.wechat_dir, self.keys_file):
            widget.setEnabled(decrypt_mode)
        self.decrypt_wal.setEnabled(decrypt_mode)
        self.decrypted_dir.setEnabled(True)

    # ================================================================ #
    # 记录
    # ================================================================ #
    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.load_history)
        row.addWidget(refresh)
        copy_btn = QPushButton("复制选中候选")
        copy_btn.clicked.connect(self._copy_history)
        row.addWidget(copy_btn)
        hint = QLabel(f"文件：{self.cfg.history_path()}")
        hint.setObjectName("Muted")
        row.addWidget(hint, 1)
        lay.addLayout(row)

        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["时间", "对象", "对方消息", "候选（复制用）"])
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.history_table, 1)
        self._history_rows: list[list[str]] = []
        self.load_history()
        return w

    def load_history(self, limit: int = 100) -> None:
        self.history_table.setRowCount(0)
        self._history_rows = []
        path = self.cfg.history_path()
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        import time
        for line in reversed(lines):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            incoming = (ev.get("incoming") or {}).get("content", "")
            cands = ev.get("candidates") or []
            texts = "\n---\n".join(f"[{c.get('tone')}] {c.get('text')}" for c in cands)
            r = self.history_table.rowCount()
            self.history_table.insertRow(r)
            self.history_table.setItem(r, 0, QTableWidgetItem(
                time.strftime("%m-%d %H:%M", time.localtime(ev.get("ts", 0)))))
            self.history_table.setItem(r, 1, QTableWidgetItem(str(ev.get("contact", ""))))
            self.history_table.setItem(r, 2, QTableWidgetItem(_one_line(incoming)))
            self.history_table.setItem(r, 3, QTableWidgetItem(texts))
            self._history_rows.append([c.get("text", "") for c in cands])

    def _copy_history(self) -> None:
        rows = sorted({i.row() for i in self.history_table.selectedIndexes()})
        if not rows or rows[0] >= len(self._history_rows):
            return
        texts = self._history_rows[rows[0]]
        if texts:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(texts[0])
            self.status.setText("已复制该事件的第一条候选")

    # ================================================================ #
    def _apply(self) -> None:
        self.cfg.contacts = self._collect_contacts()
        self.cfg.llm.provider = "openai" if self.provider.currentIndex() == 1 else "offline"
        self.cfg.llm.base_url = self.base_url.text().strip()
        self.cfg.llm.api_key = self.api_key.text().strip()
        self.cfg.llm.model = self.model.text().strip()
        self.cfg.llm.temperature = self.temperature.value()
        self.cfg.llm.max_tokens = self.max_tokens.value()
        self.cfg.max_candidates = self.max_candidates.value()
        self.cfg.context_messages = self.context_messages.value()
        self.cfg.style_samples = self.style_samples.value()
        self.cfg.monitor_enabled = self.monitor_enabled.isChecked()
        self.cfg.source_mode = "decrypt" if self.source_mode.currentIndex() == 1 else "dir"
        self.cfg.decrypted_dir = self.decrypted_dir.text().strip()
        self.cfg.wechat_dir = self.wechat_dir.text().strip()
        self.cfg.keys_file = self.keys_file.text().strip()
        self.cfg.poll_interval_sec = self.poll_interval.value()
        self.cfg.max_event_age_min = self.max_age.value()
        self.cfg.replay_on_start = self.replay.isChecked()
        self.cfg.decrypt_wal = self.decrypt_wal.isChecked()
        self.cfg.popup_corner = self.popup_corner.currentText()
        self.cfg.save()
        if self.cfg.llm.provider == "openai" and not (self.cfg.llm.base_url and self.cfg.llm.model):
            QMessageBox.warning(
                self, "模型配置不完整",
                "选了 openai 但 Base URL 或模型 ID 是空的。\n"
                "现在保存也能用，但生成时会退回离线模板并在弹窗里提示原因。")
        self.apply_requested.emit()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def refresh_from_config(self) -> None:
        self.contact_table.setRowCount(0)
        for c in self.cfg.contacts:
            self._add_contact_row(c)
        self.monitor_enabled.setChecked(self.cfg.monitor_enabled)
        self.decrypted_dir.setText(self.cfg.decrypted_dir)
        self.wechat_dir.setText(self.cfg.wechat_dir)
        self.keys_file.setText(self.cfg.keys_file)


def _one_line(text: str, limit: int = 160) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


__all__ = ["MainWindow", "ContactPickerDialog", "DEFAULT_TONES"]
