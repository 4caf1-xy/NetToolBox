# -*- coding: utf-8 -*-
"""
renderer.py —— 命令模板渲染引擎

职责：
    1. {{参数}} 占位解析（支持 {{name}} 与 {{name:默认值}} 两种写法）
    2. 参数校验（VLAN ID / IPv4 / 掩码长度 / 整数范围 / 端口范围 / 枚举 / 正则）
    3. 厂商接口命名规则表 + 端口范围智能展开
       "0/1-0/10" → 思科 Gi0/1..Gi0/10、华为 Eth0/0/1..Eth0/0/10、H3C Gi1/0/1..Gi1/0/10
    4. 注释行剔除（! # 开头）、配置包头部注释块生成

硬性约束：本模块为纯字符串处理，不 import 任何网络模块。
"""

import re
import json
import datetime

# ---------------------------------------------------------------------------
# 一、{{参数}} 占位解析
# ---------------------------------------------------------------------------

# 支持 {{name}}、{{name:默认值}}、{{ name }}；
# 参数名允许中英文/数字/下划线/点/连字符
PARAM_RE = re.compile(r"\{\{\s*([A-Za-z0-9_\-\.\u4e00-\u9fa5]+)\s*(?::([^{}]*))?\s*\}\}")

# 结构化 params 的 type 白名单（schema 升级 2026-09-30）：
#   string 普通文本 / int 整数（range 提示）/ enum 枚举（choices 必带）
#   ip IPv4 地址 / flag 开关型（未勾选 → 占位整体消失，勾选 → 渲染 on_value）
PARAM_TYPES = ("string", "int", "enum", "ip", "flag")

# validate 规则白名单（validate_seed 与 check_entry_params 共用同一口径）
KNOWN_VALIDATE_RULES = ("", "vlan", "ipv4", "ip", "masklen", "mask", "port",
                        "port_range", "ifname", "nic", "path", "file")

_RE_INT_RANGE = re.compile(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$")

# 注释行判定：! 或 # 开头的行（允许前置空白）
COMMENT_LINE_RE = re.compile(r"^\s*[!#]")

# ---------------------------------------------------------------------------
# 一·B、变更类判定表（裁决B 2026-10-09，schema 升级 rollback 配对）
# ---------------------------------------------------------------------------
# ★ 本数据是判定表的**唯一事实源**：seed-guide.md / AI prompt 只引用不改写，
#   禁止在文档或别处另写一份导致漂移（裁决B增补②）。
#
# 判定规则（fail-safe 默认：分不清就当变更类——宁误报不漏报）：
#   1. 取 commands 全部**非注释行**（# / ! 开头不计，裁决B增补③），
#      逐行规范化（小写、压缩空白）；
#   2. 任一行命中【强制变更组】→ 变更类（clear/debug/reload/erase 等无论
#      上下文一律按变更对待，rollback 必须给出对应撤销动作）；
#   3. 全部行命中【查询类白名单】→ 查询类（rollback 免填）；
#   4. 其余任何情况 → 变更类。
#
# 白名单按"命令+子命令"精确前缀匹配（裁决B增补①）：
#   systemctl status 在名单 ≠ systemctl 在名单；display current 类变体逐个列。
#   匹配口径：规范化行 == 前缀，或以「前缀 + 一个空格」开头（词边界，杜绝
#   showtech-support 之类误命中）。单动词家族（show/display/get/print）本身
#   只读，其后任意子命令都算查询；多义动词（systemctl/ip/iptables 等）必须
#   带子命令逐个列入。
QUERY_WHITELIST = (
    # ---- 会话级命令（进入/退出特权模式、终端显示开关，不改变设备配置状态）----
    "enable", "disable", "terminal monitor", "terminal no monitor",
    "terminal trapping", "terminal no trapping", "monitor start",
    "monitor stop", "monitor list",
    # ---- 网络侧单动词家族（其后子命令均为读操作）----
    "show", "display", "get", "print",
    # ---- Linux 只读命令（单义动词，整词在名单）----
    "cat", "ls", "less", "more", "grep", "head", "tail", "wc", "file", "stat",
    "pwd", "ss", "netstat", "lsof", "ps", "uname", "uptime", "free", "df",
    "du", "lsblk", "blkid", "dmesg",     "lscpu", "lshw", "dmidecode", "lspci",
    "lsusb", "lsmod", "iostat", "vmstat", "mpstat", "sar", "ethtool",
    "top -b", "top -bn",   # top 批处理模式为只读快照（含 -bn1 无空格变体）；交互式 top 不入名单
    "ping", "ping6", "traceroute", "tracepath", "mtr", "dig", "nslookup",
    "host", "arping", "tcpdump", "arp", "id", "who", "w", "whoami", "groups",
    "date", "hostname", "getenforce", "sestatus", "mount", "rpm", "dpkg",
    "journalctl", "history", "last", "lastlog", "env", "printenv",
    # ---- Linux 多义动词：命令+子命令精确前缀 ----
    #   ★ 不收裸前缀（ip addr / ip route 这类）——"ip addr add" 会被
    #     startswith("ip addr ") 吞进查询类（裁决B增补① 明令禁止的形态）
    "ip addr show", "ip link show", "ip route show", "ip neigh show",
    "ip rule show", "ip maddr show", "ip -s link show", "ip -s addr show",
    "systemctl status", "systemctl is-active", "systemctl is-enabled",
    "systemctl is-failed", "systemctl list-units", "systemctl list-unit-files",
    "systemctl list-dependencies", "systemctl list-timers", "systemctl show",
    "systemctl cat", "systemctl get-default",
    "service --status-all", "status",
    "hostnamectl status", "timedatectl status",
    "nmcli device show", "nmcli dev status", "nmcli connection show",
    "nmcli general status", "nmcli general",
    "firewall-cmd --state", "firewall-cmd --list-all",
    "firewall-cmd --list-all-zones", "firewall-cmd --get-active-zones",
    "firewall-cmd --get-zones", "firewall-cmd --get-services",
    "firewall-cmd --get-default-zone", "firewall-cmd --info-zone",
    "firewall-cmd --info-service",
    "iptables -L", "iptables -S", "iptables -vnL", "iptables -t nat -L",
    "iptables -t mangle -L", "iptables -t filter -L",
    "rpm -qa", "rpm -q", "rpm -qi", "rpm -ql",
    "dpkg -l", "dpkg -s", "dpkg -L",
    "yum list", "yum info", "dnf list", "dnf info",
    "apt list", "apt show", "apt-cache policy", "apt-cache show",
    "crontab -l", "lsscsi", "fdisk -l", "sfdisk -l",
    # ---- FortiOS HA 只读诊断（精确前缀；diagnose 族整体有副作用命令，不整族入名单）----
    "diagnose sys ha checksum cluster", "diagnose sys ha heartbeat",
    "diagnose sys ha status", "diagnose sys ha history",
)

# 强制变更组（词前缀匹配，命中即变更类，优先级高于白名单）：
#   这些动词有即时/持久化副作用，混在查询序列里也必须按变更对待。
CHANGE_FORCE_PREFIXES = (
    "clear", "undebug", "no debug", "debug", "reload", "erase", "undo",
    "reset", "restart", "write", "save", "commit", "delete", "format",
    "restore", "shutdown", "reboot", "poweroff", "halt", "kill", "pkill",
    "killall", "init 0", "init 6", "dd", "mkfs", "useradd", "userdel",
    "usermod", "passwd", "chmod", "chown", "chattr", "swapoff", "swapon",
    "insmod", "rmmod", "modprobe", "setenforce", "truncate",
)


def _norm_cmd_line(line):
    """命令行规范化：去首尾空白、转小写、压缩连续空白（供判定表匹配）"""
    return " ".join((line or "").strip().lower().split())


def _prefix_hit(norm, prefixes):
    """词边界前缀命中：norm == p 或 norm 以「p + 空格」开头"""
    for p in prefixes:
        if norm == p or norm.startswith(p + " "):
            return True
    return False


def classify_entry(entry):
    """
    变更类/查询类判定（裁决B，schema 2026-10-09）。
    返回 "query"（查询类，rollback 免填）或 "change"（变更类，rollback 必填）。
    判定仅扫描 commands 的非注释行；rollback 文本不参与判定。
    validate_seed / AI 入库钩子共用（经由 check_entry_params），单一执法点。

    语义补充：commands 全部为注释行（典型：Web 控制台路径条目，正文是
    操作路径说明而非 CLI 命令）或为空 → 没有可执行命令、没有 CLI 回退对象，
    判查询类（rollback 免填）。这是刻意设计，不是真空真理的巧合。
    """
    text = (entry.get("commands") if isinstance(entry, dict) else "") or ""
    for ln in split_command_lines(text, skip_comments=True):
        norm = _norm_cmd_line(ln)
        if not norm:
            continue
        if _prefix_hit(norm, CHANGE_FORCE_PREFIXES):
            return "change"
        if not _prefix_hit(norm, QUERY_WHITELIST):
            return "change"
    return "query"


def extract_params(text):
    """
    从命令文本中提取参数占位，按出现顺序去重。
    返回 [{name, default}]，default 为 {{name:xxx}} 里的 xxx，没有则为空串。
    """
    result = []
    seen = set()
    for match in PARAM_RE.finditer(text or ""):
        name = match.group(1)
        default = (match.group(2) or "").strip()
        if name in seen:
            # 后出现的默认值可补全先前缺失的默认值
            if default:
                for item in result:
                    if item["name"] == name and not item["default"]:
                        item["default"] = default
            continue
        seen.add(name)
        result.append({"name": name, "default": default})
    return result


def normalize_spec(spec):
    """
    结构化字段（type/choices/range）→ 细粒度 validate 规则推导。
    设计取舍：type 是粗分类（驱动 UI 控件选择），validate 是细粒度校验规则
    （存量 987 个 spec 都靠它工作）——两者并存，**显式 validate 优先**，
    只有 validate 为空时才从 type/choices/range 推导，避免破坏存量条目。
    返回新 dict（不改入参）；无可推导内容时原样返回。
    """
    if not isinstance(spec, dict):
        return spec
    stype = (spec.get("type") or "").strip()
    if (spec.get("validate") or "").strip() or not stype:
        return spec
    derived = ""
    if stype == "enum":
        choices = spec.get("choices") or []
        if choices:
            derived = "enum:" + "|".join(str(c) for c in choices)
    elif stype == "int":
        m = _RE_INT_RANGE.match(str(spec.get("range") or ""))
        if m:
            derived = "int:%s-%s" % (m.group(1), m.group(2))
    elif stype == "ip":
        derived = "ipv4"
    # string / flag：无默认规则（flag 由渲染层特判）
    if not derived:
        return spec
    out = dict(spec)
    out["validate"] = derived
    return out


def merge_param_specs(param_specs, text):
    """
    把"命令里提取到的参数"与"条目 params 字段里的规格"合并：
        - 规格里有的参数：补全 label/required/validate/example 等
        - 命令里有但规格缺失的参数：用名字兜底成一条非必填规格
    返回完整的参数规格列表（顺序 = 命令中出现的顺序）。
    结构化 spec 会先过 normalize_spec（type/choices/range → validate 推导）。
    """
    spec_map = {}
    for spec in param_specs or []:
        if isinstance(spec, dict) and spec.get("name"):
            spec_map[spec["name"]] = normalize_spec(spec)

    merged = []
    for item in extract_params(text):
        spec = dict(spec_map.get(item["name"], {}))
        spec.setdefault("name", item["name"])
        spec.setdefault("label", item["name"])
        spec.setdefault("default", item["default"])
        if not spec.get("default"):
            spec["default"] = item["default"]
        spec.setdefault("required", False)
        spec.setdefault("validate", "")
        spec.setdefault("example", "")
        merged.append(spec)

    # 规格里定义了但命令里没写的参数也保留（方便生成器统一展示）
    for name, spec in spec_map.items():
        if name not in [m["name"] for m in merged]:
            merged.append(spec)
    return merged


def has_params(text):
    """命令文本是否含参数占位"""
    return bool(PARAM_RE.search(text or ""))


# ---------------------------------------------------------------------------
# 二、参数校验
# ---------------------------------------------------------------------------

_RE_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
# 端口 token：允许 "0/1"、"1/0/1"、"Gi0/1"、"ge-0/0/1"、"eth0" 等形式
_RE_PORT_TOKEN = re.compile(r"^[A-Za-z\-_]*\d+(?:/\d+)*$")


def _ok():
    return True, ""


def _fail(msg):
    return False, msg


def _is_ipv4(value):
    m = _RE_IPV4.match(value or "")
    if not m:
        return False
    return all(0 <= int(g) <= 255 for g in m.groups())


def check_vlan(value):
    """VLAN ID：1 - 4094 整数"""
    if not str(value).strip().isdigit():
        return _fail("VLAN ID 必须是 1 - 4094 的整数")
    num = int(value)
    if not (1 <= num <= 4094):
        return _fail("VLAN ID 超出范围（1 - 4094），当前 %d" % num)
    return _ok()


def check_ipv4(value):
    """IPv4 地址格式"""
    if not _is_ipv4(str(value).strip()):
        return _fail("IPv4 地址格式错误，示例 192.168.1.1")
    return _ok()


def check_masklen(value):
    """
    掩码：既接受前缀长度（0-32），也接受点分十进制掩码（255.255.255.0），
    额外允许 Cisco ACL 的反掩码（0.0.0.255）——采用宽松校验，只要点分格式合法即可。
    """
    text = str(value).strip()
    if text.isdigit():
        num = int(text)
        if 0 <= num <= 32:
            return _ok()
        return _fail("掩码前缀长度应在 0 - 32 之间，当前 %d" % num)
    if _is_ipv4(text):
        return _ok()
    return _fail("掩码格式错误，示例 24 或 255.255.255.0")


def check_int_range(value, lo, hi):
    """整数范围校验"""
    text = str(value).strip()
    if not re.match(r"^-?\d+$", text):
        return _fail("必须是整数")
    num = int(text)
    if not (lo <= num <= hi):
        return _fail("数值应在 %d - %d 之间，当前 %d" % (lo, hi, num))
    return _ok()


def check_port(value):
    """单端口格式，如 0/1、1/0/1、Gi0/1、ge-0/0/1、eth0"""
    text = str(value).strip()
    if _RE_PORT_TOKEN.match(text):
        return _ok()
    return _fail("端口格式错误，示例 0/1 或 0/0/1 或 Gi0/1")


def check_port_range(value):
    """
    端口范围格式：单端口、逗号列表或短横线区间，例如
        0/1        0/1-0/10       0/1,0/3,0/5-0/7       Gi0/1-0/10
    """
    text = str(value).strip()
    if not text:
        return _fail("端口范围不能为空")
    # 统一各类连字符与波浪号
    normalized = text.replace("－", "-").replace("—", "-").replace("~", "-").replace("～", "-")
    for seg in re.split(r"[,，]", normalized):
        seg = seg.strip()
        if not seg:
            return _fail("端口范围里存在空段（多余的逗号？）")
        parts = [p.strip() for p in seg.split("-") if p.strip() != ""]
        if len(parts) > 2:
            return _fail("单段最多一个短横线，示例 0/1-0/10")
        for p in parts:
            if not _RE_PORT_TOKEN.match(p):
                return _fail("端口格式错误：%s（示例 0/1-0/10）" % p)
    return _ok()


def check_enum(value, allowed):
    """枚举校验，allowed 为允许值列表"""
    if str(value).strip() in allowed:
        return _ok()
    return _fail("取值必须是：%s" % " / ".join(allowed))


# Linux 网卡命名：eth0 / ens33 / enp3s0 / eno1 / bond0 / br0 / vlan10 / wlan0 / eth0.10
_RE_IFNAME = re.compile(
    r"^(eth\d+(\.[\d]+)?|ens\d+(f\d+)?|enp\d+s\d+(f\d+)?|eno\d+|enx[0-9a-f]+"
    r"|bond\d+|br\d+|vlan\d+|wlan\d+|nm-bond|docker\d+|virbr\d+)$", re.IGNORECASE)


def check_ifname(value):
    """
    网卡名校验（Linux 条目专用）：
    接受 eth0 / ens33 / enp3s0 / eno1 / bond0 / br0 / vlan10 / wlan0 / eth0.10 等常见命名，
    拒绝带空格的写法（如 "eth 0"）与纯数字。
    """
    text = str(value).strip()
    if not text:
        return _fail("网卡名不能为空")
    if " " in text or "\t" in text:
        return _fail("网卡名不能包含空格（正确写法如 eth0、enp3s0、bond0）")
    if _RE_IFNAME.match(text):
        return _ok()
    return _fail("网卡名格式不正确，常见写法：eth0 / ens33 / enp3s0 / bond0 / br0 / wlan0")


def validate_value(spec, value):
    """
    按参数规格里的 validate 字段做校验，返回 (是否通过, 错误信息)。
    validate 语法：
        ""                仅做必填校验
        "vlan"            VLAN ID 1-4094
        "ipv4"            IPv4 地址
        "masklen"         掩码前缀长度或点分十进制
        "int:1-255"       整数范围
        "port"            单端口
        "port_range"      端口范围/列表
        "enum:a|b|c"      枚举
        "regex:^xx$"      正则
    """
    text = "" if value is None else str(value).strip()
    if spec.get("required") and not text:
        label = spec.get("label") or spec.get("name") or "该参数"
        return _fail("「%s」为必填项" % label)
    if not text:
        return _ok()

    rule = (spec.get("validate") or "").strip()
    # 标了"端口展开"的字段，取值天然是范围/列表，一律按 port_range 校验
    # （避免老条目写 validate=port 却填了 0/1-0/10 被误判为非法）
    if spec.get("expand") == "port" and rule in ("", "port"):
        rule = "port_range"
    if not rule:
        return _ok()
    if rule == "vlan":
        return check_vlan(text)
    if rule in ("ipv4", "ip"):
        return check_ipv4(text)
    if rule in ("masklen", "mask"):
        return check_masklen(text)
    if rule.startswith("int:"):
        body = rule[4:]
        m = re.match(r"^(-?\d+)-(-?\d+)$", body)
        if m:
            return check_int_range(text, int(m.group(1)), int(m.group(2)))
        return _ok()
    if rule == "port":
        return check_port(text)
    if rule == "port_range":
        return check_port_range(text)
    if rule in ("ifname", "nic"):
        return check_ifname(text)
    if rule in ("path", "file"):
        # 路径类只做危险字符拦截（Linux 条目路径不强制格式）
        if any(ch in text for ch in ("\n", "\t", "\0")):
            return _fail("路径里不能有换行/制表符")
        return _ok()
    if rule.startswith("enum:"):
        return check_enum(text, [x for x in rule[5:].split("|") if x])
    if rule.startswith("regex:"):
        try:
            if re.match(rule[6:], text):
                return _ok()
            return _fail("不符合格式要求")
        except re.error:
            return _ok()
    return _ok()


def validate_form(specs, values):
    """
    整表校验，返回 {参数名: 错误信息}（全部通过则返回空 dict）
    values 为 {参数名: 值}
    """
    errors = {}
    for spec in specs or []:
        name = spec.get("name")
        if not name:
            continue
        passed, msg = validate_value(spec, values.get(name, ""))
        if not passed:
            errors[name] = msg
    return errors


def check_entry_params(entry):
    """
    条目级参数对账 + 结构化 params 校验（schema 升级 2026-09-30）。
    validate_seed / AI 入库钩子共用同一口径，避免两处规则漂移。

    返回 (problems, warnings)：
        problems  —— ERROR 级：占位引用未定义、结构化字段缺失/非法、
                     default 违反自身 type/choices/range、新条目 description 为空
        warnings  —— WARNING 级：参数定义未引用、
                     变更类缺 rollback / rollback 占位符未声明（迁移期宽松，任务6 升 ERROR）

    兼容策略：spec 没有 type 字段 = 旧格式，只做对账与 default 自校验，其余放行；
    有 type = 结构化校验生效（name/type/description 必填，enum 必带 choices）。
    required 且 default 为空是 P2 整改后的合法形态（UI 表单现场输入），不算错误。
    """
    problems = []
    warnings = []
    if not isinstance(entry, dict):
        return problems, warnings
    text = entry.get("commands") or ""
    specs = _load_params(entry)

    used_names = set()
    for item in extract_params(text):
        used_names.add(item["name"])

    declared_names = set()
    for spec in specs:
        if not isinstance(spec, dict):
            problems.append("params 里存在非对象项（%r）" % (spec,))
            continue
        name = str(spec.get("name") or "").strip()
        if not name:
            problems.append("params 存在缺 name 的参数规格")
            continue
        declared_names.add(name)

        stype = str(spec.get("type") or "").strip()
        structured = bool(stype)
        if structured and stype not in PARAM_TYPES:
            problems.append("参数 %s 的 type 非法（%s），只允许 %s"
                            % (name, stype, "/".join(PARAM_TYPES)))
        if structured and not str(spec.get("description") or "").strip():
            # desc_pending 迁移机制 2026-10-09 退役：结构化条目缺 description 一律 ERROR
            problems.append("参数 %s 缺 description（结构化条目必填）" % name)
        if structured and stype == "enum" and not (spec.get("choices") or []):
            problems.append("enum 参数 %s 缺 choices（必须给出可选值全集）" % name)

        rule = (spec.get("validate") or "").strip()
        if rule and rule not in KNOWN_VALIDATE_RULES \
                and not rule.startswith(("int:", "enum:", "regex:")):
            problems.append("参数 %s 的校验规则无法识别（%s）" % (name, rule))

        # default 非空时必须通过自身校验（type/choices/range 经 normalize_spec 并入 validate）
        default = spec.get("default")
        if str(default or "").strip():
            passed, msg = validate_value(normalize_spec(spec), default)
            if not passed:
                problems.append("参数 %s 的默认值不合法（%s）" % (name, msg))

    for name in sorted(used_names - declared_names):
        problems.append("命令里的 {{%s}} 没有在 params 中声明" % name)
    for name in sorted(declared_names - used_names):
        warnings.append("参数 %s 已声明但命令里未使用" % name)

    # ---- rollback 配对校验（schema 2026-10-09，裁决A1：多行字符串与 commands 同构）----
    #   变更类（classify_entry 判定，fail-safe）rollback 必填；查询类免填。
    #   rollback 占位符必须 ⊆ params 声明（双向对账的 rollback 侧）。
    #   批1 合入为 WARNING（迁移期宽松：变更类缺口预计 ~264 条属软着陆设计，
    #   门槛始终 EXIT=0）；任务6（B23 闭环）将以下两条升 ERROR。
    if isinstance(entry, dict):
        rollback = entry.get("rollback")
        if isinstance(rollback, (list, dict)):
            problems.append("rollback 必须是多行字符串（与 commands 同构），不能是 %s"
                            % type(rollback).__name__)
            rollback = ""
        rollback = str(rollback or "").strip()
        rtype = classify_entry(entry)
        if rtype == "change" and not rollback:
            warnings.append("变更类条目缺 rollback（迁移期宽松，任务6 升 ERROR）")
        for item in extract_params(rollback):
            if item["name"] not in declared_names:
                warnings.append("rollback 引用 {{%s}} 未在 params 声明" % item["name"])

    return problems, warnings


# ---------------------------------------------------------------------------
# 三、厂商接口命名规则表 + 端口范围展开
# ---------------------------------------------------------------------------

"""
规则字段说明：
    short / long   : 接口名短写 / 全写（如 Gi / GigabitEthernet）
    mode           : input  = 槽位取自用户输入（Cisco Gi0/1）
                     fixed  = 槽位有别名/固定值（H3C 默认 stack 成员 1）
                     flat   = 无槽位概念，直接拼模板（Forti 用 port1、AF 用 eth1）
    slot_count     : 端口号之前应该有几段（Cisco 1 → Gi0/1；华为/Juniper 2 → Eth0/0/1）
    tail           : 输入段数不足 slot_count 时用于补齐的固定段（华为补 0）
    fixed          : mode=fixed 时的默认槽位段（H3C [1,0] → Gi1/0/1）
    template       : mode=flat 时的模板，用 {port} 占位
    example        : 展开示例（UI 里给用户看的提示）
"""
PORT_RULES = {
    "cisco": {
        "short": "Gi", "long": "GigabitEthernet",
        "mode": "input", "slot_count": 1, "tail": [], "template": "",
        "example": "0/1-0/10 → Gi0/1 ... Gi0/10",
    },
    "huawei": {
        "short": "Eth", "long": "Ethernet",
        "mode": "input", "slot_count": 2, "tail": [0], "template": "",
        "example": "0/1-0/10 → Eth0/0/1 ... Eth0/0/10；也可直接写 0/0/1",
    },
    "h3c": {
        "short": "Gi", "long": "GigabitEthernet",
        "mode": "fixed", "fixed": [1, 0], "template": "",
        "example": "0/1-0/10 → Gi1/0/1 ... Gi1/0/10",
    },
    "ruijie": {
        "short": "Gi", "long": "GigabitEthernet",
        "mode": "input", "slot_count": 1, "tail": [], "template": "",
        "example": "0/1-0/10 → Gi0/1 ... Gi0/10",
    },
    "juniper": {
        "short": "ge-", "long": "ge-",
        "mode": "input", "slot_count": 2, "tail": [0], "template": "",
        "example": "0/1-0/10 → ge-0/0/1 ... ge-0/0/10",
    },
    "fortinet": {
        "short": "port", "long": "port",
        "mode": "flat", "template": "port{port}",
        "example": "1-10 → port1 ... port10",
    },
    "sangfor": {
        "short": "eth", "long": "eth",
        "mode": "flat", "template": "eth{port}",
        "example": "1-10 → eth1 ... eth10",
    },
    "topsec": {
        "short": "eth", "long": "eth",
        "mode": "flat", "template": "eth{port}",
        "example": "1-10 → eth1 ... eth10",
    },
    "paloalto": {
        "short": "ethernet1/", "long": "ethernet1/",
        "mode": "flat", "template": "ethernet1/{port}",
        "example": "1-10 → ethernet1/1 ... ethernet1/10",
    },
    "zte": {
        "short": "gei_", "long": "gei_",
        "mode": "input", "slot_count": 1, "tail": [], "template": "",
        "example": "1/1-1/10 → gei_1/1 ... gei_1/10",
    },
    "arista": {
        "short": "Et", "long": "Ethernet",
        "mode": "input", "slot_count": 0, "tail": [], "template": "",
        "example": "1-10 → Et1 ... Et10",
    },
}

# 允许用户手动指定接口类型（生成器下拉框用）：key = vendor slug，value = [(全名, 短名), ...]
INTERFACE_TYPES = {
    "cisco": [("GigabitEthernet", "Gi"), ("TenGigabitEthernet", "Te"),
              ("FastEthernet", "Fa"), ("Ethernet", "Et"), ("Port-channel", "Po")],
    "huawei": [("Ethernet", "Eth"), ("GigabitEthernet", "GE"), ("XGigabitEthernet", "XGE"),
               ("Eth-Trunk", "Eth-Trunk")],
    "h3c": [("GigabitEthernet", "GE"), ("Ten-GigabitEthernet", "XGE"),
            ("Ethernet", "Eth"), ("Bridge-Aggregation", "BAGG")],
    "ruijie": [("GigabitEthernet", "Gi"), ("TenGigabitEthernet", "Te"),
               ("AggregatePort", "Ag")],
    "juniper": [("ge-", "ge-"), ("xe-", "xe-"), ("et-", "et-"), ("ae", "ae")],
    "fortinet": [("port", "port")],
    "sangfor": [("eth", "eth")],
    "topsec": [("eth", "eth")],
    "paloalto": [("ethernet1/", "ethernet1/")],
    "zte": [("gei_", "gei_")],
    "arista": [("Ethernet", "Et")],
}

# OS 级规则覆盖：同一个厂商不同产品线接口命名不同（如华为 S 系列 Eth0/0/1 与 AR 系列 GE1/0/1）
# key = (vendor slug, os_family slug)
OS_PORT_OVERRIDES = {
    ("huawei", "vrp8"): {
        "short": "GE", "long": "GigabitEthernet",
        "mode": "input", "slot_count": 2, "tail": [0], "template": "",
        "example": "1/0/1-1/0/10 → GE1/0/1 ... GE1/0/10（AR 系列路由器）",
    },
    ("huawei", "vrp5"): {
        "short": "Eth", "long": "Ethernet",
        "mode": "input", "slot_count": 2, "tail": [0], "template": "",
        "example": "0/1-0/10 → Eth0/0/1 ... Eth0/0/10（S 系列交换机）",
    },
    ("juniper", "junos"): {
        "short": "ge-", "long": "ge-",
        "mode": "input", "slot_count": 2, "tail": [0], "template": "",
        "example": "0/1-0/10 → ge-0/0/1 ... ge-0/0/10",
    },
}

DEFAULT_PORT_RULE = {
    "short": "Gi", "long": "GigabitEthernet",
    "mode": "input", "slot_count": 1, "tail": [], "template": "", "example": "0/1-0/10",
}

# 单次展开的最大端口数，防止用户输入 1/1-9/48 生成上千行把界面卡死
MAX_EXPAND = 512


def get_port_rule(vendor, os_family=None):
    """
    取端口命名规则。优先级：OS 级覆盖 > 厂商级规则 > 默认（思科风格）。
    vendor / os_family 都支持传 slug 或老的显示名（内部会归一化）。
    """
    try:
        import db as _db
        v = _db.normalize_vendor(vendor)
        o = _db.normalize_os(os_family) if os_family else ""
    except Exception:
        v, o = (vendor or "").lower(), (os_family or "").lower()
    if o and (v, o) in OS_PORT_OVERRIDES:
        return OS_PORT_OVERRIDES[(v, o)]
    return PORT_RULES.get(v, DEFAULT_PORT_RULE)


def _parse_port_token(token):
    """'Gi0/1' / '0/0/1' / 'port5' / '1' → [数字段...]；解析失败返回 []"""
    nums = re.findall(r"\d+", token or "")
    return [int(n) for n in nums]


def _normalize_separator(expr):
    """统一各类全角/异形分隔符"""
    text = str(expr or "")
    for ch in ("－", "—", "–", "~", "～", "至"):
        text = text.replace(ch, "-")
    return text.replace("，", ",").replace("；", ",").replace(";", ",")


def parse_port_expr(expr):
    """
    解析端口表达式，返回 (端口数字段列表, 错误信息)。
    每个元素是一个 int 列表，例如 '0/1-0/3' → [[0,1],[0,2],[0,3]]；
    'port1-10' → [[1],[2],...[10]]。
    """
    text = _normalize_separator(expr).strip()
    if not text:
        return [], "端口表达式为空"

    results = []
    for seg in re.split(r"[,\s]+", text):
        seg = seg.strip()
        if not seg:
            continue
        parts = [p for p in seg.split("-") if p.strip() != ""]
        if len(parts) == 1:
            nums = _parse_port_token(parts[0])
            if not nums:
                return [], "无法解析端口：%s" % seg
            results.append(nums)
            continue
        if len(parts) > 2:
            return [], "单段最多一个短横线：%s" % seg

        start, end = _parse_port_token(parts[0]), _parse_port_token(parts[1])
        if not start or not end:
            return [], "无法解析端口区间：%s" % seg

        if len(start) == len(end) and start[:-1] == end[:-1]:
            # 同一槽位内的端口区间：只展开最后一段
            if start[-1] > end[-1]:
                return [], "区间起始端口大于结束端口：%s" % seg
            for p in range(start[-1], end[-1] + 1):
                results.append(start[:-1] + [p])
        elif len(start) == len(end) and len(start) >= 2:
            # 跨槽位区间：如 1/1-2/24，槽位逐级递增，中间槽位从 1 开始
            for slot in range(start[0], end[0] + 1):
                lo = start[-1] if slot == start[0] else 1
                hi = end[-1] if slot == end[0] else max(end[-1], 1)
                if lo > hi:
                    continue
                for p in range(lo, hi + 1):
                    mid = [slot] + start[1:-1]
                    results.append(mid + [p])
        else:
            # 纯数字区间：port1-10
            if start[-1] > end[-1]:
                return [], "区间起始端口大于结束端口：%s" % seg
            for p in range(start[-1], end[-1] + 1):
                results.append([p])

        if len(results) > MAX_EXPAND:
            return results[:MAX_EXPAND], "端口数量超过 %d，已截断" % MAX_EXPAND

    return results, ""


def build_interface_name(nums, vendor, use_long=False, if_type=None, os_family=None):
    """
    把数字段按厂商规则拼成接口名：
        Cisco  [0,1]   → Gi0/1        [1,0,1] → Gi1/0/1（堆叠三段的写法也认）
        华为    [0,1]   → Eth0/0/1      [0,0,1] → Eth0/0/1（不足自动补槽位 0）
        H3C    [0,1]   → Gi1/0/1      [2,0,1] → Gi2/0/1（显式给了槽位就听用户的）
        Arista [1]     → Et1
        Forti  [1]     → port1
    if_type 可显式指定接口前缀（如 "Te"、"xge-"），留空则用厂商默认。
    """
    rule = get_port_rule(vendor, os_family)

    if rule["mode"] == "flat":
        if if_type:
            return "%s%d" % (if_type, nums[-1])
        return rule["template"].format(port=nums[-1])

    prefix = if_type if if_type else (rule["long"] if use_long else rule["short"])
    # 除最后一段是端口号外，前面都是槽位段
    slot_nums = nums[:-1] if nums else []
    port = nums[-1] if nums else 0

    if rule["mode"] == "fixed":
        fixed = list(rule.get("fixed", []))
        # 用户显式写全了槽位段（段数够）就听用户的，否则用厂商默认槽位
        components = slot_nums if len(slot_nums) >= len(fixed) else fixed
    else:
        need = int(rule.get("slot_count", 1))
        if len(slot_nums) >= need:
            components = slot_nums
        else:
            pad = list(rule.get("tail", []))
            if pad:
                missing = need - len(slot_nums)
                components = slot_nums + (pad * (missing // len(pad) + 1))[:missing]
            else:
                components = slot_nums        # Cisco / Arista：单段即端口号

    return "%s%s" % (prefix, "/".join([str(c) for c in components] + [str(port)]))


def expand_port_range(expr, vendor, use_long=False, if_type=None, limit=MAX_EXPAND,
                      separator=",", os_family=None):
    """
    端口范围展开主入口。返回 (展开结果字符串, 列表, 错误/提示信息)。
        "0/1-0/10" + Cisco → "Gi0/1,Gi0/2,...,Gi0/10"
    展开失败时结果为空串与空列表，信息里带原因。
    """
    nums_list, err = parse_port_expr(expr)
    if err and not nums_list:
        return "", [], err
    if not nums_list:
        return "", [], "端口表达式为空"

    names = [build_interface_name(nums, vendor, use_long=use_long, if_type=if_type,
                                 os_family=os_family)
             for nums in nums_list[:limit]]
    return separator.join(names), names, (err or "")




# ---------------------------------------------------------------------------
# 四、模板渲染
# ---------------------------------------------------------------------------

def render(text, values, vendor=None, specs=None, keep_unknown=True, os_family=None):
    """
    渲染命令文本，返回 (渲染结果, 缺失参数列表)。
        - {{name}} 用 values[name] 替换；没给值则用 {{name:默认值}} 的默认值
        - 端口类参数（specs 里 expand == "port"）自动做厂商接口名展开
        - keep_unknown=True 时未填参数保留 {{name}} 原样，方便界面提示"待填写"
    """
    values = values or {}
    spec_map = {}
    for spec in specs or []:
        if isinstance(spec, dict) and spec.get("name"):
            spec_map[spec["name"]] = spec

    missing = []

    def _sub(match):
        name = match.group(1)
        inline_default = (match.group(2) or "").strip()
        raw = values.get(name, "")
        if raw is None or str(raw).strip() == "":
            raw = inline_default
        spec = spec_map.get(name, {})
        if raw is None or str(raw).strip() == "":
            if spec.get("type") == "flag":
                # flag 参数（2026-09-30 裁决的渲染语义）：未勾选/无值 →
                # 占位整体消失（不进 missing、不保留 {{xxx}}），命令行不残留空白残缺
                return ""
            if name not in missing:
                missing.append(name)
            return match.group(0) if keep_unknown else ""

        if spec.get("expand") == "port":
            expanded, _names, err = expand_port_range(str(raw), vendor or spec.get("vendor", ""),
                                                      os_family=os_family)
            if expanded and not err:
                return expanded
            if expanded and err:
                # 展开成功但被截断，仍用展开结果
                return expanded
        return str(raw)

    return PARAM_RE.sub(_sub, text or ""), missing


def render_entry(entry, values, vendor=None, os_family=None):
    """
    按条目渲染：自动合并条目 params 与命令中出现的占位。
    vendor / os_family 不传时取条目自身的取值（决定端口命名规则）。
    返回 (渲染结果, 缺失参数列表, 完整参数规格列表)
    """
    if not entry:
        return "", [], []
    commands = entry.get("commands", "") or ""
    specs = merge_param_specs(_load_params(entry), commands)
    # 默认值先落到 values 里，用户显式传入的值优先
    merged = {}
    for spec in specs:
        if spec.get("default"):
            merged[spec["name"]] = spec["default"]
    merged.update({k: v for k, v in (values or {}).items() if v not in (None, "")})
    text, missing = render(commands, merged, vendor=vendor or entry.get("vendor"),
                           specs=specs, os_family=os_family or entry.get("os_family"))
    return text, missing, specs


def _load_params(entry):
    """从条目里取 params（接受 JSON 字符串或 list），解析失败返回 []"""
    raw = entry.get("params") if isinstance(entry, dict) else None
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 五、注释剔除 / 配置包头部
# ---------------------------------------------------------------------------

def strip_comments(text):
    """
    剔除注释行（! 或 # 开头的行，允许前置空白），并压缩连续空行。
    用于"仅复制命令"——直接把纯命令粘进 Xshell 跑。
    """
    lines = []
    for line in (text or "").splitlines():
        if COMMENT_LINE_RE.match(line):
            continue
        lines.append(line.rstrip())

    # 压缩连续空行（最多保留 1 行）
    compressed = []
    blank = False
    for line in lines:
        if line.strip() == "":
            if blank:
                continue
            blank = True
        else:
            blank = False
        compressed.append(line)
    return "\n".join(compressed).strip("\n")


def split_command_lines(text, skip_comments=False):
    """
    把命令文本拆成"逐条复制"用的行列表。
    skip_comments=True 时剔除注释行；空行一律保留为分隔（但不参与逐条计数）。
    """
    lines = []
    for line in (text or "").splitlines():
        if skip_comments and COMMENT_LINE_RE.match(line):
            continue
        lines.append(line)
    return lines


def count_effective_lines(text):
    """统计有效命令条数（非空、非注释）"""
    n = 0
    for line in (text or "").splitlines():
        if line.strip() and not COMMENT_LINE_RE.match(line):
            n += 1
    return n


def comment_mark(vendor):
    """
    按厂商选注释符：! 用于 Cisco / 华为 / H3C / 锐捷 / 中兴 等；
    # 用于 Fortinet / 深信服 / 天融信 / Palo Alto / Juniper。
    注释行不会被设备执行，粘贴到 Xshell 里也安全（用"仅复制命令"可剔除）。
    vendor 支持 slug 或显示名（内部归一化）。
    """
    try:
        import db as _db
        v = _db.normalize_vendor(vendor)
    except Exception:
        v = (vendor or "").lower()
    return "#" if v in ("fortinet", "sangfor", "topsec", "paloalto", "juniper") else "!"


def build_header(entry=None, operator="", scene="", vendor="", model="", extra=""):
    """
    生成配置包头部注释块（离网环境留痕：设备型号 / 生成时间 / 操作人 / 场景说明）。
    注释符按厂商自动选择（见 comment_mark）。

    ★ 厂商 / OS 要显示中文名而不是库里的 slug —— 这段注释会随配置贴进变更单、
      留在设备上，出现 "zte / zxr10" 这种 slug 对现场没有可读性。
    """
    entry = entry or {}
    vend = vendor or entry.get("vendor", "") or ""
    mark = comment_mark(vend)
    try:
        import db as _db
        vend_show = _db.display_vendor(vend) if vend else "-"
        os_show = _db.display_os(entry.get("os_family"))
    except Exception:
        vend_show = vend or "-"
        os_show = entry.get("os_family", "") or "-"

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "%s ==================================================" % mark,
        "%s  NetToolBox 生成配置包（未经验证，执行前请自行核对）" % mark,
        "%s --------------------------------------------------" % mark,
        "%s  条目名称：%s" % (mark, entry.get("title", "") or "-"),
        "%s  设备厂商：%s" % (mark, vend_show),
        "%s  OS 版本 ：%s" % (mark, os_show),
        "%s  设备型号：%s" % (mark, model or entry.get("models", "") or "-"),
        "%s  场景分类：%s" % (mark, scene or entry.get("category", "") or "-"),
        "%s  生成时间：%s" % (mark, stamp),
        "%s  操 作 人：%s" % (mark, operator or "（未填写）"),
    ]
    if extra:
        lines.append("%s  备    注：%s" % (mark, extra))
    lines.append("%s ==================================================" % mark)
    return "\n".join(lines)



