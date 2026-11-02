# -*- coding: utf-8 -*-
"""
ui_tools.py —— 工具箱 Tab（R 批 2026-11-02）

三个纯本地工具，一个外层 Tab（内部子 Tab，避免主窗 Tab 继续膨胀）：
    ① 子网掩码 / CIDR 计算器 —— IP+前缀 → 网络/广播/掩码/可用范围与数量，含常用前缀预设
    ② 进制转换器 —— 二 / 八 / 十 / 十六 互转（含前缀识别、二进制 4 位分组）
    ③ curl 生成器 —— 表单 → 可复制命令（POSIX / CMD / PowerShell 三套引号转义）

纪律：
    · 全部零网络、零执行：只算数、只拼字符串，命令由用户自己复制到终端执行；
    · 非法输入不静默：错误直接红字写在结果区上方（消息来自 tool_calc / tool_curl）；
    · UI 规范（AGENTS.md §6）：QSplitter 走 theme.GripSplitter + split_state 记忆，
      样式只走 theme.qss 既有 objectName，内联色只用 theme.py 常量。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QSpinBox, QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QCheckBox, QApplication, QTabWidget, QMenu,
)

import tool_calc
import tool_curl
from theme import (ACCENT, DANGER, TEXT_MUTED, GripSplitter, read_sizes,
                   mono_font)

PREFIX_PRESETS = ("/8", "/16", "/24", "/28", "/30")


def _copy(text, widget=None):
    QApplication.clipboard().setText(text or "")
    try:
        win = widget.window() if widget is not None else None
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage("已复制（%d 字符）" % len(text or ""), 2500)
    except Exception:
        pass


class _ErrLabel(QLabel):
    """统一错误/提示行：错误红、成功灰（颜色取 theme 常量）"""

    def __init__(self, parent=None):
        super(_ErrLabel, self).__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setObjectName("Hint")

    def error(self, text):
        self.setText("<span style='color:%s'><b>✗ %s</b></span>"
                     % (DANGER, text.replace("<", "&lt;")))

    def ok(self, text):
        self.setText("<span style='color:%s'>%s</span>"
                     % (TEXT_MUTED, text.replace("<", "&lt;")))

    def clear(self):
        self.setText("")


# ===========================================================================
# ① 子网 / CIDR 计算器
# ===========================================================================
class SubnetTab(QWidget):
    """IP + 前缀 → 子网信息（/0 /24 /31 /32 与非法输入均为显式行为）"""

    def __init__(self, parent=None):
        super(SubnetTab, self).__init__(parent)
        self._build()
        self.on_preset(24)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QLabel("<b>子网掩码 / CIDR 计算器</b> —— 输入 IP 与前缀，输出网络地址、广播地址、"
                      "掩码、可用主机范围与数量。支持 <code>192.168.1.130/26</code> 形式的 "
                      "CIDR 直填；<b>/31 按 RFC 3021 点对点处理，/32 为单主机</b>。")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        split = GripSplitter(Qt.Horizontal)

        # ---- 左：输入 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("IP / CIDR："), 0, 0)
        self.ed_ip = QLineEdit()
        self.ed_ip.setObjectName("SearchBox")
        self.ed_ip.setPlaceholderText("192.168.1.130 或 192.168.1.130/26")
        self.ed_ip.returnPressed.connect(self.on_calc)
        grid.addWidget(self.ed_ip, 0, 1, 1, 3)

        grid.addWidget(QLabel("前缀长度："), 1, 0)
        self.cmb_prefix = QComboBox()
        for i in range(33):
            self.cmb_prefix.addItem("/%d（%s）" % (i, tool_calc.prefix_to_mask(i)), i)
        self.cmb_prefix.setCurrentIndex(24)
        grid.addWidget(self.cmb_prefix, 1, 1)

        btn_calc = QPushButton("计算")
        btn_calc.setObjectName("Primary")
        btn_calc.setDefault(True)
        btn_calc.clicked.connect(self.on_calc)
        grid.addWidget(btn_calc, 1, 2)

        btn_clear = QPushButton("清空")
        btn_clear.setObjectName("Ghost")
        btn_clear.clicked.connect(self.on_clear)
        grid.addWidget(btn_clear, 1, 3)
        lv.addLayout(grid)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("常用前缀："))
        for p in PREFIX_PRESETS:
            btn = QPushButton(p)
            btn.setObjectName("Ghost")
            btn.setToolTip("设为 %s 并立即计算" % p)
            btn.clicked.connect(lambda _c=False, val=int(p[1:]): self.on_preset(val))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        lv.addLayout(preset_row)

        self.lbl_err = _ErrLabel()
        lv.addWidget(self.lbl_err)

        self.lbl_notes = QLabel("")
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.setTextFormat(Qt.RichText)
        self.lbl_notes.setObjectName("HintMuted")
        lv.addWidget(self.lbl_notes)
        lv.addStretch(1)

        btn_copy = QPushButton("复制结果（TSV）")
        btn_copy.setObjectName("Ghost")
        btn_copy.clicked.connect(self.on_copy)
        lv.addWidget(btn_copy)
        split.addWidget(left)

        # ---- 右：结果表 ----
        self.tbl = QTableWidget(0, 2)
        self.tbl.setHorizontalHeaderLabels(["项目", "值"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setWordWrap(True)
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        header = self.tbl.horizontalHeader()
        header.setHighlightSections(False)
        split.addWidget(self.tbl)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 620])
        self.split = split
        root.addWidget(split, 1)

    # ---- 动作 ----
    def on_preset(self, prefix):
        self.cmb_prefix.setCurrentIndex(int(prefix))
        if not self.ed_ip.text().strip():
            self.ed_ip.setText("192.168.1.0")
        self.on_calc()

    def on_clear(self):
        self.ed_ip.clear()
        self.tbl.setRowCount(0)
        self.lbl_err.clear()
        self.lbl_notes.clear()

    def on_calc(self):
        text = self.ed_ip.text().strip()
        if not text:
            self.tbl.setRowCount(0)
            self.lbl_err.error("请输入 IP 地址（如 192.168.1.130/24）")
            return
        try:
            info = tool_calc.subnet_info(text, self.cmb_prefix.currentData())
        except tool_calc.CalcError as exc:
            self.tbl.setRowCount(0)
            self.lbl_err.error(str(exc))
            self.lbl_notes.clear()
            return
        except Exception as exc:                    # 兜底：绝不让界面挂掉
            self.tbl.setRowCount(0)
            self.lbl_err.error("计算失败：%s" % exc)
            return
        self.lbl_err.ok("计算完成：%s" % info["input"])
        self._fill(info)

    def _fill(self, info):
        rows = [
            ("IP 地址", info["ip"]),
            ("前缀长度", "/%d（%s）" % (info["prefix"], info["netmask"])),
            ("子网掩码", info["netmask"]),
            ("反掩码（Wildcard）", info["wildcard"]),
            ("网络地址", info["network"]),
            ("广播地址", info["broadcast"]),
            ("可用地址范围", "%s ~ %s" % (info["first_host"], info["last_host"])),
            ("可用主机数", "{:,}".format(info["host_count"])),
            ("地址总数", "{:,}".format(info["total_addrs"])),
            ("CIDR 表示", info["cidr"]),
            ("IP 十进制整数", "{:,}".format(info["ip_int"])),
            ("掩码十进制整数", "{:,}".format(info["mask_int"])),
            ("地址分类", info["class"]),
            ("地址用途", info["scope"]),
            ("计数口径", info["usable_note"]),
        ]
        self.tbl.setRowCount(len(rows))
        for i, (key, value) in enumerate(rows):
            k = QTableWidgetItem(key)
            v = QTableWidgetItem(str(value))
            v.setFont(mono_font(10))
            self.tbl.setItem(i, 0, k)
            self.tbl.setItem(i, 1, v)
        self.tbl.resizeRowsToContents()
        self.lbl_notes.setText("<br>".join("· %s" % n for n in info["notes"]))

    def on_copy(self):
        if not self.tbl.rowCount():
            return
        lines = ["%s\t%s" % (self.tbl.item(i, 0).text(), self.tbl.item(i, 1).text())
                 for i in range(self.tbl.rowCount())]
        _copy("\n".join(lines), self)

    def split_state(self):
        try:
            return {"tools_subnet_split": list(self.split.sizes())}
        except Exception:
            return {}

    def restore_split_state(self, state):
        sizes = read_sizes(state, "tools_subnet_split", (320, 320))
        if sizes:
            self.split.setSizes(sizes)


# ===========================================================================
# ② 进制转换器
# ===========================================================================
class BaseConvertTab(QWidget):
    """二 / 八 / 十 / 十六 互转；输入即时转换，错误即时提示"""

    def __init__(self, parent=None):
        super(BaseConvertTab, self).__init__(parent)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QLabel("<b>进制转换器</b> —— 二 / 八 / 十 / 十六 互转。可带 <code>0x</code> / "
                      "<code>0b</code> / <code>0o</code> 前缀（与所选进制必须一致），上限 64 位；"
                      "不支持负号与分隔符（避免静默误解）。")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("数值："))
        self.ed_value = QLineEdit()
        self.ed_value.setObjectName("SearchBox")
        self.ed_value.setPlaceholderText("例如 255 / 0xFF / 0b1010 / 0o755")
        self.ed_value.textChanged.connect(self.on_convert)
        row.addWidget(self.ed_value, 1)
        row.addWidget(QLabel("源进制："))
        self.cmb_base = QComboBox()
        self.cmb_base.addItem("自动识别", 0)
        for base, label in ((2, "二进制"), (8, "八进制"), (10, "十进制"), (16, "十六进制")):
            self.cmb_base.addItem("%s（%d）" % (label, base), base)
        self.cmb_base.currentIndexChanged.connect(self.on_convert)
        row.addWidget(self.cmb_base)
        btn_copy = QPushButton("复制全部")
        btn_copy.setObjectName("Ghost")
        btn_copy.clicked.connect(self.on_copy)
        row.addWidget(btn_copy)
        root.addLayout(row)

        self.lbl_err = _ErrLabel()
        root.addWidget(self.lbl_err)

        self.tbl = QTableWidget(0, 2)
        self.tbl.setHorizontalHeaderLabels(["进制 / 视图", "结果"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setWordWrap(True)
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl.horizontalHeader().setHighlightSections(False)
        root.addWidget(self.tbl, 1)

        self.lbl_hint = QLabel("提示：二进制分组按 4 位从右向左切分，便于与掩码 / 十六进制逐位对照。")
        self.lbl_hint.setObjectName("HintMuted")
        self.lbl_hint.setWordWrap(True)
        root.addWidget(self.lbl_hint)

    def on_convert(self, *_args):
        text = self.ed_value.text().strip()
        if not text:
            self.tbl.setRowCount(0)
            self.lbl_err.clear()
            return
        base = self.cmb_base.currentData() or 0
        try:
            out = tool_calc.convert_auto(text) if base == 0 else tool_calc.convert(text, base)
        except tool_calc.CalcError as exc:
            self.tbl.setRowCount(0)
            self.lbl_err.error(str(exc))
            return
        except Exception as exc:
            self.tbl.setRowCount(0)
            self.lbl_err.error("转换失败：%s" % exc)
            return
        self.lbl_err.ok(out["input_note"])
        rows = [
            ("二进制（0b）", out["bin"]),
            ("二进制（4 位分组）", out["bin_grouped"]),
            ("八进制（0o）", out["oct"]),
            ("十进制", out["dec"]),
            ("十六进制（0x）", out["hex"]),
            ("有效位数", "%d bit" % out["bits"]),
        ]
        self.tbl.setRowCount(len(rows))
        for i, (key, value) in enumerate(rows):
            self.tbl.setItem(i, 0, QTableWidgetItem(key))
            item = QTableWidgetItem(str(value))
            item.setFont(mono_font(10))
            self.tbl.setItem(i, 1, item)
        self.tbl.resizeRowsToContents()

    def on_copy(self):
        lines = ["%s\t%s" % (self.tbl.item(i, 0).text(), self.tbl.item(i, 1).text())
                 for i in range(self.tbl.rowCount())]
        if lines:
            _copy("\n".join(lines), self)


# ===========================================================================
# ③ curl 生成器
# ===========================================================================
class CurlTab(QWidget):
    """表单 → curl 命令；三种 shell 引号转义一次生成，默认 Windows CMD"""

    def __init__(self, parent=None):
        super(CurlTab, self).__init__(parent)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QLabel("<b>curl 命令生成器</b> —— 填表生成可复制的 curl 命令。三种目标 shell 各自处理引号转义"
                      "（CMD 用双引号 + <code>\\\"</code>，PowerShell 用单引号并转义内层双引号，POSIX 用单引号 + "
                      "<code>'\\''</code>）。本工具<b>只生成不执行</b>。")
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        split = GripSplitter(Qt.Horizontal)

        # ---- 左：表单 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(4)

        lv.addWidget(QLabel("URL（必须带 http/https 协议头）："))
        self.ed_url = QLineEdit()
        self.ed_url.setObjectName("SearchBox")
        self.ed_url.setPlaceholderText("https" + "://api.example.com/v1/items?q=a&page=2")
        lv.addWidget(self.ed_url)

        row = QHBoxLayout()
        row.addWidget(QLabel("方法："))
        self.cmb_method = QComboBox()
        self.cmb_method.addItems(list(tool_curl.METHODS))
        self.cmb_method.setCurrentText("GET")
        row.addWidget(self.cmb_method)
        row.addWidget(QLabel("超时(s)："))
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(0, 3600)
        self.sp_timeout.setSpecialValueText("不限")
        self.sp_timeout.setValue(30)
        row.addWidget(self.sp_timeout)
        row.addWidget(QLabel("重试："))
        self.sp_retry = QSpinBox()
        self.sp_retry.setRange(0, 10)
        self.sp_retry.setValue(0)
        row.addWidget(self.sp_retry)
        lv.addLayout(row)

        lv.addWidget(QLabel("请求头（一行一个，名称: 值）："))
        self.ed_headers = QPlainTextEdit()
        self.ed_headers.setObjectName("CodeBlock")
        self.ed_headers.setFont(mono_font(9))
        self.ed_headers.setPlaceholderText("Content-Type: application/json\nAuthorization: Bearer <token>")
        self.ed_headers.setFixedHeight(76)
        lv.addWidget(self.ed_headers)

        lv.addWidget(QLabel("请求体（-d；JSON 请压成一行）："))
        self.ed_data = QPlainTextEdit()
        self.ed_data.setObjectName("CodeBlock")
        self.ed_data.setFont(mono_font(9))
        self.ed_data.setPlaceholderText('{"name":"demo"}')
        self.ed_data.setFixedHeight(76)
        lv.addWidget(self.ed_data)

        lv.addWidget(QLabel("--resolve（host:port:ip，一行一个，可留空）："))
        self.ed_resolve = QLineEdit()
        self.ed_resolve.setPlaceholderText("api.example.com:443:10.0.0.5")
        lv.addWidget(self.ed_resolve)

        opt_row = QHBoxLayout()
        self.chk_insecure = QCheckBox("-k 跳过证书校验")
        self.chk_location = QCheckBox("-L 跟随重定向")
        self.chk_silent = QCheckBox("-sS 静默但显示错误")
        for chk in (self.chk_insecure, self.chk_location, self.chk_silent):
            opt_row.addWidget(chk)
        opt_row.addStretch(1)
        lv.addLayout(opt_row)

        btn_row = QHBoxLayout()
        btn_gen = QPushButton("生成命令")
        btn_gen.setObjectName("Primary")
        btn_gen.setDefault(True)
        btn_gen.clicked.connect(self.on_generate)
        btn_row.addWidget(btn_gen)
        btn_demo = QPushButton("填入示例")
        btn_demo.setObjectName("Ghost")
        btn_demo.clicked.connect(self.fill_demo)
        btn_row.addWidget(btn_demo)
        btn_row.addStretch(1)
        lv.addLayout(btn_row)

        self.lbl_err = _ErrLabel()
        lv.addWidget(self.lbl_err)
        lv.addStretch(1)
        split.addWidget(left)

        # ---- 右：输出 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(4)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("目标 shell："))
        self.cmb_shell = QComboBox()
        for shell in ("cmd", "powershell", "posix"):
            self.cmb_shell.addItem(tool_curl.SHELL_LABELS[shell], shell)
        self.cmb_shell.currentIndexChanged.connect(self.on_generate)
        out_row.addWidget(self.cmb_shell, 1)
        btn_copy = QPushButton("复制命令")
        btn_copy.setObjectName("Primary")
        btn_copy.clicked.connect(self.on_copy)
        out_row.addWidget(btn_copy)
        rv.addLayout(out_row)

        self.txt_out = QPlainTextEdit()
        self.txt_out.setObjectName("CodeBlock")
        self.txt_out.setReadOnly(True)
        self.txt_out.setFont(mono_font(10))
        self.txt_out.setPlaceholderText("点「生成命令」后，这里显示可复制的命令行。")
        self.txt_out.setContextMenuPolicy(Qt.CustomContextMenu)
        self.txt_out.customContextMenuRequested.connect(self._out_menu)
        rv.addWidget(self.txt_out, 1)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setObjectName("HintMuted")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setTextFormat(Qt.RichText)
        rv.addWidget(self.lbl_warn)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([520, 640])
        self.split = split
        root.addWidget(split, 1)

    # ---- 动作 ----
    def fill_demo(self):
        self.ed_url.setText("https" + "://api.example.com/v1/items?q=中文&page=2")
        self.cmb_method.setCurrentText("POST")
        self.ed_headers.setPlainText("Content-Type: application/json\nAuthorization: Bearer TOKEN")
        self.ed_data.setPlainText('{"name":"张三","note":"a&b|c"}')
        self.ed_resolve.setText("api.example.com:443:10.0.0.5")
        self.chk_location.setChecked(True)
        self.chk_silent.setChecked(True)
        self.on_generate()

    def spec(self):
        return {
            "url": self.ed_url.text(),
            "method": self.cmb_method.currentText(),
            "headers": self.ed_headers.toPlainText(),
            "data": self.ed_data.toPlainText(),
            "timeout": self.sp_timeout.value() or None,
            "retries": self.sp_retry.value(),
            "resolve": self.ed_resolve.text(),
            "insecure": self.chk_insecure.isChecked(),
            "location": self.chk_location.isChecked(),
            "silent": self.chk_silent.isChecked(),
        }

    def on_generate(self, *_args):
        shell = self.cmb_shell.currentData() or "cmd"
        try:
            result = tool_curl.build_curl(self.spec(), shell)
        except tool_curl.CurlError as exc:
            self.txt_out.setPlainText("")
            self.lbl_warn.setText("")
            self.lbl_err.error(str(exc))
            return
        except Exception as exc:                # 兜底
            self.txt_out.setPlainText("")
            self.lbl_err.error("生成失败：%s" % exc)
            return
        self.txt_out.setPlainText(result["cmd"])
        self.lbl_err.ok("已生成 %s 命令（%d 字符）" % (result["shell_label"], len(result["cmd"])))
        warns = result["warnings"]
        self.lbl_warn.setText("<br>".join("· %s" % w for w in warns))

    def on_copy(self):
        text = self.txt_out.toPlainText()
        if text.strip():
            _copy(text, self)

    def _out_menu(self, pos):
        menu = QMenu(self)
        act_all = menu.addAction("复制全部")
        act_line = menu.addAction("复制当前行")
        chosen = menu.exec_(self.txt_out.mapToGlobal(pos))
        if chosen is act_all:
            self.on_copy()
        elif chosen is act_line:
            _copy(self.txt_out.textCursor().selectedText() or
                  self.txt_out.toPlainText().splitlines()[0], self)

    def split_state(self):
        try:
            return {"tools_curl_split": list(self.split.sizes())}
        except Exception:
            return {}

    def restore_split_state(self, state):
        sizes = read_sizes(state, "tools_curl_split", (380, 380))
        if sizes:
            self.split.setSizes(sizes)


# ===========================================================================
# 外层：工具箱
# ===========================================================================
class ToolsTab(QWidget):
    """子网计算器 / 进制转换器 / curl 生成器（内部子 Tab）"""

    def __init__(self, parent=None):
        super(ToolsTab, self).__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sub = QTabWidget()
        self.sub.setDocumentMode(True)
        self.tab_subnet = SubnetTab()
        self.tab_base = BaseConvertTab()
        self.tab_curl = CurlTab()
        self.sub.addTab(self.tab_subnet, "子网计算器")
        self.sub.addTab(self.tab_base, "进制转换")
        self.sub.addTab(self.tab_curl, "curl 生成器")
        lay.addWidget(self.sub)

    def split_state(self):
        payload = {}
        for tab in (self.tab_subnet, self.tab_curl):
            try:
                payload.update(tab.split_state())
            except Exception:
                pass
        return payload

    def restore_split_state(self, state):
        for tab in (self.tab_subnet, self.tab_curl):
            try:
                tab.restore_split_state(state)
            except Exception:
                pass
