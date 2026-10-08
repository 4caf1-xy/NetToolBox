# 功能实测矩阵 —— 全量代码与功能审计（2026-10-08）

> 审计基线 commit：`8095ba6`（2026-10-01）。实测环境：Windows + 系统 Python 3.13.4 + PyQt5（offscreen），
> requests 经隔离 venv 桥接（PYTHONPATH，仓库零改动）。全程只读（内存库 + 程序自带 offscreen 截图工具）。
> 证据编号对应 docs/audit/20261008-full-audit.md 方法说明节。

## 实测通道

| 通道 | 覆盖 | 结果 |
|---|---|---|
| `--validate-seed` 硬门槛 | 种子库质检网关 | **EXIT=0**，327 条目 + 26 树 + 82 报错（[W] 6 处参数警告 + 84 处覆盖提示均不阻塞，与文档一致） |
| `--check-ai` | AI 集成自检 | 桥接 requests 后 **EXIT=0**；未桥接时 EXIT=1（系统 Python 缺 requests，exe 交付路径不受影响，见审计报告环境备注） |
| scripts/smoke_ai_chat.py | AI Tab 8 大项 | **84/84 通过**（新建/上下文折叠/发送流式/历史/案例/草稿/向导入口/界面重构） |
| scripts/smoke_ai_stage2.py | 产出入库全链路 | **93/93 通过**（三去向入库/溯源/查重升级/树追加/历史抽屉统计） |
| scripts/smoke_ai_stage3.py | 查重三级决策 | **68/68 通过** |
| .workbuddy/audit_probe.py（审计探针，32 项） | 命令库/排查向导/报错诊断数据链 + 主窗口组装 | **32/32 通过** |
| scripts/ui_screenshot.py | 四 Tab 渲染证据 | docs/screenshots/legacy/audit-20261008/ 四张 PNG |

## 功能矩阵

| 功能点 | 声明来源 | 实测结果 | 证据 | 判定 |
|---|---|---|---|---|
| **命令库 Tab** | | | | |
| 种子导入/口径 | README/AGENTS.md | 327+26+82=435 全量导入，重复导入幂等 | probe #1-4 | ✅ |
| 关键字搜索（标题/描述/命令/备注） | README | "trunk" 19 条命中；中文"路由"可命中 | probe #5、validate-seed 自检输出 | ✅ |
| 多维过滤（平台/厂商/OS/场景/收藏/已验证） | README | vendor+platform 组合 34 条；收藏过滤空集正常 | probe #6-7 | ✅ |
| 左侧分类树（四层/三层双形态） | 规格 01/03 | network 3 设备类型、linux 5 发行版 | probe #8-9 | ✅ |
| 详情渲染 + 参数填充 | renderer.py | render_entry 正常渲染、缺失参数 0、params 3 个 | probe #11-12 | ✅ |
| 复制（全部/仅命令/逐条） | README | 剪贴板入口 copy_to_clipboard（ui_main.py:86），UI 全局唯一实现 | 截图 command_lib.png | ✅ |
| 收藏/取消（含连点） | README | 数据层连点两次还原；UI 层无防重入（见审计 P2） | probe #14 | ✅（数据层）/⚠️（UI 层） |
| 修改历史留痕 | db.py | toggle 两次产生 3 条 history | probe #15 | ✅ |
| .nlb 导出/回导闭环 | README | 327 导出 → 空库回导 327 | probe #16 | ✅ |
| **排查向导 Tab** | | | | |
| 树列表/分类分组 | 规格 04 | 26 棵、7 分类全渲染 | probe #17-18、截图 troubleshoot.png | ✅ |
| 步骤推进/结论展示 | 规格 04 | steps 结构完整（id/branches/leafs） | probe #19 | ✅ |
| cmd_ref 命令跳转 | db.py | 26 棵树首步解析全部命中、零歧义 | probe #20 | ✅ |
| **报错诊断 Tab** | | | | |
| 关键字检索/匹配详情 | 规格 05 | 82 条字典；样例正则自测命中 | probe #22-23、截图 errorfix.png | ✅ |
| 关联命令跳转 | err_dict.ref_entry_uuid | smoke_stage2 覆盖（93 项含跳转链路） | smoke_stage2 | ✅ |
| 收件箱（零匹配入库/删除） | db.py | 写入/列出/删除闭环 | probe #25-27 | ✅ |
| **AI 诊断 Tab** | | | | |
| 新建对话/设备上下文折叠 | smoke 8 项 | 折叠状态持久化 + 跨实例继承 | smoke_ai_chat #9 组 | ✅ |
| 发送与流式 | smoke | chunk 缓冲 100ms 批量渲染、取消恢复 | smoke_ai_chat #1-6 组 | ✅ |
| 历史/案例目录 | smoke | 打开恢复不重复落盘、删除带确认 | smoke_ai_chat #8 组 | ✅ |
| 产出入库全链路 | smoke stage2/3 | 三去向 + 溯源 + 三级查重 161 项 | smoke_stage2/3 | ✅ |
| 附件（文本/图片） | ai_bridge | smoke 覆盖（大小上限/截断标注） | smoke_ai_chat | ✅ |
| 断网报错表现 | ai_bridge | _friendly_net_error 转人话；DNS/超时/拒绝分档 | ai_bridge.py:278-289（静态） | ✅（静态核实） |
| **全局** | | | | |
| 窗口组装/四 Tab | ui_main.py:728-731 | 四 Tab 顺序正确、refresh_all 无异常 | probe #28-29 | ✅ |
| QSplitter 分栏记忆 | AGENTS.md §6 | split_state/restore 收口 ui_main.py:851-875，closeEvent 落盘 | 代理审计 + 静态核实 | ✅ |
| validate-seed 网关 | AGENTS.md §5 | EXIT=0 硬门槛通过 | 通道表 | ✅ |
| 主题 token 遵守 | AGENTS.md §6 | setStyleSheet 全 app 仅 1 处（ui_main.py:313 色块） | grep 全量 | ✅ |

## 声明了但未实测/需人工的项（如实记录）

| 项 | 原因 | 状态 |
|---|---|---|
| 真机窗口拖拽手感/高分屏缩放视觉 | 离屏模式无法模拟 | 需人工走查（无证据不作判定） |
| U 盘只读降级全流程 | 需物理只读介质 | 静态核实（db.py:509-559 逻辑完整），未实测 |
| 真实 AI 端点流式对话 | 审计不发真实请求 | 静态核实 + smoke mock 覆盖 |

## 文档欠账（没声明但存在的功能）

- `ai_imports` 溯源双向跳转（条目详情 ↔ 会话）在 README 功能列表中未提及
- `--audit-offline`（main.py:206，离网审计自检）未写入 README 使用说明
- 查重"追加原因分析"决策（append_err_reason，db.py:2601）用户文档无描述
