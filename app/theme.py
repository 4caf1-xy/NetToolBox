# -*- coding: utf-8 -*-
"""
theme.py —— NetToolBox 视觉设计系统（唯一真源）

设计目标：深夜机房盯屏场景下的深色主题，纯 PyQt5 + QSS 实现。

【零新依赖】只用 PyQt5 自带能力：
    · 不用 qdarkstyle / qt-material / 任何第三方主题库
    · 不加载任何图标文件（图标一律用 Unicode 字形或 QPainter 自绘）
    · 不联网

【本文件提供】
    1. 设计令牌（颜色 / 字体 / 圆角 / 间距刻度）—— Python 常量
    2. DARK_QSS —— 全站唯一样式表（app.setStyleSheet 一次生效，各 Tab/对话框自动继承）
    3. 公共 helper —— 字体、QSplitter grip 手柄、高分屏、布局记忆读写

【为什么不用 str.format 拼 QSS】
    QSS 里有 200+ 对样式大括号（QWidget { ... }），用 str.format 必须逐个写成
    {{{{ }}}} ，可读性直接崩塌且极易漏转义。这里用**等价的令牌替换**实现：
    QSS 中以 {token} 形式引用令牌，渲染时由同一个 TOKENS 字典驱动替换。
    效果与 .format 完全一致（同一份令牌 → 同一条 QSS），但 QSS 保持原样可读。
    令牌名写错会**立刻抛 KeyError**，不会静默留个空色值 —— 这是刻意为之。

改动约定：
    · 各 UI 模块不要再写内联 setStyleSheet 定颜色，一律走本文件的 objectName 约定
    · 动态颜色（厂商色块）例外：那是数据驱动，不是主题
"""

import json
import os
import re

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QFontDatabase, QPolygonF
from PyQt5.QtWidgets import QApplication, QSplitter, QSplitterHandle, QComboBox


# ===========================================================================
# 一、设计令牌
# ===========================================================================
# ---- 背景三阶（明度递增：窗口底 → 面板 → 浮起）----
BG_WINDOW = "#1b1d23"
BG_PANEL = "#232530"
BG_RAISED = "#2a2d3a"
CODE_BG = "#16181d"          # 命令代码块：比窗口底再深一档，突出"这是代码"

# ---- 边框 ----
BORDER = "#333745"
BORDER_HOVER = "#4a4f61"

# ---- 语义色 ----
ACCENT = "#4f8cff"          # 选中 / hover / 焦点
ACCENT_HOVER = "#6ba0ff"
ACCENT_PRESSED = "#3f7ae8"
SUCCESS = "#3fbf6f"         # 已验证徽章
WARNING = "#e0a83c"         # 未验证告警 / 参数占位
DANGER = "#e05656"          # 错误 / 删除

# ---- 文字三阶 ----
TEXT_PRIMARY = "#e8eaf0"
TEXT_SECONDARY = "#9aa0b0"
TEXT_MUTED = "#6a7080"

# ---- 半透明强调（hover 底 / 徽章底）----
#   Qt QSS 的 rgba() 第 4 位是 0-255 整数（不是 CSS 的 0-1 小数）
ACCENT_12 = "rgba(79, 140, 255, 31)"      # 12% 强调底
ACCENT_20 = "rgba(79, 140, 255, 51)"      # 20% 强调底（hover 强调）
SUCCESS_14 = "rgba(63, 191, 111, 36)"
WARNING_14 = "rgba(224, 168, 60, 36)"
DANGER_14 = "rgba(224, 86, 86, 36)"

# ★ 列表/树/表格的"选中行底色"：accent 12% 混到窗口底后的**不透明**等价色。
#   为什么不用 rgba 直接写：QStyleSheetStyle 画视图项时，会先按 palette 的
#   Highlight 铺一层选中底，再叠加 QSS 的 rgba —— 两层叠加后比预期亮一大截
#   （实测选中项变成刺眼的纯蓝实底）。用不透明色 = 视觉等价、且只画一层。
SEL_BG = "#212a3d"

# ---- 圆角 / 间距刻度 ----
RADIUS = 6
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

# ---- 字体 ----
UI_FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif'
MONO_FONT_FAMILY = 'Consolas, "Cascadia Mono", monospace'
UI_FONT_SIZE = "9pt"
MONO_FONT_SIZE = "10pt"

# ---- 分隔条 ----
HANDLE_WIDTH = 8

# ---- 状态色（沿用原配色语义，纳入主题统一管理）----
VERIFIED_COLOR = SUCCESS
UNVERIFIED_COLOR = TEXT_MUTED
FAVORITE_COLOR = WARNING

# ---- 命令语法高亮配色（P-G：与主题同一套，改这里即全站生效）----
CODE_IP_COLOR = "#a98bc4"       # IPv4 地址（低饱和紫，弱于关键字）
CODE_IFACE_COLOR = "#7fbfb0"    # 接口名 / 端口号（低饱和青，弱于关键字）

# ---- 厂商配色（数据驱动，不由主题推导，集中在此便于全站引用）----
#   ★ 按 vendor slug 索引（库里存 slug，界面显示中文/品牌名）
VENDOR_COLORS = {
    "cisco": "#1f6feb",
    "huawei": "#c9302c",
    "h3c": "#2e8b57",
    "ruijie": "#8e44ad",
    "fortinet": "#d9480f",
    "juniper": "#0f7b6c",
    "sangfor": "#1a73a8",
    "topsec": "#b8860b",
    "paloalto": "#c2410c",
    "zte": "#3b5bdb",
    # 占位厂商（暂无条目，树里仍占位显示）
    "arista": "#5b6bbf",
    "checkpoint": "#7d2a8c",
    "hillstone": "#2f6f7f",
    "qianxin": "#8c3b3b",
    "venustech": "#4a7d2a",
    "nsfocus": "#2a6f4a",
    "maipu": "#6f5a2a",
    "digitalchina": "#4a4a8c",
    "anheng": "#8c4a2a",
    # Linux 发行版（模块 03）
    "rhel": "#a03030",
    "centos": "#7a5aa0",
    "ubuntu": "#c06020",
    "debian": "#a02060",
    "openeuler": "#2070a0",
    "kylin": "#a05a20",
    "ulos": "#3060a0",
    # 跨发行版 Linux 运维工具（transfer_tools 等）
    "ops": "#4a8a6a",
}
DEFAULT_VENDOR_COLOR = "#6e7681"

# ---- 平台配色（树顶层两个分支）----
PLATFORM_COLORS = {"network": "#4a9eff", "linux": "#d29922"}

# 令牌字典：QSS 渲染时按名取值（写错 → KeyError，不静默）
TOKENS = {
    "bg_window": BG_WINDOW,
    "bg_panel": BG_PANEL,
    "bg_raised": BG_RAISED,
    "code_bg": CODE_BG,
    "border": BORDER,
    "border_hover": BORDER_HOVER,
    "accent": ACCENT,
    "accent_hover": ACCENT_HOVER,
    "accent_pressed": ACCENT_PRESSED,
    "accent_12": ACCENT_12,
    "accent_20": ACCENT_20,
    "sel_bg": SEL_BG,
    "success": SUCCESS,
    "success_14": SUCCESS_14,
    "warning": WARNING,
    "warning_14": WARNING_14,
    "danger": DANGER,
    "danger_14": DANGER_14,
    "text_primary": TEXT_PRIMARY,
    "text_secondary": TEXT_SECONDARY,
    "text_muted": TEXT_MUTED,
    "ui_font": UI_FONT_FAMILY,
    "mono_font": MONO_FONT_FAMILY,
    "ui_size": UI_FONT_SIZE,
    "mono_size": MONO_FONT_SIZE,
    "radius": str(RADIUS),
    "handle_w": str(HANDLE_WIDTH),
}


# ===========================================================================
# 二、QSS 模板 + 渲染
# ===========================================================================
# 用法：颜色/尺寸位置写 {token}，其余 QSS 原样书写（大括号不需要转义）
_QSS_TEMPLATE = """
/* ====================== 全局 ====================== */
QWidget {
    color: {text_primary};
    font-family: {ui_font};
    font-size: {ui_size};
}
QMainWindow, QDialog {
    background-color: {bg_window};
}
/* QLabel 默认透明：否则会盖住列表项的 hover/选中底色（卡片式列表的关键） */
QLabel { background: transparent; }
QToolTip {
    background-color: {bg_raised};
    color: {text_primary};
    border: 1px solid {border_hover};
    border-radius: {radius}px;
    padding: 6px 8px;
    font-size: {ui_size};
}

/* ====================== 菜单栏 / 菜单 ====================== */
QMenuBar {
    background-color: {bg_panel};
    border-bottom: 1px solid {border};
    padding: 2px 4px;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 12px;
    border-radius: {radius}px;
}
QMenuBar::item:selected { background-color: {bg_raised}; color: {text_primary}; }
QMenuBar::item:pressed { background-color: {accent_12}; }
QMenu {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 28px 6px 14px;
    border-radius: {radius}px;
}
QMenu::item:selected { background-color: {accent_12}; color: {accent_hover}; }
QMenu::item:disabled { color: {text_muted}; }
QMenu::separator { height: 1px; background: {border}; margin: 5px 10px; }
QMenu::indicator { width: 14px; height: 14px; }

/* ====================== Tab 页 ====================== */
QTabWidget::pane {
    border: none;
    border-top: 1px solid {border};
    background: transparent;
}
QTabWidget::tab-bar { left: 0px; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent;
    color: {text_secondary};
    padding: 7px 18px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:hover { color: {text_primary}; background: {bg_panel}; }
QTabBar::tab:selected {
    color: {accent};
    border-bottom: 2px solid {accent};
    background: transparent;
}
QTabBar::tab:disabled { color: {text_muted}; }
QTabBar::close-button { background: transparent; }

/* ====================== 输入类 ====================== */
/* ★ padding 刻意保持 3px（与旧版一致）：项目里有不少按 24px 行高设计的紧凑布局，
   控件一旦抬高就会互相重叠（编辑条目对话框实测）。要更高只对特定 objectName 加。 */
QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 3px 8px;
    color: {text_primary};
    selection-background-color: {accent};
    selection-color: #ffffff;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover { border-color: {border_hover}; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: {accent}; }
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
    color: {text_muted};
    background-color: {bg_window};
    border-color: {border};
}

QComboBox {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 3px 24px 3px 8px;
    color: {text_primary};
}
QComboBox:hover { border-color: {border_hover}; }
QComboBox:focus, QComboBox:on { border-color: {accent}; }
QComboBox:disabled { color: {text_muted}; background-color: {bg_window}; }
/* 下拉弹出列表（popup）。
   ★ 注意：QSS 注释必须成对闭合。实测漏一个注释结束符，会让解析器从该处
     开始错乱，后面所有规则（包括 QListWidget::item:selected）全部静默失效 ——
     表现是"选中态莫名其妙退回原生蓝块"，排查成本极高，改 QSS 时务必小心。 */
QComboBox QAbstractItemView {
    background-color: {bg_panel};
    border: 1px solid {border_hover};
    border-radius: {radius}px;
    padding: 4px;
    outline: none;
    selection-background-color: {accent_12};
    selection-color: {accent_hover};
}
QComboBox QAbstractItemView::item {
    min-height: 22px;
    padding: 2px 6px;
}

QCheckBox { spacing: 7px; background: transparent; }
QCheckBox:disabled { color: {text_muted}; }
/* 勾选框：只加边框（纯 QSS 画不出"勾"字形）。
   实测取舍：一旦覆盖 ::indicator，Fusion 的勾就不再绘制，退化为
   "填充=选中 / 空框=未选" 的实心方块表达。深色 UI 里这个约定清晰且
   比"有勾无框"更成形，故采用；四态（hover/checked/disabled）齐全。 */
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid {border_hover};
    border-radius: 4px;
    background-color: {bg_panel};
}
QCheckBox::indicator:hover { border-color: {accent}; }
QCheckBox::indicator:checked { background-color: {accent}; border-color: {accent}; }
QCheckBox::indicator:disabled { border-color: {border}; background-color: {bg_window}; }

/* ====================== 按钮：四态 + 四级权重 ====================== */
/* 默认 = 次级实底 */
QPushButton {
    background-color: {bg_raised};
    border: 1px solid {border_hover};
    border-radius: {radius}px;
    padding: 6px 14px;
    color: {text_primary};
    min-height: 16px;
}
QPushButton:hover { background-color: #333849; border-color: {accent}; }
QPushButton:pressed { background-color: {bg_panel}; border-color: {accent_pressed}; }
QPushButton:checked {
    background-color: {accent_20};
    border-color: {accent};
    color: #ffffff;
}
QPushButton:disabled {
    background-color: {bg_panel};
    border-color: {border};
    color: {text_muted};
}
QPushButton:focus { outline: none; border-color: {accent}; }

/* #Primary —— 主操作（复制全部）：accent 实底 */
QPushButton#Primary {
    background-color: {accent};
    border: 1px solid {accent};
    color: #ffffff;
    font-weight: bold;
    padding: 7px 18px;
}
QPushButton#Primary:hover { background-color: {accent_hover}; border-color: {accent_hover}; }
QPushButton#Primary:pressed { background-color: {accent_pressed}; }
QPushButton#Primary:disabled {
    background-color: {bg_panel}; border-color: {border}; color: {text_muted};
}

/* #Ghost —— 工具操作：透明 + 描边 */
QPushButton#Ghost {
    background-color: transparent;
    border: 1px solid {border_hover};
    color: {text_secondary};
}
QPushButton#Ghost:hover {
    background-color: {accent_12};
    border-color: {accent};
    color: {text_primary};
}
QPushButton#Ghost:pressed { background-color: {accent_20}; }
QPushButton#Ghost:checked {
    background-color: {accent_20}; border-color: {accent}; color: #ffffff;
}
QPushButton#Ghost:disabled {
    background-color: transparent; border-color: {border}; color: {text_muted};
}

/* #Subtle —— 管理操作：无边框，hover 才出底色 */
QPushButton#Subtle {
    background-color: transparent;
    border: 1px solid transparent;
    color: {text_secondary};
}
QPushButton#Subtle:hover {
    background-color: {bg_raised};
    border-color: {border_hover};
    color: {text_primary};
}
QPushButton#Subtle:pressed { background-color: {bg_panel}; }
QPushButton#Subtle:disabled { color: {text_muted}; background: transparent; }

/* #Danger —— 删除 */
QPushButton#Danger {
    background-color: transparent;
    border: 1px solid {danger};
    color: {danger};
}
QPushButton#Danger:hover { background-color: {danger_14}; }
QPushButton#Danger:pressed { background-color: {danger}; color: #ffffff; }

/* ====================== QToolButton（过滤开关 / 星标）====================== */
QToolButton {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 5px 10px;
    color: {text_secondary};
}
QToolButton:hover { border-color: {border_hover}; color: {text_primary}; }
QToolButton:checked {
    background-color: {accent_12};
    border: 1px solid {accent};
    color: {accent_hover};
}
QToolButton:checked:hover { background-color: {accent_20}; }
QToolButton:disabled { color: {text_muted}; border-color: {border}; background: {bg_window}; }
QToolButton::menu-indicator { image: none; }

/* 星形收藏：无边框，只靠字形本身表意 */
QToolButton#Star {
    background: transparent;
    border: 1px solid transparent;
    padding: 2px 4px;
    color: {text_muted};
    font-size: 15pt;
}
QToolButton#Star:hover { color: {warning}; border-color: {border_hover}; }
QToolButton#Star:checked { color: {warning}; background: transparent; border-color: transparent; }
QToolButton#Star:disabled { color: {border}; }

/* ====================== 树 / 列表 / 表格 ====================== */
QTreeView, QTreeWidget {
    background-color: {bg_panel};
    border: none;
    outline: none;
    padding: 4px 2px;
}
QTreeView::item, QTreeWidget::item {
    min-height: 24px;
    padding: 3px 6px;
    border-left: 3px solid transparent;
    color: {text_secondary};
}
QTreeView::item:hover, QTreeWidget::item:hover {
    background-color: {bg_raised};
    color: {text_primary};
}
QTreeView::item:selected, QTreeWidget::item:selected {
    background-color: {sel_bg};
    border-left: 3px solid {accent};
    color: #ffffff;
}
QTreeView::branch { background: transparent; }
QTreeView::branch:hover { background: transparent; }

QListView, QListWidget {
    background-color: {bg_window};
    border: none;
    outline: none;
    padding: 4px 6px;
}
/* ★ 这里刻意不写 selection-background-color / selection-color：
   实测该属性一旦设置，QStyleSheetStyle 就改用自己的选中绘制路径，
   反而让下面 ::item:selected 的圆角卡片样式失效（选中退化成满宽方块）。
   选中外观统一交给 ::item:selected 控制。 */
/* 卡片式条目：normal 透明 + 底部 1px 分隔；选中 = accent 12% + 左侧 3px 竖条 */
QListWidget::item {
    background: transparent;
    border-bottom: 1px solid {bg_raised};
    border-left: 3px solid transparent;
    border-radius: {radius}px;
    margin: 1px 6px 1px 2px;
    padding: 0px;
}
QListWidget::item:hover {
    background-color: {bg_panel};
    border-left: 3px solid {border_hover};
}
QListWidget::item:selected {
    background-color: {sel_bg};
    border-left: 3px solid {accent};
    border-bottom: 1px solid {sel_bg};
}
QListWidget::item:disabled { color: {text_muted}; }

QTableView, QTableWidget {
    background-color: {bg_panel};
    alternate-background-color: {bg_raised};
    border: 1px solid {border};
    border-radius: {radius}px;
    gridline-color: {border};
    outline: none;
    selection-background-color: {sel_bg};
    selection-color: {text_primary};
}
QTableView::item, QTableWidget::item {
    padding: 6px 8px;
    border: none;
    color: {text_primary};
}
QTableView::item:hover, QTableWidget::item:hover { background-color: {bg_raised}; }
QTableView::item:selected, QTableWidget::item:selected {
    background-color: {sel_bg}; color: {text_primary};
}
QHeaderView { background: transparent; }
QHeaderView::section {
    background-color: {bg_raised};
    color: {text_secondary};
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    padding: 7px 8px;
    font-weight: bold;
}
QHeaderView::section:hover { color: {text_primary}; }
QTableCornerButton::section { background-color: {bg_raised}; border: none; }

/* ====================== 滚动条 ====================== */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
    border: none;
}
QScrollBar::handle:vertical {
    background: {border_hover};
    border-radius: 4px;
    min-height: 30px;
    margin: 0px 2px;
}
QScrollBar::handle:vertical:hover { background: {accent}; }
QScrollBar::handle:vertical:pressed { background: {accent_pressed}; }
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: {border_hover};
    border-radius: 4px;
    min-width: 30px;
    margin: 2px 0px;
}
QScrollBar::handle:horizontal:hover { background: {accent}; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ====================== QSplitter 分隔条 ====================== */
/* 默认用面板色：比窗口底亮半档 —— 仍算"接近背景"不抢视线，
   但能看出分栏边界（用窗口同色时列表区和分隔条融为一体，看不出能拖）。
   hover 高亮为强调色；grip 点由 GripSplitterHandle 自绘 */
QSplitter { background: transparent; }
QSplitter::handle { background-color: {bg_panel}; }
QSplitter::handle:horizontal { width: {handle_w}px; margin: 0px; }
QSplitter::handle:vertical { height: {handle_w}px; margin: 0px; }
QSplitter::handle:hover { background-color: {accent_20}; }
QSplitter::handle:pressed { background-color: {accent}; }

/* ====================== 状态栏 ====================== */
QStatusBar {
    background-color: {bg_panel};
    color: {text_secondary};
    border-top: 1px solid {border};
}
QStatusBar::item { border: none; }

/* ====================== 对话框 / 消息框 ====================== */
QMessageBox { background-color: {bg_window}; }
QMessageBox QLabel { color: {text_primary}; background: transparent; }
QMessageBox QPushButton { min-width: 84px; padding: 6px 16px; }
QDialog QLabel { background: transparent; }
QDialogButtonBox { button-layout: 1; }
QDialogButtonBox QPushButton { min-width: 84px; padding: 6px 16px; }

/* ====================== 分组框（生成器/编辑器用）====================== */
QGroupBox {
    border: 1px solid {border};
    border-radius: {radius}px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0px 5px;
    color: {text_secondary};
    font-weight: bold;
}

/* ====================== 通用小部件 ====================== */
QProgressBar {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: 4px;
    text-align: center;
    color: {text_primary};
    height: 14px;
}
QProgressBar::chunk { background-color: {accent}; border-radius: 3px; }
QSlider::groove:horizontal {
    height: 4px; background: {border}; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: {accent}; width: 12px; margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: {accent_hover}; }
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: {border}; background: {border}; }

/* ---- 语义化 objectName 约定（各界面按需引用）---- */
QLabel#Muted { color: {text_muted}; }
QLabel#Secondary { color: {text_secondary}; }
QLabel#SectionTitle { color: {text_primary}; font-weight: bold; }
QLabel#PageHint { color: {text_muted}; font-size: 8pt; }
QLabel#Badge {
    background-color: {bg_raised};
    color: {text_secondary};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 8pt;
}
QLabel#CountBadge {
    background-color: {bg_raised};
    color: {text_secondary};
    border: 1px solid {border};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 8pt;
}
QLabel#EmptyTitle { color: {text_secondary}; font-size: 12pt; }
QLabel#EmptyBody { color: {text_muted}; font-size: 9pt; }

/* 空状态 / 提示信息卡片（左侧竖条） */
QFrame#InfoCard {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-left: 3px solid {accent};
    border-radius: {radius}px;
}
QFrame#WarnCard {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-left: 3px solid {warning};
    border-radius: {radius}px;
}
QFrame#VendorBlock { border-radius: 3px; }
QFrame#Divider { background-color: {border}; border: none; }
QFrame#VLine { background-color: {border}; border: none; max-width: 1px; }

/* ---- 命令代码块（详情区命令全文）：比面板更深，1px 描边 + 圆角 ---- */
QPlainTextEdit#CodeBlock {
    background-color: {code_bg};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 10px 12px;
    color: {text_primary};
    selection-background-color: {accent};
    selection-color: #ffffff;
}
QPlainTextEdit#CodeBlock:focus { border-color: {accent}; }

/* ---- 列表条目卡片内部的四个部件（P-B）---- */
QLabel#EntryVendor {
    border-radius: 3px;
    color: #ffffff;
    font-size: 8pt;
    font-weight: bold;
}
QLabel#EntryTitle {
    color: {text_primary};
    font-size: 10pt;
    font-weight: bold;
}
QLabel#EntryTitle[fav="true"] { color: {warning}; }
QLabel#EntryTag {
    background-color: {bg_raised};
    color: {text_secondary};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 8pt;
}
/* 验证徽章：右对齐、统一尺寸（宽度在代码里 setFixedWidth 固定） */
QLabel#EntryBadge {
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 8pt;
    font-weight: bold;
}
QLabel#EntryBadge[state="verified"] {
    background-color: {success_14};
    color: {success};
    border: 1px solid {success};
}
QLabel#EntryBadge[state="unverified"] {
    background-color: {bg_raised};
    color: {text_muted};
    border: 1px solid {border};
}
/* 骨架条目徽章（审计 P1：待真机核对的 CLI 骨架，比"未验证"更醒目） */
QLabel#EntryBadge[state="skeleton"] {
    background-color: {warning_14};
    color: {warning};
    border: 1px solid {warning};
}
QLabel#EntrySub { color: {text_muted}; font-size: 8pt; }

/* ---- 过滤栏 / 工具栏 ---- */
QFrame#FilterBar { background: transparent; border: none; }
QFrame#ToolBar { background: transparent; border: none; }
QLineEdit#SearchBox {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 6px 10px;
    font-size: 9pt;
    min-height: 20px;
}
QLineEdit#SearchBox:focus { border-color: {accent}; background-color: {bg_raised}; }
QToolButton#FilterToggle {
    background-color: {bg_panel};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 4px 12px;
    color: {text_secondary};
}
QToolButton#FilterToggle:hover { border-color: {border_hover}; color: {text_primary}; }
QToolButton#FilterToggle:checked {
    background-color: {accent_12}; border-color: {accent}; color: {accent_hover};
}

/* ---- 空状态（列表为空 / 搜索无结果）---- */
QWidget#EmptyState { background: transparent; }
QLabel#EmptyIcon { color: {border_hover}; font-size: 30pt; }
QLabel#EmptyTitle { color: {text_secondary}; font-size: 11pt; }
QLabel#EmptyBody { color: {text_muted}; font-size: 9pt; }

/* ---- 详情区标题 / 提示 ---- */
QLabel#DetailTitle { color: {text_primary}; font-size: 12pt; font-weight: bold; }
QLabel#DetailMeta { color: {text_secondary}; font-size: 8pt; }
QLabel#HintText { color: {warning}; font-size: 8pt; }
QLabel#LinePos { color: {warning}; font-size: 8pt; }

/* ---- 左导航容器与底部统计 ---- */
QFrame#NavPane {
    background-color: {bg_panel};
    border: none;
    border-right: 1px solid {border};
}
QLabel#NavStats {
    color: {text_secondary};
    padding: 8px 12px 4px 12px;
    font-size: 8pt;
    border-top: 1px solid {border};
}
QLabel#NavPath {
    color: {text_muted};
    padding: 0px 12px 10px 12px;
    font-size: 8pt;
}

/* ---- 详情区信息页（P-E：统一留白，行距由 HTML 控制为 1.5）---- */
QTextEdit#InfoPane {
    background-color: {bg_panel};
    border: none;
    padding: 12px 14px;
    color: {text_primary};
}
"""

_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def render_qss(template, tokens=None):
    """
    把 QSS 模板里的 {token} 替换为令牌值。
    令牌名必须存在（写错立刻报错，避免静默生成空色值）。
    """
    table = TOKENS if tokens is None else tokens

    def _repl(match):
        key = match.group(1)
        if key not in table:
            raise KeyError("theme.py: QSS 里引用了未定义的设计令牌 {%s}" % key)
        return str(table[key])

    return _PLACEHOLDER_RE.sub(_repl, template)


def check_qss(qss):
    """
    QSS 结构自检：注释与花括号必须成对。

    ★ 为什么值得一个专门的检查函数：
      实测漏一个注释结束符后，QSS 解析器会从该处开始错乱，
      **其后所有规则静默失效**（表现为"列表选中态莫名其妙退回原生蓝块"），
      没有任何报错、没有日志，肉眼也看不出来 —— 排查代价极大。
      渲染后立刻自检，把这类错误在启动瞬间暴露出来。
    """
    issues = []
    if qss.count("/*") != qss.count("*/"):
        issues.append("注释未成对（开 %d / 闭 %d）" % (qss.count("/*"), qss.count("*/")))
    if qss.count("{") != qss.count("}"):
        issues.append("花括号未成对（开 %d / 闭 %d）" % (qss.count("{"), qss.count("}")))
    return issues


DARK_QSS = render_qss(_QSS_TEMPLATE)

# 渲染后立即自检：有问题写到 stderr（不抛异常，离网现场不能因为主题问题起不来程序）
QSS_ISSUES = check_qss(DARK_QSS)
if QSS_ISSUES:
    import sys as _sys
    _sys.stderr.write("theme.py: DARK_QSS 结构异常 —— %s\n" % "；".join(QSS_ISSUES))


# ===========================================================================
# 三、字体 helper
# ===========================================================================
def mono_font(point_size=None):
    """代码/命令等宽字体（Consolas → Cascadia Mono 兜底 → 系统等宽）"""
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    if point_size is None:
        point_size = int(MONO_FONT_SIZE.rstrip("pt"))
    font.setPointSize(point_size)
    return font


def ui_font(point_size=None):
    """界面字体（Microsoft YaHei UI）"""
    font = QFont("Microsoft YaHei UI")
    font.setStyleHint(QFont.SansSerif)
    if point_size is not None:
        font.setPointSize(point_size)
    return font


def has_family(name):
    """字体是否可用（用于代码字体降级判断）"""
    try:
        return name in QFontDatabase().families()
    except Exception:
        return False


def code_font_family():
    """选一个真实存在的等宽字体名（Consolas 优先，其次 Cascadia Mono）"""
    for name in ("Consolas", "Cascadia Mono", "Courier New"):
        if has_family(name):
            return name
    return "monospace"


# ===========================================================================
# 四、QSplitter：可拖拽 + grip 点 + hover 反馈
# ===========================================================================
class GripSplitterHandle(QSplitterHandle):
    """
    分隔条手柄：在 QSS 铺好的底色上再自绘 3 个 grip 点。
      · 默认：点用 text_muted（低调，不抢视线）
      · hover：点变 accent，配合 QSS 的 handle:hover 淡蓝底
    """

    def __init__(self, orientation, parent):
        super(GripSplitterHandle, self).__init__(orientation, parent)
        self.setAttribute(Qt.WA_Hover, True)      # 让 QSS 的 :hover 生效
        self._hover = False
        if orientation == Qt.Horizontal:
            self.setCursor(Qt.SplitHCursor)
        else:
            self.setCursor(Qt.SplitVCursor)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super(GripSplitterHandle, self).enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super(GripSplitterHandle, self).leaveEvent(event)

    def paintEvent(self, event):
        # 先让 QSS 画底色（含 :hover 高亮）
        super(GripSplitterHandle, self).paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ACCENT if self._hover else TEXT_MUTED))

        rect = self.rect()
        cx = rect.center().x() + 1.0
        cy = rect.center().y() + 1.0
        radius = 1.6
        if self.orientation() == Qt.Horizontal:
            # 垂直排列的点（拖左右）
            for offset in (-7.0, 0.0, 7.0):
                painter.drawEllipse(QPointF(cx, cy + offset), radius, radius)
        else:
            # 水平排列的点（拖上下）
            for offset in (-7.0, 0.0, 7.0):
                painter.drawEllipse(QPointF(cx + offset, cy), radius, radius)
        painter.end()


class GripSplitter(QSplitter):
    """
    两级拖拽分栏用的 QSplitter：
      · handle 宽 8px（默认 4px 太窄，鼠标难抓 —— P-A 的直接原因之一）
      · setChildrenCollapsible(False)：拖到最小尺寸有卡限，不会被拖成 0
      · 自绘 grip 点 + hover 变色
    """

    def __init__(self, orientation, parent=None):
        super(GripSplitter, self).__init__(orientation, parent)
        self.setHandleWidth(HANDLE_WIDTH)
        self.setChildrenCollapsible(False)
        self.setOpaqueResize(True)

    def createHandle(self):
        return GripSplitterHandle(self.orientation(), self)


# ===========================================================================
# 四点半、深色 QPalette
# ===========================================================================
def apply_dark_palette(app=None):
    """
    设置深色 QPalette。

    ★ 为什么不能只靠 QSS：
      QSS 只管它显式覆盖到的部分，剩下的原生绘制（下拉箭头、复选框的"勾"、
      QMessageBox 的图标、菜单勾选标记…）仍按 palette 取色。只 setStyleSheet
      不设 palette 时，Fusion 会按**浅色** palette 画这些细节 ——
      深色背景上就会出现"几乎看不见的深灰箭头"和突兀的原生灰控件。
      两者配套，才算真正把主题收口。
    """
    from PyQt5.QtGui import QPalette

    app = app or QApplication.instance()
    if app is None:
        return

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG_WINDOW))
    pal.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Base, QColor(BG_PANEL))
    pal.setColor(QPalette.AlternateBase, QColor(BG_RAISED))
    pal.setColor(QPalette.ToolTipBase, QColor(BG_RAISED))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Button, QColor(BG_RAISED))
    pal.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, QColor(ACCENT))
    pal.setColor(QPalette.LinkVisited, QColor(ACCENT_PRESSED))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    # 明暗阶（Fusion 画复选框边框、分隔线、3D 边框时取这些色）：
    # 不设的话 Fusion 会从 Window 色推导出一片"看不出边界"的深色，勾选框会像悬空
    pal.setColor(QPalette.Light, QColor(BORDER_HOVER))
    pal.setColor(QPalette.Midlight, QColor(BORDER))
    pal.setColor(QPalette.Mid, QColor(BORDER_HOVER))
    pal.setColor(QPalette.Dark, QColor(BORDER))
    pal.setColor(QPalette.Shadow, QColor(BG_WINDOW))
    try:
        pal.setColor(QPalette.PlaceholderText, QColor(TEXT_MUTED))
    except AttributeError:
        pass

    # 禁用态：统一压到 muted，避免出现"看起来还能点"的控件
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(TEXT_MUTED))
    pal.setColor(QPalette.Disabled, QPalette.Base, QColor(BG_WINDOW))
    pal.setColor(QPalette.Disabled, QPalette.Button, QColor(BG_PANEL))
    pal.setColor(QPalette.Disabled, QPalette.Highlight, QColor(BG_RAISED))
    pal.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(TEXT_MUTED))

    app.setPalette(pal)


def apply_theme(app=None):
    """
    一次性把主题装到应用上：Fusion 样式 → 深色 palette → DARK_QSS。
    顺序有讲究：palette 必须在 setStyleSheet 之前设好，
    否则 QSS 里没覆盖到的原生细节会先按浅色 palette 定型。
    """
    app = app or QApplication.instance()
    if app is None:
        return None
    app.setStyle("Fusion")          # 统一各版本 Windows 的控件外观，QSS 更可控
    apply_dark_palette(app)
    app.setStyleSheet(DARK_QSS)
    return app


# ===========================================================================
# 四点八、ThemedComboBox —— 自绘下拉箭头
# ===========================================================================
class ThemedComboBox(QComboBox):
    """
    带自绘下拉箭头的下拉框。

    ★ 为什么不写 QSS 的 ::down-arrow：
        QSS 里唯一的画三角办法是 `width:0; height:0; border-*` 取巧，
        但 Qt 的 subcontrol 不支持这种 CSS hack —— 实测画出来是一个白方块。
        而只要给 ::down-arrow 写了规则，QStyleSheetStyle 就不再回退到
        base style，箭头直接消失。所以箭头由本类在 paintEvent 里 QPainter 自绘。

    行为与 QComboBox 完全一致（itemData / currentData / findData 等全部继承），
    只是多了 5 行绘制。QSS 里已给下拉框右侧留了 24px 的箭头位。
    """

    def paintEvent(self, event):
        super(ThemedComboBox, self).paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        if not self.isEnabled():
            painter.setBrush(QColor(TEXT_MUTED))
        elif self.underMouse() or self.hasFocus():
            painter.setBrush(QColor(ACCENT))
        else:
            painter.setBrush(QColor(TEXT_SECONDARY))

        half_w = 4.5
        half_h = 2.5
        center_x = self.width() - 12.0
        center_y = self.height() / 2.0
        arrow = QPolygonF([
            QPointF(center_x - half_w, center_y - half_h),
            QPointF(center_x + half_w, center_y - half_h),
            QPointF(center_x, center_y + half_h + 1.0),
        ])
        painter.drawPolygon(arrow)
        painter.end()


# ===========================================================================
# 五、高分屏
# ===========================================================================
def enable_high_dpi():
    """
    高分屏适配 —— 必须在 QApplication 实例化**之前**调用。
    125% / 150% 缩放下若不做处理，控件会按物理像素画导致错位、裁切。

    PassThrough：保留小数缩放因子（1.25 就是 1.25），
    否则 Qt 默认四舍五入到 1.0，125% 屏上界面会"变小一圈"。
    """
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass
    try:
        policy_enum = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
        if policy_enum is not None:
            QApplication.setHighDpiScaleFactorRoundingPolicy(policy_enum.PassThrough)
    except Exception:
        # 老版本 PyQt5 没有该 API —— 忽略即可，不影响基本适配
        pass


# ===========================================================================
# 六、布局记忆（ui_state.json，与程序同目录 —— 沿用项目"数据跟着程序走"的惯例）
# ===========================================================================
UI_STATE_FILENAME = "ui_state.json"
STATE_VERSION = 1


def state_file_path():
    """ui_state.json 完整路径（exe/源码同目录；只读介质上写入会失败，静默忽略）"""
    try:
        from db import get_base_dir
        base = get_base_dir()
    except Exception:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, UI_STATE_FILENAME)


def load_ui_state():
    """
    读布局记忆。任何异常（文件不存在/JSON 坏了/不是字典）一律返回 {}，
    由调用方用默认值兜底 —— 离网现场不能因为一个状态文件把程序拖死。
    """
    path = state_file_path()
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_ui_state(payload):
    """写布局记忆（显式 utf-8）。失败静默 —— U 盘写保护时不应弹错。返回是否成功"""
    try:
        path = state_file_path()
        data = dict(payload or {})
        data["version"] = STATE_VERSION
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def read_sizes(state, key, minimums):
    """
    从 state 里取某个 splitter 的 sizes，并做合法性校验：
      · 必须是长度匹配的整数列表
      · 每一项不得小于对应 pane 的最小尺寸（否则拖到看不见，反而更糟）
    不合法返回 None，调用方用默认比例。
    """
    raw = (state or {}).get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) != len(minimums):
        return None
    sizes = []
    for value, floor in zip(raw, minimums):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if number < floor:
            return None
        sizes.append(number)
    if sum(sizes) <= 0:
        return None
    return sizes
