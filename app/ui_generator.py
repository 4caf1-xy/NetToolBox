# -*- coding: utf-8 -*-
"""
ui_generator.py —— 参数化命令生成器（模态对话框）

三大块：
    1. ParamFormWidget   参数表单：自动根据 {{参数}} 生成，含类型校验、必填标红、
                         端口范围按厂商命名规则智能展开并实时预览
    2. GeneratorDialog   单条命令生成器：左侧填参数 / 右侧实时预览 / 复制 / 导出 .txt
    3. PackageDialog     配置包合并导出：多条命令勾选合并成一个脚本，
                         头部自动加注释块（设备型号 / 生成时间 / 操作人 / 场景说明）

说明：本模块只做"生成文本"，不含任何设备连接能力——命令交给外部 Xshell/SecureCRT 执行。
"""

import os
import re
import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QLabel, QLineEdit, QComboBox, QSpinBox, QCheckBox,
                             QPushButton, QPlainTextEdit, QListWidget, QListWidgetItem,
                             QScrollArea, QSplitter, QMessageBox, QFileDialog,
                             QGroupBox, QAbstractItemView)

import renderer
import db as dbmod
from ui_main import CommandHighlighter, copy_to_clipboard   # 复用主窗的高亮与剪贴板封装

# 会话内记住上次填的操作人，省得每次重敲（离网环境留痕要用）
_LAST_OPERATOR = {"value": ""}

def mono_font(size=10):
    """等宽字体（命令区统一用它）"""
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(size)
    return f


# ---------------------------------------------------------------------------
# 一、参数表单
# ---------------------------------------------------------------------------
from theme import repolish, set_state  # 状态标签 / error 属性的动态重polish

class ParamFormWidget(QWidget):
    """
    根据参数规格动态生成表单。
        - validate 以 enum: 开头 → 下拉框
        - validate 以 int:  开头 → 数字框（带上下限）
        - 其余 → 单行输入框
        - expand == "port"   → 额外显示"端口将展开为 …"的实时预览
        - required           → 标签加红色星号，空值时输入框标红并提示
    """

    changed = pyqtSignal(dict)      # 任一参数变化时发出当前全部取值

    def __init__(self, specs, vendor="", parent=None, os_family=""):
        super(ParamFormWidget, self).__init__(parent)
        self.specs = [s for s in (specs or []) if isinstance(s, dict) and s.get("name")]
        self.vendor = vendor or ""
        self.os_family = os_family or ""
        self.editors = {}           # 参数名 -> 输入控件
        self.error_labels = {}      # 参数名 -> 错误提示 QLabel
        self.containers = {}        # 参数名 -> 包裹控件（用于整体标红/定位）
        self._build()

    # ---------------- 构建 ----------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if not self.specs:
            tip = QLabel("该条目没有 {{参数}} 占位，无需填写参数，直接用『复制全部』即可。")
            tip.setWordWrap(True)
            tip.setObjectName("Hint")
            tip.setIndent(12)
            outer.addWidget(tip)
            outer.addStretch(1)
            return

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setSpacing(10)
        form.setContentsMargins(4, 4, 8, 4)

        for spec in self.specs:
            name = spec["name"]
            label = QLabel()
            label.setTextFormat(Qt.RichText)
            text = spec.get("label") or name
            if spec.get("required"):
                label.setText("%s <span style='color:#e05656; font-weight:bold;'>*</span>"
                              % _escape(text))
            else:
                label.setText(_escape(text))
            label.setToolTip("参数字段名：{{%s}}" % name)

            editor = self._make_editor(spec)
            self.editors[name] = editor

            hint = QLabel("")
            hint.setWordWrap(True)
            hint.setObjectName("StateLabel")
            self.error_labels[name] = hint

            box = QWidget()
            bx = QVBoxLayout(box)
            bx.setContentsMargins(0, 0, 0, 0)
            bx.setSpacing(2)
            bx.addWidget(editor)
            bx.addWidget(hint)
            self.containers[name] = box

            form.addRow(label, box)

        outer.addLayout(form)
        outer.addStretch(1)
        self.validate_all(silent=True)

    def _make_editor(self, spec):
        """按校验规则选控件类型；默认值预填"""
        rule = (spec.get("validate") or "").strip()
        default = spec.get("default")
        default = "" if default is None else str(default)

        # flag 型参数（schema 升级 2026-09-30）：勾选 → 渲染 on_value（默认参数名），
        # 不勾选 → 占位整体消失。UI 表达就是开关本身，不做文本输入。
        if (spec.get("type") or "").strip() == "flag":
            chk = QCheckBox()
            on_value = str(spec.get("on_value") or spec.get("example") or spec["name"])
            chk.setProperty("on_value", on_value)
            chk.setChecked(bool(default))
            chk.setToolTip("勾选后渲染为：%s；不勾选则该占位从命令中消失" % on_value)
            chk.toggled.connect(self._on_changed)
            return chk

        if rule.startswith("enum:"):
            cmb = QComboBox()
            for value in [v for v in rule[5:].split("|") if v]:
                cmb.addItem(value)
            idx = cmb.findText(default)
            cmb.setCurrentIndex(idx if idx >= 0 else 0)
            cmb.currentIndexChanged.connect(self._on_changed)
            return cmb

        if rule.startswith("int:"):
            match = re.match(r"^int:(-?\d+)-(-?\d+)$", rule)
            if match:
                lo, hi = int(match.group(1)), int(match.group(2))
                spin = QSpinBox()
                spin.setRange(lo, hi)
                try:
                    spin.setValue(int(default) if default else lo)
                except ValueError:
                    spin.setValue(lo)
                spin.valueChanged.connect(self._on_changed)
                return spin

        editor = QLineEdit(default)
        editor.setPlaceholderText(spec.get("example") or spec.get("label") or "")
        editor.textChanged.connect(self._on_changed)
        return editor

    # ---------------- 取值 / 赋值 ----------------
    @staticmethod
    def _editor_value(widget):
        if isinstance(widget, QCheckBox):
            # flag 参数：勾选 → on_value（渲染文本）；不勾选 → 空串（占位消失）
            return str(widget.property("on_value") or "") if widget.isChecked() else ""
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QSpinBox):
            return str(widget.value())
        return widget.text()

    def values(self):
        """当前所有参数取值 {参数名: 值}"""
        return dict((name, self._editor_value(w)) for name, w in self.editors.items())

    def set_values(self, values):
        """批量赋值（不触发逐字段校验风暴，最后统一校验一次）"""
        for name, value in (values or {}).items():
            widget = self.editors.get(name)
            if widget is None or value is None:
                continue
            widget.blockSignals(True)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(value))
                except ValueError:
                    pass
            else:
                widget.setText(str(value))
            widget.blockSignals(False)
        self.validate_all(silent=True)
        self.changed.emit(self.values())

    def reset_defaults(self):
        """恢复参数默认值"""
        values = {}
        for spec in self.specs:
            values[spec["name"]] = str(spec.get("default") or "")
        self.set_values(values)

    # ---------------- 校验 ----------------
    def errors(self):
        """返回 {参数名: 错误信息}，不修改界面"""
        result = {}
        current = self.values()
        for spec in self.specs:
            name = spec["name"]
            passed, msg = renderer.validate_value(spec, current.get(name, ""))
            if not passed:
                result[name] = msg
                continue
            # 端口类参数额外校验"能否展开"
            if spec.get("expand") == "port" and str(current.get(name, "")).strip():
                expanded, names, err = renderer.expand_port_range(
                    current.get(name), self.vendor, os_family=self.os_family)
                if not expanded:
                    result[name] = err or "端口范围无法展开"
        return result

    def validate_all(self, silent=False):
        """
        刷新所有字段的校验提示，返回是否全部通过。
        silent=True 时通过就不显示任何提示（首次构建用，避免满屏绿字）。
        """
        errors = self.errors()
        for spec in self.specs:
            name = spec["name"]
            widget = self.editors.get(name)
            hint = self.error_labels.get(name)
            if widget is None or hint is None:
                continue

            if name in errors:
                widget.setProperty("error", True)
                repolish(widget)
                set_state(hint, "err")
                hint.setText("✘ %s" % errors[name])
                continue

            widget.setProperty("error", False)
            repolish(widget)
            # 通过时显示"示例/端口展开预览"，而不是啰嗦的"校验通过"
            set_state(hint, "idle")
            hint.setText(self._hint_text(spec, silent=silent))
        return not errors

    def _hint_text(self, spec, silent=False):
        """字段下方提示：端口展开预览优先，其次示例，最后厂商命名规则"""
        name = spec["name"]
        value = str(self.values().get(name, "")).strip()
        if spec.get("expand") == "port" and value:
            expanded, names, err = renderer.expand_port_range(
                value, self.vendor, os_family=self.os_family)
            rule = renderer.get_port_rule(self.vendor, self.os_family)
            if expanded:
                preview = ", ".join(names[:4])
                if len(names) > 4:
                    preview += ", … "
                return "将展开为 %d 个接口：%s\n（%s 命名规则示例：%s）" % (
                    len(names), preview,
                    dbmod.display_vendor(self.vendor) if self.vendor else "默认",
                    rule.get("example", ""))
            return "端口展开失败：%s" % (err or "格式不正确")

        parts = []
        if spec.get("example"):
            parts.append("示例：%s" % spec["example"])
        if silent and not parts:
            parts.append("选填" if not spec.get("required") else "必填")
        return " ｜ ".join(parts)

    def _on_changed(self, *_args):
        """任一控件变化：刷新提示 + 对外广播取值"""
        self.validate_all(silent=True)
        self.changed.emit(self.values())

    def focus_first_error(self):
        """把焦点移到第一个出错的参数上"""
        for spec in self.specs:
            if spec["name"] in self.errors():
                widget = self.editors.get(spec["name"])
                if widget is not None:
                    widget.setFocus()
                    return


def _escape(text):
    """转义富文本特殊字符，避免参数名里的 & < > 破坏标签显示"""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# 二、单条命令生成器
# ---------------------------------------------------------------------------
class GeneratorDialog(QDialog):
    """
    参数化命令生成器（模态）。
    左侧参数表单 → 右侧实时预览 → 复制 / 导出。
    以 apply_mode=True 打开时，"应用参数"按钮会把取值回传（配置包合并时用）。
    """

    def __init__(self, db, entry, parent=None, apply_mode=False):
        super(GeneratorDialog, self).__init__(parent)
        self.db = db
        self.entry = entry or {}
        self.apply_mode = apply_mode

        self.commands = self.entry.get("commands") or ""
        self.specs = renderer.merge_param_specs(
            db.load_params(self.entry) if db else [], self.commands)

        self.setWindowTitle("参数化命令生成器 · %s" % (self.entry.get("title") or "未命名"))
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)
        self._build()
        self._update_preview()

    # ---------------- 构建 ----------------
    def _build(self):
        # ---- 顶部：条目信息 ----
        vendor = self.entry.get("vendor") or ""
        color = _vendor_color(vendor)
        info = QLabel(
            "<span style='color:%s; font-weight:bold;'>%s</span> ｜ %s ｜ %s ｜ 场景：%s ｜ 型号：%s<br>"
            "<span style='color:#9aa0b0;'>%s</span>"
            % (color, _escape(dbmod.display_vendor(vendor)),
               _escape(self.entry.get("device_type")
                       or dbmod.display_platform(self.entry.get("platform"))),
               _escape(dbmod.display_os(self.entry.get("os_family"))),
               _escape(self.entry.get("category") or "-"),
               _escape(self.entry.get("models") or "-"),
               _escape(self.entry.get("description") or "")))
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        info.setIndent(4)

        if int(self.entry.get("verified") or 0) == 1:
            badge = QLabel("已验证")
            badge.setObjectName("Badge")
            badge.setProperty("state", "ok")
        else:
            badge = QLabel("未验证 · 执行前请核对")
            badge.setObjectName("Badge")
            badge.setProperty("state", "muted")

        head = QHBoxLayout()
        head.addWidget(info, 1)
        head.addWidget(badge, 0, Qt.AlignTop)

        # ---- 左：参数表单（可滚动）----
        self.form = ParamFormWidget(self.specs, vendor=vendor,
                                    os_family=self.entry.get("os_family"))
        self.form.changed.connect(self._on_form_changed)

        form_box = QGroupBox("参数（%d 个）" % len(self.specs))
        fb = QVBoxLayout(form_box)
        fb.setContentsMargins(8, 10, 8, 8)
        fb.addWidget(self.form)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(form_box)

        self.btn_reset = QPushButton("恢复默认值")
        self.btn_reset.setObjectName("Ghost")
        self.btn_reset.clicked.connect(self.form.reset_defaults)
        self.chk_header = QCheckBox("在脚本头部加注释块（设备型号/生成时间/操作人/场景）")
        self.chk_header.setChecked(True)
        self.chk_header.stateChanged.connect(self._update_preview)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        lv.setSpacing(6)
        lv.addWidget(scroll, 1)
        lv.addWidget(self.chk_header)
        lv.addWidget(self.btn_reset)

        # ---- 右：实时预览 ----
        self.txt_preview = QPlainTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_preview.setFont(mono_font(10))
        self.txt_preview.setObjectName("CodeBlock")
        self.highlighter = CommandHighlighter(self.txt_preview.document())

        prev_box = QGroupBox("实时预览")
        pv = QVBoxLayout(prev_box)
        pv.setContentsMargins(8, 10, 8, 8)
        pv.addWidget(self.txt_preview)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StateLabel")
        self.lbl_status.setWordWrap(True)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 8, 8, 8)
        rv.setSpacing(6)
        rv.addWidget(prev_box, 1)
        rv.addWidget(self.lbl_status)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        split.setSizes([440, 740])

        # ---- 底部：操作人 / 场景说明 + 按钮 ----
        self.ed_operator = QLineEdit(_LAST_OPERATOR["value"])
        self.ed_operator.setPlaceholderText("操作人（写进脚本头部，离网环境留痕用）")
        self.ed_scene = QLineEdit("")
        self.ed_scene.setPlaceholderText("场景说明（可留空，默认取条目的场景分类）")
        self.ed_model = QLineEdit("")
        self.ed_model.setPlaceholderText("设备型号（可留空，默认取条目的适用型号）")
        for widget in (self.ed_operator, self.ed_scene, self.ed_model):
            widget.textChanged.connect(self._update_preview)

        opts = QHBoxLayout()
        opts.setSpacing(6)
        opts.addWidget(QLabel("操作人："))
        opts.addWidget(self.ed_operator, 2)
        opts.addWidget(QLabel("场景："))
        opts.addWidget(self.ed_scene, 2)
        opts.addWidget(QLabel("型号："))
        opts.addWidget(self.ed_model, 2)

        self.btn_copy_all = QPushButton("复制全部")
        self.btn_copy_all.setObjectName("Primary")
        self.btn_copy_all.clicked.connect(lambda: self._copy(False))
        self.btn_copy_cmd = QPushButton("仅复制命令")
        self.btn_copy_cmd.clicked.connect(lambda: self._copy(True))
        self.btn_export = QPushButton("导出 .txt")
        self.btn_export.setObjectName("Ghost")
        self.btn_export.clicked.connect(self._export_txt)
        self.btn_apply = QPushButton("应用参数")
        self.btn_apply.setObjectName("Ok")
        self.btn_apply.clicked.connect(self.accept)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("Ghost")
        self.btn_close.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.btn_copy_all)
        buttons.addWidget(self.btn_copy_cmd)
        buttons.addWidget(self.btn_export)
        buttons.addStretch(1)
        if not self.apply_mode:
            self.btn_apply.setVisible(False)
        buttons.addWidget(self.btn_apply)
        buttons.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addLayout(head)
        root.addWidget(split, 1)
        root.addLayout(opts)
        root.addLayout(buttons)

    # ---------------- 实时预览 ----------------
    def _on_form_changed(self, _values):
        self._update_preview()

    def _render(self):
        """按当前取值渲染命令文本（不含头部）"""
        values = self.form.values()
        text, missing = renderer.render(self.commands, values,
                                        vendor=self.entry.get("vendor"),
                                        specs=self.specs,
                                        os_family=self.entry.get("os_family"))
        return text, missing

    def _rendered_text(self):
        """带/不带头部注释块的最终文本"""
        text, missing = self._render()
        if not self.chk_header.isChecked():
            return text, missing
        header = renderer.build_header(
            entry=self.entry,
            operator=self.ed_operator.text().strip(),
            scene=self.ed_scene.text().strip() or self.entry.get("category", ""),
            model=self.ed_model.text().strip() or self.entry.get("models", ""),
            extra="参数：%s" % "，".join("%s=%s" % (k, v) for k, v in self.form.values().items()
                                        if str(v).strip()))
        return header + "\n\n" + text, missing

    def _update_preview(self, *_args):
        text, missing = self._rendered_text()
        self.txt_preview.setPlainText(text)
        errors = self.form.errors()
        count = renderer.count_effective_lines(text)
        total_lines = len([l for l in text.splitlines() if l.strip()])
        if errors:
            set_state(self.lbl_status, "err")
            self.lbl_status.setText("✘ 有 %d 个参数不合法：%s（修正后才能复制）"
                                    % (len(errors), "；".join(errors.values())))
        elif missing:
            set_state(self.lbl_status, "warn")
            self.lbl_status.setText("⚠ 仍有未填参数：%s（将保持 {{占位}} 原样）"
                                    % "、".join(missing))
        elif count == 0 and total_lines:
            # 深信服 AF / 天融信这类以 Web 控制台为主的厂商：整条都是说明性文字，没有可执行命令
            set_state(self.lbl_status, "warn")
            self.lbl_status.setText("⚠ 本条为【Web 控制台操作路径清单】（共 %d 行说明），"
                                    "没有可直接粘贴执行的命令 —— 请按界面路径操作"
                                    % total_lines)
        else:
            set_state(self.lbl_status, "ok")
            self.lbl_status.setText("✓ 渲染正常，共 %d 条有效命令" % count)

    # ---------------- 复制 / 导出 ----------------
    def _copy(self, commands_only):
        errors = self.form.errors()
        if errors:
            QMessageBox.warning(self, "参数不合法",
                                "请先修正标红的参数：\n\n%s" % "\n".join(errors.values()))
            self.form.focus_first_error()
            return
        # 骨架条目复制前的一次性提醒（审计 P1，与详情页共用同一守卫/同一状态）
        from ui_main import guard_skeleton_copy
        guard_skeleton_copy(self.parent(), self.entry)
        text, _missing = self._rendered_text()
        if commands_only:
            text = renderer.strip_comments(text)
        if not copy_to_clipboard(text):
            set_state(self.lbl_status, "err")
            self.lbl_status.setText("✘ 复制失败（剪贴板被占用），请手动选中右侧预览区内容复制。")
            return
        op = self.ed_operator.text().strip()
        if op:
            _LAST_OPERATOR["value"] = op
        kind = "纯命令（已剔除注释）" if commands_only else "全部内容（含注释）"
        count = renderer.count_effective_lines(text)
        set_state(self.lbl_status, "ok")
        self.lbl_status.setText("✓已复制 %s，共 %d 条命令 → 切到 Xshell 粘贴" % (kind, count))

    def _export_txt(self):
        errors = self.form.errors()
        if errors:
            QMessageBox.warning(self, "参数不合法", "请先修正标红的参数。")
            self.form.focus_first_error()
            return
        text, _missing = self._rendered_text()
        title = re.sub(r'[\\/:*?"<>|]', "_", self.entry.get("title") or "command")
        default = os.path.join(_base_dir(), "%s_%s.txt"
                               % (title, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
        path, _ = QFileDialog.getSaveFileName(self, "导出为 .txt", default, "文本文件 (*.txt)")
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(text)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        set_state(self.lbl_status, "ok")
        self.lbl_status.setText("✓ 已导出：%s" % path)

    def values(self):
        """返回当前参数取值（apply_mode 下由调用方读取）"""
        return self.form.values()


# ---------------------------------------------------------------------------
# 三、配置包合并导出
# ---------------------------------------------------------------------------
class PackageDialog(QDialog):
    """
    多条命令勾选合并成一个配置包：
        - 左侧勾选要打包的条目（来自当前主窗筛选结果）
        - 双击带参数的条目可单独填写参数（复用 GeneratorDialog）
        - 右侧实时预览合并后的完整脚本（含头部注释块）
        - 支持复制 / 导出 .txt
    """

    def __init__(self, db, entries, parent=None):
        super(PackageDialog, self).__init__(parent)
        self.db = db
        self.all_entries = list(entries or [])
        self.values_by_uuid = {}        # 条目 UUID -> 已填参数
        self.setWindowTitle("配置包生成器 · 勾选多条命令合并导出")
        self.resize(1240, 800)
        self.setMinimumSize(980, 640)
        self._build()
        self._refresh_preview()

    # ---------------- 构建 ----------------
    def _build(self):
        tip = QLabel("勾选要打包的命令条目 → 右侧实时预览 → 复制或导出 .txt。"
                     "双击带 {{参数}} 的条目可单独填写参数（未填则用默认值）。")
        tip.setObjectName("Hint")
        tip.setIndent(4)
        tip.setWordWrap(True)

        # 左侧勾选列表
        self.list_items = QListWidget()
        self.list_items.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_items.itemChanged.connect(self._on_item_changed)
        self.list_items.itemDoubleClicked.connect(self._edit_item_params)
        for entry in self.all_entries:
            param_count = len(renderer.merge_param_specs(
                self.db.load_params(entry), entry.get("commands") or ""))
            item = QListWidgetItem("[%s] %s%s" % (dbmod.display_vendor(entry.get("vendor")),
                                                  entry.get("title") or "",
                                                  "  （%d 个参数）" % param_count if param_count else ""))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)          # 默认全选，方便整体打包
            item.setData(Qt.UserRole, entry.get("uuid"))
            item.setToolTip("双击填写参数" if param_count else "无参数")
            self.list_items.addItem(item)

        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton("全不选")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        self.btn_edit_param = QPushButton("填写参数…")
        self.btn_edit_param.clicked.connect(self._edit_item_params)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(btn_all)
        tools.addWidget(btn_none)
        tools.addStretch(1)
        tools.addWidget(self.btn_edit_param)

        self.lbl_count = QLabel("已选 0 条")
        self.lbl_count.setObjectName("Secondary")

        left_box = QGroupBox("选择命令条目")
        lb = QVBoxLayout(left_box)
        lb.setContentsMargins(8, 10, 8, 8)
        lb.setSpacing(6)
        lb.addWidget(self.list_items, 1)
        lb.addLayout(tools)
        lb.addWidget(self.lbl_count)

        # 右侧预览
        self.txt_preview = QPlainTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_preview.setFont(mono_font(10))
        self.txt_preview.setObjectName("CodeBlock")
        self.highlighter = CommandHighlighter(self.txt_preview.document())

        self.chk_header = QCheckBox("加脚本头部注释块")
        self.chk_header.setChecked(True)
        self.chk_header.stateChanged.connect(self._refresh_preview)
        self.chk_blank = QCheckBox("条目之间插空行分隔")
        self.chk_blank.setChecked(True)
        self.chk_blank.stateChanged.connect(self._refresh_preview)

        self.ed_operator = QLineEdit(_LAST_OPERATOR["value"])
        self.ed_operator.setPlaceholderText("操作人")
        self.ed_scene = QLineEdit("")
        self.ed_scene.setPlaceholderText("场景说明")
        self.ed_model = QLineEdit("")
        self.ed_model.setPlaceholderText("设备型号")
        for widget in (self.ed_operator, self.ed_scene, self.ed_model):
            widget.textChanged.connect(self._refresh_preview)

        opts = QHBoxLayout()
        opts.setSpacing(6)
        opts.addWidget(self.chk_header)
        opts.addWidget(self.chk_blank)
        opts.addWidget(QLabel("操作人："))
        opts.addWidget(self.ed_operator, 2)
        opts.addWidget(QLabel("场景："))
        opts.addWidget(self.ed_scene, 2)
        opts.addWidget(QLabel("型号："))
        opts.addWidget(self.ed_model, 2)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StateLabel")
        self.lbl_status.setWordWrap(True)

        right_box = QGroupBox("配置包预览")
        rb = QVBoxLayout(right_box)
        rb.setContentsMargins(8, 10, 8, 8)
        rb.setSpacing(6)
        rb.addWidget(self.txt_preview, 1)
        rb.addLayout(opts)
        rb.addWidget(self.lbl_status)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left_box)
        split.addWidget(right_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 7)
        split.setSizes([400, 820])

        self.btn_copy = QPushButton("复制配置包")
        self.btn_copy.setObjectName("Primary")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_export = QPushButton("导出 .txt")
        self.btn_export.setObjectName("Ghost")
        self.btn_export.clicked.connect(self._export_txt)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("Ghost")
        self.btn_close.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.btn_copy)
        buttons.addWidget(self.btn_export)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(tip)
        root.addWidget(split, 1)
        root.addLayout(buttons)

    # ---------------- 交互 ----------------
    def _set_all_checked(self, checked):
        self.list_items.blockSignals(True)
        for row in range(self.list_items.count()):
            self.list_items.item(row).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list_items.blockSignals(False)
        self._refresh_preview()

    def _on_item_changed(self, _item):
        self._refresh_preview()

    def _current_entry(self):
        """当前高亮的条目 dict"""
        item = self.list_items.currentItem()
        if item is None:
            return None
        entry_uuid = item.data(Qt.UserRole)
        for entry in self.all_entries:
            if entry.get("uuid") == entry_uuid:
                return entry
        return None

    def _checked_entries(self):
        """已勾选的条目 dict 列表（保持列表顺序）"""
        result = []
        for row in range(self.list_items.count()):
            item = self.list_items.item(row)
            if item.checkState() != Qt.Checked:
                continue
            entry_uuid = item.data(Qt.UserRole)
            for entry in self.all_entries:
                if entry.get("uuid") == entry_uuid:
                    result.append(entry)
                    break
        return result

    def _edit_item_params(self, _item=None):
        """双击/按钮：为当前条目单独填参数"""
        entry = self._current_entry()
        if entry is None:
            return
        specs = renderer.merge_param_specs(self.db.load_params(entry),
                                           entry.get("commands") or "")
        if not specs:
            QMessageBox.information(self, "无需填写", "该条目没有 {{参数}} 占位，直接打包即可。")
            return
        dlg = GeneratorDialog(self.db, entry, self, apply_mode=True)
        dlg.form.set_values(self.values_by_uuid.get(entry.get("uuid"), {}))
        if dlg.exec_() == QDialog.Accepted:
            self.values_by_uuid[entry.get("uuid")] = dlg.values()
            self._refresh_preview()

    # ---------------- 预览 / 输出 ----------------
    def _render_one(self, entry):
        """
        渲染单个条目，返回 (文本, 缺失参数列表)。
        走 renderer.render_entry：参数表里的默认值会自动填进去，
        这样没单独填过的条目也能得到一份可直接粘的脚本。
        """
        values = self.values_by_uuid.get(entry.get("uuid"), {})
        text, missing, _specs = renderer.render_entry(entry, values, vendor=entry.get("vendor"))
        return text, missing

    def _build_text(self):
        """组装配置包全文（多条时每条前面加一行标题注释，方便在 Xshell 里定位）"""
        entries = self._checked_entries()
        texts, all_missing = [], []
        multi = len(entries) > 1
        for entry in entries:
            text, missing = self._render_one(entry)
            if multi:
                mark = renderer.comment_mark(entry.get("vendor"))
                title_line = "%s ---- %s ｜ %s ｜ %s" % (
                    mark, entry.get("title") or "",
                    dbmod.display_vendor(entry.get("vendor")), entry.get("category") or "-")
                text = title_line + "\n" + text
            texts.append(text)
            for name in missing:
                all_missing.append("%s → %s" % (entry.get("title") or "", name))

        separator = "\n\n" if self.chk_blank.isChecked() else "\n"
        body = separator.join(t for t in texts if t.strip())

        if self.chk_header.isChecked() and entries:
            # 头部以第一条选中条目为模板（多厂商混搭时以主条目为准）
            header = renderer.build_header(
                entry=entries[0],
                operator=self.ed_operator.text().strip(),
                scene=self.ed_scene.text().strip(),
                model=self.ed_model.text().strip(),
                extra="共 %d 条命令条目" % len(entries))
            return header + "\n\n" + body, all_missing, entries
        return body, all_missing, entries

    def _refresh_preview(self, *_args):
        text, missing, entries = self._build_text()
        self.txt_preview.setPlainText(text)
        self.lbl_count.setText("已选 %d 条" % len(entries))

        # 参数未填的条目标记出来，避免拿着 {{占位}} 就上设备
        pending = self._entries_with_pending_params(entries)
        count = renderer.count_effective_lines(text)
        if missing:
            set_state(self.lbl_status, "warn")
            self.lbl_status.setText("⚠ 有参数未填写：%s" % "；".join(missing[:5]))
        elif pending:
            set_state(self.lbl_status, "warn")
            self.lbl_status.setText("⚠ %d 条使用了默认值：%s（双击条目可单独填写）"
                                    % (len(pending), "、".join(pending[:3])))
        elif count == 0 and entries:
            set_state(self.lbl_status, "warn")
            self.lbl_status.setText("⚠ 勾选的条目全部是【Web 控制台操作路径】清单，"
                                    "打包结果不能直接执行，请按界面路径操作")
        else:
            set_state(self.lbl_status, "ok")
            self.lbl_status.setText("✓ 共 %d 个条目 / %d 条有效命令" % (len(entries), count))

    def _entries_with_pending_params(self, entries):
        """哪些条目还在用默认值（没被单独填过参数）"""
        result = []
        for entry in entries:
            if entry.get("uuid") in self.values_by_uuid:
                continue
            specs = renderer.merge_param_specs(self.db.load_params(entry),
                                               entry.get("commands") or "")
            if specs:
                result.append(entry.get("title") or entry.get("uuid"))
        return result

    def _copy(self):
        text, missing, entries = self._build_text()
        if not entries:
            QMessageBox.warning(self, "未选择条目", "请先勾选至少一条命令。")
            return
        if not text.strip():
            QMessageBox.warning(self, "内容为空", "勾选的条目没有可生成的命令。")
            return
        if not copy_to_clipboard(text):
            set_state(self.lbl_status, "err")
            self.lbl_status.setText("✘ 复制失败（剪贴板被占用），请手动选中右侧预览区内容复制。")
            return
        op = self.ed_operator.text().strip()
        if op:
            _LAST_OPERATOR["value"] = op
        count = renderer.count_effective_lines(text)
        set_state(self.lbl_status, "ok")
        self.lbl_status.setText("✓已复制配置包：%d 个条目 / %d 条有效命令 → 切到 Xshell 粘贴"
                                % (len(entries), count))

    def _export_txt(self):
        text, _missing, entries = self._build_text()
        if not entries:
            QMessageBox.warning(self, "未选择条目", "请先勾选至少一条命令。")
            return
        default = os.path.join(_base_dir(), "配置包_%s.txt"
                               % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        path, _ = QFileDialog.getSaveFileName(self, "导出配置包", default, "文本文件 (*.txt)")
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(text)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        set_state(self.lbl_status, "ok")
        self.lbl_status.setText("✓ 已导出：%s" % path)
        QMessageBox.information(self, "导出完成", "配置包已导出：\n%s" % path)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _vendor_color(vendor):
    """取厂商色块颜色（与主窗保持一致，按 slug 索引；传显示名也能识别）"""
    try:
        from ui_main import VENDOR_COLORS, DEFAULT_VENDOR_COLOR
        return VENDOR_COLORS.get(dbmod.normalize_vendor(vendor), DEFAULT_VENDOR_COLOR)
    except Exception:
        return "#6a7080"


def _base_dir():
    """db 同目录（导出文件默认落这里，跟着 U 盘走）"""
    try:
        from db import get_base_dir
        return get_base_dir()
    except Exception:
        return os.path.abspath(".")
