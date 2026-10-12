# -*- coding: utf-8 -*-
"""
export_site.py —— 静态站点导出器（单文件自包含 HTML）

产出
    一个 .html 文件：数据内联 JSON + 原生 JS 过滤/搜索 + 内联 CSS。
    零外部资源（无 CDN/网络字体/外链 JS），双击即开，离线可用。

四库页面
    仪表盘（三库四档条 + 厂商×分类矩阵热力，与 ui_dashboard 同源 SQL，导出即对账）
    命令库（厂商/分类过滤 + 全文搜索 + 详情：params 表 / commands 注释灰化 /
            rollback 折叠 / 四档验证徽章如实呈现）
    排查树 / 报错字典（裁决 B：三库全导）

硬性约束
    · 脱敏零命中才落盘（sanitize_scan.assert_clean，命中定位到 uuid.字段）；
    · 导出器零写权：导出前后主库 sha256 逐字节一致，不一致即报错退出；
    · history / err_unresolved / ai_config 永不进站点（只 SELECT 三库）；
    · 挂起=正当状态，徽章如实，不隐藏。

用法
    python scripts/export_site.py --out dist/NetToolBox站点.html
    python scripts/export_site.py --out site.html --db path/to/command_lib.db
"""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sanitize_scan  # noqa: E402


# ---------------------------------------------------------------------------
# 一、数据层（与 ui_dashboard 同源 SQL，导出即对账）
# ---------------------------------------------------------------------------

def tier_counts(conn, table, has_notes=True):
    """与 app/ui_dashboard._tier_counts 逐字同源（改这里必须同步改那边，反之亦然）。"""
    sql = (
        "SELECT "
        "SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END) AS green, "
        "SUM(CASE WHEN verified <> 1 AND exec_level = 'verified-cli' "
        "THEN 1 ELSE 0 END) AS syntax, "
        "SUM(CASE WHEN verified <> 1 AND exec_level IN ('skeleton', 'web-only') "
        "THEN 1 ELSE 0 END) AS skeleton "
        "FROM %s" % table)
    row = conn.execute(sql).fetchone()
    green = int(row[0] or 0)
    syntax = int(row[1] or 0)
    suspended = int(row[2] or 0)
    if has_notes:
        suspended += int(conn.execute(
            "SELECT count(*) FROM %s WHERE verified <> 1 AND exec_level = '' "
            "AND notes LIKE '%%不可验证%%'" % table).fetchone()[0])
    total = int(conn.execute("SELECT count(*) FROM %s" % table).fetchone()[0])
    return {"green": green, "syntax": syntax, "suspended": suspended,
            "unverified": max(0, total - green - syntax - suspended),
            "total": total}


def load_data(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    def rows(table):
        cur = conn.execute("SELECT * FROM %s" % table)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    entries = rows("entries")
    trees_raw = rows("trouble_trees")
    errs_raw = rows("err_dict")
    coverage = {
        "entries": tier_counts(conn, "entries", has_notes=True),
        "errs": tier_counts(conn, "err_dict", has_notes=False),
        "trees": None,  # 下面特例口径
    }
    g, t = conn.execute(
        "SELECT SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END), count(*) "
        "FROM trouble_trees").fetchone()
    coverage["trees"] = {"green": int(g or 0), "syntax": 0, "suspended": 0,
                         "unverified": int(t or 0) - int(g or 0),
                         "total": int(t or 0)}
    coverage["matrix"] = [dict(r) for r in conn.execute(
        "SELECT vendor, category, count(*) AS n FROM entries "
        "GROUP BY vendor, category")]
    conn.close()

    # JSON 字符串字段预解析（JS 侧免二次 parse；解析失败按原文兜底）
    for e in entries:
        try:
            e["params"] = json.loads(e["params"]) if e.get("params") else []
        except (ValueError, TypeError):
            e["params"] = []
    for t in trees_raw:
        try:
            t["steps"] = json.loads(t["steps"]) if t.get("steps") else []
        except (ValueError, TypeError):
            t["steps"] = []
    for x in errs_raw:
        try:
            x["examples"] = json.loads(x["examples"]) if x.get("examples") else []
        except (ValueError, TypeError):
            x["examples"] = [x["examples"]] if x.get("examples") else []
    return {"entries": entries, "trees": trees_raw, "errs": errs_raw,
            "coverage": coverage}


# ---------------------------------------------------------------------------
# 二、token 同源（不做第二套色：SITE_TOKENS 与 theme.py 常量逐一比对）
# ---------------------------------------------------------------------------

SITE_TOKENS = {
    "bg_window": "#1b1d23",
    "bg_panel": "#232530",
    "bg_raised": "#2a2d3a",
    "code_bg": "#16181d",
    "border": "#333745",
    "accent": "#4f8cff",
    "success": "#3fbf6f",
    "warning": "#e0a83c",
    "danger": "#e05656",
    "text_primary": "#e8eaf0",
    "text_secondary": "#9aa0b0",
    "text_muted": "#6a7080",
}


def verify_tokens_against_theme():
    """正则扫 theme.py 常量定义，与 SITE_TOKENS 逐字比对（防两套色漂移）。"""
    src = open(os.path.join(REPO, "app", "theme.py"), encoding="utf-8").read()
    name_map = {
        "bg_window": "BG_WINDOW", "bg_panel": "BG_PANEL", "bg_raised": "BG_RAISED",
        "code_bg": "CODE_BG", "border": "BORDER", "accent": "ACCENT",
        "success": "SUCCESS", "warning": "WARNING", "danger": "DANGER",
        "text_primary": "TEXT_PRIMARY", "text_secondary": "TEXT_SECONDARY",
        "text_muted": "TEXT_MUTED",
    }
    drift = []
    for key, const in name_map.items():
        m = re.search(r"^%s\s*=\s*\"(#[0-9a-fA-F]{6})\"" % const, src, re.M)
        if not m:
            drift.append("%s 在 theme.py 找不到定义" % const)
        elif m.group(1).lower() != SITE_TOKENS[key].lower():
            drift.append("%s: theme=%s site=%s" % (const, m.group(1), SITE_TOKENS[key]))
    return drift


def css_vars():
    # rgba 记号转 CSS：QSS 第 4 位 0-255 → CSS 0-1
    def rgba255(css_rgb, alpha8):
        a = round(alpha8 / 255.0, 3)
        return "rgba(%d, %d, %d, %s)" % (css_rgb[0], css_rgb[1], css_rgb[2], a)
    acc = (79, 140, 255)
    return (
        ":root{--bg-window:%(bg_window)s;--bg-panel:%(bg_panel)s;"
        "--bg-raised:%(bg_raised)s;--code-bg:%(code_bg)s;"
        "--border:%(border)s;--accent:%(accent)s;--success:%(success)s;"
        "--warning:%(warning)s;--danger:%(danger)s;"
        "--txt:%(text_primary)s;--txt2:%(text_secondary)s;--txt3:%(text_muted)s;"
        "--accent-12:%(acc12)s;--success-14:%(suc14)s;"
        "--warning-14:%(war14)s;--danger-14:%(dan14)s;}" % {
            "bg_window": SITE_TOKENS["bg_window"],
            "bg_panel": SITE_TOKENS["bg_panel"],
            "bg_raised": SITE_TOKENS["bg_raised"],
            "code_bg": SITE_TOKENS["code_bg"],
            "border": SITE_TOKENS["border"],
            "accent": SITE_TOKENS["accent"],
            "success": SITE_TOKENS["success"],
            "warning": SITE_TOKENS["warning"],
            "danger": SITE_TOKENS["danger"],
            "text_primary": SITE_TOKENS["text_primary"],
            "text_secondary": SITE_TOKENS["text_secondary"],
            "text_muted": SITE_TOKENS["text_muted"],
            "acc12": rgba255(acc, 31),
            "suc14": rgba255((63, 191, 111), 36),
            "war14": rgba255((224, 168, 60), 36),
            "dan14": rgba255((224, 86, 86), 36),
        })


# ---------------------------------------------------------------------------
# 三、模板（单文件；数据以 JSON 内联，</ 转义防截断）
# ---------------------------------------------------------------------------

def data_to_json(data):
    return json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NetToolBox 命令库 · 静态站点</title>
<style>
/* token 由导出器从 SITE_TOKENS 注入（与 app/theme.py 同源，导出时校验，不做第二套色） */
__CSS_TOKENS__
*{box-sizing:border-box}
body{margin:0;background:var(--bg-window);color:var(--txt);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none}
.topbar{display:flex;align-items:center;gap:18px;padding:10px 20px;
background:var(--bg-panel);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:9}
.topbar .logo{font-weight:700;font-size:15px}
.tab{padding:5px 14px;border-radius:6px;cursor:pointer;color:var(--txt2);user-select:none}
.tab:hover{background:var(--bg-raised);color:var(--txt)}
.tab.on{background:var(--accent-12);color:var(--accent);font-weight:600}
.page{display:none;padding:16px 20px;max-width:1280px;margin:0 auto}
.page.on{display:block}
.hint{color:var(--txt3);font-size:12px;margin:6px 0}
/* 仪表盘 */
.tiers{display:flex;flex-direction:column;gap:10px;margin:14px 0}
.tier{display:flex;align-items:center;gap:12px}
.tier .lbl{width:64px;color:var(--txt2);font-size:13px}
.tier .bar{flex:1;display:flex;gap:2px;height:14px}
.tier .bar i{display:block;height:100%}
.tier .nums{width:330px;color:var(--txt2);font-size:12px}
.tier .nums b{color:var(--txt)}
table.matrix{border-collapse:collapse;width:100%;margin-top:10px;font-size:12.5px}
table.matrix th,table.matrix td{border:1px solid var(--border);padding:4px 8px;text-align:center}
table.matrix th{background:var(--bg-raised);color:var(--txt2);font-weight:600;position:sticky;top:46px}
table.matrix td.v{text-align:left;color:var(--txt2);white-space:nowrap}
table.matrix td.c{cursor:pointer}
/* 命令库布局 */
.lib{display:flex;gap:16px}
.side{width:200px;flex-shrink:0}
.side .grp{color:var(--txt3);font-size:12px;margin:12px 4px 4px}
.vbtn{display:flex;justify-content:space-between;padding:4px 10px;border-radius:6px;
cursor:pointer;color:var(--txt2);font-size:13px}
.vbtn:hover{background:var(--bg-raised)}
.vbtn.on{background:var(--accent-12);color:var(--accent)}
.vbtn .n{color:var(--txt3);font-size:11px}
.main{flex:1;min-width:0}
.toolbar{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
input[type=text],select{background:var(--bg-panel);border:1px solid var(--border);
color:var(--txt);border-radius:6px;padding:6px 10px;font-size:13px;outline:none}
input[type=text]:focus,select:focus{border-color:var(--accent)}
input.search{flex:1;min-width:200px}
.list .item{background:var(--bg-panel);border:1px solid var(--border);border-radius:8px;
padding:10px 14px;margin-bottom:8px;cursor:pointer}
.list .item:hover{border-color:var(--border);background:var(--bg-raised)}
.item .t{font-weight:600}
.item .m{color:var(--txt3);font-size:12px;margin-top:2px}
.badge{display:inline-block;padding:1px 8px;border-radius:4px;font-size:11px;margin-left:8px;vertical-align:1px}
.b-green{background:var(--success-14);color:var(--success)}
.b-syntax{background:var(--accent-12);color:var(--accent)}
.b-hold{background:var(--warning-14);color:var(--warning)}
.b-unverified{background:transparent;color:var(--txt3);border:1px dashed var(--border)}
.detail{background:var(--bg-panel);border:1px solid var(--border);border-radius:8px;padding:16px 20px;margin-top:10px}
.detail h2{margin:4px 0 8px;font-size:17px}
.meta{color:var(--txt3);font-size:12px;margin-bottom:10px}
pre.code{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;
padding:12px 14px;overflow:auto;white-space:pre-wrap;word-break:break-all;
font:12.5px/1.55 Consolas,"Courier New",monospace;margin:8px 0}
pre.code .cm{color:var(--txt3);font-style:italic}
.params{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}
.params th,.params td{border:1px solid var(--border);padding:5px 9px;text-align:left;vertical-align:top}
.params th{background:var(--bg-raised);color:var(--txt2)}
.sec{margin:14px 0 6px;font-weight:600;font-size:13px;color:var(--txt2)}
.fold{border:1px solid var(--border);border-radius:8px;margin:8px 0;overflow:hidden}
.fold summary{cursor:pointer;padding:8px 14px;background:var(--bg-raised);color:var(--txt2);user-select:none}
.fold[open] summary{color:var(--accent)}
.notes{white-space:pre-wrap;color:var(--txt2);background:var(--code-bg);
border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:12.5px}
.card{background:var(--bg-panel);border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:10px}
.card h3{margin:2px 0 8px;font-size:15px}
.step{border-left:2px solid var(--accent);margin:10px 0;padding:2px 0 2px 14px}
.step .sid{color:var(--accent);font-weight:700;font-size:12px}
.branch{display:inline-block;background:var(--bg-raised);border:1px solid var(--border);
border-radius:5px;padding:2px 10px;margin:4px 6px 0 0;font-size:12px;color:var(--txt2)}
.branch b{color:var(--accent)}
.kw{color:var(--accent)}
.empty{color:var(--txt3);text-align:center;padding:40px 0}
</style>
</head>
<body>
<div class="topbar">
  <span class="logo">NetToolBox 命令库</span>
  <span class="tab on" data-p="dash">仪表盘</span>
  <span class="tab" data-p="lib">命令库</span>
  <span class="tab" data-p="trees">排查树</span>
  <span class="tab" data-p="errs">报错字典</span>
</div>
<div class="page on" id="p-dash"></div>
<div class="page" id="p-lib"></div>
<div class="page" id="p-trees"></div>
<div class="page" id="p-errs"></div>
<script>
var DATA = __SITE_DATA__;
</script>
<script>
/* ================= 工具 ================= */
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
function tierBadge(e){
  if(e.verified==1)return '<span class="badge b-green">绿·已验证</span>';
  if(e.exec_level=="verified-cli")return '<span class="badge b-syntax">语法核对</span>';
  if(e.exec_level=="skeleton")return '<span class="badge b-hold">骨架·待真机</span>';
  if(e.notes&&e.notes.indexOf("不可验证")>=0)return '<span class="badge b-hold">挂起</span>';
  return '<span class="badge b-unverified">未验证</span>';
}
function tierOf(e){
  if(e.verified==1)return "green";
  if(e.exec_level=="verified-cli")return "syntax";
  if(e.exec_level=="skeleton"||(e.notes&&e.notes.indexOf("不可验证")>=0))return "suspended";
  return "unverified";
}
function lerp(c1,c2,r){ /* #rrggbb 插值，与 ui_dashboard._lerp_color 同逻辑 */
  var a=parseInt(c1.slice(1),16),b=parseInt(c2.slice(1),16);
  var mix=function(x,y){return Math.round(x+(y-x)*r)};
  var r0=mix(a>>16,b>>16),g0=mix((a>>8)&255,(b>>8)&255),b0=mix(a&255,b&255);
  return "rgb("+r0+","+g0+","+b0+")";
}
function codeHtml(text){
  // 命令注释灰化（与 renderer 同规则：#/! 开头独立行）
  return String(text||"").split("\n").map(function(l){
    var t=l.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    if(/^\s*[#!]/.test(l))return '<span class="cm">'+t+'</span>';
    return t;}).join("\n");
}
/* ================= 仪表盘 ================= */
function renderDash(){
  var cv=DATA.coverage,p=document.getElementById("p-dash");
  function tierRow(name,c){
    var tot=Math.max(1,c.green+c.syntax+c.suspended+c.unverified);
    var segs=[["绿",c.green,"var(--success)"],["语法核对",c.syntax,"var(--accent)"],
      ["挂起",c.suspended,"var(--warning)"],["未验证",c.unverified,"rgba(106,112,128,.45)"]];
    return '<div class="tier"><span class="lbl">'+name+'</span><div class="bar">'+
      segs.filter(function(s){return s[1]>0}).map(function(s){
        return '<i style="width:'+(100*s[1]/tot)+'%;background:'+s[2]+'"></i>'}).join("")+
      '</div><span class="nums"><b>'+c.green+'</b> / <b>'+c.syntax+'</b> / <b>'+c.suspended+
      '</b> / <b>'+c.unverified+'</b>　共 '+c.total+'</span></div>';
  }
  var matrix={},vendors={},cats={};
  cv.matrix.forEach(function(r){matrix[r.vendor+"|"+r.category]=r.n;
    vendors[r.vendor]=1;cats[r.category]=1;});
  var vs=Object.keys(vendors).sort(),cs=Object.keys(cats).sort();
  var maxn=0;cv.matrix.forEach(function(r){maxn=Math.max(maxn,r.n)});
  var h='<h2 style="margin:8px 0 2px">覆盖度仪表盘</h2>'+
    '<div class="hint">与桌面端仪表盘同源口径：绿/语法核对/挂起/未验证；挂起=正当状态（环境不具备·骨架·介质依赖），如实呈现。</div>'+
    '<div class="tiers">'+tierRow("命令",cv.entries)+tierRow("报错",cv.errs)+tierRow("树",cv.trees)+'</div>'+
    '<div class="sec">厂商 × 分类 矩阵（格内数字=条目数，色深映射数量，点击跳命令库）</div>'+
    '<table class="matrix"><tr><th style="text-align:left">厂商 \\ 分类</th>'+
    cs.map(function(c){return "<th>"+esc(c)+"</th>"}).join("")+"</tr>"+
    vs.map(function(v){return '<tr><td class="v">'+esc(v)+"</td>"+
      cs.map(function(c){var n=matrix[v+"|"+c];
        if(!n)return "<td></td>";
        var bg=lerp("#2a2d3a","#4f8cff",0.12+0.55*n/maxn);
        return '<td class="c" style="background:'+bg+'" onclick="jumpLib(\''+esc(v)+'\',\''+esc(c)+'\')">'+n+"</td>";
      }).join("")+"</tr>"}).join("")+"</table>";
  p.innerHTML=h;
}
/* ================= 命令库 ================= */
var libV="*",libC="*",libQ="";
function renderLib(){
  var p=document.getElementById("p-lib");
  var vcnt={},ccnt={};
  DATA.entries.forEach(function(e){
    vcnt[e.vendor]=(vcnt[e.vendor]||0)+1;
    if(e.vendor==libV||libV=="*")ccnt[e.category]=(ccnt[e.category]||0)+1;});
  var vs=Object.keys(vcnt).sort(),cs=Object.keys(ccnt).sort();
  p.innerHTML='<div class="lib"><div class="side">'+
    '<div class="grp">厂商</div><div class="vbtn '+(libV=="*"?"on":"")+'" onclick="setV(\'*\')">全部 <span class="n">'+DATA.entries.length+'</span></div>'+
    vs.map(function(v){return '<div class="vbtn '+(libV==v?"on":"")+'" onclick="setV(\''+esc(v)+'\')">'+esc(v)+' <span class="n">'+vcnt[v]+'</span></div>'}).join("")+
    '<div class="grp">分类</div><div class="vbtn '+(libC=="*"?"on":"")+'" onclick="setC(\'*\')">全部</div>'+
    cs.map(function(c){return '<div class="vbtn '+(libC==c?"on":"")+'" onclick="setC(\''+esc(c)+'\')">'+esc(c)+'</div>'}).join("")+
    '</div><div class="main"><div class="toolbar">'+
    '<input type="text" class="search" placeholder="搜索：标题 / 描述 / 命令 / 备注…（Ctrl+F 焦点）" value="'+esc(libQ)+'" oninput="libQ=this.value;renderList()">'+
    '</div><div class="hint" id="libCount"></div><div class="list" id="libList"></div></div></div>';
  renderList();
}
function setV(v){libV=v;libC="*";renderLib()}
function setC(c){libC=c;renderLib()}
function renderList(){
  var q=libQ.trim().toLowerCase();
  var rows=DATA.entries.filter(function(e){
    if(libV!="*"&&e.vendor!=libV)return false;
    if(libC!="*"&&e.category!=libC)return false;
    if(!q)return true;
    return (e.title+" "+e.description+" "+e.commands+" "+e.notes+" "+e.os_family).toLowerCase().indexOf(q)>=0;});
  document.getElementById("libCount").textContent="命中 "+rows.length+" 条";
  document.getElementById("libList").innerHTML=rows.map(function(e,i){
    return '<div class="item" onclick="showDetail(\''+e.uuid+'\')"><div class="t">'+esc(e.title)+tierBadge(e)+
      '</div><div class="m">'+esc(e.vendor)+" · "+esc(e.category)+" · "+esc(e.os_family||"")+
      (e.models?" · "+esc(e.models):"")+'</div></div>'}).join("")||
    '<div class="empty">无匹配条目</div>';
}
function jumpLib(v,c){switchTab("lib");libV=v;libC=c;renderLib()}
function showDetail(uuid){
  var e=DATA.entries.filter(function(x){return x.uuid==uuid})[0];
  if(!e)return;
  var list=document.getElementById("libList");
  var dur={"temp":"临时生效","perm":"持久化","both":"临时+持久化"}[e.duration]||"";
  var h='<div class="detail"><h2>'+esc(e.title)+tierBadge(e)+'</h2>'+
    '<div class="meta">'+esc(e.vendor)+" / "+esc(e.category)+" / "+esc(e.os_family||"")+
    (e.models?" · "+esc(e.models):"")+(dur?" · "+dur:"")+"</div>"+
    '<div class="sec">说明</div><div>'+esc(e.description)+"</div>";
  if((e.commands||"").trim()){
    h+='<div class="sec">命令'+(e.interactive?' <span class="badge b-hold">交互式</span>':"")+'</div><pre class="code">'+codeHtml(e.commands)+"</pre>";
  }
  if(e.params&&e.params.length){
    h+='<div class="sec">参数</div><table class="params"><tr><th>参数</th><th>类型</th><th>必填</th><th>默认</th><th>说明</th></tr>'+
      e.params.map(function(p){return "<tr><td><span class='kw'>"+esc(p.name)+"</span></td><td>"+esc(p.type||"")+
        (p.choices?"<br><span style='color:var(--txt3)'>"+esc((p.choices||[]).join(" | "))+"</span>":"")+
        "</td><td>"+(p.required?"✓":"")+"</td><td>"+esc(p.default==null?"":p.default)+"</td><td>"+esc(p.description||"")+"</td></tr>"}).join("")+"</table>";
  }
  if((e.rollback||"").trim()){
    h+='<details class="fold"><summary>回退方案（rollback）</summary><pre class="code">'+codeHtml(e.rollback)+"</pre></details>";
  }
  if((e.notes||"").trim()){
    h+='<div class="sec">坑点 / 判读口径</div><div class="notes">'+esc(e.notes)+"</div>";
  }
  if(e.verified==1){
    h+='<div class="meta" style="margin-top:10px">验证：'+esc(e.verified_by||"")+" @ "+esc(e.verified_model||"")+" · "+esc(e.verified_date||"")+"</div>";
  }else if(e.exec_level=="skeleton"||(e.notes||"").indexOf("不可验证")>=0){
    h+='<div class="meta" style="margin-top:10px;color:var(--warning)">挂起（正当状态）：'+esc(e.exec_level=="skeleton"?"骨架条目，待真机核对":"环境不具备，待真机/介质")+"</div>";
  }
  h+='<div style="margin-top:12px"><a href="#" onclick="renderLib();return false">← 返回列表</a></div></div>';
  list.innerHTML=h;
  window.scrollTo(0,0);
}
/* ================= 排查树 ================= */
function renderTrees(){
  var p=document.getElementById("p-trees");
  p.innerHTML='<h2 style="margin:8px 0 2px">排查树（'+DATA.trees.length+'）</h2>'+
    '<div class="hint">步骤编号 sN；分支 when→goto；步骤命令引用按厂商列出。</div>'+
    DATA.trees.map(function(t){
      var steps={};
      t.steps.forEach(function(s){steps[s.id]=s});
      return '<div class="card"><h3>'+esc(t.symptom)+' '+(t.verified==1?'<span class="badge b-green">已验证</span>':'<span class="badge b-unverified">未验证</span>')+
        '</h3><div class="meta">'+esc(t.category||"")+" · 适用厂商："+esc(t.vendor_hint||"通用")+"</div>"+
        t.steps.map(function(s){
          return '<div class="step"><span class="sid">'+esc(s.id)+'</span> <b>'+esc(s.title)+"</b>"+
            (s.observe?'<div class="hint">观察：'+esc(s.observe)+"</div>":"")+
            (s.explain?'<div style="color:var(--txt2);font-size:12.5px">'+esc(s.explain)+"</div>":"")+
            (s.cmd_ref?refHtml(s.cmd_ref):"")+
            ((s.branches||[]).length?'<div>'+(s.branches||[]).map(function(b){
              return '<span class="branch">'+esc(b.when)+' → <b>'+esc(b.goto)+"</b></span>"}).join("")+"</div>":"")+
            "</div>"}).join("")+"</div>";
    }).join("");
}
function refHtml(ref){
  var bv=ref.by_vendor||{},ks=Object.keys(bv).sort();
  var inner='<span class="kw">'+esc(ref.vendor_category||"")+"</span>"+
    (ref.title_keyword?" · "+esc(ref.title_keyword):"");
  if(!ks.length)return '<div class="hint">命令来源：'+inner+"</div>";
  return '<div class="hint">命令来源（'+inner+'）：</div>'+ks.map(function(v){
    var u=bv[v];
    return '<span class="branch" style="cursor:pointer" onclick="jumpUuid(\''+u.uuid+'\')">'+esc(v)+'：'+esc(u.title)+" →</span>";
  }).join("");
}
function jumpUuid(uuid){switchTab("lib");libV="*";libC="*";libQ="";renderLib();showDetail(uuid)}
/* ================= 报错字典 ================= */
var errQ="";
function renderErrs(){
  var p=document.getElementById("p-errs");
  p.innerHTML='<h2 style="margin:8px 0 2px">报错字典（'+DATA.errs.length+'）</h2>'+
    '<div class="toolbar"><input type="text" class="search" placeholder="搜索报错关键词…" oninput="errQ=this.value;renderErrList()"></div>'+
    '<div id="errList"></div>';
  renderErrList();
}
function renderErrList(){
  var q=errQ.trim().toLowerCase();
  var rows=DATA.errs.filter(function(x){
    if(!q)return true;
    return (x.pattern+" "+x.cause+" "+x.solution_steps+" "+(x.vendor||"")).toLowerCase().indexOf(q)>=0;});
  document.getElementById("errList").innerHTML=rows.map(function(x){
    return '<div class="card"><h3>'+esc(x.pattern)+" "+(x.vendor?'<span class="badge b-syntax">'+esc(x.vendor)+"</span>":"")+"</h3>"+
      (x.cause?'<div class="sec">原因</div><div style="white-space:pre-wrap">'+esc(x.cause)+"</div>":"")+
      (x.solution_steps?'<div class="sec">处置</div><div style="white-space:pre-wrap">'+esc(x.solution_steps)+"</div>":"")+
      (x.fix_template?'<div class="sec">修复模板</div><pre class="code">'+codeHtml(x.fix_template)+"</pre>":"")+
      ((x.examples||[]).length?'<div class="sec">回显样例</div>'+x.examples.map(function(t){
        return '<pre class="code">'+esc(t)+"</pre>"}).join(""):"")+
      "</div>"}).join("")||'<div class="empty">无匹配</div>';
}
/* ================= Tab 切换 ================= */
function switchTab(name){
  document.querySelectorAll(".tab").forEach(function(t){t.classList.toggle("on",t.dataset.p==name)});
  document.querySelectorAll(".page").forEach(function(p){p.classList.toggle("on",p.id=="p-"+name)});
  window.scrollTo(0,0);
}
document.querySelectorAll(".tab").forEach(function(t){
  t.addEventListener("click",function(){switchTab(t.dataset.p)});
});
renderDash();renderLib();renderTrees();renderErrs();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 四、导出主流程（脱敏 → 渲染 → 零写权复核）
# ---------------------------------------------------------------------------

def find_db():
    for p in (os.path.join(REPO, "app", "command_lib.db"),
              os.path.join(REPO, "dist_package", "command_lib.db"),
              os.path.join(os.getcwd(), "command_lib.db")):
        if os.path.isfile(p):
            return p
    raise SystemExit("[E] 未找到 command_lib.db，用 --db 显式指定")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export(db_path, out_path):
    drift = verify_tokens_against_theme()
    if drift:
        print("[E] SITE_TOKENS 与 theme.py 漂移，拒绝导出（不做第二套色）：")
        for d in drift:
            print("   -", d)
        return 6

    db_before = sha256(db_path)
    data = load_data(db_path)
    print("[1/4] 数据加载：entries=%d trees=%d errs=%d"
          % (len(data["entries"]), len(data["trees"]), len(data["errs"])))

    sanitize_scan.assert_clean(data)
    print("[2/4] 脱敏扫描零命中。")

    cv = data["coverage"]
    print("[3/4] 覆盖度对账：命令 %d（绿 %d/语法 %d/挂起 %d/未验 %d）"
          % (cv["entries"]["total"], cv["entries"]["green"],
             cv["entries"]["syntax"], cv["entries"]["suspended"],
             cv["entries"]["unverified"]))

    html = _TEMPLATE.replace("__SITE_DATA__", data_to_json(data)) \
                    .replace("__CSS_TOKENS__", css_vars())
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    size_kb = os.path.getsize(out_path) / 1024.0

    db_after = sha256(db_path)
    if db_before != db_after:
        print("[E] 零写权断言失败：导出过程主库 sha256 发生变化（%s -> %s）"
              % (db_before[:12], db_after[:12]))
        return 7
    print("[4/4] 零写权实证：主库指纹前后一致 %s…" % db_before[:12])
    print("[OK] %s（%.0f KB）" % (out_path, size_kb))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="产物 .html 路径")
    ap.add_argument("--db", default=None, help="主库路径（默认自动定位）")
    args = ap.parse_args(argv)
    return export(args.db or find_db(), args.out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
