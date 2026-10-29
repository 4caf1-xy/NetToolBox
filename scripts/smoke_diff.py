# -*- coding: utf-8 -*-
"""
smoke_diff.py —— Before/After 变更对比（H 批）冒烟（2026-10-14 新增）

覆盖八案（全部 offscreen + 本地样本，主库零接触、零联网）：
    [A] 规则随种子：load_rules 4 类 / 17 变体；validate_rules 零 [E]/[W]
    [B] 华为接口真实格式 diff：四态齐全；uptime/时间戳/计数器行不产生假变化
    [C] 思科接口 + VLAN 集合增删 + OSPF（华为/思科 brief）+ 路由（华为/思科/Linux）
        —— 下一跳变更必须报 ⚠
    [D] 变体抽验 ≥15 命中（沿用 E 批纪律，实际 17 变体逐个跑样本）
    [E] 坏提取规则被 validate [E] 硬拦截（临时 seed 目录同名文件优先级注入，
        端到端跑 main.validate_seed）；恢复后 validate 复位 EXIT=0
    [F] 降级行级 diff：无规则命中/空输入/规则命中零提取 均不崩；
        仅噪声行差异 → 判定一致
    [G] HTML 导出：token 同源 / 零 script·外链·url() / A4 / 四态徽章 /
        行级降级导出可用
    [H] 离屏 UI：DiffTab 构造/split_state/对比/表格回填/交换；操作单旁挂按钮
        main=None 安全提示；模块零联网源码断言

用法：python scripts/smoke_diff.py   （EXIT=0 全过；PyQt5 经 _vendor）
"""
import os
import sys
import json
import shutil
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


# ======================================================================
# 真实格式样本（公知回显格式；键入样本与噪声行按真机回显形态构造）
# ======================================================================
HW_IF_BEFORE = """<HUAWEI>display interface GigabitEthernet0/0/1
GigabitEthernet0/0/1 current state : UP
Line protocol current state : UP
Description: CORE-UPLINK
Route Port,The Maximum Frame Length is 9216
Internet Address is 10.255.0.1/30
Last physical up time   : 2026-10-13 20:11:32
Current system time: 2026-10-14 09:00:00
    Last 300 seconds input rate 32 bits/sec, 8 packets/sec
    120 packets input, 15330 bytes
    98 packets output, 12480 bytes
    Input:  0 bytes, 0 packets
    Output: 0 unicasts, 0 broadcasts
<HUAWEI>"""

HW_IF_AFTER = """<HUAWEI>display interface GigabitEthernet0/0/1
GigabitEthernet0/0/1 current state : UP
Line protocol current state : UP
Description: CORE-UPLINK
Route Port,The Maximum Frame Length is 9216
Internet Address is 10.255.0.1/30
Last physical up time   : 2026-10-14 08:55:01
Current system time: 2026-10-14 09:30:00
    Last 300 seconds input rate 128 bits/sec, 20 packets/sec
    480 packets input, 61320 bytes
    96 packets output, 12200 bytes
    Input:  0 bytes, 0 packets
    Output: 0 unicasts, 0 broadcasts
<HUAWEI>"""

CISCO_IF_BEFORE = """Switch#show interfaces GigabitEthernet0/1
GigabitEthernet0/1 is up, line protocol is up (connected)
  Hardware is Gigabit Ethernet, address is 0011.2233.4455
  Internet address is 10.255.0.2/30
  MTU 1500 bytes, BW 1000000 Kbit, DLY 10 usec
     5 minute input rate 100 bits/sec, 2 packets/sec
     5 minute output rate 88 bits/sec, 1 packets/sec
     2345 packets input, 234567 bytes
     1987 packets output, 187654 bytes
Switch#"""

CISCO_IF_AFTER = """Switch#show interfaces GigabitEthernet0/1
GigabitEthernet0/1 is down, line protocol is down (notconnect)
  Hardware is Gigabit Ethernet, address is 0011.2233.4455
  Internet address is 10.255.0.2/30
  MTU 1500 bytes, BW 1000000 Kbit, DLY 10 usec
     5 minute input rate 0 bits/sec, 0 packets/sec
     5 minute output rate 0 bits/sec, 0 packets/sec
     2345 packets input, 234567 bytes
     1987 packets output, 187654 bytes
Switch#"""

HW_VLAN_BEFORE = """<SW1>display vlan
VID  Type    Ports
--------------------------------------------------------------------------------
 1   common  UT:GE0/0/1(U)      GE0/0/2(U)
 10  common  UT:GE0/0/3(U)
 20  common  UT:GE0/0/4(U)
<SW1>"""

HW_VLAN_AFTER = """<SW1>display vlan
VID  Type    Ports
--------------------------------------------------------------------------------
 1   common  UT:GE0/0/1(U)      GE0/0/2(U)
 20  common  UT:GE0/0/4(U)
 30  common  UT:GE0/0/5(U)
<SW1>"""

CISCO_VLAN_BEFORE = """SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Gi0/1, Gi0/2
10   OFFICE                           active    Fa0/5
20   VOICE                            active
SW1#"""

CISCO_VLAN_AFTER = """SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Gi0/1, Gi0/2
10   OFFICE                           active    Fa0/5
30   GUEST                            active
SW1#"""

HW_OSPF_BEFORE = """<R1>display ospf peer brief

	 OSPF Process 1 with Router ID (2.2.2.2)
		   Peer Brief Information
 Area Id         Interface       Neighbor id     State
 0.0.0.0         GE0/0/0         1.1.1.1         Full
 0.0.0.0         GE0/0/1         3.3.3.3         Exchange
<R1>"""

HW_OSPF_AFTER = """<R1>display ospf peer brief

	 OSPF Process 1 with Router ID (2.2.2.2)
		   Peer Brief Information
 Area Id         Interface       Neighbor id     State
 0.0.0.0         GE0/0/0         1.1.1.1         Full
 0.0.0.0         GE0/0/1         3.3.3.3         Full
<R1>"""

CISCO_OSPF_BEFORE = """R1#show ip ospf neighbor
Neighbor ID     Pri   State           Dead Time   Address         Interface
2.2.2.2           1   FULL/DR         00:00:35    10.1.1.2        GigabitEthernet0/0
3.3.3.3           1   INIT/ -         00:00:31    10.2.1.2        Serial0/0/0
R1#"""

CISCO_OSPF_AFTER = """R1#show ip ospf neighbor
Neighbor ID     Pri   State           Dead Time   Address         Interface
2.2.2.2           1   FULL/DR         00:00:35    10.1.1.2        GigabitEthernet0/0
3.3.3.3           1   FULL/ -         00:00:31    10.2.1.2        Serial0/0/0
R1#"""

HW_ROUTE_BEFORE = """<R1>display ip routing-table
Destinations : 12       Routes : 12

Destination/Mask    Proto   Pre  Cost     Flags NextHop         Interface
       10.1.1.0/24  Direct  0    0           D  10.1.1.1        GigabitEthernet0/0/0
       20.1.1.0/24  Static  60   0          RD  10.1.1.2        GigabitEthernet0/0/0
       30.1.1.0/24  OSPF    10   2           D  10.1.1.2        GigabitEthernet0/0/0
<R1>"""

HW_ROUTE_AFTER = """<R1>display ip routing-table
Destinations : 12       Routes : 12

Destination/Mask    Proto   Pre  Cost     Flags NextHop         Interface
       10.1.1.0/24  Direct  0    0           D  10.1.1.1        GigabitEthernet0/0/0
       20.1.1.0/24  Static  60   0          RD  10.1.1.9        GigabitEthernet0/0/0
<R1>"""

CISCO_ROUTE_BEFORE = """R1#show ip route
Codes: L - local, C - connected, S - static
Gateway of last resort is 10.255.0.1 to network 0.0.0.0
C       10.1.1.0/24 is directly connected, GigabitEthernet0/0
S     192.168.20.0/24 [1/0] via 10.1.1.2
O    20.1.1.0/24 [110/2] via 10.1.1.2, 00:05:01, GigabitEthernet0/0
R1#"""

CISCO_ROUTE_AFTER = """R1#show ip route
Codes: L - local, C - connected, S - static
Gateway of last resort is 10.255.0.1 to network 0.0.0.0
C       10.1.1.0/24 is directly connected, GigabitEthernet0/0
S     192.168.20.0/24 [1/0] via 10.1.1.9
S*   0.0.0.0/0 [1/0] via 10.255.0.1
R1#"""

LINUX_ROUTE_BEFORE = """[ops@host ~]$ ip route show
default via 192.168.1.1 dev ens192 proto static metric 100
10.113.66.0/23 dev ens192 proto kernel scope link src 10.113.67.14
[ops@host ~]$"""

LINUX_ROUTE_AFTER = """[ops@host ~]$ ip route show
default via 192.168.1.254 dev ens192 proto static metric 100
10.113.66.0/23 dev ens192 proto kernel scope link src 10.113.67.14
[ops@host ~]$"""


# ======================================================================
# [A] 规则随种子
# ======================================================================
def case_a():
    import diff_tool
    data, problems = diff_tool.load_rules()
    check("A1 规则文件载入无文件级问题", problems == [], "; ".join(problems))
    rules = data.get("diff_rules") or []
    ids = [r.get("rule_id") for r in rules]
    check("A2 四类规则（接口/VLAN/OSPF/路由）",
          sorted(ids) == ["if_status", "ospf_peer", "route_table", "vlan_table"], str(ids))
    n_var = sum(len(r.get("variants") or []) for r in rules)
    check("A3 厂商变体 17 个", n_var == 17, str(n_var))
    errs, warns = diff_tool.validate_rules(data)
    check("A4 validate_rules 零 [E]", errs == [], "; ".join(errs[:3]))
    check("A5 validate_rules 零 [W]", warns == [], "; ".join(warns[:3]))
    # 语义齐全：kv 三类 + set 一类
    sem = {r["rule_id"]: r.get("semantics") for r in rules}
    check("A6 语义标注（kv=键值 / set=集合）",
          sem.get("if_status") == "kv" and sem.get("ospf_peer") == "kv"
          and sem.get("route_table") == "kv" and sem.get("vlan_table") == "set", str(sem))


# ======================================================================
# [B] 华为接口真实格式 diff + 噪声不产生假变化
# ======================================================================
def case_b():
    import diff_tool
    r = diff_tool.run_compare(HW_IF_BEFORE, HW_IF_AFTER)
    check("B1 规则命中（if_status/huawei）",
          r["mode"] == "rule" and r["rule_id"] == "if_status"
          and r["variant_used"] == "huawei",
          "%s/%s/%s" % (r["mode"], r["rule_id"], r["variant_used"]))
    by_key = {it["key"]: it for it in r["items"]}
    check("B2 提取只有接口键（无 Description/Internet Address 等混入）",
          set(by_key) == {"GigabitEthernet0/0/1"}, str(sorted(by_key)))
    st = by_key.get("GigabitEthernet0/0/1", {}).get("state")
    check("B3 状态不变判 same（速率/计数器/时间戳变化被忽略）", st == "same",
          str(by_key.get("GigabitEthernet0/0/1")))
    check("B4 汇总 clean", r["level"] == "clean" and r["summary"]["same"] >= 1,
          str(r["summary"]))
    check("B5 噪声行有过滤计数", r["noise_dropped"] >= 4, str(r["noise_dropped"]))

    # 人为制造 up→down：必须报 ⚠ 变化，且值是 before→after
    after_down = HW_IF_AFTER.replace("GigabitEthernet0/0/1 current state : UP",
                                     "GigabitEthernet0/0/1 current state : DOWN")
    r2 = diff_tool.run_compare(HW_IF_BEFORE, after_down)
    st2 = {it["key"]: it for it in r2["items"]}["GigabitEthernet0/0/1"]
    check("B6 up→down 报 ⚠ 变化", st2["state"] == "changed"
          and st2["before"] == "UP" and st2["after"] == "DOWN", str(st2))
    check("B7 汇总 changed", r2["level"] == "changed", str(r2["level"]))


# ======================================================================
# [C] 思科/华为/Linux 各类真实格式
# ======================================================================
def case_c():
    import diff_tool

    # 思科接口：up→down（含 line protocol）
    r = diff_tool.run_compare(CISCO_IF_BEFORE, CISCO_IF_AFTER)
    it = {i["key"]: i for i in r["items"]}.get("GigabitEthernet0/1", {})
    check("C1 思科接口 up→down ⚠（值含 line protocol）",
          r["rule_id"] == "if_status" and it.get("state") == "changed"
          and "down" in str(it.get("after")), "%s/%s" % (r["rule_id"], it))

    # 华为 VLAN 集合：10 消失 / 30 新增 / 1、20 不变
    r = diff_tool.run_compare(HW_VLAN_BEFORE, HW_VLAN_AFTER)
    check("C2 华为 VLAN 集合语义四态",
          r["rule_id"] == "vlan_table" and r["summary"] ==
          {"same": 2, "changed": 0, "added": 1, "removed": 1}, str(r["summary"]))

    # 思科 VLAN 集合：20 消失 / 30 新增
    r = diff_tool.run_compare(CISCO_VLAN_BEFORE, CISCO_VLAN_AFTER)
    check("C3 思科 VLAN 集合语义四态",
          r["rule_id"] == "vlan_table" and r["summary"] ==
          {"same": 2, "changed": 0, "added": 1, "removed": 1}, str(r["summary"]))

    # OSPF：华为 brief 邻居 Exchange→Full（收敛 ⚠）
    r = diff_tool.run_compare(HW_OSPF_BEFORE, HW_OSPF_AFTER)
    it = {i["key"]: i for i in r["items"]}.get("3.3.3.3", {})
    check("C4 华为 OSPF brief Exchange→Full ⚠",
          r["rule_id"] == "ospf_peer" and it.get("state") == "changed"
          and it.get("before") == "Exchange" and it.get("after") == "Full", str(it))

    # OSPF：思科 INIT→FULL
    r = diff_tool.run_compare(CISCO_OSPF_BEFORE, CISCO_OSPF_AFTER)
    it = {i["key"]: i for i in r["items"]}.get("3.3.3.3", {})
    check("C5 思科 OSPF INIT→FULL ⚠（Dead Time 噪声不干扰）",
          r["rule_id"] == "ospf_peer" and it.get("state") == "changed"
          and it.get("before") == "INIT" and it.get("after") == "FULL", str(it))

    # 路由：华为 下一跳变化 ⚠ + 30.1.1.0/24 消失
    r = diff_tool.run_compare(HW_ROUTE_BEFORE, HW_ROUTE_AFTER)
    by_key = {i["key"]: i for i in r["items"]}
    check("C6 华为路由 下一跳变化 ⚠（20.1.1.0/24）",
          by_key.get("20.1.1.0/24", {}).get("state") == "changed"
          and "10.1.1.2" in str(by_key.get("20.1.1.0/24", {}).get("before"))
          and "10.1.1.9" in str(by_key.get("20.1.1.0/24", {}).get("after")),
          str(by_key.get("20.1.1.0/24")))
    check("C7 华为路由 30.1.1.0/24 消失",
          by_key.get("30.1.1.0/24", {}).get("state") == "removed", str(r["summary"]))

    # 路由：思科 下一跳变化 ⚠ + 默认路由新增
    r = diff_tool.run_compare(CISCO_ROUTE_BEFORE, CISCO_ROUTE_AFTER)
    by_key = {i["key"]: i for i in r["items"]}
    check("C8 思科路由 下一跳变化 ⚠（192.168.20.0/24）",
          by_key.get("192.168.20.0/24", {}).get("state") == "changed", str(by_key.get("192.168.20.0/24")))
    check("C9 思科默认路由 0.0.0.0/0 新增",
          by_key.get("0.0.0.0/0", {}).get("state") == "added", str(r["summary"]))

    # 路由：Linux 网关变化 ⚠
    r = diff_tool.run_compare(LINUX_ROUTE_BEFORE, LINUX_ROUTE_AFTER)
    by_key = {i["key"]: i for i in r["items"]}
    check("C10 Linux ip route 网关变化 ⚠（default）",
          r["rule_id"] == "route_table"
          and by_key.get("default", {}).get("state") == "changed"
          and "192.168.1.1" in str(by_key.get("default", {}).get("before"))
          and "192.168.1.254" in str(by_key.get("default", {}).get("after")),
          str(by_key.get("default")))


# ======================================================================
# [D] 变体抽验 ≥15（沿用 E 批纪律；17 变体逐个过样本）
# ======================================================================
VARIANT_SAMPLES = [
    # (rule_id, vendor, sample_before, sample_after)
    ("if_status", "huawei", HW_IF_BEFORE, HW_IF_AFTER),
    ("if_status", "cisco", CISCO_IF_BEFORE, CISCO_IF_AFTER),
    ("if_status", "h3c", "dis interface brief\nInterface        Link   Speed  Duplex  Type  PVID\nGE1/0/1          UP     1000(a) F(a)   --    1\nGE1/0/2          DOWN   auto   auto    --    1",
     "dis interface brief\nInterface        Link   Speed  Duplex  Type  PVID\nGE1/0/1          DOWN   auto   auto    --    1\nGE1/0/2          DOWN   auto   auto    --    1"),
    ("if_status", "ruijie", CISCO_IF_BEFORE, CISCO_IF_AFTER),
    ("if_status", "juniper", "root> show interfaces terse\nInterface               Admin Link Proto    Local                 Remote\nge-0/0/0                up    up\nge-0/0/0.0              up    up   inet     10.1.1.1/24\nge-0/0/1                down  down\n",
     "root> show interfaces terse\nInterface               Admin Link Proto    Local                 Remote\nge-0/0/0                up    up\nge-0/0/0.0              up    up   inet     10.1.1.1/24\nge-0/0/1                up    up\n"),
    ("vlan_table", "huawei", HW_VLAN_BEFORE, HW_VLAN_AFTER),
    ("vlan_table", "cisco", CISCO_VLAN_BEFORE, CISCO_VLAN_AFTER),
    ("vlan_table", "ruijie", CISCO_VLAN_BEFORE, CISCO_VLAN_AFTER),
    ("ospf_peer", "huawei", HW_OSPF_BEFORE, HW_OSPF_AFTER),
    ("ospf_peer", "h3c", HW_OSPF_BEFORE, HW_OSPF_AFTER),
    ("ospf_peer", "cisco", CISCO_OSPF_BEFORE, CISCO_OSPF_AFTER),
    ("ospf_peer", "ruijie", CISCO_OSPF_BEFORE, CISCO_OSPF_AFTER),
    ("route_table", "huawei", HW_ROUTE_BEFORE, HW_ROUTE_AFTER),
    ("route_table", "h3c", HW_ROUTE_BEFORE, HW_ROUTE_AFTER),
    ("route_table", "cisco", CISCO_ROUTE_BEFORE, CISCO_ROUTE_AFTER),
    ("route_table", "ruijie", CISCO_ROUTE_BEFORE, CISCO_ROUTE_AFTER),
    ("route_table", "linux", LINUX_ROUTE_BEFORE, LINUX_ROUTE_AFTER),
]


def case_d():
    import diff_tool
    data, _p = diff_tool.load_rules()
    rules_by_id = {r["rule_id"]: r for r in data.get("diff_rules") or []}
    hits = 0
    misses = []
    for rule_id, vendor, b, a in VARIANT_SAMPLES:
        rule = rules_by_id.get(rule_id)
        if rule is None:
            misses.append("%s(规则缺失)" % rule_id)
            continue
        # 同构厂商（如 ruijie 复用 cisco 正则、h3c 复用 huawei 正则）line 逐字相同，
        # 提取取最多命中变体时命中任一等价变体即算通过——但绝不允许命中"异构"正则
        target_line = next((v.get("line") for v in rule.get("variants") or []
                            if v.get("vendor") == vendor), None)
        equiv = {v.get("vendor") for v in rule.get("variants") or []
                 if v.get("line") == target_line}
        items, variant, _noise = diff_tool.extract_items(b, rule, diff_tool.compile_noise(data))
        used_vendor = (variant or {}).get("vendor")
        if len(items) >= 1 and used_vendor in equiv:
            hits += 1
        else:
            misses.append("%s/%s(提取 %d, 命中变体 %s)" % (rule_id, vendor, len(items), used_vendor))
    check("D1 变体抽验 ≥15 命中", hits >= 15,
          "hits=%d misses=%s" % (hits, misses))
    check("D2 全 17 变体命中（E 批纪律从优）", hits == 17 and not misses, str(misses))


# ======================================================================
# [E] 坏提取规则被 validate [E] 硬拦截（端到端）
# ======================================================================
BAD_RULES = {
    "format": "netcmdl - before/after diff extract rules",
    "schema": 1,
    "noise": [{"pattern": "(unclosed", "desc": "坏正则"}],
    "diff_rules": [
        {"rule_id": "ok_rule", "label": "合法规则", "semantics": "kv",
         "match": "display clock",
         "variants": [{"vendor": "huawei", "line": "^(?P<key>\\S+)\\s+(?P<value>.+)$"}]},
        {"rule_id": "bad_regex", "label": "坏正则", "semantics": "kv",
         "match": "display x",
         "variants": [{"vendor": "huawei", "line": "^(?P<key>["}]},
        {"rule_id": "bad_groups", "label": "命名组缺失", "semantics": "kv",
         "match": "display y",
         "variants": [{"vendor": "huawei", "line": "^\\s*\\S+\\s+\\S+$"}]},
        {"rule_id": "ok_rule", "label": "重复 rule_id", "semantics": "set",
         "match": "display z",
         "variants": [{"vendor": "cisco", "line": "^(?P<item>\\d+)$"}]},
        {"rule_id": "bad_sem", "label": "语义非法", "semantics": "table",
         "match": "display w",
         "variants": [{"vendor": "huawei", "line": "^(?P<key>\\S+)$"}]},
    ],
}


def case_e():
    import diff_tool
    errs, warns = diff_tool.validate_rules(BAD_RULES)
    joined = "；".join(errs)
    check("E1 坏正则被拦", any("正则无法编译" in e for e in errs), joined[:120])
    check("E2 命名组缺失被拦", any("缺少命名组" in e for e in errs), joined[:120])
    check("E3 重复 rule_id 被拦", any("重复" in e for e in errs), joined[:120])
    check("E4 非法语义被拦", any("semantics" in e for e in errs), joined[:120])
    check("E5 全局噪声坏正则被拦", any("noise" in e for e in errs), joined[:120])

    # 端到端：临时 seed 目录注入同名坏文件（_collect_seed_files 同名优先级）→
    # main.validate_seed 必须以 [E] 退出码 1 收场
    import io
    import contextlib
    import main as mainmod
    import db as dbmod
    bad_dir = os.path.join(_se.setup(), "seed_data")
    os.makedirs(bad_dir, exist_ok=True)
    bad_path = os.path.join(bad_dir, "diff_rules.json")
    with open(bad_path, "w", encoding="utf-8") as fp:
        json.dump(BAD_RULES, fp, ensure_ascii=False)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_bad = mainmod.validate_seed()
        out = buf.getvalue()
        check("E6 端到端 validate 退出码 1（坏规则 [E] 硬拦截）", rc_bad == 1, "rc=%s" % rc_bad)
        check("E7 输出含 [X] 变更对比规则段落", "[X] 变更对比规则" in out, out[-200:])
        check("E8 输出定位到坏正则", "正则无法编译" in out, "")
    finally:
        os.remove(bad_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_ok = mainmod.validate_seed()
    check("E9 移除坏文件后 validate 复位 EXIT=0", rc_ok == 0, "rc=%s" % rc_ok)
    check("E10 底数不变（358/26/97 口径）",
          "358 条条目 + 26 棵排查树 + 97 条报错字典" in buf.getvalue(), "")


# ======================================================================
# [F] 降级行级 diff
# ======================================================================
def case_f():
    import diff_tool
    # 无规则命中（未收录命令类）→ 行级比对，不崩
    r = diff_tool.run_compare("display clock\n2026-10-14 09:00:00\n",
                              "display clock\n2026-10-14 10:00:00\n")
    check("F1 无规则命中降级 line 模式", r["mode"] == "line" and r["fallback_reason"] != "",
          "%s/%s" % (r["mode"], r["fallback_reason"]))
    check("F2 行级比对出变化（时间戳行已被噪声过滤，仍判一致）",
          r["level"] == "clean" and r["summary"]["added"] == 0
          and r["summary"]["removed"] == 0, str(r["summary"]))

    # 空输入
    r = diff_tool.run_compare("", "")
    check("F3 双空输入不崩", r["items"] == [] and r["level"] == "clean", str(r["summary"]))
    r = diff_tool.run_compare(None, None)
    check("F4 None 输入不崩", isinstance(r, dict) and r["level"] == "clean", "")

    # 规则命中但零提取（只有提示符行）→ 降级不崩
    r = diff_tool.run_compare("<SW1>display vlan", "<SW1>display vlan")
    check("F5 规则命中零提取降级", r["mode"] == "line"
          and "零提取" in (r["fallback_reason"] or ""), r["fallback_reason"])

    # 行级模式下的真实增删（含噪声行剔除）
    b = "display clock\n2026-10-14 09:00:00\nserver 10.255.0.1\n"
    a = "display clock\n2026-10-14 10:00:00\nserver 10.255.0.9\nntp service enable\n"
    r = diff_tool.run_compare(b, a)
    delta = (r["summary"]["changed"] + r["summary"]["added"] + r["summary"]["removed"])
    check("F6 行级 diff 真实变化如实上报（≥2）且不崩",
          r["mode"] == "line" and delta >= 2, str(r["summary"]))
    check("F7 时间戳行被噪声过滤（不进差异项）",
          all("2026-10-14" not in str(it["key"]) for it in r["items"]),
          str([it["key"] for it in r["items"]]))


# ======================================================================
# [G] HTML 导出
# ======================================================================
def case_g():
    import diff_tool
    import diff_export
    check("G1 打印 token 同源校验",
          diff_export.verify_print_tokens_against_theme() == [],
          "; ".join(diff_export.verify_print_tokens_against_theme()))

    r = diff_tool.run_compare(HW_VLAN_BEFORE, HW_VLAN_AFTER)
    html = diff_export.build_html(r, title="冒烟对比")
    check("G2 零 script 标签", "<script" not in html.lower())
    check("G3 零外链 http(s)", "http://" not in html and "https://" not in html)
    check("G4 零 url() 资源引用", "url(" not in html.lower())
    check("G5 A4 @page", "@page" in html and "A4" in html)
    check("G6 四态徽章齐备", all(c in html for c in ("st-same", "st-added", "st-removed")))
    check("G7 汇总分级进单", "L2 · 键集增删" in html and "＋ 新增 1" in html)
    check("G8 噪声计数进单", "噪声过滤" in html)
    check("G9 提取规则标注进单", "VLAN 表" in html)

    # 行级降级结果也可导出
    r2 = diff_tool.run_compare("display clock\nabc\n", "display clock\nxyz\n")
    html2 = diff_export.build_html(r2, title="降级对比")
    check("G10 降级行级导出可用", "降级：行级比对" in html2 and "st-changed" in html2)

    tmp = tempfile.mkdtemp(prefix="ntbx_smoke_diff_")
    out = os.path.join(tmp, "d.html")
    rc, msg = diff_export.export_diff(r, out)
    check("G11 export_diff 落盘 rc=0", rc == 0 and os.path.isfile(out), msg)
    shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# [H] 离屏 UI + 零联网源码断言
# ======================================================================
def case_h():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    from ui_diff import DiffTab
    from ui_runbook import RunbookTab

    tab = DiffTab(None)
    check("H1 Tab 构造 + 规则提示", "提取规则" in tab.lbl_rule.text(),
          tab.lbl_rule.text()[:60])
    state = tab.split_state()
    check("H2 split_state 记忆 key", "diff_split" in state, str(state))

    tab.ed_before.setPlainText(HW_IF_BEFORE)
    tab.ed_after.setPlainText(HW_IF_AFTER)
    tab.on_compare()
    check("H3 对比后表格回填", tab.tbl.rowCount() >= 1, str(tab.tbl.rowCount()))
    check("H4 clean 汇总标签", "L0 · 一致" in tab.lbl_summary.text(), tab.lbl_summary.text()[:80])

    tab.ed_after.setPlainText(HW_IF_AFTER.replace("current state : UP",
                                                  "current state : DOWN", 1))
    tab.on_compare()
    check("H5 状态列着色（theme 常量）", tab.tbl.item(0, 0) is not None
          and tab.tbl.item(0, 0).foreground().color().name().lower()
          in ("#e0a83c", "#3fbf6f", "#e05656", "#4f8cff"),
          str(tab.tbl.item(0, 0).foreground().color().name()))

    b_text = tab.ed_before.toPlainText()
    tab.on_swap()
    check("H6 交换 Before/After", tab.ed_before.toPlainText() != b_text
          and tab.ed_after.toPlainText() == b_text)

    # 操作单旁挂按钮：存在；main=None 点击只提示不崩
    d = _seeded_db()
    rb = RunbookTab(d, None)
    check("H7 操作单旁挂按钮存在", hasattr(rb, "btn_diff"), "")
    try:
        rb.on_open_diff()
        check("H8 main=None 安全提示不崩", True)
    except Exception as exc:
        check("H8 main=None 安全提示不崩", False, repr(exc))

    # 零联网源码断言：三模块不得 import 任何网络库
    net_tokens = ("import requests", "import socket", "import urllib",
                  "import http", "from urllib", "from http", "urlopen",
                  "ftplib", "telnetlib", "subprocess")
    offenders = []
    for mod in ("diff_tool.py", "diff_export.py", "ui_diff.py"):
        src = open(os.path.join(APP_DIR, "app", mod), encoding="utf-8").read()
        for tok in net_tokens:
            if tok in src:
                offenders.append("%s 含 %r" % (mod, tok))
    check("H9 三模块零联网源码断言", offenders == [], "; ".join(offenders))


def _seeded_db():
    import db as dbmod
    d = dbmod.Database(":memory:")
    d.import_seed_dir()
    return d


def main():
    print("=" * 68)
    print("smoke_diff —— Before/After 变更对比冒烟（H 批）")
    print("=" * 68)
    for fn in (case_a, case_b, case_c, case_d, case_e, case_f, case_g, case_h):
        fn()
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
