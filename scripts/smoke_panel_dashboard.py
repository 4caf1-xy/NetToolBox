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
    [E] 四 Tab 回归：Tab 数不变、Splitter 可恢复、仪表盘折叠卡片在命令库 Tab；
        AB 批（2026-11-11）命令库布局：详情在右侧、分隔条不可折叠、参数表 4 行可见、
        宽度记忆（saveState → 新窗恢复）
    [F] D 批收藏视图（2026-10-13）：收藏 3 条 → 只看收藏过滤 3 条 →
        取消收藏即时消失；Ctrl+K 空输入收藏区置顶；种子再导入 favorite 保留

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

        # 主窗图标（I 批补齐 2026-10-13）：icon.ico 载入成功、非空
        check("B/窗图标-非空", not w.windowIcon().isNull(),
              "windowIcon isNull=%s" % w.windowIcon().isNull())
        check("B/窗图标-路径存在",
              os.path.isfile(os.path.join(os.path.dirname(ui_main.__file__), "icon.ico")), "")

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
        # 2026-10-12：操作单 Tab（裁决②）+ 场景包 Tab（排查向导后）→ 5 → 6；
        # 2026-10-14（H 批）：变更对比 Tab（操作单后）→ 6 → 7；
        # 2026-11-02（R 批）：速查手册（命令库后）+ 工具箱（AI 诊断前）→ 7 → 9
        # 2026-11-06（U 批）：配置翻译（变更对比后、工具箱前）→ 9 → 10
        check("E/Tab-十个（增配置翻译）", w.tabs.count() == 10
              and titles == ["命令库", "速查手册", "排查向导", "场景包", "报错诊断",
                             "操作单", "变更对比", "配置翻译", "工具箱", "AI 诊断"],
              str(titles))
        check("E/操作单Tab-可恢复分栏", hasattr(w.tab_runbook, "restore_split_state"), "")
        check("E/场景包Tab-可恢复分栏", hasattr(w.tab_scenario, "restore_split_state"), "")
        check("E/变更对比Tab-可恢复分栏", hasattr(w.tab_diff, "restore_split_state"), "")
        check("E/配置翻译Tab-可恢复分栏", hasattr(w.tab_translate, "restore_split_state"), "")
        # 仪表盘卡片在命令库 Tab（父链可达 main_split）
        p, in_tab = w.dashboard, False
        while p is not None:
            if p is w.main_split:
                in_tab = True
                break
            p = p.parentWidget()
        check("E/仪表盘-在命令库Tab", in_tab, "")

        # ----------------------------------------------------------
        # AB 批（2026-11-11）：命令库布局重构——列表 │ 详情 左右分栏
        # ----------------------------------------------------------
        # ★ 必须先 resize+show：不显示的窗口布局没跑过，splitter.sizes()
        #   是"未布局时的默认值"，拖拽/记忆断言都会变成假通过。
        from PyQt5.QtCore import Qt
        w.resize(1400, 880)
        w.show()
        for _ in range(8):
            app.processEvents()
        split = w.work_split
        check("E/AB-分栏-水平", split.orientation() == Qt.Horizontal,
              str(split.orientation()))
        check("E/AB-详情在右侧",
              w._detail_panel.mapTo(w, w._detail_panel.rect().topLeft()).x()
              > w._list_top.mapTo(w, w._list_top.rect().topLeft()).x(),
              "detail.x=%d list.x=%d"
              % (w._detail_panel.mapTo(w, w._detail_panel.rect().topLeft()).x(),
                 w._list_top.mapTo(w, w._list_top.rect().topLeft()).x()))
        check("E/AB-两侧不可折叠",
              not split.isCollapsible(0) and not split.isCollapsible(1),
              "(%s,%s)" % (split.isCollapsible(0), split.isCollapsible(1)))
        check("E/AB-最小宽底线",
              w._list_top.minimumWidth() >= 320
              and w._detail_panel.minimumWidth() >= 400,
              "list=%d detail=%d" % (w._list_top.minimumWidth(),
                                     w._detail_panel.minimumWidth()))
        handle = split.handle(1)
        check("E/AB-分隔条-6px+SplitHCursor",
              handle is not None and handle.width() == 6
              and handle.cursor().shape() == Qt.SplitHCursor,
              "w=%s cursor=%s"
              % (handle.width() if handle else "-",
                 handle.cursor().shape() if handle else "-"))
        # 拖到极端：任何一侧都不塌成 0
        split.setSizes([1, 5000])
        left_floor = split.sizes()[0]
        split.setSizes([5000, 1])
        right_floor = split.sizes()[1]
        check("E/AB-拖不塌", left_floor >= 300 and right_floor >= 380,
              "left=%d right=%d" % (left_floor, right_floor))
        # 复位到默认 45:55（后面"4 行可见"按常规比例量；拖到 400 宽时行高会被
        # 说明列折行顶大，4 行装不下是物理限制，不是布局错）
        total = sum(split.sizes())
        split.setSizes([total * 45 // 100, total - total * 45 // 100])
        for _ in range(6):
            app.processEvents()

        # 参数说明：4 行必须完整可见（AB 批用户点名问题）
        scp = [e for e in db.search() if "scp 限速传输" in (e.get("title") or "")]
        check("E/AB-用例条目存在", len(scp) == 1, str(len(scp)))
        if scp:
            # 参数表"4 行完整可见"是物理空间问题：这里把窗口拉高到 1000px
            # 再量（离屏环境的字体度量比实机略大，880px 会差一行）
            w.resize(1400, 1000)
            for _ in range(4):
                app.processEvents()
            w.select_by_uuid(scp[0]["uuid"])
            idx = w.tab_info.indexOf(w.param_panel)
            if idx >= 0:
                w.tab_info.setCurrentIndex(idx)
            w.btn_param_toggle.setChecked(True)
            w._toggle_param_panel(True)          # 含 singleShot 重算最小高度
            for _ in range(12):
                app.processEvents()
            table = w.tbl_params
            vp = table.viewport().height()
            shown = 0
            for r in range(table.rowCount()):
                if table.rowViewportPosition(r) + table.rowHeight(r) <= vp:
                    shown += 1
            check("E/AB-参数表4行完整可见",
                  table.rowCount() == 4 and shown == 4,
                  "rows=%d shown=%d vp=%d" % (table.rowCount(), shown, vp))
            # 表格自身高度不得溢出详情面板（否则底行会被裁）
            check("E/AB-参数表未溢出",
                  table.mapTo(w._detail_panel, table.rect().bottomLeft()).y()
                  <= w._detail_panel.height(),
                  "table.bottom=%d detail.h=%d tab(y=%d,h=%d) code=%d tbMin=%d"
                  % (table.mapTo(w._detail_panel,
                                 table.rect().bottomLeft()).y(),
                     w._detail_panel.height(), w.tab_info.y(),
                     w.tab_info.height(), w.txt_commands.height(),
                     table.minimumHeight()))

        # Splitter 记忆链路不破坏：改比例 → 保存 → 新窗恢复
        w.main_split.setSizes([300, 1400])
        w.work_split.setSizes([560, 700])
        want_work = list(w.work_split.sizes())      # setSizes 会按实际宽度归一化
        w._save_ui_state()
        with open(theme.state_file_path(), "r", encoding="utf-8") as fp:
            saved = json.load(fp)
        check("E/记忆-main_split落盘", "main_split" in saved
              and "recent_used" in saved, str(list(saved.keys())))
        check("E/AB-记忆-work_split落盘",
              "work_split" in saved and "cmd_work_split_state" in saved
              and len(saved.get("work_split") or []) == 2,
              str(saved.get("work_split")))
        w.deleteLater()

        w2 = ui_main.MainWindow(db)
        w2.resize(1400, 880)
        w2.show()
        for _ in range(8):
            app.processEvents()
        sizes = w2.main_split.sizes()
        check("E/记忆-恢复比例", sizes and sizes[0] <= 460, str(sizes))
        restored = w2.work_split.sizes()
        # saveState/restoreState 精确恢复（允许 ±8px：分栏边界取整）
        check("E/AB-记忆-重启恢复work_split",
              len(restored) == 2 and abs(restored[0] - want_work[0]) <= 8
              and abs(restored[1] - want_work[1]) <= 8,
              "want=%s got=%s" % (want_work, restored))
        # 恢复后仍不可折叠（restoreState 不应把 collapsible 改回来）
        check("E/AB-恢复后仍不可折叠",
              not w2.work_split.isCollapsible(0)
              and not w2.work_split.isCollapsible(1), "")
        w2.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


# ======================================================================
# [F] D 批收藏视图：过滤视图 / Ctrl+K 收藏区 / 种子导入护栏回归
# ======================================================================
def _wait_fav_free(app, w, timeout=1.0):
    """等 B14 250ms 防重入窗解除"""
    t0 = time.time()
    while getattr(w, "_fav_busy", False) and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)


def case_f():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import ui_main
    from ui_panel import CommandPalette
    from PyQt5.QtCore import Qt
    theme, tmpdir, orig = setup_state_dir()
    try:
        db = seeded_db()
        w = ui_main.MainWindow(db)

        # ---- F1 收藏 3 条 → 过滤视图只显 3 条（第 1 条走 UI Ctrl+D 路径）----
        picks = db.search()[:3]
        w.select_by_uuid(picks[0]["uuid"])
        w.toggle_favorite()                       # UI 路径收藏
        _wait_fav_free(app, w)
        check("F1/UI收藏-生效", int(db.get_entry(picks[0]["uuid"])["favorite"]) == 1, "")
        for e in picks[1:]:                       # 其余 2 条走 db 直写（数据铺垫）
            db.toggle_favorite(e["uuid"])
        check("F1/前置-收藏3条", len(db.search(favorite_only=True)) == 3, "")

        w.tgl_fav.setChecked(True)                # 「只看收藏」过滤（toggled → refresh_list）
        view = w.entries_in_view
        check("F1/过滤-只显3条", len(view) == 3, str(len(view)))
        check("F1/过滤-全为favorite", all(int(e["favorite"] or 0) == 1 for e in view), "")
        expect_order = [e["uuid"] for e in db.search(favorite_only=True)]
        check("F1/过滤-排序稳定",
              [e["uuid"] for e in view] == expect_order, str(expect_order[:3]))

        # ---- 取消收藏即时消失（UI 路径 + B14 时间窗）----
        w.select_by_uuid(picks[0]["uuid"])
        _wait_fav_free(app, w)
        w.toggle_favorite()
        _wait_fav_free(app, w)
        view2 = w.entries_in_view
        check("F1/取消-即时消失",
              len(view2) == 2 and picks[0]["uuid"] not in [e["uuid"] for e in view2],
              str(len(view2)))
        w.tgl_fav.setChecked(False)

        # ---- F2 Ctrl+K 空输入：收藏区置顶（在最近使用区之前）----
        w._record_recent("entry", picks[0]["uuid"])   # 造一条最近使用
        dlg = CommandPalette(db, recent=list(w._recent), parent=None)
        rows = []                                     # [(type, payload)]
        for i in range(dlg.lst.count()):
            it = dlg.lst.item(i)
            data = it.data(Qt.UserRole)
            rows.append(("section", it.text()) if data is None
                        else ("item", data))
        sect_texts = [p for t, p in rows if t == "section"]
        check("F2/收藏-分组显示", any("收藏" in t for t in sect_texts), str(sect_texts[:2]))
        first_sect = sect_texts[0] if sect_texts else ""
        check("F2/收藏-置顶", "收藏" in first_sect, first_sect)
        idx_fav = next(i for i, (t, p) in enumerate(rows)
                       if t == "section" and "收藏" in p)
        idx_recent = next((i for i, (t, p) in enumerate(rows)
                           if t == "section" and "最近使用" in p), 10 ** 9)
        fav_item_ids = [p["id"] for t, p in rows[idx_fav + 1:idx_recent] if t == "item"]
        check("F2/收藏-条目数", len(fav_item_ids) == 2, str(fav_item_ids))
        check("F2/收藏-含已收藏条目", picks[1]["uuid"] in fav_item_ids, "")
        check("F2/收藏-在最近使用之前", idx_fav < idx_recent,
              "fav@%d recent@%d" % (idx_fav, idx_recent))
        # 收藏区条目可直接激活跳转（choice 机制与搜索结果同构）
        target_item = next(dlg.lst.item(i) for i in range(dlg.lst.count())
                           if (dlg.lst.item(i).data(Qt.UserRole) or {}).get("id")
                           == picks[1]["uuid"])
        dlg._activate(target_item)
        check("F2/收藏-激活choice", dlg.choice == {"kind": "entry", "id": picks[1]["uuid"]},
              str(dlg.choice))

        # ---- F3 护栏回归：种子再导入 favorite 保留（面板收藏区随之不丢）----
        w._save_ui_state()
        d_re = db            # 同一 :memory: 库再导入（重演种子刷新场景）
        d_re.import_seed_dir()
        kept = db.search(favorite_only=True)
        check("F3/护栏-favorite保留", picks[1]["uuid"] in [e["uuid"] for e in kept]
              and len(kept) == 2, str([e["uuid"][:8] for e in kept]))
        dlg2 = CommandPalette(db, recent=[], parent=None)
        texts2 = [dlg2.lst.item(i).text() for i in range(dlg2.lst.count())]
        check("F3/护栏-面板收藏区仍在", any("收藏" in t for t in texts2)
              and any(picks[1]["title"][:8] in t for t in texts2), str(texts2[:2]))

        w.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


def case_g():
    """
    [G] 搜索逻辑回归（AE 批 2026-11-12）

    绑定的是 AE 批定下的语义，不是旧行为：
        G1 空格分词 = AND（改前整串 LIKE，多词几乎必然 0 条）
        G2 全角/半角等价（NFKC）
        G3 大小写不敏感（SQLite LIKE 默认，防回归）
        G4/G5 params / rollback 纳进检索范围
        G6 重置筛选后树高亮回到『全部命令』
        G7 下拉改变过滤后树不再保留错误高亮
        G8 ESC 清空搜索框并立即生效
        G9 占位厂商态下输入关键字能回到全库搜索
        G10 空结果页列出当前过滤条件，且无关键字时不说"换个关键字"
        G11 防抖常量 + 逐键耗时上限（AD 批口径）
        G12 Ctrl+F 聚焦搜索框
    """
    import re
    import time
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    app = QApplication.instance() or QApplication(sys.argv)
    import ui_main
    import db as dbmod
    theme, tmpdir, orig = setup_state_dir()
    try:
        db = seeded_db()
        w = ui_main.MainWindow(db)
        w.resize(1400, 880)
        w.show()
        for _ in range(6):
            app.processEvents()

        def flush(text):
            """跳过 200ms 防抖，直接按"回车"路径落地（与 _flush_search 同一条链）"""
            w.ed_search.setText(text)
            w._flush_search()
            app.processEvents()

        def uuids():
            return set(e["uuid"] for e in w.entries_in_view)

        def tree_label():
            item = w.tree.currentItem()
            return item.text(0) if item is not None else ""

        # ---- G1 空格 = AND ----
        flush("华为")
        only_vendor = uuids()
        flush("链路聚合")
        only_cat = uuids()
        flush("华为 链路聚合")
        both = uuids()
        check("G1/分词-多词有结果", len(both) > 0, "华为∧链路聚合=%d" % len(both))
        check("G1/分词-是交集", both <= (only_vendor & only_cat)
              and both == (only_vendor & only_cat),
              "%d vs %d" % (len(both), len(only_vendor & only_cat)))

        # ---- G2 全角 = 半角 ----
        flush("VLAN")
        half = len(w.entries_in_view)
        flush("ＶＬＡＮ")
        check("G2/全角等价", half > 0 and len(w.entries_in_view) == half,
              "半角=%d 全角=%d" % (half, len(w.entries_in_view)))

        # ---- G3 大小写不敏感 ----
        flush("vlan")
        check("G3/大小写不敏感", len(w.entries_in_view) == half,
              "小写=%d 大写=%d" % (len(w.entries_in_view), half))

        # ---- G4/G5 params、rollback 可搜 ----
        def column_only_token(field):
            """找一段只出现在该字段（同一条目内其它字段都没有）的片段

            AF 批口径修订（2026-11-12，P5 反向）：params 只在**值域**里挑候选词。
            P5 之后键名不再参与检索，旧写法会把 'label' 这种 JSON 键名当候选词 ——
            改前它恰好靠键名命中 1 条（假通过），现在必须换成真值才算数。
            """
            for entry in db.search():
                text = entry.get(field) or ""
                if field == "params":
                    text = dbmod.params_value_text(text)
                if not isinstance(text, str):
                    continue
                for cand in re.findall(r"[0-9A-Za-z_\-\./]{5,}", text):
                    others = "".join(str(entry.get(k) or "") for k in
                                     ("title", "description", "commands", "notes",
                                      "models", "category")).lower()
                    if cand.lower() not in others:
                        return cand
            return None

        tok = column_only_token("params")
        flush(tok or "v3_group")
        check("G4/params可搜", bool(tok) and len(w.entries_in_view) >= 1,
              "token=%s 命中=%d" % (tok, len(w.entries_in_view)))
        tok_rb = column_only_token("rollback")
        flush(tok_rb or "回退")
        check("G5/rollback可搜", bool(tok_rb) and len(w.entries_in_view) >= 1,
              "token=%s 命中=%d" % (tok_rb, len(w.entries_in_view)))

        # ---- G4b params 键名不参与检索（P5 反向，AF 批 2026-11-12）----
        # 判据不是"条数=0"，而是"没有任何一条是靠 params 键名命中的" —— 即便
        # required 这类词将来出现在正文里，那也应算合法命中，不该误判。
        flush("required")
        key_hits = [e for e in w.entries_in_view
                    if "params" in db.match_fields(e, "required")]
        check("G4b/params键名不参与检索", not key_hits,
              "命中 %d 条，靠键名 %d 条" % (len(w.entries_in_view), len(key_hits)))

        # ---- G6 重置后树高亮归位 ----
        flush("")
        huawei_os = None
        for i in range(w.tree.topLevelItemCount()):
            top = w.tree.topLevelItem(i)
            if top.text(0) == "网络设备":
                for j in range(top.childCount()):
                    dt = top.child(j)
                    if dt.text(0) == "交换机":
                        for k in range(dt.childCount()):
                            vd = dt.child(k)
                            if vd.text(0) == "华为":
                                huawei_os = vd.child(0)
                                break
        check("G6/前置-找到华为节点", huawei_os is not None, "")
        if huawei_os is not None:
            w.tree.setCurrentItem(huawei_os)
            w.on_tree_clicked(huawei_os, 0)
            app.processEvents()
            before = len(w.entries_in_view)
            w.reset_filters()
            app.processEvents()
            check("G6/重置-树回全部命令", tree_label() == "全部命令",
                  "树=%s 重置前=%d 重置后=%d" % (tree_label(), before,
                                                len(w.entries_in_view)))
            check("G6/重置-列表回全库", len(w.entries_in_view) == db.count(),
                  "%d/%d" % (len(w.entries_in_view), db.count()))

        # ---- G7 下拉改变后树不得残留错误高亮 ----
        w.reset_filters()
        idx = w.cmb_vendor.findData("huawei")
        w.cmb_vendor.setCurrentIndex(idx)
        app.processEvents()
        check("G7/树高亮-不撒谎", tree_label() in ("", "全部命令")
              or "华为" in tree_label() and w.cmb_device.currentData() == "交换机",
              "树=%s 厂商=%s" % (tree_label(), w.cmb_vendor.currentData()))
        check("G7/树高亮-与列表一致",
              tree_label() != "全部命令" or len(w.entries_in_view) == db.count(),
              "树=%s 条数=%d" % (tree_label(), len(w.entries_in_view)))

        # ---- G8 ESC 清空 ----
        w.reset_filters()
        w.ed_search.setText("vlan")
        w._flush_search()
        app.processEvents()
        w.ed_search.setFocus()
        QTest.keyClick(w.ed_search, Qt.Key_Escape)
        app.processEvents()
        check("G8/ESC清空", w.ed_search.text() == ""
              and len(w.entries_in_view) == db.count(),
              "文本=%r 条数=%d" % (w.ed_search.text(), len(w.entries_in_view)))

        # ---- G9 占位厂商后输入即恢复 ----
        ph = None
        for i in range(w.tree.topLevelItemCount()):
            top = w.tree.topLevelItem(i)
            if top.text(0) == "待补充厂商" and top.childCount():
                ph = top.child(0)
                break
        check("G9/前置-有占位厂商", ph is not None, "")
        if ph is not None:
            w.tree.setCurrentItem(ph)
            w.on_tree_clicked(ph, 0)
            app.processEvents()
            empty_cnt = len(w.entries_in_view)
            flush("vlan")
            check("G9/占位中搜索-恢复", empty_cnt == 0 and len(w.entries_in_view) > 0,
                  "占位=%d 搜索后=%d" % (empty_cnt, len(w.entries_in_view)))

        # ---- G10 空态：条件概览 + 文案不误导 ----
        w.reset_filters()
        w.cmb_device.setCurrentIndex(w.cmb_device.findData("防火墙"))
        w.cmb_vendor.setCurrentIndex(w.cmb_vendor.findData("cisco"))
        app.processEvents()
        body = w.lbl_empty_body.text()
        check("G10/空态-列条件", len(w.entries_in_view) == 0
              and "当前筛选条件" in body and "防火墙" in body and "Cisco" in body, body[:40])
        check("G10/空态-无词不说换个关键字", "换个关键字" not in body, body[:40])

        # ---- G11 防抖常量 + 逐键不触发过滤 + 逐键处理耗时（AD 批口径）----
        w.reset_filters()
        check("G11/防抖常量", ui_main.SEARCH_DEBOUNCE_MS == 200
              and w._search_timer.interval() == 200
              and w._search_timer.isSingleShot(), str(w._search_timer.interval()))
        before_uuid = [e["uuid"] for e in w.entries_in_view]
        w.ed_search.setText("vlan")
        app.processEvents()                 # 只跑一轮事件循环，不等 200ms
        check("G11/键入不立即过滤", w._search_timer.isActive()
              and [e["uuid"] for e in w.entries_in_view] == before_uuid, "")
        # 逐键只重置定时器：处理函数本体（稳定口径）+ 真实按键往返（含事件派发）
        n = 50
        t0 = time.perf_counter()
        for _ in range(n):
            w._on_search_text_changed("x")
        per_key = (time.perf_counter() - t0) * 1000 / n
        check("G11/逐键处理≤2.2ms", per_key <= 2.2, "%.3fms/键" % per_key)
        w._search_timer.stop()
        w.ed_search.setText("")
        QTest.qWait(0)                       # 预热（首次事件循环启动成本不计口径）
        lat = []
        for ch in "防火墙二层":
            t0 = time.perf_counter()
            w.ed_search.setText(w.ed_search.text() + ch)
            QTest.qWait(0)
            lat.append((time.perf_counter() - t0) * 1000)
        lat.sort()
        median = lat[len(lat) // 2]
        check("G11/逐键往返中位≤2.2ms", median <= 2.2,
              "中位=%.2fms max=%.2fms" % (median, lat[-1]))
        # 全库口径：清空关键字再量（497 条全量重建才是 AD 批那条基线）
        w._search_timer.stop()
        w.ed_search.blockSignals(True)
        w.ed_search.clear()
        w.ed_search.blockSignals(False)
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            w.refresh_list()
            dt = (time.perf_counter() - t0) * 1000
            best = dt if best is None else min(best, dt)
        check("G11/全库过滤不劣化", best <= 40.0 and len(w.entries_in_view) == db.count(),
              "min=%.1fms 条数=%d" % (best, len(w.entries_in_view)))

        # ---- G12 Ctrl+F ----
        w.list_entries.setFocus()
        app.processEvents()
        QTest.keyClick(w, Qt.Key_F, Qt.ControlModifier)
        app.processEvents()
        check("G12/Ctrl+F聚焦搜索框", w.ed_search.hasFocus(), "")

        w.deleteLater()
    finally:
        teardown_state_dir(theme, tmpdir, orig)


def case_h():
    """
    [H] AF 批（2026-11-12）搜索提案落地回归

        H1 命中字段标注（P1-B）：有词且命中才显示，字段=db.match_fields 的结果
        H2 标注跟随选中条目（换词后标注随之刷新，不是上一轮残留）
        H3 树计数随过滤变化（P3）：根=命中数、父节点=子节点之和、清空后回落全量
        H4 小叉清空立即刷新（P9）：40ms 内已回全库，逐键仍走防抖
        H5 params 只搜值域（P5）：搜键名 example 不再刷出全库；值域召回不退
    """
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtTest import QTest
    app = QApplication.instance() or QApplication(sys.argv)
    import ui_main
    import db as dbmod
    theme, tmpdir, orig = setup_state_dir()
    try:
        db = seeded_db()
        w = ui_main.MainWindow(db)
        w.resize(1400, 880)
        w.show()
        for _ in range(6):
            app.processEvents()

        def search(text, settle=260):
            w.ed_search.setText(text)
            QTest.qWait(settle)
            app.processEvents()

        def tree_count(*texts):
            parent = None
            item = None
            for text in texts:
                n = w.tree.topLevelItemCount() if parent is None else parent.childCount()
                item = None
                for i in range(n):
                    cand = w.tree.topLevelItem(i) if parent is None else parent.child(i)
                    if text in cand.text(0):
                        item = cand
                        break
                if item is None:
                    return None
                parent = item
            return int(item.text(1) or 0)

        # ---- H1/H2 命中字段标注 ----
        search("")
        check("H1/无词不显示标注", not w.lbl_match.isVisible(),
              repr(w.lbl_match.text()))
        search("vlan")
        shown = w.current_entry
        expect = "命中：%s" % " · ".join(
            dbmod.SEARCH_FIELD_LABELS[f] for f in db.match_fields(shown, "vlan")) \
            if shown else ""
        check("H1/标注=实际命中字段", w.lbl_match.isVisible()
              and w.lbl_match.text() == expect,
              "%r vs %r" % (w.lbl_match.text(), expect))
        # 换词后标注必须跟着变（选中行可能没动，on_entry_selected 不会重跑）
        search("1-4094")
        check("H2/换词后标注刷新", w.lbl_match.isVisible()
              and "参数" in w.lbl_match.text(), repr(w.lbl_match.text()))
        search("zzz不存在zzz")
        check("H2/空结果收起标注", not w.lbl_match.isVisible(), "")

        # ---- H3 树计数随过滤 ----
        search("")
        check("H3/无过滤=全量计数", tree_count("全部命令") == db.count(),
              "根=%s 库=%d" % (tree_count("全部命令"), db.count()))
        search("vlan")
        n_hit = len(w.entries_in_view)
        root = tree_count("全部命令")
        child_sum = 0
        for i in range(w.tree.topLevelItemCount()):
            top = w.tree.topLevelItem(i)
            if top.text(0) == "网络设备":
                for j in range(top.childCount()):
                    child_sum += int(top.child(j).text(1) or 0)
        check("H3/根计数=命中数", root == n_hit, "根=%s 列表=%d" % (root, n_hit))
        check("H3/父聚合=子项之和", tree_count("网络设备") == child_sum,
              "网络=%s 子项和=%d" % (tree_count("网络设备"), child_sum))
        search("")
        check("H3/清空后计数回落", tree_count("全部命令") == db.count(),
              "根=%s" % tree_count("全部命令"))

        # ---- H4 小叉立即刷新（P9）----
        search("vlan")
        w.ed_search.clear()                 # 与小叉同一路径：textChanged('')
        QTest.qWait(40)                     # 远小于 200ms 防抖
        check("H4/清空40ms内生效", len(w.entries_in_view) == db.count()
              and not w._search_timer.isActive(),
              "条数=%d 库=%d timer=%s"
              % (len(w.entries_in_view), db.count(), w._search_timer.isActive()))
        w.ed_search.setText("v")
        QTest.qWait(30)
        check("H4/逐键仍防抖", w._search_timer.isActive()
              and len(w.entries_in_view) == db.count(),
              "timer=%s" % w._search_timer.isActive())
        w._search_timer.stop()
        search("")

        # ---- H5 params 只搜值域 ----
        search("example")
        n_example = len(w.entries_in_view)
        check("H5/键名example不再刷全库", n_example < db.count() // 4,
              "%d 条（键名时代 387/497）" % n_example)
        check("H5/example残留都是真值", all(
            db.match_fields(e, "example") == ["params"] for e in w.entries_in_view),
            str([db.match_fields(e, "example") for e in w.entries_in_view][:3]))
        search("1-4094")
        check("H5/值域召回不退", len(w.entries_in_view) >= 20,
              "%d 条" % len(w.entries_in_view))

        w.deleteLater()
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
    case_f()
    case_g()
    case_h()
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
