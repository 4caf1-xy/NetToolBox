# 截图目录

README 首图引用 `docs/screenshots/main.png`——已放置命令库 Tab 界面图。

## 主题统一 + AI 诊断布局重构（2026-09-29）

before / after 对比图（左右拼接，可直接用于汇报材料）：

| Tab | 对比图 | 看点 |
|---|---|---|
| 命令库 | `compare_command_lib.png` | 四 Tab 统一海军蓝深色主题（#0B1220 系 / accent #3B82F6） |
| 排查向导 | `compare_troubleshoot.png` | 步骤卡 / 结论卡 / 命令代码块全部走 theme.qss objectName |
| 报错诊断 | `compare_errorfix.png` | 卡片、状态标签（property 状态选择器）统一 |
| AI 诊断 | `compare_ai.png` | 上下文折叠条 + 空态示例问题 + Composer 一体化卡片（token 右下角） |

单图：`before/before_*.png`（重构前留档）、`after/after_*.png`（重构后）。
截图脚本：`python scripts/ui_screenshot.py <输出目录> <标签>`（offscreen，1280×800）。

## AI 回复渲染修复 + QSplitter 分栏（2026-10-01）

- `before/before_ai_card.png` / `after/after_ai_card.png`：AI 回复窄柱气泡 → 全宽内容卡片（sshd 排查样例：长命令完整可见、节标题/标签词渲染、代码块复制浮层）
- `after/ai_split_1280x800.png`：最小窗口 1280×800 不破版
- `after/ai_split_restarted.png`：分栏拖拽后重启，restoreState 恢复生效
（本轮截图由 `.workbuddy` 冒烟脚本产出，脚本已按会话纪律清理）

## 主题约定（本次重构起）

- 唯一样式真源：`app/theme.qss`（token 占位符由 `app/theme.py` 渲染；打包时随 exe 分发，缺失时 theme.py 有内置兜底副本）
- 各 UI 模块禁止内联 setStyleSheet 定样式，一律用 theme.qss 的 objectName / 属性状态（`[state="ok"]` / `[error="true"]` + `theme.set_state()`）
- 动态颜色唯一例外：厂商色块（数据驱动，VENDOR_COLORS）
