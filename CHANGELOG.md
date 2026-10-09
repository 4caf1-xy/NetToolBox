# Changelog

本文件记录 NetToolBox 对外可感知的变更。版本号遵循 semver 口径：
新功能 / 大改 / 种子扩充 → 次版本 +1；纯修复 → 修订号 +1。

## v0.5.0（2026-10-09）

自 v0.4.0 以来的全部变更。主题：**schema 浪潮**（params 结构化收尾 + rollback 配对 + validate [E] 收口）+ **Ctrl+K 全局搜索** + **覆盖度仪表盘** + 扫尾种子 + **compose 实验室**上线。种子规模 327 → **338 条命令 + 26 棵排查树 + 82 条报错字典**。

#### 种子数据
- **rollback 回退方案全覆盖（B23 闭环）**：变更类 231 条全部内置 rollback（218 条含实体回退序列 + 13 条说明型），15 厂商分 16 个提交点逐批落地（cisco/huawei/h3c/juniper/fortinet/paloalto/sangfor/topsec/centos/kylin/openeuler/ubuntu/ops/ruijie/zte）；undo/no/delete 序列以"恢复变更前状态"为终点、复核命令收尾，口令类以尖括号人工位回填旧值。注入期 **[W] 缺口 240→0 软着陆**：每厂商批完即 validate，"注入数 + 翻 query 数"逐步核销，全程对账吻合（记录见 docs/dev-notes.md 批3 抽查节）；**防火墙四家 51 条对照表呈人工复核通过后解除硬停**。validate 升 **[E]**——变更类缺 rollback / rollback 占位符未声明一律 ERROR 拦截（AI 入库钩子同口径），宽容期结束，全量 validate EXIT=0 零违规；
- **desc_pending 迁移机制退役**：986 个死标记全量清扫（描述 100% 已填），结构化条目 description 为空一律 ERROR（单一执法点 renderer.check_entry_params）；清扫过程三不碰探针断言（uuid/verified/notes/exec_level/commands/title 零改动）+ 幂等重跑 0 清理，主库备份 `backup/command_lib-pre-params-20261009.db`；
- **扫尾批（覆盖度低洼填平 + B24 树缺口清零）**：命令条目 327 → **338**——zte ×4（CPU / 接口状态 / 系统日志 / BGP 诊断，接口诊断/日志/BGP 三个分类从 0 起步）+ juniper ×4（同场景四条 Junos 命令）+ openEuler ×3（tcpdump 五式 / 连通性三连 / ethtool 链路诊断，对齐兄弟发行版形态）；排查树厂商引用缺口 **84 → 0**（validate 树覆盖度提示清零）：12 棵网络树 by_vendor 回填 zte/juniper uuid 硬引用 80 处，Linux 2 棵树 4 处叶子动作 cmd_ref 改 uuid 硬引用消除歧义；
- **[W] 参数警告 6 处清零**：U 盘挂载拷贝删冗余 dst_path 参数（目标固定为挂载点）；cisco / 锐捷 / PAN-OS / 天融信 / 深信服 5 条版本升级条目补 backup_server 参数引用（新增 0b 备份服务器可达性确认步）；validate 参数 WARNING 归零，本批起新条目与存量同标准；
- 冒烟基线重算：覆盖度矩阵逐格对账 227 → 233 格（新增 6 格为 zte/juniper × 接口诊断/日志/BGP），对账逻辑不变 mismatch=0；by_vendor 双向对账 567 处引用 0 异常。

#### 新功能
- **详情页『回退方案』折叠面板**：变更类条目内置回退序列随条目展示（默认收起，代码块字体 + 命令注释高亮），查询类/未配置条目整页隐藏；**变更类复制确认弹窗**——复制变更类命令前提示回退路径（含"本会话不再提示"复选框，当日有效次日失效，不提供永久永不弹），全局开关在『视图』菜单；查询类不弹；挂点覆盖详情页复制全部/仅复制命令/逐条复制 + 参数化生成器四处；
- **变更类判定表入库（单一事实源）**：`renderer.QUERY_WHITELIST`（命令+子命令精确词前缀，含强制变更组）+ `classify_entry`（仅扫非注释行，fail-safe 默认变更类），validate 与 AI 入库钩子共用；起草过程 5 轮精化修正 21 条会话级/只读诊断条目的误判（enable/terminal monitor/trapping/monitor start/diagnose sys ha 只读族/top -b 家族/ip -4 变体），消除 21 条凑数 rollback 误报；
- **AI 管线对齐**：SYSTEM_PROMPT 增补产出约定（【参数表】JSON 块全字段 / 变更类必带【回退方案】块）；`ai_bridge.parse_ai_blocks/strip_ai_blocks` 解析并剥离标注块；三处 AI 入库点接线（合并入库/排查树步骤/诊断草稿），`"params": []` 硬编码清零——AI 草稿自带的参数与回退方案随条目入库并受同一校验把关；
- **Ctrl+K 全局命令面板**：任意 Tab 一键唤起居中浮层（视图菜单同入口），一次输入同时搜三库——命令复用 `db.search()` 八字段匹配、报错字典匹配 pattern/原因/处置步骤/样例、排查树匹配现象/分类/适用厂商/步骤标题；结果按 [命令]/[报错]/[树] 分组展示（厂商·分类副标题，每组上限 40 条），键盘完整可达（↑↓ 选择、Enter 跳转、Esc/点击面板外关闭），空输入显示「最近使用」分组、空结果给明确文案；跳转自动切 Tab → 复用各库定位机制 → 选中条目并展开详情（报错字典定位到字典维护对话框、排查树直接载入走树）；
- **最近使用**：面板跳转与详情页打开自动记录（复制不记录），uuid+类型+时间戳随 ui_state.json 持久化，去重置顶上限 10，库中已删除条目自动丢弃；面板空输入即见最近使用，面板内一键清空（带确认）；
- **覆盖度仪表盘**：命令库顶部折叠卡片，收起时常显一行摘要（总数/绿/挂起/未验证），展开见厂商×分类条目数矩阵（底色深浅映射条目数，灰格=无条目）+ 三库四档验证进度分段条（绿/语法核对/挂起/未验证，色值全走 theme token）；口径与 verify-guide 四档标准一致——「不可验证」类（centos7 systemctl 等 notes 标注）如实计入挂起段，语法核对档（verified-cli 未判绿）独立成段；数据全部 db 直查无缓存、零写入口，只读视图。

#### 界面优化
- **参数面板控件结构化优先**（裁决F）：生成器表单优先按 `type=enum+choices` 渲染下拉、`type=int+range` 渲染数字框，`validate=` 解析降级兜底；条目编辑器参数表补 type/choices/range/description 四列（离网人工补条目免手写 JSON），并修复参数表回读丢失表外字段（on_value 等）的潜在数据丢失。

#### 工程
- **compose 实验室上线**：`lab/docker-compose.yml` 四服务（FRR / SR Linux / CentOS 7 / Ubuntu）一键验证环境 + 指南 [docs/lab/README.md](docs/lab/README.md) + CI `lab-smoke` 冒烟（up→wait-healthy→逐服务断言，失败路径全量 annotation 诊断）；随 CI 实证修复一批：frr 改 privileged（cap_sys_admin）、centos7 弃 systemd PID 1 改 sshd 前台直跑 + vault EOL 源、ubuntu 补 systemd-sysv、srlinux 加 tty + mem 2g、wait-healthy 假阳性修正、示例2 断言按实证修正（FRR 不展示下一跳不可达静态路由等）；
- **entries 表新增 rollback 列**（建表 + 老库懒升级 ADD COLUMN）：`.nlb` 导出携带、老文件导入缺字段按空串向后兼容；rollback 进 EDITABLE 白名单（与 P1-2 verified 护栏无冲突）；
- **smoke 主库零接触改造**：新增 `scripts/_smoke_env.py` 统一测试环境——db/ai_bridge/theme 路径解析全劫持到一次性临时目录，真主库 sha256 指纹 atexit 复核、不一致退出码 42 硬失败；六套件头部接线，smoke_ai_chat 自带假配置消除隐式读真 `ai_config.json` 的暗接触；310 项全绿且主库指纹前后逐字节一致；
- 新增 `scripts/smoke_schema_rollback.py`（9 项）：[E] 校验口径正反例、判定表词边界/强制组/全注释体、desc_pending 清零终态、备份锚点、回退面板三态、复制确认四分档、AI mock 正反例；新增 `scripts/smoke_panel_dashboard.py`（43 项）：面板三库命中/激活/空结果、跳转定位、最近使用记录/去重/上限/落盘/清空、仪表盘四档口径与矩阵逐格对账、B14 连点回归、四 Tab 布局与 Splitter 记忆回归；全量冒烟 9+43+13+84+93+68 = **310 项全绿**，仪表盘逐格对账 mismatch=0；
- **B14 收藏防重入修复**（审计 2026-10-08 遗留）：toggle_favorite 写期间置忙 + 按钮禁用 + 250ms 时间窗，连点不再产生多余 history 记录（回归 smoke_panel_dashboard.py [D]）；
- **CI 自动打包发版上线**：新增 `.github/workflows/release.yml`——push `v*` tag 触发，windows-latest 装依赖（PyQt5 / PyInstaller / requests）→ `scripts/build_exe.py` 构建 → 产物断言（exe 存在且体积合理、zip 非空且体积合理、出库包不含密钥/运行时文件）→ 出库组装（exe + 净库重建 `command_lib.db` + `check_db.py` + `seed_data/`，与本地出库纪律一致）→ zip 自动挂载到对应 Release：已存在则 `--clobber` 覆盖同名附件，不存在则建 **draft** 待人核后 publish；CI 只见公开仓内容，构建产物天然无密钥（安全边界写入 workflow 注释）；
- migrate_params.py 退役标注保留（使命完成）。

#### 文档
- seed-guide 增补四·B 节（rollback 形态/判定表/三类典型写法）+ **rollback 反例清单**六类（no 形式带属性参数、解引用次序、华为 port-group 组内撤销等起草实例）；AGENTS.md 补记主库实际路径与判定表备忘；
- dev-notes 落批3 抽查记录（总账/白名单精化 5 轮/幻觉反例/防火墙复核/全查组与分厂商抽查）、ui_tree_editor 能力清单与差距报告候选池（G1–G7，触发条件制，不立项）、smoke 主库零接触改造记录；
- README 更新至 v0.5.0：新功能入档（Ctrl+K 含快捷键表 / 覆盖度仪表盘 / 回退安全体系 / validate [E] / AI 结构化产参）、种子规模 338、lab/ 目录导引。

## Unreleased

（暂无——下一批变更从这里开始累积。）

## v0.4.0（2026-09-29 首发，2026-10-08 重新发版）

自 v0.3-batch3 以来的全部变更，共两批。首发批主题：**全站界面重构 + 工程治理**；追加批主题：**参数系统结构化升级 + 全量审计修复**。种子库规模维持 **327 条命令 + 26 棵排查树 + 82 条报错字典**。

### 追加批次（2026-10-08 随重新发版并入，提交 b04b802 → 1e31bc3）

本批主题：**参数系统结构化升级** —— params 自描述 schema + 完整命令预览 + 注释渲染；
追加：**AI 回复渲染修复 + 全局 QSplitter 分栏改造**；
追加（2026-10-08）：**全量审计修复批** —— P1×3 修复 + 小型 P2 清扫 + dead code 清理（审计报告：docs/audit/20261008-full-audit.md）。

#### 新功能
- **params 结构化 schema**：参数规格新增 `type`（string/int/enum/ip/flag）、`description`（必填）、`choices`（enum 必带）、`range` 等自描述字段；`validate` 细粒度校验规则保留并与 type 推导互补（显式规则优先），旧格式（无 type）完全兼容继续放行；
- **flag 开关型参数**：不勾选 → 占位从命令中整体消失，勾选 → 渲染指定文本；生成器以勾选框呈现；
- **详情页参数说明折叠面板**：默认收起，无参数条目整页不渲染；行结构升级为「名称｜必填*｜默认值｜类型｜说明」，说明列含 description 正文 + 范围/示例/可选值灰字附注（enum 展示全集）；
- **详情页完整命令预览状态条**：缺必填参数 → 红条提示并**阻止复制全部/仅复制命令/逐条复制**（P2 落实），预览保留 `{{占位}}` 不生成残缺命令；缺选填 → 黄条提醒；
- **# 注释行渲染增强**：命令区注释行按灰斜体小字呈现，种子约定注释行以 `#` 开头独立成行，复制命令时可剔除。

#### 种子数据
- 启动存量 987 个参数规格的结构化迁移（`scripts/migrate_params.py`，dry-run 预览 + 快照兜底 + 幂等可重跑），迁移期空描述带 `desc_pending` 标记仅报 WARNING，AI 分批补齐后清扫标记；
- 种子规模维持 **327 条命令 + 26 棵排查树 + 82 条报错字典**，升级后 `--validate-seed` 全量 EXIT=0。

#### 界面优化
- **AI 回复渲染修复**：AI 回复改为全宽内容卡片（对话区宽度减两侧 24px 边距；用户消息仍为右侧气泡 ≤80%）；代码块等宽字体 + 自动换行 + 换行悬挂缩进，长命令（如 grep ListenAddress 类）不再横向裁剪且可完整复制；代码块右上角"复制"浮层（accent 底）复制原始文本；"第 N 步"类标题渲染为节标题（加粗 + 上下间距），观察/判断/结论等标签词加粗提色；
- **分栏全局可拖拽**：AI 诊断（对话区│输入区，默认 4:1，输入区最小高 120px）、命令库（列表│详情）、报错诊断、排查向导四个 Tab 分栏统一改为 QSplitter，分隔条默认隐形、hover 显主色高亮线；分栏位置记忆至 ui_state.json，重启自动恢复（恢复时校验尺寸合理性，坏值回退默认）；最小窗口 1280×800 不破版；流式输出宽度只增不减、滚动不跳动。

#### 工程修复
- **审计修复批（2026-10-08，五笔 commit 400a2c7 → de6c3f0）**：
  - P1-1 命令库/报错诊断四处 db 写入口（标记验证/取消验证/报错验证/收件箱）补异常弹窗，只读竞态不再静默失败；
  - P1-2 导入不携带验证态：verified 四字段新增强制 0、更新不采纳（外部 .nlb 无法伪造验证状态），验证状态只经真机回填流程产生；已验证条目内容仍可导入更新；
  - P1-3 AI 对话取消即时生效：端点停滞时点"停止"由最长等 90s 降为约 0.6s（实测底层 socket `_real_close` 才能唤醒 Windows 阻塞读），异常路径补发取消信号防按钮卡死；
  - 新增 `scripts/smoke_p1_fixes.py` 三案永久回归（弹窗/验证态归零/慢端点取消，全 mock）；
  - 小型 P2 清扫：会话文件名表达式、附件按字节截断标注、quiet 静默语义、探测线程旧实例收尾、依赖探针意图注释；
  - dead code 清理 12 组函数/常量 + 约 30 处无用导入（pyflakes 全量仅剩 3 条意图探针）；
  - `[去排查树]` 同分类多树时弹选择（≤3 棵轻量菜单 / 更多带关键字过滤），不再静默取第一棵；
  - 5 处散落硬编码色收敛 theme token（新增 `ERROR_LINE_BG`，`TREE_CATEGORY_COLORS` 迁 theme.py）。
- `validate-seed` 参数校验收口为统一的 `renderer.check_entry_params`（与 AI 入库钩子同一口径）：`{{占位符}}` 引用未定义 → ERROR；定义未引用 → 升格为显式 WARNING；default 违反自身 type/choices/range → ERROR；新条目 description 为空 → ERROR；
- AI 产出入库增加校验钩子：对账不过 / 结构化字段缺失一律打回不入库；AI 产出 prompt 同步要求结构化参数标注与 `#` 注释行。

#### 文档
- 新增 `docs/seed-guide.md`：面向贡献者的 params 字段写法 + 完整示例条目 + 提交自查清单；README 贡献指引同步链接。
- **文档体系全面更新**：新增 `docs/README.md`（文档地图：每份文档的受众/回答问题/更新时机，每主题唯一权威出处）、`docs/architecture.md`（数据流 + 三库职责 + 双仓分工）、`docs/verify-guide.md`（验证操作手册：环境映射/四档判定/回填流程/报错复现/晋升标准）、根目录 `CONTRIBUTING.md`（薄壳，PR 流程 + Issue 三类指引）；README 架构节与贡献指引改为链接架构/贡献文档；
- `docs/seed-guide.md` 补齐：命名/分类/厂商代码约定（从现有种子归纳）、validate-seed 规则摘要、红线专节（示例 IP 用文档专用段、default 禁真实值、description 三要素）；
- `AGENTS.md` 增补文档落位规则（新 .md 只进 docs/ 对应位置，禁根目录新建）与 params 约定生效时机注记；
- `docs/打包与部署说明.md` 收敛为纯打包+部署手册：修正 `scripts/build_exe.py` / `app/main.py` 路径，剥离模块功能演进史（以 CHANGELOG 为唯一权威出处），数字改为"以 --validate-seed 实时输出为准"；
- `docs/dev-notes.md` 已知问题清单化（排查树遗漏分支 → checkbox 跟踪，标注来源批次）；`docs/prompts/README.md` 补批次对照索引；审计报告文首加快照归档头注（不再更新，日后审计另起新文件）。

### 首发批次（2026-09-29）

#### 界面优化
- 全站 UI 样式重构：引入统一样式表（theme.qss），命令库、排查向导、报错诊断等八个功能页视觉整体翻新，观感与交互风格保持一致；
- 版本号正式落位：标题栏显示「离网网络运维工具箱 v0.4.0 · NetToolBox」，关于对话框同步显示版本号（此前关于框内的版本信息与发布版本脱节，已修正为统一常量驱动）。

#### 工程修复
- 打包入口统一为 `scripts/build_exe.py`：构建产物定向到仓库根 `build/`、`dist/`，交付物自动收拢到 `dist_package/`，不再散落；
- 废除旧版 `.stale-*` 让位机制，改为 `.trash-<时间戳>` 瞬时让位（构建成功后自动清理）；
- 仓库根目录治理：移除历史构建残渣，界面截图归位 `docs/screenshots/`（过程稿进 `legacy/`），根目录门面收敛为 README / LICENSE / 配置模板 + 标准目录结构。

#### 种子数据
- 本版无新增，规模维持 **327 条命令 + 26 棵排查树 + 82 条报错字典**（`--validate-seed` 全量校验通过，样例自测 100% 命中）。

#### 文档
- README 种子规模更新为实测值，主界面截图更换为 UI 重构后成品图；
- 新增本 CHANGELOG（首版回填 v0.3-batch3 以来的全部变更）。
