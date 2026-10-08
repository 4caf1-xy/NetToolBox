# -*- coding: utf-8 -*-
"""
ui_troubleshoot.py —— 排查向导（模块 04）

决策树引擎 + 分步卡片 UI：
    左侧：现象列表，按五类分组（连通性/端口/性能/路由协议/管理面），绿徽章=整树已验证
    右侧：当前步骤卡片 —— 渲染命令（来自命令库条目）→ 观察项 → 分支按钮 → 下一步
          走到叶子显示结论 + 处理动作；顶部面包屑显示已走路径，可回退任意步

硬约束：
    ★ 树步骤的命令一律由 cmd_ref 引用命令库条目实时渲染（改库条目后树内命令跟着变），
      严禁在树/本文件里硬编码任何设备命令文本
    厂商由顶部下拉选择，cmd_ref 解析按该厂商进行（找不到条目时给友好提示）
"""

import os
import datetime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QCursor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QTreeWidget, QTreeWidgetItem, QPushButton, QPlainTextEdit,
                             QMessageBox, QFrame, QLineEdit,
                             QDialog, QScrollArea, QTableWidget, QTableWidgetItem,
                             QAbstractItemView, QMenu, QListWidget, QListWidgetItem,
                             QDialogButtonBox)

import time

import db as dbmod
import renderer
import output_analyzer
import report as report_mod
from ui_main import VERIFIED_COLOR, VerifyDialog, copy_to_clipboard

def _esc(text):
    """转义富文本特殊字符（QLabel 用 RichText 渲染时必须转义，避免内容破坏排版）"""
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _mono(size=10):
    f = QFont("Consolas")
    f.setPointSize(size)
    return f


from theme import set_state, GripSplitter, read_sizes  # 状态标签 / 分栏手柄 / 记忆校验
from theme import TEXT_MUTED, TREE_CATEGORY_COLORS  # C4 收敛散落色（审计 B18）

class TroubleshootTab(QWidget):
    """排查向导 Tab"""

    SPLIT_KEY = "ts_split"              # 现象树│步骤区分栏记忆 key（ui_state.json）
    SPLIT_MINS = (240, 420)             # 左树 / 右步骤区最小宽度

    def __init__(self, db, parent=None):
        super(TroubleshootTab, self).__init__(parent)
        self.db = db
        self.current_tree = None      # 当前树的 dict
        self.current_step_id = None
        self.path = []                # 已走过的步骤 id 列表（含当前）
        self.walk_log = []            # 排查记录（报告用）：步骤/命令/观察/选择/结论
        # ★ 结构化走树记录 / 叶子数据 / 实际命中厂商（[问 AI] 上下文用，任务1/4）
        self.walk_record = []         # [{step_id, title, cmd_rendered, branch_label}]
        self.leaf_data = None         # {conclusion, actions[]}；非叶子步骤恒为 None
        self._resolved_vendor = ""    # 本次 cmd_ref 实际命中的厂商 slug
        self.started_at = None        # 计时起点
        self.last_findings = []       # 最近一次输出分析的结论（可并入报告）
        self._leaf_cmd_text = ""      # 叶子处理动作里取自命令库的命令（整体复制用）
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---- 顶部：厂商选择 + 工具按钮 ----
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("设备厂商："))
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.setMinimumWidth(140)
        self.cmb_vendor.currentIndexChanged.connect(self._on_vendor_changed)
        top.addWidget(self.cmb_vendor)
        # 长说明改为 tooltip（原先是行内 QLabel，顶栏 9 个控件时会把说明挤到裁切）
        self.cmb_vendor.setToolTip("命令按所选厂商实时渲染，改库条目后这里跟着变")

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("按现象搜索（回车）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMaximumWidth(180)
        self.ed_search.returnPressed.connect(self.refresh)
        top.addWidget(self.ed_search)
        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("设备型号（写进报告）")
        self.ed_model.setMaximumWidth(170)
        top.addWidget(self.ed_model)
        self.ed_operator = QLineEdit()
        self.ed_operator.setPlaceholderText("操作人（写进报告）")
        self.ed_operator.setMaximumWidth(140)
        top.addWidget(self.ed_operator)
        top.addStretch(1)

        self.btn_restart = QPushButton("重新开始")
        self.btn_restart.setObjectName("Ghost")
        self.btn_restart.clicked.connect(self.restart)
        self.btn_copy_path = QPushButton("复制完整路径")
        self.btn_copy_path.setObjectName("Ghost")
        self.btn_copy_path.clicked.connect(self.copy_path_summary)
        self.btn_analyze = QPushButton("粘贴输出分析")
        self.btn_analyze.setToolTip("粘贴设备/系统的回显（show interfaces、df -h…）自动找异常项")
        self.btn_analyze.clicked.connect(self.open_analyzer)
        top.addWidget(self.btn_analyze)
        self.btn_reports = QPushButton("报告历史")
        self.btn_reports.setObjectName("Ghost")
        self.btn_reports.clicked.connect(self.open_report_history)
        top.addWidget(self.btn_reports)
        # ★ 排查树维护入口（第 3 轮 缺口B）：此前树只能靠改 seed JSON / 导入 .nlb 维护，
        #   现场想加一棵树、改一个步骤引用都做不到。
        self.btn_manage = QPushButton("管理排查树")
        self.btn_manage.setObjectName("Ghost")
        self.btn_manage.setToolTip("新建/编辑/删除排查树与步骤（全部落 history）")
        self.btn_manage.clicked.connect(self.open_tree_manager)
        top.addWidget(self.btn_manage)
        self.btn_mark = QPushButton("标记整树已验证")
        self.btn_mark.setObjectName("Ghost")
        self.btn_mark.clicked.connect(self.mark_tree_verified)
        top.addWidget(self.btn_restart)
        top.addWidget(self.btn_copy_path)
        top.addWidget(self.btn_mark)
        root.addLayout(top)

        # ---- 左：现象列表 / 右：步骤卡片 ----
        split = GripSplitter(Qt.Horizontal)

        self.tree_list = QTreeWidget()
        self.tree_list.setHeaderLabel("现象（按分类）")
        self.tree_list.setMinimumWidth(240)
        self.tree_list.itemClicked.connect(self.on_symptom_clicked)
        split.addWidget(self.tree_list)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 4, 4, 4)
        rv.setSpacing(6)

        # 面包屑 + 回退到任意步
        hist = QHBoxLayout()
        hist.setSpacing(6)
        self.lbl_breadcrumb = QLabel("选择左侧现象开始排查。")
        self.lbl_breadcrumb.setWordWrap(True)
        self.lbl_breadcrumb.setObjectName("Hint")
        hist.addWidget(self.lbl_breadcrumb, 1)
        self.lbl_ai_source = QLabel("")
        self.lbl_ai_source.setTextFormat(Qt.RichText)
        self.lbl_ai_source.setVisible(False)
        hist.addWidget(self.lbl_ai_source)
        hist.addWidget(QLabel("回退到："))
        self.cmb_history = QComboBox()
        self.cmb_history.setMinimumWidth(230)
        self.cmb_history.setToolTip("回退到已走过的任意一步（后面的路径作废）")
        self.cmb_history.currentIndexChanged.connect(self._on_history_jump)
        hist.addWidget(self.cmb_history)
        rv.addLayout(hist)

        # 当前步骤卡片
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)

        self.lbl_step_title = QLabel("")
        self.lbl_step_title.setObjectName("SectionTitle")
        self.lbl_step_title.setWordWrap(True)
        cv.addWidget(self.lbl_step_title)

        self.lbl_step_explain = QLabel("")
        self.lbl_step_explain.setWordWrap(True)
        self.lbl_step_explain.setObjectName("Hint")
        cv.addWidget(self.lbl_step_explain)

        cv.addWidget(QLabel("执行命令（来自命令库条目渲染）："))
        self.txt_step_commands = QPlainTextEdit()
        self.txt_step_commands.setReadOnly(True)
        self.txt_step_commands.setFont(_mono(10))
        self.txt_step_commands.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_step_commands.setObjectName("CodeBlock")
        self.txt_step_commands.setMinimumHeight(120)
        cv.addWidget(self.txt_step_commands, 2)

        btn_row = QHBoxLayout()
        self.btn_copy_cmd = QPushButton("复制该步命令")
        self.btn_copy_cmd.clicked.connect(self.copy_step_commands)
        btn_row.addWidget(self.btn_copy_cmd)
        # [问 AI]：把当前树现象/已走路径/本步命令与观察结果带入 AI 诊断 Tab
        self.btn_ask_ai = QPushButton("问 AI")
        self.btn_ask_ai.setObjectName("Ghost")
        self.btn_ask_ai.setToolTip("把当前现象、已走路径、本步命令与观察结果带入 AI 诊断 Tab 继续排查")
        self.btn_ask_ai.clicked.connect(self.ask_ai_about_step)
        btn_row.addWidget(self.btn_ask_ai)
        self.lbl_no_entry = QLabel("")
        self.lbl_no_entry.setObjectName("ErrorText")
        btn_row.addWidget(self.lbl_no_entry, 1)
        cv.addLayout(btn_row)

        self.lbl_observe = QLabel("")
        self.lbl_observe.setWordWrap(True)
        self.lbl_observe.setObjectName("HintText")
        cv.addWidget(QLabel("观察："))
        cv.addWidget(self.lbl_observe)

        self.lbl_branch_title = QLabel("根据观察结果选择：")
        self.lbl_branch_title.setObjectName("SectionTitle")
        cv.addWidget(self.lbl_branch_title)
        self.branch_area = QVBoxLayout()
        self.branch_area.setSpacing(4)
        cv.addLayout(self.branch_area)

        # 叶子结果卡片
        self.leaf_frame = QFrame()
        self.leaf_frame.setMinimumHeight(130)   # 防止被布局压成 0 高度
        self.leaf_frame.setFrameShape(QFrame.StyledPanel)
        self.leaf_frame.setObjectName("LeafCard")
        lv = QVBoxLayout(self.leaf_frame)
        lv.setContentsMargins(12, 10, 12, 10)
        lv.setSpacing(6)
        self.lbl_leaf_title = QLabel("✔ 排查结论")
        self.lbl_leaf_title.setObjectName("LeafTitle")
        lv.addWidget(self.lbl_leaf_title)
        self.lbl_leaf_conclusion = QLabel("")
        self.lbl_leaf_conclusion.setWordWrap(True)
        lv.addWidget(self.lbl_leaf_conclusion)
        self.lbl_leaf_actions = QLabel("")
        self.lbl_leaf_actions.setWordWrap(True)
        self.lbl_leaf_actions.setObjectName("InfoBody")
        lv.addWidget(self.lbl_leaf_actions)

        # 叶子"补充命令"区（P1-5）：处理动作里带 cmd_ref 的，命令一律从命令库条目渲染出来，
        # 不再把命令文本硬编码在树里 —— 改库条目后这里跟着变。
        self.lbl_leaf_cmds = QLabel("")
        self.lbl_leaf_cmds.setWordWrap(True)
        self.lbl_leaf_cmds.setObjectName("Hint")
        lv.addWidget(self.lbl_leaf_cmds)
        self.txt_leaf_cmds = QPlainTextEdit()
        self.txt_leaf_cmds.setReadOnly(True)
        self.txt_leaf_cmds.setFont(_mono(10))
        self.txt_leaf_cmds.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_leaf_cmds.setObjectName("CodeBlock")
        self.txt_leaf_cmds.setMaximumHeight(140)
        self.txt_leaf_cmds.setVisible(False)
        lv.addWidget(self.txt_leaf_cmds)
        self.btn_copy_leaf_cmds = QPushButton("复制处理动作命令")
        self.btn_copy_leaf_cmds.setObjectName("Ghost")
        self.btn_copy_leaf_cmds.setToolTip("复制本页处理动作中取自命令库的命令（只复制，不连设备）")
        self.btn_copy_leaf_cmds.clicked.connect(self.copy_leaf_commands)
        self.btn_copy_leaf_cmds.setVisible(False)
        lv.addWidget(self.btn_copy_leaf_cmds, 0, Qt.AlignLeft)

        leaf_btns = QHBoxLayout()
        self.btn_report = QPushButton("生成排查报告")
        self.btn_report.setToolTip("把本次路径+命令+结论导出成 HTML/纯文本报告（存 trouble_reports/）")
        self.btn_report.clicked.connect(self.generate_report)
        leaf_btns.addWidget(self.btn_report)
        self.btn_leaf_analyze = QPushButton("分析一份输出补充结论")
        self.btn_leaf_analyze.setObjectName("Ghost")
        self.btn_leaf_analyze.clicked.connect(self.open_analyzer)
        leaf_btns.addWidget(self.btn_leaf_analyze)
        leaf_btns.addStretch(1)
        lv.addLayout(leaf_btns)
        self.leaf_frame.setVisible(False)

        rv.addWidget(card, 3)
        rv.addWidget(self.leaf_frame, 1)

        split.addWidget(right)
        right.setMinimumWidth(self.SPLIT_MINS[1])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 1100])
        root.addWidget(split, 1)
        self.split = split

    # ---- 分栏状态记忆（MainWindow 统一收口，任务2-2/2-3）----
    def split_state(self):
        try:
            return {self.SPLIT_KEY: list(self.split.sizes())}
        except Exception:
            return {}

    def restore_split_state(self, state):
        """恢复分栏比例；Tab 未显示时 QSplitter 未布局，值存 pending 首次 show 应用"""
        self._pending_split = read_sizes(state, self.SPLIT_KEY, self.SPLIT_MINS)
        if self._pending_split and self.isVisible():
            self._apply_pending_split()

    def _apply_pending_split(self):
        if getattr(self, "_pending_split", None):
            self.split.setSizes(self._pending_split)
            self._pending_split = None

    def showEvent(self, event):
        super(TroubleshootTab, self).showEvent(event)
        self._apply_pending_split()

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def refresh(self):
        """重建左侧现象列表（命令库/排查树有变化时由主窗调用）"""
        keep = self.current_tree.get("tree_id") if self.current_tree else None
        self.tree_list.clear()
        keyword = (self.ed_search.text() or "").strip() if hasattr(self, "ed_search") else ""
        trees = self.db.all_trees()
        if keyword:
            kw = keyword.lower()
            trees = [t for t in trees if kw in (t.get("symptom") or "").lower()
                     or kw in (t.get("category") or "").lower()]

        for category in dbmod.TREE_CATEGORIES:
            group = [t for t in trees if t.get("category") == category]
            if not group:
                continue
            color = TREE_CATEGORY_COLORS.get(category, TEXT_MUTED)
            cat_item = QTreeWidgetItem(["%s（%d）" % (category, len(group))])
            cat_item.setForeground(0, QColor(color))
            self.tree_list.addTopLevelItem(cat_item)
            for t in sorted(group, key=lambda x: x.get("symptom") or ""):
                verified = int(t.get("verified") or 0) == 1
                vendors = "、".join(dbmod.display_vendor(v)
                                    for v in (t.get("vendor_hint") or [])[:4])
                text = "%s %s" % ("✔ " if verified else "◇ ",
                                  t.get("symptom") or t.get("tree_id"))
                item = QTreeWidgetItem([text])
                item.setData(0, Qt.UserRole, t.get("tree_id"))
                item.setForeground(0, QColor(VERIFIED_COLOR if verified else "#e8eaf0"))
                item.setToolTip(0, "分类：%s\n适用厂商：%s\n%s"
                               % (category, vendors or "-",
                                  "已验证（真机走通过）" if verified else "未验证"))
                cat_item.addChild(item)
            cat_item.setExpanded(True)

        # 恢复刷新前的选中项：keep 本来就是为此保留的，
        # 此前只被一段空转的死代码引用（已清理），这里把意图补全 ——
        # 否则每次刷新（改库条目后主窗都会调用）列表选中项都会丢失。
        if keep:
            for i in range(self.tree_list.topLevelItemCount()):
                cat = self.tree_list.topLevelItem(i)
                for j in range(cat.childCount()):
                    ch = cat.child(j)
                    if ch.data(0, Qt.UserRole) == keep:
                        self.tree_list.setCurrentItem(ch)
                        break
        # 顶部厂商下拉（网络设备厂商）
        cur = self.cmb_vendor.currentData()
        self.cmb_vendor.blockSignals(True)
        self.cmb_vendor.clear()
        self.cmb_vendor.addItem("自动匹配", "")
        for slug in sorted(self.db.distinct("vendor", platform="network")):
            self.cmb_vendor.addItem(dbmod.display_vendor(slug), slug)
        idx = self.cmb_vendor.findData(cur) if cur else 0
        self.cmb_vendor.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_vendor.blockSignals(False)

    def focus_category(self, category=None, tree_id=None):
        """跨 Tab 跳转入口：命令库的[去排查树]按钮调用"""
        if tree_id:
            tree = self.db.get_tree(tree_id)
            if tree:
                self.start_tree(tree_id)
                return
        # 按分类定位：单棵直接进，多棵让用户选（C3：此前静默取第一棵）
        if category:
            trees = [t for t in self.db.all_trees() if t.get("category") == category]
            if len(trees) == 1:
                self.start_tree(trees[0].get("tree_id"))
                return
            if trees:
                self._pick_tree(trees, category)
                return
        self.refresh()

    def _pick_tree(self, trees, category):
        """同分类多棵树的选择交互：≤3 棵 QMenu 轻选，更多弹带关键字过滤的对话框"""
        def symptom_of(t):
            return t.get("symptom") or t.get("tree_id") or "（未命名）"

        if len(trees) <= 3:
            menu = QMenu(self)
            acts = [menu.addAction(symptom_of(t)) for t in trees]
            menu.addSeparator()
            act_cancel = menu.addAction("取消")
            chosen = menu.exec_(QCursor.pos())
            if chosen and chosen is not act_cancel and chosen in acts:
                self.start_tree(trees[acts.index(chosen)].get("tree_id"))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("选择排查树（%s · 共 %d 棵）" % (category, len(trees)))
        dlg.resize(420, 360)
        v = QVBoxLayout(dlg)
        ed = QLineEdit()
        ed.setPlaceholderText("输入现象关键字过滤…")
        lst = QListWidget()
        for t in trees:
            lst.addItem(QListWidgetItem(symptom_of(t)))
        ed.textChanged.connect(lambda s: [
            lst.item(i).setHidden(s.lower() not in lst.item(i).text().lower())
            for i in range(lst.count())])
        lst.itemDoubleClicked.connect(lambda _item: dlg.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(ed)
        v.addWidget(lst)
        v.addWidget(buttons)
        if dlg.exec_() == QDialog.Accepted and lst.currentItem() is not None:
            self.start_tree(trees[lst.currentRow()].get("tree_id"))

    # ------------------------------------------------------------------
    # 树行走
    # ------------------------------------------------------------------
    def on_symptom_clicked(self, item, _column):
        tree_id = item.data(0, Qt.UserRole)
        if tree_id:
            self.start_tree(tree_id)

    def start_tree(self, tree_id):
        tree = self.db.get_tree(tree_id)
        if not tree:
            return
        self.current_tree = tree
        self.path = []
        self.walk_log = []
        # ★ 结构化走树记录 / 叶子数据 / 实际命中厂商（[问 AI] 用，任务1/4）
        self.walk_record = []          # [{step_id, title, cmd_rendered, branch_label}]
        self.leaf_data = None          # {conclusion, actions[]}；非叶子步骤恒为 None
        self._resolved_vendor = ""     # 本次 cmd_ref 实际命中的厂商 slug
        self.started_at = time.time()
        self.last_findings = []
        self._update_ai_source(tree)
        self._goto_first_step()

    def _update_ai_source(self, tree):
        """AI 溯源（🤖）：树来自 AI 会话入库时显示来源，点击跳转会话定位回复"""
        try:
            from ui_ai import make_ai_source_label, open_ai_source
            recs = self.db.imports_for_target(tree.get("tree_id"))
        except Exception:
            recs = []
        pair = make_ai_source_label(self.db, tree.get("tree_id")) if recs else None
        try:
            self.lbl_ai_source.linkActivated.disconnect()
        except Exception:
            pass
        if pair:
            _lbl, rec = pair
            self.lbl_ai_source.setText(_lbl.text())
            self.lbl_ai_source.linkActivated.connect(
                lambda *_: open_ai_source(self.window(), rec))
            self.lbl_ai_source.setVisible(True)
        else:
            self.lbl_ai_source.setVisible(False)

    def _steps(self):
        if not self.current_tree:
            return []
        return self.current_tree.get("steps") or []

    def _step_by_id(self, step_id):
        for s in self._steps():
            if s.get("id") == step_id:
                return s
        return None

    def _goto_first_step(self):
        steps = self._steps()
        if not steps:
            return
        self.path = [steps[0].get("id")]
        self.render_current_step()

    def _goto(self, step_id, when=""):
        """进入下一步；when 是本次分支选择的观察结果（写进排查记录）"""
        if when and self.walk_log:
            self.walk_log[-1]["when"] = when
        # ★ 结构化走树记录：把"本次选择的观察选项"记在离开的那一步上
        #   （它属于当前步的分支判定依据，不属于下一一步）
        if when and self.walk_record and self.walk_record[-1]["step_id"] == self.current_step_id:
            self.walk_record[-1]["branch_label"] = when
        self.path.append(step_id)
        self.render_current_step()

    def _sync_walk_record(self):
        """
        让 walk_record 与当前 path 对齐：path 被截断（上一步/面包屑回退）时，
        多余的记录一并丢弃 —— 这是"回退后旧结论不得残留"的第一道保证。
        """
        while len(self.walk_record) > len(self.path):
            self.walk_record.pop()

    def render_current_step(self):
        if not self.path:
            return
        step = self._step_by_id(self.path[-1])
        if not step:
            return
        self.current_step_id = step.get("id")
        self._sync_walk_record()

        # 面包屑（可读的路径展示）
        titles = []
        for sid in self.path:
            s = self._step_by_id(sid)
            titles.append("%s %s" % (sid, (s or {}).get("title", "")))
        self.lbl_breadcrumb.setText(
            "路径：%s ｜ 当前：%s" % ("  →  ".join(titles), self.current_step_id))
        # 回退下拉：列出已走路径（当前项在最后），选中即回退
        self.cmb_history.blockSignals(True)
        self.cmb_history.clear()
        for i, sid in enumerate(self.path):
            s = self._step_by_id(sid) or {}
            self.cmb_history.addItem("%d. %s %s" % (i + 1, sid,
                                                    (s.get("title") or "")[:22]),
                                     sid)
        self.cmb_history.setCurrentIndex(len(self.path) - 1)
        self.cmb_history.blockSignals(False)

        # 步骤内容
        self.lbl_step_title.setText("步骤 %s： %s" % (step.get("id"), step.get("title") or ""))
        self.lbl_step_explain.setText(step.get("explain") or "")

        # 命令渲染：cmd_ref → 命令库条目
        # ★ 厂商取下拉选择；选"自动匹配"（空值）时退化为本树 vendor_hint 的第一个厂商。
        #   否则 vendor 为空会跨厂商取候选，必然多义、结果也不确定。
        vendor = self.cmb_vendor.currentData()
        if not vendor:
            hints = (self.current_tree or {}).get("vendor_hint") or []
            vendor = hints[0] if hints else ""
        entry, reason = self.db.resolve_cmd_ref_ex(step.get("cmd_ref"), vendor=vendor)
        if entry:
            text, _missing, _specs = renderer.render_entry(entry, {})
            self.txt_step_commands.setPlainText(text)
            self.btn_copy_cmd.setEnabled(True)
            self.lbl_no_entry.setText("")
            self._current_entry = entry
        else:
            self.txt_step_commands.setPlainText(self._no_entry_hint(reason, vendor))
            self.btn_copy_cmd.setEnabled(False)
            self.lbl_no_entry.setText(self._no_entry_label(reason, vendor))
            self._current_entry = None

        self.lbl_observe.setText(step.get("observe") or "")

        # 写排查记录（报告用）：同一 step 不重复记录
        if not (self.walk_log and self.walk_log[-1]["step_id"] == step.get("id")):
            self.walk_log.append({
                "step_id": step.get("id"), "title": step.get("title") or "",
                "observe": step.get("observe") or "",
                "cmd": self.txt_step_commands.toPlainText(), "when": "",
                "conclusion": "", "actions": [],
            })

        # ★ 结构化走树记录（[问 AI] 上下文用）：{step_id, title, cmd_rendered, branch_label}
        #   branch_label 在离开该步（选择分支）时由 _goto 回填；当前步尚未选择则为空。
        if self.walk_record and self.walk_record[-1]["step_id"] == step.get("id"):
            self.walk_record[-1]["cmd_rendered"] = self.txt_step_commands.toPlainText()
        else:
            self.walk_record.append({
                "step_id": step.get("id"),
                "title": step.get("title") or "",
                "cmd_rendered": self.txt_step_commands.toPlainText(),
                "branch_label": "",
            })
        # 记录本次 resolve 实际命中的厂商（任务4：厂商上下文补传）
        self._resolved_vendor = vendor if entry else ""

        # 分支 / 叶子
        self._clear_layout(self.branch_area)
        leaf = step.get("leafs")
        branches = step.get("branches") or []
        if leaf:
            self.leaf_frame.setVisible(True)
            self.lbl_leaf_conclusion.setText(leaf.get("conclusion") or "")
            actions = leaf.get("actions") or []
            self._render_leaf_actions(actions, vendor, leaf.get("conclusion") or "")
            self.lbl_branch_title.setText("排查完成。处理动作：")
        else:
            # ★ 非叶子步骤：结论必须失效 —— 回退到中途步后，上一次走到底的
            #   leaf_data 绝不能残留在下次 [问 AI] 的预填里
            self.leaf_data = None
            self.leaf_frame.setVisible(False)
            # 清掉上一处叶子留下的"补充命令"，避免切到非叶子步骤时残留
            self._leaf_cmd_text = ""
            self.txt_leaf_cmds.setVisible(False)
            self.btn_copy_leaf_cmds.setVisible(False)
            self.lbl_leaf_cmds.setText("")
            self.lbl_branch_title.setText("根据观察结果选择：")
            for b in branches:
                btn = QPushButton("➜ %s" % b.get("when", ""))
                btn.setObjectName("BranchBtn")
                btn.clicked.connect(lambda _=False, sid=b.get("goto"),
                                    w=b.get("when"): self._goto(sid, w))
                self.branch_area.addWidget(btn)
            if not branches:
                self.branch_area.addWidget(QLabel("（该步骤没有分支，也没有结论——树数据有误）"))
            back = QPushButton("↩ 上一步")
            back.setObjectName("Ghost")
            back.clicked.connect(self.go_back)
            back.setEnabled(len(self.path) > 1)
            self.branch_area.addWidget(back)

    # ---- 叶子"处理动作"渲染（★ P1-5：命令一律来自命令库，树里不再硬编码）----
    def _render_leaf_actions(self, actions, vendor, conclusion=""):
        """
        渲染叶子节点的处理动作清单。动作有两种结构：
            "纯文本动作描述"                          ← 兼容旧数据
            {"text": "动作描述", "cmd_ref": {...}}    ← P1-5 起的新结构
        带 cmd_ref 的动作，其命令**从命令库条目实时渲染**（uuid / vendor_category 引用），
        改库条目后这里跟着变；解析不到就明确告警，绝不把命令文本硬编码回树里。

        动作清单（连同各自渲染出的命令）会写入 walk_log，供排查报告复用。
        """
        items, cmd_blocks, cmd_texts = [], [], []
        for i, a in enumerate(actions, 1):
            if isinstance(a, dict):
                atext = str(a.get("text") or "")
                aref = a.get("cmd_ref") if isinstance(a.get("cmd_ref"), dict) else None
            else:
                atext, aref = str(a), None

            acmd, awarn = "", ""
            if aref:
                a_entry, a_reason = self.db.resolve_cmd_ref_ex(aref, vendor=vendor)
                if a_entry:
                    acmd, _m, _s = renderer.render_entry(a_entry, {})
                    cmd_texts.append(acmd)
                    cmd_blocks.append("【动作 %d｜%s】\n%s"
                                      % (i, a_entry.get("title") or "", acmd))
                else:
                    awarn = self._no_entry_label(a_reason, vendor)
                    cmd_blocks.append("【动作 %d】未取到命令：%s" % (i, awarn))
            shown = "%d. %s" % (i, atext)
            if awarn:
                shown += "（%s）" % awarn
            items.append({"text": atext, "cmd": acmd, "warn": awarn, "shown": shown})

        self.lbl_leaf_actions.setText("\n".join(it["shown"] for it in items))
        if self.walk_log:
            self.walk_log[-1]["conclusion"] = conclusion
            self.walk_log[-1]["actions"] = items
        # ★ 叶子结论数据（[问 AI] 上下文用）：结论 + 处理动作（含各自渲染出的命令）
        self.leaf_data = {"conclusion": conclusion or "",
                          "actions": [{"text": it["text"], "cmd": it["cmd"],
                                       "shown": it["shown"], "warn": it["warn"]}
                                      for it in items]}

        self._leaf_cmd_text = "\n\n".join(cmd_texts)
        has_warn = any(it["warn"] for it in items)
        self.txt_leaf_cmds.setPlainText("\n\n".join(cmd_blocks))
        self.txt_leaf_cmds.setVisible(bool(cmd_blocks))
        self.btn_copy_leaf_cmds.setVisible(bool(cmd_texts))
        self.btn_copy_leaf_cmds.setText("复制处理动作命令")
        if cmd_texts:
            self.lbl_leaf_cmds.setText(
                "下列 %d 条命令取自命令库条目（改库条目后自动同步）：" % len(cmd_texts))
        elif has_warn:
            self.lbl_leaf_cmds.setText("部分动作未取到命令 —— 提示见上方，可在命令库补条目。")
        else:
            self.lbl_leaf_cmds.setText("")
        self.lbl_leaf_cmds.setVisible(bool(cmd_blocks))

    def copy_leaf_commands(self):
        """复制叶子处理动作里取自命令库的命令（只复制，不连设备）"""
        text = getattr(self, "_leaf_cmd_text", "") or ""
        if not text.strip():
            self._status("当前没有可复制的处理动作命令。")
            return
        if not copy_to_clipboard(text):
            self._status("复制失败（剪贴板被占用），请手动选中后复制。")
            return
        self._status("已复制处理动作命令（%d 行）" % len(
            [ln for ln in text.splitlines() if ln.strip()]))
        try:
            self.btn_copy_leaf_cmds.setText("✓ 已复制")
            QTimer.singleShot(1200, self._restore_leaf_btn)
        except RuntimeError:
            pass                    # 控件已销毁，跳过反馈即可（复制已完成）

    def _restore_leaf_btn(self):
        try:
            self.btn_copy_leaf_cmds.setText("复制处理动作命令")
        except RuntimeError:
            pass

    # ---- 解析失败时的提示（区分"歧义"与"缺条目"，避免用户误以为数据没配）----
    def _no_entry_hint(self, reason, vendor):
        """命令区里显示的说明文本"""
        if reason == "ambiguous":
            return ("# 本步骤的 cmd_ref 存在歧义：同一厂商 + 同一场景下有多条条目可选，\n"
                    "# 程序按确定性规则无法判定该用哪一条，因此不渲染任何命令（避免给错命令）。\n"
                    "#\n"
                    "# 处理办法（二选一）：\n"
                    "#   1) 给该步骤的 cmd_ref 补 title_keyword，使候选唯一命中；\n"
                    "#   2) 改用 uuid 硬引用：{\"uuid\": \"<命令库条目的 uuid>\"}（最可靠）。\n"
                    "# 改完重开本树即可；也可跑 python main.py --validate-seed 一次列出全部歧义。")
        if reason == "bad_ref":
            return ("# 本步骤的 cmd_ref 配置不完整（缺少 vendor_category）。\n"
                    "# 请补上 vendor_category（可再加 title_keyword）或改用 uuid 硬引用。")
        return ("# 该厂商暂无对应命令条目。\n"
                "# 请在命令库（Ctrl+N）为该厂商补充此分类的条目后，这里会自动渲染出来。")

    @staticmethod
    def _no_entry_label(reason, vendor):
        """命令区上方的一行醒目提示"""
        if reason == "ambiguous":
            return "引用存在歧义：请补 title_keyword 或改用 uuid（已停止渲染，避免给错命令）"
        if reason == "bad_ref":
            return "cmd_ref 配置不完整：缺少 vendor_category"
        return "当前厂商（%s）缺少该分类条目" % dbmod.display_vendor(vendor or "")

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_vendor_changed(self, _index):
        # 厂商变了 → 当前步骤命令重新渲染
        if self.current_tree and self.path:
            self.render_current_step()

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 排查报告（模块 04 第 7 轮）
    # ------------------------------------------------------------------
    def generate_report(self):
        """把本次排查导出成 HTML + 纯文本报告"""
        if not self.current_tree or not self.walk_log:
            return
        if not self.walk_log[-1].get("conclusion") and not self.leaf_frame.isVisible():
            r = QMessageBox.question(
                self, "还没走到结论",
                "当前还没走到结论步骤，报告会标记「未走到结论」。继续生成吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        duration = int(time.time() - self.started_at) if self.started_at else 0
        model = self.ed_model.text().strip()
        try:
            html_path, txt_path = report_mod.save_report(
                self.current_tree, self.walk_log,
                vendor=self.cmb_vendor.currentData() or "",
                model=model, operator=self.ed_operator.text().strip(),
                duration_sec=duration, findings=self.last_findings)
        except Exception as exc:
            QMessageBox.warning(self, "生成失败", "报告写入失败：%s\n（U 盘只读？）" % exc)
            return
        box = QMessageBox(self)
        box.setWindowTitle("报告已生成")
        box.setText("排查报告已生成：\n%s" % html_path)
        box.setInformativeText("同时输出了纯文本版：\n%s\n\n"
                               "目录：trouble_reports/（与程序同目录，跟着 U 盘走）" % txt_path)
        btn_open = box.addButton("打开报告", QMessageBox.AcceptRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is btn_open:
            try:
                import os as _os
                _os.startfile(html_path)      # Windows 默认浏览器打开（本地文件，不联网）
            except Exception:
                QMessageBox.information(self, "提示", "请手动打开文件：\n%s" % html_path)

    def open_report_history(self):
        dlg = ReportHistoryDialog(self)
        dlg.exec_()

    def open_tree_manager(self):
        """打开排查树维护（增/删/改树与步骤，全部落 history）"""
        from ui_tree_editor import TreeManagerDialog
        dlg = TreeManagerDialog(self.db, self)
        dlg.exec_()
        # 树可能被改过：重建现象列表，并把当前树重新渲染一次
        self.refresh()
        if self.current_tree:
            again = self.db.get_tree(self.current_tree.get("tree_id"))
            if again:
                self.current_tree = again
                self.render_current_step()

    def open_analyzer(self):
        """打开输出分析器（可把结论并入之后的报告）"""
        dlg = OutputAnalyzerDialog(self.db, self)
        dlg.exec_()
        if dlg.findings:
            self.last_findings = dlg.findings
            self._status("已记录 %d 条输出分析结论，生成报告时会一并写入。"
                         % len(dlg.findings))

    def _status(self, message):
        """
        在状态栏提示。
        ★ 本类继承自 QWidget，本身没有 statusBar()（那是 QMainWindow 的方法），
          直接调 self.statusBar() 会抛 AttributeError。这里统一走"向上找宿主窗口
          的 statusBar"，找不到（例如单测里单独实例化本 Tab）就静默跳过，
          提示本身不是关键路径，不能因为它把功能打断。
        """
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(message)

    def go_back(self):
        """回退上一步"""
        if len(self.path) > 1:
            self.path.pop()
            self.render_current_step()

    def _on_history_jump(self, index):
        """面包屑回退：跳回已走路径中的任意一步"""
        if index < 0 or not self.current_tree:
            return
        sid = self.cmb_history.itemData(index)
        if not sid or sid == self.path[-1]:
            return
        try:
            pos = self.path.index(sid)
        except ValueError:
            return
        self.path = self.path[:pos + 1]
        self.render_current_step()

    def restart(self):
        """重新开始本树"""
        if self.current_tree:
            self._goto_first_step()

    def copy_step_commands(self):
        """复制当前步骤渲染出的命令（只复制，不连设备）"""
        text = self.txt_step_commands.toPlainText()
        if not copy_to_clipboard(text):
            self._status("复制失败（剪贴板被占用），请手动选中后复制。")
            return
        self.btn_copy_cmd.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.btn_copy_cmd.setText("复制该步命令"))

    def ask_ai_about_step(self):
        """
        步骤卡片 [问 AI]（任务1/2/4）：
            · 传完整结构化走树记录（每步标题 + 该步实际选择的观察结果）
            · 走到叶子时附排查结论 + 处理动作
            · 厂商传 resolve_cmd_ref **实际命中**的 slug（截图上厂商空着的问题）
        厂商确认不了时不瞎填，交给 ui_ai 在上下文里注明"设备厂商未确认"。
        """
        win = self.window()
        if not hasattr(win, "goto_ai_tab"):
            self._status("主窗未加载 AI 诊断 Tab。")
            return
        tree = self.current_tree or {}
        # 优先用"本次 cmd_ref 实际命中"的厂商；退化为下拉选择（含自动匹配时树 hint 兜底）
        vendor_slug = getattr(self, "_resolved_vendor", "") or (self.cmb_vendor.currentData() or "")
        entry = getattr(self, "_current_entry", None)
        win.goto_ai_tab(
            vendor=dbmod.display_vendor(vendor_slug) if vendor_slug else "",
            os_name=dbmod.display_os(entry.get("os_family")) if entry else "",
            model=(entry or {}).get("models") or "",
            symptom=(tree.get("symptom") or "").strip(),
            echo=self.txt_step_commands.toPlainText(),
            walk_record=list(self.walk_record),          # ← 结构化走树记录（副本）
            leaf_data=dict(self.leaf_data) if self.leaf_data else None,
        )
        if self.leaf_data:
            self._status("已带入 AI 诊断：含走树记录 + 排查结论 + 处理动作，AI 将评估该结论。")
        else:
            self._status("已带入 AI 诊断：含已走路径与各步观察结果（尚未走到结论）。")

    def copy_path_summary(self):
        """复制完整排查路径（变更单/报告素材）"""
        if not self.current_tree:
            return
        sym = self.current_tree.get("symptom", "")
        lines = ["# 排查路径记录（NetToolBox 排查向导）",
                 "# 现象：%s ｜ 时间：%s" % (sym, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
                 ""]
        for sid in self.path:
            s = self._step_by_id(sid)
            if not s:
                continue
            lines.append("步骤 %s：%s" % (sid, s.get("title", "")))
            if s.get("cmd_ref") and self._current_entry is not None:
                lines.append("命令：（见各步骤渲染命令）")
            if s.get("observe"):
                lines.append("观察点：%s" % s.get("observe"))
            lines.append("")
        if not copy_to_clipboard("\n".join(lines)):
            self._status("复制失败（剪贴板被占用），请手动选中后复制。")
            return
        self.btn_copy_path.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.btn_copy_path.setText("复制完整路径"))

    def mark_tree_verified(self):
        """整棵树走通一次真实排障后标记已验证（复用绿徽章体系）"""
        if not self.current_tree:
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法标记验证。")
            return
        tree = self.current_tree
        fake = {"title": "排查树：%s" % tree.get("symptom", ""),
                "verified_by": tree.get("verified_by") or "",
                "verified_model": "",
                "verified_date": tree.get("verified_date") or "",
                "models": ""}
        dlg = VerifyDialog(fake, self)
        if dlg.exec_() == dlg.Accepted:
            by = dlg.ed_by.text().strip()
            if not by:
                QMessageBox.warning(self, "缺少验证人", "验证人必填。")
                return
            self.db.mark_tree_verified(tree.get("tree_id"), by,
                                       dlg.ed_model.text().strip(),
                                       dlg.ed_date.text().strip())
            self.refresh()
            self.render_current_step()
            QMessageBox.information(self, "已标记",
                                    "排查树已标记为已验证（绿徽章），命令库不受影响。")


class OutputAnalyzerDialog(QDialog):
    """
    输出分析器（模块 04 第 7 轮）：
        选命令类型（可自动识别）→ 粘贴设备/系统回显 → 解析出异常项 + 建议下一步
    异常项按严重度着色（错误红/警告黄/提示蓝），可[复制原文]、[去排查树]。
    ★ 无法识别的格式给友好提示，绝不抛异常。
    """

    def __init__(self, db, parent=None):
        super(OutputAnalyzerDialog, self).__init__(parent)
        self.db = db
        self.findings = []
        self.setWindowTitle("输出分析器 · 粘贴回显找异常")
        self.resize(900, 660)

        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("命令类型："))
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItem("自动识别", "")
        for key, label in output_analyzer.KINDS:
            self.cmb_kind.addItem(label, key)
        self.cmb_kind.setMinimumWidth(340)
        row.addWidget(self.cmb_kind)
        self.btn_parse = QPushButton("解析")
        self.btn_parse.clicked.connect(self.on_parse)
        row.addWidget(self.btn_parse)
        self.btn_copy_all = QPushButton("复制结论（纯文本）")
        self.btn_copy_all.setObjectName("Ghost")
        self.btn_copy_all.clicked.connect(self.copy_findings)
        row.addWidget(self.btn_copy_all)
        # [问 AI]：把回显原文/命令类型/异常清单带入 AI 诊断 Tab（任务4-4）
        self.btn_ask_ai = QPushButton("问 AI")
        self.btn_ask_ai.setObjectName("Ghost")
        self.btn_ask_ai.setToolTip("把粘贴的回显原文、命令类型与异常项清单带入 AI 诊断 Tab")
        self.btn_ask_ai.clicked.connect(self.ask_ai_about_findings)
        row.addWidget(self.btn_ask_ai)
        row.addStretch(1)
        v.addLayout(row)

        v.addWidget(QLabel("粘贴设备/系统回显（show interfaces、display interface brief、"
                           "df -h、ss -tlnp、systemctl status…）："))
        self.txt_input = QPlainTextEdit()
        self.txt_input.setFont(_mono(10))
        self.txt_input.setPlaceholderText(
            "示例（Cisco）：\n"
            "  GigabitEthernet0/1 is up, line protocol is up\n"
            "    input errors 1523, CRC 300, output errors 0\n"
            "\n示例（Linux）：\n"
            "  /dev/vda1        50G   48G  1.2G  98% /\n"
            "  tcp   LISTEN 0  128  127.0.0.1:3306  0.0.0.0:*  users:((\"mysqld\"))")
        self.txt_input.setMinimumHeight(170)
        v.addWidget(self.txt_input)

        self.lbl_note = QLabel("粘贴后点「解析」；识别不出格式会给提示，不会报错。")
        self.lbl_note.setObjectName("StateLabel")
        self.lbl_note.setWordWrap(True)
        v.addWidget(self.lbl_note)

        self.result_area = QScrollArea()
        self.result_area.setWidgetResizable(True)
        self.result_host = QWidget()
        self.result_layout = QVBoxLayout(self.result_host)
        self.result_layout.setContentsMargins(4, 4, 4, 4)
        self.result_layout.setSpacing(6)
        self.result_layout.addStretch(1)
        self.result_area.setWidget(self.result_host)
        v.addWidget(self.result_area, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        v.addLayout(bottom)

    # ------------------------------------------------------------------
    def on_parse(self):
        text = self.txt_input.toPlainText()
        kind = self.cmb_kind.currentData() or None
        findings, note = output_analyzer.analyze(text, kind)
        self.findings = findings
        self._clear()
        if not findings:
            self.lbl_note.setText("⚠ " + (note or "没有解析出异常项。"))
            set_state(self.lbl_note, "warn")
            return
        used = kind or output_analyzer.detect_kind(text)
        self.lbl_note.setText("识别类型：%s ｜ 共 %d 项异常（错误 %d / 警告 %d）"
                              % (output_analyzer.kind_label(used), len(findings),
                                 sum(1 for f in findings if f["severity"] == "error"),
                                 sum(1 for f in findings if f["severity"] == "warn")))
        set_state(self.lbl_note, "ok")
        for f in findings:
            self.result_layout.insertWidget(self.result_layout.count() - 1, self._card(f))

    def _clear(self):
        while self.result_layout.count() > 1:
            item = self.result_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _card(self, f):
        color, label = output_analyzer.SEVERITY_STYLE.get(f["severity"], ("#4f8cff", "提示"))
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setObjectName("DupCard")
        card.setProperty("level", {"error": "high", "warn": "warn", "info": "info"}.get(f["severity"], "info"))
        cv = QVBoxLayout(card)
        cv.setContentsMargins(10, 8, 10, 8)
        cv.setSpacing(4)

        head = QLabel("<span style='color:%s; font-weight:bold;'>[%s] %s</span>"
                      "<span style='color:#9aa0b0;'>  （第 %d 行）</span>"
                      % (color, _esc(label), _esc(f["title"]), f["line_no"]))
        head.setTextFormat(Qt.RichText)
        cv.addWidget(head)

        original = QLabel("<span style='font-family:Consolas; color:#c9d1d9;'>%s</span>"
                          % _esc(f["line"]))
        original.setTextFormat(Qt.RichText)
        original.setWordWrap(True)
        cv.addWidget(original)

        advice = QLabel("<span style='color:%s;'>建议下一步：</span>%s"
                        % (color, _esc(f["advice"])))
        advice.setTextFormat(Qt.RichText)
        advice.setWordWrap(True)
        cv.addWidget(advice)

        row = QHBoxLayout()
        btn_copy = QPushButton("复制这行原文")
        btn_copy.setObjectName("Ghost")
        btn_copy.clicked.connect(lambda _=False, t=f["line"]: self._copy(t))
        row.addWidget(btn_copy)
        if f.get("tree"):
            tree_id, step_id = f["tree"]
            tree = self.db.get_tree(tree_id)
            if tree:
                btn_tree = QPushButton("去排查树：%s" % (tree.get("symptom") or tree_id))
                btn_tree.clicked.connect(lambda _=False, t=tree_id, s=step_id:
                                         self._jump_tree(t, s))
                row.addWidget(btn_tree)
        row.addStretch(1)
        cv.addLayout(row)
        return card

    def _copy(self, text):
        if not copy_to_clipboard(text):
            self.lbl_note.setText("✘ 复制失败（剪贴板被占用），请手动选中后复制。")
            set_state(self.lbl_note, "err")
            return
        self.lbl_note.setText("✓ 已复制该行原文到剪贴板")
        set_state(self.lbl_note, "ok")

    def _jump_tree(self, tree_id, step_id):
        """跳到排查向导对应树的指定步骤（跨对话框跳转）"""
        parent = self.parent()
        while parent is not None and not hasattr(parent, "start_tree"):
            parent = parent.parent()
        if parent is None:
            return
        self.accept()
        parent.start_tree(tree_id)
        if step_id:
            parent._goto(step_id)
            # parent 是 TroubleshootTab（QWidget 子类），没有 statusBar()；
            # 走它自己的 _status() 由内部向上找宿主窗口
            if hasattr(parent, "_status"):
                parent._status("已按分析结论跳到排查树对应步骤。")

    def copy_findings(self):
        if not self.findings:
            return
        lines = ["# 输出分析结论（NetToolBox 输出分析器）",
                 "# 时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""]
        for f in self.findings:
            lines.append("[%s] %s" % (f["title"], f["advice"]))
            lines.append("    原文（第 %d 行）：%s" % (f["line_no"], f["line"]))
        if not copy_to_clipboard("\n".join(lines)):
            self.lbl_note.setText("✘ 复制失败（剪贴板被占用），请手动选中后复制。")
            set_state(self.lbl_note, "err")
            return
        self.lbl_note.setText("✓ 已复制 %d 条结论到剪贴板（可直接贴进变更单）" % len(self.findings))
        set_state(self.lbl_note, "ok")

    def ask_ai_about_findings(self):
        """[问 AI]：回显=粘贴原文，现象=输出分析结论，步骤=异常项清单（任务4-4）"""
        # ★ 对话框是顶层窗口，self.window() 返回自己而非主窗（_jump_tree 同坑）——
        #   必须沿 parent 链向上找带 goto_ai_tab 的宿主
        host = self.parent()
        while host is not None and not hasattr(host, "goto_ai_tab"):
            host = host.parent()
        if host is None:
            self.lbl_note.setText("主窗未加载 AI 诊断 Tab。")
            return
        echo = self.txt_input.toPlainText().strip()
        if not echo:
            self.lbl_note.setText("先粘贴回显再问 AI。")
            set_state(self.lbl_note, "warn")
            return
        used = self.cmb_kind.currentData() or output_analyzer.detect_kind(echo)
        items = []
        for f in self.findings:
            items.append("- [%s] %s ｜ 证据（第 %d 行）：%s ｜ 建议：%s"
                         % (f.get("severity") or "info", f.get("title") or "",
                            f.get("line_no") or 0, (f.get("line") or "")[:80],
                            (f.get("advice") or "")[:80]))
        host.goto_ai_tab(
            symptom="输出分析（%s）发现 %d 项异常，请结合回显给出下一步排查建议"
                    % (output_analyzer.kind_label(used) if used else "类型未识别",
                       len(self.findings)),
            echo=echo,
            steps=("输出分析器识别的异常项清单：\n" + "\n".join(items)) if items else "",
        )
        self.lbl_note.setText("✓ 已带入 AI 诊断 Tab（含回显原文与异常清单）。")
        set_state(self.lbl_note, "ok")


class ReportHistoryDialog(QDialog):
    """排查报告历史档案：按现象/关键字搜索，可打开（本机默认程序）"""

    def __init__(self, parent=None):
        super(ReportHistoryDialog, self).__init__(parent)
        self.setWindowTitle("排查报告历史 · trouble_reports/")
        self.resize(880, 520)
        v = QVBoxLayout(self)

        row = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("按现象 / 结论 / 时间搜索（回车）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.returnPressed.connect(self.refresh)
        row.addWidget(self.ed_search, 1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("Ghost")
        btn_refresh.clicked.connect(self.refresh)
        row.addWidget(btn_refresh)
        v.addLayout(row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["生成时间", "现象", "结论摘要", "文件"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)
        v.addWidget(self.table, 1)

        self.lbl_dir = QLabel("")
        self.lbl_dir.setObjectName("Hint")
        v.addWidget(self.lbl_dir)

        bottom = QHBoxLayout()
        btn_open = QPushButton("打开报告")
        btn_open.clicked.connect(self.open_selected)
        bottom.addWidget(btn_open)
        btn_open_dir = QPushButton("打开报告目录")
        btn_open_dir.setObjectName("Ghost")
        btn_open_dir.clicked.connect(self.open_dir)
        bottom.addWidget(btn_open_dir)
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        v.addLayout(bottom)
        self.refresh()

    def refresh(self):
        keyword = self.ed_search.text().strip()
        reports = report_mod.search_reports(keyword)
        self.table.setRowCount(0)
        for r in reports:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate([r.get("time", ""), r.get("symptom", ""),
                                         r.get("conclusion", ""),
                                         os.path.basename(r.get("file", ""))]):
                item = QTableWidgetItem(str(value))
                if col == 3:
                    item.setData(Qt.UserRole, r.get("file"))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.lbl_dir.setText("报告目录：%s ｜ 共 %d 份%s"
                             % (report_mod.reports_dir(), len(reports),
                                ("（搜索：%s）" % keyword) if keyword else ""))

    def _selected_file(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 3)
        return item.data(Qt.UserRole) if item else None

    def open_selected(self):
        path = self._selected_file()
        if not path:
            return
        try:
            os.startfile(path)
        except Exception:
            QMessageBox.information(self, "提示", "请手动打开：\n%s" % path)

    def open_dir(self):
        try:
            os.startfile(report_mod.reports_dir())
        except Exception:
            QMessageBox.information(self, "提示", "报告目录：\n%s" % report_mod.reports_dir())
