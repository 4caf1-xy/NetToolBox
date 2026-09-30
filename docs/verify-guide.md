# 验证操作手册（verify-guide.md）

> 受众：执行真机/模拟器验证的工程师（含新会话 AI 协作执行） ｜ 更新时机：验证流程或判定标准变更时。
> 一场验证会的全流程：环境准备 → 导出清单 → 逐条执行 → 四档判定 → 回填 → 统计 → 伴生仓存档。
> 架构背景见 [architecture.md](architecture.md)，条目写法见 [seed-guide.md](seed-guide.md)。

## 一、环境映射表

| device_type | 建议验证环境 | 说明 |
|---|---|---|
| 交换机 | 网络模拟器（EVE-NG / GNS3 / Cisco IOSvL2、华为 eNSP、H3C HCL）或真机 | 二层特性（STP/VACL/端口展开）优先真机或高保真镜像 |
| 路由器 | 网络模拟器（IOSv / VRP / vSRX / vRouter）或真机 | OSPF/BGP 邻居类需至少双节点拓扑 |
| 防火墙 | 防火墙虚机（FortiGate VM / PAN-OS VM / 深信服 AF 虚拟化 / 天融信 虚拟化） | Web 控制台条目需对应版本的管理界面；CLI 骨架条目见下 |
| 服务器（Linux） | Linux 虚机（CentOS 7 / Ubuntu 22.04 / 麒麟 V10 / openEuler 22.03，VMware/ESXi），验证前打挂起快照 | 与现场主用发行版一致；涉及重启/网络瞬断的条目必须先拍快照 |

**骨架条目核对规则（exec_level: skeleton）**：与其他条目**同标准执行，不降级、不跳过**。
核对通过 → 按"绿"档晋升且 `exec_level` 升级为 `verified-cli`；核对发现不可执行 → 按
"红"档实录，骨架标注照旧保留直到修正。interactive 条目在控制台手工交互完成，不做脚本化渲染验证。

## 二、结果四档标准

| 档 | 判定条件 | 回填动作 | 判定示例 |
|---|---|---|---|
| **绿（验证通过）** | 命令在目标环境完整执行、输出与 notes 判读口径一致 | `verified=1` + 回填 `verified_by` / `verified_model` / `verified_date` | VLAN 条目实配后 `show vlan` 确认端口划入成功 |
| **语法核对** | 命令被设备接受（无语法错）但场景未完整复现，或仅能确认语法/模式正确 | notes 记录核对范围；`exec_level` 按实际情况升级（skeleton→verified-cli） | 堡垒机仅有只读账号，确认命令语法与帮助回显一致 |
| **红（失败）** | 报错、输出与预期不符或结论不成立 | notes **如实记录完整回显与现象**；报错本身有价值的转报错字典 err_unresolved 待解析 | `switchport access vlan 999` 在该型号报 `% Invalid input`（VRP5 实为 `port default vlan`） |
| **挂起** | 环境不具备 / 权限不足 / 需变更窗口 | **不动状态字段**，仅登记到挂起清单，下轮验证会再排 | 需要 reload 窗口的升级回滚条目，本轮窗口不够 |

判定纪律：**宁红勿绿**——证据不足一律降档处理；notes 是唯一事实记录，禁止事后美化。

## 三、操作流程

1. **导出清单**：从主库按本轮范围（厂商/分类/批次）导出待验证条目清单 CSV；
2. **前置备份**：回填前先备份主库（`python scripts/backup_db.py` 或手动拷贝
   `command_lib.db.bak-<日期>`）——回填失败可整体回滚；
3. **逐条执行**：按清单在映射环境中执行，每条当场记录四档判定与回显要点；
4. **回填 CSV**：按清单逐行回填结果，**字段名与回填工具 CSV 列严格一致**（核心列：
   `table` / `uuid` / `result` / `verified` / `verified_by` / `verified_model` /
   `verified_date` / `notes` / `exec_level`；以伴生仓回填工具当版文档为准，不得增删改名）；
5. **统计**：绿/语法核对/红/挂起四档计数 + 验证覆盖率（绿数/总数），写入本轮验证纪要；
6. **伴生仓存档**：证据（回显 .txt、截图）提交伴生私有仓 `nettoolbox-data` 的
   `docs/verify/<YYYYMMDD>-<场景>/`，提交打 tag `verify-YYYYMMDD-场景`（单写者纪律：db 与
   tag 只在主力机写）。

## 四、报错条目低成本复现技巧

| 报错类型 | 复现方法 |
|---|---|
| 账号锁定 | 交换机/防火墙 SSH 连续输错密码至 `retry exceeded`（先确认 lockout 阈值可恢复，避免真机长时间锁死） |
| DHCP 租约耗尽 | Linux 虚机起 dnsmasq，把 pool 掩码缩到 /30 后让第二个客户端请求，即得 `no address available` |
| DNS 指空 | `nmcli con mod <con> ipv4.dns 192.0.2.99`（TEST-NET 段）→ `resolvectl query example.com` 得超时/解析失败回显 |
| 端口 err-disable | 模拟器里对同一接口反复 `shutdown / no shutdown` 或触发 BPDU guard（开 bpduguard 的口接交换机链路） |
| 语法/权限类 | 用最低权限账号执行管理命令，即得 `% Permission denied` 类回显 |

报错回显采集后直接喂报错诊断 Tab 测 pattern 命中，命中失败说明字典正则要修。

## 五、种子晋升标准与流程

**标准**（全部满足才算晋升）：
1. 判定为**绿**档，且回显要点已写进 notes（判读口径可复述）；
2. 回填四元组齐全（verified/verified_by/verified_model/verified_date）；
3. 参数写法符合 [seed-guide](seed-guide.md)（结构化 params + `#` 注释行）；
4. 不含任何现场真实信息（IP/主机名/拓扑）。

**流程**：验证会闭环 → 单写者按晋升清单改 `app/seed_data/*.json`（verified=1 等字段）→
跑 `--validate-seed` EXIT=0 → 提交公开仓（CHANGELOG 记入 Unreleased）→ 同场证据已在
伴生仓 tag 存档。骨架条目晋升时同步 `exec_level: "verified-cli"`。

## 六、纪律引用

- 证据**只进伴生仓 `docs/verify/`**，公开仓零真实回显（AGENTS.md 第 3/4 节）；
- 主库 db 属运行时数据，不入公开仓 git；db 与 verify tag 遵守伴生仓**单写者纪律**；
- 本手册不复制 AGENTS/seed-guide 条文，冲突时以各权威出处为准。
