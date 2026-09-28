# -*- coding: utf-8 -*-
"""
ui_tree_editor.py —— 排查树维护界面（第 3 轮 缺口B）

背景
    此前 add_tree / update_tree / delete_tree 三个 db 层方法**零 UI 调用**：
    排查树只能靠改 seed_data 的 JSON 或导入 .nlb 来维护，现场想加一棵树、
    改一个步骤的引用都做不到。本模块补上这条闭环。

能力（最小可用集）
    TreeManagerDialog
        · 树的 新建 / 编辑 / 保存 / 删除（全部落 history）
        · 树级字段：现象 / 现象分类 / 适用厂商（vendor_hint）
        · 步骤列表：增 / 改 / 删 / 上移 / 下移
    StepEditDialog
        · 步骤 id / 标题 / 说明 / 观察点
        · cmd_ref 可视化编辑（场景分类 + title_keyword + uuid 可选）
        · 分支（when → goto）
        · 叶子（结论 + 处理动作，动作可带命令来源，命令仍来自命令库条目）

硬约束：纯本地界面，不联网、不连设备；命令一律由 cmd_ref 引用命令库条目渲染。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QLabel, QLineEdit, QComboBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
                             QSplitter, QGroupBox, QCheckBox, QAbstractItemView,
                             QDialogButtonBox)

import db as dbmod

MONO = "Consolas"


def _mono(size=10):
    f = QFont(MONO)
    f.setPointSize(size)
    return f


def _ref_summary(ref):
    """cmd_ref 的人类可读摘要"""
    if not isinstance(ref, dict) or not ref:
        return "（无）"
    if ref.get("uuid"):
        return "uuid:%s…" % str(ref["uuid"])[:8]
    cat = ref.get("vendor_category") or ref.get("category") or "-"
    kw = ref.get("title_keyword") or ""
    osf = ref.get("os_family") or ""
    extra = " ｜".join(x for x in (("kw=" + kw) if kw else "", ("os=" + osf) if osf else "") if x)
    return "%s%s" % (cat, (" ｜" + extra) if extra else "")


class StepEditDialog(QDialog):
    """单个排查步骤的可视化编辑"""

    def __init__(self, db, other_step_ids=None, step=None, parent=None):
        super(StepEditDialog, self).__init__(parent)
        self.db = db
        self.other_ids = set(other_step_ids or [])
        self.step = dict(step or {})
        self.result_step = None
        self.setWindowTitle("编辑步骤" if step else "新增步骤")
        self.resize(760, 660)
        self._build()
        self._load()

    # ---------------- 构建 ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        form = QFormLayout()
        self.ed_id = QLineEdit()
        self.ed_id.setFont(_mono(10))
        self.ed_id.setPlaceholderText("步骤标识，树内唯一，如 s1 / s2（向导的分支靠它跳转）")
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("这一步做什么，如：查接口物理层状态")
        self.ed_explain = QLineEdit()
        self.ed_explain.setPlaceholderText("补充说明（可留空）")
        self.ed_observe = QLineEdit()
        self.ed_observe.setPlaceholderText("让现场看什么，如：Line protocol is up 还是 down？")
        form.addRow("步骤 ID *", self.ed_id)
        form.addRow("标题 *", self.ed_title)
        form.addRow("说明", self.ed_explain)
        form.addRow("观察点", self.ed_observe)

        # ---- cmd_ref：命令一律来自命令库条目 ----
        self.cmb_cat = QComboBox()
        self.cmb_cat.setEditable(True)
        cats = sorted(set(self.db.distinct("category")) |
                      set(dbmod.ALL_CATEGORIES or []))
        for c in cats:
            self.cmb_cat.addItem(c)
        self.cmb_cat.setCurrentText("")
        self.ed_kw = QLineEdit()
        self.ed_kw.setPlaceholderText("标题关键字（同场景多条时用它收敛到唯一一条）")
        self.ed_uuid = QLineEdit()
        self.ed_uuid.setFont(_mono(9))
        self.ed_uuid.setPlaceholderText("可选：直接填命令库条目 uuid（最可靠，填了就优先用它）")
        form.addRow("命令场景分类 *", self.cmb_cat)
        form.addRow("标题关键字", self.ed_kw)
        form.addRow("条目 uuid", self.ed_uuid)
        root.addLayout(form)

        tip = QLabel(
            "★ 步骤命令一律由 cmd_ref 引用命令库条目实时渲染（改库条目后树里跟着变），"
            "因此这里<b>不允许</b>填命令文本。\n"
            "引用要能唯一命中：同一厂商 + 同一场景下若有多条条目，请补标题关键字；"
            "同厂商多产品线可用 uuid 精确指定。")
        tip.setTextFormat(Qt.RichText)
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0b0; font-size:11px;")
        root.addWidget(tip)

        # ---- 分支 ----
        br_box = QGroupBox("分支（根据观察结果跳转；叶子步骤请留空）")
        bv = QVBoxLayout(br_box)
        self.tbl_branch = QTableWidget(0, 2)
        self.tbl_branch.setHorizontalHeaderLabels(["观察结果（when）", "跳转到步骤（goto）"])
        self.tbl_branch.verticalHeader().setVisible(False)
        self.tbl_branch.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_branch.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_branch.setMaximumHeight(140)
        bv.addWidget(self.tbl_branch)
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
        bv.addLayout(br_btns)
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
        self.tbl_actions.setHorizontalHeaderLabels(["处理动作（纯文字描述，不要写命令）",
                                                    "命令来源（可选，来自命令库）"])
        self.tbl_actions.verticalHeader().setVisible(False)
        self.tbl_actions.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_actions.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_actions.setMaximumHeight(170)
        lv.addWidget(self.tbl_actions)
        a_btns = QHBoxLayout()
        a_add = QPushButton("加一条动作")
        a_add.setObjectName("Ghost")
        a_add.clicked.connect(lambda: self._add_action("", None))
        a_btns.addWidget(a_add)
        a_del = QPushButton("删除所选动作")
        a_del.setObjectName("Ghost")
        a_del.clicked.connect(self._del_action)
        a_btns.addWidget(a_del)
        a_tip = QLabel("命令请通过步骤的 cmd_ref 取；动作里只写“做什么”。")
        a_tip.setStyleSheet("color:#9aa0b0; font-size:11px;")
        a_btns.addWidget(a_tip, 1)
        lv.addLayout(a_btns)
        root.addWidget(leaf_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------------- 载入 / 收集 ----------------
    def _load(self):
        s = self.step
        self.ed_id.setText(s.get("id") or "")
        self.ed_title.setText(s.get("title") or "")
        self.ed_explain.setText(s.get("explain") or "")
        self.ed_observe.setText(s.get("observe") or "")
        ref = s.get("cmd_ref") if isinstance(s.get("cmd_ref"), dict) else {}
        self.cmb_cat.setCurrentText(ref.get("vendor_category") or ref.get("category") or "")
        self.ed_kw.setText(ref.get("title_keyword") or "")
        self.ed_uuid.setText(ref.get("uuid") or "")
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

    def _on_leaf_toggle(self, *_a):
        on = self.chk_leaf.isChecked()
        self.ed_conclusion.setEnabled(on)
        self.tbl_actions.setEnabled(on)

    def _add_branch(self, when, goto):
        r = self.tbl_branch.rowCount()
        self.tbl_branch.insertRow(r)
        self.tbl_branch.setItem(r, 0, QTableWidgetItem(when))
        self.tbl_branch.setItem(r, 1, QTableWidgetItem(goto))

    def _del_branch(self):
        r = self.tbl_branch.currentRow()
        if r >= 0:
            self.tbl_branch.removeRow(r)

    def _add_action(self, text, ref):
        r = self.tbl_actions.rowCount()
        self.tbl_actions.insertRow(r)
        item = QTableWidgetItem(text)
        item.setData(Qt.UserRole, ref)          # 保留原有 cmd_ref，编辑文本时不丢引用
        self.tbl_actions.setItem(r, 0, item)
        src = QTableWidgetItem(_ref_summary(ref))
        src.setFlags(Qt.ItemIsEnabled)          # 只读：命令来源不在本对话框改
        self.tbl_actions.setItem(r, 1, src)

    def _del_action(self):
        r = self.tbl_actions.currentRow()
        if r >= 0:
            self.tbl_actions.removeRow(r)

    def values(self):
        """收集成 step dict"""
        ref = {}
        cat = self.cmb_cat.currentText().strip()
        if cat:
            ref["vendor_category"] = cat
        if self.ed_kw.text().strip():
            ref["title_keyword"] = self.ed_kw.text().strip()
        if self.ed_uuid.text().strip():
            ref["uuid"] = self.ed_uuid.text().strip()
        step = {
            "id": self.ed_id.text().strip(),
            "title": self.ed_title.text().strip(),
        }
        if self.ed_explain.text().strip():
            step["explain"] = self.ed_explain.text().strip()
        if self.ed_observe.text().strip():
            step["observe"] = self.ed_observe.text().strip()
        if ref:
            step["cmd_ref"] = ref
        branches = []
        for r in range(self.tbl_branch.rowCount()):
            w = (self.tbl_branch.item(r, 0).text() if self.tbl_branch.item(r, 0) else "").strip()
            g = (self.tbl_branch.item(r, 1).text() if self.tbl_branch.item(r, 1) else "").strip()
            if w or g:
                branches.append({"when": w, "goto": g})
        if branches:
            step["branches"] = branches
        if self.chk_leaf.isChecked():
            actions = []
            for r in range(self.tbl_actions.rowCount()):
                item = self.tbl_actions.item(r, 0)
                text = (item.text() if item else "").strip()
                if not text:
                    continue
                old_ref = item.data(Qt.UserRole) if item else None
                if isinstance(old_ref, dict) and old_ref:
                    actions.append({"text": text, "cmd_ref": old_ref})
                else:
                    actions.append({"text": text})
            step["leafs"] = {"conclusion": self.ed_conclusion.text().strip(),
                             "actions": actions}
        return step

    def _on_ok(self):
        step = self.values()
        if not step["id"]:
            QMessageBox.warning(self, "缺少步骤 ID", "步骤 ID 必填（树内唯一，分支跳转靠它）。")
            return
        if step["id"] in self.other_ids:
            QMessageBox.warning(self, "步骤 ID 重复", "该 ID 已被本树其他步骤占用：%s"
                                % step["id"])
            return
        if not step["title"]:
            QMessageBox.warning(self, "缺少标题", "步骤标题必填。")
            return
        if not step.get("cmd_ref") and not step.get("leafs"):
            QMessageBox.warning(self, "缺少命令引用",
                                "非叶子步骤必须填 cmd_ref（命令场景分类），"
                                "否则排查向导无法渲染命令。")
            return
        if not step.get("branches") and not step.get("leafs"):
            QMessageBox.warning(self, "缺下一步",
                                "既没有分支也没有结论，向导会走不下去："
                                "请加分支（跳转）或勾选“本步骤是叶子”。")
            return
        self.result_step = step
        self.accept()


class TreeManagerDialog(QDialog):
    """排查树维护：树的增 / 删 / 改 + 步骤维护，全部落 history"""

    def __init__(self, db, parent=None):
        super(TreeManagerDialog, self).__init__(parent)
        self.db = db
        self.tree = None            # 当前编辑中的树（dict 副本）
        self.trees = []
        self.setWindowTitle("排查树维护")
        self.resize(1180, 760)
        self._build()
        self.refresh()

    # ---------------- 构建 ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        split = QSplitter(Qt.Horizontal)

        # ---- 左：树列表 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(6)
        lv.addWidget(QLabel("排查树（按现象分类）"))
        self.tbl_trees = QTableWidget(0, 4)
        self.tbl_trees.setHorizontalHeaderLabels(["现象", "分类", "步骤", "已验证"])
        self.tbl_trees.verticalHeader().setVisible(False)
        self.tbl_trees.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_trees.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_trees.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_trees.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_trees.itemSelectionChanged.connect(self._on_tree_selected)
        lv.addWidget(self.tbl_trees, 1)
        lb = QHBoxLayout()
        self.btn_new = QPushButton("新建树")
        self.btn_new.setObjectName("Primary")
        self.btn_new.clicked.connect(self.on_new_tree)
        lb.addWidget(self.btn_new)
        self.btn_dup = QPushButton("复制一份")
        self.btn_dup.setObjectName("Ghost")
        self.btn_dup.clicked.connect(self.on_dup_tree)
        lb.addWidget(self.btn_dup)
        self.btn_del = QPushButton("删除树")
        self.btn_del.setObjectName("Ghost")
        self.btn_del.clicked.connect(self.on_del_tree)
        lb.addWidget(self.btn_del)
        lb.addStretch(1)
        lv.addLayout(lb)
        split.addWidget(left)

        # ---- 右：树详情 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(6)

        info_box = QGroupBox("树信息")
        form = QFormLayout(info_box)
        self.ed_symptom = QLineEdit()
        self.ed_symptom.setPlaceholderText("现象，如：同网段 ping 不通")
        self.cmb_cat = QComboBox()
        self.cmb_cat.setEditable(True)
        for c in dbmod.TREE_CATEGORIES:
            self.cmb_cat.addItem(c)
        self.ed_vendors = QLineEdit()
        self.ed_vendors.setPlaceholderText("适用厂商 slug，逗号分隔，如：cisco,huawei,h3c,ruijie")
        form.addRow("现象 *", self.ed_symptom)
        form.addRow("现象分类 *", self.cmb_cat)
        form.addRow("适用厂商 *", self.ed_vendors)
        rv.addWidget(info_box)

        step_box = QGroupBox("步骤（命令一律由 cmd_ref 引用命令库条目，不在树里写命令）")
        sv = QVBoxLayout(step_box)
        self.tbl_steps = QTableWidget(0, 5)
        self.tbl_steps.setHorizontalHeaderLabels(
            ["步骤 ID", "标题", "观察点", "命令引用", "分支 / 结论"])
        self.tbl_steps.verticalHeader().setVisible(False)
        self.tbl_steps.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_steps.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_steps.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_steps.doubleClicked.connect(lambda _i: self.on_edit_step())
        sv.addWidget(self.tbl_steps, 1)
        sb = QHBoxLayout()
        for text, slot, obj in (("添加步骤", self.on_add_step, "Primary"),
                                ("编辑步骤", self.on_edit_step, ""),
                                ("删除步骤", self.on_del_step, "Ghost"),
                                ("上移", lambda: self.on_move_step(-1), "Ghost"),
                                ("下移", lambda: self.on_move_step(1), "Ghost")):
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(slot)
            sb.addWidget(b)
        sb.addStretch(1)
        sv.addLayout(sb)
        rv.addWidget(step_box, 1)
        split.addWidget(right)
        split.setSizes([380, 800])
        root.addWidget(split, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#9aa0b0;")
        self.btn_save = QPushButton("保存树")
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(self.on_save)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.lbl_status, 1)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        if self.db.readonly:
            for w in (self.btn_new, self.btn_dup, self.btn_del, self.btn_save):
                w.setEnabled(False)
            self.lbl_status.setText("命令库处于只读状态（U 盘写保护），无法维护排查树。")
            self.lbl_status.setStyleSheet("color:#e05656;")

    # ---------------- 列表刷新 ----------------
    def refresh(self, keep=None):
        keep = keep or (self.tree or {}).get("tree_id")
        self.trees = self.db.all_trees()
        self.tbl_trees.blockSignals(True)
        self.tbl_trees.setRowCount(0)
        target = -1
        for t in self.trees:
            r = self.tbl_trees.rowCount()
            self.tbl_trees.insertRow(r)
            vals = [t.get("symptom") or "", t.get("category") or "-",
                    str(len(t.get("steps") or [])),
                    "是" if int(t.get("verified") or 0) == 1 else "否"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.UserRole, t.get("tree_id"))
                self.tbl_trees.setItem(r, c, item)
            if t.get("tree_id") == keep:
                target = r
        self.tbl_trees.blockSignals(False)
        self.tbl_trees.resizeColumnsToContents()
        self.tbl_trees.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        if target < 0 and self.trees:
            target = 0
        if target >= 0:
            self.tbl_trees.selectRow(target)
            # ★ 不能只依赖 itemSelectionChanged 来同步 self.tree：
            #   同一行被再次选中时 Qt 不发该信号（例如刚保存后 refresh(keep=同一棵树)），
            #   self.tree 会停留在旧的内存副本 —— 下一次保存就会写错树，甚至因为
            #   tree_id 为空而误新建一棵。这里显式同步一次。
            self._load_selected_tree()
        else:
            self.tree = None
            self._render_tree()
        if not self.db.readonly:
            self.lbl_status.setText("共 %d 棵排查树。" % len(self.trees))

    def _current_tree_id(self):
        r = self.tbl_trees.currentRow()
        if r < 0:
            return None
        item = self.tbl_trees.item(r, 0)
        return item.data(Qt.UserRole) if item else None

    def _load_selected_tree(self):
        """把当前选中行对应的树从库里重新载入 self.tree（内存副本）"""
        tid = self._current_tree_id()
        t = self.db.get_tree(tid) if tid else None
        self.tree = dict(t) if t else None
        self._render_tree()

    def _on_tree_selected(self):
        self._load_selected_tree()

    # ---------------- 右侧渲染 ----------------
    def _render_tree(self):
        t = self.tree or {}
        self.ed_symptom.setText(t.get("symptom") or "")
        self.cmb_cat.setCurrentText(t.get("category") or "")
        self.ed_vendors.setText(",".join(t.get("vendor_hint") or []))
        self.tbl_steps.setRowCount(0)
        for s in t.get("steps") or []:
            r = self.tbl_steps.rowCount()
            self.tbl_steps.insertRow(r)
            br = s.get("branches") or []
            leaf = s.get("leafs") or {}
            if leaf:
                summary = "叶子：%s" % ((leaf.get("conclusion") or "")[:34] or "（无结论）")
            elif br:
                summary = "，".join("%s→%s" % (b.get("when") or "?", b.get("goto") or "?")
                                    for b in br)[:60]
            else:
                summary = "（无分支，也非叶子 —— 向导会走不下去）"
            vals = [s.get("id") or "", s.get("title") or "", s.get("observe") or "",
                    _ref_summary(s.get("cmd_ref")), summary]
            for c, v in enumerate(vals):
                self.tbl_steps.setItem(r, c, QTableWidgetItem(str(v)))
        self.tbl_steps.resizeColumnsToContents()
        self.tbl_steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

    def _current_step(self):
        r = self.tbl_steps.currentRow()
        steps = (self.tree or {}).get("steps") or []
        if r < 0 or r >= len(steps):
            return None, -1
        return steps[r], r

    # ---------------- 树级动作 ----------------
    def _collect_tree_fields(self):
        symptom = self.ed_symptom.text().strip()
        category = self.cmb_cat.currentText().strip()
        vendors = [v.strip() for v in
                   (self.ed_vendors.text() or "").replace("；", ",").split(",") if v.strip()]
        return symptom, category, vendors

    def _ensure_tree_edited(self):
        """把右侧已改的树级字段写回内存中的 self.tree（不落库）"""
        if not self.tree:
            return
        symptom, category, vendors = self._collect_tree_fields()
        self.tree["symptom"] = symptom
        self.tree["category"] = category
        self.tree["vendor_hint"] = [dbmod.normalize_vendor(v) for v in vendors]

    def _rerender_keep_edits(self):
        """
        步骤表发生变化后重画右侧。

        ★ 必须先把表单里已改的树级字段回写进 self.tree 再重画 ——
          _render_tree() 是"self.tree → 表单"的单向渲染，直接调用会用旧值
          覆盖用户刚填的现象/分类/厂商。现场典型操作"填完现象 → 点添加步骤"
          就会把刚填的内容悄悄抹掉（真 bug，已验证）。
        """
        self._ensure_tree_edited()
        self._render_tree()

    def on_new_tree(self):
        if self.db.readonly:
            return
        # 默认套用网络组标准 vendor_hint（审计任务5：分组定义统一收敛在 db.TREE_HINT_GROUPS；
        # 防火墙树建树时把 vendor_hint 改成 db.TREE_HINT_GROUPS["firewall"] 即可挂靠防火墙组）
        self.tree = {"tree_id": "", "symptom": "（新现象，请修改）",
                     "category": dbmod.TREE_CATEGORIES[0],
                     "vendor_hint": list(dbmod.TREE_HINT_GROUPS["network"]),
                     "steps": [], "verified": 0}
        self._render_tree()
        self.lbl_status.setText("已新建（未落库）：填好现象/分类/适用厂商与步骤后点「保存树」。")

    def on_dup_tree(self):
        if self.db.readonly or not self.tree:
            return
        import copy
        self.tree = copy.deepcopy(self.tree)
        self.tree["tree_id"] = ""
        self.tree["symptom"] = (self.tree.get("symptom") or "") + " - 副本"
        self.tree["verified"] = 0
        self._render_tree()
        self.lbl_status.setText("已复制一份（未落库）：点「保存树」写入库。")

    def on_del_tree(self):
        tid = self._current_tree_id()
        if not tid:
            QMessageBox.information(self, "未选中", "请先在左侧选中一棵排查树。")
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法删除排查树。")
            return
        t = self.db.get_tree(tid) or {}
        if QMessageBox.question(
                self, "确认删除",
                "确认删除排查树「%s」？\n\n共 %d 个步骤。删除后 history 会保留删除记录，"
                "不可撤销。" % (t.get("symptom") or tid, len(t.get("steps") or [])),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.db.delete_tree(tid, operator="排查树维护")
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.tree = None
        self.refresh(keep=None)
        self.lbl_status.setText("已删除该排查树（history 已留痕）。")

    def on_save(self):
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法保存。")
            return
        if not self.tree:
            QMessageBox.information(self, "未选择", "先在左侧选一棵树，或点「新建树」。")
            return
        self._ensure_tree_edited()
        symptom, category, vendors = self._collect_tree_fields()
        if not symptom:
            QMessageBox.warning(self, "缺少现象", "现象必填。")
            return
        if not category:
            QMessageBox.warning(self, "缺少分类", "现象分类必填。")
            return
        if not vendors:
            QMessageBox.warning(self, "缺少适用厂商",
                                "适用厂商必填（排查向导按它决定用哪家的命令）。")
            return
        steps = self.tree.get("steps") or []
        if not steps:
            QMessageBox.warning(self, "没有步骤", "至少加一个步骤再保存。")
            return
        ids = [s.get("id") for s in steps]
        if len(set(ids)) != len(ids):
            QMessageBox.warning(self, "步骤 ID 重复", "步骤 ID 必须树内唯一。")
            return
        for s in steps:
            for b in s.get("branches") or []:
                if b.get("goto") not in ids:
                    QMessageBox.warning(self, "分支跳转无效",
                                        "步骤 %s 的分支跳转目标不存在：%s"
                                        % (s.get("id"), b.get("goto")))
                    return

        raw = {"symptom": symptom, "category": category,
               "vendor_hint": [dbmod.normalize_vendor(v) for v in vendors],
               "steps": steps}
        try:
            if self.tree.get("tree_id"):
                self.db.update_tree(self.tree["tree_id"], raw, operator="排查树维护")
                tid = self.tree["tree_id"]
                msg = "已保存修改（history 已留痕）。"
            else:
                tid = self.db.add_tree(raw, operator="排查树维护")
                msg = "已新建排查树：%s" % tid
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.refresh(keep=tid)
        self.lbl_status.setText(msg)

    # ---------------- 步骤级动作 ----------------
    def on_add_step(self):
        if not self.tree:
            QMessageBox.information(self, "未选择", "先在左侧选一棵树，或点「新建树」。")
            return
        exist = [s.get("id") for s in self.tree.get("steps") or []]
        dlg = StepEditDialog(self.db, other_step_ids=exist, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self.tree.setdefault("steps", []).append(dlg.result_step)
            self._rerender_keep_edits()
            self.lbl_status.setText("已添加步骤 %s（未落库，记得点「保存树」）。"
                                    % dlg.result_step.get("id"))

    def on_edit_step(self):
        step, row = self._current_step()
        if step is None:
            QMessageBox.information(self, "未选中", "请先在步骤表里选中一行。")
            return
        others = [s.get("id") for i, s in enumerate(self.tree.get("steps") or [])
                  if i != row]
        dlg = StepEditDialog(self.db, other_step_ids=others, step=step, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self.tree["steps"][row] = dlg.result_step
            self._rerender_keep_edits()
            self.tbl_steps.selectRow(row)
            self.lbl_status.setText("已修改步骤（未落库，记得点「保存树」）。")

    def on_del_step(self):
        step, row = self._current_step()
        if step is None:
            return
        if QMessageBox.question(self, "确认删除",
                                "删除步骤 %s（%s）？"
                                % (step.get("id"), step.get("title")),
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self.tree["steps"].pop(row)
        self._rerender_keep_edits()
        self.lbl_status.setText("已删除步骤（未落库，记得点「保存树」）。")

    def on_move_step(self, delta):
        _step, row = self._current_step()
        if row < 0:
            return
        target = row + delta
        steps = self.tree.get("steps") or []
        if target < 0 or target >= len(steps):
            return
        steps[row], steps[target] = steps[target], steps[row]
        self._rerender_keep_edits()
        self.tbl_steps.selectRow(target)
