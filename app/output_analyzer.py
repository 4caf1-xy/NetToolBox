# -*- coding: utf-8 -*-
"""
output_analyzer.py —— 设备输出分析器（模块 04 第 7 轮）

把设备/系统的回显文本解析成「异常项 + 建议下一步」：
    网络侧：show interfaces / show ip route / show log（Cisco/华为/H3C 三家语法并行支持）
    Linux ：ip addr / ss -tlnp / systemctl status / df -h / journalctl / ip route

硬约束：
    ★ 纯本地文本解析，绝不联网、绝不连设备
    ★ 遇到无法识别的格式必须给友好提示，绝不抛异常（粘贴什么都得活下来）
"""

import re

# ---------------------------------------------------------------------------
# 命令类型（用户选择 + 自动探测）
# ---------------------------------------------------------------------------
KINDS = [
    ("interface_detail", "接口详情（show interfaces / display interface）"),
    ("interface_status", "接口状态概览（show interface status / display interface brief）"),
    ("route_table", "路由表（show ip route / display ip routing-table / ip route）"),
    ("log", "日志（show logging / display logbuffer / journalctl）"),
    ("listen_ports", "监听端口（ss -tlnp / netstat）"),
    ("disk", "磁盘（df -h / du）"),
    ("ip_addr", "IP 地址与网卡（ip addr）"),
    ("service_status", "服务状态（systemctl status）"),
    ("resources", "资源占用（top / free）"),
]

# 自动探测特征（★ 顺序即优先级：先具体后宽泛）
#   注意 show interfaces status 的表头里也有 "Duplex"、且 down 端口会带 (notconnect)，
#   所以"接口详情"的特征必须收紧成 input errors / CRC / Duplex: Half 这种详情专属词，
#   不能用裸 duplex —— 否则概览会被误判成详情（反之亦然）。
KIND_HINTS = [
    ("interface_detail", r"(input errors|output errors|CRC\s|Line protocol is|"
                         r"Duplex:\s*(Half|Full)|duplex[^\n]*mismatch|Last clearing|"
                         r"5 minute input rate)"),
    ("interface_status", r"(connected\s+\d+|notconnect|err-disabled|\bsuspended\b|"
                         r"PHY:\s*(up|down)|Protocol:\s*(up|down)|Status\s+Vlan|"
                         r"sfpAbsent)"),
    ("route_table", r"(Codes:|Gateway of last resort|Routing Tables|"
                    r"^S\*|^\s*C\s+\d+\.|Destination/Mask|Proto\s+Pre)"),
    ("log", r"(%LINK-|%LINE-|%SPANTREE|%\w+-\d-\w+|logbuffer|-- Logs begin|"
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d+\s+\d+:)"),
    ("listen_ports", r"(LISTEN|Local Address:Port|State\s+Recv-Q|tcp\s+LISTEN)"),
    ("disk", r"(Filesystem\s+Size\s+Used|Use%\s+Mounted|df: )"),
    ("ip_addr", r"(^\d+:\s+\S+:|inet \d+\.\d+\.\d+\.\d+|link/ether|state UP|state DOWN)"),
    ("service_status", r"(Active:\s+(active|failed|inactive)|Loaded:.*;\s*(enabled|disabled)|"
                        r"systemd\[1\])"),
    ("resources", r"(%Cpu|KiB Mem|MiB Mem|load average|Tasks:\s+\d+|Mem:\s+\d+)"),
]

# ---------------------------------------------------------------------------
# 规则表：每条 {re, sev, title, advice, tree}
#   sev：error（红）/ warn（黄）/ info（蓝）
#   tree：可选的 (tree_id, step_id)，用于界面上的「去排查树」
# ---------------------------------------------------------------------------
RULES = {
    "interface_detail": [
        {"re": r"CRC", "sev": "error", "title": "CRC 校验错误",
         "advice": "物理层质量问题：更换线缆/光模块、清洁光纤头，再复查计数是否停止增长"},
        {"re": r"input errors\s+(\d+)", "sev": "warn", "title": "输入错误计数",
         "advice": "入方向错误：查线缆/光模块/对端发送质量；计数持续增长才需处理"},
        {"re": r"output errors\s+(\d+)", "sev": "warn", "title": "输出错误计数",
         "advice": "出方向错误：多为协商或物理层问题，核对两端速率双工"},
        {"re": r"(duplex[^,\n]*mismatch|Duplex:\s*Half|Half-duplex)", "sev": "error",
         "title": "双工不匹配 / 半双工",
         "advice": "两端统一固定速率双工或统一 auto，半双工会造成丢包与冲突计数上升"},
        {"re": r"Line protocol is down|protocol state\s+DOWN|Protocol:\s*down", "sev": "error",
         "title": "协议层 down",
         "advice": "物理 up 而协议 down：查对端配置/封装/VLAN；两端都 down 先查物理层"},
        {"re": r"administratively down", "sev": "warn", "title": "端口被手工关闭",
         "advice": "接口配置了 shutdown，确认无业务影响后 no shutdown 恢复"},
        {"re": r"(reset|Resets)\s+\d+", "sev": "info", "title": "接口重协商计数",
         "advice": "重置计数不为 0 说明链路有抖动，结合日志看时间点"},
    ],
    "interface_status": [
        {"re": r"err-disabled", "sev": "error", "title": "端口被 err-disable",
         "advice": "先查原因日志（端口安全/BPDU 违规/环路），处理后 shutdown + no shutdown 恢复",
         "tree": ("port-errdisable", "s1")},
        {"re": r"\bnotconnect\b|\bdown\b", "sev": "warn", "title": "端口未连接 / down",
         "advice": "物理层未起：查线缆、对端端口、光模块；对端是否 shutdown",
         "tree": ("port-down", "s1")},
        {"re": r"\bsfpAbsent\b|\bSFP\b.*(not present|absent)", "sev": "error",
         "title": "光模块缺失",
         "advice": "光口没插模块或模块不识别：插好模块、核对兼容性",
         "tree": ("port-optical", "s1")},
        {"re": r"\bdisabled\b", "sev": "warn", "title": "端口被管理性关闭",
         "advice": "确认无业务后恢复端口"},
        {"re": r"\bsuspended\b", "sev": "error", "title": "端口被挂起（STP/环路保护）",
         "advice": "多为生成树保护触发：查环路与 BPDU 保护配置",
         "tree": ("perf-broadcast-storm", "s2")},
        {"re": r"\bmonitoring\b|\bshared\b", "sev": "info", "title": "端口处于镜像/共享状态",
         "advice": "核对是否为镜像口或共享口，避免误判为业务口"},
    ],
    "route_table": [
        {"re": r"Gateway of last resort is not set|default.*not\s+set", "sev": "warn",
         "title": "未设置默认网关",
         "advice": "无默认路由将导致跨网段不通：按静态路由条目补默认路由",
         "tree": ("conn-cross-subnet", "s3")},
        {"re": r"\bvia \d+\.\d+\.\d+\.\d+", "sev": "info", "title": "存在下一跳路由",
         "advice": "核对下一跳是否可达（ping 一下），可达才生效",
         "tree": ("route-static-broken", "s2")},
        {"re": r"\b(OSPF|BGP|EIGRP)\b", "sev": "info", "title": "存在动态路由条目",
         "advice": "动态路由正常；若特定网段不通注意优先级与过滤策略"},
        {"re": r"%\s*(Network not in table|unreachable)", "sev": "error",
         "title": "缺路由（Network not in table）",
         "advice": "目的网段没有路由：补静态路由或检查动态协议宣告",
         "tree": ("route-static-broken", "s1")},
    ],
    "log": [
        {"re": r"%LINK-\d-UPDOWN|%LINE-\d-UPDOWN", "sev": "warn", "title": "链路状态翻转",
         "advice": "看翻转频率与时间点：偶发=线缆/模块，频繁=环路或对端重启",
         "tree": ("port-slow-loss", "s1")},
        {"re": r"(LOOP|loopback detected|MAC.*flapping)", "sev": "error", "title": "疑似环路",
         "advice": "立即按风暴/环路树排查，必要时 shutdown 嫌疑端口止血",
         "tree": ("perf-broadcast-storm", "s2")},
        {"re": r"err-disabled", "sev": "error", "title": "端口被 err-disable",
         "advice": "从本行往上看原因（psecure-violation/bpduguard/loopback）",
         "tree": ("port-errdisable", "s2")},
        {"re": r"%SPANTREE-\d-|TOPOLOGY_CHANGE", "sev": "warn", "title": "生成树拓扑变化",
         "advice": "频繁拓扑变化会引发短暂 flooding：查环路与端口抖动"},
        {"re": r"\berror\b|\bError\b|\bFAIL", "sev": "warn", "title": "含 error/fail 的日志行",
         "advice": "结合上下文与时间点定位；Linux 侧可配合 journalctl -u 服务名",
         "tree": ("linux-service-fail", "s2")},
        {"re": r"authentication fail|login failed|denied", "sev": "warn",
         "title": "认证失败记录",
         "advice": "排查口令/认证方式；连续失败注意是否被锁定",
         "tree": ("mgmt-ssh-fail", "s6")},
    ],
    "listen_ports": [
        {"re": r"127\.0\.0\.1:(\d+)", "sev": "warn", "title": "仅监听回环地址",
         "advice": "只监听 127.0.0.1 时外部访问不通：改服务绑定地址为 0.0.0.0 或业务 IP",
         "tree": ("linux-port-closed", "s3")},
        {"re": r"::1:(\d+)", "sev": "info", "title": "仅监听 IPv6 回环",
         "advice": "确认业务是否需要 IPv4 监听"},
        {"re": r"LISTEN", "sev": "info", "title": "存在监听端口",
         "advice": "核对目标端口是否在列表中——不在列表说明服务没起，而不是防火墙问题",
         "tree": ("linux-port-closed", "s1")},
    ],
    "disk": [
        {"re": r"(9[0-9]|100)%", "sev": "error", "title": "磁盘使用率超过 90%",
         "advice": "立即清理：journalctl --vacuum-size / 截断大日志；df 满 du 找不到 → 查 deleted 句柄",
         "tree": ("linux-disk-full", "s2")},
        {"re": r"8[5-9]%", "sev": "warn", "title": "磁盘使用率超过 85%",
         "advice": "按磁盘满树提前清理，避免写满导致服务异常",
         "tree": ("linux-disk-full", "s1")},
        {"re": r"^\S+:\s+No such file or directory", "sev": "info", "title": "挂载点不存在",
         "advice": "核对挂载点与 fstab 配置"},
    ],
    "ip_addr": [
        {"re": r"state DOWN", "sev": "error", "title": "网卡 DOWN",
         "advice": "ip link set 网卡 up 后复查；物理层不通查线缆/虚机网卡",
         "tree": ("linux-same-subnet", "s2")},
        {"re": r"\bNO-CARRIER\b", "sev": "error", "title": "无载波（网线未通）",
         "advice": "物理链路问题：查网线/网口/交换机端口状态",
         "tree": ("linux-same-subnet", "s4")},
        {"re": r"dynamic", "sev": "info", "title": "地址为动态获取（DHCP）",
         "advice": "业务设备建议改为静态地址，避免租约变化导致不通"},
        {"re": r"mtu\s+(\d+)", "sev": "info", "title": "MTU 值",
         "advice": "两端 MTU 不一致会导致大包不通（能 ping 小包不能传文件）"},
        {"re": r"scope link", "sev": "info", "title": "仅链路本地地址",
         "advice": "没有可路由地址：按网络配置条目配 IP"},
    ],
    "service_status": [
        {"re": r"Active:\s+failed", "sev": "error", "title": "服务启动失败",
         "advice": "看下面的日志行与 journalctl -u 服务名：多为配置语法错/端口占用/依赖未起",
         "tree": ("linux-service-fail", "s1")},
        {"re": r"Active:\s+inactive", "sev": "warn", "title": "服务未运行",
         "advice": "systemctl start（并 enable 设开机自启）",
         "tree": ("linux-service-fail", "s4")},
        {"re": r"Active:\s+active", "sev": "info", "title": "服务运行中",
         "advice": "服务本身正常，问题可能在监听地址/防火墙/上游"},
        {"re": r"disabled", "sev": "warn", "title": "未设置开机自启",
         "advice": "systemctl enable 服务名 防止重启后服务缺失"},
        {"re": r"(address already in use|Address already in use)", "sev": "error",
         "title": "端口被占用",
         "advice": "ss -tlnp 找占用进程，停冲突服务或改端口",
         "tree": ("linux-service-fail", "s3")},
    ],
    "resources": [
        {"re": r"(%?wa)\s*[:=]?\s*([2-9]\d|100)(\.\d+)?\s*%?", "sev": "warn",
         "title": "IO 等待偏高（%wa）",
         "advice": "磁盘瓶颈：查空间是否将满与大 IO 任务",
         "tree": ("linux-cpu-high", "s3")},
        {"re": r"load average:\s*(\d+)", "sev": "warn", "title": "负载偏高",
         "advice": "结合 CPU 核数判断：负载 > 核数说明排队；看 CPU 分布定方向",
         "tree": ("linux-cpu-high", "s1")},
        {"re": r"^\s*(\d+)\s+\S+\s+(\S+)\s+.*\s(\d+(\.\d+)?)\s+\d+(\.\d+)?\s+\d+(\.\d+)?\s+\S+\s+\S+\s+(\S+)",
         "sev": "info", "title": "进程占用行", "advice": "关注 %CPU/%MEM 最高的进程，先定性再动手"},
        {"re": r"Mem:.*(9[0-9]|100)%|available\s+(\d+)(\.\d+)?\s*(Mi|Gi)", "sev": "warn",
         "title": "内存吃紧或可用内存偏低",
         "advice": "free 看 available；swap 频繁使用说明内存不足",
         "tree": ("linux-cpu-high", "s4")},
    ],
}

SEVERITY_STYLE = {
    "error": ("#e5534b", "错误"),
    "warn": ("#e8a33d", "警告"),
    "info": ("#5aa9f0", "提示"),
}
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def detect_kind(text):
    """按内容特征猜命令类型；猜不出返回 None（由用户手选）"""
    text = text or ""
    for kind, pattern in KIND_HINTS:
        try:
            if re.search(pattern, text, re.MULTILINE | re.IGNORECASE):
                return kind
        except re.error:
            continue
    return None


def _safe_search(pattern, line):
    """单条规则匹配（坏正则不影响整体）"""
    try:
        return re.search(pattern, line, re.IGNORECASE)
    except re.error:
        return None


def analyze(text, kind=None, max_findings=200):
    """
    解析输出文本，返回 (findings, note)。
        findings：[{severity, title, advice, line_no, line, tree}, ...]
        note    ：无法识别时的友好提示（正常为 ""）
    ★ 绝不抛异常：任何解析问题都退化成"未识别"提示。
    """
    text = text or ""
    if not text.strip():
        return [], "没有可分析的文本——请先粘贴设备/系统的回显内容。"

    if not kind:
        kind = detect_kind(text)
        if not kind:
            return [], ("没识别出已知格式。可以在上方手动选择命令类型后重试；"
                        "常见可分析类型：接口详情、路由表、日志、监听端口、磁盘、"
                        "服务状态、资源占用。")

    rules = RULES.get(kind) or []
    findings = []
    seen = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        for rule in rules:
            m = _safe_search(rule["re"], line)
            if not m:
                continue
            key = (rule["title"], rule["advice"])
            if key in seen:
                continue        # 同类问题只报一次（首处为准），避免刷屏
            seen.add(key)
            findings.append({
                "severity": rule["sev"],
                "title": rule["title"],
                "advice": rule["advice"],
                "line_no": line_no,
                "line": line.strip()[:200],
                "tree": rule.get("tree"),
            })
            if len(findings) >= max_findings:
                break
        if len(findings) >= max_findings:
            break

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["line_no"]))
    note = ""
    if not findings:
        note = ("该类型下没有匹配到已知异常特征——说明这份输出看起来是正常的，"
                "或该版本输出格式有差异。可换命令类型再试。")
    return findings, note


def kind_label(kind):
    for k, label in KINDS:
        if k == kind:
            return label
    return kind or "未指定"
