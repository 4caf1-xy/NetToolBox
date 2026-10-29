# -*- coding: utf-8 -*-
"""
diff_tool.py —— Before/After 变更对比引擎（H 批 2026-10-14）

定位：离线变更验证的最后一环。变更前后各粘贴一次同命令回显，自动输出状态
差异摘要（✓ 不变 / ⚠ 变化 before→after / ＋新增 / －消失），自动忽略噪声行
（uptime、包计数器、时间戳、CPU 瞬时值）。离线粘贴流零联网，Copy Only 不变。

提取规则落位（H 批盘点裁决）：**种子侧 JSON**（seed_data/diff_rules.json，
随包分发、U 盘现场可改、validate 门禁校验）——不进 DB 表、不新增条目。
本模块只做规则的「读取 + 校验 + 执行」，不做规则编辑。

硬约束：
    ★ 纯本地正则，不 import 任何网络库；粘贴内容只在内存中处理
    ★ 任何坏规则（正则坏/结构坏）都不能拖垮对比流程：坏规则跳过，
      全部规则不可用时降级行级 diff，绝不抛异常中断
    ★ 无规则命中时降级行级 diff（去噪后逐行比对），不报错不中断

四态与分级（与 seed-guide / dev-notes H 批节一致）：
    逐项  state ∈ {"same", "changed", "added", "removed"}
    汇总  level ∈ {"clean", "changed", "delta"}
          clean   全部 ✓ 不变
          changed 仅状态值变化（重点核对：如接口 up/down 翻转）
          delta   键集有新增/消失（变更目标达成与否的主判据）
"""

import json
import os
import re
import difflib

RULES_FILENAME = "diff_rules.json"

SEMANTICS = ("kv", "set")           # kv=键值语义（key→value）；set=集合语义（仅 item）
STATES = ("same", "changed", "added", "removed")
LEVELS = ("clean", "changed", "delta")

# rule_id 合法形态（小写字母数字下划线）
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# 一、规则加载与校验（validate 门禁共用）
# ---------------------------------------------------------------------------

def load_rules(seed_dirs=None):
    """
    按 seed_dirs 优先级读取 diff_rules.json（同名文件取优先级最高的一份，
    与条目种子同机制——现场 U 盘改规则重启即生效）。
    返回 (data, file_problems)：data 为 dict（读不到/读坏了为 {}），
    file_problems 非空 = 文件级错误（validate 按 [E] 拦截；运行时静默降级）。
    """
    if seed_dirs is None:
        import db as dbmod
        seed_dirs = dbmod.seed_dirs()
    for d in seed_dirs or []:
        path = os.path.join(d, RULES_FILENAME)
        if not os.path.isfile(path):        # isfile 防御：open 坏路径的教训
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (ValueError, OSError) as exc:
            return {}, ["diff_rules.json：读取失败（%s）" % exc]
        if not isinstance(data, dict):
            return {}, ["diff_rules.json：顶层不是 JSON 对象"]
        return data, []
    return {}, []       # 没有规则文件：运行时合法（降级行级 diff），validate 单独判缺失


def _lint_regex(pattern):
    """正则编译 + 复杂度 lint。返回 (compiled, [E消息], [W消息])。
    复用报错字典 pattern_lint（嵌套量词 [E] / 超长 [W]）同一门禁。"""
    import error_match as _em
    problems_e, problems_w = [], []
    compiled = None
    try:
        compiled = re.compile(pattern or "", re.IGNORECASE)
    except re.error as exc:
        problems_e.append("正则无法编译：%r（%s）" % (pattern, exc))
        return None, problems_e, problems_w
    for level, msg in _em.pattern_lint(pattern or ""):
        if level == "E":
            problems_e.append("正则复杂度 %s：%s" % (msg, (pattern or "")[:40]))
        else:
            problems_w.append("正则复杂度 %s：%s" % (msg, (pattern or "")[:40]))
    return compiled, problems_e, problems_w


def _check_noise_list(noise, where, errors, warnings):
    """noise 数组：每项 {pattern, desc?}（兼容纯字符串）。坏 pattern [E]。"""
    if noise is None:
        return
    if not isinstance(noise, list):
        errors.append("%s：noise 必须是数组" % where)
        return
    for i, item in enumerate(noise):
        if isinstance(item, str):
            item = {"pattern": item}
        if not isinstance(item, dict) or not str(item.get("pattern") or "").strip():
            errors.append("%s：noise[%d] 缺 pattern" % (where, i))
            continue
        _c, es, ws = _lint_regex(item.get("pattern"))
        for e in es:
            errors.append("%s：noise[%d] %s" % (where, i, e))
        for w in ws:
            warnings.append("%s：noise[%d] %s" % (where, i, w))


def validate_rules(data):
    """
    规则合法性校验（validate 门禁 [E] 硬拦截口径）：
        结构坏 / 正则坏 / 命名组不符语义 / rule_id 重复 / (rule_id, vendor) 重复 → [E]
        vendor 未收录（VENDOR_SLUGS 之外且非 linux）→ [W]（提示不阻塞，
        运行时按 match 文本特征选规则，vendor 仅作展示与后续扩展）
    返回 (errors, warnings)。
    """
    import db as dbmod
    errors, warnings = [], []
    rules = data.get("diff_rules")
    if not isinstance(rules, list) or not rules:
        errors.append("diff_rules.json：diff_rules 为空或不是数组")
        return errors, warnings
    _check_noise_list(data.get("noise"), "全局 noise", errors, warnings)

    seen_rule = set()
    seen_pair = set()
    for i, rule in enumerate(rules):
        where = "diff_rules[%d]" % i
        if not isinstance(rule, dict):
            errors.append("%s：不是 JSON 对象" % where)
            continue
        rid = str(rule.get("rule_id") or "").strip()
        if not rid:
            errors.append("%s：缺 rule_id" % where)
        elif not _RULE_ID_RE.match(rid):
            errors.append("%s：rule_id %r 不合规范（小写字母开头的 [a-z0-9_]）" % (where, rid))
        elif rid in seen_rule:
            errors.append("%s：rule_id %r 重复" % (where, rid))
        seen_rule.add(rid)
        if not str(rule.get("label") or "").strip():
            errors.append("%s：缺 label（展示名）" % where)
        semantics = rule.get("semantics")
        if semantics not in SEMANTICS:
            errors.append("%s：semantics %r 不合法（只允许 %s）" % (where, semantics, "/".join(SEMANTICS)))
        match_pat = str(rule.get("match") or "").strip()
        if not match_pat:
            errors.append("%s：缺 match（命令回显识别正则）" % where)
        else:
            _c, es, ws = _lint_regex(match_pat)
            for e in es:
                errors.append("%s：match %s" % (where, e))
            for w in ws:
                warnings.append("%s：match %s" % (where, w))
        _check_noise_list(rule.get("noise"), where + ".noise", errors, warnings)

        variants = rule.get("variants")
        if not isinstance(variants, list) or not variants:
            errors.append("%s：variants 为空或不是数组（每类至少一个厂商变体）" % where)
            continue
        for j, variant in enumerate(variants):
            vwhere = "%s.variants[%d]" % (where, j)
            if not isinstance(variant, dict):
                errors.append("%s：不是 JSON 对象" % vwhere)
                continue
            vendor = str(variant.get("vendor") or "").strip()
            if not vendor:
                errors.append("%s：缺 vendor" % vwhere)
            elif vendor != "linux" and vendor not in dbmod.VENDOR_SLUGS:
                warnings.append("%s：vendor %r 不在厂商注册表（展示用，不拦截）" % (vwhere, vendor))
            line_pat = str(variant.get("line") or "").strip()
            if not line_pat:
                errors.append("%s：缺 line（回显行提取正则）" % vwhere)
                continue
            compiled, es, ws = _lint_regex(line_pat)
            for e in es:
                errors.append("%s：%s" % (vwhere, e))
            for w in ws:
                warnings.append("%s：%s" % (vwhere, w))
            if compiled is not None and semantics in SEMANTICS:
                groups = set(compiled.groupindex.keys())
                need = {"key", "value"} if semantics == "kv" else {"item"}
                missing = need - groups
                if missing:
                    errors.append("%s：%s 语义缺少命名组 %s（规则要求 %s）"
                                  % (vwhere, semantics, "、".join(sorted(missing)),
                                     "/".join(sorted(need))))
            pair = (rid, vendor)
            if pair in seen_pair:
                errors.append("%s：(rule_id, vendor) %r 重复" % (vwhere, pair))
            seen_pair.add(pair)
    return errors, warnings


# ---------------------------------------------------------------------------
# 二、噪声过滤
# ---------------------------------------------------------------------------

def compile_noise(rules_data):
    """全局 + 全部规则内 noise 编译合并（坏正则跳过，绝不抛异常）"""
    out = []
    for item in (rules_data.get("noise") or []):
        if isinstance(item, str):
            item = {"pattern": item}
        if isinstance(item, dict) and item.get("pattern"):
            try:
                out.append((re.compile(item["pattern"], re.IGNORECASE),
                            item.get("desc") or ""))
            except re.error:
                continue
    return out


def is_noise_line(line, noise_compiled):
    """命中任一噪声规则 → True（uptime/计数器/时间戳/CPU 瞬时值等）"""
    for rx, _desc in noise_compiled:
        if rx.search(line):
            return True
    return False


# ---------------------------------------------------------------------------
# 三、规则选择与状态提取
# ---------------------------------------------------------------------------

def pick_rule(text, rules_data):
    """按回显命令特征选规则：rules 顺序即优先级（文件里第一个命中的）。
    返回 (rule dict, 命中行) 或 (None, "")。"""
    text = text or ""
    if not text.strip():
        return None, ""
    for rule in rules_data.get("diff_rules") or []:
        try:
            rx = re.compile(rule.get("match") or "", re.IGNORECASE)
        except re.error:
            continue
        for ln in text.splitlines():
            if rx.search(ln):
                return rule, ln
    return None, ""


def _extract_with_variant(text, compiled):
    """用单个已编译变体提取：返回 items dict（kv: key→状态串；set: item→""）"""
    items = {}
    for ln in text.splitlines():
        if not ln.strip():
            continue
        m = compiled.search(ln)
        if not m:
            continue
        gd = m.groupdict()
        if "key" in gd and gd["key"] is not None:
            key = gd["key"].strip()
            parts = [str(gd.get("value") or "").strip()]
            for gname in compiled.groupindex:
                if gname in ("key", "value"):
                    continue
                v = gd.get(gname)
                if v:
                    parts.append(v.strip())
            items[key] = " / ".join(p for p in parts if p)
        elif "item" in gd and gd["item"] is not None:
            items.setdefault(gd["item"].strip(), "")
    return items


def extract_items(text, rule, noise_compiled):
    """
    用规则的全部厂商变体试提取，取命中条数最多的变体（厂商自动适配）。
    返回 (items, variant_used, noise_dropped)。

    ★ 噪声不参与提取过滤：提取正则本就精确（键/状态形态强约束），
      而噪声正则是整行级的——OSPF 邻居行含 Dead Time（00:00:35）、
      接口详情行含时间戳，按行剔除会把真实状态行一并杀掉。
      噪声只作用于 ① noise_dropped 计数 ② 降级行级比对（diff_lines）。
    """
    text = text or ""
    noise_dropped = 0
    for ln in text.splitlines():
        if ln.strip() and is_noise_line(ln, noise_compiled):
            noise_dropped += 1

    best = ({}, None)
    for variant in rule.get("variants") or []:
        try:
            compiled = re.compile(variant.get("line") or "", re.IGNORECASE)
        except re.error:
            continue
        items = _extract_with_variant(text, compiled)
        if len(items) > len(best[0]):
            best = (items, variant)
    return best[0], best[1], noise_dropped


# ---------------------------------------------------------------------------
# 四、差异计算
# ---------------------------------------------------------------------------

def diff_kv(before_items, after_items):
    """键值语义四态。返回 [{key, before, after, state}, ...] 按稳定排序"""
    out = []
    for key in before_items:
        if key in after_items:
            if before_items[key] == after_items[key]:
                out.append({"key": key, "before": before_items[key],
                            "after": after_items[key], "state": "same"})
            else:
                out.append({"key": key, "before": before_items[key],
                            "after": after_items[key], "state": "changed"})
        else:
            out.append({"key": key, "before": before_items[key],
                        "after": "", "state": "removed"})
    for key in after_items:
        if key not in before_items:
            out.append({"key": key, "before": "", "after": after_items[key],
                        "state": "added"})
    return out


def diff_lines(before_text, after_text, noise_compiled):
    """降级行级 diff：去噪后 SequenceMatcher。
    replace 段配对成 changed，delete→removed，insert→added。
    返回 [{key: 去噪行, before, after, state}, ...]"""
    def _clean(text):
        keep = []
        for ln in (text or "").splitlines():
            if ln.strip() and not is_noise_line(ln, noise_compiled):
                keep.append(ln.strip())
        return keep

    b, a = _clean(before_text), _clean(after_text)
    sm = difflib.SequenceMatcher(None, b, a, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ln in b[i1:i2]:
                out.append({"key": ln, "before": ln, "after": ln, "state": "same"})
        elif tag == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                bb = b[i1 + k] if i1 + k < i2 else ""
                aa = a[j1 + k] if j1 + k < j2 else ""
                out.append({"key": aa or bb, "before": bb, "after": aa,
                            "state": "changed"})
        elif tag == "delete":
            for ln in b[i1:i2]:
                out.append({"key": ln, "before": ln, "after": "", "state": "removed"})
        elif tag == "insert":
            for ln in a[j1:j2]:
                out.append({"key": ln, "before": "", "after": ln, "state": "added"})
    return out


def summarize(items):
    """逐项列表 → (summary dict, level)。delta > changed > clean 优先级"""
    summary = {s: 0 for s in STATES}
    for it in items:
        summary[it["state"]] += 1
    if summary["added"] or summary["removed"]:
        level = "delta"
    elif summary["changed"]:
        level = "changed"
    else:
        level = "clean"
    return summary, level


def run_compare(before_text, after_text, rules_data=None):
    """
    主入口：变更前后两段回显 → 差异结果 dict（UI / HTML 导出共用）。
    永不抛异常：规则缺失/全坏/零命中 → 降级行级 diff（mode="line"）。
    """
    before_text = before_text or ""
    after_text = after_text or ""
    if rules_data is None:
        rules_data, _problems = load_rules()

    noise_compiled = compile_noise(rules_data)
    result = {
        "mode": "line",
        "rule_id": "", "rule_label": "", "semantics": "",
        "variant_used": "",
        "items": [], "summary": {s: 0 for s in STATES},
        "level": "clean",
        "noise_dropped": 0,
        "fallback_reason": "",
    }
    if not before_text.strip() and not after_text.strip():
        result["fallback_reason"] = "粘贴内容为空"
        return result

    rule = None
    if rules_data.get("diff_rules"):
        rule, _hit = pick_rule(before_text, rules_data) \
            or pick_rule(after_text, rules_data)
    if rule is None:
        result["fallback_reason"] = "无提取规则命中（未识别到已收录命令类）"
    else:
        try:
            before_items, variant_b, noise_b = extract_items(before_text, rule, noise_compiled)
            after_items, variant_a, noise_a = extract_items(after_text, rule, noise_compiled)
            result["rule_id"] = rule.get("rule_id") or ""
            result["rule_label"] = rule.get("label") or ""
            result["semantics"] = rule.get("semantics") or ""
            result["noise_dropped"] = noise_b + noise_a
            result["variant_used"] = ((variant_b or {}).get("vendor") or "")
            if not before_items and not after_items:
                result["fallback_reason"] = ("规则「%s」命中但回显中零提取"
                                             % result["rule_label"])
            else:
                result["mode"] = "rule"
                result["items"] = diff_kv(before_items, after_items)
                result["summary"], result["level"] = summarize(result["items"])
                return result
        except Exception as exc:            # 提取层任何意外都不中断（离线现场不崩）
            result["fallback_reason"] = "规则提取异常，已降级行级比对（%s）" % exc

    # ---- 降级：行级 diff ----
    result["items"] = diff_lines(before_text, after_text, noise_compiled)
    result["summary"], result["level"] = summarize(result["items"])
    result["noise_dropped"] = sum(
        1 for t in (before_text, after_text)
        for ln in t.splitlines()
        if ln.strip() and is_noise_line(ln, noise_compiled))
    return result
