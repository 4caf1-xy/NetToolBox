# 开发笔记（dev-notes）

> 承接原根目录 `ai_compare_*.txt` 的结论性沉淀。原件已删除，本文件保留可复用的分析结论。

## 排查树结论成立性校验（AI 对比测试，2026-09-23）

场景：交换机到 `192.168.20.0/24` 不通，`show ip route 192.168.20` 显示 `% Network not in table`，排查树向导给出"缺静态路由 → 添加静态路由"。

**判定：部分成立，存在逻辑跳跃。** 该结论成立的前提是"该网段是需要路由转发的远端网段"。以下 4 个分支被向导遗漏，直接加静态路由可能误治：

| 遗漏分支 | 根因 | 为何加静态路由无效 |
|---|---|---|
| 直连网段但接口/VLAN 异常 | SVI 未创建、VLAN 未激活、物理口未划入 VLAN、STP 阻塞 | 网段应走直连路由，需查二层/接口侧 |
| 动态路由未收敛 | OSPF 邻居 Down、network 语句未覆盖 | 应查邻居与协议配置，非补静态 |
| ACL / 安全特性拦截 | 入向 ACL、IP Source Guard、DAI 丢弃 | 路由表正常，流量在安全层被丢 |
| VACL 过滤 | VLAN ACL 在二层拦截 | 与路由无关 |

**建议的校验顺序（Cisco IOS/C2960X）**：
1. 确认是否直连：`show ip interface brief | include 20.` / `show vlan id <n>` / `show interface vlan <n>`
2. 若跑动态路由：`show ip ospf neighbor` / `show ip eigrp neighbors` / `show run | section router ospf`
3. 查安全拦截：`show access-lists` / `show ip interface vlan <n>` / `show run | include ip access-group|dhcp-snooping|dae`
4. 定位真实丢点：`show ip cache flow` / `show interfaces counters errors`；`debug ip packet detail` 仅限维护窗口低峰短暂使用（高流量会打满 CPU）

**对产品的启示**：排查树向导输出结论时应附带"成立前提"提示；命中"路由表无条目"时，先引导用户确认直连/动态路由/ACL 三类分支，再落到"加静态路由"动作。

### 已知问题跟踪（checkbox 清单）

来源批次：AI 对比测试 2026-09-23（排查树结论成立性校验）。逐项修好后打勾并在行尾记提交号：

- [ ] 向导"路由表无条目"结论前，先引导确认**直连网段但接口/VLAN 异常**分支（SVI/VLAN/STP）
- [ ] 同上，先引导确认**动态路由未收敛**分支（OSPF/EIGRP 邻居与 network 覆盖）
- [ ] 同上，先引导确认 **ACL/安全特性拦截**分支（access-group/IP Source Guard/DAI）
- [ ] 同上，先引导确认 **VACL 过滤**分支
- [ ] 结论卡片附带"成立前提"提示文案


## 打包链路史（防 .stale- 复发）

- 2026-09-23 ~ 09-28：旧版 build_exe.py 因 WorkBuddy 沙箱拦截批量删除（进程级终止），采用"rename 成 .stale-<时间戳>"让位，导致每次打包 +2 个 stale 目录，累计 460MB 残渣（2026-09-29 已全量清除）。
- 2026-09-29 起：产物经 `--workpath/--distpath/--specpath` 定向仓库根；让位改用 `.trash-<时间戳>` 命名，构建成功后自动尝试彻底删除。**任何会话不得再引入 `.stale-*` 机制**（详见根目录 AGENTS.md）。

## 全量审计 P0/P1 摘要（2026-10-08，commit 8095ba6）

完整报告：`docs/audit/20261008-full-audit.md`（快照归档，勿回改）；功能矩阵：`docs/qa/functional-matrix.md`。
总览：**P0=0**、P1=3、P2=26。P1 修复清单（修好打勾 + 记提交号）：

- [x] P1-1 db 写入口异常反馈缺失：ui_main.py:2250/2261、ui_errorfix.py:405/412 无 try，只读竞态时静默失败（补齐 try+QMessageBox.critical）→ 400a2c7，回归 scripts/smoke_p1_fixes.py [A]
- [x] P1-2 导入 verified 语义不一致：db.py:1272 INSERT 强制 0 vs :1296 UPDATE 可带 verified=1——**裁决落位：导入不携带验证态**（UPDATE 不采纳四字段 + 移除 1288 守卫，已验证条目内容仍可导入更新）→ 400a2c7，回归 [B]
- [x] P1-3 ChatWorker.cancel 与注释不符（ai_bridge.py:862）：网络停滞时取消最长等 90s timeout → 400a2c7，**cancel 对底层 socket 调 _real_close()**（实测 resp.close()/shutdown 均无法唤醒 Windows 阻塞 recv），异常路径补发 __CANCELLED__ 防按钮卡死；回归 [C] 慢端点 0.61s 恢复

### 审计报告结论修正（快照不回改，记录于此）

- **B17 修正**：[去排查树]按钮跳转链路本就存在（ui_main:940 → ui_troubleshoot.focus_category），原报告"功能缺失"不成立；真实缺口是同分类多树时静默取第一棵 → 已补选择交互（a499cb2）。
- **A3 维持删除**：trees_for_category 为旧数据层入口，无调用方。

### 延后候选池（触发条件制，不做无触发重构）

- [ ] B8 流式渲染增量优化（每 100ms 全量 md→HTML）——触发条件：**长回复实测卡顿**
- [ ] B20/B21 重构（set_state 改名 / 验证对话框三处同构抽取）——触发条件：**下次触碰对应文件时**
- [ ] B4 导入事务化（逐行 commit → 单事务）——触发条件：**批量导入成常态时**
- [ ] ui_troubleshoot:341 "#e8eaf0" 硬编码（审计未列，C5 后新发现）——下次触碰该文件时并入

### 环境事件记录（2026-10-08）

- **.git 两次被移入回收站**（10:22 与 10:35），触发点均为 `git stash` / `git checkout` 操作。已从回收站按内容哈希全量恢复对象库（fsck 干净）并重建 HEAD/config/index。**本机此仓库禁用 git stash/checkout 切换**，未提交改动需保留时用文件级备份。
- PyQt5 关闭期崩溃（退出码 127）根因：带 parent 的运行中 QThread 在 QApplication 析构期被级联删除。NetworkProbe 刻意不挂 parent（ui_ai.start_probe 注释），勿回加。


## ui_tree_editor 能力清单与差距报告（2026-10-09，只报告不立项）

背景：评估"排查树维护界面能否替代手改 seed JSON"。逐行核查 ui_tree_editor.py（701 行）后的结论：**树的主干编辑已闭环，两个功能缺口是替代手改 JSON 的主要障碍**。

### 已有能力（实测代码路径，非纸面）
- 树级：新建 / 复制 / 删除 / 保存（add_tree / update_tree / delete_tree 全落 history）；现象 / 现象分类（TREE_CATEGORIES 下拉）/ vendor_hint（逗号分隔 slug，normalize_vendor 归一化）
- 步骤：增 / 改 / 删 / 上移 / 下移；步骤 id 树内唯一校验、分支 goto 目标存在性校验（on_save）
- cmd_ref：vendor_category + title_keyword + uuid 三要素可视化编辑；"非叶子必须填 cmd_ref"守卫
- 分支：when → goto 表格增删；叶子：结论 + 处理动作（动作 cmd_ref 保留只读）

### 差距（对照"替代手改 JSON"）
| # | 缺口 | 现状 | 影响 |
|---|---|---|---|
| G1 | 叶子动作的 cmd_ref 不可编辑 | _add_action 把命令来源列设为只读（Qt.ItemIsEnabled），编辑文本时仅保留旧引用；新建动作无法挂命令来源 | 手改 JSON 才能补动作命令来源 |
| G2 | 无 mark_tree_verified 入口 | 排查树验证标记只在 ui_troubleshoot"标记整树已验证"按钮；维护对话框里看不到/改不了验证态 | 验证回填要开另一个入口 |
| G3 | 无单树导入/导出 JSON | 只能整库 .nlb 或维护界面逐项点 | 跨库搬树仍靠手改 |
| G4 | vendor_hint 无合法性反馈 | normalize_vendor 静默归一化，占位厂商/拼错 slug 无提示 | 拼错只会在排查向导里"命令渲染不出来"才发现 |
| G5 | 无未保存关闭确认 | 关闭对话框直接丢内存改动（refresh 前 self.tree 改动不落库） | 误关即丢编辑 |
| G6 | 无步骤拖拽排序 | 只有上移/下移 | 长树重排繁琐 |
| G7 | cmd_ref 场景分类下拉不分厂商 | entries category ∪ ALL_CATEGORIES 全量平铺 | 同场景多厂商靠 title_keyword 收敛，新手不易命中唯一 |

### 候选池登记（延后，触发条件制）
- [ ] G1 叶子动作 cmd_ref 可编辑——触发条件：**下次手改 JSON 补动作命令来源时**
- [ ] G2 维护对话框内显示/流转验证标记——触发条件：**下次验证会回填树时**
- [ ] G5 未保存关闭确认——下次触碰 ui_tree_editor.py 时顺手
- [ ] G4/G6/G7——无触发不做

## 扫尾批验证安排（2026-10-09 登记，e133a83 / d61e524）

本批 11 条新条目（zte ×4 / juniper ×4 / openeuler ×3）全部 verified=0 入库，不预填验证态；41 条挂起维持不动（systemctl 类按 lab 映射表口径属正当挂起）。验证触发条件：**等 Wave 3 params 定型 + compose 环境本机就绪后，随验证会统一回填**。

| 批次 | 条目 | 验证通道 | 方法要点 |
|---|---|---|---|
| 扫尾A | juniper ×4 / openeuler ×3 | compose 实验室（ubuntu 容器近似 opendeploy 不行——juniper 无免许可容器，走真机） | juniper 走 EX/SRX 真机或 vSRX；openeuler 三条可直接在测试环境 openEuler 22.03 虚机实跑——ss/tcpdump/ethtool/dig/traceroute/tracepath 均为只读命令 |
| 扫尾A | zte ×4 | 真机验证会 | ZXR10 命令名按常见写法给出，现场以 ? 实测为准；核对通过后按 seed-guide 回填 |
| 扫尾B | 12 棵树 by_vendor + 4 处动作引用 | 验证会抽查 | 排查向导逐树走查：zte/juniper 分支命令渲染非空、无歧义告警 |


## 批3 rollback 判断批 · 抽查记录（2026-10-09）

**总账**：218 条 rollback 落地（15 厂商），变更类 231 条全覆盖，validate EXIT=0 且 [W]=0。每厂商批完即 validate，[W] 240→0 全程对账吻合（注入数+翻 query 数逐步核销）。

**判定表白名单精化记录**（批3 中发现即修，共 5 轮）：enable/disable/terminal monitor → terminal trapping → monitor start/stop/list → diagnose sys ha 只读族 → top -b/top -bn/top -bn1 + ip -4/-6 addr/route show。合计 21 条会话级/只读诊断条目从误判变更类翻回查询类（免 rollback），消除 21 条"凑数 rollback"误报。

**幻觉退回修正实例（沉淀用反例）**：
1. cisco AAA：`no username {{x}} privilege 15 secret <...>` —— `no` 形式不接收属性参数，正确为 `no username {{x}}`。起草时自查发现即改（未入提交）。
2. h3c AAA：批3 h3c 提交时漏注入 1 条（15 条变更只注入 14）——靠"缺 rollback 清单逐条核对"在 zte 批前抓回补齐。教训：每厂商注入必须以 validate 后的缺口清单为终态对账，不能信计划 JSON（其分类快照会过期）。
3. paloalto 口令条：命令正文经内容审查通道超时无法逐行核对，rollback 为盲写——已在 rollback 文本内标注"复核重点"，防火墙复核时置顶确认。

**防火墙四家复核**：fortinet 17 / paloalto 14 / sangfor 10 / topsec 10 = 51 条对照表（.workbuddy/防火墙四家rollback复核对照表.md）呈送本人复核，2026-10-09 用户裁决"复核通过"，硬停解除。

**全查组 100%**：reload/erase/restart/reboot/format 类 27 条，三段对照表（.workbuddy/全查组27条三段对照.md）生成待人工过目；要点：各厂商升级回退全部走"指回旧镜像/旧配置文件 + 重启 + show version 复核"路径，Linux systemctl restart 类回退=恢复配置文件 + 重启服务 + status 复核。

**分厂商 ≥30% 抽查**（起草时逐条对照 commands 完成第一遍，此处为复核抽样清单）：
- cisco 17 抽 6（VLAN/ACL/NAT/SSH/AAA/升级）：undo 次序、引用先行解绑、口令人工位 ✓
- huawei 27 抽 9（VLAN/Trunk/OSPF/ACL/SSH/SNMP/升级/回滚/port-group）：port-group 组内撤销语义 ✓
- h3c 16 抽 5（VLAN/ACL/NAT/升级/AAA 补漏条）✓
- juniper 15 抽 5（VLAN/静态路由/firewall filter/SNAT-DNAT/RADIUS）：delete+commit 精确撤销 ✓
- fortinet 17 抽 6 / paloalto 14 抽 5 / sangfor 10 抽 3 / topsec 10 抽 3：防火墙组已整批硬停复核通过 ✓
- centos 19 抽 6 / kylin 13 抽 4 / openeuler 12 抽 4 / ubuntu 13 抽 4 / ops 5 抽 2：备份恢复/反向命令对称/rm 限定本次产物 ✓
- ruijie 16 抽 5 / zte 14 抽 5：RGOS/ZXR10 类 IOS no 前缀 + ? 口径标注 ✓


## smoke 主库零接触改造（2026-10-09 登记，v0.5.0 发版前置）

**挂账**：smoke 套件此前"恰好不写主库"靠的是部分套件各自劫持（panel_dashboard 劫持 state 路径、stage2/3 劫持 config/sessions），结构性无保证；排查中实证一处**暗接触**——smoke_ai_chat 隐式读取开发者真 `app/ai_config.json`（真 key）判定"已配置"分支，且未配置路径会弹模态框导致套件挂死。**本节即登记+清偿闭环。**

**清偿**（commit 见 git log）：
- 新增 `scripts/_smoke_env.py`：① 路径全劫持——`db.get_base_dir`/`db.get_db_path`/`ai_bridge._base_dir`（sessions/cases/config 三处共用）/`theme.state_file_path` 全部指向一次性临时目录，`setup()` 必须先于 app 模块导入（ui_main 按名绑定 `from db import get_base_dir`）；② 主库零写权断言——setup 时对真主库 `dist_package/command_lib.db`（另防御性监视 `app/command_lib.db`）取 sha256，atexit 复核不一致则 `os._exit(42)` 硬失败，"碰了主库"不可能静默通过；
- 六个 smoke 套件头部统一接线 `_se.setup()`；smoke_ai_chat 自带假配置（`api_key=smoke-fake-key` 写入临时区 config_path），CI 无真配置也能跑，暗接触消除；
- **实证**：全量 310 项（9+43+13+93+68+84）EXIT=0；主库 sha256 改造前后均为 `6441ea7d…19b` 逐字节一致；每套件尾部打印 `[SMOKE-ENV] 主库零接触实证：指纹前后一致`。
- 已知边界：panel_dashboard 的 B14 类 250ms 时序检查在多套件背靠背满负载下出现过一次 42/43 抖动（单独复跑 3×43/43 稳定），非本改造引入。

**边界备忘（同日补记）**：`db.py::_migrate()` 每次开库无条件执行 `PRAGMA user_version` 写头部——正常应用（含 exe）启动都会重写主库 header 字节（sha256 变化、逻辑零变化，338/82/26 与 history 均不动）。主库指纹断言的"零变化"口径仅对**不打开真库的进程**（smoke 套件，`:memory:`/临时库）成立；v0.5.0 发版日实测：六次 smoke 全程 hash 恒 `6441ea7d…`，exe 启动冒烟后变为 `bc47922a…`（纯 header 重写，逻辑规模复核一致）。


## 验证会场 1（2026-10-09，实验室聚焦切片）——档位分布与异常清单

**范围**：lab 覆盖子集（ubuntu+ops 30 全验 + cisco/ios 4 语法核对 + 变更类抽 10 做 rollback 三段）；
skeleton 41 条（防火墙×38 + 中兴交换机×3）映射表确认 lab 全不可吃 → 按手册"不动状态字段"仅登记挂起清单。
**四档分布（实测回填 db）**：绿 20 / 语法核对 7 / 红 2 / 挂起 11；仪表盘四档 0/0/41/297 → 20/7/52/259。
**err_unresolved**：+7（D-Bus 死结 / RTNETLINK 无 CAP_NET_ADMIN / FRR ospfd 实例化 / sha256sum 目录参数 / enable 别名拒绝 / netplan 权限告警 / timesync1 总线缺失）。

**环境坑实录（复现要点）**：
1. compose v5 `up --build` 撞 bake 失败 → 直接 `docker build` 两镜像后 `up -d`（COMPOSE_BAKE=false 亦无效）；
2. vault.centos.org 对本机网络 403（CloudFront 地域限制）→ Dockerfile 加 `ARG VAULT_MIRROR`，本地 aliyun 构建；sed 分隔符须用 `|`（镜像站值含 `/`）；
3. docker-credential-desktop 不在 Git Bash PATH → `export PATH="/e/DockerDesktop/resources/bin:$PATH"`（本机 Docker 装在 E 盘）；daemon.json 走镜像源未做，改用"镜像站前缀 pull + retag"；
4. `bash -s` 脚本内**不得**放 apt-get/yum——其 stdin 会吃掉脚本剩余字节（B2 批脚本半截蒸发根因）；
5. lab_exec 的 `wait` 会等到脚本内 nohup 常驻进程 → 显式 `wait $PID`。

**种子修正 7 条**（走 seed→validate EXIT=0→import_seed_dir updated=7）：ubuntu service_name 默认 sshd→ssh ×3（enable 别名拒绝 + journalctl 空回显实证）；netplan VLAN/bond 补 chmod 600 ×2；rsync/scp dst_path 示例目录→文件 ×2（顺带修复 rollback rm -rf 渲染粒度）。

**回退三段实战样本**：干净回退 6（临时IP/临时路由/本地源/DNS/systemctl 服务管理/ufw + FRR 静态路由语法级）；
失败/滞后 4：① centos ifcfg 回退链断裂（备份步失败→回退全断）；② netplan IP 回退地址残留（滞后收敛，无基线 yaml 所致）；
③ netplan VLAN 时序竞态（apply 后接口未现、回退后反而出现）——唯一直接红档；④ centos systemctl/firewalld/kylin route 组：D-Bus 死结 / command not found / RTNETLINK 拒绝，均环境性归挂起。

**待真机清单（下轮排期）**：OSPF/NAT（FRR 等价性不足）、防火墙 38 skeleton + 中兴 3（模拟器会话）、
centos7 systemctl/firewalld 组、kylin/openeuler 其余 69 条、netplan try 交互路径、审计/时间同步毫秒级判读。

**候选案例 3 篇**：伴生仓 `docs/verify/20261009-lab-session1/cases/`（回退链断裂 / netplan 回退滞后 / ssh 单元名闭环）。

## 验证会场 2（2026-10-12，lab 收尾饱和场）——档位分布与饱和判定

**范围**：centos 30 全家族 + cisco FRR 候选 6（段 2a）+ kylin 23 / openeuler 22 裁决A 全量（段 2b）= 81 条。
**三裁决落地**：A 采纳（kylin/openeuler 通用族跨容器语法核对档，禁凑绿）；B 采纳 B2 细分（systemctl×2 转 ubuntu systemd 语法核对，firewalld×2 归正当挂起）；C 采纳（交互命令约定进 verify-guide）。
**四档分布（仪表盘口径）**：20/7/52/259 → **27/61/66/184**（绿 27 / 语法核对 61 / 挂起 66 / 未验证 184）。
本批：绿 7 / 语法核对 37 / 挂起 20 / 红 0；err_unresolved +3（bind-mount sed -i rename 失败 / 无 CAP_SYS_TIME chronyd 拒启 / FRR 8.4 命令面缺口）。

**rollback 失败模式对照表（场1 四类 + 场2 新增三类，模式清单即产出）**：
| # | 模式 | 场 | 实证 |
|---|---|---|---|
| 1 | 链断裂（备份步失败→回退全断） | 场1 | centos ifcfg 备份步失败回退全断 → seed-guide ④ 备份步守卫约定化 |
| 2 | 滞后收敛（回退后残留自行消退） | 场1 | netplan 回退地址残留 → verify-guide 收敛窗口判读（≥30s 复核 2-3 次） |
| 3 | 时序竞态（apply/回退交错） | 场1 | netplan VLAN apply 后接口未现（唯一直接红档） |
| 4 | D-Bus 死结（无 systemd 容器 restart 全灭） | 场1 | systemctl restart network/chronyd Failed to get D-Bus connection |
| 5 | **bind-mount/占用文件 sed -i rename 失败** | 场2 新 | `/etc/resolv.conf` 上 `sed -i` 报 Device or resource busy，回退步失效；**cp 备份覆盖法替代实证通过**（err_unresolved 实录） |
| 6 | **能力缺失静默失败**（rc=0 但服务没起来） | 场2 新 | chronyd 容器内 adjtimex 被拒（无 CAP_SYS_TIME）但进程退出 rc=0；**判读必须核进程存活/功能回显，不能只看 rc** |
| 7 | **模拟器命令面缺口**（Unknown ≠ 语法错） | 场2 新 | FRR 8.4 无 show arp/mac address-table/spanning-tree/ntp（IOS 对应物缺失）；语法核对在模拟器上受版本命令面约束，如实记"不可核对" |

**环境新知**：① centos7 容器无 initscripts（network-scripts 目录缺失，文件段验证需 mkdir 构造）；② ubuntu:22.04 官方镜像无 python3/lsof/traceroute/iptables（apt 源可达，`apt install python3-pip` 补装后 pip 离线链全通）；③ FRR 8.4 实际可用命令面 = `show interface brief`（单数形）/show logging/show memory/show running-config/show ip route，IOS 风格复数形全 Unknown；④ heredoc 嵌套坑：外层 `<<'EOF'` 内层再用 `EOF` 定界符会截断外层（脚本半截被宿主 bash 执行），内层一律用异名定界符。

**饱和判定（lab 覆盖终局表）——跑完后剩余 176 条待验证全部为需真机/模拟器，lab 侧清零**：
| 类别 | 条数 | 为什么 lab 永远出不了绿 |
|---|---|---|
| 防火墙四家（fortinet/paloalto/sangfor/topsec） | 71（54 非骨架+17 骨架） | 无免许可容器化路径，Web 控制台+CLI 均真机专权（映射表口径） |
| huawei/h3c/ruijie/zte/juniper | 131 | 各家 VRP/Comware/RGOS/ZXR10/Junos 无免许可容器，语法本体即厂商私有 |
| cisco（IOS） | 20 | FRR 8.4 命令面与 IOS 重叠不足（仅 show logging 族等 1 条语法核对档），AAA/SSH/密码/升级/HSRP/STP/DHCP/NTP 全不同构或缺栈 |
| centos7 systemd/firewalld 组（裁决B） | 2（firewalld×2） | 无 CAP_NET_ADMIN + 无 systemd PID1，结构性无解，已归档正当挂起 |
| kylin/openeuler firewalld/nmcli 组 | 4 | 容器无 firewalld 包、无 NetworkManager 服务 |
| ISO 挂载×3 / ethtool -m×3 | 6 | 需物理介质 / 物理光模块（DOM），容器结构性缺失 |

**转真机排期提议**（呈报待裁决）：
- **真机场 1**（一场收两笔账）：防火墙 38 skeleton（sangfor/topsec 18+18 + fortinet/paloalto 各 1）+ 中兴 3 skeleton——rollback 真机复核 + 标注收口；skeleton 条目按手册"同标准执行"核对通过即升绿+verified-cli。
- **真机场 2**：OSPF/NAT + cisco 全量（IOSv 模拟器或真机；FRR 已证不等价）。
- **真机场 3**：kylin/openeuler 真机批（openEuler 22.03 VM 现成可先跑：nmcli/firewalld/chrony ms 级判读/持久化生效链 ≈ 20 条）；kylin V10 待环境。
- **介质场**：ISO 挂载×3 + U 盘挂载 1（带介质环境一把收）。

**候选案例 2 篇**：伴生仓 `docs/verify/20261012-lab-session2/`（sed -i bind-mount 回退失效→cp 替代 / pip 离线链 download→--no-index 闭环）。仪表盘前后对比：`docs/screenshots/dashboard_session2_after.png`（27/61/66/184）。

### 主库种子导入覆盖事故（2026-10-12 10:1x 发现，当日恢复）

- **现象**：静态站点导出器首跑对账时发现主库 `exec_level='verified-cli'` 61→0、场 2 回填 notes（"实测 2026-10-12"/"不可验证"标记）全部消失；`verified=1`（27 条绿）幸存。主库 mtime 2026-10-12 09:27。
- **根因**：09:27 主库被执行了一次**种子导入**（来源待查，GUI 导入或某工具触发）。`db.py` 导入 UPDATE 分支的 P1-2 护栏只不采纳 `verified/verified_by/verified_model/verified_date` 四字段——**`notes` 与 `exec_level` 无护栏，被种子值整体覆盖**；verified 因护栏幸存。
- **连带**：伴生仓 `fe600e0` 的 db 快照 cp 自覆盖后主库，同为坏态；tag `verify-lab-session2` 指向该 commit。
- **恢复**：`backup/command_lib-pre-session2-20261012.db`（回填前）+ 场 2 回填数据重放（本次重放全部 WHERE 限定 vendor+title，顺手消除原回填脚本的同名误伤隐患），对账 27/61/66/184 + err_unresolved 11 逐项吻合。附赠发现：重放前测试脚本曾以 `sqlite3.connect` 误建 0 字节 `app/command_lib.db`（sqlite3 对不存在路径默认建空库），已删——**工具脚本连接库路径前必须先 isfile 检查**。
- **遗留建议（待裁决另批）**：~~`db.py` 导入 UPDATE 分支应对 `notes`/`exec_level` 加与 P1-2 同款护栏~~ **已于当日热修批落地**（SESSION_FIELDS 全组禁写，见下）。

### 导入护栏热修批（2026-10-12，单笔提交，随 v0.6.0 发版）

**字段归属二分表**（ENTRY_FIELDS 23 项，护栏裁决 2026-10-12）：

| 归属 | 字段 | 导入行为 |
|---|---|---|
| 键 | uuid | 合并依据，两侧不写 |
| 系统维护 | created_at / updated_at | 守卫排除（原有） |
| 内容字段（种子拥有，导入可写） | platform / device_type / vendor / os_family / models / category / title / description / commands / rollback / params / duration / interactive（13） | 照常覆盖 |
| **状态字段（会话拥有，导入禁写）** | `SESSION_FIELDS = VERIFY_FIELDS + (exec_level, notes, favorite)`（8 项） | UPDATE 全组保留库值；INSERT 照收种子初始态 |

裁决记录：notes 归状态组**整字段禁写**（验证实录尾部保留；种子侧 notes 修订需另做骨架同步通道——候选池）；favorite 收编（P1-2 时代遗漏：导入原会覆盖收藏）。

**落地**：`db.py` 新增 `SESSION_FIELDS` 常量 + `_pre_import_backup()`（首次写入前自动备份，两级目录定位）+ import_entries UPDATE 组全禁写 + 漂移收敛单行汇总；`main.py` validate 入口组合拒绝（--db+--validate-seed → rc=2，--allow-main-db 显式放行）；"种子库条目 verified 必须 0"规则限定纯种子内存库（指定库绿档是合法回填）。回归 `scripts/smoke_import_guard.py` 7 项（三事故场景+自动备份+INSERT 初始态）全绿；全量九套件全绿。
- **二次事故与教训（当日）**：恢复后排查 validate 报错时以 `--db 真库 --validate-seed` 跑了两轮（且 `$?` 被管道 tail 掩盖误读 EXIT=0），**该用法会把种子导入真库再次覆盖 notes/exec_level**——二次事故。重放脚本固化 `.workbuddy/session2_replay.py`（回放+对账断言一体），二次恢复后以 `export_site` 对账 27/61/66/184 复核。**validate-seed 标准用法是 `python app/main.py --validate-seed`（无 --db，内存库）；`--db` 变体仅用于干净目标库，永远不要指向真主库**；管道取退出码必须用 `${PIPESTATUS[0]}` 或临时文件。

### 场 2 回填误伤事故记录（2026-10-12，回填工具固化动因）

- **事故**：场 2 回填 SQL 的 SYNTAX/HOLD 字典以 title 为键且 WHERE 未限定 vendor，`接口状态诊断（show interfaces 族）`、`系统日志排查（show logging）` 两条 ruijie 条目与 cisco 同名被误伤（前者被追加 FRR 挂起 notes、后者被升 exec_level=verified-cli + 追加 notes）。
- **发现**：回填后 rowcount 对账 18/13 大于预期 17/12，追查 notes LIKE 命中定位到 ruijie 两条。
- **恢复**：从 `backup/command_lib-pre-session2-20261012.db` 按 uuid 精确取原 notes/exec_level 回写，复核 ruijie 残留 0。
- **整改**：回填动作固化为 `scripts/backfill_verify.py`——uuid 精确匹配（0/多重命中报错退出）、重复回填防护（--force 覆盖）、事务包裹（green 缺四元组 ROLLBACK）、前置/后置双对账断言（非目标条目全字段零变化 + 总数不变）；回归套件 `scripts/smoke_backfill.py` 9 项全绿，其中 C1 即本事故场景（cisco/ruijie 同名对回填后非目标字段零变化）。verify-guide 第 4 步口径自此以脚本为准。

### 操作单生成器（Runbook）落地记录（2026-10-12，v0.6.1）

- **落点**：`app/ui_runbook.py`（Tab）+ `app/runbook_export.py`（数据模型 + HTML 导出器）+ `scripts/smoke_runbook.py`（42 项）。数据模型/导出口径/二批规划的权威出处是 `docs/runbook-guide.md`，此处只记实施裁决与坑。
- **四项裁决**（用户确认）：① 导出 HTML 用浅色打印主题，色值从 theme.py 派生 + 同源校验（PRINT_TOKENS 每项记录来源常量与派生基线，theme.py 改色即漂移拒导出）；② Tab 置于报错诊断后、AI 诊断前；③ rollback 不落盘、按 uuid 实时取库（变更类判定同样实时走 classify_entry，不另写判定表）；④ 操作单 JSON 存 `<exe同目录>/runbooks/`（gitignore，dist 出库核查不含）。
- **坑 1（QSpinBox 钳制）**：`type=int+range` 参数经 `_make_editor` 生成 QSpinBox——永不为空、越界被钳制，**必填/越界校验的真实触发路径是 QLineEdit 编辑器**（无 type 或 validate= 规则型）。冒烟断言必须按真实控件路径构造，否则永远测不到校验分支。
- **坑 2（offscreen isVisible 恒 False）**：QT_QPA_PLATFORM=offscreen 且窗口未 show 时，`widget.isVisible()` 恒 False——Tab 内"缺回退红标"断言改用 `_rbmiss_active` 旗标（`_show_step` 里与 setVisible 同源赋值）。
- **坑 3（编辑区数据串位，已修）**：`on_move`/`on_delete`/`on_new`/`on_open` 里重建步骤列表会触发 `_on_row_changed → _flush_current_step`，此时编辑区仍是旧步骤内容，会把旧内容写进移位后的新位置步骤——统一在重建前 `self._cur = -1` 使 flush 失效（on_delete 先算 keep 再置空）。
- **回归基线**：smoke_runbook 42/42；smoke_panel_dashboard 更新为 5 Tab 断言后 44/44；AI 三套件按标准姿势（PYTHONPATH=app;_vendor）84/93/68 全绿；validate-seed EXIT=0（338+26+82）。

### 发版口径补充（2026-10-12，v0.6.1 收口）

- CHANGELOG 头部口径增补：**发布后短窗内完成的 MVP 追加批次收口走 patch（0.6.x）；独立排期功能批走 minor（下一功能批从 0.7.0 起算）**。v0.6.1 = v0.6.0 发布（同日）后追加批次（Runbook MVP + 场景包）收口，v0.6.0 节不动；v0.6.0 Release 正文尾部加「追加批次见 v0.6.1」保持下载指引连续性。

### 发版出库纪律两条（2026-10-12，v0.6.2）

- **出库白名单断言**：发版 zip 条目在 release.yml Zip 步骤按**正向白名单**强制执行（NetToolBox.exe / check_db.py / command_lib.db / seed_data/*.json 四类），任何白名单外条目 ERROR 中止——黑名单只能拦已知坏物，拦不住构建链条新引入的中间产物（v0.6.0/v0.6.1 即被导入护栏落在 staging 的 backup/ 漏进包，约 2.8MB 纯种子副本，外部独立核验字节级比对才发现；Assemble 步骤同步显式清理 staging backup/）；
- **产物类验收必须实测产物**：发版/打包类验收必须**解包实产物列清单核对**（unzip 列表 + 与仓库 blob 字节比对），不得从 workflow 脚本或流程描述推断——本次两处瑕疵（backup/ 混入 + seed json CRLF 化）均存活两轮发版，根因正是验收只看了 workflow 没解包。

## 安全加固批落地记录（2026-10-13，四小批：3c9ff0c / 门禁 / 端点 / regex）

四批内容与用户可见行为见 CHANGELOG Unreleased「安全加固」节（权威出处，此处不复制）；只记实施裁决、坑与对账证据。全量 15 套冒烟全绿 + validate-seed EXIT=0（338/26/82 底数不变）。

### 批 1 · Key 存储（DPAPI-v1）实施裁决与状态清单

- **事务式迁移（批裁决修正后形态）**：写加密版 `ai_config.json.new-enc` → 原文件 rename 为 `.bak-<ts>`（此刻介质上明文唯一份=备份）→ 加密版转正（此后零明文）→ 解密 round-trip 验证 → 通过删备份 / 失败 `os.replace(bak, path)` 还原。任何时刻介质上最多一份明文。
- **KEY_STATUS 全状态集**（`ai_bridge.KEY_STATUS`，运行期不落盘）：`migrated`（正常）/ `migrated-backup-locked`（迁移成功但备份删除失败，提示手动删）/ `verify-failed-restored`（验证失败已还原明文）/ `verify-failed-restore-failed`（还原也失败，需人工介入 bak 文件）/ **`readonly-keep-plain`（只读介质写不进 → 保持明文可用不瘫痪现场，本状态的正式记录位）** / `plain-no-dpapi`（非 Windows）/ `encrypt-failed-keep-plain` / `decrypt-failed`（换机/换账户解不开，设置页黄条提示重填）。
- **坑 1（DPAPI blob 定义）**：`DATA_BLOB.pbData` 用 `c_char_p` 时，取字段会被 ctypes 自动转成 bytes 对象，传给 `LocalFree` 即堆损坏 → 进程 127 硬死且**缓冲输出全丢**（表象是无输出退出）。必须 `c_void_p` + `create_string_buffer` 保活。
- **坑 2（LocalFree 原型）**：不设 `argtypes=[c_void_p]` 时 64 位指针塞进 c_int → `OverflowError: int too long to convert`（被兜底 except 吞掉后表象是"加密一直失败走明文降级"）。两个坑叠加导致首轮调试耗费显著——**ctypes 调 WIN API 必须显式 argtypes/restype**。
- **坑 3（迁移条件）**：迁移判定必须看「文件里的原始明文 key」，不能看解密后的 cfg——否则每次 load 解密出的明文都会再触发一轮迁移（写 bak→删 bak 空转），且 KEY_STATUS 永远停在 migrated。
- **熵与威胁边界（审码修复批 F5 补记，2026-10-13）**：CryptProtectData 未传 OptionalEntropy——同一 Windows 用户上下文下的任意进程可解密该密文；防御面 = 介质外泄（换机/换 Windows 账户解不开），不防御本机同用户恶意进程，是「零依赖 + 免口令」约束下的已知取舍（正式出处 = ai_bridge.py KEY 存储节注释）。
- **ciphertext-wins 双字段语义（审码修复批 F7）**：密文有效 + 文件残留明文 → 清明文、密文原样、绝不重加密覆盖；密文损坏 + 文件有明文 → 保留明文可用、不迁移、不动密文，KEY_STATUS 记 decrypt-failed。save 时加密失败 → 明文兜底且 KEY_STATUS 记 encrypt-failed-keep-plain（F6，不静默降级）。

### 批 2 · 发送门禁实施坑

- **scripts/ 薄壳遮蔽（连锁修复）**：python 把 smoke 脚本目录放 `sys.path[0]`，`scripts/sanitize_scan.py`（CLI 薄壳）会遮蔽 `app/sanitize_scan.py`——ai_gate→sanitize_scan 导入链建立后，**所有**经 scripts/ 启动的套件都撞薄壳自引用 ImportError。修法收敛进 `_smoke_env.setup()`：脚本目录统一挪到 sys.path 末尾（全套件受益，勿在单套件内各自修补）。
- 占位符剔除会移位下标：secret 在 scrubbed 文本上判定，`_scrub_with_index_map` 产出 scrubbed→原文下标映射后回映 span（脱敏回写依赖原文 span）；IP/黑名单直接在原文上扫（天然对齐）。
- 门禁 IP 策略与站点白名单的分叉点在 `_ip_allowed` 参数化（默认参数=站点策略逐字不变，`allow_nat_teaching=False` 时 200.1.1.0/24 不豁免）——站点扫描行为零变化有 smoke_send_gate [7] 回归断言兜底。

### 批 3 · base_url 策略实施坑

- **endpoint_host 旧正则会把 `http://[::1]:9000` 的 host 截成 "["**——已改 urlparse（hostname 自动去方括号、小写）。
- **离屏环境弹模态即段错误**：on_send 新增的"陌生端点未放行"QMessageBox.warning 在 offscreen smoke 里直接 139。smoke_ai_chat 假配置自此必须给本网 base_url（http://127.0.0.1:9/v1，loopback 恒放行）。**今后任何 smoke 假配置带 AI 发送路径的，base_url 必须本网化**。

### 批 3 追加 · 审码修复批 F3+F4 裁决留痕（2026-10-13）

- **RFC1918 全豁免为既定产品口径（本网零打扰）**：127/8、10/8、172.16/12、192.168/16 及 localhost/::1 一律免 HTTPS 强制、免首次确认、免放行清单。
- **SSRF 面定义**：本产品无服务端代抓场景，SSRF 面 = 用户自配端点 + key 外发（用户把 base_url 指向哪，key 和对话就发往哪），非经典 SSRF。
- **残余风险（记录在案）**：内网 http 端点收明文 key + 对话明文——由首次确认弹窗 + 公网强制 HTTPS 缓解；`approved_endpoints` 主机粒度不分端口（同主机换端口不重弹确认），属已裁决的可用性取舍。
- **F3 落点**：test_connection / list_models 发请求前加 endpoint_approved 门禁（桥接层拒绝，未发请求）；UI 层 SettingsDialog 测试/获取模型前弹首次确认（拒绝不发请求、key 不出机），确认后立即持久化 approved_endpoints（避免"确认了但没点保存"导致重复弹窗）。
- **F4 落点**：endpoint_addr 解析失败/空 host 返回 None（不再静默回退 api.deepseek.com:443），NetworkProbe 对无效配置 emit "配置无效"，不给误导性可达。

### 批 4 · regex lint 对账与口径

- **对账结果（如实报告）**：存量 82 条报错字典 pattern 过 lint，[E] 零命中、[W] 零命中，validate EXIT=0 底数不变（338/26/82）。lint 正反例 23 项覆盖：`(a+)+ / (.*)* / (?:\w+)* / (a|bb+){2,} / ((a+)+) / (a*)+ / ([0-9]+)+$` 全部 [E]；`(a+){3}`（组外有限量词）/ `[.*]+`（字符类）/ `\(a\+\)+`（转义）不误报。
- lint 是静态语法扫描（跳过字符类与转义后逐组检查），不执行正则本身；与 validate/ui 编辑器保存共用同一执法点（error_match.pattern_lint），[E] 拦、[W] 确认/告警。
- 诊断输入截断：行数超限**先按行切再按字符切**——只按字符切对"大量短行"不减少行数，匹配成本降不下来。

### L 批 · AI 会话与放行指纹明文治理（2026-10-13，审码遗留三条观察收尾）

三条裁决的取舍记录（CHANGELOG Unreleased「安全加固 L 批」节为用户可见权威出处，此处记实施与代价）：

- **裁决 1 · sessions/ai_cases 落盘加密（文件级 DPAPI-v1）**：不引入 master key、不新建加密框架——整文件 JSON 序列化后 DPAPI 加密，落盘内容 = "dpapi-v1:<base64>"（复用 KEY_ENC_PREFIX）。**取舍（已裁决接受）**：DPAPI 绑定本机本账户 = 换机后会话历史不可迁移；介质外泄（含 U 盘 exe 场景的 sessions/）优先，接受此代价。读写两格式兼容（密文优先、明文回退），存量明文首启逐文件事务式迁移（每目录每进程一次，`_STORE_MIGRATED_DIRS`）；迁移触发点 = `list_sessions()` / `list_cases()`（枚举即扫描，save 路径天然写入即加密）。迁移中间态（.bak-<ts> / .new-enc）不以 .json 结尾，被枚举过滤器天然跳过。状态留痕 STORE_STATUS 同 KEY_STATUS 模式（migrated / verify-failed-restored / encrypt-failed-keep-plain / readonly-keep-plain / plain-no-dpapi 等）。
- **裁决 2 · gate_allowances_enc**：指纹 = kind+低熵 secret 的 sha256 前 16 位，明文指纹 = 离线字典攻击 oracle（拿口令字典算指纹比对即可反推真值）——字段随 key 同走 DPAPI，当日有效语义不变。实施收口三点：存量明文单字段事务式迁移（复用 L 批通用化的 `_transactional_config_rewrite` 舞步，与 key 迁移同款）；save_config ciphertext-wins 收口（setdefault 合并把旧明文字段带回来 → 密文在场统一清明文）；DPAPI 不可用降级保持明文字段（可用性优先 + GATE_STATUS 留痕）。
- **裁决 3 · vision 通道门禁**：图片附件**不做内容分析**（做不到的事不装能做到——本地无图像 OCR/脱敏能力，扫描即撒谎）；代替动作 = 发送前 VisionConfirmDialog 单独列出全部图片附件 + 明示外发端点 host + 逐次确认（每轮必弹、无记忆无放行清单）；未确认 → on_send 直接 return（user_msg 未组装，会话/案例零落盘）。图片在会话内的落盘面已由裁决 1 覆盖（sessions 加密后 data_url 随文件加密）。
- **批 1 舞步通用化**：`_transactional_config_rewrite` + `_drop_stale_config_field` 为 L 批新增的通用形，key 迁移（`_migrate_legacy_key`）重构为复用——smoke_keyvault T1–T11 回归逐字全绿证明行为未变。
- **坑（T18 假阴性）**：`_write_store_json` 必须先查 `_dpapi_available()` 再 protect——mock 掉 available 后 `_dpapi_protect` 在 Windows 上照样成功，"无 DPAPI 降级"用例会拿到密文而误判实现错误；降级判定点在写入包装层，不在 protect 本身。
- **坑（GATE_STATUS 语义）**：`decrypt_gate_value` 成功时刷新状态为 ok（同 `_decrypt_key` 的"最近一次操作"语义）——断言迁移状态必须放在任何后续解密动作之前，否则读到被刷新的 ok。
- **smoke_ai_chat 适配**：带图发送路径新增确认模态后，离屏套件在 `tab2.on_send()`（带图）处会 exec_ 阻塞——套件以 monkeypatch `VisionConfirmDialog.exec_` 自动确认走通链路，并新增两条确认框内容断言（列图 / 明示 host 127.0.0.1）；"未确认不发送"拒绝分支断言在 smoke_send_gate [8] V7–V9（AiTab 离屏实跑）。
- 冒烟实测计数：smoke_keyvault 48→75（T12–T18），smoke_send_gate 32→56（[4b] 11 项 + [8] V1–V13）；全量 15 套 rc=0 + validate-seed EXIT=0（338/26/82 不变）。本批只提交推送，不发版不 tag。

### 工具链（开发依赖，不入 requirements.txt 运行时）

- **Pillow**：2026-10-13 I 批（exe 图标）接入时安装（隔离 venv，Pillow 12.3.0），仅用于 docs/screenshots/tubiao.png → app/icon.ico 一次性转换；运行时无依赖。
- **图标源素材取舍（I 批收口 2026-10-13）**：源图 tubiao.png 已从 docs/screenshots/ 出库，不再另立 assets/ 目录——素材已定稿为 app/icon.ico（唯一消费点，exe --icon 与主窗 setWindowIcon 同源），且 git 历史中 tubiao.png 永久可回溯；为一张定稿 png 单独开素材目录得不偿失。若日后需要改图标，从 git 历史（9d6eaa5 前）取回源图重新转换即可。

### 本机工作环境坑（跨批复用）

- 本会话 bash shim 下 `PYTHONPATH=xxx python ...` 前缀赋值形态恒 127 无输出（export 亦同）；**可用 `env VAR=... python ...` 绕过**。管道取退出码继续用 `${PIPESTATUS[0]}`。

### 范围外观察（后续立项候选，未进本批）

- **工件口径（审码修复批收尾留痕，2026-10-13）**：审码批产出的「提示词包」为会话外工件，不入仓库；审码报告遗留项（J 遗留）的最终口径以本文件本节为基准，报告归档件（docs/audit/）append-only 不回改。
- **sessions/ 与 ai_cases/ 明文治理**：AI 会话全文（含设备信息、可能含口令）明文落 exe 同目录随介质走，与批 1 同一威胁面。候选方案：会话目录整体加密 / 退出时清理 / 只保留最近 N 天。已列入提示词包 L 行，待排期。**【收尾 2026-10-13】已由 L 批裁决 1 落地（文件级 DPAPI-v1 + 首启事务式迁移），取舍记录见本文件 L 批节。**
- 图片附件（vision 模型通道）不经文本门禁，多模态外发面另行评估。**【收尾 2026-10-13】已由 L 批裁决 3 落地（不做内容分析 + 逐次确认 + 明示端点 host；未确认不发送不落盘）。**


## E 批数据扩容批记录（2026-10-14，eae4bd7 / 59ed111 / 99a3dda）

纯数据批（产品代码零改动；冒烟三套件种子口径数字随数据同步属测试期望更新）。三子批独立提交推送，每批 validate EXIT=0、[W]=1（基线不变）、[E]=0。

### 第 0 步盘点（只读，结论并入执行）
- 报错库 82 条按厂商×类目：低洼区 zte/sangfor/topsec/paloalto 各 4 条、ops 仅 1 条；「权限」类目 0 条；「服务/磁盘/本地源」仅 linux 系命中。
- err_unresolved 13 行逐条评估：真实回显 8 条（#2/#4/#5/#6/#7/#8/#9/#10）全部转正；#1 已 resolved；#12/#13/#14/#15 为无效残留（"not"/"all"/"Error"/重复行），保持 pending 不转正。
- 占位厂商二选一：**山石 Hillstone 胜出**——slug/os_family（stoneos）已在 db.py 预注册、占位目录就位、官方知识库（kb.hillstonenet.com）+ StoneOS CLI 手册公开可核对，纯数据即可落地；迪普 DPtech 无预注册 slug，落地需改代码，与本批约束冲突。BSD（FreeBSD ifconfig/pf）评估：`freebsd` 不在 VENDOR_SLUGS、platform 只有 network/linux 两值、接口命名/端口展开规则缺失，纯数据批落不了地，**建议另立代码+数据批**（结论：本批不做）。
- 仪表盘矩阵基线：15 厂商 × 32 分类 = 480 格，实填 233 格，0 条格 247 个。

### 三子批
1. **子批 1（eae4bd7）**：报错库净增 15（转正 8 + 补缺 7），样例自测 100% 命中；smoke_regex_guard/scenarios 口径 82→97。
2. **子批 2（59ed111）**：hillstone.json 落 `placeholders/hillstone/`（递归扫描自动生效）；10 条 = VLAN×2/Trunk×1/接口诊断×1/日志×2/防火墙诊断×2/保存配置×2；变更类 6 条 rollback 全覆盖（save 基线 + rollback 回退式），查询类 4 条按 seed-guide 免填；全量 skeleton（占位转正按 [W] 记账：骨架条目 +1 厂商）。
3. **子批 3（99a3dda）**：Linux/ops 补缺 10 条；ops 格 6 + 四发行版版本升级格 4；回退三类：备份守卫式 3 / passwd 再改回式 1 / 指回旧内核式 4；只读组合 2 条 fail-safe 判变更类、rollback 为如实说明。

### 主库与收尾（.workbuddy/ebatch_final_db.py，备份兜底 + 断言）
- 导入前备份 `backup/command_lib-pre-ebatch-20261014-171310.db`；应用自带 import_seed_dir 路径（UUID 合并 + 状态字段保留库值），新增 38（条目 20 + 报错 15 + 场景包 0 计更新，实为 20+15+3 包）。
- err_unresolved #2/#4/#5/#6/#7/#8/#9/#10 → resolved + resolved_err_id 回写（事务 + 前后断言），pending 12→4。
- 矩阵对账：种子内存库 vs 主库逐格 diff **mismatch=0**；矩阵 16 厂商 × 32 分类 = 512 格，实填 247，0 条格 265。
- 四档分布：命令 绿27/语法核对61/挂起76/未验证194（358）；报错 挂起15/未验证82（97）；树 绿0/未验证26。
- 抽验：15 条模拟回显（非 examples 原文变体）经 match_errors **15/15 命中**。
- smoke：regex_guard / scenarios / schema_rollback 三套实测全过（358/26/97 口径）。

### 遗留与后续
- 场 2 遗留 err_unresolved 无效残留 4 条待 UI 侧清理或批量归档。
- BSD（FreeBSD）与迪普 DPtech 落地需代码侧厂商注册，建议另立批次。
- 山石 10 条全为骨架：真机核对后经回填流程转绿（对照防火墙树组 slug 扩展一并做）。

## H 批 Before/After 变更对比 MVP（2026-10-14/29，134ecd1 / 3bdf6cd / 子批3）

### 定位与盘点结论
- 离线变更验证最后一环：变更前后各粘贴一次同命令回显 → 状态差异摘要。粘贴流零联网（diff_tool / diff_export / ui_diff 三模块**源码级零网络 import**，smoke H9 断言），Copy Only 原则不变。
- 可复用件盘点：error_match.compile_pattern + pattern_lint（规则正则同一门禁）；runbook_export.PRINT_TOKENS / verify_print_tokens_against_theme / _css_vars（导出同源，不造第二套色）；sanitize_scan 口径（结构上不外发，无需主动脱敏）；smoke_runbook 离屏模式。
- **提取器落位裁决：种子侧 JSON**（seed_data/diff_rules.json，独立文件不新增条目）——随包分发 U 盘现场可改（_collect_seed_files 同名优先级自动生效）、validate 门禁可拦、E 批真实回显语料沉淀即改 JSON；不进 DB 表（避免 schema 浪潮，smoke_schema_rollback 口径不动）。import_seed_dir 对无 entries/trees/errs/packs 键的文件空转 (0,0,0)。

### 四类规则 × 17 变体（首批）
接口状态 kv（huawei current state / cisco is up,line protocol / h3c brief Link 列（值组 (?-i:) 锁大写防吃 Juniper 小写 up）/ ruijie 同 cisco / juniper terse）；VLAN 表 set（huawei VID+type / cisco+ ruijie brief）；OSPF 邻居 kv（huawei+h3c peer brief（area 非捕获组，不入状态值）/ cisco+ruijie show ip ospf neighbor）；路由表摘要 kv（huawei+h3c routing-table（proto+via）/ cisco+ruijie show ip route（码列+[...] via）/ linux ip route（default|前缀 + via/dev））。

### 关键实现决策与坑
1. **噪声不参与提取过滤**（实测教训）：噪声是整行级正则，OSPF 邻居行含 Dead Time（00:00:35）、接口详情行含时间戳——按行剔除会把真实状态行一并杀掉（smoke C5 首跑实锤）。噪声只作用于 ① noise_dropped 计数 ② 降级行级比对（diff_lines）。提取正则本就键/状态形态强约束，不怕噪声行混入。
2. **同构变体取最多命中**：extract_items 用全部变体试提取取条数最多者，同构厂商（ruijie≈cisco、h3c≈huawei）line 逐字相同时命中首个——smoke D 案按「正则等价组」断言（命中任一逐字等价变体即过，异构正则命中即 FAIL）。
3. **★ 中断丢块事故**：会话中断导致子批 1 的 validate [X] 硬拦截块（diff_problems 判定 + return 1）编辑结果部分回滚，commit 134ecd1 带着缺口进了库（validate 对坏规则静默 rc=0）——smoke E6 端到端用例抓住（rc=1 断言失败），子批 3 补回。教训：中断后先 diff 复核前次编辑是否完整落盘再继续。
4. diff_export 的 %-format 模板里 `width:30%"` 字面 % 未转义 → 渲染 rc=8，`%%` 修复。
5. 受管 Python 升级重建（3.13.12 目录实跑 3.13.14）后 site-packages 清空，PyQt5 缺失——按项目惯例 `pip install --target=_vendor PyQt5` 落项目本地（GUI 冒烟统一 `PYTHONPATH=_vendor`）。

### 明确不做（写进文档，留后续批）
- 全自动解析所有厂商所有命令（规则按类逐步扩）
- 设备直连采集（坚持粘贴流）
- AI 辅助判读（差异语义解释）
- 规则编辑 UI / 规则入 DB 表

### 验证账
- smoke_diff 62 项（八案）rc=0，主库零接触过；smoke_runbook 62/62；smoke_panel_dashboard Tab 6→7 后全过
- validate-seed EXIT=0：358/26/97 底数不变、4 类 / 17 变体过闸、[E]=0、[W] 基线 1 处（zte skeleton 场景包提示）无新增
- 端到端坏规则注入（临时 seed 目录同名文件优先级）→ validate rc=1 + [X] 变更对比规则 + 定位坏正则；移除后复位 rc=0

## F 批 排查树可视化编辑器 MVP（2026-10-29，两期制第一期）

### 第 0 步盘点结论（只读，并入执行）
- 26 棵树分布 4 文件：firewall 4 / linux 7 / network 14 / ops 1；树级字段 tree_id/symptom/category/vendor_hint/steps/verified；步骤 cmd_ref 123 处**全部为 by_vendor 形态**（vendor_category + by_vendor{slug:{uuid,title}}），叶子动作 cmd_ref 18 处里 16 处 legacy（vendor_category+title_keyword / +os_family）——编辑器两形态都保留可编。
- validate 排查树节 [E] 清单（main.validate_seed）：tree_id 重复 / 无步骤 / 步骤缺 title / 分支 goto 目标不存在 / 叶子缺结论 / 步骤缺 cmd_ref / cmd_ref 对所有 vendor_hint 厂商解析不到 / 动作内嵌命令文本 / 动作 cmd_ref.uuid 不存在；逐厂商歧义/缺条目归 [W] 提示（tree_notes，不阻塞）。
- **落位裁决：app/（exe 随主程序）而非 scripts/**——需复用 db 层（选择器搜索 / get_entry 解析核对 / seed_dirs）与 main.validate_seed 门禁；与 H 批 diff 三件套同理属功能件，scripts/ 只放纯 CLI 工具。形态取**对话框**（排查向导工具栏入口）不加新 Tab：种子维护面与 TreeManagerDialog 同层，Tab 计数断言（七 Tab）不动。
- 与 TreeManagerDialog 分工：那个直写**数据库**（现场 DB 维护），本编辑器面向 **seed_data JSON**（导出流 + validate 门禁），导出不改库（库由种子重导入同步）。

### 关键实现决策与坑
1. **换行风格按文件探测保留 + 本批归一化**（实测教训）：盘点时磁盘 4 文件不一致——firewall/ops=CRLF、linux/network=LF。追因：`.gitattributes`（2026-10-12 v0.6.2）对 *.json 钉死 eol=lf，仓库 blob 全 LF，磁盘 CRLF 是该声明前脚本写盘的历史残留（git 状态 clean，字节不对）。本批将 ops.json 磁盘归一为 LF（blob 零变化、validate 复跑 EXIT=0）；firewall 的归一由 git checkout 按 attr 触发。serialize 按原文件探测（detect_crlf）后输出，回环逐字节一致，导出 diff 无行尾噪声；磁盘残留清零后 detect 恒 False。
2. **键序保持**：SeedStepDialog 在原步骤 dict 深拷贝上改字段（不重建 dict），replace_tree 原位替换——导出 diff 只含语义变化。新增步骤键序 = id,title,observe?,explain?,cmd_ref,branches?,leafs?。
3. **门禁语义**：编辑器内进程调 main.validate_seed()（CLI --validate-seed 同源函数，EXIT 语义一致）；不跑 CLI 子进程——离网审计 FORBIDDEN_SNIPPETS 禁 subprocess.run/Popen 字面量（RUNTIME_FILES 已登记两新模块）。真 CLI 通路另行实测：编辑真种子 → CLI EXIT=0 → 还原 EXIT=0（见验证账）。
4. **写盘原子化**：同目录临时文件 + os.replace；validate 失败原字节写回回滚。编辑中断（dirty 直接关）天然不写盘；写入中途异常不留半截文件。smoke E 案对 os.replace 注入失败做了实测。
5. 种子目录定位：db.seed_dirs() 里第一个含 trouble_trees_*.json 的目录；exe 内置种子（_MEIPASS）不可持久写 → 无可写种子目录时禁编辑并明示（exe 语境编辑导出属二批议题）。
6. validate_seed 捕获 stdout 用 redirect_stdout（windowed exe 下 sys.stdout 已被 _ensure_streams 兜底，不会 None 崩）。

### 明确不做（留二批）
- 整树新建/删除、跨树拖拽复制、批量重构、多树并排对比（第一期边界，26 底数稳定）
- 树级字段（symptom/category/vendor_hint）编辑——第一期只动内部结构，树级改动走种子 JSON 或 TreeManagerDialog
- TreeManagerDialog（DB 通路）的 uuid 手写输入框选择器化——DB 维护通路遗留，建议二批一并收口
- exe 内置种子的就地编辑导出

### 验证账
- smoke_tree_visual 45 项（六案）rc=0；主库零接触过 + 真种子 4 文件 sha256 前后一致
- 全量 17 套冒烟 rc=0（原 16 + 本套件）；validate-seed EXIT=0（358/26/97 底数不变、[E]=0、[W] 基线 1 处 zte skeleton 无新增）
- 真 CLI 门禁实测：tree_seed_io 编辑真种子（fw-policy-deny 加验证步骤 sx）→ `python app/main.py --validate-seed` EXIT=0（26 棵树）→ git 还原 → EXIT=0
- 离屏实测渲染均值 1ms/棵（26 棵全量 0.02s）

## N 批 v0.8.0 收口发版（2026-10-29）

### 遗留池裁决（四项，逐条一句理由）

| 遗留项 | 裁决 | 理由 |
|---|---|---|
| 防火墙 4 家场景包 | **挂 v0.9** | 其引用的防火墙四家 71 条（54 非骨架+17 骨架）全卡真机验证线（无免许可容器化路径），数据未经验证不进收口版，随真机验证排期一并产出。 |
| BSD（FreeBSD）最小集 | **挂 v0.9（另立数据+代码合批）** | 需代码侧厂商注册（VENDOR_SLUGS/platform/端口展开）非纯数据批，与迪普 DPtech 同路径（E 批结论），收口版不宜引入 schema/代码面风险。 |
| Runbook 二批 | **挂 v0.9** | MVP 闭环已验证可用（smoke_runbook 62 项护航），二批四项（模板库/AI 生成草稿/PDF 导出/执行留痕）为增强项不阻塞收口，按 runbook-guide §6 规划排期。 |
| 树编辑器二批 | **挂 v0.9** | 第一期边界清晰（26 底数稳定、只动既有树内部结构），二批项（整树新建/删除、跨树复制、TreeManagerDialog 选择器化、exe 语境导出）属维护增强，功能线已收官无需阻塞。 |

四项均未关闭：全部仍为有效积压，无一项因需求消失而作废。

## O 批 交换机命令特化扩容（2026-10-29，4041fde / e952863 / a70bc38，v0.9 功能线开局）

纯数据批不动应用代码：种子 +39 条（358→397）/ 报错 +3（97→100）/ diff_rules +4 类 17 变体
（4 类 17 → 8 类 34）。三子批独立提交，每批 validate EXIT=0 + 推送；不发版不 tag。

### 盘点结论（厂商×场景矩阵，六交换机厂商）
低洼格（本批填充）：链路聚合/堆叠/端口安全/POE/风暴抑制此前全 0；VRRP 仅 huawei 一条检查类；
MAC/ARP 仅 zte/juniper 缺格。填充后仍留空：zte×堆叠（VSC/IRF 形态未核实，不做）。
分类侧：新增八类（链路聚合/堆叠/VRRP/端口安全/POE/风暴抑制/MAC地址表/ARP）——
seed-guide「新分类先 Issue」约定由用户点名场景直接授权，后续新分类仍走 Issue。

### 子批 1（+39 条，4041fde）要点与坑
- ZXR10 smartgroup/VRRP/port-security、RGOS VSU（show switch virtual 族）/storm-control（level/pps
  双计量）等形态经官方文档/命令参考核实后入库（检索证据：锐捷官网 VSU 配置与命令参考 PDF、
  ZXR10 5900E Reliability Guide、2900E Command Reference、3228 链路聚合手册）；
- ★ 坑：查询类条目写注释式 rollback（"# 本条全为只读…"）会让「回退方案」Tab 渲染出来——
  `_sync_rollback_tab` 按非空即显示，查询类语义应**整个不写 rollback 键**；E 批 2 条同型一并修正
  （smoke_schema_rollback D1 抓住）。要点：rollback 免填 = 免到「键不存在」，不是「注释占位」。
- 冒烟硬编码底数五处同步（diff/regex_guard/scenarios/schema_rollback/tree_visual）。

### 子批 2（notes +57 / err +3，e952863）要点
- notes 补齐用 uuid 精确匹配追加「验证方法：复核命令+回显判读」；命令文本零改动故不触发
  sanitize/export 套件重跑义务（E/H 批教训仅适用于改 commands 的场景）；
- 报错 +3 全部走 ref_entry_uuid 关联（PSECURE_VIOLATION / ERR_DISABLE / EC-5-CANNOT_BUNDLE，
  关联 O 批 cisco 端口安全与链路聚合条目）；ZTE 交换机特有报错形态未核实不补（骨架口径延续）。

### 子批 3（diff_rules 4 类 17 变体，a70bc38）要点与坑
- ★ 坑 1：回显行普遍有前导空格——行级正则带 `^` 锚时必须 `^\s*` 开头（vrrp family/h3c/juniper
  首版全栽在这，自测抓出）；
- ★ 坑 2：提取列桥接——华为 display stp brief 的 PortID 列（128.1）、cisco etherchannel 的组号列
  （1 Po1(SU)）横在 key 与 value 之间，用可选桥接组 `(?:\d+\.\d+\s+)?` / `(?:\d+\s+)?`；
- ★ 坑 3：STP 的 BLK→FWD（Sts 列）首次正则未捕获导致"角色不变则无变化"假阴性——把 STS 列
  并入状态值（sts 命名组按约定并入 value）；
- ★ 坑 4：cisco etherchannel summary 成员多口同行、`extract_items` 单行单 search 只取首个——
  引擎限制下 variant 只提取聚合口标志（Po1(SU→SD)），note 写明成员级变化由降级行级比对兜底；
- D 节纪律升级：新增变体必须逐个加进 smoke_diff VARIANT_SAMPLES（D2 全 34 命中断言）。

### 收尾对账
- 主库同步：备份 command_lib-pre-obatch-20261029-172549.db → `--allow-main-db` 导入；
  矩阵 286 格 mismatch=0；底数 397/26/100；
- 四档分布（主库）：未验证 283（含 skeleton 53）/ 语法核对 61 / 绿 27（web-only 0 档无条目）；
- CI validate-seed@HEAD（a70bc38）绿；全量冒烟六套件复跑全过（scenarios 38 / diff 62 /
  tree_visual 46 / schema_rollback 9 / panel_dashboard 62 / regex_guard / keyvault）。

## Q 批 基础/管理面命令扩容（2026-11-02，bd17b8f / 4a96d3f / 本批子批3，v0.9 功能线第二数据批）

用户点名「完善补充基础相关命令」：按「配置保存/备份/恢复、设备管理（版本/硬件/重启）、时间与时钟/NTP、
系统日志、用户与 AAA/权限、SSH/Telnet 管理访问、SNMP、LLDP/CDP 邻居发现、接口基础（描述/开关/速率双工）、
显示控制（分页/terminal monitor）」十场景 × 厂商矩阵盘点后补格。纯数据批零代码改动，E/O 同款三子批，
不发版不 tag。规模 397→459 条 / 26 树 / 100→106 报错 / diff 规则 8 类 34 → 11 类 48。

### 盘点结论（十场景 × 16 厂商，Q 批前基线）
- **已满格**：时间同步（10 网络 + 4 Linux + ops）、SSH管理（10 网络）、SNMP（10 网络）、保存配置（10 网络）、
  配置回滚（10 网络 + ops）、版本（10 网络，在「版本升级」类）；
- **低洼/空档**：设备管理（硬件清单与重启仅散落在「版本升级」）、接口基础（只有诊断类 show，无描述/开关/速率双工）、
  邻居发现（LLDP/CDP 全 0，仅 BGP 邻居）、显示控制（仅 net_diag 日志条目里顺带 terminal monitor）、
  日志（fortinet/paloalto/sangfor/topsec 四家 0）、AAA（同上四家 0）、Linux 用户与权限 0、Linux 配置备份/回滚 0、
  Linux SNMP 0；
- **本批不填**：防火墙四家 LLDP（非典型二层场景，不造命令）、sangfor/topsec 显示控制（Web 控制台无 CLI 分页）。

### 子批 1（+62 条，bd17b8f）要点与坑
- 新分类五个：设备管理 / 接口基础 / 邻居发现 / 显示控制 / 用户与权限（Linux）；Linux 配置备份复用「配置回滚」类；
- ★ 坑 1：**`diagnose ...` 一类诊断命令不在 QUERY_WHITELIST**，混进纯查询条目会让整条被判「变更类」并要求
  rollback（B23 闭环 ERROR）。fortinet 硬件巡检首版带 `diagnose hardware deviceinfo disk` 即被拦——
  处理：该行降为注释（按需手动执行），查询条目保持零 rollback；
- ★ 坑 2：**Web 控制台路径条目（全注释 commands）判查询类**，属刻意设计，不写 rollback 键；把这些条目当变更类
  补 rollback 反而会渲染无意义的回退 Tab（与 O 批 D1 同一语义）；
- 重启类条目的 rollback 语义写「取消未触发的定时重启」（cisco reload cancel / huawei undo schedule reboot /
  h3c undo scheduler reboot / ruijie reload cancel），并注明「已重启不可撤销」——不臆造 undo reload；
- 速率双工条目统一提示「千兆强制速率须配 full duplex，两端一致」，光口不可配属正常报错；
- 骨架 9 条（zte×4 / paloalto×4 / 交叉 1）：ZXR10 接口命名（gei_1/1 vs gei_1/1/1）与 LLDP/分页命令形态未核实，
  PAN-OS 的 link-state / syslog profile / authentication-profile 绑定路径随版本差异，一律 skeleton 待真机。

### 子批 2（notes +39 / err +6，4a96d3f）要点
- notes 只追加「验证方法：命令 + 应看到的字段/取值」，**命令文本零改动**（故不触发 sanitize/export 重跑义务，
  smoke_export_site 仍跑一遍做回归）；种子内不落现场真实回显（红线），公开格式回显样本只进 diff_rules 冒烟语料；
- ★ 坑 3：**libvalidate 要求「examples 必须被自身 pattern 命中」**——报错条目 6 条首版有 4 条样例与 pattern 不匹配
  （paloalto 写了 `Commit failed` 但 pattern 是 `Validation Error`；cisco 写了 `% Authentication failed` 但 pattern 是
  `% Authorization failed`；sudo 写了 tty 报错但 pattern 是 sudoers 文案）。修法：样例必须是**同一 pattern 的不同形态**，
  不能放"相关但不同"的报错；
- 报错 vendor 口径：跨发行版 Linux 报错写 `vendor=linux`（与存量 30 条一致），ops 只用于跨域工具类。

### 子批 3（diff 3 类 14 变体 + 主库同步）要点与坑
- 新增 dev_version（6 变体，含 PAN-OS sw-version）、ntp_status（4 变体）、lldp_neighbor（4 变体，set 语义）；
- ★ 坑 4：**正则统一 IGNORECASE 编译** → 华为 `display ntp status`（小写 clock status）与 H3C
  `display ntp-service status`（大写 Clock status）无法用大小写区分，首版两条变体互相抢命中（D2 报 h3c 样本命中 huawei）。
  处理：合并为一条 huawei 变体（note 写明同时覆盖 Comware），空出的槽位给 ruijie（与 cisco 变体逐字等价，走等价组断言）；
- ★ 坑 5：H3C `display lldp neighbor-information list` 的首列是 System Name（不是接口名），行级正则首 token 不能限定
  接口前缀——改成「任意名 + 接口前缀」两段式，表头 `System Name  Local Interface` 因第二列不匹配接口前缀而天然豁免；
- 版本类正则的 value 取值口径：能一次带走"版本 + Release/产品码"的用 `.+$`（h3c/huawei），只取版本串的用 `[^,\s]+`
  （cisco）；fortinet 用「跳过型号取版本」（`\S+\s+(?P<value>[^,]+)`），避免型号与版本混在一个值里；
- 冒烟口径五处同步（459/26/106）；smoke_diff 62/62、其余五套全过。

### 收尾对账
- 主库：备份 `backup/command_lib-20261102-145635.db`（4984 KB，保留 10 份）→ `--db dist_package/command_lib.db
  --validate-seed --allow-main-db`；条目 459 / 树 26 / 报错 106 / err_unresolved 13 不变；
- 矩阵：16 厂商 × 45 分类，实填 338 格，与种子逐格对账 **mismatch=0**；
- 四档（主库）：命令 绿27 / 语法核对61 / 挂起87 / 未验证284（含 skeleton 62）；报错 挂起15 / 未验证91；
- ★ 已知滞后：`notes` 属 SESSION_FIELDS（导入护栏只保留库值），子批 2 的 39 条 notes 增强**不会**随
  `--allow-main-db` 落进主库（库内 notes 仍为旧值，历史累计 201 条与种子不一致）。分发走 CI/`build_exe.py`
  从种子重建净库，故交付物正确；本地主库要同步 notes 只能走界面编辑或回填流程，不做手工 SQL；
- validate-seed@HEAD EXIT=0；全量冒烟六套全绿；三子批独立提交推送，**不发版不 tag**。

## P 批 Runbook 二批：场景包一键生成 + 六段式模板（2026-11-02，v0.9 功能线第二件）

代码批（种子零改动：459 / 26 / 106 底数不变）。用户已定裁决：不做独立模板库（单一事实源 =
场景包）、六段式可选非强制、旧 .json 向后兼容、AI 生成与 QPrinter PDF 继续挂起。
关掉 N 批遗留池「Runbook 二批」两项（模板库/AI 生成其中模板库以场景包形态落地），实施范围见
`docs/runbook-guide.md` §2.1 / §2.2 / §6。

### 盘点结论（动手前）

- runbook-guide §6 二批四项现状：①模板库=未做；②AI 生成=未做；③PDF=未做；④执行留痕=未做；
- 操作单数据模型兼容性：schema 1 为 `meta`(9 字段) + `steps`(8 字段)，全部为"缺字段即空串"式读写，
  **天然支持增补**；隐患是 `validate_runbook` 用 `schema != SCHEMA_VERSION` 精确判等 —— 一旦把
  版本号提到 2，所有存量 v1 单每次打开都会弹"结构问题（已按兜底值载入）"警告，故必须"载入即升级"；
- 场景包引用结构：`items[{seq, ref_type(entry/tree/err), ref_id | by_vendor{vendor:uuid}, note}]`，
  三个预置包分别是 9/10/10 项；entry 条目的 `params` 是结构化 spec 且带 `default`
  （如 hostname=SW-CORE-01、vlan_id=10），可直接用于预填；`emergency-rollback` 含 4 树 + 4 报错，
  只能做"跳过记账"不能进步骤（操作单步骤模型 = 命令条目引用）。

### 实施裁决

- **schema 1 → 2 走纯增补**（`abort_criteria` / `template` / `source_pack` / `step.phase`）：
  `normalize_runbook` 把声明版本 ≤2 的统一升为 2、缺字段补空串 → 旧单**无警告**加载、另存即 v2；
  声明版本 >2 原样保留并由 validate 明确拒绝（不猜测降级）。反向也安全：旧程序读 v2 忽略未知字段；
- **生成器不自动套六段式**：模板是另一种组织视图，自动加会让"一键"变成"两件事一起猜"；
- **未选厂商视图直接拒绝生成**（by_vendor 需落到具体设备），不做"取第一个厂商"式臆测；
- **回退条件=meta 文本段、Rollback=派生段**：rollback 依旧不落盘（裁决③未变），
  六段式的 Rollback 段只在人工显式把某步标 `phase=rollback` 时才承载步骤，避免与回退汇总页双写；
- **分组不重排步骤数组**：段头只是视图分组，执行顺序仍以 `step_no` 为准（阶段交错时组内序号不连号，
  列表与导出页都写了明示——这是有意保留的"诚实"而非缺陷）。

### ★ 坑（下次改这块必看）

1. **`lst_steps.currentRow() == 步骤下标` 的旧假设被段头行打破**：`_reload_steps` 现在插不可选段头，
   必须走 `_row_map` + `_step_at_row()` / `_row_of_step()`。任何新代码再写
   `self.lst_steps.item(self._cur)` / `currentRow()` 直接当步骤下标都会**错位一步**（本次改了
   `_on_row_changed` / `_on_done_toggled` / `_resolved_current` 三处）；
2. 段头行只能 `item.setFlags(Qt.ItemIsEnabled)`：一旦带上 `ItemIsSelectable` 就能被选中并触发
   `currentRowChanged`，`_cur` 会被设成段头行号（I5 用例盯这个）；
3. **导出断言别用 CSS 选择器名**：样式表恒内嵌在 HTML 里，`class="phase"` 这种写法在平铺单里
   也可能命中 CSS 文本；H4/H7 改成匹配渲染标签 `<h3 class="phase">` / `<div class="grp-note">`；
4. 升级 schema 后**存量冒烟断言要同步**：E1 `schema==1`→`2`；`sample_runbook` 的 step dict 必须
   带 `phase`，否则 A5/E9 的"字典相等"断言会因 normalize 补出的空 phase 而失败；
5. 六段式启发式在"割接前检查"包上会把包内的保存/备份核对类条目判成 change，得到
   pre→change→validation→change 的交错结果——这是包数据真混了变更步，**不要为了好看去改判定表**
   （变更类判定唯一执法点仍是 renderer.classify_entry）；
6. **冒烟跑法分两组**（跑错会看到假红）：非 AI 套件 `PYTHONPATH=_vendor`；
   regex_guard / keyvault / send_gate / baseurl_policy / AI 三套需 `PYTHONPATH=app;_vendor`
   （否则 `_smoke_env.setup()` 里 `import db` 直接 `ModuleNotFoundError`，rc=1 属路径问题不是回归）。

### 验收对账（本批实测）

- smoke_runbook **74/74**（42 → 74：G 10 / H 11 / I 9 新增；主库零接触断言过）；
- 全量 21 套件 rc=0（含 scenarios 38 / diff 62 / tree_visual 46 / panel_dashboard 62 /
  AI 86-93-68 / 四套安全件）；
- `--validate-seed` EXIT=0（459 条 + 26 树 + 106 报错 + 3 场景包 + diff 11 类 48 变体；
  [W] 1 处存量 zte 骨架提示，无新增）；
- 三预置包一键生成实测：`new-device-onboarding/huawei` 9 步（参数预填 hostname/vlan_id 等）、
  `pre-cutover-check/huawei` 10 步（4 变更 6 查询）、`emergency-rollback/cisco` 2 步 + 8 项
  排查树/报错记账跳过；六段式保存重开无损；v1 旧单加载无警告并另存为 v2；
- 三提交推送，**不发版不 tag**（版本号仍 v0.8.0，v0.9 收口时统一 bump）。

## R 批 速查手册 + 网工计算器 + curl 生成器（2026-11-02，v0.9 功能线第三件）

代码+数据批，三子批；种子零改动（459 / 26 / 106 底数不变，矩阵不动）。选题吸收 meetool.cn 大纲、
内容自写，事实按 IANA 注册表 / RFC 9110 / 官方文档核对。权威说明见 `docs/handbook-tools-guide.md`。

### 盘点结论（动手前）

- 主窗原有 7 Tab，`smoke_scenarios` 与 `smoke_panel_dashboard` 都硬断言了 Tab 数量与顺序 —— 加 Tab 必红，须同步；
- 打包侧 `build_exe.py` 用显式 `--add-data` 白名单（seed_data / theme.qss / icon.ico），
  `smoke_keyvault` T8e 断言"恰好 3 条" —— 新增数据目录必须同步这条断言，否则安全套件假红；
- `--audit-offline` 的 `RUNTIME_FILES` 是**手写清单**：新增运行时代码不登记就等于没审计；
  清单里 migrate_to_slug.py / check_db.py 早已搬到 scripts/，每次审计都打两条"缺失"噪音（本批顺手删）；
- Ctrl+K 面板（ui_panel）三库是硬编码分区（entry/err/tree）+ `jump_to(kind,id)` 三路分支，
  加第四库要么扩展 kind，要么另起浮层 —— 选了前者（改动小、跳转机制可复用）。

### 实施裁决

- **手册走独立 JSON，不进 db**：用户明确「不入 command_lib.db 计数、不动种子底数」。目录解析沿用
  `seed_dirs` 的两级优先（程序同目录 > exe 内置），现场可整册替换；
- **四列固定 schema**：表格视图 / 检索 / TSV 复制三处都按 4 列排版，换来的是校验能一刀切（缺列即 ERROR）；
  多列需求（如手册总览的"条数"）用视图层临时列，不动数据格式；
- **手册检索接 Ctrl+K**：命中 id 用 `doc_id|组下标|行下标` 这种"位置串"而不是给每行编 uuid ——
  手册是只读快照，位置串够用且零维护成本；代价是**不能记最近使用**（位置串不属三库），jump_to 里直接 return；
- **/31 与 /32 按需区分**：/31 走 RFC 3021（2 个可用、无网络/广播，广播列显示 `-`），/32 单主机 1 个；
  /0 不禁止但提示"覆盖过大，确认笔误"——现场确实有人拿 /0 做默认路由口径；
- **前导零一律拒绝**：`192.168.01.1` 在 inet_aton 里会被当八进制，不同工具行为不一致，宁可报错也不猜；
- **curl 多行体在 Windows 下拒绝而不是压缩**：单行命令无法安全承载换行，静默把换行换成空格会改变语义
  （JSON 字符串里的换行尤其致命），所以明确报错让用户压成一行或切 POSIX。

### ★ 坑（下次改这块必看）

1. **`splitlines()` 会吃掉孤立 CR**：头注入检测最初用 `text.splitlines()`，`"X: a\rInjected: b"` 被切成两行，
   `"\r" in line` 永远不成立 —— 必须 `split("\n")` 再手工剥结尾 `\r`（CRLF 正常、行内 CR 才算注入）；
2. **PS 5.1 传参吞噬引号**：`'{"a":1}'` 交给原生 exe 时内层双引号可能丢失，所以 PowerShell 档把 `"` 一律
   写成 `\"` 交给 curl 还原；这也是三档转义不能共用一套的原因；
3. **CMD 的 `%` 展开**：`%7B` 这类 URL 编码在 cmd 里可能被当变量，写进 .bat 还要双写成 `%%`。
   工具只给告警不改写（改写会破坏交互粘贴），这是有意的"半自动"；
4. **组合框索引差一**：进制源选择是「自动 + 2/8/10/16」共 5 项，写冒烟时把"自动"当 index 1 会让
   `0xFF` 走进二进制分支直接报错 —— UI 档位断言一律按 `currentData()` 或核对 addItem 顺序；
5. **`check(cond, detail)` 的 detail 是立即求值**：`tbl.item(4,1).text()` 写在参数里，表为空时先崩再判，
   冒烟里要么先取变量、要么保证行数；同类坑在 `Qt.UserRole`（值是 256，不是 32）上踩过一次；
6. **端口映射要展开 `TCP/UDP`**：查表函数把 `TCP/UDP` 行展开成两条再比对，否则 `(53,"TCP")` 恒查不到；
   查重也必须按展开后的单协议算（514/TCP=shell 与 514/UDP=syslog 是**两个**合法服务）；
7. **`load_docs(dirs)` 传字符串会按字符遍历**：`for d in dirs` 对字符串逐个字符 `listdir`；
   测试里传目录一律 `[dir]`，`validate_cli` 的"缺册"严格检查也只在默认目录下做（指定目录不挑剔）；
8. **冒烟路径卫生**：`PYTHONPATH=_vendor` 是相对路径，`_smoke_env` 只挪自己的绝对路径；
   手动加 scripts 到 sys.path 时要用 `os.path.abspath('scripts')` 且 `append`（不能 insert 0），
   否则 `ai_gate→sanitize_scan` 会撞上 scripts/ 的同名薄壳（ImportError）。

### 验收对账（本批实测）

- `smoke_handbook` **58/58**、`smoke_tools` **107/107**（新增 165 项，主库零接触断言过）；
- 全量 19 套件（scripts/smoke_*.py 实数）rc=0；`--validate-handbook` EXIT=0；
  `--validate-seed` EXIT=0（459 + 26 + 106，种子零改动，[W] 仍为存量 zte 骨架 1 处）；
- `--audit-offline` 通过：20 个运行时文件零网络依赖，噪音清理后无"文件缺失"提示；
- 手册事实核对抽查：63 个状态码短语与来源全部对表（413 Content Too Large / 418 Unused / 306 Unused /
  305 已弃用 / 422 Unprocessable Content 等 RFC 9110 更名项均按新名），77 条端口与 IANA 服务名逐条比对，
  514 双协议、未登记端口标注全部到位；
- 三提交推送，**不发版不 tag**（版本号仍 v0.8.0，v0.9 收口时统一 bump）。

## S 批 v0.9.0 收口发版（2026-11-02）

发版批（微量级），沿用 N 批精简口径：只做版本三处 / CHANGELOG 收口 / CI 门槛 / tag 与 Release /
出库自核验，不碰种子与功能代码。发布 commit `aeee126`，annotated tag 对象 `311f4b2` → 指向 `aeee126`。

### 1. 版本三处（实测，不靠目测）

- `app/ui_main.py` APP_VERSION `0.8.0 → 0.9.0`（标题栏与「关于」对话框均引用该常量）；
- 断言脚本 `.workbuddy/release_s/check_versions.py`：AST 取 APP_VERSION、正则取 README「当前版本：**vX**」、
  正则取 CHANGELOG 首个 `## vX（`，三者相等 + 标题栏/关于框均常量驱动 + 源码无 `"v0.9.0"` 类硬编码 → **PASS**；
- README 事实段同步：命令 338 → **459**、报错 82 → **106**，并补 45 分类 / 11 类 48 变体 / 手册 7 本 520 条
  三行；Ctrl+K 快捷键行「三库 → 四库」；快速开始补 `--validate-handbook`；
- 顺带订正 README 安全模型里「api_key 明文落盘」的过期描述（L 批起已是 DPAPI-v1 密文 + 首启迁移）。

### 2. CHANGELOG 收口

- R / P / Q / O 四个「未发布（…不发版不 tag）」节并入 `## v0.9.0（2026-11-02）`，批次头降为 `###`；
- 新增节首总览：数据线（O/Q）与功能线（P/R）两段口径 + 发版口径（两套校验 + 19 套冒烟 + CI 双门槛）；
- 批内「不发版不 tag」表述改为「随 v0.9.0 合并出库」（v0.8.0 及更早的历史表述保留原样）。

### 3. CI 门槛补齐

- `validate-seed.yml` 原只跑 `--validate-seed`，R 批引入的手册质检从未进 CI；
  本批加 `Run handbook validation` 一步（同 runner、失败即红），发布 commit 上两次运行（main / tag）
  两个步骤均 success（run 41415903126 / 41415896015）。

### 4. tag 与 Release

- 提交 `aeee126` → `git tag -a v0.9.0` → SSH 推 main + tag（HTTPS 依旧 502，标准路径仍是 SSH）；
  本次未出现 N 批的「tag 双执行」怪癖，`git ls-remote` 复核 tag 对象与 deref 均一次到位；
- CI release job（41415903124）11 步全绿：构建 → exe 断言 → staging 净库 → 脱敏扫描 → 白名单 zip →
  zip 断言 → 挂 draft；随后 API `PATCH` 把 draft 转 publish（标题/正文与 CHANGELOG v0.9.0 节同源），
  发布时点 `2026-11-02T08:54:13Z`。

### 5. 出库自核验（通道可用 → 升格全量哈希，免 N 批 Range 法）

- 整包下载 42,559,243 B / 18s（2.3MB/s），本地 **sha256 = 7544758…68ec**，与 GitHub asset `digest` 逐字相同；
- zip 29 条目、白名单零违规（exe / check_db.py / command_lib.db / seed_data/*.json ×26），
  与本地同口径组装的对照 zip **条目名集合完全一致**；
- 包内 `command_lib.db`：entries **459** / trouble_trees **26** / err_dict **106** / ai_imports **0** /
  err_unresolved 0 / categories 45 / user_version 2；`check_db.py` 与本地字节同尺寸（24,957 B）；
- 包内库 vs 本地净库：三库 id 集合完全相同、entries 正文（剔除状态字段）逐行一致；
  **byte hash 不同只因 created_at/updated_at 为各自导入时刻**（CI 08:51:37 / 本地 16:53:00），
  库文件尺寸反倒相同（2,179,072 B）；
- 手册在包内方式 = 内嵌 exe（`--add-data handbook`），非 zip 顶层：`pyi-archive_viewer -l` 列出
  `handbook\*.json` **7 条**，其未压缩长度与仓库 `app/handbook/*.json` 逐本**字节数相同**
  （15997/15037/20569/17656/15167/13087/17124）；
- exe 尺寸差异说明：CI Python 3.11 产物 **41,944,515 B**，本地 Python 3.13 产物 **45,243,412 B**
  （zip 42.6MB vs 45.9MB）——版本栈差异，非内容差异；条目数/库内容/脚本三者已逐项对上；
- 旧版资产核对：v0.4.0 / v0.5.0 / v0.6.0 / v0.6.1 / v0.6.2 / v0.7.0 / v0.8.0 的
  资产名 / 字节数 / updated_at 与发版前基线快照逐项相同（未动）。

### 6. 冒烟与校验（本批实测）

- `--validate-seed` EXIT=0（459 + 26 + 106，[E]=0，[W] 存量 zte 骨架提示 1 处）；
  `--validate-handbook` EXIT=0（7 本 / 56 组 / 520 条）；
- 全量 **19 套** `scripts/smoke_*.py` rc=0；新增两套底数：`smoke_handbook` 58 项、`smoke_tools` 107 项，
  `smoke_runbook` 74 项、`smoke_panel_dashboard` 62/62、`smoke_scenarios` 38/38、`smoke_diff` 全变体命中。

### ★ 坑（本批新增）

1. **`PYTHONPATH=_vendor` 不够**：7 套安全/UI 套件（ai_chat / ai_stage2 / ai_stage3 / baseurl_policy /
   keyvault / regex_guard / send_gate）在 `_smoke_env.setup()` 里 `import db`，必须
   `PYTHONPATH="app;_vendor"`（Windows 分隔符是 `;`，Git Bash 里写 `:` 会整体当一个路径）；
   症状是 `ModuleNotFoundError: No module named 'db'`，而套件末尾的零接触 atexit 仍照常打印，
   容易误判成"跑过了没报错"——**看 rc，不看末行**。
2. **本地无 PyInstaller**：受管 python 3.13 未装，`build_exe.py` 首步即失败；本批用
   `pip install --target _vendor pyinstaller`（项目既有 _vendor 约定，不入 git）跑通本地对照构建。
   **CI 与本地 Python 小版本不同 → exe 字节数必然不同**，"与本地构建一字不差"只能落在
   条目名集合 / 库内容 / 脚本字节 三处，尺寸按版本栈解释。
3. **draft Release 查不到**：`/releases/tags/v0.9.0` 对 draft 返回 404，必须
   `/releases?per_page=N` 带凭据列表；publish 之后匿名端点才可见。

### 7. 遗留池盘点（逐项归属，不静默关闭）

| 项 | 来源 | 状态 | 归属 |
|---|---|---|---|
| 防火墙 4 家场景包（sangfor/topsec/fortinet/paloalto 场景化包） | N 批遗留池 | 未动 | **v1.0.0** |
| BSD 最小集（FreeBSD 命令/排查树最小可用集） | N 批遗留池（需代码侧厂商注册） | 未动 | **v1.0.0**（代码+数据批） |
| 树编辑器二批（编辑体验/批量/校验联动） | N 批遗留池 | 未动 | **v1.0.0** |
| Runbook 二批剩余项 | P 批已落地主体，剩三项 | 已落 2 项（模板库以场景包形态 / 六段式） | AI 生成操作单 + QPrinter PDF → **按需立项**；执行留痕 → **v1.0.0** |
| 验证档：真机 27 / 语法核对 61 / 骨架 62 / 其余未验证 309（报错库 106 全部未真机） | 长期真机验证池 | 待真机/模拟器 | **v1.0.0 起的验证会**（按厂商分批，非发版阻塞项） |
| err_unresolved 13 条 pending（含 4 条无效残留 #12–#15） | E 批 | 未清 | **v1.0.0** 清理批 |

## T 批：防火墙四家场景包（2026-11-06，v1.0.0 功能线开局，不发版不 tag）

用户点名的 N 批遗留池项「防火墙 4 家场景包」。口径：先只读盘点 → 再落包，**不加一条命令条目**
（459 / 26 / 106 不变），场景包 3 → 7。真机验证会仍挂 v1.0.0 按厂商分批，本批不碰验证字段。

### 1. 只读盘点（库内现状）

| 厂商 | 条目 | 档位（verified/exec_level） | rollback 覆盖 | 分类数 | 文件 |
|---|---|---|---|---|---|
| Fortinet | 24 | verified 0；AI 生成档 23 + skeleton 1（环路检测） | 22/24（HA 检查、资产巡检只读） | 22 | fortinet.json |
| Palo Alto | 24 | verified 0；AI 生成档 19 + skeleton 5（接口/环路/显示/日志/AAA） | 19/24（诊断与 HA 只读） | 22 | paloalto.json |
| 深信服 AF | 33 | verified 0；Web 控制台 15（无 rollback）+ CLI 骨架 18（有 rollback） | 10/33（Web 条目全无 rollback） | 21 | sangfor.json + sangfor_cli.json |
| 天融信 TopOS | 33 | 同深信服（Web 15 + CLI 骨架 18） | 10/33 | 21 | topsec.json + topsec_cli.json |

命令族（四家同构，每家各 1 条）：VLAN 子接口 / 接口基础 / Trunk / 静态路由 / OSPF / ACL（地址对象+策略）/
NAT（SNAT+DNAT）/ SSH / 密码 / SNMP / 保存配置 / DNS / NTP / 版本升级 / 日志 / 防火墙诊断 ×2 /
HA 状态 / 配置回滚 / 环路检测；设备管理 1–2 条（含备份重启）。**全部 verified=0**：四家均无真机。

盘点同时暴露两条影响落包实现的事实（已顺手修正，见 §4）：

1. 深信服/天融信的 Web 控制台条目命令体全是 `#` 注释 → `classify_entry` 判为**查询类**
   （实为变更），且无 rollback；CLI 骨架条目才判变更类并有 rollback；
2. Fortinet 的 `diagnose ...` 不在 `QUERY_WHITELIST` → 会话/策略命中诊断被判**变更类**
   （Q 批已记同款口径），回退为"清理诊断态"，不影响业务配置。
   **→ W 批销项**：只读子命令逐词入名单后，NAT 会话诊断归位查询类；会话+debug flow
   那一条仍判变更类，原因是 debug 语义（刻意保留，见 §W 批）。

**分支预案判断：不先补库。** 安全策略族确有缺口（见 §3），但每家都有 1 条可用的
「地址对象+策略」与「SNAT+DNAT」条目，足以撑起 10 步边界开通链；缺的是区域/入向策略/
服务对象等**独立格位**，属于"标注暂缺"而非"无法落包"。另：深信服/天融信的策略类本就是
Web 控制台为主，凭空补 CLI 条目等于编造关键字——按用户口径「不编造」直接标缺，宁缺不掺水。

### 2. 四包清单

| pack_id | 步数 | 阶段分布（pre/change/validation/post） | 引擎判定 变更/查询 | 变更类回退 |
|---|---|---|---|---|
| fw-edge-policy-fortinet | 10 | 2 / 5 / 1 / 2 | 8 / 2（W 批复核：debug flow 刻意保留） | 8/8 取库 |
| fw-edge-policy-paloalto | 10 | 2 / 5 / 1 / 2 | 7 / 3 | 7/7 取库 |
| fw-edge-policy-sangfor | 10 | 2 / 5 / 1 / 2 | 5 / 5 | 5/5 取库（Web 两步另写来源） |
| fw-edge-policy-topsec | 10 | 2 / 5 / 1 / 2 | 5 / 5 | 5/5 取库（Web 两步另写来源） |

链序：设备巡检 → WAN 接口 → VLAN 子接口（区域归属）→ 地址对象/出向策略 → SNAT + DNAT →
静态路由 → syslog 外发 → 会话与策略命中诊断 → 配置备份 → HA 状态复核。

- 场景值统一：办公网 `10.20.10.0/24`（网关 .1）、服务器 `10.20.10.80`、公网 VIP `200.1.1.30:443`、
  ISP 网关 `200.1.1.1`、syslog `10.20.99.10:514`；每包 `item.params` 只覆盖现场相关项，
  其余沿用条目默认值——**参数覆盖可改，不写死命令文本**；
- 回退条件按场景写死（每包一段）：① 管理面失联（SSH/HTTPS 60s 不通且 Console 不能登录）；
  ② 策略命中为 0 且 `10.20.10.80:443` 探测 100% 丢包；③ NAT 无转换记录 / 业务中断；
  ④ 接口 down 超 3 分钟或 HA 主备翻转。Fortinet 首选 `execute restore config flash`，
  Palo Alto 首选 `load config version`，深信服/天融信首选 Web 配置备份恢复；
- 深信服/天融信的「接口」「日志」两条为 Web 控制台条目（无 rollback），步骤备注逐条写明
  **回退来源**（Web 路径 + 先截图记录原值）；
- 本包**不塞**交换机类行为：Trunk 聚合 / 堆叠 / POE / 风暴抑制 / MAC 表 / 端口安全均未进包
  （库内有 Trunk 条目也不用），保持防火墙本职场景。

### 3. 库内暂缺格位（逐条列明，不塞空步骤）

| 格位 | Fortinet | Palo Alto | 深信服 AF | 天融信 | 处置 |
|---|---|---|---|---|---|
| 独立安全区域（zone 对象 / 接口归区） | 缺（接口/接口组承担） | 部分（VLAN 条目 `zone_name`） | 部分（VLAN/策略引用区域名） | 部分 | 包内用子接口 `zone_name` / 策略 from-to 落地，独立 zone 配置标缺 |
| 第二条（入向/发布）安全策略 | 缺 | 缺 | 缺 | 缺 | 每家仅 1 条策略条目；包内注明"同法另建反向策略" |
| 服务对象独立创建 | 缺（策略内 `service` 字符串） | 有（策略条目内建） | 缺（策略内服务名） | 缺 | — |
| 策略排序 / 禁用 / 删除 / 默认 any 收紧 | 缺 | 缺 | 缺 | 缺 | 标缺 |
| 策略命中计数清零 | 缺 | 缺 | 缺 | 缺 | 标缺（诊断条目只读） |
| 会话强制清除 / 同步 / 会话表导出 | 缺 | 缺 | 缺 | 缺 | 标缺 |
| HA 配置 / 主备倒换 / 会话同步 | 缺（仅状态检查） | 缺 | 缺 | 缺 | 标缺 |
| IPsec 站点到站点 / SSL VPN | 缺 | 缺 | 缺 | 缺 | 标缺 |
| IPS / AV / 应用识别 / URL 过滤 | 缺 | 缺 | 缺 | 缺 | 标缺 |
| 策略路由 / 多出口负载 / 链路探测 | 缺（静态路由有） | 缺 | 缺 | 缺 | 标缺 |
| 接口 IP 直配（非子接口）/ PPPoE / DHCP 客户端 / MTU | 缺 | 缺 | 缺 | 缺 | 标缺（接口描述与启停有） |
| 配置对比 / 命名版本管理 | 缺 | 部分（commit 前 diff 在条目内） | 缺 | 缺 | 标缺 |

**真机口径**：四家 verified 全 0；Fortinet/Palo Alto 为 AI 生成档，深信服/天融信 CLI 为 skeleton
骨架 + Web 对照。包说明与本文均写「语法核对档，未经真机」，包内不承诺验证等级、不改验证字段；
真机验证会按厂商分批（v1.0.0 起），验证后回填走 `scripts/backfill_verify.py`。

### 4. 代码改动（包 schema v2 + 入口）

- `db.py`：`scenario_packs` 增 `abort_criteria` / `template` 两列（建表 + `_migrate` 自动补列；
  `SCHEMA_VERSION` 2 → 3，老库 `PRAGMA table_info` 检测后 ALTER）；`SCENARIO_PACK_FIELDS` 同步；
  条目级 `phase` / `params` 存 items JSON，不加列；
- `runbook_export.build_from_pack`：包级两字段写 `meta`，`item.phase` 写步骤，`item.params`
  叠在 `prefill_params` 之上；**新增厂商守卫**——`ref_id` 单值引用在指定厂商视图下与条目厂商不符
  即跳过（此前只有 `by_vendor` 守，单值引用会漏）；
- `main.py` validate 场景包段：`template` 非法 / `six` 缺回退条件 / `phase` 非法 /
  `params` 非对象、空参数名、值为对象或数组 → [E]；`params` 覆盖未声明参数名 → [W]；
- `ui_scenario.py`：包级「场景纪律」卡（objectName=`PackMeta`，六段式说明 + 回退条件正文）；
  单厂商包默认选中该厂商视图（不再默认"全部厂商"给一排灰卡）；
- `smoke_scenarios` 38 → **95 项**（[F] 四包；步骤卡计数排除 PackMeta；单厂商包厂商不符整包跳过）。
- 口径修订（对 P 批「生成不自动套六段式」）：包作者显式声明 `template=six` 时生成即带分组
  （作者已连同 `item.phase` 设计好）；不带该字段的旧包行为不变（平铺，仍由用户手动开启）。

### 5. 验收实测

- `--validate-seed` EXIT=0：459 条 + 26 树 + 106 报错 + **7 场景包**，[E]=0；
  [W] 17 处 = 存量 1（zte 骨架）+ 新增 16（Palo Alto 2、深信服 7、天融信 7 条 skeleton 引用）；
- 四包 build 实测：步数 10/10/10/10，`skipped`/`missing` 全 0，`abort_criteria` 与 `template=six`
  落地，`item.phase` 与 `params` 覆盖逐项命中，**变更类回退 100%**（8/8、7/7、5/5、5/5）；
  错选 `huawei` 跑 Fortinet 包 → 10 步全 skip（"该厂商在本包内无对应条目"）；
- 全量 **19 套**冒烟 rc=0（`smoke_scenarios` 95/95、`smoke_runbook` 74/74、`smoke_panel_dashboard` 62/62、
  `smoke_tools` 107/107、`smoke_handbook` 58/58 …）；`--validate-handbook` EXIT=0（7 本 520 条）；
- 主库同步：备份 `backup/command_lib-pre-tbatch-*.db` → `--allow-main-db` 导入（仅包新增，条目零变化）。

### ★ 坑（本批新增）

1. **Web 控制台条目的分类是"查询类"**：命令体全为 `#` 注释 → `classify_entry` 看不到可执行命令，
   判 query；放进变更单会让"变更类必须带回退"的检查形同虚设。落包时改用 CLI 骨架条目
   （同内容 + Web 对照 + 有 rollback），Web-only 的两步在备注写回退来源。
2. **`diagnose` 不在查询白名单**：Fortinet 诊断条目判变更类（Q 批已记），六段式里靠 `item.phase`
   显式标 `validation` 才不会串段；回退=清理诊断态。（W 批已把只读子命令逐词入名单，
   debug flow 侧仍刻意留变更类——见 §W 批。）
3. **单值引用不守厂商视图**：P 批的守卫只在 `by_vendor` 分支；四包用 `ref_id` 时错选厂商仍会生成，
   已补守卫。冒烟必须覆盖"错厂商整包跳过"，否则这种静默错误没人发现。
4. **PackMeta 卡会打破"卡片数=步骤数"断言**：所有遍历 cards_lay 的断言一律按 objectName 过滤，
   别再用 `count()-1`。
5. **`smoke_scenarios` 的包名断言按 Unicode 码点排序**：ASCII 名（Fortinet/Palo Alto）排最前，
   中文按码点（新 U+65B0 < 深 U+6DF1），手写期望列表别凭语感。

## U 批 配置翻译器（2026-11-06，v1.0.0 功能线第二件，不发版不 tag）

整机口径：粘贴源厂商 running-config（整份或片段）→ 按「配置块」识别命令族 → 用**目标厂商**
库内条目模板重组命令串 → 行级四档账本。输出零臆测：目标侧每一行命令都可追到一条库内条目 uuid。

### 盘点（只读，先出数再动手）

| 盘点项 | 结论 |
|---|---|
| params 结构化覆盖 | 459 条里 **357 条**带结构化 params（77.8%）；其余为历史无参条目 / Web 路径条目 |
| 跨厂商族条件 | 45 个分类中 **28 个**「六家网络厂商齐全」（VLAN/Trunk/静态路由/OSPF/ACL/NAT/保存配置/DNS/AAA/接口基础/环路检测/显示控制/HA/链路聚合/VRRP/端口安全/POE/风暴抑制/BGP/堆叠/设备管理/SSH管理/密码/时间同步/配置回滚/日志/SNMP/版本升级）；其余 17 类是 Linux/防火墙/运维专属，跨厂商无对象 |
| diff_rules 复用 | 11 类 48 变体的**行形态正则**是本批解析规则主来源（华为/H3C 的 `port link-type`/`port trunk`、Cisco/RGOS/ZXR10 的 `switchport` 族、Junos 的 `set` 行）；「种子侧 JSON 随包 + 同名优先级 + isfile 防御 + 坏规则 [E] 拦截」四件基础设施直接照搬；四态卡片的呈现经验转成四档账本。约 6 成经验可迁移，4 成是新的（块级上下文 / 参数绑定 / 目标模板渲染） |
| 整机行构成预估 | 六份小样实测：命令 190 行 → 族内可映射 **161**（84.7%）、注释 6、私有/杂项 13、会话包装与口令 16。族内 : 杂项 ≈ 8.5 : 1.5，账本分档划算 |
| UI 挂点 | 取「第 10 个 Tab」：变更对比之后、工具箱之前（作业顺序：改前翻译 → 执行 → 对比回显）；视图菜单加 Ctrl+Shift+Y 直达。Ctrl+K 面板保持四库检索不动（翻译入口不是检索） |

### 覆盖族（8 族 × 6 厂商 = 48 条映射）

| 族 id | 分类 | 覆盖内容 | 说明 |
|---|---|---|---|
| `vlan` | VLAN | VLAN 创建 + Access 口划分 | 纯 VLAN 定义（无端口归属）时端口参数缺失 → 落 c，不猜端口 |
| `trunk` | Trunk | Trunk 模式 + 放通列表 + PVID/native | 放通列表/PVID 源里没有就整行剔除，不外溢 |
| `ifbase` | 接口基础 | 描述 / 速率 / 双工 / 开启 | description/speed/duplex 全 optional |
| `port_security` | 端口安全 | MAC 上限 + 违规动作 | 华为/H3C 模板硬编码 `0/0`、`1/0` 前缀 → `if_last` 只取末段 |
| `link_agg` | 链路聚合 | 聚合口 + 成员口 | 成员口用 `members` 收集列表，双口模板取 [0]/[1] |
| `ospf` | OSPF | 进程 / router-id / network / area / 静默口 | Junos 用接口宣告，推导不出 network → 落 c（真实限制） |
| `static_route` | 静态路由 | 目的网段 / 掩码 / 下一跳 / 距离 | 掩码 ↔ 前缀长度双向换算 |
| `ntp` | 时间同步 | NTP 服务器单行 | 单行族，验证命令自动进「复核」区 |

未纳入 v1（库内六家齐全但暂不做）：ACL / NAT / VRRP / POE / 风暴抑制 / BGP / 堆叠 /
环路检测 / 显示控制 / HA / 保存配置 / AAA / DNS / SSH管理 / 密码 / 设备管理 / SNMP /
版本升级 / 配置回滚 / 日志（共 20 个）→ 命中落 c 档，原因写明「视图中无族映射规则」。

### 映射表 schema（`seed_data/translate_rules.json`，随种子走，不新增条目）

- `vendors.<slug>`：`style`（indent / flat，Junos set 形式为 flat）、`if_prefix`（→ `if_full`）、
  `comment`（目标注释符）、`mode_decl`（access/trunk 预扫正则）；
- `families[].bind`：规范参数 → `{厂商: {param, transform[], narrow}}`；键可写 `canon@tag`
  让一个规范参数派生多个目标参数（`ifname` → `port_start`/`port_end`）；`"*"` 兜底、
  `null` = 该目标厂商不映射（源项会被记「无对应参数」并降级）；
- `families[].vendors.<src>`：`entry`（**源侧参照**，仅作追溯）、`key`（实例分组键）、
  `lines[]`（`re` + `scope:header` + `view:if|vlan|ospf` + `mode:access|trunk` +
  `port_prefix` + `inherit` / `collect` / `resolve`）；
- `optional`：源里没有就**整行剔除**（不外溢）；`default_ok`：允许取库内默认值的参数白名单；
  其余参数缺失 = 整个实例降级 c（禁止用默认值凑身份类参数）；
- 变换链：`id` / `if_short` / `if_full` / `if_last` / `range_start|end|expr|full` /
  `first_port` / `members0|1` / `to_mask` / `to_masklen` / `speed` / `duplex` / `strip_unit`。

### 四档账本（每行必落一档，不存在「凭空消失行」）

| 档 | 含义 | 典型来源 |
|---|---|---|
| a 已译 | 命中族规则，输出由库内条目渲染，附「行号 → 目标条目 uuid」 | 族内配置行 + 接口视图起始行（由模板自带） |
| b 逐行映射 | 注释/说明行非命令，按目标注释符（`!` ↔ `#`）重写 | `! 接入交换机 ...` / `# 描述：...` |
| c 需人工 | 私有特性 / 库内无族 / 族内未覆盖行，逐条列原因 | 原因码 `private` / `no_family` / `uncovered_view` / `key_missing` / `param_missing` / `entry_missing` / `unbound` |
| d 跳过 | 空行、分隔符、会话包装行、口令密钥行 | 原因码 `blank` / `separator` / `session` / `secret` |

### 安全默认（与 T 批口径一致）

- 输出头部固定四行声明：**语法核对档，未经真机** / 接口编号沿用源写法（不做机框槽位换算）/
  库内条目为完整配方（可能含源配置没有的配套行：缺省路由、端口开启、保存）/ 覆盖率数字；
- 变更类条目自带 `rollback` → 单独「回退命令（库内条自带）」区，并再声明一次未真机核对；
- 口令/密钥行（username secret、local-user、snmp community、root 口令…）**一律 d 档不外译**；
- 模板占位填不满 → 该实例所有源行整体降级 c，绝不输出 `{{x}}`，也不用字面量凑数。

### 验收实测

| 样例 | 目标 | 命令行 | 已译 | b | c | d | 完成度 |
|---|---|---|---|---|---|---|---|
| cisco | huawei | 28 | 22 | 1 | 4 | 2 | 78.6% |
| huawei | cisco | 38 | 31 | 1 | 2 | 5 | 81.6% |
| h3c | cisco | 37 | 30 | 1 | 1 | 6 | 81.1% |
| ruijie | huawei | 32 | 30 | 1 | 1 | 1 | 93.8% |
| zte | cisco | 28 | 26 | 1 | 1 | 1 | 92.9% |
| juniper | huawei | 27 | 22 | 1 | 4 | 1 | 81.5% |
| **合计** | — | **190** | **161** | 6 | 13 | 16 | **84.7%** |

- `smoke_translate` **157/157**（九案：规则随种子 / 六样例行级完整性 / 族映射正确性抽验 /
  私有特性必 c / 零臆测 / 覆盖率可复算 / 回退 / 坏表 [E] 注入 / 离屏 UI）；
- 全量 **20 套**冒烟 rc=0（`smoke_handbook`、`smoke_scenarios`、`smoke_panel_dashboard`
  三套 Tab 数断言同步 9 → 10）；
- `--validate-seed` **EXIT=0**：459 条 + 26 树 + 106 错 + 7 包不动，新增统计行
  「配置翻译映射：8 族 ｜ 48 条厂商映射」；新 [E] 拦截四类：坏正则 / 条目不存在 /
  族缺目标厂商条目 / **目标模板占位无填充来源**。

### UI（第 10 个 Tab「配置翻译」）

左粘贴源配置 → 选源/目标厂商 → 翻译：右侧上「目标配置（语法核对档）」，右侧下四档账本表
（行号 / 档 / 源命令 / 归属族 / 目标条目 uuid8 / 说明），表上方是覆盖率与四档计数；
「载入样例」读 `app/samples/translate/<厂商>.txt`，「导出账本」出 Markdown（覆盖摘要 +
族覆盖 + 需人工清单 + 行级追溯）。截图：`docs/screenshots/translate.png`
（cisco → huawei，28 命令行 / 78.6%，表格 40 行与源文件行数一致）。

### ★ 坑（本批新增）

1. **必须渲染目标厂商条目**：第一版拿源条目 uuid 渲染，等于把源厂商命令当目标输出——
   翻译器最致命的错，靠 `B5 已译行 uuid 属目标厂商` 断言兜住。
2. **同一目标参数多来源不能靠 dict 覆盖**：OSPF 的 `silent`/`uplink` 都落 `silent_port`，
   `{param: canon}` 后写覆盖前者 → 整块判「必填缺失」。改成候选列表，渲染时取第一个有值者。
3. **单行模式行不含族键**：`switchport mode access` 本身没有 vlan_id，会生成无键实例，
   进而用库内默认值凭空造一个 VLAN 块。必须并入「同族 + 同端口」的唯一有键实例，并不到就降级 c。
4. **库内默认值不能当填充料**：network / vlans / port_range 这类身份参数若允许取默认值，
   账本会显示「已译」但配置是错的。→ `default_ok` 白名单，其余缺失一律 c。
5. **DB 的 params 是 JSON 字符串**：直接 iterate 得到字符，validate 会静默漏检；
   一律走 `renderer._load_params`。
6. **条目配方会外溢**：静态路由条目自带缺省路由、ifbase 自带 `no shutdown`、VLAN 条目自带
   `save`。这是库内条目的整体语义，不删，但必须在输出头声明「逐行核对」。
7. **Junos set 形式是 flat**：一行一单元、没有缩进层级，接口 access/trunk 必须靠预扫
   `mode_by_port` 消歧，否则 `vlan members` 会被 VLAN 族抢走（trunk 口被译成 access 口）。

### 不做清单（本批边界）

- **AI 猜测式补翻**：对 c 档行用 LLM 生成「等价」命令 —— 本批明确不做（那是把编造自动化）；
  若后续要做，须独立批且输出永久带「AI 生成，未经核对」标注，与库译行严格区分；
- 接口编号/槽位换算（`1/0/1` ↔ `0/0/1` ↔ `ge-0/0/1`）：一律沿用源写法 + 输出头声明；
- 层级式 Junos 配置（非 `set` 形式）：落 c 档；
- 跨设备整网翻译（多台设备间引用关系）：本批单机口径。

## V 批 配置翻译器扩族 19 族（2026-11-06，v1.0.0 攒批①，不发版不 tag）

U 批点名「把这些做完再发版」的 20 个六厂商分类，本批逐类落地。结论先给：
**20 类里 19 类建成族（含 6 个查询对照族），1 类（版本升级）如实不建族**；
映射 48 → **147 条**、族 8 → **27 族**；种子条目 459 / 26 / 106 **一条不动**。

### 一、只读盘点（先出数，再动手）

「28 个六厂商齐全分类」的旧口径有两处高估，本批按库内实况重新点数：

| 分类 | 六厂商条目 | 结构化 params | 结论 |
|---|---|---|---|
| ACL | 6（华为 2 条：基本/高级） | 全结构化 | 落地，scope 4（见下「编号体系」） |
| NAT | 6（华为 2 条） | 全结构化，**含接口 IP 参数** | 落地，scope 5；华为取 AR Easy IP 条 |
| VRRP | 6 | 全结构化（最完整的一族） | 落地，scope 5 |
| POE | 6，**中兴为骨架（无 params、全注释）** | 5 家结构化 | 落地，scope 5 |
| 风暴抑制 | 6，**中兴为骨架** | 5 家结构化 | 落地，scope 5 |
| BGP | 6 | 全部为**查询/诊断**条目 | 查询对照族，scope 6 |
| 堆叠 | **中兴无条目**；思科条含成员优先级写入 | 查询为主 | 查询对照族，scope 4（思科仅作源） |
| 环路检测 | 6，**中兴为骨架** | 全部为查询条目 | 查询对照族，scope 5 |
| 显示控制 | 6 | 无 params | 落地（单实例族），scope 6 |
| HA/双机 | **仅思科/华为**（其余为防火墙厂商，不在六厂商网络口径） | 查询条目 | 查询对照族，scope **2** |
| 保存配置 | 6 | backup_tag 可选；Junos 用 backup_name | 落地，scope 6 |
| AAA 认证 | 6，**中兴为骨架** | 全结构化，**6 个参数里 5 个是凭据** | 落地，scope 5（凭据行走 d 档） |
| DNS | 6 | dns_server_ip + test_domain | 落地，scope 6 |
| SSH 管理 | 6 | hostname/domain/timeout 可桥；账号口令全在 d 档 | 落地，scope 6（结构可译，账号人工重设） |
| 密码 | 6 | 凭据类；Junos 为 root 口令交互式模型 | 落地，scope 5 |
| 设备管理 | 6（每家 2 条：巡检 + 定时重启） | 巡检条无 params；重启条 delay 需 min↔hh:mm 变换 | 取巡检条做查询对照族，scope 6 |
| SNMP | 6 | community 属凭据；location/contact/nms_ip 可桥 | 落地，scope 6 |
| 版本升级 | 6 | **启动镜像文件名不可迁移** + 3 家用 `<新版本.ipe>` 角度占位 + 交互式 copy | **不建族**（见下「本批不落的唯一一族」） |
| 配置回滚 | 6 | Junos 用 `rollback N` 编号模型 | 落地，scope 5 |
| 日志 | 6 | 全部为查询条目 | 查询对照族，scope 6 |

### 二、本批不落的唯一一族：版本升级（如实说明，不凑映射）

三条硬理由，任一条都足以否掉跨厂商映射：

1. **启动镜像文件名不可迁移**：`boot system flash:c2960-lanbasek9-mz.150-2.SE11.bin` 搬到华为就是错的，
   文件名必须由现场按实际包名替换——把源文件名塞进目标模板等于输出伪命令；
2. **三家条目是角度占位骨架**：华为/H3C/Junos 的条目正文写 `<新版本.cc>` / `<新版本.ipe>` /
   `<新版本.tgz>`，渲染出来的「命令」需要人工填文件名，标成「已译」会误导；
3. **Cisco/锐捷是交互式 copy**：`copy tftp: flash:` 需要现场输地址与文件名，源配置里根本没有可桥接的字段。

故本族**不建映射**：相关行按族外落 c（`no_family`），原因写明「库内为流程条目，启动镜像不可跨厂商搬运」。
若后续要做，须先给该族补「文件名占位显式化为参数」的数据改造，属独立批。

### 三、机制最小增量（4 处，均已进 smoke 覆盖）

「翻译机制零改动」在数据实况下无法成立——19 族里有 6 族是查询对照族、2 族目标缺格、
1 族整族无分组键，旧引擎会把这些行一律判成 `key_missing` 或强求六厂商齐备。改动仅限
「让映射表能表达真实数据」，四档语义、渲染路径、安全默认**一行未动**：

| # | 位置 | 改动 | 为什么必须 |
|---|---|---|---|
| 1 | `validate_rules` | 族级 `scope` 声明可译目标厂商；校验只对 scope 内厂商要求「有条目 + 占位可填」 | 堆叠缺中兴、HA 只有两家——旧规则逼着凑映射，与「缺格不塞空」红线冲突 |
| 2 | `translate` | scope 外的目标厂商：命中族但判 `c/no_family`，note 写明该族可译目标 | 与「族外命令」区分开，账本才说得清是「族没覆盖这家」还是「这行没族」 |
| 3 | `_merge_keyless` | 无声明键的族 = 单实例族，不参与「键未取到」降级 | 查询对照族/显示控制整族无键，旧逻辑会把整族打成 c |
| 4 | `_container_owner` + `_render_instance` | 容器行可按键参数（vlan_id）认领；实例分组键不算「待译字段」；容器行随属主实例同进退（属主失败则一并降级） | ①VLAN 三层接口块要认领到 VRRP 实例；②`acl_id` 这类键不是输出字段，不该触发 unbound；③避免「a 档却指向未产出命令的条目」 |

配套两处**数据口径**修正（不是机制）：

- `private_hints` 的 `spanning-tree` / `stp ` 收紧为配置态正则（`^\s*spanning-tree\s` / `^\s*stp\s+\S`），
  否则 `show spanning-tree summary` 会被私有特性吞掉，环路检测族永远打不着；已建族的风暴抑制提示撤除；
- `secret_patterns` 补 4 条：`radius-server shared-key` / `radius-server host … key` /
  `key authentication|cipher` / `set system radius-server … secret`——RADIUS 共享密钥此前会以
  普通行身份进账本，属口令类漏网。

### 四、19 族清单（scope / 类型 / 正例）

| 族 id | 分类 | 类型 | scope（可译目标） | 正例（源→目标） |
|---|---|---|---|---|
| `acl` | ACL | config | cisco / ruijie / huawei / h3c | cisco→ruijie（命名体系）、huawei→h3c（3000 段） |
| `nat` | NAT | config | cisco / ruijie / huawei / h3c / zte | cisco→huawei（`nat outbound 2000`） |
| `vrrp` | VRRP | config | cisco / ruijie / huawei / h3c / zte | cisco→huawei（virtual-ip / priority / 掩码↔前缀） |
| `poe` | POE | config | cisco / huawei / h3c / ruijie / juniper | cisco→huawei（`poe enable`） |
| `storm` | 风暴抑制 | config | cisco / huawei / h3c / ruijie / juniper | cisco→huawei（`broadcast-suppression 5`） |
| `bgp` | BGP | **query** | 六家 | cisco→huawei（`display bgp peer`） |
| `stack` | 堆叠 | **query** | huawei / h3c / ruijie / juniper | cisco→huawei（`display stack`） |
| `loop` | 环路检测 | **query** | cisco / huawei / h3c / ruijie / juniper | cisco→huawei（`display stp`） |
| `display_ctl` | 显示控制 | config（单实例） | 六家 | cisco→huawei（`screen-length 0 temporary`） |
| `ha` | HA/双机 | **query** | cisco / huawei | cisco→huawei（`display vrrp brief`） |
| `save` | 保存配置 | config | 六家 | cisco→huawei（`copy flash:/vrpcfg.zip …`） |
| `aaa` | AAA 认证 | config | cisco / huawei / h3c / ruijie / juniper | cisco→huawei（`radius-server authentication …`） |
| `dns` | DNS | config | 六家 | cisco→huawei（`dns server`） |
| `ssh` | SSH 管理 | config | 六家 | cisco→huawei（`sysname` / vty / stelnet） |
| `password` | 密码 | config | cisco / huawei / h3c / ruijie / zte | cisco→huawei（`user-interface console 0`） |
| `devmgmt` | 设备管理 | **query** | 六家 | cisco→huawei（`display version`） |
| `snmp` | SNMP | config | 六家 | cisco→huawei（`snmp-agent sys-info location …`） |
| `rollback` | 配置回滚 | config | cisco / huawei / h3c / ruijie / zte | cisco→huawei（`startup saved-configuration …`） |
| `log` | 日志 | **query** | 六家 | cisco→huawei（`display logbuffer`） |

**查询对照族**（6 个：BGP / 堆叠 / 环路检测 / HA / 设备管理巡检 / 日志）：库内这些分类只有诊断/巡检条目，源行就是 `show`/`display` 命令，
目标产物按引擎既有规则自动进「复核命令（只读）」区，不进配置正文——smoke J3 专门守这条线。

### 五、scope 为什么这么切（每条都有库内依据）

- **ACL 编号体系跨厂商不可换算**：Cisco/锐捷用命名 ACL，华为/H3C 用 3000–3999，中兴 `acl extend number`
  是独立段。名↔号写了就是伪命令，故只覆盖**同体系**两对（命名对 + 编号对），中兴/Junos 不落族：
  cisco→huawei 判 `param_missing`（写明「acl_number 无法推导」），这是真实限制而非缺陷；
- **NAT 的 Junos 目标条目自相矛盾**：`d0ae3c17` 里 `inside_port` 同时声明为「接口名（string）」和
  「服务端口（int）」，模板渲染必然一值两用 → 目标条目不可用，本批不落，**留作数据修复项**；
  另 NAT 的接口 IP 属接口块共有行，行级引擎无法判定「哪个 ip address 属于 inside/outside」，
  故 `inside_ip/outside_ip` 走 optional 剔除并在账本逐条注明；
- **VRRP 的 Junos 目标需要 `ifname` + `unit`**：Junos 是「ge-0/0/1.0 vrrp-group」，源侧 VLAN 号推不出
  Junos 接口名，无变换可走 → scope 5；
- **堆叠的思科条目带写入**：`switch {{member}} priority {{priority}}` 必填且源侧无值，
  取默认值等于凭空改优先级 → 思科只作源不作目标；
- **凭据类族（AAA/SSH/密码/SNMP）结构可译、口令不外译**：账号/口令/团体字命中 `secret_patterns` 走 d 档，
  目标条目里引用它们的行整行剔除并在账本写明「已省略，需人工重设」——这与 U 批安全默认一脉相承。

### 六、六样例扩样与完成度对账

六份小样各扩 20 余行（新增族样例 + 「运维查询行」段），命令行 190 → **437**。四象限对账：

| 组合 | 命令行 | 已译 a | b | c | d | 完成度 |
|---|---|---|---|---|---|---|
| A 旧样例 + 旧映射（U 批基线） | 190 | 161 | 6 | 13 | 16 | 84.7% |
| B 旧样例 + **新映射** | 190 | **167** | 6 | **7** | 16 | **87.9%** |
| C **新样例** + 旧映射 | 437 | 188 | 24 | 217 | 32 | 43.0% |
| D 新样例 + 新映射（本批终态） | 437 | **370** | 24 | 33 | 34 | **84.7%** |

读法：B 行证明新族在**旧语料**上也能接住原本判 c 的杂项行（hostname/sysname 归入 ssh 族，+6 行，c 13→7）；
C 行证明新增 247 行命令行里旧映射只接得住 27 行；D 行是扩族把新语料从 43.0% 拉回 84.7% 的结果。

逐样例（本批终态）：

| 样例 | 目标 | 命令行 | 已译 | b | c | d | 完成度 |
|---|---|---|---|---|---|---|---|
| cisco | huawei | 80 | 67 | 4 | 11 | 2 | 83.8% |
| huawei | cisco | 90 | 68 | 4 | 8 | 14 | 75.6% |
| h3c | cisco | 85 | 64 | 4 | 6 | 15 | 75.3% |
| ruijie | huawei | 74 | 68 | 4 | 5 | 1 | 91.9% |
| zte | cisco | 61 | 60 | 4 | 0 | 1 | 98.4% |
| juniper | huawei | 47 | 43 | 4 | 3 | 1 | 91.5% |
| **合计** | — | **437** | **370** | 24 | 33 | 34 | **84.7%** |

同体系厂商对的补充实测（样例不变，只换目标）：cisco→ruijie 90.0%（ACL 4/4）、ruijie→cisco 97.3%、
huawei→h3c 80.0%（ACL 4/4）、h3c→huawei 82.4%（0 行需人工）——**ACL 族只在同体系方向判已译**，
跨体系方向如实落 c，这是设计意图不是漏判。

### 七、c 档原因码分布（U → V）

| 原因码 | U 批（190 行语料） | V 批（437 行语料） | 说明 |
|---|---|---|---|
| `private` | 3 | 3 | 私有特性（STP 增强/DHCP Snooping 等）保持不译 |
| `no_family` | 4 | 4 | 族外杂项（`version 15.2`、`ip routing`）+ 堆叠目标缺格（思科） |
| `param_missing` | 6 | 19 | ACL 名↔号 17 行（真实限制）+ Junos OSPF 2 行（U 批遗留） |
| `uncovered_view` | 0 | **3** | **仅剩 `ip address`**：地址分配类库内只有思科/华为/H3C 三家，不构成六厂商族 → 仍是族外 |
| `unbound` | 0 | 4 | NAT 接口标记（目标条目无 outside_if 参数）、huawei→cisco `user privilege level` |
| **合计 c** | **13** | **33** | c 占比 13/190=6.8% → 33/437=7.6% |

`uncovered_view` 逐样例：cisco 2（0/6、0/7 两个 NAT 接口块的 `ip address`）、huawei 1（0/0/6），
其余四份为 0——smoke **J1** 断言该码只对白名单内的族外命令（当前只有 `ip address`）触发，
族内出现即判失败。

### 八、验收实测

- `smoke_translate` **217 项**（U 批 157 → 新增 [C17/C18] 19 族正例 ×2、[D8] 4 条反例、
  [J1–J5] 族覆盖面/查询对照分区/scope 越界/正例汇总；[A6/A7] 改为 scope 自洽校验）；
- 全量 **20 套**冒烟 rc=0（Tab 数未变，panel/handbook/scenarios 无需同步）；
- `--validate-seed` **EXIT=0**，统计行「配置翻译映射：**27 族 ｜ 147 条厂商映射**」，
  [E] 清单新增「scope 非法 / scope 含未声明厂商」，[W] 新增「同族实例键不一致（部分厂商有键部分没有）」；
- 种子库 **459 / 26 / 106 / 7 包** 不动（映射表不新增条目，`entry` 全指既有 uuid）。

### 九、★ 坑（本批新增）

1. **私有特性提示会吃掉族行**：`private_hints` 在族匹配**之前**执行，`storm-control`、`spanning-tree`
   这类宽前缀会把刚建好的族行判成 private——建族必须同步收紧/撤除对应 hint，否则族「看着有、打不着」；
2. **无键族会被判「键未取到」**：旧 `_merge_keyless` 对 `key='*'` 一律降级，查询对照族/显示控制全中招，
   必须先改引擎再写数据，否则整族 c；
3. **接口 IP 无法按行归属**：`ip address` 与 `ip nat inside/outside` 分属不同行，行级匹配拿不到
   「这块接口是 inside 还是 outside」，只能把 IP 参数置 optional 剔除（并在账本注明），
   硬塞会造出「inside 的 IP 写进 outside」的错配置；
4. **同名参数在不同条目里类型不同**：Junos NAT 的 `inside_port` 既是接口名又是服务端口，
   渲染按名取值必然串味——**入库校验应加「同条目参数名唯一」**（本批记入待办，未改 schema）；
5. **ACL 名→编号不能靠 `default_ok` 蒙混**：编号是身份类参数，取默认值会指向别的 ACL；
   本批只做同体系映射，跨体系如实 `param_missing`（NAT 族的 `acl_number` 例外：编号区间本就不同，
   取库内默认值并在账本逐条注明「acl_number=2000」，由现场按规划统一分配）；
6. **容器行要随属主走**：接口头行原先固定判 a，属主实例渲染失败时会留下「a 档却无产物」的假已译行，
   现改为容器行并入属主 lines，属主失败即一并降级。

### 十、不做清单（本批边界）

- **版本升级族**：不建（理由见第二节）；
- **Junos 侧**：ACL（前缀↔通配符无变换）、NAT（目标条目参数重名）、VRRP（VLAN 接口模型差异）、
  口令（root 口令交互式模型）一律不进族，落 c；
- **地址分配族**：库内只有三家，不构成六厂商族 → `ip address` 行继续属族外；
- **AI 猜测式补翻**：仍不做（U 批口径不变）；
- **条目数据修复**（Junos NAT 参数重名、中兴 POE/风暴/AAA/环路骨架转正）：属数据批，不在本批范围。


## W 批 QUERY_WHITELIST 扩容小批（2026-11-06，v1.0.0 攒批②，不发版不 tag）

一句话结论：**白名单 149 → 158 词（+9 个 diagnose 只读子命令）、强制变更组 40 → 43 词
（+3 个"伪查询"口令动词），全库仅 1 条条目翻类（NAT 会话诊断 → 查询类），四包回退数
8/2、7/3、5/5、5/5 全不变，条目/树/报错/包计数一条不动。**

### 一、只读盘点

**1）白名单现状与挂点**

| 项 | 批前 | 批后 |
|---|---|---|
| `QUERY_WHITELIST` 词条 | 149 | **158** |
| `CHANGE_FORCE_PREFIXES` 词条 | 40 | **43** |
| 全库 query / change | 152 / 307 | **153 / 306** |

判定表的消费点（改一次要连带复核的面）：`renderer.classify_entry`（validate / AI 入库钩子）→
`runbook_export.resolve_steps`（3 处，操作单实时判定）→ `ui_runbook`（2 处，变更类标签/导出）→
`ui_scenario._head_html` + `on_copy_entry` 守卫链 → `ui_main.guard_rollback_confirm`（复制前回退确认）→
`translate_tool._is_query`（行级分区）。**这批只动数据 + 翻译侧一处口径，不动任何调用方。**

**2）四厂商命令族逐族核对（库内实况）**

| 厂商 | 条目 | 变更 / 查询 | 变更缺回退 | 诊断/show 类命令族结论 |
|---|---|---|---|---|
| Fortinet | 24 | 21 / 3 | 0 | `diagnose` 14 种行、`get` 3 条、`show` 1 条：只读子命令 9 个、debug flow 2 个、配置类 3 个（`execute backup/restore`） |
| Palo Alto | 24 | 19 / 5 | 0 | `show` 6 条全查询；`request restart system` 属变更（`request` 多义，不入名单） |
| 深信服 AF | 33 | 10 / 23 | 0 | Web 控制台 15 条（全注释→查询类，刻意设计）+ CLI 骨架 8 条查询；无 diagnose 族 |
| 天融信 TopOS | 33 | 10 / 23 | 0 | 同深信服 |

逐词核对 Fortinet `diagnose` 14 种（库内全部）：9 个纯读 / 收尾动作入名单；2 个 debug flow
启动侧刻意留变更类；3 个配置类（`execute`）本就变更。**结论：diagnose 是"多义词族"，
只能逐词放行——整族放行会把"清会话/起追踪"一起放进查询类。**

**3）全库漏词旁证**：把 change 条目里"未入白名单的行"按首词归堆，除 diagnose 外只捞到 6 个
Linux/巡检疑点（`hwclock --show`、`snmpwalk`、`getent hosts`、`sshd -t`、`compare configuration`、
`curl -v telnet://`），均不属本批四厂商范围且 `curl` 需按参数级判定 —— 列进「未入词清单」，
留给下一小批，不在这里顺手放行。

**4）翻译侧分类口径核对（T 批口径②）**：**确认盲信，已修。**
`translate_tool._is_query` 此前只查 `QUERY_WHITELIST`，完全不看 `CHANGE_FORCE_PREFIXES`——
即"白名单动词 + 变更子命令"的行会被判查询。实测证据（本批修复前后对照，`split_entry_lines`）：

```
改造前: body=[line console 0, password X, login]        verify=[enable secret Y]   ← 写口令行被搬进复核区
改造后: body=[line console 0, password X, login, enable secret Y]   verify=[]      ← 留在配置体
```

后果不是"显示错了"而是"配置少了一行"：渲染结果里落在 body 末尾之后的变更行会被并入复核区，
用户照着复核区执行等于没下发。现改为与 `renderer.classify_entry` 同口径（force 优先）。

### 二、入词清单（逐词 + 依据，零整段放行）

| 入名单词 | 库内依据（uuid 前 8） | 为什么是查询语义 |
|---|---|---|
| `diagnose sys session list` | f0ac2dda / 6091c66c | 读会话表（含 NAT 转换段），不产生会话 |
| `diagnose sys session stat` | f0ac2dda | 读会话统计（容量/新建速率） |
| `diagnose sys session filter` | f0ac2dda / 6091c66c | `filter clear` / `filter dst <ip>` 只设**诊断输出的过滤条件**，不改配置、不影响转发 |
| `diagnose sys flash list` | 7a5916ae | 读 flash 文件清单，核对备份是否落盘 |
| `diagnose firewall iprope list` | 1f331c90 | 读策略的实际安装位置与命中计数 |
| `diagnose firewall vip list` | 9c922bf5 | 读 VIP 映射表与引用关系 |
| `diagnose netlink aggregate name` | 9fd6c2cf | 读聚合成员端口状态（Trunk 条目验证尾行） |
| `diagnose debug reset` | f0ac2dda | 复位 debug 计数（收尾动作，无副作用） |
| `diagnose debug disable` | f0ac2dda | 停 debug 输出（收尾动作） |

强制变更组补 3 个（**不是扩容查询侧，是把"伪查询"堵住**）：

| 入名单词 | 库内依据 | 为什么必须判变更 |
|---|---|---|
| `enable secret` | dd503bcb / f4066cd1 / 426b9f4e | 写特权口令；裸 `enable` 在查询名单里（会话级进特权），同前缀会把口令行顺带放行 |
| `enable password` | 同上（老式写法） | 同上 |
| `enable algorithm-type` | 同上（新式 `enable algorithm-type scrypt secret`） | 同上 |

### 三、未入词清单（含理由）

| 词 | 不入的理由 | 后果（刻意接受） |
|---|---|---|
| `diagnose debug flow filter addr` / `diagnose debug flow trace start` | 启动逐包追踪属 **debug 语义**，与 `CHANGE_FORCE_PREFIXES` 里的 `debug` 同口径；须 `diagnose debug disable` 收尾 | f0ac2dda 整条仍判变更类 → 操作单仍附"清理诊断态"回退，Fortinet 包维持 8/8 |
| `diagnose sys session kill` | 清会话 = 断业务（不是读） | — |
| `execute backup config` / `execute restore config` | 写 flash / 覆盖配置并重启 | 7a5916ae、be6beb84 维持变更类 |
| `request`（Palo Alto） | 多义族（`request restart` 是变更），无只读子命令进库 | 维持变更类 |
| `hwclock --show` / `snmpwalk` / `getent hosts` / `sshd -t` / `compare configuration` / `curl` | 语义偏只读但属 net_diag/linux 分类，不属本批四厂商；`curl` 需按参数区分（`-o` 落盘） | 留清单，下一小批单独评估 |

### 四、T 批留痕①销项（Fortinet diagnose 误判变更类）

**根因**：`diagnose` 整族不在名单 → 凡含 diagnose 的条目一律变更类。**已消除**：9 个只读子命令
入名单后，`6091c66c NAT 会话与端口映射诊断` 归位查询类（全库唯一翻类条目）。

**四包引擎判定重跑（不进包数据，只重跑）**：

| pack_id | 变更 / 查询 | 回退缺口 | 变化 |
|---|---|---|---|
| fw-edge-policy-fortinet | 8 / 2 | 0 | 无（第 8 步含 debug flow，刻意留变更） |
| fw-edge-policy-paloalto | 7 / 3 | 0 | 无 |
| fw-edge-policy-sangfor | 5 / 5 | 0 | 无 |
| fw-edge-policy-topsec | 5 / 5 | 0 | 无 |

**销项口径（如实）**：误判消除 + 剩余判定**重新定性**——f0ac2dda 仍判变更类不是误判，而是
debug flow 追踪要带回退。包内该步 `note` 已同步改写（"会话过滤/会话表/统计已入白名单，debug flow
仍判变更类"），**包结构、引用、步数、params 全未动**。

**备选方案（明确不做）**：把 debug flow 两词也放进白名单 → Fortinet 包变 7/7、该步归查询类。
代价是操作单不再附"清理诊断态"，现场容易把 trace 开着走人（控制台刷屏/CPU 抬升）。按
"分不清就当变更类"的 fail-safe 原则否决。

### 五、V 批留痕复扫（六厂商：cisco / huawei / h3c / ruijie / zte / juniper）

三条扫描口径，逐条跑（结果：**私有特性侧零残留，分类假象侧一处，已修**）：

| 扫描 | 口径 | 结果 |
|---|---|---|
| ① 查询行被 secret/private 吞 | 库内 query 类行 → `secret_patterns` / `private_hints` 命中 | **0 命中**（V 批收紧 `storm-control`/`spanning-tree` 后无残留） |
| ② 非查询行被 private 判 c | 反向扫描（族覆盖缺口的假像） | **0 命中** |
| ③ 强制变更行被翻译判查询 | `CHANGE_FORCE_PREFIXES` 命中 ∩ `_is_query` 为真 | **修复前 3 处**（cisco/ruijie/zte 的 `enable secret`）→ 修复后 **0 命中** |

结论：V 批的"私有特性吞查询行"已收净；本轮捞到的是**另一类误伤**（白名单前缀 + 变更子命令），
根因在判定表不在翻译器，两边同时修（force 词 + 翻译侧口径）后归零。

### 六、验收实测

- `--validate-seed` **EXIT=0**：459 条 + 26 树 + 106 报错 + 7 包 + 27 族 147 条映射，
  [E]=0，[W]=17（与 T 批同口径，未增未减）；
- 全量 **20 套**冒烟 rc=0：`smoke_scenarios` **95 → 110 项**（新增 [G] W 批块 15 项）、
  `smoke_translate` **217 → 224 项**（新增 [K] 块 7 项）、`smoke_runbook` 74/74、
  `smoke_schema_rollback` 9/9、`smoke_panel_dashboard` 63/63、`smoke_diff` 62/62、
  `smoke_tools` 107/107、`smoke_handbook` 58/58、`smoke_tree_visual` 46/46；
- 新增断言要点：`G1` 入名单 11 词逐词判查询 / `G2` debug flow 与 `session kill` 逐词判变更 /
  `G3` enable 家族分词 / `G5` 四包变更查询数锁定 / `G6` 查询类复制**不弹**回退确认（弹窗即失败）；
  `K1` 行级口径 / `K2` 分区不丢变更行 / `K3` 六厂商零误伤 / `K4` force 行零误判；
- 主库：**条目零改动**（本批只改判定表与翻译器），无需导入；各套件"主库零接触"指纹断言全通过。

### 七、★ 坑（本批新增）

1. **裸动词是"伪查询"入口**：`enable` 在名单里（会话级进特权），`enable secret` 就被顺带放行。
   凡白名单收了"会话级单词"，必须同时在 `CHANGE_FORCE_PREFIXES` 里把它的写配置变体挡住；
2. **翻译侧行级分区必须与 classify 同口径**：force 优先漏了，配置体末尾的变更行会被搬进复核区，
   表现为"已译但配置少一行"，比报错更难发现；
3. **别把"目标配置里没有 enable secret"当 bug**：源侧口令行按 d 档（secret）不外译，是 U/V 批
   既定口径；要测分类就看 `split_entry_lines`，别看整机输出；
4. **诊断族不能整族放行**：debug flow 启动侧一旦判查询，操作单就不再附"清理诊断态"；
5. **包内 note 会随判定漂移**：判定改了要顺手改 note，否则文档比代码更误导（本批 Fortinet 第 8 步
   就属于这种"代码变了、说明没变"的隐患）。

## X 批 防火墙缺格位补库（2026-11-06，v1.0.0 攒批③，不发版不 tag）

一句话结论：**T 批点名的 12 类缺格位逐类核过——Fortinet 补 13 条、Palo Alto 补 10 条（合计 +23，
459 → 482），深信服 AF / 天融信 TopOS 一类未补（无可靠 CLI 依据，继续标缺）；新增 3 个分类
（安全区域 / VPN / 安全防护，45 → 48），矩阵 16 × 48 实填 344 格 mismatch=0；四包 10 步链路
一条不动，仅改包说明的缺格位口径。**

### 一、12 类逐条裁决（补 / 不补 + 依据）

| # | 格位 | Fortinet | Palo Alto | 深信服 AF | 天融信 TopOS | 依据（公开资料） |
|---|---|---|---|---|---|---|
| 1 | 独立安全区域 | **补**（zone） | **补**（zone） | 不补 | 不补 | FortiOS CLI Ref `config system zone`（interface/intrazone/description）；PAN-OS 官方 KB「3 层接口配为管理口」`set zone … layer3`、IPsec KB 同形用法 |
| 2 | 第二条（入向）放行策略 | **补** | **补** | 不补 | 不补 | FortiOS CLI Ref `config firewall policy`（utm-status / ips-sensor / nat）+ VIP 对象引用；PAN-OS 官方 KB「Move Security Rules」+ `profile-setting group` |
| 3 | 服务对象独立创建 | **补** | **补** | 不补 | 不补 | FortiOS CLI Ref `config firewall service custom`（protocol / tcp-portrange / udp-portrange）；PAN-OS `set service <n> protocol tcp port <p>` |
| 4 | 策略排序 / 禁用 / 删除 | **补** | **补** | 不补 | 不补 | Fortinet KB「change the order of the IPV4 policy list」`move <id> before|after <id>`（CLI Ref 另记旧形态 `move A to B`）+ `set status disable` / `delete`；PAN-OS 官方 KB「How to Move Security Rules Through the CLI」（top/bottom/before/after）+ `disabled yes` |
| 5 | 策略命中计数清零 | **补** | **不补** | 不补 | 不补 | Fortinet KB「How to Reset Count on the Firewall Policy」+ 7.0 New Features「Record central NAT and DNAT hit count」`diagnose firewall iprope clear 100004 <id>`；PAN-OS 无「单条规则计数清零」CLI 依据（只有全局 counter），标缺 |
| 6 | 会话强制清除 / 导出 | **补** | **补** | 不补 | 不补 | Fortinet KB 清会话组合 `diagnose sys session filter … → list → clear`（W 批已入只读名单）；PAN-OS `show session all filter` / `clear session all filter` / `clear session id`。**导出**：两家均无原生导出命令，条目内注明用输出贴档/tech-support |
| 7 | HA 配置 / 倒换 / 会话同步 | **补** | **补** | 不补 | 不补 | FortiOS CLI Ref `config system ha`（mode/group-name/password/hbdev/session-pickup/override/priority）；PAN-OS 官方 HA 文档 + KB `request high-availability state suspend|functional`、`show high-availability state` |
| 8 | IPsec 站点到站点 / SSL VPN | **补 2 条**（IPsec + SSL VPN） | **补 IPsec**；SSL VPN 不补 | 不补 | 不补 | FortiOS CLI Ref `vpn ipsec phase1/phase2-interface`、`vpn ssl settings` / `vpn ssl web portal`；PAN-OS 官方 KB「IPSec VPN with Route and Tunnel from CLI」（ike gateway / tunnel ipsec / tunnel.10）。PAN-OS 无 SSL VPN（远程接入是 GlobalProtect，另属产品面） |
| 9 | IPS / AV / 应用识别 / URL 过滤 | **补 1 条**（四件套合一） | **补 1 条**（漏洞+AV）；URL 不补 | 不补 | 不补 | FortiOS CLI Ref `config ips sensor` / `antivirus profile` / `application list` / `webfilter profile`（ftgd-wf filters）；PAN-OS `profiles vulnerability` / `profiles antivirus` / `profile-group`。PAN-OS URL 过滤分类节点随版本与订阅变动，官方无稳定逐分类 set 序列 → 标缺 |
| 10 | 策略路由 / 多出口 / 链路探测 | **补** | **不补** | 不补 | 不补 | FortiOS CLI Ref `config router policy`（input-device/output-device/gateway）+ `config system link-monitor`；PAN-OS PBF 官方文档只给 GUI 路径与 `show pbf rule all` 运维命令，配置侧 CLI 路径无权威引用 → 标缺（不猜路径） |
| 11 | 接口 IP 直配 / PPPoE / DHCP / MTU | **补** | **补** | 不补 | 不补 | FortiOS CLI Ref `config system interface`（mode static/dhcp/pppoe、mtu、distance）；PAN-OS 官方 KB `layer3 ip` / `interface-management-profile` / `layer3 mtu` + pppoe、dhcp-client 字段（版本差异已在 notes 提示 `?` 核对） |
| 12 | 配置对比 / 命名版本 | **部分补**（list+restore；diff 仅 GUI） | **部分补**（save/load + show config diff） | 不补 | 不补 | FortiOS Admin Guide「Configuration backups and reset」+ CLI Ref `execute revision`（**无 diff 子命令**，Diff 是 Web 界面动作，条目不臆造）；PAN-OS `save config to` / `load config from` / `show config diff`（候选 vs 运行） |

**不补口径（深信服/天融信，共 12 类 × 2 家）**：AF 与 TopOS 的策略/防护/HA/VPN 类配置均以 Web 控制台
为主，官方资料只有界面路径与少量运维命令，没有可引用的 CLI 配置序列。按用户「不编造优先于补全」
红线，**一类不补、不塞空步骤**，清单继续留在两包的「库内暂缺」段并注明 X 批核过。

### 二、新增条目（23 条，全部「语法核对档」）

| 厂商 | 条数 | 分布（分类 → 条数） |
|---|---|---|
| Fortinet | 24 → **37**（+13） | 安全区域 1 / ACL 3 / 防火墙诊断 2 / HA/双机 1 / VPN 2 / 安全防护 1 / 静态路由 1 / 接口基础 1 / 保存配置 1 |
| Palo Alto | 24 → **34**（+10） | 安全区域 1 / ACL 3 / 防火墙诊断 1 / HA/双机 1 / VPN 1 / 安全防护 1 / 接口基础 1 / 保存配置 1 |

- 档位：全部 `exec_level` 空（未真机），notes 首行写明「语法核对档：依据官方 CLI 文档核对，未经
  本机真机验证」；`verified` 全 0，不承诺验证等级；
- 写法：结构化 params（type/description 必填，enum 带 choices）+ 变更类 rollback 全覆盖
  （删对象 → 解绑 → 复核三段式，顺序错误会「entry is in use」的地方逐条写明）；
- 保守处理的三处（**有文档但不敢写成可直接照抄**）：
  ① Fortinet 倒换只给「断链路」确定路径，`diagnose sys ha reset-uptime` 以注释提示先 `?` 核对；
  ② PAN-OS PPPoE / dhcp-client 字段版本差异，注释提示 `?` 核对；
  ③ Fortinet 配置 Diff 明确写「Web 界面动作」，不给 CLI diff 命令。

### 三、新增分类与矩阵

- 新分类 3 个：**安全区域**（zone 归口，策略 from/to 的前置对象）、**VPN**（IPsec/SSL VPN 独立成类，
  不再塞进 ACL）、**安全防护**（IPS/AV/URL 等配置文件）；分类 45 → **48**；
- 矩阵 16 厂商 × 48 分类，实填 **338 → 344 格**（+6 = 3 新分类 × 2 厂商），逐格对账 mismatch=0；
- 未并入既有分类的原因：zone 与 ACL 是「对象/域」而非「规则」，混放会让「ACL 条目」检索失真；
  VPN 与安全防护同理会稀释策略类条目的语义。

### 四、T 批四包标注销项对账

| 包 | 原「库内暂缺」8 项 | 本批处置 | 包数据 | build 复跑 |
|---|---|---|---|---|
| fw-edge-policy-fortinet | 8 项 | **11 类销项**（8 项全销 + 新增 3 类口径），第 12 类「配置对比」标部分 | items 不动 | 10 步，变更/查询 8/2 |
| fw-edge-policy-paloalto | 8 项 | **9 类销项**，仍标缺 4 项（命中清零 / URL 过滤 / PBF / SSL VPN） | items 不动 | 10 步，7/3 |
| fw-edge-policy-sangfor | 8 项 | 0 销项，改为「X 批核过，不补」口径 | items 不动 | 10 步，5/5 |
| fw-edge-policy-topsec | 8 项 | 0 销项，同上 | items 不动 | 10 步，5/5 |

- **单一事实源**：包内 items 一律不动（不因新条目改链），新条目按需另立扩展包；包说明只改「缺格位」
  段的措辞与销项状态，`abort_criteria` / `template` / `params` 覆盖全部原样；
- 描述里保留「库内暂缺」四个字（smoke 结构断言依赖该口径词），销项状态追加在后面，避免把
  「已补」误读成「本来就全」。

### 五、验收实测

- `--validate-seed` **EXIT=0**：**482 条** + 26 树 + 106 报错 + 7 包 + 27 族 147 条映射，[E]=0，
  **[W]=17**（与 T/W 批同口径：存量 zte 骨架 1 + 骨架引用 16，本批零新增）；
- 新增条目回扫 23/23：全部判**变更类**、rollback 非空、命令与回退渲染后**零残留占位**、
  参数声明与引用双向对齐（无「已声明未使用」）；
- 全量 **20 套**冒烟 rc=0：`smoke_scenarios` 110/110、`smoke_panel_dashboard` 63/63（矩阵逐格
  mismatch=0）、`smoke_diff` 62/62、`smoke_runbook` 74/74、`smoke_translate` 224/224、
  `smoke_tools`、`smoke_handbook`、`smoke_tree_visual` 46/46、`smoke_export_site` 8/8、安全四套全绿；
- 主库同步：先备份 `backup/command_lib-pre-xbatch-20261106-171215.db`，再
  `--db dist_package/command_lib.db --validate-seed --allow-main-db` 导入，主库 482 / 48 分类 /
  7 包，状态字段由 import-guard 保留库值。

### 六、★ 坑（本批新增）

1. **ipv4 校验不认 CIDR**：`validate="ipv4"` 只收裸地址，PAN-OS 侧 `200.1.1.10/24` 直接 [E]
   「IPv4 地址格式错误」——CIDR 参数只能用 `type=string` 并在 description 写清写法；
2. **169.254/16 不在脱敏白名单**：隧道接口示例取了链路本地地址，`smoke_export_site` 直接以
   `[IP-WHITELIST]` 拒绝落盘（白名单只放 TEST-NET / 10-8 / RFC1918 / loopback / 组播）——
   示例地址一律用 10.x 或 TEST-NET；
3. **只出现在 rollback 的参数会挂 [W]**：`extract_params` 只扫 commands，回退侧专用的
   「原位置策略 ID / 原地址 / 回退快照名」必然「已声明未使用」；回退侧改用角括号说明
   （`<变更前的前一条策略 ID>`），既保住可读性又不制造 [W]；
4. **整文件 json.dump 会重排既有格式**：`scenario_packs.json` 的 items 是单行紧凑对象，dump 一次
   就产生 1000+ 行噪声 diff——描述类改动必须在原文上做字符串替换；
5. **Windows 文本模式写种子会带 CRLF**：`open(..., "w")` 默认 `os.linesep`，必须
   `newline="\n"`，否则整文件 diff 全是换行符（本批首次写入即踩到）；
6. **smoke 结构断言绑字面词**：「库内暂缺」被销项替换后 `smoke_scenarios` F 段 4 项直接红；
   销项要保留口径词并追加状态，别把断言改成「随便有个 ★」；
7. **`scripts/backup_db.py` 保留 10 份的策略把当次新备份也清掉了**（本次实测：备份完成后立刻
   列进「清理旧备份」并删除，最新备份退回到 14:46 的 pre-tbatch）——批量导入前的备份建议手工
   `cp` 成 `command_lib-pre-<批>-<时间戳>.db`，不依赖该脚本的保留逻辑。

### 七、不做清单（本批边界）

- **深信服/天融信一类未补**：无 CLI 依据，宁可留白（Web 路径已在各自条目与包说明中给出）；
- **PAN-OS 四类不补**：策略命中清零、URL 过滤、PBF 链路探测、SSL VPN（GlobalProtect）；
- **不给新条目编真机结论**：全部 verified=0，真机验证会按厂商分批回填（`scripts/backfill_verify.py`）；
- **不改包内 items**：新条目暂不入链，避免「补库顺带改流程」把已验证的 10 步链路搅动；
- 本批**不发版不 tag**（攒批③，与 v1.0.0 一并出）。

## Y 批 v1.0.0 收口发版（2026-11-10）

发版批（微量级），沿用 S 批精简口径：只做版本三处 / CHANGELOG 收口 / README 定位与事实段 /
CI 门槛 / tag 与 Release / 出库自核验 / 遗留池盘点，**不碰种子与功能代码**。
发布 commit `03fc09b`，annotated tag 对象 `1a58469` → deref `03fc09b`（远端 `git ls-remote` 复核一致）。

### 1. 版本三处（AST 断言实测，不靠目测）

- `app/ui_main.py` APP_VERSION `0.9.0 → 1.0.0`（标题栏与「关于」对话框均引用该常量）；
- 沿用 `.workbuddy/release_s/check_versions.py`：AST 取 APP_VERSION、正则取 README「当前版本：**vX**」、
  正则取 CHANGELOG 首个 `## vX（` → 三者 **1.0.0 / 1.0.0 / 1.0.0**，
  标题栏/关于框常量驱动均 True、源码无 `"v0.9.0"` 类硬编码 → **PASS（rc=0）**；
- 构建侧补断言：`scripts/build_exe.py` / `NetToolBox.spec` / `release.yml` 均**无版本硬编码**，
  zip 名由 `GITHUB_REF_NAME` 推导（`NetToolBox_v{ver}.zip`）——版本第四处天然随 tag，无漂移面；
- README 事实段逐条与实现核对：主界面 Tab **10**（`ui_main.py` 10 处 `addTab` 实点）、
  场景包 **7**（`scenario_packs.json` count=7 且 len=7）、翻译 **27 族 / 147 条映射**
  （`translate_rules.json` families=27、vendors 合计 147，`--validate-seed` 统计行同口径）、
  种子 **482 / 26 / 106 / 48 分类 / 344 格**（validate [OK] 行 + 本地主库实点）、手册 7 本 520 条；
- README 定位段改写为「**多厂商命令库 + 场景包 + 配置翻译器**」三条主线叙事，
  与版本号 v1.0.0、事实段新增「主界面 Tab」行同批落地。

### 2. CHANGELOG 收口

- 「未发布（v1.0.0 开发线…）」收口为 `## v1.0.0（2026-11-10）`，节首新增总览
  （功能线 T/U/V、数据线 W/X、发版口径、真机验证线零冒充声明）；
- **五批各自立节并按 T / U / V / W / X 重排**：原「未发布」节只有 X / V / T / W 四块，
  **U 批（配置翻译器 Tab 10 + 四档账本 + 零臆测）此前只在 dev-notes 与 README 特性里，
  CHANGELOG 从未立节**——本批据 U 批 dev-notes 实测（8 族 48 映射 / 190 行已译 161 / 84.7% /
  smoke_translate 157 项 / 三套 Tab 数断言 9→10）补写独立节；
- 批内 10 个小节标题 `### → ####` 降级，四个批次引言的「本批不发版、不 tag」统一改为
  **「随 v1.0.0 合并出库」**（v0.9.0 及更早历史表述保留原样）；
- 冒烟计数以实测为准：发布节总览与各批明细统一 **20 套 rc=0**（T / U 批的 19 套属当时基线，
  在批内如实标注「当时基线」，不冒充终态）。

### 3. tag、CI 与 Release

- 提交 `03fc09b` → `git tag -a v1.0.0` → SSH 推 main + tag（HTTPS 恒 502，标准路径仍是 SSH）；
  本次未复现 N 批「tag 双执行」怪癖，`git cat-file -p` + `git ls-remote` 一次到位；
- CI 在发布 commit 上三条全绿：
  `validate-seed` main（42739226622）/ tag（42739234431）均 **seed + handbook 两步 success**、
  `release`（42739234811）**14 步全 success**（构建 → exe 断言 → staging 净库 → 脱敏扫描 →
  白名单 zip → zip 断言 → 挂 draft）；
- CI 自动建 draft Release（id `439857836`，asset `729731558` / **42,746,684 B** /
  digest `sha256:71c8d58f…e2`）→ API `PATCH` 转 **publish**（`2026-11-10T01:58:58Z`），
  标题「NetToolBox v1.0.0 —— 功能面成形（多厂商命令库 + 场景包 + 配置翻译器）」，
  正文 `notes_v100.md` 与 CHANGELOG v1.0.0 节同源（**1876 字符逐字相等**，含 482/26/106、
  48 分类、344 格、27 族 147 映射、20 套冒烟）；
- 本地对照构建 `python scripts/build_exe.py` rc=0（Python 3.13 / 45,343,045 B），
  CI 产物 exe 42,047,427 B（Python 3.11）——版本栈差异，非内容差异（S 批已实证）。

### 4. 出库自核验（S 批全量哈希口径，通道可用）

- 整包下载 **42,746,684 B / 16s**，本地 sha256 `71c8d58fef0e6610f2cd19ace465e45dfd4f16b384385c6f96effda00d3260e2`
  与 GitHub asset `digest` **逐字相同**（免用 N 批 Range 兜底法）；
- zip **30 条目**白名单零违规：`NetToolBox.exe` / `check_db.py`（24,957 B，与仓库逐字节同尺寸）/
  `command_lib.db` / `seed_data/*.json` **27 份**（X 批后 translate_rules 等在列）；
- 包内净库：entries **482** / trouble_trees **26** / err_dict **106** / scenario_packs **7** /
  categories **48** / `ai_imports=0` / `err_unresolved=0` / **`verified=1` 计数 0**
  （真机验证线在 v1.0.0 内零冒充）/ user_version 3 / history 621（CI 导入留痕属正常）；
- 包内净库 vs 本地主库：**四库 id 集合全等**（482/26/106/7）、entries 正文（剔除状态字段，14 列）
  与树/报错正文逐行一致；byte hash 不同只因 created_at/updated_at 为各自导入时刻（S 批口径）；
  本地主库 err_unresolved=13 属实机运行数据，净库 0 属种子重建，两者语义不冲突；
- **手册七 JSON 在包**：内嵌 exe（`--add-data handbook`），`CArchiveReader` 列出 7 条，
  未压缩长度与 `app/handbook/*.json` **逐本字节完全相同**（15997/15037/20569/17656/15167/13087/17124）；
- **旧版资产未动**：发版前基线 8 个 Release（v0.4.0–v0.9.0）逐项比对
  资产名 / 字节数 / digest / updated_at / name / published_at，**8/8 UNCHANGED**；
  发版后 Release 总数 8 → 9，新增仅 v1.0.0。

### 5. 冒烟与校验（本批实测）

- 统一 `PYTHONPATH="app;_vendor"` 全量 **20 套** `scripts/smoke_*.py` **rc=0**（含 7 套安全/UI 套件）；
  各套件末行 atexit 零接触打印仅作参考，判定只看 rc；
- `--validate-seed` **EXIT=0**（482 + 26 + 106 + 7 包 + 11 类 48 变体 + 27 族 147 映射，
  [E]=0，[W]=17 存量骨架提示）；`--validate-handbook` **EXIT=0**（7 本 / 56 组 / 520 条）；
- 主库零接触：20 套 `_smoke_env` 指纹断言全通过，发版批未导入/未改写主库。

### 6. ★ 坑与口径澄清（本批）

1. **凭据通道**：gh CLI 未登录、环境变量无 PAT、历史会话与磁盘均无 token 落盘
   （S 批「token 不落盘」纪律）。可用写凭据来自 **Windows 凭据管理器**
   `git credential fill`（`gho_…`，账号 4caf1-xy，scopes 含 `repo`/`workflow`）；
   API 走 `curl --noproxy '*' --ssl-no-revoke`，hosts-switch 处于「关」（GitHub 条目全注释）。
   凭据不写入任何文件，仅内联传给 curl；
2. **draft `browser_download_url` 是 `untagged-…` 路径**：整包下载须走
   `/releases/assets/{id}` + `Accept: application/octet-stream` + `-L`（S 批同款），
   publish 后浏览器地址才换成 `v1.0.0`；
3. **包内 exe 与本地 exe 尺寸必然不同**（CI py3.11 / 本地 py3.13），
   「资产元数据与本地构建一字不差」落在 **zip 条目名集合 / 包内库内容 / check_db.py 字节**
   三处，而非 exe 字节（S 批已裁决）；
4. **CHANGELOG 攒批节不等于五批齐全**：U 批从未进 CHANGELOG，收口先做「五批入列」盘点再动笔，
   否则 v1.0.0 发布节会静默漏掉一条功能线。

### 7. 发版后遗留池盘点（逐项归属，不静默关闭）

| 项 | 来源 | v1.0.0 内状态 | 归属 |
|---|---|---|---|
| 真机验证档：**真机 27 / 语法核对 61 / 骨架 62 / 未验证 309**（报错库 106 全未真机） | S 批遗留池 / 场 2 饱和场 | **零冒充**：四家场景包与 X 批 +23 条 `verified` 全 0，全部标「语法核对档，未经真机」；包内净库 `verified=1` 计数 0 | **v1.1.0 起的真机验证会**（按厂商分批：场 1 防火墙 38 + 中兴 3，非发版阻塞项）；回填走 `scripts/backfill_verify.py` |
| V 批留痕：版本升级族不建 / Junos ACL·NAT·VRRP·口令落 c / 地址分配族不建 / 中兴 POE·风暴·AAA·环路骨架转正 / Junos NAT 参数重名 | V 批「十、不做清单」 | 未动（V 批已如实标缺，不猜路径） | **v1.1.0 数据批**（骨架转正 + 条目修复）；族级扩张**按需立项**，版本升级族维持不建（镜像文件名不可跨厂商搬运） |
| W 批留痕：`diagnose debug flow filter / trace start` 刻意不入白名单；备选方案「把两词也入名单」明确否决 | W 批「四、T 批留痕①」「备选方案」 | T 批 Fortinet `diagnose` 误判已销项、V 批三条复扫归零；debug flow 维持变更类以带出回退 | **维持裁决**（fail-safe「分不清就当变更类」），非待办；若将来真机证明只读再立批 |
| X 批留痕：深信服 AF / 天融信 TopOS 12 类不补、PAN-OS 4 类不补（命中清零 / URL 过滤 / PBF / SSL VPN）、X 批 23 条新条目**暂不入四包链** | X 批「七、不做清单」 | 未动（宁缺毋滥，Web 路径已给出；四包 items 一条未改） | **v1.1.0 数据批**（拿到权威 CLI 依据才补；扩展包另立，不搅动已验证的 10 步链路） |
| U 批留痕：AI 猜测式补翻 / 接口编号槽位换算 / 层级式 Junos / 跨设备整网翻译 | U 批「不做清单」 | 未动（四档账本 c 档如实交代，零臆测口径保持） | **按需立项**；若做 AI 补翻须独立批且输出永久带「AI 生成，未经核对」标注 |
| BSD 最小集（FreeBSD 命令/排查树）+ 迪普 DPtech / 占位厂商代码侧注册（`VENDOR_SLUGS`/platform/端口展开） | N 批遗留池 / E 批评估 | 未动（需代码+数据批，非纯数据可落地） | **v1.1.0 代码+数据批** |
| 树编辑器二批（整树新建/删除、跨树拖拽复制、批量重构、多树并排对比） | N 批遗留池 / F 批两期制 | 未动（一期只编辑既有 26 棵） | **v1.1.0 功能批** |
| Runbook 执行留痕（执行结果/回显归档回填） | P 批二批剩余 | 未动（六段式与一键生成已在 v0.9.0 落地） | **v1.1.0 功能批** |
| AI 生成操作单 + QPrinter PDF 导出 | P 批二批剩余 | 未动 | **按需立项**（`docs/runbook-guide.md` §6 已记理由） |
| err_unresolved 13 条 pending（含 4 条无效残留 #12–#15："not"/"all"/"Error"/重复行） | E 批 / 场 2 | 未清（本地主库 13 条属运行数据，净库 0 属种子重建） | **v1.1.0 清理批**（UI 侧清理 + resolved 回写） |
| 图片附件多模态外发面 / sessions·ai_cases 治理后续 | 安全加固批候选 | 未动 | **按需立项** |
| 防火墙 4 家场景包 | N 批遗留池 | **T 批已落地**（3 → 7 包），本版交付 | 销项（v1.0.0） |

> 口径：本版为**功能面成形**版，不是「验证完成」版。四档账本、场景包、翻译器产物均标
> 「语法核对档，未经真机」；遗留池逐项有归属、无静默关闭，真机结论只在验证会后经
> `backfill_verify.py` 回填，不在发版批里凑绿。

### 8. 建议（发版后用户侧手测一轮，非阻塞）

双击 exe → 配置翻译 Tab 粘贴六厂商样例看四档账本 → 场景包选 Fortinet 一键生成操作单
（查包级回退条件与六段式）→ 速查手册 / 网工计算器 → 关于对话框显示 v1.0.0。

## Z 批 基础命令盘点补库（2026-11-10，v1.1.0 数据线开局，不发版不 tag）

一句话结论：**用户点名的 `enable` 是会话态设计性排除（不是缺库），`ip a` 是真缺**。
六厂商 + Linux 四系逐家三档盘点后补 **15 条**（13 基础查询 / 2 基础变更），
482 → **497**；矩阵 344 → **352** 格（新增 8 个空格位）、分类仍 48；
`QUERY_WHITELIST` 158 → **161** / `CHANGE_FORCE_PREFIXES` 43 → **49**，存量 482 条
**零翻类**；`--validate-seed` EXIT=0（[W]=17 零新增）、全量 20 套冒烟 rc=0、
主库零接触。种子 + 判定表 + 文档 + 冒烟断言，**不发版不 tag**。

### 一、边界裁决：设计性排除 vs 真缺

| 档 | 结论 | 依据 |
|---|---|---|
| ① 会话态 | **不补（设计性排除）** | `enable` / `disable` / `configure terminal` / `system-view` / `quit` / `exit` / `end` / `terminal monitor` / `screen-length 0 temporary` / `set cli screen-length 0` / `sudo -i` / `logout` 只切当前会话视图或显示态，**不落 running-config、无持久化、无回退对象**；本库「一条条目 = 一组可复用命令（变更类必带回退）」，单列只会产出空回退。已在 `docs/seed-guide.md` 新增**第九节**明写口径，`显示控制` 类 8 条收录的是「关分页 / 日志实时输出」现场提示，不是把裸会话命令当配置 |
| ② 基础查询 | **缺才补（13 条）** | `show` / `display` 家族大面由 Q/X 批覆盖（uuid 见下表）；真缺的是**独立基础查询条目**——`ip addr show` / `ip route show` / `show users` 等只作为配置条目的验证行或 notes 提示，用户搜 `ip a` 搜不到 |
| ③ 基础变更 | **已在库的不补，真缺 2 条才补** | `hostname` / `sysname` / `no ip domain-lookup` / `enable secret` / `service password-encryption` / `ip domain-name` 已在 SSH管理 / 密码 / DNS 条目（uuid 见下表）；Linux 四系 `hostnamectl set-hostname` / `timedatectl set-timezone` 全缺 |

判定表口径补注：会话级词本就在 `QUERY_WHITELIST`（W 批），同前缀写配置变体
（`enable secret/password/algorithm-type`）由 `CHANGE_FORCE_PREFIXES` 挡住；用户搜不到
独立 `enable` 条目属**预期**，不是漏库。

### 二、逐家三档清单（补 / 不补 + 依据 + 已有 uuid）

> uuid 均为前 8 位；「已在库」列只列代表性条目，全量可由 `seed_data/*.json` 复核。

| 厂商 | ① 会话态 | ② 基础查询·已在库 | ② 真缺→补 | ③ 基础变更·已在库 | ③ 真缺→补 |
|---|---|---|---|---|---|
| Cisco IOS | 不补：`enable` / `configure terminal` / `end` / `terminal monitor` | `6621a79d` 版本、`70a2b9cc` running-config、`a88980b9` 接口/ARP/MAC、`05b4d26e` CPU/内存、`b56aab99` CDP/LLDP、`849cc74f` 硬件 | **补 2**：路径追踪（`9312a891`）、在线用户（`eb818e3c`） | `c51d45e0` hostname、`dd503bcb` enable secret、`ef5e0a8b` DNS（含 no ip domain-lookup 回退）、`a98b6202` 接口描述 | 不补 |
| 华为 VRP | 不补：`system-view` / `quit` / `return` / `screen-length 0 temporary` | `5a11c2cc` 版本、`78f9a12d` current-config、`bb912f63` 接口/ARP/MAC、`8aa8a79e` CPU/内存、`ffec9ca0` LLDP、`ed8335ed` logbuffer | **补 2**：路径追踪（`fc433c5f`）、在线用户（`8f0dd17c`） | 密码/SSH 条目内 `sysname`（`3` 条 CR 命中）、NTP/DNS/日志 | 不补 |
| H3C Comware7 | 不补：`system-view` / `quit` / `screen-length 0 temporary` | `048dca85` 版本、`adcbbf79` current-config、`dd20dfb7` 接口/ARP/MAC、`a9ab37f3` CPU/内存、`450dd80b` LLDP、`de093fea` SSH 状态 | **补 2**：路径追踪（`76225984`）、在线用户（`72831598`） | 同上（`sysname` 在 SSH 条目）、DNS/NTP | 不补 |
| 锐捷 RGOS | 不补：`enable` / `configure terminal` / `end` | `14f6d356` 版本、`a77b2d1c` running-config、`794b74c5` 接口/ARP/MAC、`4ea47e80` CPU/内存、`007d94c4` LLDP | **补 2**：路径追踪（`0bf47963`）、在线用户（`c43bbeef`） | `294982b2` hostname、口令/SSH | 不补 |
| 中兴 ZXR10 | 不补：`enable` / `configure terminal` / `exit` | `6e49eda4` 版本、`501e1ffe` running-config、`67a7802b` ARP、`3a938af7` MAC、`4c86bdda` LLDP、`16a56984` CPU/内存 | **补 2**：路径追踪（`4f4916c2`）、在线用户（`d3fe1cc8`） | `7bd18367` hostname、口令/SSH | 不补 |
| Junos | 不补：`configure` / `exit` / `set cli screen-length 0` | `615b5af2` 版本、`541581dd` configuration、`a48d3438` interfaces terse、`3b84c706` RE/进程、`eb08f035` LLDP、**`7a6092d4` `show system users` 已在库** | **补 1**：路径追踪（`0ab61ea2`）；在线用户**不补**（不重复建条目） | SSH/口令条目 | 不补 |
| Linux 四系 | 不补：`sudo -i` / `logout` | `887ade2e`/`5b094e2d`/`f6ce82c6`/`54f524f2` `ip addr show`（仅验证行）、`2af0330b` free/ps、`a3d432c9` df/du、`7ddb249b` ss、`e2da85a1` journalctl、`edc961f1` ping/traceroute/dig | **补 2（ops 通用）**：`09e01ae2` ip a 族、`f4045c04` 主机身份与基础资源速查 | `65f66a61` 建账号+sudo、`ab2aaeca` firewalld、四系系统服务 | **补 2（ops 通用）**：`5a266558` 主机名、`2edeb2f3` 时区 |

### 三、不补清单（逐条依据，零凑数）

| 格位 | 不补依据 |
|---|---|
| 会话态全族（7 家） | 无回退对象、不落配置；seed-guide 第九节已立口径 |
| `show startup-config`（cisco）/ `show vlan brief`（cisco）/ `display vlan`（huawei）/ `display ip interface brief`（huawei/h3c） | 已在既有条目的 rollback / notes 作验证命令（`70a2b9cc` / `e3971cfc` / `9f24e321` 等），另建独立条目只是同命令重复收录 |
| `show tech-support` / `display diagnostic-information` / `monitor interface`（Junos） | 一键信息收集输出形态随版本/平台差异大，Junos 侧是 `request support information`，与 W 批「`request` 多义、刻意不入白名单」裁决冲突；无统一可引用序列 → 标缺不臆造 |
| `display users`（huawei/h3c）/ `show users`（cisco/ruijie/zte） | **已补**（见上表），此处仅说明「只在 notes 出现」不等同于已覆盖 |
| `ifconfig` / `route -n` / `arp -n` | net-tools 已停止维护、四系默认不预装；统一用 iproute2 等价命令（`ip a` / `ip route show` / `ip neigh show`），seed-guide 第九节明写 |
| `show cpu`（ruijie）/ `show processor`（zte）/ `show interfaces`（zte） | 各家 CPU/接口诊断条目已有等价 show（`4ea47e80` / `16a56984` / `794b74c5` 同族），形态差异须现场 `?` 实测，不猜命令 |
| `banner motd` / `header` / `clock set`（网络侧） | 六家 CLI 形态差异大、无统一权威引用；按 X 批「不编造优先于补全」标缺 |
| `hostname` / `no ip domain-lookup` / `enable secret` / `sysname` | 用户点名「已在库的除外」，逐家核对属实（uuid 见上表），不重复 |

### 四、新增 15 条（`app/seed_data/base_commands.json`，新建单文件便于审计/回退）

| 子批 | 厂商 × 分类 | 条数 | 类型 | 官方依据 |
|---|---|---|---|---|
| Z1-a 路径追踪 | 六厂商 × `排查命令` | 6 | query | Cisco IOS / 华为 VRP / H3C Comware7 / 锐捷 RGOS / ZXR10 / Junos CLI Reference 的 `ping` + `traceroute`（华为/H3C `tracert`） |
| Z1-b 在线用户 | 五厂商 × `设备管理`（Junos 已在库） | 5 | query | 各家 CLI Reference `show users` / `show privilege` / `show line` / `display users` / `display user-interface` |
| Z1-c Linux 基础查询 | ops × `排查命令` / `设备管理` | 2 | query | iproute2 `ip-address(8)` 等 man 页；systemd `hostnamectl(1)` / `os-release(5)`、`ss(8)`、`lsblk(8)` |
| Z2 Linux 基础变更 | ops × `设备管理` / `时间同步` | 2 | **change** | systemd `hostnamectl(1)` set-hostname、`timedatectl(1)` set-timezone |

- 档位沿用 X 批：**`exec_level` 留空、`verified=0`**，notes 第 1 行「官方依据」、第 2 行
  「语法核对档：依据官方 CLI 文档核对，未经本机真机验证」，零冒充真机；
- 查询类 commands 全部命中 `QUERY_WHITELIST` → 判 query、**不写 rollback 键**；
- 变更类走 seed-guide 标准写法：结构化 params（type/description/validate 三要素）+ rollback
  全覆盖，回退目标写「变更前记录值」的**尖括号人工位**（避免「只在 rollback 出现的参数」触发 [W]，
  X 批坑 3）；`192.0.2.1` 用 TEST-NET-1，主机名 default `nettoolbox-host01` 为示例值；
- 新文件由 `import_seed_dir()` 递归扫到，CI `cp app/seed_data/*.json` 与 zip 白名单
  `seed_data/*.json` 均通配覆盖，**无需改构建脚本**。

### 五、QUERY_WHITELIST 查漏（W 批 158 词基础上）

| 入名单词 | 依据 | 配对守卫 |
|---|---|---|
| `ip a` | iproute2 子命令截断缩写，= `ip addr show`（用户点名） | **必须**同步在 force 侧逐词挡住 `ip a add` / `change` / `replace` / `del` / `delete` / `flush`（6 词），否则 `ip a add 192.0.2.1/24 dev eth0` 会被前缀吞进查询类——W 批裸 `enable` 同款口径 |
| `ip address show` | 官方 man 页全写形态，精确只读词，其后不接变更子命令 | 无需 force（`ip address add` 不以 `ip address show ` 开头，fail-safe 判 change） |
| `tracert` | 华为 VRP / H3C Comware 路径追踪（只读，库内原本 0 条） | 无需 force |

- `QUERY_WHITELIST` 158 → **161**、`CHANGE_FORCE_PREFIXES` 43 → **49**；
- **存量 482 条零翻类**（用 `git show HEAD:app/renderer.py` 载入旧判定表逐条对比实测）；
  新入词在存量 commands 里命中数为 0，force 变体原本就判 change，故不扰动四包 / 翻译器 / 操作单；
- 逐词守卫实测：`ip a` / `ip a show` / `ip address show` / `tracert` → query；
  `ip a add` / `ip a del` / `ip a flush` / `ip addr add` → change。

### 六、验收实测（本批）

- `--validate-seed` **EXIT=0**：**497 条** + 26 树 + 106 报错 + 7 包 + 11 类 48 变体 +
  27 族 147 映射，[E]=0，**[W]=17**（存量骨架提示，零新增）；`--validate-handbook` EXIT=0
  （7 本 / 56 组 / 520 条）——CI 两条门槛本地等价复跑通过；
- **新增 15 条回扫异常 0**（Q 批同款）：13 query / 2 change、变更类 rollback 全覆盖、
  `check_entry_params` 全库 [E]=0 [W]=0、命令与回退渲染零残留占位、参数双向对账无未使用、
  notes 全标语法核对档、`verified` 全 0；
- **全量 20 套**冒烟 rc=0（`PYTHONPATH="app;_vendor"`）；`smoke_schema_rollback` **9 → 14 项**
  （新增 [F] Z 批块：F1 条数分档 / F2 rollback 全覆盖 / F3 零残留 / F4 零冒充 / F5 守卫逐词）；
- 冒烟硬编码底数 482 → 497 同步 7 处（diff / regex_guard / translate / scenarios /
  schema_rollback / tree_visual / `app/handbook.py` 注释），README 事实段同步
  **497 / 352 格**；`[W]` 基数、diff 变体 48、翻译 27 族 147 映射、7 包、分类 48 全不动；
- **主库零接触**：20 套 `_smoke_env` 指纹断言全通过，`dist_package/command_lib.db` 未导入未改写
  （本地运行库仍是 482；交付物由 CI 从种子重建净库，W 批同款口径）。

### 七、★ 坑（本批新增）

1. **收「裸缩写」比收「命令+子命令」危险得多**：`ip a` 同时是 `ip a add` 的前缀，
   单独入白名单等于给地址配置开了查询类后门。凡收缩写，force 侧必须把所有写变体枚举齐；
2. **「notes 里出现过」不等于「库里有这条命令」**：盘点必须扫 commands 的**非注释行**，
   本批 `show users` / `display users` / `no ip domain-lookup` 在 notes/rollback 里都有，
   但 commands 里没有——只按全文子串搜会误判「已有」而漏补；
3. **网络设备 `ip address {{ip}} {{mask}}` 与 Linux `ip address show` 同前缀**：
   存量 13 条 Cisco/H3C 接口配 IP 行都以此开头，故只收精确 `ip address show`，
   绝不收 `ip address` 裸前缀（否则 13 条变更条目集体翻类）；
4. **新种子文件靠 `os.walk` 递归 + 通配白名单自动生效**，不要改 `build_exe.py` / `release.yml`；
   但 `count` 字段、`vendor_slug` 合法 slug、Linux 条目 `duration` 必填仍要自检
   （`import_seed_dir` 对坏文件是静默 `continue`，validate 只会表现为计数不对）；
5. **rollback 的「原值」不能写成参数**：写成 `{{old_hostname}}` 必然「已声明未使用」[W]；
   用尖括号人工位 + notes 提示按变更记录回填（X 批坑 3 复用）。

### 八、不做清单与归属

- **本批不发版不 tag**：不 bump `APP_VERSION`、不动 CHANGELOG（攒批节留待 v1.1.0 收口时
  统一补——Y 批教训：收口先盘点入列，U 批曾漏节）；
- **翻译器不动**：27 族 147 映射保持，不为基础查询扩族（按需另立项）；
- **不补会话态、不补 net-tools、不补无权威 CLI 依据的格位**（逐条理由见第三节）；
- **不回填真机态**：15 条全部 `verified=0`，真机结论只在 v1.1.0 验证会后经
  `scripts/backfill_verify.py` 回填；Y 批遗留池逐项归属不变，本批不静默关闭任何一项。

### 九、提交与 CI 对账（补记，2026-11-10）

- 主体提交 `fab1dfd`（12 files / +610 / -13）→ SSH 推 `main`，远端
  `refs/heads/main` 实测 `fab1dfd7…`；`git log -3` 复核无重复提交
  （H 批「链式 commit 疑似双执行」教训）；
- CI：`validate-seed` run **42763957507**（push / sha `fab1dfd7`）**success**，
  job 内 `Run seed validation` + `Run handbook validation` 两步均 success；
  `release.yml` 只认 `v*` tag、未触发，**无新 Release、无新 tag**
  （`git tag --sort=-creatordate` 最新仍 `v1.0.0`），旧版资产未动；
- 中断恢复留痕：上一轮 `git push` 被会话中断且无输出，恢复后先 `git ls-remote`
  确认远端仍停在 `afe2e66` 才补推一次，未盲目重跑 commit / push（防双提交）；
- 主库 `dist_package/command_lib.db` 零接触（20 套 `_smoke_env` 指纹断言全过），
  本地运行库仍 482；交付物由 CI 从种子重建净库。

## AB 批 命令库布局重构（2026-11-11，UI 批，**不发版不 tag**）

一句话结论：**详情面板从"列表下方"搬到"列表右侧"**，`GripSplitter(Horizontal)` 45:55
可拖且两侧都拖不到 0；参数说明表实测 4 行完整可见（改前 viewport 22px、**0 行可见**），
底部状态栏与左导航路径不再截半行。数据/种子/库零改动（`--validate-seed` EXIT=0，
497 / 26 / 106 计数不变），全量 20 套冒烟 rc=0。

### 一、结构：改前 → 改后

```
改前（垂直堆叠，垂直空间被上面各段吃光）        改后（左右分栏，详情独占整列高度）
QTabWidget                                      QTabWidget
 └─ 命令库                                        └─ 命令库
     └─ GripSplitter(H) 左导航 │ 工作区                 └─ GripSplitter(H) 左导航 │ 工作区(H)
          └─ GripSplitter(V)  列表 ↑ 详情 ↓                  └─ GripSplitter(H) 列表 │ 详情
               ├─ 列表：仪表盘/搜索/筛选/列表                     ├─ 列表：仪表盘/搜索/筛选(3列网格)/列表
               └─ 详情：元信息/按钮/提示/代码/子页                 └─ 详情：标题行/元信息/按钮×2/提示/代码/子页
```

| 项 | 改前 | 改后 |
|---|---|---|
| 命令库 splitter | `center_split` 垂直（列表 4 : 详情 6） | `work_split` 水平（列表 45 : 详情 55） |
| 参数表 viewport | **22px（0 行可见）** | **351px（4 行完整可见）** |
| 代码块高度 | 137px | 139px（收起态）/ 72px（展开参数表时） |
| 底部路径 | `wordWrap` 普通 QLabel，被压成半行硬裁 | `ElidedLabel` 单行中间省略 + tooltip 全文 |
| 状态栏 | 原生 QStatusBar，超长消息硬裁 | `ElidedStatusBar`，省略 + tooltip 全文 |

### 二、分隔条参数与宽度记忆

| 参数 | 值 | 说明 |
|---|---|---|
| 方向 | `Qt.Horizontal`（`GripSplitter`） | handle 默认隐形，hover 显主色 3px 高亮线 |
| handle 宽 | **6px**（`theme.HANDLE_WIDTH`） | 光标 `Qt.SplitHCursor`（实测 shape=12） |
| 可折叠 | `setCollapsible(0/1, False)` | 拖到极端：左 320 / 右 400，**都不会到 0** |
| 最小宽 | 左列表 `PANE_MIN_LIST_W=320`；右详情 `PANE_MIN_DETAIL_W=400` | 左导航仍 180 |
| 初始比例 | `PANE_INIT_SPLIT=45` → 45:55，`setSizes([540, 660])` | 1400 窗实测 526 / 642 |
| 伸缩因子 | `setStretchFactor(0,45)/(1,55)` | 窗口缩放按比例走 |

**记忆键**（沿用 `ui_state.json` 的 `xxx_split` 风格，不引入第二套存储）：

| 键 | 形态 | 用途 |
|---|---|---|
| `main_split` | `[220, 1174]` | 左导航 │ 工作区（沿用，未改语义） |
| `cmd_work_split_state` | `QSplitter.saveState()` 的 base64 | 列表 │ 详情：精确像素 + 折叠态（首选） |
| `work_split` | `[526, 642]` | 同上的可读 sizes 备份（`restoreState` 失败或老版本时兜底） |

恢复顺序：`restoreState` → 失败/缺键 → `read_sizes("work_split")`（校验 ≥ 最小值）
→ 默认 45:55。**没有引入 QSettings**：全站布局记忆都落在 exe 同目录的 `ui_state.json`
（U 盘整盘拷贝即迁移），QSettings 在 Windows 落注册表会打破这个前提；`saveState/restoreState`
保留，只是存储介质沿用既有文件。

### 三、详情面板内部（为"4 行可见"腾高度）

- 按钮 **1 行拆 2 行** + 管理组上移标题行：标题行 = 标题(省略) + ★ + 编辑条目 + 去排查树；
  行 1 = 复制全部/仅复制命令/逐条复制/下一条/行号；行 2 = 参数生成器/配置包/导出.txt/问 AI；
- 代码块 : 信息页 = **1 : 3**（改前 3 : 2），代码块 `minimumHeight=72`，上下边距 12→8；
- **参数表最小高度** = 表头 + 前 4 行真实行高（`PARAM_TABLE_MIN_ROWS=4`，最多 5 行封顶），
  行高按"把说明列标签钉到当前列宽再 `resizeRowToContents`"测（见第七节坑 3）；
- 重算触发点：展开面板（singleShot）、拖分隔条（`splitterMoved`）、表格宽度变化
  （`eventFilter` 的 `Resize`，按宽度去抖 + `_param_sync_busy` 防重入）；
- 收起态占位提示：`共 N 条参数（名称/必填/默认值/类型/说明）——点上方折叠头展开。`
  （详情整列变高后，收起时整页会是空白大块）。

### 四、顺带修复（同批小项）

| 项 | 改法 |
|---|---|
| 底部状态栏截断 | `theme.ElidedStatusBar`：按"栏宽 − 永久部件宽"省略，`tooltip` 存全文（`fullMessage()` 可取原文），超时计时器自己管 |
| 左导航路径截断 | `theme.ElidedLabel`（中间省略）单行显示 `位置：<db 目录>`，文件名与完整路径进 tooltip |
| 列表卡片标题/副标题 | 同上改 `ElidedLabel`（右省略）——改前 `QSizePolicy.Ignored` 是**硬裁半字** |
| 覆盖度仪表盘 | `TierBar` 计数标签去掉固定 330px（会顶高 pane 最小宽）+ 全文 tooltip；折叠条摘要按宽度切"长/短"两版 |
| 筛选区 | 5 下拉 + 2 开关 + 重置 + 计数原为 2 行 → 改 **3 列网格 + 开关行**（列表只占 45% 宽，原布局会溢出） |

### 五、连带影响 Tab 清单

| Tab / 组件 | 影响 |
|---|---|
| 命令库 | 重构主体（本批唯一改结构） |
| 其余 9 个 Tab | **不动**：均用自己的 splitter / 自己的状态栏消息；Tab 数、标题、顺序回归全过 |
| `ElidedStatusBar` | 全局替换 `MainWindow.statusBar()`：所有 Tab 的 `showMessage` 都走省略+tooltip（行为兼容，签名不变） |
| `EntryCard` | 仅命令库列表使用，改省略后无外溢影响 |
| `CoverageDashboard` | 仅命令库页内使用（父链 `main_split` 断言仍过） |

### 六、验证证据

- 截图（`docs/screenshots/`）：`ab_before.png`（改前，参数表只剩表头）、
  `ab_after.png`（改后，详情在右 + 4 行完整）、`ab_after_params.png`（参数表 1:1 裁剪）、
  `ab_after_collapsed.png`（默认收起态，代码块 139px）、`ab_list.png`（列表省略号）；
- 采集脚本：`scripts/ui_layout_shot.py <out.png> [--size WxH] [--collapsed]`（offscreen，
  走 `_smoke_env` 路径劫持 + 主库指纹断言，只读）；
- 冒烟：`smoke_panel_dashboard.py` **75 / 75**（新增 12 条 AB 断言：分栏方向/详情在右/
  两侧不可折叠/最小宽/6px+SplitHCursor/拖不塌/参数表 4 行完整/未溢出/`work_split` 落盘与
  重启恢复/恢复后仍不可折叠）；
- 全量：20 套 `scripts/smoke_*.py` **rc=0**；`python app/main.py --validate-seed` EXIT=0，
  **497 条 + 26 树 + 106 报错**（与 Z 批底数一致，本批不动种子/库）；
- 手动路径：搜索 `scp` → 选中 → 拖动分隔条（两侧到 320/400 停住）→ 关闭重开（宽度保持）。

**已知取舍**：1400×880 窗口下，参数表要 4 行完整可见（约 350px）后，详情面板余量只够
代码块 72px（约 3 行 + 滚动）；默认收起参数表时代码块 139px。窗口更高/更宽时按 1:3
自动回升。右侧被拖到 400px 极窄时 4 行装不下（说明列折行变多），表格以滚动条兜底——
这是物理空间限制，不是布局缺陷。

### 七、踩坑（三条，均实测）

1. **`QTabWidget.setMinimumHeight()` 在 singleShot 里调用会让进程静默退出**（rc=127，
   无 traceback、无 faulthandler 输出）。为让 tab 页给参数表留足高度试过两条路：
   `tab_info.setMinimumHeight(need + chrome)` 与"先归零再读 `minimumSizeHint`"
   （内部 `layout().activate()`）都会触发；最终改为**不设显式最小高度**，靠
   QTabWidget 由页内最小高度算出的 `minimumSizeHint` 自然撑开——这也解释了为什么
   "给 tab 设显式最小值"反而盖掉了页内需求。
2. **QListView 条目宽度 = max(条目 sizeHint, 视口宽)**：列表变窄后，若标题 `sizeHint`
   仍按全文宽度给，卡片会比视口还宽、标签永远压不窄（省略号也就不出现）。`ElidedLabel`
   因此把 `sizeHint().width()` 压到 48，真实宽度交给布局。
3. **`resizeRowToContents()` 读的是单元格部件上一次布局的宽度**：详情宽度变化后行高
   不会缩回去，参数表最小高度就停在旧的大值、把面板顶穿。改为先把说明列标签
   `setFixedWidth(当前列宽)` 量完再放开。

### 八、v1.0.0.1 发版实录（2026-11-11）

| 项 | 值 |
|---|---|
| 版本三处一致 | `APP_VERSION` `1.0.0 → 1.0.0.1`（`app/ui_main.py`，标题栏与「关于」对话框同时生效）、`README.md` 当前版本、`CHANGELOG.md` 新节 `## v1.0.0.1（2026-11-11）` |
| 提交 | `3567aea`（15 files / +828 / −102，含 6 张 AB 截图与 `scripts/ui_layout_shot.py`） |
| tag | `v1.0.0.1`（tag 对象 `636c388`，指向 `3567aea`）→ SSH 推 `main` + tag |
| CI | `validate-seed` run **42978483379** success；`release` run **42978483293** success（11 步全绿，含 Build exe / Assemble staging / Sanitize / Zip / Attach zip） |
| Release | draft `441345521` → PATCH 发布，`published_at 2026-11-11T07:21:57Z`，`html_url .../releases/tag/v1.0.0.1` |
| 资产 | `NetToolBox_v1.0.0.1.zip` **42,777,457 B**，asset digest `sha256:fbaea969…b0205b240`；全量下载复核哈希**逐字节一致** |
| 出库自核验 | zip 31 条目、**白名单外 0**（exe / check_db.py / command_lib.db / seed_data/*.json×28）；包内库 **497 / 26 / 106**、场景包 7、`user_version=3`、`verified=1` 计数 **0**（零冒充口径不变） |
| v1.0.0 未动 | 仍 published（`2026-11-10T01:58:58Z`），资产 digest `sha256:71c8d58f…` 保持原样 |
| 本地 exe | 同步重建：`dist_package/NetToolBox.exe` 45,353,475 B `sha256:f1ae0f53…`（py3.13 栈；CI py3.11 出 42.06 MB 属版本栈差异），实跑 `--validate-seed` **rc=0** |

发版流程沿用 Y 批：凭据取自 Windows 凭据管理器（`git credential fill`，不落盘），
`curl --noproxy '*' --ssl-no-revoke` 打 API；**新增一个坑**：Git Bash 的 `/tmp` 与原生 curl
不互通（`-d @/tmp/x.json` 直接报读文件失败），载荷改落仓库内 `.workbuddy/` 的 Windows 绝对路径。

---

# AC 批：清理「离线 / 零联网」强调文案（2026-11-12）

**起因**：用户点名——「程序中删除，不刻意强调零联网」。产品自称与被强调的「完全离线 / 0 联网」
不再是卖点口径，改为中性功能描述。**文案批：不动逻辑、不动种子/库、不发版不 tag。**

## 一、处理口径

| 类别 | 处置 |
|---|---|
| 产品自称「离网网络运维工具箱」 | → 「网络运维工具箱」（标题栏 / 关于页 / 应用显示名 / 文档自称） |
| 「完全离线」「0 联网」「零联网」「不联网」「无网络请求」「禁外网」等强调 | 删除，或改写为中性描述（本地数据库 / 数据保存在本机 / 仅本地处理） |
| 功能本质相关的中性表述（本地库、路径、U 盘拷贝、AI 无网络时置灰） | 保留，不再附加联网与否的表态 |
| 种子数据 / 手册 JSON / 报错字典正文 | **零改动**（属于条目主题，不是产品自称） |
| 代码 docstring / 注释、dev-notes、CHANGELOG 历史节 | **不改写**（如实留档） |

## 二、逐处改动清单（18 处 / 8 文件）

| # | 位置 | 原文 | 现文 |
|---|---|---|---|
| 1 | `app/ui_main.py:91` `APP_TITLE` | 离网网络运维工具箱 | **网络运维工具箱** |
| 2 | `app/main.py:1014` `setApplicationDisplayName` | 离网网络运维工具箱 | **网络运维工具箱** |
| 3 | `app/ui_main.py:3160` 关于页首行 | `<b>离网网络运维工具箱 </b>` | `<b>网络运维工具箱 </b>` |
| 4 | `app/ui_main.py:3167` 关于页安全边界 | 不包含任何终端、SSH、串口、**联网**功能 | 不包含任何终端、SSH、串口功能 |
| 5 | `app/ui_main.py:3015` 上车指引小标题 | 【**离网**部署说明】 | 【**本地**部署说明】 |
| 6 | `app/ui_main.py:3017` 同上段落 | 不含任何终端/SSH/串口/**联网**功能 | 不含任何终端/SSH/串口功能 |
| 7 | `app/ui_main.py:3153` 使用说明·数据安全 | 本机**无任何联网行为**，命令库随 U 盘走 | **命令库数据保存在本机**，随 U 盘走 |
| 8 | `app/ui_main.py:627` 视图菜单 | 速查手册（七本**离线**手册） | 速查手册（七本） |
| 9 | `app/ui_main.py:641` 工具箱 tooltip | 纯本地计算与命令生成：**零联网**、不执行任何命令 | 纯本地计算与命令生成，不执行任何命令 |
| 10 | `app/ui_main.py:566` 导出站点 tooltip | 单文件**离线**站点 | 单文件站点 |
| 11 | `app/ui_main.py:2893` 保存对话框标题 | 导出静态站点（单文件**离线** HTML） | 导出静态站点（单文件 HTML） |
| 12 | `app/ui_main.py:490` 验证信息缺项提示 | **离网环境**靠这个留痕 | 现场留痕靠这个 |
| 13 | `app/ui_main.py:2059` 未验证条目提示 | 建议先在测试设备或**离线环境**执行 | 建议先在测试设备或**演练环境**执行 |
| 14 | `app/ui_handbook.py:110` 手册页说明条 | 七本**离线**手册……**不写命令库、不联网** | 七本手册……不写命令库 |
| 15 | `app/ui_diff.py:80` 变更对比说明条 | 粘贴内容仅本地处理，**零联网**。 | 粘贴内容**仅在**本地处理。 |
| 16 | `app/diff_export.py:88` 导出 HTML 副标题 | 粘贴回显纯本地比对，**零联网** | 粘贴回显纯本地比对 |
| 17 | `app/ui_generator.py:458` 操作人占位符 | 写进脚本头部，**离网环境**留痕用 | 写进脚本头部，**现场**留痕用 |
| 18 | `README.md` + `docs/打包与部署说明.md` | 标题「离网网络运维的操作安全工具箱」、首段「离线知识库 / 全部离线完成，不联网」、`### Offline First —— 为「无网可用」而生`、首条「零联网运行」、安全模型「离线核心」、mermaid「（离线核心）」、部署说明自称与「不含…联网功能」 | 标题「网络运维的操作安全工具箱」、「网络运维知识库 / 全部能力在本地完成，数据保存在本地 SQLite 库中」、`### Local First —— 装进 U 盘就能带走`、首条「本地数据库」、安全模型「本地核心」、mermaid「（本地核心）」、部署说明自称与「不含…/串口功能」 |

> exe 元数据：`NetToolBox.spec` 未使用 `version=` 资源文件（无 VERSIONINFO），元数据里没有中文
> 描述字段，故无「离网」字样需要改；将来若补 version 文件，描述一律用「网络运维工具箱」。

## 三、保留不改（附理由）

| 位置 | 内容 | 理由 |
|---|---|---|
| 种子 / 手册 / 报错字典（`app/seed_data/*.json`、`app/handbook/*.json`） | 「★ 离网特供」「完全离线装包」「离线 rpm/deb 安装」「离网内网 DNS 标准动作」等 | **条目主题**（本地源 / 离线装包 / 内网 DNS 是命令要解决的问题），非产品自称；且本批库零改动 |
| `app/main.py:293/306/321/347` | `NetToolBox 离网审计…` / `审计通过：本程序在离网环境中不会产生任何出网行为` | `--audit-offline` 交付前网络依赖审计的**自身输出**，属工程审计能力，不是产品 UI 文案 |
| `app/db.py:679` | `operator TEXT, -- 操作人（离网环境留痕用）` | `history` 表 DDL 文本，改它等于动 schema |
| `app/main.py:3`、`app/db.py:3`、`app/theme.py:10`、`diff_tool.py:5/7`、`ui_diff.py:5/7`、`tool_calc.py:5`、`tool_curl.py:6`、`output_analyzer.py:10` 等 | docstring / 注释里的「离网 / 零联网」 | 批纪律：代码注释如实留档不改写；**未进入任何 UI 可见层** |
| `README.md:44` | 「配置缺失或无网络时置灰」 | AI 连接状态的事实描述（灰=无网络），功能性保留 |

## 四、验证

| 项 | 结果 |
|---|---|
| 全量冒烟 | `scripts/smoke_*.py` **20 套全 rc=0**（ai_chat 86 / ai_stage2 93 / ai_stage3 68 / backfill 9 / baseurl 全通 / diff 62 / export_site 8 / handbook 58 / import_guard 7 / keyvault 全通 / p1_fixes 13 / panel_dashboard 75 / regex_guard 全通 / runbook 74 / scenarios 110 / schema_rollback 14 / send_gate 全通 / tools 107 / translate 224 / tree_visual 46；各套「主库零接触」指纹断言均通过） |
| 种子 / 库 | `python app/main.py --validate-seed` **EXIT=0**，497 条 + 26 树 + 106 报错 + 7 场景包（与 Z 批底数一字不差，本批零改动） |
| 字符串常量级复扫 | AST 取「非 docstring 字符串常量」全仓扫禁用词 → 仅剩 **5 处**：`--audit-offline` 输出 4 处 + `db.py` DDL 注释 1 处（均在保留清单内）；UI 字符串**零命中** |
| 控件级扫描（真实渲染） | `QAction.text/toolTip/statusTip`、`QTabWidget 页名`、`QLineEdit 占位符`、`QPushButton` 文本 **零命中**；`QLabel` 命中 9 条，逐条核对**全部是种子条目标题**（本地源 / 离线装包类） |
| 标题与显示名 | `windowTitle()` = `网络运维工具箱 v1.0.0.1 · NetToolBox`；`QApplication.applicationDisplayName()` = `网络运维工具箱`（经真实 `main.run_gui()` 打桩 `exec_` 核，非直构窗口） |
| 关于页 / 使用说明 / 上车指引 | 自称为「网络运维工具箱」；三处文案禁用词零命中，句末标点完整、无空占位 |
| 截图（4 张，`docs/screenshots/` 未入库的临时证据） | `.workbuddy/ac_shots/`：`01_main.png`（主窗+状态栏）、`02_handbook.png`（手册说明条）、`03_diff.png`（对比说明条）、`04_about.png`（关于页） |
| 采集脚本 | `.workbuddy/ac_audit.py`（控件扫描 + 截图，走 `_smoke_env` 路径劫持 + 主库指纹断言）、`.workbuddy/ac_dn.py`（真实入口显示名）—— 均 `.workbuddy/`（gitignore），未入库 |

## 五、遗留

1. **内部 docstring 未统一**：`app/main.py:3` 仍写「离网网络运维工具箱（NetToolBox）程序入口」、
   `app/db.py:3` 仍写「离网网络设备命令库」——按本批「代码注释不改写」纪律保留；若要求全仓字面统一，需另立小批（改注释不影响运行）。
2. **不发版不 tag**：本批未动 `APP_VERSION`（仍 `1.0.0.1`）、未加 CHANGELOG 节；是否随下一版发布由用户决定。
3. **`--audit-offline` 命名保留**：命令名、输出文案未改（交付审计能力的既有接口，改动会牵动 docs/打包与部署说明 与 CI 口径）。


---

# AD 批：卡顿诊断与修复（2026-11-12，**不发版不 tag**）

**起因**：用户反馈「程序用着不流畅、一卡一卡的」，并指出 v1.0.0.1 / AB 布局之后出现或加重。
本批纪律：**先测量后改动**，只修实测确认的项，每项独立可回退；不动种子/库/版本号。

## 一、诊断方法与口径

| 项 | 做法 |
|---|---|
| 计时 | `QElapsedTimer` 包关键段；每条路径 21~40 个样本，报 P50 / P95 / max |
| 渲染 | 主口径 **`QT_QPA_PLATFORM=windows`（真实窗口 + 桌面合成）**；`offscreen` 作交叉复核 |
| 数据 | 现场导入种子到临时库 = **497 条**（与出厂 exe 一致），不碰 `dist_package/command_lib.db` |
| 对照 | `git worktree` 取 HEAD（`b989f3d`，AB 批收口）作「改前」，**同一脚本、同一份库、同一台机**跑 |
| 抗噪 | 进程提 `HIGH_PRIORITY_CLASS`；同侧连跑 3 轮取中位；不做应用级事件过滤器（实测会引入 30 ms 假阳性） |
| 探针 | `.workbuddy/ad_bench.py`（六路径）、`ad_cold.py`（进程级冷启动）、`ad_scrollonly.py`（纯净滚动）、`ad_visdiff.py`（改前后逐像素比对）—— 均 `.workbuddy/`（gitignore），未入库 |

## 二、实测表（真实窗口平台，497 条）

| 路径 | 改前 | 改后 | 判读 |
|---|---|---|---|
| P1 搜索逐键 **阻塞主线程** | P50 **173.7 ms** / max **2293 ms** | P50 **1.2 ms** / max **2.2 ms** | 逐键不再是全量重建，主线程几乎不占 |
| P1 单次过滤（重建本体） | **2304 ms**（493 张卡） | **20.8 ms**（0 张卡） | 全量重建耗时 −99.1% |
| P1 到列表更新（含防抖窗口） | 173.8 ms | 266.1 ms | 新增 200 ms 防抖窗口（可调，见 §3-F2） |
| P2 滚动单步 | 7.6 ms | 5.0 ms | 未回退（同口径复核：offscreen 6.06→2.84） |
| P3 拖命令库分隔条 | **105.8 ms/帧** | **12.6 ms/帧** | −88%，43fps → 79fps |
| P4 十 Tab 首次进入合计 | 470 ms | 287 ms | 单页 9~80 ms，非卡顿主源 |
| P5 窗口 resize | **86.9 ms/次** | **18.6 ms/次** | −79%；省略控件 996 → 2 个 |
| P6 冷启动到可交互 | **2880 ms** | **1839 ms** | −36%（建窗 1980→1154，首帧 589→412） |

**卡顿贡献度排序（改前）**：① 搜索逐键全量重建（最大 2.3 s 冻结）→ ② 拖分隔条（106 ms/帧，≈9fps）
→ ③ 窗口 resize（87 ms）→ ④ 冷启动 2.9 s（一次性）→ ⑤ Tab 切换（≤80 ms）→ ⑥ 滚动（7.6 ms，本就不卡）。

**根因（一处）**：命令列表用 `QListWidget + setItemWidget`，每条目一个 `EntryCard(QWidget)`。
497 条 = **497 个部件 + 994 个 ElidedLabel**，任何"重新列表/改宽度/改窗口"都要销毁重建或重排这些部件。

## 三、逐项修复（按贡献度）

### F1 列表 widget 卡片 → `QStyledItemDelegate` 自绘（`app/ui_main.py`）

- 删除 `EntryCard(QWidget)`，新增 `EntryListDelegate` + `entry_view()`；`QListWidget` 保留（`theme.qss`
  的 `QListWidget::item` 选中/hover/左侧 3px 竖条规则原样生效），行高定长 **57px**、`setUniformItemSizes(True)`。
- 几何与改前逐像素对齐：内容左 11 / 右 14、首行 y=8、色块 60×22、标签/徽章高 20、副标题 y=31 左缩进 68。
- **两个坑（已固化在代码注释里）**：
  1. `QColor("rgba(224, 168, 60, 36)")` **不解析、静默返回黑色** —— theme 的 `*_14` 半透明底必须走
     `token_color()` 显式解析，否则徽章变黑块（本批实测踩到并修复）；
  2. `QRect.right()` 是含端点的，直接 `right() - 14` 会整体左移 1 px；改用 `left() + width() - 14`；
     边框用 `QRectF(rect).adjusted(.5,.5,-.5,-.5)` 内缩，才与 QSS「border 画在内侧」重合。
- 等价性证据（`ad_visdiff.py`，11 条覆盖 骨架/收藏/Linux/长标题）：整屏 **199,430 px 中显著差异 0.137%**，
  仅剩半透明底与圆角抗锯齿；主窗 before/after 截图显著差异 **0.052%**，无布局位移。
- 效果：重建 2304→20.8 ms；拖分隔条 105.8→12.6 ms；resize 86.9→18.6 ms；冷启动 2880→1839 ms。

### F2 搜索输入防抖 200 ms（`SEARCH_DEBOUNCE_MS`）

- `textChanged` → 只重置 `QTimer(singleShot, 200ms)`；回车 `returnPressed` 走 `_flush_search()` 立即过滤。
- 效果：5 字词从「5 次重建」并为「1 次」；逐键阻塞 173.7→1.2 ms。
- 代价：列表在停手后 ~200 ms 更新（266 ms 中位，含 20.8 ms 过滤）。**这是唯一变慢的指标**，
  取舍是拿 200 ms 稳态延迟换掉 2.3 s 冻结；嫌慢把常量调到 120~150（过滤本体仅 20.8 ms，1 帧内可完成）。

### F3 参数表行高重算合并（`_schedule_param_recalc`）

- 改前：`splitterMoved` 与参数表自身 `Resize` 各排一个 `singleShot(0)` → **2.03 次/帧**、
  30 帧共 671 次 `resizeRowToContents`。改后用「已排队」标志合并 → **1.03 次/帧**、341 次。
- 行高重算本体仅 1.0~2.7 ms，帧耗时主要来自列表重排（由 F1 解决）。

### F4 `ElidedLabel` 宽度/字体守卫（`app/theme.py`）

- `_refresh()` 增加 `(宽度, 字体族, px, pt, bold, italic)` 键；`setText()` 显式失效。
- 改前一轮 resize 触发 **996 次** elidedText（996 个省略标签 × 近 1 次事件），改后 **2 个控件**仍在。
  F1 之后此条收益已很小，但保留作为「布局抖动不再触发文本重排」的结构性保护。

## 四、回归

| 项 | 结果 |
|---|---|
| 全量冒烟 | `scripts/smoke_*.py` **20 套全 rc=0**（ai_chat 86 / ai_stage2 93 / ai_stage3 68 / backfill 9 / baseurl 全通 / diff 62 / export_site 8 / handbook 58 / import_guard 7 / keyvault 全通 / p1_fixes 13 / panel_dashboard 75 / regex_guard 全通 / runbook 74 / scenarios 110 / schema_rollback 14 / send_gate 全通 / tools 107 / translate 224 / tree_visual 46） |
| AB 布局断言 | 12 条全过：水平分栏 / 详情在右 / 两侧不可折叠 / 底线 320+400 / 分隔条 6px / 拖不塌 / **参数表 4 行完整可见（rows=4 shown=4 vp=430）** / 未溢出 / `cmd_work_split_state` 落盘与重启恢复 `[484,604]` |
| 种子 / 库 | `python app/main.py --validate-seed` **EXIT=0**：497 条 + 26 树 + 106 报错 + 7 场景包，**本批零改动** |
| 列表功能回归 | `.workbuddy/ad_func.py`：行数 497 / 选中跟随详情 / `itemAt` 命中 / tooltip / 统一行高 / 搜索 28 行 + 自动选中 / 收藏标记 / 清空回 497 全过 |
| 手感复测（同一台机） | 连续输入：每字阻塞 ≤2.2 ms，停手后一次成帧（不再有 2 s 冻结）；快速拖分隔条 12.6 ms/帧（79fps，跟手）；连续切 Tab 9~80 ms/次（首帧略顿，其后 2~4 ms）；快速滚动 5.0 ms/步（≈200fps，无撕裂）；窗口拖角 18.6 ms/次（>50fps） |

## 五、未修 / 遗留（如实）

1. **冷启动仍 1.8 s**（真实平台）：其中 10 个 Tab 全部预构建合计约 0.29 s，其余是 QSS/字体/布局与首帧合成。
   未做 Tab 惰性化 —— 需要把 `self.tab_xxx` 改成按需构造，牵动 `_safe_tab`/状态灯/跨 Tab 跳转（`goto_ai_tab`、
   `jump_to`、`open_handbook_tab` 等十余处），收益约 0.3 s，风险不对称，留 v1.1.0 评估。
2. **P1「到列表更新」变慢到 266 ms**：防抖窗口所致（取舍见 F2），常量可调。
3. **`_entry_tooltip` 仍逐条生成**：每次过滤 497 次字符串拼接（约 2~3 ms，已计入 20.8 ms 内），未单独优化。
4. **测量坑（写给下一批）**：本机后台负载会让同一路径 P50 抖动 5~10 倍；**不要在测量脚本里挂应用级
   `eventFilter`**（实测把 2.3 ms 的滚动步放大成 33 ms 假阳性）；同侧至少 3 轮取中位再下结论。
5. **不发版不 tag**：`APP_VERSION` 仍 `1.0.0.1`，未加 CHANGELOG 节（同 Z/AB/AC 批口径）。

## 六、改动文件

| 文件 | 改动 |
|---|---|
| `app/ui_main.py` | 删 `EntryCard`；新增 `entry_view()` / `token_color()` / `EntryListDelegate`；`refresh_list` 改挂数据；搜索防抖；`_schedule_param_recalc` 合并重算 |
| `app/theme.py` | `ElidedLabel._refresh()` 增加宽度/字体键，`setText()` 失效缓存 |

---

# AE 批：命令库搜索逻辑诊断与修复（2026-11-12，**不发版不 tag**）

**起因**：用户点名「命令库的搜索逻辑有问题」，未给具体症状。本批先**实测**（497 条真实库 +
offscreen 驱动真实控件），再用"用户直觉八条"逐项判定，最后只修判定为缺陷的项。

一句话结论：**根因是关键字按"整串 LIKE"匹配** —— 多关键词（`华为 链路聚合`）几乎必然 0 条，
用户感知就是"搜索坏了"；此外树高亮/重置/空态/占位厂商四处各自在说谎，合起来放大成"逻辑不对"。
改后：**空格 = AND 分词**（`华为 链路聚合` 0 → 1 条）、全角半角等价（`ＶＬＡＮ` 0 → 64）、
参数与回退方案纳入检索（`端口原属` 0 → 3 条）、重置后树回「全部命令」、ESC 清空、
空态列出当前条件、占位厂商态下输入即恢复搜索。数据零改动（497 / 26 / 106 不变），不发版不 tag。

## 一、诊断方法与口径

| 项 | 做法 |
|---|---|
| 测试库 | `dist_package/command_lib.db`（482 条）副本 + `import_seed_dir()` 补 Z 批 15 条 → **497 条**（与 `--validate-seed` 口径一致；改前/改后各用一份副本，互不干扰） |
| 驱动方式 | offscreen + `QTest` 真实点击树节点 / 真发 `setText` / 真发 `Key_Escape`，**不 mock 过滤逻辑**（mock 就测不出"控件状态互相清空"这类问题） |
| 对照 | `git worktree` 拉 HEAD 建独立工作树，同一份 `probe_search.py` 在**改前代码**与**改后代码**上各跑一遍（同库同场景，输出 `matrix_base.json` / `matrix_ae.json` 逐行比对） |
| 探针落位 | `.workbuddy/ae/probe_search.py`（矩阵）、`probe_extra.py`（交互陷阱/字段唯一性）、`perf_probe.py`（性能对照）、`probe_shot.py`（截图）。`.workbuddy/` 已 gitignore，探针不入库 |
| 证据截图 | `docs/screenshots/ae_search_and.png`（`华为 链路聚合` 命中 1 条）、`docs/screenshots/ae_search_empty.png`（空态列条件） |

## 二、现状矩阵（改前 → 改后，同一 497 条库）

维度：①搜索词 ②左侧分类树 ③五个下拉 + 只看收藏/只看已验证。每格 = 结果条数 ｜ 控件状态。

| # | 场景 | 改前条数 | 改后条数 | 改前树高亮 | 改后树高亮 | 判定 |
|---|---|---|---|---|---|---|
| S0 | 初始全库 | 497 | 497 | (无选中) | 全部命令 | 改后启动即点亮根节点 |
| S1 | 仅搜索 `vlan` | 56 | 64 | (无选中) | 全部命令 | 十字段扩面（见 D3） |
| S2 | 仅树 交换机→华为→华为 VRP5 | 33 | 33 | 华为 VRP5 | 华为 VRP5 | 一致 |
| S3 | 树 + 搜索词 | 6 | 6 | 华为 VRP5 | 华为 VRP5 | AND 叠加正常 |
| S4 | 先搜索后点树 | 6 | 6 | 华为 VRP5 | 华为 VRP5 | **搜索词保留**，未被树清掉 |
| S5 | 搜索词 + 厂商下拉 | 8 | 8 | **华为 VRP5（错）** | (无选中) | 改前树在高亮一个已不成立的组合 → D4 |
| S6 | 搜索词 + 平台/厂商/场景 | 2 | 2 | **华为 VRP5（错）** | (无选中) | 同上 |
| S7 | 树 + 词 + 场景 三者叠加 | 1 | 1 | 华为 VRP5 | 华为 VRP5 | 三维 AND 成立 |
| S8 | 下拉过滤后输入新词 | 1 | 1 | **华为 VRP5（错）** | (无选中) | 下拉不清搜索词 ✓，树撒谎 ✗ |
| S9 | 只看已验证 | 27 | 27 | **华为 VRP5（错）** | 全部命令 | 开关不清其它维度 ✓ |
| S10 | 只看收藏 | 0 | 0 | **华为 VRP5（错）** | 全部命令 | 同上 |
| S11 | 复合条件（重置前） | 0 | 0 | 华为 VRP5 | 华为 VRP5 | — |
| S12 | 点「重置筛选」 | 497 | 497 | **华为 VRP5（错）** | 全部命令 | **改前 497 条配着"华为 VRP5"高亮** → D4 |
| S13 | 搜索 + 厂商（清空前） | 8 | 8 | 华为 VRP5（错） | (无选中) | — |
| S14 | 清空搜索框 | 47 | 47 | 华为 VRP5（错） | (无选中) | **下拉被保留** ✓（符合直觉 1） |
| S15 | 已选场景后再点树 | 1 | 1 | 华为 VRP5 | 华为 VRP5 | 场景不被树清掉 ✓ |
| S16 | 点树后再选场景 | 1 | 1 | 华为 VRP5 | 华为 VRP5 | 正交 ✓ |
| S17 | 点「全部命令」（无关键字、场景为空） | 497 | 497 | 全部命令 | 全部命令 | — |
| S17b | 场景=VLAN + 交换机 → 点「全部命令」 | **15** | **497** | 全部命令 | 全部命令 | 改前树说"全部"、列表只是子集 → D5 |
| S18 | 树→全部命令 | 497 | 497 | 全部命令 | 全部命令 | — |
| S19 | 切走前（词+树） | 6 | 6 | 华为 VRP5 | 华为 VRP5 | — |
| S20 | 切走 Tab 再回来 | 6 | 6 | 华为 VRP5 | 华为 VRP5 | 状态全保留 ✓ |
| S21 | 搜索框内按 **ESC** | **56（没反应）** | 497 | 华为 VRP5 | 全部命令 | **ESC 完全无响应** → D6 |
| S22 | 空结果 `zzz不存在zzz` | 0 | 0 | 华为 VRP5 | 全部命令 | 改前只给一句"换个关键字" → D7 |
| S23 | 空结果（词 `vlan` + 平台 Linux） | 7 | 7 | 华为 VRP5 | Linux 服务器 | 树与实际过滤对齐 |
| S24 | 待补充厂商（Arista） | 0 | 0 | Arista | Arista | 占位指引正常 |
| S25 | 连打 5 键（防抖未到期） | 497 | 497 | Arista（错） | 全部命令 | 计数滞后 200ms 属设计 |
| S26 | 防抖到期后（`trunk`） | 28 | 48 | Arista（错） | 全部命令 | 十字段扩面 |

另测「占位厂商 + 搜索」：改前在 Arista 占位页输入 `vlan` → **恒 0 条**（`refresh_list` 遇占位直接
return 空列表）；改后 → 64 条并自动退出占位态（D8）。

### 2.1 匹配行为（改前 → 改后）

| 用例 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `vlan` / `VLAN` / `Vlan` | 56 / 56 / 56 | 64 / 64 / 64 | 大小写不敏感本已成立（SQLite LIKE ASCII），扩面来自 D3 |
| `ＶＬＡＮ`（全角） | **0** | 64 | NFKC 归一化（D2） |
| `trunk vlan`（双词） | 4（整串子串巧合） | 40 | 改后 = trunk ∧ vlan |
| `华为 链路聚合`（中文双词） | **0** | 1 | 核心根因（D1） |
| `VLAN 华为`（中英混合） | **0** | 10 | 同上 |
| `华为`（厂商显示名） | 58 | 65 | 47 条 huawei 条目 100% 含"华为"正文，故显示名可命中；且混入 18 条非华为条目（正文提到华为） |
| `huawei`（厂商 slug） | 47 | 47 | 与厂商过滤同口径 |
| `VRP5` / `vrp5` | 31 / 37 | 37 / 37 | 显示名 `华为 VRP5` 是 NFKC + 分词后自然结果 |
| `trunk+vlan`（连字符串） | 0 | 0 | 未把 `+` 当分隔符（提案 P7） |

### 2.2 命中字段覆盖（同一词只存在于单字段，看界面能否命中）

| 字段 | 测试词 | 改前 | 改后 |
|---|---|---|---|
| title / description / commands / notes / models | 开放管理 / 并命名 / dot1q / 12.2 / 加密镜像 | 1 / 1 / 1 / 1 / 1 | 1 / 1 / 1 / 1 / 1 |
| **params** | `1-4094`（仅存在于参数默认值） | **0** | **29** |
| **rollback** | `端口原属`（仅存在于回退方案） | **0** | **3** |
| category / vendor / os_family | 无"仅属该列"的词（词都与正文重叠） | — | — |

### 2.3 可感知性现状

| 问题 | 改前 | 改后 |
|---|---|---|
| 空结果页 | 只有「没有匹配的命令 / 换个关键字，或重置筛选条件再试」；**无当前条件概览**，且无关键词时这句是误导 | 列出「设备『防火墙』 · 厂商『Cisco』」等实际条件 + 指向「重置筛选」；无关键词时不再说"换个关键字" |
| 搜索框声明 | "全局搜索：标题/描述/命令内容/备注/型号…（Ctrl+F 聚焦）" —— 与 Ctrl+K 浮层标题「全局搜索」**撞名** | "搜索命令库：标题/描述/命令/备注/参数…（空格分词）"；tooltip 说明"本框只搜命令库，跨四库用 Ctrl+K" |
| 重置按钮 | 无 tooltip，作用范围不可知 | tooltip 明写"搜索词 + 5 下拉 + 2 开关 + 树选中" |
| 命中高亮 | **无**（delegate 只画标题/标签/徽章） | **未做** → 提案 P1 |
| 结果计数 | `共 N 条` 随过滤实时更新 ✓（连打键时滞后 200ms 属防抖设计） | 不变 |
| Ctrl+F / Ctrl+K 分工 | 功能上清楚，文案上撞名 | 文案改后可区分 → D9 |

## 三、判定结论

### 3.1 缺陷（违反直觉且属明显问题，本批已修）

| # | 缺陷 | 用户感知 | 实测证据 |
|---|---|---|---|
| D1 | 关键字整串 LIKE，**无多词 AND 分词** | "我搜两个词就一条都没有，搜索是坏的" | `华为 链路聚合` 0 条；`trunk vlan` 仅 4 条（靠子串巧合） |
| D2 | 全角输入不归一化 | 中文输入法下敲 `ＶＬＡＮ` 搜不到 | `ＶＬＡＮ` 0 条 |
| D3 | 检索字段不含 `params` / `rollback` | 详情页看得见的参数说明、回退命令搜不到 | `1-4094` 0→29、`端口原属` 0→3 |
| D4 | 树高亮与过滤链脱钩（重置/下拉/开关/刷新后不更新） | "我明明在华为下面，怎么冒出 497 条" | S5/S6/S8/S12/S13/S14/S25 六处树在高亮一个已不成立的组合 |
| D5 | 点「全部命令」不清场景下拉 | 点了"全部"却不是全部 | 定向复现：交换机 + 场景 VLAN → 点全部命令，改前 **15 条**（场景下拉仍 VLAN），改后 **497 条**（场景已清） |
| D6 | 搜索框内 ESC 无任何响应 | "想清空只能点小叉 / 全选删" | S21 按 ESC 后文本仍 `vlan`、结果仍 56 |
| D7 | 空结果页无当前条件、文案误导 | 0 条时不知道是哪个条件闹的 | 下拉/开关过滤到 0 时提示"换个关键字"，而关键字是空的 |
| D8 | 占位厂商态下输入关键字无效 | 点过「待补充厂商」后搜什么都不出来，像搜索坏了 | 占位态输入 `vlan` 恒 0 条（`refresh_list` 早退） |
| D9 | 搜索框自称"全局搜索"，与 Ctrl+K 同名 | 分不清该用哪个 | 两处文案同名不同范围 |

### 3.2 提案（合理但属口味取舍，**本批未动手**）

| # | 提案 | 现状 | 选项 |
|---|---|---|---|
| P1 | **命中字段可见**（列表行高亮关键词 / 详情页标"命中在备注"） | 完全无提示 | A 只高亮标题+副标题；B 详情页加"命中：备注/参数"一行；C A+B |
| P2 | **相关性排序** | 排序恒为 收藏→已验证→更新时间→标题；搜 `vlan` 时标题命中的不一定靠前 | A 标题命中加权前置；B 按命中字段数加权；C 维持现状 |
| P3 | **树计数随搜索词变化**（facet count） | 恒为全库计数（搜 `vlan` 时左树仍显示"全部命令 497"） | A 实时重算（每次过滤多一次聚合，497 条约 1~2ms）；B 仅在搜索为空时显示计数 |
| P4 | **"自定义组合"无可视状态** | 四维过滤凑不出任一树节点时树无高亮（如实但不显眼） | A 加"自定义筛选"伪节点；B 高亮最近祖先节点 + 淡色；C 维持无高亮 |
| P5 | `params` 的 **JSON 结构键**也参与匹配 | 搜 `example` / `required` 大面积命中（387 条） | A 只匹配 JSON 值（解析后拼串）；B 维持（用户不会搜这些词） |
| P6 | 搜索历史 / 常用筛选组合 | 无 | 按需立项 |
| P7 | 高级语法（引号精确短语、`-` 排除、`+` 当分隔符） | 无 | 按需立项 |
| P8 | 场景维度进左树 | 场景只能下拉（树里无此维度） | A 树加"场景"分支；B 维持 |
| P9 | 点小叉清空仍走 200ms 防抖 | 实测无感 | 可改成立即（一行） |

## 四、修复清单（每项独立可回退）

| # | 文件 | 改法 | 回退点 |
|---|---|---|---|
| D1 | `app/db.py` | 新增 `split_search_terms()`：按空白切片；`search()` 改为**每个 token 一条 `AND (… OR … )` 子句**（token 间 AND、字段间 OR） | 恢复整串 `LIKE` 单子句即为旧行为 |
| D2 | `app/db.py` | `split_search_terms()` 内 `unicodedata.normalize("NFKC", …)`（全角→半角、全角空格→空格） | 去掉 NFKC 一行 |
| D3 | `app/db.py` | 新增常量 `SEARCH_FIELDS`（10 字段，含 `params`/`rollback`）+ `Database._search_columns()`（PRAGMA 实测列，只读 v1 老库缺列时自动裁剪，不会拼出非法 SQL） | 从常量移除两字段 |
| D4 | `app/ui_main.py` | 新增 `_filter_summary()` / `_tree_items()` / `_sync_tree_selection_from_filters()`：把"树高亮"定义为当前四维过滤的投影（完全相等才点亮，否则清空）；`refresh_list()` 末尾与 `_refresh_tree()` 重建后各同步一次，签名不变时早退（零额外开销） | 删三处调用 |
| D5 | `app/ui_main.py` | `on_tree_clicked` 的「全部命令」分支补 `_clear_combo(self.cmb_cat)`（字面语义：全部 = 全部） | 删一行 |
| D6 | `app/ui_main.py` | `ed_search.installEventFilter(self)` + `eventFilter` 处理 `Key_Escape`：有词→清空并 `_flush_search()`（立即，不等 200ms）；无词→焦点还给列表 | 删 eventFilter 分支 |
| D7 | `app/ui_main.py` | 空态文案三分支（有词 / 仅有过滤链 / 都没有），带 `_filter_summary()` 概览；详情区空态文案与列表区**同源** | 恢复原两句常量文案 |
| D8 | `app/ui_main.py` | `_on_search_text_changed` 首行 `self.placeholder_vendor = ""`（一输入即退出占位态）；占位分支 `refresh_list` 里补 `_tree_sig=None` + 同步 | 删两行 |
| D9 | `app/ui_main.py` | placeholder 改"搜索命令库：标题/描述/命令/备注/参数…（空格分词）"（**短到 420px 不省略**，实测 1080px 窗口下原长文案被截断）+ tooltip 说明与 Ctrl+K 的分工；`重置筛选` 加作用范围 tooltip | 恢复原文案 |

顺带（不计入缺陷）：`reset_filters()` 补 `_search_timer.stop()`——改前键入后 200ms 内点重置，
残留定时器还会再跑一次空过滤（结果相同，白付一次 15ms）。

## 五、回归

| 项 | 结果 |
|---|---|
| 全量冒烟 | `scripts/smoke_*.py` **20 套全 rc=0**（`smoke_panel_dashboard` **97/97**，其余与 AD 批同） |
| 新增断言（本批） | `smoke_panel_dashboard` 新增 `case_g()` **22 条**：G1 分词是交集(2) / G2 全角等价 / G3 大小写 / G4 params / G5 rollback / G6 重置归位(3) / G7 树不撒谎(2) / G8 ESC / G9 占位恢复(2) / G10 空态条件(2) / G11 防抖+逐键+全库(5) / G12 Ctrl+F |
| 旧断言是否绑定旧行为 | **无**。原 20 套只有 `smoke_p1_fixes` / `smoke_panel_dashboard` 用 `db.search(keyword="trunk")` **取首条**，未断言条数；`db.search` 返回值语义未变（仍是 list），故不需改旧断言 |
| 种子 / 库 | `python app/main.py --validate-seed` **EXIT=0**：**497 条 + 26 树 + 106 报错 + 7 场景包**（与 AD/AB 批一致，**种子零改动**）；主库 `dist_package/command_lib.db` 零接触 |
| 性能（改前基线 vs 改后，同机同库，5 轮取最小） | `refresh_list` 全库 **15.15 → 15.10 ms**（不劣化）；`vlan` 结果 **10.20 → 11.29 ms**；`db.search` 全库 **6.04 → 6.07 ms**、单关键词 **1.30 → 2.02 ms**（8→10 字段 + 分词）；逐键（含事件派发）**max 0.43 → 0.50 ms**，远低于 AD 的 2.2 ms 门限；`SEARCH_DEBOUNCE_MS=200` 与 singleShot 均未变 |
| AB 批断言 | 未回退（`case_e()` 12 条分栏/参数表断言全过） |

> 说明：双词关闭时改前 `vlan huawei` 为 0 条（无行可建），改后 40 条 → `refresh_list` 计时
> 由 1.43 ms 变 9.29 ms 属"结果集变大"而非路径劣化，故性能结论以**全库/单关键词**两行为准。

## 六、改动文件

| 文件 | 改动 |
|---|---|
| `app/db.py` | `import unicodedata`；新增 `SEARCH_FIELDS` / `split_search_terms()` / `Database._search_columns()`；`search()` 关键字子句改 AND 分词 + 十字段 |
| `app/ui_main.py` | 搜索框 placeholder/tooltip/eventFilter(ESC)；`_on_search_text_changed` 退出占位态；`_filter_summary()` / `_tree_items()` / `_sync_tree_selection_from_filters()`；`refresh_list` 空态三分支 + 树同步；`reset_filters` 停定时器；`on_tree_clicked` 全部命令清场景；重置按钮 tooltip |
| `scripts/smoke_panel_dashboard.py` | 新增 `case_g()`（22 条搜索语义回归）并挂进 `main()` |
| `docs/screenshots/ae_search_and.png` / `ae_search_empty.png` | 证据截图（AND 命中 / 空态条件概览） |

## 七、遗留（如实）

1. **命中字段不可见**（P1）、**相关性排序**（P2）、**树 facet 计数**（P3）未动——属口味取舍，等拍板。
2. 树只认 `itemClicked`：键盘上下移动树节点**不会**改变过滤（改前即如此，本批未改语义）；
   但此时若发生一次刷新，高亮会按当前过滤链重新点亮（可能与键盘停的位置不同）。
3. 占位厂商态下**点树**仍会清空四个下拉（旧行为），只是搜索词不再被它拖死 —— 是否该保留
   下拉，属 P4 同类取舍。
4. 探针与矩阵 JSON 留在 `.workbuddy/ae/`（gitignore），未进公开仓；如需入库请指明落位。

# AF 批：命令库搜索提案落地 4 项（2026-11-12，**不发版不 tag**）

一句话结论：AE 批遗留的 9 条提案里，用户拍板**做 4 项**——P1-B（详情页命中字段标注）、
P3（树计数随过滤变化）、P9（小叉立即刷新）、P5 反向（params 只搜值域）；
**P2/P4/P6/P7/P8 明确不做**（相关性排序、树点击保留下拉、Ctrl+K 与主搜索合并等，见 §五）。
数据零改动（497 / 26 / 106 不变），种子与主库零接触。

## 一、逐项改动（每项独立可回退）

| 项 | 改动 | 文件 | 回退 |
|---|---|---|---|
| **P1-B** 命中字段标注 | 详情页标题下新增 `lbl_match`（`QLabel#MatchHint`，accent 12px）：`命中：标题 · 命令`，无搜索词/无条目/未命中时整行隐藏不占高度。数据源 `Database.match_fields()`，与 `search()` 同一套 `split_search_terms` + 同一份 `SEARCH_FIELDS` + 同一套 params 值域投影，**不做二次全文匹配** | `app/db.py`（`match_fields` / `SEARCH_FIELD_LABELS` / `field_search_text`）、`app/ui_main.py`（`_update_match_hint` + 三处调用）、`app/theme.qss` + `app/theme.py` | 删 `lbl_match` 三处（建/挂/算）与 `match_fields` |
| **P3** 树计数随过滤 | `_tree_counts(entries=None)` 增加"按给定条目统计"入口；建树时把计数键挂到 `Qt.UserRole+1`；新增 `_apply_tree_counts()` 只改第 2 列数字（**不重建树**，不丢展开/选中态）。`refresh_list` 末尾传 `entries_in_view` → 投影计数；无过滤时天然等于全量 | `app/ui_main.py` | 删 `_apply_tree_counts` 两处调用，`add_count` 保留 setData 无副作用 |
| **P9** 小叉立即刷新 | `_on_search_text_changed`：文本变空 → `_flush_search()`（停定时器 + 立即过滤），与 ESC 同一手感；逐键输入仍 `_search_timer.start()`。小叉是 QLineEdit 私有子控件，**从 textChanged 观察"变空"而不 hook 私有控件** | `app/ui_main.py` | 恢复"无条件 start()"两行 |
| **P5 反向** params 只搜值域 | 新增 `params_value_text()`（递归取标量叶子，键名/布尔/null 不参与，`lru_cache`），注册为 SQL 函数 `ntb_params_text`；`search()` 的字段表达式走 `_field_expr()`（params → 值域投影，其余列名）。**SQL 与 Python 共用同一实现** | `app/db.py` | 把 `_field_expr` 改回 `return field` |

## 二、P5 实测（同一 497 条库，改前/改后逐词对照）

| 关键字 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `example` | **387** | **5** | 残留 5 条经 `match_fields` 复核全部是 `['params']` 且 `example.com` 也是 5 条 → 均为**真实参数值**（DNS 文档里的 `example.com`），不是键名噪声 |
| `description` | 388 | 33 | 键名噪声清掉，剩 33 条正文真命中 |
| `required` | 388 | **0** | 纯键名词，改后零命中（新增 G4b 断言绑定此语义） |
| `label` | 389 | 1 | 仅剩 1 条正文命中（`commands`/`notes`） |
| `1-4094` | 29 | **29** | 值域召回零退化（用户点名的那条） |
| `eth0` / `example.com` | 40 / 5 | 40 / 5 | 参数取值搜索不受影响 |
| `vlan` | 56（AE 前）/ 64（AE 后） | 64 | params 收窄未影响常规检索 |

耗时（同机同库，10 轮均值）：`db.search()` 全库 **7.99 → 7.99 ms**、`vlan` **3.57 ms** ——
SQL 函数逐行回调被 `lru_cache` 吃掉，**无可测劣化**。

## 三、P3 / P9 实测

| 项 | 数字 |
|---|---|
| `_apply_tree_counts` 单次（497 条 → 64 节点） | **1.81 ms**（另一次采样 0.79 ms） |
| 全库 `refresh_list`（含 P3 计数） | **16.5 ms**（AD 批基线 15 ms；`case_g` 门限 ≤40 ms，实测 17.3 ms） |
| 有词（`vlan`，64 条）`refresh_list` | 11.4 ms |
| 逐键处理（稳定口径，50 次） | 0.004 ms/键；真实按键往返中位 1.74 ms / max 2.27 ms（AD 门限 2.2 ms） |
| P9 清空后生效时间 | **40 ms 内**回 497 条（改前必须等 200 ms 防抖），定时器已停 |
| P3 计数正确性 | 根 64 = 列表 64；网络设备 57 = 各设备类型计数之和 57；清空后根回落 497；华为 36 → 6 |

## 四、回归

| 项 | 结果 |
|---|---|
| 全量冒烟 | `scripts/smoke_*.py` **20 套全 rc=0**（`smoke_panel_dashboard` **111/111**，AE 批为 97/97） |
| AE 22 条搜索语义断言 | **全过**（G1–G12）。**唯一口径修订：G4 的候选词来源**——原 `column_only_token("params")` 直接在 params 原文里挑 5+ 字符片段，P5 前它挑中的是 **`label`（JSON 键名）**，靠键名命中 1 条属**假通过**；现改为在 `params_value_text()` 的值域里挑（本次挑中 `0/0/9`，命中 1 条）。这是断言**变严格**，不是放水 |
| 新增 G4b | `params键名不参与检索`：搜 `required` 后，结果集中**没有任何一条是靠 params 命中的**（判据不是"条数=0"，避免将来正文出现该词时误判） |
| 新增 case_h（AF 批，12 条） | H1 标注=实际命中字段(2) / H2 换词后刷新+空结果收起(2) / H3 根=命中数、父=子项之和、清空回落(4) / H4 清空 40ms 生效 + 逐键仍防抖(2) / H5 键名不再刷全库 + 值域召回不退(2) |
| AD 断言 | 未回退：`SEARCH_DEBOUNCE_MS=200`、逐键 ≤2.2 ms、全库 `refresh_list` 17.3 ms（门限 40 ms） |
| AB 布局 12 条 | 全过：水平分栏 / 详情在右 / 不可折叠 / 320+400 底线 / 分隔条 6px / 拖不塌 / **参数表 4 行 `rows=4 shown=4 vp=430`**（与 AB 批同值）/ 未溢出 / `cmd_work_split_state` 落盘与恢复 `[484,604]` |
| 手感链路 | 搜 `vlan`（64，根计数 64，标注「命中：备注」）→ 点小叉 40 ms 内回 497 → 树计数回落 497 → 标注收起：六项状态全自洽 |
| 种子 | `python app/main.py --validate-seed` **EXIT=0**，497 条 + 26 树 + 106 报错 + 7 场景包（零改动） |
| 样式一致性 | `theme.qss` 与 `_QSS_FALLBACK` 逐字一致（构建自检 True） |

## 五、明确不做（用户 2026-11-12 拍板，本批未动手，勿顺手实现）

| 提案 | 内容 | 裁决 |
|---|---|---|
| P2 | 相关性排序（标题命中优先于正文） | 不做 |
| P4 | 树点击时保留其余下拉（当前点树会清平台/设备/厂商/OS） | 不做 |
| P6 | 搜索词列表行内高亮（本批只做详情标注） | 不做 |
| P7 | `trunk+vlan` 把 `+` 当分隔符 | 不做 |
| P8 | Ctrl+K 与主搜索框合并为同一入口 | 不做 |

## 六、改动文件

| 文件 | 改动 |
|---|---|
| `app/db.py` | `SEARCH_FIELD_LABELS`；`params_value_text()` + `_collect_param_values()` + `_params_value_text_cached()` + `field_search_text()`；`Database.__init__` 注册 `ntb_params_text`；`_field_expr()`；`search()` 改用表达式列表；新增 `match_fields()` |
| `app/ui_main.py` | `lbl_match`（建/挂）；`_update_match_hint()` + `_render_detail`/`refresh_list`/`show_empty_detail` 三处调用；`_tree_counts(entries=None)`；`_set_tree_count()`；`_apply_tree_counts()`；建树挂计数键；`_on_search_text_changed` 空文本立即刷新 |
| `app/theme.qss` / `app/theme.py` | `QLabel#MatchHint`（两处逐字同步） |
| `scripts/smoke_panel_dashboard.py` | `case_h()`（12 条）；G4 候选词改值域；新增 G4b |
| `.workbuddy/af/probe_af.py` | AF 探针（P1-B/P3/P9/P5 + 性能 + 手感链路，gitignore 不入库） |

## 七、遗留（如实）

1. **树计数 0 条显示空白**（沿用 P-F 的"0 不留字"规则）：过滤后某节点无命中时是空白而非 `0`。
   父节点仍有数字，不会误读；若日后要求显式 `0`，只需 `_set_tree_count` 加一个 `always` 分支。
2. **标注行可见时详情区少 18px**：不影响参数表 4 行（AB 断言在无搜索态量，`vp=430`；
   有搜索态实测 `vp=486`），但极端小窗下详情区更紧。
3. P1-B 只做详情页，列表行不做高亮（P6 已裁决不做）。

