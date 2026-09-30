# 架构与数据流（architecture.md）

> 受众：维护者与深度贡献者 ｜ 更新时机：数据流、存储结构或双仓分工变更时。
> 本文只讲结构与分工；条目怎么写看 [seed-guide.md](seed-guide.md)，验证怎么做看 [verify-guide.md](verify-guide.md)。

## 数据流

```
seed_data/*.json ──validate-seed(EXIT=0 硬门槛)──> command_lib.db（主库）
       ^                                                |
       |                                                v
       |                                    AI 导入管线（结构化校验/占位重映射）
       |                                                |
  种子晋升 <── 真机验证（verify-guide 四档判定）<────────┘
       |                                                |
       └── 证据归档 ──> 伴生仓 nettoolbox-data/docs/verify/（私有）
```

- **种子是唯一事实源**：改种子 → `python app/main.py --validate-seed` 全量绿 → 才允许进主库/合入。
- **主库可运行时演化**：AI 导入、现场手工新增、verified 回填都发生在 exe 同目录的
  `command_lib.db`；这些运行时数据**不回写种子**，只有通过验证晋升筛选的结论才回流种子。
- **闭环**：沉淀（种子）→ 分发（exe/db）→ 现场验证 → 晋升回种子 + 证据进伴生仓。

## 三库职责

**命令库（entries）**：按「厂商 → OS → 场景」组织的命令条目，带 `{{参数}}` 结构化 params、
`#` 注释行、`exec_level`（verified-cli/skeleton/web-only）与 verified 四元组
（verified/verified_by/verified_model/verified_date）。它回答"这条命令怎么写、参数怎么填、
可不可信"。

**排查树（trouble_trees）**：按故障场景组织的分步向导，步骤命令通过 `cmd_ref` 实时渲染自
命令库条目（无硬编码命令文本），按 vendor_hint 分组共享（network/linux/firewall 三组，
见 `db.py → TREE_HINT_GROUPS`）。它回答"这个现象该按什么顺序排查"。

**报错字典（err_dict）**：设备报错原文的正则匹配规则 + 人话原因 + 解决步骤，可引用命令库
条目生成修正命令；零匹配报错进 err_unresolved 收件箱，人工补充后转正闭环。它回答
"这个报错是什么意思、怎么处理"。

## 公开仓与伴生仓分工

| | 公开仓 nettoolbox | 伴生仓 nettoolbox-data（私有） |
|---|---|---|
| 内容 | 代码 + 种子 + 文档 + CI | 主库 db（真实运行数据）+ 验证证据 docs/verify/ |
| 写入纪律 | 正常 PR 流程 | **单写者**：db 只在主力机写；每场验证会后提交并打 tag `verify-YYYYMMDD-场景` |
| 数据红线 | 零真实回显、零密钥（ai_config.json 永不入库） | 内部资料，不外发 |

公开仓 CI 只跑 `validate-seed`；伴生仓承载一切"不宜开源的真实数据"。两仓通过
种子晋升流程衔接：验证结论（不含现场敏感信息）回公开仓种子，原始证据留伴生仓。
