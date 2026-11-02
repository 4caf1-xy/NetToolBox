# -*- coding: utf-8 -*-
"""
handbook.py —— 速查手册数据层（R 批 2026-11-02）

定位：七本速查手册（HTTP 状态码 / 常用端口 / Git / Linux / Vim / SSH / 网络命令）
是**纯数据**，独立 JSON 随程序打包（app/handbook/*.json），不写进 command_lib.db，
因此不影响种子底数（459 / 26 / 106）与覆盖度矩阵；离网现场可查、可整册替换。

职责边界：
    · 只做加载 / 结构校验 / 全文检索，不 import 任何 GUI 与网络模块；
    · 数据目录两级优先（与 seed_dirs 同口径）：exe 同目录 handbook/ > exe 内置 handbook/，
      现场想补一本手册，把 JSON 丢进同目录 handbook/ 即可，无需重新打包；
    · 数据错误一律「收集成 problems 列表」而不是抛异常——手册坏一本不能拖垮整个界面。

数据格式（每本一个 JSON）：
    {
      "doc_id": "ports", "title": "常用端口", "subtitle": "...",
      "source": "权威来源（IANA/RFC/官方文档）", "updated": "2026-11-02",
      "notes": ["口径说明…"],
      "groups": [
        {"name": "分组名", "desc": "分组说明",
         "columns": ["列1","列2","列3","列4"],
         "rows": [["…","…","…","…"], …]}
      ]
    }
    约束：四列固定（表格视图与检索都按 4 列排版）；每行 4 个非空字符串。

命令行（由 main.py --validate-handbook 调用）：
    python app/main.py --validate-handbook   # EXIT=0 表示七本手册结构/事实自检全过
"""

import io
import json
import os

import db as dbmod

DIRNAME = "handbook"
COLUMN_COUNT = 4

# 手册展示顺序（未列入的按文件名排在其后，方便现场新增自定义手册）
DOC_ORDER = ["http-status", "ports", "git", "linux", "vim", "ssh", "netcmd"]

REQUIRED_DOC_FIELDS = ("doc_id", "title", "subtitle", "source", "updated", "groups")
REQUIRED_GROUP_FIELDS = ("name", "columns", "rows")

# 端口册：传输层取值白名单（IANA registry 的三种常见组合）
PROTOCOLS = ("TCP", "UDP", "TCP/UDP")

# ---------------------------------------------------------------------------
# 目录解析
# ---------------------------------------------------------------------------
def handbook_dirs():
    """
    手册候选目录（按优先级去重）：
        1. exe/源码同目录 handbook/ —— 现场可整册替换或补册
        2. 打包进 exe 的内置 handbook/ —— 随包发布
    """
    result = []
    try:
        base = dbmod.get_base_dir()
    except Exception:
        base = None
    for path in (os.path.join(base, DIRNAME) if base else None,
                 dbmod.resource_path(DIRNAME)):
        if path and os.path.isdir(path) and path not in result:
            result.append(path)
    return result


def _doc_files(dirs=None):
    """按文件名去重收集 JSON（前面的目录优先，与 seed_dirs 语义一致）"""
    files = {}
    for d in (dirs if dirs is not None else handbook_dirs()):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if name.lower().endswith(".json") and name not in files:
                files[name] = os.path.join(d, name)
    return files


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------
def load_docs(dirs=None):
    """
    加载全部手册。返回 (docs, problems)：
        docs     —— 按 DOC_ORDER 排序的 doc dict 列表（结构坏的文档不进列表）
        problems —— 人类可读的问题清单（空列表 = 全部合格）
    """
    docs = []
    problems = []
    seen_ids = {}
    for name, path in sorted(_doc_files(dirs).items()):
        try:
            raw = io.open(path, "r", encoding="utf-8-sig").read()
            doc = json.loads(raw)
        except Exception as exc:
            problems.append("%s：读取/解析失败（%s）" % (name, exc))
            continue
        errs = validate_doc(doc, name)
        if errs:
            problems.extend(errs)
            continue
        did = doc["doc_id"]
        if did in seen_ids:
            problems.append("%s：doc_id 与 %s 重复（%s）" % (name, seen_ids[did], did))
            continue
        seen_ids[did] = name
        doc["_file"] = name
        docs.append(doc)

    def _key(doc):
        did = doc.get("doc_id")
        rank = DOC_ORDER.index(did) if did in DOC_ORDER else len(DOC_ORDER)
        return (rank, did)

    docs.sort(key=_key)
    # 缺册提示（只对「随包发布的七本」做严格检查；指定目录加载时不挑剔，
    # 便于现场新增自定义手册与测试用单册样本）
    if dirs is None:
        for did in DOC_ORDER:
            if did not in seen_ids and not problems:
                problems.append("缺少手册：%s（预期七本齐全）" % did)
    return docs, problems


def validate_doc(doc, name="<doc>"):
    """单本手册结构校验，返回问题列表（不抛异常）"""
    problems = []
    if not isinstance(doc, dict):
        return ["%s：顶层不是对象" % name]
    for field in REQUIRED_DOC_FIELDS:
        if field not in doc or doc[field] in (None, "", [], {}):
            problems.append("%s：缺字段或为空 %s" % (name, field))
    if problems:
        return problems
    if not isinstance(doc["groups"], list) or not doc["groups"]:
        return ["%s：groups 必须是非空数组" % name]

    total = 0
    for gi, group in enumerate(doc["groups"], 1):
        head = "%s 第 %d 组" % (name, gi)
        if not isinstance(group, dict):
            problems.append("%s：不是对象" % head)
            continue
        for field in REQUIRED_GROUP_FIELDS:
            if field not in group:
                problems.append("%s：缺字段 %s" % (head, field))
        gname = str(group.get("name") or "").strip()
        if not gname:
            problems.append("%s：分组名为空" % head)
        cols = group.get("columns")
        if not isinstance(cols, list) or len(cols) != COLUMN_COUNT:
            problems.append("%s（%s）：columns 必须是 %d 列" % (head, gname, COLUMN_COUNT))
            continue
        if any(not str(c).strip() for c in cols):
            problems.append("%s（%s）：columns 存在空列名" % (head, gname))
        rows = group.get("rows")
        if not isinstance(rows, list) or not rows:
            problems.append("%s（%s）：rows 必须是非空数组" % (head, gname))
            continue
        for ri, row in enumerate(rows, 1):
            total += 1
            if not isinstance(row, list) or len(row) != COLUMN_COUNT:
                problems.append("%s（%s）第 %d 行：必须是 %d 个单元格"
                                % (head, gname, ri, COLUMN_COUNT))
                continue
            for ci, cell in enumerate(row, 1):
                if not isinstance(cell, str) or not cell.strip():
                    problems.append("%s（%s）第 %d 行第 %d 列：空单元格"
                                    % (head, gname, ri, ci))
    if total == 0:
        problems.append("%s：一条数据都没有" % name)

    # 分册专属校验（数字对不上就是必须拦下的硬伤）
    if not problems:
        did = doc.get("doc_id")
        if did == "ports":
            problems.extend(_check_ports(doc, name))
        elif did == "http-status":
            problems.extend(_check_http(doc, name))
    return problems


def _check_ports(doc, name):
    """端口册：号码范围、传输层取值、(端口+传输层) 唯一"""
    problems = []
    seen = {}
    for group in doc["groups"]:
        for row in group["rows"]:
            port_s, proto = row[0].strip(), row[1].strip()
            try:
                port = int(port_s)
            except ValueError:
                problems.append("%s：端口不是整数（%r）" % (name, port_s))
                continue
            if not 1 <= port <= 65535:
                problems.append("%s：端口越界（%s）" % (name, port_s))
            if proto not in PROTOCOLS:
                problems.append("%s：端口 %d 的传输层取值非法（%s）" % (name, port, proto))
                continue
            for single in ("TCP", "UDP") if proto == "TCP/UDP" else (proto,):
                key = (port, single)
                if key in seen:
                    problems.append("%s：%d/%s 重复登记（已在「%s」）"
                                    % (name, port, single, seen[key]))
                else:
                    seen[key] = row[2].strip()
    return problems


def _check_http(doc, name):
    """状态码册：三位数字、全局唯一、号段与分组一致"""
    problems = []
    seen = set()
    for group in doc["groups"]:
        gname = group["name"]
        lead = gname.strip()[:1]
        for row in group["rows"]:
            code = row[0].strip()
            if len(code) != 3 or not code.isdigit():
                problems.append("%s：状态码格式非法（%r）" % (name, code))
                continue
            if lead.isdigit() and code[0] != lead:
                problems.append("%s：状态码 %s 放错了分组（%s）" % (name, code, gname))
            if code in seen:
                problems.append("%s：状态码 %s 重复" % (name, code))
            seen.add(code)
    return problems


# ---------------------------------------------------------------------------
# 统计与检索
# ---------------------------------------------------------------------------
def doc_stats(doc):
    """(分组数, 行数)"""
    groups = doc.get("groups") or []
    return len(groups), sum(len(g.get("rows") or []) for g in groups)


def summary(docs):
    """一行汇总文本（CLI / 状态栏共用）"""
    rows = sum(doc_stats(d)[1] for d in docs)
    groups = sum(doc_stats(d)[0] for d in docs)
    return "速查手册：%d 本 / %d 组 / %d 条" % (len(docs), groups, rows)


def iter_rows(doc):
    """遍历 (组下标, 行下标, 行) —— 下标均从 0 起，供跳转定位复用"""
    for gi, group in enumerate(doc.get("groups") or []):
        for ri, row in enumerate(group.get("rows") or []):
            yield gi, ri, row


def row_text(row):
    """一行的可复制文本（列间用「 ｜ 」分隔，便于贴到工单）"""
    return " ｜ ".join(str(c).replace("\n", " / ") for c in row)


def search(docs, keyword, limit=300):
    """
    全库检索：在四列文本里做包含匹配（大小写不敏感）。
    返回 [{"doc_id","doc_title","group","group_index","row_index","cells","text"}]，
    行内命中次数高的排前面（同分保持原始顺序）——Ctrl+K 与手册内搜索共用。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    hits = []
    for doc in docs:
        for gi, ri, row in iter_rows(doc):
            blob = " ".join(str(c) for c in row).lower()
            pos = blob.find(kw)
            if pos < 0:
                continue
            score = blob.count(kw)
            groups = doc.get("groups") or []
            hits.append({
                "score": score,
                "doc_id": doc.get("doc_id"),
                "doc_title": doc.get("title") or "",
                "group": (groups[gi].get("name") if gi < len(groups) else "") or "",
                "group_index": gi,
                "row_index": ri,
                "cells": list(row),
                "text": row_text(row),
            })
    hits.sort(key=lambda h: -h["score"])     # 稳定排序：同分保持原顺序
    return hits[:limit]


def find_doc(docs, doc_id):
    for doc in docs:
        if doc.get("doc_id") == doc_id:
            return doc
    return None


def validate_cli(verbose=True):
    """
    CLI 入口（main.py --validate-handbook）：
        打印目录、每本手册的分组/条数、问题清单；返回 0/1。
    """
    dirs = handbook_dirs()
    docs, problems = load_docs()
    if verbose:
        print("=" * 66)
        print("NetToolBox 速查手册质检（app/handbook/*.json）")
        print("=" * 66)
        print("手册目录：%s" % ("；".join(dirs) or "（未找到）"))
    if not docs and not problems:
        problems.append("没有找到任何手册 JSON（目录：%s）" % dirs)
    for doc in docs:
        groups, rows = doc_stats(doc)
        if verbose:
            print("  [OK] %-12s %-10s %d 组 / %3d 条  来源：%s"
                  % (doc.get("doc_id"), doc.get("title"), groups, rows,
                     (doc.get("source") or "")[:34]))
    if verbose:
        print("-" * 66)
        print(summary(docs))
    if problems:
        print("[X] 手册数据发现 %d 个问题：" % len(problems))
        for item in problems[:40]:
            print("   - " + item)
        return 1
    if verbose:
        print("[OK] 手册质检通过：结构完整（四列非空 / 端口与状态码自检全过）")
    return 0
