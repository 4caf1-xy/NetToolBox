# -*- coding: utf-8 -*-
"""
translate_tool.py —— 整机配置翻译器引擎（U 批 2026-11-06）

定位：把一台设备的 running-config（整份或片段）按「配置块」解析，块内命中的
命令族按库内条目模板重组成目标厂商命令串，未命中的行逐条交代，绝不臆造。

硬约束（U 批验收红线）：
    ★ 输出零臆测：目标侧每一行命令都来自某条库内条目的 commands 模板渲染，
      行级账本记下「源行号 → 目标条目 uuid」；模板里有占位填不满 → 该条命令
      不输出，相关源行整体降级为「需人工」
    ★ 每行必落一档（a 已译 / b 逐行映射 / c 需人工 / d 跳过），不允许凭空消失
    ★ 纯本地正则 + 字典，不 import 任何网络库；粘贴内容只在本进程内存处理
    ★ 映射表随种子走（seed_data/translate_rules.json），坏规则由 --validate-seed
      [E] 硬拦截；运行期读不到规则 = 全行落 c 档（提示），不崩、不猜

分层口径：
    a 已译      命中族规则，输出由条目渲染，附 uuid + 行号追溯
    b 逐行映射  注释/说明行（非命令），按目标厂商注释符重写
    c 需人工    私有特性 / 库内无对应族 / 族内未覆盖行，逐条列原因
    d 跳过      空行、分隔符、会话包装行、口令密钥行（列明理由）

安全默认：
    · 输出整体标「语法核对档，未经真机」，与 T 批口径一致
    · 变更类条目自带 rollback 时，一并渲染到「回退命令」区（仍标未真机核对）
    · 口令/密钥行一律不外译（d 档），由人工在目标设备重设
"""

import json
import os
import re

import renderer

RULES_FILENAME = "translate_rules.json"

TIERS = ("a", "b", "c", "d")
TIER_LABELS = {
    "a": "已译",
    "b": "逐行映射",
    "c": "需人工",
    "d": "跳过",
}

# 会话包装行（视图进出/落盘），目标侧由条目模板自带的同类行统一给出
SESSION_LINES = {
    "enable", "configure terminal", "configure", "conf t", "system-view", "sys",
    "end", "return", "exit", "quit", "write", "write memory", "save", "save force",
    "commit", "commit and-quit", "commit check", "y",
}

_WRAP_EXACT = ("enable", "configure terminal", "configure", "system-view", "sys",
               "end", "return", "write", "write memory", "save", "save force",
               "commit", "commit and-quit", "y")

COMMENT_RE = re.compile(r"^\s*([!#])")

# 接口名前缀剥离：GigabitEthernet0/1 → 0/1；ge-0/0/1 → 0/0/1；Eth-Trunk1 → 1
_IF_RE = re.compile(r"^([A-Za-z][A-Za-z\-_]*?)[\s_]?(\d.*)$")

# 掩码换算
_MASK_BITS = None


# ---------------------------------------------------------------------------
# 一、规则加载 / 校验（validate 门禁共用）
# ---------------------------------------------------------------------------

def _seed_dirs(seed_dirs=None):
    if seed_dirs is not None:
        return seed_dirs
    import db as dbmod
    return dbmod.seed_dirs()


def load_rules(seed_dirs=None):
    """
    按 seed_dirs 优先级读 translate_rules.json（同名文件取最高优先级一份）。
    返回 (data, problems)：problems 非空 = 文件级错误（validate [E] 拦截）。
    """
    for d in _seed_dirs(seed_dirs) or []:
        path = os.path.join(d, RULES_FILENAME)
        if not os.path.isfile(path):        # isfile 防御：open 坏路径的教训
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (ValueError, OSError) as exc:
            return {}, ["%s：读取失败（%s）" % (RULES_FILENAME, exc)]
        if not isinstance(data, dict):
            return {}, ["%s：顶层不是 JSON 对象" % RULES_FILENAME]
        return data, []
    return {}, []


def _compile(pattern, where, errors):
    try:
        return re.compile(pattern or "", re.IGNORECASE)
    except re.error as exc:
        errors.append("%s：正则无法编译（%s）" % (where, exc))
        return None


def _placeholders(text):
    return [m.group(1) for m in renderer.PARAM_RE.finditer(text or "")]


def validate_rules(data, entry_getter=None, vendor_slugs=None):
    """
    校验映射表。返回 (errors, warnings)。
    entry_getter：uuid → 条目 dict（main.validate_seed 传 database.get_entry）。
    无 entry_getter 时跳过条目存在性/占位可填性检查（离线单测用）。
    """
    errors, warnings = [], []
    if not isinstance(data, dict) or not data:
        return ["%s：缺失或不是对象" % RULES_FILENAME], []

    vendors_meta = data.get("vendors") or {}
    if not isinstance(vendors_meta, dict) or not vendors_meta:
        errors.append("%s：vendors 为空或不是对象" % RULES_FILENAME)
        return errors, warnings

    slugs = set(vendor_slugs or [])
    if slugs:
        for v in vendors_meta:
            if v not in slugs:
                errors.append("vendors：未知厂商 slug「%s」（不在 VENDOR_SLUGS 内）" % v)

    for v, meta in vendors_meta.items():
        if not isinstance(meta, dict):
            errors.append("vendors[%s]：不是对象" % v)
            continue
        if meta.get("style") not in ("indent", "flat"):
            errors.append("vendors[%s]：style 必须是 indent / flat" % v)
        if not meta.get("if_prefix"):
            warnings.append("vendors[%s]：缺 if_prefix，if_full 变换退化为原样" % v)
        if meta.get("mode_decl"):
            _compile(meta["mode_decl"], "vendors[%s].mode_decl" % v, errors)

    for i, pat in enumerate(data.get("secret_patterns") or []):
        _compile(pat, "secret_patterns[%d]" % i, errors)

    for v, hints in (data.get("private_hints") or {}).items():
        if v not in vendors_meta:
            warnings.append("private_hints：厂商「%s」未在 vendors 声明" % v)
        for j, item in enumerate(hints or []):
            _compile((item or {}).get("re"), "private_hints[%s][%d]" % (v, j), errors)
            if not (item or {}).get("note"):
                warnings.append("private_hints[%s][%d]：缺 note" % (v, j))

    fams = data.get("families") or []
    if not fams:
        errors.append("%s：families 为空" % RULES_FILENAME)
        return errors, warnings

    seen = set()
    for fam in fams:
        if not isinstance(fam, dict):
            errors.append("families：存在非对象项")
            continue
        fid = str(fam.get("id") or "")
        if not re.match(r"^[a-z][a-z0-9_]*$", fid):
            errors.append("families：族 id 非法「%s」（要求小写字母数字下划线）" % fid)
            continue
        if fid in seen:
            errors.append("families：族 id 重复「%s」" % fid)
        seen.add(fid)
        if not fam.get("name"):
            warnings.append("families[%s]：缺 name（UI 显示用）" % fid)
        bind = fam.get("bind") or {}
        if not isinstance(bind, dict):
            errors.append("families[%s]：bind 不是对象" % fid)
            bind = {}
        fv = fam.get("vendors") or {}
        if not isinstance(fv, dict) or not fv:
            errors.append("families[%s]：vendors 为空" % fid)
            continue
        key_stats = {}
        # 族级目标厂商范围（V 批 2026-11-06）：库内确有厂商缺格的族（如堆叠缺中兴、
        # HA 仅思科/华为）如实声明可译目标，不再用「六厂商齐备」硬校验逼出凑数映射。
        # 未声明 scope = 与顶层 vendors 同集（旧表行为不变）。
        scope = fam.get("scope")
        if scope is not None:
            if not isinstance(scope, list) or not scope:
                errors.append("families[%s]：scope 必须是非空数组" % fid)
                scope = None
            else:
                unknown = [s for s in scope if s not in vendors_meta]
                if unknown:
                    errors.append("families[%s]：scope 含未声明厂商「%s」"
                                  % (fid, "、".join(unknown)))
        targets = [s for s in (scope if scope else list(vendors_meta))
                   if s in vendors_meta]
        # scope 外的源厂商是合法设计（如堆叠族以思科为源、思科本身不作目标），不告警
        for src, meta in fv.items():
            where = "families[%s].vendors[%s]" % (fid, src)
            if src not in vendors_meta:
                errors.append("%s：厂商未在顶层 vendors 声明" % where)
                continue
            if not isinstance(meta, dict):
                errors.append("%s：不是对象" % where)
                continue
            entry = None
            uuid = str(meta.get("entry") or "")
            if not uuid:
                errors.append("%s：缺 entry（库内条目 uuid）" % where)
            elif entry_getter is not None:
                entry = entry_getter(uuid)
                if not entry:
                    errors.append("%s：entry 指向的条目不存在（%s）" % (where, uuid[:8]))
                elif str(entry.get("vendor") or "") != src:
                    errors.append("%s：entry 属于厂商「%s」，与族声明不符"
                                  % (where, entry.get("vendor")))
            key = meta.get("key") or fam.get("key") or []
            if key and not isinstance(key, list):
                errors.append("%s：key 必须是数组" % where)
            key_stats[src] = bool(key)
            lines = meta.get("lines") or []
            if not lines:
                errors.append("%s：lines 为空（该厂商无任何可识别行）" % where)
            for j, rule in enumerate(lines):
                lw = "%s.lines[%d]" % (where, j)
                if not isinstance(rule, dict):
                    errors.append("%s：不是对象" % lw)
                    continue
                if not rule.get("re"):
                    errors.append("%s：缺 re" % lw)
                    continue
                compiled = _compile(rule["re"], lw, errors)
                if compiled is not None:
                    for name in compiled.groupindex:
                        if name not in (rule.get("inherit") or {}) \
                                and name not in (rule.get("collect") or {}) \
                                and name not in _canon_names(fam, src):
                            warnings.append(
                                "%s：捕获组「%s」不在族参数表内（可能拼错，值会被忽略）"
                                % (lw, name))
                for tgt in list((rule.get("inherit") or {}).keys()) + \
                        list((rule.get("collect") or {}).keys()):
                    if tgt not in _canon_names(fam, src):
                        warnings.append("%s：inherit/collect 目标「%s」不在族参数表内" % (lw, tgt))

        # 无键族 = 单实例族（V 批：查询对照 / 显示控制），整族一致即合法；
        # 只有「部分厂商有键、部分没有」才是可疑的写法
        if key_stats and any(key_stats.values()) and not all(key_stats.values()):
            warnings.append("families[%s]：实例分组键不一致（%s 有键、%s 无键）"
                            % (fid, "、".join(k for k, v in key_stats.items() if v),
                               "、".join(k for k, v in key_stats.items() if not v)))

        # 目标模板占位可填性：目标厂商条目的每个占位必须有来源
        if entry_getter is not None:
            for dst in targets:
                if dst not in fv:
                    errors.append("families[%s]：缺目标厂商 %s 的条目（无法译到该厂商）"
                                  % (fid, dst))
                    continue
                errors.extend(_check_template_fillable(fam, dst, entry_getter))

    if not isinstance(data.get("tiers"), dict):
        warnings.append("tiers：缺四档说明（UI 会退回内置文案）")
    return errors, warnings


def _canon_names(fam, src):
    """族参数表里的规范参数名（bind 键 + 该厂商行规则捕获组/继承键）"""
    names = set((fam.get("bind") or {}).keys())
    m = (fam.get("vendors") or {}).get(src) or {}
    for rule in m.get("lines") or []:
        names.update((rule.get("inherit") or {}).keys())
        names.update((rule.get("collect") or {}).keys())
        for part in re.findall(r"\(\?P<([^>]+)>", rule.get("re") or ""):
            names.add(part)
    names.update(fam.get("optional") or [])
    return names


def _resolve_bind(bind, dst):
    """
    bind：`规范名[@标签]` → {厂商: {param,transform,narrow}, '*': ...}
    返回 {目标参数名: (规范名, transform, narrow)}；`@标签` 允许一个规范参数
    派生出多个目标参数（如 ifname → port_start 与 port_end）。
    """
    out = {}
    for key, per_vendor in (bind or {}).items():
        if not isinstance(per_vendor, dict):
            continue
        spec = per_vendor.get(dst, per_vendor.get("*"))
        if not spec:
            continue
        param = spec.get("param")
        if param:
            # 同一目标参数可由多个规范项提供（如 ospf silent / uplink 都落到 silent_port）：
            # 保留候选列表，渲染时取第一个有值者，不做隐式覆盖
            out.setdefault(param, []).append((str(key).split("@")[0], spec.get("transform"),
                                              bool(spec.get("narrow"))))
    return out


def fam_scope(fam, vendors_meta=None):
    """族的目标厂商范围（未声明 = 顶层 vendors 全集；旧表行为不变）"""
    scope = fam.get("scope")
    if scope:
        return list(scope)
    return list((vendors_meta or {}).keys())


def fam_target_covered(fam, dst, vendors_meta=None):
    return dst in fam_scope(fam, vendors_meta)


def fam_has_key(fam):
    """族是否声明了实例分组键；无键族 = 单实例族（如查询对照族、显示控制）"""
    fv = fam.get("vendors") or {}
    for meta in fv.values():
        if (meta or {}).get("key"):
            return True
    return bool(fam.get("key"))


def _check_template_fillable(fam, dst, entry_getter):
    """目标厂商条目模板里的每个占位必须可填，否则报 [E]（防止输出残缺/凭空字面量）"""
    errs = []
    fid = fam.get("id")
    bind = fam.get("bind") or {}
    optional = set(fam.get("optional") or [])
    meta = (fam.get("vendors") or {}).get(dst) or {}
    uuid = meta.get("entry")
    if not uuid:
        return errs
    entry = entry_getter(uuid)
    if not entry:
        return errs
    specs = {}
    for sp in renderer._load_params(entry):
        if isinstance(sp, dict) and sp.get("name"):
            specs[sp["name"]] = sp
    resolved = _resolve_bind(bind, dst)
    default_ok = set(fam.get("default_ok") or [])
    seen = set()
    for name in _placeholders(entry.get("commands") or ""):
        if name in seen:
            continue
        seen.add(name)
        spec = specs.get(name, {})
        if spec.get("type") == "flag":
            continue
        if name in optional or name in resolved:
            continue
        if spec.get("default") and name in default_ok:
            continue
        errs.append(
            "families[%s]：目标 %s 条目（%s）模板占位 {{%s}} 无填充来源"
            "（bind/库内默认/optional 三缺一）" % (fid, dst, (uuid or "")[:8], name))
    return errs


# ---------------------------------------------------------------------------
# 二、接口名与参数变换
# ---------------------------------------------------------------------------

def _if_short(name):
    s = (name or "").strip()
    if not s:
        return ""
    m = _IF_RE.match(s)
    return m.group(2) if m else s


def _if_prefix(name):
    s = (name or "").strip()
    m = _IF_RE.match(s)
    return m.group(1) if m else ""


def parse_range(raw):
    """
    把源侧接口表达式拆成首/末端口的短名。
    支持：GigabitEthernet 0/1 to GigabitEthernet 0/10 / 0/1-0/10 / Gi0/1,Gi0/3 / 单口
    """
    s = re.sub(r"^interface\s+(?:range\s+)?", "", (raw or "").strip(), flags=re.I)
    if not s:
        return "", ""
    m = re.match(r"^(\S+)\s+(\S+)\s+to\s+(\S+)\s+(\S+)$", s, re.I)
    if m:
        return _if_short(m.group(2)), _if_short(m.group(4))
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return _if_short(parts[0]), _if_short(parts[-1])
    if " to " in s:
        a, b = s.split(" to ", 1)
        return _if_short(a), _if_short(b)
    m = re.match(r"^(.+?)-(\d[\d/.]*)$", s)
    if m and m.group(1) and m.group(1)[-1].isdigit():
        return _if_short(m.group(1)), _if_short(m.group(2))
    return _if_short(s), _if_short(s)


def _fmt_range(first, last):
    if not first:
        return last or ""
    if not last or last == first:
        return first
    return "%s-%s" % (first, last)


def _mask_to_len(value):
    v = str(value or "").strip()
    if v.isdigit():
        return v
    try:
        parts = [int(p) for p in v.split(".")]
    except ValueError:
        return v
    if len(parts) != 4:
        return v
    bits = 0
    for p in parts:
        bits += bin(p).count("1")
    return str(bits)


def _len_to_mask(value):
    v = str(value or "").strip()
    if "." in v:
        return v
    try:
        n = int(v)
    except ValueError:
        return v
    if not 0 <= n <= 32:
        return v
    bits = (0xFFFFFFFF << (32 - n)) & 0xFFFFFFFF if n else 0
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


def _speed(value, dst):
    v = str(value or "").strip().lower()
    table = {"10": ("10", "10m"), "100": ("100", "100m"), "1000": ("1000", "1g"),
             "10000": ("10000", "10g"), "1g": ("1000", "1g"), "10m": ("10", "10m"),
             "100m": ("100", "100m"), "10g": ("10000", "10g"), "auto": ("auto", "auto")}
    if v not in table:
        return v
    return table[v][1] if dst == "juniper" else table[v][0]


def _duplex(value, dst):
    v = str(value or "").strip().lower()
    if v in ("full", "full-duplex"):
        return "full-duplex" if dst == "juniper" else "full"
    if v in ("half", "half-duplex"):
        return "half-duplex" if dst == "juniper" else "half"
    return v


def apply_transform(value, transform, dst, vmeta):
    """按 transform 链逐级变换（transform 可为字符串或列表）"""
    if transform is None:
        return value
    steps = transform if isinstance(transform, list) else [transform]
    v = value
    for step in steps:
        if isinstance(v, (list, tuple)) and step not in ("members0", "members1"):
            v = ",".join([str(x) for x in v if x])
        if step == "id":
            continue
        if step == "if_short":
            v = _if_short(v if isinstance(v, str) else str(v))
        elif step == "if_full":
            s = (v if isinstance(v, str) else str(v)).strip()
            prefix = (vmeta or {}).get("if_prefix") or ""
            short = _if_short(s)
            if prefix and s.lower().startswith(prefix.lower()):
                v = s
            else:
                v = (prefix + short) if prefix else s
        elif step == "if_last":
            short = _if_short(v if isinstance(v, str) else str(v))
            v = short.split("/")[-1] if short else ""
        elif step == "strip_unit":
            v = re.sub(r"\.\d+$", "", str(v))
        elif step == "first_port":
            v = parse_range(str(v))[0]
        elif step == "last_port":
            v = parse_range(str(v))[1]
        elif step == "range_start":
            v = parse_range(str(v))[0]
        elif step == "range_end":
            v = parse_range(str(v))[1]
        elif step == "range_full":
            a, b = parse_range(str(v))
            prefix = (vmeta or {}).get("if_prefix") or ""
            v = (prefix + a) if a and (b in ("", a)) else ("%s%s-%s%s" % (prefix, a, prefix, b))
        elif step == "range_expr":
            a, b = parse_range(str(v))
            v = _fmt_range(a, b)
        elif step == "members0":
            v = v[0] if isinstance(v, list) and v else ""
        elif step == "members1":
            v = v[1] if isinstance(v, list) and len(v) > 1 else ""
        elif step == "to_mask":
            v = _len_to_mask(v)
        elif step == "to_masklen":
            v = _mask_to_len(v)
        elif step == "speed":
            v = _speed(v, dst)
        elif step == "duplex":
            v = _duplex(v, dst)
        else:
            raise ValueError("未知变换：%s" % step)
    return v


# ---------------------------------------------------------------------------
# 三、解析：单元（配置块）切分与上下文
# ---------------------------------------------------------------------------

def parse_units(text, style="indent"):
    """把配置文本切成单元；indent 风格按「顶格行 + 其缩进子行」成块。"""
    units = []
    cur = None
    for no, raw in enumerate((text or "").splitlines(), 1):
        line = raw.rstrip()
        s = line.strip()
        if not s:
            units.append({"no": no, "lines": [(no, line)], "header": None, "kind": "blank"})
            cur = None
            continue
        if s[0] in "!#":
            units.append({"no": no, "lines": [(no, line)], "header": None, "kind": "comment"})
            cur = None
            continue
        if style == "flat" or s.startswith("set ") or s.startswith("delete "):
            units.append({"no": no, "lines": [(no, line)], "header": (no, line),
                          "kind": "cmd", "flat": True})
            cur = None
            continue
        if line[:1].strip():            # 顶格 = 新块
            cur = {"no": no, "lines": [(no, line)], "header": (no, line),
                   "kind": "cmd", "flat": False}
            units.append(cur)
        elif cur is not None:
            cur["lines"].append((no, line))
        else:
            units.append({"no": no, "lines": [(no, line)], "header": None,
                          "kind": "cmd", "flat": False})
    return units


def unit_ctx(unit):
    """块的视图上下文：view / port（短名）/ port_raw / vlan_id / process_id"""
    ctx = {"view": "root", "port": "", "port_raw": "", "vlan_id": "", "process_id": "",
           "agg_id": ""}
    if unit.get("flat") or not unit.get("header"):
        return ctx
    head = unit["header"][1].strip()
    m = re.match(r"^interface\s+(?:range\s+)?(.+)$", head, re.I)
    if m:
        raw = m.group(1).strip()
        ctx["view"] = "if"
        ctx["port_raw"] = raw
        ctx["port"] = _fmt_range(*parse_range(raw))
        am = re.match(r"^(?:Eth-Trunk|Port-channel|port-channel|aggregateport|"
                      r"AggregatePort|Bridge-Aggregation|smartgroup|ae)\s*(\d+)\s*$",
                      raw, re.I)
        if am:
            ctx["agg_id"] = am.group(1)
        return ctx
    m = re.match(r"^vlan\s+(\d+)\s*$", head, re.I)
    if m:
        ctx["view"] = "vlan"
        ctx["vlan_id"] = m.group(1)
        return ctx
    m = re.match(r"^(?:router\s+)?ospf\s+(\d+)", head, re.I)
    if m:
        ctx["view"] = "ospf"
        ctx["process_id"] = m.group(1)
    return ctx


def _prepass(units, vendors_meta, all_lines):
    """预扫：接口 access/trunk 模式表 + VLAN 名→ID 表（供族规则消歧/解析）"""
    mode_by_port = {}
    vlan_id_by_name = {}
    for unit in units:
        ctx = unit_ctx(unit)
        for _no, line in unit["lines"]:
            m = re.match(r"^set\s+vlans\s+(\S+)\s+vlan-id\s+(\d+)\s*$", line, re.I)
            if m:
                vlan_id_by_name[m.group(1)] = m.group(2)
        if ctx["view"] == "vlan" and ctx["vlan_id"]:
            for _no, line in unit["lines"]:
                m = re.match(r"^\s*(?:name|description)\s+(\S+)", line, re.I)
                if m:
                    vlan_id_by_name.setdefault(m.group(1), ctx["vlan_id"])
        if ctx["view"] == "if" and ctx["port"]:
            for _no, line in unit["lines"]:
                m = re.match(r"^\s*(?:switchport\s+mode|port\s+link-type)\s+(access|trunk)\s*$",
                             line, re.I)
                if m:
                    mode_by_port[ctx["port"]] = m.group(1).lower()
    for _no, line in all_lines:
        m = re.match(r"^set\s+interfaces\s+(\S+)\s+unit\s+\d+\s+family\s+ethernet-switching"
                     r"\s+interface-mode\s+(access|trunk)\s*$", line, re.I)
        if m:
            mode_by_port[_if_short(m.group(1))] = m.group(2).lower()
    return mode_by_port, vlan_id_by_name


# ---------------------------------------------------------------------------
# 四、主流程：逐行归族 + 实例渲染 + 四档账本
# ---------------------------------------------------------------------------

class TranslateResult(dict):
    """dict 子类，便于 UI 直接取字段（保持 JSON 友好）"""

    @property
    def coverage(self):
        return self.get("coverage") or {}


def translate(text, src, dst, rules=None, entry_getter=None):
    """
    整机/片段翻译主入口。返回 TranslateResult：
        lines     [{no,text,tier,family,uuid,title,reason,note}]
        instances [{family,key,uuid,title,params,defaults,dropped,narrow,status,reason,
                    out,rollback,lines:[no...]}]
        coverage  {total,command,translated,comment,manual,skipped,pct}
        output    {config:[...],verify:[...],rollback:[...],header:[...]}
        families  [{id,name,instances,lines}]
        manual    [{no,text,reason}]
        warnings  [str]
        src/dst/src_label/dst_label
    """
    rules = rules if rules is not None else load_rules()[0]
    warnings = []
    if not rules:
        return _empty_result(src, dst, text, "映射表缺失或为空：全部行按需人工处理")
    if src == dst:
        warnings.append("源厂商与目标厂商相同：输出为库内条目重新组装的等价配置，用于核对")
    vendors_meta = rules.get("vendors") or {}
    if src not in vendors_meta or dst not in vendors_meta:
        return _empty_result(src, dst, text, "厂商未在映射表声明（%s → %s）" % (src, dst))

    families = rules.get("families") or []
    secret_res = [re.compile(p, re.I) for p in (rules.get("secret_patterns") or [])]
    hint_res = {}
    for v, hints in (rules.get("private_hints") or {}).items():
        hint_res[v] = [(re.compile(h["re"], re.I), h.get("note") or "私有特性")
                       for h in (hints or [])]

    all_lines = list(enumerate((text or "").splitlines(), 1))
    style = vendors_meta[src].get("style") or "indent"
    units = parse_units(text, style)
    mode_by_port, vlan_id_by_name = _prepass(units, vendors_meta, all_lines)

    ledger = {}          # no -> entry dict
    line_params = {}     # no -> 该行命中的规范参数集合（用于"无对应参数"降级）
    instances = {}       # (family_id, key) -> instance
    order = []           # instance keys in first-seen order
    seg_map = {}         # no -> (family_id, key)

    for unit in units:
        ctx = unit_ctx(unit)
        for idx, (no, line) in enumerate(unit["lines"]):
            is_header = bool(unit.get("header")) and idx == 0 and not unit.get("flat")
            s = line.strip()
            if not s:
                ledger[no] = _line(no, line, "d", reason="blank", note="空行")
                continue
            if s[0] in "!#":
                if s in ("!", "#", "! ", "# "):
                    ledger[no] = _line(no, line, "d", reason="separator",
                                       note="分隔符（源配置的块分隔线）")
                else:
                    ledger[no] = _line(no, line, "b", reason="comment",
                                       note="注释行非命令：按目标厂商注释符重写，语义不变")
                continue
            if _norm(s) in SESSION_LINES:
                ledger[no] = _line(no, line, "d", reason="session",
                                   note="会话包装行（视图进出/落盘），由目标条目模板自带头尾")
                continue
            if any(rx.search(line) for rx in secret_res):
                ledger[no] = _line(no, line, "d", reason="secret",
                                   note="口令/密钥行：不外译，按原值由人工在目标设备重设")
                continue
            if any(rx.search(line) for rx, _note in (hint_res.get(src) or [])):
                hit = next(rx_note for rx, rx_note in (hint_res.get(src) or [])
                           if rx.search(line))
                ledger[no] = _line(no, line, "c", reason="private",
                                   note="私有特性：%s（库内无跨厂商映射族）" % hit)
                continue

            match = _match_line(line, is_header, ctx, mode_by_port, vlan_id_by_name,
                                families, src)
            if not match:
                ledger[no] = None        # 待二次判定（容器行 / 未覆盖）
                continue
            fam, vmeta, params = match
            if not fam_target_covered(fam, dst, vendors_meta):
                # 族声明了目标厂商范围（库内确缺该厂商条目）→ 如实判需人工，
                # 不硬凑映射、也不归入「族外」口径
                ledger[no] = _line(
                    no, line, "c", reason="no_family",
                    note="族「%s」库内无 %s 条目（该族可译目标：%s），需人工处理"
                         % (fam.get("name") or fam["id"],
                            (vendors_meta.get(dst) or {}).get("label") or dst,
                            "、".join((vendors_meta.get(s) or {}).get("label") or s
                                     for s in fam_scope(fam, vendors_meta))))
                continue
            key = _instance_key(fam, vmeta, params, ctx)
            inst = instances.get((fam["id"], key))
            if inst is None:
                inst = {
                    "family": fam["id"], "family_name": fam.get("name") or fam["id"],
                    "key": key,
                    "uuid": (fam.get("vendors") or {}).get(dst, {}).get("entry") or "",
                    "src_entry": vmeta.get("entry") or "", "vendor": src,
                    "params": {}, "members": [], "lines": [],
                    "status": "pending", "reason": "", "defaults": [], "dropped": [],
                    "narrow": [], "out": [], "rollback": [],
                }
                instances[(fam["id"], key)] = inst
                order.append((fam["id"], key))
            for k, v in params.items():
                if k == "members":
                    if v and v not in inst["members"]:
                        inst["members"].append(v)
                elif k not in inst["params"] or inst["params"].get(k) in (None, ""):
                    inst["params"][k] = v
            inst["lines"].append(no)
            line_params[no] = set(params.keys())
            seg_map[no] = (fam["id"], key)
            ledger[no] = _line(no, line, "a", family=fam["id"],
                               uuid=inst["uuid"] or "", note="")

    # ---- 二次判定：容器行 / 完全未命中行 ----
    families_by_id = {f["id"]: f for f in families}
    for unit in units:
        ctx = unit_ctx(unit)
        for idx, (no, line) in enumerate(unit["lines"]):
            if ledger.get(no) is not None:
                continue
            is_header = bool(unit.get("header")) and idx == 0 and not unit.get("flat")
            if is_header and ctx["view"] == "if":
                owner = _container_owner(instances, order, ctx["port"])
                if owner is not None:
                    ledger[no] = _line(no, line, "a", family=owner["family"],
                                       uuid=owner["uuid"],
                                       note="接口视图起始行：由目标条目模板自带（%s）"
                                            % owner["family_name"])
                    seg_map[no] = (owner["family"], owner["key"])
                    # 容器行随属主实例同进退：属主渲染失败时一并降级，
                    # 不留「a 档却指向未产出命令的条目」的假已译行
                    owner["lines"].append(no)
                    continue
            if ctx["view"] in ("if", "vlan", "ospf"):
                reason = "uncovered_view"
                note = ("「%s」视图内该行无库内族映射规则（族清单见 docs/dev-notes.md V 批）"
                        % unit["header"][1].strip())
            else:
                reason = "no_family"
                note = "库内无对应族条目，需人工换算"
            ledger[no] = _line(no, line, "c", reason=reason, note=note)

    # ---- 无键实例合并：同类同端口的「无键」行并入有键实例 ----
    nokey = {f["id"] for f in families if not fam_has_key(f)}
    _merge_keyless(instances, order, ledger, nokey)

    # ---- 实例渲染 ----
    rendered = []
    for fam_id, key in order:
        inst = instances[(fam_id, key)]
        fam = families_by_id[fam_id]
        entry = entry_getter(inst["uuid"]) if entry_getter else None
        if entry is None:
            inst["status"] = "fail"
            inst["reason"] = "目标条目取不到（uuid %s），需人工" % (inst["uuid"] or "")[:8]
            _degrade(inst, ledger, "c", "entry_missing", inst["reason"])
            continue
        info = _render_instance(fam, entry, inst, dst, vendors_meta.get(dst) or {})
        if info.get("error"):
            inst["status"] = "fail"
            inst["reason"] = info["error"]
            _degrade(inst, ledger, "c", "param_missing", info["error"])
            continue
        inst.update({"status": "ok", "defaults": info["defaults"],
                     "dropped": info["dropped"], "narrow": info["narrow"],
                     "out": info["out"], "rollback": info["rollback"],
                     "pre": info["pre"], "post": info["post"], "verify": info["verify"],
                     "unbound": info.get("unbound") or [],
                     "title": entry.get("title") or ""})
        unbound_set = set(info.get("unbound") or [])
        for no in inst["lines"]:
            if no in ledger and ledger[no]["tier"] == "a":
                keys = line_params.get(no) or set()
                if keys and unbound_set and keys.issubset(unbound_set):
                    ledger[no]["tier"] = "c"
                    ledger[no]["reason"] = "unbound"
                    ledger[no]["note"] = ("源项在目标条目无对应参数（%s），不输出，需人工处理"
                                          % "、".join(sorted(keys)))
                    continue
                notes = []
                if info["defaults"]:
                    notes.append("参数取库内默认值：%s"
                                 % "、".join("%s=%s" % (k, v) for k, v in info["defaults"]))
                if info["dropped"]:
                    notes.append("源配置无对应值，目标模板相应行已省略：%s"
                                 % "、".join(info["dropped"]))
                if info.get("unbound"):
                    notes.append("源项在目标条目无对应参数，已省略（需人工确认）：%s"
                                 % "、".join(info["unbound"]))
                if info["narrow"]:
                    notes.append("；".join(info["narrow"]))
                ledger[no]["note"] = "；".join(notes)
                ledger[no]["title"] = inst["title"]
                ledger[no]["family_name"] = inst["family_name"]
        rendered.append(inst)

    # ---- 组装输出 ----
    output = _assemble(rendered, dst, vendors_meta.get(dst) or {}, src, dst,
                       ledger, vendors_meta.get(src) or {})

    # ---- 统计 ----
    lines_out = [ledger[no] for no in sorted(ledger)]
    total = len(lines_out)
    blank = sum(1 for x in lines_out if x.get("reason") == "blank")
    comment = sum(1 for x in lines_out if x["tier"] == "b")
    sep = sum(1 for x in lines_out if x.get("reason") == "separator")
    command = total - blank - comment - sep
    translated = sum(1 for x in lines_out if x["tier"] == "a")
    manual = sum(1 for x in lines_out if x["tier"] == "c")
    skipped = sum(1 for x in lines_out if x["tier"] == "d") - blank - sep
    pct = round(translated * 100.0 / command, 1) if command else 0.0

    fam_stat = []
    for fam in families:
        hits = sum(1 for x in lines_out if x.get("family") == fam["id"])
        insts = [i for i in rendered if i["family"] == fam["id"]]
        if hits or insts:
            fam_stat.append({"id": fam["id"], "name": fam.get("name") or fam["id"],
                             "category": fam.get("category") or "",
                             "instances": len(insts), "lines": hits,
                             "ok": sum(1 for i in insts if i["status"] == "ok")})

    return TranslateResult({
        "src": src, "dst": dst,
        "src_label": (vendors_meta.get(src) or {}).get("label") or src,
        "dst_label": (vendors_meta.get(dst) or {}).get("label") or dst,
        "lines": lines_out,
        "instances": rendered,
        "coverage": {"total": total, "command": command, "translated": translated,
                     "comment": comment, "manual": manual, "skipped": skipped,
                     "pct": pct},
        "output": output,
        "families": fam_stat,
        "manual": [{"no": x["no"], "text": x["text"], "reason": x.get("note") or ""}
                   for x in lines_out if x["tier"] == "c"],
        "warnings": warnings + output.get("warnings", []),
    })


def _empty_result(src, dst, text, reason):
    lines_out = [_line(no, line, "c", reason="norules", note=reason)
                 for no, line in enumerate((text or "").splitlines(), 1) if line.strip()]
    return TranslateResult({
        "src": src, "dst": dst, "src_label": src, "dst_label": dst,
        "lines": lines_out, "instances": [],
        "coverage": {"total": len(lines_out), "command": len(lines_out), "translated": 0,
                     "comment": 0, "manual": len(lines_out), "skipped": 0, "pct": 0.0},
        "output": {"config": [], "verify": [], "rollback": [], "header": []},
        "families": [], "manual": [{"no": x["no"], "text": x["text"], "reason": reason}
                                   for x in lines_out],
        "warnings": [reason],
    })


def _norm(line):
    return " ".join((line or "").strip().lower().split())


def _line(no, text, tier, **kw):
    item = {"no": no, "text": text.rstrip(), "tier": tier, "family": kw.get("family") or "",
            "uuid": kw.get("uuid") or "", "title": kw.get("title") or "",
            "family_name": kw.get("family_name") or "", "reason": kw.get("reason") or "",
            "note": kw.get("note") or ""}
    return item


def _degrade(inst, ledger, tier, reason, note):
    for no in inst["lines"]:
        if no in ledger and ledger[no]["tier"] == "a":
            ledger[no]["tier"] = tier
            ledger[no]["reason"] = reason
            ledger[no]["note"] = note
            ledger[no]["uuid"] = inst.get("uuid") or ""


def _match_line(line, is_header, ctx, mode_by_port, vlan_id_by_name, families, src):
    for fam in families:
        vmeta = (fam.get("vendors") or {}).get(src)
        if not vmeta:
            continue
        for rule in vmeta.get("lines") or []:
            scope = rule.get("scope")
            if scope == "header" and not is_header:
                continue
            if scope == "body" and is_header:
                continue
            view = rule.get("view")
            if view and ctx.get("view") != view:
                continue
            prefix = rule.get("port_prefix")
            if prefix and ctx.get("view") == "if":
                raw = (ctx.get("port_raw") or "").lower()
                if not any(k in raw for k in prefix):
                    continue
            m = re.match(rule["re"], line, re.I)
            if not m:
                continue
            groups = {k: v for k, v in m.groupdict().items() if v not in (None, "")}
            port_mode = None
            probe = _if_short(groups.get("ifname") or ctx.get("port") or "")
            if probe:
                port_mode = mode_by_port.get(probe)
            need_mode = rule.get("mode")
            if need_mode and port_mode and port_mode != need_mode:
                continue
            params = dict(groups)
            for tgt, src_key in (rule.get("inherit") or {}).items():
                if not params.get(tgt):
                    params[tgt] = ctx.get(src_key) or ""
            for tgt, src_key in (rule.get("collect") or {}).items():
                params[tgt] = ctx.get(src_key) or ""
            for from_key, to_key in (rule.get("resolve") or {}).items():
                if not params.get(to_key):
                    params[to_key] = vlan_id_by_name.get(params.get(from_key) or "") or ""
            params = {k: v for k, v in params.items() if v not in (None, "")}
            return fam, vmeta, params
    return None


def _instance_key(fam, vmeta, params, ctx):
    keys = vmeta.get("key") or fam.get("key") or []
    vals = []
    for k in keys:
        v = params.get(k) or ctx.get(k) or ""
        vals.append(str(v))
    return "|".join(vals) if any(vals) else "*"


def _merge_keyless(instances, order, ledger, nokey_fams=None):
    """
    一行匹配上了族但取不到族键（如『switchport mode access』本身不含 vlan_id）时，
    该行先落在无键实例（key='*'）。这里把它并入「同族 + 同端口」的唯一有键实例；
    并不到则整行降级为需人工——绝不拿库内默认值凑一个实例出来。

    无键族（V 批：查询对照族 / 显示控制这类单实例族）不适用降级：
    整族本就没有分组键，key='*' 即其唯一实例。
    """
    nokey_fams = nokey_fams or set()
    by_port = {}
    for fam_id, key in list(order):
        if key == "*":
            continue
        inst = instances[(fam_id, key)]
        p = inst["params"].get("ifname") or inst["params"].get("port") or ""
        if p:
            by_port.setdefault((fam_id, _fmt_range(*parse_range(str(p)))), []).append(inst)
    for fam_id, key in list(order):
        if key != "*":
            continue
        if fam_id in nokey_fams:
            continue                    # 无键族：key='*' 就是它的唯一实例，原样保留
        inst = instances[(fam_id, key)]
        p = inst["params"].get("ifname") or inst["params"].get("port") or ""
        cands = by_port.get((fam_id, _fmt_range(*parse_range(str(p))))) if p else None
        if not cands:
            sib = [instances[(f, k)] for f, k in order if f == fam_id and k != "*"]
            if len(sib) == 1:
                cands = sib
        uniq = {id(c): c for c in (cands or [])}
        if len(uniq) == 1:
            tgt = list(uniq.values())[0]
            for no in inst["lines"]:
                tgt["lines"].append(no)
                if no in ledger:
                    ledger[no]["uuid"] = tgt["uuid"]
            tgt["lines"] = sorted(set(tgt["lines"]))
            for k, v in inst["params"].items():
                tgt["params"].setdefault(k, v)
            del instances[(fam_id, key)]
            order.remove((fam_id, key))
            continue
        for no in inst["lines"]:
            if no in ledger:
                ledger[no]["tier"] = "c"
                ledger[no]["reason"] = "key_missing"
                ledger[no]["note"] = ("族「%s」判定键未取到（该行不含键值），"
                                      "无法定位目标条目实例，需人工" % inst["family_name"])
        del instances[(fam_id, key)]
        order.remove((fam_id, key))


def _container_owner(instances, order, port):
    cands = []
    for fam_id, key in order:
        inst = instances[(fam_id, key)]
        p = (inst["params"].get("ifname") or inst["params"].get("port")
             or inst["params"].get("vlan_id") or "")
        if p and _fmt_range(*parse_range(str(p))) == port:
            cands.append(inst)
            continue
        members = [_fmt_range(*parse_range(str(m))) for m in (inst.get("members") or [])]
        if port and any(port == m for m in members):
            cands.append(inst)
    if not cands:
        return None
    for inst in cands:
        if inst["family"] == "ifbase":
            return inst
    return cands[0]


def _drop_template_lines(template, param):
    """把引用某参数的模板行整行剔除（该参数源配置里没有，且不允许拿默认值凑）"""
    marks = ("{{%s}}" % param, "{{ %s }}" % param)
    keep = []
    for ln in (template or "").splitlines():
        if any(mk in ln for mk in marks):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _render_instance(fam, entry, inst, dst, dst_meta):
    """渲染单个实例：bind → 取值/默认 → 渲染 → 剔除可选行 → 拆分头/体/尾/复核"""
    specs = [s for s in renderer._load_params(entry)
             if isinstance(s, dict) and s.get("name")]
    spec_map = {s["name"]: s for s in specs}
    optional = set(fam.get("optional") or [])
    default_ok = set(fam.get("default_ok") or [])
    resolved = _resolve_bind(fam.get("bind") or {}, dst)

    values, defaults, dropped, missing, narrow_flags = {}, [], [], [], []
    for name, spec in spec_map.items():
        raw = None
        if name in resolved:
            for canon, transform, narrow in resolved[name]:
                cand = inst["params"].get(canon)
                if cand in (None, "") and canon == "members":
                    cand = inst["members"]
                if cand in (None, ""):
                    continue
                try:
                    raw = apply_transform(cand, transform, dst, dst_meta)
                except ValueError as exc:
                    return {"error": "变换失败：%s" % exc}
                if narrow:
                    a, b = parse_range(str(cand if not isinstance(cand, list) else ""))
                    if a and b and a != b:
                        narrow_flags.append("源为端口范围 %s，目标模板为单口：仅译首个端口 %s"
                                            % (_fmt_range(a, b), a))
                if raw not in (None, ""):
                    break
        else:
            raw = inst["params"].get(name)
        if isinstance(raw, (list, tuple)):
            raw = ",".join([str(x) for x in raw if x])
        if raw not in (None, "") and str(raw).strip():
            values[name] = str(raw)
            continue
        if name in optional:
            dropped.append(name)
            continue
        default = spec.get("default")
        if default not in (None, "") and name in default_ok:
            values[name] = str(default)
            defaults.append((name, str(default)))
            continue
        if spec.get("type") == "flag":
            continue
        missing.append(name)
    if missing:
        return {"error": "目标条目必填参数无法从源配置推导：%s（库内默认值不在可用清单内）"
                         % "、".join(missing)}

    template = entry.get("commands") or ""
    for p in dropped:
        template = _drop_template_lines(template, p)
    text, miss = renderer.render(template, values, vendor=dst, specs=[], keep_unknown=True)
    if miss:
        return {"error": "目标条目模板占位未填：%s（映射表 bind 缺项）" % "、".join(miss)}
    lines = [ln.rstrip() for ln in text.splitlines()]
    pre, body, post, verify = split_entry_lines(lines)

    rb_out = []
    if (entry.get("rollback") or "").strip():
        rb_tpl = entry.get("rollback") or ""
        for p in dropped:
            rb_tpl = _drop_template_lines(rb_tpl, p)
        rb_text, rb_miss = renderer.render(rb_tpl, values, vendor=dst, specs=[],
                                           keep_unknown=True)
        if not rb_miss:
            rb_out = [ln for ln in (l.rstrip() for l in rb_text.splitlines()) if ln.strip()]
    bound = {c for cands in resolved.values() for (c, _t, _n) in cands}
    for name in spec_map:
        if name in inst["params"] and name not in resolved:
            bound.add(name)
    # 实例分组键只用于定位目标条目实例，不是「待译字段」——不参与无对应参数判定
    key_names = set(fam.get("key") or [])
    key_names.update(((fam.get("vendors") or {}).get(inst.get("vendor") or "") or {})
                     .get("key") or [])
    unbound = sorted(k for k in inst["params"]
                     if k not in bound and k not in key_names and k != "members")
    return {"pre": pre, "body": body, "post": post, "verify": verify,
            "out": body, "rollback": rb_out, "defaults": defaults,
            "dropped": dropped, "narrow": narrow_flags, "unbound": unbound}


def split_entry_lines(lines):
    """把条目渲染结果拆成 (头, 体, 尾, 复核)。复核 = 查询类行，头尾 = 会话包装/落盘。"""
    pre, body, post, verify = [], [], [], []
    idx_body_start = None
    for i, ln in enumerate(lines):
        # 纯查询条目（全篇 show/display + 注释）没有「配置体」：整篇进复核区，
        # 否则夹在查询行之间的注释会掉进配置区，看着像丢了内容
        if (not _is_wrapper(ln) and not _is_comment(ln) and ln.strip()
                and not _is_query(ln)):
            idx_body_start = i
            break
    if idx_body_start is None:
        return [], [], [], [ln for ln in lines if ln.strip()]
    idx_body_end = idx_body_start
    for i in range(idx_body_start, len(lines)):
        ln = lines[i]
        if ln.strip() and not _is_wrapper(ln) and not _is_comment(ln) and not _is_query(ln):
            idx_body_end = i
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        if i < idx_body_start:
            pre.append(ln)
        elif i <= idx_body_end:
            body.append(ln)
        elif _is_query(ln):
            verify.append(ln)
        else:
            post.append(ln)
    # 复核行若落在体内（个别条目把验证命令写在中间）→ 移出
    kept = []
    for ln in body:
        if _is_query(ln) and not _is_comment(ln):
            if ln not in verify:
                verify.append(ln)
        else:
            kept.append(ln)
    return pre, kept, post, verify


def _is_wrapper(line):
    s = _norm(line)
    if s in _WRAP_EXACT:
        return True
    return s in ("exit", "quit", "end")


def _is_comment(line):
    s = (line or "").strip()
    return bool(s) and s[0] in "!#"


def _is_query(line):
    s = _norm(line)
    if not s:
        return False
    return renderer._prefix_hit(s, renderer.QUERY_WHITELIST)


def _rewrite_comment(line, mark):
    """注释行换厂商注释符（! ↔ #），语义不变；非命令，不改内容"""
    body = (line or "").strip()
    body = re.sub(r"^[!#]+\s?", "", body).strip()
    return (mark + " " + body).rstrip() if body else mark


def _assemble(rendered, dst, dst_meta, src, dst_label, ledger, src_meta):
    """组装目标配置：头注释 + 首块头 + 各块体 + 末块尾 + 复核 + 回退"""
    mark = dst_meta.get("comment") or "!"
    ok = [i for i in rendered if i["status"] == "ok"]
    warnings = []
    total = len([x for x in ledger.values() if x])
    trans = sum(1 for x in ledger.values() if x and x["tier"] == "a")
    manual = sum(1 for x in ledger.values() if x and x["tier"] == "c")
    cmd = sum(1 for x in ledger.values() if x and x["reason"] not in ("blank", "separator")
              and x["tier"] != "b")
    pct = round(trans * 100.0 / cmd, 1) if cmd else 0.0

    header = [
        "%s ===== 配置翻译：%s → %s =====" % (
            mark, src_meta.get("label") or src, dst_meta.get("label") or dst_label),
        "%s ⚠ 语法核对档：输出由库内条目模板生成，未经真机验证" % mark,
        "%s ⚠ 接口编号沿用源设备写法，未做机框/槽位换算，执行前逐条核对" % mark,
        "%s ⚠ 目标条目为库内完整配方：可能含源配置未出现的配套行（缺省路由/端口开启/"
        "保存），逐行核对后再执行" % mark,
        "%s 覆盖：已译 %d / 命令行 %d（%.1f%%）｜需人工 %d 行（见账本）"
        % (mark, trans, cmd, pct, manual),
    ]
    config = list(header)
    if not ok:
        config.append("%s 无任何族命中：本份配置全部需人工处理，未生成目标命令" % mark)
        return {"config": config, "verify": [], "rollback": [], "header": header,
                "warnings": ["零族命中：未输出任何目标命令（宁可账本长，不猜一行）"]}

    pre = next((i["pre"] for i in ok if i.get("pre")), []) if ok else []
    post = next((i["post"] for i in reversed(ok) if i.get("post")), [])
    config.extend(pre)
    if pre:
        config.append("")
    for i, inst in enumerate(ok):
        for ln in inst["out"]:
            config.append(ln)
        config.append("")
    if post:
        config.extend(post)
    verify = []
    for inst in ok:
        for ln in inst.get("verify", []):
            if ln not in verify:
                verify.append(ln)
    if verify:
        config.append("%s ===== 复核命令（只读）=====" % mark)
        config.extend(verify)
    rollback = []
    for inst in ok:
        for ln in inst.get("rollback", []):
            if ln not in rollback:
                rollback.append(ln)
    if rollback:
        config.append("")
        config.append("%s ===== 回退命令（库内条自带，同样未经真机核对）=====" % mark)
        config.extend(rollback)
    comments = [_rewrite_comment(x["text"], mark) for x in ledger.values()
                if x and x["tier"] == "b"]
    if comments:
        config.append("")
        config.append("%s ===== 源配置注释行（注释符重写，非命令）=====" % mark)
        config.extend(comments)
    if not pre and not post:
        warnings.append("目标条目未带会话包装行，粘贴前请自行进入配置视图")
    return {"config": config, "verify": verify, "rollback": rollback,
            "header": header, "warnings": warnings}


# ---------------------------------------------------------------------------
# 五、报告
# ---------------------------------------------------------------------------

def render_markdown(result):
    """账本导出（Markdown）：覆盖摘要 + 族覆盖 + 四档明细 + 需人工清单"""
    cov = result.get("coverage") or {}
    out = []
    out.append("# 配置翻译账本（%s → %s）"
               % (result.get("src_label"), result.get("dst_label")))
    out.append("")
    out.append("> 语法核对档，未经真机验证。目标侧每一行命令均来自 NetToolBox 库内条目模板。")
    out.append("")
    out.append("## 覆盖率")
    out.append("")
    out.append("| 指标 | 数值 |")
    out.append("|---|---|")
    out.append("| 已译行（a 档） | %d |" % cov.get("translated", 0))
    out.append("| 命令行总数（非空非注释非分隔） | %d |" % cov.get("command", 0))
    out.append("| 翻译完成度 | %.1f%% |" % cov.get("pct", 0.0))
    out.append("| 逐行映射（b 档，注释） | %d |" % cov.get("comment", 0))
    out.append("| 需人工（c 档） | %d |" % cov.get("manual", 0))
    out.append("| 跳过（d 档） | %d |" % cov.get("skipped", 0))
    out.append("")
    if result.get("families"):
        out.append("## 族覆盖")
        out.append("")
        out.append("| 族 | 分类 | 实例 | 覆盖行 | 渲染成功 |")
        out.append("|---|---|---|---|---|")
        for f in result["families"]:
            out.append("| %s | %s | %d | %d | %d |"
                       % (f["name"], f.get("category") or "-", f["instances"],
                          f["lines"], f.get("ok", 0)))
        out.append("")
    if result.get("manual"):
        out.append("## 需人工清单（%d 行）" % len(result["manual"]))
        out.append("")
        out.append("| 行号 | 源命令 | 原因 |")
        out.append("|---|---|---|")
        for m in result["manual"]:
            out.append("| %d | `%s` | %s |"
                       % (m["no"], m["text"].strip().replace("|", "\\|"), m["reason"]))
        out.append("")
    out.append("## 行级账本")
    out.append("")
    out.append("| 行号 | 档 | 源命令 | 归属族 | 目标条目 | 说明 |")
    out.append("|---|---|---|---|---|---|")
    for ln in result.get("lines") or []:
        out.append("| %d | %s | `%s` | %s | %s | %s |" % (
            ln["no"], TIER_LABELS.get(ln["tier"], ln["tier"]),
            (ln["text"] or "").strip().replace("|", "\\|"), ln.get("family_name") or ln.get("family") or "-",
            (ln.get("uuid") or "-")[:8], (ln.get("note") or "").replace("|", "\\|")))
    out.append("")
    return "\n".join(out)
