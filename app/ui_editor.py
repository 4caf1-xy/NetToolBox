# -*- coding: utf-8 -*-
"""
ui_editor.py —— 命令库编辑器（离网积累的命脉）

两大块：
    1. EntryEditorDialog    单条条目的新建 / 编辑表单：
                            基本信息 + 命令全文（高亮） + 参数表（可视化增删改，免手写 JSON）
                            新条目强制 verified=0（灰色徽章"未验证"）
    2. LibraryEditorDialog  命令库管理器：
                            全部条目一览 + 新建/编辑/复制一份/删除/标记已验证/取消验证/
                            导入导出 .nlb + 条目演变历史（history 表）

设计原则：所有写操作都走 db.py，自动落 history 表；只读库（U 盘写保护）时禁用全部写操作。
"""

import os
import re
import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QLabel, QLineEdit, QComboBox, QPlainTextEdit, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
                             QFileDialog, QGroupBox, QSplitter, QScrollArea, QFrame,
                             QAbstractItemView)

import renderer
from ui_main import (CommandHighlighter, VERIFIED_COLOR, UNVERIFIED_COLOR,
                     VENDOR_COLORS, APP_NAME)
import db as dbmod
from db import ALL_CATEGORIES

# 常用取值（可编辑下拉框，允许现场自由填）
# ★ 厂商 / OS 的下拉候选从 db 的 slug 表统一取（显示名），保存时归一化成 slug
DEVICE_TYPES = ["交换机", "路由器", "防火墙", "无线AC", "负载均衡", "上网行为管理"]
PLATFORM_CHOICES = [("网络设备", "network"), ("Linux 服务器", "linux")]
CATEGORIES = list(ALL_CATEGORIES)
VENDORS = dbmod.vendor_candidates()      # 显示名列表（含网络设备与 Linux 发行版）
OS_FAMILIES = dbmod.os_candidates()

# 参数校验规则候选（可编辑，允许自定义）
VALIDATE_RULES = [
    "", "vlan", "ipv4", "masklen",
    "int:1-255", "int:1-4094", "int:0-65535",
    "port", "port_range", "ifname",
    "enum:enable|disable", "regex:",
]

# 参数表列定义
PARAM_COLUMNS = ["参数名", "显示名", "默认值", "必填", "校验规则", "示例", "端口展开"]


def mono_font(size=10):
    """等宽字体"""
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(size)
    return f


# ---------------------------------------------------------------------------
# 参数表（可视化编辑 params 字段，免手写 JSON）
# ---------------------------------------------------------------------------
from theme import repolish, set_state  # 状态标签 / error 属性的动态重polish
from theme import TEXT_SECONDARY  # C4 收敛散落色（审计 B18）

class ParamTableWidget(QWidget):
    """
    参数表：每行对应 params JSON 数组里的一个对象
        name / label / default / required / validate / example / expand
    提供"从命令自动提取参数"，省得手工对照占位名。
    """

    def __init__(self, parent=None):
        super(ParamTableWidget, self).__init__(parent)
        self._build()

    def _build(self):
        self.table = QTableWidget(0, len(PARAM_COLUMNS))
        self.table.setHorizontalHeaderLabels(PARAM_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(180)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)

        btn_extract = QPushButton("从命令提取参数")
        btn_extract.setObjectName("Ghost")
        btn_extract.setToolTip("扫描命令里的 {{参数}} 占位并自动补进表里（带 :默认值 的判为选填）")
        btn_extract.clicked.connect(self.extract_callback)

        btn_add = QPushButton("添加一行")
        btn_add.setObjectName("Ghost")
        btn_add.clicked.connect(lambda: self.add_row({}))

        btn_del = QPushButton("删除所选行")
        btn_del.setObjectName("Ghost")
        btn_del.clicked.connect(self.remove_selected)

        btn_up = QPushButton("上移")
        btn_up.setObjectName("Ghost")
        btn_up.clicked.connect(lambda: self.move_selected(-1))

        btn_down = QPushButton("下移")
        btn_down.setObjectName("Ghost")
        btn_down.clicked.connect(lambda: self.move_selected(1))

        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(btn_extract)
        tools.addWidget(btn_add)
        tools.addWidget(btn_del)
        tools.addStretch(1)
        tools.addWidget(btn_up)
        tools.addWidget(btn_down)

        self.lbl_hint = QLabel("参数表决定生成器里的表单字段；校验规则留空表示只做必填校验。")
        self.lbl_hint.setObjectName("Hint")
        self.lbl_hint.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        root.addWidget(self.table, 1)
        root.addLayout(tools)
        root.addWidget(self.lbl_hint)

    # 外部注入"提取参数"回调（由 EntryEditorDialog 提供，需要拿到命令文本）
    def set_extract_callback(self, func):
        self.extract_callback = func

    def extract_callback(self):
        """默认空实现，由外部覆盖"""
        pass

    # ---------------- 行操作 ----------------
    def add_row(self, spec):
        """新增一行参数"""
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(str(spec.get("name") or ""))
        name_item.setFont(mono_font(9))
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QTableWidgetItem(str(spec.get("label") or "")))
        default_item = QTableWidgetItem(str(spec.get("default") or ""))
        default_item.setFont(mono_font(9))
        self.table.setItem(row, 2, default_item)

        req_item = QTableWidgetItem("必填")
        req_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        req_item.setCheckState(Qt.Checked if spec.get("required") else Qt.Unchecked)
        req_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 3, req_item)

        rule = str(spec.get("validate") or "")
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(VALIDATE_RULES)
        combo.setCurrentText(rule)
        combo.setToolTip("vlan / ipv4 / masklen / int:1-255 / port / port_range / "
                         "enum:a|b / regex:^x$\n留空 = 只做必填校验")
        self.table.setCellWidget(row, 4, combo)

        self.table.setItem(row, 5, QTableWidgetItem(str(spec.get("example") or "")))

        expand_item = QTableWidgetItem("展开")
        expand_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        expanded = spec.get("expand") == "port" or rule == "port_range"
        expand_item.setCheckState(Qt.Checked if expanded else Qt.Unchecked)
        expand_item.setTextAlignment(Qt.AlignCenter)
        expand_item.setToolTip("勾选后，该参数按厂商接口命名规则展开成多口列表\n"
                               "（如 0/1-0/10 → 华为 Eth0/0/1…Eth0/0/10）")
        self.table.setItem(row, 6, expand_item)
        return row

    def remove_selected(self):
        """删除选中的行（倒序删，避免行号漂移）"""
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        if not rows:
            QMessageBox.information(self, "未选中行", "请先在参数表里点选要删除的行。")
            return
        for row in rows:
            self.table.removeRow(row)

    def move_selected(self, delta):
        """上移/下移选中的行"""
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()))
        if not rows:
            return
        row = rows[0]
        target = row + delta
        if target < 0 or target >= self.table.rowCount():
            return
        specs = self.params()
        specs[row], specs[target] = specs[target], specs[row]
        self.set_params(specs)
        self.table.selectRow(target)

    # ---------------- 读写 ----------------
    def params(self):
        """读成 params JSON 数组结构"""
        result = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue

            def _text(col):
                item = self.table.item(row, col)
                return item.text().strip() if item else ""

            req_item = self.table.item(row, 3)
            expand_item = self.table.item(row, 6)
            combo = self.table.cellWidget(row, 4)
            rule = combo.currentText().strip() if isinstance(combo, QComboBox) else ""

            spec = {
                "name": name,
                "label": _text(1) or name,
                "default": _text(2),
                "required": bool(req_item and req_item.checkState() == Qt.Checked),
                "validate": rule,
                "example": _text(5),
            }
            if expand_item and expand_item.checkState() == Qt.Checked:
                spec["expand"] = "port"
            result.append(spec)
        return result

    def set_params(self, specs):
        """整表覆盖写入"""
        self.table.setRowCount(0)
        for spec in specs or []:
            if isinstance(spec, dict):
                self.add_row(spec)

    def merge_extracted(self, specs):
        """把提取到的参数合并进来（已存在同名的不动），返回新增数量"""
        exist = set(p["name"] for p in self.params())
        added = 0
        for spec in specs:
            if spec["name"] in exist:
                continue
            self.add_row(spec)
            added += 1
        return added


# ---------------------------------------------------------------------------
# 单条条目编辑对话框
# ---------------------------------------------------------------------------
class EntryEditorDialog(QDialog):
    """
    新建 / 编辑一条命令条目。
        entry=None → 新建（保存后 verified 强制为 0，灰色徽章）
        entry=dict → 编辑（走 db.update_entry，自动写 history）
    """

    def __init__(self, db, entry=None, parent=None, operator=""):
        super(EntryEditorDialog, self).__init__(parent)
        self.db = db
        self.entry = entry
        self.is_new = entry is None
        self.operator = operator or ""

        self.setWindowTitle("新建命令条目" if self.is_new
                            else "编辑条目 · %s" % (entry.get("title") or ""))
        self.resize(1080, 860)
        self.setMinimumSize(900, 700)
        self._build()
        self._load()

    # ---------------- 构建 ----------------
    def _build(self):
        # ---- 基本信息 ----
        self.cmb_device = self._editable_combo(DEVICE_TYPES)
        self.cmb_platform = self._slug_combo(PLATFORM_CHOICES)
        self.cmb_vendor = self._slug_combo([(n, s) for s, n in dbmod.VENDOR_SLUGS.items()],
                                           normalizer=dbmod.normalize_vendor)
        self.cmb_os = self._slug_combo([(n, s) for s, n in dbmod.OS_SLUGS.items()],
                                       normalizer=dbmod.normalize_os)
        self.cmb_category = self._editable_combo(CATEGORIES)
        self.cmb_duration = self._slug_combo([("（不适用/留空）", ""),
                                             ("临时生效（重启失效）", "temp"),
                                             ("持久化配置", "perm"),
                                             ("临时 + 持久化", "both")])
        self.ed_models = QLineEdit()
        self.ed_models.setPlaceholderText("例如：S5720-28X-SI-AC / Catalyst 2960（可留空）")
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("标题（必填），例如：创建 VLAN 并把端口划入 Access")
        self.ed_desc = QLineEdit()
        self.ed_desc.setPlaceholderText("一句话描述这条命令干什么用")

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("平台 *", self.cmb_platform)
        form.addRow("设备类型", self.cmb_device)
        form.addRow("厂商 *", self.cmb_vendor)
        form.addRow("OS 版本族 *", self.cmb_os)
        form.addRow("场景分类 *", self.cmb_category)
        form.addRow("生效方式", self.cmb_duration)
        form.addRow("适用型号", self.ed_models)
        form.addRow("标题 *", self.ed_title)
        form.addRow("描述", self.ed_desc)

        self.lbl_platform_tip = QLabel(
            "厂商 / OS 下拉里存的是小写 slug（如 cisco / ios / kylin / kylinV10），"
            "界面显示中文名；手输显示名或 slug 都能识别。\n"
            "生效方式（duration）仅 Linux 条目需要：临时命令选 temp，配置文件选 perm。")
        self.lbl_platform_tip.setObjectName("Hint")
        self.lbl_platform_tip.setWordWrap(True)

        base_box = QGroupBox("基本信息")
        bb = QVBoxLayout(base_box)
        bb.setContentsMargins(10, 12, 10, 10)
        bb.setSpacing(4)
        bb.addLayout(form)
        bb.addWidget(self.lbl_platform_tip)

        # ---- 命令全文 ----
        self.txt_commands = QPlainTextEdit()
        self.txt_commands.setFont(mono_font(10))
        self.txt_commands.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_commands.setPlaceholderText(
            "在此粘贴/编写命令原文（一行一条）。\n"
            "需要用户填写的部分用双花括号占位，例如：\n"
            "  vlan {{vlan_id:10}}\n"
            "  interface {{port_range}}\n"
            "带冒号默认值（:10）的占位按「选填」提取，不带的按「必填」提取。")
        self.txt_commands.setObjectName("CodeBlock")
        self.txt_commands.setMinimumHeight(240)
        self.highlighter = CommandHighlighter(self.txt_commands.document())
        self.txt_commands.textChanged.connect(self._on_commands_changed)

        self.lbl_params_found = QLabel("")
        self.lbl_params_found.setObjectName("Hint")

        cmd_box = QGroupBox("命令全文（含 {{参数}} 占位）")
        cb = QVBoxLayout(cmd_box)
        cb.setContentsMargins(10, 12, 10, 10)
        cb.setSpacing(4)
        cb.addWidget(self.txt_commands, 1)
        cb.addWidget(self.lbl_params_found)

        # ---- 参数表 ----
        self.param_table = ParamTableWidget()
        self.param_table.set_extract_callback(self.extract_params)

        param_box = QGroupBox("参数定义（可视化编辑，不用手写 JSON）")
        pb = QVBoxLayout(param_box)
        pb.setContentsMargins(10, 12, 10, 10)
        pb.addWidget(self.param_table)

        # ---- 坑点备注 ----
        self.txt_notes = QPlainTextEdit()
        self.txt_notes.setMinimumHeight(90)
        self.txt_notes.setPlaceholderText(
            "现场踩过的坑写这里，例如：\n"
            "1) save 会二次确认，脚本里要跟一行 y\n"
            "2) 老版本不支持 interface range")
        notes_box = QGroupBox("坑点备注")
        nb = QVBoxLayout(notes_box)
        nb.setContentsMargins(10, 12, 10, 10)
        nb.addWidget(self.txt_notes)

        # ---- 操作人 / 验证状态 ----
        self.ed_operator = QLineEdit(self.operator)
        self.ed_operator.setPlaceholderText("操作人（写入 history，留痕用）")
        self.ed_operator.setMaximumWidth(180)

        if self.is_new:
            state_text = "新条目保存后一律为『未验证』（灰色徽章），真机验证通过后才能变绿。"
            state_key = "warn"
        elif int(self.entry.get("verified") or 0) == 1:
            state_text = ("当前状态：已验证（%s / %s / %s）"
                          % (self.entry.get("verified_by") or "-",
                             self.entry.get("verified_model") or "-",
                             self.entry.get("verified_date") or "-"))
            state_key = "ok"
        else:
            state_text = "当前状态：未验证（灰色徽章）。保存后仍保持未验证，需走团队验证流程。"
            state_key = "idle"
        self.lbl_state = QLabel(state_text)
        self.lbl_state.setObjectName("StateLabel")
        set_state(self.lbl_state, state_key)
        self.lbl_state.setWordWrap(True)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        foot.addWidget(QLabel("操作人："))
        foot.addWidget(self.ed_operator)
        foot.addWidget(self.lbl_state, 1)

        # ---- 按钮 ----
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(lambda: self._save(False))
        self.btn_save_new = QPushButton("保存并新建下一条")
        self.btn_save_new.setObjectName("Ghost")
        self.btn_save_new.clicked.connect(lambda: self._save(True))
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.btn_save)
        if self.is_new:
            buttons.addWidget(self.btn_save_new)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_cancel)

        split_top = QSplitter(Qt.Vertical)
        split_top.addWidget(cmd_box)
        split_top.addWidget(param_box)
        split_top.setStretchFactor(0, 5)
        split_top.setStretchFactor(1, 4)
        split_top.setSizes([420, 320])

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ★ 第 5 轮 UI 专项修复：四个区块的最小高度总和（表单 + 命令区 240 +
        #   参数表 180 + 备注 90）本来就超过对话框高度，布局被硬压时控件互相
        #   重叠（命令区盖住参数提示、按钮盖住表格行）。包一层 QScrollArea
        #   让最小尺寸得到尊重，空间不足时出滚动条，而不是互相叠压。
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)
        body_lay.addWidget(base_box)
        body_lay.addWidget(split_top, 1)
        body_lay.addWidget(notes_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)

        root.addWidget(scroll, 1)
        root.addLayout(foot)
        root.addLayout(buttons)

        if self.db is not None and self.db.readonly:
            for widget in (self.btn_save, self.btn_save_new):
                widget.setEnabled(False)
            self.lbl_state.setText("命令库处于只读状态（U 盘写保护），无法保存。")
            set_state(self.lbl_state, "err")

    @staticmethod
    def _editable_combo(items):
        cmb = QComboBox()
        cmb.setEditable(True)       # 允许现场填表里没有的取值
        cmb.addItems(items)
        cmb.setCurrentIndex(-1)
        cmb.setCurrentText("")
        return cmb

    @staticmethod
    def _slug_combo(pairs, normalizer=None):
        """
        (显示名, slug) 下拉：界面显示显示名，userData 存 slug。
        normalizer 用于 editable 模式下把用户手输的内容归一化成 slug。
        """
        cmb = QComboBox()
        cmb.setEditable(True)
        for name, slug in pairs:
            cmb.addItem(name, slug)
        cmb.setCurrentIndex(-1)
        cmb.setCurrentText("")
        if normalizer:
            cmb.normalizer = normalizer      # 挂在控件上，_slug_value 读取
        return cmb

    @staticmethod
    def _slug_value(cmb):
        """
        取下拉的 slug：优先用选项 userData；用户手输了列表外的内容时，
        用 normalizer 归一化（显示名/slug 都能识别），保证入库一定是 slug。
        """
        data = cmb.currentData()
        if data:
            return data
        text = cmb.currentText().strip()
        if not text:
            return ""
        normalizer = getattr(cmb, "normalizer", None)
        if normalizer:
            return normalizer(text)
        # 平台下拉：手输时按中文名匹配
        for name, slug in PLATFORM_CHOICES:
            if text == name or text == slug:
                return slug
        return ""

    def _set_slug_combo(self, cmb, slug):
        """把 slug 回填到下拉（显示对应显示名）"""
        idx = cmb.findData(slug)
        if idx >= 0:
            cmb.setCurrentIndex(idx)
        else:
            cmb.setCurrentText(slug or "")

    # ---------------- 载入 / 收集 ----------------
    def _load(self):
        """编辑模式：把已有数据铺进表单"""
        entry = self.entry
        if not entry:
            self._set_slug_combo(self.cmb_platform, "network")
            self._on_commands_changed()
            return
        self._set_slug_combo(self.cmb_platform, dbmod.normalize_platform(entry.get("platform")))
        self.cmb_device.setCurrentText(entry.get("device_type") or "")
        self._set_slug_combo(self.cmb_vendor, dbmod.normalize_vendor(entry.get("vendor")))
        self._set_slug_combo(self.cmb_os, dbmod.normalize_os(entry.get("os_family")))
        self.cmb_category.setCurrentText(entry.get("category") or "")
        self._set_slug_combo(self.cmb_duration, dbmod.normalize_duration(entry.get("duration")))
        self.ed_models.setText(entry.get("models") or "")
        self.ed_title.setText(entry.get("title") or "")
        self.ed_desc.setText(entry.get("description") or "")
        self.txt_commands.setPlainText(entry.get("commands") or "")
        self.param_table.set_params(self.db.load_params(entry) if self.db else [])
        self.txt_notes.setPlainText(entry.get("notes") or "")

    def _collect(self):
        """收集表单 → 条目 dict（厂商/OS/平台/生效方式统一转成 slug）"""
        return {
            "platform": self._slug_value(self.cmb_platform) or "network",
            "device_type": self.cmb_device.currentText().strip(),
            "vendor": self._slug_value(self.cmb_vendor),
            "os_family": self._slug_value(self.cmb_os),
            "category": self.cmb_category.currentText().strip(),
            "duration": self._slug_value(self.cmb_duration),
            "models": self.ed_models.text().strip(),
            "title": self.ed_title.text().strip(),
            "description": self.ed_desc.text().strip(),
            "commands": self.txt_commands.toPlainText(),
            "params": self.param_table.params(),
            "notes": self.txt_notes.toPlainText(),
        }

    # ---------------- 交互 ----------------
    def _on_commands_changed(self):
        """命令变化 → 显示检测到的占位数量"""
        text = self.txt_commands.toPlainText()
        found = renderer.extract_params(text)
        if found:
            self.lbl_params_found.setText(
                "检测到 %d 个参数占位：%s（点『从命令提取参数』自动补进下表）"
                % (len(found), "、".join(p["name"] for p in found)))
        else:
            self.lbl_params_found.setText("未检测到 {{参数}} 占位——固定命令可以直接保存。")

    def extract_params(self):
        """从命令里提取参数并合并进参数表（按参数名智能预置校验规则）"""
        text = self.txt_commands.toPlainText()
        found = renderer.extract_params(text)
        if not found:
            QMessageBox.information(self, "没有参数", "命令里没有 {{参数}} 占位。")
            return
        is_linux = (self._slug_value(self.cmb_platform) == "linux")
        specs = []
        for item in found:
            has_default = bool(item.get("default"))
            name = item["name"]
            if re_is_port_like(name):
                rule = "port_range"
            elif re_is_ifname_like(name):
                rule = "ifname"
            elif re_is_ip_like(name):
                rule = "ipv4"
            elif re_is_vlan_like(name):
                rule = "vlan"
            else:
                rule = ""
            specs.append({
                "name": name,
                "label": name,
                "default": item.get("default") or "",
                # 带 :默认值 的判为选填，不带的按必填处理（宁可多校验一次）
                "required": not has_default,
                "validate": rule,
                "example": "",
            })
        added = self.param_table.merge_extracted(specs)
        extra = ("\nLinux 条目的生效方式已选：%s"
                 % (dbmod.DURATIONS.get(self._slug_value(self.cmb_duration)) or "未选")
                 if is_linux else "")
        QMessageBox.information(
            self, "提取完成",
            "检测到 %d 个参数，新增 %d 行到参数表。\n\n"
            "已按参数名预置校验规则（端口/网卡/IP/VLAN），请逐个核对显示名与必填标记；"
            "端口类参数记得勾选『端口展开』。%s"
            % (len(found), added, extra))

    # ---------------- 保存 ----------------
    def _validate(self, data):
        """保存前校验，返回 (错误列表, 警告列表)"""
        errors = []
        if not data["title"]:
            errors.append("标题必填。")
        if not data["commands"].strip():
            errors.append("命令全文不能为空。")
        if not data["vendor"]:
            errors.append("厂商必填（决定端口命名规则与色块）。")
        if not data["os_family"]:
            errors.append("OS 版本族必填（左侧树与端口命名要用）。")
        if dbmod.normalize_platform(data["platform"]) == "network" and not data["device_type"]:
            errors.append("网络设备条目的『设备类型』必填（左侧树按它分交换机/路由器/防火墙）。")
        if data["duration"] and dbmod.normalize_platform(data["platform"]) != "linux":
            errors.append("『生效方式(duration)』只用于 Linux 条目，网络设备条目请留空。")

        # 参数名去重 / 合法性
        seen = set()
        for spec in data["params"]:
            name = spec["name"]
            if name in seen:
                errors.append("参数名重复：%s" % name)
            if not re_fullmatch_name(name):
                errors.append("参数名只能包含字母/数字/下划线：%s" % name)
            seen.add(name)

        # 命令里的占位 vs 参数表
        in_cmd = set(p["name"] for p in renderer.extract_params(data["commands"]))
        undefined = in_cmd - seen
        unused = seen - in_cmd
        warnings = []
        if undefined:
            warnings.append("命令里出现但参数表未定义：%s（生成时会保持 {{占位}} 原样）"
                            % "、".join(sorted(undefined)))
        if unused:
            warnings.append("参数表定义但命令里没用到：%s" % "、".join(sorted(unused)))
        # 厂商不在规范 slug 表里（现场自建厂商）：左侧树与厂商色块会退化成默认样式，
        # 三库按 slug 联动也匹配不上。只警告、不阻断 —— 现场确有必须自建的情况。
        vendor = (data.get("vendor") or "")
        if vendor and vendor not in dbmod.VENDOR_SLUGS:
            warnings.append(
                "厂商 %r 不在规范表内：左侧树与厂商色块会显示为默认样式，"
                "建议改用下拉里已有的厂商；确需自建请同步维护 db.VENDOR_SLUGS")
        return errors, warnings

    def _save(self, and_new):
        data = self._collect()
        errors, warnings = self._validate(data)

        if warnings:
            box = QMessageBox(self)
            box.setWindowTitle("请确认")
            box.setIcon(QMessageBox.Warning)
            box.setText("命令与参数表存在不一致：")
            box.setInformativeText("\n".join(warnings) + "\n\n是否仍然继续保存？")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec_() != QMessageBox.Yes:
                return

        if errors:
            QMessageBox.critical(self, "无法保存", "\n".join(errors))
            return

        operator = self.ed_operator.text().strip()
        try:
            if self.is_new:
                data["verified"] = 0
                data["favorite"] = 0
                self.db.add_entry(data, operator=operator)
            else:
                self.db.update_entry(self.entry["uuid"], data, operator=operator)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        if and_new:
            QMessageBox.information(self, "已保存", "「%s」已保存为未验证条目，可继续新建下一条。"
                                    % data["title"])
            self.entry = None
            self.is_new = True
            self.ed_title.clear()
            self.ed_desc.clear()
            self.txt_commands.clear()
            self.param_table.set_params([])
            self.txt_notes.clear()
            self.ed_title.setFocus()
            self.operator = operator
            self.ed_operator.setText(operator)
            return

        self.operator = operator
        self.result_operator = operator
        self.result_title = data["title"]
        self.accept()

    def saved_operator(self):
        """保存时填的操作人（主窗拿去复用）"""
        return getattr(self, "result_operator", self.ed_operator.text().strip())

    def saved_title(self):
        """本次保存的标题（用于在主列表里定位刚保存的条目）"""
        return getattr(self, "result_title", "")


# ---------------------------------------------------------------------------
# 命令库管理器（全部条目一览 + 增删改 + 历史）
# ---------------------------------------------------------------------------
class LibraryEditorDialog(QDialog):
    """
    命令库管理器：
        上方工具条：新建 / 编辑 / 复制一份 / 删除 / 标记已验证 / 取消验证 / 刷新 / 导入 / 导出
        中部表格：全部条目（厂商 / 标题 / 场景 / OS / 型号 / 状态 / 收藏 / 更新时间）
        下方：选中条目的修改历史（history 表）
    """

    def __init__(self, db, parent=None, operator=""):
        super(LibraryEditorDialog, self).__init__(parent)
        self.db = db
        self.operator = operator or ""
        self.entries = []
        self.setWindowTitle("命令库管理器")
        self.resize(1280, 820)
        self.setMinimumSize(1000, 660)
        self._build()
        self.refresh()

    # ---------------- 构建 ----------------
    def _build(self):
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("快速筛选：标题 / 厂商 / 场景 / 命令内容（输入即筛）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self.refresh)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        for text, slot, obj in (
                ("新建条目", self.on_new, "Primary"),
                ("编辑", self.on_edit, ""),
                ("复制一份", self.on_duplicate, ""),
                ("删除", self.on_delete, ""),
                ("标记已验证", self.on_verify, ""),
                ("取消验证", self.on_unverify, ""),
                ("刷新", self.refresh, "Ghost"),
                ("导入 .nlb", self.on_import, "Ghost"),
                ("导出 .nlb", self.on_export, "Ghost")):
            btn = QPushButton(text)
            if obj:
                btn.setObjectName(obj)
            btn.clicked.connect(slot)
            tools.addWidget(btn)
        tools.addStretch(1)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["厂商", "标题", "场景", "OS 版本", "适用型号", "状态", "类型", "更新时间", "UUID"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setColumnHidden(8, True)         # UUID 不显示（内部标识）
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(lambda _i: self.on_edit())

        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(["时间", "动作", "变更内容", "旧值", "操作人"])
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_history.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_history.setMaximumHeight(220)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StatusStrip")
        if self.db.readonly:
            self.lbl_status.setText("⚠ 命令库处于只读状态（U 盘写保护），新建/编辑/删除/标记验证均被禁用。")
            set_state(self.lbl_status, "err")

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("Ghost")
        self.btn_close.clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addWidget(self.lbl_status, 1)
        bottom.addWidget(self.btn_close)

        hist_box = QGroupBox("条目修改历史（history 表）")
        hb = QVBoxLayout(hist_box)
        hb.setContentsMargins(8, 10, 8, 8)
        hb.addWidget(self.tbl_history)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(self.ed_search)
        root.addLayout(tools)
        root.addWidget(self.table, 1)
        root.addWidget(hist_box)
        root.addLayout(bottom)

    # ---------------- 刷新 ----------------
    def refresh(self, *_args):
        """重新查询并填表；尽量保持原选中项"""
        keyword = self.ed_search.text().strip()
        self.entries = self.db.search(keyword=keyword)
        keep_uuid = self.current_uuid()

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for entry in self.entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            verified = int(entry.get("verified") or 0) == 1
            values = [
                dbmod.display_vendor(entry.get("vendor")),
                entry.get("title") or "",
                entry.get("category") or "-",
                dbmod.display_os(entry.get("os_family")),
                entry.get("models") or "-",
                "已验证" if verified else "未验证",
                entry.get("device_type") or dbmod.display_platform(entry.get("platform")),
                entry.get("updated_at") or "-",
                entry.get("uuid") or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setForeground(QColor(VENDOR_COLORS.get(
                        dbmod.normalize_vendor(entry.get("vendor")), TEXT_SECONDARY)))
                if col == 5:
                    item.setForeground(QColor(VERIFIED_COLOR if verified else UNVERIFIED_COLOR))
                if col == 8:
                    item.setData(Qt.UserRole, entry.get("uuid"))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        self.lbl_status.setText("共 %d 条%s" % (len(self.entries),
                                             "（只读模式，写操作已禁用）" if self.db.readonly else ""))

        # 恢复选中
        target = 0
        for row, entry in enumerate(self.entries):
            if entry.get("uuid") == keep_uuid:
                target = row
                break
        if self.entries:
            self.table.selectRow(target)
        else:
            self.tbl_history.setRowCount(0)

    def current_uuid(self):
        """当前选中行的 UUID"""
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 8)
        return item.text() if item else None

    def current_entry(self):
        """当前选中行的条目 dict"""
        entry_uuid = self.current_uuid()
        if not entry_uuid:
            return None
        return self.db.get_entry(entry_uuid)

    def _on_selection(self):
        """选中行变化 → 刷新下方历史"""
        entry_uuid = self.current_uuid()
        self.tbl_history.setRowCount(0)
        if not entry_uuid:
            return
        action_map = {"create": "新建", "update": "修改", "verify": "标记已验证",
                      "unverify": "取消验证", "favorite": "收藏变更",
                      "delete": "删除", "import": "导入种子库"}
        for rec in self.db.get_history(entry_uuid, limit=100):
            row = self.tbl_history.rowCount()
            self.tbl_history.insertRow(row)
            values = [rec.get("ts", ""),
                      action_map.get(rec.get("action"), rec.get("action", "")),
                      rec.get("new_value", ""),
                      rec.get("old_value", ""),
                      rec.get("operator", "") or "—"]
            for col, value in enumerate(values):
                self.tbl_history.setItem(row, col, QTableWidgetItem(str(value)))
        self.tbl_history.resizeColumnsToContents()
        self.tbl_history.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

    # ---------------- 增删改 ----------------
    def _guard_readonly(self):
        """只读库拦截，返回 True 表示已拦截"""
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式",
                                "命令库处于只读状态（U 盘写保护），无法执行写操作。")
            return True
        return False

    def on_new(self):
        """新建条目"""
        if self._guard_readonly():
            return
        dlg = EntryEditorDialog(self.db, None, self, self.operator)
        if dlg.exec_() == QDialog.Accepted:
            self.operator = dlg.saved_operator() or self.operator
            self.refresh()
            self._select_by_title(dlg.saved_title())

    def on_edit(self):
        """编辑当前条目"""
        if self._guard_readonly():
            return
        entry = self.current_entry()
        if not entry:
            QMessageBox.information(self, "未选中", "请先在列表里选中一条条目。")
            return
        dlg = EntryEditorDialog(self.db, entry, self, self.operator)
        if dlg.exec_() == QDialog.Accepted:
            self.operator = dlg.saved_operator() or self.operator
            self.refresh()

    def on_duplicate(self):
        """复制一份（同厂商同场景改参数时最省事）"""
        if self._guard_readonly():
            return
        entry = self.current_entry()
        if not entry:
            QMessageBox.information(self, "未选中", "请先选中一条条目。")
            return
        raw = dict(entry)
        raw["uuid"] = ""                       # 清空 UUID → db 会生成新的
        raw["title"] = (entry.get("title") or "") + " - 副本"
        raw["verified"] = 0
        raw["favorite"] = 0
        try:
            self.db.add_entry(raw, operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "复制失败", str(exc))
            return
        self.refresh()
        self.lbl_status.setText("已复制一份（新条目为未验证状态）")

    def on_delete(self):
        """删除条目（二次确认，history 保留可追溯）"""
        if self._guard_readonly():
            return
        entry = self.current_entry()
        if not entry:
            QMessageBox.information(self, "未选中", "请先选中一条条目。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setIcon(QMessageBox.Warning)
        box.setText("确认删除条目「%s」？" % (entry.get("title") or ""))
        box.setInformativeText("删除后条目内容不可恢复（history 表会保留这条删除记录）。\n"
                               "如果只是临时不用，建议加入收藏或导出备份后再删。")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec_() != QMessageBox.Yes:
            return
        try:
            self.db.delete_entry(entry["uuid"], operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.refresh()
        self.lbl_status.setText("已删除「%s」" % (entry.get("title") or ""))

    def on_verify(self):
        """标记已验证：填验证人 / 设备型号 / 日期"""
        if self._guard_readonly():
            return
        entry = self.current_entry()
        if not entry:
            QMessageBox.information(self, "未选中", "请先选中一条条目。")
            return
        if int(entry.get("verified") or 0) == 1:
            QMessageBox.information(self, "已是已验证", "该条目已经是绿色徽章，无需重复标记。")
            return
        # 复用主窗的验证对话框（同一套留痕字段）
        try:
            from ui_main import VerifyDialog
        except Exception:
            VerifyDialog = None
        if VerifyDialog is None:
            QMessageBox.warning(self, "不可用", "验证对话框加载失败。")
            return
        dlg = VerifyDialog(entry, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        by, model, date = dlg.values()
        try:
            self.db.mark_verified(entry["uuid"], by, model, date, operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "标记失败", str(exc))
            return
        self.refresh()
        self.lbl_status.setText("已标记已验证：%s / %s / %s" % (by, model, date))

    def on_unverify(self):
        """取消验证 → 退回灰色"""
        if self._guard_readonly():
            return
        entry = self.current_entry()
        if not entry:
            QMessageBox.information(self, "未选中", "请先选中一条条目。")
            return
        if int(entry.get("verified") or 0) != 1:
            QMessageBox.information(self, "未验证", "该条目本来就不是已验证状态。")
            return
        if QMessageBox.question(self, "取消验证",
                                "确认把「%s」退回『未验证』灰色状态？"
                                % (entry.get("title") or ""),
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.db.mark_verified(entry["uuid"], "", "", unverify=True, operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "操作失败", str(exc))
            return
        self.refresh()
        self.lbl_status.setText("已取消验证，徽章恢复灰色。")

    # ---------------- 导入导出 ----------------
    def on_import(self):
        """导入 .nlb：按 UUID 合并，冲突保留已验证那条"""
        if self._guard_readonly():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 .nlb 命令库文件",
                                              os.path.dirname(self.db.db_path),
                                              "命令库文件 (*.nlb *.json)")
        if not path:
            return
        try:
            added, updated, skipped = self.db.import_nlb(path, merge=True)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", "文件解析失败：\n%s" % exc)
            return
        self.refresh()
        QMessageBox.information(self, "导入完成",
                                "新增 %d 条，更新 %d 条，跳过 %d 条。\n\n"
                                "（同 UUID 冲突时保留『已验证』的那一条）" % (added, updated, skipped))

    def on_export(self):
        """导出整个库为 .nlb"""
        default = os.path.join(os.path.dirname(self.db.db_path),
                               "%s_%s.nlb" % (APP_NAME, datetime.datetime.now().strftime("%Y%m%d")))
        path, _ = QFileDialog.getSaveFileName(self, "导出整个命令库", default, "命令库文件 (*.nlb)")
        if not path:
            return
        if not path.lower().endswith(".nlb"):
            path += ".nlb"
        try:
            count = self.db.export_nlb(path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", "已导出 %d 条到：\n%s" % (count, path))

    def _select_by_title(self, title):
        """按标题定位（新建后回到列表用）"""
        if not title:
            return
        for row, entry in enumerate(self.entries):
            if entry.get("title") == title:
                self.table.selectRow(row)
                return


# ---------------------------------------------------------------------------
# 参数名合法性（与 renderer.PARAM_RE 保持一致）
# ---------------------------------------------------------------------------
def re_fullmatch_name(name):
    """参数名只允许中英文/数字/下划线/点/连字符"""
    return bool(re.fullmatch(r"[A-Za-z0-9_\-\.\u4e00-\u9fa5]+", name or ""))


def _name_parts(name):
    """把参数名拆成小写词元，便于按词匹配（避免 'description' 被误判成 IP）"""
    return (name or "").lower().replace("-", "_").replace(".", "_").split("_")


def re_is_port_like(name):
    """参数名长得像端口（port/interface/端口/接口），自动预置 port_range 校验"""
    text = (name or "").lower()
    if any(k in text for k in ("端口", "接口")):
        return True
    return any(p in ("port", "ports", "interface", "intf", "intfs") for p in _name_parts(name))


def re_is_ifname_like(name):
    """参数名长得像 Linux 网卡（ifname/nic/网卡/dev），预置 ifname 校验"""
    text = (name or "").lower()
    if any(k in text for k in ("网卡", "网口")):
        return True
    return any(p in ("ifname", "nic", "dev", "devname", "ethname") for p in _name_parts(name))


def re_is_ip_like(name):
    """参数名长得像 IP（ip/addr/gateway/next_hop/dns），预置 ipv4 校验"""
    return any(p in ("ip", "ipv4", "addr", "address", "gateway", "gw", "dns",
                     "nexthop", "hop") for p in _name_parts(name))


def re_is_vlan_like(name):
    """参数名长得像 VLAN ID，预置 vlan 校验"""
    parts = _name_parts(name)
    return "vlan" in parts and ("id" in parts or "vlanid" in parts)
