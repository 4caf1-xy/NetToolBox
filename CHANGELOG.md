# Changelog

本文件记录 NetToolBox 对外可感知的变更。版本号遵循 semver 口径：
新功能 / 大改 / 种子扩充 → 次版本 +1；纯修复 → 修订号 +1。

## 未发布（Unreleased）

本批主题：**参数系统结构化升级** —— params 自描述 schema + 完整命令预览 + 注释渲染。

### 新功能
- **params 结构化 schema**：参数规格新增 `type`（string/int/enum/ip/flag）、`description`（必填）、`choices`（enum 必带）、`range` 等自描述字段；`validate` 细粒度校验规则保留并与 type 推导互补（显式规则优先），旧格式（无 type）完全兼容继续放行；
- **flag 开关型参数**：不勾选 → 占位从命令中整体消失，勾选 → 渲染指定文本；生成器以勾选框呈现；
- **详情页参数说明折叠面板**：默认收起，无参数条目整页不渲染；行结构升级为「名称｜必填*｜默认值｜类型｜说明」，说明列含 description 正文 + 范围/示例/可选值灰字附注（enum 展示全集）；
- **详情页完整命令预览状态条**：缺必填参数 → 红条提示并**阻止复制全部/仅复制命令/逐条复制**（P2 落实），预览保留 `{{占位}}` 不生成残缺命令；缺选填 → 黄条提醒；
- **# 注释行渲染增强**：命令区注释行按灰斜体小字呈现，种子约定注释行以 `#` 开头独立成行，复制命令时可剔除。

### 种子数据
- 启动存量 987 个参数规格的结构化迁移（`scripts/migrate_params.py`，dry-run 预览 + 快照兜底 + 幂等可重跑），迁移期空描述带 `desc_pending` 标记仅报 WARNING，AI 分批补齐后清扫标记；
- 种子规模维持 **327 条命令 + 26 棵排查树 + 82 条报错字典**，升级后 `--validate-seed` 全量 EXIT=0。

### 工程修复
- `validate-seed` 参数校验收口为统一的 `renderer.check_entry_params`（与 AI 入库钩子同一口径）：`{{占位符}}` 引用未定义 → ERROR；定义未引用 → 升格为显式 WARNING；default 违反自身 type/choices/range → ERROR；新条目 description 为空 → ERROR；
- AI 产出入库增加校验钩子：对账不过 / 结构化字段缺失一律打回不入库；AI 产出 prompt 同步要求结构化参数标注与 `#` 注释行。

### 文档
- 新增 `docs/seed-guide.md`：面向贡献者的 params 字段写法 + 完整示例条目 + 提交自查清单；README 贡献指引同步链接。
- **文档体系全面更新**：新增 `docs/README.md`（文档地图：每份文档的受众/回答问题/更新时机，每主题唯一权威出处）、`docs/architecture.md`（数据流 + 三库职责 + 双仓分工）、`docs/verify-guide.md`（验证操作手册：环境映射/四档判定/回填流程/报错复现/晋升标准）、根目录 `CONTRIBUTING.md`（薄壳，PR 流程 + Issue 三类指引）；README 架构节与贡献指引改为链接架构/贡献文档；
- `docs/seed-guide.md` 补齐：命名/分类/厂商代码约定（从现有种子归纳）、validate-seed 规则摘要、红线专节（示例 IP 用文档专用段、default 禁真实值、description 三要素）；
- `AGENTS.md` 增补文档落位规则（新 .md 只进 docs/ 对应位置，禁根目录新建）与 params 约定生效时机注记；
- `docs/打包与部署说明.md` 收敛为纯打包+部署手册：修正 `scripts/build_exe.py` / `app/main.py` 路径，剥离模块功能演进史（以 CHANGELOG 为唯一权威出处），数字改为"以 --validate-seed 实时输出为准"；
- `docs/dev-notes.md` 已知问题清单化（排查树遗漏分支 → checkbox 跟踪，标注来源批次）；`docs/prompts/README.md` 补批次对照索引；审计报告文首加快照归档头注（不再更新，日后审计另起新文件）。

## v0.4.0（2026-09-29）

自 v0.3-batch3 以来的全部变更。本版主题：**全站界面重构 + 工程治理**，种子库规模与上一版持平。

### 界面优化
- 全站 UI 样式重构：引入统一样式表（theme.qss），命令库、排查向导、报错诊断等八个功能页视觉整体翻新，观感与交互风格保持一致；
- 版本号正式落位：标题栏显示「离网网络运维工具箱 v0.4.0 · NetToolBox」，关于对话框同步显示版本号（此前关于框内的版本信息与发布版本脱节，已修正为统一常量驱动）。

### 工程修复
- 打包入口统一为 `scripts/build_exe.py`：构建产物定向到仓库根 `build/`、`dist/`，交付物自动收拢到 `dist_package/`，不再散落；
- 废除旧版 `.stale-*` 让位机制，改为 `.trash-<时间戳>` 瞬时让位（构建成功后自动清理）；
- 仓库根目录治理：移除历史构建残渣，界面截图归位 `docs/screenshots/`（过程稿进 `legacy/`），根目录门面收敛为 README / LICENSE / 配置模板 + 标准目录结构。

### 种子数据
- 本版无新增，规模维持 **327 条命令 + 26 棵排查树 + 82 条报错字典**（`--validate-seed` 全量校验通过，样例自测 100% 命中）。

### 文档
- README 种子规模更新为实测值，主界面截图更换为 UI 重构后成品图；
- 新增本 CHANGELOG（首版回填 v0.3-batch3 以来的全部变更）。
