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

## 打包链路史（防 .stale- 复发）

- 2026-09-23 ~ 09-28：旧版 build_exe.py 因 WorkBuddy 沙箱拦截批量删除（进程级终止），采用"rename 成 .stale-<时间戳>"让位，导致每次打包 +2 个 stale 目录，累计 460MB 残渣（2026-09-29 已全量清除）。
- 2026-09-29 起：产物经 `--workpath/--distpath/--specpath` 定向仓库根；让位改用 `.trash-<时间戳>` 命名，构建成功后自动尝试彻底删除。**任何会话不得再引入 `.stale-*` 机制**（详见根目录 AGENTS.md）。
