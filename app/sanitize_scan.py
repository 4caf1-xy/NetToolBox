# -*- coding: utf-8 -*-
"""
sanitize_scan.py —— 静态站点脱敏扫描器（红线件）

职责
    对即将写入静态站点的三库数据（entries / trouble_trees / err_dict）做
    导出前全字段扫描：IP 段白名单 + secret 真值关键词。**零命中才允许落盘**。

范围（结构性排除，不依赖调用方自觉）
    · 仅扫三库条目字段；
    · history / err_unresolved / ai_config / sessions 永不进扫描输入，
      也永不进站点（export_site.py 侧仅取三库）。

IP 白名单（裁决 2026-10-12）
    · 文档专用段：192.0.2.0/24  198.51.100.0/24  203.0.113.0/24（TEST-NET）
    · 10.0.0.0/8（seed 红线允许的占位段，如 10.255.x.x）
    · 192.168.0.0/16、172.16.0.0/12（RFC1918 教学示例）
    · 127.0.0.0/8、0.0.0.0、255.0.0.0/8 掩码与 0.0.0.255 反掩码形态、广播
    · 224.0.0.0/4 组播、240.0.0.0/4 保留
    · 公共 DNS：8.8.8.8 / 8.8.4.4 / 114.114.114.114 / 223.5.5.5 / 223.6.6.6
    · 教学惯例段（裁决 2026-10-12）：x.x.x.x 同段重复 ≥3 次（Router ID 惯例，
      如 1.1.1.1 / 9.9.9.9）；200.1.1.0/24（NAT/防火墙 NAT 教学段）；0.0.0.1
      （ACL 反掩码示例）。理由：均为 params default/example 中的配置语法教学值，
      非网络可达的真实分配地址。
    白名单之外零容忍——命中即失败，定位到 库.表.uuid.字段。

secret 真值检测
    · password/secret/passwd/pwd/community 后跟"真值"（非 {{占位}}、非 <人工位>、
      非 ?、非空）即嫌疑；真值形态如 `password Admin123`；
    · 明文黑名单：构建期口令（ntbxlab 等）任何位置出现即失败。

用法
    python scripts/sanitize_scan.py --db <path>     # 独立扫描模式（只读，不产出）
    from sanitize_scan import scan_site_data        # export_site.py 内嵌调用
"""

from __future__ import print_function

import ipaddress
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- IP 白名单 ----
_ALLOWED_NETS = [ipaddress.ip_network(n) for n in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",   # TEST-NET
    "10.0.0.0/8",                                          # seed 占位段
    "192.168.0.0/16", "172.16.0.0/12",                     # RFC1918 教学示例
    "127.0.0.0/8",                                         # loopback
    "224.0.0.0/4", "240.0.0.0/4",                          # 组播 / 保留
)]
_ALLOWED_IPS = {"0.0.0.0", "255.255.255.255", "0.0.0.1"}   # 通配 / 广播 / 反掩码示例
_ALLOWED_DNS = {"8.8.8.8", "8.8.4.4", "114.114.114.114",   # 公共 DNS（裁决放行）
                "223.5.5.5", "223.6.6.6"}

_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")

# 公开别名（ai_gate 门禁复用；语义与站点白名单对应项一致）
ip_network = ipaddress.ip_network
PUBLIC_DNS = _ALLOWED_DNS

# ---- secret 真值 ----
# 先剔除 {{占位}} / <人工位> 再匹配；值判定限同一行（[ \t]+ 不跨行）——
# secret 语句的值不会跨行，跨行命中必是下一行首词误报。
_SECRET_RE = re.compile(
    r"\b(password|secret|passwd|pwd|community)[ \t]+"
    r"([A-Za-z0-9!@#$%^&*._+=-]{2,})\b", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}|<[^<>\n]{0,40}>")
# 值为厂商子命令词 / 常见英文语句词 / 命令旗标 → 豁免（`community read {{x}}`
# 剔占位后剩 `community read`，read 是子命令不是真值；`passwd -S` 的 -S 是
# 命令旗标不是真值——E 批 passwd 锁定排查条目实锤的误报形态，H 批收口）
_SECRET_VALUE_EXEMPT = re.compile(
    r"^(?:\?\?|.*\?| certification|level|class|order|policy|storage|"
    r"algorithm|regeneration|override|unlock|control|authentication|"
    r"encryption|profile|server|enable|disable|auto|none|0|7|"
    r"RO|RW|read|write|cipher|simple|plain|irreversible-cipher|"
    r"minimum-length|change-type|authentication-order|"
    r"-(?:S|L|U|l|u|d|e|n|x)\w*|"
    r"has|been|expired|is|are|was|locked|for|failed|failure)$", re.IGNORECASE)
# 子命令歧义豁免已并入 _SECRET_VALUE_EXEMPT（cipher/simple/等级词等）

# 明文黑名单：构建期/工具期口令，任何字段任何位置不得出现
_BLACKLIST = ("ntbxlab",)


def _ip_allowed(ip_str, allowed_nets=None, allowed_ips=None, allowed_dns=None,
                allow_nat_teaching=True):
    """IP 白名单判定。默认参数 = 站点导出策略（原行为，逐字不变）；
    AI 发送门禁等场景可传入自己的白名单（安全加固批 2）：
        · allowed_nets/allowed_ips/allowed_dns —— 覆盖默认集合
        · allow_nat_teaching —— 是否豁免 200.1.1.0/24 NAT 教学段
    """
    allowed_nets = _ALLOWED_NETS if allowed_nets is None else allowed_nets
    allowed_ips = _ALLOWED_IPS if allowed_ips is None else allowed_ips
    allowed_dns = _ALLOWED_DNS if allowed_dns is None else allowed_dns
    if ip_str in allowed_ips or ip_str in allowed_dns:
        return True
    parts = [int(x) for x in ip_str.split(".")]
    if all(p in (0, 255) for p in parts):
        return True  # 掩码/反掩码形态
    if len(set(parts)) == 1 and len({x for x in ip_str.split(".")}) == 1:
        return True  # x.x.x.x 同段重复（Router ID 教学惯例，裁决 2026-10-12）
    if allow_nat_teaching and ip_str.startswith("200.1.1."):
        return True  # NAT 教学段（裁决 2026-10-12）
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 非法 IP（如 1.2.3.256）不是地址，不算命中
    return any(addr in net for net in allowed_nets)


def _secret_exempt(value):
    v = value.strip()
    if "{{" in v or (v.startswith("<") and v.endswith(">")):
        return True  # 防御：调用方未 scrub 时占位/人工位直接豁免
    if _SECRET_VALUE_EXEMPT.match(v):
        return True
    return False


SCAN_FIELDS = {
    "entries": ("title", "description", "commands", "params", "rollback", "notes",
                "models", "category", "vendor", "os_family", "verified_by",
                "verified_model"),
    "trouble_trees": ("symptom", "vendor_hint", "category", "steps"),
    "err_dict": ("vendor", "os_family", "pattern", "category", "cause",
                 "solution_steps", "fix_template", "examples"),
}


class Finding(object):
    def __init__(self, table, uuid, field, kind, value):
        self.table, self.uuid, self.field, self.kind, self.value = \
            table, uuid, field, kind, value

    def __str__(self):
        return "[%s] %s/%s.%s: %s" % (self.kind, self.table, self.uuid,
                                      self.field, self.value[:80])


def _scan_text(findings, table, uuid, field, text):
    if not text:
        return
    text = str(text)
    # 先剔除 {{占位}} / <人工位>——占位不是真值，且剔除后才能正确判定
    # "password cipher {{x}}"（cipher 后已无值）这类形态
    scrubbed = _PLACEHOLDER_RE.sub(" <PLACEHOLDER> ", text)
    for m in _IP_RE.finditer(text):
        ip = m.group(1)
        if not _ip_allowed(ip):
            findings.append(Finding(table, uuid, field, "IP-WHITELIST",
                                    "...%s..." % text[max(0, m.start() - 30):m.end() + 20]))
    for m in _SECRET_RE.finditer(scrubbed):
        word, value = m.group(1), m.group(2)
        if _secret_exempt(value):
            continue
        findings.append(Finding(table, uuid, field, "SECRET",
                                "...%s..." % text[max(0, m.start() - 30):m.end() + 20]))
    for bl in _BLACKLIST:
        if bl in text:
            findings.append(Finding(table, uuid, field, "BLACKLIST",
                                    "明文黑名单值 %r 出现" % bl))


def _scrub_with_index_map(text):
    """
    {{占位}} / <人工位> → " <PLACEHOLDER> "，同时产出 scrubbed 串与
    「scrubbed 下标 → 原文下标」映射（供 secret 命中区间回映原文，
    AI 门禁回写脱敏需要原文 span；站点扫描不受影响——仍走 _scan_text）。
    """
    out = []
    idx_map = []                      # idx_map[i] = scrubbed[i] 在原文的下标
    last = 0
    for m in _PLACEHOLDER_RE.finditer(text):
        out.append(text[last:m.start()])
        idx_map.extend(range(last, m.start()))
        rep = " <PLACEHOLDER> "
        out.append(rep)
        idx_map.extend([m.start()] * len(rep))   # 占位区整段映射到占位起点
        last = m.end()
    out.append(text[last:])
    idx_map.extend(range(last, len(text)))
    return "".join(out), idx_map


def scan_free_text(text, allowed_nets, allowed_ips, allowed_dns,
                   allow_nat_teaching=False):
    """
    文本级扫描（引擎复用入口，安全加固批 2：AI 发送门禁等自由文本场景）。
    与站点扫描同一套正则/豁免规则（secret 真值判定、明文黑名单逐字一致），
    IP 白名单由调用方注入（门禁策略比站点严：RFC1918 要拦）。

    返回 finding 字典列表，全部带原文 span（start/end 覆盖命中值本身）：
        {"kind": "IP"|"SECRET"|"BLACKLIST", "value": str,
         "start": int, "end": int, "context": str,
         "pre": str, "post": str}          # pre/post = 命中值前后窗口（预览高亮用）
    """
    text = text or ""
    findings = []

    def _win(s, e):
        return text[max(0, s - 30):s], text[e:e + 20]

    # ---- IP（原文 span，无需映射）----
    for m in _IP_RE.finditer(text):
        ip = m.group(1)
        if not _ip_allowed(ip, allowed_nets, allowed_ips, allowed_dns,
                           allow_nat_teaching):
            pre, post = _win(m.start(), m.end())
            findings.append({"kind": "IP", "value": ip,
                             "start": m.start(), "end": m.end(),
                             "pre": pre, "post": post,
                             "context": "...%s%s%s..." % (pre, ip, post)})
    # ---- secret（在 scrubbed 上判定，span 回映原文）----
    scrubbed, idx_map = _scrub_with_index_map(text)
    for m in _SECRET_RE.finditer(scrubbed):
        word, value = m.group(1), m.group(2)
        if _secret_exempt(value):
            continue
        s = idx_map[m.start(2)]
        e = idx_map[m.end(2) - 1] + 1
        pre, post = _win(s, e)
        findings.append({"kind": "SECRET", "value": value, "start": s, "end": e,
                         "pre": pre, "post": post,
                         "context": "...%s%s%s..." % (pre, value, post)})
    # ---- 明文黑名单（原文 span）----
    for bl in _BLACKLIST:
        pos = 0
        while True:
            i = text.find(bl, pos)
            if i < 0:
                break
            pre, post = _win(i, i + len(bl))
            findings.append({"kind": "BLACKLIST", "value": bl,
                             "start": i, "end": i + len(bl),
                             "pre": pre, "post": post,
                             "context": "...%s%s%s..." % (pre, bl, post)})
            pos = i + len(bl)
    findings.sort(key=lambda f: (f["start"], f["end"]))
    return findings


def scan_site_data(data):
    """data = {'entries': [dict], 'trees': [dict], 'errs': [dict]} → findings 列表。

    只扫站点将包含的字段（SCAN_FIELDS 白名单之外的字段结构性不入站）。
    params 为 JSON 字符串（entries），steps 为 JSON 字符串（trees）——整体当文本扫。
    """
    findings = []
    for row in data.get("entries", []):
        for f in SCAN_FIELDS["entries"]:
            _scan_text(findings, "entries", row.get("uuid", "?"), f, row.get(f))
    for row in data.get("trees", []):
        for f in SCAN_FIELDS["trouble_trees"]:
            _scan_text(findings, "trouble_trees", row.get("tree_id", "?"), f, row.get(f))
    for row in data.get("errs", []):
        for f in SCAN_FIELDS["err_dict"]:
            _scan_text(findings, "err_dict", row.get("err_id", "?"), f, row.get(f))
    return findings


def assert_clean(data):
    findings = scan_site_data(data)
    if findings:
        print("[E] 脱敏扫描 %d 处命中，拒绝落盘：" % len(findings))
        for f in findings[:20]:
            print("   -", f)
        if len(findings) > 20:
            print("   ... 共 %d 处" % len(findings))
        raise SystemExit(5)
    return 0


def main(argv):
    db_path = None
    it = iter(argv)
    for a in it:
        if a == "--db":
            db_path = next(it, None)
    if not db_path or not os.path.isfile(db_path):
        raise SystemExit("用法: python scripts/sanitize_scan.py --db <command_lib.db>（只读扫描）")
    import json as _json
    conn = __import__("sqlite3").connect(db_path)
    conn.row_factory = __import__("sqlite3").Row

    def rows(table):
        cur = conn.execute("SELECT * FROM %s" % table)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    data = {"entries": rows("entries"), "trees": rows("trouble_trees"),
            "errs": rows("err_dict")}
    findings = scan_site_data(data)
    if findings:
        print("[FAIL] %d 处命中：" % len(findings))
        for f in findings:
            print("   -", f)
        return 5
    print("[OK] 三库全字段扫描零命中（IP 白名单 + secret 真值 + 明文黑名单）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
