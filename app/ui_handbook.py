# -*- coding: utf-8 -*-
"""
ui_handbook.py —— 速查手册 Tab（R 批 2026-11-02）

定位：离网现场的「纸质手册替代品」。七本手册（HTTP 状态码 / 常用端口 / Git /
Linux / Vim / SSH / 网络命令）以独立 JSON 随程序打包，本 Tab 只读展示与检索，
**不写库、不改种子、不联网**。

界面口径（AGENTS.md §6）：
    · 左「分册 → 分组」两级树，右「表格化」正文：QTreeWidget + QTableWidget；
    · 分栏走 theme.GripSplitter，比例记忆 key=handbook_split（split_state /
      restore_split_state 由 MainWindow 统一收口）；
    · 样式只走 theme.qss 既有 objectName 与 theme.py token，无内联色值。

检索口径：
    · 本页搜索框 = 单册内搜索（跨分组，命中行带「分组」列）；
    · Ctrl+K 全局搜索 = 四库（命令 / 报错 / 树 / 手册）跨册检索，跳转落到本 Tab。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMenu, QToolButton, QApplication,
)

import handbook as hb
from theme import GripSplitter, TEXT_MUTED, read_sizes, mono_font, ui_font

KIND_DOC = Qt.UserRole + 1
KIND_GROUP = Qt.UserRole + 2


class HandbookTab(QWidget):
    """速查手册：分册树 + 分组表格 + 本册搜索 + 复制"""

    SPLIT_KEY = "handbook_split"
    SPLIT_MINS = (200, 480)

    def __init__(self, docs=None, parent=None):
        super(HandbookTab, self).__init__(parent)
        self.docs = []
        self.problems = []
        self._pending_split = None
        self._current_doc = None
        self._current_group = None          # 组下标（搜索视图下为 None）
        self._view_rows = []                # 当前表格里的 [(cells, group_name)]
        self._build()
        self.reload(docs)

    # ==================================================================
    # 数据加载
    # ==================================================================
    def reload(self, docs=None):
        """重新加载手册（docs 为 None 时按两级目录自行加载）"""
        if docs is None:
            self.docs, self.problems = hb.load_docs()
        else:
            self.docs, self.problems = list(docs), []
        self._populate_tree()
        self._update_head()
        if self.problems:
            self.lbl_hint.setText("⚠ 手册数据存在 %d 处问题（见下方），可用内容仍可查阅。"
                                  % len(self.problems))
            self.lbl_hint.setToolTip("\n".join(self.problems[:20]))
        else:
            self.lbl_hint.setToolTip("")

    def _update_head(self):
        if self.docs:
            self.lbl_summary.setText(hb.summary(self.docs))
        else:
            self.lbl_summary.setText("未加载到任何手册")

    def _populate_tree(self):
        self.tree.clear()
        for doc in self.docs:
            groups, rows = hb.doc_stats(doc)
            top = QTreeWidgetItem(["%s（%d）" % (doc.get("title") or doc.get("doc_id"), rows)])
            top.setData(0, KIND_DOC, doc.get("doc_id"))
            top.setToolTip(0, "%s\n%s\n来源：%s"
                           % (doc.get("title") or "", doc.get("subtitle") or "",
                              doc.get("source") or ""))
            font = ui_font()
            font.setBold(True)
            top.setFont(0, font)
            for gi, group in enumerate(doc.get("groups") or []):
                child = QTreeWidgetItem(["%s（%d）" % (group.get("name") or "?", 
                                                       len(group.get("rows") or []))])
                child.setData(0, KIND_GROUP, (doc.get("doc_id"), gi))
                child.setToolTip(0, group.get("desc") or "")
                top.addChild(child)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()
        if self.tree.topLevelItemCount():
            first = self.tree.topLevelItem(0)
            target = first.child(0) if first.childCount() else first
            self.tree.setCurrentItem(target)

    # ==================================================================
    # 布局
    # ==================================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head_row = QHBoxLayout()
        head = QLabel("<b>速查手册</b> —— 七本手册（HTTP 状态码 / 常用端口 / Git / "
                      "Linux / Vim / SSH / 网络命令），数据随程序打包，<b>不写命令库</b>。"
                      "右键单元格可复制，Ctrl+K 可跨册搜索。")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        head_row.addWidget(head, 1)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("CountBadge")
        head_row.addWidget(self.lbl_summary, 0, Qt.AlignTop)
        root.addLayout(head_row)

        split = GripSplitter(Qt.Horizontal)

        # ---- 左：分册 / 分组树 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setMinimumWidth(self.SPLIT_MINS[0])
        self.tree.currentItemChanged.connect(self._on_tree_changed)
        lv.addWidget(self.tree, 1)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("HintMuted")
        self.lbl_hint.setWordWrap(True)
        lv.addWidget(self.lbl_hint)
        split.addWidget(left)

        # ---- 右：搜索 + 表格 + 口径说明 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(4)

        row = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("SearchBox")
        self.ed_search.setPlaceholderText("在本册内搜索（任一列包含即命中；清空回到分组视图）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._on_search)
        row.addWidget(self.ed_search, 1)

        self.btn_copy = QPushButton("复制整组")
        self.btn_copy.setObjectName("Ghost")
        self.btn_copy.setToolTip("把当前表格按 TSV 复制（含表头），可直接粘进表格软件")
        self.btn_copy.clicked.connect(self._copy_all)
        row.addWidget(self.btn_copy)
        rv.addLayout(row)

        self.lbl_title = QLabel("")
        self.lbl_title.setObjectName("SectionTitle")
        self.lbl_title.setWordWrap(True)
        rv.addWidget(self.lbl_title)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setWordWrap(True)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tbl.setAlternatingRowColors(False)
        self.tbl.horizontalHeader().setHighlightSections(False)
        self.tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(self._on_context_menu)
        rv.addWidget(self.tbl, 1)

        # 口径说明：默认收起，点开看这本手册的边界与来源
        self.btn_notes = QToolButton()
        self.btn_notes.setObjectName("FilterToggle")
        self.btn_notes.setText("口径说明 / 来源")
        self.btn_notes.setCheckable(True)
        self.btn_notes.setToolTip("展开本册的数据来源、收录口径与使用注意")
        self.btn_notes.toggled.connect(self._toggle_notes)
        rv.addWidget(self.btn_notes, 0, Qt.AlignLeft)

        self.lbl_notes = QLabel("")
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.setObjectName("Hint")
        self.lbl_notes.setTextFormat(Qt.RichText)
        self.lbl_notes.setVisible(False)
        rv.addWidget(self.lbl_notes)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 1150])
        self.split = split
        root.addWidget(split, 1)

    # ==================================================================
    # 分栏记忆（MainWindow 统一收口；pending 模式同 ui_diff / ui_runbook）
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
        if self._pending_split:
            self.split.setSizes(self._pending_split)
            self._pending_split = None

    def showEvent(self, event):
        super(HandbookTab, self).showEvent(event)
        self._apply_pending_split()

    # ==================================================================
    # 树交互 / 渲染
    # ==================================================================
    def _on_tree_changed(self, current, _previous):
        if current is None:
            return
        group_ref = current.data(0, KIND_GROUP)
        doc_id = current.data(0, KIND_DOC)
        if group_ref:
            self.ed_search.blockSignals(True)
            self.ed_search.clear()
            self.ed_search.blockSignals(False)
            doc_id, gi = group_ref
            self._show_group(doc_id, gi)
        elif doc_id:
            self.ed_search.blockSignals(True)
            self.ed_search.clear()
            self.ed_search.blockSignals(False)
            self._show_doc_overview(doc_id)

    def _doc_by_id(self, doc_id):
        return hb.find_doc(self.docs, doc_id)

    def _show_doc_overview(self, doc_id):
        """点分册节点：展示该册的“总览”（分组说明 + 条数），避免空白页"""
        doc = self._doc_by_id(doc_id)
        if not doc:
            return
        self._current_doc, self._current_group = doc, None
        rows = []
        for group in doc.get("groups") or []:
            rows.append([group.get("name") or "", str(len(group.get("rows") or [])),
                         group.get("desc") or "", "点左侧分组查看明细"])
        self._fill_table(["分组", "条数", "说明", "操作"], rows,
                         "%s · 总览（%s）" % (doc.get("title"), doc.get("subtitle") or ""),
                         flat=True)
        self._render_notes(doc)

    def _show_group(self, doc_id, gi):
        doc = self._doc_by_id(doc_id)
        if not doc:
            return
        groups = doc.get("groups") or []
        if gi >= len(groups):
            return
        group = groups[gi]
        self._current_doc, self._current_group = doc, gi
        rows = [[list(r), group.get("name") or ""] for r in group.get("rows") or []]
        self._fill_table(list(group.get("columns") or []), rows,
                         "%s · %s" % (doc.get("title"), group.get("name")),
                         desc=group.get("desc") or "")
        self._render_notes(doc)

    def _on_search(self, text):
        kw = (text or "").strip()
        doc = self._current_doc
        if not doc:
            return
        if not kw:
            if self._current_group is not None:
                self._show_group(doc.get("doc_id"), self._current_group)
            else:
                self._show_doc_overview(doc.get("doc_id"))
            return
        hits = hb.search([doc], kw)
        base_cols = (doc.get("groups") or [{}])[0].get("columns") or ["列1", "列2", "列3", "列4"]
        rows = [[h["cells"], h["group"]] for h in hits]
        self._fill_table(["分组"] + list(base_cols), rows,
                         "「%s」在《%s》内命中 %d 条" % (kw, doc.get("title"), len(hits)),
                         desc="本册搜索（跨分组）；要跨册检索用 Ctrl+K。",
                         prefix_group=True)
        self._render_notes(doc)

    def _fill_table(self, columns, rows, title, desc="", flat=False, prefix_group=False):
        """
        columns —— 表头（4 或 5 列）
        rows    —— [(cells4, group_name)]；flat=True 时 rows 为 [[4 个单元格]]
        """
        self._view_rows = []
        self._view_display = []
        self.lbl_title.setText(("<span style='color:%s'>%s</span>%s"
                                % (TEXT_MUTED, title,
                                   ("　·　%s" % desc) if desc else "")))
        self.lbl_title.setTextFormat(Qt.RichText)
        self.tbl.clear()
        self.tbl.setColumnCount(len(columns))
        self.tbl.setHorizontalHeaderLabels(columns)
        header = self.tbl.horizontalHeader()
        for i in range(len(columns)):
            if i == 0:
                header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            else:
                header.setSectionResizeMode(i, QHeaderView.Stretch)

        self.tbl.setRowCount(len(rows))
        for ri, item in enumerate(rows):
            if flat:
                cells, gname = list(item), ""
            else:
                cells, gname = list(item[0]), item[1]
            if prefix_group:
                display = [gname] + cells
            else:
                display = cells
            for ci, value in enumerate(display):
                cell = QTableWidgetItem(str(value))
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                # 命令行类内容用等宽字体（第 1 列或含命令的列）
                if ci > 0 and not flat:
                    cell.setFont(mono_font(9))
                if ci == 0 and not flat and prefix_group:
                    cell.setForeground(QColor(TEXT_MUTED))
                self.tbl.setItem(ri, ci, cell)
            self._view_rows.append((cells, gname))
            self._view_display.append(display)
        self.tbl.resizeRowsToContents()
        if rows:
            self.tbl.selectRow(0)

    def _render_notes(self, doc):
        notes = doc.get("notes") or []
        lines = ["<b>%s</b>　<span style='color:%s'>%s</span>"
                 % (doc.get("title") or "", TEXT_MUTED, doc.get("subtitle") or "")]
        lines.append("<b>来源：</b>%s　<b>更新时间：</b>%s"
                     % (doc.get("source") or "-", doc.get("updated") or "-"))
        if notes:
            lines.append("<b>口径与注意：</b>")
            lines.extend("· %s" % n for n in notes)
        self.lbl_notes.setText("<br>".join(lines))

    def _toggle_notes(self, checked):
        self.lbl_notes.setVisible(bool(checked))

    # ==================================================================
    # 复制
    # ==================================================================
    def _row_cells(self, row_index):
        if 0 <= row_index < len(self._view_rows):
            return self._view_rows[row_index][0]
        return None

    def _on_context_menu(self, pos):
        idx = self.tbl.indexAt(pos)
        menu = QMenu(self)
        act_row = menu.addAction("复制整行")
        act_cell = menu.addAction("复制单元格")
        menu.addSeparator()
        act_all = menu.addAction("复制整组（TSV，含表头）")
        if not idx.isValid():
            act_row.setEnabled(False)
            act_cell.setEnabled(False)
        chosen = menu.exec_(self.tbl.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_row:
            self._copy_row(idx.row())
        elif chosen is act_cell:
            item = self.tbl.item(idx.row(), idx.column())
            self._set_clipboard(item.text() if item else "")
        elif chosen is act_all:
            self._copy_all()

    def _set_clipboard(self, text):
        QApplication.clipboard().setText(text or "")
        try:
            self.window().statusBar().showMessage("已复制（%d 字符）" % len(text or ""), 2500)
        except Exception:
            pass

    def _copy_row(self, row_index):
        cells = self._row_cells(row_index)
        if cells:
            self._set_clipboard(hb.row_text(cells))

    def _copy_all(self):
        if not self._view_display:
            return
        headers = [self.tbl.horizontalHeaderItem(i).text()
                   for i in range(self.tbl.columnCount())]
        lines = ["\t".join(headers)]
        for display in self._view_display:
            lines.append("\t".join(str(c).replace("\n", " / ") for c in display))
        self._set_clipboard("\n".join(lines))

    # ==================================================================
    # Ctrl+K 跳转定位
    # ==================================================================
    def goto(self, doc_id, group_index=None, row_index=0):
        """跳到某册某组的某一行（Ctrl+K 四库搜索的落点）；返回是否成功"""
        doc = self._doc_by_id(doc_id)
        if not doc:
            return False
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, KIND_DOC) != doc_id:
                continue
            target = top
            if group_index is not None and 0 <= group_index < top.childCount():
                target = top.child(group_index)
            self.tree.setCurrentItem(target)
            self.tree.scrollToItem(target)
            gi = group_index if group_index is not None else 0
            self._show_group(doc_id, gi)
            if 0 <= row_index < self.tbl.rowCount():
                self.tbl.selectRow(row_index)
                self.tbl.scrollToItem(self.tbl.item(row_index, 0))
            return True
        return False
