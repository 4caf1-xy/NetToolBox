# -*- coding: utf-8 -*-
"""
tool_calc.py —— 网工计算器引擎（R 批 2026-11-02）

两块纯函数能力，零 GUI、零联网、零第三方依赖：
    1. 子网掩码 / CIDR 计算：IP + 前缀 → 网络地址、广播地址、掩码、可用主机范围与数量
    2. 进制转换：二 / 八 / 十 / 十六 互转（含前缀识别与 64 位上限）

设计纪律（用户口径：非法输入明确报错不静默）：
    · 任何非法输入一律抛 CalcError（ValueError 子类），消息是给人看的中文；
    · 绝不"猜"用户意图：前导零、负号、越界前缀、非法数位都直接报错并说清原因；
    · 边界按 RFC 与业界共识处理：/31 走 RFC 3021（点对点，2 个可用地址）、
      /32 为单主机（1 个可用地址），两者都不存在"网络地址/广播地址"的保留概念。

命令行自测（无需 PyQt5）：
    python app/tool_calc.py
"""

import ipaddress


class CalcError(ValueError):
    """计算器输入错误（消息直接展示给用户）"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _int_to_ip(value):
    return str(ipaddress.IPv4Address(value & 0xFFFFFFFF))


def _ip_to_int(text):
    return int(ipaddress.IPv4Address(text))


def normalize_ip(text):
    """
    严格解析 IPv4 点分十进制。返回规范字符串（去掉多余空格）。
    拒绝：非 4 段、非数字段、越界、前导零（010 在 inet_aton 里会被当八进制，静默换算 = 事故源）。
    """
    raw = (text or "").strip()
    if not raw:
        raise CalcError("IP 地址为空，请填写如 192.168.1.10")
    if "/" in raw:
        raise CalcError("IP 字段里不要带前缀（收到 %r）；前缀请单独选择或写进 CIDR 模式" % raw)
    parts = raw.split(".")
    if len(parts) != 4:
        raise CalcError("IPv4 必须是 4 段（收到 %d 段）：%s" % (len(parts), raw))
    for i, part in enumerate(parts, 1):
        if not part.isdigit():
            raise CalcError("第 %d 段不是十进制数字：%r" % (i, part))
        if len(part) > 1 and part[0] == "0":
            raise CalcError("第 %d 段 %r 带前导零：不同工具会按八进制解析，本工具拒绝猜测，请写成 %d"
                            % (i, part, int(part)))
        value = int(part)
        if value > 255:
            raise CalcError("第 %d 段 %d 超过 255" % (i, value))
    return ".".join(str(int(p)) for p in parts)


def normalize_prefix(value):
    """前缀转 int（接受 '/24'、' 24 '、'24'），范围 0-32"""
    text = str(value).strip()
    if not text:
        raise CalcError("前缀为空，请填写 0-32 的整数（如 24）")
    if text.startswith("/"):
        text = text[1:].strip()
    if not text.isdigit():
        raise CalcError("前缀必须是 0-32 的整数（收到 %r）" % value)
    prefix = int(text)
    if not 0 <= prefix <= 32:
        raise CalcError("前缀 %d 越界：IPv4 只允许 0-32" % prefix)
    return prefix


def prefix_to_mask(prefix):
    """前缀 → 点分十进制掩码（/0 → 0.0.0.0，/32 → 255.255.255.255）"""
    prefix = normalize_prefix(prefix)
    return str(ipaddress.IPv4Network("0.0.0.0/%d" % prefix).netmask)


def mask_to_prefix(mask):
    """掩码 → 前缀（拒绝非连续掩码，如 255.0.255.0）"""
    text = normalize_ip(mask)
    try:
        network = ipaddress.IPv4Network("0.0.0.0/%s" % text)
    except ValueError:
        raise CalcError("掩码 %s 不是连续 1 掩码（如 255.255.255.0）" % text)
    return network.prefixlen


def _scope_of(network_int, prefix):
    """地址段用途（给现场判断「这是私网还是公网」用）"""
    net = ipaddress.IPv4Network("%s/%d" % (_int_to_ip(network_int), prefix), strict=False)
    if net.is_loopback:
        return "环回（127.0.0.0/8）"
    if net.is_link_local:
        return "链路本地（169.254.0.0/16）"
    if net.is_multicast:
        return "组播（224.0.0.0/4）"
    if net.is_private:
        return "私有（RFC 1918）"
    if str(net.network_address) == "0.0.0.0":
        return "未指定地址（0.0.0.0/8）"
    if net.is_reserved:
        return "保留（IANA 特殊用途）"
    return "公网（可路由）"


def _class_of(first_octet):
    """传统 A/B/C/D/E 分类（仅作参考，实际以掩码为准）"""
    if 1 <= first_octet <= 126:
        return "A 类（1-126）"
    if first_octet == 127:
        return "环回（127）"
    if 128 <= first_octet <= 191:
        return "B 类（128-191）"
    if 192 <= first_octet <= 223:
        return "C 类（192-223）"
    if 224 <= first_octet <= 239:
        return "D 类组播（224-239）"
    return "E 类保留（240-255）"


def parse_cidr(text):
    """
    解析 CIDR 或「IP + 前缀」两种输入（UI 的便捷入口）：
        "192.168.1.130/26"  → ("192.168.1.130", 26)
        "192.168.1.130 26"  → 同上
        "192.168.1.130"     → 前缀位缺省报错，由调用方决定是否给默认值
    """
    raw = (text or "").strip()
    if not raw:
        raise CalcError("请输入 IP 或 CIDR（如 192.168.1.130/24）")
    if "/" in raw:
        ip_part, _, prefix_part = raw.partition("/")
        if not prefix_part.strip():
            raise CalcError("CIDR 的斜杠后缺前缀：%r" % raw)
        return normalize_ip(ip_part), normalize_prefix(prefix_part)
    if " " in raw:
        ip_part, _, prefix_part = raw.partition(" ")
        if prefix_part.strip():
            return normalize_ip(ip_part), normalize_prefix(prefix_part)
    return normalize_ip(raw), None


# ---------------------------------------------------------------------------
# 子网计算
# ---------------------------------------------------------------------------
def subnet_info(ip_text, prefix=None):
    """
    计算子网信息。返回 dict（字段固定，UI 与冒烟都按字段名断言）：

        input / ip / prefix / netmask / wildcard / network / broadcast /
        first_host / last_host / host_count / total_addrs / cidr /
        ip_int / mask_int / class / scope / usable_note / notes(list)
    """
    ip_part, cidr_prefix = parse_cidr(ip_text)
    if prefix in (None, ""):
        if cidr_prefix is None:
            raise CalcError("缺前缀：请选择 0-32 的前缀（或在 IP 框里写 /24 形式的 CIDR）")
        prefix = cidr_prefix
    else:
        prefix = normalize_prefix(prefix)
        if cidr_prefix is not None and cidr_prefix != prefix:
            raise CalcError("输入冲突：IP 框里的前缀 /%d 与选择的前缀 /%d 不一致，请只留一处"
                            % (cidr_prefix, prefix))

    ip_int = _ip_to_int(ip_part)
    total = 1 << (32 - prefix)
    mask_int = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    network_int = ip_int & mask_int
    broadcast_int = network_int | (~mask_int & 0xFFFFFFFF)

    notes = []
    if prefix == 32:
        host_count = 1
        first_host = last_host = ip_part
        usable_note = "单主机路由（/32）：仅 1 个可用地址，无网络/广播地址概念"
        notes.append("/32 常用于主机路由或指定单一目标（如 ip route add 10.0.0.1/32）。")
    elif prefix == 31:
        host_count = 2
        first_host = _int_to_ip(network_int)
        last_host = _int_to_ip(broadcast_int)
        usable_note = "点对点链路（/31，RFC 3021）：2 个可用地址，不保留网络/广播地址"
        notes.append("/31 用于点对点互联（/30 的省地址替代方案），两端都是可用地址。")
    else:
        host_count = total - 2
        first_host = _int_to_ip(network_int + 1)
        last_host = _int_to_ip(broadcast_int - 1)
        usable_note = "可用主机数 = 2^(32-前缀) - 2（扣掉网络地址与广播地址）"
        if prefix == 30:
            notes.append("/30 只有 2 个可用地址，典型用于点对点互联。")

    if prefix <= 30 and host_count > 0:
        notes.append("首地址 %s 为网络地址、末地址 %s 为广播地址，不能配到主机上。"
                     % (_int_to_ip(network_int), _int_to_ip(broadcast_int)))
    if prefix < 8:
        notes.append("前缀 /%d 覆盖过大（%d 个地址），实际规划中极少直接下发，请确认是否笔误。"
                     % (prefix, total))

    return {
        "input": "%s/%d" % (ip_part, prefix),
        "ip": ip_part,
        "prefix": prefix,
        "netmask": _int_to_ip(mask_int),
        "wildcard": _int_to_ip(~mask_int & 0xFFFFFFFF),
        "network": _int_to_ip(network_int),
        "broadcast": _int_to_ip(broadcast_int) if prefix <= 30 else "-",
        "first_host": first_host,
        "last_host": last_host,
        "host_count": host_count,
        "total_addrs": total,
        "cidr": "%s/%d" % (_int_to_ip(network_int), prefix),
        "ip_int": ip_int,
        "mask_int": mask_int,
        "class": _class_of(ip_int >> 24),
        "scope": _scope_of(network_int, prefix),
        "usable_note": usable_note,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 进制转换
# ---------------------------------------------------------------------------
BASE_INFO = {
    2: ("二进制", "0b", set("01")),
    8: ("八进制", "0o", set("01234567")),
    10: ("十进制", "", set("0123456789")),
    16: ("十六进制", "0x", set("0123456789abcdef")),
}
MAX_BITS = 64
MAX_VALUE = (1 << MAX_BITS) - 1


def _group_bits(bits, size=4):
    """二进制按 4 位分组（从右往左分，便于按位核对）"""
    padded = bits.zfill((len(bits) + size - 1) // size * size)
    return " ".join(padded[i:i + size] for i in range(0, len(padded), size))


def convert(text, from_base):
    """
    进制转换：把 text（按 from_base 解释）转成四进制输出。
    返回 dict：value(int) / dec / hex / oct / bin / bin_grouped / bits / input_note
    """
    if from_base not in BASE_INFO:
        raise CalcError("源进制只支持 2 / 8 / 10 / 16（收到 %r）" % (from_base,))
    name, prefix, alphabet = BASE_INFO[from_base]
    raw = (text or "").strip().lower()
    if not raw:
        raise CalcError("请输入要转换的数值")
    if raw.startswith("-") or raw.startswith("+"):
        raise CalcError("本工具只处理非负整数（网络场景的地址/掩码/端口都不带符号）")
    if raw.startswith("0x") or raw.startswith("0b") or raw.startswith("0o"):
        given = {"0x": 16, "0b": 2, "0o": 8}[raw[:2]]
        if given != from_base:
            raise CalcError("前缀 %s 表示 %d 进制，与所选源进制 %d 不一致"
                            % (raw[:2], given, from_base))
        raw = raw[2:]
    if not raw:
        raise CalcError("只给了前缀没有数值")
    if "_" in raw or "," in raw or " " in raw:
        raise CalcError("数值里不要带分隔符（下划线/逗号/空格）：请写成连续数字")
    for idx, ch in enumerate(raw, 1):
        if ch not in alphabet:
            raise CalcError("第 %d 个字符 %r 不是%s的合法数位" % (idx, ch, name))
    value = int(raw, from_base)
    if value > MAX_VALUE:
        raise CalcError("数值超过 64 位上界（2^64-1 = 18446744073709551615）")
    bits = value.bit_length() if value else 1
    return {
        "value": value,
        "dec": str(value),
        "hex": "0x%X" % value,
        "oct": "0o%o" % value,
        "bin": bin(value),
        "bin_grouped": _group_bits(bin(value)[2:]),
        "bits": bits,
        "input_note": "%s → %d（十进制）" % (name, value),
    }


def convert_auto(text):
    """按前缀自动判定源进制（无前缀按十进制）——UI 的「自动识别」档位用"""
    raw = (text or "").strip().lower()
    if raw.startswith("0x"):
        return convert(raw, 16)
    if raw.startswith("0b"):
        return convert(raw, 2)
    if raw.startswith("0o"):
        return convert(raw, 8)
    return convert(raw, 10)


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def _selftest():
    cases = [
        ("192.168.1.130", 24), ("10.0.0.0", 8), ("172.16.5.7", 16),
        ("192.168.1.0", 31), ("10.1.1.1", 32), ("1.2.3.4", 0), ("192.168.1.1", 30),
    ]
    for ip, prefix in cases:
        info = subnet_info(ip, prefix)
        print("%-18s → 网络 %-15s 广播 %-15s 掩码 %-15s 可用 %s~%s（%d 个）"
              % (info["input"], info["network"], info["broadcast"], info["netmask"],
                 info["first_host"], info["last_host"], info["host_count"]))
    print("-" * 60)
    for text, base in (("255", 10), ("0xFF", 16), ("0b1010", 2), ("0o755", 8)):
        out = convert(text, base)
        print("%-8s(%2d) → dec %s | hex %s | oct %s | bin %s"
              % (text, base, out["dec"], out["hex"], out["oct"], out["bin"]))
    print("-" * 60)
    for bad in ("192.168.01.1", "256.1.1.1", "1.2.3", "1.2.3.4.5", "abc"):
        try:
            normalize_ip(bad)
            print("[X] 未拦截：%s" % bad)
        except CalcError as exc:
            print("[OK] 拒绝 %-14s → %s" % (bad, exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
