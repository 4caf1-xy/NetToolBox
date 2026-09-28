# -*- coding: utf-8 -*-
"""
error_match.py —— 报错诊断的本地正则匹配引擎（模块 05）

职责：
    1. 厂商自动识别（按报错签名逐行扫描，识别失败让用户手选）
    2. 逐行 re.search（用户会带着提示符粘贴，不能用 fullmatch）
    3. 一段文本含多个不同报错 → 全部列出，不合并不遗漏
    4. 排序：pattern 越具体越靠前，其次 hit_count 降序
    5. 命中即 hit_count+1；零匹配返回空列表（由 UI 写入待解决收件箱）

硬约束：
    ★ 纯本地正则，不调用任何在线服务
    ★ 所有正则在编译期 try/except 兜底：坏正则不能拖垮整个匹配流程
"""

import re

# ---------------------------------------------------------------------------
# 厂商签名表（按优先级顺序扫描；key = vendor slug）
# ---------------------------------------------------------------------------
VENDOR_SIGNATURES = [
    # 思科：% 开头的错误行是它最显著的指纹
    ("cisco", re.compile(r"%\s*(Invalid input|Ambiguous command|Incomplete command|"
                         r"Command rejected|Login invalid|Authentication failed|"
                         r"Unrecognized host|Error opening)", re.I)),
    # 华为：Error: 前缀（VRP 英文模式）或中文"错误："，注意与 H3C 的 % 前缀区分
    ("huawei", re.compile(r"(Error:\s*\w+|错误[:：])", re.I)),
    # H3C：% Unrecognized command found at '^' position 是 Comware 指纹
    ("h3c", re.compile(r"%\s*Unrecognized command found|%\s*Wrong parameter|"
                       r"%\s*Incomplete command found", re.I)),
    # Juniper：error: 小写语法
    ("juniper", re.compile(r"error:\s*(syntax error|configuration)", re.I)),
    # 锐捷：与思科同构，靠上下文（跟在 cisco 之后作为次选）
    ("ruijie", re.compile(r"%\s*(Invalid input|Ambiguous command|Incomplete command)", re.I)),
    # Fortinet：Command fail. Error code / -5 一类
    ("fortinet", re.compile(r"Command fail\.|Unknown action 0", re.I)),
    # Palo Alto：set 命令报错 / invalid syntax
    ("paloalto", re.compile(r"(?:invalid syntax|Server Unavailable)", re.I)),
    # Linux：发行版无关的通用错误
    ("linux", re.compile(r"(command not found|Permission denied|No such file or directory|"
                         r"Address already in use|Connection refused|Connection timed out|"
                         r"Network is unreachable|No space left on device|"
                         r"Read-only file system|not in the sudoers file|"
                         r"Failed to start|Too many open files)", re.I)),
]


def guess_vendor(text):
    """
    按报错签名猜厂商。
    返回 (vendor_slug, 置信度说明)；认不出来返回 ("", "")——由用户手选。
    同一段文本可能同时命中 cisco/ruijie（语法同构），返回列表供 UI 展示候选。
    """
    text = text or ""
    if not text.strip():
        return "", ""
    hits = []
    for vendor, sig in VENDOR_SIGNATURES:
        if sig.search(text):
            hits.append(vendor)
    # 同构厂商去重：思科/锐捷语法相同，保留签名更具体的排序
    if not hits:
        return "", ""
    main = hits[0]
    return main, ("候选：%s" % "、".join(hits))


def compile_pattern(pattern):
    """正则编译（忽略大小写）；坏正则返回 None，绝不抛异常拖垮匹配流程"""
    try:
        return re.compile(pattern or "", re.IGNORECASE)
    except re.error:
        return None


def match_errors(text, dicts, vendor=None):
    """
    在文本里匹配报错字典。
    参数：
        text   —— 用户粘贴的原文（可多行、可带提示符）
        dicts  —— 字典行列表（db.all_errs() 的返回，含 err_id/vendor/pattern/...）
        vendor —— 用户手选的厂商 slug；None 表示不限厂商（自动模式）
    返回：[(dict行, 命中的原文行), ...] 按规则排序；零匹配返回 []
    """
    text = text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    # 候选字典：指定厂商 → 只用该厂商；未指定 → 全部
    pool = []
    for d in dicts:
        if vendor:
            if (d.get("vendor") or "") != vendor and d.get("vendor") not in ("", "linux"):
                continue
            if vendor == "linux" and d.get("vendor") != "linux":
                continue
        pool.append(d)

    hits = {}     # err_id -> (dict行, 命中行)
    for d in pool:
        regex = compile_pattern(d.get("pattern"))
        if regex is None:
            continue
        for ln in lines:
            m = regex.search(ln)
            if m:
                if d.get("err_id") not in hits:
                    hits[d["err_id"]] = (d, ln)
                break        # 该条目命中一次即可，继续下一条字典

    def specificity(d):
        # pattern 越长越具体（粗略但有效的启发式），其次 hit_count
        try:
            return (-len(d.get("pattern") or ""), -int(d.get("hit_count") or 0))
        except Exception:
            return (0, 0)

    result = sorted(hits.values(), key=lambda pair: specificity(pair[0]))
    return result


def selftest_dicts(dicts):
    """
    字典自测：每条条目的 examples 必须能被自己的 pattern 命中。
    返回 [(err_id, 序号, 是否通过, 说明), ...]——质检器与编辑器[测试]按钮共用。
    """
    results = []
    for d in dicts:
        examples = d.get("examples") or []
        regex = compile_pattern(d.get("pattern"))
        if regex is None:
            results.append((d.get("err_id"), 0, False, "正则无法编译：%s" % d.get("pattern")))
            continue
        if not examples:
            results.append((d.get("err_id"), 0, False, "缺少 examples 样例"))
            continue
        for i, ex in enumerate(examples, 1):
            ok = bool(regex.search(str(ex)))
            results.append((d.get("err_id"), i, ok,
                            "命中" if ok else "未命中（examples 与 pattern 不一致）"))
    return results
