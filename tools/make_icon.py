"""生成应用图标（不依赖外部图片素材）：resources/app.ico / app.png

    python tools/make_icon.py
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt                        # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap         # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "resources"


def draw(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = size * 0.18
    p.setBrush(QColor("#2f6fed"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, r, r)
    p.setPen(QColor("white"))
    f = p.font()
    f.setPointSizeF(size * 0.5)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), int(Qt.AlignCenter), "回")
    p.end()
    return pm


def main() -> int:
    QApplication.instance() or QApplication([])      # QPainter 需要 QGuiApplication
    OUT.mkdir(parents=True, exist_ok=True)
    png = draw(256)
    ok = png.save(str(OUT / "app.png"), "PNG")
    # Qt 的 ICO 写入插件支持多尺寸合并
    ico = QPixmap(QSize(256, 256))
    ico.fill(QColor(0, 0, 0, 0))
    painter = QPainter(ico)
    painter.drawPixmap(0, 0, draw(256))
    painter.end()
    ok_ico = ico.save(str(OUT / "app.ico"), "ICO")
    print(f"app.png {'ok' if ok else '失败'} / app.ico {'ok' if ok_ico else '失败'}  → {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
