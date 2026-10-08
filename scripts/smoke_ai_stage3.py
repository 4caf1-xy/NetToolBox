# -*- coding: utf-8 -*-
"""查重增强第 1 轮冒烟：查重引擎（dedupe.py）+ 数据层增量（db.find_duplicates 等）"""
import os
import sys
import time
import tempfile

import dedupe
import db as dbmod

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ((" —— " + detail) if (detail and not cond) else ""))


print("[1] 归一化统一（dedupe 引擎 + db 委托）")
text = "! 注释行\n\ndisplay  DNS   Server \n# 另一注释\nDISPLAY Interface Brief"
norm = dedupe.normalize_text(text)
check("去注释/去空行/压缩空白/统一小写",
      norm == "display dns server\ndisplay interface brief", repr(norm))
check("db.normalize_commands_text 委托 dedupe（含小写）",
      dbmod.Database.normalize_commands_text(text) == norm)
check("db.normalize_error_text 委托 dedupe（小写）",
      dbmod.Database.normalize_error_text("  Error: BAD  ") == "error:bad")
check("报错原文归一化去全部空白", dedupe.normalize_error_text("a  b\tc\nd") == "abcd")

print("[2] 相似度算法（按比对对象选择）")
base = "\n".join("display ip route %02d" % i for i in range(1, 21))     # 20 行真实命令
near = base.replace("display ip route 10", "display arp 10")            # 改 1 行 ≈95%+
mid = "\n".join(("display mac-address %02d" if i % 5 == 0 else "display ip route %02d" % i)
                for i in range(1, 21))                                  # 改 4 行 ≈85%
unrelated = " completely different stuff \nnothing matches here"
s_ident = dedupe.command_similarity(base, base)
s_near = dedupe.command_similarity(base, near)
s_mid = dedupe.command_similarity(base, mid)
s_unrel = dedupe.command_similarity(base, unrelated)
check("完全相同 = 1.0", s_ident == 1.0)
check("95% 相似案例 ≥0.95（Jaccard 与序列比取大）", s_near >= 0.95, "score=%.3f" % s_near)
check("85% 相似案例落在 0.80~0.95 警示带", 0.80 <= s_mid < 0.95, "score=%.3f" % s_mid)
check("不相关内容 <0.80", s_unrel < 0.80, "score=%.3f" % s_unrel)
s_err = dedupe.text_similarity("Error: %Unrecognized command found at '^' position.",
                               "Error: %Unrecognized command found at '^' posilion.")
check("报错原文 SequenceMatcher 字符比可用", 0.80 <= s_err < 1.0, "score=%.3f" % s_err)
s_sym = dedupe.text_similarity("端口频繁震荡（华为 VRP8）", "端口频繁震荡（华为 VRP5）")
check("现象文本相似度可用", s_sym >= 0.80, "score=%.3f" % s_sym)

print("[3] 三级判定")
check("≥0.95 → high", dedupe.verdict(0.96) == "high" and dedupe.verdict(1.0) == "high")
check("0.80~0.95 → warn", dedupe.verdict(0.85) == "warn" and dedupe.verdict(0.80) == "warn")
check("<0.80 → pass", dedupe.verdict(0.79) == "pass")
check("percent 展示", dedupe.percent(0.923) == "92%")

print("[4] diff 预览")
diff = dedupe.unified_diff_preview(base, near)
check("unified_diff 头与差异标记",
      "--- 库内已有" in diff and "+++ 新产出" in diff
      and any(ln.startswith("-") for ln in diff.splitlines())
      and any(ln.startswith("+") for ln in diff.splitlines()))
check("差异行数受 max_lines 限制",
      len(dedupe.unified_diff_preview(base, near, max_lines=6).splitlines()) <= 6)

# ---------------------------------------------------------------------------
print("[5] db.find_duplicates（entry/err/tree 三去向）")
tmpdir = tempfile.mkdtemp(prefix="ntb_stage3_")
d = dbmod.Database(os.path.join(tmpdir, "t.db"))
# 库内种子：一条 95% 相似（huawei）、一条 85% 相似（huawei）、一条不相关（cisco）
uuid_high = d.add_entry({"title": "已有·接口状态检查", "vendor": "huawei",
                         "os_family": "vrp8", "platform": "network",
                         "commands": base, "category": "接口诊断"}, operator="t")
uuid_mid = d.add_entry({"title": "已有·变化版", "vendor": "huawei",
                        "os_family": "vrp8", "platform": "network",
                        "commands": mid, "category": "接口诊断"}, operator="t")
d.add_entry({"title": "无关条目", "vendor": "cisco", "os_family": "ios",
             "platform": "network", "commands": unrelated, "category": "其他"}, operator="t")

cands = d.find_duplicates("entry", {"commands": near, "vendor": "huawei"})
check("entry 查重命中 2 条且按相似度倒序",
      len(cands) == 2 and cands[0]["score"] >= cands[1]["score"])
check("高分候选 level=high 且指向正确条目",
      cands[0]["level"] == "high" and cands[0]["uuid"] == uuid_high)
check("低分候选 level=warn", cands[1]["level"] == "warn" and cands[1]["uuid"] == uuid_mid)
check("候选带 diff 预览与 summary", bool(cands[0]["diff"]) and "｜" in cands[0]["summary"])
check("vendor 分桶：cisco 桶不命中",
      d.find_duplicates("entry", {"commands": near, "vendor": "cisco"}) == [])
check("threshold=0.95 只留高分", len(d.find_duplicates("entry", {"commands": near}, threshold=0.95)) == 1)
check("不相关新命令零打扰（无候选）",
      d.find_duplicates("entry",
                        {"commands": "fresh brand new command\nno match here at all"}) == [])

err_id = d.add_err_dict_item({
    "raw_text": "Error: %Unrecognized command found at '^' position.",
    "cause": "命令拼写错误", "vendor": "huawei", "os_family": "vrp8"}, None)
cands_e = d.find_duplicates("err", {"raw_text":
    "Error: %Unrecognized command found at '^' posilion."})
check("err 查重：近似报错命中且 level=high（0.95 字符比）",
      len(cands_e) == 1 and cands_e[0]["err_id"] == err_id
      and cands_e[0]["score"] >= 0.95, "score=%.3f" % (cands_e[0]["score"] if cands_e else -1))
check("err 查重：无关报错零打扰",
      d.find_duplicates("err", {"raw_text": " totally different failure text "}) == [])

tree_id = d.create_trouble_tree({"symptom": "端口频繁震荡（华为 VRP8）",
                                 "vendor_hint": ["huawei"], "steps": []}, None)
cands_t = d.find_duplicates("tree", {"symptom": "端口频繁震荡（华为 VRP5）"})
check("tree 查重：相近现象命中", len(cands_t) == 1 and cands_t[0]["tree_id"] == tree_id
      and cands_t[0]["score"] >= 0.80)
def _raises_value_error(db_):
    try:
        db_.find_duplicates("bogus", {})
        return False
    except ValueError:
        return True


check("未知 kind 抛 ValueError", _raises_value_error(d))

print("[6] 替换决策：update_entry_commands")
d.mark_verified(uuid_high, verified_by="tester", verified_model="S5720")
entry_before = d.get_entry(uuid_high)
check("前置：条目已验证（绿）", int(entry_before["verified"]) == 1)
new_cmds = base + "\ndisplay device"
changed = d.update_entry_commands(uuid_high, new_cmds,
                                  {"operator": "tester", "dup_of_uuid": uuid_high,
                                   "message": "AI 产出版本更新"})
entry_after = d.get_entry(uuid_high)
check("commands 已更新", entry_after["commands"] == new_cmds)
check("verified 强制归 0 且验证信息清空",
      int(entry_after["verified"]) == 0 and entry_after["verified_by"] == "")
h = d.conn.execute("SELECT new_value FROM history WHERE table_name='entries' "
                   "AND record_id=? ORDER BY id DESC LIMIT 1", (uuid_high,)).fetchone()
check("history 记录替换备注（含 dup_of_uuid）",
      bool(h) and "查重替换" in h["new_value"] and uuid_high in h["new_value"])
check("内容未变时替换不生效", d.update_entry_commands(
    uuid_high, new_cmds, {"operator": "tester"}) is False)

print("[7] 追加决策：append_err_reason")
err_before = d.get_err(err_id)
verified_before = int(err_before["verified"])
ok = d.append_err_reason(err_id, "另一种可能：命令未在当前视图下执行。",
                         {"operator": "tester", "dup_of_uuid": err_id})
err_after = d.get_err(err_id)
check("cause 追加成功且原文保留",
      ok and "另一种可能" in err_after["cause"] and "命令拼写错误" in err_after["cause"])
check("验证状态不变", int(err_after["verified"]) == verified_before)
h2 = d.conn.execute("SELECT new_value FROM history WHERE table_name='err_dict' "
                    "AND record_id=? ORDER BY id DESC LIMIT 1", (err_id,)).fetchone()
check("history 记录追加动作（验证状态不变）",
      bool(h2) and "追加原因分析" in h2["new_value"] and "验证状态不变" in h2["new_value"])
check("空原因不追加", d.append_err_reason(err_id, "   ") is False)

print("[8] dup_of_uuid 补列（旧库升级）+ 决策溯源")
check("新库含 dup_of_uuid 列",
      "dup_of_uuid" in {r["name"] for r in d.conn.execute(
          "PRAGMA table_info(ai_imports)").fetchall()})
# 模拟旧库：去掉 dup_of_uuid 列
import sqlite3
old2 = os.path.join(tmpdir, "old2.db")
raw = sqlite3.connect(old2)
raw.execute("CREATE TABLE ai_imports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, message_index INTEGER, block_index INTEGER, "
            "target_type TEXT DEFAULT 'entry', target_uuid TEXT, imported_at TEXT, "
            "operator TEXT, model TEXT)")
raw.commit(); raw.close()
d2 = dbmod.Database(old2)
cols = {r["name"] for r in d2.conn.execute("PRAGMA table_info(ai_imports)").fetchall()}
check("旧库打开自动补列 dup_of_uuid", "dup_of_uuid" in cols)
ok_log = d2.log_ai_import("sess-x", 3, 0, "entry", "new-uuid",
                          operator="t", model="m", dup_of_uuid="existing-uuid")
rec = d2.get_ai_import("sess-x", 3, 0)
check("仍入库/替换溯源写入 dup_of_uuid",
      ok_log and rec["dup_of_uuid"] == "existing-uuid")

print("[9] 性能：3000 条目查重 <1s")
mem = dbmod.Database(":memory:")
rows = []
for i in range(3000):
    rows.append(("title%d" % i, "display command %d" % (i % 500), "huawei" if i % 2 else "cisco"))
mem.conn.executemany("INSERT INTO entries (uuid, title, commands, vendor, platform) "
                     "VALUES (?,?,?,?, 'network')",
                     [("u%d" % i, t, c, v) for i, (t, c, v) in enumerate(rows)])
mem.conn.commit()
new_input = "\n".join("display command %d" % i for i in range(20))   # 不在库内
t0 = time.monotonic()
res = mem.find_duplicates("entry", {"commands": new_input, "vendor": "huawei"})
cost = time.monotonic() - t0
check("3000 条目（Jaccard 快筛）查重 <1s", cost < 1.0, "cost=%.3fs" % cost)
# 相似条目在库内：构造一条 95% 相似后确认可检出且耗时可控
mem.conn.execute("INSERT INTO entries (uuid, title, commands, vendor, platform) "
                 "VALUES ('u-hot', '热条目', ?, 'huawei', 'network')", (base,))
mem.conn.commit()
t0 = time.monotonic()
res2 = mem.find_duplicates("entry", {"commands": near, "vendor": "huawei"})
cost2 = time.monotonic() - t0
check("含相似候选时仍 <1s 且命中", cost2 < 1.0 and res2
      and res2[0]["uuid"] == "u-hot", "cost=%.3fs" % cost2)
mem.close()          # d / d2 保留给 [10] UI 用例继续使用


# ---------------------------------------------------------------------------
print("[10] 第 2 轮 UI：异步预查 + diff 卡片 + 四选决策 + 内部去重")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)
import ui_ai
import PyQt5.QtWidgets as qw

boxes = {"info": [], "question": []}
qw.QMessageBox.information = staticmethod(
    lambda *a, **kw: boxes["info"].append(a[1] if len(a) > 1 else ""))
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.Yes)
_orig_write_nlb = ui_ai.DraftFromAiDialog._write_draft_nlb
ui_ai.DraftFromAiDialog._write_draft_nlb = staticmethod(lambda entries: "（测试跳过留痕）")
import ai_bridge
# A14（审计 2026-10-08）：原 dir() 守卫行是永不生效的死代码（import 在后，
# 守卫恒 False），且下一行本就会保存原始函数指针——直接删除
_orig_cfg_path = ai_bridge.config_path
ai_bridge.config_path = lambda: os.path.join(tmpdir, "ai_config_s3.json")

tab = ui_ai.AiTab(db=d)
tab.show()
app.processEvents()


def wait_dup(dlg, kind, timeout=4.0):
    t0 = time.time()
    while dlg._dup_state.get(kind) != "done" and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


ctx_d = {"vendor": "华为", "os": "VRP8", "model_name": "S5720",
         "symptom": "接口状态检查", "echo": "", "steps": ""}
resp_near = "```bash\n" + near + "\n```"          # 与 base 95% 相似
uuid_pure = d.add_entry({"title": "纯净基准条目", "vendor": "huawei", "os_family": "vrp8",
                         "platform": "network", "commands": base,
                         "category": "接口诊断"}, operator="t")   # 未被其它用例改写

# ---- entry：异步预查命中 2 候选（1 high + 1 warn）----
dlg = ui_ai.AiImportDialog(tab, d, source_text=resp_near, ctx=ctx_d,
                           session_id="d1", message_index=0, target="entry")
dlg.show()
wait_dup(dlg, "entry")
check("异步预查完成且卡片面板可见", dlg._dup_state.get("entry") == "done"
      and dlg.dup_panel.isVisible())
check("命中卡片数 = 3（pure high + 改写后 warn + mid warn）", len(dlg.dup_candidates["entry"]) == 3)
c_high = dlg.dup_candidates["entry"][0]
check("high 候选默认决策 = 跳过", dlg._dup_decisions["entry"].get(c_high["uuid"]) == "skip")
card_texts = []
for i in range(dlg.dup_v.count()):
    w = dlg.dup_v.itemAt(i).widget()
    if w is not None:
        from PyQt5.QtWidgets import QPushButton
        btn = w.findChild(QPushButton)
        if btn and "疑似与《" in btn.text():
            card_texts.append(btn.text())
check("卡片头部含相似度百分比与标题", any("疑似与《" in t and "%" in t for t in card_texts),
      str(card_texts)[:120])

# 默认跳过 → 提交被拦截，库不变
n_before = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
dlg.on_confirm()
app.processEvents()
check("high 默认跳过 → 提交拦截", "跳过" in dlg.lbl_hint.text()
      and d.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == n_before)

# ---- 替换：verified 归 0 + dup_of_uuid 指向正确 ----
d.mark_verified(uuid_pure, verified_by="tester", verified_model="S5720")
dlg._dup_decisions["entry"][c_high["uuid"]] = "replace"
dlg.on_confirm()
app.processEvents()
e_after = d.get_entry(uuid_pure)
check("替换后 commands 更新且 verified 归 0",
      "arp" in e_after["commands"] and int(e_after["verified"]) == 0)
imp = d.get_ai_import("d1", 0, -1, "entry")   # 替换路径溯源记录 block_index=-1
check("替换溯源 dup_of_uuid 指向既有条目", bool(imp) and imp["dup_of_uuid"] == uuid_pure)
check("无新增条目（替换不产生新行）",
      d.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == n_before)

# ---- 仍入库：notes 重复备注 + 溯源 dup_of_uuid ----
dlg2 = ui_ai.AiImportDialog(tab, d, source_text=resp_near, ctx=ctx_d,
                            session_id="d2", message_index=1, target="entry")
dlg2.show()
wait_dup(dlg2, "entry")
dlg2._dup_decisions["entry"][dlg2.dup_candidates["entry"][0]["uuid"]] = "keep"
dlg2.on_confirm()
app.processEvents()
check("仍入库：新增 1 条", d.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == n_before + 1)
new_e = d.conn.execute("SELECT * FROM entries ORDER BY rowid DESC LIMIT 1").fetchone()
check("仍入库：notes 含重复备注", "查重" in new_e["notes"] and "仍入库" in new_e["notes"])
imp2 = d.get_ai_import("d2", 1, 0, "entry")
check("仍入库：溯源 dup_of_uuid 指向既有条目", bool(imp2) and imp2["dup_of_uuid"] == uuid_pure)
h_dup = d.conn.execute("SELECT new_value FROM history WHERE table_name='entries' "
                       "AND record_id=? ORDER BY id DESC LIMIT 1", (new_e["uuid"],)).fetchone()
check("history 留痕（create 含备注原文）", bool(h_dup) and "查重" in h_dup["new_value"])

# ---- 内部去重：同次提交两个相同块只入库一个 ----
resp_dup = "```bash\ndisplay version\n```\n```bash\ndisplay version\n```"
dlg3 = ui_ai.AiImportDialog(tab, d, source_text=resp_dup, ctx=ctx_d,
                            session_id="d3", message_index=2, target="entry")
dlg3.show()
wait_dup(dlg3, "entry")
dlg3.rb_single.setChecked(True)
app.processEvents()
n3 = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
dlg3.on_confirm()
app.processEvents()
n3_after = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
check("内部重复：两相同块只入库一个", n3_after == n3 + 1)
dup_rows = [r for r in dlg3.entry_rows if not r["chk"].isEnabled()]
check("内部重复块标灰（禁用）", len(dup_rows) == 1 and "内部重复" in dup_rows[0]["chk"].text())

# ---- err：默认推荐追加原因分析，原条目命令与验证状态不变 ----
resp_err = "```text\nError: %Unrecognized command found at '^' posilion.\n```"
ctx_e = dict(ctx_d, symptom="命令敲错",
             echo="Error: %Unrecognized command found at '^' posilion.")
dlg4 = ui_ai.AiImportDialog(tab, d, source_text=resp_err, ctx=ctx_e,
                            session_id="d4", message_index=3, target="err")
dlg4.show()
wait_dup(dlg4, "err")
check("err 候选命中且默认决策 = 追加原因分析",
      len(dlg4.dup_candidates["err"]) >= 1
      and dlg4._dup_decisions["err"].get(dlg4.dup_candidates["err"][0]["err_id"]) == "append")
err_before = d.get_err(err_id)
n_err = d.count_errs()
dlg4.ed_cause.setPlainText("AI 分析：可能是未进入系统视图。")
dlg4.on_confirm()
app.processEvents()
err_after = d.get_err(err_id)
check("追加原因分析：cause 已追加、条目数不变",
      d.count_errs() == n_err and "未进入系统视图" in err_after["cause"]
      and "命令拼写错误" in err_after["cause"])
check("追加后验证状态不变", int(err_after["verified"]) == int(err_before["verified"]))
imp4 = d.get_ai_import("d4", 3, -1, "err")
check("追加溯源 dup_of_uuid 指向既有条目", bool(imp4) and imp4["dup_of_uuid"] == err_id)

# err 仍入库路径
dlg5 = ui_ai.AiImportDialog(tab, d, source_text=resp_err, ctx=ctx_e,
                            session_id="d5", message_index=4, target="err")
dlg5.show()
wait_dup(dlg5, "err")
dlg5._dup_decisions["err"][dlg5.dup_candidates["err"][0]["err_id"]] = "keep"
dlg5.on_confirm()
app.processEvents()
check("err 仍入库：第二条映射生成且 cause 含重复备注",
      d.count_errs() == n_err + 1
      and "仍入库" in d.conn.execute("SELECT cause FROM err_dict ORDER BY rowid DESC LIMIT 1").fetchone()[0])
imp5 = d.get_ai_import("d5", 4, -1, "err")
check("err 仍入库溯源 dup_of_uuid", bool(imp5) and imp5["dup_of_uuid"] == err_id)

# ---- tree：高度疑似自动引导切换追加模式且追加成功 ----
resp_t = "步骤 1：查震荡\n```bash\ndisplay interface brief\n```\n## 原因\n链路质量差。"
tree2_id = d.create_trouble_tree({
    "symptom": "端口频繁震荡（华为 VRP5）",
    "vendor_hint": ["huawei"], "steps": [
        {"id": "s1", "title": "查接口", "observe": "是否符合预期？",
         "branches": [{"when": "正常/符合预期", "goto": "leaf"},
                      {"when": "异常/不符合预期", "goto": "leaf"}]},
        {"id": "leaf", "title": "结论", "leafs": {"conclusion": "链路问题", "actions": []}},
    ]}, None)
ctx_t = dict(ctx_d, symptom="端口频繁震荡（华为 VRP5）")     # ≈0.96 高度疑似
dlg6 = ui_ai.AiImportDialog(tab, d, source_text=resp_t, ctx=ctx_t,
                            session_id="d6", message_index=5, target="tree")
dlg6.show()
wait_dup(dlg6, "tree")
check("tree 高度疑似 → 自动切换「追加现有树」", dlg6.rb_append_tree.isChecked())
sel = dlg6._selected_append_tree()
check("追加树已预选命中候选", bool(sel) and sel["tree_id"] == tree2_id)
dlg6.on_confirm()
app.processEvents()
tree_after = d.get_tree(tree2_id)
check("追加成功：原 2 节点 + 新步骤 + 叶子仍在最后",
      len(tree_after["steps"]) == 3 and tree_after["steps"][-1].get("leafs"),
      str([(x.get("id"), bool(x.get("leafs"))) for x in tree_after["steps"]]))
s1n = tree_after["steps"][0]
check("原步骤「正常」分支已改指新步骤", s1n["branches"][0]["goto"] == tree_after["steps"][1]["id"])

# ---- 无命中零打扰 ----
resp_new = "```bash\ndisplay unique-command-2024-09-24 status\n```"
n7 = d.conn.execute("SELECT count(*) FROM entries").fetchone()[0]
dlg7 = ui_ai.AiImportDialog(tab, d, source_text=resp_new, ctx=ctx_d,
                            session_id="d7", message_index=6, target="entry")
dlg7.show()
wait_dup(dlg7, "entry")
check("无命中：查重面板隐藏（零打扰）", not dlg7.dup_panel.isVisible())
dlg7.on_confirm()
app.processEvents()
check("无命中：提交流程与升级前一致（正常入库）",
      d.conn.execute("SELECT count(*) FROM entries").fetchone()[0] == n7 + 1)
check("无命中：notes 无查重备注",
      "查重" not in d.conn.execute("SELECT notes FROM entries ORDER BY rowid DESC LIMIT 1").fetchone()[0])

qw.QMessageBox.information = staticmethod(lambda *a, **kw: None)
qw.QMessageBox.question = staticmethod(lambda *a, **kw: qw.QMessageBox.No)
ui_ai.DraftFromAiDialog._write_draft_nlb = _orig_write_nlb
ai_bridge.config_path = _orig_cfg_path

print()
print("=" * 60)
print()
print("=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL PASS")
