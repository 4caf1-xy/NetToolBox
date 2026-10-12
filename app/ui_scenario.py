# -*- coding: utf-8 -*-
"""
ui_scenario.py —— 场景包 Tab（2026-10-12 新增）

定位：跨三库（命令条目/排查树/报错条目）的策划捆绑，清单式打包复用——
与排查向导的区别：树逐步引导决策，场景包是按序清单一键调出整组内容。

单一事实源：包内 items 只引用不复制（ref_type=entry/tree/err，多厂商步骤
用 by_vendor 映射复用排查树 cmd_ref.by_vendor 语义）；坏引用由
--validate-seed [E] 硬拦截 + 本 Tab 渲染前 get_* 复核兜底（失效项灰卡提示，
不拖垮整包加载）。

复用件（不另造轮子）：
    · 跳转定位：MainWindow.jump_to（entry→命令库选中 / tree→排查向导 /
      err→报错字典管理器，三库定位机制全量复用）
    · 复制守卫：ui_main.guard_skeleton_copy / guard_rollback_confirm
      （骨架弹窗 + 变更类确认，parent=主窗同口径）
    · 必填守卫：渲染后仍残留 {{占位}} 的必填参数 → 拦截复制并提示
    · 命令渲染：renderer.render_entry（默认值渲染，与 validate 同一执法点）
    · 语法高亮：ui_main.CommandHighlighter

UI 纪律（AGENTS.md §6）：
    · QSplitter（水平 1 层）+ 状态记忆 key=scenario_split（pending 模式，
      Tab 未显示时未布局，首次 show 应用——同 ui_runbook）；
    · 样式只走 theme.qss 既有 objectName / theme.py token，无内联色值。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QComboBox, QScrollArea, QPlainTextEdit,
    QGroupBox,
)

import db as dbmod
import renderer
from theme import ACCENT, DANGER, WARNING, TEXT_MUTED
from ui_generator import mono_font

REF_TYPE_META = {
    # ref_type → (类型徽章, 类型说明)
    "entry": ("命令", "命令库条目"),
    "tree": ("排查树", "排查向导树"),
    "err": ("报错", "报错字典条目"),
}


class ScenarioTab(QWidget):
    """场景包 Tab：左包列表 | 右有序条目卡片（引用渲染 + 跳转 + 复制守卫）"""

    SPLIT_KEY = "scenario_split"
    SPLIT_MINS = (280, 420)

    def __init__(self, db, main=None):
        super(ScenarioTab, self).__init__(main)
        self.db = db
        self.main = main                 # 主窗引用：跳转 jump_to / 复制守卫借宿
        self._loading = False
        self._pending_split = None
        self._build()
        self.refresh()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ---- 工具行：厂商切换 + 统计 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("厂商视图:"))
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.setMinimumWidth(160)
        self.cmb_vendor.currentIndexChanged.connect(self._on_vendor_changed)
        bar.addWidget(self.cmb_vendor)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("Hint")
        bar.addWidget(self.lbl_count)
        bar.addStretch(1)
        self.lbl_hint = QLabel("清单式打包复用：条目只引用不复制，单项可跳转回对应库")
        self.lbl_hint.setObjectName("Hint")
        bar.addWidget(self.lbl_hint)
        root.addLayout(bar)

        # ---- 主体：左包列表 | 右条目卡片（QSplitter 1 层）----
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(4)
        self.lst_packs = QListWidget()
        self.lst_packs.currentRowChanged.connect(self._on_pack_changed)
        lv.addWidget(self.lst_packs, 1)
        split.addWidget(left)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.holder = QWidget()
        self.cards_lay = QVBoxLayout(self.holder)
        self.cards_lay.setContentsMargins(4, 0, 8, 0)
        self.cards_lay.setSpacing(8)
        self.scroll.setWidget(self.holder)
        split.addWidget(self.scroll)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 1000])
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
        from theme import read_sizes
        self._pending_split = read_sizes(state, self.SPLIT_KEY, self.SPLIT_MINS)
        if self._pending_split and self.isVisible():
            self._apply_pending_split()

    def _apply_pending_split(self):
        if getattr(self, "_pending_split", None):
            self.split.setSizes(self._pending_split)
            self._pending_split = None

    def showEvent(self, event):
        super(ScenarioTab, self).showEvent(event)
        self._apply_pending_split()

    # ==================================================================
    # 数据刷新与包选择
    # ==================================================================
    def refresh(self):
        """重读场景包（保持当前选中 pack_id；库缺表/为空给空态提示）"""
        try:
            packs = self.db.all_packs()
        except Exception:
            packs = []
        keep = self.current_pack_id() if self.current_pack() else None
        self._loading = True
        try:
            self.lst_packs.blockSignals(True)
            self.lst_packs.clear()
            for p in packs:
                item = QListWidgetItem("%s（%d 项）" % (p.get("title") or "(未命名)",
                                                       len(p.get("items") or [])))
                item.setData(Qt.UserRole, p.get("pack_id"))
                item.setToolTip(str(p.get("description") or ""))
                self.lst_packs.addItem(item)
            self.lst_packs.blockSignals(False)
            self.lbl_count.setText("%d 个场景包" % len(packs))
            row = 0
            for i, p in enumerate(packs):
                if keep and p.get("pack_id") == keep:
                    row = i
                    break
            self.lst_packs.setCurrentRow(row if packs else -1)
        finally:
            self._loading = False
        if packs:
            self._render_pack(self.current_pack())
        else:
            self._render_pack(None)

    def current_pack_id(self):
        item = self.lst_packs.currentItem()
        return item.data(Qt.UserRole) if item else None

    def current_pack(self):
        pid = self.current_pack_id()
        if not pid:
            return None
        try:
            return self.db.get_pack(pid)
        except Exception:
            return None

    def _on_pack_changed(self, _row):
        if self._loading:
            return
        self._render_pack(self.current_pack())

    # ==================================================================
    # 厂商视图
    # ==================================================================
    def _pack_vendors(self, pack):
        """包内出现的厂商全集（by_vendor 键 + 单值 entry 引用的厂商），稳定排序"""
        vendors = []
        for it in pack.get("items") or []:
            bvm = it.get("by_vendor") or {}
            if bvm:
                for v in bvm:
                    if v not in vendors:
                        vendors.append(v)
                continue
            if it.get("ref_type") == "entry":
                e = self._safe_entry(it.get("ref_id"))
                v = (e or {}).get("vendor") or ""
                if v and v not in vendors:
                    vendors.append(v)
        return sorted(vendors)

    def _rebuild_vendor_combo(self, pack):
        keep = str(self.cmb_vendor.currentData() or "")
        self.cmb_vendor.blockSignals(True)
        try:
            self.cmb_vendor.clear()
            self.cmb_vendor.addItem("全部厂商", "")
            if pack:
                for v in self._pack_vendors(pack):
                    self.cmb_vendor.addItem(dbmod.display_vendor(v), v)
            # 保留切换前的厂商视图（重建不重置——否则切厂商会被弹回"全部"）
            idx = self.cmb_vendor.findData(keep)
            if idx > 0:
                self.cmb_vendor.setCurrentIndex(idx)
        finally:
            self.cmb_vendor.blockSignals(False)

    def _on_vendor_changed(self, _idx):
        if self._loading:
            return
        # 厂商切换只重渲卡片，不重建下拉（重建会连带重置选择）
        self._render_pack(self.current_pack(), rebuild_combo=False)

    def _sel_vendor(self):
        return str(self.cmb_vendor.currentData() or "")

    # ==================================================================
    # 引用解析
    # ==================================================================
    def _safe_entry(self, uuid):
        try:
            return self.db.get_entry(uuid) if uuid else None
        except Exception:
            return None

    def _safe_tree(self, tree_id):
        try:
            return self.db.get_tree(tree_id) if tree_id else None
        except Exception:
            return None

    def _safe_err(self, err_id):
        try:
            return self.db.get_err(err_id) if err_id else None
        except Exception:
            return None

    def _resolve_item(self, item):
        """
        按当前厂商视图解析一个 item →
            {status: ok/missing/skip, entry|tree|err: dict, vendor: slug, hint: str}
        skip = 当前厂商视图下无对应条目（by_vendor 缺该厂商键 / 单值厂商不符）
        """
        rtype = item.get("ref_type")
        vendor = self._sel_vendor()
        bvm = item.get("by_vendor") or {}
        if rtype == "entry":
            if bvm:
                if not vendor:
                    return {"status": "multi", "vendors": sorted(bvm)}
                uid = bvm.get(vendor)
                if not uid:
                    return {"status": "skip"}
                e = self._safe_entry(uid)
                return {"status": "ok" if e else "missing", "entry": e,
                        "vendor": vendor}
            e = self._safe_entry(item.get("ref_id"))
            if not e:
                return {"status": "missing", "entry": None}
            if vendor and (e.get("vendor") or "") != vendor:
                return {"status": "skip"}
            return {"status": "ok", "entry": e, "vendor": e.get("vendor") or ""}
        if rtype == "tree":
            t = self._safe_tree(item.get("ref_id"))
            return {"status": "ok" if t else "missing", "tree": t}
        if rtype == "err":
            er = self._safe_err(item.get("ref_id"))
            if er and vendor and (er.get("vendor") or "") != vendor:
                return {"status": "skip"}
            return {"status": "ok" if er else "missing", "err": er}
        return {"status": "missing"}

    # ==================================================================
    # 卡片渲染
    # ==================================================================
    def _clear_cards(self):
        while self.cards_lay.count():
            item = self.cards_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render_pack(self, pack, rebuild_combo=True):
        self._clear_cards()
        if rebuild_combo:
            self._rebuild_vendor_combo(pack)
        if pack is None:
            empty = QLabel("没有场景包。种子侧为 seed_data/scenario_packs.json，"
                           "重新导入种子或检查该文件。")
            empty.setObjectName("Hint")
            self.cards_lay.addWidget(empty)
            self.cards_lay.addStretch(1)
            return
        for it in pack.get("items") or []:
            self.cards_lay.addWidget(self._build_card(pack, it))
        self.cards_lay.addStretch(1)

    @staticmethod
    def _esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _build_card(self, pack, item):
        seq = item.get("seq")
        rtype = item.get("ref_type")
        badge, type_desc = REF_TYPE_META.get(rtype, (rtype or "?", "未知引用类型"))
        note = str(item.get("note") or "")
        box = QGroupBox("第 %s 步 · %s" % (seq, badge))
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        r = self._resolve_item(item)
        status = r["status"]

        if status == "multi":
            vendors = r["vendors"]
            lay.addWidget(QLabel("多厂商步骤（%s）——用上方「厂商视图」切换后展开命令。"
                                 % "、".join(dbmod.display_vendor(v) for v in vendors)))
            if note:
                self._add_note(lay, note)
            return box

        if status == "skip":
            head = QLabel("<span style='color:%s'>当前厂商视图无此步骤（已置灰）</span>"
                          % TEXT_MUTED)
            head.setTextFormat(Qt.RichText)
            lay.addWidget(head)
            if note:
                self._add_note(lay, note)
            box.setEnabled(False)
            return box

        if status == "missing":
            head = QLabel("<span style='color:%s;font-weight:bold'>⚠ 引用失效</span> —— "
                          "目标已不在库中（validate-seed 会以 [E] 拦截此坏引用）"
                          % DANGER)
            head.setTextFormat(Qt.RichText)
            head.setWordWrap(True)
            lay.addWidget(head)
            if note:
                self._add_note(lay, note)
            return box

        if rtype == "entry":
            self._fill_entry_card(lay, box, item, r)
        elif rtype == "tree":
            self._fill_tree_card(lay, item, r)
        elif rtype == "err":
            self._fill_err_card(lay, item, r)
        return box

    def _add_note(self, lay, note):
        lbl = QLabel(note)
        lbl.setObjectName("Hint")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

    def _head_html(self, entry, extra=""):
        try:
            cat = renderer.classify_entry(entry)
        except Exception:
            cat = "change"
        cat_html = ("<span style='color:%s;font-weight:bold'>［变更类］</span>" % DANGER
                    if cat == "change"
                    else "<span style='color:%s'>［查询类］</span>" % ACCENT)
        sk = ""
        if str(entry.get("exec_level") or "").strip() == "skeleton":
            sk = " <span style='color:%s;font-weight:bold'>⚠ 骨架</span>" % WARNING
        return ("<b>%s</b> %s%s<br><span style='color:%s'>%s · %s · %s%s</span>"
                % (self._esc(entry.get("title") or "(无标题)"), cat_html, sk,
                   TEXT_MUTED,
                   dbmod.display_vendor(entry.get("vendor")),
                   entry.get("category") or "-",
                   dbmod.display_os(entry.get("os_family")), extra))

    def _fill_entry_card(self, lay, box, item, r):
        entry = r["entry"]
        head = QLabel(self._head_html(entry))
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)

        text, missing, _specs = renderer.render_entry(entry, {},
                                                      vendor=entry.get("vendor"))
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setLineWrapMode(QPlainTextEdit.NoWrap)
        txt.setFont(mono_font(10))
        txt.setObjectName("CodeBlock")
        txt.setPlainText(text or "（无命令）")
        txt.setMaximumHeight(180)
        try:
            from ui_main import CommandHighlighter
            self._hl = CommandHighlighter(txt.document())   # noqa: F841 生命周期随 card
        except Exception:
            pass
        lay.addWidget(txt)

        row = QHBoxLayout()
        btn_copy = QPushButton("复制命令")
        btn_copy.clicked.connect(lambda _=False, e=entry, t=text: self.on_copy_entry(e, t))
        row.addWidget(btn_copy)
        btn_jump = QPushButton("跳转定位 ↗")
        btn_jump.setToolTip("切到命令库并选中该条目")
        btn_jump.clicked.connect(lambda _=False, uid=entry.get("uuid"): self.on_jump("entry", uid))
        row.addWidget(btn_jump)
        row.addStretch(1)
        lay.addLayout(row)

        if missing:
            self._add_note(lay, "⚠ 以下必填参数没有默认值，复制前须到命令库表单补齐：%s"
                           % "、".join(missing))
        note = str(item.get("note") or "")
        if note:
            self._add_note(lay, note)

    def _fill_tree_card(self, lay, item, r):
        t = r["tree"]
        steps = t.get("steps") or []
        head = QLabel("<b>%s</b><br><span style='color:%s'>%s ｜ %d 个步骤</span>"
                      % (self._esc(t.get("symptom") or "(未命名)"), TEXT_MUTED,
                         self._vendors_text(t.get("vendor_hint") or []),
                         len(steps)))
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)
        row = QHBoxLayout()
        btn = QPushButton("打开排查树 ↗")
        btn.clicked.connect(lambda _=False, tid=t.get("tree_id"): self.on_jump("tree", tid))
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        note = str(item.get("note") or "")
        if note:
            self._add_note(lay, note)

    def _fill_err_card(self, lay, item, r):
        er = r["err"]
        head = QLabel("<b>%s</b><br><span style='color:%s'>%s ｜ %s ｜ pattern: %s</span>"
                      % (self._esc((er.get("cause") or "(无说明)")[:60]), TEXT_MUTED,
                         dbmod.display_vendor(er.get("vendor")),
                         er.get("category") or "-",
                         self._esc((er.get("pattern") or "")[:60])))
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)
        row = QHBoxLayout()
        btn = QPushButton("查看报错字典 ↗")
        btn.clicked.connect(lambda _=False, eid=er.get("err_id"): self.on_jump("err", eid))
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        note = str(item.get("note") or "")
        if note:
            self._add_note(lay, note)

    @staticmethod
    def _vendors_text(hint):
        try:
            return "、".join(dbmod.display_vendor(v) for v in (hint or []))
        except Exception:
            return ""

    # ==================================================================
    # 动作：复制（守卫）与跳转
    # ==================================================================
    def on_copy_entry(self, entry, text):
        if not entry:
            return
        # 守卫顺序（与操作单同口径）：必填占位拦截 → 骨架提醒 → 变更类回退确认
        _text, missing, _specs = renderer.render_entry(entry, {},
                                                       vendor=entry.get("vendor"))
        if missing:
            self._status("未复制：必填参数无默认值（%s），请先到命令库补齐。"
                         % "、".join(missing))
            return
        from ui_main import copy_to_clipboard, guard_skeleton_copy, guard_rollback_confirm
        parent = self.main if self.main is not None else self.window()
        guard_skeleton_copy(parent, entry)
        if not guard_rollback_confirm(parent, entry):
            self._status("已取消复制（变更类未确认）。")
            return
        if not copy_to_clipboard(text):
            self._status("复制失败（剪贴板被占用），请手动选中文本复制。")
            return
        try:
            n = renderer.count_effective_lines(text)
        except Exception:
            n = "?"
        self._status("已复制「%s」命令（%s 条有效命令）→ 切到 Xshell 直接粘贴"
                     % (entry.get("title") or "", n))

    def on_jump(self, kind, target_id):
        if not target_id:
            return
        win = self.main if self.main is not None else self.window()
        if not hasattr(win, "jump_to"):
            self._status("主窗不可用，无法跳转。")
            return
        win.jump_to(kind, target_id)

    def _status(self, text):
        win = self.main if self.main is not None else self.window()
        try:
            win.statusBar().showMessage(text)
        except Exception:
            pass
