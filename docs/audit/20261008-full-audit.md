# NetToolBox 全量代码与功能审计报告（快照归档）

- **审计日期**：2026-10-08
- **基线 commit**：`8095ba6`（2026-10-01 22:23 feat: AI 回复全宽内容卡片 + 全局 QSplitter 分栏改造），工作区干净
- **审计范围**：app/ 全部 16 个 .py（21,707 行）+ scripts/ 全部 9 个工具（2,872 行）+ app/theme.qss（780 行），合计 23,219 行；排除 _vendor/、build/、dist/、dist_package/、backup/
- **审计性质**：全程只读。零代码/种子/配置改动（git status 验证：仅新增审计产物文件）
- **分级标准**：P0=数据丢失损坏/崩溃路径/安全风险；P1=功能缺陷/边界出错；P2=隐患与坏味道。每条附 file:line + 证据 + 影响 + 一句话修复方向
- **配套产物**：功能矩阵见 [docs/qa/functional-matrix.md](../qa/functional-matrix.md)；证据截图 docs/screenshots/legacy/audit-20261008/

---

## 总览

| 模块 | P0 | P1 | P2 | 备注 |
|---|---|---|---|---|
| db.py | 0 | 1 | 4 | 数据层整体健壮（幂等导入/只读降级/损坏自愈设计到位） |
| ai_bridge.py | 0 | 1 | 5 | 网络层隔离干净（lazy import/loopback 防代理/key 无泄漏） |
| ui_main.py | 0 | 1 | 2 | 写入口异常反馈不统一 |
| ui_ai.py | 0 | 0 | 4 | 245 项冒烟全过；遗留性能与生命周期隐患 |
| ui_errorfix / ui_troubleshoot / editor×3 | 0 | 1 | 4 | 与 ui_main 同类问题 |
| renderer / analyzer / report / error_match / dedupe | 0 | 0 | 2 | dead code 为主 |
| theme.py + theme.qss | 0 | 0 | 1 | token 遵守率极高 |
| main.py + scripts/ | 0 | 0 | 4 | validate-seed 网关 EXIT=0 |
| **合计** | **0** | **3** | **26** | |

**P0 结论：未发现。** 专项核查过的 P0 候选路径（损坏库自愈、导入原子性、key 泄漏、路径注入、公开仓红线、跨线程 UI 操作）全部排除，证据见 §安全专项 与各条目。

---

## P1 逐条（3 条）

### P1-1 db 写入口异常反馈缺失：失败时用户无感知
- **位置**：app/ui_main.py:2250、2261（mark_verified）；app/ui_errorfix.py:405（mark_err_verified）、412（add_unresolved）
- **证据**：四处槽函数直接调用 db 写方法且**无 try 包裹**。而 db 层在只读/IO 异常时会 raise（db.py:1234-1235 `RuntimeError("命令库处于只读状态…无法标记验证")`）。同文件其余写入口（delete ui_main.py:2126-2127、duplicate :2100、编辑器 ui_editor.py:969/1000/1024）均用模态 + try + `QMessageBox.critical` 显式报错——唯独验证标记/收件箱这条链路漏了。
- **影响**：U 盘写保护竞态（打开程序后盘被切只读）时点"标记已验证"→ 异常抛进 Qt 槽，traceback 只进 stderr/日志文件，界面无任何提示，用户误以为标记成功。
- **复现**：把 command_lib.db 设为只读 → 启动程序 → 选中条目点"标记已验证"→ 确认对话框后界面无反应（日志文件出现 RuntimeError）。
- **修复方向**：四处补齐与 delete 一致的 try + QMessageBox.critical。

### P1-2 导入路径 verified 语义不一致：外部文件可改写验证状态
- **位置**：app/db.py:1271-1275（INSERT 路径强制 verified=0）vs db.py:1295-1304（UPDATE 路径未过滤 verified/verified_by/verified_model/verified_date）
- **证据**：`import_entries` 新增分支强制 `data["verified"] = 0`（注释："内置种子库同样一律 verified=0"）；但更新分支对 EDITABLE_FIELDS 全字段比对，`verified`/`verified_by` 等都在 EDITABLE_FIELDS（db.py:47-53）内——源 .nlb/种子 JSON 里带 `verified: 1` 时，库内未验证条目会被直接改成已验证。
- **影响**：与"必须走真机验证流程"的注释意图相悖；恶意/误编辑的 .nlb 可伪造验证状态。**方向存疑**：团队间 .nlb 同步"已验证条目"可能是期望行为（INSERT 强制 0 反而会丢队友的验证状态），需要产品裁决后统一两侧语义。
- **复现**：导出 .nlb → 手工把某条目 `"verified": 0` 改为 1 并填 verified_by → 回导 → 该条目绿徽章点亮，history 记 action=verify。
- **修复方向**：先裁决语义（建议：UPDATE 路径同样强制 0，团队验证状态同步走显式字段白名单），再对齐 INSERT/UPDATE 两分支。

### P1-3 ChatWorker.cancel 注释与实现不符：网络停滞时取消最长等 90 秒
- **位置**：app/ai_bridge.py:862-864
- **证据**：`cancel()` 文档串写"置标志 + 关闭底层连接（iter_lines 会立刻抛错退出循环）"，实现只做 `self._cancelled = True`，**没有保存 resp 引用、没有 close 调用**。`iter_lines` 阻塞在无数据可读时，标志位要等下一个 chunk 或 read 超时（cfg.timeout，默认 90s）才被检查。
- **影响**：端点挂起时点"停止"按钮，UI 恢复要等超时；期间用户可能重复操作。
- **复现**：配置一个能连上但不回数据的端点（如 nc 监听端口）→ 发送 → 点停止 → 观察按钮恢复时间 ≈ timeout 而非立即。
- **修复方向**：worker 保存 resp 引用，cancel() 里直接 resp.close() 使 iter_lines 立即抛错。

---

## P2 逐条（26 条，按类归组）

### A. Dead code（vulture + pyflakes 线索，已逐条 grep 核实无调用点）

| # | 位置 | 内容 | 备注 |
|---|---|---|---|
| A1 | db.py:57 | `EXEC_LEVEL_LABELS` 未使用 | 疑似 UI 侧自带了映射 |
| A2 | db.py:1205 | `set_favorite()` 未使用 | bug 整改纪念 API，调用方都走 toggle_favorite |
| A3 | db.py:2133 | `trees_for_category()` 未使用 | 注释声称供[去排查树]按钮用，按钮实际无调用——**功能意图落空**，与 P2-B17 相关 |
| A4 | ai_bridge.py:361 | `build_messages()` 未使用 | 被 build_chat_messages 取代 |
| A5 | ai_bridge.py:423 | `save_case()` 未使用 | 被 sessions 体系取代（旧库保留只读兼容） |
| A6 | ai_bridge.py:866 | `cancelled()` 方法未使用 | UI 直接读 _cancelled |
| A7 | renderer.py:680 | `expand_port_range_lines()` 未使用 | |
| A8 | renderer.py:878 | `build_package()` 未使用 | |
| A9 | theme.py:1109 | `code_font_family()` 未使用 | |
| A10 | ui_main.py:155 | `set_rich_text()` 未使用 | |
| A11 | main.py:119 | `merged_specs` 赋值未使用 | |
| A12 | scripts/check_db.py:112,116 | `norm_platform`/`norm_duration` 未使用 | |
| A13 | 多文件 | 无用导入约 15 处：ui_ai.py:33-46（QUrl/QSplitter/BG_RAISED/WARNING_14/DANGER_14）、:1927 subprocess；ui_main.py:29-52（QPainter/QPalette/QSplitter/QComboBox/QCheckBox + 8 个 theme token）；ui_editor.py:65、ui_errorfix.py:33、ui_tree_editor.py:56、ui_troubleshoot.py:55（theme.repolish）；scripts/migrate_params.py:42 renderer | |
| A14 | scripts/smoke_ai_stage3.py:214-215 | `ai_bridge` dir() 守卫死代码 | **pyflakes 误报标注**：pyflakes 报 undefined name，实际 dir() 守卫使其运行时不崩，仅是永不生效的防御行 |
| A15 | scripts/build_exe.py:231 | `shutil` 重复导入覆盖 ：181 | |

### B. 隐患与坏味道

| # | 位置 | 内容 | 影响 |
|---|---|---|---|
| B1 | db.py:820-822 | `_migrate()` 整体 except 静默 | 老库升级失败（如 ALTER 半途失败）无任何痕迹，后续读路径可能撞 "no such column" |
| B2 | db.py:595-598 | 损坏库改名失败被 pass | 改名失败后仍连原文件，错误延迟暴露 |
| B3 | db.py:1435-1437 | 种子单文件导入失败静默 continue | 坏 JSON 文件用户无感知（数量对不上才发现） |
| B4 | db.py 导入循环 | import_trees :1664 / import_errs :1878 逐行 commit | 非原子：中途断电得半批数据（不损坏，但无"整体回滚"语义；与 add_entries_from_ai 的单事务承诺不一致） |
| B5 | ui_ai.py:1303 | `_adjust_browser_height` except pass | 气泡高度停更、内容裁切无提示 |
| B6 | ui_ai.py:2213 | 溯源标签 except pass | DB 异常时 🤖 来源标签静默消失，溯源断裂 |
| B7 | ui_ai.py:3397 | 历史统计 except pass | 已入库徽章缺失 |
| B8 | ui_ai.py:1377-1385 | 流式渲染每 100ms 对**累积全文**做 md→HTML+setHtml | 长回复后期 CPU/卡顿主要来源（性能隐患，功能可用） |
| B9 | ui_ai.py:1622-1629 | NetworkProbe 无 parent、旧 probe 仅 isRunning 跳过不清理 | 收尾竞态下信号可能丢失；靠引用覆盖防 GC，脆弱但当前不崩 |
| B10 | ai_bridge.py:775 | `"%s.json" % session.get("session_id") or "session"` 优先级错误 | session_id 缺失时写 None.json，`or "session"` 兜底永不生效 |
| B11 | ai_bridge.py:586-587,613 | 附件截断按**字符**读却标注"仅前 N 字节" | CJK 内容下标注失实（差 3 倍量级） |
| B12 | ai_bridge.py:429-431 | save_case 同秒 + 同现象 slug → 同名覆盖 | 极端情况下丢案例 |
| B13 | ui_errorfix.py:680 | `quiet` 参数被传入但函数体内从不读取 | _auto_test 语义（静默测试）未实现 |
| B14 | ui_main.py:1998 toggle_favorite | UI 层无防重入（数据层幂等，见探针 #14） | 连点产生多余 history 记录 |
| B15 | ai_bridge.py:156,233,871 | `import requests` 后未直接使用 | **pyflakes 误报标注**：实为 ImportError 依赖可用性探针，属意图写法，建议加注释平息工具告警 |
| B16 | db.py:607-617 | `_readonly_uri` 仅转义 `%/空格/#/?` | 含 `;:@` 等极端字符的路径可能打不开（低概率，中文路径已覆盖） |
| B17 | ui_main.py | [去排查树]按钮声称跳转相关树，实际 trees_for_category 无调用 | 与 A3 同源：按钮功能与数据层意图脱节（需核实按钮现行为） |
| B18 | theme token | 硬编码色非 token 5 处：ui_errorfix.py:226-227,247（#e0a83c/#ff6b6b）、ui_troubleshoot.py:36-40（TREE_CATEGORY_COLORS）、ui_generator.py:96,916（#e05656）、ui_editor.py:835（#9aa0b0） | 换主题时不变；AGENTS.md §6 要求只走 token |
| B19 | ui_main.py:313 | 全 app 唯一 setStyleSheet（色块背景） | 轻微偏离"样式只走 theme.qss" |
| B20 | ui_ai.py:633 vs theme.py:1094 | `set_state` 同名不同义（Tab 状态机 vs widget 属性） | 命名混乱点 |
| B21 | 重复逻辑 | 验证标记对话框 ×3（ui_main:2250 / ui_errorfix:405 / ui_tree_editor 同构）；编辑器表单校验同构（ui_editor._validate:606 vs ui_tree_editor.on_save:607-627） | 重构候选，只记录不动手 |
| B22 | 仓库根 CHANGELOG.md | AGENTS.md §1 根目录门面清单未包含 CHANGELOG.md | 规范与现状偏差（发版惯例先行），建议 AGENTS.md 加豁免或移入 docs/ |
| B23 | validate_seed vs seed-guide 契约 | 结构化 params 强校验（ERROR 拦截）尚未合入，当前 6 处仅 [W] 警告 | **文档先行的预期偏差**（AGENTS.md §5 已预告），不算缺陷 |
| B24 | seeds 覆盖度 | 排查树 84 处厂商条目缺口提示（zte/juniper 集中） | 种子数据欠账，validate-seed 不阻塞 |
| B25 | scripts/ui_screenshot.py | 依赖系统字体目录硬编码 C:\Windows\Fonts | 仅开发机工具，可接受 |
| B26 | 环境 | 系统 Python 缺 requests 时 --check-ai EXIT=1 | **非产品缺陷**：exe 由 build_exe.py 注入依赖；开发环境用隔离 venv 桥接即可（本次实测即如此） |

---

## 安全与配置专项（任务3）

| 检查项 | 方法 | 结论 |
|---|---|---|
| key 泄漏面 | grep api_key 全量 + check_ai 输出审查 | ✅ key 仅进入 HTTP headers（ai_bridge.py:159,244,878）与 password 模式输入框（ui_ai.py:428）；无任何 print/log/异常拼接带 key；ai_selfcheck 刻意不打印 URL 与 key（ai_bridge.py:1297-1302） |
| example 模板边界 | 文件核对 | ✅ ai_config.example.json 为占位模板入库，真实 ai_config.json 被 .gitignore:12 拦截且未跟踪 |
| 路径逃逸 | open/Path 拼接点全量核查 | ✅ 无用户输入直拼路径；附件名经 os.path.basename（ai_bridge.py:618,660）；导出名程序生成（ui_ai.py:3875）；os.startfile 3 处（ui_troubleshoot.py:746,1207,1213）参数均为程序生成路径 |
| subprocess 注入面 | grep | ✅ app/ 无实际 subprocess 调用 |
| 公开仓红线（当前） | `git ls-files` 正则扫 db/nlb/log/key/session | ✅ 零命中 |
| 公开仓红线（历史） | `git log --all --diff-filter=A` 扫敏感文件名 | ✅ 历史从未提交过 .db/.nlb/.log/ai_config.json |
| .gitignore 覆盖 | 核对 | ✅ *.log / *.nlb / *.db / ai_config.json / sessions/ 齐全（.gitignore:6-13） |

## 一致性与规范复查（任务4）摘要

- **AGENTS.md §1 产物落位**：本次审计产物按表落位（qa→docs/qa、audit→docs/audit、截图→docs/screenshots/legacy/）；唯一规范偏差为 B22（CHANGELOG.md 在根目录）
- **AGENTS.md §2 打包纪律**：无 .stale-* 残留（glob 核查）
- **AGENTS.md §6 UI 纪律**：setStyleSheet 全 app 1 处（B19）；QSplitter 状态记忆收口完备；AI 回复全宽卡片实现符合修订版规则
- **validate_seed 契约**：偏差 B23/B24 已列出，属文档先行预期项

---

## 审计方法说明

1. **工具链**：pyflakes（系统 Python，33 条线索）+ vulture 2.16（隔离 venv，高置信度 3 条 / 中置信度 61 条）。全部线索逐条 grep/read 人工核实：**确认误报并标注**的有——sqlite `row_factory` 赋值、Qt 覆写方法（closeEvent/highlightBlock/isatty）、防 GC 引用属性（highlighter/_streaming）、TOKENS 字典键（ui_font）、ai_bridge 依赖探针导入（B15）、smoke_stage3 dir() 守卫（A14）
2. **人工审查**：db.py / ai_bridge.py 全文逐行；main.py 结构级；ui_main/ui_ai/ui_errorfix/ui_troubleshoot/ui_editor/ui_tree_editor/ui_generator 由两个探查代理按 8 项清单扫描后抽验复核
3. **实测**：validate-seed / check-ai / 三套冒烟（245 项）/ 自研 32 项探针 / 四 Tab 离屏截图；GUI 退出码均用 PIPESTATUS 取真实值
4. **证据文件**：截图 docs/screenshots/legacy/audit-20261008/*.png；探针脚本 .workbuddy/audit_probe.py（gitignore 区，未入库）

## 修复 backlog（按 P0→P1→P2 排序，工作量：小 ≤0.5 天 / 中 1-2 天 / 大 ≥3 天）

| 序 | 级别 | 事项 | 工作量 |
|---|---|---|---|
| 1 | P1-1 | 四处 db 写入口补异常反馈（对齐 delete 的 try+critical 模式） | 小 |
| 2 | P1-3 | cancel() 真正断连（worker 保存 resp 引用） | 小 |
| 3 | P1-2 | 裁决并统一导入 verified 语义（INSERT/UPDATE 两分支对齐） | 小（决策先行） |
| 4 | P2-A | dead code 批量清理（A1-A15，一次性 PR，删前逐条再核引用） | 小 |
| 5 | P2-B13/B10/B11 | quiet 实现、save_session 表达式、截断标注修正 | 小 |
| 6 | P2-B1/B2/B3/B4 | db 层异常日志 + 导入事务化（统一 log_history 内联模式） | 中 |
| 7 | P2-B9 | NetworkProbe 挂 parent + 旧线程收尾 | 小 |
| 8 | P2-B8 | 流式渲染增量优化（只重渲尾部块/文本追加） | 中 |
| 9 | P2-B18/B19 | 硬编码色收敛 token | 小 |
| 10 | P2-B20/B21 | set_state 改名 + 验证对话框抽取公共组件 | 中（重构候选池） |
| 11 | P2-B17/A3 | [去排查树]按钮行为裁决（实现或移除） | 小 |
| 12 | P2-B22 | CHANGELOG.md 位置规范裁决 | 小 |
| 13 | P2-B24 | 排查树 zte/juniper 条目补齐（种子任务，非代码） | 中 |

---

*快照归档，之后不回改。后续修复另行安排任务，与本报告分离。*
