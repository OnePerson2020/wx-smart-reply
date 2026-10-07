"""轻量样式表（不依赖外部主题）。"""
from __future__ import annotations

ACCENT = "#2f6fed"
BG = "#ffffff"
TEXT = "#1b1f24"
MUTED = "#6b7280"
BORDER = "#e5e7eb"

QSS = f"""
QWidget {{ color: {TEXT}; font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif; font-size: 13px; }}
QMainWindow, QDialog {{ background: {BG}; }}
#Card {{ background: #fafbfc; border: 1px solid {BORDER}; border-radius: 10px; }}
#CardText {{ font-size: 14px; line-height: 20px; }}
#ToneBadge {{ background: {ACCENT}; color: white; border-radius: 8px; padding: 2px 8px; font-size: 12px; }}
#RiskLow {{ color: #047857; font-size: 12px; }}
#RiskMid {{ color: #b45309; font-size: 12px; }}
#RiskHigh {{ color: #b91c1c; font-size: 12px; }}
#Muted {{ color: {MUTED}; font-size: 12px; }}
#Title {{ font-size: 15px; font-weight: 600; }}
#Incoming {{ background: #eef2ff; border: 1px solid #dbe4ff; border-radius: 8px; padding: 8px; }}
QPushButton {{ background: #f3f4f6; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover {{ background: #e9ebef; }}
QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #2559c9; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: white; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; }}
QTabBar::tab {{ padding: 6px 14px; }}
QTabBar::tab:selected {{ color: {ACCENT}; font-weight: 600; }}
QTableWidget {{ gridline-color: {BORDER}; }}
QHeaderView::section {{ background: #f8f9fa; border: none; border-bottom: 1px solid {BORDER}; padding: 5px; }}
"""
