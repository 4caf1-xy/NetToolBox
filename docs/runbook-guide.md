# 操作单生成器（Runbook）使用与设计说明

> 本文是「操作单」功能的**唯一权威出处**：数据模型、导出口径、复用边界、二批规划。
> UI 操作常识不在此赘述（界面即文档）。上线批次：2026-10-12（Unreleased）。

## 1. 定位

命令库落地的**最后一公里**：选厂商/OS + 场景，从命令库挑条目组成一份可打印的
变更窗口操作单——命令序列（params 已填）+ 每步预期回显要点 + 回退方案（自动带出
rollback）+ 复核勾选框。命令执行者仍是外部 Xshell/SecureCRT，操作单只负责
"变更窗口里按单推进、可审计、可回退"。

## 2. 数据模型（schema 1）

操作单落盘为 JSON（**运行数据**，存放 `<exe同目录>/runbooks/`，gitignore 口径，
永不入库、永不进交付 zip——单内参数值即现场真实 IP/设备名）：

```json
{
  "schema": 1,
  "meta": {
    "title": "XX局点 VLAN 100 划分变更",
    "device_model": "S5720", "vendor": "huawei", "os_family": "vrp8",
    "window_start": "2026-10-20 00:00", "window_end": "2026-10-20 06:00",
    "operator": "…", "reviewer": "…",
    "rollback_point": "已备份 vrpcfg.zip 至 …",
    "created_at": "…", "updated_at": "…"
  },
  "steps": [{
    "step_no": 1,
    "entry_uuid": "<命令库条目 uuid>",
    "title_snapshot": "…", "vendor_snapshot": "…",
    "params": {"vlan_id": "100"},
    "expect": "display vlan 100 可见…",
    "note": "", "checked": false
  }]
}
```

三条核心裁决（2026-10-12 用户确认）：

| 裁决 | 内容 | 落点 |
|---|---|---|
| ① | 导出 HTML 用**浅色打印主题**，色值从 theme.py 派生 + 同源校验（漂移即拒导出） | `runbook_export.PRINT_TOKENS` / `verify_print_tokens_against_theme()` |
| ② | Tab 位置：报错诊断之后、AI 诊断之前 | `ui_main` addTab 顺序 |
| ③ | **rollback 不落盘**：按 uuid 实时取库（库为权威，防快照漂移）；变更类实时判定 | `runbook_export.resolve_steps()` |
| ④ | 存放 `runbooks/` + gitignore + dist 出库核查不含 | `.gitignore` / 打包与部署说明 |

不落盘的还有**变更类判定**：始终调用 `renderer.classify_entry()`（唯一执法点，
QUERY_WHITELIST / CHANGE_FORCE_PREFIXES），操作单侧不另写判定表。

uuid 失效（条目被删/换库）：步骤标「条目失效」，`title_snapshot` 兜底可读，
导出 HTML 内显式警告——不做静默降级。

## 3. 复用边界（不另造轮子）

| 能力 | 复用件 | 说明 |
|---|---|---|
| 添加步骤 | `ui_panel.CommandPalette`（Ctrl+K 同款） | 三库浮层；只有命令条目可入列 |
| 参数填写 | `ui_generator.ParamFormWidget` | 必填/类型/enum/端口展开全量复用 |
| 命令渲染 | `renderer.render_entry` | 参数填入 {{占位}}，未填保留占位 |
| 复制守卫 | `ui_main.guard_skeleton_copy / guard_rollback_confirm` | parent=主窗 → 查询类不弹、变更类确认弹窗、当日抑制、视图菜单开关**全同主程序口径** |
| 必填拦截 | `ParamFormWidget.errors()` + `renderer.validate_form` | 复制前拦截并定位首个错误；导出前对全单校验（有缺值确认后放行，占位保留） |
| 导出骨架 | 模式同 `site_export.py` | 单文件 HTML、`</` 转义、token 同源校验、零外部请求 |

变更类步骤**缺 rollback**（旧库/手工数据才会出现，validate 已是 [E] 拦截级）：
Tab 内红字告警 + 导出 HTML 红字标出，**不阻断**——与主程序复制守卫"无 rollback
版文案"一致。

## 4. 导出 HTML 口径

- 单文件、零外部请求（无 script / 外链 / @import / url()），双击即开；
- `@page A4` + `page-break-inside:avoid`（步骤卡不跨页断开）+ `@media print`；
- 版面：单头元信息表 → 命令序列（每步：勾选框 + 参数表 + 渲染命令 + 预期回显 +
  备注）→ **回退方案汇总页**（变更类步骤的 rollback 集中成独立页，页首回填
  `meta.rollback_point`）→ **复核确认页**（5 项 checklist + 执行人/复核人签字栏）；
- 配色：浅色打印主题，每个 token 记录 theme.py 来源常量与派生基线值，
  导出前逐字比对，theme.py 改色即漂移拒绝（模式同 site_export）。

## 5. 文件与代码落位

| 件 | 落位 |
|---|---|
| Tab | `app/ui_runbook.py`（RunbookTab；`ui_main._build_ui` 局部导入 + `_safe_tab` 兜底） |
| 数据模型 + 导出器 | `app/runbook_export.py`（纯 stdlib + renderer/db，不依赖 Qt） |
| 操作单 JSON | `<exe同目录>/runbooks/*.json`（gitignore；dist 核查不含） |
| 冒烟 | `scripts/smoke_runbook.py`（42 项：数据模型 / rollback 实时带出 / 导出口径 / 必填校验 / Tab 行为 / 保存重开） |

## 6. 二批规划（MVP 未含）

1. **模板库**：常见场景预设操作单（新开局 / 版本升级 / 回退演练），种子 JSON 分发；
2. **AI 直接生成操作单**：ai_bridge 输入场景 → AI 从三库挑条目排序生成草稿 →
   人工核对后入库（ai_imports 溯源同款）；
3. **导出 PDF**：Qt QPrinter 原生导出（离线），替代浏览器打印路径；
4. 执行留痕：复核签字回写、操作单完成态写 history 表。

## 7. 验收基线（2026-10-12 实测）

- smoke_runbook 42/42 全绿（含主库零接触断言）；全量九套件回归绿；
- `python app/main.py --validate-seed` EXIT=0（338 条 + 26 树 + 82 报错）；
- 人工链路：新建 → 添加 5 步（含变更类）→ 填参 → 导出 HTML → 浏览器打印预览
  A4 可用、rollback 自动在列；保存重开字段无损。
