# -*- coding: utf-8 -*-
"""
smoke_panel_dashboard.py —— Ctrl+K 面板 / 最近使用 / 覆盖度仪表盘 / B14 回归
（2026-10-09 批次，基线 v0.4.0 后）

覆盖五案（全部 offscreen + mock，不依赖真实网络/设备）：
    [A] 命令面板：三库各 1 命中（条目/报错/树）、空输入最近使用分组、
        空结果文案、激活返回 choice
    [B] 跳转与最近使用：jump_to 三库各跳一条 → Tab 切换/定位/选中正确；
        记录去重置顶、上限 10、清空；ui_state.json 落盘（临时目录，不碰真文件）
    [C] 仪表盘对账：coverage_counts 与 db 直查 SQL 逐格一致；
        矩阵行列与 GROUP BY 一致；四档合计=总数（含"不可验证"挂起口径）
    [D] B14 连点回归：250ms 时间窗内连点 toggle_favorite 只产生 1 条 history
    [E] 四 Tab 回归：Tab 数不变、Splitter 可恢复、仪表盘折叠卡片在命令库 Tab

用法：python scripts/smoke_panel_dashboard.py   （EXIT=0 全过；需 PyQt5）
"""
import os
import sys
import json
import time
import tempfile
import shutil

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 断言

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


# ======================================================================
# 公共：临时 ui_state + 种子库
# ======================================================================
def setup_state_dir():
    """把 theme.state_file_path 劫持到临时目录，避免污染真实 ui_state.json"""
    import theme
    tmpdir = tempfile.mkdtemp(prefix="ntbx_smoke_state_")
    orig = theme.state_file_path
    theme.state_file_path = lambda: os.path.join(tmpdir, "ui_state.json")
    return theme, tmpdir, orig


def teardown_state_dir(theme, tmpdir, orig):
    theme.state_file_path = orig
    shutil.rmtree(tmpdir, ignore_errors=True)


def seeded_db():
    import db as dbmod
    d = dbmod.Database(":memory:")
    d.import_seed_dir()
    return d


# ======================================================================
# [A] 命令面板搜索与激活（纯面板，不建主窗）
# ======================================================================
def case_a():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    from ui_panel import CommandPalette

    db = seeded_db()
    entry = db.search(keyword="trunk")[0]
    err = db.all_errs()[0]
    tree = db.all_trees()[0]

    # 空输入 + 无最近使用：给文案不空白
    dlg = CommandPalette(db, recent=[], parent=None)
    check("A/空输入-列表非空", dlg.lst.count() > 0, "")
    check("A/空输入-提示文案", "最近使用" in dlg.lst.item(0).text()
          or "关键字" in dlg.lst.item(0).text(),
          dlg.lst.item(0).text())

    # 三库各 1 命中
    def kinds_after_reload(kw):
        dlg._reload(kw)
        found = set()
        for i in range(dlg.lst.count()):
            data = dlg.lst.item(i).data(__import__("PyQt5.QtCore", fromlist=["Qt"]).Qt.UserRole)
            if isinstance(data, dict):
                found.add(data["kind"])
        return found

    # 命令：trunk；报错：用第一条 err 的 pattern 关键字；树：用第一条 symptom 关键字
    err_kw = (err.get("pattern") or "").split()[0][:6].lower()
    tree_kw = (tree.get("symptom") or "").split()[0][:4]
    check("A/命中-命令", "entry" in kinds_after_reload("trunk"), "kw=trunk")
    check("A/命中-报错", "err" in kinds_after_reload(err_kw), "kw=%s" % err_kw)
    check("A/命中-树", "tree" in kinds_after_reload(tree_kw), "kw=%s" % tree_kw)

    # 激活返回 choice
    dlg._reload("trunk")
    first = None
    from PyQt5.QtCore import Qt
    for i in range(dlg.lst.count()):
        it = dlg.lst.item(i)
        if it.flags() & Qt.ItemIsEnabled and it.data(Qt.UserRole):
            first = it
            break
    dlg._activate(first)
    check("A/激活-choice", dlg.choice and dlg.choice["kind"] == "entry", str(dlg.choice))

    # 空结果文案
    dlg._reload("zzzz_不存在关键字_zzzz")
    texts = [dlg.lst.item(i).text() for i in range(dlg.lst.count())]
    check("A/空结果-给文案", any("没有匹配" in t for t in texts), str(texts[:2]))

    # 最近使用分组（传假 recent，全部已删除 → 丢弃不崩）
    dlg2 = CommandPalette(db, recent=[{"kind": "entry", "id": "no-such-uuid"}],
                          parent=None)
    check("A/最近-死引用丢弃", dlg2.lst.count() >= 1, "")

    # 有真实最近使用 → 显示分组，激活可跳
    dlg3 = CommandPalette(db, recent=[{"kind": "entry", "id": entry["uuid"]}],
                          parent=None)
    texts = [dlg3.lst.item(i).text() for i in range(dlg3.lst.count())]
    check("A/最近-分组显示", any("最近使用" in t for t in texts)
          and any(entry["title"][:8] in t for t in texts if t), str(texts[:3]))

    # 清空（mock 确认框）
    from PyQt5.QtWidgets import QMessageBox
    cleared = []
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    dlg3.on_clear_recent = lambda: cleared.append(1)
    dlg3._on_clear_recent()
    check("A/清空-回调触发", len(cleared) == 1, "")


# ======================================================================
# [B] 跳转 + 最近使用记录/去重/上限/落盘
# ======================================================================
def case_b():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    theme, tmpdir, orig = setup_state_dir()
    try:
        import ui_main
        import ui_errorfix
        import db as dbmod

        db = seeded_db()
        w = ui_main.MainWindow(db)

        entry = db.search(keyword="trunk")[0]
        err = db.all_errs()[0]
        tree = db.all_trees()[0]

        # mock 报错字典对话框（exec_ 会阻塞，offscreen 下不能真开）
        opened = {"err_id": None, "selected": None}

        class FakeDictMgr:
            def __init__(self, db, parent=None):
                pass
            def select_err(self, err_id):
                opened["selected"] = err_id
                return True
            def exec_(self):
                opened["exec"] = True
                return 1
        orig_mgr = ui_errorfix.DictManagerDialog
        ui_errorfix.DictManagerDialog = FakeDictMgr
        try:
            w.jump_to("entry", entry["uuid"])
            check("B/跳转-命令Tab", w.tabs.currentIndex() == 0, str(w.tabs.currentIndex()))
            check("B/跳转-命令选中", w.current_entry
                  and w.current_entry.get("uuid") == entry["uuid"],
                  str(w.current_entry and w.current_entry.get("uuid")))

            w.jump_to("err", err["err_id"])
            check("B/跳转-报错Tab", w.tabs.currentWidget() is w.tab_errorfix, "")
            check("B/跳转-报错选中", opened["selected"] == err["err_id"], "")

            w.jump_to("tree", tree["tree_id"])
            check("B/跳转-树Tab", w.tabs.currentWidget() is w.tab_troubleshoot, "")
            check("B/跳转-树载入", w.tab_troubleshoot.current_tree
                  and w.tab_troubleshoot.current_tree.get("tree_id") == tree["tree_id"], "")
        finally:
            ui_errorfix.DictManagerDialog = orig_mgr

        # 记录：三条都进最近使用，最新在顶
        kinds = [r["kind"] for r in w._recent]
        check("B/记录-三条", kinds == ["tree", "err", "entry"], str(kinds))
        check("B/记录-置顶", w._recent[0]["id"] == tree["tree_id"], "")

        # 去重置顶：再次跳 entry → entry 升顶且只一份
        w.jump_to("entry", entry["uuid"])
        ids = [r["id"] for r in w._recent]
        check("B/去重置顶", ids.count(entry["uuid"]) == 1 and ids[0] == entry["uuid"],
              str(ids))

        # 上限 10：灌 15 条
        entries = db.search()[:15]
        for e in entries:
            w._record_recent("entry", e["uuid"])
        check("B/上限10", len(w._recent) == 10, str(len(w._recent)))

        # 落盘：ui_state.json（临时目录）里能读回
        w._save_ui_state()
        with open(theme.state_file_path(), "r", encoding="utf-8") as fp:
            saved = json.load(fp)
        check("B/落盘-recent_used",
              isinstance(saved.get("recent_used"), list) and len(saved["recent_used"]) == 10,
              str(saved.get("recent_used", [])[:2]))

        # 面板跳转重置筛选期间不记 row0（抑制标志路径）：重建主窗验证
        w2 = ui_main.MainWindow(db)
        before = [r["id"] for r in w2._recent]
        row0 = w2.entries_in_view[0]["uuid"]
        target = db.search(keyword="bgp")[0]
        w2.jump_to("entry", target["uuid"])
        after = [r["id"] for r in w2._recent]
        check("B/抑制-row0不新增", row0 in before or row0 == target or row0 not in after,
              "row0=%s before=%s" % (row0[:8], before[:3]))
        check("B/抑制-目标置顶", after and after[0] == target["uuid"], str(after[:2]))

        # 清空
        w2._clear_recent()
        check("B/清空", w2._recent == [], "")

        w.deleteLater()
        w2.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


# ======================================================================
# [C] 仪表盘对账（四档口径 + 矩阵逐格）
# ======================================================================
def case_c():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import ui_dashboard
    import db as dbmod

    db = seeded_db()
    counts = ui_dashboard.coverage_counts(db)

    # 与 db 直查逐项对账（entries）
    conn = db.conn
    row = conn.execute(
        "SELECT SUM(CASE WHEN verified=1 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN verified<>1 AND exec_level='verified-cli' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN verified<>1 AND exec_level IN ('skeleton','web-only') THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN verified<>1 AND exec_level='' AND notes LIKE '%不可验证%' THEN 1 ELSE 0 END), "
        "count(*) FROM entries").fetchone()
    e = counts["entries"]
    check("C/entries-绿", e["green"] == int(row[0] or 0), "%s vs %s" % (e["green"], row[0]))
    check("C/entries-语法核对", e["syntax"] == int(row[1] or 0), "%s vs %s" % (e["syntax"], row[1]))
    check("C/entries-挂起(含不可验证)",
          e["suspended"] == int(row[2] or 0) + int(row[3] or 0),
          "%s vs skeleton=%s unver=%s" % (e["suspended"], row[2], row[3]))
    check("C/entries-合计=总数",
          e["green"] + e["syntax"] + e["suspended"] + e["unverified"] == e["total"],
          str(e))
    check("C/errs-合计=总数",
          counts["errs"]["green"] + counts["errs"]["syntax"]
          + counts["errs"]["suspended"] + counts["errs"]["unverified"]
          == counts["errs"]["total"], str(counts["errs"]))
    check("C/trees-合计=总数",
          counts["trees"]["green"] + counts["trees"]["unverified"]
          == counts["trees"]["total"], str(counts["trees"]))

    # 主窗内仪表盘：矩阵行列与 GROUP BY 对账
    import ui_main
    theme, tmpdir, orig = setup_state_dir()
    try:
        w = ui_main.MainWindow(db)
        dash = w.dashboard
        check("C/卡片-默认收起", dash.toggle.isChecked() is False
              and not dash.content.isVisible(), "")
        dash.toggle.setChecked(True)      # 展开 → 重建
        got = dash.refresh()
        check("C/卡片-展开重建", dash.tbl.columnCount() > 1 and dash.tbl.rowCount() > 0,
              "%dx%d" % (dash.tbl.rowCount(), dash.tbl.columnCount()))
        # 对账：每格数字 == db GROUP BY
        rows = db.conn.execute(
            "SELECT vendor, category, count(*) AS n FROM entries "
            "GROUP BY vendor, category").fetchall()
        hdr = [dash.tbl.horizontalHeaderItem(c).text()
               for c in range(dash.tbl.columnCount())]
        vhdr = [dash.tbl.verticalHeaderItem(r).text()
                for r in range(dash.tbl.rowCount())]
        mism = 0
        for r in rows:
            v = dbmod.display_vendor(r["vendor"] or "-")
            cat = r["category"] or "-"
            if v not in vhdr or cat not in hdr:
                mism += 1
                continue
            # hdr[0]="厂商"、末列="小计"，cats[i] 的列号就是 hdr.index(cat)
            cell = dash.tbl.item(vhdr.index(v), hdr.index(cat))
            if cell is None or cell.text() != str(int(r["n"] or 0)):
                mism += 1
        check("C/矩阵-逐格对账", mism == 0, "mismatch=%d (期望核对 %d 格)" % (mism, len(rows)))
        check("C/refresh-返回一致", got["entries"]["total"] == counts["entries"]["total"], "")
        w.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


# ======================================================================
# [D] B14 连点回归
# ======================================================================
def case_d():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import ui_main
    theme, tmpdir, orig = setup_state_dir()
    try:
        db = seeded_db()
        w = ui_main.MainWindow(db)
        entry = db.search(keyword="trunk")[0]
        w.select_by_uuid(entry["uuid"])
        check("D/前置-未收藏", int(entry["favorite"] or 0) == 0, "")

        hist0 = len(db.get_history(entry["uuid"]))
        w.toggle_favorite()                       # 第 1 次：生效
        w.toggle_favorite()                       # 250ms 窗口内连点：被吞
        w.toggle_favorite()                       # 继续连点：被吞
        hist1 = len(db.get_history(entry["uuid"]))
        check("D/连点-窗口内只算一次", hist1 - hist0 == 1,
              "hist +%d" % (hist1 - hist0))
        check("D/连点-收藏生效", w.current_entry["favorite"] == 1, "")

        # 等时间窗解除（上限 1s）
        t0 = time.time()
        while getattr(w, "_fav_busy", False) and time.time() - t0 < 1.0:
            app.processEvents()
            time.sleep(0.02)
        check("D/时间窗解除", not getattr(w, "_fav_busy", False),
              "%.2fs" % (time.time() - t0))
        w.toggle_favorite()                       # 解除后再次：生效（1→0）
        hist2 = len(db.get_history(entry["uuid"]))
        check("D/解除后-再切换", hist2 - hist0 == 2 and w.current_entry["favorite"] == 0,
              "hist +%d" % (hist2 - hist0))
        w.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


# ======================================================================
# [E] 四 Tab 布局回归
# ======================================================================
def case_e():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import ui_main
    theme, tmpdir, orig = setup_state_dir()
    try:
        db = seeded_db()
        w = ui_main.MainWindow(db)
        titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        check("E/Tab-仍是四个", w.tabs.count() == 4
              and titles == ["命令库", "排查向导", "报错诊断", "AI 诊断"], str(titles))
        # 仪表盘卡片在命令库 Tab（父链可达 main_split）
        p, in_tab = w.dashboard, False
        while p is not None:
            if p is w.main_split:
                in_tab = True
                break
            p = p.parentWidget()
        check("E/仪表盘-在命令库Tab", in_tab, "")
        # Splitter 记忆链路不破坏：改比例 → 保存 → 新窗恢复
        w.main_split.setSizes([300, 1400])
        w._save_ui_state()
        with open(theme.state_file_path(), "r", encoding="utf-8") as fp:
            saved = json.load(fp)
        check("E/记忆-main_split落盘", "main_split" in saved
              and "recent_used" in saved, str(list(saved.keys())))
        w.deleteLater()

        w2 = ui_main.MainWindow(db)
        sizes = w2.main_split.sizes()
        check("E/记忆-恢复比例", sizes and sizes[0] <= 460, str(sizes))
        w2.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


def main():
    print("=" * 66)
    print("NetToolBox Ctrl+K 面板 / 最近使用 / 仪表盘 / B14 回归")
    print("=" * 66)
    case_a()
    case_b()
    case_c()
    case_d()
    case_e()
    print("-" * 66)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print("%s %s  %s" % ("✓" if ok else "✘", name, detail))
    print("-" * 66)
    print("通过 %d / %d" % (len(RESULTS) - len(fails), len(RESULTS)))
    if fails:
        print("FAIL: %d 项未过" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
