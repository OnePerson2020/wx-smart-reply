"""界面层：主窗口 + 新消息弹窗。"""
from .app import Controller, make_icon, run_gui
from .main_window import MainWindow
from .popup import PopupWindow

__all__ = ["Controller", "make_icon", "run_gui", "MainWindow", "PopupWindow"]
