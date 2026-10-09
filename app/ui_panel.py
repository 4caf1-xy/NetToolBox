# -*- coding: utf-8 -*-
"""
ui_panel.py —— 全局命令面板（Ctrl+K，2026-10-09 批次任务1/2）

定位
    任意 Tab 唤起的居中浮层，一次输入同时搜三库（命令 / 报错字典 / 排查树），
    Enter 直接跳转到对应 Tab 并定位。类似 VSCode 的命令面板。

硬约束（AGENTS.md / 本批规格）
    · 搜索直接查 db：entries 走 db.search()（薄封装，不改语义）；
      err_dict / trouble_trees 在 all_errs() / all_trees() 结果上做 Python 侧
      过滤（82+26 条，微秒级），不建内存副本/缓存层
    · 样式全走 theme.qss 既有 objectName（SearchBox / Ghost / Hint），
      不新增 QSS 选择器
    · 键盘完整可达：↑↓ 选择、Enter 跳转、Esc 关闭；空结果给文案不空白
    · 空输入显示「最近使用」分组（记录/持久化由主窗负责，面板只读它）
"""

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QLabel, QToolButton,
                             QMessageBox, QApplication, QShortcut)

import db as dbmod

# 每组最多展示条数（超出提示去详情页/对应 Tab 细搜）
GROUP_CAP = 40
KIND_TAG = {"entry": "命令", "err": "报错", "tree": "树"}


class CommandPalette(QDialog):
    """三库统一搜索浮层。exec_() 关闭后读 .choice 决定跳转（None = 未选）"""

    def __init__(self, db, recent=None, on_clear_recent=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.recent = list(recent or [])          # [{kind, id, ts}]，主窗提供
        self.on_clear_recent = on_clear_recent
        self.choice = None
        self.setWindowTitle("全局搜索")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.resize(680, 520)
        self._build()
        self._reload("")
        # 点击浮层外部关闭（模态下父窗输入被拦，用全局事件过滤器实现）
        QApplication.instance().installEventFilter(self)
        self.ed_search.setFocus()

    # ---------------- 构建 ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("SearchBox")     # 复用主题搜索框样式
        self.ed_search.setPlaceholderText(
            "搜索命令 / 报错 / 排查树…（↑↓ 选择，Enter 跳转，Esc 关闭）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMinimumHeight(34)
        self.ed_search.textChanged.connect(self._reload)
        self.ed_search.installEventFilter(self)
        root.addWidget(self.ed_search)

        self.lst = QListWidget()
        self.lst.setSelectionMode(QListWidget.SingleSelection)
        self.lst.itemActivated.connect(self._activate)
        self.lst.itemClicked.connect(self._activate)
        root.addWidget(self.lst, 1)

        foot = QHBoxLayout()
        hint = QLabel("↑↓ 选择 · Enter 跳转 · Esc 关闭 · 点击面板外关闭")
        hint.setObjectName("Hint")
        foot.addWidget(hint, 1)
        self.btn_clear_recent = QToolButton()
        self.btn_clear_recent.setObjectName("Ghost")
        self.btn_clear_recent.setText("清空最近使用")
        self.btn_clear_recent.setToolTip("清空「最近使用」记录（不影响库内容）")
        self.btn_clear_recent.clicked.connect(self._on_clear_recent)
        foot.addWidget(self.btn_clear_recent)
        root.addLayout(foot)

        sc_esc = QShortcut(QKeySequence("Escape"), self)
        sc_esc.activated.connect(self.reject)

    # ---------------- 数据 ----------------
    def _recent_items(self):
        """把主窗的最近使用解析成 (kind, id, 标题, 副标题)；库中已删除的丢弃"""
        out = []
        for rec in self.recent:
            kind, rid = rec.get("kind"), str(rec.get("id") or "")
            title, sub = None, ""
            try:
                if kind == "entry":
                    e = self.db.get_entry(rid)
                    if e:
                        title = e.get("title") or "(无标题)"
                        sub = "%s · %s" % (dbmod.display_vendor(e.get("vendor")),
                                           e.get("category") or "-")
                elif kind == "err":
                    e = self.db.get_err(rid)
                    if e:
                        title = e.get("pattern") or "(无 pattern)"
                        sub = "%s · %s" % (dbmod.display_vendor(e.get("vendor")),
                                           e.get("category") or "-")
                elif kind == "tree":
                    t = self.db.get_tree(rid)
                    if t:
                        title = t.get("symptom") or "(未命名)"
                        sub = t.get("category") or "-"
            except Exception:
                continue
            if title:
                out.append((kind, rid, title, sub))
        return out

    @staticmethod
    def _kw_blob(*parts):
        return " ".join(str(p or "") for p in parts).lower()

    def _search_errs(self, kw):
        rows = self.db.all_errs()
        return [e for e in rows
                if kw in self._kw_blob(e.get("pattern"), e.get("cause"),
                                       e.get("category"), e.get("fix_template"),
                                       e.get("examples"),
                                       [s.get("text") for s in
                                        (e.get("solution_steps") or [])
                                        if isinstance(s, dict)])]

    def _search_trees(self, kw):
        rows = self.db.all_trees()
        out = []
        for t in rows:
            step_texts = " ".join(
                "%s %s" % (s.get("title") or "", s.get("explain") or "")
                for s in (t.get("steps") or []) if isinstance(s, dict))
            if kw in self._kw_blob(t.get("symptom"), t.get("category"),
                                   t.get("vendor_hint"), step_texts):
                out.append(t)
        return out

    # ---------------- 渲染 ----------------
    @staticmethod
    def _section(text):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)          # 分组标题不可选，键盘导航自动跳过
        return item

    def _add_result(self, kind, rid, title, sub):
        text = "[%s] %s    ─  %s" % (KIND_TAG[kind], title, sub)
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, {"kind": kind, "id": rid})
        self.lst.addItem(item)

    def _reload(self, keyword=""):
        kw = (keyword or "").strip().lower()
        self.lst.clear()
        if not kw:
            recent = self._recent_items()
            if recent:
                self.lst.addItem(self._section("── 最近使用（%d）──" % len(recent)))
                for kind, rid, title, sub in recent:
                    self._add_result(kind, rid, title, sub)
            else:
                self.lst.addItem(self._section(
                    "输入关键字搜索三库；暂无最近使用记录。"))
            return

        found = False
        # 命令库：复用 db.search()（标题/描述/命令/备注/型号/场景/厂商/OS）
        try:
            entries = self.db.search(kw)
        except Exception:
            entries = []
        if entries:
            found = True
            self.lst.addItem(self._section("── 命令（%d%s）──"
                             % (len(entries), "，超出仅显示前 %d" % GROUP_CAP
                                if len(entries) > GROUP_CAP else "")))
            for e in entries[:GROUP_CAP]:
                self._add_result("entry", e.get("uuid"), e.get("title") or "(无标题)",
                                 "%s · %s" % (dbmod.display_vendor(e.get("vendor")),
                                              e.get("category") or "-"))
        # 报错字典
        try:
            errs = self._search_errs(kw)
        except Exception:
            errs = []
        if errs:
            found = True
            self.lst.addItem(self._section("── 报错（%d%s）──"
                             % (len(errs), "，超出仅显示前 %d" % GROUP_CAP
                                if len(errs) > GROUP_CAP else "")))
            for e in errs[:GROUP_CAP]:
                self._add_result("err", e.get("err_id"), e.get("pattern") or "(无 pattern)",
                                 "%s · %s" % (dbmod.display_vendor(e.get("vendor")),
                                              e.get("category") or "-"))
        # 排查树
        try:
            trees = self._search_trees(kw)
        except Exception:
            trees = []
        if trees:
            found = True
            self.lst.addItem(self._section("── 树（%d%s）──"
                             % (len(trees), "，超出仅显示前 %d" % GROUP_CAP
                                if len(trees) > GROUP_CAP else "")))
            for t in trees[:GROUP_CAP]:
                self._add_result("tree", t.get("tree_id"),
                                 t.get("symptom") or "(未命名)",
                                 t.get("category") or "-")
        if not found:
            self.lst.addItem(self._section(
                "没有匹配结果 —— 换个关键字试试（命令/报错/树三库都已搜索）。"))
        # 把选中落在第一个可选条目上
        for i in range(self.lst.count()):
            if self.lst.item(i).flags() & Qt.ItemIsEnabled:
                self.lst.setCurrentRow(i)
                break

    # ---------------- 动作 ----------------
    def _activate(self, item):
        data = item.data(Qt.UserRole) if item else None
        if not isinstance(data, dict) or not data.get("id"):
            return
        self.choice = {"kind": data["kind"], "id": data["id"]}
        self.accept()

    def _on_clear_recent(self):
        if QMessageBox.question(
                self, "清空最近使用",
                "清空全部「最近使用」记录？\n（只影响面板展示，不改动命令库内容）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.recent = []
        if self.on_clear_recent:
            try:
                self.on_clear_recent()
            except Exception:
                pass
        self._reload(self.ed_search.text())

    # ---------------- 事件 ----------------
    def eventFilter(self, obj, ev):
        # 输入框 ↑↓ 移动列表选择、Enter 激活
        if obj is self.ed_search and ev.type() == QEvent.KeyPress:
            key = ev.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self.lst.currentRow()
                n = self.lst.count()
                step = 1 if key == Qt.Key_Down else -1
                for _ in range(n):
                    row = (row + step) % n if row >= 0 else 0
                    if self.lst.item(row).flags() & Qt.ItemIsEnabled:
                        self.lst.setCurrentRow(row)
                        self.lst.scrollToItem(self.lst.item(row))
                        break
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self.lst.currentItem()
                if item and item.flags() & Qt.ItemIsEnabled:
                    self._activate(item)
                return True
        # 点击浮层外部关闭
        if ev.type() == QEvent.MouseButtonPress and self.isVisible():
            try:
                w = QApplication.widgetAt(ev.globalPos())
                inside = False
                while w is not None:
                    if w is self:
                        inside = True
                        break
                    w = w.parentWidget()
                if not inside:
                    self.reject()
                    return True
            except Exception:
                pass
        return super().eventFilter(obj, ev)

    def done(self, result):
        try:
            QApplication.instance().removeEventFilter(self)
        except Exception:
            pass
        super().done(result)
