# -*- coding: utf-8 -*-
"""四 Tab 截图工具（offscreen）：python scripts/ui_screenshot.py <输出目录> [before|after]
抓 MainWindow 四个 Tab 的 PNG，供主题/布局重构 before/after 对比。只读数据库（内存库+种子导入），
不写 ui_state.json / sessions，不发起网络探测。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # offscreen 平台字体目录

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))

from PyQt5.QtWidgets import QApplication          # noqa: E402
import theme                                       # noqa: E402
import db as dbmod                                 # noqa: E402


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(APP_DIR, "docs", "screenshots")
    tag = sys.argv[2] if len(sys.argv) > 2 else "shot"
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    theme.enable_high_dpi()
    theme.apply_theme(app)

    database = dbmod.Database(":memory:")
    database.import_seed_dir()

    from ui_main import MainWindow
    win = MainWindow(database)
    win.resize(1280, 800)

    # 离屏环境不跑网络探测 / 启动向导
    win.startup_flow = lambda: None
    if hasattr(win, "tab_ai") and hasattr(win.tab_ai, "start_probe"):
        win.tab_ai.start_probe = lambda: None
    if hasattr(win, "tab_ai") and hasattr(win.tab_ai, "refresh_config_state"):
        win.tab_ai.refresh_config_state()

    win.show()
    for _ in range(6):
        app.processEvents()

    # AI Tab：无论是否配置过，都抓"主界面"而非引导页
    if hasattr(win, "tab_ai") and hasattr(win.tab_ai, "stack"):
        win.tab_ai.stack.setCurrentIndex(1)
        for _ in range(6):
            app.processEvents()

    names = ["command_lib", "troubleshoot", "errorfix", "ai"]
    for i, name in enumerate(names):
        win.tabs.setCurrentIndex(i)
        for _ in range(6):
            app.processEvents()
        path = os.path.join(out_dir, "%s_%s.png" % (tag, name))
        win.grab().save(path)
        print("saved:", path)

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
