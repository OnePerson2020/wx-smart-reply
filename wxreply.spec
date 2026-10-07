# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：Windows 上 `pyinstaller wxreply.spec` 产出 dist/WxReply/WxReply.exe。"""

from pathlib import Path

block_cipher = None

datas = [("tools/make_demo_kb.py", "tools")]      # 自检用；不想带就删掉这一行
icon = "resources/app.ico" if Path("resources/app.ico").exists() else None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["wxreply", "wxreply.kb", "wxreply.ui", "Crypto", "Crypto.Cipher.AES", "httpx"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WxReply",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # GUI 程序不弹黑框；想排错改成 True
    disable_windowed_traceback=False,
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WxReply",
)
