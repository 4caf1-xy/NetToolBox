# -*- coding: utf-8 -*-
"""
report.py —— 排查报告引擎（模块 04 第 7 轮）

向导走完自动生成报告（HTML + 纯文本两份）：
    现象、设备型号、完整步骤路径、每步命令与观察结论、最终原因、耗时
存 exe 同目录 trouble_reports/，并提供历史档案查询（按现象/关键字搜索）。
"""

import os
import re
import html
import datetime

import db as dbmod


def reports_dir():
    """报告目录：exe 同目录 trouble_reports/（跟着 U 盘走）"""
    path = os.path.join(dbmod.get_base_dir(), "trouble_reports")
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


def _safe_name(text):
    """文件名安全化（去掉路径分隔符与非法字符）"""
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", (text or "report").strip())
    return name[:40] or "report"


def build_report(tree, walk_log, vendor="", model="", operator="",
                 duration_sec=0, findings=None):
    """
    生成报告正文。
    参数：
        tree       —— 排查树 dict（dy symptom/category/tree_id）
        walk_log   —— [{step_id, title, cmd, when, observe}, ...] 走过的步骤与选择
        findings   —— 可选的输出分析结论（[{severity,title,advice,line}]）
    返回 (html_text, plain_text)
    """
    tree = tree or {}
    walk_log = walk_log or []
    findings = findings or []
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symptom = tree.get("symptom") or "-"
    leaf = walk_log[-1] if walk_log else {}
    conclusion = leaf.get("conclusion") or "（未走到结论）"

    # ---------- 纯文本 ----------
    lines = [
        "=" * 62,
        "排查报告（NetToolBox 排查向导）",
        "=" * 62,
        "现　　象：%s" % symptom,
        "分类　　：%s" % (tree.get("category") or "-"),
        "设备厂商：%s" % (dbmod.display_vendor(vendor) if vendor else "（未指定）"),
        "设备型号：%s" % (model or "（未填写）"),
        "操 作 人：%s" % (operator or "（未填写）"),
        "生成时间：%s" % stamp,
        "排查耗时：%s" % _fmt_duration(duration_sec),
        "-" * 62,
        "【排查路径】",
    ]
    for i, w in enumerate(walk_log, 1):
        lines.append("%d. [%s] %s" % (i, w.get("step_id", "?"), w.get("title", "")))
        if w.get("observe"):
            lines.append("   观察点：%s" % w.get("observe"))
        if w.get("when"):
            lines.append("   选择　：%s" % w.get("when"))
        if w.get("cmd"):
            lines.append("   命令　：")
            for cl in w["cmd"].splitlines():
                if cl.strip():
                    lines.append("     %s" % cl)
        lines.append("")
    lines.append("-" * 62)
    lines.append("【最终结论】")
    lines.append(conclusion)
    actions = leaf.get("actions") or []
    if actions:
        lines.append("")
        lines.append("【处理动作】")
        for i, a in enumerate(actions, 1):
            # 动作可能是纯字符串（旧数据），也可能是
            # {"text": ..., "cmd": ...}（P1-5 起：命令来自命令库条目渲染）
            if isinstance(a, dict):
                atext = a.get("text") or ""
                acmd = a.get("cmd") or ""
            else:
                atext, acmd = str(a), ""
            lines.append("%d. %s" % (i, atext))
            if acmd:
                for cl in acmd.splitlines():
                    if cl.strip():
                        lines.append("     %s" % cl)
    if findings:
        lines.append("")
        lines.append("-" * 62)
        lines.append("【输出分析结论（粘贴回显解析）】")
        for f in findings:
            lines.append("[%s] %s" % (f.get("title", ""), f.get("advice", "")))
            if f.get("line"):
                lines.append("    原文：%s" % f["line"])
    lines.append("")
    lines.append("=" * 62)
    lines.append("本报告由 NetToolBox 自动生成；命令均来自命令库条目，"
                 "未经真机验证前请自行核对。")
    plain = "\n".join(lines)

    # ---------- HTML ----------
    def esc(t):
        return html.escape(str(t or ""))

    rows = []
    for i, w in enumerate(walk_log, 1):
        cmd_html = esc(w.get("cmd") or "").replace("\n", "<br>")
        rows.append(
            "<tr><td class='idx'>{i}</td>"
            "<td><div class='sid'>{sid}</div>{title}</td>"
            "<td>{observe}</td><td class='when'>{when}</td>"
            "<td><pre>{cmd}</pre></td></tr>".format(
                i=i, sid=esc(w.get("step_id")), title=esc(w.get("title")),
                observe=esc(w.get("observe")), when=esc(w.get("when")),
                cmd=cmd_html))

    def _action_html(a):
        """动作可能是 str（旧数据）或 {"text","cmd"}（命令来自命令库渲染）"""
        if isinstance(a, dict):
            atext = a.get("text") or ""
            acmd = a.get("cmd") or ""
        else:
            atext, acmd = str(a), ""
        html = esc(atext)
        if acmd:
            html += "<pre class='acmd'>%s</pre>" % esc(acmd)
        return html

    actions_html = "".join("<li>%s</li>" % _action_html(a) for a in actions)
    findings_html = ""
    if findings:
        items = []
        for f in findings:
            color = {"error": "#e5534b", "warn": "#e8a33d"}.get(f.get("severity"), "#5aa9f0")
            items.append(
                "<li><b style='color:%s'>%s</b>：%s<br><span class='orig'>原文：%s</span></li>"
                % (color, esc(f.get("title")), esc(f.get("advice")), esc(f.get("line"))))
        findings_html = ("<h2>输出分析结论</h2><ul class='findings'>%s</ul>"
                         % "".join(items))

    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>排查报告 · {symptom}</title>
<style>
 body {{ font-family: "Microsoft YaHei UI","Microsoft YaHei",sans-serif; margin: 28px;
         color:#222; line-height:1.62; }}
 h1 {{ font-size: 20px; border-bottom:2px solid #1f6feb; padding-bottom:8px; }}
 h2 {{ font-size: 15px; margin-top: 22px; color:#1f6feb; }}
 table {{ border-collapse: collapse; width:100%; margin-top:8px; }}
 th,td {{ border:1px solid #d0d7de; padding:6px 8px; font-size:12px;
          vertical-align: top; text-align:left; }}
 th {{ background:#f2f5f9; }}
 td.idx {{ width:28px; text-align:center; color:#666; }}
 .sid {{ color:#1f6feb; font-weight:bold; }}
 .when {{ color:#c9302c; }}
 pre {{ margin:0; font-family: Consolas, monospace; font-size:11.5px;
        white-space: pre-wrap; }}
 .meta td:first-child {{ width:96px; background:#f8fafc; }}
 .concl {{ background:#eaf7ee; border-left:4px solid #2ea043; padding:10px 14px;
           margin-top:8px; }}
 .orig {{ color:#8b9199; }}
 .findings li {{ margin-bottom:6px; }}
 pre.acmd {{ background:#0f1011; color:#c9d1d9; padding:6px 8px; margin:4px 0 0 0;
             border-radius:3px; font-size:11px; }}
 footer {{ margin-top: 26px; color:#8b9199; font-size:11px; }}
</style></head><body>
<h1>排查报告 · {symptom}</h1>
<table class="meta">
  <tr><td>现象</td><td>{symptom}</td><td>分类</td><td>{category}</td></tr>
  <tr><td>设备厂商</td><td>{vendor}</td><td>设备型号</td><td>{model}</td></tr>
  <tr><td>操作人</td><td>{operator}</td><td>生成时间</td><td>{stamp}</td></tr>
  <tr><td>排查耗时</td><td colspan="3">{duration}</td></tr>
</table>

<h2>排查路径</h2>
<table>
 <tr><th>#</th><th>步骤</th><th>观察点</th><th>选择</th><th>执行命令（来自命令库）</th></tr>
 {rows}
</table>

<h2>最终结论</h2>
<div class="concl"><b>{conclusion}</b></div>
{actions}

{findings}

<footer>本报告由 NetToolBox 排查向导自动生成；命令均来自命令库条目，
未经真机验证前请自行核对。报告内含设备配置片段，请按内部资料管理。</footer>
</body></html>""".format(
        symptom=esc(symptom), category=esc(tree.get("category")),
        vendor=esc(dbmod.display_vendor(vendor) if vendor else "（未指定）"),
        model=esc(model or "（未填写）"), operator=esc(operator or "（未填写）"),
        stamp=esc(stamp), duration=esc(_fmt_duration(duration_sec)),
        rows="".join(rows),
        conclusion=esc(conclusion),
        actions=("<h2>处理动作</h2><ol>%s</ol>" % actions_html) if actions_html else "",
        findings=findings_html)
    return doc, plain


def _fmt_duration(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        return "-"
    if seconds < 60:
        return "%d 秒" % seconds
    return "%d 分 %d 秒" % (seconds // 60, seconds % 60)


def save_report(tree, walk_log, vendor="", model="", operator="",
                duration_sec=0, findings=None):
    """写出 HTML + TXT 两份；返回 (html路径, txt路径)"""
    doc, plain = build_report(tree, walk_log, vendor=vendor, model=model,
                              operator=operator, duration_sec=duration_sec,
                              findings=findings)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = "%s_%s" % (stamp, _safe_name((tree or {}).get("symptom")))
    folder = reports_dir()
    html_path = os.path.join(folder, base + ".html")
    txt_path = os.path.join(folder, base + ".txt")
    with open(html_path, "w", encoding="utf-8") as fp:
        fp.write(doc)
    with open(txt_path, "w", encoding="utf-8") as fp:
        fp.write(plain)
    return html_path, txt_path


def list_reports():
    """
    列出历史报告（新的在前）：[{file, txt, time, symptom, conclusion, size}]
    以 .txt 为主（HTML 同名）。
    """
    folder = reports_dir()
    out = []
    try:
        names = sorted(os.listdir(folder), reverse=True)
    except Exception:
        return out
    for name in names:
        if not name.endswith(".txt"):
            continue
        path = os.path.join(folder, name)
        info = {"file": os.path.join(folder, name.replace(".txt", ".html")),
                "txt": path, "time": "", "symptom": "", "conclusion": "", "size": 0}
        try:
            info["size"] = os.path.getsize(path)
            with open(path, "r", encoding="utf-8", errors="replace") as fp:
                content = fp.read()
            for line in content.splitlines():
                if line.startswith("现　　象："):
                    info["symptom"] = line.replace("现　　象：", "").strip()
                elif line.startswith("生成时间："):
                    info["time"] = line.replace("生成时间：", "").strip()
            idx = content.find("【最终结论】")
            if idx >= 0:
                info["conclusion"] = content[idx + len("【最终结论】"):].strip().splitlines()[0][:60]
        except Exception:
            pass
        out.append(info)
    return out


def search_reports(keyword):
    """按现象/结论关键字搜索历史报告"""
    keyword = (keyword or "").strip().lower()
    reports = list_reports()
    if not keyword:
        return reports
    return [r for r in reports
            if keyword in (r.get("symptom") or "").lower()
            or keyword in (r.get("conclusion") or "").lower()
            or keyword in (r.get("time") or "").lower()]
