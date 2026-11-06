# 操作单生成器（Runbook）使用与设计说明

> 本文是「操作单」功能的**唯一权威出处**：数据模型、导出口径、复用边界、二批进度。
> UI 操作常识不在此赘述（界面即文档）。上线批次：MVP 2026-10-12；二批（场景包一键生成 +
> 六段式模板）2026-11-02（Unreleased）。

## 1. 定位

命令库落地的**最后一公里**：选厂商/OS + 场景，从命令库挑条目组成一份可打印的
变更窗口操作单——命令序列（params 已填）+ 每步预期回显要点 + 回退方案（自动带出
rollback）+ 复核勾选框。命令执行者仍是外部 Xshell/SecureCRT，操作单只负责
"变更窗口里按单推进、可审计、可回退"。

## 2. 数据模型（schema 2）

操作单落盘为 JSON（**运行数据**，存放 `<exe同目录>/runbooks/`，gitignore 口径，
永不入库、永不进交付 zip——单内参数值即现场真实 IP/设备名）：

```json
{
  "schema": 2,
  "meta": {
    "title": "XX局点 VLAN 100 划分变更",
    "device_model": "S5720", "vendor": "huawei", "os_family": "vrp8",
    "window_start": "2026-10-20 00:00", "window_end": "2026-10-20 06:00",
    "operator": "…", "reviewer": "…",
    "rollback_point": "已备份 vrpcfg.zip 至 …",
    "abort_criteria": "变更后 5 分钟内 BGP 邻居未全部 Established 即回退",
    "template": "six",
    "source_pack": "new-device-onboarding",
    "created_at": "…", "updated_at": "…"
  },
  "steps": [{
    "step_no": 1,
    "entry_uuid": "<命令库条目 uuid>",
    "title_snapshot": "…", "vendor_snapshot": "…",
    "params": {"vlan_id": "100"},
    "expect": "display vlan 100 可见…",
    "note": "", "checked": false,
    "phase": "change"
  }]
}
```

**schema 1 → 2（2026-11-02，纯增补，无破坏性改动）**

| 新字段 | 取值 | 语义 |
|---|---|---|
| `meta.abort_criteria` | 文本 | 六段式第 4 段「回退条件」——触发回退的判据（文本段，不是步骤） |
| `meta.template` | `""` / `"six"` | 六段式分组开关；空 = 旧式平铺视图（模板**可选非强制**） |
| `meta.source_pack` | 包 id | 由场景包一键生成时溯源；人工建单为空 |
| `step.phase` | `""`/`pre`/`change`/`validation`/`rollback`/`post` | 步骤所处阶段（`""` = 未分组） |

**向后兼容**：v1 文件缺上述字段 → `normalize_runbook` 补空串默认值（**不产生警告**），
载入后另存即写为 schema 2；声明版本高于 2 的文件原样保留版本号并由 `validate_runbook`
明确拒绝，不做猜测式降级。反向亦安全：旧版程序读 schema 2 文件时未知字段被忽略。

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

## 2.1 场景包 → 操作单草稿（二批①，2026-11-02）

**单一事实源 = 场景包**（`scenario_packs.items`），**不另建模板库**——模板库会与场景包
形成两份需要同步的清单，是漂移源。入口在场景包 Tab 工具栏「一键生成操作单 →」，
按当前包 + 当前厂商视图生成草稿，落到操作单 Tab（**未保存态**，人工核对后另存）。

| 口径 | 规则 |
|---|---|
| 步骤范围 | 只收 `ref_type=entry`；排查树 / 报错条目不进步骤，逐项记入 `report.skipped` 并在状态栏告知（**不静默丢弃**） |
| 顺序 | 按包内 `seq` 升序（seq 缺失者排在后面、保持原相对顺序） |
| 厂商 | `by_vendor` 步骤必须先选定厂商视图：未选 → 该步记 skip（"先选定目标厂商"）；该厂商无对应条目 → 记 skip；不猜设备 |
| 参数预填 | 条目 `params.default`（含 `{{name:默认}}` 内联默认）铺进 `step.params`；**无默认值的必填项留空**，由表单现场填 |
| 回退 | 变更类**不写** rollback 字段（schema 裁决③），渲染/导出时实时取库自动附 |
| 备注 | 包内 `item.note`（步骤指导语）→ `step.note` |
| 单头 | `title` = 包标题、`vendor` = 厂商视图、`source_pack` = 包 id；`os_family` 仅当包内条目 OS 一致时带出 |
| 引用失效 | `uuid` 不在库 → `report.missing` 记账，其余步骤照常生成（不因一项失效拖垮整单） |

生成**不自动套六段式**：模板是另一种组织视图，由用户在操作单里显式开启（见 2.2）。

### 2.1.1 场景包 schema v2（T 批 2026-11-06）

包文件 `seed_data/scenario_packs.json` 与操作单 JSON 是两套版本号（包文件固定 `schema: 2`，
本次是**纯增补**，老包不带这些字段照旧可用）：

| 位置 | 字段 | 规则 |
|---|---|---|
| 包级 | `abort_criteria` | 回退条件文本（命中即执行 Rollback）；`template=six` 时**必填**，validate [E] |
| 包级 | `template` | `""` / `"six"`；非法值 validate [E]，脏值在 build 时回落平铺 |
| 条目级 | `phase` | `pre` / `change` / `validation` / `rollback` / `post`；非法值 validate [E] |
| 条目级 | `params` | 参数覆盖表（参数名 → 值），叠在条目 `params.default` 之上；非对象/空参数名 [E]，参数名未在条目声明 [W] |

- 生成时：包级两字段写进 `meta.abort_criteria` / `meta.template`，`item.phase` 写进 `step.phase`，
  `item.params` 覆盖预填值——**顺序仍是 seq，阶段只做分组**；
- **单厂商包**：条目用 `ref_id` + 单厂商视图；厂商视图里只有一家时自动选中，错选别家厂商
  build 会整包跳过（`该厂商（x）在本包内无对应条目`），不会把别家命令塞进单里；
- 四个防火墙包（`fw-edge-policy-{fortinet,paloalto,sangfor,topsec}`）即此形态：10 步、六段式、
  场景化预填 + 逐包回退条件；库内暂缺格位与「未真机」口径见包说明与
  `docs/dev-notes.md` T 批节（不塞空步骤、不承诺验证等级）。


## 2.2 六段式阶段模板（二批②，2026-11-02）

六段固定顺序：**Pre-check → Change → Validation → 回退条件 → Rollback → Post-check**。

- **回退条件=文本段**：`abort_criteria` 写在单头，导出时落在回退汇总页页首（命中即执行 Rollback）；
- **Rollback=派生段**：变更类条目的回退方案实时取库汇总成回退汇总页；只有人工把某步
  标成 `phase=rollback` 时才作为**显式回退步骤**出现在 Rollback 段——两处不重复落数据；
- **启发式预标（草稿级建议，人工可改）**：变更类 → `change`；首个变更步之前的查询类 →
  `pre`；整单无变更类 → 全部 `pre`（"割接前检查"这类只读包）；末步非变更类 → `post`；
  其余查询类 → `validation`。已有人工标注的步骤**不被覆盖**（`only_empty`）；
- **UI**：左侧列表按阶段插段头行（不可选中），`_row_map` 维持「行 → 步骤下标」映射；
  步骤编辑区「阶段」下拉可逐步改（**选阶段会自动开启分组**，否则标记在平铺视图里看不见）；
  关闭开关 = 清模板 + 清全部阶段标记，回到旧式平铺；
- **顺序诚实**：分组只组织视图，执行顺序以「第 N 步」序号为准（列表与导出页均有明示，
  因为阶段可能非连续，例如 pre / change / validation 交错时组内序号不连号）。


## 3. 复用边界（不另造轮子）

| 能力 | 复用件 | 说明 |
|---|---|---|
| 添加步骤 | `ui_panel.CommandPalette`（Ctrl+K 同款） | 三库浮层；只有命令条目可入列 |
| 参数填写 | `ui_generator.ParamFormWidget` | 必填/类型/enum/端口展开全量复用 |
| 命令渲染 | `renderer.render_entry` | 参数填入 {{占位}}，未填保留占位 |
| 复制守卫 | `ui_main.guard_skeleton_copy / guard_rollback_confirm` | parent=主窗 → 查询类不弹、变更类确认弹窗、当日抑制、视图菜单开关**全同主程序口径** |
| 必填拦截 | `ParamFormWidget.errors()` + `renderer.validate_form` | 复制前拦截并定位首个错误；导出前对全单校验（有缺值确认后放行，占位保留） |
| 导出骨架 | 模式同 `site_export.py` | 单文件 HTML、`</` 转义、token 同源校验、零外部请求 |
| 场景包 → 草稿 | `db.get_pack / get_entry` + `renderer.merge_param_specs`（参数默认值） | 包内 `by_vendor` 语义直接沿用场景包 Tab 的厂商视图口径 |

变更类步骤**缺 rollback**（旧库/手工数据才会出现，validate 已是 [E] 拦截级）：
Tab 内红字告警 + 导出 HTML 红字标出，**不阻断**——与主程序复制守卫"无 rollback
版文案"一致。

## 4. 导出 HTML 口径

- 单文件、零外部请求（无 script / 外链 / @import / url()），双击即开；
- `@page A4` + `page-break-inside:avoid`（步骤卡不跨页断开）+ `@media print`；
- 版面：单头元信息表 → 命令序列（每步：勾选框 + 参数表 + 渲染命令 + 预期回显 +
  备注）→ **回退方案汇总页**（变更类步骤的 rollback 集中成独立页，页首回填
  `meta.rollback_point`，紧随「回退条件」块）→ **复核确认页**（5 项 checklist +
  执行人/复核人签字栏）；
- **六段式分组（`meta.template=six`）**：命令序列按 Pre-check / Change / Validation /
  Rollback / Post-check 分段（空段不印，未分组步骤置顶「未分组」段），页首加
  「分组为组织视图、执行顺序以步骤序号为准」提示。「回退条件」作为独立块落在回退
  汇总页（不占命令序列段位）；平铺单（`template=""`）导出结果与 MVP 逐字节同构；
- 配色：浅色打印主题，每个 token 记录 theme.py 来源常量与派生基线值，
  导出前逐字比对，theme.py 改色即漂移拒绝（模式同 site_export）。

## 5. 文件与代码落位

| 件 | 落位 |
|---|---|
| Tab | `app/ui_runbook.py`（RunbookTab；`ui_main._build_ui` 局部导入 + `_safe_tab` 兜底） |
| 数据模型 + 导出器 | `app/runbook_export.py`（纯 stdlib + renderer/db，不依赖 Qt） |
| 操作单 JSON | `<exe同目录>/runbooks/*.json`（gitignore；dist 核查不含） |
| 生成器入口 | `ui_scenario.py` 工具栏「一键生成操作单 →」→ `RunbookTab.load_from_pack()` → `runbook_export.build_from_pack()` |
| 冒烟 | `scripts/smoke_runbook.py`（74 项：数据模型 / rollback 实时带出 / 导出口径 / 必填校验 / Tab 行为 / 保存重开 / 场景包一键生成 / 六段式 / 旧 v1 兼容） |

## 6. 二批规划与进度（2026-11-02 更新）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 模板库（常见场景预设操作单） | **已落地（换实现形态）** | 裁决：**不做独立模板库**，单一事实源 = 场景包（`ref_type=entry` 条目按 seq 预排 + 参数预填 + 变更类自动带回退）。理由：模板库会与场景包形成两份需同步的清单，是漂移源。见 §2.1 |
| 2 | 六段式阶段模板 | **已落地** | 可选非强制（`meta.template`），旧单平铺视图不受影响；见 §2.2 |
| 3 | AI 直接生成操作单 | **挂起（按需）** | ai_bridge 输入场景 → AI 从三库挑条目排序生成草稿 → 人工核对后入库（ai_imports 溯源同款）。挂起理由：AI 排序无确定性验收口径，现场改动风险高于收益；且场景包已覆盖高频开场，等真实需求再立项 |
| 4 | 导出 PDF（QPrinter） | **挂起（按需）** | 现走浏览器打印 A4（零依赖、离线可用、版式已按 `@page A4` 调校）；QPrinter 只解决"多一步点打印"，却引入 Qt 打印栈与字体在离网机的额外变量。保留为需求驱动项 |
| 5 | 执行留痕（签字回写 / 完成态写 history） | **待排** | 涉及主库写入与 SESSION_FIELDS 纪律，需单独设计（当前签字栏走 HTML 纸面） |

## 7. 验收基线

**MVP（2026-10-12 实测）**：smoke_runbook 42/42 全绿（含主库零接触断言）；全量九套件回归绿；
`--validate-seed` EXIT=0（338 + 26 + 82）；人工链路：新建 → 添加 5 步（含变更类）→ 填参 →
导出 HTML → 浏览器打印预览 A4 可用、rollback 自动在列；保存重开字段无损。

**二批（P 批 2026-11-02 实测）**：smoke_runbook **74/74** 全绿（新增 [G] 场景包一键生成 10 项、
[H] 六段式 11 项、[I] Tab 层 9 项）；全量 21 套件 rc=0；`--validate-seed` EXIT=0
（459 条 + 26 树 + 106 报错，零种子改动）；三预置场景包各一键生成实测（顺序与包一致、
参数预填、变更类回退在列）：`new-device-onboarding/huawei` 9 步、`pre-cutover-check/huawei` 10 步、
`emergency-rollback/cisco` 2 步（其余 8 项排查树/报错记账跳过）；六段式保存重开无损、v1 旧单兼容加载。

