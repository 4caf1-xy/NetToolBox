# 文档地图（docs/README.md）

> 本文件是 NetToolBox 文档体系的**唯一索引**。原则：每个主题只有一个权威出处，
> 其余文档一律链接、不复制内容。找不到答案时先查本表。
>
> 受众：所有会话与贡献者 ｜ 更新时机：新增/退役任何文档时同步维护本表。

| 文档 | 受众 | 回答什么问题 | 更新时机 |
|---|---|---|---|
| [README.md](../README.md)（根） | 所有人 | 这是什么工具、怎么跑、规模多大、免责与 License | 每次发版 |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 潜在贡献者 | 怎么参与：PR 流程、Issue 怎么提 | 贡献流程变更时 |
| [AGENTS.md](../AGENTS.md) | AI 会话 / 维护者 | 会话产物落位、打包纪律、种子数据纪律 | 约定新增或变更时 |
| [architecture.md](architecture.md) | 维护者 / 深度贡献者 | 数据流、三库职责、公开仓与伴生仓分工 | 架构变更时 |
| [seed-guide.md](seed-guide.md) | 种子作者 | 怎么写一条从零到 validate 通过的合格条目 | schema / 校验规则变更时 |
| [verify-guide.md](verify-guide.md) | 验证执行者 | 怎么跑一场验证会：环境、四档判定、回填与存档 | 验证流程变更时 |
| [打包与部署说明.md](打包与部署说明.md) | 打包 / 分发执行者 | 怎么打包 exe、U 盘部署、升级迁移、现场排障 | 打包链路变更时 |
| [dev-notes.md](dev-notes.md) | 维护者 / AI 会话 | 已知问题跟踪、历史坑与结论性沉淀 | 随发现随记（append） |
| [prompts/README.md](prompts/README.md) | 维护者 | 历次 AI 提示词批次 → 产出对照 | 每批提示词归档后 |
| [screenshots/README.md](screenshots/README.md) | 文档 / 汇报作者 | 截图索引、主题与截图脚本约定 | UI 变更后补图时 |
| [审计报告-*.md](审计报告-种子库与主库覆盖度-20260924.md) | 快照读者 | 某时点的种子库覆盖度快照 | **冻结归档，不再更新**；新审计另起新文件 |
| [CHANGELOG.md](../CHANGELOG.md)（根） | 用户 / 贡献者 | 每个版本对外可感知的变更 | 随做随记 Unreleased，发版时切割 |

## 主题 → 权威出处速查

| 主题 | 权威出处（其余只链接） |
|---|---|
| params 字段写法 / 条目 schema | `seed-guide.md` |
| 验证会流程 / 结果判定 / 晋升 | `verify-guide.md` |
| 打包与 U 盘部署 | `打包与部署说明.md` |
| 架构与双仓分工 | `architecture.md` |
| 会话产物落位 / 密钥纪律 | `AGENTS.md` |
| 版本变更记录 | `CHANGELOG.md` |
