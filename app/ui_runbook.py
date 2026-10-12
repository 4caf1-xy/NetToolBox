# -*- coding: utf-8 -*-
"""
ui_runbook.py —— 操作单生成器（Runbook）Tab（2026-10-12 新增）

定位：命令库落地的最后一公里——选厂商/OS + 场景，从命令库挑条目组成一份
可打印的变更窗口操作单：命令序列（params 已填）+ 每步预期回显要点 +
回退方案（实时取库）+ 复核勾选框。

数据模型 / 导出器：app/runbook_export.py（schema 1；rollback 不落盘、
变更类实时判定——唯一执法点 renderer.classify_entry）。

复用件（不另造轮子）：
    · 添加步骤：ui_panel.CommandPalette（Ctrl+K 同款三库浮层，选中命令条目即入列）
    · 参数填写：ui_generator.ParamFormWidget（必填校验 / 类型校验 / 端口展开全量复用）
    · 复制守卫：ui_main.guard_skeleton_copy / guard_rollback_confirm
      （parent=主窗 → 查询类不弹、变更类确认弹窗、当日抑制、视图菜单开关全同口径）
    · 命令渲染：renderer.render_entry
    · HTML 导出：runbook_export.export_runbook（token 同源校验 + 零外部请求）

UI 纪律（AGENTS.md §6）：
    · QSplitter（水平 1 层，嵌套 ≤2）+ 状态记忆 key=runbook_split（pending 模式，
      Tab 未显示时 QSplitter 未布局，首次 show 应用——同 ui_troubleshoot）；
    · 样式只走 theme.qss 既有 objectName / theme.py token，无内联色值。
"""

import os
import re
import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QListWidget, QListWidgetItem, QSplitter,
    QFileDialog, QMessageBox, QGroupBox, QScrollArea, QCheckBox,
)

import db as dbmod
import renderer
import runbook_export
from theme import (ACCENT, DANGER, WARNING, TEXT_MUTED,
                   read_sizes)
from ui_generator import ParamFormWidget, mono_font

RUNBOOKS_DIRNAME = "runbooks"       # exe 同目录子目录；gitignore / dist 核查口径
FILE_FILTER = "操作单 (*.json);;所有文件 (*.*)"
HTML_FILTER = "HTML (*.html);;所有文件 (*.*)"


def _runbooks_dir():
    """操作单 JSON 默认目录：exe 同目录 / runbooks（与 ui_state.json 同一落位纪律）"""
    try:
        base = dbmod.get_base_dir()
    except Exception:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, RUNBOOKS_DIRNAME)


def _safe_filename(title):
    """标题 → 安全文件名（保留中英文数字与常用符号，其余转下划线）"""
    name = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "_", str(title or "").strip())
    name = re.sub(r"\s+", " ", name).strip()
    return name[:60] or "未命名操作单"


class RunbookTab(QWidget):
    """操作单 Tab：头部元信息 + 有序步骤（uuid 引用 + 参数 + 预期回显 + 备注）"""

    SPLIT_KEY = "runbook_split"
    SPLIT_MINS = (300, 420)

    def __init__(self, db, main=None):
        super(RunbookTab, self).__init__(main)
        self.db = db
        self.main = main                 # 主窗引用：复制守卫借宿 _ui_state
        self.data = runbook_export.new_runbook()
        self.current_path = None         # 当前 .json；None = 未保存过
        self.dirty = False
        self._cur = -1                   # 当前选中步骤下标（data["steps"] 内）
        self._loading = False            # 载入步骤期间抑制回写
        self._pending_split = None
        self._build()
        self._sync_toolbar()
        self._show_step(-1)

    # ==================================================================
    # 构建
    # ==================================================================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ---- 工具行：文件操作 + 步骤操作 + 导出 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)

        def btn(text, slot, ghost=False, tip=""):
            b = QPushButton(text)
            if ghost:
                b.setObjectName("Ghost")
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
            return b

        self.btn_new = btn("新建", self.on_new, ghost=True, tip="新建空白操作单")
        self.btn_open = btn("打开…", self.on_open, ghost=True, tip="打开 .json 操作单")
        self.btn_save = btn("保存", self.on_save, tip="保存到当前 .json（首次保存弹另存为）")
        self.btn_saveas = btn("另存为…", self.on_save_as, ghost=True)
        bar.addSpacing(10)
        self.btn_add = btn("＋ 添加步骤", self.on_add_step,
                           tip="Ctrl+K 同款三库搜索浮层，选中命令条目即加入操作单")
        self.btn_up = btn("↑", lambda: self.on_move(-1), ghost=True, tip="上移该步骤")
        self.btn_down = btn("↓", lambda: self.on_move(1), ghost=True, tip="下移该步骤")
        self.btn_del = btn("删除步骤", self.on_delete_step, ghost=True)
        bar.addSpacing(10)
        self.btn_export = btn("导出 HTML…", self.on_export,
                              tip="单文件 A4 打印友好 HTML（含勾选框与回退汇总页）")
        bar.addStretch(1)
        self.lbl_file = QLabel("未保存")
        self.lbl_file.setObjectName("Hint")
        bar.addWidget(self.lbl_file)
        root.addLayout(bar)

        # ---- 头部元信息（QFormLayout 紧凑三列两行半）----
        meta_box = QGroupBox("变更单头")
        form = QFormLayout(meta_box)
        form.setSpacing(4)
        form.setContentsMargins(8, 6, 8, 6)
        self.ed_meta = {}
        for key, label, placeholder in (
                ("title", "操作单标题", "如：XX局点 VLAN 100 划分变更"),
                ("device_model", "设备型号", "如：S5720-52C-EI"),
                ("vendor", "厂商 slug", "如：huawei（可与步骤条目自动带出）"),
                ("os_family", "OS slug", "如：vrp8"),
                ("window_start", "维护窗口起", "如：2026-10-20 00:00"),
                ("window_end", "维护窗口止", "如：2026-10-20 06:00"),
                ("operator", "执行人", ""),
                ("reviewer", "复核人", ""),
                ("rollback_point", "回退点", "配置备份位置 / 检查点 / 快照描述"),
        ):
            ed = QLineEdit()
            ed.setPlaceholderText(placeholder)
            ed.textChanged.connect(self._on_meta_changed)
            self.ed_meta[key] = ed
        # 两列排布省纵向空间（QFormLayout 单列 9 行太高）
        col_l, col_r = QFormLayout(), QFormLayout()
        col_l.setSpacing(4)
        col_r.setSpacing(4)
        pairs_l = [("title", "标题"), ("device_model", "设备型号"), ("vendor", "厂商"),
                   ("os_family", "OS"), ("window_start", "窗口起"), ("window_end", "窗口止")]
        pairs_r = [("operator", "执行人"), ("reviewer", "复核人"), ("rollback_point", "回退点")]
        grid = QHBoxLayout()
        for k, label in pairs_l:
            col_l.addRow(label, self.ed_meta[k])
        for k, label in pairs_r:
            col_r.addRow(label, self.ed_meta[k])
        grid.addLayout(col_l, 1)
        grid.addLayout(col_r, 1)
        form.addRow(grid)
        root.addWidget(meta_box)

        # ---- 主体：左步骤列表 | 右步骤编辑（QSplitter 1 层）----
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # 左：步骤列表
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(4)
        self.lst_steps = QListWidget()
        self.lst_steps.currentRowChanged.connect(self._on_row_changed)
        lv.addWidget(self.lst_steps, 1)
        self.lbl_count = QLabel("0 步")
        self.lbl_count.setObjectName("Hint")
        lv.addWidget(self.lbl_count)
        split.addWidget(left)

        # 右：步骤详情
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(6)

        self.lbl_step_head = QLabel("")
        self.lbl_step_head.setTextFormat(Qt.RichText)
        self.lbl_step_head.setWordWrap(True)
        rv.addWidget(self.lbl_step_head)

        self.lbl_rbmiss = QLabel("⚠⚠ 变更类步骤缺少回退方案（rollback 为空）——正常不应出现，"
                                 "请先在命令库补齐回退方案。")
        self.lbl_rbmiss.setWordWrap(True)
        self.lbl_rbmiss.setStyleSheet("color:%s; font-weight:bold;" % DANGER)
        self.lbl_rbmiss.setVisible(False)
        rv.addWidget(self.lbl_rbmiss)

        self.chk_done = QCheckBox("本步已完成（勾选进度随操作单保存）")
        self.chk_done.toggled.connect(self._on_done_toggled)
        rv.addWidget(self.chk_done)

        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QScrollArea.NoFrame)
        self.form_holder = QWidget()
        self.form_lay = QVBoxLayout(self.form_holder)
        self.form_lay.setContentsMargins(0, 0, 0, 0)
        self.form_scroll.setWidget(self.form_holder)
        self.form_scroll.setMinimumHeight(120)
        rv.addWidget(self.form_scroll, 2)

        rv.addWidget(QLabel("预期回显要点（本步执行成功应看到什么）:"))
        self.ed_expect = QPlainTextEdit()
        self.ed_expect.setPlaceholderText("如：display vlan 100 中可见 VLAN100，状态 active…")
        self.ed_expect.setMaximumHeight(72)
        self.ed_expect.textChanged.connect(self._on_expect_changed)
        rv.addWidget(self.ed_expect)

        row_note = QHBoxLayout()
        row_note.addWidget(QLabel("备注:"))
        self.ed_note = QLineEdit()
        self.ed_note.textChanged.connect(self._on_note_changed)
        row_note.addWidget(self.ed_note, 1)
        rv.addLayout(row_note)

        rv.addWidget(QLabel("命令预览（参数已填入；变更类附回退方案）:"))
        self.txt_preview = QPlainTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_preview.setFont(mono_font(10))
        self.txt_preview.setObjectName("CodeBlock")
        try:
            from ui_main import CommandHighlighter
            self._hl = CommandHighlighter(self.txt_preview.document())
        except Exception:
            self._hl = None
        rv.addWidget(self.txt_preview, 2)

        row_copy = QHBoxLayout()
        self.btn_copy_step = QPushButton("复制该步命令")
        self.btn_copy_step.clicked.connect(self.on_copy_step)
        row_copy.addWidget(self.btn_copy_step)
        self.btn_copy_rb = QPushButton("复制该步回退")
        self.btn_copy_rb.clicked.connect(self.on_copy_rollback)
        row_copy.addWidget(self.btn_copy_rb)
        row_copy.addStretch(1)
        self.lbl_step_state = QLabel("")
        self.lbl_step_state.setObjectName("Hint")
        self.lbl_step_state.setWordWrap(True)
        row_copy.addWidget(self.lbl_step_state, 1)
        rv.addLayout(row_copy)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 980])
        self.split = split
        root.addWidget(split, 1)

    # ==================================================================
    # 分栏状态记忆（MainWindow 统一收口；pending 模式同 ui_troubleshoot）
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
        super(RunbookTab, self).showEvent(event)
        self._apply_pending_split()

    # ==================================================================
    # 状态栏 / 脏标记
    # ==================================================================
    def _status(self, text):
        win = self.main if self.main is not None else self.window()
        try:
            win.statusBar().showMessage(text)
        except Exception:
            pass

    def _mark_dirty(self):
        self.dirty = True
        self._sync_toolbar()

    def _sync_toolbar(self):
        name = os.path.basename(self.current_path) if self.current_path else "未保存"
        star = " *" if self.dirty else ""
        self.lbl_file.setText("%s%s" % (name, star))
        has_step = 0 <= self._cur < len(self.data["steps"])
        self.btn_up.setEnabled(has_step and self._cur > 0)
        self.btn_down.setEnabled(has_step and self._cur < len(self.data["steps"]) - 1)
        self.btn_del.setEnabled(has_step)
        self.btn_save.setEnabled(True)
        self.btn_export.setEnabled(bool(self.data["steps"]))

    def _confirm_discard(self):
        """脏单确认：新建/打开前问一句；返回 True = 可以继续（已保存或用户放弃）"""
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("操作单未保存")
        box.setText("当前操作单有未保存的修改，继续将丢失这些修改。\n\n先保存？")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Save)
        ret = box.exec_()
        if ret == QMessageBox.Save:
            return self.on_save()
        return ret == QMessageBox.Discard

    # ==================================================================
    # 文件：新建 / 打开 / 保存 / 另存为
    # ==================================================================
    def on_new(self):
        if not self._confirm_discard():
            return
        self.data = runbook_export.new_runbook()
        self.current_path = None
        self.dirty = False
        self._cur = -1            # 置空再重建：防 _on_row_changed 用旧编辑区回写新数据
        self._load_meta()
        self._reload_steps(keep=-1)
        self._show_step(-1)
        self._sync_toolbar()
        self._status("已新建空白操作单。")

    def on_open(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开操作单", _runbooks_dir(), FILE_FILTER)
        if not path:
            return
        data, err = runbook_export.load_runbook(path)
        if data is None:
            QMessageBox.warning(self, "打开失败", err)
            return
        self.data = data
        self.current_path = path
        self.dirty = False
        self._cur = -1            # 同 on_new：防旧编辑区内容串写进新单
        self._load_meta()
        self._reload_steps(keep=0 if self.data["steps"] else -1)
        self._show_step(0 if self.data["steps"] else -1)
        self._sync_toolbar()
        self._status("已打开 %s（%d 步）%s" % (os.path.basename(path),
                                              len(self.data["steps"]),
                                              ("；" + err) if err else ""))

    def on_save(self):
        if self.current_path:
            return self._save_to(self.current_path)
        return self.on_save_as()

    def on_save_as(self):
        default = os.path.join(_runbooks_dir(),
                               "%s.json" % _safe_filename(self.data["meta"].get("title")))
        path, _ = QFileDialog.getSaveFileName(self, "保存操作单", default, FILE_FILTER)
        if not path:
            return False
        return self._save_to(path)

    def _save_to(self, path):
        self._flush_current_step()
        self.data["meta"]["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.data["meta"].get("created_at"):
            self.data["meta"]["created_at"] = self.data["meta"]["updated_at"]
        err = runbook_export.save_runbook(self.data, path)
        if err:
            QMessageBox.warning(self, "保存失败", err)
            return False
        self.current_path = path
        self.dirty = False
        self._sync_toolbar()
        self._status("已保存：%s" % path)
        return True

    # ==================================================================
    # 元信息
    # ==================================================================
    def _load_meta(self):
        self._loading = True
        try:
            for key, ed in self.ed_meta.items():
                ed.setText(str(self.data["meta"].get(key) or ""))
        finally:
            self._loading = False

    def _on_meta_changed(self, *_a):
        if self._loading:
            return
        for key, ed in self.ed_meta.items():
            self.data["meta"][key] = ed.text().strip()
        self._mark_dirty()

    # ==================================================================
    # 步骤列表
    # ==================================================================
    def _reload_steps(self, keep=-1):
        """从 self.data 重建左侧列表（keep = 选中下标，越界取 -1）"""
        self.lst_steps.blockSignals(True)
        self.lst_steps.clear()
        for i, step in enumerate(self.data["steps"]):
            entry = self._entry_of(step)
            if entry is None:
                tag = "条目失效"
            else:
                try:
                    tag = "变更类" if renderer.classify_entry(entry) == "change" else "查询类"
                except Exception:
                    tag = "变更类"
            done = "✔ " if step.get("checked") else ""
            item = QListWidgetItem("%d. %s%s  ［%s］" % (i + 1, done,
                                                        step.get("title_snapshot") or "(无标题)",
                                                        tag))
            if entry is None or tag == "变更类":
                item.setForeground(QColor(DANGER if entry is None else WARNING))
            self.lst_steps.addItem(item)
        self.lst_steps.blockSignals(False)
        n = len(self.data["steps"])
        self.lbl_count.setText("%d 步（变更类自动附回退方案）" % n)
        if 0 <= keep < n:
            self.lst_steps.setCurrentRow(keep)
        else:
            self.lst_steps.setCurrentRow(-1)

    def _entry_of(self, step):
        try:
            return self.db.get_entry(step.get("entry_uuid")) if self.db else None
        except Exception:
            return None

    def on_add_step(self):
        from ui_panel import CommandPalette
        recent = getattr(self.main, "_recent", None) if self.main else None
        dlg = CommandPalette(self.db, recent=recent, parent=self)
        dlg.exec_()
        choice = dlg.choice
        if not choice:
            return
        if choice.get("kind") != "entry":
            self._status("操作单步骤仅支持命令库条目（报错/排查树请直接走对应 Tab）。")
            return
        entry = self._entry_of({"entry_uuid": choice.get("id")})
        if not entry:
            self._status("条目读取失败，未加入步骤。")
            return
        step = {
            "step_no": len(self.data["steps"]) + 1,
            "entry_uuid": entry.get("uuid") or "",
            "title_snapshot": entry.get("title") or "(无标题)",
            "vendor_snapshot": entry.get("vendor") or "",
            "params": {},
            "expect": "",
            "note": "",
            "checked": False,
        }
        self.data["steps"].append(step)
        # 首条带出厂商/OS 到单头（留空才填，不覆盖用户手填）
        if not self.data["meta"].get("vendor"):
            self.ed_meta["vendor"].setText(entry.get("vendor") or "")
        if not self.data["meta"].get("os_family"):
            self.ed_meta["os_family"].setText(entry.get("os_family") or "")
        self._mark_dirty()
        self._reload_steps(keep=len(self.data["steps"]) - 1)
        self._status("已添加步骤：%s" % step["title_snapshot"])

    def on_delete_step(self):
        if not (0 <= self._cur < len(self.data["steps"])):
            return
        title = self.data["steps"][self._cur].get("title_snapshot") or "(无标题)"
        if QMessageBox.question(self, "删除步骤", "删除第 %d 步「%s」？" % (self._cur + 1, title),
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        keep = min(self._cur, len(self.data["steps"]) - 2)  # 删完后选中同位置步骤（越界为-1）
        del self.data["steps"][self._cur]
        self._renumber()
        self._cur = -1            # 置空再重建：防旧编辑区内容串写进移位后的步骤
        self._mark_dirty()
        self._reload_steps(keep=keep)

    def on_move(self, delta):
        i = self._cur
        j = i + delta
        if not (0 <= i < len(self.data["steps"])) or not (0 <= j < len(self.data["steps"])):
            return
        steps = self.data["steps"]
        steps[i], steps[j] = steps[j], steps[i]
        self._renumber()
        self._mark_dirty()
        self._reload_steps(keep=j)

    def _renumber(self):
        for i, step in enumerate(self.data["steps"]):
            step["step_no"] = i + 1

    # ==================================================================
    # 步骤编辑区
    # ==================================================================
    def _on_row_changed(self, row):
        if self._loading:
            return
        self._flush_current_step()
        self._show_step(row)

    def _show_step(self, index):
        """切换到指定步骤（-1 = 空态）。切换前须已 flush"""
        self._loading = True
        try:
            self._cur = index if isinstance(index, int) else -1
            has = 0 <= self._cur < len(self.data["steps"])
            self.chk_done.setVisible(has)
            self.btn_copy_step.setVisible(has)
            self.btn_copy_rb.setVisible(has)
            if not has:
                self._cur = -1
                self.lbl_step_head.setText("<b>未选中步骤</b> —— 点上方「＋ 添加步骤」"
                                           "从命令库挑条目加入操作单。")
                self.lbl_rbmiss.setVisible(False)
                self.lbl_step_state.setText("")
                self._clear_param_form()
                self.ed_expect.setPlainText("")
                self.ed_note.setText("")
                self.txt_preview.setPlainText("")
                self._sync_toolbar()
                return
            step = self.data["steps"][self._cur]
            entry = self._entry_of(step)
            self.chk_done.setChecked(bool(step.get("checked")))
            self.ed_expect.setPlainText(str(step.get("expect") or ""))
            self.ed_note.setText(str(step.get("note") or ""))
            if entry is None:
                self.lbl_step_head.setText(
                    "<b>第 %d 步 · %s</b> <span style='color:%s'>［条目失效］</span>"
                    % (self._cur + 1, self._esc(step.get("title_snapshot")), TEXT_MUTED))
                self.lbl_rbmiss.setVisible(False)
                self._rbmiss_active = False
                self._clear_param_form()
                self.txt_preview.setPlainText(
                    "⚠ 条目已不在当前命令库中（uuid=%s）。\n"
                    "命令内容以快照标题为准，请人工核对，或删除该步骤重新添加。"
                    % step.get("entry_uuid"))
                self.lbl_step_state.setText("")
            else:
                try:
                    cat = renderer.classify_entry(entry)
                except Exception:
                    cat = "change"
                cat_html = ("<span style='color:%s;font-weight:bold'>［变更类］</span>" % DANGER
                            if cat == "change"
                            else "<span style='color:%s'>［查询类］</span>" % ACCENT)
                self.lbl_step_head.setText(
                    "<b>第 %d 步 · %s</b> %s<br><span style='color:%s'>%s · %s · %s</span>"
                    % (self._cur + 1, self._esc(entry.get("title") or "(无标题)"), cat_html,
                       TEXT_MUTED,
                       dbmod.display_vendor(entry.get("vendor")),
                       entry.get("category") or "-",
                       dbmod.display_os(entry.get("os_family"))))
                # offscreen/未显示窗口 isVisible() 恒 False，冒烟用旗标断言意图
                self._rbmiss_active = (cat == "change"
                                       and not str(entry.get("rollback") or "").strip())
                self.lbl_rbmiss.setVisible(self._rbmiss_active)
                self._build_param_form(entry, step)
            self._update_preview()
            self._sync_toolbar()
        finally:
            self._loading = False

    @staticmethod
    def _esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _clear_param_form(self):
        while self.form_lay.count():
            item = self.form_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_param_form(self, entry, step):
        """按条目重建参数表单（ParamFormWidget 全量复用：必填/类型/端口展开）"""
        self._clear_param_form()
        commands = entry.get("commands") or ""
        specs = renderer.merge_param_specs(self.db.load_params(entry), commands)
        self._form = ParamFormWidget(specs, vendor=entry.get("vendor") or "",
                                     os_family=entry.get("os_family") or "")
        self._form.set_values({k: str(v) for k, v in (step.get("params") or {}).items()})
        self._form.changed.connect(self._on_params_changed)
        self.form_lay.addWidget(self._form)
        if not specs:
            tip = QLabel("该条目无 {{参数}} 占位，无需填参。")
            tip.setObjectName("Hint")
            self.form_lay.addWidget(tip)

    def _on_params_changed(self, values):
        """表单任意参数变化 → 回写当前步骤并刷新预览"""
        if self._loading or not (0 <= self._cur < len(self.data["steps"])):
            return
        self.data["steps"][self._cur]["params"] = dict(values or {})
        self._mark_dirty()
        self._update_preview()

    def _on_expect_changed(self):
        if self._loading or not (0 <= self._cur < len(self.data["steps"])):
            return
        self.data["steps"][self._cur]["expect"] = self.ed_expect.toPlainText()
        self._mark_dirty()

    def _on_note_changed(self, *_a):
        if self._loading or not (0 <= self._cur < len(self.data["steps"])):
            return
        self.data["steps"][self._cur]["note"] = self.ed_note.text()
        self._mark_dirty()

    def _on_done_toggled(self, on):
        if self._loading or not (0 <= self._cur < len(self.data["steps"])):
            return
        self.data["steps"][self._cur]["checked"] = bool(on)
        self._mark_dirty()
        # 列表里的 ✔ 同步（blockSignals 防止 textChanged 链路误触发选中变化）
        item = self.lst_steps.item(self._cur)
        if item is not None:
            text = item.text()
            if on and not text.startswith("✔ "):
                item.setText("✔ " + text)
            elif not on and text.startswith("✔ "):
                item.setText(text[2:])

    def _flush_current_step(self):
        """切换/保存前把编辑区落回数据（参数由 changed 信号实时回写，此处兜底 expect/note）"""
        if self._loading or not (0 <= self._cur < len(self.data["steps"])):
            return
        step = self.data["steps"][self._cur]
        if hasattr(self, "_form") and self._form is not None:
            try:
                step["params"] = dict(self._form.values())
            except Exception:
                pass
        step["expect"] = self.ed_expect.toPlainText()
        step["note"] = self.ed_note.text()
        step["checked"] = bool(self.chk_done.isChecked())

    # ==================================================================
    # 预览与复制
    # ==================================================================
    def _resolved_current(self):
        """当前步骤的 (entry, category, rendered, missing_params, rollback, form_errors)"""
        if not (0 <= self._cur < len(self.data["steps"])):
            return None
        step = self.data["steps"][self._cur]
        entry = self._entry_of(step)
        if entry is None:
            return {"step": step, "entry": None, "category": None, "rendered": "",
                    "missing_params": [], "rollback": "", "errors": {}}
        try:
            cat = renderer.classify_entry(entry)
        except Exception:
            cat = "change"
        text, missing, specs = renderer.render_entry(entry, step.get("params") or {})
        errors = {}
        form = getattr(self, "_form", None)
        if form is not None and self._cur == self.lst_steps.currentRow():
            try:
                errors = form.errors()
            except Exception:
                errors = {}
        if not errors:
            # 表单未构建/失焦时兜底走 renderer.validate_form（同一执法点）
            try:
                errors = renderer.validate_form(specs, step.get("params") or {})
            except Exception:
                errors = {}
        rollback = str(entry.get("rollback") or "") if cat == "change" else ""
        return {"step": step, "entry": entry, "category": cat, "rendered": text,
                "missing_params": missing, "rollback": rollback, "errors": errors}

    def _update_preview(self):
        r = self._resolved_current()
        if r is None or r["entry"] is None:
            return
        parts = [r["rendered"] or "（无命令）"]
        if r["category"] == "change":
            parts.append("―――― 回退方案（rollback）――――")
            parts.append(r["rollback"].strip() or "⚠⚠ 缺少回退方案（rollback 为空）")
        if r["missing_params"]:
            parts.append("（未填参数：%s）" % ", ".join(r["missing_params"]))
        self.txt_preview.setPlainText("\n".join(parts))
        if r["errors"]:
            self.lbl_step_state.setText("✘ 参数未填好：%s（复制前须先补齐）"
                                        % "、".join(sorted(r["errors"])))
        else:
            self.lbl_step_state.setText("")

    def _guard_required(self, r):
        """复制前必填校验（与主程序 _guard_missing_required 同口径：有错拦截 + 定位）"""
        if r["errors"]:
            self._status("参数未填好：%s" % "、".join(
                "%s（%s）" % (k, v) for k, v in sorted(r["errors"].items())))
            form = getattr(self, "_form", None)
            if form is not None:
                try:
                    form.focus_first_error()
                except Exception:
                    pass
            return False
        return True

    def on_copy_step(self):
        r = self._resolved_current()
        if r is None or r["entry"] is None:
            self._status("先选中一个有效步骤。")
            return
        if not self._guard_required(r):
            return
        from ui_main import copy_to_clipboard, guard_skeleton_copy, guard_rollback_confirm
        parent = self.main if self.main is not None else self
        guard_skeleton_copy(parent, r["entry"])
        if not guard_rollback_confirm(parent, r["entry"]):
            self._status("已取消复制（变更类未确认）。")
            return
        if not copy_to_clipboard(r["rendered"]):
            self._status("复制失败（剪贴板被占用），请手动选中预览区复制。")
            return
        n = renderer.count_effective_lines(r["rendered"])
        self._status("已复制第 %d 步命令（%d 条有效命令）→ 切到 Xshell 直接粘贴"
                     % (self._cur + 1, n))

    def on_copy_rollback(self):
        r = self._resolved_current()
        if r is None or r["entry"] is None:
            self._status("先选中一个有效步骤。")
            return
        if not r["rollback"].strip():
            self._status("该步骤不是变更类或没有回退方案。")
            return
        if not self._guard_required(r):
            return
        from ui_main import copy_to_clipboard, guard_rollback_confirm
        parent = self.main if self.main is not None else self
        if not guard_rollback_confirm(parent, r["entry"]):
            self._status("已取消复制（变更类未确认）。")
            return
        if not copy_to_clipboard(r["rollback"]):
            self._status("复制失败（剪贴板被占用）。")
            return
        self._status("已复制第 %d 步回退方案。" % (self._cur + 1))

    # ==================================================================
    # 导出 HTML
    # ==================================================================
    def on_export(self):
        if not self.data["steps"]:
            self._status("操作单还没有步骤，先添加。")
            return
        self._flush_current_step()
        resolved = runbook_export.resolve_steps(self.data, self.db)
        bad = [r for r in resolved if r["errors"]]
        dead = [r for r in resolved if r["status"] == "missing"]
        rbmiss = [r for r in resolved if r["missing_rollback"]]
        if bad:
            names = "、".join("第 %d 步" % r["index"] for r in bad[:6])
            if QMessageBox.question(
                    self, "存在未填参数",
                    "%s 等步骤的必填参数未填，导出 HTML 中命令将保留 {{占位}}。\n\n仍要导出？"
                    % names, QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        warn = []
        if dead:
            warn.append("有 %d 步条目已失效（HTML 内标警告）" % len(dead))
        if rbmiss:
            warn.append("有 %d 步变更类缺回退方案（HTML 内标红）" % len(rbmiss))
        if warn and QMessageBox.question(
                self, "导出前提示", "%s。\n\n仍要导出？" % "；".join(warn),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        default = os.path.join(_runbooks_dir(),
                               "%s.html" % _safe_filename(self.data["meta"].get("title")))
        path, _ = QFileDialog.getSaveFileName(self, "导出操作单 HTML", default, HTML_FILTER)
        if not path:
            return
        rc, msg = runbook_export.export_runbook(self.data, self.db, path)
        if rc == 0:
            self._status("已导出：%s" % msg)
            QMessageBox.information(self, "导出成功",
                                    "已导出单文件 HTML（零外部请求，双击即开，浏览器打印即为 A4 版式）：\n%s"
                                    % msg)
        else:
            QMessageBox.critical(self, "导出失败", msg)
