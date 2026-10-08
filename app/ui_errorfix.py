# -*- coding: utf-8 -*-
"""
ui_errorfix.py —— 报错诊断 Tab（模块 05）

流程：粘贴设备报错 → 本地正则匹配（error_match.py）→ 结果卡片
      （红色命中行 / 人话原因 / 解决步骤 / 复制修正命令 / 去排查树）
      →「解决了我的问题」标记绿徽章；零匹配自动进待解决收件箱。

硬约束：纯本地正则匹配，绝不联网；零匹配不报错（进收件箱）。
"""

from PyQt5.QtCore import Qt, QTimer      # QTimer：复制按钮闪烁反馈的还原定时器
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QPushButton, QPlainTextEdit, QScrollArea, QFrame,
                             QMessageBox, QDialog, QFormLayout, QLineEdit, QTextEdit,
                             QDialogButtonBox, QGroupBox, QTreeWidget, QTreeWidgetItem,
                             QAbstractItemView,
                             QTableWidget, QTableWidgetItem, QHeaderView)

import db as dbmod
import error_match
import renderer
from ui_main import VerifyDialog, copy_to_clipboard


def _mono(size=10):
    f = QFont("Consolas")
    f.setPointSize(size)
    return f


from theme import repolish, set_state, GripSplitter, read_sizes  # 状态标签 / error 属性的动态重polish
from theme import WARNING, DANGER, TEXT_SECONDARY, ERROR_LINE_BG  # C4 收敛散落色（审计 B18）

class ErrorFixTab(QWidget):

    SPLIT_KEY = "errorfix_split"        # 粘贴区│结果区分栏记忆 key（ui_state.json）
    SPLIT_MINS = (140, 180)             # 粘贴区 / 结果区最小高度
    """报错诊断 Tab"""

    def __init__(self, db, parent=None):
        super(ErrorFixTab, self).__init__(parent)
        self.db = db
        self._build()
        self.refresh_inbox_count()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ---- 上半：粘贴 + 诊断 ----
        top = QGroupBox("第一步：粘贴设备报错原文")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(10, 10, 10, 8)
        self.txt_paste = QPlainTextEdit()
        self.txt_paste.setFont(_mono(10))
        self.txt_paste.setPlaceholderText(
            "把终端里的报错原文（可多行、可带提示符）粘贴到这里，例如：\n"
            "  % Invalid input detected at '^' marker.\n"
            "  Error: Unrecognized command found at '^' position.\n"
            "  bash: tcpdump: command not found")
        self.txt_paste.setMinimumHeight(90)
        tv.addWidget(self.txt_paste)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_diagnose = QPushButton("开始诊断")
        self.btn_diagnose.clicked.connect(self.on_diagnose)
        row.addWidget(self.btn_diagnose)
        row.addWidget(QLabel("厂商（自动识别，可手改）："))
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.setMinimumWidth(140)
        self.cmb_vendor.addItem("自动识别", "")
        for slug in sorted(dbmod.VENDOR_SLUGS.keys()):
            self.cmb_vendor.addItem(dbmod.display_vendor(slug), slug)
        row.addWidget(self.cmb_vendor)
        self.lbl_vendor = QLabel("")
        self.lbl_vendor.setObjectName("HintText")
        row.addWidget(self.lbl_vendor, 1)
        self.btn_inbox = QPushButton("待解决收件箱")
        self.btn_inbox.setObjectName("Ghost")
        self.btn_inbox.clicked.connect(self.show_inbox)
        row.addWidget(self.btn_inbox)
        # ★ 字典维护入口（第 3 轮 缺口A）：此前字典只能"新增"（收件箱转化），
        #   转错了没法改，只能删库重录；这里补上编辑/删除/测试。
        self.btn_dict = QPushButton("字典维护")
        self.btn_dict.setObjectName("Ghost")
        self.btn_dict.setToolTip("浏览/新增/编辑/删除报错字典条目（编辑后写入 history）")
        self.btn_dict.clicked.connect(self.show_dict_manager)
        row.addWidget(self.btn_dict)
        tv.addLayout(row)
        top.setMinimumHeight(self.SPLIT_MINS[0])

        # ---- 下半：结果卡片（滚动区）----
        bottom_host = QWidget()
        bv = QVBoxLayout(bottom_host)
        bv.setContentsMargins(0, 6, 0, 0)
        bv.setSpacing(6)
        bv.addWidget(QLabel("第二步：诊断结果（命中的报错原文以红色标注）"))
        self.results_area = QScrollArea()
        self.results_area.setWidgetResizable(True)
        self.results_host = None
        self.results_layout = None
        self._reset_results()
        bv.addWidget(self.results_area, 1)
        self.show_empty_results("尚无诊断结果。粘贴报错后点「开始诊断」；"
                                "字典里没有的报错会自动存入待解决收件箱。")
        bottom_host.setMinimumHeight(self.SPLIT_MINS[1])

        # ---- 纵向 GripSplitter：粘贴区 │ 结果区（可拖 + 状态记忆，任务2-2）----
        self.split = GripSplitter(Qt.Vertical)
        self.split.addWidget(top)
        self.split.addWidget(bottom_host)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([200, 460])
        root.addWidget(self.split, 1)

    # ---- 分栏状态记忆（MainWindow 统一收口）----
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
        super(ErrorFixTab, self).showEvent(event)
        self._apply_pending_split()

    def refresh_inbox_count(self):
        """收件箱按钮显示 pending 数量"""
        n = len(self.db.list_unresolved(status="pending")) if not self.db.closed else 0
        self.btn_inbox.setText("待解决收件箱（%d）" % n)

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    def on_diagnose(self):
        text = self.txt_paste.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "先粘贴报错", "请先把设备报错原文粘贴到上面的输入框。")
            return

        vendor_manual = self.cmb_vendor.currentData()
        v_auto, hint = error_match.guess_vendor(text)
        vendor = vendor_manual or v_auto
        self.lbl_vendor.setText(
            "识别厂商：%s ｜ %s" % (dbmod.display_vendor(vendor) if vendor else "未识别", hint)
            if vendor else "未识别出厂商（已按全部字典匹配），可手动指定后重试")

        dicts = self.db.all_errs()
        hits = error_match.match_errors(text, dicts, vendor=vendor or None)

        self._reset_results()

        if not hits:
            if not self.db.readonly:
                self.db.add_unresolved(text, vendor or "")
                self.refresh_inbox_count()
            empty = QLabel("✘ 字典里没有匹配到该报错。\n\n"
                           "已自动存入「待解决收件箱」——可从收件箱把它转成新的字典条目，"
                           "帮助团队积累。")
            empty.setObjectName("ErrorText")
            empty.setWordWrap(True)
            self.results_layout.addWidget(empty)
            self.results_layout.addStretch(1)
            return

        for d, matched_line in hits:
            self.db.increment_err_hit(d["err_id"])
            self.results_layout.addWidget(self._build_card(d, matched_line, text))
        self.results_layout.addStretch(1)

    def _reset_results(self):
        """
        清空结果区：整个宿主控件重建。
        ★ 不要用"逐个 deleteLater + 保留 layout"的方式——动态滚动区里
          deleteLater 的旧卡片与 insertWidget 的新卡片混用会触发 Qt 崩溃。
        """
        self.results_area.setWidget(None)
        if getattr(self, "results_host", None) is not None:
            self.results_host.deleteLater()
        self.results_host = QWidget()
        self.results_layout = QVBoxLayout(self.results_host)
        self.results_layout.setContentsMargins(4, 4, 4, 4)
        self.results_layout.setSpacing(8)
        self.results_area.setWidget(self.results_host)

    def show_empty_results(self, message):
        """空态提示"""
        self._reset_results()
        empty = QLabel(message)
        empty.setWordWrap(True)
        empty.setObjectName("Hint")
        self.results_layout.addWidget(empty)
        self.results_layout.addStretch(1)

    # ------------------------------------------------------------------
    # 结果卡片
    # ------------------------------------------------------------------
    def _build_card(self, d, matched_line, full_text):
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)

        # 标题行：分类 + 厂商 + 验证徽章
        verified = int(d.get("verified") or 0) == 1
        head = QLabel("<span style='color:%s; font-weight:bold;'>[%s]</span> "
                      "<span style='color:%s;'>%s ｜ 命中 %d 次 ｜ %s</span>"
                      % (WARNING,
                         d.get("category") or "-",
                         TEXT_SECONDARY,
                         dbmod.display_vendor(d.get("vendor")),
                         int(d.get("hit_count") or 0) + 1,
                         "✔ 已验证" if verified else "未验证"))
        head.setTextFormat(Qt.RichText)
        cv.addWidget(head)

        # AI 溯源（🤖）：报错映射来自 AI 会话入库时可点击跳转
        try:
            from ui_ai import make_ai_source_label, open_ai_source
            _src_pair = make_ai_source_label(self.db, d.get("err_id"))
        except Exception:
            _src_pair = None
        if _src_pair:
            _src_lbl, _src_rec = _src_pair
            _src_lbl.linkActivated.connect(lambda *_: open_ai_source(self.window(), _src_rec))
            cv.addWidget(_src_lbl)

        # 红色高亮命中原文
        line_lbl = QLabel("<span style='background-color:%s; color:%s; "
                          "font-family:Consolas;'>%s</span>"
                          % (ERROR_LINE_BG, DANGER,
                             self._escape(matched_line)))
        line_lbl.setTextFormat(Qt.RichText)
        line_lbl.setWordWrap(True)
        cv.addWidget(line_lbl)

        # 原因（人话）
        cause = QLabel("<span style='color:#e0a83c; font-weight:bold;'>原因：</span>%s"
                       % self._escape(d.get("cause") or "-"))
        cause.setTextFormat(Qt.RichText)
        cause.setWordWrap(True)
        cv.addWidget(cause)

        # 骨架报错提示（批2B M1：err_dict.exec_level=skeleton = 构造样例待真机核对）
        if str(d.get("exec_level") or "").strip() == "skeleton":
            sk = QLabel("<span style='color:#e0a83c; font-weight:bold;'>"
                        "⚠ 骨架——待真机核对：</span>本条报错样例为构造值，"
                        "实际命中后请以设备真实输出修正匹配规则。")
            sk.setTextFormat(Qt.RichText)
            sk.setWordWrap(True)
            cv.addWidget(sk)

        # 解决步骤
        cv.addWidget(QLabel("解决步骤："))
        for i, step in enumerate(d.get("solution_steps") or [], 1):
            row = QHBoxLayout()
            row.setSpacing(6)
            text = "%d. %s" % (i, step.get("text") or "")
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setIndent(12)
            row.addWidget(lbl, 1)

            ref_uuid = step.get("ref_entry_uuid")
            if ref_uuid:
                entry = self.db.get_entry(ref_uuid)
                if entry:
                    btn = QPushButton("复制修正命令")
                    btn.setObjectName("Ghost")
                    btn.setToolTip("渲染命令库条目「%s」并复制" % entry.get("title", ""))
                    btn.clicked.connect(lambda _=False, en=entry, b=btn:
                                        self._copy_entry(en, b))
                    row.addWidget(btn)
            if step.get("fix_template"):
                btn2 = QPushButton("复制修正模板")
                btn2.setObjectName("Ghost")
                btn2.clicked.connect(lambda _=False, tpl=step["fix_template"], b=btn2:
                                     self._copy_text(tpl, b))
                row.addWidget(btn2)
            cv.addLayout(row)

        # 动作按钮行
        act = QHBoxLayout()
        act.setSpacing(6)
        btn_ok = QPushButton("这条方案解决了我的问题")
        btn_ok.clicked.connect(lambda _=False, dd=dict(d): self._mark_verified(dd))
        act.addWidget(btn_ok)
        btn_bad = QPushButton("对结果不满意（存入待解决）")
        btn_bad.setObjectName("Ghost")
        btn_bad.clicked.connect(lambda _=False, dd=dict(d): self._mark_unsatisfied(dd, full_text))
        act.addWidget(btn_bad)
        # [问 AI]：报错原文 + 识别出的厂商带入 AI 诊断（任务4-3）
        btn_ai = QPushButton("问 AI")
        btn_ai.setObjectName("Ghost")
        btn_ai.setToolTip("把报错原文与识别出的厂商/OS 带入 AI 诊断 Tab，让 AI 给第二意见")
        btn_ai.clicked.connect(lambda _=False, dd=dict(d), ft=full_text: self._ask_ai(dd, ft))
        act.addWidget(btn_ai)
        act.addStretch(1)
        cv.addLayout(act)
        return card

    def _ask_ai(self, d, full_text):
        """结果卡片 [问 AI]：现象=报错原因，回显=粘贴的报错原文，步骤=本地已给方案"""
        win = self.window()
        if not hasattr(win, "goto_ai_tab"):
            return
        local_plan = "\n".join("%d. %s" % (i, s.get("text") or "")
                               for i, s in enumerate(d.get("solution_steps") or [], 1))
        win.goto_ai_tab(
            vendor=dbmod.display_vendor(d.get("vendor") or ""),
            os_name=dbmod.display_os(d.get("os_family") or ""),
            symptom=(d.get("cause") or "").strip(),
            echo=full_text,
            steps=("本地报错字典已给出以下方案但未解决，请给进一步排查思路：\n%s" % local_plan)
                  if local_plan else "",
        )
        self._flash_feedback("已带入 AI 诊断 Tab。")

    @staticmethod
    def _escape(text):
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _copy_entry(self, entry, button=None):
        """修正命令：渲染命令库条目后进剪贴板（与命令库同源，改库后这里跟着变）"""
        text, missing, _specs = renderer.render_entry(entry, {})
        if not copy_to_clipboard(text):
            QMessageBox.warning(self, "复制失败",
                                "写入剪贴板失败（可能被其它程序占用），请手动选中后复制。")
            return
        self._flash_feedback("已复制修正命令（%s）" % (entry.get("title") or ""), button)

    def _copy_text(self, text, button=None):
        if not copy_to_clipboard(text):
            QMessageBox.warning(self, "复制失败",
                                "写入剪贴板失败（可能被其它程序占用），请手动选中后复制。")
            return
        self._flash_feedback("已复制修正模板", button)

    def _flash_feedback(self, tip, button=None):
        """
        复制成功的**非阻塞**反馈：按钮文字短暂变「✓ 已复制」+ 主窗状态栏提示。

        ★ 为什么不用模态提示框：复制命令是「复制 → 切到 Xshell 粘贴」的高频动作，
          每点一次都要求用户手动关掉一个对话框，既打断操作节奏，也多跑一层嵌套
          事件循环；离网现场以稳定与轻量为先，能不做的事就不做。
        ★ 定时器回调必须容错：诊断结果区每次都会整体重建（见 _reset_results），
          按钮可能在 1.2 秒的闪烁期内被销毁，此时再 setText 会抛
          RuntimeError("wrapped C/C++ object ... has been deleted") 把程序打挂。
        """
        if button is not None:
            try:
                if not button.property("baseText"):
                    button.setProperty("baseText", button.text())
                base = button.property("baseText")
                button.setText("✓ 已复制")
                QTimer.singleShot(
                    1200, lambda b=button, t=base: self._restore_button_text(b, t))
            except RuntimeError:
                pass        # 控件已销毁：跳过反馈即可，复制本身已经完成
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(tip)

    @staticmethod
    def _restore_button_text(button, text):
        """还原闪烁过的按钮文字；控件已被销毁时静默跳过"""
        try:
            button.setText(text)
        except RuntimeError:
            pass

    def _mark_verified(self, d):
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法标记验证。")
            return
        fake = {"title": "报错：%s" % (d.get("cause") or d.get("err_id"))[:40],
                "verified_by": d.get("verified_by") or "",
                "verified_model": "",
                "verified_date": d.get("verified_date") or "",
                "models": ""}
        dlg = VerifyDialog(fake, self)
        if dlg.exec_() == dlg.Accepted:
            by = dlg.ed_by.text().strip()
            if not by:
                QMessageBox.warning(self, "缺少验证人", "验证人必填。")
                return
            try:
                self.db.mark_err_verified(d["err_id"], by, dlg.ed_model.text().strip(),
                                          dlg.ed_date.text().strip())
            except Exception as exc:
                QMessageBox.critical(self, "标记失败", str(exc))
                return
            QMessageBox.information(self, "已标记", "字典条目已标记为已验证（绿徽章）。")
            self.on_diagnose()          # 刷新卡片徽章

    def _mark_unsatisfied(self, d, full_text):
        if not self.db.readonly:
            try:
                self.db.add_unresolved(full_text, d.get("vendor") or "")
            except Exception as exc:
                QMessageBox.critical(self, "收件箱写入失败", str(exc))
                return
            self.refresh_inbox_count()
        QMessageBox.information(self, "已记录",
                                "已存入待解决收件箱，可在收件箱里补充原因转成新字典条目。")

    # ------------------------------------------------------------------
    # 待解决收件箱
    # ------------------------------------------------------------------
    def show_inbox(self):
        dlg = InboxDialog(self.db, self)
        dlg.exec_()
        self.refresh_inbox_count()

    def show_dict_manager(self):
        """打开字典维护（浏览/新增/编辑/删除）"""
        dlg = DictManagerDialog(self.db, self)
        dlg.exec_()
        self.refresh_inbox_count()


class InboxDialog(QDialog):
    """待解决收件箱：pending → 转字典条目 → resolved"""

    def __init__(self, db, parent=None):
        super(InboxDialog, self).__init__(parent)
        self.db = db
        self.setWindowTitle("待解决收件箱")
        self.resize(760, 520)
        v = QVBoxLayout(self)

        # ★ 注意：QTreeWidget / QTreeWidgetItem / QAbstractItemView 必须在模块顶部导入。
        #   早期版本把这几个类的 import 写在了本方法内部，导致 refresh()（另一个方法）
        #   里引用 QTreeWidgetItem 时抛 NameError —— 一打开收件箱就崩（已由现场实测发现）。
        filt = QHBoxLayout()
        filt.addWidget(QLabel("筛选："))
        self.cmb_status = QComboBox()
        self.cmb_status.addItem("待处理（pending）", "pending")
        self.cmb_status.addItem("已处理（resolved）", "resolved")
        self.cmb_status.addItem("全部", None)
        self.cmb_status.currentIndexChanged.connect(self.refresh)
        filt.addWidget(self.cmb_status)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("Hint")
        filt.addWidget(self.lbl_count)
        filt.addStretch(1)
        v.addLayout(filt)

        self.lst = QTreeWidget()
        self.lst.setHeaderLabels(["时间", "猜的厂商", "报错原文（摘要）", "状态"])
        self.lst.setRootIsDecorated(False)
        self.lst.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lst.itemDoubleClicked.connect(lambda *_: self.to_dict_entry())
        v.addWidget(self.lst, 1)

        row = QHBoxLayout()
        self.btn_to_dict = QPushButton("转为字典条目…")
        self.btn_to_dict.clicked.connect(self.to_dict_entry)
        row.addWidget(self.btn_to_dict)
        self.btn_del = QPushButton("删除选中")
        self.btn_del.setObjectName("Ghost")
        self.btn_del.clicked.connect(self.delete_selected)
        row.addWidget(self.btn_del)
        self.btn_clean = QPushButton("批量清理已处理")
        self.btn_clean.setObjectName("Ghost")
        self.btn_clean.setToolTip("删除所有 status=resolved 的收件箱记录（字典条目不受影响）")
        self.btn_clean.clicked.connect(self.clean_resolved)
        row.addWidget(self.btn_clean)
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        v.addLayout(row)
        self.refresh()

    def refresh(self):
        """按状态筛选刷新收件箱"""
        status = self.cmb_status.currentData()
        rows = self.db.list_unresolved(status=status)
        self.lst.clear()
        for r in rows:
            st = r.get("status", "")
            item = QTreeWidgetItem([r.get("ts", ""), r.get("guessed_vendor") or "-",
                                    (r.get("pasted_text") or "")[:60].replace("\n", " "),
                                    "已转条目" if st == "resolved" else "待处理"])
            item.setData(0, Qt.UserRole, r.get("id"))
            if st == "resolved":
                item.setForeground(3, QColor("#3fbf6f"))
            self.lst.addTopLevelItem(item)
        pending = len(self.db.list_unresolved(status="pending"))
        resolved = len(self.db.list_unresolved(status="resolved"))
        self.lbl_count.setText("当前 %d 条 ｜ 全部：待处理 %d / 已处理 %d"
                               % (len(rows), pending, resolved))

    def _selected_id(self):
        item = self.lst.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def to_dict_entry(self):
        uid = self._selected_id()
        if uid is None:
            return
        rows = [r for r in self.db.list_unresolved(status=None) if r.get("id") == uid]
        if not rows:
            return
        row = rows[0]
        # 预填：厂商 + 样例 + 原因（取报错原文第一行做草稿，人工再润色成人话）
        first_line = ""
        for ln in (row.get("pasted_text") or "").splitlines():
            if ln.strip():
                first_line = ln.strip()[:60]
                break
        dlg = DictEditDialog(self.db, self,
                             prefill={"vendor": row.get("guessed_vendor") or "",
                                      "cause": first_line,
                                      "examples": "\n".join(
                                          (row.get("pasted_text") or "").splitlines()[:3])})
        if dlg.exec_() == dlg.Accepted:
            err_id = dlg.saved_err_id
            if err_id:
                self.db.resolve_unresolved(uid, err_id)
            self.refresh()

    def delete_selected(self):
        uid = self._selected_id()
        if uid is None:
            return
        if not self.db.readonly:
            self.db.delete_unresolved(uid)
        self.refresh()

    def clean_resolved(self):
        """批量清理已处理的收件箱记录（字典条目不受影响）"""
        rows = self.db.list_unresolved(status="resolved")
        if not rows:
            QMessageBox.information(self, "无需清理", "没有已处理的收件箱记录。")
            return
        r = QMessageBox.question(
            self, "批量清理",
            "将删除 %d 条「已处理」的收件箱记录。\n\n"
            "注意：只是清理收件箱台账，**已生成的字典条目不会被删除**。继续吗？"
            % len(rows), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法清理。")
            return
        for row in rows:
            self.db.delete_unresolved(row.get("id"))
        self.refresh()
        QMessageBox.information(self, "已清理", "已清理 %d 条记录。" % len(rows))


class DictEditDialog(QDialog):
    """
    字典条目编辑（含模块 05 最关键的[测试]按钮）：
    pattern 旁粘贴 examples 样例即时显示命中与否——防止写出永远匹配不到的正则。

    两种模式（第 3 轮 缺口A）：
        err_id=None  → 新增（走 db.add_err，界面给一句草稿提示）
        err_id=xxx   → 编辑既有条目（载入原值，保存走 db.update_err，可改 pattern/原因）
    """

    def __init__(self, db, parent=None, prefill=None, err_id=None, operator=""):
        super(DictEditDialog, self).__init__(parent)
        self.db = db
        self.err_id = err_id or ""
        self.operator = operator or ("编辑字典条目" if err_id else "收件箱转化")
        self.saved_err_id = None
        self.setWindowTitle("编辑字典条目" if err_id else "报错字典条目")
        self.resize(680, 580)
        prefill = prefill or {}
        if err_id:
            exist = db.get_err(err_id) or {}
            prefill = {
                "vendor": exist.get("vendor") or "",
                "category": exist.get("category") or "",
                "pattern": exist.get("pattern") or "",
                "cause": exist.get("cause") or "",
                "fix_template": exist.get("fix_template") or "",
                "examples": "\n".join(str(x) for x in (exist.get("examples") or [])),
                "steps": "\n".join(str((st or {}).get("text") or "")
                                   for st in (exist.get("solution_steps") or [])),
            }

        v = QVBoxLayout(self)
        form = QFormLayout()
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.addItem("（不限）", "")
        for slug in sorted(dbmod.VENDOR_SLUGS.keys()):
            self.cmb_vendor.addItem(dbmod.display_vendor(slug), slug)
        if prefill.get("vendor"):
            idx = self.cmb_vendor.findData(dbmod.normalize_vendor(prefill["vendor"]))
            self.cmb_vendor.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_category = QComboBox()
        self.cmb_category.setEditable(True)      # 允许现场自定分类
        for c in dbmod.ERR_CATEGORIES:
            self.cmb_category.addItem(c)
        if prefill.get("category"):
            idx = self.cmb_category.findText(prefill["category"])
            if idx >= 0:
                self.cmb_category.setCurrentIndex(idx)
            else:
                self.cmb_category.setCurrentText(prefill["category"])
        self.ed_pattern = QLineEdit()
        self.ed_pattern.setFont(_mono(10))
        self.ed_cause = QLineEdit()
        self.ed_fix = QLineEdit()
        self.ed_fix.setFont(_mono(10))
        form.addRow("厂商", self.cmb_vendor)
        form.addRow("分类", self.cmb_category)
        form.addRow("正则 pattern *", self.ed_pattern)
        form.addRow("原因（人话）*", self.ed_cause)
        form.addRow("修正命令模板", self.ed_fix)
        v.addLayout(form)

        # ★ [测试]按钮：pattern 旁即时验证（模块 05 最大的坑，必须做）
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("测试 pattern")
        self.btn_test.setObjectName("Ghost")
        self.btn_test.clicked.connect(self.test_pattern)
        test_row.addWidget(self.btn_test)
        self.lbl_test = QLabel("粘贴一条真实报错到下面的大框，点测试看命中与否")
        self.lbl_test.setObjectName("StateLabel")
        test_row.addWidget(self.lbl_test, 1)
        v.addLayout(test_row)

        self.txt_examples = QTextEdit()
        self.txt_examples.setFont(_mono(10))
        self.txt_examples.setPlaceholderText("真实报错样例（每行一条，至少 2 条，用于自测与测试按钮）")
        self.txt_examples.setPlainText(prefill.get("examples", ""))
        self.txt_examples.setMinimumHeight(90)
        v.addWidget(QLabel("真实样例（每行一条）："))
        v.addWidget(self.txt_examples)

        self.txt_steps = QTextEdit()
        self.txt_steps.setPlaceholderText("解决步骤（每行一条，可包含参数占位 {{xxx}}）")
        self.txt_steps.setMinimumHeight(80)
        if prefill.get("steps"):
            self.txt_steps.setPlainText(prefill["steps"])
        v.addWidget(QLabel("解决步骤（每行一条）："))
        v.addWidget(self.txt_steps)

        if prefill.get("pattern"):
            self.ed_pattern.setText(prefill["pattern"])
        if prefill.get("cause"):
            self.ed_cause.setText(prefill["cause"])
            if not self.err_id:
                self.ed_cause.setPlaceholderText(
                    "（草稿）把报错原文改写成一句人话原因，再补解决步骤")
        if prefill.get("fix_template"):
            self.ed_fix.setText(prefill["fix_template"])
        if self.err_id:
            tip = QLabel("编辑既有字典条目：保存后改动会写入 history（可见改动前后的值）。")
            tip.setWordWrap(True)
            tip.setObjectName("Hint")
            v.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        self.txt_examples.textChanged.connect(self._auto_test)

    def _auto_test(self):
        """examples 变化时自动跑一遍 pattern 命中测试"""
        self.test_pattern(quiet=True)

    def test_pattern(self, quiet=False):
        """pattern 命中测试。quiet=True（examples 输入中自动触发）时只算不刷 UI，
        避免打字过程中标签闪烁报错（B13：此前 quiet 参数被传入但从未读取）"""
        import error_match as em
        pattern = self.ed_pattern.text().strip()
        if not pattern:
            if not quiet:
                self.lbl_test.setText("✘ pattern 为空")
                set_state(self.lbl_test, "err")
            return False
        regex = em.compile_pattern(pattern)
        if regex is None:
            if not quiet:
                self.lbl_test.setText("✘ 正则无法编译（请检查语法）")
                set_state(self.lbl_test, "err")
            return False
        lines = [ln for ln in self.txt_examples.toPlainText().splitlines() if ln.strip()]
        if not lines:
            if not quiet:
                self.lbl_test.setText("⚠ 没有样例可测——先在下方粘贴真实报错")
                set_state(self.lbl_test, "warn")
            return False
        hit = sum(1 for ln in lines if regex.search(ln))
        ok = hit == len(lines)
        if not quiet:
            self.lbl_test.setText(
                ("%s 命中 %d/%d 条样例" % ("✓" if ok else "✘", hit, len(lines))))
            set_state(self.lbl_test, "ok" if ok else "err")
        return ok

    def save(self):
        import error_match as em
        pattern = self.ed_pattern.text().strip()
        cause = self.ed_cause.text().strip()
        if not pattern or not cause:
            QMessageBox.warning(self, "缺少必填项", "pattern 与原因必须填写。")
            return
        if em.compile_pattern(pattern) is None:
            QMessageBox.warning(self, "正则无法编译", "pattern 语法有误，请修正后再保存。")
            return
        examples = [ln.strip() for ln in self.txt_examples.toPlainText().splitlines()
                    if ln.strip()]
        if len(examples) < 1:
            QMessageBox.warning(self, "缺少样例", "至少粘贴 1 条真实报错样例。")
            return
        miss = [ex for ex in examples if not em.compile_pattern(pattern).search(ex)]
        if miss:
            r = QMessageBox.question(
                self, "样例未全部命中",
                "有 %d 条样例不能被 pattern 命中（第一条：%s）。\n"
                "确定要保存吗？（保存后质检会提示）" % (len(miss), miss[0][:50]),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        steps = [{"text": ln.strip()} for ln in self.txt_steps.toPlainText().splitlines()
                 if ln.strip()]
        payload = {
            "vendor": self.cmb_vendor.currentData(),
            "os_family": "",
            "pattern": pattern,
            "category": self.cmb_category.currentText().strip(),
            "cause": cause,
            "solution_steps": steps,
            "fix_template": self.ed_fix.text().strip(),
            "examples": examples,
        }
        try:
            if self.err_id:
                # 编辑既有条目：update_err 已做归一化与序列化，且值无变化时返回 False
                changed = self.db.update_err(self.err_id, payload, operator=self.operator)
                if changed is False:
                    QMessageBox.information(self, "没有改动", "内容与库内一致，未写入。")
                self.saved_err_id = self.err_id
            else:
                self.saved_err_id = self.db.add_err(payload, operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.accept()


class DictManagerDialog(QDialog):
    """
    报错字典维护（第 3 轮 缺口A）。

    此前字典条目只有"新增"一条路（收件箱转化），转错了没法改，只能删库重录。
    这里补上完整的浏览 / 新增 / 编辑 / 删除，全部走 db 层并自动落 history。
    """

    def __init__(self, db, parent=None):
        super(DictManagerDialog, self).__init__(parent)
        self.db = db
        self.rows = []
        self.setWindowTitle("报错字典维护")
        self.resize(1060, 620)
        self._build()
        self.refresh()

    # ---------------- 构建 ----------------
    def _build(self):
        top = QHBoxLayout()
        top.setSpacing(6)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("快速筛选：pattern / 原因 / 步骤 / 分类（输入即筛）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self.refresh)
        top.addWidget(self.ed_search, 1)
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.addItem("全部厂商", "")
        for slug in sorted(dbmod.VENDOR_SLUGS.keys()):
            self.cmb_vendor.addItem(dbmod.display_vendor(slug), slug)
        self.cmb_vendor.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.cmb_vendor)
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.setObjectName("Ghost")
        self.btn_reload.clicked.connect(self.refresh)
        top.addWidget(self.btn_reload)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.btn_new = QPushButton("新增条目")
        self.btn_new.setObjectName("Primary")
        self.btn_new.clicked.connect(self.on_new)
        tools.addWidget(self.btn_new)
        self.btn_edit = QPushButton("编辑选中")
        self.btn_edit.clicked.connect(self.on_edit)
        tools.addWidget(self.btn_edit)
        self.btn_test = QPushButton("测试 pattern")
        self.btn_test.setObjectName("Ghost")
        self.btn_test.setToolTip("用该条目的 examples 逐个验证 pattern 是否命中")
        self.btn_test.clicked.connect(self.on_test)
        tools.addWidget(self.btn_test)
        self.btn_del = QPushButton("删除选中")
        self.btn_del.setObjectName("Ghost")
        self.btn_del.clicked.connect(self.on_delete)
        tools.addWidget(self.btn_del)
        tools.addStretch(1)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["厂商", "分类", "正则 pattern", "原因", "样例数", "已验证", "命中次数"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _i: self.on_edit())
        self.table.itemSelectionChanged.connect(self._on_selection)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StatusStrip")
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        self.lbl_test.setObjectName("StateLabel")

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.lbl_status, 1)
        bottom.addWidget(btn_close)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)
        root.addLayout(top)
        root.addLayout(tools)
        root.addWidget(self.table, 1)
        root.addWidget(self.lbl_test)
        root.addLayout(bottom)

        if self.db.readonly:
            for w in (self.btn_new, self.btn_edit, self.btn_del):
                w.setEnabled(False)
            self.lbl_status.setText("命令库处于只读状态（U 盘写保护），无法新增/编辑/删除。")
            set_state(self.lbl_status, "err")

    # ---------------- 刷新 ----------------
    def refresh(self):
        kw = (self.ed_search.text() or "").strip().lower()
        vendor = self.cmb_vendor.currentData() if hasattr(self, "cmb_vendor") else ""
        rows = self.db.all_errs(vendor=vendor or None)
        if kw:
            def blob(e):
                parts = [e.get("pattern"), e.get("cause"), e.get("category"),
                         e.get("fix_template")]
                parts += [str((s or {}).get("text") or "")
                          for s in (e.get("solution_steps") or [])]
                return " ".join(str(x or "") for x in parts).lower()
            rows = [r for r in rows if kw in blob(r)]
        self.rows = rows

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for e in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [dbmod.display_vendor(e.get("vendor")),
                    e.get("category") or "-",
                    e.get("pattern") or "",
                    e.get("cause") or "",
                    str(len(e.get("examples") or [])),
                    "已验证" if int(e.get("verified") or 0) == 1 else "未验证",
                    str(int(e.get("hit_count") or 0))]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.UserRole, e.get("err_id"))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        if not self.db.readonly:
            self.lbl_status.setText("共 %d 条字典条目%s"
                                    % (len(rows), "（已筛选）" if kw else ""))
        if rows:
            self.table.selectRow(0)
        else:
            self.lbl_test.setText("")

    def _selected(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 0)
        eid = item.data(Qt.UserRole) if item else None
        return self.db.get_err(eid) if eid else None

    def _on_selection(self):
        e = self._selected()
        if not e:
            self.lbl_test.setText("")
            return
        self.lbl_test.setText("pattern：%s" % (e.get("pattern") or ""))

    # ---------------- 动作 ----------------
    def on_new(self):
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法新增字典条目。")
            return
        dlg = DictEditDialog(self.db, self)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()
            self.lbl_status.setText("已新增字典条目（%s）。" % (dlg.saved_err_id or ""))

    def on_edit(self):
        e = self._selected()
        if not e:
            QMessageBox.information(self, "未选中", "请先在列表里选中一条字典条目。")
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法编辑字典条目。")
            return
        dlg = DictEditDialog(self.db, self, err_id=e.get("err_id"), operator="字典维护")
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()
            self.lbl_status.setText("已保存修改（改动已写入 history）。")

    def on_test(self):
        import error_match as em
        e = self._selected()
        if not e:
            QMessageBox.information(self, "未选中", "请先选中一条字典条目。")
            return
        rows = em.selftest_dicts([e])
        lines = ["%s ｜ %s" % (e.get("pattern"), (e.get("cause") or "")[:40])]
        for _eid, idx, ok, msg in rows:
            lines.append("  样例 %d：%s %s" % (idx, "命中" if ok else "未命中", msg))
        bad = [r for r in rows if not r[2]]
        self.lbl_test.setText("\n".join(lines))
        set_state(self.lbl_test, "err" if bad else "ok")

    def on_delete(self):
        e = self._selected()
        if not e:
            QMessageBox.information(self, "未选中", "请先选中一条字典条目。")
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库为只读，无法删除字典条目。")
            return
        if QMessageBox.question(
                self, "确认删除",
                "确认删除字典条目？\n\npattern：%s\n原因：%s\n\n"
                "删除后 history 会保留这条删除记录，且不能撤销。"
                % (e.get("pattern"), (e.get("cause") or "")[:60]),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.db.delete_err(e.get("err_id"), operator="字典维护")
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.refresh()
        self.lbl_status.setText("已删除该字典条目。")
