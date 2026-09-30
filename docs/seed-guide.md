# 种子贡献指南 —— params 结构化参数写法（schema 2026-09-30）

> 受众：向 `app/seed_data/` 贡献条目的种子作者 ｜ 更新时机：schema 或校验规则变更时。
> **新条目一律使用结构化 params**；`validate-seed`（CI 硬门槛，EXIT=0 才能合入）会做
> 双向对账与字段合法性校验。贡献流程见根目录 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 一、params 字段总览

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 与命令里的 `{{name}}` 占位**同名**（小写下划线命名）；命令里引用了而 params 未声明 → ERROR |
| `type` | 结构化条目 ✅ | `string` / `int` / `enum` / `ip` / `flag` 五选一 |
| `required` | ✅ | `true` = 必填（UI 表单标红星，缺填阻止复制生成）；`false` = 选填 |
| `default` | ✅ | 默认值；非空时必须通过自身 type/choices/range 校验；required 且留空是合法形态（现场强制输入） |
| `description` | 结构化条目 ✅ | **写清三点：该参数控制什么、取值范围/常用值、安全注意**（影响转发、需 commit/save 生效等必须注明）。禁止复述参数名充数 |
| `example` | 建议 | 示例值（参数说明面板灰字展示） |
| `choices` | type=enum ✅ | 可选值全集（列表），UI 下拉框按它渲染 |
| `range` | 建议 | 数值范围提示，如 `"1-4094"`（type=int 时据此生成数字框上下限） |
| `label` | 建议 | 界面显示名（中文） |
| `validate` | 建议 | 细粒度校验规则：`vlan` / `ipv4` / `masklen` / `int:lo-hi` / `port` / `port_range` / `ifname` / `path` / `enum:a\|b\|c` / `regex:^…$`；显式写了一律优先于 type 推导 |

> 兼容约定：不带 `type` 的旧格式 spec 仍可通过 validate（存量 987 个正在迁移）；
> 新条目必须结构化。`desc_pending: true` 是迁移期标记（空 description 只报 WARNING），
> 新条目**不要带**这个标记——缺 description 直接 ERROR。

## 二、type 五类怎么选

| type | 用途 | 渲染语义 |
|---|---|---|
| `string` | 主机名、描述、文件路径等 | 文本输入框（validate 决定具体校验） |
| `int` | VLAN ID、timeout、cost 等 | 数字框（range 转上下限） |
| `enum` | 模式/角色等有限取值 | 下拉框（choices 全集） |
| `ip` | IPv4 地址 | 文本框 + IPv4 格式校验 |
| `flag` | 开关型：不勾选 → 占位从命令中**整体消失**；勾选 → 渲染 `on_value`（默认参数名本身，可用 `example`/`on_value` 指定渲染文本） | 勾选框 |

## 三、命令正文的 # 注释约定

命令 `commands` 中，关键步骤前加一行以 `#` 开头、独立成行的注释，说明该步做什么、
是否影响转发、是否需要 `commit`/`save` 才生效：

- 详情页/生成器中注释行按**灰斜体小字**渲染，一眼与命令区分；
- 「仅复制命令」会把注释行剔除，粘贴进 Xshell 直接可跑；
- Cisco/华为/H3C 等用 `!` 开头亦可（renderer 两种注释符都认），种子统一约定 **`#`**。

## 四、完整示例条目

```json
{
  "uuid": "e3971cfc-1d22-5542-89d5-7b72b2c3ee6a",
  "platform": "network",
  "device_type": "交换机",
  "vendor": "cisco",
  "os_family": "ios",
  "models": "Catalyst 2960 / 3560 / 3750 / 9300",
  "category": "VLAN",
  "title": "创建 VLAN 并把端口划入 Access",
  "description": "新建业务 VLAN 并命名，把一批物理端口批量划为 access 口加入该 VLAN。",
  "commands": "# ===== 创建 VLAN 并划入端口（改动即生效，write memory 前重启丢失）=====\nenable\nconfigure terminal\nvlan {{vlan_id}}\n name {{vlan_name}}\n exit\ninterface range {{port_range}}\n switchport mode access\n switchport access vlan {{vlan_id}}\n no shutdown\n exit\nend\nwrite memory",
  "params": [
    {
      "name": "vlan_id",
      "type": "int",
      "label": "VLAN ID",
      "required": true,
      "default": "10",
      "description": "新建 VLAN 的编号，控制该二层广播域的标识；取值 1-4094（1 和 1002-1005 为系统保留，勿用）；write memory 前仅 running-config 生效",
      "example": "10",
      "range": "1-4094",
      "validate": "vlan"
    },
    {
      "name": "vlan_name",
      "type": "string",
      "label": "VLAN 名称",
      "required": false,
      "default": "BUSINESS",
      "description": "VLAN 的可读名称，仅作运维辨识用途，不影响转发行为；建议用业务系统名缩写",
      "example": "OA_BIZ"
    },
    {
      "name": "port_range",
      "type": "string",
      "label": "端口范围",
      "required": true,
      "default": "",
      "description": "划入该 VLAN 的物理端口范围；支持 0/1-0/10 区间与逗号列表，生成器会按厂商命名规则展开；选错端口会把业务口误划进 VLAN，影响转发",
      "example": "0/1-0/10",
      "validate": "port_range",
      "expand": "port"
    },
    {
      "name": "with_desc",
      "type": "flag",
      "label": "附带端口描述",
      "required": false,
      "default": "",
      "description": "勾选后在每个端口下追加 description 标注所属 VLAN，便于 later 排查；不勾选则该占位不出现在命令里",
      "example": "description access-vlan"
    }
  ],
  "notes": "坑点：……",
  "verified": 0
}
```

## 五、命名 / 分类 / 厂商代码约定（从现有种子归纳，不新造）

**vendor（小写 slug）**：`cisco` / `huawei` / `h3c` / `juniper` / `ruijie` / `zte` /
`fortinet` / `paloalto` / `sangfor` / `topsec`；Linux 四系 `centos` / `ubuntu` / `kylin` /
`openeuler`；跨厂商通用条目用 `ops`。新厂商进 `seed_data/placeholders/` 对应目录，
slug 取目录名。

**os_family（小写 slug，允许 `|` 复合如 `vrp5|vrp8`）**：`ios` / `vrp5` / `vrp8` /
`comware7` / `junos` / `rgos` / `zxr10` / `fortios` / `panos` / `sangfor_af` / `topos`；
Linux 侧 `centos7` / `ubuntu2204` / `kylinV10` / `openeuler2203`，通用 Linux 用 `linux`。

**device_type**：网络侧 `交换机` / `路由器` / `防火墙`；Linux 侧 `服务器`（跨形态通用条目可留空）。

**category**：网络侧沿用十类骨架 `VLAN` / `Trunk` / `静态路由` / `OSPF` / `ACL` / `NAT` /
`SSH管理` / `密码` / `SNMP` / `保存配置`，扩展类 `接口诊断` / `BGP` / `DNS域名解析` /
`时间同步` / `版本升级` / `HA/双机` / `地址分配` / `配置回滚` / `AAA认证` / `防火墙诊断` /
`环路检测` / `传输工具`；Linux 侧 `网络配置` / `排查命令` / `本地源` / `防火墙` / `服务管理` /
`日志` / `磁盘` / `系统资源` / `进程管理` / `审计日志`。新分类先在 Issue 提出再入库。

**uuid**：uuidv5 风格小写（示例见第四节）；同一实体跨批次迁移**保持 uuid 不变**，
这是 `.nlb` 合并去重与主库同步的身份键。

## 六、validate-seed 质检门槛（规则摘要）

`python app/main.py --validate-seed` 逐条校验以下项，**任一 ERROR 即非零退出**：

- 必填字段非空；uuid 全局唯一；`verified` 不得误写 1（真机验证才回填）；
- `{{占位符}}` 与 params **双向对账**：命令引用未声明 → ERROR；声明未引用 → WARNING；
- 结构化字段合法性：type 五选一、enum 必带 choices、default 违反自身 type/choices/range → ERROR；
- 新条目 `description` 为空 → ERROR；`exec_level` 取值合法，commands 含"待真机核对"必须标 skeleton；
- 端口参数可展开、带默认值渲染后无残留占位、样例自测与重复导入幂等。

**EXIT=0 是 CI 合入与打包的硬门槛**——本地不过别提 PR，`scripts/build_exe.py` 也会先跑它。

## 七、红线

- **示例 IP 一律用文档专用段**：`192.0.2.0/24`（TEST-NET-1）、`198.51.100.0/24`、
  `203.0.113.0/24` 及 `10.0.0.0/8` 内的占位段（如 `10.255.x.x`）；禁止任何真实公网/内网地址；
- **default 禁真实值**：口令、团体字、密钥类参数 default 置空并 `required: true`
  （弱口令兜底已整改，勿回退）；
- **description 禁复述参数名充数**：必须写清"控制什么 / 取值范围 / 安全注意"三要素，
  写不出三要素说明你还没理解这个参数；
- 禁止收录任何现场真实回显、主机名、拓扑信息进种子。

## 八、提交前自查清单

- [ ] 每个命令里的 `{{占位}}` 都在 params 有同名声明（反向多声明只报 WARNING，建议清理）
- [ ] `type` 五选一；enum 必带 `choices`；int 建议 `range`
- [ ] `description` 三要素齐全（控制什么 / 取值范围 / 安全注意），没有复述参数名
- [ ] `default` 非空时能通过自身 type/choices/range 校验（`python app/main.py --validate-seed` 会替你查）
- [ ] 关键步骤有 `#` 注释行；影响转发 / 需 commit 生效的已注明
- [ ] 本地 `python app/main.py --validate-seed` 返回 EXIT=0
