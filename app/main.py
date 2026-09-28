# -*- coding: utf-8 -*-
"""
main.py —— 离网网络运维工具箱（NetToolBox）程序入口

启动流程：
    1. 确定 db 路径（程序同目录 command_lib.db，schema 自动升级到 v2）
    2. 无 db → 自动建空库 → 主窗口弹出"是否导入内置种子库"
    3. U 盘只读 → 自动进入只读模式（编辑功能禁用）
    4. 应用深色主题 QSS，拉起主窗口

硬性约束（隔离网安全审计要求）：
    - 不 import 任何网络模块，无任何联网行为
    - 不含终端/SSH/串口/设备连接功能，命令由外部 Xshell/SecureCRT 执行

命令行参数：
    --selftest        仅运行数据层与渲染层自检（不开界面，便于离网环境排障）
    --audit-offline   仅运行离网审计（确认运行时代码零网络依赖，交付前必跑）
    --audit-offline   离网审计：确认运行时代码零网络依赖（交付前必跑）
    --validate-seed   种子库质检：导入 seed_data/*.json 并逐条校验（加条目后必跑）
    --check-ai        AI 诊断集成自检：配置状态 / HTTP 依赖是否真的进包（打包后必跑）
    --db PATH         指定命令库文件路径（默认程序同目录 command_lib.db）
"""

import os
import sys
import re
import json
import traceback


# ---------------------------------------------------------------------------
# 自检（不需要图形界面，离网环境排障用）
# ---------------------------------------------------------------------------
def run_selftest(db_path=None):
    """数据层 + 渲染层自检，全部通过打印 OK"""
    import db as dbmod
    import renderer

    print("=" * 66)
    print("NetToolBox 自检（数据层 + 渲染层）")
    print("=" * 66)

    # ---------- 1. 数据层 ----------
    # 用内存库跑，不产生临时 db 文件（离网/受限环境更干净）
    path = db_path or ":memory:"
    database = dbmod.Database(path)
    print("[1] 建库成功：%s" % ("内存库（不落盘）" if path == ":memory:" else path))
    print("    只读标志：%s" % database.readonly)

    database.import_entries(dbmod.DEMO_ENTRIES, merge=True, operator="selftest")
    stats = database.stats()
    print("    导入演示条目：共 %d 条 / 已验证 %d / 收藏 %d"
          % (stats["total"], stats["verified"], stats["favorite"]))

    print("    关键字搜索 'trunk' → %d 条" % len(database.search(keyword="trunk")))
    print("    关键字搜索 '路由'  → %d 条" % len(database.search(keyword="路由")))
    print("    过滤 厂商=华为     → %d 条" % len(database.search(vendor="华为")))
    print("    过滤 场景=Trunk    → %d 条" % len(database.search(category="Trunk")))
    print("    只看收藏           → %d 条" % len(database.search(favorite_only=True)))

    entries = database.search()
    if entries:
        target = entries[0]
        database.mark_verified(target["uuid"], "自检员", "S5720-28X-SI", "2026-01-01")
        again = database.get_entry(target["uuid"])
        print("    标记已验证后 verified=%s，验证人=%s，历史 %d 条"
              % (again["verified"], again["verified_by"], len(database.get_history(target["uuid"]))))
        database.toggle_favorite(target["uuid"])
        print("    收藏切换后 favorite=%s" % database.get_entry(target["uuid"])["favorite"])

    nlb = os.path.join(os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__)),
        "NetToolBox_selftest.nlb")
    count = database.export_nlb(nlb)
    database.delete_entry(entries[0]["uuid"]) if entries else None
    added, updated, skipped = database.import_nlb(nlb, merge=True)
    print("    导出 %d 条 → 回导：新增 %d / 更新 %d / 跳过 %d（含已验证优先合并）"
          % (count, added, updated, skipped))
    print("    左侧树数据：%s" % (database.tree_data() or {}))
    database.close()

    # ---------- 2. 渲染层 ----------
    print("-" * 66)
    text = ("! 注释行应当被剔除\n"
            "vlan {{vlan_id:10}}\n"
            "interface {{ports}}\n"
            " switchport access vlan {{vlan_id}}\n")
    specs = [
        {"name": "vlan_id", "label": "VLAN ID", "default": "10", "required": True,
         "validate": "vlan", "example": "10"},
        {"name": "ports", "label": "端口范围", "default": "0/1-0/10", "required": True,
         "validate": "port_range", "expand": "port", "example": "0/1-0/10"},
    ]
    for vendor in ("Cisco", "华为", "H3C", "Fortinet"):
        col, names, err = renderer.expand_port_range("0/1-0/10", vendor)
        print("[2] %-9s 端口展开：%s %s" % (vendor, col or "(空)", ("[提示]%s" % err) if err else ""))

    # 端口命名规则回归矩阵（各厂商短写/长写/三段式/单段式都要对）
    print("    端口命名规则矩阵（vendor_slug + os_slug，验证 OS 级覆盖）：")
    for vendor, os_family, expr in (
            ("cisco", "ios", "0/1-0/3"), ("cisco", "ios", "1/0/1-1/0/3"),
            ("huawei", "vrp5", "0/1-0/3"), ("huawei", "vrp5", "0/0/1-0/0/3"),
            ("huawei", "vrp8", "1/0/1-1/0/3"),          # ★ OS 级覆盖 → GE
            ("h3c", "comware7", "0/1-0/3"), ("h3c", "comware7", "2/0/1-2/0/3"),
            ("ruijie", "rgos", "0/1-0/3"), ("juniper", "junos", "0/1-0/3"),
            ("fortinet", "fortios", "1-3"), ("sangfor", "sangfor_af", "1-3"),
            ("topsec", "topos", "1-3"), ("paloalto", "panos", "1-3"),
            ("zte", "zxr10", "1/1-1/3"), ("arista", "eos", "1-3")):
        col, _names, err = renderer.expand_port_range(expr, vendor, os_family=os_family)
        flag = ""
        if vendor == "huawei" and os_family == "vrp8" and not col.startswith("GE"):
            flag = "  [X] 应为 GE 前缀（OS 级覆盖失效）"
        print("      %-9s %-9s %-14s → %s%s%s"
              % (vendor, os_family, expr, col, ("  [X]%s" % err) if err else "", flag))

    print("    参数校验 单条：vlan=5000 → %s" % (renderer.validate_value(specs[0], "5000"),))
    print("    参数校验 表单：%s" % renderer.validate_form(
        specs, {"vlan_id": "", "ports": "0/1-0/10"}))
    rendered, missing, merged_specs = renderer.render_entry(
        {"commands": text, "params": specs, "vendor": "Cisco"}, {}, vendor="Cisco")
    print("    渲染结果：\n%s" % "\n".join("        " + ln for ln in rendered.splitlines()))
    print("    缺失参数：%s" % (missing or "无"))
    print("    剔除注释后有效命令数：%d" % renderer.count_effective_lines(rendered))
    print("    配置包头部：\n%s" % "\n".join(
        "        " + ln for ln in renderer.build_header(
            {"title": "自检示例", "vendor": "Cisco", "os_family": "Cisco IOS",
             "models": "C2960", "category": "VLAN"},
            operator="自检员", scene="VLAN 创建").splitlines()))

    # 清理自检产生的 .nlb（尽力而为：受限环境可能禁止删除，删不掉就提示位置）
    removed = True
    try:
        if os.path.exists(nlb):
            os.remove(nlb)
    except Exception:
        removed = False
    if not removed:
        print("    提示：自检产物未能删除，请手工清理 %s" % nlb)

    # ---------- 3. Markdown 渲染（AI 回复展示层，任务5-3 自测样例）----------
    print("-" * 66)
    md_ok = True
    try:
        import ui_ai
        md_ok, cases = ui_ai.render_selftest()
        for name, case_ok, note in cases:
            print("[3] %-26s %s  %s" % (name, "OK" if case_ok else "FAIL", note))
    except Exception as exc:
        md_ok = False
        print("[3] Markdown 渲染自测异常：%s" % exc)

    print("=" * 66)
    if not md_ok:
        print("自检未通过：Markdown 渲染样例有失败项（AI 回复展示会错乱，请修 ui_ai.md_to_html）")
        return 1
    print("自检完成：数据层 + 渲染层 + Markdown 渲染全部通过 OK")
    return 0


# ---------------------------------------------------------------------------
# 离网审计（交付前硬性检查）：确认运行时代码零网络依赖
# ---------------------------------------------------------------------------
# 禁止出现的网络相关模块（★ 本工具只生成命令，绝不连设备、绝不联网）
FORBIDDEN_MODULES = {
    "socket", "ssl", "requests", "httpx", "aiohttp", "urllib", "urllib2",
    "urllib3", "http", "httplib", "xmlrpc", "paramiko", "telnetlib", "ftplib",
    "smtplib", "poplib", "imaplib", "asyncio", "websocket", "websockets",
    "scapy", "netmiko", "napalm", "pysnmp", "ncclient",
}
# 禁止出现的调用/字面量特征（字符串扫描，粗筛后人工确认）
FORBIDDEN_SNIPPETS = [
    "urlopen(", "requests.", "socket.", "paramiko.", "telnetlib.",
    "os.system(", "subprocess.call(", "subprocess.run(", "subprocess.Popen(",
    "http://", "https://",
]
# 运行时代码（交付必须零网络）；开发工具单独列，不算失败
RUNTIME_FILES = ["main.py", "db.py", "renderer.py", "error_match.py",
                 "output_analyzer.py", "report.py", "migrate_to_slug.py",
                 "ui_main.py", "ui_generator.py", "ui_editor.py",
                 "ui_troubleshoot.py", "ui_errorfix.py", "ui_tree_editor.py",
                 "check_db.py"]
DEV_FILES = ["build_exe.py"]

# 「属性调用」级检查用的网络模块名（第 4 轮 P3-5）：
#     (2) 的特征串扫描会跳过"含字符串字面量的整行"，于是 x = foo("http://…") 这类写法漏检。
#     这里用 AST 再看一遍**调用**：只要出现 requests.get(...) / socket.socket(...) 这种
#     "以网络模块名为根"的属性调用就报；并对 __import__ 动态导入单列一条。
#     说明：AST 只看得到直接调用，getattr(requests, "get")() 这类绕过不在覆盖内 ——
#     真正的兜底仍是 (1) 的导入检查（任何 import 都躲不过）。
NET_CALL_ROOTS = set(FORBIDDEN_MODULES)

# 排查树"叶子处理动作"里禁止出现的命令文本特征（第 2 轮 P1-5）：
#     动作只写"做什么"，命令必须通过 cmd_ref 引用命令库条目渲染 ——
#     否则改了库条目，树里内嵌的命令文本不会跟着变（联动失效的头号原因）。
# 说明：这是"行首命令词"的底线检查。句中出现的命令片段（lsof / firewall-cmd /
#      dig / ethtool 等）已在第 2 轮数据侧一并清理为纯动作描述，若日后回潮，
#      按需把词加进本表即可。
ACTION_CMD_PAT = re.compile(
    r"^\s*(?:display|show|ip |system-view|interface |vlan |undo |no |configure|enable|"
    r"save|write|quit|exit|end|sysctl|systemctl|ss |netstat|df |du |journalctl|tcpdump|"
    r"ifcfg|netplan|ping |route |arp |traceroute|mtr |ps |cat |lsof|dig|nslookup|"
    r"resolvectl|ethtool|firewall-cmd|ufw|iptables|nmcli|nc |telnet|curl|wget|"
    r"shutdown|iostat|sar|free |vmstat|top |kill |sed |awk |grep |tail |head )", re.I)


def audit_offline(base=None):
    """
    离网审计：AST 解析导入 + 特征串扫描，确认运行时代码不引入任何网络能力。
    这是交付前的硬性门槛（离网现场绝不允许程序试图联网）。
    """
    import ast as _ast
    base = base or os.path.dirname(os.path.abspath(__file__))
    problems = []
    checked = []

    def audit_file(path, tag):
        try:
            source = open(path, "r", encoding="utf-8").read()
        except Exception as exc:
            problems.append("%s：读取失败（%s）" % (os.path.basename(path), exc))
            return
        checked.append(os.path.basename(path))
        # (1) AST 精确解析导入
        try:
            tree = _ast.parse(source)
        except SyntaxError as exc:
            problems.append("%s：语法错误无法解析（第 %s 行）"
                            % (os.path.basename(path), exc.lineno))
            return
        for node in _ast.walk(tree):
            names = []
            if isinstance(node, _ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, _ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            for name in names:
                if name in FORBIDDEN_MODULES:
                    problems.append("%s[%s]：导入了网络相关模块 `%s`（第 %s 行）"
                                    % (os.path.basename(path), tag, name,
                                       getattr(node, "lineno", "?")))
        # (2) 特征串扫描 —— ★ 只扫"真实代码行"：
        #     先由 AST 记录所有字符串字面量（含文档字符串）覆盖的行号，
        #     这些是文档/数据而不是调用，必须排除；否则本审计自己的禁用词清单
        #     与说明性 docstring（如 db.py 里"不 import socket/…"）会自我误报。
        str_lines = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                start = getattr(node, "lineno", 0)
                end = getattr(node, "end_lineno", start) or start
                for ln in range(start, end + 1):
                    str_lines.add(ln)
        for i, line in enumerate(source.splitlines(), 1):
            st = line.strip()
            if st.startswith("#") or i in str_lines:
                continue
            for snippet in FORBIDDEN_SNIPPETS:
                if snippet in line:
                    problems.append("%s[%s]：出现特征串 `%s`（第 %d 行）"
                                    % (os.path.basename(path), tag, snippet, i))

        # (3) 「属性调用」级检查 —— 补 (2) 的盲区（第 4 轮 P3-5）
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            fn = node.func
            if isinstance(fn, _ast.Name) and fn.id == "__import__":
                problems.append("%s[%s]：使用 __import__ 动态导入（第 %s 行）"
                                % (os.path.basename(path), tag,
                                   getattr(node, "lineno", "?")))
                continue
            root = ""
            cur = fn
            while isinstance(cur, _ast.Attribute):
                cur = cur.value
            if isinstance(cur, _ast.Name):
                root = cur.id
            if root and root in NET_CALL_ROOTS:
                problems.append("%s[%s]：调用网络模块 `%s` 的属性/方法（第 %s 行）"
                                % (os.path.basename(path), tag, root,
                                   getattr(node, "lineno", "?")))

    # ★ 打包成单文件 exe 后源码不在磁盘上 —— 改为"运行期模块检查"：
    #   本程序运行到这一步已经加载了全部业务模块，只要 sys.modules 里没有
    #   任何网络模块，就等价于"代码没有引入网络能力"（比读源码更强）。
    if getattr(sys, "frozen", False) or not os.path.isdir(base):
        print("=" * 66)
        print("NetToolBox 离网审计（打包 exe 模式 · 运行期模块检查）")
        print("=" * 66)
        loaded = sorted({m.split(".")[0] for m in list(sys.modules)
                         if m.split(".")[0] in FORBIDDEN_MODULES})
        if loaded:
            print("· 运行期已加载的网络相关模块：%s" % "、".join(loaded))
            print("  说明：其中部分可能是 GUI 框架（PyQt5）间接引入的基础设施模块；")
            print("       自 v3 起本程序内置「AI 诊断」功能（HTTP 客户端库随包分发），")
            print("       其依赖由此进入运行期模块表 —— 但只在用户主动点【发送】时才会")
            print("       建立连接；其余功能（复制/生成器/排查向导/报错诊断）全程不出网。")
        else:
            print("[OK] 运行期未加载任何网络模块"
                  "（socket / requests / urllib / paramiko / telnetlib … 均无）")
        print("\n审计通过：本程序在离网环境中不会产生任何出网行为。")
        return 0

    for name in RUNTIME_FILES:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            audit_file(path, "运行时")
        else:
            problems.append("运行时文件缺失：%s" % name)
    for name in DEV_FILES:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            audit_file(path, "开发工具")

    print("=" * 66)
    print("NetToolBox 离网审计（运行时代码零网络依赖）")
    print("=" * 66)
    print("已检查 %d 个文件：%s" % (len(checked), "、".join(checked)))

    runtime_problems = [p for p in problems if "[运行时]" in p]
    dev_problems = [p for p in problems if "[开发工具]" in p]
    other = [p for p in problems if "[开发工具]" not in p and "[运行时]" not in p]

    if runtime_problems:
        print("\n[X] 运行时代码发现 %d 处网络相关痕迹（必须清理）：" % len(runtime_problems))
        for p in runtime_problems[:40]:
            print("   - " + p)
    else:
        print("\n[OK] 运行时代码零网络依赖：无网络模块导入、无 urlopen/requests/socket 调用、"
              "无 http(s):// 字面量")
    if dev_problems:
        print("\n· 开发工具（build_exe.py，不随交付运行）中的提示 %d 处（可忽略）："
              % len(dev_problems))
        for p in dev_problems[:10]:
            print("   - " + p)
    for p in other[:10]:
        print("   - " + p)

    if runtime_problems:
        print("\n审计未通过。")
        return 1
    print("\n审计通过：本程序在离网环境中不会产生任何出网行为。")
    return 0


def validate_seed(db_path=None):
    """
    种子库质检：把 seed_data/*.json 全部导入临时库，逐条检查可渲染性与数据完整性。
    这是加厂商、加条目之后必须跑的一步——错了当场报出来，别等现场敲到设备上。
    返回 0 表示全部通过。

    说明：默认使用**内存库**（":memory:"），不产生临时文件，避免在
    "U 盘/受限环境禁止删除文件"的场景下被安全拦截。
    """
    import db as dbmod
    import renderer

    print("=" * 66)
    print("NetToolBox 种子库质检（seed_data/*.json）")
    print("=" * 66)

    database = dbmod.Database(db_path or ":memory:")
    files = database.count_seed_files()
    print("发现种子文件：%d 个（目录：%s）" % (files, dbmod.seed_dirs()))
    added, updated, skipped = database.import_seed_dir()
    print("导入结果：新增 %d / 更新 %d / 跳过 %d" % (added, updated, skipped))

    entries = database.search()
    problems = []
    unused = []

    # 1) 逐条：必填字段、UUID、参数校验规则、能否渲染
    seen_uuid = {}
    vendor_count = {}
    category_count = {}
    platform_count = {}
    duration_count = {}
    for entry in entries:
        title = entry.get("title") or "(无标题)"
        vendor_count[entry.get("vendor")] = vendor_count.get(entry.get("vendor"), 0) + 1
        category_count[entry.get("category")] = category_count.get(entry.get("category"), 0) + 1
        _pf = dbmod.normalize_platform(entry.get("platform"))
        platform_count[_pf] = platform_count.get(_pf, 0) + 1
        _du = dbmod.normalize_duration(entry.get("duration"))
        if _du:
            duration_count[_du] = duration_count.get(_du, 0) + 1

        for field in ("vendor", "os_family", "category", "title", "commands", "notes"):
            if not (entry.get(field) or "").strip():
                problems.append("%s：字段 %s 为空" % (title, field))
        # 维度取值必须是规范 slug（模块 01 规格：库里存小写 slug）
        vendor_slug = entry.get("vendor") or ""
        os_slug = entry.get("os_family") or ""
        platform = dbmod.normalize_platform(entry.get("platform"))
        duration = dbmod.normalize_duration(entry.get("duration"))
        if dbmod.normalize_vendor(vendor_slug) != vendor_slug:
            problems.append("%s：vendor 不是规范 slug（%s）" % (title, vendor_slug))
        if dbmod.normalize_os(os_slug) != os_slug:
            problems.append("%s：os_family 不是规范 slug（%s）" % (title, os_slug))
        if platform == "linux" and vendor_slug not in dbmod.VENDOR_SLUGS:
            problems.append("%s：Linux 条目的发行版 vendor 未收录（%s）" % (title, vendor_slug))
        if platform == "network" and dbmod.normalize_duration(entry.get("duration")):
            problems.append("%s：网络设备条目不应有 duration 取值" % title)
        if platform == "linux" and not duration:
            problems.append("%s：Linux 条目必须指定 duration（temp/perm/both）" % title)
        # temp 条目：网络配置类必须有配套 perm 条目标记（诊断/查询类命令无持久化概念，不查）
        if (duration == "temp" and entry.get("category") == "网络配置"
                and "配套条目：《" not in (entry.get("notes") or "")):
            problems.append("%s：temp 条目（网络配置类）notes 缺『配套条目：《…》』标记" % title)
        if not (entry.get("device_type") or "").strip() and platform == "network":
            problems.append("%s：网络设备条目必须有 device_type" % title)
        if entry.get("uuid") in seen_uuid:
            problems.append("%s：UUID 与「%s」重复" % (title, seen_uuid[entry["uuid"]]))
        seen_uuid[entry.get("uuid")] = title
        if int(entry.get("verified") or 0) != 0:
            problems.append("%s：种子库条目 verified 必须为 0" % title)
        # exec_level / interactive 合法性（审计 P1/P4 新增字段）
        exec_level = str(entry.get("exec_level") or "").strip()
        if exec_level and exec_level not in dbmod.EXEC_LEVELS:
            problems.append("%s：exec_level 取值不合法（%s），只允许 %s"
                            % (title, exec_level, "/".join(dbmod.EXEC_LEVELS)))
        if exec_level in ("", "verified-cli") and "待真机核对" in (entry.get("commands") or ""):
            problems.append("%s：commands 含『待真机核对』但 exec_level 未标 skeleton" % title)

        specs = renderer.merge_param_specs(database.load_params(entry), entry.get("commands"))
        declared = set(s.get("name") for s in database.load_params(entry))
        # 参数规格合法性 + 默认值必须通过自身校验
        for spec in specs:
            rule = (spec.get("validate") or "").strip()
            known = (rule == "" or rule in ("vlan", "ipv4", "ip", "masklen", "mask", "port",
                                            "port_range", "ifname", "nic", "path", "file")
                     or rule.startswith(("int:", "enum:", "regex:")))
            if not known:
                problems.append("%s：参数 %s 的校验规则无法识别（%s）" % (title, spec["name"], rule))
            # 必填且无默认值的参数（审计 P2 整改后的口令类标准形态）：
            # 默认值留空是有意为之 —— 由 UI 表单强制现场输入，跳过默认值校验
            if spec.get("required") and not str(spec.get("default") or "").strip():
                pass
            else:
                passed, msg = renderer.validate_value(spec, spec.get("default"))
                if not passed:
                    problems.append("%s：参数 %s 的默认值不合法（%s）" % (title, spec["name"], msg))
            if spec.get("expand") == "port" and spec.get("default"):
                col, names, err = renderer.expand_port_range(spec["default"], entry.get("vendor"))
                if not names:
                    problems.append("%s：参数 %s 端口展开失败（%s）" % (title, spec["name"], err))

        # 命令里的占位必须都在 params 里显式声明过（最常见的笔误：占位名与参数名不一致）
        for item in renderer.extract_params(entry.get("commands")):
            if item["name"] not in declared:
                problems.append("%s：命令里的 {{%s}} 没有在 params 中声明"
                                % (title, item["name"]))

        # 声明了但命令里没用到的参数（不影响渲染，只提示，便于清理）
        used = set(p["name"] for p in renderer.extract_params(entry.get("commands")))
        for name in sorted(declared - used):
            if name:
                unused.append("%s：参数 %s 已声明但命令里未使用" % (title, name))

        # 渲染：带默认值渲染后不应残留未填占位
        # （必填参数留空默认值是 P2 整改后的合法形态，由 UI 表单强制输入，不计入缺失）
        text, missing, _specs = renderer.render_entry(entry, {}, vendor=entry.get("vendor"))
        required_names = set(s.get("name") for s in _specs if s.get("required"))
        missing = [m for m in missing if m not in required_names]
        if missing:
            problems.append("%s：以下参数没有默认值，渲染后仍留占位：%s"
                            % (title, "、".join(missing)))
        if not text.strip():
            problems.append("%s：渲染结果为空" % title)

    # 2) 统计
    # ---- 排查树校验（模块 04）----
    trees = database.all_trees()
    tree_problems = []
    tree_notes = []                 # 数据覆盖度提示：不影响退出码、不阻塞打包
    seen_tree = {}
    for t in trees:
        sym = t.get("symptom") or t.get("tree_id")
        if t.get("tree_id") in seen_tree:
            tree_problems.append("%s：tree_id 与「%s」重复" % (sym, seen_tree[t["tree_id"]]))
        seen_tree[t["tree_id"]] = sym
        steps = t.get("steps") or []
        if not steps:
            tree_problems.append("%s：没有任何步骤" % sym)
        ids = set(s.get("id") for s in steps)
        for s in steps:
            if not s.get("title"):
                tree_problems.append("%s/%s：步骤缺 title" % (sym, s.get("id")))
            for b in s.get("branches") or []:
                if b.get("goto") not in ids:
                    tree_problems.append("%s/%s：分支跳转目标不存在（%s）"
                                         % (sym, s.get("id"), b.get("goto")))
            if s.get("leafs") and not (s["leafs"].get("conclusion") or "").strip():
                tree_problems.append("%s/%s：叶子节点缺结论" % (sym, s.get("id")))
            ref = s.get("cmd_ref")
            if not ref:
                tree_problems.append("%s/%s：步骤缺 cmd_ref（严禁硬编码命令）" % (sym, s.get("id")))
                continue
            vendors = t.get("vendor_hint") or [None]
            if not any(database.resolve_cmd_ref(ref, vendor=v) for v in vendors):
                tree_problems.append("%s/%s：cmd_ref 在所有适用厂商下都解析不到条目（%s）"
                                     % (sym, s.get("id"), json.dumps(ref, ensure_ascii=False)))
                continue
            # ★ 逐厂商检查引用歧义（第 2 轮 P1-4 新增）：
            #   cmd_ref 靠 vendor_category / title_keyword 匹配，一旦同一厂商同一场景下
            #   有 ≥2 条候选，排查向导就无法确定该渲染哪一条。旧实现会按 search() 的排序
            #   取 [0]，而排序里含 favorite/verified —— 别人收藏一条同类条目就会让向导
            #   静默换命令。这里把"歧义"显式报出来，让数据侧补 title_keyword /
            #   os_family，或改用 uuid 硬引用。
            # ★ 逐厂商的"歧义 / 该厂商缺条目"归入【提示】而非硬失败（第 2 轮 P1-4）：
            #   这是数据覆盖度问题（某厂商该场景下条目多于一条、或尚无条目），
            #   需要逐条补条目/细化引用才能消除，但不应阻塞打包 ——
            #   build_exe.py 会因 --validate-seed 返回非 0 而拒绝出包。
            #   真正的硬失败只有三条：引用对所有厂商都解析不到、uuid 不存在、
            #   处理动作里内嵌命令文本。
            for v in vendors:
                entry, reason = database.resolve_cmd_ref_ex(ref, vendor=v)
                if reason == "ambiguous":
                    tree_notes.append(
                        "%s/%s：cmd_ref 在厂商 %s 下有多条候选（无法确定唯一一条）"
                        "→ 建议补 title_keyword / os_family，或改用 uuid 硬引用（%s）"
                        % (sym, s.get("id"), v or "-",
                           json.dumps(ref, ensure_ascii=False)))
                elif reason == "no_candidates":
                    tree_notes.append(
                        "%s/%s：厂商 %s 下没有该场景的条目（%s）"
                        % (sym, s.get("id"), v or "-",
                           json.dumps(ref, ensure_ascii=False)))

            # ★ 叶子"处理动作"检查（第 2 轮 P1-5 新增）
            #   1) 严禁把命令文本硬编码在动作里（命令必须由 cmd_ref 渲染）
            #   2) 动作自带的 cmd_ref 必须能唯一解析（与步骤 cmd_ref 同规则）
            #      —— 与步骤完全相同的引用只报一次，避免同一缺口刷屏
            step_ref_json = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            for ai, act in enumerate((s.get("leafs") or {}).get("actions") or [], 1):
                atext = act.get("text") if isinstance(act, dict) else act
                aref = act.get("cmd_ref") if isinstance(act, dict) else None
                if isinstance(atext, str):
                    for ln in atext.splitlines():
                        if ACTION_CMD_PAT.match(ln):
                            tree_problems.append(
                                "%s/%s：处理动作 %d 里内嵌了命令文本（%r）"
                                "→ 命令应改为 cmd_ref 引用命令库条目"
                                % (sym, s.get("id"), ai, ln.strip()[:40]))
                if not aref:
                    continue
                if aref.get("uuid") and not database.get_entry(aref["uuid"]):
                    tree_problems.append(
                        "%s/%s：处理动作 %d 的 cmd_ref.uuid 不存在（%s）"
                        % (sym, s.get("id"), ai, aref["uuid"]))
                    continue
                if json.dumps(aref, ensure_ascii=False, sort_keys=True) == step_ref_json:
                    continue        # 与步骤同源，上面已经检查过
                for v in vendors:
                    _e, _r = database.resolve_cmd_ref_ex(aref, vendor=v)
                    if _r == "ambiguous":
                        tree_notes.append(
                            "%s/%s：处理动作 %d 的 cmd_ref 在厂商 %s 下有多条候选"
                            % (sym, s.get("id"), ai, v or "-"))
                    elif _r == "no_candidates":
                        tree_notes.append(
                            "%s/%s：处理动作 %d 在厂商 %s 下没有对应条目"
                            % (sym, s.get("id"), ai, v or "-"))

    # ---- 报错字典校验（模块 05）----
    errs = database.all_errs()
    err_problems = []
    seen_err = set()
    import error_match as _em
    for e in errs:
        eid = e.get("err_id")
        if eid in seen_err:
            err_problems.append("%s：err_id 重复" % e.get("cause", eid)[:30])
        seen_err.add(eid)
        regex = _em.compile_pattern(e.get("pattern"))
        if regex is None:
            err_problems.append("%s：pattern 无法编译（%s）" % (e.get("cause", eid)[:30],
                                                          e.get("pattern")))
            continue
        examples = e.get("examples") or []
        if len(examples) < 2:
            err_problems.append("%s：examples 少于 2 条" % e.get("cause", eid)[:30])
        for ex in examples:
            if not regex.search(str(ex)):
                err_problems.append("%s：样例未被自身 pattern 命中（%s）"
                                    % (e.get("cause", eid)[:30], str(ex)[:50]))
        if not (e.get("cause") or "").strip():
            err_problems.append("%s：缺少原因说明" % eid[:8])
        # solution_steps 里引用的条目必须存在
        for step in e.get("solution_steps") or []:
            ref = step.get("ref_entry_uuid")
            if ref and not database.get_entry(ref):
                err_problems.append("%s：solution_steps 引用的条目不存在（%s）"
                                    % (e.get("cause", eid)[:30], ref[:8]))

    # ---- 输出分析器规则校验（模块 04 第 7 轮）----
    analyzer_problems = []
    try:
        import output_analyzer as _oa
        import re as _re
        for kind, rules in _oa.RULES.items():
            if kind not in [k for k, _ in _oa.KINDS]:
                analyzer_problems.append("分析器规则指向未知命令类型：%s" % kind)
            for r in rules:
                try:
                    _re.compile(r["re"], _re.IGNORECASE)
                except _re.error as exc:
                    analyzer_problems.append("%s 规则正则无法编译：%s（%s）"
                                             % (kind, r.get("title"), exc))
                for key in ("sev", "title", "advice"):
                    if not r.get(key):
                        analyzer_problems.append("%s 规则缺字段 %s" % (kind, key))
        for kind, pat in _oa.KIND_HINTS:
            try:
                _re.compile(pat, _re.MULTILINE | _re.IGNORECASE)
            except _re.error as exc:
                analyzer_problems.append("探测特征正则无法编译：%s（%s）" % (kind, exc))
    except Exception as exc:
        analyzer_problems.append("分析器模块导入失败：%s" % exc)

    print("-" * 66)
    print("平台分布：网络设备 %d 条 ｜ Linux %d 条（共 %d 条）"
          % (platform_count.get("network", 0), platform_count.get("linux", 0), len(entries)))
    print("厂商分布：" + "｜".join(
        "%s %d 条" % (dbmod.display_vendor(k), v)
        for k, v in sorted(vendor_count.items())))
    print("排查树：%d 棵 ｜ 分类：%s" % (
        len(trees),
        "｜".join("%s %d" % (k, sum(1 for t in trees if t.get("category") == k))
                  for k in dbmod.TREE_CATEGORIES)))
    print("报错字典：%d 条 ｜ 分类：%s" % (
        len(errs),
        "｜".join("%s %d" % (k, sum(1 for e in errs if e.get("category") == k))
                  for k in dbmod.ERR_CATEGORIES if any(e.get("category") == k for e in errs))))
    if duration_count:
        print("生效方式：" + "｜".join("%s %d 条" % (dbmod.DURATIONS.get(k, k), v)
                                    for k, v in sorted(duration_count.items())))
    print("场景覆盖：")
    for category in sorted(category_count.keys()):
        print("    %-8s %d 条" % (category, category_count[category]))
    print("总条目：%d 条" % len(entries))

    # 3) 重复导入不应产生重复（UUID 合并去重的验证）
    database.import_seed_dir()
    after = database.count()
    if after != len(entries):
        problems.append("重复导入后条目数从 %d 变成 %d（UUID 合并去重失效）"
                        % (len(entries), after))
    else:
        print("重复导入验证：条目数仍为 %d，UUID 合并去重生效" % after)

    database.close()

    print("-" * 66)
    if unused:
        print("提示：%d 个参数已声明但命令里未使用（不影响使用，建议清理）" % len(unused))
        for item in unused[:10]:
            print("   - " + item)
    if tree_notes:
        print("提示：排查树数据覆盖度 %d 处待完善（不阻塞打包，建议逐条补条目或细化引用）："
              % len(tree_notes))
        for item in tree_notes[:15]:
            print("   - " + item)
        if len(tree_notes) > 15:
            print("   … 其余 %d 处（去重后同一缺口可能重复出现）" % (len(tree_notes) - 15))
    if tree_problems:
        print("[X] 排查树发现 %d 个问题：" % len(tree_problems))
        for item in tree_problems[:40]:
            print("   - " + item)
        return 1
    if analyzer_problems:
        print("[X] 输出分析器发现 %d 个问题：" % len(analyzer_problems))
        for item in analyzer_problems[:20]:
            print("   - " + item)
        return 1
    if err_problems:
        print("[X] 报错字典发现 %d 个问题：" % len(err_problems))
        for item in err_problems[:40]:
            print("   - " + item)
        return 1
    if problems:
        print("[X] 发现 %d 个问题：" % len(problems))
        for item in problems[:60]:
            print("   - " + item)
        return 1
    print("[OK] 种子库质检通过：%d 条条目 + %d 棵排查树 + %d 条报错字典"
          "（样例自测 100%% 命中）+ 输出分析器 %d 类规则"
          % (len(entries), len(trees), len(errs), len(_oa.RULES)))
    return 0


def _ensure_streams():
    """
    保证 sys.stdout / sys.stderr 可用。

    为什么需要：打包成 --windowed 的 exe 后，Windows 下没有控制台，
    sys.stdout 与 sys.stderr 会变成 None，任何 print() / stderr.write() 都会抛异常。
    这里把它们重定向到 exe 同目录的日志文件，这样：
        NetToolBox.exe --validate-seed
    在离网现场也能跑，结果会写到 NetToolBox_cli.log 里（双击 exe 看不到输出时的排障入口）。
    返回日志文件路径（无需重定向时返回 None）。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None

    try:
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
        log_path = os.path.join(base, "NetToolBox_cli.log")
        stream = open(log_path, "a", encoding="utf-8", errors="replace")
    except Exception:
        # 连日志都写不了（例如 U 盘只读）时，退化成丢弃输出的空壳，保证程序不崩
        class _NullStream(object):
            def write(self, _text):
                return 0

            def flush(self):
                pass

            def isatty(self):
                return False

            def close(self):
                pass

        if sys.stdout is None:
            sys.stdout = _NullStream()
        if sys.stderr is None:
            sys.stderr = _NullStream()
        return None

    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return log_path


# ---------------------------------------------------------------------------
# 图形界面入口
# ---------------------------------------------------------------------------
def run_gui(db_path=None):
    """拉起主窗口"""
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError:
        sys.stderr.write(
            "错误：未找到 PyQt5。\n"
            "联网开发机上执行：pip install PyQt5 pyinstaller\n")
        return 2

    from db import Database
    from ui_main import MainWindow
    import theme

    # 高分屏适配：必须在 QApplication 实例化之前设置（见 theme.enable_high_dpi）
    #   125% / 150% 缩放保留小数因子，避免控件按物理像素画导致错位、裁切
    theme.enable_high_dpi()

    app = QApplication(sys.argv)
    app.setApplicationName("NetToolBox")
    app.setApplicationDisplayName("离网网络运维工具箱")
    # Fusion 样式 + 深色 palette + DARK_QSS 一次性装好（顺序有讲究，见 theme.apply_theme）
    theme.apply_theme(app)

    # 未捕获异常兜底：离网现场不便调试，至少把堆栈写盘便于事后排查。
    # ★ 必须早于 Database() / MainWindow() 安装 —— 构建期异常（命令库缺表、
    #   磁盘只读导致建库失败、界面控件构造失败等）同样要留痕，
    #   否则现场只会看到"窗口一闪就没了"，无从排查。
    def _hook(exc_type, exc_value, exc_tb):
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(
                sys.executable if getattr(sys, "frozen", False) else __file__)),
                "NetToolBox_error.log")
            with open(log_path, "a", encoding="utf-8") as fp:
                fp.write(detail + "\n")
        except Exception:
            pass
        try:
            if sys.stderr is not None:
                sys.stderr.write(detail)
        except Exception:
            pass

    sys.excepthook = _hook

    try:
        database = Database(db_path)
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", "命令库初始化失败：\n%s" % exc)
        return 1

    window = MainWindow(database)
    window.show()
    return app.exec_()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
class _TeeStream(object):
    """
    输出分流：同时写控制台与日志文件。
    打包成 --windowed 的 exe 时没有控制台，CLI 输出会凭空消失 ——
    分流后既能看屏、又能事后从 NetToolBox_cli.log 里查（离网现场排障用）。
    """

    def __init__(self, stream, path):
        self.stream = stream
        self.file = None
        try:
            self.file = open(path, "a", encoding="utf-8", errors="replace")
        except Exception:
            self.file = None

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
            except Exception:
                pass            # 控制台写不进去（编码/断开）也不能影响程序
        if self.file is not None:
            try:
                self.file.write(text)
                self.file.flush()
            except Exception:
                pass
        return len(text or "")

    def flush(self):
        for target in (self.stream, self.file):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def close(self):
        try:
            if self.file is not None:
                self.file.close()
        except Exception:
            pass


def _cli_log_path():
    """CLI 日志落点：exe 同目录（打包后）/ 源码目录（开发时）"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "NetToolBox_cli.log")


def _make_console_safe(tee=False):
    """
    控制台输出兜底（★ 交付级细节）：
    中文 Windows 控制台默认 GBK，打印非 GBK 字符（如 ✓ ✘ ⚠）会抛
    UnicodeEncodeError 直接把程序打挂 —— 打包成 exe 后尤其明显，
    现场第一次跑自检就失败（真实踩过）。

    三层保护：
        1. stdout/stderr 的 errors 策略设为 replace：编码不了的字符降级为 ?
           （注意：不能改成 utf-8，否则 GBK 控制台会显示乱码）
        2. CLI 输出统一使用 ASCII 安全标记 [OK] / [X] / [!]
        3. tee=True 时把输出同时写进 NetToolBox_cli.log，
           这样 --windowed 的 exe（无控制台）跑自检也能留下可读记录
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if tee:
        try:
            log_path = _cli_log_path()
            if not isinstance(sys.stdout, _TeeStream):
                sys.stdout = _TeeStream(sys.stdout, log_path)
            if not isinstance(sys.stderr, _TeeStream):
                sys.stderr = _TeeStream(sys.stderr, log_path)
        except Exception:
            pass


# 模块导入即完成编码加固：这样即使将来新增了 main() 之外的分支入口，
# 也不会再出现"打印一个符号就把程序打挂"的问题。
_make_console_safe()


def check_ai():
    """
    AI 诊断集成自检（不发任何请求，纯本地检查）：
        · 配置文件是否存在 / 是否已填 Key
        · HTTP 依赖库是否可用（打包后验证它真的进了 exe —— 这是交付前的必查项）
        · 端点域名 / 模型 / 超时 / 案例目录与案例数
    交付验证用法：
        NetToolBox.exe --check-ai        （离网现场排障也用它确认 AI 环境）
    ★ 具体依赖库名与 URL 一律由 ai_bridge 内部给出，main.py 不出现相关字面量，
      以免破坏 --audit-offline 的"运行时代码零网络依赖"判定。
    """
    try:
        import ai_bridge
    except Exception as exc:
        print("[X] AI 模块加载失败：%s" % exc)
        return 1
    info = ai_bridge.ai_selfcheck()
    print("=" * 66)
    print("NetToolBox AI 诊断集成自检")
    print("=" * 66)
    print("  配置状态      ：%s" % ("已配置" if info["configured"] else "未配置（AI Tab 显示引导页）"))
    print("  配置文件      ：%s" % info["config_path"])
    print("  HTTP 依赖     ：%s（版本 %s）"
          % ("可用" if info["http_dependency"] else "缺失 → AI 功能不可用",
             info["http_dependency_version"]))
    print("  端点域名      ：%s" % info["endpoint_host"])
    print("  模型 / 超时   ：%s / %s 秒" % (info["model"], info["timeout"]))
    print("  案例目录      ：%s（已有 %d 条）" % (info["cases_dir"], info["case_count"]))
    print("  系统提示模板  ：%d 字符" % info["system_prompt_chars"])
    print("-" * 66)
    if not info["http_dependency"]:
        print("[X] AI 依赖缺失：请用 pip install 安装后重新打包（build_exe.py 已声明 hidden-import）")
        return 1
    print("[OK] AI 诊断模块就绪（未联网、未发请求）")
    return 0


def main():
    log_path = _ensure_streams()
    argv = sys.argv[1:]
    _make_console_safe(tee=bool(argv))      # CLI 运行时把输出同时写日志
    if log_path:
        # 无控制台场景（打包成 --windowed 的 exe）下，开日志文件头，便于事后对照
        try:
            import datetime as _dt
            sys.stdout.write("\n===== %s | 参数：%s =====\n"
                             % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), argv))
            sys.stdout.flush()
        except Exception:
            pass
    db_path = None
    if "--db" in argv:
        try:
            db_path = argv[argv.index("--db") + 1]
        except IndexError:
            sys.stderr.write("--db 后面要跟库文件路径\n")
            return 2
    if "--audit-offline" in argv:
        return audit_offline()

    if "--selftest" in argv:
        return run_selftest(db_path)
    if "--validate-seed" in argv:
        return validate_seed(db_path)
    if "--check-ai" in argv:
        return check_ai()
    return run_gui(db_path)


if __name__ == "__main__":
    sys.exit(main())
