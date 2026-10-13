# -*- coding: utf-8 -*-
"""
ai_gate.py —— AI 发送脱敏门禁（安全加固批 2）

威胁模型：AI 链路把设备信息（内网 IP、明文口令/SNMP community、构建期口令）
外发给云端端点。发送前对「本轮 payload」做引擎级扫描（复用 app/sanitize_scan.py
红线件的 IP 正则 + secret 真值 + 明文黑名单，逐字同一套规则），命中 → 预览页
逐项放行 → 放行项原文、未放行项脱敏替换后发送。

IP 门禁策略（批 2 裁决，比站点白名单严）：
    拦    RFC1918（10/8、172.16/12、192.168/16）+ 白名单外真实地址
    豁免  TEST-NET 文档段（192.0.2/198.51.100/203.0.113）· loopback 127/8 ·
          0.0.0.0/255.255.255.255 与全 0/255 掩码形态 · 公共 DNS ·
          x.x.x.x 同段重复教学惯例
    ★ 200.1.1.0/24 NAT 教学段不在豁免清单（站点白名单有、门禁无）→ 命中可逐项放行

放行记忆（当日有效）：指纹 = kind+value 的 sha256 前缀，存 ai_config.json 的
gate_allowances 字段（{指纹: "YYYY-MM-DD"}），加载时剪掉非当日条目。
扫描范围（批 2 裁决）：仅本轮 —— 设备上下文六字段 + 正文 + 文本附件；
历史轮上一轮已扫过，不重扫。AI 未配置时 on_send 在门禁之前已 return，
门禁自然不生效。

硬约束：零新依赖（hashlib/ipaddress 标准库；引擎复用 sanitize_scan）。
"""

import datetime
import hashlib

import ai_bridge
import sanitize_scan

# 门禁 IP 白名单（裁决 2026-10-13）：TEST-NET 文档段 + loopback
GATE_ALLOWED_NETS = [sanitize_scan.ip_network(n) for n in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",   # TEST-NET 文档段
    "127.0.0.0/8",                                          # loopback
)]
GATE_ALLOWED_IPS = {"0.0.0.0", "255.255.255.255"}           # 通配 / 广播
GATE_ALLOWED_DNS = set(sanitize_scan.PUBLIC_DNS)            # 公共 DNS（原样复用）

# 掩码/全 0/255 形态与 x.x.x.x 同段重复教学惯例由 sanitize_scan._ip_allowed
# 内建规则放行（门禁沿用）；NAT 教学段 200.1.1.0/24 门禁不豁免。

MASK_SECRET = "【已脱敏】"
MASK_IP = "【IP已脱敏】"

ALLOWANCE_FIELD = "gate_allowances"     # ai_config.json 字段名

# 设备上下文字段 → 预览页展示名
CTX_LABELS = {
    "vendor": "厂商", "os": "OS 版本", "model_name": "设备型号",
    "symptom": "故障现象", "echo": "命令回显", "steps": "已尝试步骤",
}


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def build_pieces(ctx, text, attachments):
    """
    组装本轮待扫文本块（批 2 裁决范围：ctx 六字段 + 正文 + 文本附件）。
    返回 [{"label": str, "text": str}]，顺序稳定（redact 按同序写回）。
    图片附件不在扫描范围（走 vision 模型的多模态通道，另行治理）。
    """
    pieces = []
    for key, label in CTX_LABELS.items():
        value = str((ctx or {}).get(key) or "")
        if value.strip():
            pieces.append({"label": "上下文·%s" % label, "text": value})
    text = str(text or "")
    if text.strip():
        pieces.append({"label": "正文", "text": text})
    for att in attachments or []:
        if att.get("is_image"):
            continue
        content = str(att.get("content") or "")
        if content.strip():
            pieces.append({"label": "附件·%s" % (att.get("name") or "未命名"),
                           "text": content})
    return pieces


def scan_pieces(pieces):
    """逐块扫（门禁 IP 策略），finding 附 piece 下标与块名。"""
    findings = []
    for idx, piece in enumerate(pieces or []):
        for f in sanitize_scan.scan_free_text(
                piece.get("text") or "",
                GATE_ALLOWED_NETS, GATE_ALLOWED_IPS, GATE_ALLOWED_DNS,
                allow_nat_teaching=False):
            f["piece"] = idx
            f["piece_label"] = piece.get("label") or ("块 %d" % idx)
            findings.append(f)
    return findings


# ---------------------------------------------------------------------------
# 指纹与当日放行记忆
# ---------------------------------------------------------------------------
def fingerprint(finding):
    """命中指纹：kind + 精确值的 sha256 前 16 位（同值同指纹，跨会话稳定）。"""
    raw = "%s|%s" % (finding.get("kind") or "", finding.get("value") or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def day_stamp():
    return datetime.date.today().isoformat()


def load_allowances(cfg):
    """读当日放行指纹（非当日条目直接剪掉 → 隔日自动失效）。"""
    raw = (cfg or {}).get(ALLOWANCE_FIELD) or {}
    today = day_stamp()
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items()
            if isinstance(v, str) and v == today}


def remember_fps(cfg, fps):
    """把新放行指纹写入配置并落盘（当日戳；失败静默——门禁不因记忆失败阻断）。"""
    try:
        allowances = dict(load_allowances(cfg))
        today = day_stamp()
        for fp in fps or []:
            allowances[str(fp)] = today
        cfg[ALLOWANCE_FIELD] = allowances
        ai_bridge.save_config(cfg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 脱敏回写
# ---------------------------------------------------------------------------
def redact_text(text, findings, allowed_fps):
    """未放行命中的 span 替换为掩码（放行项原文保留）。返回新文本。"""
    out = str(text or "")
    spans = []
    for f in findings or []:
        if f.get("fingerprint") in (allowed_fps or set()):
            continue
        s, e = int(f.get("start") or 0), int(f.get("end") or 0)
        if e > s:
            spans.append((s, e, f.get("kind")))
    spans.sort()
    # 从后往前替换；重叠区间取并（前一个已替换的部分跳过）
    merged = []
    for s, e, kind in spans:
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e), merged[-1][2])
            continue
        merged.append((s, e, kind))
    for s, e, kind in reversed(merged):
        mask = MASK_IP if kind == "IP" else MASK_SECRET
        out = out[:s] + mask + out[e:]
    return out


def apply_redaction(pieces, findings, allowed_fps):
    """按 piece 回写脱敏文本。返回 {piece 下标: 新文本}（只含有变化的块）。"""
    by_piece = {}
    for f in findings or []:
        by_piece.setdefault(int(f.get("piece") or 0), []).append(f)
    result = {}
    for idx, piece in enumerate(pieces or []):
        fs = by_piece.get(idx) or []
        if not fs:
            continue
        new_text = redact_text(piece.get("text") or "", fs, allowed_fps)
        if new_text != (piece.get("text") or ""):
            result[idx] = new_text
    return result
