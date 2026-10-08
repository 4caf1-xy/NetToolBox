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

