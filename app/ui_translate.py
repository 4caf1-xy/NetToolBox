# -*- coding: utf-8 -*-
"""
ui_translate.py —— 配置翻译 Tab（U 批 2026-11-06，主窗第 8 个 Tab）

定位：把一台设备的整份/片段 running-config 粘贴进来，按配置块识别命令族，
用库内目标厂商条目模板重组命令串；未命中的行逐条交代（四档账本）。

边界（UI 上必须可见）：
    「库内族自动翻译，其余逐条交代」——覆盖率、需人工清单、行级 uuid 追溯
    一张表全给出来；输出整体标「语法核对档，未经真机」。

纪律（AGENTS.md §6）：
    · 两级 GripSplitter（水平 1 层 + 右列垂直 1 层，嵌套 ≤2）+ split_state 记忆；
    · 样式只走 theme.qss 既有 objectName，内联色只用 theme.py 常量；
    · 零网络零执行：只做文本变换，命令由用户自己复制到终端。
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QComboBox, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QApplication,
)

import translate_tool
from theme import (ACCENT, DANGER, WARNING, SUCCESS, TEXT_MUTED,
                   TEXT_SECONDARY, GripSplitter, read_sizes, mono_font)

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "samples", "translate")

TIER_UI = {
    "a": ("已译", SUCCESS),
    "b": ("逐行映射", TEXT_SECONDARY),
    "c": ("需人工", WARNING),
    "d": ("跳过", TEXT_MUTED),
}

VENDOR_ORDER = ("cisco", "huawei", "h3c", "ruijie", "zte", "juniper")


def _copy(text, widget=None):
    QApplication.clipboard().setText(text or "")
    try:
        win = widget.window() if widget is not None else None
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage("已复制（%d 字符）" % len(text or ""), 2500)
    except Exception:
        pass


class TranslateTab(QWidget):
    """配置翻译 Tab：左粘贴源配置，右目标配置 + 四档账本"""

    SPLIT_KEY = "translate_split"
    SPLIT_MINS = (420, 520)
    SPLIT_R_KEY = "translate_right_split"
    SPLIT_R_MINS = (300, 260)

    def __init__(self, db, main=None):
        super(TranslateTab, self).__init__()
        self.db = db
        self.main = main
        self._rules = {}
        self._result = None
        self._pending_split = None
        self._pending_r_split = None
        self._build()
        self._reload_rules()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        banner = QLabel(
            "库内族自动翻译，其余逐条交代 —— 目标侧每一行命令都可追溯到库内条目 uuid；"
            "未命中的行落「需人工」，绝不臆造命令。")
        banner.setObjectName("Hint")
        banner.setWordWrap(True)
        root.addWidget(banner)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("源厂商"))
        self.cmb_src = QComboBox()
        self.cmb_dst = QComboBox()
        for slug, label in self._vendor_items():
            self.cmb_src.addItem(label, slug)
            self.cmb_dst.addItem(label, slug)
        self.cmb_dst.setCurrentIndex(1 if self.cmb_dst.count() > 1 else 0)
        row.addWidget(self.cmb_src)
        arrow = QLabel("→")
        arrow.setObjectName("Hint")
        row.addWidget(arrow)
        row.addWidget(QLabel("目标厂商"))
        row.addWidget(self.cmb_dst)
        row.addStretch(1)

        self.btn_sample = QPushButton("载入样例")
        self.btn_sample.setToolTip("载入该厂商的整机配置小样（含族内行 / 私有特性 / 杂项行）")
        self.btn_sample.clicked.connect(self.on_load_sample)
        row.addWidget(self.btn_sample)

        self.btn_run = QPushButton("翻译")
        self.btn_run.setObjectName("Primary")
        self.btn_run.setToolTip("解析源配置 → 按族映射 → 输出目标配置与四档账本")
        self.btn_run.clicked.connect(self.on_translate)
        row.addWidget(self.btn_run)

        self.btn_ledger = QPushButton("导出账本")
        self.btn_ledger.setObjectName("Ghost")
        self.btn_ledger.setToolTip("导出 Markdown 账本（覆盖摘要 / 族覆盖 / 需人工清单 / 行级追溯）")
        self.btn_ledger.clicked.connect(self.on_export)
        row.addWidget(self.btn_ledger)
        root.addLayout(row)

        self.lbl_rule = QLabel("")
        self.lbl_rule.setObjectName("Hint")
        self.lbl_rule.setWordWrap(True)
        root.addWidget(self.lbl_rule)

        # ---- 左：源配置；右：目标配置 + 账本 ----
        self.ed_src = QPlainTextEdit()
        self.ed_src.setFont(mono_font(9))
        self.ed_src.setPlaceholderText(
            "在此粘贴源厂商 running-config（整份或片段）…\n"
            "例：show running-config / display current-configuration 的输出")

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        lt = QLabel("源配置（粘贴区）")
        lt.setObjectName("Hint")
        lv.addWidget(lt)
        lv.addWidget(self.ed_src, 1)

        self.ed_out = QPlainTextEdit()
        self.ed_out.setFont(mono_font(9))
        self.ed_out.setReadOnly(True)
        self.ed_out.setPlaceholderText("目标厂商配置（库内条目渲染结果，语法核对档）")

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ["行号", "档", "源命令", "归属族", "目标条目", "说明"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setAlternatingRowColors(True)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        self.tbl.setColumnWidth(2, 230)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)

        self.lbl_cov = QLabel("覆盖率：—")
        self.lbl_cov.setObjectName("Hint")

        self.btn_copy = QPushButton("复制目标配置")
        self.btn_copy.setObjectName("Ghost")
        self.btn_copy.clicked.connect(
            lambda: _copy(self.ed_out.toPlainText(), self.btn_copy))

        out_head = QHBoxLayout()
        out_head.setSpacing(8)
        t2 = QLabel("目标配置（语法核对档）")
        t2.setObjectName("Hint")
        out_head.addWidget(t2)
        out_head.addStretch(1)
        out_head.addWidget(self.btn_copy)

        top_right = QWidget()
        tv = QVBoxLayout(top_right)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(6)
        tv.addLayout(out_head)
        tv.addWidget(self.ed_out, 1)

        ledger_box = QWidget()
        bv = QVBoxLayout(ledger_box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(6)
        bv.addWidget(self.lbl_cov)
        bv.addWidget(self.tbl, 1)

        self.right_split = GripSplitter(Qt.Vertical)
        self.right_split.addWidget(top_right)
        self.right_split.addWidget(ledger_box)
        self.right_split.setSizes([420, 320])

        self.split = GripSplitter(Qt.Horizontal)
        self.split.addWidget(left)
        self.split.addWidget(self.right_split)
        self.split.setSizes([520, 620])
        root.addWidget(self.split, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Hint")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

    def _vendor_items(self):
        """厂商清单取映射表声明（顺序固定为六家网络厂商），标签走库内显示名"""
        items = []
        try:
            import db as dbmod
            slugs = VENDOR_ORDER
            for slug in slugs:
                items.append((slug, dbmod.display_vendor(slug)))
        except Exception:
            items = [(s, s) for s in VENDOR_ORDER]
        return items

    # ------------------------------------------------------------------
    # 分栏状态记忆
    # ------------------------------------------------------------------
    def split_state(self):
        state = {}
        try:
            state[self.SPLIT_KEY] = list(self.split.sizes())
            state[self.SPLIT_R_KEY] = list(self.right_split.sizes())
        except Exception:
            return {}
        return state

    def restore_split_state(self, state):
        self._pending_split = read_sizes(state, self.SPLIT_KEY, self.SPLIT_MINS)
        self._pending_r_split = read_sizes(state, self.SPLIT_R_KEY, self.SPLIT_R_MINS)
        if self.isVisible():
            self._apply_pending_split()

    def _apply_pending_split(self):
        if self._pending_split:
            self.split.setSizes(self._pending_split)
            self._pending_split = None
        if self._pending_r_split:
            self.right_split.setSizes(self._pending_r_split)
            self._pending_r_split = None

    def showEvent(self, event):
        super(TranslateTab, self).showEvent(event)
        self._apply_pending_split()

    # ------------------------------------------------------------------
    # 规则 / 动作
    # ------------------------------------------------------------------
    def _reload_rules(self):
        try:
            data, problems = translate_tool.load_rules()
        except Exception as exc:
            data, problems = {}, [str(exc)]
        self._rules = data or {}
        fams = (self._rules.get("families") or [])
        if problems or not fams:
            self.lbl_rule.setText(
                "⚠ 映射表未载入（%s）—— 所有行将落「需人工」，不输出任何命令。"
                % ("；".join(problems) or "translate_rules.json 缺失"))
            return
        names = "、".join("%s（%s）" % (f.get("name") or f.get("id"),
                                      f.get("category") or "-") for f in fams)
        self.lbl_rule.setText("映射表：%d 族 ｜ %s" % (len(fams), names))

    def on_load_sample(self):
        slug = self.cmb_src.currentData()
        path = os.path.join(SAMPLE_DIR, "%s.txt" % slug)
        if not os.path.isfile(path):
            QMessageBox.information(self, "无样例", "该厂商暂无整机配置样例。")
            return
        with open(path, "r", encoding="utf-8") as fp:
            self.ed_src.setPlainText(fp.read())
        self.lbl_status.setText("已载入样例：%s（%s）" % (os.path.basename(path), slug))

    def on_translate(self):
        text = self.ed_src.toPlainText()
        if not text.strip():
            self.lbl_status.setText("请先粘贴源配置（或点「载入样例」）。")
            return
        src = self.cmb_src.currentData()
        dst = self.cmb_dst.currentData()
        try:
            result = translate_tool.translate(text, src, dst, self._rules,
                                              self.db.get_entry)
        except Exception as exc:      # 引擎异常不得拖垮 Tab
            self.lbl_status.setText("翻译失败：%s" % exc)
            return
        self._result = result
        self._show(result)

    def _show(self, result):
        self.ed_out.setPlainText("\n".join(result["output"]["config"]))
        cov = result["coverage"]
        self.lbl_cov.setText(
            "覆盖率：已译 <b>%d</b> / 命令行 <b>%d</b>（<b>%.1f%%</b>）｜逐行映射 %d ｜"
            "需人工 <b style='color:%s'>%d</b> ｜跳过 %d"
            % (cov["translated"], cov["command"], cov["pct"], cov["comment"],
               WARNING, cov["manual"], cov["skipped"]))
        self.lbl_cov.setTextFormat(Qt.RichText)

        self.tbl.setRowCount(0)
        for ln in result["lines"]:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            tier = ln["tier"]
            label, color = TIER_UI.get(tier, (tier, TEXT_MUTED))
            cells = [
                str(ln["no"]),
                label,
                (ln["text"] or "").strip(),
                ln.get("family_name") or ln.get("family") or "",
                (ln["uuid"] or "")[:8],
                ln.get("note") or "",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if c == 1:
                    item.setForeground(QColor(color))
                elif c in (0, 4):
                    item.setForeground(QColor(TEXT_SECONDARY))
                self.tbl.setItem(r, c, item)

        warn = "；".join(result.get("warnings") or [])
        self.lbl_status.setText(
            "完成：%s → %s ｜ 族命中 %d 个实例 ｜ 需人工 %d 行（导出账本可逐条看原因）"
            % (result.get("src_label"), result.get("dst_label"),
               len(result["instances"]), cov["manual"])
            + (("｜" + warn) if warn else ""))

    def on_export(self):
        if not self._result:
            self.lbl_status.setText("请先翻译一次，再导出账本。")
            return
        default = "translate_%s_to_%s.md" % (self._result["src"], self._result["dst"])
        path, _ = QFileDialog.getSaveFileName(
            self, "导出账本", default, "Markdown (*.md);;文本 (*.txt);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(translate_tool.render_markdown(self._result))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", "写入失败：%s" % exc)
            return
        self.lbl_status.setText("账本已导出：%s" % path)
