# -*- coding: utf-8 -*-
"""
db.py —— 离网网络设备命令库 · SQLite 数据访问层

职责：
    1. 建库建表（entries / history）、建索引
    2. 全文搜索（LIKE 兜底，不用 FTS5，规避中文分词问题）+ 多维过滤
    3. 条目增删改查、收藏切换、标记已验证
    4. 修改历史记录（history 表）
    5. .nlb 文件导入导出（JSON 格式），按 UUID 合并去重

硬性约束：
    - 不 import 任何网络相关模块（requests/urllib/socket/paramiko/telnetlib...）
    - db 文件固定放在 exe 同目录，U 盘整盘拷贝即迁移
"""

import os
import re
import sys
import json
import uuid
import sqlite3
import datetime

import dedupe

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DB_FILENAME = "command_lib.db"      # 库文件名（永远在 exe 同目录）
SCHEMA_VERSION = 2                  # 表结构版本号（v2：新增 platform/duration、history 泛化）
NLB_FORMAT = "netcmdl - command library"  # .nlb 文件标识（JSON 内 format 字段）

# entries 表全部字段（顺序与建表语句一致）
# v2 新增：platform(network/linux)、duration(temp/perm/both)
# v2.1 新增：exec_level(命令可用性标记)、interactive(交互式命令标记) —— 审计 P1/P4 整改
ENTRY_FIELDS = [
    "uuid", "platform", "device_type", "vendor", "os_family", "models",
    "category", "title", "description", "commands", "params",
    "notes", "duration", "verified", "verified_by", "verified_model", "verified_date",
    "favorite", "created_at", "updated_at",
    "exec_level", "interactive",
]

# 允许被编辑/导入覆盖的字段（uuid/created_at 不可由导入方随意改动）
EDITABLE_FIELDS = [
    "platform", "device_type", "vendor", "os_family", "models",
    "category", "title", "description", "commands", "params",
    "notes", "duration", "verified", "verified_by", "verified_model", "verified_date",
    "favorite", "updated_at",
    "exec_level", "interactive",
]

# 验证状态四字段：只经真机回填流程（mark_verified）产生，导入一律不采纳
# （P1-2 裁决 2026-10-08，见 docs/seed-guide.md 与 docs/audit/20261008-full-audit.md）
VERIFY_FIELDS = ("verified", "verified_by", "verified_model", "verified_date")

# exec_level 合法取值（审计 P1：区分"真机验证过的 CLI / 待核对的 CLI 骨架 / 仅 Web 路径"）
EXEC_LEVELS = ("", "verified-cli", "skeleton", "web-only")
EXEC_LEVEL_LABELS = {
    "": "",
    "verified-cli": "已核对 CLI",
    "skeleton": "骨架（待真机核对）",
    "web-only": "仅 Web 路径",
}

# 排查树 vendor_hint 标准分组（审计任务5：网络组扩入 zte/juniper，防火墙组先建好待挂靠）
TREE_HINT_GROUPS = {
    "network": ["cisco", "huawei", "h3c", "ruijie", "zte", "juniper"],
    "linux": ["centos", "ubuntu", "kylin", "openeuler"],
    "firewall": ["fortinet", "paloalto", "sangfor", "topsec"],
}

# ---------------------------------------------------------------------------
# 维度取值规范（模块 01 规格：库里存小写 slug，界面显示中文/品牌名）
# ---------------------------------------------------------------------------

# 平台：顶层导航分支
PLATFORMS = {"network": "网络设备", "linux": "Linux 服务器"}
DURATIONS = {"": "", "temp": "临时生效（重启失效）",
             "perm": "持久化配置", "both": "临时 + 持久化"}

# vendor slug → 显示名（网络设备厂商）
VENDOR_SLUGS = {
    "cisco": "Cisco",
    "huawei": "华为",
    "h3c": "H3C",
    "ruijie": "锐捷",
    "fortinet": "Fortinet",
    "juniper": "Juniper",
    "paloalto": "Palo Alto",
    "sangfor": "深信服AF",
    "topsec": "天融信",
    "zte": "中兴",
    # 跨发行版 Linux 运维工具（transfer_tools 等，platform=linux 下的通用 vendor）
    "ops": "Linux 通用",
    # 以下为占位厂商（只建导航目录，条目空）
    "arista": "Arista",
    "checkpoint": "Check Point",
    "hillstone": "山石网科",
    "qianxin": "奇安信",
    "venustech": "启明星辰",
    "nsfocus": "绿盟",
    "maipu": "迈普",
    "digitalchina": "神州数码",
    "anheng": "安恒信息",
    # Linux 发行版（device_type=linux 时使用）
    "centos": "CentOS",
    "rhel": "RHEL",
    "ubuntu": "Ubuntu",
    "debian": "Debian",
    "openeuler": "openEuler",
    "kylin": "麒麟",
    "ulos": "统信 UOS",
}

# os_family slug → 显示名（网络设备 OS / Linux 大版本）
OS_SLUGS = {
    "ios": "Cisco IOS",
    "iosxe": "Cisco IOS-XE",
    "nxos": "Cisco NX-OS",
    "vrp5": "华为 VRP5",
    "vrp8": "华为 VRP8",
    "comware5": "H3C Comware5",
    "comware7": "H3C Comware7",
    "rgos": "锐捷 RGOS",
    "fortios": "FortiOS",
    "junos": "Junos",
    "sangfor_af": "深信服 AF",
    "topos": "天融信 TopOS",
    "panos": "PAN-OS",
    "zxr10": "中兴 ZXR10",
    "eos": "Arista EOS",
    "gaia": "Check Point Gaia",
    "stoneos": "山石 StoneOS",
    # Linux 大版本
    "centos7": "CentOS 7",
    "centos8": "CentOS 8",
    "rhel7": "RHEL 7",
    "rhel8": "RHEL 8",
    "ubuntu1804": "Ubuntu 18.04",
    "ubuntu2004": "Ubuntu 20.04",
    "ubuntu2204": "Ubuntu 22.04",
    "debian11": "Debian 11",
    "openeuler2203": "openEuler 22.03",
    "kylinV10": "麒麟 V10",
    "ulos20": "统信 UOS 20",
}

# 反向索引：显示名 / 各种历史写法 → slug（做迁移与兼容读用）
_VENDOR_ALIASES = {
    "思科": "cisco", "华为": "huawei", "新华三": "h3c", "fortinet": "fortinet",
    "飞塔": "fortinet", "瞻博": "juniper", "paloalto": "paloalto",
    "palo alto": "paloalto", "深信服af": "sangfor", "深信服": "sangfor",
    "天融信": "topsec", "中兴": "zte", "锐捷": "ruijie",
    "山石网科": "hillstone", "山石": "hillstone", "启明星辰": "venustech",
    "绿盟": "nsfocus", "安恒信息": "anheng", "神州数码": "digitalchina",
    "迈普": "maipu", "统信 uos": "ulos", "uos": "ulos", "麒麟": "kylin",
}
_OS_ALIASES = {
    "cisco ios": "ios", "ios-xe": "iosxe", "iosxe": "iosxe", "nx-os": "nxos",
    "华为 vrp5": "vrp5", "华为 vrp8": "vrp8", "vrp": "vrp5",
    "h3c comware7": "comware7", "comware7": "comware7",
    "h3c comware5": "comware5", "comware5": "comware5",
    "锐捷 rgos": "rgos", "fortios": "fortios", "junos": "junos",
    "深信服 af": "sangfor_af", "天融信 topos": "topos", "pan-os": "panos",
    "中兴 zxr10": "zxr10", "arista eos": "eos",
    "centos 7": "centos7", "centos 8": "centos8",
    "rhel 7": "rhel7", "rhel 8": "rhel8",
    "ubuntu 18.04": "ubuntu1804", "ubuntu 20.04": "ubuntu2004",
    "ubuntu 22.04": "ubuntu2204", "debian 11": "debian11",
    "openeuler 22.03": "openeuler2203", "麒麟 v10": "kylinV10",
    "kylinv10": "kylinV10",          # 小写变体（normalize 会先 lower）
    "统信 uos 20": "ulos20",
}


def normalize_vendor(value):
    """
    vendor 归一化为小写 slug。
    兼容读：老库里存的是显示名（"Cisco"/"华为"），新库里存 slug（cisco/huawei）。
    认不出来时返回原值的 slug 化形式（小写去空格），保证不丢数据。
    """
    text = (value or "").strip()
    if not text:
        return ""
    key = text.lower()
    if key in VENDOR_SLUGS:
        return key
    if key in _VENDOR_ALIASES:
        return _VENDOR_ALIASES[key]
    return key.replace(" ", "")


def normalize_os(value):
    """os_family 归一化为小写 slug（兼容老的显示名写法）"""
    text = (value or "").strip()
    if not text:
        return ""
    key = text.lower()
    if key in OS_SLUGS:
        return key
    if key in _OS_ALIASES:
        return _OS_ALIASES[key]
    return key.replace(" ", "")


def normalize_platform(value):
    """platform 归一化：只认 network / linux，其它一律按 network 处理"""
    key = (value or "").strip().lower()
    return "linux" if key == "linux" else "network"


def normalize_duration(value):
    """duration 归一化：只认 temp / perm / both，其余为空"""
    key = (value or "").strip().lower()
    return key if key in ("temp", "perm", "both") else ""


def display_vendor(slug):
    """slug → 界面显示名（认不出来就原样显示，兼容团队自加厂商）"""
    return VENDOR_SLUGS.get(normalize_vendor(slug), slug or "未指定厂商")


def display_os(slug):
    """os_family slug → 界面显示名"""
    return OS_SLUGS.get(normalize_os(slug), slug or "未指定OS")


def display_platform(slug):
    """platform slug → 中文显示名"""
    return PLATFORMS.get(normalize_platform(slug), "网络设备")


def vendor_candidates():
    """厂商下拉候选（显示名列表，按显示名排序）"""
    return sorted(VENDOR_SLUGS.values())


def os_candidates():
    """OS 版本下拉候选（显示名列表，网络设备在前、Linux 在后）"""
    net = [v for k, v in OS_SLUGS.items() if not _is_linux_os(k)]
    linux = [v for k, v in OS_SLUGS.items() if _is_linux_os(k)]
    return net + linux


def _is_linux_os(slug):
    """判断某个 os_family slug 是否属于 Linux 发行版版本"""
    return any((slug or "").startswith(p) for p in
               ("centos", "rhel", "ubuntu", "debian", "openeuler", "kylinV", "ulos"))


# 占位厂商：只建导航目录、暂无条目（选中时界面提示"该厂商模板待补充，可在编辑器中添加"）
PLACEHOLDER_VENDORS = [
    "arista", "checkpoint", "hillstone", "qianxin",
    "venustech", "nsfocus", "maipu", "digitalchina", "anheng",
]

# 场景分类全集（模块 01 规格的过滤链，共 15 类）
# 报错字典（模块 05）
# exec_level：报错条目的可用性标记（批2B：骨架报错=构造样例待真机核对）
ERR_FIELDS = ["err_id", "vendor", "os_family", "pattern", "category", "cause",
              "solution_steps", "fix_template", "examples", "verified",
              "verified_by", "verified_date", "hit_count", "created_at", "updated_at",
              "exec_level"]
ERR_CATEGORIES = ["语法", "模式", "依赖", "资源冲突", "管理认证", "保存系统",
                  "权限", "服务", "磁盘", "本地源"]

# 排查树（模块 04）
TREE_FIELDS = ["tree_id", "symptom", "vendor_hint", "category", "steps",
               "verified", "verified_by", "verified_date", "created_at", "updated_at"]
# v2.2（批3 M0）：增补 策略诊断（批2B 防火墙树）/ 地址分配（DHCP 树）——否则排查向导现象列表不渲染对应分组
TREE_CATEGORIES = ["连通性", "端口", "性能", "路由协议", "管理面", "策略诊断", "地址分配"]

ALL_CATEGORIES = [
    "VLAN", "端口", "Trunk", "静态路由", "OSPF", "BGP", "ACL", "NAT",
    "SSH管理", "密码", "SNMP", "日志", "HA", "接口诊断", "保存配置",
    # Linux 侧常用分类（模块 03）
    "网络配置", "服务管理", "防火墙", "排查命令", "磁盘", "系统资源", "本地源",
]


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------

def now_str():
    """当前时间字符串（秒级精度），入库统一格式"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_uuid():
    """生成条目 UUID"""
    return str(uuid.uuid4())


def cmp_value(value):
    """
    把字段值归一化成可比较的字符串。
    ★ 必须这么做的原因：trouble_trees.steps / err_dict.solution_steps 等字段，
      库里存 JSON 字符串（读出来是 list），而待导入数据也是 JSON 字符串 ——
      直接 str() 比较会永远判为"有变化"，导致重复导入每次都刷一遍全库。
    """
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value or "")
    if text[:1] in ("[", "{"):
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
        except Exception:
            return text
    return text


def load_list(raw):
    """JSON 数组解析（trouble_trees 的 steps / vendor_hint 用），解析失败返回 []"""
    try:
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_base_dir():
    """
    取"程序所在目录"：
        - 打包成 exe 后：exe 所在目录（U 盘根目录或任意位置）
        - 开发调试时：db.py 所在目录
    db 与 seed_data 都以此为基准（seed_data 实际由 resource_path 处理）
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_db_path():
    """库文件完整路径：exe 同目录 / command_lib.db"""
    return os.path.join(get_base_dir(), DB_FILENAME)


def resource_path(*parts):
    """
    取随程序打包的内置资源路径（seed_data/*.json）。
        - PyInstaller onefile：资源被解到 sys._MEIPASS
        - 开发调试：源码目录
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def seed_dirs():
    """
    种子库候选目录（按优先级去重）：
        1. exe/源码同目录下的 seed_data —— 现场可以直接往 U 盘里补条目，不用重新打包
        2. 打包进 exe 的内置 seed_data —— 首次释放
    """
    result = []
    for path in (os.path.join(get_base_dir(), "seed_data"), resource_path("seed_data")):
        if path and os.path.isdir(path) and path not in result:
            result.append(path)
    return result


# ---------------------------------------------------------------------------
# 第 1 轮硬编码假数据（第 3 轮由 seed_data/*.json 正式种子库取代）
# 所有条目 verified 一律为 0 —— AI 生成的命令未经真机验证，必须走团队验证流程
# ---------------------------------------------------------------------------

DEMO_ENTRIES = [
    {
        "uuid": "11111111-1111-4111-8111-111111111111",
        "device_type": "交换机",
        "vendor": "Cisco",
        "os_family": "Cisco IOS",
        "models": "Catalyst 2960 / 3560 / 3750",
        "category": "VLAN",
        "title": "创建 VLAN 并把端口划入 Access",
        "description": "新建业务 VLAN，命名，并把指定物理端口批量划为 access 口加入该 VLAN。",
        "commands": (
            "! ===== 创建 VLAN 并划入端口（Cisco IOS） =====\n"
            "enable\n"
            "configure terminal\n"
            "vlan {{vlan_id}}\n"
            " name {{vlan_name}}\n"
            " exit\n"
            "interface range {{port_range}}\n"
            " switchport mode access\n"
            " switchport access vlan {{vlan_id}}\n"
            " no shutdown\n"
            " exit\n"
            "end\n"
            "write memory\n"
        ),
        "params": [
            {"name": "vlan_id", "label": "VLAN ID", "default": "10", "required": True,
             "validate": "vlan", "example": "10"},
            {"name": "vlan_name", "label": "VLAN 名称", "default": "OFFICE", "required": False,
             "validate": "", "example": "OFFICE"},
            {"name": "port_range", "label": "端口范围", "default": "0/1-0/10", "required": True,
             "validate": "port_range", "expand": "port", "example": "0/1-0/10"},
        ],
        "notes": (
            "1) interface range 语法在 IOS 12.2(55)SE 之后才支持，老版本要逐口敲。\n"
            "2) 华为/H3C 无 interface range，需分别进接口或用 port-group。\n"
            "3) write memory 等价于 copy running-config startup-config，别只 end 不保存。"
        ),
        "verified": 0,
        "verified_by": "", "verified_model": "", "verified_date": "",
        "favorite": 1,
    },
    {
        "uuid": "22222222-2222-4222-8222-222222222222",
        "device_type": "交换机",
        "vendor": "华为",
        "os_family": "华为 VRP5",
        "models": "S5700 / S5720 / S6720",
        "category": "Trunk",
        "title": "配置 Trunk 口并放通指定 VLAN",
        "description": "把上行端口配成 trunk，放通允许通过的 VLAN 列表，PVID 设为管理 VLAN。",
        "commands": (
            "# ===== 配置 Trunk 口（华为 VRP5 交换机） =====\n"
            "system-view\n"
            "interface {{port}}\n"
            " port link-type trunk\n"
            " port trunk allow-pass vlan {{vlan_list}}\n"
            " port trunk pvid vlan {{pvid}}\n"
            " undo shutdown\n"
            " quit\n"
            "save\n"
            "y\n"
        ),
        "params": [
            {"name": "port", "label": "端口", "default": "0/0/1", "required": True,
             "validate": "port_range", "expand": "port", "example": "0/0/1 或 0/0/1-0/0/10"},
            {"name": "vlan_list", "label": "放通 VLAN", "default": "10 20 30",
             "required": True, "validate": "", "example": "10 20 30 或 10 to 20"},
            {"name": "pvid", "label": "PVID", "default": "1", "required": False,
             "validate": "vlan", "example": "1"},
        ],
        "notes": (
            "1) VRP 的 save 会二次确认 [Y/N]，脚本里要跟一行 y，否则粘贴后会卡住。\n"
            "2) 华为交换机端口命名是 槽位/子槽位/端口（0/0/1），别按思科的 0/1 敲。\n"
            "3) allow-pass 用 'vlan 10 to 20' 是连续段，'vlan 10 20 30' 是不连续列表，别混。"
        ),
        "verified": 0,
        "verified_by": "", "verified_model": "", "verified_date": "",
        "favorite": 0,
    },
    {
        "uuid": "33333333-3333-4333-8333-333333333333",
        "device_type": "路由器",
        "vendor": "H3C",
        "os_family": "H3C Comware7",
        "models": "MSR830 / MSR2600 / MSR3600",
        "category": "静态路由",
        "title": "配置静态路由与下一跳",
        "description": "新增一条静态路由，指定目的网段、掩码与下一跳地址，可选配置优先级与描述。",
        "commands": (
            "# ===== 配置静态路由（H3C Comware7） =====\n"
            "system-view\n"
            "ip route-static {{dest_net}} {{mask}} {{next_hop}} preference {{preference}} description {{desc}}\n"
            "display ip routing-table {{dest_net}}\n"
            "save force\n"
        ),
        "params": [
            {"name": "dest_net", "label": "目的网段", "default": "192.168.20.0", "required": True,
             "validate": "ipv4", "example": "192.168.20.0"},
            {"name": "mask", "label": "掩码/前缀", "default": "24", "required": True,
             "validate": "masklen", "example": "24 或 255.255.255.0"},
            {"name": "next_hop", "label": "下一跳", "default": "10.0.0.2", "required": True,
             "validate": "ipv4", "example": "10.0.0.2"},
            {"name": "preference", "label": "优先级", "default": "60", "required": False,
             "validate": "int:1-255", "example": "60"},
            {"name": "desc", "label": "描述", "default": "to-branch", "required": False,
             "validate": "", "example": "to-branch"},
        ],
        "notes": (
            "1) Comware7 保存是 save force 可直接落盘；save 会交互确认。\n"
            "2) preference（优先级）数值越小越优先，默认 60；写 1 会让静态路由抢 OSPF 的活。\n"
            "3) description 不能带空格，要连字符或下划线。"
        ),
        "verified": 0,
        "verified_by": "", "verified_model": "", "verified_date": "",
        "favorite": 0,
    },
]


# ---------------------------------------------------------------------------
# 数据库封装
# ---------------------------------------------------------------------------

class Database(object):
    """命令库数据访问对象。所有 SQL 都收敛在这里，UI 层不直接写 SQL。"""

    def __init__(self, db_path=None):
        self.db_path = db_path or get_db_path()
        # 支持 ":memory:" 内存库：自检/质检用，不落盘、不产生临时文件（离网环境更干净）
        self.is_memory = (self.db_path == ":memory:")
        self.was_created = not self.is_memory and not os.path.exists(self.db_path)
        self.readonly = False                                 # U 盘只读标志
        self.corrupt_backup = None                            # 损坏库备份路径
        self.closed = False                                   # 连接是否已关闭
        self.pending_import = {}                              # 上次导入时未处理的三库数组计数
        self.missing_tables = []                              # 只读库缺失的表（供 UI 显示精准指引）
        self.missing_columns = []                             # 只读库缺失的列（v1 老库：platform 等）

        # 先判断文件层可写性：只读库文件必须以只读方式打开，
        # 否则建表语句会抛 "attempt to write a readonly database"，导致程序根本起不来。
        self.file_readonly = (not self.is_memory and not self.was_created
                             and not os.access(self.db_path, os.W_OK))
        if self.was_created:
            folder = os.path.dirname(self.db_path) or "."
            if not os.access(folder, os.W_OK):
                raise RuntimeError(
                    "程序所在目录不可写（U 盘写保护或权限不足），无法创建命令库文件：\n%s\n\n"
                    "请关闭 U 盘写保护开关后重试。若手上有旧的 command_lib.db，"
                    "把它拷到程序同目录即可直接使用。" % self.db_path)

        self._connect()
        if self.file_readonly:
            self.readonly = True
            # 只读库不做 DDL（只能读不能写），所以遍历全部 5 张表做一次结构校验：
            #   · 核心表（entries / history）缺失 → 这根本不是本程序的命令库，拒绝启动，
            #     并给出"在可写环境打开一次完成升级"的可执行指引；
            #   · 辅助表（trouble_trees / err_dict / err_unresolved）缺失 → 记入
            #     missing_tables 交给 UI 把对应 Tab 降级为占位提示。只读介质上无法
            #     自动建表升级，但命令库本体的搜索/浏览/复制必须仍然可用。
            missing = []
            for table in ("entries", "history", "trouble_trees",
                          "err_dict", "err_unresolved"):
                try:
                    self.conn.execute("SELECT count(*) FROM %s" % table).fetchone()
                except sqlite3.Error:
                    missing.append(table)
            self.missing_tables = missing
            # 老库（schema v1）除了缺表，还缺 v2 新增的列（entries.platform/duration、
            # history.table_name/record_id）。只读时同样无法 ALTER TABLE 升级，
            # 把这些列也记下来 —— 涉及它们的读路径必须退化成 v1 语义，
            # 否则主窗口读一次 tree_data() 就崩在 "no such column: platform"。
            for table, cols in (("entries", ("platform", "duration")),
                                ("history", ("table_name", "record_id"))):
                if table in missing:
                    continue
                try:
                    have = set(r["name"] for r in self.conn.execute(
                        "PRAGMA table_info(%s)" % table).fetchall())
                except sqlite3.Error:
                    continue
                for col in cols:
                    if col not in have:
                        self.missing_columns.append("%s.%s" % (table, col))
            fatal = [t for t in missing if t in ("entries", "history")]
            if fatal:
                raise RuntimeError(
                    "命令库文件为只读，且核心表缺失（%s），无法作为命令库使用：\n%s\n\n"
                    "修复指引：请在【可写】环境下用本程序打开一次该库 —— 程序会自动补齐"
                    "缺失的表结构（schema 升级到 v%d）后即可正常使用；\n"
                    "或者关闭 U 盘写保护开关后重新打开程序。"
                    % ("、".join(fatal), self.db_path, SCHEMA_VERSION))
        else:
            self._create_schema()
            self._detect_readonly()

    # ------------------------------------------------------------------
    # 连接与建表
    # ------------------------------------------------------------------
    def _connect(self):
        """
        建立连接。
        · 只读文件 → 用 SQLite URI 以 mode=ro 打开（能读不能写，满足现场浏览需求）
        · 库文件损坏（U 盘拔插/掉电常见）→ 重命名为 .corrupt-<时间>.bak 后重建空库，
          保证程序永远能起来（离网现场没有重装机会）
        """
        if self.is_memory:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            return

        if self.file_readonly:
            self.conn = sqlite3.connect(self._readonly_uri(self.db_path), uri=True, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            return

        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            try:
                self.conn.close()
            except Exception:
                pass
            stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            self.corrupt_backup = "%s.corrupt-%s.bak" % (self.db_path, stamp)
            try:
                os.rename(self.db_path, self.corrupt_backup)
            except Exception:
                pass
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        # 不用 WAL：U 盘 / FAT32 分区上 WAL 容易产生残留 -wal 文件导致库异常
        try:
            self.conn.execute("PRAGMA journal_mode = DELETE")
        except sqlite3.Error:
            pass

    @staticmethod
    def _readonly_uri(db_path):
        """
        拼 SQLite 只读 URI。
        项目禁止 import urllib 等网络模块，这里手工做最小必要的转义
        （空格与 # ? 在 URI 里有特殊含义，必须转义，否则打不开中文/带空格的路径）。
        """
        path = db_path.replace("\\", "/")
        path = path.replace("%", "%25").replace(" ", "%20")
        path = path.replace("#", "%23").replace("?", "%3F")
        return "file:%s?mode=ro" % path

    def _create_schema(self):
        """
        建表 + 建索引（index 建在 title / commands 上，配合 LIKE 搜索）。
        建完再走一次 _migrate()，保证老库（schema v1）也能自动升级到 v2。
        """
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                uuid            TEXT PRIMARY KEY,
                platform        TEXT DEFAULT 'network', -- 平台：network / linux
                device_type     TEXT,           -- 设备形态：交换机/路由器/防火墙/无线AC（linux 侧可空）
                vendor          TEXT,           -- 厂商 slug：cisco/huawei/kylin...
                os_family       TEXT,           -- OS slug：ios/vrp5/comware7/centos7/kylinV10...
                models          TEXT,           -- 适用型号（自由文本）
                category        TEXT,           -- 场景分类：VLAN/Trunk/OSPF...
                title           TEXT NOT NULL,  -- 标题
                description     TEXT,           -- 描述
                commands        TEXT,           -- 命令全文，含 {{参数}} 占位
                params          TEXT,           -- JSON 数组：[{name,label,default,required,validate,example}]
                notes           TEXT,           -- 坑点备注
                duration        TEXT DEFAULT '',-- temp/perm/both，仅 linux 条目使用
                verified        INTEGER DEFAULT 0,   -- 0=未验证(灰) 1=已验证(绿)
                verified_by     TEXT,
                verified_model  TEXT,
                verified_date   TEXT,
                favorite        INTEGER DEFAULT 0,   -- 收藏置顶
                created_at      TEXT,
                updated_at      TEXT,
                exec_level      TEXT DEFAULT '',     -- 命令可用性：''/verified-cli/skeleton/web-only
                interactive     INTEGER DEFAULT 0    -- 1=含交互式输入（口令等），不适合脚本渲染
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name  TEXT,       -- entries / trouble_trees / err_dict（三库共用）
                record_id   TEXT,       -- 对应表的记录标识（entries 用 uuid）
                entry_uuid  TEXT,       -- 兼容 v1 老数据，新记录同样回填
                action      TEXT,       -- create/update/verify/unverify/favorite/delete/import
                old_value   TEXT,
                new_value   TEXT,
                operator    TEXT,       -- 操作人（离网环境留痕用）
                ts          TEXT
            )
            """
        )
        # ★ 先补列再建索引：老库（v1）里 CREATE TABLE IF NOT EXISTS 不会改结构，
        #   必须先跑 _migrate() 把 platform/duration 等新列加上，否则索引会报 no such column
        self._migrate()
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_title ON entries(title)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_commands ON entries(commands)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_vendor ON entries(vendor)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_platform ON entries(platform)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_history_rec ON history(table_name, record_id)")
        # 报错字典（模块 05）：pattern 为正则（忽略大小写），examples 供自测
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS err_dict (
                err_id        TEXT PRIMARY KEY,
                vendor        TEXT,
                os_family     TEXT,
                pattern       TEXT NOT NULL,
                category      TEXT,       -- 语法/模式/依赖/资源冲突/管理认证/保存系统
                cause         TEXT,       -- 人话解释
                solution_steps TEXT,      -- JSON：[{text, ref_entry_uuid?, fix_template?}]
                fix_template  TEXT,
                examples      TEXT,       -- JSON：≥2 条真实样例
                verified      INTEGER DEFAULT 0,
                verified_by   TEXT,
                verified_date TEXT,
                hit_count     INTEGER DEFAULT 0,
                created_at    TEXT,
                updated_at    TEXT,
                exec_level    TEXT DEFAULT ''   -- ''/skeleton 等（骨架报错=构造样例待核对）
            )
            """
        )
        # 待解决收件箱：匹配不到的报错先存着，人工转成字典条目
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS err_unresolved (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                pasted_text    TEXT NOT NULL,
                guessed_vendor TEXT,
                ts             TEXT,
                status         TEXT DEFAULT 'pending',   -- pending / resolved
                resolved_err_id TEXT
            )
            """
        )
        # 排查树（模块 04）：steps 存 JSON 数组，cmd_ref 引用命令库条目（严禁硬编码命令）
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trouble_trees (
                tree_id      TEXT PRIMARY KEY,
                symptom      TEXT NOT NULL,
                vendor_hint  TEXT,
                category     TEXT,
                steps        TEXT,
                verified     INTEGER DEFAULT 0,
                verified_by  TEXT,
                verified_date TEXT,
                created_at   TEXT,
                updated_at   TEXT
            )
            """
        )
        # AI 产出入库溯源（阶段二）：本地知识留痕，不参与 .nlb 导出导入。
        #   每次从 AI 会话气泡 [产出入库] 成功写入命令库/报错库/排查树，
        #   都在这里记一条（session_id + message_index + block_index 可唯一定位到块）。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_imports (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT,          -- 来源会话（sessions/{session_id}.json）
                message_index INTEGER,       -- 会话消息序号（0 起，messages 数组下标）
                block_index   INTEGER,       -- 回复内代码块序号（-1=整条回复/非块级）
                target_type   TEXT DEFAULT 'entry',  -- entry / err / tree
                target_uuid   TEXT,          -- entries.uuid / err_dict.err_id / trouble_trees.tree_id
                imported_at   TEXT,
                operator      TEXT,
                model         TEXT,          -- 生成该回复的模型名
                dup_of_uuid   TEXT           -- 查重"仍入库/替换"时指向的既有条目（增量列）
            )
            """
        )
        # 旧库自动补列（未来 ai_imports 结构演进时在此扩展；当前新建即全列）
        icols = set(r["name"] for r in self.conn.execute(
            "PRAGMA table_info(ai_imports)").fetchall()) if "ai_imports" in \
            [t[0] for t in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else set()
        for col, ddl in (("session_id", "TEXT"), ("target_type", "TEXT DEFAULT 'entry'"),
                         ("message_index", "INTEGER"), ("block_index", "INTEGER"),
                         ("dup_of_uuid", "TEXT")):
            if icols and col not in icols:
                self.conn.execute("ALTER TABLE ai_imports ADD COLUMN %s %s" % (col, ddl))
        self.conn.commit()

    def _migrate(self):
        """
        版本升级（可重入，每次启动都会跑）：
            v1 → v2：
                1. entries 补 platform / duration 两列
                2. platform 按 device_type 回填（交换机/路由器/防火墙/无线AC → network）
                3. duration 一律留空
                4. history 补 table_name / record_id 两列并回填 entries 语义
                5. vendor / os_family 的老显示名归一化成 slug
        所有步骤都先检测再执行，重复运行无副作用；只读库直接跳过。
        """
        if self.readonly or self.file_readonly:
            return
        try:
            cols = set(r["name"] for r in
                       self.conn.execute("PRAGMA table_info(entries)").fetchall())
            if "platform" not in cols:
                self.conn.execute("ALTER TABLE entries ADD COLUMN platform TEXT DEFAULT 'network'")
                self.conn.execute("UPDATE entries SET platform = 'network' "
                                  "WHERE platform IS NULL OR platform = ''")
            if "duration" not in cols:
                self.conn.execute("ALTER TABLE entries ADD COLUMN duration TEXT DEFAULT ''")
            # v2.1（审计 P1/P4 整改）：骨架标记 + 交互式标记，老库自动补列
            if "exec_level" not in cols:
                self.conn.execute("ALTER TABLE entries ADD COLUMN exec_level TEXT DEFAULT ''")
            if "interactive" not in cols:
                self.conn.execute("ALTER TABLE entries ADD COLUMN interactive INTEGER DEFAULT 0")

            # v2.2（批2B）：err_dict 补 exec_level 列（骨架报错条目标记）
            ecols = set(r["name"] for r in
                        self.conn.execute("PRAGMA table_info(err_dict)").fetchall())
            if ecols and "exec_level" not in ecols:
                self.conn.execute("ALTER TABLE err_dict ADD COLUMN exec_level TEXT DEFAULT ''")

            hcols = set(r["name"] for r in
                        self.conn.execute("PRAGMA table_info(history)").fetchall())
            if "table_name" not in hcols:
                self.conn.execute("ALTER TABLE history ADD COLUMN table_name TEXT")
            if "record_id" not in hcols:
                self.conn.execute("ALTER TABLE history ADD COLUMN record_id TEXT")
            self.conn.execute(
                "UPDATE history SET table_name = 'entries' "
                "WHERE table_name IS NULL OR table_name = ''")
            self.conn.execute(
                "UPDATE history SET record_id = entry_uuid "
                "WHERE (record_id IS NULL OR record_id = '') "
                "AND entry_uuid IS NOT NULL AND entry_uuid <> ''")

            # 维度取值归一化（老库里的显示名 → slug）
            for row in self.conn.execute(
                    "SELECT uuid, vendor, os_family FROM entries").fetchall():
                new_vendor = normalize_vendor(row["vendor"])
                new_os = normalize_os(row["os_family"])
                if new_vendor != (row["vendor"] or "") or new_os != (row["os_family"] or ""):
                    self.conn.execute(
                        "UPDATE entries SET vendor = ?, os_family = ? WHERE uuid = ?",
                        (new_vendor, new_os, row["uuid"]))

            self.conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        except sqlite3.Error:
            # 迁移失败不让程序起不来：表结构可能已是新版，或权限受限
            pass

    def _detect_readonly(self):
        """
        U 盘只读检测：文件/目录不可写，或写事务开不起来 → readonly=True。
        UI 层据此禁用编辑功能并提示。
        """
        if self.was_created and not os.access(get_base_dir(), os.W_OK):
            self.readonly = True
            return
        if os.path.exists(self.db_path) and not os.access(self.db_path, os.W_OK):
            self.readonly = True
            return
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("COMMIT")
            self.readonly = False
        except sqlite3.Error:
            try:
                self.conn.execute("ROLLBACK")
            except Exception:
                pass
            self.readonly = True

    def close(self):
        """关闭连接（退出时调用）"""
        self.closed = True
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _escape_like(text):
        """转义 LIKE 通配符，避免用户输入的 % _ \\ 破坏查询语义"""
        return (text or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _row_to_dict(row):
        return dict(row) if row is not None else None

    @staticmethod
    def _dump_params(params):
        """params 字段统一存 JSON 字符串；传进来是 list 就序列化，是 str 就原样"""
        if params is None:
            return "[]"
        if isinstance(params, (list, dict)):
            return json.dumps(params, ensure_ascii=False)
        return str(params)

    @staticmethod
    def load_params(entry):
        """把条目里的 params 字符串解析成 list，解析失败返回 []（不抛异常）"""
        if not entry:
            return []
        raw = entry.get("params") or "[]"
        if isinstance(raw, (list, dict)):
            return raw
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _normalize_entry(self, raw):
        """把外部传入的 dict 规整成字段齐全、类型正确的一行数据（含维度 slug 归一化）"""
        data = {}
        for f in ENTRY_FIELDS:
            data[f] = raw.get(f, "")
        # 类型归位
        data["uuid"] = str(data["uuid"] or new_uuid())
        data["params"] = self._dump_params(raw.get("params", "[]"))
        for f in ENTRY_FIELDS:
            if data[f] is None:
                data[f] = ""
        # 维度归一化：库里统一存 slug（兼容老的显示名写法）
        data["platform"] = normalize_platform(data.get("platform") or raw.get("platform"))
        data["vendor"] = normalize_vendor(data.get("vendor"))
        data["os_family"] = normalize_os(data.get("os_family"))
        data["duration"] = normalize_duration(data.get("duration"))
        data["verified"] = 1 if str(data.get("verified", 0)) in ("1", "True", "true") else 0
        data["favorite"] = 1 if str(data.get("favorite", 0)) in ("1", "True", "true") else 0
        # exec_level：只接受规范取值，脏数据一律回落为 ''（审计 P1）
        data["exec_level"] = str(data.get("exec_level") or "").strip()
        if data["exec_level"] not in EXEC_LEVELS:
            data["exec_level"] = ""
        # interactive：0/1 布尔化（审计 P4）
        data["interactive"] = 1 if str(data.get("interactive", 0)) in ("1", "True", "true") else 0
        now = now_str()
        data["created_at"] = data["created_at"] or now
        data["updated_at"] = data["updated_at"] or now
        return data

    def log_history(self, table_name, record_id, action,
                    old_value="", new_value="", operator=""):
        """
        写一条修改历史；只读库直接跳过。
        三库共用：entries 用 uuid 作 record_id，trouble_trees/err_dict 用各自的 id。
        entry_uuid 列同时回填，兼容 v1 老数据的读取方式。
        """
        if self.readonly or self.closed:
            return
        record_id = str(record_id or "")
        try:
            self.conn.execute(
                "INSERT INTO history (table_name, record_id, entry_uuid, action, "
                "old_value, new_value, operator, ts) VALUES (?,?,?,?,?,?,?,?)",
                (table_name, record_id, record_id, action, str(old_value or ""),
                 str(new_value or ""), operator, now_str()),
            )
            self.conn.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_entry(self, entry_uuid):
        """按 UUID 取单条"""
        cur = self.conn.execute("SELECT * FROM entries WHERE uuid = ?", (entry_uuid,))
        return self._row_to_dict(cur.fetchone())

    def has_column(self, field, table="entries"):
        """
        判断某列在只读老库里是否存在。
        v1 → v2 迁移会新增 platform / duration / table_name / record_id，
        但只读介质上迁移被跳过，因此涉及这些列的查询要先问一句。
        """
        return ("%s.%s" % (table, field)) not in self.missing_columns

    def search(self, keyword="", platform=None, device_type=None, vendor=None,
               os_family=None, category=None, duration=None,
               favorite_only=False, verified_only=False):
        """
        组合搜索：关键字（标题/描述/命令/备注/型号/场景）+ 多维过滤链。
        排序：收藏优先 → 已验证优先 → 最近更新 → 标题。
        使用 LIKE ESCAPE 兜底，不依赖 FTS5，中文关键字可直接命中。
        维度参数支持传 slug 或老的显示名（内部统一归一化后比较）。
        """
        sql = ["SELECT * FROM entries WHERE 1 = 1"]
        args = []

        kw = (keyword or "").strip()
        if kw:
            like = "%" + self._escape_like(kw) + "%"
            sql.append(
                "AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
                "OR commands LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\' "
                "OR models LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\' "
                "OR vendor LIKE ? ESCAPE '\\' OR os_family LIKE ? ESCAPE '\\')"
            )
            args.extend([like] * 8)

        if platform:
            platform_slug = normalize_platform(platform)
            if not self.has_column("platform"):
                # v1 老库没有 platform 列：全部条目都按网络设备看待，
                # 因此"只看 Linux"在语义上必然是空集（不是查不到，是真的没有）
                if platform_slug == "linux":
                    return []
            else:
                sql.append("AND platform = ?")
                args.append(platform_slug)

        # 厂商 / OS 走归一化后在 slug 上比较，避免"过滤用显示名、库内存 slug"对不上
        for field, value, normalizer in (
                ("device_type", device_type, None),
                ("vendor", vendor, normalize_vendor),
                ("os_family", os_family, normalize_os),
                ("category", category, None),
                ("duration", duration, normalize_duration)):
            if not value or value in ("全部", "全部设备", "全部厂商",
                                      "全部OS", "全部场景", "全部平台"):
                continue
            if not self.has_column(field):
                # 老库缺该列（duration）：语义上没有任何取值，必为空集
                return []
            sql.append("AND %s = ?" % field)
            args.append(normalizer(value) if normalizer else value)

        if favorite_only:
            sql.append("AND favorite = 1")
        if verified_only:
            sql.append("AND verified = 1")

        sql.append("ORDER BY favorite DESC, verified DESC, updated_at DESC, title ASC")
        cur = self.conn.execute(" ".join(sql), args)
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def all_entries(self):
        """全部条目（导出用）"""
        return self.search()

    def count(self):
        """条目总数"""
        return int(self.conn.execute("SELECT count(*) FROM entries").fetchone()[0])

    def distinct(self, field, platform=None):
        """取某字段的去重值列表（构建左侧树 / 过滤下拉框用）。返回的是 slug 原值"""
        if field not in ENTRY_FIELDS or not self.has_column(field):
            return []
        sql = ("SELECT DISTINCT %s AS v FROM entries "
               "WHERE %s IS NOT NULL AND %s <> ''" % (field, field, field))
        args = []
        if platform:
            platform_slug = normalize_platform(platform)
            if not self.has_column("platform"):
                if platform_slug == "linux":
                    return []
            else:
                sql += " AND platform = ?"
                args.append(platform_slug)
        sql += " ORDER BY v"
        cur = self.conn.execute(sql, args)
        return [r["v"] for r in cur.fetchall()]

    def tree_data(self):
        """
        左侧树数据，按平台分两大类（模块 01/03 规格）：
            网络设备：平台 → 设备类型 → 厂商 → OS 版本（四层）
            Linux   ：平台 → 发行版   → 版本  （三层）
        返回 {"network": {l1: {l2: [l3, ...]}}, "linux": {l1: {l2: []}}}
        （Linux 分支的 l2 对应空列表，表示到版本就到头了）
        """
        result = {"network": {}, "linux": {}}
        if self.has_column("platform"):
            cur = self.conn.execute(
                "SELECT DISTINCT platform, device_type, vendor, os_family FROM entries"
            )
        else:
            # v1 老库没有 platform 列：全部条目都是网络设备（v1 无 Linux 条目）
            cur = self.conn.execute(
                "SELECT DISTINCT device_type, vendor, os_family FROM entries"
            )
        for r in cur.fetchall():
            platform = normalize_platform(r["platform"] if "platform" in r.keys() else None)
            vendor = normalize_vendor(r["vendor"])
            os_family = normalize_os(r["os_family"])
            if platform == "linux":
                result["linux"].setdefault(vendor, {}).setdefault(os_family, [])
            else:
                device_type = r["device_type"] or "未分类"
                bucket = result["network"].setdefault(device_type, {}).setdefault(vendor, [])
                if os_family and os_family not in bucket:
                    bucket.append(os_family)
        for device_type in result["network"]:
            for vendor in result["network"][device_type]:
                result["network"][device_type][vendor].sort()
        return result

    def platform_stats(self):
        """按平台统计条目数：{'network': n, 'linux': n}"""
        if not self.has_column("platform"):
            # v1 老库：无 platform 列，条目全部视为网络设备
            return {"network": self.count(), "linux": 0}
        cur = self.conn.execute(
            "SELECT platform, count(*) AS n FROM entries GROUP BY platform")
        stats = {"network": 0, "linux": 0}
        for r in cur.fetchall():
            stats[normalize_platform(r["platform"])] = int(r["n"] or 0)
        return stats

    def stats(self):
        """底部统计：{'total': n, 'verified': n, 'favorite': n}"""
        row = self.conn.execute(
            "SELECT count(*) AS total, "
            "SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END) AS v, "
            "SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END) AS f FROM entries"
        ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "verified": int(row["v"] or 0),
            "favorite": int(row["f"] or 0),
        }

    def get_history(self, record_id, table_name="entries", limit=200):
        """
        取某条记录的修改历史（新的在前）。
        默认查 entries 表；trouble_trees / err_dict 传入对应 table_name 即可（三库共用）。
        ★ v1 老库的 history 表没有 table_name / record_id 两列，只读时无法迁移，
          退化成按 entry_uuid 查询（v1 只记录 entries 的历史）。
        """
        if not self.has_column("table_name", "history"):
            cur = self.conn.execute(
                "SELECT * FROM history WHERE entry_uuid = ? "
                "ORDER BY id DESC LIMIT ?",
                (str(record_id or ""), limit),
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        cur = self.conn.execute(
            "SELECT * FROM history WHERE table_name = ? AND record_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (table_name, str(record_id or ""), limit),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add_entry(self, raw, operator=""):
        """新增条目；返回 uuid。新条目 verified 一律强制为 0（未验证）"""
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法新增条目")
        data = self._normalize_entry(raw)
        data["verified"] = 0
        data["verified_by"] = ""
        data["verified_model"] = ""
        data["verified_date"] = ""
        cols = ",".join(ENTRY_FIELDS)
        marks = ",".join(["?"] * len(ENTRY_FIELDS))
        self.conn.execute(
            "INSERT INTO entries (%s) VALUES (%s)" % (cols, marks),
            [data[f] for f in ENTRY_FIELDS],
        )
        self.conn.commit()
        self.log_history("entries", data["uuid"], "create", "", data["title"], operator)
        return data["uuid"]

    def update_entry(self, entry_uuid, changes, operator=""):
        """按字段局部更新；会把 old/new 差异写入 history"""
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法修改条目")
        old = self.get_entry(entry_uuid)
        if not old:
            raise KeyError("条目不存在：%s" % entry_uuid)

        fields, values, diffs = [], [], []
        for key, value in changes.items():
            if key not in EDITABLE_FIELDS or key == "updated_at":
                continue
            if key == "params":
                value = self._dump_params(value)
            if key in ("verified", "favorite"):
                value = 1 if str(value) in ("1", "True", "true") else 0
            if key == "verified" and value == old.get("verified"):
                continue
            if str(old.get(key, "")) != str(value):
                fields.append("%s = ?" % key)
                values.append(value)
                diffs.append("%s: %r -> %r" % (key, old.get(key, ""), value))

        if not fields:
            return False

        fields.append("updated_at = ?")
        values.append(now_str())
        values.append(entry_uuid)
        self.conn.execute("UPDATE entries SET %s WHERE uuid = ?" % ",".join(fields), values)
        self.conn.commit()

        # 动作名判定（★ 只认"真的写进去了"的字段，避免把纯收藏记成取消验证）：
        #   · verified 确有变化 → verify / unverify
        #   · 仅 favorite 一项  → favorite
        #   · 其余              → update
        # 早前用 `if "verified" in changes` 判断：收藏时会顺带塞一个 verified 键，
        # 于是纯收藏被记成 action=unverify，留痕表直接误导。
        changed_keys = set(f.split(" = ")[0] for f in fields) - {"updated_at"}
        action = "update"
        if "verified" in changed_keys:
            action = "verify" if int(changes.get("verified") or 0) == 1 else "unverify"
        elif changed_keys == {"favorite"}:
            action = "favorite"
        self.log_history("entries", entry_uuid, action,
                         " | ".join(diffs) if action == "update" else (old.get("verified_by", "") or ""),
                         "; ".join(diffs), operator)
        return True

    def delete_entry(self, entry_uuid, operator=""):
        """删除条目（history 保留，便于追溯）"""
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法删除条目")
        old = self.get_entry(entry_uuid)
        if not old:
            return False
        self.conn.execute("DELETE FROM entries WHERE uuid = ?", (entry_uuid,))
        self.conn.commit()
        self.log_history("entries", entry_uuid, "delete", old.get("title", ""), "", operator)
        return True

    def set_favorite(self, entry_uuid, value, operator=""):
        """
        收藏 / 取消收藏。

        ★ 只传 favorite 一个键：早前实现额外塞了 verified 键，会被 update_entry
          的动作判定误判成"取消验证"，把一次纯收藏记成 action=unverify（留痕错乱）。
        """
        return self.update_entry(entry_uuid, {"favorite": 1 if value else 0}, operator)

    def toggle_favorite(self, entry_uuid, operator=""):
        """收藏状态取反，返回新状态"""
        cur = self.get_entry(entry_uuid)
        if not cur:
            return 0
        new_value = 0 if cur.get("favorite") else 1
        if self.readonly:
            return cur.get("favorite", 0)
        self.conn.execute(
            "UPDATE entries SET favorite = ?, updated_at = ? WHERE uuid = ?",
            (new_value, now_str(), entry_uuid),
        )
        self.conn.commit()
        self.log_history("entries", entry_uuid, "favorite",
                         str(cur.get("favorite", 0)), str(new_value), operator)
        return new_value

    def mark_verified(self, entry_uuid, verified_by, verified_model, verified_date=None,
                      unverify=False, operator=""):
        """标记已验证（徽章变绿）/ 取消验证（徽章变灰）"""
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法标记验证")
        if unverify:
            changes = {"verified": 0, "verified_by": "", "verified_model": "", "verified_date": ""}
        else:
            changes = {
                "verified": 1,
                "verified_by": verified_by or "",
                "verified_model": verified_model or "",
                "verified_date": verified_date or datetime.date.today().isoformat(),
            }
        return self.update_entry(entry_uuid, changes, operator)

    # ------------------------------------------------------------------
    # 种子库导入 / .nlb 导入导出（按 UUID 合并去重）
    # ------------------------------------------------------------------
    def import_entries(self, entries, merge=True, operator="import"):
        """
        批量导入。按 UUID 合并去重：
            - 库中没有该 UUID → 新增
            - 库中已有：merge=True 时逐字段比对，有变化才更新；merge=False 一律跳过
            - 验证态四字段（VERIFY_FIELDS）一律不采纳：新增强制 0，更新不改动
              （P1-2 裁决 2026-10-08：导入不携带验证态，验证状态只经真机回填流程产生）
        返回 (新增数, 更新数, 跳过数)
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法导入")
        added = updated = skipped = 0
        for raw in entries or []:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            data = self._normalize_entry(raw)
            # P1-2 裁决（2026-10-08）：导入不携带验证态——verified 四字段一律归零，
            # 验证状态只经真机回填流程产生（新增/更新两路径统一在此收敛）
            data["verified"] = 0
            data["verified_by"] = ""
            data["verified_model"] = ""
            data["verified_date"] = ""
            exist = self.get_entry(data["uuid"])
            if not exist:
                cols = ",".join(ENTRY_FIELDS)
                marks = ",".join(["?"] * len(ENTRY_FIELDS))
                self.conn.execute(
                    "INSERT INTO entries (%s) VALUES (%s)" % (cols, marks),
                    [data[f] for f in ENTRY_FIELDS],
                )
                self.log_history("entries", data["uuid"], "import", "", data["title"], operator)
                added += 1
                continue

            if not merge:
                skipped += 1
                continue

            # ★ 真幂等：逐字段比对，没有任何实际变化就跳过 ——
            #   否则每次重复导入都会写一遍全库、并在 history 里刷一堆无意义记录
            #   验证态四字段已在上方归零且不允许经导入改动（VERIFY_FIELDS 白名单外），
            #   故原"已验证一方胜出"守卫随 P1-2 裁决一并移除（防降级职责由排除承担）
            changes = {}
            for f in EDITABLE_FIELDS:
                if f == "updated_at" or f in VERIFY_FIELDS:
                    continue
                if cmp_value(data[f]) != cmp_value(exist.get(f)):
                    changes[f] = data[f]
            if not changes:
                skipped += 1
                continue
            self.update_entry(data["uuid"], changes, operator)
            updated += 1
        self.conn.commit()
        return added, updated, skipped

    def import_nlb(self, path, merge=True, operator="import"):
        """
        从 .nlb（JSON）文件导入三库 + 收件箱内容。
        结构（v3）：{"entries":[...], "trouble_trees":[...], "err_dict":[...],
                    "err_unresolved":[...]}
        兼容：只含 entries 的 v1 老文件、无 err_unresolved 的 v2 文件，以及裸数组文件。
        返回 (新增, 更新, 跳过)。
        """
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, list):
            entries, trees, errs, unres = data, [], [], []
        elif isinstance(data, dict):
            entries = data.get("entries", []) or []
            trees = data.get("trouble_trees", []) or []
            errs = data.get("err_dict", []) or []
            unres = data.get("err_unresolved", []) or []
        else:
            entries, trees, errs, unres = [], [], [], []

        result = self.import_entries(entries, merge=merge, operator=operator)
        a, u, s = result
        if trees:
            ta, tu, ts = self.import_trees(trees, merge=merge, operator=operator)
            a += ta; u += tu; s += ts
        if errs:
            ea, eu, es = self.import_errs(errs, merge=merge, operator=operator)
            a += ea; u += eu; s += es
        if unres:
            ua, uu, us = self.import_unresolved(unres, operator=operator)
            a += ua; u += uu; s += us
        self.pending_import = {"trouble_trees": len(trees), "err_dict": len(errs),
                               "err_unresolved": len(unres)}
        return (a, u, s)

    def export_nlb(self, path, uuids=None):
        """
        导出为 .nlb（JSON），三库 + 待解决收件箱共用一个交换文件。
        uuids=None 表示导出全部条目。
        返回导出条目数。
        """
        if uuids:
            entries = [e for e in (self.get_entry(u) for u in uuids) if e]
        else:
            entries = self.all_entries()
        trees = self.export_trouble_trees() if hasattr(self, "export_trouble_trees") else []
        errs = self.export_err_dict() if hasattr(self, "export_err_dict") else []
        # ★ 收件箱一并导出（第 3 轮 缺口C）：它是现场积累的团队资产，
        #   原先不随 .nlb 走，团队间同步会把"待解决的报错"整批丢掉。
        unres = self.export_unresolved()
        payload = {
            "format": NLB_FORMAT,
            "schema": SCHEMA_VERSION,
            "exported_at": now_str(),
            "count": len(entries),          # 兼容 v1 读法
            "counts": {"entries": len(entries),
                       "trouble_trees": len(trees),
                       "err_dict": len(errs),
                       "err_unresolved": len(unres)},
            "entries": entries,
            "trouble_trees": trees,
            "err_dict": errs,
            "err_unresolved": unres,
        }
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        return len(entries)

    # ------------------------------------------------------------------
    # 首次运行：导入内置种子库
    # ------------------------------------------------------------------
    def seed_demo(self):
        """
        第 1 轮：导入 3 条硬编码演示数据，用于跑通"搜索/浏览/复制"。
        第 3 轮会替换为读取 seed_data/*.json 正式种子库（import_seed_dir）。
        """
        return self.import_entries(DEMO_ENTRIES, merge=True, operator="seed")

    def _collect_seed_files(self, seed_dir=None):
        """
        收集要导入的种子文件，**同名文件按目录优先级只取最高优先级那一份**：
            exe/源码同目录的 seed_data  >  打进 exe 的内置 seed_data

        为什么要按文件名去重：现场经常会在 U 盘的 seed_data 里改条目（比如把某条命令
        按真机修正过）。如果两份都导入，内置的那份会把现场的修正又覆盖回去，
        现场改的东西"重启就没了" —— 这是必须避免的。
        返回 [(相对路径, 绝对路径), ...]，顺序稳定。
        """
        candidates = [seed_dir] if seed_dir else seed_dirs()
        chosen = {}
        order = []
        visited = set()
        for root_dir in candidates:
            if not root_dir or not os.path.isdir(root_dir):
                continue
            real = os.path.realpath(root_dir)
            if real in visited:
                continue
            visited.add(real)
            for current, dirs, files in os.walk(root_dir):
                # 跳过隐藏目录与 __pycache__
                dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
                for name in sorted(files):
                    if not name.lower().endswith(".json"):
                        continue
                    rel = os.path.relpath(os.path.join(current, name), root_dir)
                    if rel in chosen:
                        continue        # 已有更高优先级的同名文件，跳过
                    chosen[rel] = os.path.join(current, name)
                    order.append(rel)
        return [(rel, chosen[rel]) for rel in order]

    def import_seed_dir(self, seed_dir=None):
        """
        导入内置种子库：递归扫描 seed_data（含厂商子目录）下的 .json 文件。
        按 UUID 合并去重，所以重复导入不会产生重复条目；
        同名厂商文件只取优先级最高的那一份（见 _collect_seed_files）。
        返回 (新增, 更新, 跳过)；目录不存在时安全返回 (0,0,0)。
        """
        added = updated = skipped = 0
        for rel, full in self._collect_seed_files(seed_dir):
            try:
                a, u, s = self.import_seed_file(full, operator="seed:" + rel)
                added += a
                updated += u
                skipped += s
            except Exception:
                # 单个文件坏了不影响其它厂商文件导入
                continue
        return added, updated, skipped

    def import_seed_file(self, path, merge=True, operator="import"):
        """
        导入单个种子文件，自动分发三库内容：
            entries / trouble_trees / err_dict（err_dict 由模块 05 接入）
        返回 (新增, 更新, 跳过) 合计。
        """
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, list):
            entries, trees = data, []
        elif isinstance(data, dict):
            entries = data.get("entries", []) or []
            trees = data.get("trouble_trees", []) or []
        else:
            entries, trees = [], []

        errs = []
        if isinstance(data, dict):
            errs = data.get("err_dict", []) or []
        a, u, s = 0, 0, 0
        if entries:
            ea, eu, es = self.import_entries(entries, merge=merge, operator=operator)
            a += ea; u += eu; s += es
        if trees:
            ta, tu, ts = self.import_trees(trees, merge=merge, operator=operator)
            a += ta; u += tu; s += ts
        if errs:
            ea, eu, es = self.import_errs(errs, merge=merge, operator=operator)
            a += ea; u += eu; s += es
        return a, u, s

    def count_seed_files(self, seed_dir=None):
        """统计待导入的种子库文件数（同名文件按优先级只算一份）"""
        return len(self._collect_seed_files(seed_dir))

    # ==================================================================
    # 排查向导：trouble_trees 表（模块 04）
    #   steps 结构：[{"id","title","cmd_ref","observe","explain","branches",
    #                 "leafs":{"conclusion","actions":[...]}}]
    #   cmd_ref：{"uuid": 条目uuid} 或 {"vendor_category": "场景分类"}
    #   —— 命令一律实时渲染自命令库，严禁在树里硬编码命令文本
    # ==================================================================

    def _dump_json(self, obj):
        try:
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return "[]"

    def _load_json(self, raw):
        try:
            data = json.loads(raw) if raw else []
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _normalize_tree(self, raw):
        data = {}
        for f in TREE_FIELDS:
            data[f] = raw.get(f, "")
        data["tree_id"] = str(data["tree_id"] or new_uuid())
        # vendor_hint / steps 统一存 JSON 字符串
        vh = raw.get("vendor_hint") or []
        if not isinstance(vh, list):
            vh = self._load_json(vh)
        data["vendor_hint"] = self._dump_json(vh)
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            steps = self._load_json(steps)
        data["steps"] = self._dump_json(steps)
        for f in TREE_FIELDS:
            if data[f] is None:
                data[f] = ""
        data["verified"] = 1 if str(data.get("verified", 0)) in ("1", "True", "true") else 0
        now = now_str()
        data["created_at"] = data["created_at"] or now
        data["updated_at"] = data["updated_at"] or now
        return data

    @staticmethod
    def _tree_to_dict(row):
        """行 → dict，并把 JSON 字符串解析回数组（给 UI 直接用）"""
        d = dict(row)
        d["vendor_hint"] = load_list(d.get("vendor_hint"))
        d["steps"] = load_list(d.get("steps"))
        d["verified"] = int(d.get("verified") or 0)
        return d

    def all_trees(self, category=None):
        """全部排查树（按分类、现象名排序）"""
        sql = "SELECT * FROM trouble_trees WHERE 1 = 1"
        args = []
        if category:
            sql += " AND category = ?"
            args.append(category)
        sql += " ORDER BY category, symptom"
        return [self._tree_to_dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def get_tree(self, tree_id):
        cur = self.conn.execute("SELECT * FROM trouble_trees WHERE tree_id = ?",
                                (tree_id,))
        row = cur.fetchone()
        return self._tree_to_dict(row) if row else None

    def count_trees(self):
        """
        排查树数量。
        ★ 只读库缺 trouble_trees 表时返回 0 而不是抛异常：本方法只用于
          "是否启用[去排查树]按钮"这类界面判断（ui_main._render_detail），
          缺表时若抛 sqlite3.OperationalError，会导致选中任意条目即崩。
          真正需要区分的场景由 UI 通过 self.missing_tables 给明确提示。
        """
        if "trouble_trees" in self.missing_tables:
            return 0
        return int(self.conn.execute("SELECT count(*) FROM trouble_trees").fetchone()[0])

    def add_tree(self, raw, operator=""):
        """新增排查树；返回 tree_id。新树 verified 一律为 0"""
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法新增排查树")
        data = self._normalize_tree(raw)
        data["verified"] = 0
        cols = ",".join(TREE_FIELDS)
        marks = ",".join(["?"] * len(TREE_FIELDS))
        self.conn.execute("INSERT INTO trouble_trees (%s) VALUES (%s)" % (cols, marks),
                          [data[f] for f in TREE_FIELDS])
        self.conn.commit()
        self.log_history("trouble_trees", data["tree_id"], "create", "", data["symptom"], operator)
        return data["tree_id"]

    def update_tree(self, tree_id, changes, operator=""):
        """
        按字段局部更新排查树。

        ★ 与 update_entry / update_err 对齐：JSON 字段（vendor_hint / steps）必须先
          序列化再绑参。此前直接把 list 绑进 SQL，会抛
          sqlite3.ProgrammingError: Error binding parameter 3: type 'list' is not supported
          —— add_tree 走 _normalize_tree 所以没事，改树这条路则一直是坏的
          （因长期没有 UI 调用点而未被发现，第 3 轮接入排查树维护界面后暴露）。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法修改排查树")
        exist = self.get_tree(tree_id)
        if not exist:
            raise RuntimeError("排查树不存在：%s" % tree_id)
        diffs = []
        sets, args = [], []
        for f, v in changes.items():
            if f not in TREE_FIELDS or f in ("tree_id", "created_at"):
                continue
            if f in ("vendor_hint", "steps"):
                # 允许传 list，也允许传 JSON 字符串；统一规范化后存储
                if not isinstance(v, list):
                    v = self._load_json(v)
                v = self._dump_json(v)
            elif f == "verified":
                v = 1 if str(v) in ("1", "True", "true") else 0
            elif v is None:
                v = ""
            if str(exist.get(f, "")) == str(v):
                continue                    # 值没变就不写，避免无意义留痕
            if f in ("vendor_hint", "steps"):
                old_show = "（结构变更）"
            else:
                old_show = exist.get(f, "")
            diffs.append("%s: %s -> %s" % (f, old_show, v))
            sets.append("%s = ?" % f)
            args.append(v)
        if not sets:
            return False
        sets.append("updated_at = ?")
        args.append(now_str())
        args.append(tree_id)
        self.conn.execute("UPDATE trouble_trees SET %s WHERE tree_id = ?" % ",".join(sets), args)
        self.conn.commit()
        if diffs:
            self.log_history("trouble_trees", tree_id, "update",
                             " | ".join(diffs)[:500], "; ".join(diffs)[:500], operator)
        return True

    def delete_tree(self, tree_id, operator=""):
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法删除排查树")
        exist = self.get_tree(tree_id)
        self.conn.execute("DELETE FROM trouble_trees WHERE tree_id = ?", (tree_id,))
        self.conn.commit()
        if exist:
            self.log_history("trouble_trees", tree_id, "delete",
                             exist.get("symptom", ""), "", operator)

    def mark_tree_verified(self, tree_id, verified_by, verified_model, verified_date, operator=""):
        """整棵树走通一次真实排障后标记已验证（复用绿徽章体系）"""
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法标记验证")
        self.conn.execute(
            "UPDATE trouble_trees SET verified = 1, verified_by = ?, verified_model = ?, "
            "verified_date = ?, updated_at = ? WHERE tree_id = ?",
            (verified_by, verified_model, verified_date, now_str(), tree_id))
        self.conn.commit()
        self.log_history("trouble_trees", tree_id, "verify", "", "%s / %s / %s"
                         % (verified_by, verified_model, verified_date), operator)

    def import_trees(self, trees, merge=True, operator="import"):
        """
        导入排查树（按 tree_id 合并去重，冲突保留 verified=1）。
        返回 (新增, 更新, 跳过)。
        """
        added = updated = skipped = 0
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法导入排查树")
        for raw in trees or []:
            if not isinstance(raw, dict) or not (raw.get("tree_id") or "").strip():
                skipped += 1
                continue
            data = self._normalize_tree(raw)
            exist = self.get_tree(data["tree_id"])
            if not exist:
                data["verified"] = 0
                data["verified_by"] = ""
                data["verified_date"] = ""
                cols = ",".join(TREE_FIELDS)
                marks = ",".join(["?"] * len(TREE_FIELDS))
                self.conn.execute("INSERT INTO trouble_trees (%s) VALUES (%s)" % (cols, marks),
                                  [data[f] for f in TREE_FIELDS])
                self.conn.commit()
                self.log_history("trouble_trees", data["tree_id"], "import", "",
                                 data["symptom"], operator)
                added += 1
                continue
            if not merge:
                skipped += 1
                continue
            if int(exist.get("verified") or 0) == 1 and int(data.get("verified") or 0) == 0:
                skipped += 1
                continue
            changes = {}
            for f in TREE_FIELDS:
                if f in ("tree_id", "created_at", "updated_at"):
                    continue
                if cmp_value(data[f]) != cmp_value(exist.get(f)):
                    changes[f] = data[f]
            if not changes:
                skipped += 1
                continue
            self.update_tree(data["tree_id"], changes, operator)
            updated += 1
        return added, updated, skipped

    def export_trouble_trees(self):
        """导出全部排查树（.nlb 三库之一）"""
        out = []
        for t in self.all_trees():
            t = dict(t)
            t["vendor_hint"] = self._dump_json(t.get("vendor_hint") or [])
            t["steps"] = self._dump_json(t.get("steps") or [])
            out.append({f: t.get(f, "") for f in TREE_FIELDS})
        return out

    # ------------------------------------------------------------------
    # 报错字典 / 待解决收件箱（模块 05）
    # ------------------------------------------------------------------
    def _normalize_err(self, raw):
        data = {}
        for f in ERR_FIELDS:
            data[f] = raw.get(f, "")
        data["err_id"] = str(data["err_id"] or new_uuid())
        for jf in ("solution_steps", "examples"):
            v = raw.get(jf) or []
            if not isinstance(v, list):
                v = load_list(v)
            data[jf] = self._dump_json(v)
        data["vendor"] = normalize_vendor(data.get("vendor"))
        data["os_family"] = normalize_os(data.get("os_family"))
        data["hit_count"] = int(data.get("hit_count") or 0)
        data["verified"] = 1 if str(data.get("verified", 0)) in ("1", "True", "true") else 0
        # exec_level：报错条目可用性标记（批2B），脏值回落为 ''
        data["exec_level"] = str(data.get("exec_level") or "").strip()
        if data["exec_level"] not in EXEC_LEVELS:
            data["exec_level"] = ""
        for f in ERR_FIELDS:
            if data[f] is None:
                data[f] = ""
        now = now_str()
        data["created_at"] = data["created_at"] or now
        data["updated_at"] = data["updated_at"] or now
        return data

    @staticmethod
    def _err_to_dict(row):
        d = dict(row)
        d["solution_steps"] = load_list(d.get("solution_steps"))
        d["examples"] = load_list(d.get("examples"))
        d["verified"] = int(d.get("verified") or 0)
        d["hit_count"] = int(d.get("hit_count") or 0)
        return d

    def all_errs(self, vendor=None, category=None):
        """全部字典条目（按命中次数降序 → 分类）"""
        sql = "SELECT * FROM err_dict WHERE 1 = 1"
        args = []
        if vendor:
            sql += " AND vendor = ?"
            args.append(normalize_vendor(vendor))
        if category:
            sql += " AND category = ?"
            args.append(category)
        sql += " ORDER BY hit_count DESC, category, err_id"
        return [self._err_to_dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def get_err(self, err_id):
        cur = self.conn.execute("SELECT * FROM err_dict WHERE err_id = ?", (err_id,))
        row = cur.fetchone()
        return self._err_to_dict(row) if row else None

    def count_errs(self):
        """报错字典数量；只读库缺 err_dict 表时返回 0（原因同 count_trees）"""
        if "err_dict" in self.missing_tables:
            return 0
        return int(self.conn.execute("SELECT count(*) FROM err_dict").fetchone()[0])

    def add_err(self, raw, operator=""):
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法新增字典条目")
        data = self._normalize_err(raw)
        data["verified"] = 0
        cols = ",".join(ERR_FIELDS)
        marks = ",".join(["?"] * len(ERR_FIELDS))
        self.conn.execute("INSERT INTO err_dict (%s) VALUES (%s)" % (cols, marks),
                          [data[f] for f in ERR_FIELDS])
        self.conn.commit()
        self.log_history("err_dict", data["err_id"], "create", "", data["cause"][:80], operator)
        return data["err_id"]

    def update_err(self, err_id, changes, operator=""):
        """
        按字段局部更新字典条目。

        ★ 与 update_entry 对齐（此前两者语义不一致，是实际缺陷）：
          · solution_steps / examples 允许直接传 list —— 内部序列化成 JSON 存储；
            此前直接绑进 SQL 会抛 sqlite3.ProgrammingError: type 'list' is not supported
          · vendor / os_family 归一化成小写 slug —— 此前传中文名会原样入库，
            破坏"库里只存 slug"的约定，导致按厂商联动查不到
          · 无实际变更（未给可更新字段，或值与库内完全一致）返回 False，
            与 update_entry 一致；顺带避免写出一条什么都没改的 history
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法修改字典条目")
        old = self.get_err(err_id)
        if not old:
            raise KeyError("字典条目不存在：%s" % err_id)

        sets, args, diffs = [], [], []
        for f, v in changes.items():
            if f not in ERR_FIELDS or f in ("err_id", "created_at"):
                continue
            # 当前值：solution_steps / examples 在 get_err 里已反序列化成 list，
            # 比较前要统一成同一种形态，否则"没改"会被误判成"改了"
            base = old.get(f, "")
            if f in ("solution_steps", "examples"):
                if not isinstance(base, list):
                    base = load_list(base)
                base = self._dump_json(base)
                if not isinstance(v, list):
                    v = load_list(v)
                v = self._dump_json(v)
            elif f == "vendor":
                v = normalize_vendor(v)
            elif f == "os_family":
                v = normalize_os(v)
            elif f == "hit_count":
                v = int(v or 0)
            elif f == "verified":
                v = 1 if str(v) in ("1", "True", "true") else 0
            elif v is None:
                v = ""

            if str(base) != str(v):
                sets.append("%s = ?" % f)
                args.append(v)
                # 留痕要能看到"改了什么、原值是什么"（第 3 轮 P2-3）
                diffs.append("%s: %r -> %r" % (f, base, v))

        if not sets:
            return False
        sets.append("updated_at = ?")
        args.append(now_str())
        args.append(err_id)
        self.conn.execute("UPDATE err_dict SET %s WHERE err_id = ?" % ",".join(sets), args)
        self.conn.commit()
        self.log_history("err_dict", err_id, "update",
                         " | ".join(diffs)[:500], "; ".join(diffs)[:500], operator)
        return True

    def delete_err(self, err_id, operator=""):
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法删除字典条目")
        exist = self.get_err(err_id)
        self.conn.execute("DELETE FROM err_dict WHERE err_id = ?", (err_id,))
        self.conn.commit()
        if exist:
            self.log_history("err_dict", err_id, "delete", exist.get("cause", "")[:80], "", operator)

    def increment_err_hit(self, err_id):
        """命中即计数（排序用）"""
        if self.readonly:
            return
        self.conn.execute("UPDATE err_dict SET hit_count = hit_count + 1 WHERE err_id = ?",
                          (err_id,))
        self.conn.commit()

    def mark_err_verified(self, err_id, verified_by, verified_model, verified_date, operator=""):
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法标记验证")
        self.conn.execute(
            "UPDATE err_dict SET verified = 1, verified_by = ?, verified_model = ?, "
            "verified_date = ?, updated_at = ? WHERE err_id = ?",
            (verified_by, verified_model, verified_date, now_str(), err_id))
        self.conn.commit()
        self.log_history("err_dict", err_id, "verify", "",
                         "%s / %s / %s" % (verified_by, verified_model, verified_date), operator)

    def import_errs(self, errs, merge=True, operator="import"):
        """导入报错字典（按 err_id 合并去重，冲突保留 verified=1）。返回 (新增, 更新, 跳过)"""
        added = updated = skipped = 0
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法导入报错字典")
        for raw in errs or []:
            if not isinstance(raw, dict) or not (raw.get("err_id") or "").strip():
                skipped += 1
                continue
            data = self._normalize_err(raw)
            exist = self.get_err(data["err_id"])
            if not exist:
                data["verified"] = 0
                cols = ",".join(ERR_FIELDS)
                marks = ",".join(["?"] * len(ERR_FIELDS))
                self.conn.execute("INSERT INTO err_dict (%s) VALUES (%s)" % (cols, marks),
                                  [data[f] for f in ERR_FIELDS])
                self.conn.commit()
                self.log_history("err_dict", data["err_id"], "import", "",
                                 data["cause"][:80], operator)
                added += 1
                continue
            if not merge:
                skipped += 1
                continue
            if int(exist.get("verified") or 0) == 1 and int(data.get("verified") or 0) == 0:
                skipped += 1
                continue
            changes = {}
            for f in ERR_FIELDS:
                if f in ("err_id", "created_at", "updated_at"):
                    continue
                if cmp_value(data[f]) != cmp_value(exist.get(f)):
                    changes[f] = data[f]
            if not changes:
                skipped += 1
                continue
            self.update_err(data["err_id"], changes, operator)
            updated += 1
        return added, updated, skipped

    # ---- 待解决收件箱 ----
    # ★ 三个写动作都要落 history（第 3 轮 P2-2）：
    #   收件箱是现场积累的团队资产，被转成字典或清理掉之后必须能追溯。
    def add_unresolved(self, pasted_text, guessed_vendor=""):
        """零匹配的报错存入待解决（界面不报错）"""
        if self.readonly:
            return None
        self.conn.execute(
            "INSERT INTO err_unresolved (pasted_text, guessed_vendor, ts, status) "
            "VALUES (?,?,?, 'pending')",
            (pasted_text[:4000], guessed_vendor, now_str()))
        self.conn.commit()
        new_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.log_history("err_unresolved", new_id, "create", "",
                         "厂商 %s ｜ %s" % (guessed_vendor or "-",
                                          (pasted_text or "").strip().splitlines()[0][:60]
                                          if (pasted_text or "").strip() else ""))
        return new_id

    def list_unresolved(self, status="pending", limit=200):
        sql = "SELECT * FROM err_unresolved"
        args = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def resolve_unresolved(self, unresolved_id, err_id):
        """收件箱条目已转为字典条目 → 标 resolved 并关联"""
        if self.readonly:
            return
        self.conn.execute(
            "UPDATE err_unresolved SET status = 'resolved', resolved_err_id = ? WHERE id = ?",
            (err_id, unresolved_id))
        self.conn.commit()
        self.log_history("err_unresolved", unresolved_id, "update",
                         "pending", "resolved → 字典条目 %s" % (err_id or ""))

    def delete_unresolved(self, unresolved_id):
        if self.readonly:
            return
        old = None
        for r in self.conn.execute("SELECT * FROM err_unresolved WHERE id = ?",
                                   (unresolved_id,)).fetchall():
            old = dict(r)
        self.conn.execute("DELETE FROM err_unresolved WHERE id = ?", (unresolved_id,))
        self.conn.commit()
        if old is not None:
            self.log_history("err_unresolved", unresolved_id, "delete",
                             "%s ｜ %s" % (old.get("status") or "",
                                          (old.get("pasted_text") or "")[:60]), "")

    # ---- 收件箱的导出 / 导入（第 3 轮 缺口C：让收件箱随 .nlb 在团队间同步）----
    UNRESOLVED_EXPORT_FIELDS = ("id", "pasted_text", "guessed_vendor", "ts",
                                "status", "resolved_err_id")

    def export_unresolved(self):
        """导出全部收件箱记录（供 .nlb 使用）"""
        return [{f: r.get(f) if r.get(f) is not None else ""
                 for f in self.UNRESOLVED_EXPORT_FIELDS}
                for r in self.list_unresolved(status=None, limit=100000)]

    def import_unresolved(self, items, operator="import"):
        """
        按 id 合并导入收件箱记录，返回 (新增, 更新, 跳过)。

        合并规则（第 3 轮 缺口C 规格）：
            · 库内没有该 id                 → 插入（保留来件状态）
            · 来件 pending、库内 resolved   → 改回 pending（避免把现场"未解决"的报错丢掉）
            · 库内 pending                  → 保留库内（pending 优先，不被来件覆盖）
            · 双方都 resolved               → 以库内为准，跳过
        """
        added = updated = skipped = 0
        if self.readonly:
            return (0, 0, len(items or []))
        for rec in items or []:
            if not isinstance(rec, dict):
                continue
            try:
                rid = int(rec.get("id"))
            except (TypeError, ValueError):
                skipped += 1
                continue
            row = self.conn.execute("SELECT * FROM err_unresolved WHERE id = ?",
                                    (rid,)).fetchone()
            exist = dict(row) if row else None
            text = str(rec.get("pasted_text") or "")
            vendor = str(rec.get("guessed_vendor") or "")
            ts = str(rec.get("ts") or "") or now_str()
            st = str(rec.get("status") or "pending").lower()
            st = "resolved" if st == "resolved" else "pending"

            if exist is None:
                self.conn.execute(
                    "INSERT INTO err_unresolved (id, pasted_text, guessed_vendor, ts, "
                    "status, resolved_err_id) VALUES (?,?,?,?,?,?)",
                    (rid, text, vendor, ts, st, str(rec.get("resolved_err_id") or "")))
                self.conn.commit()
                self.log_history("err_unresolved", rid, "import", "",
                                 "厂商 %s ｜ %s" % (vendor or "-", text[:60]), operator)
                added += 1
                continue

            if st == "pending" and (exist.get("status") or "") != "pending":
                self.conn.execute(
                    "UPDATE err_unresolved SET status = 'pending', resolved_err_id = '' "
                    "WHERE id = ?", (rid,))
                self.conn.commit()
                self.log_history("err_unresolved", rid, "update", exist.get("status"),
                                 "pending（导入时来件仍未解决，优先保留）", operator)
                updated += 1
                continue

            # 库内已是 pending（pending 优先）或双方都 resolved（库内为准）
            skipped += 1
        return (added, updated, skipped)

    def export_err_dict(self):
        """导出全部报错字典（.nlb 三库之一）"""
        out = []
        for e in self.all_errs():
            e = dict(e)
            for jf in ("solution_steps", "examples"):
                e[jf] = self._dump_json(e.get(jf) or [])
            out.append({f: e.get(f, "") for f in ERR_FIELDS})
        return out

    def resolve_cmd_ref(self, ref, vendor=None, os_family=None):
        """
        把树步骤的 cmd_ref 解析成命令库条目（渲染引擎的核心）。
        解析不到、或存在歧义，一律返回 None。详见 resolve_cmd_ref_ex()。
        """
        return self.resolve_cmd_ref_ex(ref, vendor=vendor, os_family=os_family)[0]

    def resolve_cmd_ref_ex(self, ref, vendor=None, os_family=None):
        """
        同 resolve_cmd_ref，但额外给出失败原因，便于 UI 精准提示。
        返回 (条目|None, 原因)，原因取值：
            ""              解析成功
            "bad_ref"       ref 形态不对（缺 category）
            "no_candidates" 该厂商 + 该场景下没有条目
            "ambiguous"     有多条候选，无法确定唯一一条

        ref 形态：
            {"uuid": "..."}                       → 直接按 uuid 取（最可靠，推荐）
            {"vendor_category": "VLAN",
             "title_keyword": "创建 VLAN"}         → 按厂商 + 场景分类 + 标题关键字找
            {"vendor_category": "OSPF",
             "os_family": "vrp5"}                 → 再用 OS 版本族收敛（区分同厂商多产品线）

        ★ 确定性要求：
          旧实现把 search() 的结果直接取 [0]，而 search() 的排序里含
          favorite / verified / updated_at —— 只要有人收藏或标记验证了同分类下的
          另一条条目，排查向导就会在**零告警**的情况下渲染出另一条命令。
          现在的顺序：按 (title, uuid) 稳定排序 → title_keyword 收敛 →
          os_family 收敛 → 剔除"待真机核对"骨架条目 → 若仍有多条候选，
          返回 (None, "ambiguous")，由 UI 明确告警，绝不瞎猜。
        """
        if not isinstance(ref, dict):
            return None, "bad_ref"
        if ref.get("uuid"):
            entry = self.get_entry(ref["uuid"])
            return (entry, "") if entry else (None, "no_candidates")

        vendor_slug = normalize_vendor(vendor) if vendor else ""

        # ⓪ 按厂商的 uuid 硬引用（收尾动作③）：
        #    一棵树的 cmd_ref 被 vendor_hint 里的所有厂商共享，单靠 vendor_category +
        #    title_keyword 匹配本质上是脆弱的（条目改名/分类调整就会漂移）。
        #    这里优先取 by_vendor[厂商].uuid；某厂商没登记时继续走下面的模糊匹配兜底，
        #    因此老数据无需改动即可继续工作（向后兼容）。
        by_vendor = ref.get("by_vendor")
        if isinstance(by_vendor, dict) and vendor_slug:
            hit = by_vendor.get(vendor_slug)
            if isinstance(hit, dict) and hit.get("uuid"):
                entry = self.get_entry(hit["uuid"])
                return (entry, "") if entry else (None, "no_candidates")

        category = ref.get("vendor_category") or ref.get("category")
        if not category:
            return None, "bad_ref"

        if vendor_slug:
            candidates = self.search(vendor=vendor_slug, category=category)
            if not candidates:
                # ★ 指定了厂商就不许"拿别家的条目来凑"：旧实现在此回退到全厂商查找，
                #   后果一是把别的厂商的命令渲染给当前设备，二是候选变多必然多义、
                #   把"该厂商缺此分类条目"错报成"引用歧义"。这里直接如实返回无候选。
                return None, "no_candidates"
        else:
            candidates = self.search(category=category)
            if not candidates:
                return None, "no_candidates"

        # ① 稳定排序：彻底剔除 favorite / verified / updated_at 对结果的影响，
        #    uuid 作末位 tiebreaker，保证同一份数据任何时候结果完全一致
        candidates = sorted(candidates,
                            key=lambda e: (e.get("title") or "", e.get("uuid") or ""))

        # ② 标题关键字收敛（命中 0 条时保留分类候选，由第 ⑤ 步的唯一性判断兜底）
        kw = (ref.get("title_keyword") or "").strip().lower()
        if kw:
            hit = [e for e in candidates if kw in (e.get("title") or "").lower()]
            if hit:
                candidates = hit

        # ③ OS 版本族收敛（有精确匹配才收）
        #    参数 os_family 优先；ref 里显式声明的 os_family 次之。
        #    后者用于"同厂商多产品线"：华为 S 系列(vrp5) 与 AR 系列(vrp8) 在同一场景下
        #    各有一条条目，靠标题子串去猜既脆弱又不可读，改为由数据显式声明用哪一条。
        #    对 os_family 不匹配的厂商，"有精确匹配才收"的规则会自然跳过，
        #    所以给共享的 cmd_ref 加 os_family 不会影响同一棵树里的其他厂商。
        os_want = os_family or ref.get("os_family")
        if os_want:
            os_slug = normalize_os(os_want)
            exact = [e for e in candidates if normalize_os(e.get("os_family")) == os_slug]
            if exact:
                candidates = exact

        # ④ 带"待真机核对"标记的骨架条目排后（存在真实条目时才剔除）
        real = [e for e in candidates if "待真机核对" not in (e.get("title") or "")]
        if real:
            candidates = real

        # ⑤ 唯一性：仍然多义就报歧义，让 UI 明确告警而不是悄悄换一条
        if len(candidates) > 1:
            return None, "ambiguous"
        return candidates[0], ""

    def trees_for_category(self, category):
        """给命令库详情面板的[去排查树]按钮用：按现象分类/厂商找相关树"""
        trees = self.all_trees()
        if not category:
            return trees
        hit = [t for t in trees if t.get("category") == category]
        if hit:
            return hit
        # 按厂商再找一次
        return trees



    # ==================================================================
    # AI 产出入库（阶段二）：溯源 + 三去向数据层
    #   幂等约定：ai_imports 里 (session_id, message_index, block_index)
    #   唯一定位一个"AI 回复中的块"；入库前 UI 先查 get_ai_import，
    #   已入库的块默认不勾选（重复提交不产生重复数据）。
    # ==================================================================
    def _insert_ai_import(self, session_id, message_index, block_index,
                          target_type, target_uuid, operator="", model="",
                          dup_of_uuid=""):
        """写一条溯源记录（不自行 commit，由调用方事务决定；现场留痕允许失败静默）"""
        if self.readonly or self.closed:
            return
        try:
            self.conn.execute(
                "INSERT INTO ai_imports (session_id, message_index, block_index, "
                "target_type, target_uuid, imported_at, operator, model, dup_of_uuid) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (str(session_id or ""),
                 int(message_index) if message_index is not None else -1,
                 int(block_index) if block_index is not None else -1,
                 str(target_type or "entry"), str(target_uuid or ""),
                 now_str(), str(operator or ""), str(model or ""),
                 str(dup_of_uuid or "")),
            )
        except sqlite3.Error:
            pass

    def log_ai_import(self, session_id, message_index, block_index, target_type,
                      target_uuid, operator="", model="", dup_of_uuid=""):
        """公共溯源写入入口（"仍入库/替换"决策时 UI 侧用，dup_of_uuid 指向既有条目）；
        自行 commit。返回是否成功。"""
        if self.readonly or self.closed:
            return False
        self._insert_ai_import(session_id, message_index, block_index,
                               target_type, target_uuid, operator, model, dup_of_uuid)
        try:
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def get_ai_import(self, session_id, message_index, block_index, target_type=None):
        """按块定位溯源记录（幂等判断用）。返回 dict 或 None"""
        if "ai_imports" in getattr(self, "missing_tables", []):
            return None
        sql = ("SELECT * FROM ai_imports WHERE session_id = ? AND message_index = ? "
               "AND block_index = ?")
        args = [str(session_id or ""), int(message_index), int(block_index)]
        if target_type:
            sql += " AND target_type = ?"
            args.append(str(target_type))
        sql += " ORDER BY id DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def list_ai_imports(self, session_id):
        """某会话的全部溯源记录（id 升序）"""
        if "ai_imports" in getattr(self, "missing_tables", []):
            return []
        rows = self.conn.execute(
            "SELECT * FROM ai_imports WHERE session_id = ? ORDER BY id",
            (str(session_id or ""),)).fetchall()
        return [dict(r) for r in rows]

    def ai_import_stats(self, session_id):
        """按去向分列统计：{"entry": n, "err": n, "tree": n}（历史抽屉"已入库 N/M 块"用）"""
        stats = {"entry": 0, "err": 0, "tree": 0}
        for rec in self.list_ai_imports(session_id):
            key = rec.get("target_type") or "entry"
            stats[key] = stats.get(key, 0) + 1
        return stats

    def imports_for_target(self, target_uuid):
        """某目标（条目/报错/树）的全部 AI 溯源记录（id 降序；详情溯源展示/跳转用）"""
        if "ai_imports" in getattr(self, "missing_tables", []):
            return []
        rows = self.conn.execute(
            "SELECT * FROM ai_imports WHERE target_uuid = ? ORDER BY id DESC",
            (str(target_uuid or ""),)).fetchall()
        return [dict(r) for r in rows]

    def ai_imported_uuids(self):
        """已入库目标的 uuid 集合（命令库列表 🤖 徽标用，一次查询避免逐条命中）"""
        if "ai_imports" in getattr(self, "missing_tables", []):
            return set()
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT target_uuid FROM ai_imports WHERE target_type = 'entry'"
            ).fetchall()
        except sqlite3.Error:
            return set()
        return {r["target_uuid"] for r in rows if r["target_uuid"]}

    def get_target_by_import(self, rec):
        """溯源记录 → 当前目标对象（条目/报错/树）；目标已删除返回 None。
        溯源双向跳转（条目详情 ↔ 会话）用。"""
        ttype, tuid = rec.get("target_type"), rec.get("target_uuid")
        if not tuid:
            return None
        if ttype == "err":
            return self.get_err(tuid)
        if ttype == "tree":
            return self.get_tree(tuid)
        return self.get_entry(tuid)

    # ---- 归一化规则（查重/幂等比对用）----
    @staticmethod
    def normalize_commands_text(text):
        """命令文本归一化（统一委托 dedupe 引擎）：去首尾空白、去空行、去 !/# 注释行、
        压缩行内连续空白、统一小写后比对"""
        return dedupe.normalize_text(text, lower=True)

    @staticmethod
    def normalize_error_text(text):
        """报错原文归一化（统一委托 dedupe 引擎）：去全部空白 + 统一小写后比对"""
        return dedupe.normalize_error_text(text, lower=True)

    def find_entry_by_commands(self, commands, vendor=None):
        """
        按归一化 commands 查重（去空白、去注释行后比对）。
        返回命中的条目 dict 或 None。vendor 给定则限定厂商。
        """
        norm = self.normalize_commands_text(commands)
        if not norm:
            return None
        sql = "SELECT * FROM entries"
        args = []
        if vendor:
            sql += " WHERE vendor = ?"
            args.append(normalize_vendor(vendor))
        for row in self.conn.execute(sql, args).fetchall():
            if self.normalize_commands_text(row["commands"]) == norm:
                return self.get_entry(row["uuid"])
        return None

    def find_err_by_text(self, raw_text):
        """
        报错原文归一化查重：与库内 pattern（转义正则 → 还原字面）及 examples 比对。
        返回 (命中条目 dict 或 None, 命中位置描述)。
        """
        norm = self.normalize_error_text(raw_text)
        if not norm:
            return None, ""
        for err in self.all_errs():
            # pattern 存的是按词转义、词间 \s* 的容错正则：
            # 还原字面 = 去掉 \s* 连接符 + 去掉转义反斜杠，再走归一化比对
            literal = re.sub(r"\\s\*", "", err.get("pattern") or "").replace("\\", "")
            if self.normalize_error_text(literal) == norm:
                return err, "报错原文"
            for ex in err.get("examples") or []:
                if self.normalize_error_text(ex) == norm:
                    return err, "样例"
        return None, ""

    # ---- 去向 1：命令库（合并单条目批量入库，单事务）----
    def add_entries_from_ai(self, items, meta=None):
        """
        AI 产出入命令库：单事务批量，任一条失败整体回滚并抛出带序号的错误。
        items: [entry dict]，可携带 _block_index（该条目来自回复的第几个命令块）。
        meta:  {session_id, message_index, operator, model}
        返回入库 uuid 列表（与 items 等长同序）。verified 一律强制 0。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法入库")
        meta = meta or {}
        items = [it for it in (items or []) if isinstance(it, dict)]
        if not items:
            raise ValueError("没有可入库的条目")
        uuids = []
        idx = -1
        try:
            for idx, raw in enumerate(items):
                block_index = raw.pop("_block_index", -1)
                block_indexes = None
                if isinstance(block_index, (list, tuple)):
                    # 合并单条目：一条 entry 覆盖多个块 → 入库后逐块各写一条溯源
                    block_indexes = [int(bi) if bi is not None else -1
                                     for bi in block_index]
                    block_index = None
                elif block_index is None:
                    block_index = -1
                data = self._normalize_entry(raw)
                data["verified"] = 0
                data["verified_by"] = ""
                data["verified_model"] = ""
                data["verified_date"] = ""
                cols = ",".join(ENTRY_FIELDS)
                marks = ",".join(["?"] * len(ENTRY_FIELDS))
                self.conn.execute("INSERT INTO entries (%s) VALUES (%s)" % (cols, marks),
                                  [data[f] for f in ENTRY_FIELDS])
                # history：直接内联写（log_history 会 commit，破坏单事务语义）
                if not (self.readonly or self.closed):
                    try:
                        self.conn.execute(
                            "INSERT INTO history (table_name, record_id, entry_uuid, "
                            "action, old_value, new_value, operator, ts) "
                            "VALUES ('entries', ?, ?, 'create', '', ?, ?, ?)",
                            (data["uuid"], data["uuid"], data["title"][:80],
                             meta.get("operator") or "ai-import", now_str()))
                    except sqlite3.Error:
                        pass
                dup_of = meta.get("dup_of_uuid") or ""
                if block_indexes is not None:
                    for bi in block_indexes:
                        self._insert_ai_import(meta.get("session_id"),
                                               meta.get("message_index"),
                                               bi, "entry", data["uuid"],
                                               meta.get("operator"), meta.get("model"),
                                               dup_of)
                elif block_index is not None:
                    self._insert_ai_import(meta.get("session_id"), meta.get("message_index"),
                                           block_index, "entry", data["uuid"],
                                           meta.get("operator"), meta.get("model"), dup_of)
                uuids.append(data["uuid"])
            self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            raise RuntimeError("第 %d 条入库失败，已整体回滚：%s" % (idx + 1, exc))
        return uuids

    # ---- 去向 2：报错库 ----
    def add_err_dict_item(self, item, meta=None):
        """
        AI 产出入报错库。item: {raw_text(必填), cause, fix_commands, fix_template,
        solution_steps, category, vendor, os_family, block_index}
        报错原文转字面正则（re.escape）入库 → 报错诊断 Tab 可直接命中；
        examples 预置原文一条。返回 err_id。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法入库")
        meta = meta or {}
        raw_text = str(item.get("raw_text") or "").strip()
        if not raw_text:
            raise ValueError("报错原文为空，不允许提交")
        # pattern = 按词转义、词间以 \s* 连接的容错正则：
        # 报错诊断 Tab 用它对粘贴原文做正则匹配（空格数不一致也能命中）；
        # 查重侧 find_err_by_text 会还原字面再比对。
        pattern = item.get("pattern") or r"\s*".join(
            re.escape(tok) for tok in raw_text.split())
        examples = item.get("examples") or [raw_text]
        raw = {
            "vendor": item.get("vendor") or "",
            "os_family": item.get("os_family") or "",
            "pattern": pattern,
            "category": item.get("category") or "",
            "cause": item.get("cause") or "",
            "solution_steps": item.get("solution_steps") or [],
            "fix_template": item.get("fix_template") or "",
            "examples": examples,
        }
        err_id = self.add_err(raw, meta.get("operator") or "ai-import")
        self._insert_ai_import(meta.get("session_id"), meta.get("message_index"),
                               item.get("block_index", -1), "err", err_id,
                               meta.get("operator"), meta.get("model"),
                               meta.get("dup_of_uuid") or "")
        self.conn.commit()
        return err_id

    # ---- 去向 3：排查树 ----
    def create_trouble_tree(self, tree, meta=None):
        """
        AI 产出入排查树（新建）。tree: {symptom, vendor_hint, category, steps}
        走 add_tree 管线（verified 强制 0），并写 ai_imports 溯源。返回 tree_id。
        ★ 步骤里的命令必须先落成命令库条目、用 cmd_ref={"uuid": …} 引用
          （与树编辑器/排查向导读写完全兼容，严禁在树里硬编码命令文本）。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法入库")
        meta = meta or {}
        tree_id = self.add_tree(tree, meta.get("operator") or "ai-import")
        self._insert_ai_import(meta.get("session_id"), meta.get("message_index"),
                               meta.get("block_index", -1), "tree", tree_id,
                               meta.get("operator"), meta.get("model"))
        self.conn.commit()
        return tree_id

    def append_steps(self, tree_id, steps, after_step_id=None, operator="", rewire=None):
        """
        向现有树追加步骤（程序化建树 API 的另一半）。
        steps: 步骤 dict 列表，id 可用占位（"__new1"…），branches.goto 引用占位
               或既有 id，本方法统一分配真实 id（s{n}，避开库内已有）并重映射。
        after_step_id: 插入到该步骤之后；None = 追加到末尾。
        rewire: (old_step_id, when_keyword) 可选 —— 把 old_step_id 的分支中
                when 含该关键字的 goto 改指向第一个新步骤（线性树接线用：
                原步骤的"正常"分支原本指向叶子，插入新链后须改指新步骤）。
        返回新步骤的真实 id 列表。原步骤除被 rewire 的分支接线外不受影响。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法修改排查树")
        tree = self.get_tree(tree_id)
        if not tree:
            raise RuntimeError("排查树不存在：%s" % tree_id)
        old_steps = [dict(s) for s in (tree.get("steps") or [])]
        existing_ids = set(s.get("id") or "" for s in old_steps)
        # 分配新 id：s1…sN 跳过已有（含非 s 前缀的自定义 id）
        seq = 1
        while ("s%d" % seq) in existing_ids:
            seq += 1
        mapping = {}
        allocated = []
        for s in steps or []:
            if not isinstance(s, dict):
                continue
            s = dict(s)
            temp = s.get("id")
            new_id = "s%d" % seq
            seq += 1
            s["id"] = new_id
            if temp:
                mapping[temp] = new_id
            # 分支 goto 重映射（占位 → 真实 id；既有 id 原样保留）
            for b in s.get("branches") or []:
                if b.get("goto") in mapping:
                    b["goto"] = mapping[b["goto"]]
            allocated.append(s)
        # 插入位置
        if after_step_id:
            pos = next((i for i, s in enumerate(old_steps)
                        if s.get("id") == after_step_id), None)
            if pos is None:
                raise RuntimeError("插入位置步骤不存在：%s" % after_step_id)
            new_steps = old_steps[:pos + 1] + allocated + old_steps[pos + 1:]
        else:
            new_steps = old_steps + allocated
        # 线性树接线：原步骤的"正常"分支改指第一个新步骤
        if rewire and allocated:
            rid, keyword = rewire
            for s in new_steps:
                if s.get("id") != rid:
                    continue
                for b in s.get("branches") or []:
                    if keyword in (b.get("when") or ""):
                        b["goto"] = allocated[0]["id"]
        self.update_tree(tree_id, {"steps": new_steps}, operator=operator)
        return [s["id"] for s in allocated]

    # ==================================================================
    # 入库查重增强（三级查重：精确 → 相似度 → 用户决策）
    #   相似度算法与归一化统一在 dedupe.py（零依赖标准库）；本类负责扫描。
    #   无命中时行为与阶段二完全一致（find_entry_by_commands /
    #   find_err_by_text 保留，作为精确级快速路径）。
    # ==================================================================
    def find_duplicates(self, kind, payload, threshold=0.80):
        """
        查重候选检索：返回相似度 ≥ threshold 的候选，按相似度倒序限前 5 条。
            kind = "entry"  payload={"commands" 必填, "vendor"/"category" 可选分桶}
                 = "err"    payload={"raw_text" 必填}
                 = "tree"   payload={"symptom" 必填}
        每条候选：{主键字段, "title", "score", "level", "diff", "summary"}
            score = 相似度(0~1)；level = high(≥0.95)/warn(0.80~0.95)；diff = unified 预览
        性能：先用行集合 Jaccard 快筛（<0.45 直接放弃，跳过昂贵的 SequenceMatcher），
              数千条目毫秒级；破万建议调用方按 vendor/category 分桶后分批调用。
        """
        payload = payload or {}
        results = []

        if kind == "entry":
            new_text = str(payload.get("commands") or "")
            if not dedupe.normalize_text(new_text):
                return []
            sql, args = "SELECT uuid, title, commands, vendor, category FROM entries", []
            if payload.get("vendor"):
                sql += " WHERE vendor = ?"
                args.append(normalize_vendor(payload["vendor"]))
            for row in self.conn.execute(sql, args).fetchall():
                # 快筛：Jaccard 低于保守下限直接跳过（免 SequenceMatcher）
                if dedupe.jaccard_lines(new_text, row["commands"]) < 0.45:
                    continue
                score = dedupe.command_similarity(new_text, row["commands"])
                if score >= threshold:
                    results.append({
                        "uuid": row["uuid"], "title": row["title"] or row["uuid"],
                        "score": score, "level": dedupe.verdict(score),
                        "diff": dedupe.unified_diff_preview(row["commands"], new_text),
                        "summary": "%s ｜ %s" % (row["vendor"] or "-", row["category"] or "-"),
                    })

        elif kind == "err":
            new_text = str(payload.get("raw_text") or "")
            if not dedupe.normalize_error_text(new_text):
                return []
            for row in self.conn.execute(
                    "SELECT * FROM err_dict").fetchall():
                err = self._err_to_dict(row)
                # 与 pattern 还原字面 及 examples 逐一比对，取最大
                candidates = [(err.get("pattern") or "").replace("\\", "")]
                candidates += [str(ex) for ex in (err.get("examples") or []) if str(ex).strip()]
                score = 0.0
                for cand in candidates:
                    if cand.strip():
                        score = max(score, dedupe.text_similarity(new_text, cand))
                if score >= threshold:
                    results.append({
                        "err_id": err["err_id"],
                        "title": (err.get("cause") or err["err_id"])[:60],
                        "score": score, "level": dedupe.verdict(score),
                        "diff": dedupe.unified_diff_preview(candidates[0], new_text),
                        "summary": "%s ｜ 命中 %d 次" % (
                            err.get("vendor") or "-", int(err.get("hit_count") or 0)),
                    })

        elif kind == "tree":
            new_text = str(payload.get("symptom") or "")
            if not dedupe.normalize_error_text(new_text):
                return []
            for tree in self.all_trees():
                score = dedupe.text_similarity(new_text, tree.get("symptom") or "")
                if score >= threshold:
                    results.append({
                        "tree_id": tree["tree_id"],
                        "title": tree.get("symptom") or tree["tree_id"],
                        "score": score, "level": dedupe.verdict(score),
                        "diff": dedupe.unified_diff_preview(tree.get("symptom") or "",
                                                            new_text),
                        "summary": "%d 步 ｜ %s" % (len(tree.get("steps") or []),
                                                    tree.get("category") or "-"),
                    })
        else:
            raise ValueError("未知查重对象类型：%s" % kind)

        results.sort(key=lambda r: -r["score"])
        return results[:dedupe.MAX_CANDIDATES]

    def update_entry_commands(self, entry_uuid, commands, meta=None):
        """
        查重"替换"决策用：整条更新 commands。
        ★ verified 强制归 0 并清空验证信息（内容变了，原验证即失效），history 记录。
        meta: {operator, dup_of_uuid, message}（dup_of_uuid 写入本次溯源的指向）
        返回是否发生实际更新。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法更新")
        meta = meta or {}
        entry = self.get_entry(entry_uuid)
        if not entry:
            raise KeyError("条目不存在：%s" % entry_uuid)
        old_commands = entry.get("commands") or ""
        new_commands = str(commands or "")
        if self.normalize_commands_text(old_commands) == self.normalize_commands_text(new_commands):
            return False
        self.update_entry(entry_uuid, {
            "commands": new_commands,
            "verified": 0,
            "verified_by": "",
            "verified_model": "",
            "verified_date": "",
        }, meta.get("operator") or "ai-import")
        self.log_history(
            "entries", entry_uuid, "update", old_commands[:80],
            "AI 查重替换（verified 归 0；dup_of_uuid=%s）%s"
            % (meta.get("dup_of_uuid") or "-",
               ("；%s" % meta["message"]) if meta.get("message") else ""),
            meta.get("operator") or "ai-import")
        return True

    def append_err_reason(self, err_id, reason_text, meta=None):
        """
        查重"追加原因分析"决策用（报错库专属）：把新原因追加到现有条目 cause 尾部。
        ★ 不动条目其它内容、不影响其验证状态；history 记录追加动作。
        返回是否发生实际追加。
        """
        if self.readonly:
            raise RuntimeError("命令库处于只读状态（U 盘写保护），无法更新")
        meta = meta or {}
        err = self.get_err(err_id)
        if not err:
            raise KeyError("字典条目不存在：%s" % err_id)
        reason = str(reason_text or "").strip()
        if not reason:
            return False
        old_cause = err.get("cause") or ""
        new_cause = (old_cause + "\n" + reason).strip()
        self.update_err(err_id, {"cause": new_cause}, meta.get("operator") or "ai-import")
        self.log_history(
            "err_dict", err_id, "update", old_cause[:80],
            "追加原因分析（AI 查重；验证状态不变；dup_of_uuid=%s）"
            % (meta.get("dup_of_uuid") or "-"),
            meta.get("operator") or "ai-import")
        return True
