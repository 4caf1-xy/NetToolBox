# -*- coding: utf-8 -*-
"""
ui_main.py —— 主窗口

布局：
    ┌──────────────────────────────────────────────────────────────┐
    │ 菜单栏：文件 / 命令 / 视图 / 帮助                            │
    ├────────────┬─────────────────────────────────────────────────┤
    │ 左侧树导航 │ 搜索框 + 过滤条 + 操作行                        │
    │ 设备类型   ├─────────────────────────────────────────────────┤
    │  └ 厂商    │ 条目列表（厂商色块/标题/场景标签/验证徽章）      │
    │     └ OS   ├─────────────────────────────────────────────────┤
    │ 底部统计   │ 详情面板：命令全文 / 参数表 / 备注 / 验证 / 历史 │
    └────────────┴─────────────────────────────────────────────────┘

复制三模式（与 Xshell 配合的核心能力）：
    复制全部      → 整块脚本（含注释）进剪贴板
    仅复制命令    → 剔除 ! # 注释行后的纯命令
    逐条复制      → 逐条高亮 + 回车下一条，排查单条命令时精确定位

快捷键：Ctrl+F 聚焦搜索 / Ctrl+C 复制选中命令块 / Ctrl+D 收藏切换
"""

import os
import html
import datetime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import (QColor, QFont, QKeySequence, QSyntaxHighlighter,
                         QTextCharFormat, QTextCursor, QPainter, QPalette)
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QSplitter, QLineEdit, QComboBox, QCheckBox, QPushButton,
                             QListWidget, QListWidgetItem, QLabel, QPlainTextEdit,
                             QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
                             QTabWidget, QMessageBox, QFileDialog, QDialog, QFormLayout,
                             QDialogButtonBox, QAction, QShortcut, QMenu,
                             QHeaderView, QAbstractItemView, QTextEdit, QSizePolicy,
                             QStackedWidget, QToolButton, QFrame)

import renderer
import db as dbmod
from db import (get_base_dir, display_vendor, display_os, display_platform,
                PLACEHOLDER_VENDORS)

# ---------------------------------------------------------------------------
# 视觉设计系统（第 5 轮 UI 专项）
#   全部样式令牌 / QSS / 公共组件统一来自 theme.py。
#   ★ 本文件不再自带颜色字面量：改样式请改 theme.py，避免两处不一致。
#   ★ 下列名字同时 re-export，ui_editor / ui_generator / ui_troubleshoot
#     继续 from ui_main import VENDOR_COLORS 不受影响。
# ---------------------------------------------------------------------------
from theme import (DARK_QSS,                                   # 全站唯一样式表（main.py 引用）
                   VENDOR_COLORS, DEFAULT_VENDOR_COLOR, PLATFORM_COLORS,
                   VERIFIED_COLOR, UNVERIFIED_COLOR, FAVORITE_COLOR,
                   SEL_BG, ACCENT, SUCCESS, WARNING, DANGER,
                   CODE_IP_COLOR, CODE_IFACE_COLOR,
                   BG_WINDOW, BG_PANEL, BG_RAISED, BORDER, BORDER_HOVER,
                   TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
                   SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL,
                   GripSplitter, ThemedComboBox, mono_font,
                   load_ui_state, save_ui_state, read_sizes, set_state)

# 布局尺寸约定（第 5 轮 UI 专项：三栏 pane 的初始/最小尺寸集中在此便于调整）
PANE_MIN_NAV = 180          # 左导航最小宽（任务要求 ≥180）
PANE_MIN_LIST = 300         # 命令列表最小宽（≥300）
PANE_MIN_DETAIL = 360       # 详情面板最小宽（≥360）
PANE_INIT_NAV = 220         # 左导航初始宽
PANE_MIN_LIST_H = 150       # 列表区最小高（上下分栏）
PANE_MIN_DETAIL_H = 260     # 详情区最小高

# ---------------------------------------------------------------------------
# 厂商配色（左侧色块 + 列表色块，便于一眼区分厂商）
# ★ 按 vendor slug 索引（库里存 slug，界面显示中文/品牌名）
# ---------------------------------------------------------------------------
# 厂商配色 / 平台配色 / 状态色 / 产品名 均由 theme.py 统一提供（见上方 import）。
# 下面只保留与"数据"相关、不属于主题的常量。

# 产品名（模块 06 规格：exe 名 NetToolBox，中文标题"离网网络运维工具箱"）
APP_NAME = "NetToolBox"
APP_TITLE = "离网网络运维工具箱"
APP_SUBTITLE = "网络设备 + Linux 命令库 / 排查向导 / 报错诊断"
# 产品版本（semver；发版时与 tag/Release/CHANGELOG 三处对齐，标题栏与关于对话框均引用此常量）
APP_VERSION = "0.4.0"


def copy_to_clipboard(text):
    """
    统一的剪贴板写入（第 4 轮 P2-4）。

    此前全项目 12 处直接调 QApplication.clipboard().setText(...)，**无一处包异常**。
    剪贴板被其它程序独占时可能抛错，而这些调用点大多在按钮槽函数里 ——
    一次失败会中断后续状态更新（"✓已复制"提示、逐条下标推进、_LAST_OPERATOR 记录）。
    这里统一兜底，返回是否成功，由调用方决定怎么提示用户。

    ★ 放在 ui_main 而不是各自模块：所有 UI 模块都已经从 ui_main 导入东西
      （CommandHighlighter / VerifyDialog / VERIFIED_COLOR 等），不会引入循环依赖。
    """
    try:
        QApplication.clipboard().setText(text if text is not None else "")
        return True
    except Exception:
        return False


def guard_skeleton_copy(parent, entry):
    """
    骨架条目复制前的一次性提醒（审计 P1）：
    exec_level=skeleton 的条目首次复制时弹窗提示"待真机核对，复制执行前须验证"，
    确认后把 skeleton_copy_warned 写进 ui_state.json，之后不再打扰。

    ★ 放在模块级：详情页（MainWindow）与参数化生成器（GeneratorDialog）的复制
      按钮共用；一次性状态借宿主窗的 _ui_state（生成器的 parent 就是主窗），
      与窗口尺寸记忆走同一个 ui_state.json，不新增状态文件。
    返回 True 表示允许继续复制。
    """
    if not entry or str(entry.get("exec_level") or "").strip() != "skeleton":
        return True
    main = parent
    state = getattr(main, "_ui_state", None)
    if state is None:            # 找不到主窗状态（如独立测试调用）→ 不弹，放行
        return True
    if state.get("skeleton_copy_warned"):
        return True
    QMessageBox.information(
        main, "骨架条目提醒",
        "⚠ 本条目为 CLI 骨架——待真机核对：\n\n"
        "命令行未在本厂商真机验证过，复制执行前须先核对命令可用性\n"
        "（可先敲 ? 确认，或改用条目内的 Web 控制台路径操作）。\n\n"
        "本提醒只出现一次；如需再次查看，见条目标题旁的橙色『骨架』徽章。")
    state["skeleton_copy_warned"] = True
    if hasattr(main, "_save_ui_state"):
        main._save_ui_state()
    return True


def build_text_card(text, tone="info"):
    """
    把纯文本包成"信息卡片"HTML（左侧竖条 + 行距 1.5），用于坑点备注/验证信息页。

    为什么用 HTML 而不是 QSS：QTextEdit 的 line-height 只能靠 HTML 内联样式控制。
    tone 决定左侧竖条颜色：info(accent) / warn(warning) / ok(success) / plain(灰)。
    """
    bar = {"info": ACCENT, "warn": WARNING, "ok": SUCCESS, "plain": BORDER_HOVER}.get(tone, ACCENT)
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = safe.replace("\n", "<br/>")
    return (
        "<table cellpadding='0' cellspacing='0' width='100%%'>"
        "<tr>"
        "<td style='background-color:%s; width:3px;'></td>"
        "<td style='padding:2px 0 2px 12px; line-height:150%%; color:%s;'>%s</td>"
        "</tr></table>" % (bar, TEXT_PRIMARY, body)
    )


def set_rich_text(edit, text):
    """给 QTextEdit 填纯文本（自动转义 + 行距 1.5），替代裸 setPlainText"""
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    edit.setHtml("<div style='line-height:150%%; color:%s;'>%s</div>"
                 % (TEXT_PRIMARY, safe.replace("\n", "<br/>")))

# 深色主题 QSS 已收口到 theme.py（DARK_QSS 由上方 import 引入），
# 本文件不再自带样式表定义。


# ---------------------------------------------------------------------------
# 命令语法高亮
# ---------------------------------------------------------------------------
class CommandHighlighter(QSyntaxHighlighter):
    """
    命令语法高亮（第 5 轮 P-G：四色分级，主次分明）

        注释行（# / ! 开头）  → muted 灰 + 斜体      —— 退到背景里
        命令关键字            → accent 蓝 + 粗体      —— 一眼抓住"这条命令干什么"
        {{参数}} 占位         → warning 黄 + 粗体     —— 提示"这里要填东西"
        其余正文              → primary 亮白          —— 命令主体
        IP 地址 / 接口名      → 低饱和青              —— 具体取值，弱于关键字

    设计取舍：全部染蓝（改造前）等于没分级；现在"关键字蓝 / 参数黄 / 注释灰"，
    扫一眼就知道哪条要改、哪行是注释、哪里有占位待填。
    """

    KEYWORDS = [
        # 通用
        "enable", "configure", "terminal", "exit", "end", "quit", "undo", "no",
        "write", "memory", "copy", "running-config", "startup-config", "save", "force",
        "display", "show", "set", "edit", "commit", "system-view", "return", "reset",
        # 二层
        "vlan", "interface", "port", "switchport", "mode", "access", "trunk", "hybrid",
        "allowed", "allow-pass", "pvid", "link-type", "shutdown", "description", "range",
        "port-group", "stp", "lldp", "aggregation",
        # 三层
        "ip", "route", "route-static", "static", "address", "mask", "preference",
        "ospf", "area", "network", "bgp", "peer", "redistribute", "cost", "priority",
        "vrrp", "dhcp", "nat", "pat", "acl", "permit", "deny", "rule", "deny-rule",
        # 管理面
        "ssh", "user", "username", "password", "secret", "privilege", "level",
        "snmp-server", "snmp-agent", "snmp", "sysname", "hostname", "clock", "logging",
        "info-center", "telnet", "aaa", "local-user", "service-type", "authentication",
        # 防火墙
        "policy", "security", "zone", "address-object", "service", "nat-policy",
        "config", "firewall", "objects", "srcintf", "dstintf", "srcaddr", "dstaddr",
        "action", "next", "unset", "get", "get-config",
    ]

    def __init__(self, parent=None):
        super(CommandHighlighter, self).__init__(parent)
        # 规则格式：(正则, 格式, 是否整个匹配串都上色)
        #   group=True  关键字类，正则带一个捕获组，只给捕获组上色
        #   group=False {{参数}} 类，整个 {{xxx}} 一起上色更直观
        self._rules = []

        # 命令关键字（accent 蓝 + 粗体）
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(ACCENT))
        kw_fmt.setFontWeight(QFont.Bold)
        self._rules.append((self._compile(self.KEYWORDS), kw_fmt, True))

        # {{参数}}（warning 黄 + 粗体）
        param_fmt = QTextCharFormat()
        param_fmt.setForeground(QColor(WARNING))
        param_fmt.setFontWeight(QFont.Bold)
        self._rules.append((renderer.PARAM_RE, param_fmt, False))

        # 注释行（muted 灰 + 斜体 + 小字 —— schema 升级 2026-09-30：种子约定
        # "# 注释行独立成行"，渲染上进一步退到背景里，复制时可经 strip_comments 剔除）
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor(TEXT_MUTED))
        comment_fmt.setFontItalic(True)
        comment_fmt.setFontPointSize(9)
        self._comment_re = self._compile_comment()
        self._comment_fmt = comment_fmt

        # IPv4 地址（低饱和紫，弱于关键字）
        ip_fmt = QTextCharFormat()
        ip_fmt.setForeground(QColor(CODE_IP_COLOR))
        self._rules.append((self._compile_ip(), ip_fmt, False))

        # 接口名 / 端口号（低饱和青，弱于关键字）
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(CODE_IFACE_COLOR))
        self._rules.append((self._compile_if(), num_fmt, False))

    # ---- 正则编译 ----
    @staticmethod
    def _compile(words):
        r"""按整词匹配编译关键字正则（用 (?<![\w-]) 兼容 system-view 这类带连字符的命令）"""
        import re
        escaped = sorted([re.escape(w) for w in words], key=len, reverse=True)
        return re.compile(r"(?<![\w\-])(" + "|".join(escaped) + r")(?![\w\-])", re.IGNORECASE)

    @staticmethod
    def _compile_comment():
        import re
        return re.compile(r"^\s*[!#].*$")

    @staticmethod
    def _compile_ip():
        import re
        return re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

    @staticmethod
    def _compile_if():
        import re
        return re.compile(r"(?<![\w\-])(?:Gi|Te|Fa|Eth|GE|XGE|ge-|xe-|et-|ethernet|port|gei_)"
                          r"[0-9/\.\-]+", re.IGNORECASE)

    def highlightBlock(self, text):
        """逐行高亮"""
        for pattern, fmt, use_group in self._rules:
            for match in pattern.finditer(text):
                if use_group and match.groups():
                    start, end = match.start(1), match.end(1)
                else:
                    start, end = match.start(), match.end()
                self.setFormat(start, end - start, fmt)
        comment = self._comment_re.match(text or "")
        if comment:
            self.setFormat(0, len(text), self._comment_fmt)


# ---------------------------------------------------------------------------
# 条目列表卡片（厂商色块 + 标题 + 场景标签 + 验证徽章）
# ---------------------------------------------------------------------------
class EntryCard(QWidget):
    """
    列表里的一行条目卡片（第 5 轮 P-B：圆角卡片式，去掉粗蓝条）

    结构：
        [厂商色块] 标题(收藏★) ──────────── [场景标签] [验证徽章]   ← 右对齐
                   平台 · OS 版本 · 型号 · 生效方式

    背景 / 圆角 / hover / 选中态由 QSS 的 QListWidget::item 统一负责
    （选中 = accent 12% 底 + 左侧 3px accent 竖条），卡片内部部件只挂
    objectName 引主题，不再逐个内联写颜色 —— 厂商色块例外，它随数据变。
    """

    def __init__(self, entry, ai_imported=False, parent=None):
        super(EntryCard, self).__init__(parent)
        self._ai_imported = bool(ai_imported)
        self.entry = entry
        self.setAutoFillBackground(False)

        vendor_slug = dbmod.normalize_vendor(entry.get("vendor"))
        vendor_name = display_vendor(vendor_slug)
        color = VENDOR_COLORS.get(vendor_slug, DEFAULT_VENDOR_COLOR)

        # 厂商色块（颜色按 slug 取，属数据驱动 → 唯一保留内联背景色的部件）
        block = QLabel(vendor_name[:6])
        block.setObjectName("EntryVendor")
        block.setFixedSize(60, 22)
        block.setAlignment(Qt.AlignCenter)
        block.setToolTip("厂商：%s（%s）" % (vendor_name, vendor_slug or "-"))
        block.setStyleSheet("background-color:%s;" % color)

        # 标题（收藏加星；颜色交给 QSS 的属性选择器 [fav="true"]）
        favorite = bool(entry.get("favorite"))
        title = QLabel(("★ " if favorite else "") + ("🤖 " if self._ai_imported else "")
                       + (entry.get("title") or "(无标题)"))
        title.setObjectName("EntryTitle")
        title.setProperty("fav", "true" if favorite else "false")
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        title.setToolTip(entry.get("title") or "")

        # 场景标签（统一最小宽度，避免长短不一导致右侧徽章参差）
        tag = QLabel(entry.get("category") or "未分类")
        tag.setObjectName("EntryTag")
        tag.setAlignment(Qt.AlignCenter)
        tag.setMinimumWidth(58)

        # 验证徽章（右对齐 + 固定尺寸，保证所有条目徽章严格对齐）
        verified = int(entry.get("verified") or 0) == 1
        badge = QLabel("已验证" if verified else "未验证")
        badge.setObjectName("EntryBadge")
        badge.setProperty("state", "verified" if verified else "unverified")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(60, 20)

        # 骨架徽章（审计 P1：exec_level=skeleton 的待真机核对条目，插入在验证徽章左侧）
        self.is_skeleton = str(entry.get("exec_level") or "").strip() == "skeleton"
        if self.is_skeleton:
            sk = QLabel("骨架")
            sk.setObjectName("EntryBadge")
            sk.setProperty("state", "skeleton")
            sk.setAlignment(Qt.AlignCenter)
            sk.setFixedSize(44, 20)
            sk.setToolTip("CLI 骨架——待真机核对，复制执行前须验证")
            badge_stack = QHBoxLayout()
            badge_stack.setContentsMargins(0, 0, 0, 0)
            badge_stack.setSpacing(4)
            badge_stack.addWidget(sk)
            badge_stack.addWidget(badge)

        # 副标题：平台/设备形态 · OS 版本 · 型号（Linux 条目额外显示 duration）
        if dbmod.normalize_platform(entry.get("platform")) == "linux":
            left = display_platform(entry.get("platform"))
        else:
            left = entry.get("device_type") or display_platform(entry.get("platform"))
        sub_text = "%s · %s · %s" % (left or "-",
                                     display_os(entry.get("os_family")),
                                     entry.get("models") or "-")
        if dbmod.normalize_duration(entry.get("duration")):
            sub_text += " · %s" % dbmod.DURATIONS[dbmod.normalize_duration(entry["duration"])]
        sub = QLabel(sub_text)
        sub.setObjectName("EntrySub")
        sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACE_SM)
        top.addWidget(block)
        top.addWidget(title, 1)
        top.addWidget(tag)
        if self.is_skeleton:
            top.addLayout(badge_stack)
        else:
            top.addWidget(badge)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(68, 0, 0, 0)
        bottom.addWidget(sub, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 7, 8, 7)
        root.setSpacing(3)
        root.addLayout(top)
        root.addLayout(bottom)


# ---------------------------------------------------------------------------
# 标记已验证对话框
# ---------------------------------------------------------------------------
class VerifyDialog(QDialog):
    """填写验证人 / 验证设备型号 / 日期，用于把徽章从灰变绿"""

    def __init__(self, entry, parent=None):
        super(VerifyDialog, self).__init__(parent)
        self.setWindowTitle("标记为已验证")
        self.setMinimumWidth(420)

        self.ed_by = QLineEdit(entry.get("verified_by") or "")
        self.ed_by.setPlaceholderText("例如：袁贤斌")
        self.ed_model = QLineEdit(entry.get("verified_model") or entry.get("models") or "")
        self.ed_model.setPlaceholderText("例如：S5720-28X-SI-AC / 真机版本 V200R019")
        self.ed_date = QLineEdit(entry.get("verified_date") or datetime.date.today().isoformat())
        self.ed_date.setPlaceholderText("YYYY-MM-DD")

        form = QFormLayout()
        form.addRow("验证人 *", self.ed_by)
        form.addRow("验证设备型号 *", self.ed_model)
        form.addRow("验证日期", self.ed_date)

        tip = QLabel("提示：AI 生成的命令必须经真机执行成功后，才允许标记为已验证（徽章变绿）。")
        tip.setWordWrap(True)
        tip.setObjectName("HintText")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确认已验证")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(tip)
        root.addWidget(buttons)

    def _on_accept(self):
        if not self.ed_by.text().strip() or not self.ed_model.text().strip():
            QMessageBox.warning(self, "信息不完整", "验证人与验证设备型号都要填，离网环境靠这个留痕。")
            return
        self.accept()

    def values(self):
        return (self.ed_by.text().strip(), self.ed_model.text().strip(),
                self.ed_date.text().strip())


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):

    def __init__(self, db, parent=None):
        super(MainWindow, self).__init__(parent)
        self.db = db
        self.current_entry = None       # 当前选中条目 dict
        self.current_text = ""          # 当前渲染后的命令文本
        self.line_items = []            # 逐条复制的行清单 [(显示行号, 文本)]
        self.line_index = -1            # 当前逐条下标
        self.placeholder_vendor = ""    # 选中"待补充厂商"时的厂商 slug
        self.line_mode = False          # 是否处于逐条复制模式
        self.entries_in_view = []       # 当前列表里的条目
        self.operator = ""              # 当前操作人（写进 history / 配置包头部）
        self._importing = False         # 导入进行中标志（防事件重入，见 _do_seed_import）
        self._ui_state = load_ui_state()   # 上次关闭时的窗口尺寸 + 三栏比例（可能为 {}）

        self.setWindowTitle("%s v%s · %s" % (APP_TITLE, APP_VERSION, APP_NAME))
        self.resize(1420, 900)
        self.setMinimumSize(1100, 700)

        self._build_menu()
        self._build_ui()
        self._build_shortcuts()

        # 布局记忆：必须在 splitter 建好之后恢复（默认比例已在 _build_ui 里设过）
        self._restore_ui_state()

        if self.db.readonly:
            self.setWindowTitle(self.windowTitle() + " [只读模式]")

        self.refresh_all()
        QTimer.singleShot(300, self.startup_flow)

    # ==================================================================
    # 界面搭建
    # ==================================================================
    def _build_menu(self):
        bar = self.menuBar()

        m_file = bar.addMenu("文件(&F)")
        act_import = QAction("导入命令库 (.nlb)…", self)
        act_import.triggered.connect(self.on_import_nlb)
        act_export = QAction("导出整个命令库 (.nlb)…", self)
        act_export.triggered.connect(self.on_export_nlb)
        act_export_one = QAction("导出当前条目为 .txt…", self)
        act_export_one.triggered.connect(self.on_export_txt)
        act_quit = QAction("退出", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_import)
        m_file.addAction(act_export)
        m_file.addSeparator()
        m_file.addAction(act_export_one)
        m_file.addSeparator()
        act_ai_settings = QAction("设置（AI 接口）…", self)
        act_ai_settings.setToolTip("配置 AI 诊断的接口地址 / API Key / 模型（存 ai_config.json）")
        act_ai_settings.triggered.connect(self.on_open_ai_settings)
        m_file.addAction(act_ai_settings)
        m_file.addSeparator()
        m_file.addAction(act_quit)

        m_cmd = bar.addMenu("命令(&C)")
        act_gen = QAction("参数化命令生成器…", self)
        act_gen.setShortcut(QKeySequence("Ctrl+G"))
        act_gen.triggered.connect(self.on_open_generator)
        act_pkg = QAction("配置包生成器（勾选多条合并导出）…", self)
        act_pkg.setShortcut(QKeySequence("Ctrl+B"))
        act_pkg.triggered.connect(self.on_open_package)
        act_new = QAction("新建条目…", self)
        act_new.setShortcut(QKeySequence("Ctrl+N"))
        act_new.triggered.connect(self.on_new_entry)
        act_edit = QAction("编辑当前条目…", self)
        act_edit.setShortcut(QKeySequence("Ctrl+E"))
        act_edit.triggered.connect(self.on_edit_entry)
        act_dup = QAction("复制当前条目（做副本）", self)
        act_dup.triggered.connect(self.on_duplicate_entry)
        act_del = QAction("删除当前条目…", self)
        act_del.triggered.connect(self.on_delete_entry)
        act_lib = QAction("打开命令库管理器…", self)
        act_lib.setShortcut(QKeySequence("Ctrl+L"))
        act_lib.triggered.connect(self.on_open_library)
        act_verify = QAction("标记当前条目为已验证…", self)
        act_verify.triggered.connect(self.on_mark_verified)
        act_unverify = QAction("取消验证（恢复灰色徽章）", self)
        act_unverify.triggered.connect(self.on_unmark_verified)
        m_cmd.addAction(act_gen)
        m_cmd.addAction(act_pkg)
        m_cmd.addSeparator()
        m_cmd.addAction(act_new)
        m_cmd.addAction(act_edit)
        m_cmd.addAction(act_dup)
        m_cmd.addAction(act_del)
        m_cmd.addSeparator()
        m_cmd.addAction(act_verify)
        m_cmd.addAction(act_unverify)
        m_cmd.addSeparator()
        m_cmd.addAction(act_lib)

        m_view = bar.addMenu("视图(&V)")
        act_refresh = QAction("刷新列表", self)
        act_refresh.setShortcut(QKeySequence("F5"))
        act_refresh.triggered.connect(self.refresh_all)
        act_fav = QAction("只看收藏", self)
        act_fav.setCheckable(True)
        # 菜单项与过滤栏的收藏开关同步（过滤栏是 QToolButton，见 _build_ui）
        act_fav.toggled.connect(lambda v: (self.tgl_fav.setChecked(v), self.refresh_list()))
        m_view.addAction(act_refresh)
        m_view.addAction(act_fav)

        m_help = bar.addMenu("帮助(&H)")
        act_guide = QAction("使用说明 / 免责提示", self)
        act_guide.triggered.connect(self.show_guide)
        act_first = QAction("首次启动提示", self)
        act_first.triggered.connect(lambda: self.first_run_flow(force=True))
        act_about = QAction("关于 %s" % APP_NAME, self)
        act_about.triggered.connect(self.show_about)
        m_help.addAction(act_guide)
        m_help.addAction(act_first)
        m_help.addSeparator()
        m_help.addAction(act_about)

    def _build_ui(self):
        """
        总装（第 5 轮 UI 专项）：

            QTabWidget
              └─ 命令库 Tab
                   └─ GripSplitter(H)  左导航 │ 右侧
                        └─ GripSplitter(V)  列表区 │ 详情区

        三处都能拖：左导航↔右侧（左右）、列表↔详情（上下）。
        handle 宽 8px、带 grip 点、hover 高亮（theme.GripSplitter），
        各 pane 有最小尺寸卡限，拖不成 0。
        """
        # ---------- 左：树导航（P-F：层级缩进 + 灰色计数 + 选中竖条）----------
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)        # 第 2 列专放"灰色小计数"，右对齐不干扰名称
        self.tree.setIndentation(16)       # 层级缩进 16px/级
        self.tree.itemClicked.connect(self.on_tree_clicked)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)          # 名称列吃掉宽度
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 计数列贴右

        self.lbl_stats = QLabel("共 0 条 | 已验证 0 | 收藏 0")
        self.lbl_stats.setObjectName("NavStats")
        self.lbl_stats.setWordWrap(True)
        self.lbl_path = QLabel("")
        self.lbl_path.setObjectName("NavPath")
        self.lbl_path.setWordWrap(True)
        self.lbl_path.setToolTip("db 文件位置（U 盘整盘拷贝即迁移）")
        db_dir = get_base_dir()
        self.lbl_path.setText("库文件：command_lib.db\n位置：%s" % db_dir)

        left = QFrame()
        left.setObjectName("NavPane")
        left.setMinimumWidth(PANE_MIN_NAV)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lv.addWidget(self.tree, 1)
        lv.addWidget(self.lbl_stats)
        lv.addWidget(self.lbl_path)

        # ---------- 右上：过滤栏（P-C 两段式）----------
        #   第一行：搜索框通栏（抬高到 32px+）
        #   第二行：过滤链下拉（统一宽度策略，等分剩余空间）+ 两个可选中的开关 + 重置 + 计数徽章
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("SearchBox")
        self.ed_search.setPlaceholderText(
            "全局搜索：标题 / 描述 / 命令内容 / 备注 / 型号…（Ctrl+F 聚焦，输入即搜）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMinimumHeight(32)
        self.ed_search.textChanged.connect(self.refresh_list)

        self.cmb_platform = self._make_combo("全部平台")
        self.cmb_device = self._make_combo("全部设备")
        self.cmb_vendor = self._make_combo("全部厂商")
        self.cmb_os = self._make_combo("全部OS")
        self.cmb_cat = self._make_combo("全部场景")
        for cmb in (self.cmb_platform, self.cmb_device, self.cmb_vendor,
                    self.cmb_os, self.cmb_cat):
            cmb.currentIndexChanged.connect(self.refresh_list)

        # "只看收藏 / 只看已验证" 用可选中 QToolButton（checked = accent 描边），
        # 比复选框更省横向空间，也符合"过滤开关"的语义
        self.tgl_fav = QToolButton()
        self.tgl_fav.setObjectName("FilterToggle")
        self.tgl_fav.setText("只看收藏")
        self.tgl_fav.setCheckable(True)
        self.tgl_fav.setToolTip("只列出已收藏的条目（Ctrl+D 收藏当前条目）")
        self.tgl_fav.toggled.connect(self.refresh_list)

        self.tgl_verified = QToolButton()
        self.tgl_verified.setObjectName("FilterToggle")
        self.tgl_verified.setText("只看已验证")
        self.tgl_verified.setCheckable(True)
        self.tgl_verified.setToolTip("只列出真机验证过的条目（徽章为绿色）")
        self.tgl_verified.toggled.connect(self.refresh_list)

        btn_reset = QPushButton("重置筛选")
        btn_reset.setObjectName("Ghost")
        btn_reset.clicked.connect(self.reset_filters)

        self.lbl_count = QLabel("共 0 条")
        self.lbl_count.setObjectName("CountBadge")     # 徽章样式，右侧留 16px 安全边距

        row1 = QHBoxLayout()
        row1.setSpacing(SPACE_SM)
        row1.addWidget(self.ed_search, 1)

        row2 = QHBoxLayout()
        row2.setSpacing(SPACE_SM)
        # 五个下拉等比例伸缩 → 宽度策略统一；窄窗口下整体压缩而不是溢出裁切
        for cmb in (self.cmb_platform, self.cmb_device, self.cmb_vendor,
                    self.cmb_os, self.cmb_cat):
            cmb.setMinimumWidth(96)
            row2.addWidget(cmb, 1)
        row2.addWidget(self.tgl_fav)
        row2.addWidget(self.tgl_verified)
        row2.addWidget(btn_reset)
        row2.addWidget(self.lbl_count)

        self.list_entries = QListWidget()
        self.list_entries.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_entries.setUniformItemSizes(False)
        self.list_entries.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_entries.customContextMenuRequested.connect(self.on_list_context_menu)
        self.list_entries.currentItemChanged.connect(self.on_entry_selected)
        self.list_entries.itemDoubleClicked.connect(self.on_item_double_clicked)

        # 空状态页（列表为空 / 搜索无结果时切过去，不留白板）
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list_entries)
        self.list_stack.addWidget(self._build_empty_state())

        top = QWidget()
        top.setMinimumHeight(PANE_MIN_LIST_H)
        tv = QVBoxLayout(top)
        tv.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_LG, SPACE_XS)   # 右侧 16px 安全边距
        tv.setSpacing(SPACE_SM)
        tv.addLayout(row1)
        tv.addLayout(row2)
        tv.addWidget(self.list_stack, 1)
        self._list_top = top

        # ---------- 右中下部：详情面板 ----------
        detail = self._build_detail_panel()
        detail.setMinimumHeight(PANE_MIN_DETAIL_H)

        # ---------- 任务1：两级 GripSplitter，三处都能拖 ----------
        #   垂直：列表区 │ 详情区（上下拖，初始约 40% : 60%）
        self.center_split = GripSplitter(Qt.Vertical)
        self.center_split.addWidget(top)
        self.center_split.addWidget(detail)
        self.center_split.setStretchFactor(0, 4)
        self.center_split.setStretchFactor(1, 6)
        self.center_split.setMinimumWidth(PANE_MIN_DETAIL)
        self.center_split.setSizes([360, 540])

        #   水平：左导航 │ 右侧区域（左右拖，左导航初始 220）
        self.main_split = GripSplitter(Qt.Horizontal)
        self.main_split.addWidget(left)
        self.main_split.addWidget(self.center_split)
        self.main_split.setStretchFactor(0, 0)      # 左导航宽度固定，右侧吃掉伸缩
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([PANE_INIT_NAV, 1200])

        # ---------- 总装：Tab 结构（模块 04/05/06 规格）----------
        #   Tab1 命令库（原有全部内容）
        #   Tab2 排查向导（模块 04）
        #   Tab3 报错诊断（模块 05，占位）
        from ui_troubleshoot import TroubleshootTab
        from ui_errorfix import ErrorFixTab
        from ui_ai import AiTab, StatusIndicator
        self.tab_troubleshoot, self._tab_ts_ok = self._safe_tab(
            "排查向导", lambda: TroubleshootTab(self.db))
        self.tab_errorfix, self._tab_ef_ok = self._safe_tab(
            "报错诊断", lambda: ErrorFixTab(self.db))
        # AI 诊断 Tab（任务：主程序直接集成；常驻，不动态探测不排除模块。
        # 构造失败（如 PyQt5 之外的意外）也不拖垮主窗 —— _safe_tab 兜底占位页）
        self.tab_ai, self._tab_ai_ok = self._safe_tab(
            "AI 诊断", lambda: AiTab(self.db))

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.main_split, "命令库")
        self.tabs.addTab(self.tab_troubleshoot, "排查向导")
        self.tabs.addTab(self.tab_errorfix, "报错诊断")
        self.tabs.addTab(self.tab_ai, "AI 诊断")        # 位置：报错诊断之后
        self.setCentralWidget(self.tabs)

        # 状态栏常驻 AI 指示（●绿=可连接 ●灰=无网络 ●黄=配置缺失）：
        # addPermanentWidget → 不受 statusBar().showMessage() 临时消息影响
        if self._tab_ai_ok and isinstance(self.tab_ai, AiTab):
            self.tab_ai.status_indicator = StatusIndicator()
            self.statusBar().addPermanentWidget(self.tab_ai.status_indicator)

        self.statusBar().showMessage("就绪。命令执行者永远是外部 Xshell/SecureCRT，本工具只生成命令。")

    def on_open_ai_settings(self):
        """文件→设置：AI 接口配置对话框（保存后 AI Tab 自动刷新引导页/状态灯）"""
        if not getattr(self, "_tab_ai_ok", False):
            QMessageBox.information(self, "AI 诊断不可用", "AI 诊断 Tab 未成功加载，无法配置。")
            return
        self.tab_ai.open_settings()

    def on_ask_ai(self):
        """命令库详情 [问 AI]：带入厂商/OS/型号/命令全文，现象留空由用户补"""
        entry = self.current_entry
        if not entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        self.goto_ai_tab(
            vendor=dbmod.display_vendor(dbmod.normalize_vendor(entry.get("vendor"))),
            os_name=dbmod.display_os(entry.get("os_family")),
            model=entry.get("models") or "",
            echo=self.current_text or self.txt_commands.toPlainText(),
        )
        self.statusBar().showMessage("已带入 AI 诊断：补充「故障现象」后点「发送」。")

    def goto_ai_tab(self, **context):
        """
        跨 Tab [问 AI] 统一入口（第 2 轮各 Tab 按钮接线用）：
        切到 AI 诊断 Tab 并预填上下文。键名与 prefill_context 一致：
            vendor / os_name / model / symptom / echo / steps
        """
        from ui_ai import AiTab            # 局部导入：与 _build_ui 同源，避免循环导入
        self.tabs.setCurrentWidget(self.tab_ai)
        if getattr(self, "_tab_ai_ok", False) and isinstance(self.tab_ai, AiTab):
            self.tab_ai.prefill_context(**context)

    def _build_empty_state(self):
        """空状态页：图标字形 + 说明 + 重置按钮（不用白板）"""
        page = QWidget()
        page.setObjectName("EmptyState")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        lay.setSpacing(SPACE_SM)
        lay.addStretch(1)

        icon = QLabel("⌕")                       # Unicode 字形，不引入图标文件
        icon.setObjectName("EmptyIcon")
        icon.setAlignment(Qt.AlignCenter)

        self.lbl_empty_title = QLabel("没有匹配的命令")
        self.lbl_empty_title.setObjectName("EmptyTitle")
        self.lbl_empty_title.setAlignment(Qt.AlignCenter)

        self.lbl_empty_body = QLabel("换个关键字，或重置筛选条件再试")
        self.lbl_empty_body.setObjectName("EmptyBody")
        self.lbl_empty_body.setAlignment(Qt.AlignCenter)
        self.lbl_empty_body.setWordWrap(True)

        btn = QPushButton("重置筛选")
        btn.setObjectName("Ghost")
        btn.clicked.connect(self.reset_filters)

        lay.addWidget(icon)
        lay.addWidget(self.lbl_empty_title)
        lay.addWidget(self.lbl_empty_body)
        lay.addSpacing(SPACE_SM)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        return page

    def _show_empty_state(self, title, body):
        """切到空状态页并更新文案"""
        self.lbl_empty_title.setText(title)
        self.lbl_empty_body.setText(body)
        self.list_stack.setCurrentIndex(1)

    def _show_list(self):
        """切回列表页"""
        self.list_stack.setCurrentIndex(0)


    # ==================================================================
    # 布局记忆（任务1：splitter sizes + 窗口尺寸 → exe 同目录 ui_state.json）
    # ==================================================================
    def _restore_ui_state(self):
        """
        恢复上次关闭时的窗口尺寸与三栏比例。
        read_sizes 会校验"每项都不小于对应最小尺寸"，任何异常都退回默认值 ——
        离网现场不能因为一个状态文件把界面搞成看不见的样子。
        """
        state = self._ui_state or {}
        window = state.get("window")
        if isinstance(window, dict):
            try:
                w = int(window.get("w", 0))
                h = int(window.get("h", 0))
                if w >= self.minimumWidth() and h >= self.minimumHeight():
                    self.resize(w, h)
            except (TypeError, ValueError):
                pass

        sizes = read_sizes(state, "main_split", (PANE_MIN_NAV, PANE_MIN_DETAIL))
        if sizes:
            self.main_split.setSizes(sizes)

        sizes = read_sizes(state, "center_split", (PANE_MIN_LIST_H, PANE_MIN_DETAIL_H))
        if sizes:
            self.center_split.setSizes(sizes)

        # 子 Tab 分栏记忆（AI 诊断 / 报错诊断 / 排查向导，任务2-3）：各 Tab 自校验
        for tab in (self.tab_ai, self.tab_errorfix, self.tab_troubleshoot):
            if tab is not None and hasattr(tab, "restore_split_state"):
                try:
                    tab.restore_split_state(state)
                except Exception:
                    pass

    def _sub_tab_split_state(self):
        """收集子 Tab 分栏比例（构造失败的占位 Tab 无此方法，跳过）"""
        payload = {}
        for tab in (self.tab_ai, self.tab_errorfix, self.tab_troubleshoot):
            if tab is not None and hasattr(tab, "split_state"):
                try:
                    payload.update(tab.split_state())
                except Exception:
                    pass
        return payload

    def _save_ui_state(self):
        """把窗口尺寸与各分栏比例写入 ui_state.json（只读介质上静默失败）"""
        try:
            payload = {
                "window": {"w": self.width(), "h": self.height()},
                "main_split": list(self.main_split.sizes()),
                "center_split": list(self.center_split.sizes()),
            }
            payload.update(self._sub_tab_split_state())
        except Exception:
            return
        save_ui_state(payload)

    def _safe_tab(self, tab_title, factory):
        """
        安全构造业务 Tab（★ 交付级容错）。
        只读库结构不完整（缺 trouble_trees / err_dict / err_unresolved 表，典型是
        U 盘写保护 + 旧版 schema v1 的库）时，Tab 的构造函数会抛 sqlite3.OperationalError。
        这里退化成占位页并给出可执行指引 —— 保证主窗口能起来、命令库本体
        （搜索/浏览/复制/生成器/编辑器）全部照常可用，而不是整个程序起不来。

        返回 (控件, 是否构造成功)。
        """
        try:
            return factory(), True
        except Exception as exc:
            missing = "、".join(getattr(self.db, "missing_tables", []) or []) or "（未知）"
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(26, 24, 26, 24)
            lay.setSpacing(10)

            head = QLabel("【%s】功能暂不可用" % tab_title)
            head.setObjectName("ErrorHead")
            head.setWordWrap(True)
            lay.addWidget(head)

            # 修复指引放进 warning 竖条卡片（第 5 轮统一告警呈现方式）
            card = QFrame()
            card.setObjectName("WarnCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)

            body = QLabel(
                "原因：该功能所需的数据库表不存在（缺少：%s）。\n"
                "最常见的情况是 —— 命令库是旧版本（schema v1）建的，而程序运行在\n"
                "只读介质上（U 盘写保护）：只读模式下无法自动建表升级，所以这些表补不上。\n\n"
                "错误摘要：%s: %s\n\n"
                "修复指引（任选其一）：\n"
                "  1) 在【可写】环境下用本程序打开一次该命令库 —— 程序会自动补齐缺失的\n"
                "     表结构（schema 升级到 v%d），之后本功能即恢复；\n"
                "  2) 关闭 U 盘写保护开关后重新打开程序；\n"
                "  3) 改用随程序发布的 command_lib.db。\n\n"
                "命令库的搜索 / 浏览 / 复制 / 参数生成器 / 条目编辑不受影响，可继续使用。"
                % (missing, type(exc).__name__, exc, dbmod.SCHEMA_VERSION))
            body.setWordWrap(True)
            body.setObjectName("InfoBody")
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card_lay.addWidget(body)
            lay.addWidget(card)
            lay.addStretch(1)
            return page, False

    def goto_troubleshoot(self, category=None, tree_id=None):
        """跨 Tab 跳转：命令库详情的[去排查树]按钮用（带上下文）"""
        self.tabs.setCurrentWidget(self.tab_troubleshoot)
        if not getattr(self, "_tab_ts_ok", False):
            # 排查向导未成功构造：切到占位页即可，占位页本身已写明修复指引
            self.statusBar().showMessage(
                "排查向导不可用（命令库缺表），修复指引见页面提示。")
            return
        self.tab_troubleshoot.focus_category(category, tree_id)

    def on_goto_troubleshoot(self):
        """[去排查树]：按当前条目的场景分类定位排查树"""
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        if not getattr(self, "_tab_ts_ok", False):
            QMessageBox.information(
                self, "排查向导不可用",
                "排查向导所需的表在命令库中不存在（缺少：%s），无法跳转。\n\n"
                "请按「排查向导」页给出的修复指引，在可写环境下打开一次命令库完成升级。"
                % ("、".join(self.db.missing_tables or []) or "未知"))
            return
        if self.db.count_trees() == 0:
            QMessageBox.information(self, "没有排查树",
                                    "排查树库是空的（seed_data/trouble_trees_*.json）。",
                                    )
            return
        self.goto_troubleshoot(category=self.current_entry.get("category"))

    @staticmethod
    def _make_combo(all_text):
        """过滤下拉：ThemedComboBox（自绘箭头，见 theme.py）+ 统一最小宽度"""
        cmb = ThemedComboBox()
        cmb.addItem(all_text)
        cmb.setMinimumWidth(110)
        return cmb

    def _build_detail_panel(self):
        """
        详情面板（P-D 按钮三组分级 / P-E 信息页留白 / P-G 代码块）

        按钮分组（视觉权重递减，组间用 1px 竖线分隔）：
            组1 主操作  —— 复制全部(Primary) / 仅复制命令 / 逐条复制 / 下一条
            组2 生成工具 —— 参数生成器 / 配置包 / 导出 .txt      （Ghost 描边）
            组3 管理     —— 编辑条目 / 去排查树                   （Subtle 无框）
            最右独立     —— 星形收藏 toggle（★ 实心 = 已收藏）
        """
        # ---- 标题行 ----
        self.lbl_title = QLabel("未选中条目")
        self.lbl_title.setObjectName("DetailTitle")
        self.lbl_title.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.lbl_meta = QLabel("")
        self.lbl_meta.setObjectName("DetailMeta")
        self.lbl_meta.setWordWrap(True)

        def _vline():
            """组间 1px 竖分隔线"""
            line = QFrame()
            line.setObjectName("VLine")
            line.setFixedWidth(1)
            line.setMinimumHeight(22)
            return line

        # ---- 组1：主操作（复制）----
        self.btn_copy_all = QPushButton("复制全部")
        self.btn_copy_all.setObjectName("Primary")
        self.btn_copy_all.setToolTip("整块配置脚本复制为纯文本，直接粘贴到 Xshell 可跑（Ctrl+C）")
        self.btn_copy_all.clicked.connect(self.copy_all)

        self.btn_copy_cmd = QPushButton("仅复制命令")
        self.btn_copy_cmd.setToolTip("剔除 ! # 注释行后只复制纯命令，粘贴即执行")
        self.btn_copy_cmd.clicked.connect(self.copy_commands_only)

        self.btn_copy_line = QPushButton("逐条复制")
        self.btn_copy_line.setCheckable(True)
        self.btn_copy_line.setToolTip("进入逐条模式：逐条高亮 + 回车/按钮下一条，排查单条命令用")
        self.btn_copy_line.toggled.connect(self.toggle_line_mode)

        self.btn_next_line = QPushButton("下一条 ▶")
        self.btn_next_line.setEnabled(False)
        self.btn_next_line.setToolTip("逐条模式走下一条命令（Ctrl+Enter）")
        self.btn_next_line.clicked.connect(self.next_line)

        self.lbl_line_pos = QLabel("")
        self.lbl_line_pos.setObjectName("LinePos")

        # ---- 组2：生成工具 ----
        self.btn_generator = QPushButton("参数生成器")
        self.btn_generator.setObjectName("Ghost")
        self.btn_generator.setToolTip("打开参数表单，填完实时预览再复制（Ctrl+G）")
        self.btn_generator.clicked.connect(self.on_open_generator)

        self.btn_package = QPushButton("配置包")
        self.btn_package.setObjectName("Ghost")
        self.btn_package.setToolTip("勾选多条命令合并成一个配置包导出，头部自动加注释块（Ctrl+B）")
        self.btn_package.clicked.connect(self.on_open_package)

        self.btn_export_txt = QPushButton("导出 .txt")
        self.btn_export_txt.setObjectName("Ghost")
        self.btn_export_txt.setToolTip("把当前条目导出成 .txt 文件")
        self.btn_export_txt.clicked.connect(self.on_export_txt)

        self.btn_ask_ai = QPushButton("问 AI")
        self.btn_ask_ai.setObjectName("Ghost")
        self.btn_ask_ai.setToolTip("把当前条目的厂商/OS/型号/命令全文带入 AI 诊断 Tab（现象由你补充）")
        self.btn_ask_ai.clicked.connect(self.on_ask_ai)

        # ---- 组3：管理 ----
        self.btn_edit = QPushButton("编辑条目")
        self.btn_edit.setObjectName("Subtle")
        self.btn_edit.setToolTip("界面化修改这条命令（Ctrl+E）")
        self.btn_edit.clicked.connect(self.on_edit_entry)

        self.btn_goto_tree = QPushButton("去排查树")
        self.btn_goto_tree.setObjectName("Subtle")
        self.btn_goto_tree.setToolTip("跳到排查向导，按本条目的场景分类定位相关排查树")
        self.btn_goto_tree.clicked.connect(self.on_goto_troubleshoot)

        # ---- 星形收藏（P-D：不再用文字按钮，状态一眼可见）----
        self.btn_fav = QToolButton()
        self.btn_fav.setObjectName("Star")
        self.btn_fav.setText("☆")
        self.btn_fav.setCheckable(True)
        self.btn_fav.setFixedSize(32, 32)
        self.btn_fav.setToolTip("收藏 / 取消收藏当前条目（Ctrl+D）；★ 实心 = 已收藏")
        self.btn_fav.clicked.connect(self.toggle_favorite)

        btns = QHBoxLayout()
        btns.setSpacing(SPACE_SM)
        # 组 1
        btns.addWidget(self.btn_copy_all)
        btns.addWidget(self.btn_copy_cmd)
        btns.addWidget(self.btn_copy_line)
        btns.addWidget(self.btn_next_line)
        btns.addSpacing(SPACE_XS)
        btns.addWidget(_vline())
        btns.addSpacing(SPACE_XS)
        # 组 2
        btns.addWidget(self.btn_generator)
        btns.addWidget(self.btn_package)
        btns.addWidget(self.btn_export_txt)
        btns.addWidget(self.btn_ask_ai)
        btns.addSpacing(SPACE_XS)
        btns.addWidget(_vline())
        btns.addSpacing(SPACE_XS)
        # 组 3
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_goto_tree)
        btns.addStretch(1)
        # 独立：星标
        btns.addWidget(self.btn_fav)

        # ---- 提示行 ----
        self.lbl_param_hint = QLabel("")
        self.lbl_param_hint.setObjectName("HintText")
        self.lbl_param_hint.setWordWrap(True)

        # temp/perm 配套条目跳转按钮（notes 里写了"配套条目：《标题》"时出现）
        self.btn_pair = QPushButton("→ 跳转配套条目")
        self.btn_pair.setToolTip("跳到 notes 里指向的配套条目（temp ↔ perm）")
        self.btn_pair.setVisible(False)
        self.btn_pair.clicked.connect(self.jump_to_pair_entry)

        # ---- 命令全文（P-G：代码块，1px 描边 + 圆角）----
        self.txt_commands = QPlainTextEdit()
        self.txt_commands.setObjectName("CodeBlock")
        self.txt_commands.setReadOnly(True)
        self.txt_commands.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_commands.setFont(mono_font())
        self.highlighter = CommandHighlighter(self.txt_commands.document())

        # ---- 完整命令预览状态条（schema 升级 2026-09-30）----
        #   缺必填 → err 红；缺选填 → warn 黄；全齐 → 隐藏
        self.lbl_preview_state = QLabel("")
        self.lbl_preview_state.setObjectName("StatusStrip")
        self.lbl_preview_state.setWordWrap(True)
        self.lbl_preview_state.setVisible(False)

        # ---- 信息分页（P-E：统一留白 + 行距 1.5）----
        self.tab_info = QTabWidget()
        self.tab_info.setDocumentMode(True)

        # ---- 参数说明折叠面板（schema 升级 2026-09-30）----
        #   行结构：名称 | 必填* | 默认值 | 类型 | 说明（正文 + range/example/choices 灰字附注）
        #   默认收起；无参数条目整页不渲染（_render_detail 里 setTabVisible 控制）
        self.tbl_params = QTableWidget(0, 5)
        self.tbl_params.setHorizontalHeaderLabels(
            ["参数", "必填", "默认值", "类型", "说明"])
        self.tbl_params.verticalHeader().setVisible(False)
        self.tbl_params.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_params.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_params.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        self._param_tab_suffix = ""
        self.btn_param_toggle = QToolButton()
        self.btn_param_toggle.setObjectName("CollapseHeader")
        self.btn_param_toggle.setCheckable(True)
        self.btn_param_toggle.setChecked(False)
        self.btn_param_toggle.setText("▸ 参数说明")
        self.btn_param_toggle.setToolTip("展开/收起参数说明表")
        self.btn_param_toggle.clicked.connect(self._toggle_param_panel)

        self.param_panel = QWidget()
        _pv = QVBoxLayout(self.param_panel)
        _pv.setContentsMargins(0, 2, 0, 0)
        _pv.setSpacing(2)
        _pv.addWidget(self.btn_param_toggle)
        _pv.addWidget(self.tbl_params)
        self.tab_info.addTab(self.param_panel, "参数说明")

        self.txt_notes = QTextEdit()
        self.txt_notes.setReadOnly(True)
        self.txt_notes.setObjectName("InfoPane")
        self.tab_info.addTab(self.txt_notes, "坑点备注")

        self.txt_verify = QTextEdit()
        self.txt_verify.setReadOnly(True)
        self.txt_verify.setObjectName("InfoPane")
        self.tab_info.addTab(self.txt_verify, "验证信息")

        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(["时间", "动作", "变更内容", "操作人", "旧值"])
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_history.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tab_info.addTab(self.tbl_history, "修改历史")

        root = QWidget()
        rv = QVBoxLayout(root)
        rv.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        rv.setSpacing(SPACE_SM)
        rv.addWidget(self.lbl_title)
        rv.addWidget(self.lbl_meta)
        rv.addLayout(btns)
        rv.addWidget(self.lbl_param_hint)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(SPACE_SM)
        hint_row.addWidget(self.btn_pair)
        hint_row.addWidget(self.lbl_line_pos)
        hint_row.addStretch(1)
        rv.addLayout(hint_row)

        rv.addWidget(self.txt_commands, 3)
        rv.addWidget(self.lbl_preview_state)
        rv.addWidget(self.tab_info, 2)
        return root

    def _build_shortcuts(self):
        """快捷键：Ctrl+F 聚焦搜索 / Ctrl+C 复制命令块 / Ctrl+D 收藏切换"""
        sc_find = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_find.activated.connect(self.focus_search)

        sc_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        sc_copy.activated.connect(self.on_ctrl_c)

        sc_fav = QShortcut(QKeySequence("Ctrl+D"), self)
        sc_fav.activated.connect(self.toggle_favorite)

        sc_next = QShortcut(QKeySequence("Ctrl+Return"), self)
        sc_next.activated.connect(self.next_line)

        # 第 2 轮新增：生成器 / 新建 / 编辑
        sc_gen = QShortcut(QKeySequence("Ctrl+G"), self)
        sc_gen.activated.connect(self.on_open_generator)
        sc_new = QShortcut(QKeySequence("Ctrl+N"), self)
        sc_new.activated.connect(self.on_new_entry)
        sc_edit = QShortcut(QKeySequence("Ctrl+E"), self)
        sc_edit.activated.connect(self.on_edit_entry)

    # ==================================================================
    # 刷新
    # ==================================================================
    def refresh_all(self):
        """整体刷新：过滤下拉 + 左侧树 + 列表 + 统计（库已关闭时直接跳过）"""
        if self.db.closed:
            return
        self._refresh_platform_combo()
        self._refresh_filter_combos()
        self._refresh_tree()
        self.refresh_list()
        self._refresh_stats()
        # 排查向导未成功构造（命令库缺表）时是占位页，没有 refresh() 方法
        if getattr(self, "_tab_ts_ok", False):
            self.tab_troubleshoot.refresh()

    def _refresh_filter_combos(self):
        """过滤下拉框选项来自库里的实际值（保当前选择，避免刷新后跳掉）"""
        mapping = ((self.cmb_device, "device_type"),
                   (self.cmb_vendor, "vendor"),
                   (self.cmb_os, "os_family"),
                   (self.cmb_cat, "category"))
        # 下拉框第一项固定文案（统一作为"不过滤"的判定依据）
        # ★ 显示显示名、userData 存 slug：界面中文，库里 slug
        titles = {"device_type": "全部设备", "vendor": "全部厂商",
                  "os_family": "全部OS", "category": "全部场景"}
        displayers = {"device_type": None, "vendor": display_vendor,
                      "os_family": display_os, "category": None}
        for cmb, field in mapping:
            keep = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItem(titles[field], "")          # 第一项固定为"全部xxx"
            for value in self.db.distinct(field):
                show = displayers[field](value) if displayers[field] else value
                cmb.addItem(show, value)
            idx = cmb.findData(keep) if keep else 0
            cmb.setCurrentIndex(idx if idx >= 0 else 0)
            cmb.blockSignals(False)

    def _refresh_platform_combo(self):
        """平台下拉：网络设备 / Linux 服务器 / 全部"""
        keep = self.cmb_platform.currentData()
        self.cmb_platform.blockSignals(True)
        self.cmb_platform.clear()
        self.cmb_platform.addItem("全部平台", "")
        for slug, name in dbmod.PLATFORMS.items():
            self.cmb_platform.addItem(name, slug)
        idx = self.cmb_platform.findData(keep) if keep else 0
        self.cmb_platform.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_platform.blockSignals(False)

    def _tree_counts(self):
        """
        一次遍历算出左侧树各节点的条目数（P-F：厂商/设备/OS 后附灰色小计数）。

        ★ 用 all_entries() 一次性取回、在内存里聚合，而不是每个节点查一次库：
          树有几十个节点，逐个 search 会把每次刷新都拖慢；本地库全部条目也就几百条。
        """
        counts = {}
        try:
            entries = self.db.all_entries()
        except Exception:
            entries = []
        for entry in entries:
            platform = dbmod.normalize_platform(entry.get("platform"))
            vendor = dbmod.normalize_vendor(entry.get("vendor"))
            os_family = dbmod.normalize_os(entry.get("os_family"))
            device_type = entry.get("device_type") or "未分类"
            for key in (("total",),
                        ("platform", platform),
                        ("device", device_type),
                        ("vendor", vendor),
                        ("os", os_family),
                        ("vendor_platform", vendor, platform),
                        ("device_vendor", device_type, vendor),
                        ("device_vendor_os", device_type, vendor, os_family),
                        ("vendor_os", vendor, os_family)):
                counts[key] = counts.get(key, 0) + 1
        return counts

    def _refresh_tree(self):
        """
        左侧树（模块 01/03 规格 + 第 5 轮 P-F）：
            网络设备 → 设备类型 → 厂商 → OS 版本
            Linux   → 发行版 → 版本
        末尾挂"待补充厂商"分组，选中时列表区提示"该厂商模板待补充，可在编辑器中添加"。

        P-F 改造：第 2 列放灰色小计数（如"华为  24"），名称列拉伸、计数列贴右；
        层级缩进 16px/级（见 _build_ui 的 setIndentation）；
        选中节点由 QSS 给 accent 12% 底 + 左侧 3px 竖条 + 亮文字。
        """
        counts = self._tree_counts()

        def add_count(item, key):
            """给节点写第 2 列计数（0 条时留空，避免满屏"0"）"""
            number = counts.get(key, 0)
            if number:
                item.setText(1, str(number))
                item.setForeground(1, QColor(TEXT_MUTED))
                item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)

        self.tree.clear()
        root_item = QTreeWidgetItem(["全部命令", str(counts.get(("total",), 0))])
        root_item.setData(0, Qt.UserRole, None)
        root_item.setForeground(0, QColor(TEXT_PRIMARY))
        root_item.setForeground(1, QColor(TEXT_MUTED))
        root_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.addTopLevelItem(root_item)

        tree = self.db.tree_data()

        # ---- 网络设备分支：设备类型 → 厂商 → OS ----
        net_item = QTreeWidgetItem(["网络设备"])
        net_item.setData(0, Qt.UserRole, {"platform": "network"})
        net_item.setForeground(0, QColor(PLATFORM_COLORS["network"]))
        add_count(net_item, ("platform", "network"))
        self.tree.addTopLevelItem(net_item)
        for device_type in sorted(tree["network"].keys()):
            dt_item = QTreeWidgetItem([device_type])
            dt_item.setData(0, Qt.UserRole, {"platform": "network", "device_type": device_type})
            dt_item.setForeground(0, QColor(ACCENT))
            add_count(dt_item, ("device", device_type))
            net_item.addChild(dt_item)
            for vendor in sorted(tree["network"][device_type].keys()):
                color = VENDOR_COLORS.get(vendor, DEFAULT_VENDOR_COLOR)
                vd_item = QTreeWidgetItem([display_vendor(vendor)])
                vd_item.setData(0, Qt.UserRole, {"platform": "network",
                                                 "device_type": device_type, "vendor": vendor})
                vd_item.setForeground(0, QColor(color))
                add_count(vd_item, ("device_vendor", device_type, vendor))
                dt_item.addChild(vd_item)
                for os_family in tree["network"][device_type][vendor]:
                    os_item = QTreeWidgetItem([display_os(os_family)])
                    os_item.setData(0, Qt.UserRole, {"platform": "network",
                                                     "device_type": device_type,
                                                     "vendor": vendor, "os_family": os_family})
                    os_item.setForeground(0, QColor(TEXT_SECONDARY))
                    add_count(os_item, ("device_vendor_os", device_type, vendor, os_family))
                    vd_item.addChild(os_item)
            dt_item.setExpanded(True)

        # ---- Linux 分支：发行版 → 版本 ----
        linux_item = QTreeWidgetItem(["Linux 服务器"])
        linux_item.setData(0, Qt.UserRole, {"platform": "linux"})
        linux_item.setForeground(0, QColor(PLATFORM_COLORS["linux"]))
        add_count(linux_item, ("platform", "linux"))
        self.tree.addTopLevelItem(linux_item)
        if not tree["linux"]:
            empty = QTreeWidgetItem(["（暂无条目）"])
            empty.setForeground(0, QColor(TEXT_MUTED))
            empty.setData(0, Qt.UserRole, {"platform": "linux"})
            linux_item.addChild(empty)
        for vendor in sorted(tree["linux"].keys()):
            color = VENDOR_COLORS.get(vendor, DEFAULT_VENDOR_COLOR)
            vd_item = QTreeWidgetItem([display_vendor(vendor)])
            vd_item.setData(0, Qt.UserRole, {"platform": "linux", "vendor": vendor})
            vd_item.setForeground(0, QColor(color))
            add_count(vd_item, ("vendor_platform", vendor, "linux"))
            linux_item.addChild(vd_item)
            for os_family in sorted(tree["linux"][vendor].keys()):
                os_item = QTreeWidgetItem([display_os(os_family)])
                os_item.setData(0, Qt.UserRole, {"platform": "linux",
                                                 "vendor": vendor, "os_family": os_family})
                os_item.setForeground(0, QColor(TEXT_SECONDARY))
                add_count(os_item, ("vendor_os", vendor, os_family))
                vd_item.addChild(os_item)
        linux_item.setExpanded(True)

        # ---- 待补充厂商（只建导航占位，选中给提示）----
        ph_item = QTreeWidgetItem(["待补充厂商"])
        ph_item.setData(0, Qt.UserRole, {"placeholder_root": True})
        ph_item.setForeground(0, QColor(TEXT_SECONDARY))
        self.tree.addTopLevelItem(ph_item)
        for slug in PLACEHOLDER_VENDORS:
            child = QTreeWidgetItem([display_vendor(slug)])
            child.setData(0, Qt.UserRole, {"placeholder_vendor": slug})
            child.setForeground(0, QColor(VENDOR_COLORS.get(slug, DEFAULT_VENDOR_COLOR)))
            child.setToolTip(0, "该厂商模板待补充，可在编辑器中添加")
            ph_item.addChild(child)

        self.tree.expandItem(root_item)
        # ★ 不再 resizeColumnToContents(0)：第 0 列已设为 Stretch（见 _build_ui），
        #   自动撑满并让计数列贴住右边缘

    def _refresh_stats(self):
        s = self.db.stats()
        # 紧凑文案：左导航 220px 放不下完整分隔符版本（会折行掉字）
        self.lbl_stats.setText("共 %d · 已验证 %d · 收藏 %d"
                               % (s["total"], s["verified"], s["favorite"]))
        if self.db.readonly:
            self.lbl_stats.setText(self.lbl_stats.text() + "   [只读]")

    def refresh_list(self):
        """按搜索词 + 过滤链重新查询并重建列表（下拉的 currentData 是 slug）"""
        if self.db.closed:
            return
        keyword = self.ed_search.text().strip()
        platform = self.cmb_platform.currentData()
        device_type = self.cmb_device.currentData()
        vendor = self.cmb_vendor.currentData()
        os_family = self.cmb_os.currentData()
        category = self.cmb_cat.currentData()

        # 占位厂商：该厂商本就没有条目，不能退化成"显示全部" —— 直接给补充指引
        # 占位厂商：该厂商暂无条目，详情区给出"模板待补充"的下一步指引
        if self.placeholder_vendor:
            self.entries_in_view = []
            self.list_entries.blockSignals(True)
            self.list_entries.clear()
            self.list_entries.blockSignals(False)
            self.lbl_count.setText("共 0 条")
            self._show_empty_state("该厂商模板待补充", "可在编辑器中新建条目（Ctrl+N）补充")
            self._refresh_stats()
            self.show_placeholder_vendor(self.placeholder_vendor)
            return
        self.entries_in_view = self.db.search(
            keyword=keyword,
            platform=platform or None,
            device_type=device_type or None,
            vendor=vendor or None,
            os_family=os_family or None,
            category=category or None,
            favorite_only=self.tgl_fav.isChecked(),
            verified_only=self.tgl_verified.isChecked(),
        )

        imported_uuids = self.db.ai_imported_uuids()      # AI 溯源 🤖 徽标（一次查询）
        self.list_entries.blockSignals(True)
        self.list_entries.clear()
        for entry in self.entries_in_view:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, entry.get("uuid"))
            card = EntryCard(entry, entry.get("uuid") in imported_uuids)
            item.setSizeHint(card.sizeHint())
            item.setToolTip(self._entry_tooltip(entry))
            self.list_entries.addItem(item)
            self.list_entries.setItemWidget(item, card)
        self.list_entries.blockSignals(False)

        self.lbl_count.setText("共 %d 条" % len(self.entries_in_view))
        self._refresh_stats()

        # 选中项自动恢复
        if self.entries_in_view:
            self._show_list()
            if self.current_entry:
                for row, entry in enumerate(self.entries_in_view):
                    if entry.get("uuid") == self.current_entry.get("uuid"):
                        self.list_entries.setCurrentRow(row)
                        return
            self.list_entries.setCurrentRow(0)
        else:
            # 空状态（第 5 轮附加项）：区分"筛选没命中"与"库本身为空"，指引更准
            try:
                library_empty = self.db.count() == 0
            except Exception:
                library_empty = False
            if library_empty:
                self._show_empty_state(
                    "命令库还是空的",
                    "从『文件 → 导入命令库 (.nlb)』导入，或按『帮助 → 首次启动提示』\n"
                    "导入随程序发布的内置种子库（11 个厂商）。")
            else:
                self._show_empty_state(
                    "没有匹配的命令",
                    "换个关键字，或重置筛选条件再试")
            # 注：占位厂商的情形在本函数开头已提前 return，这里不必再判一次
            self.show_empty_detail("没有匹配的条目，换个关键字或重置筛选试试。")

    def show_placeholder_vendor(self, vendor_slug):
        """选中"待补充厂商"节点：列表清空 + 详情区给出明确的补充指引"""
        msg = ("【%s】该厂商模板待补充，可在编辑器中添加。\n\n"
               "补充方式二选一：\n"
               "1. 命令 → 新建条目（Ctrl+N），厂商下拉里选 %s，保存后即出现在本节点下；\n"
               "2. 按 seed_data/placeholders 下的 README 写 JSON 放进 seed_data/ 目录，\n"
               "   重启程序后『文件 → 导入命令库』或首次引导会自动导入。\n\n"
               "所有新条目一律为『未验证』状态，真机验证通过后再右键标记为已验证。"
               % (display_vendor(vendor_slug), display_vendor(vendor_slug)))
        self.show_empty_detail(msg)

    @staticmethod
    def _entry_tooltip(entry):
        """列表项悬浮提示（显示名，Linux 条目带 duration）"""
        parts = ["%s\n\n平台：%s" % (entry.get("title", ""),
                                    display_platform(entry.get("platform"))),
                 "厂商：%s" % display_vendor(entry.get("vendor")),
                 "OS：%s" % display_os(entry.get("os_family")),
                 "场景：%s" % (entry.get("category") or "-"),
                 "型号：%s" % (entry.get("models") or "-")]
        duration = dbmod.normalize_duration(entry.get("duration"))
        if duration:
            parts.append("生效方式：%s" % dbmod.DURATIONS[duration])
        parts.append("状态：%s" % ("已验证" if int(entry.get("verified") or 0) == 1 else "未验证"))
        return "\n".join(parts)

    def reset_filters(self):
        """清空搜索与过滤链（统一 blockSignals，避免中途反复重查）"""
        widgets = (self.ed_search, self.cmb_platform, self.cmb_device, self.cmb_vendor,
                   self.cmb_os, self.cmb_cat, self.tgl_fav, self.tgl_verified)
        for widget in widgets:
            widget.blockSignals(True)
        self.ed_search.clear()
        self.placeholder_vendor = ""
        for cmb in (self.cmb_platform, self.cmb_device, self.cmb_vendor,
                    self.cmb_os, self.cmb_cat):
            cmb.setCurrentIndex(0)
        self.tgl_fav.setChecked(False)
        self.tgl_verified.setChecked(False)
        for widget in widgets:
            widget.blockSignals(False)
        self.refresh_list()
        self.statusBar().showMessage("筛选条件已重置。")

    def focus_search(self):
        """Ctrl+F：聚焦搜索框并全选"""
        self.ed_search.setFocus()
        self.ed_search.selectAll()

    # ==================================================================
    # 事件
    # ==================================================================
    def on_tree_clicked(self, item, _column):
        """
        左侧树点击 → 同步过滤下拉框（单一数据源，避免两套过滤互相打架）。
        树节点数据约定：
          None                          → 全部
          {"platform": "linux"}         → 只看 Linux
          {"placeholder_vendor": slug}  → 待补充厂商（给提示，不查库）
          {"platform","device_type","vendor","os_family"} → 逐级过滤
        """
        data = item.data(0, Qt.UserRole)
        self.placeholder_vendor = ""

        if not data:
            self.cmb_platform.setCurrentIndex(0)
            self._clear_combo(self.cmb_device)
            self._clear_combo(self.cmb_vendor)
            self._clear_combo(self.cmb_os)
            self.refresh_list()
            return

        if data.get("placeholder_vendor"):
            self.placeholder_vendor = data["placeholder_vendor"]
            self._clear_combo(self.cmb_platform)
            self._clear_combo(self.cmb_device)
            self._clear_combo(self.cmb_vendor)
            self._clear_combo(self.cmb_os)
            self.refresh_list()
            self.statusBar().showMessage(
                "【%s】该厂商模板待补充，可在编辑器中添加。"
                % display_vendor(self.placeholder_vendor))
            return

        self._select_combo_data(self.cmb_platform, data.get("platform"))
        self._select_combo_data(self.cmb_device, data.get("device_type"))
        self._select_combo_data(self.cmb_vendor, data.get("vendor"))
        self._select_combo_data(self.cmb_os, data.get("os_family"))

    @staticmethod
    def _clear_combo(cmb):
        """把下拉恢复到"全部xxx"（第一项，userData 为空）"""
        cmb.setCurrentIndex(0)

    @staticmethod
    def _select_combo_data(cmb, value):
        """按 userData（slug）选中下拉项；None 表示该项不过滤"""
        if not value:
            cmb.setCurrentIndex(0)
            return
        idx = cmb.findData(value)
        cmb.setCurrentIndex(idx if idx >= 0 else 0)

    @staticmethod
    def _find_pair_entry_title(entry):
        """
        从 notes 里解析配套条目标题（模块 03 的 temp ↔ perm 互指约定）。
        匹配格式：配套条目：《标题》  返回标题或 None。
        """
        import re as _re
        text = (entry.get("notes") or "") + "\n" + (entry.get("description") or "")
        m = _re.search(r"配套条目：《(.+?)》", text)
        return m.group(1) if m else None

    def jump_to_pair_entry(self):
        """跳到配套条目：按标题搜索并选中（跨 temp/perm 的快速往返）"""
        if not self.current_entry:
            return
        pair_title = self._find_pair_entry_title(self.current_entry)
        if not pair_title:
            self.statusBar().showMessage("该条目 notes 里没有『配套条目：《…》』标记。")
            return
        matches = self.db.search(keyword=pair_title)
        if not matches:
            QMessageBox.information(self, "未找到配套条目",
                                    "按标题没搜到「%s」。\n可能已被改名或删除，"
                                    "可手动 Ctrl+F 搜索。" % pair_title)
            return
        # 精确标题优先
        exact = [e for e in matches if e.get("title") == pair_title]
        target = (exact or matches)[0]
        self.ed_search.clear()
        for cmb in (self.cmb_platform, self.cmb_device, self.cmb_vendor,
                    self.cmb_os, self.cmb_cat):
            cmb.setCurrentIndex(0)
        self.refresh_list()
        self.select_by_uuid(target.get("uuid"))
        self.statusBar().showMessage("已跳转到配套条目：「%s」" % target.get("title"))

    def on_entry_selected(self, current, _previous):
        """列表选中 → 渲染详情面板"""
        if current is None:
            return
        entry_uuid = current.data(Qt.UserRole)
        entry = self.db.get_entry(entry_uuid)
        if not entry:
            return
        self.current_entry = entry
        self._render_detail(entry)

    def on_ctrl_c(self):
        """Ctrl+C：详情编辑器里有选中就用系统复制，否则复制整个命令块"""
        if self.txt_commands.hasFocus() and self.txt_commands.textCursor().hasSelection():
            self.txt_commands.copy()
            self._flash("✓已复制选中文本", self.btn_copy_all)
            return
        self.copy_all()

    # ==================================================================
    # 详情渲染
    # ==================================================================
    def _render_detail(self, entry):
        """把条目数据铺到详情面板"""
        vendor_slug = dbmod.normalize_vendor(entry.get("vendor"))
        color = VENDOR_COLORS.get(vendor_slug, DEFAULT_VENDOR_COLOR)
        duration = dbmod.normalize_duration(entry.get("duration"))

        self.lbl_title.setText(entry.get("title") or "(无标题)")
        verified = int(entry.get("verified") or 0) == 1
        left = (display_platform(entry.get("platform")) if
                dbmod.normalize_platform(entry.get("platform")) == "linux"
                else (entry.get("device_type") or display_platform(entry.get("platform"))))
        meta = ("<span style='color:%s; font-weight:bold;'>%s</span> ｜ %s ｜ %s ｜ 场景：%s ｜ "
                "型号：%s ｜ <span style='color:%s;'>%s</span> ｜ 更新：%s"
                % (color, display_vendor(vendor_slug),
                   left or "-", display_os(entry.get("os_family")),
                   entry.get("category") or "-", entry.get("models") or "-",
                   VERIFIED_COLOR if verified else UNVERIFIED_COLOR,
                   "已验证" if verified else "未验证",
                   entry.get("updated_at") or "-"))
        # AI 溯源（🤖）：条目来自 AI 会话入库时显示来源，点击跳转会话定位回复
        self._ai_source_rec = (self.db.imports_for_target(entry.get("uuid")) or [None])[0]
        if self._ai_source_rec:
            meta += ("  ｜ <a href='aijump' style='color:%s;'>🤖 来源：AI 会话（%s）</a>"
                     % (TEXT_MUTED, self._ai_source_rec.get("imported_at") or ""))
        # 骨架 / 交互式标识（审计 P1/P4）：详情页元信息行内显著提示
        exec_level = str(entry.get("exec_level") or "").strip()
        if exec_level == "skeleton":
            meta += ("  ｜ <span style='color:%s; font-weight:bold;'>⚠ 骨架——待真机核对，"
                     "复制执行前须验证</span>" % WARNING)
        if exec_level == "web-only":
            meta += ("  ｜ <span style='color:%s;'>仅 Web 控制台路径</span>" % TEXT_MUTED)
        if int(entry.get("interactive") or 0) == 1:
            meta += ("  ｜ <span style='color:%s; font-weight:bold;'>⚠ 含交互式输入，"
                     "不适合脚本渲染</span>" % WARNING)
        self.lbl_meta.setText(meta)
        try:
            self.lbl_meta.linkActivated.disconnect()
        except Exception:
            pass
        if self._ai_source_rec:
            from ui_ai import open_ai_source
            self.lbl_meta.linkActivated.connect(
                lambda *_: open_ai_source(self, self._ai_source_rec))

        # Linux 条目的生效方式提示（模块 03 规格：temp 要醒目提示重启失效）
        # ★ 配套条目跳转：notes 里写了 "配套条目：《标题》" 的 temp 条目才有 perm 对应物；
        #   纯查询/诊断类命令（tcpdump/journalctl/top 等）没有持久化概念，给中性提示
        pair_title = self._find_pair_entry_title(entry)
        if duration == "temp":
            if pair_title:
                self.lbl_param_hint.setText(
                    "⚠ 本条为临时生效配置：重启后失效！持久化方案见 notes 里指向的 perm 配套条目。")
            else:
                self.lbl_param_hint.setText(
                    "本条为查询 / 诊断类命令，无持久化需求。")
        elif duration == "perm":
            self.lbl_param_hint.setText(
                "本条为持久化配置：给出配置文件内容 + 生效命令，重启后仍生效。")
        elif duration == "both":
            self.lbl_param_hint.setText("本条同时给出临时命令与持久化配置两套做法。")

        if pair_title:
            self.btn_pair.setText("→ 跳转配套条目：%s" % pair_title[:28])
            self.btn_pair.setVisible(True)
        else:
            self.btn_pair.setVisible(False)
        self.btn_goto_tree.setEnabled(self.db.count_trees() > 0 and not self.db.readonly)

        # 命令全文：默认值先渲染进去；未填参数保留 {{xxx}} 并给出提示
        text, missing, specs = renderer.render_entry(entry, {})
        self.current_text = text
        # P2 落实（schema 升级 2026-09-30）：缺必填 → 阻止复制（标红提示条 + copy 守卫）；
        # 预览显示 {{占位}} 而非残缺命令（render keep_unknown 已保证）
        self._missing_required = [m for m in missing
                                  if any(s.get("name") == m and s.get("required")
                                         for s in specs)]
        self._missing_optional = [m for m in missing if m not in self._missing_required]
        self.txt_commands.setPlainText(text)
        self.line_items = [(i + 1, ln) for i, ln in enumerate(
            renderer.split_command_lines(text)) if ln.strip()]
        self.line_index = -1

        if missing or renderer.has_params(entry.get("commands") or ""):
            hint = "含 {{参数}} 占位：%s。" % "、".join(missing) if missing else "含 {{参数}} 占位。"
            hint += "当前显示的是默认值渲染结果；双击条目或按 Ctrl+G 打开『参数化命令生成器』填写。"
            self.lbl_param_hint.setText(
                (self.lbl_param_hint.text() + "  " + hint) if self.lbl_param_hint.text() else hint)

        # 预览状态条：缺必填标红（P2）、缺选填黄条提醒
        if self._missing_required:
            self.lbl_preview_state.setText(
                "✘ 缺必填参数：%s —— 已阻止『复制全部 / 仅复制命令 / 逐条复制』；"
                "双击条目或 Ctrl+G 打开参数生成器填写。预览保留 {{占位}}，不生成残缺命令。"
                % "、".join(self._missing_required))
            self.lbl_preview_state.setVisible(True)
            set_state(self.lbl_preview_state, "err")
        elif self._missing_optional:
            self.lbl_preview_state.setText(
                "⚠ 未填选填参数：%s（预览保留 {{占位}}，可按需打开生成器填写）"
                % "、".join(self._missing_optional))
            self.lbl_preview_state.setVisible(True)
            set_state(self.lbl_preview_state, "warn")
        else:
            self.lbl_preview_state.setVisible(False)

        # 星形收藏 toggle：★ 实心 = 已收藏（P-D，不再用文字按钮）
        self.btn_fav.setChecked(bool(entry.get("favorite")))
        self.btn_fav.setText("★" if entry.get("favorite") else "☆")

        # 参数说明表（含折叠/隐藏联动）
        self._fill_param_table(specs)
        self._sync_param_tab(specs)

        # 备注（P-E：卡片式，左侧 accent 竖条 + 行距 1.5）
        self.txt_notes.setHtml(build_text_card(entry.get("notes") or "（暂无备注）", "info"))

        # 验证信息（P-E：未验证态用 warning 竖条的 info 卡片提示）
        if verified:
            verify_text = ("状态：已验证（绿色徽章）\n"
                           "验证人：%s\n验证设备型号：%s\n验证日期：%s\n"
                           "\n命令已在真机执行成功，可放心使用。"
                           % (entry.get("verified_by") or "-", entry.get("verified_model") or "-",
                              entry.get("verified_date") or "-"))
            self.txt_verify.setHtml(build_text_card(verify_text, "ok"))
        else:
            verify_text = ("状态：未验证（灰色徽章）\n\n"
                           "本条目由 AI 生成/人工整理，尚未在真机上执行验证。\n"
                           "使用前请先核对语法，建议先在测试设备或离线环境执行。\n"
                           "真机验证通过后，右键条目 →『标记已验证』，填写验证人/型号/日期，"
                           "徽章即可变绿。")
            self.txt_verify.setHtml(build_text_card(verify_text, "warn"))

        # 修改历史
        self._fill_history_table(self.db.get_history(entry.get("uuid")))

        # 只读状态下禁用编辑类按钮
        if self.db.readonly:
            self.btn_fav.setEnabled(False)
            self.btn_fav.setToolTip("只读库（U 盘写保护），无法修改收藏状态")

    def _toggle_param_panel(self, checked):
        """折叠面板开关：收起只留标题行，展开显示参数表"""
        self.tbl_params.setVisible(bool(checked))
        self.btn_param_toggle.setText(("▾ 参数说明" if checked else "▸ 参数说明")
                                      + self._param_tab_suffix)

    def _sync_param_tab(self, specs):
        """无参数条目：参数说明整页不渲染（任务2 要求）；有参数时默认收起"""
        idx = self.tab_info.indexOf(self.param_panel)
        if idx < 0:
            return
        try:
            self.tab_info.setTabVisible(idx, bool(specs))
        except AttributeError:
            pass    # 老版本 Qt 无 setTabVisible：保留常驻 tab，仅折叠内容兜底
        self._param_tab_suffix = "（%d）" % len(specs or [])
        self.btn_param_toggle.setText("▸ 参数说明" + self._param_tab_suffix)
        self.btn_param_toggle.setChecked(False)
        self.tbl_params.setVisible(False)

    def _fill_param_table(self, specs):
        """填充参数说明表（schema 升级 2026-09-30：名称|必填*|默认值|类型|说明）"""
        self.tbl_params.setRowCount(0)
        for spec in specs or []:
            row = self.tbl_params.rowCount()
            self.tbl_params.insertRow(row)
            stype = str(spec.get("type") or "").strip()
            rule = (spec.get("validate") or "").strip()
            type_text = stype or (rule.split(":")[0] if rule else "—")
            values = [
                spec.get("name", ""),
                "必填 *" if spec.get("required") else "选填",
                spec.get("default", "") or "—",
                type_text,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if col == 0:
                    cell.setFont(self._mono_font())
                if col == 1 and spec.get("required"):
                    cell.setForeground(QColor(WARNING))
                self.tbl_params.setItem(row, col, cell)

            # 说明列：description 正文 + range/example/choices 灰字附注（富文本单元格）
            choices = [str(c) for c in (spec.get("choices") or [])]
            notes = []
            if choices:
                notes.append("可选值：" + " / ".join(choices))     # enum 参数展示全集
            if str(spec.get("range") or "").strip():
                notes.append("范围：%s" % spec["range"])
            ex = str(spec.get("example") or "").strip()
            if ex and ex not in choices:
                notes.append("示例：%s" % ex)
            if spec.get("expand") == "port":
                notes.append("端口范围展开")
            body = str(spec.get("description") or "").strip()
            if not body:
                # 迁移期(desc_pending)/旧格式条目：描述待补，用灰字占位（不复述参数名充数）
                body = "（描述待补）" if (stype or spec.get("desc_pending")) else (ex or "—")
            desc_html = html.escape(body)
            if notes:
                desc_html += "<br><span style='color:%s;'>%s</span>" % (
                    TEXT_MUTED, html.escape(" ｜ ".join(notes)))
            lbl = QLabel(desc_html)
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setContentsMargins(4, 2, 4, 2)
            self.tbl_params.setCellWidget(row, 4, lbl)
            self.tbl_params.resizeRowToContents(row)

        self.tbl_params.resizeColumnsToContents()
        # 说明列吃掉剩余宽度，避免右侧几列被挤到看不见
        self.tbl_params.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_params.setColumnWidth(0, max(90, self.tbl_params.columnWidth(0)))
        for col in (1, 2, 3):
            self.tbl_params.setColumnWidth(col, max(64, min(120, self.tbl_params.columnWidth(col))))

    def _guard_missing_required(self):
        """P2 守卫：缺必填参数时阻止复制类动作，返回 True 表示已拦截"""
        if getattr(self, "_missing_required", None):
            QMessageBox.warning(
                self, "缺必填参数",
                "以下必填参数尚未填写，已阻止复制：\n\n%s\n\n"
                "双击条目或按 Ctrl+G 打开『参数化命令生成器』填写后再复制。"
                % "、".join(self._missing_required))
            return True
        return False

    def _fill_history_table(self, records):
        """填充修改历史表"""
        self.tbl_history.setRowCount(0)
        for rec in records or []:
            row = self.tbl_history.rowCount()
            self.tbl_history.insertRow(row)
            action_map = {"create": "新建", "update": "修改", "verify": "标记已验证",
                          "unverify": "取消验证", "favorite": "收藏变更",
                          "delete": "删除", "import": "导入种子库"}
            values = [rec.get("ts", ""), action_map.get(rec.get("action"), rec.get("action", "")),
                      rec.get("new_value", ""), rec.get("operator", "") or "—",
                      rec.get("old_value", "")]
            for col, value in enumerate(values):
                self.tbl_history.setItem(row, col, QTableWidgetItem(str(value)))
        self.tbl_history.resizeColumnsToContents()
        self.tbl_history.setColumnWidth(2, max(220, self.tbl_history.columnWidth(2)))

    @staticmethod
    def _mono_font():
        """等宽字体（统一走 theme.mono_font，避免两处定义不一致）"""
        return mono_font()

    def show_empty_detail(self, message=""):
        """无选中条目时的占位显示"""
        self.current_entry = None
        self.current_text = ""
        self.line_items = []
        self.lbl_title.setText("未选中条目")
        self.lbl_meta.setText(message)
        self.txt_commands.setPlainText("")
        self.lbl_param_hint.setText("")
        self.lbl_preview_state.setVisible(False)
        self._missing_required = []
        self._missing_optional = []
        self._sync_param_tab([])
        self.txt_notes.setHtml("")
        self.txt_verify.setHtml("")
        self.tbl_params.setRowCount(0)
        self.tbl_history.setRowCount(0)
        self.btn_pair.setVisible(False)
        self.lbl_line_pos.setText("")
        # 星标复位（未选中条目时不该留着上一条的收藏态）
        self.btn_fav.setChecked(False)
        self.btn_fav.setText("☆")

    # ==================================================================
    # 复制（与 Xshell 配合的核心）
    # ==================================================================
    def _skeleton_copy_guard(self, entry):
        """骨架条目复制前的一次性提醒（审计 P1），状态挂在主窗 _ui_state"""
        return guard_skeleton_copy(self, entry)

    def copy_all(self):
        """复制全部：整块脚本（含注释）原样进剪贴板"""
        if not self.current_text:
            self.statusBar().showMessage("没有可复制的内容。")
            return
        if self._guard_missing_required():
            return
        self._skeleton_copy_guard(self.current_entry)
        if not copy_to_clipboard(self.current_text):
            self.statusBar().showMessage("复制失败（剪贴板被占用），请手动选中后复制。")
            return
        lines = renderer.count_effective_lines(self.current_text)
        self._flash("✓已复制", self.btn_copy_all)
        self.statusBar().showMessage("已复制全部命令（%d 条有效命令）→ 切到 Xshell 直接粘贴" % lines)

    def copy_commands_only(self):
        """仅复制命令：剔除 ! # 注释行，粘贴到 Xshell 直接可跑"""
        if not self.current_text:
            self.statusBar().showMessage("没有可复制的内容。")
            return
        if self._guard_missing_required():
            return
        self._skeleton_copy_guard(self.current_entry)
        text = renderer.strip_comments(self.current_text)
        if not copy_to_clipboard(text):
            self.statusBar().showMessage("复制失败（剪贴板被占用），请手动选中后复制。")
            return
        lines = renderer.count_effective_lines(text)
        self._flash("✓已复制", self.btn_copy_cmd)
        self.statusBar().showMessage("已复制纯命令（已剔除注释行，共 %d 条）" % lines)

    def toggle_line_mode(self, checked):
        """逐条复制模式开关"""
        self.line_mode = bool(checked)
        self.line_index = -1
        self.btn_next_line.setEnabled(self.line_mode and bool(self.line_items))
        if self.line_mode:
            self.btn_copy_line.setText("退出逐条")
            self.lbl_line_pos.setText("共 %d 条，按『下一条』或 Ctrl+Enter 逐条走"
                                      % len(self.line_items))
            self.statusBar().showMessage("逐条复制模式：点『下一条 ▶』逐条高亮并复制到剪贴板")
            self.next_line()
        else:
            self.btn_copy_line.setText("逐条复制")
            self.lbl_line_pos.setText("")
            cursor = self.txt_commands.textCursor()
            cursor.clearSelection()
            self.txt_commands.setTextCursor(cursor)
            self.statusBar().showMessage("已退出逐条复制模式。")

    def next_line(self):
        """逐条复制：高亮下一条并把该条命令送进剪贴板"""
        if not self.line_mode or not self.line_items:
            return
        if self._guard_missing_required():
            self.line_index = -1        # 回到起点，填完参数后从头逐条走
            return
        self.line_index += 1
        if self.line_index >= len(self.line_items):
            self.lbl_line_pos.setText("已到最后一条（共 %d 条）" % len(self.line_items))
            self.statusBar().showMessage("已到最后一条命令。")
            return

        line_no, text = self.line_items[self.line_index]
        self._select_line(line_no)
        self._skeleton_copy_guard(self.current_entry)

        if not copy_to_clipboard(text):
            # 逐条模式下复制失败：明确告知并停在下标，避免"以为复制了"
            self.statusBar().showMessage("复制失败（剪贴板被占用），请手动复制该行。")
            self.line_index -= 1        # 回退下标，让用户可再点一次
            return
        self.lbl_line_pos.setText("第 %d / %d 条" % (self.line_index + 1, len(self.line_items)))
        self.statusBar().showMessage("逐条复制 %d/%d：%s"
                                     % (self.line_index + 1, len(self.line_items), text.strip()))

    def _select_line(self, line_no):
        """在命令全文里高亮指定行（1 基行号）"""
        cursor = self.txt_commands.textCursor()
        cursor.movePosition(QTextCursor.Start)
        for _ in range(max(0, line_no - 1)):
            if not cursor.movePosition(QTextCursor.Down):
                break
        cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
        self.txt_commands.setTextCursor(cursor)
        self.txt_commands.centerCursor()

    def toggle_favorite(self):
        """Ctrl+D：收藏 / 取消收藏当前条目"""
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目再收藏。")
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库处于只读状态（U 盘写保护），无法修改收藏。")
            return
        new_value = self.db.toggle_favorite(self.current_entry.get("uuid"))
        self.current_entry["favorite"] = new_value
        # 星形 toggle 同步（★ 实心 = 已收藏）。
        # setChecked 不会触发 clicked，所以不会重入本函数。
        self.btn_fav.setChecked(bool(new_value))
        self.btn_fav.setText("★" if new_value else "☆")
        self.refresh_list()
        self.statusBar().showMessage("已收藏。" if new_value else "已取消收藏。")

    def _flash(self, text, button, keep_text=None):
        """按钮短暂显示反馈文字，1.2 秒后还原"""
        original = keep_text or button.property("baseText") or button.text()
        if not button.property("baseText"):
            button.setProperty("baseText", original)
        button.setText(text)
        QTimer.singleShot(1200, lambda: button.setText(button.property("baseText") or original))

    # ==================================================================
    # 第 2 轮：生成器 / 编辑器 入口
    # ==================================================================
    def on_item_double_clicked(self, _item):
        """双击列表：带参数的条目直接开生成器，无参数的直接复制"""
        if self.has_params(self.current_entry):
            self.on_open_generator()
        else:
            self.copy_all()

    @staticmethod
    def has_params(entry):
        """条目是否含 {{参数}} 占位"""
        return bool(entry) and renderer.has_params(entry.get("commands") or "")

    def on_open_generator(self):
        """打开参数化命令生成器（模态）"""
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        from ui_generator import GeneratorDialog
        dlg = GeneratorDialog(self.db, self.current_entry, self)
        dlg.exec_()          # 生成器内部自行复制/导出，关闭后主窗数据无需刷新
        self.statusBar().showMessage("生成器已关闭。")

    def on_open_package(self):
        """打开配置包生成器：勾选多条命令合并导出"""
        entries = self.entries_in_view or self.db.all_entries()
        if not entries:
            QMessageBox.information(self, "没有条目", "当前筛选结果为空，先重置筛选再试。")
            return
        from ui_generator import PackageDialog
        dlg = PackageDialog(self.db, entries, self)
        dlg.exec_()
        self.statusBar().showMessage("配置包生成器已关闭。")

    def on_new_entry(self):
        """新建命令条目"""
        if self._guard_readonly():
            return
        from ui_editor import EntryEditorDialog
        dlg = EntryEditorDialog(self.db, None, self, self.operator)
        if dlg.exec_() == QDialog.Accepted:
            self.operator = dlg.saved_operator() or self.operator
            self.refresh_all()
            self.select_by_title(dlg.saved_title())
            self.statusBar().showMessage("已新建条目「%s」（未验证状态）。" % dlg.saved_title())

    def on_edit_entry(self):
        """编辑当前选中条目"""
        if self._guard_readonly():
            return
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        from ui_editor import EntryEditorDialog
        entry_uuid = self.current_entry.get("uuid")     # 刷新后当前项会重建，先记住 UUID
        dlg = EntryEditorDialog(self.db, self.current_entry, self, self.operator)
        if dlg.exec_() == QDialog.Accepted:
            self.operator = dlg.saved_operator() or self.operator
            self.refresh_all()
            self.select_by_uuid(entry_uuid)
            self.statusBar().showMessage("已保存条目「%s」。" % dlg.saved_title())

    def on_duplicate_entry(self):
        """复制当前条目为副本（改几个参数就能变成新条目）"""
        if self._guard_readonly():
            return
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        raw = dict(self.current_entry)
        raw["uuid"] = ""
        raw["title"] = (raw.get("title") or "") + " - 副本"
        raw["verified"] = 0
        raw["favorite"] = 0
        try:
            self.db.add_entry(raw, operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "复制失败", str(exc))
            return
        self.refresh_all()
        self.statusBar().showMessage("已复制为「%s」（未验证状态）。" % raw["title"])

    def on_delete_entry(self):
        """删除当前条目（二次确认）"""
        if self._guard_readonly():
            return
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        title = self.current_entry.get("title") or ""
        box = QMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setIcon(QMessageBox.Warning)
        box.setText("确认删除条目「%s」？" % title)
        box.setInformativeText("删除后内容不可恢复（history 表保留删除记录）。\n"
                               "只是临时不用的话，建议先『导出 .nlb』备份。")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec_() != QMessageBox.Yes:
            return
        try:
            self.db.delete_entry(self.current_entry.get("uuid"), operator=self.operator)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.current_entry = None
        self.refresh_all()
        self.statusBar().showMessage("已删除「%s」。" % title)

    def on_open_library(self):
        """打开命令库管理器（全部条目一览 + 增删改 + 历史）"""
        from ui_editor import LibraryEditorDialog
        dlg = LibraryEditorDialog(self.db, self, self.operator)
        dlg.exec_()
        self.refresh_all()
        self.statusBar().showMessage("命令库管理器已关闭，主窗已刷新。")

    def _guard_readonly(self):
        """只读库（U 盘写保护）拦截写操作，返回 True 表示已拦截"""
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式",
                                "命令库处于只读状态（U 盘写保护），无法执行写操作。\n"
                                "如需修改，请关闭 U 盘写保护后重新打开程序。")
            return True
        return False

    def select_by_title(self, title):
        """按标题定位列表项（新建/保存后回到刚编辑的条目）"""
        if not title:
            return
        for row, entry in enumerate(self.entries_in_view):
            if entry.get("title") == title:
                self.list_entries.setCurrentRow(row)
                return

    def select_by_uuid(self, entry_uuid):
        """按 UUID 定位列表项"""
        if not entry_uuid:
            return
        for row, entry in enumerate(self.entries_in_view):
            if entry.get("uuid") == entry_uuid:
                self.list_entries.setCurrentRow(row)
                return

    # ==================================================================
    # 右键菜单 / 验证
    # ==================================================================
    def on_list_context_menu(self, pos):
        """条目列表右键菜单"""
        item = self.list_entries.itemAt(pos)
        if not item:
            return
        self.list_entries.setCurrentItem(item)
        entry = self.current_entry
        if not entry:
            return

        menu = QMenu(self)
        act_gen = menu.addAction("参数生成器…")
        act_copy = menu.addAction("复制全部命令")
        act_copy_cmd = menu.addAction("仅复制命令（去注释）")
        menu.addSeparator()
        act_fav = menu.addAction("取消收藏" if entry.get("favorite") else "加入收藏")
        act_verify = menu.addAction("标记已验证…")
        act_unverify = menu.addAction("取消验证")
        menu.addSeparator()
        act_edit = menu.addAction("编辑条目…")
        act_dup = menu.addAction("复制一份")
        act_del = menu.addAction("删除条目…")
        menu.addSeparator()
        act_export = menu.addAction("导出为 .txt…")
        act_history = menu.addAction("查看修改历史…")
        if self.db.readonly:
            act_fav.setEnabled(False)
            act_verify.setEnabled(False)
            act_unverify.setEnabled(False)
            act_edit.setEnabled(False)
            act_dup.setEnabled(False)
            act_del.setEnabled(False)
        if int(entry.get("verified") or 0) == 1:
            act_verify.setEnabled(False)
        else:
            act_unverify.setEnabled(False)
        if not self.has_params(entry):
            act_gen.setEnabled(False)
            act_gen.setToolTip("该条目没有 {{参数}}，无需生成器")

        chosen = menu.exec_(self.list_entries.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_gen:
            self.on_open_generator()
        elif chosen == act_copy:
            self.copy_all()
        elif chosen == act_copy_cmd:
            self.copy_commands_only()
        elif chosen == act_fav:
            self.toggle_favorite()
        elif chosen == act_verify:
            self.on_mark_verified()
        elif chosen == act_unverify:
            self.on_unmark_verified()
        elif chosen == act_edit:
            self.on_edit_entry()
        elif chosen == act_dup:
            self.on_duplicate_entry()
        elif chosen == act_del:
            self.on_delete_entry()
        elif chosen == act_export:
            self.on_export_txt()
        elif chosen == act_history:
            self.show_history_dialog()

    def on_mark_verified(self):
        """标记已验证：填验证人/型号/日期 → 徽章变绿"""
        if not self.current_entry:
            self.statusBar().showMessage("先选中一个条目。")
            return
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库处于只读状态（U 盘写保护），无法标记验证。")
            return
        dlg = VerifyDialog(self.current_entry, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        by, model, date = dlg.values()
        self.db.mark_verified(self.current_entry.get("uuid"), by, model, date)
        self.refresh_list()
        self.statusBar().showMessage("已标记为已验证（绿色徽章）：%s / %s" % (by, model))

    def on_unmark_verified(self):
        """取消验证：徽章退回灰色"""
        if not self.current_entry or self.db.readonly:
            return
        if QMessageBox.question(self, "取消验证", "确认把该条目退回『未验证』灰色状态？",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.db.mark_verified(self.current_entry.get("uuid"), "", "", unverify=True)
        self.refresh_list()
        self.statusBar().showMessage("已取消验证，徽章恢复灰色。")

    # ==================================================================
    # 修改历史
    # ==================================================================
    def show_history_dialog(self):
        """查看当前条目的完整演变历史（history 表）"""
        if not self.current_entry:
            return
        entry = self.current_entry
        records = self.db.get_history(entry.get("uuid"), limit=500)

        action_map = {"create": "新建", "update": "修改", "verify": "标记已验证",
                      "unverify": "取消验证", "favorite": "收藏变更",
                      "delete": "删除", "import": "导入种子库"}

        dlg = QDialog(self)
        dlg.setWindowTitle("修改历史 · %s" % (entry.get("title") or ""))
        dlg.resize(900, 520)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["时间", "动作", "变更内容", "旧值", "操作人"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for rec in records:
            row = table.rowCount()
            table.insertRow(row)
            values = [rec.get("ts", ""),
                      action_map.get(rec.get("action"), rec.get("action", "")),
                      rec.get("new_value", ""),
                      rec.get("old_value", ""),
                      rec.get("operator", "") or "—"]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

        label = QLabel("共 %d 条变更记录（新建/修改/验证/收藏/删除/导入均会留痕）"
                       % len(records))
        label.setObjectName("Muted")

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(dlg.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(label, 1)
        bottom.addWidget(btn_close)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(table, 1)
        root.addLayout(bottom)
        dlg.exec_()

    # ==================================================================
    # 导入导出
    # ==================================================================
    def on_import_nlb(self):
        """导入 .nlb：按 UUID 合并去重，冲突保留已验证那条"""
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库处于只读状态（U 盘写保护），无法导入。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 .nlb 命令库文件",
                                              get_base_dir(), "命令库文件 (*.nlb *.json)")
        if not path:
            return
        try:
            added, updated, skipped = self.db.import_nlb(path, merge=True)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", "文件解析失败：\n%s" % exc)
            return
        self.refresh_all()
        QMessageBox.information(self, "导入完成",
                                "新增 %d 条，更新 %d 条，跳过 %d 条。\n\n"
                                "（同 UUID 冲突时保留『已验证』的那一条）" % (added, updated, skipped))

    def on_export_nlb(self):
        """导出整个库为 .nlb"""
        default = os.path.join(get_base_dir(),
                               "%s_%s.nlb" % (APP_NAME, datetime.datetime.now().strftime("%Y%m%d")))
        path, _ = QFileDialog.getSaveFileName(self, "导出整个命令库", default,
                                              "命令库文件 (*.nlb)")
        if not path:
            return
        if not path.lower().endswith(".nlb"):
            path += ".nlb"
        try:
            count = self.db.export_nlb(path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage("已导出 %d 条到 %s" % (count, path))
        QMessageBox.information(self, "导出完成", "已导出 %d 条到：\n%s" % (count, path))

    def on_export_txt(self):
        """导出当前条目（渲染后）为 .txt"""
        if not self.current_entry or not self.current_text:
            self.statusBar().showMessage("先选中一个条目。")
            return
        title = (self.current_entry.get("title") or "command").replace("/", "_").replace("\\", "_")
        default = os.path.join(get_base_dir(), "%s.txt" % title)
        path, _ = QFileDialog.getSaveFileName(self, "导出为 .txt", default, "文本文件 (*.txt)")
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(self.current_text)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage("已导出：%s" % path)

    # ==================================================================
    # 启动引导 / 首次运行
    # ==================================================================
    def startup_flow(self):
        """
        启动引导，按三种情况分别处理：
            1. 首次运行（无 db，本次自动建库）→ 完整首次引导（含免责声明 + 导入种子库）
            2. 库是空的但存在种子库文件（上次选了"空库使用"）→ 再给一次导入机会
            3. U 盘只读 → 明确告知只读模式与影响
        """
        if self.db.closed:
            return
        if self.db.was_created:
            self.first_run_flow(force=True)
            return

        if self.db.count() == 0 and self.db.count_seed_files() > 0:
            box = QMessageBox(self)
            box.setWindowTitle("命令库还是空的")
            box.setIcon(QMessageBox.Information)
            box.setText("检测到 %d 个内置种子库文件，但当前命令库一条都没有。"
                        % self.db.count_seed_files())
            box.setInformativeText("是否现在导入？导入后即可搜索、浏览、复制；\n"
                                   "所有条目均为『未验证』灰色状态，需真机验证后才变绿。")
            btn_yes = box.addButton("导入种子库", QMessageBox.AcceptRole)
            box.addButton("继续空库使用", QMessageBox.RejectRole)
            box.setDefaultButton(btn_yes)
            box.exec_()
            if box.clickedButton() is btn_yes:
                self._do_seed_import()
            else:
                self.statusBar().showMessage(
                    "已选择空库启动。可随时从『文件 → 导入命令库』导入 .nlb，"
                    "或从『帮助 → 首次启动提示』重新导入种子库。")
            return

        if self.db.readonly:
            QMessageBox.warning(
                self, "只读模式",
                "检测到程序所在目录（或 U 盘）为写保护状态，命令库已进入【只读模式】：\n\n"
                "  · 可以搜索、浏览、复制命令（不影响现场使用）\n"
                "  · 不能新建/编辑/删除条目、不能标记已验证、不能导入\n\n"
                "如需修改，请关闭 U 盘写保护开关后重新打开程序。")
            self.statusBar().showMessage("只读模式：写操作已禁用。")

    def first_run_flow(self, force=False):
        """首次运行：无 db 自动建空库 → 询问是否导入内置种子库（含免责提示）"""
        if not force and not self.db.was_created:
            return
        self.db.was_created = False

        seed_files = 0
        try:
            seed_files = self.db.count_seed_files()
        except Exception:
            seed_files = 0

        box = QMessageBox(self)
        box.setWindowTitle("首次启动 · 请先读这段")
        box.setIcon(QMessageBox.Information)
        if seed_files:
            box.setText("命令库已就绪，检测到 %d 个厂商种子库文件，是否导入？" % seed_files)
        else:
            box.setText("命令库已就绪，是否需要导入内置演示数据？")
        box.setInformativeText(
            "【重要 · 验证状态说明】\n"
            "内置命令库由 AI 生成 / 人工整理，全部标记为『未验证』（灰色徽章）。\n"
            "未经真机执行验证前，请勿直接用于生产设备！\n"
            "真机验证通过后，右键条目 →『标记已验证』填写验证人/型号/日期，徽章变绿。\n\n"
            "【离网部署说明】\n"
            "· 数据库文件与程序同目录（command_lib.db），U 盘整盘拷贝即完成迁移\n"
            "· 本工具不含任何终端/SSH/串口/联网功能，命令由外部 Xshell/SecureCRT 执行\n"
            "· U 盘写保护时自动进入只读模式，编辑功能会被禁用\n"
            "· 命令库处于只读状态时无法导入，请先关闭写保护")
        btn_yes = box.addButton("导入内置种子库", QMessageBox.AcceptRole)
        btn_no = box.addButton("先空库使用", QMessageBox.RejectRole)
        box.setDefaultButton(btn_yes)
        box.exec_()

        if box.clickedButton() is btn_no:
            self.statusBar().showMessage(
                "已选择空库启动，可随时从『文件 → 导入命令库』导入 .nlb。")
            return

        self._do_seed_import()

    def _do_seed_import(self):
        """
        执行种子库导入，成功后展示"上车指引"。返回 (新增, 更新, 跳过)。

        ★ 重入守卫（第 4 轮 P2-5）：导入过程中会调 QApplication.processEvents() 让界面
          保持响应，这会**重入事件循环** —— 用户此刻还能点菜单（例如再点一次"导入命令库"、
          或点删除/编辑），从而在同一个 sqlite 连接上叠加写事务。
          这里用 _importing 标志拒绝重入，并在导入期间禁用菜单栏；首次自动导种子同样受保护。
        """
        if self.db.closed:
            return (0, 0, 0)
        if getattr(self, "_importing", False):
            self.statusBar().showMessage("正在导入中，请稍候…")
            return (0, 0, 0)
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式",
                                "命令库处于只读状态（U 盘写保护），无法导入种子库。")
            return (0, 0, 0)
        self._importing = True
        self.statusBar().showMessage("正在导入种子库，请稍候…")
        self.menuBar().setEnabled(False)
        try:
            QApplication.processEvents()
            if self.db.count_seed_files():
                added, updated, skipped = self.db.import_seed_dir()
            else:
                # 没有种子库文件时退回内置演示数据，保证界面不是空的
                added, updated, skipped = self.db.seed_demo()
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return (0, 0, 0)
        finally:
            self._importing = False
            self.menuBar().setEnabled(True)

        self.refresh_all()
        self._show_getting_started(added, updated, skipped)
        return (added, updated, skipped)

    def _show_getting_started(self, added, updated, skipped):
        """导入完成后的"上车指引"：导入统计 + 三步上手 + 验证提醒"""
        if self.db.closed:
            return
        stats = self.db.stats()
        pstats = self.db.platform_stats()
        # 按厂商统计（显示名）
        by_vendor = {}
        by_category = set()
        for entry in self.db.search():
            name = display_vendor(entry.get("vendor"))
            by_vendor[name] = by_vendor.get(name, 0) + 1
            if entry.get("category"):
                by_category.add(entry["category"])
        vendor_line = "｜".join("%s %d 条" % (k, v)
                               for k, v in sorted(by_vendor.items(), key=lambda x: -x[1]))

        # 颜色取自主题令牌（HTML 内联色无法走 QSS，只能在这里引用 theme 常量）
        # ★ 注意：先拼接成 template 再 .format()，否则 .format 只会作用在最后一段字面量上
        template = (
            "<b>导入完成</b>　新增 %d 条 / 更新 %d 条 / 跳过 %d 条<br><br>"
            "<b>当前命令库</b>：共 %d 条（网络设备 %d ｜ Linux %d）<br>"
            "厂商 %d 家 ｜ 场景 %d 类 ｜ 已验证 0 条<br>"
            "<span style='color:{secondary};'>%s</span><br><br>"
            "<b>三步上手</b><br>"
            "1. Ctrl+F 搜关键字（如 trunk / nat / 保存 / 网卡），或点左侧树逐层筛<br>"
            "2. 双击条目（或 Ctrl+G）打开<b>参数生成器</b>，填参数看实时预览<br>"
            "3. 『复制全部』整块粘到 Xshell；或『仅复制命令』去掉注释；"
            "排查单条命令用『逐条复制』<br><br>"
            "<b>多设备批量下发</b>：Ctrl+B 打开配置包生成器，勾选多条命令合并成一个脚本，"
            "头部会自动写上设备型号、生成时间、操作人、场景说明<br><br>"
            "<span style='color:{warning};'><b>⚠ 验证提醒</b>：以上条目全部为 AI 生成，"
            "未经真机验证（灰色徽章）。请按团队流程逐条上机验证，"
            "验证通过后右键 →『标记已验证』填写验证人/设备型号/日期。</span>"
        )
        text = template.format(secondary=TEXT_SECONDARY, warning=WARNING) % (
            added, updated, skipped, stats["total"],
            pstats.get("network", 0), pstats.get("linux", 0),
            len(by_vendor), len(by_category), vendor_line)

        box = QMessageBox(self)
        box.setWindowTitle("开始使用 · 上车指引")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(text)
        btn_guide = box.addButton("查看完整使用说明", QMessageBox.ActionRole)
        box.addButton("开始使用", QMessageBox.AcceptRole)
        box.exec_()
        if box.clickedButton() is btn_guide:
            self.show_guide()
        self.statusBar().showMessage("种子库已就绪：共 %d 条，可开始搜索使用。" % stats["total"])

    def show_guide(self):
        """使用说明"""
        QMessageBox.information(
            self, "使用说明",
            "【搜索】Ctrl+F 聚焦搜索框，输入即搜（标题/描述/命令内容/备注/型号全字段 LIKE 匹配）\n"
            "【过滤链】设备类型 → 厂商 → OS 版本 → 场景分类，可叠加『只看收藏 / 只看已验证』\n\n"
            "【复制三模式】\n"
            "  复制全部    整块脚本（含注释）进剪贴板，粘到 Xshell 直接跑\n"
            "  仅复制命令  剔除 ! # 注释行，只留纯命令\n"
            "  逐条复制    逐条高亮 + Ctrl+Enter 下一条，排查单条命令时精确定位\n\n"
            "【参数化生成器 Ctrl+G / 双击条目】\n"
            "  自动按 {{参数}} 生成表单，含类型校验（VLAN 1-4094、IP、端口范围…）\n"
            "  端口范围按厂商命名规则展开：0/1-0/10 → 思科 Gi0/1…｜华为 Eth0/0/1…｜H3C Gi1/0/1…\n"
            "  右侧实时预览，可复制全部 / 仅复制命令 / 导出 .txt\n\n"
            "【配置包生成器 Ctrl+B】\n"
            "  勾选多条命令合并成一个脚本，头部自动加注释块（型号/时间/操作人/场景）\n"
            "  双击带参数的条目可单独填写参数，未填则用默认值\n\n"
            "【命令库编辑器 Ctrl+N 新建 / Ctrl+E 编辑 / Ctrl+L 库管理器】\n"
            "  界面化增删改，参数表可视化编辑，不需要手写 JSON/SQL\n"
            "  新条目一律为『未验证』（灰色）；真机验证通过后右键『标记已验证』变绿\n"
            "  每次修改（含验证、收藏、删除）都写入 history 表，可查条目演变历史\n\n"
            "【内置种子库】\n"
            "  Cisco IOS ｜ 华为 VRP5(S交换机) ｜ 华为 VRP8(AR路由器) ｜ H3C Comware7 ｜ 锐捷 RGOS ｜\n"
            "  FortiOS ｜ Junos ｜ 深信服 AF ｜ 天融信 TopOS ｜ PAN-OS ｜ 中兴 ZXR10\n"
            "  共 11 个厂商文件、每厂商覆盖 VLAN、Trunk、静态路由、OSPF、ACL、NAT、\n"
            "  SSH、密码、SNMP、保存配置 十个场景，全部为『未验证』状态\n"
            "  seed_data/ 目录与程序同目录，直接改 JSON 即可扩充，不需要重新打包 exe\n"
            "  （深信服 AF / 天融信为 Web 控制台操作路径清单，正文首行已标注，勿直接粘贴执行）\n\n"
            "【快捷键】Ctrl+F 搜索 / Ctrl+G 生成器 / Ctrl+B 配置包 / Ctrl+N 新建 / Ctrl+E 编辑\n"
            "          Ctrl+L 库管理器 / Ctrl+C 复制命令块 / Ctrl+D 收藏 / F5 刷新 / Ctrl+Q 退出\n\n"
            "【数据安全】本机无任何联网行为，命令库随 U 盘走，可导出 .nlb 在团队间同步。")

    def show_about(self):
        """关于"""
        seed_files = self.db.count_seed_files()
        # HTML 内联色 → 引用主题令牌（先拼接成 template，再交给 .format）
        template = (
            "<b>离网网络运维工具箱 </b><br><br>"
            "版本：v%s<br>"
            "技术栈：Python 3.9+ / PyQt5 / SQLite<br>"
            "库文件：command_lib.db（与程序同目录）<br>"
            "种子库：%d 个厂商文件（seed_data/，改库不需重新打包）<br>"
            "厂商：Cisco / 华为 / H3C / 锐捷 / Fortinet / Juniper / 深信服 / 天融信 / "
            "Palo Alto / 中兴<br><br>"
            "<span style='color:{warning};'>本工具不包含任何终端、SSH、串口、联网功能，<br>"
            "命令的执行者永远是外部 Xshell / SecureCRT。</span>"
        )
        QMessageBox.about(self, "关于 %s" % APP_NAME,
                          template.format(warning=WARNING) % (APP_VERSION, seed_files))

    # ==================================================================
    # 关闭
    # ==================================================================
    def closeEvent(self, event):
        # 布局记忆：窗口尺寸 + 三栏比例（写不进去也不影响关闭）
        self._save_ui_state()
        try:
            self.db.close()
        except Exception:
            pass
        event.accept()
