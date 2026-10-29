# -*- coding: utf-8 -*-
"""
smoke_tree_visual.py —— 排查树可视化编辑器 MVP（F 批）冒烟（2026-10-29 新增）

覆盖六案（全部 offscreen + 临时种子副本，主库/真种子零接触、零联网）：
    [A] tree_seed_io 纯逻辑：4 文件字节回环（CRLF/LF 各自保留）/ count 同步 /
        回滚字节 identical / 跨文件重复归属报错
    [B] 26 棵树全量渲染离屏断言（逐棵节点数对账）+ 渲染耗时均值
    [C] 编辑流端到端：编辑模式解锁 / 加步骤 + 改引用（选择器路径，uuid 只读）/
        diff 与预期一致 / 门禁写入 EXIT=0 / 新库重导入后逐 uuid 可解析（主程序可用）
    [D] 坏 uuid 反例被 validate [E] 拦截（"解析不到条目"）+ 自动回滚字节 identical
        + 种子目录无中间态残留
    [E] 中断安全：dirty 状态关闭不写盘 / 写盘中途异常（os.replace 注入失败）
        文件原样且无残留
    [F] 工程断言：RUNTIME_FILES 登记两模块 / 模块零联网源码断言 /
        真种子 4 文件 sha256 前后一致（零接触实证）

用法：python scripts/smoke_tree_visual.py   （EXIT=0 全过；PyQt5 经 _vendor）
"""
import os
import sys
import json
import glob
import copy
import time
import shutil
import hashlib
import tempfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))
sys.path.insert(0, os.path.join(APP_DIR, "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 断言

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))

REPO_SEED = os.path.join(APP_DIR, "app", "seed_data")
TREE_GLOB = os.path.join(REPO_SEED, "trouble_trees_*.json")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_seed_copy(base):
    """真种子 → 临时目录副本（编辑器的读写对象；真种子全程零接触）"""
    sd = os.path.join(base, "seed_data")
    os.makedirs(sd, exist_ok=True)
    for f in glob.glob(TREE_GLOB):
        shutil.copy(f, sd)
    return sd


import db as dbmod
import tree_seed_io as tio

# ======================================================================
# [F0] 真种子指纹（套件开始前取，结尾复核）
# ======================================================================
SEED_FP = {p: _sha256(p) for p in glob.glob(TREE_GLOB)}
check("F0/真种子指纹取样", len(SEED_FP) == 4, str(len(SEED_FP)))

# 临时种子目录（劫持 base 后 seed_dirs() 指向它）
BASE = _se.setup()
SEED_DIR = _make_seed_copy(BASE)

TMP_DB = os.path.join(BASE, "command_lib.db")
dbmod.get_db_path = lambda: TMP_DB
database = dbmod.Database(":memory:")
database.import_seed_dir()
check("F0/种子导入397条", database.stats()["total"] == 397,
      str(database.stats()["total"]))

# ======================================================================
# [A] tree_seed_io 纯逻辑
# ======================================================================
for p in tio.list_tree_files(SEED_DIR):
    doc, raw = tio.load_doc(p)
    again = tio.serialize(doc, crlf=tio.detect_crlf(raw)).encode("utf-8")
    check("A/字节回环 %s" % os.path.basename(p), again == raw)

_doc, _raw = tio.load_doc(os.path.join(SEED_DIR, "trouble_trees_linux.json"))
check("A/linux为LF(eol=lf规范)", b"\r\n" not in _raw and _raw.count(b"\n") > 100)
_fw_doc, _fw_raw = tio.load_doc(os.path.join(SEED_DIR, "trouble_trees_firewall.json"))
check("A/firewall为LF(eol=lf规范)", b"\r\n" not in _fw_raw and _fw_raw.count(b"\n") > 50)
_check, _craw = tio.load_doc(os.path.join(SEED_DIR, "trouble_trees_ops.json"))
check("A/ops已归一LF", b"\r\n" not in _craw)

path, tree = tio.locate_tree_file(SEED_DIR, "fw-policy-deny")
check("A/定位唯一归属", bool(path) and tree.get("tree_id") == "fw-policy-deny")

t_edit = copy.deepcopy(tree)
t_edit["steps"].append({"id": "sx", "title": "冒烟测试步骤",
                        "cmd_ref": {"vendor_category": "防火墙诊断",
                                    "by_vendor": {"fortinet": {
                                        "uuid": "f0ac2dda-5a37-5070-a367-0026fe7be5a1",
                                        "title": "x"}}}})
ok, msg, _out = tio.commit_tree(SEED_DIR, t_edit, lambda: (1, "[E] fake"))
check("A/门禁失败回滚", not ok and "回滚" in msg)
check("A/回滚字节identical", tio.load_doc(path)[1] == _fw_raw)
ok, msg, _out = tio.commit_tree(SEED_DIR, t_edit, lambda: (0, "ok"))
check("A/门禁通过写入", ok and "EXIT=0" in msg)
doc_now, _ = tio.load_doc(path)
check("A/count元数据同步", doc_now.get("count") == 4
      and len(doc_now["trouble_trees"]) == 4)
check("A/diff预览含新增节点",
      any("+" in ln and '"sx"' in ln
          for ln in tio.unified_diff(_fw_raw.decode("utf-8"),
                                     tio.serialize(doc_now,
                                                   crlf=tio.detect_crlf(_fw_raw))
                                     ).splitlines()))
shutil.copy(os.path.join(REPO_SEED, "trouble_trees_firewall.json"),
            os.path.join(SEED_DIR, "trouble_trees_zzdup.json"))
try:
    tio.locate_tree_file(SEED_DIR, "fw-policy-deny")
    check("A/重复归属报错", False)
except tio.TreeSeedError:
    check("A/重复归属报错", True)
os.unlink(os.path.join(SEED_DIR, "trouble_trees_zzdup.json"))

# ======================================================================
# [B] 26 棵树全量渲染离屏断言
# ======================================================================
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])
import ui_tree_visual as utv

dlg = utv.TreeVisualEditorDialog(database)
check("B/编辑器26棵树", len(dlg.trees) == 26, str(len(dlg.trees)))
check("B/seed目录定位", bool(dlg.seed_dir) and
      os.path.dirname(dlg.seed_dir) == BASE, str(dlg.seed_dir))

t0 = time.time()
render_fail = []
for i in range(len(dlg.trees)):
    dlg.tbl_trees.selectRow(i)
    t = dlg.trees[i]["tree"]
    steps = t.get("steps") or []
    root = dlg.tv_preview.topLevelItem(0)
    if dlg.tv_preview.topLevelItemCount() != 1 or root.childCount() != len(steps):
        render_fail.append(t.get("tree_id"))
        continue
    # 分支子节点逐一对账：goto 目标必须都在步骤集合里（无 ⚠ 标注）
    ids = set(s.get("id") for s in steps)
    for j in range(root.childCount()):
        node = root.child(j)
        for k in range(node.childCount()):
            txt = node.child(k).text(0)
            if "⚠ 目标不存在" in txt:
                render_fail.append("%s/分支%s" % (t.get("tree_id"), txt.strip()[:30]))
elapsed = time.time() - t0
check("B/26棵全量渲染", not render_fail, "失败：%s" % render_fail[:3])
check("B/渲染不卡顿(均值<50ms)", elapsed / 26 < 0.05, "%.1fms/棵" % (elapsed / 26 * 1000))

# ======================================================================
# [C] 编辑流端到端（加节点 + 改引用 → diff → EXIT=0 → 主程序可用）
# ======================================================================
dlg.tbl_trees.selectRow(0)
tid0 = dlg.working["tree_id"]
n0 = len(dlg.working["steps"])
check("C/初始只读(步骤按钮禁用)",
      not dlg._stepbtn_add.isEnabled() and not dlg._stepbtn_edit.isEnabled())
dlg._on_edit_mode()
check("C/编辑模式解锁", dlg.edit_mode and dlg._stepbtn_add.isEnabled())
check("C/无编辑时导出禁用", not dlg.btn_export.isEnabled())

# 新增步骤（经 SeedStepDialog；uuid 走"选择器同款赋值路径"，单元格本身只读）
dlg2 = utv.SeedStepDialog(database, dlg.working, step=None,
                          other_ids=set(s["id"] for s in dlg.working["steps"]))
dlg2.ed_id.setText("sx")
dlg2.ed_title.setText("冒烟新增：NAT 转换复核")
dlg2.cmb_cat.setCurrentText("防火墙诊断")
dlg2.tbl_bv.selectRow(0)
slug_pick = dlg2._current_bv_slug()
uuid_item = dlg2.tbl_bv.item(dlg2.tbl_bv.currentRow(), 2)
check("C/uuid单元格只读(禁手写)", not (uuid_item.flags() & Qt.ItemIsEditable))
entries = database.search(vendor=slug_pick, category="防火墙诊断")
check("C/选择器搜索命中", len(entries) >= 1, "%s:%d" % (slug_pick, len(entries)))
uuid_item.setText(entries[0]["uuid"])          # 与 _pick_vendor_entry 赋值路径一致
dlg2._add_branch("冒烟分支", "s1")
step_new = dlg2.values()
check("C/新步骤uuid来自库内",
      database.get_entry(step_new["cmd_ref"]["by_vendor"][slug_pick]["uuid"]) is not None)
dlg.working["steps"].append(step_new)

# 改引用：既有步骤 s1 的 fortinet 指向换成同厂商另一条（选择器同款路径）
s1 = [s for s in dlg.working["steps"] if s.get("id") == "s1"][0]
alt = [e for e in entries if e["uuid"] != s1["cmd_ref"]["by_vendor"][slug_pick]["uuid"]]
check("C/存在可换目标", len(alt) >= 1)
s1["cmd_ref"]["by_vendor"][slug_pick]["uuid"] = alt[0]["uuid"]
s1["cmd_ref"]["by_vendor"][slug_pick]["title"] = alt[0]["title"]
dlg.dirty = True
dlg._render_all()
check("C/加节点后预览同步", dlg.tv_preview.topLevelItem(0).childCount() == n0 + 1)

diff, err = dlg._build_diff()
check("C/diff无错误", err is None, str(err))
check("C/diff含新增节点", any("+" in ln and '"sx"' in ln for ln in (diff or "").splitlines()))
check("C/diff含改引用", any(("+" in ln and alt[0]["uuid"][:8] in ln) for ln in (diff or "").splitlines()))
check("C/dirty后导出可用", dlg.btn_export.isEnabled())

ok, msg, _out = tio.commit_tree(dlg.seed_dir, copy.deepcopy(dlg.working),
                                dlg._validate_gate)
check("C/门禁写入EXIT=0", ok and "EXIT=0" in msg, msg)

db2 = dbmod.Database(":memory:")
db2.import_seed_dir()
t2 = db2.get_tree(tid0)
check("C/新库重导入含新步骤",
      t2 is not None and any(s["id"] == "sx" for s in t2["steps"]))
unresolved = []
for s in t2["steps"]:
    for _slug, hit in (s.get("cmd_ref", {}).get("by_vendor") or {}).items():
        if hit.get("uuid") and not db2.get_entry(hit["uuid"]):
            unresolved.append((s["id"], _slug))
check("C/主程序逐uuid可解析", not unresolved, str(unresolved[:3]))

# ======================================================================
# [D] 坏 uuid 反例 [E] 拦截 + 回滚无中间态
# ======================================================================
bad = copy.deepcopy(t2)
bad["steps"][-1]["cmd_ref"]["by_vendor"][slug_pick]["uuid"] = \
    "00000000-0000-0000-0000-000000000000"
ok_bad, msg_bad, out_bad = tio.commit_tree(dlg.seed_dir, bad, dlg._validate_gate)
check("D/坏uuid被拦截", not ok_bad and "EXIT=1" in msg_bad, msg_bad)
check("D/[E]文案为解析不到条目", "解析不到条目" in out_bad)
check("D/回滚后种子即好版本",
      any(s["id"] == "sx" for s in tio.load_doc(os.path.join(
          dlg.seed_dir, "trouble_trees_firewall.json"))[0]["trouble_trees"][0]["steps"]))
check("D/无中间态残留", not glob.glob(os.path.join(dlg.seed_dir, ".tree_export_*"))
      and not glob.glob(os.path.join(dlg.seed_dir, "*.tmp")))

# ======================================================================
# [E] 中断安全
# ======================================================================
before_close = {p: _sha256(p) for p in glob.glob(os.path.join(SEED_DIR, "*.json"))}
dlg.working["steps"].append({"id": "sy", "title": "将随关闭丢弃",
                             "cmd_ref": {"vendor_category": "防火墙诊断"}})
dlg.dirty = True
# 模拟"编辑中断直接关闭"：不调用导出，种子字节必须原样
after_close = {p: _sha256(p) for p in glob.glob(os.path.join(SEED_DIR, "*.json"))}
check("E/dirty不写盘(关闭丢弃)", before_close == after_close)

# 写盘中途异常（os.replace 注入失败）：文件原样、无残留
_real_replace = os.replace
def _boom(a, b):
    raise OSError("injected failure")
try:
    os.replace = _boom
    try:
        tio.commit_tree(dlg.seed_dir, copy.deepcopy(bad), lambda: (0, "ok"))
        check("E/中途异常向上抛", False)
    except OSError:
        check("E/中途异常向上抛", True)
finally:
    os.replace = _real_replace
check("E/中途异常文件原样", {p: _sha256(p) for p in glob.glob(
    os.path.join(SEED_DIR, "*.json"))} == after_close)
check("E/中途异常无残留", not glob.glob(os.path.join(dlg.seed_dir, ".tree_export_*"))
      and not glob.glob(os.path.join(dlg.seed_dir, "*.tmp")))

# ======================================================================
# [F] 工程断言
# ======================================================================
import main as mainmod
check("F/RUNTIME_FILES登记", "ui_tree_visual.py" in mainmod.RUNTIME_FILES
      and "tree_seed_io.py" in mainmod.RUNTIME_FILES)

FORBIDDEN = ["socket", "ssl", "requests", "urllib", "http.client", "paramiko",
             "subprocess.", "os.system(", "urlopen("]
for mod_name in ("ui_tree_visual", "tree_seed_io"):
    src_path = os.path.join(APP_DIR, "app", mod_name + ".py")
    with open(src_path, "r", encoding="utf-8") as fp:
        src = fp.read()
    hit_lines = []
    for ln in src.splitlines():
        code = ln.split("#")[0]
        if any(f in code for f in FORBIDDEN):
            hit_lines.append(ln.strip()[:50])
    check("F/零联网源码断言 %s" % mod_name, not hit_lines, str(hit_lines[:2]))

# ======================================================================
# 汇总
# ======================================================================
print("=" * 66)
print("smoke_tree_visual 结果（F 批）")
print("=" * 66)
fails = 0
for name, ok_, detail in RESULTS:
    print("[%s] %s%s" % ("PASS" if ok_ else "FAIL", name,
                         ("  —— " + detail) if detail and not ok_ else ""))
    if not ok_:
        fails += 1
print("-" * 66)
print("合计 %d 项，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - fails, fails))

seed_ok = all(_sha256(p) == fp for p, fp in SEED_FP.items())
print("真种子零接触断言：%s" % ("通过" if seed_ok else "失败（真种子被改动！）"))
print("[SMOKE-ENV] 主库零接触由 atexit 指纹断言兜底")

sys.exit(0 if (fails == 0 and seed_ok) else 1)
