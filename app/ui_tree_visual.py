# -*- coding: utf-8 -*-
"""
ui_tree_visual.py —— 排查树可视化编辑器 MVP（F 批，功能线收官件）

定位（与既有 TreeManagerDialog 的分工）
    TreeManagerDialog（ui_tree_editor.py）：直写**数据库**的树维护（增删改树 + 步骤）。
    本模块（MVP）：面向 **seed_data JSON** 的可视化预览 + 结构编辑 + 种子导出——
        只读树形预览 → 「编辑模式」解锁步骤增删改/改引用 →
        「导出种子差异」人工确认 → 写 seed_data → validate 门禁 → 失败自动回滚。
    MVP 边界（设计裁决①）：只编辑既有 26 棵树的内部结构（节点增删改/改引用）；
    整树新建/删除、跨树拖拽、批量重构、多树并排对比留二批 —— 26 底数稳定。

硬约束（设计裁决③）
    cmd_ref 只能经选择器从库内挑选（CmdRefPickerDialog，复用命令库搜索），
    uuid 展示位只读，禁止手写 uuid 输入。

纪律
    · 纯本地界面：不联网、不连设备；树里不写命令文本，命令一律 cmd_ref 引用。
    · 分栏走 theme.GripSplitter（AGENTS §6）；不写死分割尺寸的持久化（对话框
      生命周期内用默认比例，ui_state 持久化仅主窗口 Tab 承担）。
"""

import copy
import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QLabel, QLineEdit, QComboBox, QPushButton, QCheckBox,
                             QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
                             QSplitter, QGroupBox, QAbstractItemView, QTreeWidget,
                             QTreeWidgetItem, QPlainTextEdit, QDialogButtonBox,
                             QApplication)

import db as dbmod
import tree_seed_io as tio
from theme import GripSplitter, set_state

MONO = "Consolas"


def _mono(size=9):
    f = QFont(MONO)
    f.setPointSize(size)
    return f


def _ref_one_line(ref):
    """cmd_ref 单行摘要（详情/表格列用）"""
    if not isinstance(ref, dict) or not ref:
        return "（无）"
    bv = ref.get("by_vendor") or {}
    if bv:
        return "%s ｜ by_vendor×%d" % (ref.get("vendor_category") or "-", len(bv))
    parts = [ref.get("vendor_category") or ref.get("category") or "-"]
    for k in ("title_keyword", "os_family"):
        if ref.get(k):
            parts.append("%s=%s" % (k, ref[k]))
    return " ｜ ".join(parts)


# ===========================================================================
# cmd_ref 条目选择器（裁决③：uuid 只能从这里挑，不能手写）
# ===========================================================================

class CmdRefPickerDialog(QDialog):
    """从命令库挑选条目：关键字 + 厂商 + 场景分类组合搜索（复用 db.search）。

    选中后 self.entry 为条目 dict；取消为 None。uuid 永远来自库内数据，
    对话框不提供任何 uuid 文本输入。
    """

    def __init__(self, db, vendor_slug=None, category=None, vendor_locked=False,
                 parent=None):
        super(CmdRefPickerDialog, self).__init__(parent)
        self.db = db
        self.entry = None
        self.setWindowTitle("从命令库挑选条目")
        self.resize(860, 560)
        self._vendor_locked = bool(vendor_slug) and vendor_locked
        self._build()
        if vendor_slug:
            self.cmb_vendor.setCurrentText(vendor_slug)
            self.cmb_vendor.setEnabled(not self._vendor_locked)
        if category:
            self.cmb_cat.setCurrentText(category)
        self._do_search()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        row = QHBoxLayout()
        self.ed_kw = QLineEdit()
        self.ed_kw.setPlaceholderText("标题关键字，如：静态路由 / ethtool / OSPF 邻居")
        self.ed_kw.returnPressed.connect(self._do_search)
        row.addWidget(self.ed_kw, 2)
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.addItem("")                       # 全部厂商
        for v in sorted(dbmod.VENDOR_SLUGS):
            self.cmb_vendor.addItem(v)
        row.addWidget(self.cmb_vendor, 1)
        self.cmb_cat = QComboBox()
        self.cmb_cat.addItem("")                          # 全部场景
        for c in sorted(set(self.db.distinct("category")) | set(dbmod.ALL_CATEGORIES or [])):
            self.cmb_cat.addItem(c)
        row.addWidget(self.cmb_cat, 1)
        btn = QPushButton("搜索")
        btn.setObjectName("Primary")
        btn.clicked.connect(self._do_search)
        row.addWidget(btn)
        root.addLayout(row)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["标题", "厂商", "OS", "场景分类"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl.doubleClicked.connect(lambda _i: self._on_ok())
        root.addWidget(self.tbl, 1)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("StateLabel")
        root.addWidget(self.lbl_count)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("选定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _do_search(self, *_a):
        rows = self.db.search(
            keyword=self.ed_kw.text().strip(),
            vendor=self.cmb_vendor.currentText().strip() or None,
            category=self.cmb_cat.currentText().strip() or None)
        self.tbl.setRowCount(0)
        for e in rows:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            vals = [e.get("title") or "", e.get("vendor") or "",
                    e.get("os_family") or "", e.get("category") or ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.UserRole, e)
                self.tbl.setItem(r, c, item)
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.lbl_count.setText("命中 %d 条（骨架条目亦可选，validate 会照常把关）" % len(rows))
        if rows:
            self.tbl.selectRow(0)

    def _on_ok(self):
        r = self.tbl.currentRow()
        item = self.tbl.item(r, 0) if r >= 0 else None
        if not item:
            QMessageBox.information(self, "未选择", "请先在列表里选中一条条目。")
            return
        self.entry = item.data(Qt.UserRole)
        self.accept()


# ===========================================================================
# 步骤编辑对话框（种子编辑器专用：cmd_ref 为 by_vendor 每厂商逐一挑选）
# ===========================================================================

class SeedStepDialog(QDialog):
    """单个排查步骤的可视化编辑（cmd_ref 按树 vendor_hint 逐厂商从库内挑选）。

    键序保持：step 传入时在其 dict 副本上改字段（不重建），
    导出 diff 才不会被键序噪声淹没。
    """

    def __init__(self, db, tree, step=None, other_ids=None, parent=None):
        super(SeedStepDialog, self).__init__(parent)
        self.db = db
        self.tree = tree
        self.step = copy.deepcopy(step) if step else {}
        self.other_ids = set(other_ids or [])
        self.result_step = None
        self.setWindowTitle("编辑步骤" if step else "新增步骤")
        self.resize(860, 720)
        self._build()
        self._load()

    # ---------------- 构建 ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        form = QFormLayout()
        self.ed_id = QLineEdit()
        self.ed_id.setFont(_mono())
        self.ed_id.setPlaceholderText("步骤标识，树内唯一，如 s1 / s2（分支跳转靠它）")
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("这一步做什么，如：查接口物理层状态")
        self.ed_observe = QLineEdit()
        self.ed_observe.setPlaceholderText("让现场看什么，如：Line protocol is up 还是 down？")
        self.ed_explain = QLineEdit()
        self.ed_explain.setPlaceholderText("补充说明（可留空）")
        form.addRow("步骤 ID *", self.ed_id)
        form.addRow("标题 *", self.ed_title)
        form.addRow("观察点", self.ed_observe)
        form.addRow("说明", self.ed_explain)
        root.addLayout(form)

        # ---- cmd_ref：vendor_category + by_vendor 每厂商挑选 ----
        ref_box = QGroupBox("命令引用 cmd_ref（命令一律引用命令库条目，不允许写命令文本）")
        rv = QVBoxLayout(ref_box)
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("命令场景分类 *"))
        self.cmb_cat = QComboBox()
        self.cmb_cat.setEditable(True)
        for c in sorted(set(self.db.distinct("category")) | set(dbmod.ALL_CATEGORIES or [])):
            self.cmb_cat.addItem(c)
        cat_row.addWidget(self.cmb_cat, 1)
        rv.addLayout(cat_row)

        bv_row = QHBoxLayout()
        bv_row.addWidget(QLabel("各厂商条目（by_vendor）"))
        b_pick = QPushButton("为所选厂商挑选条目")
        b_pick.setObjectName("Primary")
        b_pick.clicked.connect(self._pick_vendor_entry)
        bv_row.addWidget(b_pick)
        b_clear = QPushButton("清除所选厂商条目")
        b_clear.setObjectName("Ghost")
        b_clear.clicked.connect(self._clear_vendor_entry)
        bv_row.addWidget(b_clear)
        bv_row.addStretch(1)
        rv.addLayout(bv_row)

        self.tbl_bv = QTableWidget(0, 3)
        self.tbl_bv.setHorizontalHeaderLabels(["厂商 slug", "命令条目（uuid 只读·经选择器挑选）", "uuid"])
        self.tbl_bv.verticalHeader().setVisible(False)
        self.tbl_bv.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_bv.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_bv.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_bv.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_bv.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_bv.setMaximumHeight(180)
        rv.addWidget(self.tbl_bv)
        self.ed_kw = QLineEdit()
        self.ed_kw.setPlaceholderText("遗留字段 title_keyword（原步骤有则保留，可清空）")
        rv.addWidget(self.ed_kw)
        self.ed_osf = QLineEdit()
        self.ed_osf.setPlaceholderText("遗留字段 os_family（原步骤有则保留，可清空）")
        self.ed_osf.setFont(_mono())
        rv.addWidget(self.ed_osf)
        tip = QLabel("uuid 列只读回显——条目只能点「为所选厂商挑选条目」从命令库选，禁止手写。")
        tip.setObjectName("Hint")
        rv.addWidget(tip)
        root.addWidget(ref_box)

        # ---- 分支 ----
        br_box = QGroupBox("分支（根据观察结果跳转；叶子步骤请留空）")
        bv2 = QVBoxLayout(br_box)
        self.tbl_branch = QTableWidget(0, 2)
        self.tbl_branch.setHorizontalHeaderLabels(["观察结果（when）", "跳转到步骤（goto）"])
        self.tbl_branch.verticalHeader().setVisible(False)
        self.tbl_branch.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_branch.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_branch.setMaximumHeight(130)
        bv2.addWidget(self.tbl_branch)
        br_btns = QHBoxLayout()
        b_add = QPushButton("加一条分支")
        b_add.setObjectName("Ghost")
        b_add.clicked.connect(lambda: self._add_branch("", ""))
        br_btns.addWidget(b_add)
        b_del = QPushButton("删除所选分支")
        b_del.setObjectName("Ghost")
        b_del.clicked.connect(self._del_branch)
        br_btns.addWidget(b_del)
        br_btns.addStretch(1)
        bv2.addLayout(br_btns)
        root.addWidget(br_box)

        # ---- 叶子 ----
        leaf_box = QGroupBox("叶子节点（走到这里就出结论）")
        lv = QVBoxLayout(leaf_box)
        self.chk_leaf = QCheckBox("本步骤是叶子（有结论）")
        self.chk_leaf.stateChanged.connect(self._on_leaf_toggle)
        lv.addWidget(self.chk_leaf)
        self.ed_conclusion = QLineEdit()
        self.ed_conclusion.setPlaceholderText("结论（人话），如：物理层不通，先换线缆/光模块")
        lv.addWidget(self.ed_conclusion)
        self.tbl_actions = QTableWidget(0, 2)
        self.tbl_actions.setHorizontalHeaderLabels(["处理动作（纯文字，不写命令）",
                                                    "命令来源（可选，经选择器从库内挑）"])
        self.tbl_actions.verticalHeader().setVisible(False)
        self.tbl_actions.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_actions.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_actions.setMaximumHeight(150)
        lv.addWidget(self.tbl_actions)
        a_btns = QHBoxLayout()
        a_add = QPushButton("加一条动作")
        a_add.setObjectName("Ghost")
        a_add.clicked.connect(lambda: self._add_action("", None))
        a_btns.addWidget(a_add)
        a_del = QPushButton("删除所选动作")
        a_del.setObjectName("Ghost")
        a_del.clicked.connect(self._del_action)
        a_pick = QPushButton("为所选动作挑选命令来源")
        a_pick.setObjectName("Ghost")
        a_pick.clicked.connect(self._pick_action_ref)
        a_btns.addWidget(a_add)
        a_btns.addWidget(a_del)
        a_btns.addWidget(a_pick)
        a_btns.addStretch(1)
        lv.addLayout(a_btns)
        root.addWidget(leaf_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------------- 载入 / 收集 ----------------
    def _vendor_rows(self):
        """by_vendor 表行 = 树 vendor_hint ∪ 既有 by_vendor 键（排序稳定）"""
        slugs = set(self.tree.get("vendor_hint") or [])
        ref = self.step.get("cmd_ref") or {}
        slugs |= set((ref.get("by_vendor") or {}).keys())
        return sorted(slugs)

    def _load(self):
        s = self.step
        self.ed_id.setText(s.get("id") or "")
        self.ed_title.setText(s.get("title") or "")
        self.ed_observe.setText(s.get("observe") or "")
        self.ed_explain.setText(s.get("explain") or "")
        ref = s.get("cmd_ref") if isinstance(s.get("cmd_ref"), dict) else {}
        self.cmb_cat.setCurrentText(ref.get("vendor_category") or ref.get("category") or "")
        self.ed_kw.setText(ref.get("title_keyword") or "")
        self.ed_osf.setText(ref.get("os_family") or "")
        for slug in self._vendor_rows():
            hit = (ref.get("by_vendor") or {}).get(slug)
            self._add_bv_row(slug, hit)
        for b in s.get("branches") or []:
            self._add_branch(b.get("when") or "", b.get("goto") or "")
        leaf = s.get("leafs") or {}
        if leaf:
            self.chk_leaf.setChecked(True)
            self.ed_conclusion.setText(leaf.get("conclusion") or "")
            for a in leaf.get("actions") or []:
                if isinstance(a, dict):
                    self._add_action(a.get("text") or "", a.get("cmd_ref"))
                else:
                    self._add_action(str(a), None)
        self._on_leaf_toggle()

    def _add_bv_row(self, slug, hit):
        r = self.tbl_bv.rowCount()
        self.tbl_bv.insertRow(r)
        it_slug = QTableWidgetItem(slug)
        it_slug.setData(Qt.UserRole, slug)
        self.tbl_bv.setItem(r, 0, it_slug)
        if isinstance(hit, dict) and hit.get("uuid"):
            entry = self.db.get_entry(hit["uuid"])
            title = hit.get("title") or (entry or {}).get("title") or "?"
            mark = "" if entry else "（库内不存在！）"
            self.tbl_bv.setItem(r, 1, QTableWidgetItem("%s %s" % (title, mark)))
            it_uuid = QTableWidgetItem(str(hit["uuid"]))
        else:
            self.tbl_bv.setItem(r, 1, QTableWidgetItem("（未指定——排查向导将回退分类模糊匹配）"))
            it_uuid = QTableWidgetItem("")
        it_uuid.setFont(_mono())
        it_uuid.setFlags(Qt.ItemIsEnabled)                # ★ 只读：禁止手写 uuid
        self.tbl_bv.setItem(r, 2, it_uuid)

    def _current_bv_slug(self):
        r = self.tbl_bv.currentRow()
        it = self.tbl_bv.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _pick_vendor_entry(self):
        slug = self._current_bv_slug()
        if not slug:
            QMessageBox.information(self, "未选中厂商", "请先在 by_vendor 表里选中一行厂商。")
            return
        dlg = CmdRefPickerDialog(self.db, vendor_slug=slug,
                                 category=self.cmb_cat.currentText().strip(),
                                 vendor_locked=True, parent=self)
        if dlg.exec_() != QDialog.Accepted or not dlg.entry:
            return
        entry = dlg.entry
        r = self.tbl_bv.currentRow()
        self.tbl_bv.setItem(r, 1, QTableWidgetItem(entry.get("title") or ""))
        it = QTableWidgetItem(entry.get("uuid") or "")
        it.setFont(_mono())
        it.setFlags(Qt.ItemIsEnabled)
        self.tbl_bv.setItem(r, 2, it)

    def _clear_vendor_entry(self):
        r = self.tbl_bv.currentRow()
        if r < 0:
            return
        self.tbl_bv.setItem(r, 1, QTableWidgetItem("（未指定——排查向导将回退分类模糊匹配）"))
        it = QTableWidgetItem("")
        it.setFont(_mono())
        it.setFlags(Qt.ItemIsEnabled)
        self.tbl_bv.setItem(r, 2, it)

    def _on_leaf_toggle(self, *_a):
        on = self.chk_leaf.isChecked()
        self.ed_conclusion.setEnabled(on)
        self.tbl_actions.setEnabled(on)

    def _add_branch(self, when, goto):
        r = self.tbl_branch.rowCount()
        self.tbl_branch.insertRow(r)
        self.tbl_branch.setItem(r, 0, QTableWidgetItem(when))
        combo = QComboBox()
        combo.addItem("")
        for sid in sorted(set([s.get("id") for s in self.tree.get("steps") or []]
                              + ([self.step.get("id")] if self.step.get("id") else [])
                              + ([self.ed_id.text().strip()] if self.ed_id.text().strip() else []))):
            if sid:
                combo.addItem(str(sid))
        combo.setEditable(False)
        if goto:
            combo.setCurrentText(str(goto))
        self.tbl_branch.setCellWidget(r, 1, combo)

    def _del_branch(self):
        r = self.tbl_branch.currentRow()
        if r >= 0:
            self.tbl_branch.removeRow(r)

    def _add_action(self, text, ref):
        r = self.tbl_actions.rowCount()
        self.tbl_actions.insertRow(r)
        item = QTableWidgetItem(text)
        item.setData(Qt.UserRole, ref)                    # 编辑文本不丢引用
        self.tbl_actions.setItem(r, 0, item)
        ref_txt = "（无）"
        if isinstance(ref, dict) and ref:
            u = ref.get("uuid")
            if u:
                e = self.db.get_entry(u)
                ref_txt = "%s（uuid %s…）" % ((e or {}).get("title") or ref.get("title") or "?",
                                             str(u)[:8])
            else:
                ref_txt = _ref_one_line(ref)
        self.tbl_actions.setItem(r, 1, QTableWidgetItem(ref_txt))

    def _del_action(self):
        r = self.tbl_actions.currentRow()
        if r >= 0:
            self.tbl_actions.removeRow(r)

    def _pick_action_ref(self):
        r = self.tbl_actions.currentRow()
        if r < 0:
            QMessageBox.information(self, "未选中动作", "请先在动作表里选中一行。")
            return
        dlg = CmdRefPickerDialog(self.db, parent=self)
        if dlg.exec_() != QDialog.Accepted or not dlg.entry:
            return
        entry = dlg.entry
        old = self.tbl_actions.item(r, 0).data(Qt.UserRole)
        if isinstance(old, dict) and old:
            old.clear()
            old.update({"uuid": entry["uuid"], "title": entry.get("title") or ""})
            ref = old
        else:
            ref = {"uuid": entry["uuid"], "title": entry.get("title") or ""}
        self.tbl_actions.item(r, 0).setData(Qt.UserRole, ref)
        self.tbl_actions.setItem(r, 1, QTableWidgetItem(
            "%s（uuid %s…）" % (entry.get("title") or "?", str(entry["uuid"])[:8])))

    def values(self):
        """收集成 step dict（在原 dict 副本上改，保留键序；无则新建）"""
        s = self.step or {}
        s["id"] = self.ed_id.text().strip()
        s["title"] = self.ed_title.text().strip()
        if self.ed_observe.text().strip():
            s["observe"] = self.ed_observe.text().strip()
        elif "observe" in s:
            del s["observe"]
        if self.ed_explain.text().strip():
            s["explain"] = self.ed_explain.text().strip()
        elif "explain" in s:
            del s["explain"]

        ref = s.get("cmd_ref") if isinstance(s.get("cmd_ref"), dict) else {}
        cat = self.cmb_cat.currentText().strip()
        if cat:
            ref["vendor_category"] = cat
        elif "vendor_category" in ref:
            del ref["vendor_category"]
        if self.ed_kw.text().strip():
            ref["title_keyword"] = self.ed_kw.text().strip()
        elif "title_keyword" in ref:
            del ref["title_keyword"]
        if self.ed_osf.text().strip():
            ref["os_family"] = self.ed_osf.text().strip()
        elif "os_family" in ref:
            del ref["os_family"]
        by_vendor = {}
        for r in range(self.tbl_bv.rowCount()):
            slug = self.tbl_bv.item(r, 0).data(Qt.UserRole)
            uuid = (self.tbl_bv.item(r, 2).text() or "").strip()
            if uuid:
                old = (ref.get("by_vendor") or {}).get(slug) or {}
                by_vendor[slug] = {"uuid": uuid,
                                   "title": old.get("title")
                                   or (self.db.get_entry(uuid) or {}).get("title") or ""}
        if by_vendor:
            ref["by_vendor"] = by_vendor
        elif "by_vendor" in ref:
            del ref["by_vendor"]
        if ref:
            s["cmd_ref"] = ref
        elif "cmd_ref" in s:
            del s["cmd_ref"]

        branches = []
        for r in range(self.tbl_branch.rowCount()):
            w = (self.tbl_branch.item(r, 0).text() if self.tbl_branch.item(r, 0) else "").strip()
            g = self.tbl_branch.cellWidget(r, 1)
            goto = g.currentText().strip() if isinstance(g, QComboBox) else ""
            if w or goto:
                branches.append({"when": w, "goto": goto})
        if branches:
            s["branches"] = branches
        elif "branches" in s:
            del s["branches"]

        if self.chk_leaf.isChecked():
            actions = []
            for r in range(self.tbl_actions.rowCount()):
                item = self.tbl_actions.item(r, 0)
                text = (item.text() if item else "").strip()
                if not text:
                    continue
                aref = item.data(Qt.UserRole) if item else None
                if isinstance(aref, dict) and aref:
                    actions.append({"text": text, "cmd_ref": aref})
                else:
                    actions.append({"text": text})
            leaf = s.get("leafs") or {}
            leaf["conclusion"] = self.ed_conclusion.text().strip()
            leaf["actions"] = actions
            s["leafs"] = leaf
        elif "leafs" in s:
            del s["leafs"]
        return s

    def _on_ok(self):
        step = self.values()
        if not step.get("id"):
            QMessageBox.warning(self, "缺少步骤 ID", "步骤 ID 必填（树内唯一，分支跳转靠它）。")
            return
        if step["id"] in self.other_ids:
            QMessageBox.warning(self, "步骤 ID 重复", "该 ID 已被本树其他步骤占用：%s" % step["id"])
            return
        if not step.get("title"):
            QMessageBox.warning(self, "缺少标题", "步骤标题必填。")
            return
        ref = step.get("cmd_ref") or {}
        if not step.get("leafs") and not (ref.get("vendor_category") or ref.get("by_vendor")):
            QMessageBox.warning(self, "缺少命令引用",
                                "非叶子步骤必须给 cmd_ref（场景分类 + 各厂商条目），"
                                "否则排查向导无法渲染命令。")
            return
        if not step.get("branches") and not step.get("leafs"):
            QMessageBox.warning(self, "缺下一步",
                                "既没有分支也没有结论，向导会走不下去："
                                "请加分支（跳转）或勾选「本步骤是叶子」。")
            return
        ids = set([x.get("id") for x in self.tree.get("steps") or []]) - self.other_ids
        ids.add(step["id"])
        for b in step.get("branches") or []:
            if b.get("goto") not in ids:
                QMessageBox.warning(self, "分支跳转无效",
                                    "跳转目标 %s 不在本树步骤里（可选：%s）"
                                    % (b.get("goto"), "、".join(sorted(x for x in ids if x))))
                return
        self.result_step = step
        self.accept()


# ===========================================================================
# 主对话框：只读预览 → 编辑模式 → 导出确认
# ===========================================================================

class TreeVisualEditorDialog(QDialog):
    """排查树可视化编辑器（MVP）

    左：树列表（seed_data 全量 26 棵）｜ 中：树形可视化预览 ｜ 右：步骤详情。
    「编辑模式」单向下锁（退出即关闭对话框），关闭时有未导出编辑先确认。
    """

    def __init__(self, db, parent=None):
        super(TreeVisualEditorDialog, self).__init__(parent)
        self.db = db
        self.trees = []            # [{"path","tree"}]
        self.working = None        # 当前树的可编辑深拷贝
        self.dirty = False
        self.edit_mode = False
        self.seed_dir = self._find_seed_dir()
        self.setWindowTitle("排查树可视化编辑器（种子导出）— MVP")
        self.resize(1240, 780)
        self._build()
        self._reload(show_status=False)

    # ---------------- 种子目录 ----------------
    def _find_seed_dir(self):
        for d in dbmod.seed_dirs():
            if tio.list_tree_files(d):
                return d
        return None

    # ---------------- 构建 ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        self.btn_edit = QPushButton("进入编辑模式")
        self.btn_edit.setObjectName("Primary")
        self.btn_edit.clicked.connect(self._on_edit_mode)
        top.addWidget(self.btn_edit)
        self.btn_export = QPushButton("导出种子差异…")
        self.btn_export.setObjectName("Primary")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._on_export)
        top.addWidget(self.btn_export)
        self.btn_reload = QPushButton("放弃编辑并重新载入")
        self.btn_reload.setObjectName("Ghost")
        self.btn_reload.clicked.connect(self._on_reload)
        top.addWidget(self.btn_reload)
        top.addStretch(1)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StateLabel")
        top.addWidget(self.lbl_status, 1)
        root.addLayout(top)

        split = GripSplitter(Qt.Horizontal)

        # ---- 左：树列表 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(4)
        self.lbl_list = QLabel("排查树（seed_data）")
        lv.addWidget(self.lbl_list)
        self.tbl_trees = QTableWidget(0, 4)
        self.tbl_trees.setHorizontalHeaderLabels(["现象", "分类", "步骤", "来源文件"])
        self.tbl_trees.verticalHeader().setVisible(False)
        self.tbl_trees.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_trees.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_trees.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_trees.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_trees.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_trees.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_trees.itemSelectionChanged.connect(self._on_tree_selected)
        lv.addWidget(self.tbl_trees, 1)
        split.addWidget(left)

        # ---- 中：可视化预览 ----
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(4, 0, 4, 0)
        mv.setSpacing(4)
        mv.addWidget(QLabel("树形预览（分支 → 跳转目标）"))
        self.tv_preview = QTreeWidget()
        self.tv_preview.setHeaderLabels(["节点"])
        self.tv_preview.setColumnCount(1)
        self.tv_preview.itemSelectionChanged.connect(self._on_preview_selected)
        mv.addWidget(self.tv_preview, 1)
        split.addWidget(mid)

        # ---- 右：步骤详情 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("步骤详情（只读；编辑请用下方按钮）"))
        self.txt_detail = QPlainTextEdit()
        self.txt_detail.setReadOnly(True)
        self.txt_detail.setFont(_mono(9))
        rv.addWidget(self.txt_detail, 1)

        sb = QHBoxLayout()
        for key, text, slot, obj in (("add", "添加步骤", self.on_add_step, "Primary"),
                                     ("edit", "编辑步骤", self.on_edit_step, ""),
                                     ("del", "删除步骤", self.on_del_step, "Ghost"),
                                     ("up", "上移", lambda: self.on_move_step(-1), "Ghost"),
                                     ("down", "下移", lambda: self.on_move_step(1), "Ghost")):
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(slot)
            b.setEnabled(False)
            sb.addWidget(b)
            setattr(self, "_stepbtn_%s" % key, b)
        sb.addStretch(1)
        rv.addLayout(sb)
        split.addWidget(right)
        split.setSizes([300, 420, 480])
        root.addWidget(split, 1)

        bottom = QHBoxLayout()
        tip = QLabel("MVP 只编辑既有树内部结构；导出 = 差异预览 → 确认 → validate 门禁，失败自动回滚。")
        tip.setObjectName("Hint")
        bottom.addWidget(tip, 1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self._on_close)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        if not self.seed_dir:
            self.lbl_status.setText("未找到含排查树 JSON 的 seed_data 目录（exe 内置种子只读），仅可预览。")
            set_state(self.lbl_status, "err")
            self.btn_edit.setEnabled(False)

    # ---------------- 数据载入 / 列表 ----------------
    def _reload(self, keep_tree_id=None, show_status=True):
        self.trees = []
        for path in tio.list_tree_files(self.seed_dir or ""):
            try:
                doc, _raw = tio.load_doc(path)
            except tio.TreeSeedError as exc:
                QMessageBox.warning(self, "种子文件异常", str(exc))
                continue
            for t in doc["trouble_trees"]:
                self.trees.append({"path": path, "tree": t})
        self.tbl_trees.blockSignals(True)
        self.tbl_trees.setRowCount(0)
        target = -1
        for i, rec in enumerate(self.trees):
            t = rec["tree"]
            r = self.tbl_trees.rowCount()
            self.tbl_trees.insertRow(r)
            vals = [t.get("symptom") or "", t.get("category") or "-",
                    str(len(t.get("steps") or [])),
                    os.path.basename(rec["path"])]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.UserRole, t.get("tree_id"))
                self.tbl_trees.setItem(r, c, item)
            if t.get("tree_id") == (keep_tree_id or (self.working or {}).get("tree_id")):
                target = r
        self.tbl_trees.blockSignals(False)
        self.tbl_trees.resizeColumnsToContents()
        self.tbl_trees.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        if self.trees:
            self.tbl_trees.selectRow(target if target >= 0 else 0)
        else:
            self.working = None
            self._render_all()
        if show_status:
            self.lbl_status.setText("已载入 %d 棵排查树（seed_data：%s）"
                                    % (len(self.trees), self.seed_dir))

    def _current_tree_id(self):
        r = self.tbl_trees.currentRow()
        it = self.tbl_trees.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _on_tree_selected(self):
        tid = self._current_tree_id()
        if not tid:
            self.working = None
            self._render_all()
            return
        for rec in self.trees:
            if rec["tree"].get("tree_id") == tid:
                self.working = copy.deepcopy(rec["tree"])
                break
        self.dirty = False
        self._render_all()

    # ---------------- 渲染 ----------------
    def _render_all(self):
        self._render_preview()
        self._render_detail()
        self._sync_buttons()
        if self.working:
            if self.dirty:
                self.lbl_status.setText("编辑中（未导出）：%s ｜ 步骤 %d 个"
                                        % (self.working.get("symptom") or "",
                                           len(self.working.get("steps") or [])))
            else:
                self.lbl_status.setText("只读预览：%s ｜ 点「进入编辑模式」修改"
                                        % (self.working.get("symptom") or ""))

    def _render_preview(self):
        self.tv_preview.clear()
        if not self.working:
            return
        t = self.working
        vh = "、".join(t.get("vendor_hint") or []) or "-"
        root_item = QTreeWidgetItem(["现象：%s ｜ [%s] ｜ 厂商：%s"
                                     % (t.get("symptom") or "(无现象)",
                                        t.get("category") or "-",
                                        vh)])
        root_item.setData(0, Qt.UserRole, None)
        self.tv_preview.addTopLevelItem(root_item)
        ids = set(s.get("id") for s in t.get("steps") or [])
        for s in t.get("steps") or []:
            leaf = s.get("leafs") or {}
            ref_stat = self._ref_status(s.get("cmd_ref"),
                                        t.get("vendor_hint") or [])
            label = "%s  %s  ｜ %s" % (s.get("id") or "?", s.get("title") or "(无标题)",
                                       ref_stat)
            if leaf:
                label += "  ◆ 叶子：%s" % ((leaf.get("conclusion") or "(无结论)")[:40])
            node = QTreeWidgetItem([label])
            node.setData(0, Qt.UserRole, s.get("id"))
            root_item.addChild(node)
            for b in s.get("branches") or []:
                goto = b.get("goto") or "?"
                mark = "" if goto in ids else "  ⚠ 目标不存在"
                child = QTreeWidgetItem(["    → %s ｜ %s%s" % (goto, b.get("when") or "?", mark)])
                child.setData(0, Qt.UserRole, s.get("id"))
                node.addChild(child)
        root_item.setExpanded(True)
        for i in range(root_item.childCount()):
            root_item.child(i).setExpanded(True)

    def _ref_status(self, ref, vendors):
        """cmd_ref 解析状态摘要（库内 uuid 实查，歧义/缺条目如实报）"""
        if not isinstance(ref, dict) or not ref:
            return "✗ 无 cmd_ref"
        bv = ref.get("by_vendor") or {}
        if not bv:
            return "? 分类模糊引用（%s）" % (ref.get("vendor_category")
                                          or ref.get("category") or "-")
        ok, bad = 0, []
        for slug, hit in bv.items():
            if isinstance(hit, dict) and hit.get("uuid") and self.db.get_entry(hit["uuid"]):
                ok += 1
            else:
                bad.append(slug)
        if bad:
            return "✗ %d/%d 厂商（缺/坏：%s）" % (ok, len(bv), "、".join(bad))
        return "✓ %d 厂商" % ok

    def _render_detail(self):
        self.txt_detail.setPlainText(self._detail_text())

    def _detail_text(self):
        if not self.working:
            return "（未选中排查树）"
        t = self.working
        tid = self._current_tree_id()
        sid = self._current_preview_step_id()
        lines = ["树：%s" % (t.get("symptom") or ""),
                 "  分类：%s ｜ 厂商：%s" % (t.get("category") or "-",
                                          "、".join(t.get("vendor_hint") or [])),
                 "  tree_id：%s" % t.get("tree_id"),
                 ""]
        for s in t.get("steps") or []:
            if sid and s.get("id") != sid:
                continue
            lines.append("步骤 %s  %s" % (s.get("id") or "?", s.get("title") or ""))
            if s.get("observe"):
                lines.append("  观察点：%s" % s["observe"])
            if s.get("explain"):
                lines.append("  说明：%s" % s["explain"])
            ref = s.get("cmd_ref") or {}
            lines.append("  cmd_ref：%s" % _ref_one_line(ref))
            for slug, hit in (ref.get("by_vendor") or {}).items():
                e = self.db.get_entry((hit or {}).get("uuid")) if isinstance(hit, dict) else None
                lines.append("    · %-10s %s%s" % (
                    slug,
                    (e or {}).get("title") or (hit or {}).get("title") or "（解析不到）",
                    "" if e else "   ← 库内不存在"))
            for b in s.get("branches") or []:
                lines.append("  分支：%s → %s" % (b.get("when") or "?", b.get("goto") or "?"))
            leaf = s.get("leafs") or {}
            if leaf:
                lines.append("  叶子结论：%s" % (leaf.get("conclusion") or "(无)"))
                for a in leaf.get("actions") or []:
                    if isinstance(a, dict):
                        extra = ""
                        ar = a.get("cmd_ref")
                        if isinstance(ar, dict) and ar.get("uuid"):
                            e = self.db.get_entry(ar["uuid"])
                            extra = "  ［命令来源：%s］" % ((e or {}).get("title") or "?")
                        lines.append("  动作：%s%s" % (a.get("text") or "", extra))
                    else:
                        lines.append("  动作：%s" % a)
            if sid:
                break
        if not sid:
            lines.append("")
            lines.append("（在中间预览里点选步骤节点查看详情）")
        return "\n".join(lines)

    def _current_preview_step_id(self):
        items = self.tv_preview.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _on_preview_selected(self):
        self._render_detail()

    def _sync_buttons(self):
        for name in ("add", "edit", "del", "up", "down"):
            b = getattr(self, "_stepbtn_%s" % name, None)
            if b:
                b.setEnabled(self.edit_mode and bool(self.working))
        has_step = self.edit_mode and self._current_preview_step_id() is not None
        for name in ("edit", "del", "up", "down"):
            b = getattr(self, "_stepbtn_%s" % name, None)
            if b:
                b.setEnabled(has_step)
        self.btn_export.setEnabled(self.edit_mode and self.dirty and bool(self.working))

    # ---------------- 模式 / 全局动作 ----------------
    def _on_edit_mode(self):
        if self.edit_mode:
            return
        if not self.working:
            return
        self.edit_mode = True
        self.btn_edit.setText("编辑模式（已解锁）")
        self.btn_edit.setEnabled(False)
        self._sync_buttons()
        self.lbl_status.setText("编辑模式：可添加/编辑/删除步骤、改引用；改完点「导出种子差异…」。")

    def _on_reload(self):
        if self.dirty and QMessageBox.question(
                self, "放弃编辑", "当前有未导出的编辑，放弃并从种子文件重新载入？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        keep = (self.working or {}).get("tree_id")
        self.dirty = False
        self._reload(keep_tree_id=keep)

    def _on_close(self):
        if self.dirty and QMessageBox.question(
                self, "未导出的编辑", "有未导出的编辑，关闭将丢弃。确认关闭？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.reject()

    # ---------------- 步骤级动作 ----------------
    def _mutate_steps(self):
        """编辑前置检查：返回步骤列表引用（working 深拷贝内的）"""
        if not (self.edit_mode and self.working):
            QMessageBox.information(self, "只读模式", "请先点「进入编辑模式」。")
            return None
        self.working.setdefault("steps", [])
        return self.working["steps"]

    def on_add_step(self):
        steps = self._mutate_steps()
        if steps is None:
            return
        exist = [s.get("id") for s in steps]
        dlg = SeedStepDialog(self.db, self.working, step=None,
                             other_ids=exist, parent=self)
        if dlg.exec_() == QDialog.Accepted and dlg.result_step:
            steps.append(dlg.result_step)
            self.dirty = True
            self._render_all()
            self.lbl_status.setText("已添加步骤 %s（未导出，改完点「导出种子差异…」）"
                                    % dlg.result_step.get("id"))

    def _current_step(self):
        sid = self._current_preview_step_id()
        steps = (self.working or {}).get("steps") or []
        for i, s in enumerate(steps):
            if s.get("id") == sid:
                return s, i
        return None, -1

    def on_edit_step(self):
        steps = self._mutate_steps()
        if steps is None:
            return
        step, row = self._current_step()
        if step is None:
            QMessageBox.information(self, "未选中", "请在预览里选中一个步骤节点。")
            return
        others = set(s.get("id") for i, s in enumerate(steps) if i != row)
        dlg = SeedStepDialog(self.db, self.working, step=step,
                             other_ids=others, parent=self)
        if dlg.exec_() == QDialog.Accepted and dlg.result_step:
            steps[row] = dlg.result_step
            self.dirty = True
            self._render_all()
            self.lbl_status.setText("已修改步骤 %s（未导出）" % dlg.result_step.get("id"))

    def on_del_step(self):
        steps = self._mutate_steps()
        if steps is None:
            return
        step, row = self._current_step()
        if step is None:
            return
        tid = step.get("id")
        users = []
        for s in steps:
            for b in s.get("branches") or []:
                if b.get("goto") == tid:
                    users.append(s.get("id"))
        msg = "删除步骤 %s（%s）？" % (tid, step.get("title"))
        if users:
            msg += "\n\n⚠ 步骤 %s 的分支跳转指向它，删除后请同步改这些分支。" % "、".join(users)
        if QMessageBox.question(self, "确认删除", msg,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        steps.pop(row)
        self.dirty = True
        self._render_all()
        self.lbl_status.setText("已删除步骤 %s（未导出）" % tid)

    def on_move_step(self, delta):
        steps = self._mutate_steps()
        if steps is None:
            return
        _step, row = self._current_step()
        if row < 0:
            return
        target = row + delta
        if target < 0 or target >= len(steps):
            return
        steps[row], steps[target] = steps[target], steps[row]
        self.dirty = True
        self._render_all()

    # ---------------- 导出：差异预览 → 确认 → 门禁写入 ----------------
    def _build_diff(self):
        """当前 working 树 vs 种子现文本的统一差异。返回 (diff_text, err)。"""
        try:
            path, cur = tio.locate_tree_file(self.seed_dir, self.working.get("tree_id"))
            if not path:
                return None, "种子目录里找不到 tree_id=%s" % self.working.get("tree_id")
            doc, old_bytes = tio.load_doc(path)
            old_text = old_bytes.decode("utf-8")
            doc2 = copy.deepcopy(doc)
            tio.replace_tree(doc2, copy.deepcopy(self.working))
            new_text = tio.serialize(doc2, crlf=tio.detect_crlf(old_bytes))
            diff = tio.unified_diff(old_text, new_text,
                                    fromfile=os.path.basename(path) + "（当前）",
                                    tofile=os.path.basename(path) + "（导出）")
            return diff, None
        except tio.TreeSeedError as exc:
            return None, str(exc)

    def _validate_gate(self):
        """门禁：main.validate_seed 进程内执行（CLI --validate-seed 同源同 EXIT 语义）"""
        import io as _io
        import contextlib
        import main as mainmod
        buf = _io.StringIO()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with contextlib.redirect_stdout(buf):
                rc = mainmod.validate_seed()
            output = buf.getvalue()
        finally:
            QApplication.restoreOverrideCursor()
        return rc, output

    def _on_export(self):
        if not (self.edit_mode and self.dirty and self.working):
            return
        diff, err = self._build_diff()
        if err:
            QMessageBox.critical(self, "导出失败", err)
            return
        if not diff:
            QMessageBox.information(self, "无差异", "当前编辑与种子内容一致，无需导出。")
            self.dirty = False
            self._render_all()
            return
        box = QDialog(self)
        box.setWindowTitle("导出确认——种子差异预览")
        box.resize(980, 640)
        v = QVBoxLayout(box)
        head = QLabel("以下差异将写入 seed_data；确认后运行 validate 门禁，"
                      "EXIT≠0 自动回滚（种子不留中间态）。")
        head.setObjectName("Hint")
        head.setWordWrap(True)
        v.addWidget(head)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setFont(_mono(9))
        txt.setPlainText(diff)
        v.addWidget(txt, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确认写入 seed_data")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(box.accept)
        btns.rejected.connect(box.reject)
        v.addWidget(btns)
        if box.exec_() != QDialog.Accepted:
            self.lbl_status.setText("已取消导出（种子文件未动）。")
            return
        try:
            ok, msg, output = tio.commit_tree(self.seed_dir, copy.deepcopy(self.working),
                                              self._validate_gate)
        except tio.TreeSeedError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        if ok:
            self.dirty = False
            tid = (self.working or {}).get("tree_id")
            self._reload(keep_tree_id=tid)
            self.lbl_status.setText(msg)
            QMessageBox.information(self, "导出成功",
                                    "%s\n\n树 %s 已可在主程序使用（重导入种子后生效）。"
                                    % (msg, tid))
        else:
            box2 = QDialog(self)
            box2.setWindowTitle("validate 未通过——已自动回滚")
            box2.resize(980, 640)
            v2 = QVBoxLayout(box2)
            h2 = QLabel(msg)
            h2.setObjectName("Hint")
            h2.setWordWrap(True)
            v2.addWidget(h2)
            t2 = QPlainTextEdit()
            t2.setReadOnly(True)
            t2.setFont(_mono(9))
            t2.setPlainText(output or "（无输出）")
            v2.addWidget(t2, 1)
            b2 = QDialogButtonBox(QDialogButtonBox.Ok)
            b2.button(QDialogButtonBox.Ok).setText("知道了（编辑仍保留，可继续修改）")
            b2.accepted.connect(box2.accept)
            v2.addWidget(b2)
            box2.exec_()
