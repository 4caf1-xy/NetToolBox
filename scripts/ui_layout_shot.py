# -*- coding: utf-8 -*-
"""
ui_layout_shot.py —— 命令库 Tab 布局截图 + 可见行测量（AB 批起，offscreen）

用途：UI 布局改动的 before/after 证据采集。固定场景为"scp 限速传输"条目
（4 条参数），展开「参数说明」页，抓主窗截图并量出参数表实际可见行数。

用法（必须在仓库根执行）：
    PYTHONPATH="app;_vendor" python scripts/ui_layout_shot.py docs/screenshots/ab_before.png
    PYTHONPATH="app;_vendor" python scripts/ui_layout_shot.py docs/screenshots/ab_after.png --size 1400x880

只读：走 _smoke_env（路径劫持到临时目录 + 主库 sha256 atexit 断言），
不写 ui_state.json / sessions，不碰主库与种子。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
import _smoke_env as _se      # noqa: E402
_se.setup()                   # 必须先于 app 模块导入

SCP_UUID = "0a7c29e2-568f-5c00-9e22-6e92dc0c64b6"   # scp 限速传输（4 参数）


def _visible_rows(table):
    """表头以下能完整显示的数据行数（按 viewport 高度与行几何算）"""
    vp = table.viewport().height()
    if vp <= 0 or table.rowCount() == 0:
        return 0
    shown = 0
    used = 0
    for row in range(table.rowCount()):
        h = table.rowHeight(row)
        if used + h <= vp:
            shown += 1
            used += h
        else:
            break
    return shown


def main():
    out = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(REPO, "docs", "screenshots", "ab_shot.png"))
    size = (1400, 880)
    for i, arg in enumerate(sys.argv):
        if arg == "--size" and i + 1 < len(sys.argv):
            w, h = sys.argv[i + 1].lower().split("x")
            size = (int(w), int(h))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    from PyQt5.QtWidgets import QApplication
    import theme
    import db as dbmod

    app = QApplication.instance() or QApplication(sys.argv)
    theme.enable_high_dpi()
    theme.apply_theme(app)

    database = dbmod.Database(":memory:")
    database.import_seed_dir()

    from ui_main import MainWindow
    win = MainWindow(database)
    win.resize(*size)
    win.startup_flow = lambda: None
    if hasattr(win, "tab_ai"):
        if hasattr(win.tab_ai, "start_probe"):
            win.tab_ai.start_probe = lambda: None
        if hasattr(win.tab_ai, "refresh_config_state"):
            win.tab_ai.refresh_config_state()
    win.show()
    for _ in range(8):
        app.processEvents()

    win.tabs.setCurrentIndex(0)                     # 命令库 Tab
    win.select_by_uuid(SCP_UUID)                    # 选中 scp（4 参数）
    for _ in range(6):
        app.processEvents()

    # 切到「参数说明」页并展开折叠面板（复现用户截图里的实况）
    idx = win.tab_info.indexOf(win.param_panel)
    if idx >= 0:
        win.tab_info.setCurrentIndex(idx)
    if "--collapsed" not in sys.argv:      # --collapsed：抓"默认收起"态
        win.btn_param_toggle.setChecked(True)
        win._toggle_param_panel(True)
    for _ in range(8):
        app.processEvents()

    # 回读布局几何（证据用）
    print("window        : %dx%d" % (win.width(), win.height()))
    for name in ("main_split", "center_split", "work_split"):
        sp = getattr(win, name, None)
        if sp is not None:
            print("%-13s: sizes=%s collapsible=(%s,%s)"
                  % (name, sp.sizes(), sp.isCollapsible(0), sp.isCollapsible(1)))
    detail = getattr(win, "_detail_panel", None)
    if detail is not None:
        print("detail panel  : w=%d h=%d minW=%d"
              % (detail.width(), detail.height(), detail.minimumWidth()))
    print("param table   : rows=%d viewportH=%d visibleRows=%d"
          % (win.tbl_params.rowCount(), win.tbl_params.viewport().height(),
             _visible_rows(win.tbl_params)))
    print("tab_info h    : %d   code h: %d"
          % (win.tab_info.height(), win.txt_commands.height()))

    ok = win.grab().save(out)
    print("saved         : %s (%s)" % (out, "ok" if ok else "FAILED"))

    # 参数表单独裁剪一份（证据：4 行数据是否完整可见，不依赖整窗缩放）
    from PyQt5.QtCore import QPoint, QRect
    table = win.tbl_params
    crop = win.grab().copy(QRect(table.mapTo(win, QPoint(0, 0)), table.size()))
    table_path = os.path.splitext(out)[0] + "_params.png"
    crop.save(table_path)
    print("saved table   : %s" % table_path)

    win.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
