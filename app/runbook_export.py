# -*- coding: utf-8 -*-
"""
runbook_export.py —— 操作单（Runbook）数据模型 + 单文件 HTML 导出器

数据模型（schema 1，落盘 JSON，运行数据不入库不进 git——AGENTS.md §8 口径）：
    {
      "schema": 1,
      "meta": {title, device_model, vendor, os_family, window_start, window_end,
               operator, reviewer, rollback_point, created_at, updated_at},
      "steps": [{step_no, entry_uuid, title_snapshot, vendor_snapshot,
                 params, expect, note, checked}]
    }

设计裁决（2026-10-12 用户确认）：
    ① rollback 不落盘：渲染/导出时按 entry_uuid 实时从库取（库为权威，防漂移）；
      条目缺失 → 步骤标失效警告，title_snapshot 兜底可读；
    ② 变更类不落盘：renderer.classify_entry 实时判定（唯一执法点，不另写判定表）；
    ③ 导出配色：浅色打印主题，色值从 theme.py 常量派生，verify_print_tokens_against_theme()
      与 theme.py 源码逐字比对（防两套色，模式同 site_export.py）；
    ④ 零外部请求：无 <script>、无外链、无 @import，纯静态 HTML + 系统字体。

硬性约束：
    · load_runbook 先 os.path.isfile 防御（sqlite3.connect 空库事件的同款教训）；
    · 导出产物不含主库脱敏问题——操作单参数值本就是现场真实值，属运行数据，
      存放 runbooks/ 永不入库、永不进交付 zip（dist_package 核查清单）。
"""

from __future__ import print_function

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import renderer  # noqa: E402

SCHEMA_VERSION = 1

META_FIELDS = (
    "title", "device_model", "vendor", "os_family",
    "window_start", "window_end", "operator", "reviewer",
    "rollback_point",
)
STEP_FIELDS = ("step_no", "entry_uuid", "title_snapshot", "vendor_snapshot",
               "params", "expect", "note", "checked")


# ---------------------------------------------------------------------------
# 一、数据模型
# ---------------------------------------------------------------------------

def new_runbook():
    """空操作单：meta 九字段 + 空 steps"""
    meta = dict((f, "") for f in META_FIELDS)
    meta["created_at"] = ""
    meta["updated_at"] = ""
    return {"schema": SCHEMA_VERSION, "meta": meta, "steps": []}


def normalize_runbook(data):
    """把外部 JSON 规整成合法操作单（坏值兜底，不抛异常——离网现场不因状态文件拖死）"""
    out = new_runbook()
    if not isinstance(data, dict):
        return out
    try:
        out["schema"] = int(data.get("schema") or SCHEMA_VERSION)
    except (TypeError, ValueError):
        out["schema"] = SCHEMA_VERSION
    meta = data.get("meta") or {}
    if isinstance(meta, dict):
        for f in META_FIELDS:
            out["meta"][f] = str(meta.get(f) or "")
        out["meta"]["created_at"] = str(meta.get("created_at") or "")
        out["meta"]["updated_at"] = str(meta.get("updated_at") or "")
    steps = data.get("steps")
    if isinstance(steps, list):
        for i, raw in enumerate(steps):
            if not isinstance(raw, dict):
                continue
            # 缺 entry_uuid 的步骤不可用（无法回库对账），normalize 直接丢弃
            if not str(raw.get("entry_uuid") or "").strip():
                continue
            step = {}
            for f in STEP_FIELDS:
                step[f] = raw.get(f, "" if f != "checked" else False)
            step["step_no"] = i + 1
            if not isinstance(step["params"], dict):
                step["params"] = {}
            step["checked"] = bool(step["checked"])
            out["steps"].append(step)
    return out


def validate_runbook(data):
    """结构校验，返回问题列表（空 = 合法）。只查硬伤，不查业务语义"""
    issues = []
    if not isinstance(data, dict):
        return ["顶层不是 JSON 对象"]
    if int(data.get("schema") or 0) != SCHEMA_VERSION:
        issues.append("schema 版本不是 %d（收到 %r）" % (SCHEMA_VERSION, data.get("schema")))
    if not isinstance(data.get("meta"), dict):
        issues.append("meta 缺失或不是对象")
    steps = data.get("steps")
    if not isinstance(steps, list):
        issues.append("steps 缺失或不是数组")
    else:
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                issues.append("第 %d 步不是对象" % (i + 1))
                continue
            if not str(s.get("entry_uuid") or "").strip():
                issues.append("第 %d 步缺 entry_uuid" % (i + 1))
            if not isinstance(s.get("params") or {}, dict):
                issues.append("第 %d 步 params 不是对象" % (i + 1))
    return issues


def load_runbook(path):
    """读操作单 JSON，返回 (normalize 后的 data, 错误信息)。错误信息空串 = 成功"""
    if not os.path.isfile(path):
        return None, "文件不存在：%s" % path
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except (ValueError, OSError) as exc:
        return None, "读取失败：%s" % exc
    data = normalize_runbook(raw)
    issues = validate_runbook(data)
    if issues:
        return data, "结构问题（已按兜底值载入）：" + "；".join(issues)
    return data, ""


def save_runbook(data, path):
    """写操作单 JSON（utf-8）。返回错误信息，空串 = 成功"""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        return ""
    except OSError as exc:
        return "保存失败：%s" % exc


# ---------------------------------------------------------------------------
# 二、打印配色 token（浅色，从 theme.py 派生 + 同源校验）
# ---------------------------------------------------------------------------
# 每个打印 token 记录其 theme.py 来源常量与"派生时刻"的源值：
# verify_print_tokens_against_theme() 逐字比对 theme.py —— theme.py 改色即漂移报错，
# 强制重推打印色（模式同 site_export.verify_tokens_against_theme，不做第二套色）。
# 打印值 = 同色相加深/转中性，满足白底 A4 对比度（WCAG AA 正文 ≥4.5:1）。

PRINT_TOKENS = {
    "paper":    {"src": "BG_WINDOW",      "src_value": "#1b1d23", "print": "#ffffff",
                 "note": "纸面：深底反转为白"},
    "panel":    {"src": "BG_PANEL",       "src_value": "#232530", "print": "#f5f6f8",
                 "note": "信息条浅灰"},
    "code_bg":  {"src": "CODE_BG",        "src_value": "#16181d", "print": "#f2f3f6",
                 "note": "命令块浅底"},
    "border":   {"src": "BORDER",         "src_value": "#333745", "print": "#c9ced8",
                 "note": "边框转浅灰"},
    "accent":   {"src": "ACCENT",         "src_value": "#4f8cff", "print": "#2b62d9",
                 "note": "主色同相加深"},
    "success":  {"src": "SUCCESS",        "src_value": "#3fbf6f", "print": "#1f7a45",
                 "note": "绿同相加深"},
    "warning":  {"src": "WARNING",        "src_value": "#e0a83c", "print": "#9a6b15",
                 "note": "黄同相加深"},
    "danger":   {"src": "DANGER",         "src_value": "#e05656", "print": "#b23434",
                 "note": "红同相加深"},
    "text":     {"src": "TEXT_PRIMARY",   "src_value": "#e8eaf0", "print": "#1d2027",
                 "note": "正文转深墨"},
    "text2":    {"src": "TEXT_SECONDARY", "src_value": "#9aa0b0", "print": "#565d6d",
                 "note": "次级文字转中灰"},
    "text3":    {"src": "TEXT_MUTED",     "src_value": "#6a7080", "print": "#6a7080",
                 "note": "灰阶中性，白底对比 4.6:1 直接沿用"},
}


def verify_print_tokens_against_theme():
    """打印 token 同源校验：theme.py 常量逐字比对，漂移列表（空 = 通过）"""
    theme_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme.py")
    try:
        src = open(theme_py, encoding="utf-8").read()
    except OSError as exc:
        return ["theme.py 读取失败：%s" % exc]
    drift = []
    for key, spec in PRINT_TOKENS.items():
        m = re.search(r"^%s\s*=\s*\"(#[0-9a-fA-F]{6})\"" % spec["src"], src, re.M)
        if not m:
            drift.append("%s 在 theme.py 找不到定义" % spec["src"])
        elif m.group(1).lower() != spec["src_value"].lower():
            drift.append("%s: theme=%s 派生基线=%s（打印色需重推）"
                         % (spec["src"], m.group(1), spec["src_value"]))
    return drift


# ---------------------------------------------------------------------------
# 三、步骤解析（rollback 实时取库 + 变更类实时判定）
# ---------------------------------------------------------------------------

def resolve_steps(data, db):
    """
    把操作单步骤对账到当前命令库，返回逐步解析结果列表：
        status          ok（条目在库）/ missing（uuid 失效）
        category        change / query / None（missing 或无库）
        rendered        参数填入后的命令全文（missing 为空串）
        missing_params  渲染时缺值的占位名列表
        rollback        变更类条目的回退文本（实时取库；查询类/missing 为空串）
        missing_rollback 变更类且 rollback 为空（正常不应出现，UI 标红）
    """
    results = []
    for i, step in enumerate(data.get("steps") or []):
        entry = None
        if db is not None:
            try:
                entry = db.get_entry(step.get("entry_uuid"))
            except Exception:
                entry = None
        res = {
            "index": i + 1,
            "step": step,
            "entry": entry,
            "status": "ok" if entry else "missing",
            "category": None,
            "rendered": "",
            "missing_params": [],
            "rollback": "",
            "missing_rollback": False,
        }
        if entry:
            try:
                res["category"] = renderer.classify_entry(entry)
            except Exception:
                res["category"] = "change"      # fail-safe：分不清就当变更类
            try:
                text, missing, _specs = renderer.render_entry(entry, step.get("params") or {})
                res["rendered"] = text
                res["missing_params"] = missing
            except Exception:
                res["rendered"] = entry.get("commands") or ""
                res["missing_params"] = []
            if res["category"] == "change":
                res["rollback"] = str(entry.get("rollback") or "")
                res["missing_rollback"] = not res["rollback"].strip()
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# 四、HTML 导出（单文件 / A4 打印 / 勾选框 / 回退汇总页 / 复核页）
# ---------------------------------------------------------------------------

def _esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\"", "&quot;"))


def _code_html(text):
    """命令块：转义 + 注释行灰化（与 renderer / 站点导出器同规则：#/! 开头独立行）"""
    out = []
    for line in str(text or "").split("\n"):
        t = _esc(line)
        if re.match(r"^\s*[#!]", line):
            out.append('<span class="cm">%s</span>' % t)
        else:
            out.append(t)
    return "\n".join(out)


def _meta_rows(data):
    meta = data.get("meta") or {}
    rows = [
        ("设备型号", meta.get("device_model")),
        ("厂商 / OS", " / ".join([x for x in (meta.get("vendor"), meta.get("os_family")) if x])),
        ("维护窗口", " ~ ".join([x for x in (meta.get("window_start"), meta.get("window_end")) if x])
         or ""),
        ("执行人", meta.get("operator")),
        ("复核人", meta.get("reviewer")),
        ("回退点", meta.get("rollback_point")),
    ]
    return [(k, v) for k, v in rows if str(v or "").strip()]


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 变更窗口操作单</title>
<style>
/* 打印 token：由 runbook_export.PRINT_TOKENS 注入（从 app/theme.py 派生，导出时同源校验） */
__CSS_TOKENS__
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--paper);color:var(--text);
font:13px/1.65 "Microsoft YaHei","Segoe UI",-apple-system,sans-serif}
.sheet{max-width:186mm;margin:0 auto;padding:12mm 10mm}
h1{font-size:19px;margin:0 0 2mm}
.subtitle{color:var(--text3);font-size:11px;margin-bottom:5mm}
.meta{width:100%;border-collapse:collapse;margin-bottom:5mm;font-size:12px}
.meta th,.meta td{border:1px solid var(--border);padding:4px 8px;text-align:left;vertical-align:top}
.meta th{background:var(--panel);color:var(--text2);white-space:nowrap;width:22mm;font-weight:600}
.notice{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--accent);
border-radius:4px;padding:3mm 4mm;font-size:11.5px;color:var(--text2);margin-bottom:6mm}
h2.sec{font-size:14px;border-bottom:2px solid var(--accent);padding-bottom:1mm;margin:7mm 0 3mm}
.step{border:1px solid var(--border);border-radius:6px;padding:3mm 4mm;margin-bottom:4mm;
page-break-inside:avoid;break-inside:avoid}
.step.missing{border-left:3px solid var(--warning)}
.step .head{display:flex;align-items:baseline;gap:2mm;flex-wrap:wrap;margin-bottom:2mm}
.step .no{font-weight:700;color:var(--accent);font-size:13px}
.step .title{font-weight:700;font-size:13px}
.badge{display:inline-block;padding:0 6px;border-radius:3px;font-size:10px;line-height:16px;vertical-align:1px}
.b-change{background:var(--danger-14);color:var(--danger)}
.b-query{background:var(--accent-14);color:var(--accent)}
.b-dead{background:var(--panel);color:var(--text3);border:1px dashed var(--border)}
.b-warn{background:var(--warning-14);color:var(--warning)}
.cb{display:inline-block;width:11px;height:11px;border:1.5px solid var(--text2);
border-radius:2px;margin-right:2mm;vertical-align:-1px;flex-shrink:0}
.ptable{width:100%;border-collapse:collapse;font-size:11px;margin:1.5mm 0}
.ptable th,.ptable td{border:1px solid var(--border);padding:2px 6px;text-align:left}
.ptable th{background:var(--panel);color:var(--text2);font-weight:600;width:30mm}
pre.code{background:var(--code_bg);border:1px solid var(--border);border-radius:4px;
padding:2.5mm 3mm;white-space:pre-wrap;word-break:break-all;margin:1.5mm 0;
font:11px/1.55 Consolas,"Courier New",monospace}
pre.code .cm{color:var(--text3);font-style:italic}
.expect{font-size:12px;margin:1.5mm 0 0}
.expect .lbl,.note .lbl{color:var(--text2);font-weight:600;font-size:11px}
.note{font-size:11.5px;color:var(--text2);margin:1mm 0 0}
.miss{color:var(--warning);font-size:11px;margin:1mm 0 0}
.rbmiss{color:var(--danger);font-size:11.5px;font-weight:600;margin:1.5mm 0 0}
/* 回退汇总页 */
.rb-page{page-break-before:always;break-before:page}
.rb{border:1px solid var(--border);border-left:3px solid var(--danger);border-radius:6px;
padding:3mm 4mm;margin-bottom:4mm;page-break-inside:avoid;break-inside:avoid}
/* 复核页 */
.sign{display:flex;gap:8mm;margin-top:10mm;flex-wrap:wrap}
.sign .box{flex:1;min-width:50mm;border:1px solid var(--border);border-radius:6px;padding:4mm}
.sign .box .line{margin-top:10mm;border-bottom:1px solid var(--text3);height:6mm}
.checkline{margin:2mm 0;font-size:12.5px}
@page{size:A4;margin:14mm 12mm}
@media print{
  body{background:#fff}
  .sheet{max-width:none;padding:0}
  .noprint{display:none}
}
@media screen{
  body{background:#eceef1}
  .sheet{background:var(--paper);box-shadow:0 1px 6px rgba(0,0,0,.12);margin:8mm auto}
}
</style>
</head>
<body>
<div class="sheet">
<h1>__TITLE__</h1>
<div class="subtitle">变更窗口操作单 · 由 NetToolBox 导出（schema __SCHEMA__ · __GENERATED__） · 命令在 Xshell/SecureCRT 中执行</div>
__META_TABLE__
<div class="notice"><b>执行须知：</b>按步骤顺序执行，每步完成后勾选左侧勾选框；
「预期回显」与实际不符时立即停止并执行回退页对应步骤；变更类步骤均附回退方案（见回退汇总页）。</div>
<h2 class="sec">命令序列</h2>
__STEPS__
<h2 class="sec rb-page">回退方案汇总（变更类步骤）</h2>
<div class="notice"><b>回退点：</b>__ROLLBACK_POINT__</div>
__ROLLBACKS__
<h2 class="sec rb-page">复核确认</h2>
<div class="checkline"><span class="cb"></span>变更窗口内执行，未超窗口</div>
<div class="checkline"><span class="cb"></span>回退点已备份并确认可恢复</div>
<div class="checkline"><span class="cb"></span>全部步骤「预期回显」与实际一致</div>
<div class="checkline"><span class="cb"></span>变更结果已通知相关方</div>
<div class="checkline"><span class="cb"></span>异常已按回退页处置 / 已记录</div>
<div class="sign">
  <div class="box">执行人<div class="line"></div>日期：____ / ____ / ____</div>
  <div class="box">复核人<div class="line"></div>日期：____ / ____ / ____</div>
</div>
</div>
</body>
</html>
"""


def build_html(data, db=None):
    """渲染单文件 HTML（不含写盘）。data 需先 normalize_runbook"""
    data = normalize_runbook(data)
    resolved = resolve_steps(data, db)
    meta = data.get("meta") or {}

    # ---- meta 表 ----
    rows = _meta_rows(data)
    meta_table = ""
    if rows:
        meta_table = '<table class="meta">%s</table>' % "".join(
            "<tr><th>%s</th><td>%s</td></tr>" % (_esc(k), _esc(v)) for k, v in rows)

    # ---- 步骤卡 ----
    step_cards = []
    for r in resolved:
        step = r["step"]
        cls = "step missing" if (r["status"] == "missing" or r["missing_rollback"]) else "step"
        if r["status"] == "missing":
            badge = '<span class="badge b-dead">条目失效</span>'
        elif r["category"] == "change":
            badge = '<span class="badge b-change">变更类</span>'
        else:
            badge = '<span class="badge b-query">查询类</span>'
        head = ('<div class="head"><span class="cb"></span>'
                '<span class="no">第 %d 步</span><span class="title">%s</span>%s</div>'
                % (r["index"], _esc(step.get("title_snapshot") or "(无标题)"), badge))
        body = [head]
        if r["status"] == "missing":
            body.append('<div class="miss">⚠ 条目已不在当前命令库中（uuid %s），'
                        '命令以快照标题为准，请人工核对或重建操作单。</div>'
                        % _esc(step.get("entry_uuid")))
        else:
            params = step.get("params") or {}
            if params:
                body.append('<table class="params ptable"><tr><th>参数</th><th>填入值</th></tr>%s</table>'
                            % "".join("<tr><th>%s</th><td>%s</td></tr>" % (_esc(k), _esc(v))
                                      for k, v in params.items()))
            body.append('<pre class="code">%s</pre>' % _code_html(r["rendered"]))
            if r["missing_params"]:
                body.append('<div class="miss">⚠ 未填参数：%s（命令中保留占位）</div>'
                            % _esc(", ".join(r["missing_params"])))
        if str(step.get("expect") or "").strip():
            body.append('<div class="expect"><span class="lbl">预期回显要点：</span>%s</div>'
                        % _esc(step.get("expect")))
        if str(step.get("note") or "").strip():
            body.append('<div class="note"><span class="lbl">备注：</span>%s</div>'
                        % _esc(step.get("note")))
        if r["missing_rollback"]:
            body.append('<div class="rbmiss">⚠⚠ 变更类步骤缺少回退方案——正常不应出现，'
                        '请先在命令库补齐 rollback 再执行本操作单。</div>')
        step_cards.append('<div class="%s">%s</div>' % (cls, "".join(body)))
    if not step_cards:
        step_cards.append('<div class="step"><div class="head"><span class="title">'
                          '（尚无步骤——在 NetToolBox「操作单」Tab 添加）</span></div></div>')

    # ---- 回退汇总页 ----
    rb_cards = []
    for r in resolved:
        if r["category"] != "change":
            continue
        inner = ['<div class="head"><span class="cb"></span>'
                 '<span class="no">第 %d 步</span><span class="title">%s</span></div>'
                 % (r["index"], _esc(r["step"].get("title_snapshot") or "(无标题)"))]
        if r["rollback"]:
            inner.append('<pre class="code">%s</pre>' % _code_html(r["rollback"]))
        else:
            inner.append('<div class="rbmiss">⚠ 该步骤缺少回退方案（见命令序列页红字告警）。</div>')
        rb_cards.append('<div class="rb">%s</div>' % "".join(inner))
    if not rb_cards:
        rb_cards.append('<div class="rb"><div class="head"><span class="title">'
                        '本操作单不含变更类步骤，无回退需求。</span></div></div>')

    import datetime
    html = (_TEMPLATE
            .replace("__TITLE__", _esc(meta.get("title") or "未命名操作单"))
            .replace("__SCHEMA__", str(data.get("schema")))
            .replace("__GENERATED__", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__META_TABLE__", meta_table)
            .replace("__ROLLBACK_POINT__", _esc(meta.get("rollback_point") or "（未填写）"))
            .replace("__STEPS__", "\n".join(step_cards))
            .replace("__ROLLBACKS__", "\n".join(rb_cards))
            .replace("__CSS_TOKENS__", _css_vars()))
    return html


def _css_vars():
    parts = [":root{"]
    for key, spec in PRINT_TOKENS.items():
        parts.append("--%s:%s;" % (key, spec["print"]))
    parts.append("--danger-14:%s;--accent-14:%s;--warning-14:%s;}" % (
        _hex14(PRINT_TOKENS["danger"]["print"]),
        _hex14(PRINT_TOKENS["accent"]["print"]),
        _hex14(PRINT_TOKENS["warning"]["print"])))
    return "".join(parts)


def _hex14(hex6):
    """#rrggbb → 14% 透明度 rgba（浅色底上的徽章底）"""
    h = hex6.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "rgba(%d,%d,%d,0.14)" % (r, g, b)


def export_runbook(data, db, out_path):
    """
    导出主流程：token 同源校验 → 渲染 → 落盘。
    返回 (rc, message)：rc=0 成功；rc=6 token 漂移拒绝导出；rc=8 写盘失败。
    """
    drift = verify_print_tokens_against_theme()
    if drift:
        return 6, "打印 token 与 theme.py 漂移，拒绝导出（不做第二套色）：\n" + "\n".join(
            "  - " + d for d in drift)
    try:
        html = build_html(data, db)
    except Exception as exc:
        return 8, "渲染失败：%s" % exc
    try:
        parent = os.path.dirname(os.path.abspath(out_path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(html)
    except OSError as exc:
        return 8, "写盘失败：%s" % exc
    return 0, "%s（%.1f KB）" % (os.path.abspath(out_path), len(html.encode("utf-8")) / 1024.0)
