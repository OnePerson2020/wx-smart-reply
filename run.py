"""统一入口（源码运行和 PyInstaller 打包都用这个文件）。

    python run.py            # 打开界面
    python run.py selftest   # 自检
    python run.py check --kb <已解密目录>
"""
import sys

from wxreply.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
