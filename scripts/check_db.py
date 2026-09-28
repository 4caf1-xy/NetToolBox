# -*- coding: utf-8 -*-
"""
check_db.py —— NetToolBox 命令库自检脚本（离线体检工具）

用途
    把本文件拷到离网机器（与 command_lib.db / NetToolBox.exe 同目录），执行：
        python check_db.py                    # 自动找同目录的 command_lib.db
        python check_db.py D:\\path\\x.db      # 指定库文件
    屏幕输出一份可直接截图留档的文本报告。

检查项
    [1] 三库条目统计（entries / trouble_trees / err_dict / err_unresolved / history）
    [2] 悬空引用清单（err_dict.solution_steps[].ref_entry_uuid、trouble_trees.steps[].cmd_ref.uuid）
    [3] verified 覆盖率（按厂商分组）
    [4] vendor / os_family slug 混用清单
    [5] duration 异常使用清单
    [6] history 表最近 10 条记录 + 动作覆盖

硬性约束（与主程序一致）
    · 零联网：不 import 任何网络模块
    · 零 PyQt：纯标准库，离网机器只要有 python 就能跑
    · 只读：以 SQLite URI mode=ro 打开，绝不写库、绝不改数据
    · 容错：缺表 / 缺列 / JSON 损坏都不崩，逐项降级为文字说明
"""

from __future__ import print_function

import json
import os
import sqlite3
import sys

DB_FILENAME = "command_lib.db"
WIDTH = 74

# ---------------------------------------------------------------------------
# 内置兜底：db.py 的维度取值表（子集，仅用于"是不是规范 slug"的判定）
# 若同目录能找到 db.py，则优先用 db.py 的权威表；否则用这份兜底。
# ---------------------------------------------------------------------------
_FALLBACK_VENDOR_SLUGS = set("""
cisco huawei h3c ruijie fortinet juniper paloalto sangfor topsec zte
arista checkpoint hillstone qianxin venustech nsfocus maipu digitalchina anheng
centos rhel ubuntu debian openeuler kylin ulos
""".split())

_FALLBACK_ALIASES = {
    "思科": "cisco", "华为": "huawei", "新华三": "h3c", "飞塔": "fortinet",
    "瞻博": "juniper", "palo alto": "paloalto", "深信服af": "sangfor",
    "深信服": "sangfor", "天融信": "topsec", "中兴": "zte", "锐捷": "ruijie",
    "山石网科": "hillstone", "山石": "hillstone", "启明星辰": "venustech",
    "绿盟": "nsfocus", "安恒信息": "anheng", "神州数码": "digitalchina",
    "迈普": "maipu", "统信 uos": "ulos", "uos": "ulos", "麒麟": "kylin",
}

_FALLBACK_OS_SLUGS = set("""
ios iosxe nxos vrp5 vrp8 comware5 comware7 rgos fortios junos sangfor_af
topos panos zxr10 eos gaia stoneos centos7 centos8 rhel7 rhel8 ubuntu1804
ubuntu2004 ubuntu2204 debian11 openeuler2203 kylinv10 ulos20
""".split())

_LINUX_VENDORS = {"centos", "rhel", "ubuntu", "debian", "openeuler", "kylin", "ulos"}
_LINUX_OS_PREFIX = ("centos", "rhel", "ubuntu", "debian", "openeuler", "kylin", "ulos")


def _load_slug_tables():
    """优先用同目录 db.py 的权威取值表；导入失败则用内置兜底。返回 (vendor, os, aliases, by_module)"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import db as dbmod
        return (set(dbmod.VENDOR_SLUGS.keys()),
                set(dbmod.OS_SLUGS.keys()),
                dict(dbmod._VENDOR_ALIASES),
                "db.py（权威表）")
    except Exception:
        return (_FALLBACK_VENDOR_SLUGS, _FALLBACK_OS_SLUGS, _FALLBACK_ALIASES,
                "内置兜底表（同目录未找到可用的 db.py）")


VENDOR_SLUGS, OS_SLUGS, VENDOR_ALIASES, SLUG_SRC = _load_slug_tables()

_LOWER_OS = {}
for _s in OS_SLUGS:
    _LOWER_OS[_s.lower()] = _s


def norm_vendor(value):
    """vendor 归一化（与 db.normalize_vendor 同语义）"""
    text = (value or "").strip()
    if not text:
        return ""
    key = text.lower()
    if key in VENDOR_SLUGS:
        return key
    if key in VENDOR_ALIASES:
        return VENDOR_ALIASES[key]
    return key.replace(" ", "")


def norm_os(value):
    """os_family 归一化（与 db.normalize_os 同语义）"""
    text = (value or "").strip()
    if not text:
        return ""
    key = text.lower()
    if key in _LOWER_OS:
        return _LOWER_OS[key]
    return key.replace(" ", "")


def norm_platform(value):
    return "linux" if (value or "").strip().lower() == "linux" else "network"


def norm_duration(value):
    key = (value or "").strip().lower()
    return key if key in ("temp", "perm", "both") else ""


# ---------------------------------------------------------------------------
# 输出小工具（只用 ASCII 边框与 [OK]/[!] 标记，避免中文控制台编码问题）
# ---------------------------------------------------------------------------
def rule(ch="="):
    print(ch * WIDTH)


def title(text):
    print("")
    rule("-")
    print(" %s" % text)
    rule("-")


def _pad(text, width):
    """按显示宽度粗略补齐（中文按 2 列计）"""
    text = str(text)
    w = 0
    for ch in text:
        w += 2 if ord(ch) > 0x2E80 else 1
    return text + " " * max(1, width - w)


def row(cols, widths):
    print("  " + "".join(_pad(c, w) for c, w in zip(cols, widths)).rstrip())


def ok(msg):
    print("  [OK] %s" % msg)


def warn(msg):
    print("  [!]  %s" % msg)


def info(msg):
    print("  ·  %s" % msg)


# ---------------------------------------------------------------------------
# 库读取
# ---------------------------------------------------------------------------
def readonly_uri(path):
    """拼 SQLite 只读 URI（手工转义空格 # ? % 等 URI 特殊字符）"""
    p = os.path.abspath(path).replace("\\", "/")
    for a, b in (("%", "%25"), (" ", "%20"), ("#", "%23"), ("?", "%3F")):
        p = p.replace(a, b)
    return "file:%s?mode=ro" % p


def find_db(argv):
    if len(argv) > 1 and argv[1].strip():
        return os.path.abspath(argv[1])
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, DB_FILENAME), os.path.join(os.getcwd(), DB_FILENAME)]
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


def open_ro(path):
    """只读打开；URI 打不开时退回普通连接（但绝不执行任何写语句）"""
    try:
        conn = sqlite3.connect(readonly_uri(path), uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1").fetchone()
        return conn, "只读 URI（mode=ro）"
    except Exception as exc:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn, "普通连接（只读 URI 失败：%s）" % exc


def table_names(conn):
    try:
        return set(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    except Exception:
        return set()


def count_of(conn, table):
    try:
        return conn.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
    except Exception:
        return None


def load_json_list(raw):
    if isinstance(raw, list):
        return raw
    try:
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def scalar(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchone()[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 各检查段
# ---------------------------------------------------------------------------
def section_counts(conn, tables):
    title("[1] 三库条目统计")
    entries = count_of(conn, "entries")
    trees = count_of(conn, "trouble_trees")
    errs = count_of(conn, "err_dict")
    unres = count_of(conn, "err_unresolved")
    hist = count_of(conn, "history")

    def show(label, n, table):
        if table not in tables:
            row([label, "--", "表缺失！"], [26, 7, 30])
        elif n is None:
            row([label, "--", "读取失败"], [26, 7, 30])
        else:
            row([label, "%d" % n, ""], [26, 7, 30])

    row(["表 / 数据集", "条目数", "备注"], [26, 7, 30])
    row(["-" * 24, "-" * 6, "-" * 26], [26, 7, 30])
    show("entries（命令库）", entries, "entries")
    show("trouble_trees（排查树）", trees, "trouble_trees")
    show("err_dict（报错字典）", errs, "err_dict")
    show("err_unresolved（收件箱）", unres, "err_unresolved")
    show("history（变更留痕）", hist, "history")

    if entries is not None and "entries" in tables:
        try:
            net = scalar(conn, "SELECT count(*) FROM entries WHERE platform = 'network'")
            lin = scalar(conn, "SELECT count(*) FROM entries WHERE platform = 'linux'")
            if net is None or lin is None:
                net = scalar(conn, "SELECT count(*) FROM entries "
                                   "WHERE platform IS NULL OR platform <> 'linux'")
                lin = scalar(conn, "SELECT count(*) FROM entries WHERE platform = 'linux'")
            ver = scalar(conn, "SELECT count(*) FROM entries WHERE verified = 1")
            fav = scalar(conn, "SELECT count(*) FROM entries WHERE favorite = 1")
            info("平台分布：network %s ｜ linux %s" % (net, lin))
            info("已验证 %s 条（%.1f%%）｜收藏 %s 条"
                 % (ver, 100.0 * (ver or 0) / max(1, entries), fav))
        except Exception as exc:
            warn("平台/验证统计失败：%s" % exc)
    if "err_unresolved" in tables and unres:
        p = scalar(conn, "SELECT count(*) FROM err_unresolved WHERE status = 'pending'")
        r = scalar(conn, "SELECT count(*) FROM err_unresolved WHERE status = 'resolved'")
        info("收件箱状态：pending %s ｜ resolved %s" % (p, r))
    missing = [t for t in ("entries", "trouble_trees", "err_dict", "err_unresolved")
               if t not in tables]
    if missing:
        warn("缺少表：%s（旧版库 / 半成品库；只读打开时无法自动建表，"
             "排查向导与报错诊断会不可用）" % "、".join(missing))


def section_dangling(conn, tables):
    title("[2] 悬空引用清单（三库交叉引用复验）")
    problems = []
    if "entries" not in tables:
        warn("没有 entries 表，跳过。")
        return
    uuids = set()
    try:
        uuids = set(r[0] for r in conn.execute("SELECT uuid FROM entries").fetchall())
    except Exception as exc:
        warn("读取 entries.uuid 失败：%s" % exc)
        return

    refd = set()
    if "err_dict" in tables:
        for r in conn.execute("SELECT err_id, cause, solution_steps FROM err_dict").fetchall():
            for i, st in enumerate(load_json_list(r["solution_steps"]), 1):
                ref = (st or {}).get("ref_entry_uuid")
                if not ref:
                    continue
                if ref in uuids:
                    refd.add(ref)
                else:
                    problems.append(("err_dict", r["err_id"],
                                     (r["cause"] or "")[:22], "步骤%d" % i, ref))
    if "trouble_trees" in tables:
        for r in conn.execute("SELECT tree_id, symptom, steps FROM trouble_trees").fetchall():
            for s in load_json_list(r["steps"]):
                ref = (s or {}).get("cmd_ref") or {}
                u = ref.get("uuid") if isinstance(ref, dict) else None
                if not u:
                    continue
                if u in uuids:
                    refd.add(u)
                else:
                    problems.append(("trouble_trees", r["tree_id"],
                                     (r["symptom"] or "")[:22], str(s.get("id")), u))
    if problems:
        warn("发现 %d 条悬空引用（引用的条目不存在）：" % len(problems))
        row(["#", "来源表", "记录", "定位", "引用不存在的 UUID"],
            [4, 16, 24, 10, 20])
        for i, p in enumerate(problems[:200], 1):
            row(["%02d" % i, p[0], p[2], p[3], (p[4] or "")[:8]], [4, 16, 24, 10, 20])
        if len(problems) > 200:
            info("（仅列出前 200 条）")
    else:
        ok("未发现悬空引用：三库中的 uuid 型引用全部命中 entries.uuid")

    # 引用强度（提醒：模糊匹配不校验）
    n_steps = n_uuid = n_per_vendor = n_fuzzy = 0
    if "trouble_trees" in tables:
        for r in conn.execute("SELECT steps FROM trouble_trees").fetchall():
            for s in load_json_list(r["steps"]):
                n_steps += 1
                ref = (s or {}).get("cmd_ref") or {}
                if not isinstance(ref, dict):
                    continue
                bv = ref.get("by_vendor")
                has_bv = (isinstance(bv, dict) and any(
                    isinstance(x, dict) and x.get("uuid") for x in bv.values()))
                if ref.get("uuid"):
                    n_uuid += 1
                elif has_bv:
                    # 按厂商登记的 uuid 硬引用（一棵树的 ref 被多厂商共享，
                    # 因此单厂商一个 uuid 不够，用 by_vendor 表达）
                    n_per_vendor += 1
                elif ref.get("vendor_category") or ref.get("category"):
                    n_fuzzy += 1
    if n_steps:
        info("引用强度：%d 个树步骤中，uuid 硬引用 %d 个 / 按厂商 uuid 引用 %d 个 / "
             "vendor_category 模糊匹配 %d 个"
             % (n_steps, n_uuid, n_per_vendor, n_fuzzy))
        if n_fuzzy and not (n_uuid or n_per_vendor):
            warn("全部依赖模糊匹配：条目改名、场景分类调整或同性条目被收藏，"
                 "排查向导会在无告警的情况下渲染出另一条命令")
        elif n_fuzzy:
            warn("仍有 %d 个步骤依赖模糊匹配（其余已按厂商 uuid 硬引用）" % n_fuzzy)
    info("被三库引用的条目：%d / %d（其余条目未被任何树或字典引用）"
         % (len(refd & uuids), len(uuids)))


def section_verified(conn, tables):
    title("[3] verified 覆盖率（按厂商分组）")
    if "entries" not in tables:
        warn("没有 entries 表，跳过。")
        return
    try:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(vendor,''),'(未填厂商)') v, count(*) n, "
            "SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END) ok "
            "FROM entries GROUP BY v ORDER BY n DESC, v").fetchall()
    except Exception as exc:
        warn("统计失败：%s" % exc)
        return
    row(["厂商 slug", "条目数", "已验证", "覆盖率", "进度"], [18, 8, 8, 9, 24])
    row(["-" * 16, "-" * 6, "-" * 6, "-" * 7, "-" * 20], [18, 8, 8, 9, 24])
    tot = tver = 0
    for r in rows:
        n, v = r["n"], int(r["ok"] or 0)
        tot += n
        tver += v
        pct = 100.0 * v / n if n else 0.0
        bar = "#" * int(round(pct / 5.0)) + "." * (20 - int(round(pct / 5.0)))
        row([r["v"], "%d" % n, "%d" % v, "%.1f%%" % pct, bar], [18, 8, 8, 9, 24])
    row(["-" * 16, "-" * 6, "-" * 6, "-" * 7, "-" * 20], [18, 8, 8, 9, 24])
    row(["合计", "%d" % tot, "%d" % tver,
         "%.1f%%" % (100.0 * tver / tot if tot else 0.0), ""], [18, 8, 8, 9, 24])
    if tot and tver == 0:
        info("全库 0 条已验证属正常（新库/种子库一律未验证，需真机验证后才变绿）。")


def section_slug(conn, tables):
    title("[4] vendor / os_family slug 混用清单")
    info("判定依据：%s" % SLUG_SRC)
    if "entries" not in tables:
        warn("没有 entries 表，跳过。")
        return
    bad = []
    counter = {}
    unknown = {}
    try:
        rows = conn.execute("SELECT uuid, title, vendor, os_family FROM entries").fetchall()
    except Exception as exc:
        warn("读取失败：%s" % exc)
        return
    for r in rows:
        v = r["vendor"] or ""
        o = r["os_family"] or ""
        counter[v] = counter.get(v, 0) + 1
        if v:
            nv = norm_vendor(v)
            if nv != v:
                bad.append(((r["title"] or "")[:26], r["uuid"][:8], "vendor", v, nv))
            elif v not in VENDOR_SLUGS:
                unknown[v] = unknown.get(v, 0) + 1
        if o:
            no = norm_os(o)
            if no != o:
                bad.append(((r["title"] or "")[:26], r["uuid"][:8], "os_family", o, no))

    # err_dict 的 vendor 也一并查（三库共用维度）
    if "err_dict" in tables:
        try:
            for r in conn.execute("SELECT err_id, vendor FROM err_dict").fetchall():
                v = r["vendor"] or ""
                if v and norm_vendor(v) != v:
                    bad.append((("err_dict:" + r["err_id"][:8]), "-", "err.vendor", v,
                                norm_vendor(v)))
        except Exception:
            pass

    if bad:
        warn("发现 %d 条非规范 slug（应为小写 slug 或已收录显示名）：" % len(bad))
        row(["记录", "UUID", "字段", "库内取值", "应为"], [28, 10, 12, 14, 14])
        for b in bad[:80]:
            row([b[0], b[1], b[2], b[3], b[4]], [28, 10, 12, 14, 14])
        if len(bad) > 80:
            info("（仅列出前 80 条）")
    else:
        ok("entries.vendor / entries.os_family / err_dict.vendor 全部为规范小写 slug，无混用")
    if unknown:
        warn("以下 vendor 取值不在规范表内（自加厂商？联动会失效）：%s"
             % "、".join("%s x%d" % (k, v) for k, v in sorted(unknown.items())))
    if counter:
        info("entries.vendor 取值：%s"
             % "，".join("%s x%d" % (k or "(空)", v)
                        for k, v in sorted(counter.items(), key=lambda x: -x[1])))


def section_duration(conn, tables):
    title("[5] duration 异常使用清单")
    if "entries" not in tables:
        warn("没有 entries 表，跳过。")
        return
    try:
        cols = set(r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall())
    except Exception:
        cols = set()
    if "duration" not in cols:
        warn("entries 表没有 duration 列（v1 老库未迁移）——跳过本项。")
        return
    if "platform" not in cols:
        warn("entries 表没有 platform 列（v1 老库未迁移）——按 device_type 兜底判定。")

    # 分布
    try:
        rows = conn.execute("SELECT platform, COALESCE(duration,'') d, count(*) n "
                            "FROM entries GROUP BY platform, d ORDER BY platform, d").fetchall()
        row(["platform", "duration", "条目数"], [16, 14, 10])
        for r in rows:
            row([str(r["platform"]), repr(r["d"])[1:-1] or "(空)", "%d" % r["n"]],
                [16, 14, 10])
    except Exception as exc:
        warn("分布统计失败：%s" % exc)

    # 异常：网络设备条目填了 duration
    bad_net = []
    try:
        sql = ("SELECT uuid, title, platform, duration, vendor FROM entries "
               "WHERE (platform IS NULL OR platform <> 'linux') "
               "AND duration IS NOT NULL AND TRIM(duration) <> ''")
        bad_net = conn.execute(sql).fetchall()
    except Exception:
        pass
    # 异常：linux 条目未填 duration
    bad_lin = []
    try:
        bad_lin = conn.execute(
            "SELECT uuid, title, platform, duration, category, vendor FROM entries "
            "WHERE platform = 'linux' AND (duration IS NULL OR TRIM(duration) = '')").fetchall()
    except Exception:
        pass
    # 异常：duration 取非法值
    bad_val = []
    try:
        bad_val = conn.execute(
            "SELECT uuid, title, duration FROM entries "
            "WHERE duration IS NOT NULL AND TRIM(duration) <> '' "
            "AND LOWER(TRIM(duration)) NOT IN ('temp','perm','both')").fetchall()
    except Exception:
        pass

    total = len(bad_net) + len(bad_lin) + len(bad_val)
    if not total:
        ok("未发现异常：网络设备条目 duration 一律留空，Linux 条目均有 temp/perm/both")
    else:
        warn("发现 %d 条异常：" % total)
        head = ["记录", "UUID", "问题"]
        row(head, [40, 10, 26])
        for r in bad_net[:60]:
            row([(r["title"] or "")[:38], r["uuid"][:8],
                 "网络设备条目不应有 duration=%r" % r["duration"]], [40, 10, 26])
        for r in bad_lin[:60]:
            row([(r["title"] or "")[:38], r["uuid"][:8], "Linux 条目缺 duration"],
                [40, 10, 26])
        for r in bad_val[:60]:
            row([(r["title"] or "")[:38], r["uuid"][:8],
                 "duration 非法取值 %r" % r["duration"]], [40, 10, 26])


def section_history(conn, tables):
    title("[6] history 表最近 10 条记录")
    if "history" not in tables:
        warn("没有 history 表，跳过。")
        return
    try:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT 10").fetchall()
    except Exception as exc:
        warn("读取失败：%s" % exc)
        return
    if not rows:
        info("history 表为空（还没发生过任何增删改）。")
    else:
        keys = set(rows[0].keys())
        has_tn = "table_name" in keys
        row(["id", "表名", "动作", "记录ID", "操作人", "时间"],
            [7, 15, 11, 10, 20, 21])
        row(["-" * 6, "-" * 14, "-" * 10, "-" * 9, "-" * 19, "-" * 20],
            [7, 15, 11, 10, 20, 21])
        for r in rows:
            tn = (r["table_name"] if has_tn and "table_name" in keys
                  else "(entries)") or "-"
            row(["%s" % r["id"], str(tn), str(r["action"] or "-"),
                 str(r["record_id"] or r["entry_uuid"] or "-")[:8],
                 str(r["operator"] or "-")[:18], str(r["ts"] or "-")],
                [7, 15, 11, 10, 20, 21])

    # 动作覆盖
    try:
        has_tn = "table_name" in set(
            r["name"] for r in conn.execute("PRAGMA table_info(history)").fetchall())
        sql = ("SELECT %s tn, action, count(*) n FROM history GROUP BY tn, action "
               "ORDER BY tn, action" % ("table_name" if has_tn else "'entries'"))
        rows = conn.execute(sql).fetchall()
        info("动作覆盖统计：")
        for r in rows:
            print("       %-16s %-10s %d" % (r["tn"] or "(空)", r["action"], r["n"]))
        acts = set(r["action"] for r in rows)
        need = {"create", "update", "delete", "verify", "unverify", "favorite", "import"}
        lack = sorted(need - acts)
        if lack:
            info("尚未出现过的动作（正常，取决于实际使用）：%s" % "、".join(lack))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    path = find_db(argv)

    rule("=")
    print(" NetToolBox 命令库自检报告  ·  check_db.py")
    rule("=")
    if not os.path.isfile(path):
        warn("找不到命令库文件：%s" % path)
        info("把本脚本放到 command_lib.db 同目录，或用 python check_db.py <库文件路径> 指定。")
        return 2

    size_kb = os.path.getsize(path) / 1024.0
    print(" 库文件  : %s" % path)
    print(" 文件大小: %.1f KB" % size_kb)
    try:
        conn, mode = open_ro(path)
    except Exception as exc:
        warn("无法打开命令库：%s" % exc)
        return 3
    print(" 打开模式: %s" % mode)
    print(" 检查时间: %s" % _now())

    try:
        uv = scalar(conn, "PRAGMA user_version")
        jm = scalar(conn, "PRAGMA journal_mode")
        print(" schema  : user_version=%s ｜ journal_mode=%s" % (uv, jm))
        if uv is not None and uv < 2:
            warn("schema 版本低于 2（v1 老库）：vendor/OS 可能仍是显示名，"
                 "且缺 trouble_trees / err_dict 表")
        if jm and str(jm).lower() == "wal":
            warn("journal_mode=WAL：在 U 盘 / FAT32 分区上会残留 -wal 文件，"
                 "建议用主程序打开一次会自动切回 DELETE")
    except Exception:
        pass

    tables = table_names(conn)
    print(" 数据表  : %s" % ("、".join(sorted(tables)) or "(无)"))

    issues = 0
    for fn in (section_counts, section_dangling, section_verified,
               section_slug, section_duration, section_history):
        try:
            fn(conn, tables)
        except Exception as exc:
            issues += 1
            warn("本段检查异常终止（已跳过，不影响其它检查）：%s: %s"
                 % (type(exc).__name__, exc))

    # 汇总
    print("")
    rule("=")
    if issues:
        print(" 结论：[!] 有 %d 段检查未能完成，请把上面的 [OK]/[!] 行一并截图留档。" % issues)
    else:
        print(" 结论：检查完成。以上 [OK] 为通过项，[!] 为需要人工确认的问题项。")
    print(" 说明：本脚本以只读方式打开数据库，不会修改任何数据。")
    rule("=")
    try:
        conn.close()
    except Exception:
        pass
    return 0


def _now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
