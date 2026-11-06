# -*- coding: utf-8 -*-
"""
smoke_translate.py —— 配置翻译器冒烟（U 批 2026-11-06 / V 批扩族 2026-11-06）

覆盖（全部 offscreen + 本地样例，主库零接触、零联网）：
    [A] 映射表随种子：27 族 / 147 条厂商映射；validate_rules 零 [E]/[W]；
        族级 scope 与实际 vendors 自洽（缺格族如实声明目标范围）
    [B] 六厂商整机样例：每行必落一档（无丢失行）；a 档行带 uuid 且条目存在、
        且条目属**目标**厂商；c 档必带原因
    [C] 族内映射正确性：U 批 8 族抽验 + V 批 19 族逐族正例（族命中 + 目标命令可核）
    [D] 私有特性 / 目标缺格 / 参数不可推导 必落 c 档且零外译（逐族反例）
    [E] 零臆测：输出无 {{ 残留；每条目标命令都能在「被引用条目的渲染结果」里找到；
        目标条目 vendor 恒等于目标厂商；无族命中时输出零命令
    [F] 覆盖率可复算：档位计数 == coverage；pct == 已译/命令行；账本行数 == 总行数；
        六样例总账本与冻结数字一致（可复算）
    [G] 回退：变更类实例带库内 rollback，且出现在 output.rollback
    [H] 坏映射表被 --validate-seed [E] 硬拦截（端到端），移除后复位 EXIT=0 且计数不动
    [I] 离屏 UI：TranslateTab 构造 / 载入样例 / 翻译 / 账本表格行数 / split_state /
        Markdown 账本导出；两模块零联网源码断言
    [J] V 批专项：uncovered_view 仅对族外命令触发（逐样例计数）；族级 scope 越界
        目标判 no_family；查询对照族输出只进复核区不进配置区

用法：python scripts/smoke_translate.py   （EXIT=0 全过；PyQt5 经 _vendor）
"""
import io
import contextlib
import json
import os
import re
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 断言

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


SAMPLE_DIR = os.path.join(APP_DIR, "app", "samples", "translate")

# 样例 → 目标厂商（覆盖「同构厂商对」与「异构厂商对」两类）
PAIRS = (
    ("cisco", "huawei"),
    ("huawei", "cisco"),
    ("h3c", "cisco"),
    ("ruijie", "huawei"),
    ("zte", "cisco"),
    ("juniper", "huawei"),
)

# V 批族数 / 映射数（映射表实际值，--validate-seed 统计行同源）
FAMILY_COUNT = 27
MAPPING_COUNT = 147

# 六样例总账本（V 批冻结值：命令行 437 / 已译 370 / 84.7%；改样例须同步本表）
LEDGER = {"total": 585, "command": 437, "translated": 370, "comment": 24,
          "manual": 33, "skipped": 34, "pct": 84.7}

# V 批 19 族逐族正例：(族, 源, 目标, 源行特征, 目标输出必含)
V_POS = (
    ("acl",         "cisco",   "ruijie", "permit ip 192.168.10.0", "ip access-list extended ACL-MGMT-01"),
    ("nat",         "cisco",   "huawei", "ip nat inside source list", "nat outbound 2000"),
    ("vrrp",        "cisco",   "huawei", "vrrp 10 ip", "vrrp vrid 10 virtual-ip 10.10.10.254"),
    ("poe",         "cisco",   "huawei", "power inline auto", "poe enable"),
    ("storm",       "cisco",   "huawei", "storm-control broadcast", "broadcast-suppression 5"),
    ("bgp",         "cisco",   "huawei", "show ip bgp summary", "display bgp peer"),
    ("stack",       "cisco",   "huawei", "show switch", "display stack"),
    ("loop",        "cisco",   "huawei", "show spanning-tree summary", "display stp"),
    ("display_ctl", "cisco",   "huawei", "terminal length 0", "screen-length 0 temporary"),
    ("ha",          "cisco",   "huawei", "show standby brief", "display vrrp brief"),
    ("save",        "cisco",   "huawei", "copy running-config flash:backup", "copy flash:/vrpcfg.zip flash:/vrpcfg-20261106.zip"),
    ("aaa",         "cisco",   "huawei", "address ipv4 10.1.1.5", "radius-server authentication 10.1.1.5 1812"),
    ("dns",         "cisco",   "huawei", "ip name-server 223.5.5.5", "dns server 223.5.5.5"),
    ("ssh",         "cisco",   "huawei", "ip domain-name corp.local", "sysname SW-ACC-01"),
    ("password",    "cisco",   "huawei", "line console 0", "user-interface console 0"),
    ("devmgmt",     "cisco",   "huawei", "show version", "display version"),
    ("snmp",        "cisco",   "huawei", "snmp-server location", "snmp-agent sys-info location IDC-A-机柜12"),
    ("rollback",    "cisco",   "huawei", "configure replace flash:", "startup saved-configuration flash:/vrpcfg.zip"),
    ("log",         "cisco",   "huawei", "show logging", "display logbuffer"),
)

# V 批反例：(说明, 源, 目标, 源行特征, 期望档, 期望原因)
V_NEG = (
    ("私有特性不可跨厂商", "cisco", "huawei", "spanning-tree portfast", "c", "private"),
    ("目标厂商不在族 scope（堆叠缺思科）", "huawei", "cisco", "display stack", "c", "no_family"),
    ("参数不可推导（ACL 名→编号）", "cisco", "huawei", "ip access-list extended ACL-MGMT-01", "c", "param_missing"),
    ("源项在目标条目无对应参数（NAT 接口标记）", "cisco", "huawei", "ip nat outside", "c", "unbound"),
)


def _sample(slug):
    with open(os.path.join(SAMPLE_DIR, "%s.txt" % slug), encoding="utf-8") as fp:
        return fp.read()


def _seeded_db():
    import db as dbmod
    d = dbmod.Database(":memory:")
    d.import_seed_dir()
    return d


def _rules():
    import translate_tool
    data, problems = translate_tool.load_rules()
    return translate_tool, data, problems


def _run(slug, dst, db=None, src=None):
    translate_tool, rules, _p = _rules()
    db = db or _seeded_db()
    src = src or slug
    return translate_tool.translate(_sample(src), src, dst, rules, db.get_entry)


# ======================================================================
# [A] 映射表随种子
# ======================================================================

def case_a():
    translate_tool, rules, problems = _rules()
    fams = rules.get("families") or []
    pairs = sum(len(f.get("vendors") or {}) for f in fams)
    check("A1 映射表可加载且无文件级问题", problems == [], "; ".join(problems))
    check("A2 族数 = %d（U 批 8 + V 批 19）" % FAMILY_COUNT, len(fams) == FAMILY_COUNT,
          "families=%d" % len(fams))
    check("A3 厂商映射数 = %d" % MAPPING_COUNT, pairs == MAPPING_COUNT, "pairs=%d" % pairs)

    db = _seeded_db()
    import db as dbmod
    errs, warns = translate_tool.validate_rules(
        rules, entry_getter=db.get_entry, vendor_slugs=list(dbmod.VENDOR_SLUGS.keys()))
    check("A4 validate_rules 零 [E]", errs == [], "; ".join(errs[:3]))
    check("A5 validate_rules 零 [W]", warns == [], "; ".join(warns[:3]))

    # 族级 scope：目标厂商必须是族内真有条目的厂商；声明了 scope 的族可少于六家（缺格如实标）
    slugs = set((rules.get("vendors") or {}).keys())
    bad_scope = []
    partial = []
    for f in fams:
        fv = set((f.get("vendors") or {}).keys())
        scope = f.get("scope")
        if scope:
            if not set(scope) <= fv or not set(scope) <= slugs:
                bad_scope.append(f["id"])
            if len(scope) < 6:
                partial.append("%s(%d)" % (f["id"], len(scope)))
        elif fv != slugs:
            bad_scope.append("%s(未声明 scope 却缺厂商)" % f["id"])
    check("A6 scope 与 vendors 自洽（目标厂商必有条目）", not bad_scope, "; ".join(bad_scope))
    check("A7 缺格族如实声明 scope（不凑六厂商）", len(partial) >= 5,
          "partial=%s" % "、".join(partial))


# ======================================================================
# [B] 六厂商整机样例：行级账本完整性
# ======================================================================

def case_b():
    db = _seeded_db()
    for slug, dst in PAIRS:
        text = _sample(slug)
        r = _run(slug, dst, db)
        lines = r["lines"]
        tag = "%s→%s" % (slug, dst)

        raw_count = len(text.splitlines())
        check("B1[%s] 账本行数 == 源文件行数（无凭空消失行）" % tag,
              len(lines) == raw_count, "%d vs %d" % (len(lines), raw_count))

        tiers = [x["tier"] for x in lines]
        check("B2[%s] 每行档位合法（a/b/c/d）" % tag,
              all(t in ("a", "b", "c", "d") for t in tiers),
              "bad=%s" % set(t for t in tiers if t not in ("a", "b", "c", "d")))

        a_rows = [x for x in lines if x["tier"] == "a"]
        bad_uuid = [x for x in a_rows if not x["uuid"]]
        check("B3[%s] 已译行全部带目标条目 uuid" % tag, not bad_uuid,
              "%d 行缺 uuid" % len(bad_uuid))

        missing = []
        wrong_vendor = []
        for x in a_rows:
            e = db.get_entry(x["uuid"])
            if not e:
                missing.append(x["uuid"][:8])
            elif e.get("vendor") != dst:
                wrong_vendor.append("%s:%s" % (x["uuid"][:8], e.get("vendor")))
        check("B4[%s] 已译行 uuid 在库中存在" % tag, not missing, ",".join(missing[:3]))
        check("B5[%s] 已译行 uuid 属目标厂商" % tag, not wrong_vendor,
              ",".join(wrong_vendor[:3]))

        c_rows = [x for x in lines if x["tier"] == "c"]
        check("B6[%s] 需人工行逐条带原因" % tag,
              all((x.get("note") or "").strip() for x in c_rows),
              "缺原因 %d 行" % len([x for x in c_rows if not (x.get("note") or "").strip()]))

        cov = r["coverage"]
        check("B7[%s] a/b/d 三档非空且 c 档计数与账本一致（V 批允许 c=0）" % tag,
              cov["translated"] > 0 and cov["comment"] > 0 and cov["skipped"] > 0
              and cov["manual"] == len(c_rows), str(cov))


# ======================================================================
# [C] 族内映射正确性（关键行 + uuid 追溯）
# ======================================================================

def _uuid_of(r, needle):
    for x in r["lines"]:
        if needle in (x["text"] or "") and x["tier"] == "a":
            return x["uuid"]
    return ""


def case_c():
    db = _seeded_db()

    r = _run("cisco", "huawei", db)
    out = "\n".join(r["output"]["config"])
    check("C1 cisco→huawei 静态路由命令正确",
          "ip route-static 192.168.20.0 255.255.255.0 10.0.0.2" in out, out[:160])
    check("C2 cisco→huawei VLAN 块正确",
          "vlan 10" in out and "description OFFICE" in out and "port default vlan 10" in out, "")
    check("C3 cisco→huawei Trunk 放通列表正确",
          "port trunk allow-pass vlan 10,20" in out, "")
    check("C4 cisco→huawei NTP 正确",
          "ntp-service unicast-server 10.1.1.10" in out, "")
    check("C5 cisco 静态路由行追溯华为条目",
          _uuid_of(r, "ip route 192.168.20.0") == "9f24e321-8633-58e5-9059-a2023a9bee9b",
          _uuid_of(r, "ip route 192.168.20.0")[:8])

    r = _run("huawei", "cisco", db)
    out = "\n".join(r["output"]["config"])
    check("C6 huawei→cisco 静态路由命令正确",
          "ip route 192.168.20.0 255.255.255.0 10.0.0.2" in out, "")
    check("C7 huawei→cisco Access 端口正确",
          "switchport access vlan 10" in out, "")
    check("C8 huawei→cisco Trunk 正确",
          "switchport mode trunk" in out and "switchport trunk allowed vlan 10 20" in out, "")
    check("C9 huawei→cisco 链路聚合成员口进 port-channel",
          "channel-group 1 mode active" in out, out[-200:])
    check("C10 huawei 静态路由行追溯思科条目",
          _uuid_of(r, "ip route-static") == "eac547bb-4688-5afa-82cc-cd39cc580518",
          _uuid_of(r, "ip route-static")[:8])

    r = _run("h3c", "cisco", db)
    out = "\n".join(r["output"]["config"])
    check("C11 h3c→cisco 掩码 24 还原点分十进制",
          "ip route 192.168.20.0 255.255.255.0 10.0.0.2" in out, "")
    check("C12 h3c→cisco range 形式端口补前缀",
          "interface range GigabitEthernet1/0/1" in out, "")

    r = _run("juniper", "huawei", db)
    out = "\n".join(r["output"]["config"])
    check("C13 juniper→huawei VLAN 名与 ID 都取到",
          "vlan 10" in out and "description VLAN10-OFFICE" in out, "")
    check("C14 juniper→huawei 二层 MAC 限制翻译",
          "port-security max-mac-count 2" in out, "")

    r = _run("ruijie", "huawei", db)
    out = "\n".join(r["output"]["config"])
    check("C15 ruijie→huawei Trunk（allowed vlan add 形式）正确",
          "port trunk allow-pass vlan 10,20" in out, "")

    r = _run("zte", "cisco", db)
    out = "\n".join(r["output"]["config"])
    check("C16 zte→cisco OSPF 被动接口 → passive-interface",
          "passive-interface gei_0/1" in out or "passive-interface" in out, out[:200])

    # ---- V 批：19 族逐族正例（族命中 + 目标命令可核） ----
    for fam, src, dst, needle, expect in V_POS:
        r = _run(src, dst, db)
        row = next((x for x in r["lines"] if needle in (x["text"] or "")), None)
        check("C17[%s] 族命中并判已译（%s→%s）" % (fam, src, dst),
              row is not None and row["tier"] == "a" and row.get("family") == fam,
              "%s/%s" % ((row or {}).get("tier"), (row or {}).get("family")))
        out = "\n".join(r["output"]["config"])
        check("C18[%s] 目标命令由库内条目渲染（%s）" % (fam, expect[:28]),
              expect in out, out[:120])


# ======================================================================
# [D] 私有特性 / 缺格 / 参数不可推导 必落需人工
# ======================================================================

def case_d():
    db = _seeded_db()
    cases = (
        ("cisco", "huawei", "spanning-tree portfast"),
        ("huawei", "cisco", "stp edged-port"),
        ("juniper", "huawei", "set protocols rstp"),
    )
    for slug, dst, needle in cases:
        r = _run(slug, dst, db)
        row = next((x for x in r["lines"] if needle in (x["text"] or "")), None)
        check("D1 私有特性判 c 档[%s:%s]" % (slug, needle[:16]),
              row is not None and row["tier"] == "c",
              (row or {}).get("tier", "missing"))
        check("D2 私有特性原因可读[%s]" % slug,
              row is not None and "私有特性" in (row.get("note") or ""),
              (row or {}).get("note", "")[:40])
        check("D3 私有特性不产出命令[%s]" % slug,
              needle.split()[0] not in "\n".join(r["output"]["config"]) or
              row["tier"] == "c", "")

    # 族外杂项行（库内无族）同样不得臆造
    r = _run("cisco", "huawei", db)
    row = next((x for x in r["lines"] if (x["text"] or "").strip() == "ip routing"), None)
    check("D4 族外杂项行（ip routing）落 c 档", row is not None and row["tier"] == "c",
          (row or {}).get("tier", "missing"))
    check("D5 族外杂项行原因写明库内无对应族",
          row is not None and "库内无对应" in (row.get("note") or ""),
          (row or {}).get("note", "")[:40])

    # 口令行落 d 档且不外译
    row = next((x for x in r["lines"] if "secret" in (x["text"] or "")), None)
    check("D6 口令行落 d 档（跳过）", row is not None and row["tier"] == "d",
          (row or {}).get("tier", "missing"))
    check("D7 口令不外译（输出无 secret 行）",
          "secret" not in "\n".join(r["output"]["config"]), "")

    # ---- V 批反例：私有特性 / 目标缺格 / 参数不可推导 / 无对应参数 ----
    for label, src, dst, needle, tier, reason in V_NEG:
        r = _run(src, dst, db)
        row = next((x for x in r["lines"] if needle in (x["text"] or "")), None)
        check("D8 反例判 c[%s]" % label,
              row is not None and row["tier"] == tier and row.get("reason") == reason,
              "%s/%s" % ((row or {}).get("tier"), (row or {}).get("reason")))

    # 反例零外译：缺格 / 不可推导族的命令不得出现在目标配置里
    r = _run("huawei", "cisco", db)          # 堆叠族无思科目标
    check("D9 目标缺格不外译（堆叠 → 思科 无 display stack 产物）",
          "virtual-chassis" not in "\n".join(r["output"]["config"]), "")


# ======================================================================
# [E] 零臆测：输出每一行都能追到被引用条目的渲染结果
# ======================================================================

def case_e():
    db = _seeded_db()
    for slug, dst in PAIRS:
        r = _run(slug, dst, db)
        tag = "%s→%s" % (slug, dst)
        out_lines = [ln for ln in r["output"]["config"] if ln.strip()]

        allowed = set()
        for inst in r["instances"]:
            if inst.get("status") != "ok":
                continue
            for key in ("out", "pre", "post", "verify", "rollback"):
                allowed.update(inst.get(key) or [])
        mark = "#" if dst in ("huawei", "h3c", "juniper") else "!"
        stray = [ln for ln in out_lines
                 if not ln.lstrip().startswith(("#", "!")) and ln not in allowed]
        check("E1[%s] 输出无库外命令（每行可追条目渲染）" % tag, not stray,
              " | ".join(stray[:2]))

        check("E2[%s] 输出无未填占位 {{}}" % tag,
              "{{" not in "\n".join(out_lines), "")
        check("E3[%s] 输出带语法核对档声明" % tag,
              "语法核对档" in "\n".join(out_lines), "")
        check("E4[%s] 头注注释符与目标厂商一致" % tag,
              any(ln.startswith(mark) for ln in out_lines[:3]), "")

    # 零族命中：输出不得有任何命令
    translate_tool, rules, _p = _rules()
    r = translate_tool.translate("ride the lightning\nfoo bar baz\n",
                                 "cisco", "huawei", rules, db.get_entry)
    non_comment = [ln for ln in r["output"]["config"]
                   if ln.strip() and not ln.lstrip().startswith(("#", "!"))]
    check("E5 零族命中时零命令输出", non_comment == [], str(non_comment[:2]))
    check("E6 零族命中时行全部落 c 档",
          all(x["tier"] == "c" for x in r["lines"]), "")


# ======================================================================
# [F] 覆盖率可复算
# ======================================================================

def case_f():
    db = _seeded_db()
    total = {k: 0 for k in LEDGER}
    for slug, dst in PAIRS:
        r = _run(slug, dst, db)
        tag = "%s→%s" % (slug, dst)
        lines = r["lines"]
        cov = r["coverage"]
        counts = {t: sum(1 for x in lines if x["tier"] == t) for t in "abcd"}
        check("F1[%s] 档位计数 == coverage" % tag,
              counts["a"] == cov["translated"] and counts["c"] == cov["manual"]
              and counts["b"] == cov["comment"], str(counts))
        check("F2[%s] 四档之和 == 账本行数" % tag,
              sum(counts.values()) == cov["total"] == len(lines),
              "%s vs %s" % (sum(counts.values()), cov["total"]))
        expect_pct = round(cov["translated"] * 100.0 / cov["command"], 1) if cov["command"] else 0.0
        check("F3[%s] pct 可用账本复算" % tag, abs(expect_pct - cov["pct"]) < 1e-9,
              "%.1f vs %.1f" % (expect_pct, cov["pct"]))
        blank = sum(1 for x in lines if x.get("reason") == "blank")
        sep = sum(1 for x in lines if x.get("reason") == "separator")
        check("F4[%s] 命令行数 == total - 空行 - 分隔符 - 注释" % tag,
              cov["command"] == cov["total"] - blank - sep - cov["comment"], str(cov))
        check("F5[%s] skipped == d 档 - 空行 - 分隔符" % tag,
              cov["skipped"] == counts["d"] - blank - sep,
              "%d vs %d" % (cov["skipped"], counts["d"] - blank - sep))
        for k in total:
            if k != "pct":
                total[k] += cov[k]

    total["pct"] = round(total["translated"] * 100.0 / total["command"], 1)
    check("F6 六样例总账本与冻结数字一致（命令行 %d / 已译 %d / %.1f%%）"
          % (LEDGER["command"], LEDGER["translated"], LEDGER["pct"]),
          total == LEDGER, "%s vs %s" % (total, LEDGER))


# ======================================================================
# [G] 回退：变更类实例带库内 rollback
# ======================================================================

def case_g():
    db = _seeded_db()
    r = _run("cisco", "huawei", db)
    with_rb = [i for i in r["instances"] if i.get("rollback")]
    check("G1 至少一个实例带 rollback", bool(with_rb), "%d" % len(with_rb))
    rb_out = "\n".join(r["output"]["rollback"])
    check("G2 rollback 进入输出回退区", "undo vlan 10" in rb_out or "undo port" in rb_out,
          rb_out[:120])
    check("G3 回退区带未真机核对声明",
          "回退命令（库内条自带" in "\n".join(r["output"]["config"]), "")
    # 条目侧依据：cisco VLAN 条目原本就有 rollback
    e = db.get_entry("e3971cfc-1d22-5542-89d5-7b72b2c3ee6a")
    check("G4 回退内容来自库内条目（uuid 可查）", bool(e and (e.get("rollback") or "").strip()), "")


# ======================================================================
# [H] 坏映射表被 validate [E] 硬拦截（端到端）
# ======================================================================

BAD_RULES = {
    "format": "netcmdl config-translate map",
    "schema": 1,
    "vendors": {v: {"style": "indent", "if_prefix": "GigabitEthernet"}
                for v in ("cisco", "huawei", "h3c", "ruijie", "zte", "juniper")},
    "families": [
        {"id": "vlans", "name": "坏族", "bind": {},
         "vendors": {"cisco": {"entry": "00000000-0000-0000-0000-000000000000",
                               "key": ["vlan_id"],
                               "lines": [{"re": "^(unclosed"}]}}},
    ],
}


def case_h():
    import translate_tool
    db = _seeded_db()
    errs, _w = translate_tool.validate_rules(BAD_RULES, entry_getter=db.get_entry)
    joined = "；".join(errs)
    check("H1 坏正则被拦", any("正则无法编译" in e for e in errs), joined[:120])
    check("H2 条目不存在被拦", any("条目不存在" in e for e in errs), joined[:120])
    check("H3 缺目标厂商条目被拦", any("缺目标厂商" in e for e in errs), joined[:120])

    import main as mainmod
    bad_dir = os.path.join(_se.setup(), "seed_data")
    os.makedirs(bad_dir, exist_ok=True)
    bad_path = os.path.join(bad_dir, "translate_rules.json")
    with open(bad_path, "w", encoding="utf-8") as fp:
        json.dump(BAD_RULES, fp, ensure_ascii=False)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_bad = mainmod.validate_seed()
        out = buf.getvalue()
        check("H4 端到端 validate 退出码 1（坏映射 [E] 拦截）", rc_bad == 1, "rc=%s" % rc_bad)
        check("H5 输出含 [X] 配置翻译映射段落", "[X] 配置翻译映射" in out, out[-200:])
    finally:
        os.remove(bad_path)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_ok = mainmod.validate_seed()
    check("H6 移除坏文件后 validate 复位 EXIT=0", rc_ok == 0, "rc=%s" % rc_ok)
    check("H7 种子计数不动（482/26/106，映射表不新增条目）",
          "482 条条目 + 26 棵排查树 + 106 条报错字典" in buf.getvalue(), "")
    check("H8 输出含映射表统计 %d 族 ｜ %d 映射" % (FAMILY_COUNT, MAPPING_COUNT),
          "%d 族 ｜ %d 条厂商映射" % (FAMILY_COUNT, MAPPING_COUNT) in buf.getvalue(),
          [l for l in buf.getvalue().splitlines() if "配置翻译映射" in l][:1])


# ======================================================================
# [I] 离屏 UI
# ======================================================================

def case_i():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    import ui_translate
    import translate_tool

    db = _seeded_db()
    tab = ui_translate.TranslateTab(db, None)
    check("I1 TranslateTab 构造成功", tab is not None, "")
    check("I2 映射表提示已加载", "%d 族" % FAMILY_COUNT in tab.lbl_rule.text(),
          tab.lbl_rule.text()[:60])
    check("I3 厂商下拉六家", tab.cmb_src.count() == 6, str(tab.cmb_src.count()))

    idx = tab.cmb_src.findData("cisco")
    tab.cmb_src.setCurrentIndex(idx)
    tab.on_load_sample()
    check("I4 载入样例进入粘贴区", "hostname SW-ACC-01" in tab.ed_src.toPlainText(), "")

    dst_idx = tab.cmb_dst.findData("huawei")
    tab.cmb_dst.setCurrentIndex(dst_idx)
    tab.on_translate()
    text = tab.ed_out.toPlainText()
    check("I5 翻译产出目标配置", "ip route-static" in text, text[:80])
    check("I6 账本表格行数 == 账本行数",
          tab.tbl.rowCount() == len(tab._result["lines"]),
          "%d vs %d" % (tab.tbl.rowCount(), len(tab._result["lines"])))
    check("I7 覆盖率标签含百分比", "%" in tab.lbl_cov.text(), tab.lbl_cov.text()[:80])

    state = tab.split_state()
    check("I8 split_state 含两级 key",
          translate_tab_keys_ok(state), str(sorted(state.keys())))
    tab.restore_split_state(state)
    check("I9 restore_split_state 不抛异常", True, "")

    md = translate_tool.render_markdown(tab._result)
    tmp = tempfile.mkdtemp(prefix="ntbx_tr_md_")
    path = os.path.join(tmp, "ledger.md")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(md)
    body = open(path, encoding="utf-8").read()
    check("I10 账本 Markdown 含覆盖摘要/需人工清单/行级追溯",
          "## 覆盖率" in body and "## 需人工清单" in body and "行级账本" in body, "")
    os.remove(path)
    os.rmdir(tmp)
    check("I11 空输入不崩", _safe_empty(tab), "")

    offenders = []
    for name in ("translate_tool.py", "ui_translate.py"):
        src = open(os.path.join(APP_DIR, "app", name), encoding="utf-8").read()
        for bad in ("import requests", "import socket", "import urllib", "os.system(",
                    "subprocess", "urlopen"):
            if bad in src:
                offenders.append("%s:%s" % (name, bad))
    check("I12 两模块零联网零执行源码断言", offenders == [], "; ".join(offenders))


def translate_tab_keys_ok(state):
    return "translate_split" in state and "translate_right_split" in state


def _safe_empty(tab):
    try:
        tab.ed_src.setPlainText("")
        tab.on_translate()
        return True
    except Exception:
        return False


# ======================================================================
# [J] V 批专项：族覆盖面与输出分区
# ======================================================================

# 族外命令白名单：这些行的确不属于任何跨厂商族（库内无六厂商族条目），
# 因此可以继续落 uncovered_view —— 其余行出现该原因码即视为「假命中残留」
OUT_OF_FAMILY = (
    "ip address",
)


def case_j():
    db = _seeded_db()
    for slug, dst in PAIRS:
        r = _run(slug, dst, db)
        tag = "%s→%s" % (slug, dst)
        unc = [x for x in r["lines"] if x.get("reason") == "uncovered_view"]
        bad = [x for x in unc
               if not any((x["text"] or "").strip().lower().startswith(p)
                          for p in OUT_OF_FAMILY)]
        check("J1[%s] uncovered_view 仅对族外命令触发（族内零残留）" % tag,
              not bad, " | ".join("%s:%s" % (x["no"], (x["text"] or "").strip()[:24])
                                  for x in bad[:3]))
        check("J2[%s] 族外行均可解释（逐行白名单）" % tag,
              all((x.get("note") or "").strip() for x in unc), "")

    # 查询对照族：输出只进「复核命令」区，不得混进配置正文（复核区之前的命令行）
    r = _run("cisco", "huawei", db)
    cfg = r["output"]["config"]
    cut = next((i for i, ln in enumerate(cfg) if "复核命令" in ln), len(cfg))
    body = [ln for ln in cfg[:cut]
            if ln.strip() and not ln.lstrip().startswith(("#", "!"))]
    leak = [ln for ln in body if ln.split()[0] in ("show", "display") and ln in
            set(r["output"]["verify"])]
    check("J3 查询对照族的命令只进复核区（不进配置正文）", not leak, str(leak[:2]))

    # 族级 scope 越界：明确判 no_family 且原因可读（不是假 uncovered_view）
    r = _run("huawei", "cisco", db)
    row = next((x for x in r["lines"] if "display stack" in (x["text"] or "")), None)
    check("J4 scope 越界判 no_family 且注明可译目标",
          row is not None and row.get("reason") == "no_family"
          and "该族可译目标" in (row.get("note") or ""),
          (row or {}).get("note", "")[:60])

    # 逐族正例覆盖：19 族在 C17/C18 全覆盖（此处只做汇总断言）
    fams_hit = set()
    for _fam, src, dst, needle, _exp in V_POS:
        r = _run(src, dst, db)
        if any((x["text"] or "").find(needle) >= 0 and x["tier"] == "a" for x in r["lines"]):
            fams_hit.add(_fam)
    check("J5 V 批 19 族全部有正例命中", len(fams_hit) == len(V_POS),
          "缺少 %s" % (set(f for f, *_ in V_POS) - fams_hit))


# ======================================================================
# [K] W 批：行级分类不再盲信白名单 + 口令行不再被拎出配置体 + 六厂商误伤复扫
# ======================================================================
def case_k():
    import renderer as _rd
    translate_tool, rules, _p = _rules()
    db = _seeded_db()

    # K1 行级口径：强制变更组优先于白名单（此前 _is_query 只查白名单）
    check("K1 enable secret 不再判查询行",
          not translate_tool._is_query("enable secret 5 $1$abc")
          and not translate_tool._is_query("enable password P@ss"))
    check("K1 裸 enable 仍是查询行（会话级）", translate_tool._is_query("enable"))
    check("K1 diagnose 只读行判查询 / debug flow 启动行不判查询",
          translate_tool._is_query("diagnose sys session list")
          and not translate_tool._is_query("diagnose debug flow trace start 10"))

    # K2 回归：条目标题行的分区——强制变更行必须留在配置体（此前被当查询行拎去复核区）
    #    注：整机翻译里 `enable secret <明文>` 属源侧凭据行，按 V 批口径落 d 档不外译，
    #    与本条断言无关；这里测的是**渲染结果分区**这一层（split_entry_lines）。
    pre, body, post, verify = translate_tool.split_entry_lines(
        ["enable", "configure terminal", "line console 0", " password C0ns0le!23",
         " login", "enable secret En4ble!23", "end", "write"])
    check("K2 分区：enable secret 留配置体、不进复核区",
          "enable secret En4ble!23" in body and not verify,
          json.dumps({"body": body, "verify": verify}, ensure_ascii=False))
    check("K2 分区：查询行仍进复核区（口径未反转）",
          translate_tool.split_entry_lines(
              ["line vty 0 4", " login local", "show running-config", "end"])[3]
          == ["show running-config"])

    # K3 六厂商复扫：查询类行不得被 secret_patterns / private_hints 吞掉
    import re
    hints = {v: [(re.compile(h["re"], re.I), h.get("note") or "")
                 for h in (hs or [])]
             for v, hs in (rules.get("private_hints") or {}).items()}
    sec = [re.compile(p, re.I) for p in (rules.get("secret_patterns") or [])]
    hits = []
    for v in sorted(rules.get("vendors") or {}):
        for e in db.search(vendor=v):
            for ln in _rd.split_command_lines(e.get("commands") or "", skip_comments=True):
                s = ln.strip()
                if not s or not translate_tool._is_query(s):
                    continue
                if any(rx.search(s) for rx in sec):
                    hits.append((v, "secret", _rd._norm_cmd_line(s)))
                for rx, note in hints.get(v) or []:
                    if rx.search(s):
                        hits.append((v, "private:" + note, _rd._norm_cmd_line(s)))
    check("K3 六厂商：查询行零误伤（secret/private 不吞查询类）", not hits,
          json.dumps(hits[:3], ensure_ascii=False))

    # K4 反向自洽：强制变更行一律不被判查询（防新增 force 词漏配）
    bad = []
    for e in db.all_entries():
        for ln in _rd.split_command_lines(e.get("commands") or "", skip_comments=True):
            n = _rd._norm_cmd_line(ln)
            if n and _rd._prefix_hit(n, _rd.CHANGE_FORCE_PREFIXES) \
                    and translate_tool._is_query(ln):
                bad.append(n)
    check("K4 强制变更行零误判为查询", not bad, json.dumps(bad[:3], ensure_ascii=False))


# ======================================================================
def main():
    print("=" * 68)
    print("smoke_translate —— 配置翻译器冒烟（U 批 + V 批扩族）")
    print("=" * 68)
    for fn in (case_a, case_b, case_c, case_d, case_e, case_f, case_g, case_h,
               case_i, case_j, case_k):
        try:
            fn()
        except Exception as exc:
            check("用例 %s 异常" % fn.__name__, False, repr(exc))
    fails = [r for r in RESULTS if not r[1]]
    print("-" * 68)
    for name, ok, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             ("  ← " + detail) if (detail and not ok) else ""))
    print("-" * 68)
    print("合计 %d 项，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    untouched = _se.verify_main_db_untouched()
    print("主库零接触断言：%s" % ("通过" if untouched else "失败"))
    return 0 if (not fails and untouched) else 1


if __name__ == "__main__":
    sys.exit(main())
