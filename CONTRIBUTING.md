# 贡献指南（CONTRIBUTING.md）

> 受众：想给 NetToolBox 提交命令条目、报错样本或修 Bug 的贡献者 ｜ 更新时机：贡献流程变更时。
> 本文件是流程薄壳；**字段写法细节一律看 [docs/seed-guide.md](docs/seed-guide.md)，不在此展开。**

## PR 流程

1. Fork 本仓 → 从 `main` 拉特性分支（如 `seed-cisco-ntp`）；
2. 修改 `app/seed_data/` 时遵守 [AGENTS.md](AGENTS.md) 落位与纪律约定，新条目一律结构化 params；
3. 本地质检通过后提交：

   ```bash
   python app/main.py --validate-seed   # 必须 EXIT=0（CI 同款硬门槛）
   ```

4. 推分支并开 PR，标题写清范围（如 `seed: 补 openEuler 时间同步 4 条`）；
5. CI 绿 + 维护者 review 通过即合入。

推荐本地装 pre-commit 钩子（提交前自动质检）：

```bash
cp scripts/hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit   # Windows Git Bash 免 chmod
```

## Issue 三类指引

| 类型 | 标题建议 | 内容要求 |
|---|---|---|
| **报错样本** | `[err] <厂商> <报错首行>` | 报错原文、设备型号/OS 版本、当时场景；样本会进报错字典匹配测试 |
| **场景需求** | `[scene] <场景描述>` | 想覆盖的场景、涉及厂商/设备、频率与理由；会对照缺口清单排期 |
| **验证反馈** | `[verify] <条目标题/uuid>` | 真机型号、执行结果（绿/语法核对/红）、回显要点；判定标准见 [docs/verify-guide.md](docs/verify-guide.md) |

## 红线速记

- 种子规模等数字以 `--validate-seed` 实测输出为准，文档不手填旧数；
- 未真机验证的条目保持 `verified: 0`，CLI 骨架标 `exec_level: "skeleton"`；
- `ai_config.json`（真实 key）与运行时数据永不入库；
- 证据类产物（截图/QA 回显）进伴生私有仓，公开仓零真实回显。
