# -*- coding: utf-8 -*-
"""
ui_diff.py —— Before/After 变更对比 Tab（H 批 2026-10-14 新增）

定位：离线变更验证的最后一环。变更前后各粘贴一次同命令回显 → 差异卡片
（✓ 不变 / ⚠ 变化 before→after / ＋新增 / －消失），可导出单文件 HTML。
粘贴流零联网（diff_tool / diff_export 均无任何网络依赖），Copy Only 不变。

入口（盘点裁决：独立工具 Tab + 操作单旁挂按钮并行）：
    · 本 Tab（主入口）：选/自动识别命令类 → 粘贴 Before → 粘贴 After → 对比；
    · 操作单步骤「预期回显」旁「对比 Before/After」按钮 → 主窗 open_diff_tab()
      跳转本 Tab（ui_runbook 旁挂，main=None 时按钮安全禁用）。

引擎 / 导出：app/diff_tool.py（规则随种子 seed_data/diff_rules.json，坏规则
validate [E] 拦截；无规则命中降级行级 diff 不崩）+ app/diff_export.py
（打印 token 与操作单同源）。

UI 纪律（AGENTS.md §6）：
    · QSplitter（水平 1 层）+ 状态记忆 key=diff_split（pending 模式同 ui_runbook）；
    · 样式只走 theme.qss 既有 objectName / theme.py token，内联色只用常量。
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSplitter, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

import db as dbmod
import diff_tool
import diff_export
from theme import (ACCENT, DANGER, WARNING, SUCCESS, TEXT_MUTED,
                   TEXT_SECONDARY, read_sizes)
from ui_generator import mono_font

HTML_FILTER = "HTML (*.html);;所有文件 (*.*)"

# 四态 → (显示名, 颜色常量)；颜色一律 theme.py 常量（AGENTS §6）
STATE_UI = {
    "same":    ("✓ 不变", TEXT_MUTED),
    "changed": ("⚠ 变化", WARNING),
    "added":   ("＋ 新增", ACCENT),
    "removed": ("－ 消失", DANGER),
}
LEVEL_UI = {
    "clean":   ("L0 · 一致", SUCCESS),
    "changed": ("L1 · 状态值变化", WARNING),
    "delta":   ("L2 · 键集增删", ACCENT),
}


class DiffTab(QWidget):
    """变更对比 Tab：左 Before/After 粘贴区，右差异卡片 + HTML 导出"""

    SPLIT_KEY = "diff_split"
    SPLIT_MINS = (360, 420)

    def __init__(self, db):
        super(DiffTab, self).__init__()
        self.db = db
        self._last_result = None
        self._pending_split = None
        self._build()
        self._reload_rules_hint()

    # ==================================================================
    # 布局
    # ==================================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QLabel("<b>Before / After 变更对比</b> —— 变更前后各粘贴一次同命令回显，"
                      "自动输出状态差异摘要；uptime / 包计数器 / 时间戳 / CPU 瞬时值自动忽略。"
                      "粘贴内容仅本地处理，<b>零联网</b>。")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- 左：粘贴区 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(4)

        lv.addWidget(QLabel("Before（变更前回显）:"))
        self.ed_before = QPlainTextEdit()
        self.ed_before.setPlaceholderText("粘贴变更前同一条查询命令的回显…")
        self.ed_before.setFont(mono_font(10))
        self.ed_before.setObjectName("CodeBlock")
        lv.addWidget(self.ed_before, 1)

        lv.addWidget(QLabel("After（变更后回显）:"))
        self.ed_after = QPlainTextEdit()
        self.ed_after.setPlaceholderText("粘贴变更后同一条查询命令的回显…")
        self.ed_after.setFont(mono_font(10))
        self.ed_after.setObjectName("CodeBlock")
        lv.addWidget(self.ed_after, 1)

        row_btn = QHBoxLayout()
        self.btn_compare = QPushButton("对比")
        self.btn_compare.clicked.connect(self.on_compare)
        row_btn.addWidget(self.btn_compare)
        self.btn_swap = QPushButton("交换 Before/After")
        self.btn_swap.clicked.connect(self.on_swap)
        row_btn.addWidget(self.btn_swap)
        row_btn.addStretch(1)
        lv.addLayout(row_btn)

        split.addWidget(left)

        # ---- 右：结果区 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(4)

        self.lbl_rule = QLabel("")
        self.lbl_rule.setObjectName("Hint")
        self.lbl_rule.setWordWrap(True)
        rv.addWidget(self.lbl_rule)

        self.lbl_summary = QLabel("尚未对比。粘贴后点「对比」。")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setTextFormat(Qt.RichText)
        rv.addWidget(self.lbl_summary)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["状态", "键 / 行", "Before", "After"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setWordWrap(True)
        rv.addWidget(self.tbl, 1)

        row_exp = QHBoxLayout()
        self.btn_export = QPushButton("导出 HTML")
        self.btn_export.clicked.connect(self.on_export)
        row_exp.addWidget(self.btn_export)
        row_exp.addStretch(1)
        rv.addLayout(row_exp)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([520, 760])
        self.split = split
        root.addWidget(split, 1)

    # ==================================================================
    # 分栏状态记忆（MainWindow 统一收口；pending 模式同 ui_runbook）
    # ==================================================================
    def split_state(self):
        try:
            return {self.SPLIT_KEY: list(self.split.sizes())}
        except Exception:
            return {}

    def restore_split_state(self, state):
        self._pending_split = read_sizes(state, self.SPLIT_KEY, self.SPLIT_MINS)
        if self._pending_split and self.isVisible():
            self._apply_pending_split()

    def _apply_pending_split(self):
        if getattr(self, "_pending_split", None):
            self.split.setSizes(self._pending_split)
            self._pending_split = None

    def showEvent(self, event):
        super(DiffTab, self).showEvent(event)
        self._apply_pending_split()

    # ==================================================================
    # 规则提示 / 对比 / 导出
    # ==================================================================
    def _reload_rules_hint(self):
        """规则随种子：列出一眼能看的规则清单（读不到不报错，降级提示）"""
        try:
            data, problems = diff_tool.load_rules()
        except Exception:
            data, problems = {}, []
        if problems or not data.get("diff_rules"):
            self.lbl_rule.setText("⚠ 提取规则未载入（%s）——将使用降级行级比对。"
                                  % ("；".join(problems) or "diff_rules.json 缺失"))
            return
        names = "、".join(
            "%s（%s）" % (r.get("label") or r.get("rule_id"),
                          "、".join(v.get("vendor") or "?" for v in r.get("variants") or []))
            for r in data["diff_rules"])
        self.lbl_rule.setText("已载入提取规则 %d 类：%s。未收录的命令自动降级为行级比对。"
                              % (len(data["diff_rules"]), names))

    def on_swap(self):
        b = self.ed_before.toPlainText()
        self.ed_before.setPlainText(self.ed_after.toPlainText())
        self.ed_after.setPlainText(b)

    def on_compare(self):
        before = self.ed_before.toPlainText()
        after = self.ed_after.toPlainText()
        try:
            result = diff_tool.run_compare(before, after)
        except Exception as exc:            # 引擎层已兜底；此处为最终防线
            QMessageBox.critical(self, "对比失败", "引擎异常（已尽力兜底）：\n%s" % exc)
            return
        self._last_result = result
        self._show_result(result)

    def _show_result(self, result):
        summary = result.get("summary") or {}
        level = result.get("level") if result.get("level") in LEVEL_UI else "clean"
        lv_name, lv_color = LEVEL_UI[level]
        mode = ("提取规则「%s」" % (result.get("rule_label") or result.get("rule_id"))
                if result.get("mode") == "rule"
                else "降级行级比对（%s）" % (result.get("fallback_reason") or "无规则命中"))
        self.lbl_summary.setText(
            '<span style="color:%s; font-weight:bold;">%s</span>'
            '　✓ 不变 %d ｜ <span style="color:%s">⚠ 变化 %d</span> ｜ '
            '<span style="color:%s">＋ 新增 %d</span> ｜ '
            '<span style="color:%s">－ 消失 %d</span>'
            '　·　模式：%s　·　噪声过滤 %d 行'
            % (lv_color, lv_name,
               summary.get("same", 0), TEXT_MUTED, summary.get("changed", 0),
               ACCENT, summary.get("added", 0),
               DANGER, summary.get("removed", 0),
               mode, result.get("noise_dropped") or 0))

        items = result.get("items") or []
        order = {"changed": 0, "removed": 1, "added": 2, "same": 3}
        items = sorted(items, key=lambda it: order.get(it.get("state"), 9))
        self.tbl.setRowCount(len(items))
        for i, it in enumerate(items):
            state = it.get("state") if it.get("state") in STATE_UI else "same"
            name, color = STATE_UI[state]
            s_item = QTableWidgetItem(name)
            s_item.setForeground(QColor(color))
            s_item.setTextAlignment(Qt.AlignCenter)
            self.tbl.setItem(i, 0, s_item)
            self.tbl.setItem(i, 1, QTableWidgetItem(str(it.get("key") or "")))
            self.tbl.setItem(i, 2, QTableWidgetItem(str(it.get("before") or "")))
            self.tbl.setItem(i, 3, QTableWidgetItem(str(it.get("after") or "")))
            for col in (1, 2, 3):
                cell = self.tbl.item(i, col)
                if cell is not None:
                    cell.setFont(mono_font(10))

    def on_export(self):
        if self._last_result is None:
            QMessageBox.information(self, "导出", "先点「对比」得出结果，再导出 HTML。")
            return
        try:
            base = dbmod.get_base_dir()
        except Exception:
            base = os.path.expanduser("~")
        default = os.path.join(base, "diff_report.html")
        path, _f = QFileDialog.getSaveFileName(self, "导出变更对比 HTML", default, HTML_FILTER)
        if not path:
            return
        rc, msg = diff_export.export_diff(self._last_result, path)
        if rc == 0:
            QMessageBox.information(self, "导出成功", msg)
        else:
            QMessageBox.critical(self, "导出失败", msg)
