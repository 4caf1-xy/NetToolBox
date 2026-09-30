# AGENTS.md —— AI 会话产物落位规范

> 本文件约束所有 AI 会话（WorkBuddy / Claude / Copilot 等）在本仓库内的产物落位。
> 目标：根目录门面恒定为「README / LICENSE / requirements / 配置模板 + app/ scripts/ docs/ .github/」，不再堆积会话残渣。

## 1. 产物落位对照表

| 产物类型 | 落位 | 禁止 |
|---|---|---|
| UI/界面截图 | `docs/screenshots/`（过程稿放 `docs/screenshots/legacy/`） | 落仓库根目录 |
| 临时分析文件 | 用完即删；确有沉淀价值 → 摘要进 `docs/dev-notes.md` 后删除原件 | 落仓库根目录 |
| 实验/验证代码 | `scripts/`（可复用的转正并写清用法） | 以 `tmp*`、`test1.py` 等名字散落根目录 |
| 会话记忆/笔记 | `.workbuddy/`（已 gitignore） | 写入任何被跟踪目录 |
| 运行时数据（db/sessions/ai_cases/ui_state） | exe 同目录运行产物，一律 gitignore | 手动拷入仓库 |
| 正式文档（.md） | `docs/` 对应位置（索引见 `docs/README.md`） | **在仓库根目录新建任何 .md**（根目录门面恒定：README / LICENSE / CONTRIBUTING / requirements / 配置模板，新增主题文档一律先进 docs/ 并登记索引） |

## 2. 打包纪律

- 打包统一走 `python scripts/build_exe.py`（产物自动收拢到 `dist_package/`）。
- **禁止生成 `.stale-*` 目录**——旧版"重命名让位"机制已废除。
- 脚本现行为：构建前把根 `build/` `dist/` `NetToolBox.spec` rename 成 `.trash-<时间戳>` 让位（瞬时操作，不触发删除拦截）；**构建成功后**自动尝试彻底删除。删除被环境拦截时残留的 `.trash-*` 属正常降级，手动清掉即可。
- 中间产物恒定落在仓库根 `build/`、`dist/`（均已 gitignore），不得落回 `app/`。

## 3. 验证/回显类产物

- 验证截图、QA 回显、smoke 输出等属于**证据**：按需归档到 `docs/`（公开证据）或伴生私有仓 `nettoolbox-data` 的 `docs/verify/`（内部证据），不进公开仓 git。

## 4. 密钥与真实数据

- `ai_config.json`（含真实 key）只存在于本地，永远不入库；对外只提交 `ai_config.example.json` 占位模板。
- `backup/`（主库备份）、`dist_package/`（含运行数据）不入库，不入交付 zip 的敏感文件（如真实 key）在分发前清掉。

## 5. 种子数据纪律（schema 2026-09-30 起）

- 今后新增条目一律使用**结构化 params**（name/type/required/default/description 必填，enum 必带 choices），命令关键步骤加 `#` 注释行（独立成行）；字段写法见 `docs/seed-guide.md`，入库前必跑 `--validate-seed`（EXIT=0 硬门槛，description 为空 / 占位与 params 对账不过会被 ERROR 拦下）。
- **生效时机**：validate 对结构化 params 的强制校验随「参数升级任务」合入即生效；该任务合入前，存量旧格式条目维持兼容放行，新条目从现在起就按 seed-guide 写。
- 文档写作纪律：每主题唯一权威出处（params→seed-guide，验证→verify-guide，打包→打包与部署说明），其余文档只链接不复制；快照类文档（审计报告/prompts 归档）append-only 不回改。
