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
