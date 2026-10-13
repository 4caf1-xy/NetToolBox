# 审码报告：8cbf70d..bda96ef（安全加固批五提交）

> 审查时间：2026-10-13 ｜ 审查方式：只读（逐提交 diff + 实跑核验），审查期间未改任何文件
> 范围：3c9ff0c（batch1 DPAPI）/ 8aa0b3b（batch2 发送门禁）/ 6b77b16（batch3 base_url+probe）/ ca44b34（batch4 导入/regex）/ bda96ef（docs 收口）
> 实证基础：全量 15 套冒烟实跑 rc=0（含 4 套新增）+ validate-seed EXIT=0（338/26/82）+ pattern_lint 正反例实跑 + DPAPI ctypes 边界实测。主库零接触（smoke-env 指纹断言每次通过）。

## 汇总结论表

| 提交 | 结论 | 最高级问题 |
|---|---|---|
| 3c9ff0c DPAPI | 需修 | entropy 风险未文档化（P3） |
| 8aa0b3b 门禁 | 需修 | fail-open 无提示（P2，ui_ai.py:1957） |
| 6b77b16 base_url | 存疑 | RFC1918 豁免口径待裁决 |
| ca44b34 导入/regex | 需修 | 导入路径无 lint（P1，db.py:2102） |
| bda96ef docs | 需修 | 冒烟计数 3 处不符（P3） |

修复优先级：**P1 批4 导入 lint > P2 批2 门禁提示 > 批3 口径裁决 > 其余 P3**。

---

## 1. 3c9ff0c（batch1 DPAPI）—— 结论：需修（P3 文档 + 2 轻微）

| 清单项 | 核验结果 |
|---|---|
| 加密对象/覆盖 | 字段级（api_key→api_key_enc，dpapi-v1）。ai_config 唯一真敏感字段就是 api_key（base_url/model/operator 非敏感），覆盖完整 ✓ |
| entropy | **未传**（CryptProtectData 第 4 参 None）。代码威胁模型注释只写了"介质外泄不可解"，全链（代码/dev-notes/CHANGELOG）未标注"同 Windows 用户任意进程可解密" |
| 解密失败 | `_decrypt_key` 失败返回 "" + KEY_STATUS=decrypt-failed + 设置页黄条；load_config 中明文字段被解密结果覆盖，无 fallback 读明文 ✓ |
| 作用域 | 未传 CRYPTPROTECT_LOCAL_MACHINE → CurrentUser，与"介质外泄"场景匹配 ✓ |
| 旧明文迁移 | 事务式（.new-enc→rename .bak→转正→round-trip→删/还原），状态机 9 态，smoke T1–T7 覆盖 ✓ |

需修：
- **P3 文档**：ai_bridge.py 威胁模型注释块（约 61–77 行）与 dev-notes 批 1 节补一句："未传 OptionalEntropy——同用户上下文任意进程可解密；防御面=介质外泄，不防御本机同用户恶意进程"。
- 轻微：save_config 加密失败 `except: pass`（313–314 行）静默落明文、无状态记录。
- 轻微/edge：load_config（271–276 行）文件若同时含明文 api_key 与 api_key_enc，迁移会用明文字段重新加密并覆盖原密文。

## 2. 8aa0b3b（batch2 发送门禁）—— 结论：需修（P2 一处）

| 清单项 | 核验结果 |
|---|---|
| 默认拦截 | 命中即弹模态 exec_()，复选框默认全不勾（全掩码），无"不再询问"全局开关，无 env var / debug flag 绕过 ✓ |
| 失败不得静默 | **`_run_send_gate` except 分支（ui_ai.py:1957-1958）fail-open 直接返回原文，无任何用户可见提示** |
| 检查时机 | 在 _collect_ctx 之后、session 写入/build_chat_messages/气泡/worker.start() 之前；历史轮不重扫；图片附件不扫（已列 J 项）✓ |
| 门禁日志 | 无 logging 模块使用；_add_notice 只报计数；放行记忆存 sha256 指纹——零敏感内容 ✓ |
| 其他 | _collect_ctx 实返六字段与 CTX_LABELS 完全一致，扫描范围无遗漏 ✓ |

需修：ui_ai.py:1957-1958 except 分支补 `_add_notice`（fail-open 策略可保留，但必须让用户知道本轮没有门禁保护）。

## 3. 6b77b16（batch3 base_url+probe）—— 结论：存疑（1 项待裁决 + 3 轻微）

| 清单项 | 核验结果 |
|---|---|
| scheme 白名单 | 仅 http/https；file:// 因正则要求 `://主机` 直接拒；http 仅本网放行（理由明确：Local AI/Ollama）✓ |
| SSRF 面 | **127/8、10/8、172.16/12、192.168/16 被刻意豁免**（HTTPS 强制与端点确认均不适用）；169.254/16 与云元数据不在豁免 → 需 HTTPS+首次确认。userinfo 伪装（`https://good@evil.com`）host 解析正确不绕过 ✓ |
| probe 阻塞/降级 | QThread + 2s 超时不阻塞；失败→状态栏灰灯+发送置灰+tooltip，降级明确 ✓ |
| probe 缓存 | 仅内存态、60s 重探、无持久化，陈旧窗口 ≤60s ✓ |

存疑（已裁决，见第 4 节裁决记录）：RFC1918 全豁免是既定裁决（本网零打扰），但与审查清单"拦内网地址"期望有距。残余风险：内网 http 端点会收到明文 API key+对话。

轻微：① test_connection(534)/list_models(372) 只校验 URL 合规、不校验 endpoint_approved；② endpoint_addr(499) 畸形端口回退 api.deepseek.com:443，探测对坏配置给出误导性"可达"；③ approved_endpoints 主机粒度不分端口。

## 4. ca44b34（batch4 导入/regex）—— 结论：需修（P1 一处 + 1 存疑）

| 清单项 | 核验结果 |
|---|---|
| ReDoS 护栏形态 | 拒绝（[E] 硬拦）非截断；实测 `(a+)+ / (?:\d+)* / (a\|bb+){2,} / ((a+)+) / (?P<n>a+)+` 全 [E]，`(a+){2,4} / [a+]+ / \(a\+\)+` 零误报 ✓ |
| 错误信息 | 对话框只回显 pattern 片段与计数，不回显用户敏感输入 ✓ |
| 大小/行数上限 | 20MB（import_nlb 前置）+ 200KB/1 万行（诊断截断）✓ |
| regex 编译超时 | 无（Python re 不支持超时）；compile 本身快，风险在 match 期——防御全靠入口 lint |

需修：
- **P1**：.nlb 导入路径完全没有 lint——db.import_nlb(1399)→import_errs(2102)→_normalize_err(1942) 均不检查 pattern，两个 UI 入口（ui_main.on_import_nlb:2586 / ui_editor.on_import:1085）直通。恶意/手误导入含灾难 pattern 的 err_dict 后，match_errors 每次诊断逐行 re.search 即冻结 UI。修法：import_nlb 前置预检（懒 import error_match，无循环风险），[E] 直接 ValueError 拒读并列前几条 err_id；[W] 计数提示复核。

存疑（已裁决，见第 5 节裁决记录）：lint 启发式漏 alternation-overlap 类：实测 `(a|aa)+b` 对 `'a'*26+'c'` 单行 0.039s、约 1.65^n 指数膨胀（n≈40 即百秒级）。裁决：新增 [W] 规则（组内含 `|` 且组外无界 → [W]），不升级 [E]（`(foo|bar)+` 类正常形态误报率不可接受，[W] 人工确认口径）。

另：截断提示文案（ui_errorfix.py ~186 行）仅行数超限时也说"保留前 200,000 字符"，小瑕疵。

## 5. bda96ef（docs 收口）—— 结论：需修（P3 计数三处 + 表述两处）

| 抽查项 | 结果 |
|---|---|
| 行为一致性 | 批 1–3 的 CHANGELOG 行为描述与代码逐条相符；"全量 15 套全绿"实跑复核 15/15 rc=0 ✓；dev-notes"lint 正反例 23 项"实测 23 ✓ |
| 版本号 | APP_VERSION=0.6.2 = CHANGELOG 已发布节 v0.6.2；安全加固在 Unreleased，合理 ✓ |
| J 遗留 2 条 | dev-notes:292 表述与会话记忆一致；但"提示词包 L 行"不在仓内（docs/prompts/ 仅有索引 README，无实际归档），无法逐字比对 |

需修：CHANGELOG.md:15 冒烟项数与实跑不符——keyvault 31→35、baseurl 41→33、regex 36→34（send_gate 32 正确；三个文件自本批提交后未改动，属提交时即记错）；CHANGELOG.md:14"超长输入不再可能冻结匹配"表述过度；AGENTS.md §4"pattern_lint 为入库门禁"同因不准确。

存疑："提示词包 L 行"无法在仓内核验（裁决：提示词包为会话外工件不入仓，J 遗留口径以 dev-notes 本节为基准）。

---

## 裁决记录（2026-10-13，修复批依据）

1. **F1（P1）**：导入路径接入 pattern_lint，[E] ValueError 拒读（口径同 20MB 上限）、[W] 计数提示；lint 新增 [W] 规则：组内含 `|` 且组外无界 → [W]（alternation-overlap 实测指数回溯，不升 [E] 防误报）。入库门禁补齐后恢复 AGENTS/CHANGELOG"入库门禁"表述，并精确化为"阻断灾难 pattern 经校验/编辑/导入进入库；历史库中已有 pattern 不受此保护"。
2. **F2（P2）**：发送门禁 fail-open 策略保留，但 except 分支必须 `_add_notice` 告知用户"本轮未扫描直接发送"。
3. **F3+F4**：test_connection/list_models 补 endpoint_approved（未放行走首次确认弹窗，拒绝则 key 不出机）；endpoint_addr 端口畸形不再回退 DeepSeek（返回无效标记，probe 显示"配置无效"）；RFC1918 全豁免维持既定产品口径，SSRF 面定义与残余风险在 dev-notes 留痕。
4. **F5+F6+F7**：entropy 风险补文档；save_config 加密失败置 encrypt-failed 状态；load_config 双字段并存时密文优先（密文有效→删明文不重加密；密文损坏→保留明文不迁移置 decrypt-failed，禁止用明文重加密覆盖密文）。
5. **docs**：冒烟计数一律以实跑实测为准；截断提示分行/字表述；提示词包为会话外工件不入仓，J 遗留口径以 dev-notes 为基准。

## 修复批提交对照

| 修复 | 提交 | 状态 |
|---|---|---|
| 第0步 归档 | docs(audit): archive review of 8cbf70d..bda96ef | 本文件 |
| F1 | fix(import): enforce pattern_lint gate on .nlb import + lint [W] rule for alternation-overlap | 待补哈希 |
| F2 | fix(ai-gate): notify user when sanitize gate fails open | 待补哈希 |
| F3+F4 | fix(baseurl): approval gate on test/list_models + no silent port fallback + adjudication notes | 待补哈希 |
| F5+F6+F7 | fix(dpapi): entropy risk doc + encrypt-failed status + ciphertext-wins migration | 待补哈希 |
| 第5步 | docs: align smoke counts with measured values + truncation copy + prompt-pack note | 待补哈希 |
