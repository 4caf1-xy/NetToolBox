# -*- coding: utf-8 -*-
"""
ui_ai.py —— AI 诊断 Tab（对话式界面）+ 设置对话框 + 状态栏指示

结构（对话式改造 第 1 轮）：
    AiTab
      ├─ 引导页（未配置时）：图标 + 一句话 + [去设置]，不崩溃不报错
      └─ 主界面（垂直：工具条 / 上下文面板 / 消息流 / 输入区）
           ├─ 工具条：[新建对话] [历史] [案例目录] … [转为草稿条目] + 状态
           ├─ 上下文面板（六字段 = 会话基线，可折叠；首轮发送后自动折叠为一行摘要；
           │   每轮自动作为上下文随消息发送）
           ├─ 消息流（QScrollArea：用户右对齐浅色气泡 / AI 左对齐深色气泡内嵌
           │   Markdown 渲染 + [复制][入库▸] 动作条；裁剪提示灰字）
           └─ 输入区（多行输入 + [附件] + 附件标签 + token 估算 + [发送][停止]）

    SettingsDialog        文件→设置（base_url / api_key / model / timeout / 测试连接）
    StatusIndicator       状态栏常驻 AI 指示（●绿=可连接 ●灰=无网络 ●黄=配置缺失）

约定：
    - 既有能力全部保留：网络探测置灰、状态栏指示、流式响应+取消、Markdown 渲染
      +命令块复制、token 估算、案例保存、排查向导等四入口 prefill 链路
    - db.py / renderer.py / error_match.py 不动
    - 全部 IO encoding="utf-8"
"""

import base64
import datetime
import os
import re

import dedupe

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QTextCursor, QPixmap, QFontMetrics
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QComboBox, QPlainTextEdit, QPushButton, QTextBrowser,
                             QDialog, QFormLayout, QDialogButtonBox, QStackedWidget,
                             QCheckBox, QFrame, QMessageBox, QListWidget,
                             QListWidgetItem, QScrollArea, QSizePolicy, QToolButton,
                             QMenu, QFileDialog, QRadioButton, QButtonGroup)

import ai_bridge
import db as dbmod
import renderer
from ui_main import copy_to_clipboard
from theme import (SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL,
                   TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, WARNING, DANGER,
                   SUCCESS, ACCENT, BORDER, BORDER_HOVER, BG_PANEL,
                   CODE_BG, MONO_FONT_FAMILY, mono_font,
                   repolish, load_ui_state, save_ui_state,
                   GripSplitter, read_sizes)


# ===========================================================================
# 轻量 Markdown 渲染（QTextBrowser + QSS，禁用 QtWebEngine 的替代实现）
#   支持：``` 代码块（等宽底色框 + 右上角[复制]链接）/ #~###### 标题 /
#         - * • 无序列表（含 2 空格缩进嵌套）/ 1. 2、有序列表 /
#         **加粗** / `行内代码` / 空行分段
#   设计取舍：AI 回复格式有限，不引第三方 markdown 库（零新依赖约束）；
#   流式期间未闭合的 ``` 尾段按"正在输出的代码块"渲染，打字机过程不破版。
# ===========================================================================
def _esc(text):
    """HTML 转义（与 ui_main.build_text_card 同规则）"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- 观察判断类标签词（加粗提色；后跟冒号才命中，不误伤正文）----
_LABEL_WORD_RE = re.compile(r"(观察|判断|结论|原因|风险|注意|影响)([:：])")


def _md_inline(text):
    """行内 Markdown → HTML：`代码` 与 **加粗**（先转义再变换）"""
    s = _esc(text)
    s = re.sub(r"`([^`]+)`",
               r"<code style='background-color:%s; font-family:%s; font-size:9pt;'>\1</code>"
               % (CODE_BG, MONO_FONT_FAMILY), s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    # 观察/判断/结论等标签词：加粗 + accent 提色（与正文区分，扫读定位用）
    s = _LABEL_WORD_RE.sub(
        r"<b><span style='color:%s;'>\1\2</span></b>" % ACCENT, s)
    return s


# ---- 代码块排版参数（探针实测：Qt 富文本 white-space:pre-wrap 支持完整换行，
#      逐行 div + text-indent 负值 = 每行悬挂缩进，换行续行对齐块左缘）----
CODE_HANG_INDENT = 16       # 悬挂缩进量（px）：首行右移、续行挂回块左缘


def _code_body_html(code):
    """
    代码文本 → 逐行 div（white-space:pre-wrap 保留空格 + 自动换行，杜绝横向裁剪；
    margin-left + text-indent 负值实现换行悬挂缩进）。空行用 &nbsp; 撑住行高。
    """
    rows = []
    for line in code.split("\n"):
        rows.append(
            "<div style='white-space:pre-wrap; font-family:%s; font-size:9pt; "
            "color:%s; margin-left:%dpx; text-indent:-%dpx;'>%s</div>"
            % (MONO_FONT_FAMILY, TEXT_PRIMARY,
               CODE_HANG_INDENT, CODE_HANG_INDENT,
               _esc(line) if line.strip() else "&#160;"))
    return "".join(rows)


def _code_block_html(code, counter_ref):
    """
    代码块 → 等宽底色框表格，右上角 [复制] 浮层式链接（copycode://blockN → anchorClicked）。
    counter_ref 是本次渲染的 code_blocks 列表：函数自己取号并登记原文，
    这样**引用块内的代码块**与顶层代码块共用一套编号，[复制] 一定对得上。
    复制语义不变：链接取的是 counter_ref 里的原始文本，不是换行后的显示文本。
    """
    idx = len(counter_ref)
    counter_ref.append(code)
    return (
        "<table width='100%%' cellspacing='0' cellpadding='0' "
        "style='background-color:%s; border:1px solid %s; margin:6px 0px;'>"
        "<tr>"
        "<td style='padding:4px 10px 2px 10px; color:%s; font-size:8pt;'>代码块 %d</td>"
        "<td align='right' style='padding:4px 10px 2px 10px;'>"
        "<a href='copycode://block%d' style='color:#ffffff; background-color:%s; "
        "text-decoration:none; font-size:8pt;'>&#160;复制&#160;</a>"
        "</td></tr>"
        "<tr><td colspan='2' style='padding:0px 10px 8px 10px;'>%s</td></tr></table>"
    ) % (CODE_BG, BORDER, TEXT_MUTED, idx, idx, ACCENT, _code_body_html(code))


def _text_segment_html(segment, preserve_lines=False):
    """
    非代码段：标题 / 列表（含一层嵌套缩进）/ 段落。
    preserve_lines=True（引用块内）时：普通文本行按 <br/> 保留换行 ——
    引用块里多半是告警+命令+验证要点，逐行展示比合并成一段可读得多。
    """
    out = []
    para = []
    list_stack = []          # [(level, 'ul'/'ol')]，Qt 的嵌套列表支持到两层够用
    joiner = "<br/>" if preserve_lines else " "

    def flush_para():
        if para:
            out.append("<p style='margin:5px 0px; line-height:155%%;'>%s</p>"
                       % _md_inline(joiner.join(para)))
            del para[:]

    def close_to(level):
        """关掉比 level 更深的列表（level=0 即全关）"""
        while list_stack and list_stack[-1][0] > level:
            out.append("</%s>" % list_stack.pop()[1])

    for raw in segment.splitlines():
        stripped = raw.strip()
        if not stripped:
            flush_para()
            close_to(0)
            continue
        m_h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        m_step = re.match(r"^(第\s*\d+\s*步|步骤\s*\d+|Step\s*\d+)\s*(?:[:：、.．\-—)]|\s|$)",
                          stripped, re.IGNORECASE)
        m_ul = re.match(r"^(\s*)[-*•]\s+(.*)$", raw.rstrip())
        m_ol = re.match(r"^(\s*)(\d+)[.、)．]\s+(.*)$", raw.rstrip())
        if m_h:
            flush_para()
            close_to(0)
            out.append("<h3 style='margin:10px 0px 4px 0px;'><b>%s</b></h3>"
                       % _md_inline(m_h.group(2)))
        elif m_step:
            # "第 N 步 / 步骤 N / Step N" 开头 → 节标题（加粗 + 上下间距，AI 分步回复扫读用）
            flush_para()
            close_to(0)
            out.append("<h3 style='margin:12px 0px 4px 0px;'><b>%s</b></h3>"
                       % _md_inline(stripped))
        elif m_ul or m_ol:
            flush_para()
            indent = len((m_ul or m_ol).group(1))
            level = min(2, indent // 2 + 1)          # 0 缩进=1 层，2 空格=2 层…
            tag = "ol" if m_ol else "ul"
            # ★ 列表层级/类型管理（两处坑都在这儿）：
            #   1) 每处理一项都 close_lists(level-1) 会把本层也关掉 → 每项各自成表 →
            #      有序列表编号永远显示 1.（第 2 轮冒烟实测）
            #   2) 同层 ol↔ul 切换（有序子项后接顶层无序）必须关旧开新，
            #      否则后续项会被塞进上一个列表，类型串味（本轮渲染自测实测）
            while list_stack and (list_stack[-1][0] > level or
                                  (list_stack[-1][0] == level and list_stack[-1][1] != tag)):
                out.append("</%s>" % list_stack.pop()[1])
            if not (list_stack and list_stack[-1][0] == level):
                out.append("<%s style='margin:4px 0px; margin-left:%dpx;'>"
                           % (tag, 18 * (level - 1)))
                list_stack.append((level, tag))
            content = m_ul.group(2) if m_ul else m_ol.group(3)
            out.append("<li style='margin:2px 0px; line-height:150%%;'>%s</li>" % _md_inline(content))
        else:
            close_to(0)
            para.append(stripped)
    flush_para()
    close_to(0)
    return "".join(out)


# ---- 围栏 / 引用块识别（任务5）----
_FENCE_RE = re.compile(r"^\s*```")
_QUOTE_RE = re.compile(r"^\s*>\s?")


def _split_fence_segments(text, streaming):
    """
    按围栏行把一段文本切成 [("text", …) / ("code", …)]（任务5 的核心改写）。

    ★ 与旧实现（re.split 按围栏切）的差别：
        · 未闭合的**尾部**围栏：streaming=True（正在输出）→ 仍按代码块渲染，
          保住打字机体验；streaming=False（成品/历史回看）→ 按普通文本兜底，
          绝不把围栏之后的正文整段吞进代码块。
    """
    segments = []
    buf = []
    in_code = False
    for line in text.splitlines(True):
        if _FENCE_RE.match(line):
            if in_code:                     # 闭合：结出代码块
                segments.append(("code", "".join(buf)))
            else:                           # 开启：先结出前面的正文
                if "".join(buf).strip():
                    segments.append(("text", "".join(buf)))
            buf = []
            in_code = not in_code
            continue
        buf.append(line)
    if buf or in_code:
        payload = "".join(buf)
        if in_code and streaming:
            segments.append(("code", payload))
        elif payload.strip():
            segments.append(("text", payload))
    return segments


def _quote_html(inner_text, streaming, code_blocks, depth=0):
    """
    引用块 → 左侧警示竖条卡片（复用 build_text_card 的表格手法，Qt 富文本对表格支持最稳）。
    内部递归渲染：引用块里的代码围栏按代码块独立出块（任务5-1），
    且这些代码块与顶层共用 code_blocks 编号，[复制] 链接照样可用。
    """
    body = []
    for kind, payload in _split_fence_segments(inner_text, streaming):
        if kind == "code":
            body.append(_code_block_html(payload.rstrip("\n"), code_blocks))
        else:
            body.append(_text_segment_html(payload, preserve_lines=True))
    inner = "".join(body)
    if not inner.strip():
        return ""
    bar = WARNING if depth == 0 else BORDER_HOVER
    return (
        "<table width='100%%' cellpadding='0' cellspacing='0' "
        "style='margin:6px 0px; background-color:%s;'>"
        "<tr><td style='background-color:%s; width:3px;'></td>"
        "<td style='padding:3px 0px 3px 10px; color:%s;'>%s</td></tr></table>"
        % (BG_PANEL, bar, TEXT_SECONDARY, inner)
    )


def _top_blocks(text, streaming):
    """
    顶层切块：引用块（连续 > 行，整段成块）与普通段落分开处理。
    ★ 为什么必须先分组：`> ```bash` 这类引用块内的围栏，行首带 ">"，
      直接做围栏切分识别不到 → 正文与围栏被拼成一行（现场截图实测的错乱来源）。
    """
    # 预归一化：去掉行尾 \r，保留行结构
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    buf, mode = [], None            # mode: "quote" / "plain"
    for line in lines:
        is_quote = bool(_QUOTE_RE.match(line))
        now = "quote" if is_quote else "plain"
        if mode is None:
            mode = now
        if now != mode:
            blocks.append((mode, "\n".join(buf)))
            buf = []
            mode = now
        buf.append(_QUOTE_RE.sub("", line) if is_quote else line)
    if buf:
        blocks.append((mode or "plain", "\n".join(buf)))

    out = []
    for kind, payload in blocks:
        if kind == "quote":
            out.append(("quote", payload))
        else:
            out.extend(_split_fence_segments(payload + "\n", streaming))
    return out


def md_to_html(md_text, streaming=False):
    """
    AI 回复 Markdown → (完整 HTML, code_blocks)。
    code_blocks 供 [复制] 链接取原文（含引用块内的代码块）。
    streaming=True 时未闭合的尾部围栏按代码块渲染（打字机）；成品渲染时兜底为文本。
    """
    code_blocks = []
    body = []
    for kind, payload in _top_blocks(md_text or "", streaming):
        if kind == "code":
            body.append(_code_block_html(payload.rstrip("\n"), code_blocks))
        elif kind == "quote":
            html = _quote_html(payload, streaming, code_blocks)
            if html:
                body.append(html)
        elif payload.strip():
            body.append(_text_segment_html(payload))
    html = ("<div style='color:%s; font-size:9pt;'>%s</div>" % (TEXT_PRIMARY, "".join(body)))
    return html, code_blocks


def render_selftest():
    """
    Markdown 渲染自测（任务5-3）：3 个必须过的样例。
    返回 (是否全部通过, [(样例名, 通过?, 说明)])。
    纯字符串检查、无 Qt 依赖，可被 main.py --selftest 直接调用。
    """
    cases = []

    # 样例1：引用块 + 代码围栏嵌套（现场截图错乱的那种）
    md1 = ("> **风险警告**：以下操作会开放入方向流量，测试后务必恢复！\n"
           "> ```bash\n"
           "> systemctl stop firewalld\n"
           "> ping 10.0.0.1\n"
           "> ```\n"
           "> ⚠ 影响业务：防火墙关闭期间本机暴露于网络风险\n")
    html1, blocks1 = md_to_html(md1, streaming=False)
    cases.append((
        "引用块+代码围栏嵌套",
        ("1" in [str(len(blocks1))] and len(blocks1) == 1
         and "systemctl stop firewalld" in blocks1[0]
         and "代码块 0" in html1
         and "``" not in html1
         and "影响业务" in html1),
        "代码块数=%d；围栏标记未泄漏=%s" % (len(blocks1), "``" not in html1)))

    # 样例2：未闭合围栏 —— 成品渲染按文本兜底，不吞并后续段落
    md2 = "先看这段说明。\n```bash\nsystemctl status firewalld\n后续段落不能被吞掉。\n"
    html2, blocks2 = md_to_html(md2, streaming=False)
    html2s, blocks2s = md_to_html(md2, streaming=True)
    cases.append((
        "未闭合围栏（非流式兜底）",
        (len(blocks2) == 0 and "后续段落不能被吞掉" in html2
         and "systemctl status firewalld" in html2),
        "code_blocks=%d；后续段落保留=%s" % (len(blocks2), "后续段落不能被吞掉" in html2)))
    cases.append((
        "未闭合围栏（流式按代码块）",
        len(blocks2s) == 1 and "systemctl status firewalld" in blocks2s[0],
        "code_blocks=%d（打字机体验）" % len(blocks2s)))

    # 样例3：嵌套列表（有序 + 两层无序）
    md3 = ("1. 第一步\n2. 第二步\n   - 子项甲\n   - 子项乙\n- 顶层丙\n")
    html3, _b3 = md_to_html(md3, streaming=False)
    cases.append((
        "嵌套列表",
        (html3.count("<ol") == 1 and html3.count("<ul") == 2
         and "子项甲" in html3 and "顶层丙" in html3),
        "ol=%d ul=%d" % (html3.count("<ol"), html3.count("<ul"))))

    # 样例4：长命令 pre-wrap（grep ListenAddress 实测样例）——必须整段保留、声明换行
    #   （HTML 体内 & 会被转义为 &amp;，显示时还原 —— 原文完整性按转义形式断言）
    long_cmd = ("grep -nE \"^[# ]*ListenAddress\" /etc/ssh/sshd_config && sudo sshd -T | "
                "grep -i listenaddress && systemctl restart sshd")
    md4 = "检查 sshd 监听配置：\n```bash\n%s\n```\n" % long_cmd
    html4, blocks4 = md_to_html(md4, streaming=False)
    cases.append((
        "长命令 pre-wrap（无横向裁剪）",
        (len(blocks4) == 1 and blocks4[0] == long_cmd
         and "white-space:pre-wrap" in html4
         and _esc(long_cmd) in html4
         and "text-indent" in html4),
        "原文完整=%s；pre-wrap=%s；悬挂缩进=%s"
        % (_esc(long_cmd) in html4, "white-space:pre-wrap" in html4, "text-indent" in html4)))

    # 样例5：复制链接取原始文本（右上角浮层式链接仍在，copycode 编号不变）
    cases.append((
        "代码块复制链接（原文语义）",
        ("copycode://block0" in html4 and "复制" in html4 and blocks4[0] == long_cmd),
        "链接=%s" % ("copycode://block0" in html4)))

    # 样例6："第 N 步"节标题 + 观察/判断标签词提色
    md6 = "第 1 步：确认服务状态\n观察：端口未监听\n判断：配置未生效\n"
    html6, _b6 = md_to_html(md6, streaming=False)
    cases.append((
        "节标题与标签词",
        ("<h3" in html6 and "<b>第 1 步" in html6
         and "color:%s" % ACCENT in html6 and "<b><span" in html6),
        "节标题=%s 标签词=%s" % ("<b>第 1 步" in html6, "color:%s" % ACCENT in html6)))

    passed = all(c[1] for c in cases)
    return passed, cases


# ===========================================================================
# 设置对话框（文件 → 设置）
# ===========================================================================
class _TestWorker(QThread):
    """测试连接线程（避免 3s 阻塞卡死对话框）"""
    test_done = pyqtSignal(bool, str, int)      # ok, message, latency_ms

    def __init__(self, config, parent=None):
        super(_TestWorker, self).__init__(parent)
        self._config = config

    def run(self):
        ok, msg, latency = ai_bridge.test_connection(self._config)
        self.test_done.emit(ok, msg, latency)


class _ModelListWorker(QThread):
    """拉取端点模型列表线程（GET /models）"""
    models_done = pyqtSignal(bool, list, str)   # ok, [模型名], message

    def __init__(self, config, parent=None):
        super(_ModelListWorker, self).__init__(parent)
        self._config = config

    def run(self):
        ok, names, msg = ai_bridge.list_models(self._config)
        self.models_done.emit(ok, names, msg)


class SettingsDialog(QDialog):
    """
    AI 接口设置：
        base_url  默认 https://api.deepseek.com/v1，可填任意 OpenAI 兼容端点
        api_key   QLineEdit password 模式 + 显示切换
        model     下拉 + 可手输（editable QComboBox）
        timeout   默认 60s
        [测试连接] 1 token 测试请求，3s 内返回成功/失败 + 延迟
    """

    MODELS = ["deepseek-chat", "deepseek-reasoner", "gpt-4o-mini", "qwen-plus",
              "glm-4-flash"]

    def __init__(self, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.setWindowTitle("AI 接口设置")
        self.setMinimumWidth(560)
        self._test_worker = None
        self._models_worker = None
        self.saved = False                  # 点了确定并保存成功

        cfg = ai_bridge.load_config()

        self.ed_base_url = QLineEdit(cfg.get("base_url") or ai_bridge.DEFAULT_CONFIG["base_url"])
        self.ed_base_url.setPlaceholderText("https://api.deepseek.com/v1（任意 OpenAI 兼容端点）")

        self.ed_key = QLineEdit(cfg.get("api_key") or "")
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("sk-…")
        self.tgl_show = QCheckBox("显示")
        self.tgl_show.toggled.connect(
            lambda v: self.ed_key.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))

        self.cmb_model = QComboBox()
        self.cmb_model.setEditable(True)
        for name in self.MODELS:
            self.cmb_model.addItem(name)
        self.cmb_model.setCurrentText(cfg.get("model") or ai_bridge.DEFAULT_CONFIG["model"])
        self.cmb_model.setToolTip("可直接手输模型名；不确定就点右边「获取模型」拉取该端点的真实列表")
        # ★ 不要放 "custom" 这类占位项：选中它又没改成真名，会被原样发出 →
        #   服务端回 503/404（现场实测：No available channel for model custom）
        self.btn_models = QPushButton("获取模型")
        self.btn_models.setObjectName("Ghost")
        self.btn_models.setToolTip("调用端点的 /models 接口，列出该服务真实可用的模型名")
        self.btn_models.clicked.connect(self.on_fetch_models)

        self.ed_timeout = QLineEdit(str(cfg.get("timeout") or ai_bridge.DEFAULT_CONFIG["timeout"]))
        self.ed_timeout.setPlaceholderText("60")

        self.ed_operator = QLineEdit(cfg.get("operator") or "")
        self.ed_operator.setPlaceholderText("写进 AI 案例留痕（可选）")

        # 图片识别模型（可选）：配置后 [附件] 才接受 .png/.jpg
        self.ed_vision = QLineEdit(cfg.get("vision_model") or "")
        self.ed_vision.setPlaceholderText("留空 = 不支持图片附件；填写多模态模型名（如 gpt-4o-mini）即启用")

        form = QFormLayout()
        form.addRow("接口地址 base_url", self.ed_base_url)
        key_row = QHBoxLayout()
        key_row.setSpacing(SPACE_SM)
        key_row.addWidget(self.ed_key, 1)
        key_row.addWidget(self.tgl_show)
        form.addRow("API Key", key_row)
        model_row = QHBoxLayout()
        model_row.setSpacing(SPACE_SM)
        model_row.addWidget(self.cmb_model, 1)
        model_row.addWidget(self.btn_models)
        form.addRow("模型 model", model_row)
        form.addRow("超时（秒）", self.ed_timeout)
        form.addRow("操作人", self.ed_operator)
        form.addRow("图片识别模型", self.ed_vision)

        # 测试连接
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setObjectName("Ghost")
        self.btn_test.clicked.connect(self.on_test)
        self.lbl_test = QLabel("未测试")
        self.lbl_test.setObjectName("StateLabel")       # theme.qss 状态选择器配色
        self._set_test_state("idle")

        test_row = QHBoxLayout()
        test_row.setSpacing(SPACE_SM)
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.lbl_test, 1)

        tip = QLabel("提示：配置保存在程序同目录 ai_config.json（UTF-8），删除该文件即恢复未配置状态。")
        tip.setObjectName("PageHint")
        tip.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(SPACE_MD)
        root.addLayout(form)
        root.addLayout(test_row)
        root.addWidget(tip)
        root.addWidget(buttons)

    # ---- 测试连接 ----
    def _set_test_state(self, state):
        """测试连接状态标签：idle/ok/warn/err → property + repolish（无内联样式）"""
        self.lbl_test.setProperty("state", state)
        repolish(self.lbl_test)

    def on_test(self):
        if self._test_worker and self._test_worker.isRunning():
            return
        if not self.ed_key.text().strip():
            self.lbl_test.setText("✗ 请先填写 API Key")
            self._set_test_state("err")
            return
        self.btn_test.setEnabled(False)
        self.lbl_test.setText("测试中…（最多 3 秒）")
        self._set_test_state("idle")
        self._test_worker = _TestWorker(self._collect())
        self._test_worker.test_done.connect(self._on_test_done)
        self._test_worker.start()

    def on_fetch_models(self):
        """拉取端点真实模型名，填充下拉框（解决"模型名该填什么"）"""
        if self._models_worker and self._models_worker.isRunning():
            return
        if not self.ed_key.text().strip():
            self.lbl_test.setText("✗ 请先填写 API Key")
            self._set_test_state("err")
            return
        self.btn_models.setEnabled(False)
        self.btn_models.setText("获取中…")
        self.lbl_test.setText("正在拉取模型列表…")
        self._set_test_state("idle")
        self._models_worker = _ModelListWorker(self._collect())
        self._models_worker.models_done.connect(self._on_models_done)
        self._models_worker.start()

    def _on_models_done(self, ok, names, message):
        self.btn_models.setEnabled(True)
        self.btn_models.setText("获取模型")
        if not ok:
            self.lbl_test.setText("✗ %s" % message)
            self._set_test_state("err")
            return
        keep = self.cmb_model.currentText().strip()
        self.cmb_model.clear()
        for name in names:
            self.cmb_model.addItem(name)
        # 优先选中：常用默认 → 之前填的（若在列表里）→ 列表第一项
        prefer = keep if keep in names else ("deepseek-chat" if "deepseek-chat" in names else names[0])
        self.cmb_model.setCurrentText(prefer)
        tip = "✓ 已获取 %d 个模型，当前选中：%s" % (len(names), prefer)
        if keep and keep not in names:
            tip += "（原填写的「%s」不在列表里，已替换）" % keep
        self.lbl_test.setText(tip)
        self._set_test_state("ok")

    def _on_test_done(self, ok, message, latency):
        self.btn_test.setEnabled(True)
        if ok:
            self.lbl_test.setText("✓ %s（延迟 %d ms）" % (message, latency))
            self._set_test_state("ok")
        else:
            self.lbl_test.setText("✗ %s（%d ms）" % (message, latency))
            self._set_test_state("err")

    # ---- 保存 ----
    def _collect(self):
        try:
            timeout = int(self.ed_timeout.text().strip())
        except ValueError:
            timeout = ai_bridge.DEFAULT_CONFIG["timeout"]
        return {
            "base_url": self.ed_base_url.text().strip(),
            "api_key": self.ed_key.text().strip(),
            "model": self.cmb_model.currentText().strip(),
            "timeout": timeout,
            "operator": self.ed_operator.text().strip(),
            "vision_model": self.ed_vision.text().strip(),
        }

    def _on_save(self):
        cfg = self._collect()
        if not ai_bridge.save_config(cfg):
            QMessageBox.warning(self, "保存失败",
                                "ai_config.json 写入失败（磁盘只读？）。配置未保存。")
            return
        self.saved = True
        self.accept()


# ===========================================================================
# 状态栏常驻 AI 指示
# ===========================================================================
class StatusIndicator(QLabel):
    """●绿=可连接 ●灰=无网络 ●黄=配置缺失"""

    STATE_TEXT = {
        "ok": ("●", SUCCESS, "AI 可用"),
        "no_net": ("●", TEXT_MUTED, "AI 无网络"),
        "no_cfg": ("●", WARNING, "AI 未配置"),
        "testing": ("●", ACCENT, "AI 检测中…"),
    }

    def __init__(self, parent=None):
        super(StatusIndicator, self).__init__(parent)
        self.set_state("testing", "")

    def set_state(self, key, detail):
        dot, color, text = self.STATE_TEXT.get(key, self.STATE_TEXT["testing"])
        self.setText("<span style='color:%s;'>%s</span> <span style='color:%s;'>%s</span>"
                     % (color, dot, TEXT_MUTED, text))
        if detail:
            self.setToolTip("AI 状态：%s\n%s" % (text, detail))
        else:
            self.setToolTip("AI 状态：%s" % text)


# ===========================================================================
# AI 诊断 Tab（对话式）
# ===========================================================================
# 样式约定（本次重构起）：气泡 / 上下文面板 / Composer / 空态 chip 等
# 全部走 theme.qss 的 objectName / 属性状态规则（BubbleUser / BubbleAi /
# CtxPanel / Composer / ExampleChip / DupCard…），本文件零内联样式。


def _human_size(num):
    """字节数 → 人话大小"""
    num = int(num or 0)
    if num >= 1024 * 1024:
        return "%.1fMB" % (num / 1024.0 / 1024.0)
    if num >= 1024:
        return "%.0fKB" % (num / 1024.0)
    return "%dB" % num


class AiTab(QWidget):
    """
    对话式 AI 诊断：
        顶部上下文面板（六字段会话基线，可折叠）→ 中部消息流 → 底部输入区。
    每轮发送 = 系统提示 + 设备上下文（面板）+ 历史（预算内从最新往回装）+ 本条。
    网络探测：启动时一次 + 每 60s 一次（QTimer 重启 NetworkProbe）。
    """

    PROBE_INTERVAL_MS = 60 * 1000       # 60s 重探
    FLUSH_INTERVAL_MS = 100             # 流式刷新节流（避免每 token 重绘）
    ECHO_WARN_CHARS = 20 * 1024         # 回显 >20KB 提示 token 消耗
    BUBBLE_MAX_RATIO = 0.80             # 用户气泡最大宽度占消息流视口比例
    AI_CARD_MARGIN = SPACE_XL           # AI 卡片两侧边距（24px，全宽内容卡片规则）
    AI_CARD_MIN_W = 320                 # AI 卡片最小宽度（短回复自适应下限）
    SPLIT_KEY = "ai_split"              # 对话区│Composer 分栏记忆 key（ui_state.json）
    SPLIT_MINS = (300, 120)             # 对话区 / Composer 最小高度（防拖成不可用）

    def __init__(self, db, parent=None):
        super(AiTab, self).__init__(parent)
        self.db = db
        self.network_ok = False
        self.network_detail = ""
        self._probe = None              # 当前探测线程引用（防 GC）
        self._worker = None             # 当前对话线程引用
        self._response_text = ""        # 当前/最近一条 AI 回复全文（转草稿用）
        self._pending = ""              # 未刷新进 UI 的流式增量
        self._streaming = False         # 是否正在流式输出（未闭合围栏的渲染策略开关）
        self._last_context = {}         # 最近一次发送的上下文（保存案例/转草稿用）
        self._last_messages = []        # 最近一次发送的 messages
        self._current_ai = None         # 流式中的 AI 气泡句柄 dict
        self._last_ai_handle = None     # 最近一条完成的 AI 气泡句柄（工具条[产出入库]用）
        self._ai_handles = {}           # message_index -> AI 气泡句柄（溯源定位用）
        self._shown_trimmed = 0         # 消息流中已提示过的裁剪轮数（只增不减）
        self.session = ai_bridge.new_session()      # 当前会话（含 ctx 与历史消息）
        self._pending_attachments = []  # 待发送附件 [{name,type,size,content,truncated}]
        self._user_frames = []          # 用户气泡 frame（resize 时同步 80% 上限）
        self._ai_cards = []             # 全部 AI 卡片 handle（resize 时统一跟随视口）

        self._build_ui()

        # 流式刷新节流定时器
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self.FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)

        # 网络探测定时器（启动即探一次）
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(self.PROBE_INTERVAL_MS)
        self._probe_timer.timeout.connect(self.start_probe)
        QTimer.singleShot(200, self.refresh_config_state)
        QTimer.singleShot(200, self.start_probe)
        self._probe_timer.start()

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_guide_page())      # index 0：引导页
        self.stack.addWidget(self._build_main_page())       # index 1：主界面

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack)

    def _build_guide_page(self):
        """未配置时的引导页：图标 + 一句话 + [去设置]"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        lay.setSpacing(SPACE_SM)
        lay.addStretch(1)

        icon = QLabel("✦")
        icon.setObjectName("EmptyIcon")           # theme.qss：muted 色 + 40px
        icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon)

        title = QLabel("AI 诊断尚未配置")
        title.setObjectName("Secondary")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        body = QLabel("填写一次 API Key 即可启用：在设置里填入任意 OpenAI 兼容端点的接口地址与密钥，\n"
                      "AI 会结合设备信息、故障现象与命令回显，在多轮对话中给出分步诊断建议（命令均标注厂商与版本）。\n"
                      "未配置时本页只做引导，主程序其他功能完全不受影响。")
        body.setObjectName("Muted")
        body.setAlignment(Qt.AlignCenter)
        lay.addWidget(body)

        self.btn_setup = QPushButton("去设置")
        self.btn_setup.setObjectName("Primary")
        self.btn_setup.setFixedWidth(140)
        self.btn_setup.clicked.connect(self.open_settings)
        lay.addWidget(self.btn_setup, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        return page

    def _build_main_page(self):
        """
        主界面（GripSplitter 纵向分栏，任务2）：
            [工具条 + 上下文面板 + 消息流]  ↕ 可拖
            [Composer 输入卡片]
        默认 stretch 4:1；对话区 minHeight 300 / Composer minHeight 120（SPLIT_MINS），
        比例记忆进 ui_state.json（key=ai_split，restore_split_state 校验回退）。
        """
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        chat_host = QWidget()
        v = QVBoxLayout(chat_host)
        v.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        v.setSpacing(SPACE_SM)
        v.addWidget(self._build_toolbar())
        v.addWidget(self._build_context_panel())
        v.addWidget(self._build_message_flow(), 1)

        composer = self._build_input_area()
        composer.setMinimumHeight(self.SPLIT_MINS[1])
        chat_host.setMinimumHeight(self.SPLIT_MINS[0])

        self.chat_split = GripSplitter(Qt.Vertical)
        self.chat_split.addWidget(chat_host)
        self.chat_split.addWidget(composer)
        self.chat_split.setStretchFactor(0, 4)
        self.chat_split.setStretchFactor(1, 1)
        self.chat_split.setSizes([640, 160])
        # 分栏拖拽 → AiTab 自身尺寸不变（resizeEvent 不触发），必须挂 splitterMoved
        self.chat_split.splitterMoved.connect(lambda *_: self._sync_card_widths())

        root.addWidget(self.chat_split)
        return page

    # ---- 分栏状态记忆（MainWindow 关闭/启动时统一收口，任务2-3）----
    def split_state(self):
        """把对话区│Composer 当前比例交给 MainWindow 并入 ui_state.json"""
        try:
            return {self.SPLIT_KEY: list(self.chat_split.sizes())}
        except Exception:
            return {}

    def restore_split_state(self, state):
        """
        启动时恢复分栏比例；read_sizes 校验失败（坏值/小于最小高度）→ 忽略。
        ★ Tab 未显示时 QSplitter 未布局，setSizes 不保证生效（冒烟实测间歇回退）——
        值先存 pending，首次 showEvent 再真正应用。
        """
        self._pending_split = read_sizes(state, self.SPLIT_KEY, self.SPLIT_MINS)
        if self._pending_split and self.isVisible():
            self._apply_pending_split()

    def _apply_pending_split(self):
        if getattr(self, "_pending_split", None):
            self.chat_split.setSizes(self._pending_split)
            self._pending_split = None

    def showEvent(self, event):
        super(AiTab, self).showEvent(event)
        self._apply_pending_split()

    def _build_toolbar(self):
        bar = QHBoxLayout()
        bar.setSpacing(SPACE_SM)

        self.btn_new = QPushButton("新建对话")
        self.btn_new.setObjectName("Ghost")
        self.btn_new.setToolTip("自动保存当前会话 → 保留厂商/OS/型号 → 清空现象/回显/已试步骤 → 清空消息流")
        self.btn_new.clicked.connect(self.on_new_session)
        bar.addWidget(self.btn_new)

        self.btn_history = QPushButton("历史")
        self.btn_history.setObjectName("Ghost")
        self.btn_history.setToolTip("打开 AI 案例历史（按现象搜索 / 重新打开 / 删除）")
        self.btn_history.clicked.connect(self.open_history)
        bar.addWidget(self.btn_history)

        self.btn_open_cases = QPushButton("案例目录")
        self.btn_open_cases.setObjectName("Ghost")
        self.btn_open_cases.setToolTip(
            "每次对话自动保存为 sessions/{会话ID}.json；点击打开目录（可整目录拷走共享）")
        self.btn_open_cases.clicked.connect(self.open_cases_dir)
        bar.addWidget(self.btn_open_cases)

        bar.addStretch(1)

        self.btn_to_draft = QPushButton("产出入库")
        self.btn_to_draft.setObjectName("Ghost")
        self.btn_to_draft.setEnabled(False)
        self.btn_to_draft.setToolTip("把 AI 产出入库到命令库 / 报错库 / 排查树（AI 生成内容一律未验证入库）")
        self.btn_to_draft.clicked.connect(self.on_import_output)
        bar.addWidget(self.btn_to_draft)

        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("AiStatus")     # theme.qss：muted 12px
        bar.addWidget(self.lbl_state)

        host = QWidget()
        host.setLayout(bar)
        return host

    # ---- 上下文面板（六字段 = 会话基线，可折叠；折叠态跨会话记忆）----
    def _build_context_panel(self):
        self.ctx_frame = QFrame()
        self.ctx_frame.setObjectName("CtxPanel")     # theme.qss：面板底 + 边框 + 卡片圆角

        v = QVBoxLayout(self.ctx_frame)
        v.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_SM)
        v.setSpacing(SPACE_XS)

        # 折叠头：箭头按钮 + 摘要（折叠时显示）
        head = QHBoxLayout()
        head.setSpacing(SPACE_SM)
        self.btn_ctx_toggle = QPushButton("▼ 设备上下文")
        self.btn_ctx_toggle.setObjectName("CtxToggle")
        self.btn_ctx_toggle.setFlat(True)
        self.btn_ctx_toggle.setCursor(Qt.PointingHandCursor)
        # 文案唯一出处：上下文自动随发的说明只留这里一处 tooltip
        self.btn_ctx_toggle.setToolTip(
            "厂商 / OS / 型号 / 故障现象 / 命令回显 / 已试步骤，每轮自动随消息发送；"
            "有新输出时更新回显区即可，下一轮生效。点击展开或收起。")
        self.btn_ctx_toggle.clicked.connect(self.toggle_context_panel)
        head.addWidget(self.btn_ctx_toggle)
        head.addStretch(1)
        self.lbl_ctx_summary = QLabel("")
        self.lbl_ctx_summary.setObjectName("HintMuted")
        self.lbl_ctx_summary.setVisible(False)          # 默认展开，摘要不显示
        head.addWidget(self.lbl_ctx_summary)
        v.addLayout(head)

        # 可折叠体：六字段 + 常驻提示
        self.ctx_body = QWidget()
        body = QVBoxLayout(self.ctx_body)
        body.setContentsMargins(0, 2, 0, 0)
        body.setSpacing(SPACE_XS)

        def _label(text):
            lbl = QLabel(text)
            lbl.setObjectName("HintMuted")
            return lbl

        self.cmb_vendor = QComboBox()
        self.cmb_vendor.setEditable(True)
        self.cmb_vendor.addItem("")                      # 首项留空 = 不限
        for name in dbmod.vendor_candidates():
            self.cmb_vendor.addItem(name)
        self.cmb_os = QComboBox()
        self.cmb_os.setEditable(True)
        self.cmb_os.addItem("")
        for name in dbmod.os_candidates():
            self.cmb_os.addItem(name)
        self.ed_device = QLineEdit()
        self.ed_device.setPlaceholderText("例如：S5720-28X-SI / USG6000E")

        info_row = QHBoxLayout()
        info_row.setSpacing(SPACE_SM)
        col1 = QVBoxLayout()
        col1.setSpacing(2)
        col1.addWidget(_label("厂商"))
        col1.addWidget(self.cmb_vendor)
        col2 = QVBoxLayout()
        col2.setSpacing(2)
        col2.addWidget(_label("OS 版本"))
        col2.addWidget(self.cmb_os)
        col3 = QVBoxLayout()
        col3.setSpacing(2)
        col3.addWidget(_label("设备型号"))
        col3.addWidget(self.ed_device)
        info_row.addLayout(col1, 1)
        info_row.addLayout(col2, 1)
        info_row.addLayout(col3, 1)
        body.addLayout(info_row)

        self.txt_symptom = QPlainTextEdit()
        self.txt_symptom.setPlaceholderText(
            "故障现象，例如：华为 S5720 配置 trunk 后对端学不到 VLAN，ping 网关丢包 50%")
        self.txt_symptom.setFixedHeight(52)
        self.txt_echo = QPlainTextEdit()
        self.txt_echo.setPlaceholderText("命令回显：把 show/display 命令的输出粘贴到这里，作为本会话的基线；有新输出时更新此处，下一轮生效")
        # ★ 回显框纵向不再 Expanding 抢空间（挤压根因之二）：限高 140，
        #   消息流是唯一弹性项，保证对话区 ≥70% 高度
        self.txt_echo.setMinimumHeight(56)
        self.txt_echo.setMaximumHeight(140)
        self.txt_steps = QPlainTextEdit()
        self.txt_steps.setPlaceholderText("已尝试步骤（可选），例如：1. 已检查两端 trunk allowed vlan；2. 已重启端口无效")
        self.txt_steps.setFixedHeight(44)

        for edit in (self.txt_symptom, self.txt_echo, self.txt_steps):
            edit.textChanged.connect(self._on_ctx_edited)
            body.addWidget(edit)

        # 说明文案已收进折叠头 tooltip（文案去重：不再重复一行 hint）
        self.lbl_echo_warn = QLabel("⚠ 回显较大（>20KB），每轮随发将消耗较多 token")
        self.lbl_echo_warn.setObjectName("HintText")     # theme.qss：warning 12px
        self.lbl_echo_warn.setVisible(False)
        body.addWidget(self.lbl_echo_warn)

        v.addWidget(self.ctx_body)

        # 字段变化 → 摘要 + token 估算
        self.cmb_vendor.editTextChanged.connect(self._on_ctx_edited)
        self.cmb_os.editTextChanged.connect(self._on_ctx_edited)
        self.ed_device.textChanged.connect(self._on_ctx_edited)
        return self.ctx_frame

    def _collect_ctx(self):
        """从面板控件收集六字段（发送与保存前统一走这里）"""
        return {
            "vendor": self.cmb_vendor.currentText().strip(),
            "os": self.cmb_os.currentText().strip(),
            "model_name": self.ed_device.text().strip(),
            "symptom": self.txt_symptom.toPlainText().strip(),
            "echo": self.txt_echo.toPlainText().strip(),
            "steps": self.txt_steps.toPlainText().strip(),
        }

    def _apply_ctx(self, ctx):
        """把 ctx 字典回填进面板（只填非空字段）"""
        ctx = ctx or {}
        if ctx.get("vendor"):
            self.cmb_vendor.setCurrentText(ctx["vendor"])
        if ctx.get("os"):
            self.cmb_os.setCurrentText(ctx["os"])
        if ctx.get("model_name"):
            self.ed_device.setText(ctx["model_name"])
        if ctx.get("symptom"):
            self.txt_symptom.setPlainText(ctx["symptom"])
        if ctx.get("echo"):
            self.txt_echo.setPlainText(ctx["echo"])
        if ctx.get("steps"):
            self.txt_steps.setPlainText(ctx["steps"])

    def _on_ctx_edited(self):
        self._update_ctx_summary()
        self.lbl_echo_warn.setVisible(len(self.txt_echo.toPlainText()) > self.ECHO_WARN_CHARS)
        self._update_token_estimate()

    def _update_ctx_summary(self):
        """折叠摘要：厂商 OS 型号 · 现象N字 · 回显N字 · 已试N步"""
        ctx = self._collect_ctx()
        bits = []
        ident = " ".join(x for x in (ctx["vendor"], ctx["os"], ctx["model_name"]) if x)
        if ident:
            bits.append(ident)
        if ctx["symptom"]:
            bits.append("现象%d字" % len(ctx["symptom"]))
        if ctx["echo"]:
            bits.append("回显%d字" % len(ctx["echo"]))
        if ctx["steps"]:
            steps_n = len([ln for ln in ctx["steps"].splitlines() if ln.strip()])
            bits.append("已试%d步" % steps_n)
        self.lbl_ctx_summary.setText(" · ".join(bits) if bits else "（尚未填写）")

    def toggle_context_panel(self, expand=None):
        """折叠/展开上下文面板；expand=True/False 强制指定。
        折叠状态写入 ui_state.json（跨会话记忆；写失败静默，U 盘只读不弹错）"""
        if expand is None:
            expand = not self.ctx_body.isVisible()
        self.ctx_body.setVisible(expand)
        self.btn_ctx_toggle.setText("▼ 设备上下文" if expand else "▶ 设备上下文")
        self.lbl_ctx_summary.setVisible(not expand)
        if expand:
            self._update_ctx_summary()
        state = load_ui_state()
        if state.get("ai_ctx_expanded") != expand:      # 值不变不写盘
            state["ai_ctx_expanded"] = expand
            save_ui_state(state)

    # ---- 消息流 ----
    def _build_message_flow(self):
        self.flow_scroll = QScrollArea()
        self.flow_scroll.setObjectName("MessageFlow")   # theme.qss：透明无边框
        self.flow_scroll.setWidgetResizable(True)
        self.flow_scroll.setFrameShape(QFrame.NoFrame)
        self.flow_host = QWidget()
        self.flow_host.setObjectName("FlowHost")
        self.flow_lay = QVBoxLayout(self.flow_host)
        # 左右各 24px（AI_CARD_MARGIN）：AI 全宽内容卡片与视口保持的安全边距
        self.flow_lay.setContentsMargins(self.AI_CARD_MARGIN, SPACE_MD,
                                         self.AI_CARD_MARGIN, SPACE_MD)
        self.flow_lay.setSpacing(SPACE_SM)
        self._build_empty_guide()
        self.flow_lay.addWidget(self._empty_guide)
        self.flow_lay.addStretch(1)
        self.flow_scroll.setWidget(self.flow_host)
        return self.flow_scroll

    # ---- 空态引导：新对话时给 2-3 个示例问题，点击填入输入框 ----
    def _build_empty_guide(self):
        guide = QWidget()
        guide.setObjectName("EmptyState")
        v = QVBoxLayout(guide)
        v.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        v.setSpacing(SPACE_MD)

        title = QLabel("AI 诊断 · 试试这样问")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title)

        subtitle = QLabel("设备上下文里的现象 / 回显会随每轮消息自动带上，这里只描述本轮要解决的问题")
        subtitle.setObjectName("HintMuted")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        v.addWidget(subtitle)

        examples = [
            "华为 S5720 配置 trunk 后对端学不到 VLAN，怎么排查？",
            "Cisco 2960 端口进入 err-disable，如何定位原因并恢复？",
            "H3C 防火墙会话数异常升高，给我一套排查命令清单",
        ]
        for text in examples:
            chip = QPushButton(text)
            chip.setObjectName("ExampleChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, t=text: self._apply_example(t))
            v.addWidget(chip)

        v.addStretch(1)
        self._empty_guide = guide

    def _apply_example(self, text):
        """点示例问题 → 填入输入框并聚焦（不自动发送，用户可改）"""
        self.txt_input.setPlainText(text)
        self.txt_input.setFocus()
        self._update_input_height()

    def _flow_add(self, widget):
        """往消息流底部插入控件（stretch 之前）并滚到底"""
        self.flow_lay.insertWidget(self.flow_lay.count() - 1, widget)
        QTimer.singleShot(0, self._scroll_flow_bottom)

    def _scroll_flow_bottom(self):
        bar = self.flow_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_flow(self):
        """清空消息流（删除全部子控件，保留末尾 stretch）；空了重新亮出空态引导"""
        while self.flow_lay.count() > 2:
            item = self.flow_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._user_frames = []          # 防止 resizeEvent 触碰已析构控件
        self._ai_cards = []
        self._ai_handles = {}
        self._last_ai_handle = None
        self._empty_guide.setVisible(True)

    def _bubble_max_width(self):
        base = max(360, self.flow_scroll.viewport().width())
        return int(base * self.BUBBLE_MAX_RATIO)

    def _add_notice(self, text):
        """灰色居中提示（裁剪提示 / 历史回看标记等）"""
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setObjectName("HintMuted")
        self._flow_add(lbl)

    def _add_user_bubble(self, text, attachments):
        """用户消息：右对齐主色气泡（纯文本 + 附件标签，点击看原文）"""
        frame = QFrame()
        frame.setObjectName("BubbleUser")               # theme.qss：accent 底 + 卡片圆角
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 6, 10, 8)
        v.setSpacing(SPACE_XS)
        if text:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(lbl)
        for att in attachments or []:
            chip = self._attachment_chip(att)
            v.addWidget(chip)
        frame.setMaximumWidth(self._bubble_max_width())
        self._user_frames.append(frame)     # resizeEvent 里随视口同步 80% 上限

        row = QHBoxLayout()
        row.setSpacing(0)
        row.addStretch(1)
        row.addWidget(frame)
        wrapper = QWidget()                             # 默认透明（QWidget 不自绘底色）
        wrapper.setLayout(row)
        self._flow_add(wrapper)

    def _attachment_chip(self, att):
        """附件标签（名称+大小），点击查看原文；图片附件带「图片」标记"""
        tag = "图片" if att.get("is_image") else "文本"
        chip = QPushButton("📎 %s（%s·%s）" % (att.get("name") or "file",
                                              _human_size(att.get("size")), tag))
        chip.setObjectName("Chip")                      # theme.qss：小号描边左对齐
        chip.setCursor(Qt.PointingHandCursor)
        chip.setToolTip("点击查看附件原文")
        chip.clicked.connect(lambda _=False, a=att: self.view_attachment(a))
        return chip

    def view_attachment(self, att):
        """弹窗查看附件原文（图片显示预览，文本显示内容；截断过的带标注）"""
        dlg = QDialog(self)
        dlg.setWindowTitle("附件：%s（%s）" % (att.get("name"), _human_size(att.get("size"))))
        dlg.resize(760, 560)
        v = QVBoxLayout(dlg)
        if att.get("is_image"):
            img_label = QLabel()
            img_label.setAlignment(Qt.AlignCenter)
            pix = QPixmap()
            try:
                pix.loadFromData(base64.b64decode(att.get("data_url", "").split(",", 1)[-1]))
            except Exception:
                pass
            if not pix.isNull():
                img_label.setPixmap(pix.scaled(dlg.width() - 60, 420,
                                               Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                img_label.setText("（图片解码失败）")
            v.addWidget(img_label, 1)
        else:
            note = ""
            if att.get("truncated"):
                note = "（原文件 %d 字节，仅前 %d 字节参与 AI 分析）" % (
                    att.get("size") or 0, ai_bridge.ATTACH_TRUNCATE_BYTES)
            if note:
                lbl = QLabel(note)
                lbl.setObjectName("HintText")           # theme.qss：warning 12px
                v.addWidget(lbl)
            body = QPlainTextEdit()
            body.setReadOnly(True)
            body.setFont(mono_font())
            body.setPlainText(att.get("content") or "")
            v.addWidget(body, 1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_copy = QPushButton("复制全文")
        btn_copy.setObjectName("Ghost")
        btn_copy.clicked.connect(lambda: copy_to_clipboard(body.toPlainText()))
        btns.addWidget(btn_copy)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(dlg.accept)
        btns.addWidget(btn_close)
        v.addLayout(btns)
        dlg.exec_()

    def _add_ai_bubble(self):
        """
        AI 回复：左对齐全宽内容卡片（Markdown 渲染 + 底部动作条）。返回句柄 dict。
        宽度规则（对上一轮"气泡上限 80%"的修订）：AI 回复是文档型内容，
        含代码块/引用块时恒占满可用宽（视口减两侧 24px）；短回复按内容自适应、
        上限同为全宽（_fit_ai_card）。用户消息仍走右侧 80% 气泡。
        """
        frame = QFrame()
        frame.setObjectName("BubbleAi")                 # theme.qss：面板底 + 边框 + 卡片圆角
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(2)

        browser = QTextBrowser()
        browser.setObjectName("BubbleBrowser")          # theme.qss：透明无边框
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)             # copycode:// 一律走 anchorClicked
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        browser.document().documentLayout().documentSizeChanged.connect(
            lambda _sz, b=browser: self._adjust_browser_height(b))
        v.addWidget(browser)

        # 动作条：[复制] [入库▸]（入库三去向本阶段置灰，阶段二开放）
        action = QHBoxLayout()
        action.setSpacing(SPACE_SM)
        btn_copy = QToolButton()
        btn_copy.setText("复制")
        btn_copy.setObjectName("BubbleAction")           # theme.qss：小号 ghost
        handle = {"frame": frame, "browser": browser, "code_blocks": [], "text": ""}
        btn_copy.clicked.connect(lambda _=False, h=handle: self._copy_bubble(h))
        action.addWidget(btn_copy)

        btn_lib = QToolButton()
        btn_lib.setText("入库 ▸")
        btn_lib.setObjectName("BubbleAction")
        btn_lib.setCursor(Qt.PointingHandCursor)
        btn_lib.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn_lib)
        act_entry = menu.addAction("命令库")
        act_err = menu.addAction("报错库")
        act_tree = menu.addAction("排查树（第 3 轮开放）")
        act_tree.setEnabled(False)
        act_tree.setToolTip("排查树去向将在第 3 轮开放")
        act_entry.triggered.connect(lambda: self._open_import_dialog(handle, "entry"))
        act_err.triggered.connect(lambda: self._open_import_dialog(handle, "err"))
        btn_lib.setMenu(menu)
        action.addWidget(btn_lib)
        action.addStretch(1)
        v.addLayout(action)

        browser.anchorClicked.connect(lambda url, h=handle: self._on_bubble_anchor(h, url))

        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(frame)
        row.addStretch(1)
        wrapper = QWidget()                             # 默认透明（QWidget 不自绘底色）
        wrapper.setLayout(row)
        self._flow_add(wrapper)
        self._last_ai_handle = handle
        self._ai_cards.append(handle)
        return handle

    def _open_import_dialog(self, handle=None, target="entry"):
        """气泡 [入库▸] 路由：带气泡的会话/消息定位打开产出入库对话框"""
        self.stack.setCurrentIndex(1)
        source = (handle or {}).get("text") if handle else self._response_text
        if not (source or "").strip():
            QMessageBox.information(self, "没有可入库的内容", "该回复没有可入库的内容。")
            return
        sid = (handle or {}).get("session_id")
        midx = (handle or {}).get("message_index")
        if sid is None:
            sid = self.session.get("session_id") or ""
            midx = -1
        if handle is None:
            for i, m in enumerate(self.session.get("messages") or []):
                if m.get("role") == "assistant":
                    midx = i
        dlg = AiImportDialog(self, self.db, source_text=source or "",
                             ctx=self._collect_ctx(), session_id=sid,
                             message_index=midx, target=target, parent=self.window())
        dlg.exec_()
        if dlg.imported:
            win = self.window()
            if hasattr(win, "refresh_all"):
                win.refresh_all()
            self.lbl_state.setText("已入库 %d 项（未验证 · 🤖 溯源已记录）" % dlg.imported)

    @staticmethod
    def _adjust_browser_height(browser):
        """QTextBrowser 高度跟随文档（消息流统一滚动，浏览器自身不出滚动条）"""
        try:
            h = int(browser.document().size().height()) + 14
            browser.setFixedHeight(max(40, h))
        except Exception:
            pass

    # ---- AI 卡片宽度治理（全宽内容卡片规则）----
    def _ai_card_avail(self):
        """卡片可用全宽 = 消息流视口宽 - 两侧 24px 边距（下限 320 防极窄窗口）"""
        return max(self.AI_CARD_MIN_W,
                   self.flow_scroll.viewport().width() - 2 * self.AI_CARD_MARGIN)

    def _natural_card_width(self, text):
        """短回复自适应宽度：按最长相邻显示行像素宽估算（含 24px 卡片内边距）"""
        fm = QFontMetrics(self.font())
        longest = 0
        for ln in (text or "").splitlines():
            ln = ln.strip()
            if ln:
                longest = max(longest, fm.horizontalAdvance(ln[:160]))
        return longest + 30

    def _fit_ai_card(self, handle, streaming=False):
        """
        按内容性质定卡片宽：
          · 文档型（含代码块/引用块）→ 恒全宽；
          · 短回复 → min(内容自然宽, 全宽)，且不小于 320；
        流式期间宽度只增不减（杜绝打字机过程左右跳动），成品渲染精确落定。
        """
        browser = handle["browser"]
        frame = handle["frame"]
        avail = self._ai_card_avail()
        if handle.get("is_doc"):
            w = avail
        else:
            w = min(max(handle.get("natural") or 0, self.AI_CARD_MIN_W), avail)
        if streaming:
            w = max(w, handle.get("width") or 0)
        handle["width"] = w
        frame.setFixedWidth(w)
        self._adjust_browser_height(browser)

    def resizeEvent(self, event):
        """窗口/Tab 尺寸变化：卡片与用户气泡跟随视口"""
        super(AiTab, self).resizeEvent(event)
        self._sync_card_widths()

    def _sync_card_widths(self):
        """
        视口宽度变化（窗口 resize / 分栏拖拽）时统一跟随：
        全宽卡跟视口、短回复卡钳在 [320, 视口]、用户气泡跟 80% 上限。
        流式中的卡片由 _fit_ai_card 接管（只增不减，避免拖拽+打字机互相拉扯）。
        """
        if not getattr(self, "flow_scroll", None):
            return
        avail = self._ai_card_avail()
        live = list(self._ai_cards)
        if self._last_ai_handle and self._last_ai_handle not in live:
            live.append(self._last_ai_handle)
        for h in live:
            try:
                if h is self._current_ai:
                    continue
                if h.get("is_doc"):
                    h["frame"].setFixedWidth(avail)
                    self._adjust_browser_height(h["browser"])
                else:
                    h["frame"].setFixedWidth(
                        min(max(h.get("natural") or 0, self.AI_CARD_MIN_W), avail))
            except RuntimeError:
                pass                            # C++ 对话气泡已析构（清流竞态）
        for frame in self._user_frames:
            try:
                frame.setMaximumWidth(self._bubble_max_width())
            except RuntimeError:
                pass

    def _render_bubble(self, handle, text, streaming=False):
        """把 Markdown 渲染进指定 AI 气泡（每气泡独立 code_blocks，[复制] 互不串号）"""
        html, blocks = md_to_html(text or "", streaming=streaming)
        handle["code_blocks"] = blocks
        handle["text"] = text or ""
        # 宽度性质：含代码块/引用块 → 文档型恒全宽；否则按内容自适应
        handle["is_doc"] = bool(re.search(r"^\s*(```|>)", text or "", re.M))
        handle["natural"] = self._natural_card_width(text)
        handle["browser"].setHtml(html)
        self._fit_ai_card(handle, streaming=streaming)

    def _render_error_bubble(self, handle, title, message):
        """在 AI 气泡顶部叠错误卡片（红左条），已生成部分照常渲染可复制"""
        html, blocks = md_to_html(self._response_text)
        handle["code_blocks"] = blocks
        handle["text"] = self._response_text
        handle["is_doc"] = True                 # 错误卡本身是表格，恒全宽
        html = (
            "<div style='margin:6px 0px;'>"
            "<table cellpadding='0' cellspacing='0' width='100%%'>"
            "<tr><td style='background-color:%s; width:3px;'></td>"
            "<td style='padding:2px 0 2px 12px;'>"
            "<span style='color:%s; font-weight:bold;'>✗ %s</span><br/>"
            "<span style='color:#e0a83c;'>%s</span>"
            "</td></tr></table></div>%s"
            % (DANGER, DANGER, title, _esc(message), html)
        )
        handle["browser"].setHtml(html)
        self._fit_ai_card(handle)

    def _copy_bubble(self, handle):
        if copy_to_clipboard(handle.get("text") or ""):
            self.lbl_state.setText("已复制本条 AI 回复全文（%d 字）" % len(handle.get("text") or ""))

    def _on_bubble_anchor(self, handle, url):
        """
        气泡内代码块右上角 [复制] 链接：copycode://blockN → 剪贴板（每气泡独立编号）。
        ★ 索引从 host+path 里抓数字：QUrl 会把纯数字 host 规范化成 IP
          （"copycode://0" 的 host 变 "0.0.0.0"，实测踩过），不能直接 int(host)。
        """
        if url.scheme() != "copycode":
            return
        m = re.search(r"\d+", (url.host() or "") + (url.path() or ""))
        if not m:
            return
        try:
            code = handle["code_blocks"][int(m.group())]
        except (ValueError, IndexError, KeyError):
            return
        if copy_to_clipboard(code):
            self.lbl_state.setText("已复制代码块 %d（%d 行）→ 可直接粘贴"
                                   % (int(m.group()) + 1, len(code.splitlines())))

    # ---- 输入区（Composer 一体化卡片：附件条 + 多行输入 + 动作行）----
    def _build_input_area(self):
        panel = QFrame()
        panel.setObjectName("Composer")                 # theme.qss：面板底 + 卡片圆角
        v = QVBoxLayout(panel)
        v.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        v.setSpacing(SPACE_XS)

        # 附件条（有待发送附件才显示）
        self.att_bar = QWidget()
        self.att_row = QHBoxLayout(self.att_bar)
        self.att_row.setContentsMargins(0, 0, 0, 0)
        self.att_row.setSpacing(SPACE_XS)
        self.att_row.addStretch(1)
        self.att_bar.setVisible(False)
        v.addWidget(self.att_bar)

        self.txt_input = QPlainTextEdit()
        self.txt_input.setObjectName("ComposerInput")
        # placeholder 精简为一句（基线说明已收进上下文折叠条 tooltip）
        self.txt_input.setPlaceholderText("输入本轮问题（可多行），或粘贴新的命令回显")
        self.txt_input.textChanged.connect(self._update_token_estimate)
        self.txt_input.textChanged.connect(self._update_input_height)
        self.txt_input.installEventFilter(self)         # 聚焦/失焦 → 2↔5 行自适应
        self.txt_input.setFocusPolicy(Qt.StrongFocus)
        v.addWidget(self.txt_input)
        self._input_focused = False
        self._update_input_height()

        bottom = QHBoxLayout()
        bottom.setSpacing(SPACE_SM)
        self.btn_attach = QPushButton("附件")
        self.btn_attach.setObjectName("Ghost")
        self.btn_attach.setToolTip("添加文本附件（.log/.txt/.cfg/.conf/.json），单文件 ≤200KB，单轮 ≤3 个，内容随本轮消息发送")
        self.btn_attach.clicked.connect(self.on_attach)
        bottom.addWidget(self.btn_attach)

        bottom.addStretch(1)

        # token 估算并入 Composer 卡片右下角
        self.lbl_tokens = QLabel("约 0 tokens")
        self.lbl_tokens.setObjectName("HintMuted")
        bottom.addWidget(self.lbl_tokens)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self.on_cancel)
        bottom.addWidget(self.btn_stop)

        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("Primary")
        self.btn_send.clicked.connect(self.on_send)
        bottom.addWidget(self.btn_send)
        v.addLayout(bottom)
        return panel

    # ---- Composer 输入框：默认 2 行，聚焦后最多扩到 5 行，随内容自适应 ----
    COMPOSER_MIN_LINES = 2
    COMPOSER_MAX_LINES = 5

    def _input_line_height(self):
        fm = self.txt_input.fontMetrics()
        return max(16, fm.height())

    def _update_input_height(self):
        """按内容行数自适应高度：默认钳在 2 行；聚焦后放宽到 5 行（spec：聚焦自动扩）。
        内容高度用末字符 cursorRect 求（对自动换行/字体都稳；document().size() 在
        offscreen 平台返回行数而非像素，不可直接用——实测踩过）"""
        edit = self.txt_input
        line_h = self._input_line_height()
        max_lines = self.COMPOSER_MAX_LINES if self._input_focused else self.COMPOSER_MIN_LINES
        min_h = line_h * self.COMPOSER_MIN_LINES + 14
        max_h = line_h * max_lines + 14
        try:
            cursor = edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            content_h = edit.cursorRect(cursor).bottom() + 12
        except Exception:
            content_h = line_h * (edit.document().blockCount() + 1) + 12
        edit.setFixedHeight(max(min_h, min(content_h, max_h)))

    def eventFilter(self, obj, event):
        """Composer 聚焦 → 放宽到 5 行；失焦 → 收回 2 行"""
        if obj is self.txt_input:
            etype = event.type()
            if etype == event.FocusIn:
                self._input_focused = True
                self._update_input_height()
            elif etype == event.FocusOut:
                self._input_focused = False
                self._update_input_height()
        return super(AiTab, self).eventFilter(obj, event)

    # ---- 附件（文本类先行；图片类随 vision_model 配置开放）----
    def _update_attach_tooltip(self):
        """[附件] tooltip 随 vision_model 配置动态变化（未配置时说明原因）"""
        if ai_bridge.vision_supported():
            self.btn_attach.setToolTip(
                "添加附件：文本 .log/.txt/.cfg/.conf/.json（单文件 ≤200KB）或图片 .png/.jpg（≤5MB），"
                "单轮 ≤3 个，内容随本轮消息发送")
        else:
            self.btn_attach.setToolTip(
                "添加文本附件（.log/.txt/.cfg/.conf/.json），单文件 ≤200KB，单轮 ≤3 个。\n"
                "图片附件未开放：请在 文件→设置 的「图片识别模型」填入多模态模型名后启用")

    def on_attach(self):
        vision = ai_bridge.vision_supported()
        text_filter = "文本文件 (*.log *.txt *.cfg *.conf *.json)"
        image_filter = "图片 (*.png *.jpg *.jpeg)"
        filter_str = text_filter + (";;" + image_filter if vision else "") + ";;所有文件 (*.*)"
        paths, _ = QFileDialog.getOpenFileNames(self, "选择附件", "", filter_str)
        for path in paths or []:
            if len(self._pending_attachments) >= ai_bridge.MAX_ATTACH_COUNT:
                QMessageBox.information(self, "附件数量超限",
                                        "单轮最多 %d 个附件；本轮已选满，可先发送后再追加。"
                                        % ai_bridge.MAX_ATTACH_COUNT)
                break
            ext = os.path.splitext(path)[1].lower()
            if ext in ai_bridge.IMAGE_EXTS:
                if not vision:
                    QMessageBox.information(
                        self, "图片附件未开放",
                        "当前未配置图片识别模型，暂不支持图片附件。\n"
                        "请到 文件→设置 →「图片识别模型」填入多模态模型名（如 gpt-4o-mini）后重试。")
                    continue
                att, err = ai_bridge.load_image_attachment(path)
            elif ext in ai_bridge.ATTACH_TEXT_EXTS:
                att, err = ai_bridge.load_text_attachment(path)
            else:
                QMessageBox.information(
                    self, "不支持的类型",
                    "支持的附件类型：文本 %s；图片 %s（需配置图片识别模型）"
                    % (" / ".join(ai_bridge.ATTACH_TEXT_EXTS),
                       " / ".join(ai_bridge.IMAGE_EXTS)))
                continue
            if err:
                QMessageBox.warning(self, "附件读取失败", err)
                continue
            self._pending_attachments.append(att)
        self._refresh_attachment_bar()
        self._update_token_estimate()

    def _refresh_attachment_bar(self):
        """重建待发送附件条：标签（点击看原文）+ ✕（移除）"""
        while self.att_row.count() > 1:
            item = self.att_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for idx, att in enumerate(list(self._pending_attachments)):
            chip = self._attachment_chip(att)
            self.att_row.insertWidget(self.att_row.count() - 1, chip)
            x = QPushButton("✕")
            x.setObjectName("Subtle")
            x.setFlat(True)
            x.setFixedWidth(20)
            x.setToolTip("移除该附件")
            x.clicked.connect(lambda _=False, i=idx: self._remove_attachment(i))
            self.att_row.insertWidget(self.att_row.count() - 1, x)
        self.att_bar.setVisible(bool(self._pending_attachments))

    def _remove_attachment(self, index):
        if 0 <= index < len(self._pending_attachments):
            del self._pending_attachments[index]
            self._refresh_attachment_bar()
            self._update_token_estimate()

    # ------------------------------------------------------------------
    # 配置状态 / 引导页切换
    # ------------------------------------------------------------------
    def refresh_config_state(self):
        """配置变化后刷新：未配置 → 引导页 + 黄灯；已配置 → 主界面"""
        if ai_bridge.is_configured():
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)
        self._update_attach_tooltip()
        self._update_status()
        self._update_send_state()

    def open_settings(self):
        """打开设置对话框；保存成功后立即重探网络并刷新状态"""
        dlg = SettingsDialog(self.window())
        dlg.exec_()
        if dlg.saved:
            self.refresh_config_state()
            self.start_probe()

    # ------------------------------------------------------------------
    # 网络探测
    # ------------------------------------------------------------------
    def start_probe(self):
        """启动一次探测（上一轮还在跑就跳过；60s 定时器循环触发）"""
        if self._probe and self._probe.isRunning():
            return
        if self._probe is not None:
            # B9（审计 2026-10-08）：旧实例已结束，排队销毁防堆积；
            # 防 GC 靠下方 self._probe 引用持有。
            # ★ 故意不挂 parent：实测（修复批 2026-10-08）带父的 QThread 会在
            #   QApplication 析构期被级联删除，若线程仍活着直接进程崩溃
            #   （退出码 127）——smoke_ai_chat 全量可复现，勿"顺手"加回。
            self._probe.deleteLater()
        cfg = ai_bridge.load_config()
        self._probe = ai_bridge.NetworkProbe(cfg.get("base_url"))
        self._probe.probe_result.connect(self._on_probe_result)
        self._probe.start()

    def _on_probe_result(self, ok, detail):
        self.network_ok = bool(ok)
        self.network_detail = detail
        self._update_status()
        self._update_send_state()

    def _update_status(self):
        """状态栏指示：配置缺失(黄) > 无网络(灰) > 可连接(绿)"""
        widget = self.status_indicator
        if widget is None:
            return
        if not ai_bridge.is_configured():
            widget.set_state("no_cfg", "在 文件→设置 中填写 API Key 后启用")
        elif not self.network_ok:
            widget.set_state("no_net", self.network_detail or "TCP 443 不可达")
        else:
            widget.set_state("ok", "端点可达：%s" % self.network_detail)

    status_indicator = None        # 主窗注入（MainWindow 把它放进状态栏）

    # ------------------------------------------------------------------
    # 发送状态 / token 估算
    # ------------------------------------------------------------------
    def _update_send_state(self):
        """无网络/未配置/生成中 → [发送] 置灰 + tooltip；生成中禁用新建/历史切换"""
        busy = bool(self._worker and self._worker.isRunning())
        for btn in (self.btn_new, self.btn_history):
            btn.setEnabled(not busy)
            btn.setToolTip("AI 正在生成，请先等待完成或点「停止」" if busy else "")
        if busy:
            self.btn_send.setEnabled(False)
            self.btn_send.setToolTip("AI 正在生成，可点「停止」中断")
            return
        if not ai_bridge.is_configured():
            self.btn_send.setEnabled(False)
            self.btn_send.setToolTip("请先在 文件→设置 中配置 AI 接口")
            return
        if not self.network_ok:
            self.btn_send.setEnabled(False)
            self.btn_send.setToolTip("当前无网络连接")
            return
        self.btn_send.setEnabled(True)
        self.btn_send.setToolTip("发送本轮消息（流式返回，可随时停止）")

    def _update_token_estimate(self):
        """
        token 估算 = 系统提示 + 上下文面板 + 附件 + 历史 + 本条
        （文本字符数/4 粗估；图片附件按固定常数计）
        """
        ctx_block = ai_bridge.build_device_context(self._collect_ctx())
        chars = len(ai_bridge.SYSTEM_PROMPT) + len(ai_bridge.SYSTEM_PROMPT_BASELINE) + len(ctx_block)
        for msg in self.session.get("messages") or []:
            chars += len(ai_bridge.merged_message_content(msg))
            chars += ai_bridge.IMAGE_TOKEN_ESTIMATE * 4 * len(          # 图片按常数折算回字符域
                [a for a in msg.get("attachments") or [] if a.get("is_image")])
        chars += len(self.txt_input.toPlainText())
        for att in self._pending_attachments:
            if att.get("is_image"):
                chars += ai_bridge.IMAGE_TOKEN_ESTIMATE * 4
            else:
                chars += len(att.get("content") or "")
        self.lbl_tokens.setText("约 %d tokens" % ai_bridge.estimate_tokens(chars))

    # ------------------------------------------------------------------
    # 发送 / 流式接收 / 停止
    # ------------------------------------------------------------------
    def on_send(self):
        if self._worker and self._worker.isRunning():
            return
        text = self.txt_input.toPlainText().strip()
        attachments = list(self._pending_attachments)
        if not text and not attachments:
            QMessageBox.information(self, "没有内容", "请先输入问题，或点「附件」添加文本附件。")
            self.txt_input.setFocus()
            return
        if not ai_bridge.is_configured():
            self.open_settings()
            return

        ctx = self._collect_ctx()
        self.session["ctx"] = ctx
        self._last_context = dict(ctx)
        first_round = not self.session["messages"]

        user_msg = {
            "role": "user",
            "content": text,
            "attachments": [dict(a) for a in attachments],
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        history = list(self.session["messages"]) + [user_msg]
        messages, trimmed = ai_bridge.build_chat_messages(ctx, history)
        self._last_messages = messages

        # ---- 消息流：用户气泡 → 裁剪提示 → AI 气泡 ----
        meta_attachments = [{k: a[k] for k in ("name", "type", "size") if k in a}
                            for a in attachments]
        self._add_user_bubble(text, meta_attachments)
        if trimmed > self._shown_trimmed:
            self._add_notice("早期 %d 轮已折叠，不参与发送" % trimmed)
            self._shown_trimmed = trimmed

        self.session["messages"].append(user_msg)
        self._pending_attachments = []
        self._refresh_attachment_bar()
        self.txt_input.clear()

        self._response_text = ""
        self._pending = ""
        self._streaming = True
        self._current_ai = self._add_ai_bubble()
        self._current_ai["browser"].setPlainText("（连接中…）")
        # 溯源定位：assistant 消息即将落在 len(messages) 这个下标
        self._current_ai["session_id"] = self.session.get("session_id") or ""
        self._current_ai["message_index"] = len(self.session.get("messages") or [])
        self._ai_handles[self._current_ai["message_index"]] = self._current_ai

        # 首轮发送后自动折叠上下文面板
        if first_round and self.ctx_body.isVisible():
            self.toggle_context_panel(expand=False)

        self.btn_stop.setVisible(True)
        self.btn_send.setEnabled(False)
        self.btn_send.setText("生成中…")                # 发送中：禁用 + 文案（完成后复原）
        self.btn_new.setEnabled(False)
        self.btn_history.setEnabled(False)
        self.lbl_state.setText("生成中…")
        self._flush_timer.start()

        cfg = ai_bridge.load_config()
        self._worker = ai_bridge.ChatWorker(self)
        self._worker.set_request(cfg, messages)
        self._worker.chunk.connect(self._on_chunk)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished_full.connect(self._on_finished)
        self._worker.start()
        self._update_token_estimate()

    def _on_chunk(self, delta):
        """增量进缓冲区，由节流定时器统一刷新（打字机效果）"""
        self._pending += delta

    def _flush_pending(self):
        """节流刷新：把缓冲增量并入全文后整体重渲染当前 AI 气泡（100ms 一次）"""
        if not self._pending:
            return
        self._response_text += self._pending
        self._pending = ""
        if self._current_ai is not None:
            self._render_bubble(self._current_ai, self._response_text, streaming=True)
            self._scroll_flow_bottom()

    def _update_draft_button(self):
        """有可入库内容才允许「产出入库」"""
        blocks = ai_bridge.extract_code_blocks(self._response_text)
        self.btn_to_draft.setEnabled(bool(self._response_text.strip()) or bool(blocks))
        if blocks:
            self.btn_to_draft.setToolTip(
                "解析到 %d 个命令块 → 选择去向入库（命令库 / 报错库 / 排查树）；"
                "AI 生成内容一律未验证入库" % len(blocks))
        else:
            self.btn_to_draft.setToolTip("当前回复没有命令块；报错库去向可直接入库报错映射")

    def on_import_output(self):
        """
        [产出入库]：AI 回复 → 命令库 / 报错库 / 排查树 三去向路由。
        AI 生成内容一律未验证入库；幂等由 ai_imports 溯源表保证。
        """
        if not self._response_text.strip():
            QMessageBox.information(self, "没有可入库的内容",
                                    "当前没有 AI 回复内容。请先完成一次对话。")
            return
        # 优先用最后一条 AI 气泡的会话定位（消息序号）；工具条入口无气泡定位时
        # 退化为"本会话最后一条 assistant 消息"
        handle = self._last_ai_handle or {}
        sid = handle.get("session_id")
        midx = handle.get("message_index")
        if sid is None:
            sid = self.session.get("session_id") or ""
            midx = -1
            for i, m in enumerate(self.session.get("messages") or []):
                if m.get("role") == "assistant":
                    midx = i
        dlg = AiImportDialog(self, self.db, source_text=self._response_text,
                             ctx=self._collect_ctx(), session_id=sid,
                             message_index=midx, parent=self.window())
        dlg.exec_()
        if dlg.imported:
            win = self.window()
            if hasattr(win, "refresh_all"):
                win.refresh_all()
            self.lbl_state.setText("已入库 %d 项（未验证 · 🤖 溯源已记录）→ 真机验证后标记转绿"
                                   % dlg.imported)

    def _on_failed(self, message):
        """失败/取消：当前 AI 气泡内显示错误卡片，输入区可继续编辑重发"""
        self._flush_timer.stop()
        self._streaming = False
        self._pending = ""
        handle = self._current_ai
        if message == "__CANCELLED__":
            if handle is not None:
                self._render_error_bubble(handle, "已取消", "本次生成已中断，输入内容保留，可修改后重发。")
            self.lbl_state.setText("已取消。输入内容保留，可修改后重发。")
        else:
            if handle is not None:
                self._render_error_bubble(handle, "无法连接", message)
            self.lbl_state.setText("失败：%s" % message[:60])
        self._current_ai = None
        self._reset_send_ui()
        self._update_draft_button()

    def _on_finished(self, info):
        """正常结束：AI 气泡落定成品渲染 → 回复入会话 → 会话落盘"""
        self._flush_timer.stop()
        self._streaming = False          # ★ 先落定"成品"状态，再收尾渲染一次
        self._flush_pending()
        handle = self._current_ai
        if handle is not None:
            self._render_bubble(handle, self._response_text, streaming=False)
        self._current_ai = None
        self._reset_send_ui()

        usage = info.get("usage") or {}
        tokens = "prompt %s / completion %s" % (usage.get("prompt_tokens", "?"),
                                                usage.get("completion_tokens", "?"))
        self.session["messages"].append({
            "role": "assistant",
            "content": self._response_text,
            "attachments": [],
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self.session["ctx"] = self._collect_ctx()
        saved = ai_bridge.save_session(self.session)
        note = "，会话已存" if saved else "（会话保存失败：目录只读？）"
        self._update_draft_button()
        self._update_token_estimate()
        self.lbl_state.setText("完成（%s · %s%s）"
                               % (tokens, datetime.datetime.now().strftime("%H:%M:%S"), note))

    def _reset_send_ui(self):
        self.btn_stop.setVisible(False)
        self.btn_send.setEnabled(True)      # 立即恢复点击态，网络细分状态由 _update_send_state 接管
        self.btn_send.setText("发送")       # 生成中文案复原（_update_send_state 只管 enable/tooltip）
        self._update_send_state()

    def on_cancel(self):
        """中断当前生成（线程内关连接，UI 立即恢复）"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    # ------------------------------------------------------------------
    # 会话生命周期（任务4：新建对话 / 四入口适配）
    # ------------------------------------------------------------------
    def on_new_session(self):
        """[新建对话]：保存当前会话 → 保留身份字段 → 清空问题字段 → 清空消息流"""
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "正在生成", "请先等待生成完成，或点「停止」后再新建对话。")
            return
        self._save_current_session()
        ctx = self._collect_ctx()
        self._reset_session(identity={"vendor": ctx["vendor"], "os": ctx["os"],
                                      "model_name": ctx["model_name"]})
        self.lbl_state.setText("已开启新会话（厂商/OS/型号已保留，旧会话已进历史）。")

    def _reset_session(self, identity=None):
        """开启新会话的统一动作：重置会话对象 + 清空问题字段与消息流（身份字段保留）"""
        self.session = ai_bridge.new_session(identity or {})
        self.txt_symptom.clear()
        self.txt_echo.clear()
        self.txt_steps.clear()
        self.txt_input.clear()
        self._pending_attachments = []
        self._refresh_attachment_bar()
        self._shown_trimmed = 0
        self._response_text = ""
        self._last_context = {}
        self._last_messages = []
        self._current_ai = None
        self._ai_handles = {}
        self._clear_flow()
        self._add_notice("新会话已就绪：在下方输入问题；上方设备上下文面板的内容会每轮自动随消息发送。")
        self.toggle_context_panel(expand=True)
        self._update_draft_button()
        self._update_ctx_summary()
        self._update_token_estimate()

    def _save_current_session(self):
        """当前会话有消息才落盘（不重复保存空会话）"""
        if not self.session.get("messages"):
            return ""
        self.session["ctx"] = self._collect_ctx()
        return ai_bridge.save_session(self.session)

    def open_cases_dir(self):
        """打开 sessions 目录（离网场景：整目录拷走即可团队共享；旧案例仍在 ai_cases）"""
        path = ai_bridge.sessions_dir()
        try:
            os.startfile(path)               # Windows 资源管理器
        except Exception:
            self.lbl_state.setText("会话目录：%s" % path)

    def open_history(self):
        """历史抽屉：sessions 会话库（打开可继续对话）+ 旧 ai_cases 兼容浏览"""
        dlg = SessionHistoryDialog(self, self.window())
        dlg.exec_()

    def load_session_into_ui(self, session):
        """
        打开历史会话 → 完整恢复：面板六字段 + 消息流逐条重建（用户气泡/AI 气泡）。
        打开后继续对话：session 对象整体接管，组装/裁剪/保存与新消息一致
        （session_id 不变 → 落盘回写同一文件）。
        """
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "正在生成", "请先等待生成完成或点「停止」，再打开历史会话。")
            return False
        self._save_current_session()
        # 兜底归一化：缺字段补空，防止旧版本/手改文件缺键
        session.setdefault("session_id", "restored_%s" % datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        session["ctx"] = {key: str((session.get("ctx") or {}).get(key) or "")
                          for key in ai_bridge.CTX_FIELDS}
        session["messages"] = [m for m in (session.get("messages") or [])
                               if isinstance(m, dict) and m.get("role") in ("user", "assistant")]

        # 面板六字段按 ctx 精确回填（含空值，避免残留当前会话内容）
        self._panel_set_all(session["ctx"])

        self.session = session
        self._clear_flow()
        self._shown_trimmed = 0
        self._response_text = ""
        self._current_ai = None
        self._ai_handles = {}
        for idx, msg in enumerate(session["messages"]):
            meta = [{k: a[k] for k in ("name", "type", "size") if k in a}
                    for a in (msg.get("attachments") or [])]
            if msg["role"] == "user":
                self._add_user_bubble(msg.get("content") or "", meta)
            else:
                handle = self._add_ai_bubble()
                handle["session_id"] = session["session_id"]
                handle["message_index"] = idx
                self._ai_handles[idx] = handle
                self._render_bubble(handle, msg.get("content") or "", streaming=False)
                self._response_text = msg.get("content") or ""
        if not session["messages"]:
            self._add_notice("该会话没有已保存的消息，可直接输入问题继续。")
        else:
            self._add_notice("已打开历史会话 · %s（%d 条消息）— 可继续对话，新消息追加保存到本会话"
                             % (session.get("created_at") or "", len(session["messages"])))
        self.toggle_context_panel(expand=True)
        self._update_draft_button()
        self._update_ctx_summary()
        self._update_token_estimate()
        self.lbl_state.setText("历史会话已恢复（%d 条消息），可继续对话。" % len(session["messages"]))
        return True

    def open_session_and_locate(self, session_id, message_index):
        """溯源跳转：打开会话并定位到指定 AI 回复气泡（条目/报错/树详情入口）"""
        path = os.path.join(ai_bridge.sessions_dir(), "%s.json" % (session_id or ""))
        session = ai_bridge.load_session(path)
        if not session:
            self.lbl_state.setText("来源会话不存在或已被删除：%s" % session_id)
            return False
        if not self.load_session_into_ui(session):
            return False
        try:
            handle = self._ai_handles.get(int(message_index))
        except (TypeError, ValueError):
            handle = None
        if handle:
            self.flow_scroll.ensureWidgetVisible(handle["frame"], 0, 80)
            self.lbl_state.setText("已定位到来源回复（消息 #%s）。" % message_index)
        else:
            self.lbl_state.setText("会话已打开（未找到原回复定位，可能已被重建）。")
        return True

    def _panel_set_all(self, ctx):
        """面板六字段精确回填（空值也回填，用于历史恢复）"""
        self.cmb_vendor.setCurrentText(ctx.get("vendor") or "")
        self.cmb_os.setCurrentText(ctx.get("os") or "")
        self.ed_device.setText(ctx.get("model_name") or "")
        self.txt_symptom.setPlainText(ctx.get("symptom") or "")
        self.txt_echo.setPlainText(ctx.get("echo") or "")
        self.txt_steps.setPlainText(ctx.get("steps") or "")

    def load_case_record(self, record):
        """旧 ai_cases 案例回填：上下文进面板，原答案渲染为只读 AI 气泡"""
        self.stack.setCurrentIndex(1)
        ctx = record.get("context") or {}
        self._apply_ctx(ctx)
        self._update_ctx_summary()
        self._add_notice("历史案例回看 · %s（旧格式 ai_cases，新消息将按当前面板上下文继续）"
                         % (record.get("ts_iso") or ""))
        response = record.get("response") or ""
        handle = self._add_ai_bubble()
        handle["session_id"] = ""           # 旧案例无会话溯源（幂等/跳转不可用，可照常入库）
        handle["message_index"] = -1
        self._response_text = response
        self._last_context = dict(ctx)
        self._render_bubble(handle, response, streaming=False)
        self._update_draft_button()
        usage = record.get("tokens") or {}
        self.lbl_state.setText("历史案例 · %s（prompt %s / completion %s）"
                               % (record.get("ts_iso") or "",
                                  usage.get("prompt_tokens", "?"),
                                  usage.get("completion_tokens", "?")))

    # ------------------------------------------------------------------
    # 跨 Tab 联动入口（四入口信号槽与调用方零改动，只改本落点）
    # ------------------------------------------------------------------
    def prefill_context(self, vendor="", os_name="", model="", symptom="",
                        echo="", steps="", walk_record=None, leaf_data=None):
        """
        四个既有 Tab 的 [问 AI] 统一走这里（任务4-2 对话式适配）：
            1) 自动保存当前会话（有消息才落盘）
            2) 新建对话：保留身份字段（厂商/OS/型号），清空问题字段与消息流
            3) 面板按入口传入信息预填（含排查向导的结构化走树/结论格式）
            4) 上下文组装为开场消息草稿插入输入框 —— 可编辑后手动发送，不自动发送
        入口类型由传入参数特征推断（walk_record→向导；steps 前缀→报错诊断/输出分析），
        四个调用方（ui_main/ui_troubleshoot×2/ui_errorfix）零改动。
        """
        self.stack.setCurrentIndex(1)          # 从引导页切回主界面
        if self._worker and self._worker.isRunning():
            self.lbl_state.setText("AI 正在生成，请先等待完成或点「停止」后再从其他入口问 AI。")
            return
        entry = self._infer_entry(steps, symptom, walk_record)

        # 1) 保存当前会话；2) 新会话（保留身份字段）
        self._save_current_session()
        ctx_now = self._collect_ctx()
        self._reset_session(identity={"vendor": ctx_now["vendor"], "os": ctx_now["os"],
                                      "model_name": ctx_now["model_name"]})

        # 3) 面板预填
        if vendor:
            self.cmb_vendor.setCurrentText(vendor)
        if os_name:
            self.cmb_os.setCurrentText(os_name)
        if model:
            self.ed_device.setText(model)
        if symptom:
            self.txt_symptom.setPlainText(symptom)
        if echo:
            self.txt_echo.setPlainText(echo)

        if walk_record:
            # 排查向导：渲染结构化模板（用户可见，发送内容与所见一致）
            text = self._compose_walk_text(steps, walk_record, leaf_data, vendor)
            if text:
                self.txt_steps.setPlainText(text)
        elif steps:
            self.txt_steps.setPlainText(steps)    # 其它入口：保持既有自由文本行为

        # 4) 开场消息草稿（可编辑后手动发送，不自动发送）
        draft = self._compose_draft(entry, symptom)
        self.txt_input.setPlainText(draft)
        self._update_ctx_summary()
        self._update_token_estimate()
        self.txt_input.setFocus()
        self.lbl_state.setText("已按入口预填并生成开场草稿（可编辑），点「发送」开始对话。")

    @staticmethod
    def _infer_entry(steps, symptom, walk_record):
        """由参数特征推断 [问 AI] 来源入口（调用方零改动的前提下区分草稿话术）"""
        if walk_record:
            return "wizard"
        if (steps or "").startswith("本地报错字典"):
            return "errorfix"
        if (steps or "").startswith("输出分析器") or (symptom or "").startswith("输出分析"):
            return "analyzer"
        return "cmdlib"

    @staticmethod
    def _compose_draft(entry, symptom):
        """按入口生成开场消息草稿（引用面板上下文，可编辑）"""
        if entry == "wizard":
            return ("排查向导已给出结论（见设备上下文面板）。"
                    "请先评估【排查结论】的成立性、前提条件与遗漏分支，再给出补充排查命令与验证路径。")
        if entry == "errorfix":
            return ("本地报错字典已给出方案但未解决（见「已尝试步骤」），"
                    "请给出进一步排查思路与验证命令。")
        if entry == "analyzer":
            return ("输出分析器已识别异常项（见「已尝试步骤」），"
                    "请结合命令回显给出下一步排查建议与验证命令。")
        draft = "请结合上方设备上下文与命令回显，给出诊断建议、风险提示与验证步骤。"
        if not (symptom or "").strip():
            draft = "（可先补充故障现象）" + draft
        return draft

    @staticmethod
    def _compose_walk_text(steps, walk_record, leaf_data, vendor):
        """把走树记录 / 结论 / 处理动作拼成结构化上下文文本（用户可见、可编辑）"""
        parts = []
        if walk_record:
            lines = ["【已走路径】"]
            total = len(walk_record)
            for i, rec in enumerate(walk_record, 1):
                title = (rec.get("title") or rec.get("step_id") or "（未命名步骤）").strip()
                branch = (rec.get("branch_label") or "").strip()
                if branch:
                    obs = branch
                elif i == total:
                    obs = "（当前步骤，尚未选择）"
                else:
                    obs = "（未记录）"
                lines.append("%d. %s — 观察：%s" % (i, title, obs))
            parts.append("\n".join(lines))
        if steps and steps.strip():
            parts.append("【补充说明】\n%s" % steps.strip())
        if leaf_data:
            conclusion = (leaf_data.get("conclusion") or "").strip()
            if conclusion:
                parts.append("【排查结论】%s" % conclusion)
            actions = leaf_data.get("actions") or []
            if actions:
                rows = []
                for i, act in enumerate(actions, 1):
                    text = (act.get("text") if isinstance(act, dict) else str(act)) or ""
                    rows.append("%d. %s" % (i, text.strip()))
                parts.append("【处理动作建议】%s" % "\n".join(rows))
        if not parts:
            return ""
        if not vendor:
            parts.append("（注：设备厂商未确认，请对命令标注适用厂商并提示版本差异）")
        return "\n".join(parts)

# ===========================================================================
# 产出入库对话框（阶段二）：AI 产出 → 命令库 / 报错库 / 排查树 三去向路由
# ===========================================================================
class DedupeWorker(QThread):
    """
    查重预查线程（对话框打开后异步执行，不阻塞 UI）：
        dup_done(kind, candidates)  —— 候选已就绪（level/score/diff 见 dedupe.py）
        dup_failed(message)
    """
    dup_done = pyqtSignal(str, list)
    dup_failed = pyqtSignal(str)

    def __init__(self, db, kind, payload, threshold=0.80, parent=None):
        super(DedupeWorker, self).__init__(parent)
        self._db = db
        self._kind = kind
        self._payload = dict(payload or {})
        self._threshold = threshold

    def run(self):
        try:
            cands = self._db.find_duplicates(self._kind, self._payload, self._threshold)
            self.dup_done.emit(self._kind, cands)
        except Exception as exc:
            self.dup_failed.emit("%s：%s" % (self._kind, exc))


def _diff_to_html(diff_text):
    """unified diff → 着色 HTML（'+'绿/'-'红/'@@'蓝，等宽渲染）"""
    out = ["<pre style='font-family:%s; font-size:8pt; margin:2px;'>" % MONO_FONT_FAMILY]
    for ln in (diff_text or "").splitlines():
        esc = (ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        if ln.startswith("+++") or ln.startswith("---"):
            out.append("<span style='color:#6a7080;'>%s</span>" % esc)
        elif ln.startswith("@@"):
            out.append("<span style='color:#4f8cff;'>%s</span>" % esc)
        elif ln.startswith("+"):
            out.append("<span style='color:#3fbf6f;'>%s</span>" % esc)
        elif ln.startswith("-"):
            out.append("<span style='color:#e05656;'>%s</span>" % esc)
        else:
            out.append("<span style='color:#9aa0b0;'>%s</span>" % esc)
    out.append("</pre>")
    return "".join(out)


def make_ai_source_label(db, target_uuid):
    """
    AI 溯源标签（条目/报错/树详情用）。无溯源返回 None，有则返回 (label, rec)。
    label 为富文本链接（🤖 来源：AI 会话（时间）），宿主接到 linkActivated 后
    调 open_ai_source(win, rec) 完成跳转。
    """
    try:
        recs = db.imports_for_target(target_uuid)
    except Exception:
        return None
    if not recs:
        return None
    rec = recs[0]
    lbl = QLabel()
    lbl.setTextFormat(Qt.RichText)
    lbl.setText("<a href='aijump' style='color:#9aa0b0; font-size:11px;'>"
                "🤖 来源：AI 会话（%s）</a>" % (rec.get("imported_at") or ""))
    lbl.setToolTip("点击打开来源会话并定位到该条回复（消息 #%s）" % rec.get("message_index"))
    lbl.setCursor(Qt.PointingHandCursor)
    return lbl, rec


def open_ai_source(win, rec):
    """宿主窗口跳转：切到 AI 诊断 Tab，打开来源会话并定位到该条回复"""
    tab = getattr(win, "tab_ai", None)
    tabs = getattr(win, "tabs", None)
    if tab is None or tabs is None or not hasattr(tab, "open_session_and_locate"):
        return False
    tabs.setCurrentWidget(tab)
    return tab.open_session_and_locate(rec.get("session_id"), rec.get("message_index"))


class AiImportDialog(QDialog):
    """
    AI 产出入库（三去向，一次操作只落一个目标）：
        · 顶部去向切换（记住上次选择：ai_config.json 的 import_target_last）
        · 元数据预填 = AI Tab 上下文面板（厂商/OS/型号/现象）
        · 【命令库】合并单条目（默认）/ 逐块独立（高级）；非命令块不进 commands、
          可追加到 notes；drafts/*.nlb 留痕；走 add_entries_from_ai（未验证入库）
        · 【报错库】三映射编辑区（报错原文必填 / 原因分析 / 修正命令），
          归一化查重命中可选跳过；走 add_err_dict_item
        · 【排查树】第 3 轮开放（本版置灰）
        · 幂等：块已在 ai_imports 中 → 标「已入库」默认不勾选
    防幻觉闸门沿用已验收的「转为草稿条目」逻辑：默认不勾选、可编辑、
    .nlb 留痕、未验证入库（DraftFromAiDialog 保留未动，作为历史兼容）。
    """

    TARGET_LABELS = (("entry", "命令库"), ("err", "报错库"), ("tree", "排查树"))

    def __init__(self, tab, db, source_text="", ctx=None, session_id="",
                 message_index=-1, target=None, parent=None):
        super(AiImportDialog, self).__init__(parent)
        self.tab = tab
        self.db = db
        self.source_text = source_text or ""
        self.ctx = dict(ctx or {})
        self.session_id = session_id or ""
        self.message_index = int(message_index if message_index is not None else -1)
        self.imported = 0                    # 本次成功入库条数（关闭后 tab 读取）
        self.blocks = ai_bridge.extract_ai_blocks(self.source_text)

        # 幂等预检：已入库块集合 + 报错库是否已入过本条回复
        self.imported_blocks = set()
        for blk in self.blocks:
            if self.db.get_ai_import(self.session_id, self.message_index,
                                     blk["block_index"], "entry"):
                self.imported_blocks.add(blk["block_index"])
        self.err_imported = bool(self.db.get_ai_import(
            self.session_id, self.message_index, -1, "err"))
        self.tree_imported = bool(self.db.get_ai_import(
            self.session_id, self.message_index, -1, "tree"))

        # 三级查重状态：候选缓存 / 异步预查状态 / 用户决策（key=候选主键）
        self.dup_candidates = {"entry": [], "err": [], "tree": []}
        self._dup_state = {}                 # kind → idle/running/done
        self._dup_decisions = {"entry": {}, "err": {}, "tree": {}}
        self._dup_worker = None

        cfg = ai_bridge.load_config()
        self._model = cfg.get("model") or ""
        self._operator = cfg.get("operator") or "ai-import"
        # 去向优先级：调用方显式指定（气泡子菜单）> 记住的上次选择 > 默认命令库
        explicit = target if target in ("entry", "err", "tree") else None
        last = cfg.get("import_target_last")
        target = explicit or (last if last in ("entry", "err", "tree") else "entry")

        self.setWindowTitle("产出入库 · AI 产出 → 知识库（一律未验证）")
        self.setMinimumSize(880, 700)

        root = QVBoxLayout(self)
        root.setSpacing(SPACE_SM)

        # ---- 去向切换行 ----
        switch = QHBoxLayout()
        switch.setSpacing(SPACE_SM)
        switch.addWidget(QLabel("去向："))
        self._target_buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in self.TARGET_LABELS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("Ghost")
            btn.setChecked(key == target)
            btn.clicked.connect(lambda _=False, k=key: self.switch_target(k))
            group.addButton(btn)
            self._target_buttons[key] = btn
            switch.addWidget(btn)
        switch.addStretch(1)
        self.lbl_session = QLabel("会话 %s · 消息 #%s" % (self.session_id or "（无溯源）",
                                                         self.message_index))
        self.lbl_session.setObjectName("HintMuted")
        switch.addWidget(self.lbl_session)
        root.addLayout(switch)

        # ---- 共享元数据（预填自上下文面板）----
        meta_row = QHBoxLayout()
        meta_row.setSpacing(SPACE_SM)
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.setEditable(True)
        self.cmb_vendor.addItem("")
        for name in dbmod.vendor_candidates():
            self.cmb_vendor.addItem(name)
        self.cmb_vendor.setCurrentText(self.ctx.get("vendor") or "")
        self.cmb_os = QComboBox()
        self.cmb_os.setEditable(True)
        self.cmb_os.addItem("")
        for name in dbmod.os_candidates():
            self.cmb_os.addItem(name)
        self.cmb_os.setCurrentText(self.ctx.get("os") or "")
        self.ed_model = QLineEdit(self.ctx.get("model_name") or "")
        self.ed_model.setPlaceholderText("型号（可选）")
        self.cmb_platform = QComboBox()
        self.cmb_platform.addItem("网络设备", "network")
        self.cmb_platform.addItem("Linux 服务器", "linux")
        guess = DraftFromAiDialog._guess_platform(self.ctx.get("os") or "")
        self.cmb_platform.setCurrentIndex(0 if guess == "network" else 1)
        for label, widget in (("厂商", self.cmb_vendor), ("OS 版本", self.cmb_os),
                              ("型号", self.ed_model), ("平台", self.cmb_platform)):
            col = QVBoxLayout()
            col.setSpacing(2)
            cap = QLabel(label)
            cap.setObjectName("HintMuted")
            col.addWidget(cap)
            col.addWidget(widget)
            wrap = QWidget()
            wrap.setLayout(col)
            meta_row.addWidget(wrap)
        root.addLayout(meta_row)

        # ---- 三级查重卡片面板（共享；按去向渲染候选，可展开 diff）----
        self.dup_panel = QWidget()
        self.dup_v = QVBoxLayout(self.dup_panel)
        self.dup_v.setContentsMargins(0, 0, 0, 0)
        self.dup_v.setSpacing(SPACE_XS)
        self.dup_panel.setVisible(False)
        root.addWidget(self.dup_panel)

        # ---- 三去向页面 ----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_entry_page())       # index 0
        self.stack.addWidget(self._build_err_page())         # index 1
        self.stack.addWidget(self._build_tree_page())        # index 2（占位）
        root.addWidget(self.stack, 1)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setObjectName("HintMuted")
        root.addWidget(self.lbl_hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("Ghost")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        self.btn_confirm = QPushButton("确认入库（未验证）")
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.clicked.connect(self.on_confirm)
        btns.addWidget(self.btn_confirm)
        root.addLayout(btns)

        self.switch_target(target)

    # ---- 去向切换 ----
    def switch_target(self, key):
        self._target = key
        self.stack.setCurrentIndex({"entry": 0, "err": 1, "tree": 2}.get(key, 0))
        for k, btn in self._target_buttons.items():
            btn.setChecked(k == key)
        self.btn_confirm.setText("确认入库（未验证）")
        if key == "err" and self.err_imported:
            self.lbl_hint.setText("⚠ 本条回复的报错映射此前已入库过（溯源可查）；"
                                  "再次提交会产生第二条映射，请确认。")
        elif key == "tree":
            if not (self.ctx.get("symptom") or "").strip() and self.rb_new_tree.isChecked():
                self.lbl_hint.setText("⚠ 新建树要求故障现象非空：请先在 AI Tab 上下文面板填写「故障现象」"
                                      "（或改用「追加现有树」）。")
            elif self.tree_imported:
                self.lbl_hint.setText("⚠ 本条回复此前已入过一棵树（溯源可查）；再次提交请确认。")
        # 三级查重：未预查过则异步启动；已完成则渲染候选卡片
        if self._dup_state.get(key) not in ("running", "done"):
            self._start_dup_check(key)
        if self._dup_state.get(key) == "running":
            self.lbl_hint.setText("查重中…（候选就绪后自动显示，提交时会再次复核）")
        else:
            self._render_dup_cards(key)

    # ---- 三级查重：异步预查 + 候选卡片 ----
    def _dup_payload(self, kind):
        """按去向组装预查 payload（预查用当前默认内容；提交时还会复核一次）"""
        if kind == "entry":
            vendor_slug = dbmod.normalize_vendor(self.cmb_vendor.currentText().strip())
            rows = [r for r in self.entry_rows
                    if r["kind"] == "command" and r["chk"].isEnabled()]
            pseudo = [{"kind": "command", "code": r["body"].toPlainText(), "desc": r["desc"]}
                      for r in rows]
            merged, _ = ai_bridge.merge_code_blocks(pseudo, vendor_slug)
            return {"commands": merged, "vendor": vendor_slug}
        if kind == "err":
            return {"raw_text": self.ed_raw.toPlainText()}
        if kind == "tree":
            return {"symptom": (self.ctx.get("symptom") or "").strip()}
        return {}

    def _start_dup_check(self, kind):
        payload = self._dup_payload(kind)
        primary_field = {"entry": "commands", "err": "raw_text", "tree": "symptom"}.get(kind)
        has_content = bool(str(payload.get(primary_field) or "").strip())
        if not has_content:
            self._dup_state[kind] = "done"
            return
        self._dup_state[kind] = "running"
        self._dup_worker = DedupeWorker(self.db, kind, payload, dedupe.WARN_THRESHOLD)
        self._dup_worker.dup_done.connect(self._on_dup_done)
        self._dup_worker.dup_failed.connect(self._on_dup_failed)
        self._dup_worker.start()

    def _on_dup_done(self, kind, cands):
        self._dup_state[kind] = "done"
        self.dup_candidates[kind] = cands or []
        if self.lbl_hint.text().startswith("查重中"):
            self.lbl_hint.setText("")
            if kind == "err" and self.err_imported:
                self.lbl_hint.setText("⚠ 本条回复的报错映射此前已入库过（溯源可查）；"
                                      "再次提交会产生第二条映射，请确认。")
            elif kind == "tree" and self.tree_imported:
                self.lbl_hint.setText("⚠ 本条回复此前已入过一棵树（溯源可查）；再次提交请确认。")
        if self._target != kind:
            return
        if kind == "tree":
            # 高度疑似已有同现象树 → 默认引导切换「追加现有树」
            high = next((c for c in cands if c["level"] == "high"), None)
            if high and self.rb_new_tree.isChecked():
                self.rb_append_tree.setChecked(True)
                self._refresh_append_trees()
                for i, t in enumerate(getattr(self, "_append_candidates", [])):
                    if t["tree_id"] == high["tree_id"]:
                        self.cmb_append_tree.setCurrentIndex(i)
                        break
                self.lbl_hint.setText("⚠ 检测到高度疑似已有树《%s》（相似 %s）——"
                                      "已引导切换「追加现有树」，请确认插入位置后提交。"
                                      % (high["title"], dedupe.percent(high["score"])))
        self._render_dup_cards(kind)

    def _on_dup_failed(self, message):
        self.lbl_hint.setText("查重失败（不影响入库，提交时会再复核）：%s" % message[:80])

    def _default_dup_action(self, kind, cand):
        """默认决策：命令库 high→跳过 / warn→仍入库；报错库默认追加原因分析（任务2-2）"""
        if kind == "err":
            return "append"
        return "skip" if cand["level"] == "high" else "keep"

    def _render_dup_cards(self, kind):
        """把候选渲染成可展开卡片：头部相似度着色，展开为 diff 着色预览 + 处理决策"""
        while self.dup_v.count():
            item = self.dup_v.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        cands = self.dup_candidates.get(kind) or []
        if not cands:
            self.dup_panel.setVisible(False)
            return
        self.dup_panel.setVisible(True)
        cap = QLabel("查重结果：%d 条候选（黄色=疑似相似，红色=高度疑似；提交时复核）" % len(cands))
        cap.setObjectName("HintMuted")
        self.dup_v.addWidget(cap)
        for c in cands:
            key = c.get("uuid") or c.get("err_id") or c.get("tree_id")
            level = c["level"]                      # "high"→红 / 其他→黄（theme.qss 状态边框）
            card = QFrame()
            card.setObjectName("DupCard")
            card.setProperty("level", level)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(6, 4, 6, 4)
            cv.setSpacing(2)

            toggle = QPushButton("▸ 疑似与《%s》相似（%s）· %s"
                                 % (c["title"], dedupe.percent(c["score"]),
                                    c.get("summary") or ""))
            toggle.setObjectName("DupToggle")
            toggle.setProperty("level", level)
            toggle.setFlat(True)
            toggle.setCursor(Qt.PointingHandCursor)
            cv.addWidget(toggle)

            diff_view = QTextBrowser()
            diff_view.setVisible(False)
            diff_view.setMaximumHeight(140)
            diff_view.setHtml(_diff_to_html(c.get("diff") or ""))

            def _toggle(_=False, v=diff_view, btn=toggle, t=c):
                vis = not v.isVisible()
                v.setVisible(vis)
                btn.setText(("▾ " if vis else "▸ ") + "疑似与《%s》相似（%s）· %s"
                            % (t["title"], dedupe.percent(t["score"]), t.get("summary") or ""))
            toggle.clicked.connect(_toggle)
            cv.addWidget(diff_view)

            # 决策行（排查树无四选：用引导切换追加模式代替）
            if kind in ("entry", "err"):
                combo = QComboBox()
                if kind == "entry":
                    combo.addItem("跳过（不入库本次内容）", "skip")
                    combo.addItem("仍入库（不同厂商/版本的合理重复）", "keep")
                    combo.addItem("替换现有条目（verified 归 0）", "replace")
                else:
                    combo.addItem("追加原因分析到现有条目（推荐，不影响其验证状态）", "append")
                    combo.addItem("跳过（不入库）", "skip")
                    combo.addItem("仍入库（生成第二条映射）", "keep")
                default_act = self._dup_decisions[kind].get(key) or \
                    self._default_dup_action(kind, c)
                idx = combo.findData(default_act)
                combo.setCurrentIndex(max(0, idx))
                self._dup_decisions[kind][key] = default_act
                combo.currentIndexChanged.connect(
                    lambda _i, k=kind, kk=key, cb=combo:
                    self._dup_decisions[k].__setitem__(kk, cb.currentData()))
                row = QHBoxLayout()
                row.addWidget(QLabel("处理："))
                row.addWidget(combo, 1)
                cv.addLayout(row)
            elif c["level"] == "high":
                guide = QLabel("⚠ 高度疑似已有同现象树 —— 建议使用下方「追加现有树」模式（已自动切换）")
                guide.setWordWrap(True)
                guide.setObjectName("HintText")         # theme.qss：warning 12px
                cv.addWidget(guide)

            self.dup_v.addWidget(card)

    def done(self, result):
        """关闭前等待预查线程收尾，避免线程随对话框销毁导致崩溃"""
        if self._dup_worker is not None and self._dup_worker.isRunning():
            self._dup_worker.wait(1500)
        super(AiImportDialog, self).done(result)

    def _remember_target(self):
        """记住本次去向选择（写入 ai_config.json，失败静默）"""
        try:
            cfg = ai_bridge.load_config()
            cfg["import_target_last"] = self._target
            ai_bridge.save_config(cfg)
        except Exception:
            pass

    # ==================================================================
    # 去向 1：命令库
    # ==================================================================
    def _build_entry_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(SPACE_SM)

        # 模式行
        mode = QHBoxLayout()
        self.rb_merge = QRadioButton("合并单条目（推荐：多步骤拼成一个条目，注释行分隔）")
        self.rb_merge.setChecked(True)
        self.rb_merge.toggled.connect(self._refresh_entry_preview)
        self.rb_single = QRadioButton("逐块独立（高级：每块一个条目）")
        self.rb_single.toggled.connect(self._refresh_entry_preview)
        mode.addWidget(self.rb_merge)
        mode.addWidget(self.rb_single)
        mode.addStretch(1)
        v.addLayout(mode)

        # 块列表（可滚动）
        self.entry_rows = []                 # [{index, kind, chk, body, desc}]
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setSpacing(SPACE_SM)
        for blk in self.blocks:
            row_lay = QVBoxLayout()
            row_lay.setSpacing(2)
            head = QHBoxLayout()
            chk = QCheckBox("块%d · %s" % (blk["block_index"],
                                           "命令" if blk["kind"] == "command" else "非命令"))
            tags = []
            if blk["block_index"] in self.imported_blocks:
                chk.setChecked(False)        # 已入库默认不勾选（幂等提示）
                tags.append("🤖已入库")
            elif blk["kind"] == "command":
                chk.setChecked(True)         # 合并模式默认勾选命令块
            else:
                # 非命令块：不参与 commands，置灰
                chk.setEnabled(False)
                chk.setToolTip("非命令块不合并进 commands；可用下方复选追加到条目 notes")
            if blk["needs_review"]:
                tags.append("⚠待复核")
            if tags:
                chk.setText(chk.text() + "  " + "  ".join(tags))
            if blk["reasons"]:
                chk.setToolTip("；".join(blk["reasons"]))
            head.addWidget(chk)
            desc = (blk.get("desc") or "").strip()
            if desc:
                lbl = QLabel(desc[:50])
                lbl.setObjectName("HintMuted")
                head.addWidget(lbl)
            head.addStretch(1)
            row_lay.addLayout(head)
            body = QPlainTextEdit()
            body.setPlainText(blk["code"])
            body.setFont(mono_font())
            body.setMinimumHeight(46)
            body.setMaximumHeight(96)
            row_lay.addWidget(body)
            wrap = QWidget()
            wrap.setLayout(row_lay)
            host_lay.addWidget(wrap)
            self.entry_rows.append({"index": blk["block_index"], "kind": blk["kind"],
                                    "chk": chk, "body": body, "desc": desc})
        if not self.blocks:
            host_lay.addWidget(QLabel("（本条回复没有可解析的块）"))
        host_lay.addStretch(1)
        scroll.setWidget(host)
        v.addWidget(scroll, 1)

        # 合并选项 + 标题 + 预览
        self.chk_notes = QCheckBox("非命令块内容追加到条目 notes")
        self.chk_notes.setChecked(True)
        self.chk_notes.toggled.connect(self._refresh_entry_preview)
        v.addWidget(self.chk_notes)

        title_row = QHBoxLayout()
        title_row.setSpacing(SPACE_SM)
        title_row.addWidget(QLabel("条目标题"))
        self.ed_title = QLineEdit()
        self.ed_title.setText(ai_bridge.build_entry_title(
            self.ctx.get("symptom") or "",
            " ".join(x for x in (self.ctx.get("vendor") or "", self.ctx.get("os") or "") if x)))
        title_row.addWidget(self.ed_title, 1)
        self._title_row_widget = QWidget()
        self._title_row_widget.setLayout(title_row)
        v.addWidget(self._title_row_widget)

        v.addWidget(QLabel("commands 合并预览（含注释分隔行，可逐块编辑上方正文后自动更新）："))
        self.preview_commands = QPlainTextEdit()
        self.preview_commands.setReadOnly(True)
        self.preview_commands.setFont(mono_font())
        self.preview_commands.setMaximumHeight(110)
        v.addWidget(self.preview_commands)

        notes_row = QHBoxLayout()
        notes_row.addWidget(QLabel("notes"))
        self.ed_notes = QLineEdit()
        notes_row.addWidget(self.ed_notes, 1)
        self._notes_row_widget = QWidget()
        self._notes_row_widget.setLayout(notes_row)
        v.addWidget(self._notes_row_widget)
        self._refresh_entry_preview()
        return page

    def _checked_command_rows(self):
        return [r for r in self.entry_rows
                if r["kind"] == "command" and r["chk"].isChecked() and r["chk"].isEnabled()]

    def _refresh_entry_preview(self):
        merge_mode = self.rb_merge.isChecked()
        self._title_row_widget.setVisible(merge_mode)
        self.preview_commands.setVisible(merge_mode)
        self._notes_row_widget.setVisible(merge_mode)
        self.chk_notes.setVisible(merge_mode)
        if not merge_mode:
            return
        vendor_slug = dbmod.normalize_vendor(self.cmb_vendor.currentText().strip())
        pseudo = [{"kind": "command", "code": r["body"].toPlainText(), "desc": r["desc"]}
                  for r in self._checked_command_rows()]
        merged, notes = ai_bridge.merge_code_blocks(pseudo, vendor_slug)
        self.preview_commands.setPlainText(merged)
        if self.chk_notes.isChecked():
            extra = [r["body"].toPlainText() for r in self.entry_rows
                     if r["kind"] == "non_command" and r["body"].toPlainText().strip()]
            if extra:
                notes = (notes + "\n\n" + "\n\n".join(extra)).strip()
        self.ed_notes.setText(notes)

    def _submit_entry(self):
        """命令库去向提交：合并单条目 / 逐块独立，走 add_entries_from_ai 单事务"""
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库处于只读模式（U 盘写保护），无法入库。")
            return
        rows = self._checked_command_rows()
        if not rows:
            self.lbl_hint.setText("请至少勾选一个命令块。")
            return

        # 内部去重：同次提交内归一化相同的块只入库一个，后出现的标灰跳过（任务2-4）
        seen_keys, kept_rows, internal_skipped = set(), [], 0
        for r in rows:
            key = self.db.normalize_commands_text(r["body"].toPlainText())
            if key and key in seen_keys:
                internal_skipped += 1
                r["chk"].setChecked(False)
                r["chk"].setEnabled(False)
                if "内部重复" not in r["chk"].text():
                    r["chk"].setText(r["chk"].text() + "  ⓪内部重复已跳过")
                continue
            if key:
                seen_keys.add(key)
            kept_rows.append(r)
        rows = kept_rows
        if not rows:
            self.lbl_hint.setText("勾选的命令块全部为内部重复，未入库。")
            return

        vendor = dbmod.normalize_vendor(self.cmb_vendor.currentText().strip())
        os_slug = dbmod.normalize_os(self.cmb_os.currentText().strip())
        platform = self.cmb_platform.currentData()
        models = self.ed_model.text().strip()
        symptom = (self.ctx.get("symptom") or "").strip()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        note_head = ("来源：AI 会话 %s 消息#%s（模型 %s；%s%s）\n"
                     "★ 未经真机验证，禁止直接用于生产设备。"
                     % (self.session_id or "-", self.message_index, self._model, stamp,
                        ("；操作人 %s" % self._operator) if self._operator else ""))

        def _entry(commands, title, notes, block_index):
            return {
                "uuid": dbmod.new_uuid(),
                "platform": platform,
                "vendor": vendor,
                "os_family": os_slug,
                "models": models,
                "category": "AI 草稿",
                "title": (title or "AI 产出")[:60],
                "description": "AI 产出入库（未验证）",
                "commands": commands,
                "params": [],
                "notes": ((notes + "\n\n") if notes else "") + note_head,
                "verified": 0,
                "_block_index": block_index,
            }

        entries = []
        if self.rb_merge.isChecked():
            pseudo = [{"kind": "command", "code": r["body"].toPlainText(),
                       "desc": r["desc"]} for r in rows]
            merged, _ = ai_bridge.merge_code_blocks(pseudo, vendor)
            # notes 以预览面板为准（_refresh_entry_preview 已按复选框追加非命令块）
            notes = self.ed_notes.text().strip()
            title = self.ed_title.text().strip() or ai_bridge.build_entry_title(
                symptom, " ".join(x for x in (self.cmb_vendor.currentText().strip(),
                                              self.cmb_os.currentText().strip()) if x))
            entries.append(_entry(merged, title, notes,
                                  [r["index"] for r in rows]))
        else:
            for r in rows:
                entries.append(_entry(
                    r["body"].toPlainText().strip("\n"),
                    ai_bridge.build_block_title(symptom, r["desc"]),
                    "", r["index"]))

        # ---- 入库校验钩子（schema 升级 2026-09-30，任务4）----
        #   {{占位符}} 与 params 双向对账不过 / 结构化参数 description 为空 → 一律打回。
        #   与 validate_seed 共用 renderer.check_entry_params 同一口径，不搞第二套规则。
        rejected = []
        for e in entries:
            p_problems, _p_warn = renderer.check_entry_params(e)
            if p_problems:
                rejected.append((e, p_problems))
        if rejected:
            e0, ps0 = rejected[0]
            QMessageBox.warning(
                self, "入库校验未通过（已打回）",
                "《%s》参数校验不过：\n  · %s\n\n"
                "共 %d 条未通过（引用未定义 / 定义了 name-type-description 缺失 / "
                "default 违反自身取值范围）。修正后重新提交。"
                % (e0.get("title"), "\n  · ".join(ps0[:5]), len(rejected)))
            self.lbl_hint.setText("⚠ %d 条未通过参数对账校验，未入库。" % len(rejected))
            return

        # ---- 三级查重复核（提交时一次；以最高分候选的主决策为准）----
        merged_for_check = entries[0]["commands"] if self.rb_merge.isChecked() \
            else "\n".join(e["commands"] for e in entries)
        cands = self.db.find_duplicates("entry", {"commands": merged_for_check,
                                                  "vendor": vendor})
        dup_note, dup_of, dup_desc = "", "", ""
        if cands:
            c0 = cands[0]
            act = (self._dup_decisions.get("entry") or {}).get(c0["uuid"]) \
                or self._default_dup_action("entry", c0)
            if act == "skip":
                self.lbl_hint.setText("已选择跳过：与《%s》相似 %s，本次未入库。"
                                      % (c0["title"], dedupe.percent(c0["score"])))
                return
            if act == "replace":
                # 替换：整条更新现有条目（verified 强制归 0），不新增
                try:
                    changed = self.db.update_entry_commands(
                        c0["uuid"], merged_for_check,
                        {"operator": self._operator, "dup_of_uuid": c0["uuid"],
                         "message": "AI 产出替换（相似 %s）" % dedupe.percent(c0["score"])})
                except Exception as exc:
                    QMessageBox.warning(self, "替换失败", str(exc))
                    return
                if not changed:
                    self.lbl_hint.setText("内容与现有条目一致，无需替换。")
                    return
                self.db.log_ai_import(self.session_id, self.message_index, -1,
                                      "entry", c0["uuid"], self._operator, self._model,
                                      dup_of_uuid=c0["uuid"])
                self.imported = 1
                QMessageBox.information(
                    self, "已替换现有条目",
                    "《%s》commands 已更新，verified 强制归 0（原验证失效）。\n"
                    "真机执行成功后：右键条目 →「标记已验证」。"
                    % c0["title"])
                self._remember_target()
                self.accept()
                return
            # keep：仍入库（合理重复）—— notes 重复备注 + 溯源 dup_of_uuid
            dup_note = ("⚠ 查重：与《%s》相似 %s（%s），经人工确认仍入库。"
                        % (c0["title"], dedupe.percent(c0["score"]), c0["level"]))
            dup_of = c0["uuid"]
            dup_desc = "（含重复备注，dup_of=%s）" % c0["uuid"][:12]
            for e in entries:
                e["notes"] = ((e["notes"] + "\n") if e.get("notes") else "") + dup_note

        if internal_skipped:
            for e in entries:
                e["notes"] = ((e["notes"] + "\n") if e.get("notes") else "") + \
                    "本次提交内部去重：合并 %d 个内容相同的块。" % internal_skipped

        # drafts/*.nlb 留痕（沿用已验收管线）
        draft_path = DraftFromAiDialog._write_draft_nlb(
            [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries])
        try:
            uuids = self.db.add_entries_from_ai(entries, {
                "session_id": self.session_id, "message_index": self.message_index,
                "operator": self._operator, "model": self._model,
                "dup_of_uuid": dup_of})
        except Exception as exc:
            QMessageBox.warning(self, "入库失败", str(exc))
            return
        if dup_note:
            # history 显式写入重复备注（验收：85% 相似仍入库 → history 有重复备注）
            for u in uuids:
                self.db.log_history("entries", u, "update", "", dup_note, self._operator)
        self.imported = len(uuids)
        QMessageBox.information(
            self, "已入库（命令库）",
            "%s %d 条（未验证·灰徽章）%s。\n草稿留痕：%s\n真机执行成功后：右键条目 →「标记已验证」。"
            % ("合并为单条目" if self.rb_merge.isChecked() else "逐块独立", len(uuids),
               dup_desc, draft_path or "（未写出，目录只读？）"))
        self._remember_target()
        self.accept()

    # ==================================================================
    # 去向 2：报错库
    # ==================================================================
    def _build_err_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(SPACE_SM)

        tip = QLabel("把本条 AI 回复沉淀为报错映射：报错原文必填；入库后报错诊断 Tab 粘贴同类报错即可命中。"
                     "一律未验证态，命中确认后可在报错库中人工转正。")
        tip.setWordWrap(True)
        tip.setObjectName("HintText")
        v.addWidget(tip)

        v.addWidget(QLabel("报错原文（必填；已按「AI 引用的报错行 > 全部回显」预填，可修改）"))
        self.ed_raw = QPlainTextEdit()
        self.ed_raw.setMaximumHeight(90)
        echo = self.ctx.get("echo") or ""
        refs = ai_bridge.find_error_lines_in_echo(echo, self.source_text)
        prefill = "\n".join(refs) if refs else echo.strip()
        self.ed_raw.setPlainText(prefill)
        v.addWidget(self.ed_raw)

        v.addWidget(QLabel("原因分析（从 AI 回复的「原因/分析/判断依据」标题下提取，可修改）"))
        self.ed_cause = QPlainTextEdit()
        self.ed_cause.setMaximumHeight(90)
        self.ed_cause.setPlainText(ai_bridge.extract_cause_section(self.source_text))
        v.addWidget(self.ed_cause)

        v.addWidget(QLabel("修正命令（全部命令块合并，可修改）"))
        self.ed_fix = QPlainTextEdit()
        self.ed_fix.setFont(mono_font())
        self.ed_fix.setMaximumHeight(110)
        vendor_slug = dbmod.normalize_vendor(self.ctx.get("vendor") or "")
        pseudo = [{"kind": "command", "code": b["code"], "desc": b["desc"]}
                  for b in self.blocks if b["kind"] == "command"]
        merged, _ = ai_bridge.merge_code_blocks(pseudo, vendor_slug)
        self.ed_fix.setPlainText(merged)
        v.addWidget(self.ed_fix, 1)
        return page

    def _submit_err(self):
        raw_text = self.ed_raw.toPlainText().strip()
        if not raw_text:
            self.lbl_hint.setText("报错原文为空，不允许提交；请从回显中摘取或粘贴报错行。")
            return
        # ---- 三级查重复核（提交时一次；报错库默认推荐「追加原因分析」）----
        cause_text = self.ed_cause.toPlainText().strip()
        cands = self.db.find_duplicates("err", {"raw_text": raw_text})
        dup_of = ""
        if cands:
            c0 = cands[0]
            act = (self._dup_decisions.get("err") or {}).get(c0["err_id"]) \
                or self._default_dup_action("err", c0)
            if act == "skip":
                self.lbl_hint.setText("已选择跳过：与《%s》相似 %s，本次未入库。"
                                      % (c0["title"], dedupe.percent(c0["score"])))
                return
            if act == "append":
                # 追加原因分析到现有条目：不动其命令内容，不影响验证状态
                try:
                    _ = self.db.append_err_reason(
                        c0["err_id"], cause_text or ("AI 分析（相似 %s）" % dedupe.percent(c0["score"])),
                        {"operator": self._operator, "dup_of_uuid": c0["err_id"]})
                except Exception as exc:
                    QMessageBox.warning(self, "追加失败", str(exc))
                    return
                self.db.log_ai_import(self.session_id, self.message_index, -1,
                                      "err", c0["err_id"], self._operator, self._model,
                                      dup_of_uuid=c0["err_id"])
                self.imported = 1
                QMessageBox.information(
                    self, "已追加原因分析（报错库）",
                    "《%s》cause 已追加本次 AI 分析（原条目命令与验证状态不变）。\n"
                    "可到「报错诊断」Tab 粘贴同类报错验证命中。" % c0["title"])
                self._remember_target()
                self.accept()
                return
            # keep：仍入库 → 生成第二条映射，cause 带重复备注，溯源 dup_of_uuid
            dup_of = c0["err_id"]
            dup_note = ("⚠ 查重：与《%s》相似 %s（%s），经人工确认仍入库。"
                        % (c0["title"], dedupe.percent(c0["score"]), c0["level"]))
            self.ed_cause.setPlainText(
                (cause_text + "\n" + dup_note).strip())
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库处于只读模式（U 盘写保护），无法入库。")
            return
        try:
            self.db.add_err_dict_item({
                "raw_text": raw_text,
                "cause": self.ed_cause.toPlainText().strip(),
                "fix_template": self.ed_fix.toPlainText().strip(),
                "vendor": self.cmb_vendor.currentText().strip(),
                "os_family": self.cmb_os.currentText().strip(),
                "block_index": -1,
            }, {"session_id": self.session_id, "message_index": self.message_index,
                "operator": self._operator, "model": self._model,
                "dup_of_uuid": dup_of})
        except Exception as exc:
            QMessageBox.warning(self, "入库失败", str(exc))
            return
        self.imported = 1
        QMessageBox.information(self, "已入库（报错库）",
                                "报错映射已入库（未验证）。\n可到「报错诊断」Tab 粘贴同类报错验证命中。")
        self._remember_target()
        self.accept()

    # ==================================================================
    # 去向 3：排查树（线性骨架）
    # ==================================================================
    def _build_tree_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(SPACE_SM)

        # 步骤提取
        self.tree_steps, self.tree_used_markers, tree_conclusion = \
            ai_bridge.extract_steps_from_response(self.source_text)
        tip = ("步骤提取方式：%s ｜ 线性骨架：每步「正常→下一步 / 异常→叶子」，"
               "最后一步正常分支指向同一叶子。复杂分支请入库后用排查向导细调。"
               % ("已识别「步骤 N」结构" if self.tree_used_markers
                  else "未识别「步骤 N」结构，按命令块逐个建步"))
        lbl_tip = QLabel(tip)
        lbl_tip.setWordWrap(True)
        lbl_tip.setObjectName("HintMuted")
        v.addWidget(lbl_tip)

        # 可编辑步骤列表（滚动区）
        self.tree_rows = []                  # [{title, commands, observe}]
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setSpacing(SPACE_SM)
        for i, st in enumerate(self.tree_steps, 1):
            box = QVBoxLayout()
            box.setSpacing(2)
            cap = QLabel("步骤 %d" % i)
            cap.setObjectName("HintMuted")
            box.addWidget(cap)
            ed_title = QLineEdit(st.get("title") or "")
            ed_title.setPlaceholderText("步骤标题")
            box.addWidget(ed_title)
            ed_cmds = QPlainTextEdit()
            ed_cmds.setPlainText(st.get("commands") or "")
            ed_cmds.setFont(mono_font())
            ed_cmds.setMaximumHeight(84)
            ed_cmds.setPlaceholderText("本步命令（可留空 = 纯判断步骤）")
            box.addWidget(ed_cmds)
            ed_obs = QLineEdit(st.get("observe") or "")
            ed_obs.setPlaceholderText("是否符合预期？")
            box.addWidget(ed_obs)
            wrap = QWidget()
            wrap.setLayout(box)
            host_lay.addWidget(wrap)
            self.tree_rows.append({"title": ed_title, "commands": ed_cmds,
                                   "observe": ed_obs})
        if not self.tree_rows:
            host_lay.addWidget(QLabel("（本条回复没有可提取的步骤内容）"))
        host_lay.addStretch(1)
        scroll.setWidget(host)
        v.addWidget(scroll, 1)

        # 叶子结论（预填 AI 最终结论，可编辑）
        v.addWidget(QLabel("叶子结论（预填 AI 最终结论，可编辑；所有「异常」分支指向该叶子）："))
        self.ed_tree_conclusion = QPlainTextEdit()
        self.ed_tree_conclusion.setMaximumHeight(64)
        self.ed_tree_conclusion.setPlaceholderText("AI 结论未识别时请手工填写")
        self.ed_tree_conclusion.setPlainText(tree_conclusion or "")
        v.addWidget(self.ed_tree_conclusion)

        # 目标二选一：新建树 / 追加现有树
        tgt = QHBoxLayout()
        self.rb_new_tree = QRadioButton("新建树（现象取面板故障现象）")
        self.rb_new_tree.setChecked(True)
        self.rb_new_tree.toggled.connect(self._refresh_tree_target)
        self.rb_append_tree = QRadioButton("追加现有树")
        self.rb_append_tree.toggled.connect(self._refresh_tree_target)
        tgt.addWidget(self.rb_new_tree)
        tgt.addWidget(self.rb_append_tree)
        tgt.addStretch(1)
        v.addLayout(tgt)

        # 追加区（按现象模糊搜索 + 插入位置）
        self.tree_append_bar = QWidget()
        av = QVBoxLayout(self.tree_append_bar)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(SPACE_XS)
        self.ed_tree_search = QLineEdit()
        self.ed_tree_search.setPlaceholderText("按现象模糊搜索现有树…")
        self.ed_tree_search.textChanged.connect(self._refresh_append_trees)
        av.addWidget(self.ed_tree_search)
        self.cmb_append_tree = QComboBox()
        self.cmb_append_tree.currentIndexChanged.connect(self._refresh_insert_pos)
        av.addWidget(self.cmb_append_tree)
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("插入位置："))
        self.cmb_insert_pos = QComboBox()
        pos_row.addWidget(self.cmb_insert_pos, 1)
        av.addLayout(pos_row)
        self.tree_append_bar.setVisible(False)
        v.addWidget(self.tree_append_bar)
        self._refresh_append_trees()
        return page

    def _refresh_tree_target(self):
        self.tree_append_bar.setVisible(self.rb_append_tree.isChecked())
        if self.rb_append_tree.isChecked():
            self._refresh_append_trees()

    def _refresh_append_trees(self, *_):
        """按现象模糊搜索填充现有树下拉（保留选择）"""
        keyword = (self.ed_tree_search.text() or "").strip().lower()
        self.cmb_append_tree.blockSignals(True)
        self.cmb_append_tree.clear()
        self._append_candidates = []
        try:
            trees = self.db.all_trees()
        except Exception:
            trees = []
        for t in trees:
            symptom = (t.get("symptom") or "").strip()
            if keyword and not all(tok in symptom.lower() for tok in keyword.split()):
                continue
            self._append_candidates.append(t)
            self.cmb_append_tree.addItem("%s（%s · %d 步）"
                                         % (symptom or t.get("tree_id"),
                                            t.get("tree_id")[:12], len(t.get("steps") or [])))
        self.cmb_append_tree.blockSignals(False)
        self._refresh_insert_pos()

    def _refresh_insert_pos(self, *_):
        """插入位置 = 末尾 / 该树某个非叶子步骤之后"""
        self.cmb_insert_pos.clear()
        self.cmb_insert_pos.addItem("末尾（最后一步之后，原「正常」分支改指新链）", None)
        tree = self._selected_append_tree()
        if not tree:
            return
        for s in tree.get("steps") or []:
            if s.get("leafs"):
                continue
            self.cmb_insert_pos.addItem("「%s」之后" % (s.get("title") or s.get("id"))[:30],
                                        s.get("id"))

    def _selected_append_tree(self):
        idx = self.cmb_append_tree.currentIndex()
        if 0 <= idx < len(getattr(self, "_append_candidates", [])):
            return self._append_candidates[idx]
        return None

    def _submit_tree(self):
        """排查树去向提交：新建树 / 追加现有树（步骤命令先落命令库条目再 cmd_ref 引用）"""
        if self.db.readonly:
            QMessageBox.warning(self, "只读模式", "命令库处于只读模式（U 盘写保护），无法入库。")
            return
        symptom = (self.ctx.get("symptom") or "").strip()
        appending = self.rb_append_tree.isChecked()
        if not appending and not symptom:
            self.lbl_hint.setText("⚠ 排查树去向要求故障现象非空：请先在 AI Tab 上下文面板填写「故障现象」后重试。")
            return
        if appending:
            tree = self._selected_append_tree()
            if not tree:
                self.lbl_hint.setText("请先搜索并选择要追加的现有树。")
                return
            old_steps = tree.get("steps") or []
            leaf_id = next((s.get("id") for s in old_steps if s.get("leafs")), None)
            if not leaf_id:
                self.lbl_hint.setText("所选树没有叶子节点，无法按线性骨架追加。")
                return

        meta = {"session_id": self.session_id, "message_index": self.message_index,
                "operator": self._operator, "model": self._model}
        vendor_slug = dbmod.normalize_vendor(self.cmb_vendor.currentText().strip())
        os_slug = dbmod.normalize_os(self.cmb_os.currentText().strip())
        platform = self.cmb_platform.currentData()

        # 组步骤节点：每步命令先落草稿条目（verified=0），cmd_ref 引用（严禁硬编码命令）
        n = len(self.tree_rows)
        new_nodes, step_entry_count = [], 0
        for i, row in enumerate(self.tree_rows, 1):
            title = row["title"].text().strip()
            commands = row["commands"].toPlainText().strip("\n")
            observe = row["observe"].text().strip() or "是否符合预期？"
            node = {"id": "__n%d" % i, "title": title or "步骤 %d" % i,
                    "observe": observe, "explain": ""}
            if commands:
                entry_uuid = self.db.add_entry({
                    "title": ("树步骤：%s（%s）" % (title or "步骤 %d" % i,
                                                  symptom or "追加步骤"))[:60],
                    "platform": platform, "vendor": vendor_slug,
                    "os_family": os_slug, "models": self.ed_model.text().strip(),
                    "category": "AI 草稿",
                    "description": "AI 产出入库·排查树步骤（未验证）",
                    "commands": commands, "params": [],
                    "notes": "来源：AI 会话 %s 消息#%s；未经真机验证。"
                             % (self.session_id or "-", self.message_index),
                    "verified": 0,
                }, self._operator)
                self.db._insert_ai_import(self.session_id, self.message_index, -1,
                                          "entry", entry_uuid, self._operator, self._model)
                node["cmd_ref"] = {"uuid": entry_uuid}
                step_entry_count += 1
            new_nodes.append(node)

        if appending:
            # 追加：占位 id 由 append_steps 重映射；"正常"链式指向下一步，末步与
            # "异常"统一指向既有叶子（插入位置之后由 rewire 把原"正常"改指新链）
            for i, node in enumerate(new_nodes, 1):
                node["branches"] = [
                    {"when": "正常/符合预期",
                     "goto": "__n%d" % (i + 1) if i < n else leaf_id},
                    {"when": "异常/不符合预期", "goto": leaf_id}]
            after_id = self.cmb_insert_pos.currentData()
            rewire = None
            if after_id:
                rewire = (after_id, "正常")
            else:
                # 末尾追加 = 插到最后一个非叶子步骤之后（保证叶子仍是最后一个节点）
                for s in reversed(old_steps):
                    if not s.get("leafs"):
                        after_id = s.get("id")
                        rewire = (after_id, "正常")
                        break
            try:
                ids = self.db.append_steps(tree.get("tree_id"), new_nodes,
                                           after_step_id=after_id,
                                           operator=self._operator, rewire=rewire)
            except Exception as exc:
                QMessageBox.warning(self, "追加失败", str(exc))
                return
            self.db._insert_ai_import(self.session_id, self.message_index, -1,
                                      "tree", tree.get("tree_id"), self._operator, self._model)
            self.db.conn.commit()
            self.imported = step_entry_count + 1
            QMessageBox.information(
                self, "已追加步骤（排查树）",
                "向树「%s」追加 %d 步（新增命令条目 %d 条，全部未验证）。\n"
                "复杂分支可在排查向导中细调。" % (tree.get("symptom"), len(ids), step_entry_count))
            self._remember_target()
            self.accept()
            return

        # 新建树：分配真实 id（s1…sN + 叶子 sN+1），线性接线
        for i, node in enumerate(new_nodes, 1):
            node["id"] = "s%d" % i
            node["branches"] = [
                {"when": "正常/符合预期",
                 "goto": "s%d" % (i + 1) if i < n else "s%d" % (n + 1)},
                {"when": "异常/不符合预期", "goto": "s%d" % (n + 1)}]
        conclusion = self.ed_tree_conclusion.toPlainText().strip()
        leaf = {"id": "s%d" % (n + 1),
                "title": "结论：%s" % (conclusion[:40] or "排查完成"),
                "observe": "", "explain": "",
                "leafs": {"conclusion": conclusion or "按各步骤观察结果定位。",
                          "actions": []}}
        # 三级查重：与现有树的现象文本比对（提交时复核一次）
        cands = self.db.find_duplicates("tree", {"symptom": symptom})
        if cands:
            c0 = cands[0]
            listing = "\n".join("· 《%s》相似 %s（%s）"
                                % (c["title"], dedupe.percent(c["score"]), c["level"])
                                for c in cands[:5])
            if c0["level"] == "high":
                # 高度疑似 → 引导切换「追加现有树」（No = 切换；Yes = 仍新建）
                answer = QMessageBox.question(
                    self, "高度疑似已有同现象树",
                    "已有序列树与「%s」高度相似：\n%s\n\n"
                    "「Yes」= 仍新建一棵；「No」= 切换到「追加现有树」继续。"
                    % (symptom, listing),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    self.rb_append_tree.setChecked(True)
                    self._refresh_append_trees()
                    for i, t in enumerate(getattr(self, "_append_candidates", [])):
                        if t["tree_id"] == c0["tree_id"]:
                            self.cmb_append_tree.setCurrentIndex(i)
                            break
                    self.lbl_hint.setText("已切换到「追加现有树」并预选《%s》，"
                                          "请确认插入位置后提交。" % c0["title"])
                    return
            else:
                if QMessageBox.question(
                        self, "库内有相近现象树",
                        "以下树的现象与「%s」相近：\n%s\n\n"
                        "仍要新建一棵吗？（建议优先「追加现有树」）" % (symptom, listing),
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
        try:
            _ = self.db.create_trouble_tree({
                "symptom": symptom,
                "vendor_hint": [vendor_slug] if vendor_slug else [],
                "category": "AI 草稿",
                "steps": new_nodes + [leaf],
            }, meta)
        except Exception as exc:
            QMessageBox.warning(self, "入库失败", str(exc))
            return
        self.imported = step_entry_count + 1
        QMessageBox.information(
            self, "已入库（排查树）",
            "线性树「%s」已入库：%d 个步骤 + 1 个叶子（新增命令条目 %d 条，全部未验证）。\n"
            "可到「排查向导」选中该现象走树验证；复杂分支请入库后细化。"
            % (symptom, n, step_entry_count))
        self._remember_target()
        self.accept()

    # ---- 提交路由（一次操作只落一个目标）----
    def on_confirm(self):
        if self._target == "entry":
            self._submit_entry()
        elif self._target == "err":
            self._submit_err()
        elif self._target == "tree":
            self._submit_tree()


# ===========================================================================
# 历史抽屉（AI Tab 右上 [历史]）：sessions 会话库 + 旧 ai_cases 兼容浏览
# ===========================================================================
class SessionHistoryDialog(QDialog):
    """
    会话历史：
        · 会话列表按时间倒序（时间 + 现象 + 消息数），支持按现象/厂商/型号搜索
        · [打开] 完整恢复消息流与面板，可继续对话（追加保存到同一 session_id）
        · [删除] 带确认弹窗
        · 来源切换到「旧案例」→ 兼容浏览旧 ai_cases（只读回看，打开进面板+气泡）
    """

    def __init__(self, tab, parent=None):
        super(SessionHistoryDialog, self).__init__(parent)
        self.tab = tab                                  # AiTab（恢复/回看目标）
        self._rows = []                                 # [(kind, path, record)]
        self.setWindowTitle("AI 会话历史")
        self.setMinimumSize(700, 540)

        v = QVBoxLayout(self)
        v.setSpacing(SPACE_SM)

        top = QHBoxLayout()
        top.setSpacing(SPACE_SM)
        self.cmb_source = QComboBox()
        self.cmb_source.addItem("会话（新）", "session")
        self.cmb_source.addItem("旧案例（ai_cases）", "legacy")
        self.cmb_source.currentIndexChanged.connect(self.refresh)
        top.addWidget(QLabel("来源："))
        top.addWidget(self.cmb_source)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜索：现象 / 厂商 / 型号 / 消息关键字")
        self.ed_search.textChanged.connect(self._apply_filter)
        top.addWidget(self.ed_search, 1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)
        v.addLayout(top)

        self.list_items = QListWidget()
        self.list_items.itemDoubleClicked.connect(lambda _item: self.on_open())
        v.addWidget(self.list_items, 1)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("Muted")
        v.addWidget(self.lbl_count)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_open = QPushButton("打开")
        self.btn_open.setObjectName("Primary")
        self.btn_open.clicked.connect(self.on_open)
        btns.addWidget(self.btn_open)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setObjectName("Ghost")
        self.btn_delete.clicked.connect(self.on_delete)
        btns.addWidget(self.btn_delete)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.reject)
        btns.addWidget(btn_close)
        v.addLayout(btns)

        self.refresh()

    # ---- 数据 ----
    def refresh(self):
        """按来源重载列表（sessions 倒序 / ai_cases 倒序），随后应用搜索过滤"""
        self.list_items.clear()
        self._rows = []
        if self.cmb_source.currentData() == "legacy":
            for path, record in ai_bridge.list_cases():
                self._rows.append(("legacy", path, record))
                ctx = record.get("context") or {}
                title = "%s  %s" % (record.get("ts_iso") or "未知时间",
                                    (ctx.get("symptom") or "（无现象记录）")[:40])
                item = QListWidgetItem("📜 %s" % title)
                item.setData(Qt.UserRole, len(self._rows) - 1)
                item.setToolTip("旧格式案例（只读回看）：%s" % path)
                self.list_items.addItem(item)
        else:
            for path, session in ai_bridge.list_sessions():
                self._rows.append(("session", path, session))
                ctx = session.get("ctx") or {}
                created = session.get("created_at") or ""
                symptom = (ctx.get("symptom") or "（无现象记录）")[:40]
                n_msg = len(session.get("messages") or [])
                ident = " ".join(x for x in (ctx.get("vendor") or "", ctx.get("os") or "") if x)
                label = "🕓 %s  %s · %d 条消息" % (created, symptom, n_msg)
                if ident:
                    label += " ｜ %s" % ident
                # 已入库 N/M 块（按去向分列；M = 各 AI 回复的命令块总数）
                try:
                    stats = self.tab.db.ai_import_stats(session.get("session_id") or "")
                    n_blocks = sum(len(ai_bridge.extract_code_blocks(m.get("content") or ""))
                                   for m in session.get("messages") or []
                                   if m.get("role") == "assistant")
                    n_imported = sum(stats.values())
                    label += " ｜ 🤖 %d/%d 块（命令%d·报错%d·树%d）" % (
                        n_imported, n_blocks, stats.get("entry", 0),
                        stats.get("err", 0), stats.get("tree", 0))
                except Exception:
                    pass
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, len(self._rows) - 1)
                item.setToolTip("会话文件：%s（打开后可继续对话）" % path)
                self.list_items.addItem(item)
        self._apply_filter()

    def _apply_filter(self):
        """搜索过滤：现象/厂商/型号/消息文本关键字（不匹配的行隐藏）"""
        keyword = self.ed_search.text().strip().lower()
        shown = 0
        for i in range(self.list_items.count()):
            item = self.list_items.item(i)
            idx = item.data(Qt.UserRole)
            record = self._rows[idx][2] if isinstance(idx, int) and idx < len(self._rows) else {}
            ctx = record.get("ctx") or record.get("context") or {}
            hay = " ".join([
                ctx.get("symptom") or "", ctx.get("vendor") or "",
                ctx.get("os") or "", ctx.get("model_name") or "",
                record.get("response") or "",
                " ".join((m.get("content") or "") for m in record.get("messages") or []),
            ]).lower()
            item.setHidden(bool(keyword) and keyword not in hay)
            if not item.isHidden():
                shown += 1
        source = "旧案例" if self.cmb_source.currentData() == "legacy" else "会话"
        self.lbl_count.setText("共 %d 条记录（来源：%s）" % (shown, source))

    def _selected(self):
        item = self.list_items.currentItem()
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        if not isinstance(idx, int) or idx >= len(self._rows):
            return None
        return self._rows[idx]          # (kind, path, record)

    # ---- 动作 ----
    def on_open(self):
        sel = self._selected()
        if not sel:
            return
        kind, path, record = sel
        if kind == "session":
            session = ai_bridge.load_session(path)
            if not session:
                QMessageBox.warning(self, "读取失败", "会话文件损坏或被移动：\n%s" % path)
                return
            if self.tab.load_session_into_ui(session):
                self.accept()
        else:
            record = ai_bridge.load_case(path)
            if not record:
                QMessageBox.warning(self, "读取失败", "案例文件损坏或被移动：\n%s" % path)
                return
            self.tab.load_case_record(record)
            self.accept()

    def on_delete(self):
        sel = self._selected()
        if not sel:
            return
        kind, path, _record = sel
        name = os.path.basename(path)
        if QMessageBox.question(
                self, "确认删除",
                "确定删除这条%s记录？\n%s\n\n删除后不可恢复。"
                % ("会话" if kind == "session" else "旧案例", name),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        ok = ai_bridge.delete_session(path) if kind == "session" else ai_bridge.delete_case(path)
        if ok:
            self.refresh()
        else:
            QMessageBox.warning(self, "删除失败", "文件删除失败（被占用或只读）：\n%s" % path)


# ===========================================================================
# 案例历史抽屉（AI Tab 右上 [历史]）
# ===========================================================================
class CaseHistoryDialog(QDialog):
    """
    案例历史列表：
        · 按时间倒序（文件名即时间戳），显示 现象 / 设备 / 时间
        · 按现象关键字搜索（实时过滤）
        · 双击或 [打开] → 完整对话回填（上下文进左半区，原答案渲染到右半区）
        · [删除] 带确认弹窗
    """

    def __init__(self, tab, parent=None):
        super(CaseHistoryDialog, self).__init__(parent)
        self.tab = tab                                  # AiTab（回填目标）
        self._rows = []                                 # [(path, record)]
        self.setWindowTitle("AI 案例历史")
        self.setMinimumSize(680, 520)

        v = QVBoxLayout(self)
        v.setSpacing(SPACE_SM)

        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("SearchBox")
        self.ed_search.setPlaceholderText("按现象关键字搜索（实时过滤）")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self.refresh)
        v.addWidget(self.ed_search)

        self.list_cases = QListWidget()
        self.list_cases.itemDoubleClicked.connect(lambda _item: self.on_open())
        v.addWidget(self.list_cases, 1)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("Muted")
        v.addWidget(self.lbl_count)

        btns = QHBoxLayout()
        btns.setSpacing(SPACE_SM)
        btn_open = QPushButton("打开")
        btn_open.setObjectName("Primary")
        btn_open.clicked.connect(self.on_open)
        btns.addWidget(btn_open)
        btn_del = QPushButton("删除")
        btn_del.setObjectName("Danger")
        btn_del.clicked.connect(self.on_delete)
        btns.addWidget(btn_del)
        btns.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Ghost")
        btn_close.clicked.connect(self.reject)
        btns.addWidget(btn_close)
        v.addLayout(btns)

        self.refresh()

    def refresh(self):
        """按关键字过滤后重建列表（倒序）"""
        keyword = (self.ed_search.text() or "").strip().lower()
        self.list_cases.clear()
        self._rows = ai_bridge.list_cases()
        shown = 0
        for path, record in self._rows:
            ctx = record.get("context") or {}
            symptom = (ctx.get("symptom") or "(无现象记录)").strip()
            if keyword and keyword not in symptom.lower():
                continue
            device = " / ".join(x for x in (ctx.get("vendor") or "",
                                            ctx.get("model_name") or "") if x) or "-"
            when = record.get("ts_iso") or record.get("ts") or ""
            item = QListWidgetItem("%s ｜ %s\n　　%s" % (when, symptom[:44], device))
            item.setData(Qt.UserRole, path)
            item.setToolTip(symptom)
            self.list_cases.addItem(item)
            shown += 1
        total = len(self._rows)
        self.lbl_count.setText("共 %d 条案例，显示 %d 条" % (total, shown))

    def _selected_path(self):
        item = self.list_cases.currentItem()
        return item.data(Qt.UserRole) if item else None

    def on_open(self):
        """打开选中案例 → 回填 AiTab 并关抽屉"""
        path = self._selected_path()
        if not path:
            return
        record = ai_bridge.load_case(path)
        if not record:
            QMessageBox.warning(self, "读取失败", "案例文件损坏或被移动：\n%s" % path)
            self.refresh()
            return
        self.tab.load_case_record(record)
        self.accept()

    def on_delete(self):
        """删除带确认弹窗（防手滑，案例是现场积累的资产）"""
        path = self._selected_path()
        if not path:
            return
        record = ai_bridge.load_case(path) or {}
        symptom = (record.get("context") or {}).get("symptom") or path
        answer = QMessageBox.question(
            self, "确认删除",
            "确定删除这条案例吗？\n\n现象：%s\n\n删除后不可恢复。" % symptom[:60],
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if ai_bridge.delete_case(path):
            self.refresh()
        else:
            QMessageBox.warning(self, "删除失败", "文件可能被占用或介质只读。")


# ===========================================================================
# 转为草稿条目（防幻觉污染库）
# ===========================================================================
# 网络设备形态 / Linux 形态（与库内既有取值保持一致，见 db.DEMO_ENTRIES）
NET_DEVICE_TYPES = ["交换机", "路由器", "防火墙", "无线AC"]
LINUX_DEVICE_TYPES = ["服务器"]

# 生效方式（Linux 条目必填；网络设备条目一律留空，与种子库规则一致）
DURATION_CHOICES = [("不指定", ""), ("临时生效（重启失效）", "temp"),
                    ("持久化配置", "perm"), ("临时 + 持久化", "both")]


class DraftFromAiDialog(QDialog):
    """
    把 AI 响应中的命令块转成草稿条目。

    三道防幻觉闸门（对应任务6）：
        1. 逐个列出 + 默认不勾选 + 正文/标题可编辑，绝不自动入库
        2. 生成 drafts/draft_*.nlb（verified 强制 0）
        3. 走 db.import_entries 管线入库 → 徽章灰；真机验证后走既有右键标记流程转绿
    """

    def __init__(self, tab, db, parent=None):
        super(DraftFromAiDialog, self).__init__(parent)
        self.tab = tab
        self.db = db
        self.imported = 0
        self.blocks = ai_bridge.extract_code_blocks(tab._response_text)
        self._rows = []              # [(QCheckBox, QLineEdit 标题, QPlainTextEdit 正文)]
        self.setWindowTitle("转为草稿条目 · 勾选后入库（未验证）")
        self.setMinimumSize(820, 640)

        v = QVBoxLayout(self)
        v.setSpacing(SPACE_SM)

        tip = QLabel("AI 生成的命令一律按【未验证·灰徽章】入库：命令块默认不勾选，可编辑后再确认；"
                     "入库后请先在真机执行，成功后再右键条目 →「标记已验证」才会变绿。")
        tip.setWordWrap(True)
        tip.setObjectName("HintText")
        v.addWidget(tip)

        # ---- 公共元数据（整批生效，取自 AI Tab 当前上下文，可改）----
        ctx = tab._last_context or {}
        self.cmb_vendor = QComboBox()
        self.cmb_vendor.setEditable(True)
        self.cmb_vendor.addItem("")
        for name in dbmod.vendor_candidates():
            self.cmb_vendor.addItem(name)
        self.cmb_vendor.setCurrentText(ctx.get("vendor") or "")

        self.cmb_os = QComboBox()
        self.cmb_os.setEditable(True)
        self.cmb_os.addItem("")
        for name in dbmod.os_candidates():
            self.cmb_os.addItem(name)
        self.cmb_os.setCurrentText(ctx.get("os") or "")

        self.ed_model = QLineEdit(ctx.get("model_name") or "")

        self.cmb_platform = QComboBox()
        self.cmb_platform.addItem("网络设备", "network")
        self.cmb_platform.addItem("Linux 服务器", "linux")
        self.cmb_device = QComboBox()
        self.cmb_device.setEditable(True)
        self.cmb_cat = QComboBox()
        self.cmb_cat.setEditable(True)
        for name in dbmod.ALL_CATEGORIES:
            self.cmb_cat.addItem(name)
        self.cmb_duration = QComboBox()
        for label, value in DURATION_CHOICES:
            self.cmb_duration.addItem(label, value)

        # 依据 OS 初判平台（用库里过滤器同一套判定，避免两处规则漂移）
        self._auto_platform = self._guess_platform(ctx.get("os") or "")
        self.cmb_platform.setCurrentIndex(0 if self._auto_platform == "network" else 1)
        self.cmb_platform.currentIndexChanged.connect(self._on_platform_changed)
        self._on_platform_changed()

        form = QHBoxLayout()
        form.setSpacing(SPACE_SM)
        for label, widget, stretch in (("厂商", self.cmb_vendor, 1), ("OS 版本", self.cmb_os, 1),
                                       ("型号", self.ed_model, 1), ("平台", self.cmb_platform, 1)):
            col = QVBoxLayout()
            col.setSpacing(2)
            cap = QLabel(label)
            cap.setObjectName("Muted")
            col.addWidget(cap)
            col.addWidget(widget)
            form.addLayout(col, stretch)
        v.addLayout(form)

        form2 = QHBoxLayout()
        form2.setSpacing(SPACE_SM)
        for label, widget, stretch in (("设备形态", self.cmb_device, 1), ("场景分类", self.cmb_cat, 1),
                                       ("生效方式（Linux）", self.cmb_duration, 1)):
            col = QVBoxLayout()
            col.setSpacing(2)
            cap = QLabel(label)
            cap.setObjectName("Muted")
            col.addWidget(cap)
            col.addWidget(widget)
            form2.addLayout(col, stretch)
        v.addLayout(form2)

        # ---- 命令块列表（可滚动，逐块勾选 + 编辑）----
        head = QHBoxLayout()
        head.addWidget(QLabel("解析出的命令块（默认不勾选，勾选后才入库）："))
        head.addStretch(1)
        btn_all = QPushButton("全选")
        btn_all.setObjectName("Subtle")
        btn_all.clicked.connect(lambda: self._set_all(True))
        head.addWidget(btn_all)
        btn_none = QPushButton("全不选")
        btn_none.setObjectName("Subtle")
        btn_none.clicked.connect(lambda: self._set_all(False))
        head.addWidget(btn_none)
        v.addLayout(head)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        host = QWidget()
        self.rows_layout = QVBoxLayout(host)
        self.rows_layout.setContentsMargins(2, 2, 2, 2)
        self.rows_layout.setSpacing(SPACE_SM)
        self._build_rows()
        self.rows_layout.addStretch(1)
        self.area.setWidget(host)
        v.addWidget(self.area, 1)

        # ---- 底部按钮 ----
        btns = QHBoxLayout()
        btns.setSpacing(SPACE_SM)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("Muted")
        btns.addWidget(self.lbl_hint, 1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("Ghost")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        self.btn_confirm = QPushButton("生成草稿并入库")
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.clicked.connect(self.on_confirm)
        btns.addWidget(self.btn_confirm)
        v.addLayout(btns)

    # ---- 平台联动（设备形态 / 场景 / 生效方式）----
    @staticmethod
    def _guess_platform(os_display):
        """按 OS 显示名猜平台：走 db 的 Linux 判定（库内过滤器同源）"""
        slug = dbmod.normalize_os(os_display)
        try:
            return "linux" if dbmod._is_linux_os(slug) else "network"
        except Exception:
            return "network"

    def _on_platform_changed(self):
        platform = self.cmb_platform.currentData()
        is_linux = platform == "linux"
        self.cmb_device.clear()
        for name in (LINUX_DEVICE_TYPES if is_linux else NET_DEVICE_TYPES):
            self.cmb_device.addItem(name)
        if self.cmb_cat.currentText() not in dbmod.ALL_CATEGORIES:
            self.cmb_cat.setCurrentText("排查命令" if is_linux else "接口诊断")
        self.cmb_duration.setEnabled(is_linux)
        self.cmb_duration.setCurrentIndex(
            [v for _, v in DURATION_CHOICES].index("both") if is_linux else 0)

    def _build_rows(self):
        """逐个命令块生成一行：勾选框 + 标题 + 可编辑正文"""
        for i, (code, desc) in enumerate(self.blocks, 1):
            row = QFrame()
            row.setObjectName("InfoCard")
            rv = QVBoxLayout(row)
            rv.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
            rv.setSpacing(SPACE_XS)

            top = QHBoxLayout()
            top.setSpacing(SPACE_SM)
            chk = QCheckBox("第 %d 块（%d 行）" % (i, len(code.splitlines())))
            chk.setChecked(False)                     # ★ 默认不勾选：绝不自动入库
            top.addWidget(chk)
            top.addWidget(QLabel("标题："))
            title = QLineEdit(self._default_title(desc, i))
            title.setPlaceholderText("入库后的条目标题")
            top.addWidget(title, 1)
            rv.addLayout(top)

            if desc:
                memo = QLabel("AI 原文说明：%s" % desc)
                memo.setObjectName("Muted")
                memo.setWordWrap(True)
                rv.addWidget(memo)

            body = QPlainTextEdit(code)
            body.setFont(mono_font())
            body.setMinimumHeight(90)
            body.setToolTip("入库前可自由编辑（删除不适用的行、补全占位符等）")
            rv.addWidget(body)

            self._rows.append((chk, title, body))
            self.rows_layout.addWidget(row)

    @staticmethod
    def _default_title(desc, index):
        """默认标题：取 AI 说明去掉 Markdown 装饰，兜底「AI 草稿 N」"""
        text = re.sub(r"^[\s#>•\-*\d.、)）]+", "", (desc or "").strip())
        text = text.replace("**", "").replace("`", "").strip()
        return text[:40] or ("AI 草稿 %d" % index)

    def _set_all(self, checked):
        for chk, _title, _body in self._rows:
            chk.setChecked(checked)

    def selected_entries(self):
        """把勾选且正文非空的命令块转成待入库条目（verified 强制 0）"""
        platform = self.cmb_platform.currentData()
        vendor = dbmod.normalize_vendor(self.cmb_vendor.currentText().strip())
        os_slug = dbmod.normalize_os(self.cmb_os.currentText().strip())
        model = self.ed_model.text().strip()
        device = self.cmb_device.currentText().strip()
        category = self.cmb_cat.currentText().strip() or "接口诊断"
        duration = self.cmb_duration.currentData() if platform == "linux" else ""
        operator = (ai_bridge.load_config().get("operator") or "").strip()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        entries = []
        for chk, title, body in self._rows:
            if not chk.isChecked():
                continue
            commands = body.toPlainText().strip("\n")
            if not commands.strip():
                continue
            entries.append({
                "uuid": dbmod.new_uuid(),
                "platform": platform,
                "device_type": device,
                "vendor": vendor,
                "os_family": os_slug,
                "models": model,
                "category": category,
                "title": (title.text().strip() or "AI 草稿")[:60],
                "description": "AI 诊断草稿（未验证）",
                "commands": commands,
                "params": [],
                "notes": ("来源：AI 诊断（模型 %s；%s%s）。\n"
                          "★ 未经真机验证，禁止直接用于生产设备；"
                          "真机执行成功后请右键条目 →「标记已验证」。"
                          % ((ai_bridge.load_config().get("model") or "?"), stamp,
                             ("；操作人 %s" % operator) if operator else "")),
                "duration": duration,
                "verified": 0,                    # ★ 强制 0
                "favorite": 0,
            })
        return entries

    def on_confirm(self):
        entries = self.selected_entries()
        if not entries:
            self.lbl_hint.setText("请至少勾选一个命令块（正文不能为空）。")
            return
        # ① 生成 draft_*.nlb（留痕，可整包拷给同事）
        draft_path = self._write_draft_nlb(entries)
        # ② 走现有 import_entries 管线入库（内部同样会强制 verified=0）
        operator = (ai_bridge.load_config().get("operator") or "ai-draft").strip()
        try:
            added, updated, skipped = self.db.import_entries(entries, merge=True,
                                                            operator=operator)
        except Exception as exc:
            QMessageBox.warning(self, "入库失败", "写入命令库失败：%s" % exc)
            return
        self.imported = added
        QMessageBox.information(
            self, "已入库草稿条目",
            "新增 %d 条（更新 %d / 跳过 %d）。\n\n"
            "· 徽章为灰色「未验证」——AI 生成内容绝不自动转正\n"
            "· 草稿留痕：%s\n"
            "· 真机执行成功后：右键条目 →「标记已验证」→ 徽章变绿"
            % (added, updated, skipped, draft_path or "（未写出，目录只读？）"))
        self.accept()

    @staticmethod
    def _write_draft_nlb(entries):
        """生成 drafts/draft_YYYYmmdd_HHMMSS.nlb（与命令库同格式，可直接导入其它机器）"""
        import json
        import os
        folder = os.path.join(ai_bridge._base_dir(), "drafts")
        name = "draft_%s.nlb" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, name)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"format": dbmod.NLB_FORMAT, "schema": dbmod.SCHEMA_VERSION,
                           "exported_at": dbmod.now_str(), "kind": "ai_draft",
                           "count": len(entries), "entries": entries},
                          fp, ensure_ascii=False, indent=2)
            return path
        except Exception:
            return ""
