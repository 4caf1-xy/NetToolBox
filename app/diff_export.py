# -*- coding: utf-8 -*-
"""
diff_export.py —— Before/After 变更对比结果单文件 HTML 导出器（H 批 2026-10-14）

职责：diff_tool.run_compare 的结果 dict → A4 打印可用的单文件 HTML。

复用件（不另造轮子，防两套色）：
    · 打印 token / 同源校验 / CSS 变量：全部取自 runbook_export
      （PRINT_TOKENS + verify_print_tokens_against_theme + _css_vars）——
      theme.py 改色即漂移，导出拒绝（rc=6），与操作单同口径。

硬性约束（与 runbook_export 一致）：
    · 零外部请求：无 <script>、无外链、无 url()，纯静态 HTML + 系统字体；
    · 粘贴内容纯本地，导出文件写在哪由用户在保存对话框决定。
"""

from __future__ import print_function

import datetime
import os

from runbook_export import (PRINT_TOKENS, verify_print_tokens_against_theme,
                            _css_vars, _esc)

# 四态 → (显示名, CSS 变量, 徽章 css 类)；色值全部来自 token 注入，不手写十六进制
STATE_META = {
    "same":    ("✓ 不变", "success", "st-same"),
    "changed": ("⚠ 变化", "warning", "st-changed"),
    "added":   ("＋ 新增", "accent",  "st-added"),
    "removed": ("－ 消失", "danger",  "st-removed"),
}
_STATE_ORDER = {"changed": 0, "removed": 1, "added": 2, "same": 3}

LEVEL_META = {
    "clean":   ("L0 · 一致", "success", "比对全部一致（✓ 不变）——噪声行（uptime/计数器/时间戳/CPU 瞬时值）已自动忽略"),
    "changed": ("L1 · 状态值变化", "warning", "存在状态值变化（⚠ before → after），逐项核对是否为本次变更的预期结果"),
    "delta":   ("L2 · 键集增删", "accent",  "存在新增/消失键（变更目标达成与否的主判据），逐项核对增删清单"),
}

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · Before/After 变更对比</title>
<style>
/* 打印 token：由 runbook_export.PRINT_TOKENS 注入（与操作单导出同源，导出前同源校验） */
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
.meta th{background:var(--panel);color:var(--text2);white-space:nowrap;width:26mm;font-weight:600}
.notice{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--lvcolor,var(--accent));
border-radius:4px;padding:3mm 4mm;font-size:12px;margin-bottom:5mm}
.notice b{color:var(--lvcolor,var(--accent))}
table.d{width:100%;border-collapse:collapse;font-size:11.5px}
table.d th,table.d td{border:1px solid var(--border);padding:3px 6px;text-align:left;vertical-align:top}
table.d th{background:var(--panel);color:var(--text2);font-weight:600;white-space:nowrap}
table.d td.k{font:11px/1.5 Consolas,"Courier New",monospace;word-break:break-all}
table.d td.v{font:11px/1.5 Consolas,"Courier New",monospace;white-space:pre-wrap;word-break:break-all}
table.d tr.same td{color:var(--text3)}
.badge{display:inline-block;padding:0 6px;border-radius:3px;font-size:10px;line-height:16px}
.st-same{background:var(--panel);color:var(--text3);border:1px solid var(--border)}
.st-changed{background:var(--warning-14);color:var(--warning);font-weight:600}
.st-added{background:var(--accent-14);color:var(--accent);font-weight:600}
.st-removed{background:var(--danger-14);color:var(--danger);font-weight:600}
.foot{margin-top:6mm;color:var(--text3);font-size:10.5px}
@page{size:A4;margin:14mm 12mm}
@media print{
  body{background:#fff}
  .sheet{max-width:none;padding:0}
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
<div class="subtitle">Before/After 变更对比 · 由 NetToolBox 导出（__GENERATED__） · 粘贴回显纯本地比对，零联网</div>
__META_TABLE__
<div class="notice" style="--lvcolor:var(--__LVVAR__)"><b>__LEVEL_NAME__：</b>__LEVEL_DESC__</div>
__DIFF_TABLE__
<div class="foot">噪声过滤：__NOISE__ 行已自动忽略（uptime / 包计数器 / 时间戳 / CPU 瞬时值等）；
比对模式：__MODE__。本文件由 NetToolBox 本地生成，不含脚本与外部引用，可随操作单归档。</div>
</div>
</body>
</html>
"""


def _mode_text(result):
    if result.get("mode") == "rule":
        return "提取规则「%s」（%s 语义%s）" % (
            result.get("rule_label") or result.get("rule_id"),
            "键值" if result.get("semantics") == "kv" else "集合",
            ("，变体：%s" % result.get("variant_used")) if result.get("variant_used") else "")
    return "降级：行级比对（%s）" % (result.get("fallback_reason") or "无规则命中")


def build_html(result, title="变更对比"):
    """渲染单文件 HTML（不含写盘）。result 需为 diff_tool.run_compare 的返回。"""
    items = result.get("items") or []
    summary = result.get("summary") or {}
    level = result.get("level") if result.get("level") in LEVEL_META else "clean"
    lv_name, lv_var, lv_desc = LEVEL_META[level]

    rows_meta = [
        ("比对结论", "%s（✓ 不变 %d ｜ ⚠ 变化 %d ｜ ＋ 新增 %d ｜ － 消失 %d）"
         % (lv_name, summary.get("same", 0), summary.get("changed", 0),
            summary.get("added", 0), summary.get("removed", 0))),
        ("比对模式", _mode_text(result)),
    ]
    meta_table = '<table class="meta">%s</table>' % "".join(
        "<tr><th>%s</th><td>%s</td></tr>" % (_esc(k), _esc(v)) for k, v in rows_meta)

    ordered = sorted(items, key=lambda it: _STATE_ORDER.get(it.get("state"), 9))
    if ordered:
        trs = []
        for it in ordered:
            state = it.get("state") if it.get("state") in STATE_META else "same"
            s_name, _var, s_cls = STATE_META[state]
            trs.append(
                '<tr class="%s"><td><span class="badge %s">%s</span></td>'
                '<td class="k">%s</td><td class="v">%s</td><td class="v">%s</td></tr>'
                % (state, s_cls, _esc(s_name), _esc(it.get("key")),
                   _esc(it.get("before")) or "—", _esc(it.get("after")) or "—"))
        diff_table = (
            '<table class="d"><tr><th style="width:16mm">状态</th><th>键 / 行</th>'
            '<th style="width:30%%">Before</th><th style="width:30%%">After</th></tr>%s</table>'
            % "".join(trs))
    else:
        diff_table = ('<table class="d"><tr><th>状态</th><th>键 / 行</th><th>Before</th>'
                      '<th>After</th></tr><tr><td colspan="4">（无可比对内容）</td></tr></table>')

    html = (_TEMPLATE
            .replace("__TITLE__", _esc(title))
            .replace("__GENERATED__", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__CSS_TOKENS__", _css_vars())
            .replace("__META_TABLE__", meta_table)
            .replace("__LVVAR__", lv_var)
            .replace("__LEVEL_NAME__", _esc(lv_name))
            .replace("__LEVEL_DESC__", _esc(lv_desc))
            .replace("__DIFF_TABLE__", diff_table)
            .replace("__NOISE__", str(result.get("noise_dropped") or 0))
            .replace("__MODE__", _esc(_mode_text(result))))
    return html


def export_diff(result, out_path, title="变更对比"):
    """
    导出主流程：token 同源校验 → 渲染 → 落盘。
    返回 (rc, message)：rc=0 成功；rc=6 token 漂移拒绝导出；rc=8 写盘失败。
    （与 runbook_export.export_runbook 同口径）
    """
    drift = verify_print_tokens_against_theme()
    if drift:
        return 6, "打印 token 与 theme.py 漂移，拒绝导出（不做第二套色）：\n" + "\n".join(
            "  - " + d for d in drift)
    try:
        html = build_html(result, title=title)
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
