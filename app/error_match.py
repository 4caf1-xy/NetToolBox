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


# ---------------------------------------------------------------------------
# pattern 复杂度 lint（安全加固批 4）——灾难性回溯正则的入库门禁
# ---------------------------------------------------------------------------
PATTERN_MAX_LEN = 500        # 超长 pattern → [W]（validate 告警，不阻塞）

_UNBOUNDED_QTY_RE = re.compile(r"\{\d+,\}")   # {n,} 无界重复


def _unbounded_qty_count(body):
    """body 内无界量词（* + {n,}）出现次数；跳过字符类 [...] 与 \\ 转义。"""
    n = 0
    i = 0
    in_class = False
    while i < len(body):
        c = body[i]
        if in_class:
            if c == "\\":
                i += 1
            elif c == "]":
                in_class = False
        elif c == "\\":
            i += 1
        elif c == "[":
            in_class = True
        elif c in "*+":
            n += 1
        elif c == "{":
            m = _UNBOUNDED_QTY_RE.match(body, i)
            if m:
                n += 1
                i = m.end() - 1
        i += 1
    return n


def _branch_first_atoms(branch):
    """提取一个分支的「首消费元素」集合（用于分支重叠判定）。
    返回 set（如 {'a'} / {'0','1'..}）；无法可靠判定（嵌套组/空分支/复杂形态）
    返回 None → 调用方按"可能重叠"从严处理。"""
    branch = branch.strip()
    if not branch:
        return None                      # 空分支可匹配空串 → 视为可能重叠
    i = 0
    if branch[0] == "^":                 # 锚点不消费字符，跳过
        i = 1
        if i >= len(branch):
            return None
    c = branch[i]
    if c == "\\":
        if i + 1 < len(branch):
            return {branch[i + 1]}       # \. \w 等取转义后字符作近似
        return None
    if c == "[":
        close = branch.find("]", i + 1)
        if close < 0:
            return None
        inner = branch[i + 1:close]
        if inner.startswith("^"):
            return None                  # 否定字符类无法枚举，从严
        chars = set()
        j = 0
        while j < len(inner):
            if inner[j] == "\\" and j + 1 < len(inner):
                chars.add(inner[j + 1])
                j += 2
            else:
                chars.add(inner[j])
                j += 1
        return chars or None
    if c == "(":
        return None                      # 嵌套组首元素需递归，从严
    if c in "*+?{|":
        return None                      # 量词/分支开头形态怪异，从严
    return {c}


def _alternation_may_overlap(body):
    """组内分支是否存在潜在首元素重叠（(a|aa)+ 类指数回溯的静态近似）。
    任一首元素集合为 None（不可判定）或两两相交 → True。
    (a|b)+ 等互斥分支正常形态返回 False，不产生 [W] 误报。"""
    branches = []                        # 顶层 | 切分（跳过字符类/转义/嵌套括号）
    depth = 0
    in_class = False
    i = 0
    last = 0
    while i < len(body):
        c = body[i]
        if in_class:
            if c == "\\":
                i += 1
            elif c == "]":
                in_class = False
        elif c == "\\":
            i += 1
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            branches.append(body[last:i])
            last = i + 1
        i += 1
    branches.append(body[last:])
    if len(branches) < 2:
        return False
    atoms = []
    for br in branches:
        s = _branch_first_atoms(br)
        if s is None:
            return True                  # 不可判定 → 从严报 [W]
        atoms.append(s)
    for a in range(len(atoms)):
        for b in range(a + 1, len(atoms)):
            if atoms[a] & atoms[b]:
                return True
    return False


def _find_group_close(pattern, open_idx):
    """返回与 open_idx 处 "(" 配对的 ")" 下标；跳过字符类与转义；无配对返回 -1。"""
    depth = 0
    in_class = False
    i = open_idx
    while i < len(pattern):
        c = pattern[i]
        if in_class:
            if c == "\\":
                i += 1
            elif c == "]":
                in_class = False
        elif c == "\\":
            i += 1
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def pattern_lint(pattern):
    """
    报错库 pattern 复杂度 lint（安全加固批 4，零新依赖的静态扫描）。
    返回 [(level, message)]，level ∈ {"E", "W"}：
        E  嵌套量词 —— 组内已含无界量词（* + {n,}），组外再套无界量词 →
           灾难性回溯（如 (a+)+ / (?:\\d+)* / (a|bb+){2,}），恶意或手误都会让
           match_errors 逐行 re.search 冻结界面。validate 以 [E] 硬拦截。
        W  pattern 超长（> PATTERN_MAX_LEN 字符）—— validate 以 [W] 告警。
    判定为静态语法扫描：跳过字符类与转义后逐组检查，不执行正则本身。

    [W] 规则（审码批 F1，2026-10-13）：组内含 | 分支且组外再套无界量词
    （如 (a|aa)+ / (a|a)*）→ alternation 重叠回溯，实测约 1.65^n 指数膨胀
    （(a|aa)+b 对 26 个 a 单行 0.039s），但 (foo|bar)+ 类正常形态大量存在，
    升 [E] 误报不可接受 → 人工确认级 [W]。判定用分支首元素相交近似
    （_alternation_may_overlap）：互斥分支（(a|b)+）不报，首元素不可判定
    （嵌套组/否定字符类等）从严报。
    """
    pattern = pattern or ""
    problems = []
    if len(pattern) > PATTERN_MAX_LEN:
        problems.append(("W", "长度 %d 字符超过 %d 上限（建议按错误类别拆条）"
                         % (len(pattern), PATTERN_MAX_LEN)))
    i = 0
    in_class = False
    while i < len(pattern):
        c = pattern[i]
        if in_class:
            if c == "\\":
                i += 1
            elif c == "]":
                in_class = False
        elif c == "\\":
            i += 1
        elif c == "[":
            in_class = True
        elif c == "(":
            close = _find_group_close(pattern, i)
            if close > i:
                body = pattern[i + 1:close]
                qty = pattern[close + 1:close + 2]
                outer_unbounded = (
                    qty in ("*", "+")
                    or (qty == "{"
                        and bool(_UNBOUNDED_QTY_RE.match(pattern, close + 1))))
                if _unbounded_qty_count(body) and outer_unbounded:
                    if qty == "{":
                        m = _UNBOUNDED_QTY_RE.match(pattern, close + 1)
                        problems.append((
                            "E", "嵌套量词：组内含无界量词且组外为 %s（灾难性回溯）"
                                 % m.group(0)))
                    else:
                        problems.append((
                            "E", "嵌套量词：组内含无界量词且组外为 %s%s（灾难性回溯，"
                                 "如 (a+)+ 形态）" % (pattern[i:close + 1][:30], qty)))
                elif outer_unbounded and _alternation_may_overlap(body):
                    # alternation 重叠回溯（(a|aa)+ 类）→ [W] 人工确认，不硬拦
                    if qty == "{":
                        m = _UNBOUNDED_QTY_RE.match(pattern, close + 1)
                        problems.append((
                            "W", "alternation 回溯：组内含 | 分支且组外为 %s"
                                 "（分支重叠时指数回溯，如 (a|aa)+ 形态），请确认分支互斥"
                                 % m.group(0)))
                    else:
                        problems.append((
                            "W", "alternation 回溯：组内含 | 分支且组外为 %s%s"
                                 "（分支重叠时指数回溯，如 (a|aa)+ 形态），请确认分支互斥"
                                 % (pattern[i:close + 1][:30], qty)))
            # 不跳到 close：嵌套的内层组也要逐一检查（((a+)+) 只报最内层足够定位）
        i += 1
    return problems


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
