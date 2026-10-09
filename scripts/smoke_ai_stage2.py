# -*- coding: utf-8 -*-
"""阶段二第 1 轮冒烟：数据层（ai_imports/三去向 API）+ 提取层（块分类/合并模式）"""
import os
import sys
import tempfile
import time

import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 断言
import db as dbmod
import ai_bridge
import ui_ai

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ((" —— " + detail) if (detail and not cond) else ""))


print("[1] ai_imports 表自动创建（旧库补表）")
tmpdir = tempfile.mkdtemp(prefix="ntb_stage2_")
old_db_path = os.path.join(tmpdir, "old.db")
# 先手工建一个"v1 旧库"：entries 全列（无 platform/duration）、history 无 table_name，
# 再打开验证 _migrate 自动升级 + ai_imports 自动建表
import sqlite3
raw = sqlite3.connect(old_db_path)
raw.execute("""
CREATE TABLE entries (
    uuid TEXT PRIMARY KEY, device_type TEXT, vendor TEXT, os_family TEXT,
    models TEXT, category TEXT, title TEXT NOT NULL, description TEXT,
    commands TEXT, params TEXT, notes TEXT, verified INTEGER DEFAULT 0,
    verified_by TEXT, verified_model TEXT, verified_date TEXT,
    favorite INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT
)""")
raw.execute("""
CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entry_uuid TEXT, action TEXT,
    old_value TEXT, new_value TEXT, operator TEXT, ts TEXT
)""")
raw.commit()
raw.close()
d = dbmod.Database(old_db_path)
tables = {t[0] for t in d.conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
check("旧库打开后 ai_imports 自动建表", "ai_imports" in tables)

print("[2] 溯源 CRUD")
d._insert_ai_import("sess1", 2, 0, "entry", "uuid-0", operator="tester", model="m1")
d.conn.commit()
rec = d.get_ai_import("sess1", 2, 0)
check("get_ai_import 按块命中", bool(rec) and rec["target_uuid"] == "uuid-0")
check("get_ai_import 未入库块返回 None", d.get_ai_import("sess1", 2, 1) is None)
d._insert_ai_import("sess1", 3, -1, "tree", "tree-9")
d._insert_ai_import("sess1", 3, -1, "err", "err-7")
d.conn.commit()
check("ai_import_stats 按去向分列", d.ai_import_stats("sess1") == {"entry": 1, "err": 1, "tree": 1})
check("list_ai_imports 会话维度", len(d.list_ai_imports("sess1")) == 3)

print("[3] 命令库去向：add_entries_from_ai（单事务/回滚/溯源）")
items = [
    {"title": "DNS 排查（华为 VRP8）", "platform": "network", "vendor": "huawei",
     "os_family": "vrp8", "commands": "display dns server",
     "_block_index": 0, "notes": "注：先看解析配置"},
    {"title": "Linux 抓包（CentOS 7）", "platform": "linux", "vendor": "centos",
     "os_family": "centos7", "commands": "tcpdump -i any port 53",
     "_block_index": 1},
]
meta = {"session_id": "sess1", "message_index": 2, "operator": "tester", "model": "m1"}
uuids = d.add_entries_from_ai(items, meta)
check("两条目入库成功", len(uuids) == 2)
e0 = d.get_entry(uuids[0])
check("verified 强制 0（未验证默认态）", int(e0["verified"]) == 0)
check("vendor 归一化为 slug", e0["vendor"] == "huawei")
check("溯源记录写齐（每条一个 block_index）",
      d.get_ai_import("sess1", 2, 0, "entry")["target_uuid"] == uuids[0]
      and d.get_ai_import("sess1", 2, 1, "entry")["target_uuid"] == uuids[1])
h = d.conn.execute(
    "SELECT * FROM history WHERE table_name='entries' AND record_id=?",
    (uuids[0],)).fetchone()
check("history 留痕（create）", bool(h))

# 回滚：第 2 条带非法类型（params 传 dict 无法序列化）→ 整体回滚
bad_items = [
    {"title": "好条目", "commands": "display version", "vendor": "huawei"},
    {"title": {"bad": "dict"}, "commands": "x"},
]
n_before = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
imports_before = len(d.list_ai_imports("sess1"))
try:
    d.add_entries_from_ai(bad_items, meta)
    rolled = False
except RuntimeError as exc:
    rolled = "第 2 条" in str(exc)
n_after = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
check("单事务失败整体回滚 + 带序号错误", rolled and n_after == n_before,
      str(n_after))
check("回滚不留溯源脏数据", len(d.list_ai_imports("sess1")) == imports_before)

print("[4] 命令查重（归一化）")
dup = d.find_entry_by_commands("! 注释\n\ndisplay  dns   server\n", vendor="huawei")
check("去空白去注释后命中既有条目", bool(dup) and dup["uuid"] == uuids[0])
check("不同厂商不串号", d.find_entry_by_commands("display dns server", vendor="cisco") is None)

print("[5] 报错库去向：add_err_dict_item + 查重")
err_id = d.add_err_dict_item({
    "raw_text": "Error: %Unrecognized command found at '^' position.",
    "cause": "命令拼写错误或该版本不支持该命令",
    "fix_commands": "display version",
    "vendor": "huawei", "os_family": "vrp8",
    "block_index": 2,
}, meta)
check("add_err_dict_item 返回 err_id", bool(err_id))
err = d.get_err(err_id)
check("verified=0 且 pattern 为词转义容错正则", int(err["verified"]) == 0
      and "\\s*" in err["pattern"] and "Error" in err["pattern"])
hit, where = d.find_err_by_text("Error:  %Unrecognized   command found at '^' position.")
check("报错原文归一化查重命中", bool(hit) and hit["err_id"] == err_id and where == "报错原文")
check("溯源记录写入", d.get_ai_import("sess1", 2, 2, "err")["target_uuid"] == err_id)
try:
    d.add_err_dict_item({"raw_text": "  "}, meta)
    empty_rejected = False
except ValueError:
    empty_rejected = True
check("报错原文为空 → 拒绝提交", empty_rejected)
# 报错诊断可命中：用 error_match 对该报错跑一遍
import error_match
match = error_match.match_errors("Error: %Unrecognized command found at '^' position.",
                                 d.all_errs(vendor="huawei"))
check("报错诊断 Tab 可命中新入库报错", bool(match), str(match)[:60])

print("[6] 排查树去向：create_trouble_tree / append_steps")
# 树步骤命令先落草稿条目（cmd_ref 引用，与向导渲染兼容）
step_entry = d.add_entry({"title": "树步骤命令（huawei）", "vendor": "huawei",
                          "os_family": "vrp8", "platform": "network",
                          "commands": "display interface brief"}, operator="tester")
tree_id = d.create_trouble_tree({
    "symptom": "端口频繁震荡（华为 VRP8）",
    "vendor_hint": ["huawei"],
    "category": "连通性",
    "steps": [
        {"id": "s1", "title": "查端口状态",
         "cmd_ref": {"uuid": step_entry},
         "observe": "端口是否 up？",
         "branches": [{"when": "符合预期", "goto": "leaf"},
                      {"when": "不符合预期", "goto": "leaf"}]},
        {"id": "leaf", "title": "结论：链路或对端问题",
         "leafs": {"conclusion": "链路/对端问题", "actions": []}},
    ],
}, meta)
check("create_trouble_tree 返回 tree_id 且整树未验证",
      bool(tree_id) and int(d.get_tree(tree_id)["verified"]) == 0)
check("树溯源写入", d.get_ai_import("sess1", 2, -1, "tree")["target_uuid"] == tree_id)

# 追加：末尾追加两步（占位 id + goto 重映射），原步骤不受影响
new_ids = d.append_steps(tree_id, [
    {"id": "__n1", "title": "查光功率",
     "observe": "光功率是否正常？",
     "branches": [{"when": "符合预期", "goto": "leaf"}]},
    {"id": "__n2", "title": "查日志",
     "observe": "有无 CRC/震荡记录？",
     "branches": [{"when": "符合预期", "goto": "leaf"}]},
], operator="tester")
tree = d.get_tree(tree_id)
ids = [s["id"] for s in tree["steps"]]
check("追加后步骤数 = 4 且 id 顺序正确", len(ids) == 4 and ids[-2:] == new_ids,
      str(ids))
check("占位 id 已重映射为真实 id", all(i.startswith("s") for i in new_ids))
s1 = next(s for s in tree["steps"] if s["id"] == "s1")
check("原步骤 s1 不受影响", s1["title"] == "查端口状态")
# 追加步骤的 branches.goto 引用既有 leaf → 保持原样；引用占位 → 重映射
n2 = next(s for s in tree["steps"] if s["id"] == new_ids[1])
check("追加步骤分支 goto 指向既有叶子", n2["branches"][0]["goto"] == "leaf")

# 追加到某步之后 + 再追加自动避开已有 id
new_ids2 = d.append_steps(tree_id, [{"id": "__n1", "title": "中间插入"}],
                          after_step_id="s1", operator="tester")
tree2 = d.get_tree(tree_id)
check("after_step_id 中间插入", tree2["steps"][1]["title"] == "中间插入"
      and tree2["steps"][1]["id"] == new_ids2[0])
try:
    d.append_steps(tree_id, [{"id": "__x"}], after_step_id="no-such")
    bad_pos = False
except RuntimeError as exc:
    bad_pos = "插入位置" in str(exc)
check("插入位置不存在 → 明确报错", bad_pos)

# 与排查向导读写兼容：模拟向导解析（cmd_ref uuid 命中 + steps 可加载）
entry, reason = d.resolve_cmd_ref_ex({"uuid": step_entry}, vendor="huawei")
check("向导 cmd_ref 渲染兼容（uuid 命中）", entry is not None and reason == "")

print("[7] 提取层：块分类")
cmd_block = "display current-configuration | include dns\ndisplay dns server"
v_cmd = ai_bridge.classify_code_block(cmd_block)
check("纯命令块 → command", v_cmd["kind"] == "command" and not v_cmd["needs_review"])
tree_block = "流程：\n├── 查配置\n│   └── 正常\n└── 查端口\n    └── 异常"
v_tree = ai_bridge.classify_code_block(tree_block)
check("树形字符块 → 非命令", v_tree["kind"] == "non_command" and not v_tree["needs_review"])
v_comment = ai_bridge.classify_code_block("# 这只是说明\n! 另一行说明")
check("全注释块 → 非命令", v_comment["kind"] == "non_command")
v_mixed = ai_bridge.classify_code_block("display version\n上面是设备型号说明文字很长的句子")
check("混合块 → command 但 needs_review", v_mixed["kind"] == "command" and v_mixed["needs_review"])
v_noun = ai_bridge.classify_code_block("这是一段没有动词的说明文字而已")
check("无动词命中 → 非命令 needs_review", v_noun["kind"] == "non_command" and v_noun["needs_review"])
v_linux = ai_bridge.classify_code_block("[root@host ~]# systemctl status sshd\nsystemctl enable sshd")
check("Linux 提示符行正确识别", v_linux["kind"] == "command" and not v_linux["needs_review"])
v_pipe = ai_bridge.classify_code_block("some_status > /tmp/out.log")
check("重定向行命中命令", v_pipe["kind"] == "command")
# extract_ai_blocks 编号与 extract_code_blocks 一致
resp = "前文\n```bash\ndisplay version\n```\n中文\n```\n├── a\n└── b\n```"
blks = ai_bridge.extract_ai_blocks(resp)
check("extract_ai_blocks 分类标注", len(blks) == 2 and blks[0]["kind"] == "command"
      and blks[1]["kind"] == "non_command" and blks[0]["block_index"] == 0)

print("[8] 提取层：合并模式与标题模板")
blocks = [
    {"block_index": 0, "kind": "command", "needs_review": False,
     "code": "display ip interface brief", "desc": "查接口状态"},
    {"block_index": 1, "kind": "command", "needs_review": False,
     "code": "display trap buffer", "desc": "查告警"},
    {"block_index": 2, "kind": "non_command", "needs_review": False,
     "code": "├── 原因树", "desc": "原因分析"},
]
merged, notes = ai_bridge.merge_code_blocks(blocks, "huawei")
check("合并单条目：注释分隔行 + 顺序拼接",
      merged.splitlines()[0] == "!―― 步骤1：查接口状态 ――"
      and "display trap buffer" in merged and len(merged.split("!―― 步骤")) == 3)
check("华为注释符为 !", merged.startswith("!"))
merged_linux, _ = ai_bridge.merge_code_blocks(blocks[:1] + blocks[2:], "centos7")
check("Linux 注释符为 #", merged_linux.startswith("#"))
check("非命令块进 notes（不入 commands）", "原因树" in notes and "原因树" not in merged)
title = ai_bridge.build_entry_title("端口震荡", "华为 VRP8")
check("合并标题模板", title == "端口震荡（华为 VRP8）排查")
check("逐块标题模板", ai_bridge.build_block_title("端口震荡", "查告警") == "端口震荡 · 查告警")
check("缺现象优雅降级", ai_bridge.build_entry_title("", "") == "故障排查排查"
      and ai_bridge.build_block_title("", "") == "命令块")

# ---------------------------------------------------------------------------
# [9] 第 2 轮 UI：三去向路由对话框（命令库/报错库去向）+ 幂等 + 预填启发式
# ---------------------------------------------------------------------------
print("[9] 三去向路由对话框（离屏 UI）")
import PyQt5.QtWidgets as qw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

# 消息框与留痕全部 mock：不入真实 drafts/、不弹真窗
boxes = {"info": [], "question": []}
qw.QMessageBox.information = staticmethod(
    lambda *a, **kw: boxes["info"].append(a[1] if len(a) > 1 else ""))
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.Yes)
_orig_write_nlb = ui_ai.DraftFromAiDialog._write_draft_nlb
ui_ai.DraftFromAiDialog._write_draft_nlb = staticmethod(lambda entries: "（测试跳过留痕）")
# import_target_last 持久化隔离到临时文件
_orig_cfg_path = ai_bridge.config_path
ai_bridge.config_path = lambda: os.path.join(tmpdir, "ai_config_test.json")

resp2 = ("先查接口：\n```bash\ndisplay interface brief\n```\n"
         "再查告警：\n```\ndisplay trap buffer\n```\n"
         "日志显示 %LINK-3-UPDOWN: Interface GigabitEthernet0/0/1 changed state to down\n\n"
         "## 原因\n对端端口协商异常导致链路震荡。\n"
         "判断依据：\n```\n├── 光功率异常\n└── CRC 增长\n```\n")
echo2 = ("Sep 23 16:00 %LINK-3-UPDOWN: Interface GigabitEthernet0/0/1 changed state to down\n"
         "display interface brief")
ctx2 = {"vendor": "华为", "os": "VRP8", "model_name": "S5720",
        "symptom": "端口频繁震荡", "echo": echo2, "steps": ""}

# ---- 预填启发式（提取层） ----
check("AI 引用的报错行被识别（> 其它引用行）",
      "%LINK-3-UPDOWN" in ai_bridge.find_error_lines_in_echo(echo2, resp2)[0])
check("原因段落启发式提取", "对端端口协商异常" in ai_bridge.extract_cause_section(resp2))
check("无标题 → 原因提取返回空", ai_bridge.extract_cause_section("没有标题的回复") == "")

tab3 = ui_ai.AiTab(db=d)
tab3.show()
app.processEvents()

# ---- 命令库去向：合并单条目提交 ----
n_entries_before = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
dlg = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                           session_id="sess-ui", message_index=4, target="entry")
dlg.show()
app.processEvents()
check("对话框默认落在命令库去向", dlg._target == "entry" and dlg.stack.currentIndex() == 0)
check("块解析：2 命令块 + 1 非命令块", len(dlg.blocks) == 3
      and sum(1 for b in dlg.blocks if b["kind"] == "command") == 2)
rows_cmd = dlg._checked_command_rows()
check("合并模式默认勾选命令块、非命令块置灰",
      len(rows_cmd) == 2
      and all(not r["chk"].isChecked() for r in dlg.entry_rows if r["kind"] == "non_command")
      and all(not r["chk"].isEnabled() for r in dlg.entry_rows if r["kind"] == "non_command"))
check("合并预览含注释分隔行（华为→!）", "!―― 步骤1：" in dlg.preview_commands.toPlainText())
check("notes 预填含非命令块内容（默认勾选追加）",
      "├──" in dlg.ed_notes.text())
check("标题模板预填", dlg.ed_title.text() == "端口频繁震荡（华为 VRP8）排查")
dlg.on_confirm()
app.processEvents()
n_entries_after = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
check("合并提交：库内新增 1 条", n_entries_after == n_entries_before + 1)
imp0 = d.get_ai_import("sess-ui", 4, 0, "entry")
imp1 = d.get_ai_import("sess-ui", 4, 1, "entry")
check("合并条目跨块溯源：块0/块1 指向同一 uuid",
      bool(imp0 and imp1) and imp0["target_uuid"] == imp1["target_uuid"])
new_e = d.get_entry(imp0["target_uuid"])
check("合并条目：verified=0 / vendor slug / 命令与 notes 完整",
      int(new_e["verified"]) == 0 and new_e["vendor"] == "huawei"
      and "display trap buffer" in new_e["commands"]
      and "├──" in new_e["notes"] and "未经真机验证" in new_e["notes"])

# ---- 幂等：重开对话框 → 已入库块默认不勾选 ----
dlg2 = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                            session_id="sess-ui", message_index=4, target="entry")
dlg2.show()
app.processEvents()
cmd_rows2 = [r for r in dlg2.entry_rows if r["kind"] == "command"]
check("已入库块标注并默认不勾选",
      all(("已入库" in r["chk"].text()) and (not r["chk"].isChecked())
          for r in cmd_rows2))

# ---- 逐块独立模式（高级）----
dlg3 = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                            session_id="sess-ui2", message_index=0, target="entry")
dlg3.show()
_t0 = time.time()
while dlg3._dup_state.get("entry") != "done" and time.time() - _t0 < 4:
    app.processEvents(); time.sleep(0.02)
dlg3.rb_single.setChecked(True)
app.processEvents()
# 查重增强：同内容已入过库（合并条目）→ 三级查重 high 默认跳过；
# 此用例显式选择「仍入库」模拟"不同场景的合理重复"
for _c in dlg3.dup_candidates["entry"]:
    dlg3._dup_decisions["entry"][_c["uuid"]] = "keep"
dlg3.on_confirm()
app.processEvents()
imp20 = d.get_ai_import("sess-ui2", 0, 0, "entry")
imp21 = d.get_ai_import("sess-ui2", 0, 1, "entry")
check("逐块独立：两块各自成条目（uuid 不同）",
      imp20 and imp21 and imp20["target_uuid"] != imp21["target_uuid"])
check("逐块仍入库：溯源带 dup_of_uuid（合理重复可清理）",
      bool(imp20["dup_of_uuid"]) and bool(imp21["dup_of_uuid"]))
e_single = d.get_entry(imp20["target_uuid"])
check("逐块标题 = 现象 · 块标题", e_single["title"].startswith("端口频繁震荡 · "))

# ---- 报错库去向：三字段预填 + 提交 + 报错诊断命中 ----
dlg4 = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                            session_id="sess-ui3", message_index=7, target="err")
dlg4.show()
app.processEvents()
check("报错库页：原文预填 = AI 引用的报错行",
      "%LINK-3-UPDOWN" in dlg4.ed_raw.toPlainText())
check("报错库页：原因预填", "对端端口协商异常" in dlg4.ed_cause.toPlainText())
check("报错库页：修正命令预填（合并命令块）",
      "display interface brief" in dlg4.ed_fix.toPlainText()
      and "!―― 步骤" in dlg4.ed_fix.toPlainText())
n_err_before = d.count_errs()
dlg4.on_confirm()
app.processEvents()
check("报错库提交成功", d.count_errs() == n_err_before + 1)
check("报错诊断可命中新映射",
      bool(error_match.match_errors(
          "Sep 23 17:00 %%LINK-3-UPDOWN: Interface GigabitEthernet0/0/1 changed state to down"
          .replace("%%", "%"), d.all_errs(vendor="huawei"))))

# ---- 报错库幂等提示 + 去重命中仍入库路径 ----
n_err_before2 = d.count_errs()
dlg5 = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                            session_id="sess-ui3", message_index=7, target="err")
dlg5.show()
_t0 = time.time()
while dlg5._dup_state.get("err") != "done" and time.time() - _t0 < 4:
    app.processEvents(); time.sleep(0.02)
app.processEvents()
check("重开报错库页提示已入库过", "已入库" in dlg5.lbl_hint.text())
# 查重增强：仍入库改为显式决策（不再依赖 question mock）
for _c in dlg5.dup_candidates["err"]:
    dlg5._dup_decisions["err"][_c["err_id"]] = "keep"
dlg5.on_confirm()
app.processEvents()
check("去重命中后「仍入库」生成第二条映射", d.count_errs() == n_err_before2 + 1)

# ---- 记住上次选择 + 按钮更名 ----
check("记住上次去向选择", ai_bridge.load_config().get("import_target_last") == "err")
dlg7 = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                            session_id="sess-ui4", message_index=0)   # 不显式指定去向
dlg7.show()
app.processEvents()
dlg7.reject()
check("通用入口按记忆值落去向（err）", dlg7._target == "err")
check("工具条按钮更名为「产出入库」", tab3.btn_to_draft.text() == "产出入库")
# 气泡菜单三去向可用性（排查树置灰）
tab3._response_text = resp2
handle_ctx = {"text": resp2, "session_id": "sess-ui", "message_index": 4}
tab3._last_ai_handle = handle_ctx
# 直接路由打开（不 exec）：验证报错库去向可打开且定位正确
dlg6 = ui_ai.AiImportDialog(tab3, d, source_text=resp2, ctx=ctx2,
                            session_id="sess-ui", message_index=4, target="err")
dlg6.show()
app.processEvents()
dlg6.reject()
check("排查树去向按钮可用（第 3 轮已开放）", dlg._target_buttons["tree"].isEnabled())
check("气泡句柄带会话溯源定位", handle_ctx["session_id"] == "sess-ui" and handle_ctx["message_index"] == 4)

# 还原 mock
qw.QMessageBox.information = staticmethod(lambda *a, **kw: None)
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.No)
ui_ai.DraftFromAiDialog._write_draft_nlb = _orig_write_nlb
ai_bridge.config_path = _orig_cfg_path

# ---------------------------------------------------------------------------
# [10] 第 3 轮：排查树去向 UI + 溯源展示 + 会话定位
# ---------------------------------------------------------------------------
print("[10] 第 3 轮：排查树去向 + 溯源展示")
boxes["info"] = []
boxes["question"] = []
qw.QMessageBox.information = staticmethod(
    lambda *a, **kw: boxes["info"].append(a[1] if len(a) > 1 else ""))
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.Yes)
ai_bridge.config_path = lambda: os.path.join(tmpdir, "ai_config_test.json")

# ---- 步骤提取（提取层） ----
resp_t = ("步骤 1：查接口状态\n```bash\ndisplay interface brief\n```\n观察：端口是否 up？\n"
          "步骤 2：查光功率\n```bash\ndisplay transceiver-information\n```\n"
          "## 原因\n光模块老化导致链路震荡。")
steps_t, markers_t, concl_t = ai_bridge.extract_steps_from_response(resp_t)
check("步骤提取：识别「步骤 N」结构（标题/命令/观察）",
      markers_t and len(steps_t) == 2
      and steps_t[0]["observe"] == "端口是否 up？"
      and "display interface brief" in steps_t[0]["commands"]
      and steps_t[1]["title"] == "查光功率")
check("叶子结论预填提取", "光模块老化" in concl_t)
steps_f, markers_f, _c = ai_bridge.extract_steps_from_response("看这个：\n```bash\ndisplay version\n```")
check("无步骤结构 → 命令块逐个建步", (not markers_f) and len(steps_f) == 1
      and bool(steps_f[0]["title"]))

ctx_t = dict(ctx2)
n_trees_before = d.count_trees()
n_ent_before_tree = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]

# ---- 新建树：线性骨架 + 步骤命令落条目 + 溯源 ----
dlg_t = ui_ai.AiImportDialog(tab3, d, source_text=resp_t, ctx=ctx_t,
                             session_id="sess-t1", message_index=2, target="tree")
dlg_t.show()
app.processEvents()
check("排查树页：去向与步骤行就绪", dlg_t._target == "tree" and len(dlg_t.tree_rows) == 2)
check("叶子结论预填进编辑区", "光模块老化" in dlg_t.ed_tree_conclusion.toPlainText())
dlg_t.on_confirm()
app.processEvents()
check("新建树入库", d.count_trees() == n_trees_before + 1)
tree_rec = d.get_ai_import("sess-t1", 2, -1, "tree")
tree_obj = d.get_tree(tree_rec["target_uuid"])
check("线性骨架：2 步 + 1 叶子，整树未验证",
      len(tree_obj["steps"]) == 3 and tree_obj["steps"][-1].get("leafs")
      and int(tree_obj["verified"]) == 0)
s1_node = tree_obj["steps"][0]
check("步骤分支：正常→下一步 / 异常→叶子",
      s1_node["branches"][0]["goto"] == "s2" and s1_node["branches"][1]["goto"] == "s3")
check("末步正常分支指向同一叶子", tree_obj["steps"][1]["branches"][0]["goto"] == "s3")
entry_ref, reason_ref = d.resolve_cmd_ref_ex(s1_node.get("cmd_ref"), vendor="huawei")
check("步骤命令落条目且 cmd_ref 可解析（未验证）",
      entry_ref is not None and int(entry_ref["verified"]) == 0)
n_ent_after_tree = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
check("树步骤命令新增条目 2 条", n_ent_after_tree == n_ent_before_tree + 2)

# ---- 追加现有树：末尾追加 + 原步骤接线 ----
dlg_a = ui_ai.AiImportDialog(tab3, d, source_text=resp_t, ctx=ctx_t,
                             session_id="sess-t2", message_index=5, target="tree")
dlg_a.show()
app.processEvents()
dlg_a.rb_append_tree.setChecked(True)
app.processEvents()
check("追加模式列出现有树", dlg_a.cmb_append_tree.count() >= 1)
idx_sel = next(i for i, t in enumerate(dlg_a._append_candidates)
               if t["tree_id"] == tree_obj["tree_id"])
dlg_a.cmb_append_tree.setCurrentIndex(idx_sel)
app.processEvents()
check("插入位置默认末尾", dlg_a.cmb_insert_pos.currentData() is None)
dlg_a.on_confirm()
app.processEvents()
tree2 = d.get_tree(tree_obj["tree_id"])
ids2 = [s["id"] for s in tree2["steps"]]
check("追加后 2 原步 + 2 新步 + 叶子（叶子仍在最后）",
      len(ids2) == 5 and tree2["steps"][-1].get("leafs"), str(ids2))
old_s2 = next(s for s in tree2["steps"] if s["id"] == "s2")
check("原末步「正常」分支已改指新链（原步骤其余不受影响）",
      old_s2["branches"][0]["goto"] == ids2[2]
      and old_s2["branches"][1]["goto"] == ids2[4]
      and old_s2["title"] == "查光功率")
leaf_node = tree2["steps"][-1]
check("原叶子结论不受影响", leaf_node["leafs"]["conclusion"] == "光模块老化导致链路震荡。")

# ---- 现象为空引导 ----
n_trees_mid = d.count_trees()
dlg_e = ui_ai.AiImportDialog(tab3, d, source_text=resp_t,
                             ctx=dict(ctx2, symptom=""),
                             session_id="sess-t3", message_index=0, target="tree")
dlg_e.show()
app.processEvents()
dlg_e.on_confirm()
app.processEvents()
check("现象为空 → 引导补填且不入库",
      "非空" in dlg_e.lbl_hint.text() and d.count_trees() == n_trees_mid)

# ---- 同现象树重复新建确认 ----
dlg_t2 = ui_ai.AiImportDialog(tab3, d, source_text=resp_t, ctx=ctx_t,
                              session_id="sess-t4", message_index=8, target="tree")
dlg_t2.show()
app.processEvents()
boxes["info"].clear()
dlg_t2.on_confirm()      # question mock=Yes → 仍新建
app.processEvents()
check("同现象树重复新建触发确认且可继续",
      d.count_trees() == n_trees_before + 2)

# ---- 溯源展示 + 会话定位 ----
pair = ui_ai.make_ai_source_label(d, tree_rec["target_uuid"])
check("溯源标签生成（🤖 来源：AI 会话）", bool(pair) and "🤖" in pair[0].text())

sess = ai_bridge.new_session({"vendor": "华为", "os": "VRP8"})
sess["session_id"] = "sess-t1"      # 与树入库溯源的 session_id 对齐
sess["messages"] = [
    {"role": "user", "content": "帮我查端口震荡", "attachments": [], "ts": "t1"},
    {"role": "assistant", "content": resp_t, "attachments": [], "ts": "t2"},
]
ai_bridge.save_session(sess)
ok_loc = tab3.open_session_and_locate(sess["session_id"], 1)
app.processEvents()
check("溯源跳转：打开会话并定位到回复", ok_loc and 1 in tab3._ai_handles
      and "定位" in tab3.lbl_state.text())

dlg_h = ui_ai.SessionHistoryDialog(tab3, tab3.window())
dlg_h.show()
app.processEvents()
has_stats = any("🤖" in dlg_h.list_items.item(i).text()
                for i in range(dlg_h.list_items.count())
                if "sess-t" in (dlg_h.list_items.item(i).toolTip() or ""))
check("历史抽屉显示「已入库 N/M 块」按去向分列", has_stats)

# 恢复 mock
qw.QMessageBox.information = staticmethod(lambda *a, **kw: None)
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.No)
ai_bridge.config_path = _orig_cfg_path

print()
print("=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL PASS")
