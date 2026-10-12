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
