# -*- coding: utf-8 -*-
"""
smoke_scenarios.py —— 场景包（跨三库清单式捆绑）冒烟（2026-10-12 新增）

覆盖五案（全部 offscreen + mock，不依赖真实网络/设备）：
    [A] 种子正例：seed_data/scenario_packs.json 导入 3 包；--validate-seed
        同口径 in-process 跑通（rc=0）；统计行出现「场景包：3 个」
    [B] 引用校验反例：故意写错 uuid / seq 断号 / ref_type 非法 /
        ref_id+by_vendor 同现 → validate 一律 [E] 拦截（rc=1）
    [C] UI 加载：MainWindow 六 Tab；3 包可打开；卡片数=items 数；
        by_vendor 厂商视图切换（展开命令 / skip 置灰 / multi 提示）
    [D] 跳转定位：entry→命令库选中 / tree→排查向导载入 / err→字典管理器
        （FakeDictMgr mock，offscreen 不真开模态）
    [E] 复制守卫：必填占位拦截（剪贴板不变）+ 正常复制（骨架/回退守卫
        会话抑制后放行）+ splitter 状态记忆往返

用法：python scripts/smoke_scenarios.py   （EXIT=0 全过；需 PyQt5）
"""
import os
import sys
import glob
import json
import shutil
import tempfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 斥言

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def seeded_db():
    import db as dbmod
    d = dbmod.Database(":memory:")
    d.import_seed_dir()
    return d


def run_validate_with_patched_seed(seed_dir):
    """指向指定 seed_data 目录跑 validate_seed，返回 (rc, 输出全文)"""
    import io
    import contextlib
    import db as dbmod
    import main as mainmod
    orig = dbmod.seed_dirs
    dbmod.seed_dirs = lambda: [seed_dir]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mainmod.validate_seed()
    finally:
        dbmod.seed_dirs = orig
    return rc, buf.getvalue()


def make_seed_copy():
    """把仓库 seed_data 复制到临时目录，返回目录路径"""
    src = os.path.join(APP_DIR, "app", "seed_data")
    dst = os.path.join(tempfile.mkdtemp(prefix="ntbx_scn_"), "seed_data")
    os.makedirs(dst)
    for f in glob.glob(os.path.join(src, "*.json")):
        shutil.copy(f, dst)
    return dst


# ======================================================================
# [A] 种子正例 + validate 通过
# ======================================================================
def case_a():
    db = seeded_db()
    check("A/导入-3包", db.count_packs() == 3, str(db.count_packs()))
    check("A/三库规模", db.count() == 348 and db.count_trees() == 26
          and db.count_errs() == 97,
          "%d/%d/%d" % (db.count(), db.count_trees(), db.count_errs()))
    titles = sorted(p["title"] for p in db.all_packs())
    check("A/预置包名", titles == ["割接前检查", "故障应急回退", "新设备上架"],
          str(titles))
    # 顺序语义：每个包 seq 必须 1..N
    ok_seq = all(int(it.get("seq")) == i + 1
                 for p in db.all_packs()
                 for i, it in enumerate(p["items"]))
    check("A/seq连续", ok_seq, "")
    rc, out = run_validate_with_patched_seed(
        os.path.join(APP_DIR, "app", "seed_data"))
    check("A/validate-EXIT0", rc == 0, "rc=%d" % rc)
    check("A/validate-统计行", "场景包：3 个" in out, "")


# ======================================================================
# [B] 引用校验反例（坏引用 [E] 硬拦截）
# ======================================================================
def _mutate_and_validate(mutator, tag):
    sd = make_seed_copy()
    try:
        p = os.path.join(sd, "scenario_packs.json")
        d = json.load(open(p, encoding="utf-8"))
        mutator(d)
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        rc, out = run_validate_with_patched_seed(sd)
        check("B/%s-rc1" % tag, rc == 1, "rc=%d" % rc)
        check("B/%s-E拦截" % tag, "[X] 场景包发现" in out, out[-200:])
    finally:
        shutil.rmtree(os.path.dirname(sd), ignore_errors=True)


def case_b():
    # 反例 1：故意写错 uuid（验收清单指定场景）
    def bad_uuid(d):
        d["scenario_packs"][0]["items"][0]["by_vendor"]["cisco"] = \
            "00000000-0000-0000-0000-000000000000"
    _mutate_and_validate(bad_uuid, "坏uuid")

    # 反例 2：seq 断号
    def bad_seq(d):
        d["scenario_packs"][1]["items"][2]["seq"] = 99
    _mutate_and_validate(bad_seq, "seq断号")

    # 反例 3：ref_type 非法
    def bad_type(d):
        d["scenario_packs"][2]["items"][3]["ref_type"] = "entry2"
    _mutate_and_validate(bad_type, "ref_type非法")

    # 反例 4：ref_id 与 by_vendor 同现
    def bad_both(d):
        it = d["scenario_packs"][2]["items"][3]
        it["by_vendor"] = {"cisco": it["ref_id"]}
    _mutate_and_validate(bad_both, "同现")

    # 反例 5：tree 引用不存在
    def bad_tree(d):
        d["scenario_packs"][2]["items"][4]["ref_id"] = "no-such-tree"
    _mutate_and_validate(bad_tree, "坏tree引用")


# ======================================================================
# [C] UI 加载 + 厂商视图
# ======================================================================
def case_c():
    from PyQt5.QtWidgets import QApplication, QPlainTextEdit
    from PyQt5.QtCore import Qt
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import ui_main
    db = seeded_db()
    w = ui_main.MainWindow(db)

    check("C/六Tab", w.tabs.count() == 6, str(w.tabs.count()))
    sc = w.tab_scenario
    check("C/包列表3项", sc.lst_packs.count() == 3, "")
    check("C/split状态", sc.split_state().get("scenario_split"), "")

    # 包 1：新设备上架，9 项 × by_vendor 6 厂商
    for row in range(sc.lst_packs.count()):
        sc.lst_packs.setCurrentRow(row)
        pid = sc.lst_packs.item(row).data(Qt.UserRole)
        pack = db.get_pack(pid)
        n_cards = sc.cards_lay.count() - 1          # 末尾 stretch
        check("C/卡片数-%s" % pack["title"], n_cards == len(pack["items"]),
              "%d vs %d" % (n_cards, len(pack["items"])))
        if pid == "new-device-onboarding":
            check("C/厂商下拉7项", sc.cmb_vendor.count() == 7,
                  str(sc.cmb_vendor.count()))
            # 全部厂商视图：by_vendor 步骤显示 multi 提示
            first = sc.cards_lay.itemAt(0).widget()
            texts = []
            for i in range(first.layout().count()):
                wd = first.layout().itemAt(i).widget()
                if wd is not None and hasattr(wd, "text"):
                    texts.append(wd.text())
            check("C/multi提示", any("多厂商步骤" in t for t in texts),
                  str(texts[:2]))
            # 切到 cisco：第 1 步应展开命令预览（CodeBlock 非空）
            idx = sc.cmb_vendor.findData("cisco")
            sc.cmb_vendor.setCurrentIndex(idx)
            first = sc.cards_lay.itemAt(0).widget()
            code = first.findChild(QPlainTextEdit)
            check("C/cisco展开命令", code is not None
                  and "ssh" in (code.toPlainText() or "").lower(),
                  (code.toPlainText()[:60] if code else "no widget"))
            # 切到 huawei：也应有命令
            idx = sc.cmb_vendor.findData("huawei")
            sc.cmb_vendor.setCurrentIndex(idx)
            first = sc.cards_lay.itemAt(0).widget()
            code2 = first.findChild(QPlainTextEdit)
            check("C/huawei展开命令", code2 is not None
                  and bool(code2.toPlainText().strip()),
                  (code2.toPlainText()[:60] if code2 else "no widget"))
            sc.cmb_vendor.setCurrentIndex(0)

    # 包 3：厂商视图切换 → 不匹配的 err 项 skip 置灰（disabled）
    for row in range(sc.lst_packs.count()):
        if sc.lst_packs.item(row).data(Qt.UserRole) == "emergency-rollback":
            sc.lst_packs.setCurrentRow(row)
            break
    idx = sc.cmb_vendor.findData("paloalto")
    check("C/paloalto在下拉", idx > 0, "")
    sc.cmb_vendor.setCurrentIndex(idx)
    disabled = 0
    for i in range(sc.cards_lay.count() - 1):
        wd = sc.cards_lay.itemAt(i).widget()
        if wd is not None and not wd.isEnabled():
            disabled += 1
    # 3 棵树不分厂商（enabled）；4 条 err 中 cisco/huawei/juniper 3 条应 skip
    check("C/skip置灰", disabled >= 3, str(disabled))
    sc.cmb_vendor.setCurrentIndex(0)


# ======================================================================
# [D] 跳转定位（复用 MainWindow.jump_to 三库机制）
# ======================================================================
def case_d():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import ui_main
    import ui_errorfix
    db = seeded_db()
    w = ui_main.MainWindow(db)
    sc = w.tab_scenario

    pack = db.get_pack("emergency-rollback")
    items = pack["items"]
    entry_ref = items[0]["by_vendor"]["cisco"]      # 配置回滚（cisco）
    tree_ref = items[2]["ref_id"]                   # fw-upgrade-broken
    err_ref = items[6]["ref_id"]                    # cisco 保存失败

    opened = {"selected": None}

    class FakeDictMgr:
        def __init__(self, db_, parent=None):
            pass
        def select_err(self, err_id):
            opened["selected"] = err_id
            return True
        def exec_(self):
            return 1
    orig_mgr = ui_errorfix.DictManagerDialog
    ui_errorfix.DictManagerDialog = FakeDictMgr
    try:
        sc.on_jump("entry", entry_ref)
        check("D/entry跳转", w.tabs.currentIndex() == 0
              and w.current_entry
              and w.current_entry.get("uuid") == entry_ref, "")
        sc.on_jump("tree", tree_ref)
        check("D/tree跳转", w.tabs.currentWidget() is w.tab_troubleshoot
              and w.tab_troubleshoot.current_tree
              and w.tab_troubleshoot.current_tree.get("tree_id") == tree_ref, "")
        sc.on_jump("err", err_ref)
        check("D/err跳转", opened["selected"] == err_ref, "")
    finally:
        ui_errorfix.DictManagerDialog = orig_mgr


# ======================================================================
# [E] 复制守卫 + splitter 记忆
# ======================================================================
def case_e():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import datetime
    import ui_main
    db = seeded_db()
    w = ui_main.MainWindow(db)
    sc = w.tab_scenario
    # 会话抑制两守卫的模态（守卫逻辑本身另由 smoke_p1/主套件覆盖，此处验证链路）
    w._ui_state["rollback_confirm_date"] = datetime.date.today().isoformat()
    w._ui_state["skeleton_copy_warned"] = True

    pack = db.get_pack("new-device-onboarding")
    # 选一条渲染后无缺失参数的 cisco 条目做复制正例
    # （SSH/口令类条目 password 无默认值属合法形态——由表单强制输入，
    #   场景包复制守卫会拦截，反例案正好复用该形态）
    import renderer as _rd
    entry, text, missing = None, "", ["_"]
    for it in pack["items"]:
        bvm = it.get("by_vendor") or {}
        cand = db.get_entry(bvm.get("cisco") or "")
        if not cand:
            continue
        t, m, _sp = _rd.render_entry(cand, {}, vendor=cand.get("vendor"))
        if not m and str(cand.get("exec_level") or "") != "skeleton":
            entry, text, missing = cand, t, m
            break
    check("E/正例无missing", entry is not None and not missing, str(missing))

    from PyQt5.QtWidgets import QApplication as QApp
    clip = QApp.clipboard()
    clip.setText("SENTINEL")
    sc.on_copy_entry(entry, text)
    got = clip.text()
    check("E/正常复制", got == (text or ""), got[:40] if isinstance(got, str) else got)

    # 反例：必填参数无默认值 → 拦截，剪贴板不变（SSH 条目的 password 形态）
    fake = {"uuid": "fake", "title": "必填无默认", "vendor": "cisco",
            "os_family": "ios", "category": "VLAN",
            "commands": "show vlan {{req1}}",
            "params": [{"name": "req1", "label": "必填", "required": True}],
            "rollback": "", "exec_level": "", "notes": ""}
    clip.setText("SENTINEL")
    sc.on_copy_entry(fake, "show vlan {{req1}}")
    check("E/必填拦截", clip.text() == "SENTINEL", clip.text()[:40])

    # splitter 记忆往返（offscreen 下子 Tab 拿不到真实布局，按「pending 意图」
    # 口径断言——与 smoke_panel_dashboard 同款：save 落盘 + 新窗恢复 pending）
    state = sc.split_state()
    check("E/split键存在", state.get("scenario_split") is not None, str(state))
    good = {"scenario_split": [280, 1000]}
    sc.restore_split_state(good)
    check("E/split-pending记录", sc._pending_split == [280, 1000],
          str(sc._pending_split))
    sc._pending_split = None
    w._save_ui_state()
    import json as _json
    with open(__import__("theme").state_file_path(), "r", encoding="utf-8") as fp:
        saved = _json.load(fp)
    check("E/split落盘", "scenario_split" in saved, str(list(saved.keys())))
    w.deleteLater()
    w2 = ui_main.MainWindow(db)
    sc2 = w2.tab_scenario
    # offscreen 下 w 的 scenario splitter 从未布局，落盘值是退化的 [0,0]；
    # 新窗 restore 按纪律校验拒绝坏值回退默认（pending=None）——两态都算链路通
    saved_sc = saved["scenario_split"]
    valid = (isinstance(saved_sc, list) and len(saved_sc) == 2
             and all(int(v) >= m for v, m in zip(saved_sc, (280, 420))))
    if valid:
        check("E/split新窗恢复", sc2._pending_split == saved_sc,
              str(sc2._pending_split))
    else:
        check("E/split新窗恢复", sc2._pending_split is None,
              "坏值 %s 被拒绝回退默认（pending=%r）" % (saved_sc, sc2._pending_split))
    w2.deleteLater()


def main():
    import traceback
    cases = [("A", case_a), ("B", case_b), ("C", case_c), ("D", case_d), ("E", case_e)]
    for tag, fn in cases:
        try:
            fn()
            print("[case %s] done" % tag)
        except Exception as exc:
            traceback.print_exc()
            check("%s/未捕获异常" % tag, False, str(exc))
    print("=" * 66)
    fails = 0
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print("[%s] %s %s" % (mark, name, ("- " + str(detail)[:80]) if (detail and not ok) else ""))
    print("=" * 66)
    print("smoke_scenarios：%d 项，FAIL %d" % (len(RESULTS), fails))
    if _se.verify_main_db_untouched():
        print("[SMOKE-ENV] 主库零接触实证：指纹前后一致")
    else:
        fails += 1
        print("[SMOKE-ENV] FAIL 主库指纹异常")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
