# -*- coding: utf-8 -*-
"""
ui_dashboard.py —— 覆盖度仪表盘（命令库顶部折叠卡片，2026-10-09 批次任务3）

口径（与 docs/verify-guide.md 四档标准一致，全部 db 直查、无缓存）：
    绿       = verified = 1
    语法核对 = verified ≠ 1 且 exec_level = 'verified-cli'（语法核对通过不判绿）
    挂起     = verified ≠ 1 且 exec_level ∈ (skeleton, web-only)，
              或 notes 含「不可验证」标注（centos7 systemctl 类等，如实计入挂起段）
    未验证   = 其余

硬约束：
    · 只读：零写入口（面板内没有任何 db 写方法调用）
    · 渲染量级控制：QTableWidget + 底色深浅映射，不上图表库
    · 色值一律取 theme.py 常量（矩阵单元格按数据驱动着色，
      与 ui_main 厂商色块同理属 setStyleSheet 的合法数据驱动场景）
    · 空分类/空厂商正常渲染（灰格），不报错
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QToolButton, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QFrame)

import db as dbmod
from theme import (BG_RAISED, BG_INPUT, BORDER, ACCENT, SUCCESS, WARNING,
                   TEXT_PRIMARY, TEXT_MUTED, SPACE_SM, SPACE_MD, RADIUS)


# ---------------------------------------------------------------------------
# 四档计数（纯 SQL，条目/报错/树三库；err_dict 无 notes 列，"不可验证"只查 entries）
# ---------------------------------------------------------------------------

def _tier_counts(conn, table, has_notes=True):
    """返回 dict：green / syntax / suspended / unverified / total"""
    notes_cond = (" OR (verified <> 1 AND exec_level = '' "
                  "AND notes LIKE '%不可验证%')") if has_notes else ""
    sql = (
        "SELECT "
        "SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END) AS green, "
        "SUM(CASE WHEN verified <> 1 AND exec_level = 'verified-cli' "
        "THEN 1 ELSE 0 END) AS syntax, "
        "SUM(CASE WHEN verified <> 1 AND exec_level IN ('skeleton', 'web-only') "
        "THEN 1 ELSE 0 END) AS skeleton "
        "FROM %s" % table)
    row = conn.execute(sql).fetchone()
    green = int(row["green"] or 0)
    syntax = int(row["syntax"] or 0)
    suspended = int(row["skeleton"] or 0)
    if has_notes:
        extra = conn.execute(
            "SELECT count(*) FROM %s WHERE verified <> 1 AND exec_level = '' "
            "AND notes LIKE '%%不可验证%%'" % table).fetchone()[0]
        suspended += int(extra)
    total = int(conn.execute("SELECT count(*) FROM %s" % table).fetchone()[0])
    unverified = total - green - syntax - suspended
    return {"green": green, "syntax": syntax, "suspended": suspended,
            "unverified": max(0, unverified), "total": total}


def coverage_counts(db):
    """三库四档计数汇总（仪表盘与冒烟对账共用）"""
    out = {}
    try:
        out["entries"] = _tier_counts(db.conn, "entries", has_notes=True)
    except Exception:
        out["entries"] = None
    try:
        out["errs"] = _tier_counts(db.conn, "err_dict", has_notes=False)
    except Exception:
        out["errs"] = None
    try:
        row = db.conn.execute(
            "SELECT SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END), count(*) "
            "FROM trouble_trees").fetchone()
        green = int(row[0] or 0)
        total = int(row[1] or 0)
        out["trees"] = {"green": green, "syntax": 0, "suspended": 0,
                        "unverified": total - green, "total": total}
    except Exception:
        out["trees"] = None
    return out


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _lerp_color(c1, c2, ratio):
    """两个 #rrggbb 之间线性插值（ratio 0→c1, 1→c2）"""
    a = QColor(c1)
    b = QColor(c2)
    mix = lambda x, y: int(round(x + (y - x) * ratio))
    return "#%02x%02x%02x" % (mix(a.red(), b.red()),
                              mix(a.green(), b.green()),
                              mix(a.blue(), b.blue()))


class TierBar(QWidget):
    """四档分段堆叠条：绿 / 语法核对 / 挂起 / 未验证"""

    SEGMENTS = (("绿", SUCCESS), ("语法核对", ACCENT),
                ("挂起", WARNING), ("未验证", BORDER))

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.counts = {"green": 0, "syntax": 0, "suspended": 0, "unverified": 0}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_SM)
        self.lbl_name = QLabel(label)
        self.lbl_name.setFixedWidth(52)
        row.addWidget(self.lbl_name)
        self.bar = QWidget()
        self.bar.setFixedHeight(12)
        self.bar_row = QHBoxLayout(self.bar)
        self.bar_row.setContentsMargins(0, 0, 0, 0)
        self.bar_row.setSpacing(1)
        row.addWidget(self.bar, 1)
        self.lbl_nums = QLabel("")
        self.lbl_nums.setObjectName("Hint")
        self.lbl_nums.setMinimumWidth(330)
        row.addWidget(self.lbl_nums)
        self._paint()

    def set_counts(self, counts):
        if counts:
            self.counts = counts
        self._paint()

    def _paint(self):
        while self.bar_row.count():
            item = self.bar_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        c = self.counts
        total = max(1, c.get("green", 0) + c.get("syntax", 0)
                    + c.get("suspended", 0) + c.get("unverified", 0))
        values = (c.get("green", 0), c.get("syntax", 0),
                  c.get("suspended", 0), c.get("unverified", 0))
        for (name, color), value in zip(self.SEGMENTS, values):
            if value <= 0:
                continue
            seg = QFrame()
            seg.setFixedHeight(12)
            seg.setStyleSheet(
                "background-color:%s; border-radius:%dpx;"
                % (color, RADIUS // 2))
            self.bar_row.addWidget(seg, max(1, int(round(100.0 * value / total))))
        self.lbl_nums.setText("　".join(
            "%s %d" % (name, value)
            for (name, _c), value in zip(self.SEGMENTS, values)))
        tip = "总计 %d（口径见 docs/verify-guide.md 四档标准；「不可验证」计入挂起）" \
              % c.get("total", total)
        self.setToolTip(tip)


class CoverageDashboard(QWidget):
    """命令库顶部折叠卡片：标题行常显摘要，展开见矩阵 + 三库四档条"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE_SM)

        self.toggle = QToolButton()
        self.toggle.setObjectName("CollapseHeader")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setText("▸ 覆盖度仪表盘")
        self.toggle.setToolTip("展开厂商×分类覆盖矩阵与三库验证进度（只读，直查 db）")
        self.toggle.toggled.connect(self._on_toggle)
        root.addWidget(self.toggle)

        self.content = QWidget()
        cv = QVBoxLayout(self.content)
        cv.setContentsMargins(SPACE_MD, 0, SPACE_MD, 0)
        cv.setSpacing(SPACE_SM)

        self.bar_entries = TierBar("命令")
        self.bar_errs = TierBar("报错")
        self.bar_trees = TierBar("树")
        cv.addWidget(self.bar_entries)
        cv.addWidget(self.bar_errs)
        cv.addWidget(self.bar_trees)

        self.tbl = QTableWidget(0, 0)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setMaximumHeight(260)
        self.tbl.horizontalHeader().setDefaultSectionSize(64)
        cv.addWidget(self.tbl)
        self.lbl_matrix = QLabel("")
        self.lbl_matrix.setObjectName("Hint")
        cv.addWidget(self.lbl_matrix)

        self.content.setVisible(False)
        root.addWidget(self.content)

    # ---------------- 对外 ----------------
    def refresh(self):
        """刷新标题行摘要（refresh_all 直查 db，无缓存；展开状态同时重建矩阵）"""
        counts = coverage_counts(self.db)
        e = counts.get("entries") or {"total": 0, "green": 0, "suspended": 0,
                                      "unverified": 0}
        self.toggle.setText(
            "▸ 覆盖度仪表盘　—　命令 %d｜绿 %d｜挂起 %d｜未验证 %d"
            % (e["total"], e["green"], e["suspended"], e["unverified"]))
        if self.toggle.isChecked():
            self._rebuild(counts)
        return counts

    # ---------------- 内部 ----------------
    def _on_toggle(self, checked):
        self.toggle.setText(self.toggle.text().replace(
            "▸ ", "▾ " if checked else "▸ ", 1))
        self.content.setVisible(checked)
        if checked:
            self._rebuild(coverage_counts(self.db))

    def _rebuild(self, counts):
        self.bar_entries.set_counts(counts.get("entries"))
        self.bar_errs.set_counts(counts.get("errs"))
        self.bar_trees.set_counts(counts.get("trees"))
        self._rebuild_matrix()

    def _rebuild_matrix(self):
        """厂商×分类条目数矩阵（直接 GROUP BY，空分类空厂商自然缺行=灰格）"""
        try:
            rows = self.db.conn.execute(
                "SELECT vendor, category, count(*) AS n FROM entries "
                "GROUP BY vendor, category").fetchall()
        except Exception as exc:
            self.tbl.setRowCount(0)
            self.tbl.setColumnCount(1)
            self.tbl.setHorizontalHeaderLabels(["（无法读取：%s）" % exc])
            return
        if not rows:
            self.tbl.setRowCount(0)
            self.tbl.setColumnCount(1)
            self.tbl.setHorizontalHeaderLabels(["（命令库为空）"])
            return
        vendors, cats, cell = [], [], {}
        for r in rows:
            v, cat, n = r["vendor"] or "-", r["category"] or "-", int(r["n"] or 0)
            if v not in vendors:
                vendors.append(v)
            if cat not in cats:
                cats.append(cat)
            cell[(v, cat)] = n
        vendors.sort(key=lambda v: -sum(cell.get((v, c), 0) for c in cats))
        cats.sort()
        max_n = max(cell.values()) if cell else 1

        self.tbl.clear()
        self.tbl.setRowCount(len(vendors))
        self.tbl.setColumnCount(len(cats) + 1)
        self.tbl.setHorizontalHeaderLabels(["厂商"] + cats)
        self.tbl.setVerticalHeaderLabels([dbmod.display_vendor(v) for v in vendors])
        for r, v in enumerate(vendors):
            for c, cat in enumerate(cats):
                n = cell.get((v, cat), 0)
                item = QTableWidgetItem(str(n) if n else "")
                item.setTextAlignment(Qt.AlignCenter)
                if n:
                    # 底色深浅映射条目数：BG_RAISED → ACCENT 线性插值（数据驱动着色）
                    bg = _lerp_color(BG_RAISED, ACCENT, 0.15 + 0.85 * n / max_n)
                    item.setBackground(QColor(bg))
                    item.setForeground(QColor(TEXT_PRIMARY))
                    item.setToolTip("%s × %s：%d 条" % (dbmod.display_vendor(v),
                                                        cat, n))
                else:
                    item.setBackground(QColor(BG_INPUT))
                    item.setForeground(QColor(TEXT_MUTED))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)  # 只读
                self.tbl.setItem(r, c + 1, item)
            # 行尾小计
            total_v = sum(cell.get((v, c), 0) for c in cats)
            it = QTableWidgetItem(str(total_v))
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QColor(TEXT_PRIMARY))
            it.setBackground(QColor(BORDER))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl.setItem(r, len(cats) + 1, it)
        self.tbl.setHorizontalHeaderLabels(["厂商"] + cats + ["小计"])
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        col_sum = sum(cell.values())
        self.lbl_matrix.setText(
            "共 %d 个厂商 × %d 个分类，合计 %d 条；格内数字 = 条目数，灰格 = 无条目（只读视图）。"
            % (len(vendors), len(cats), col_sum))
