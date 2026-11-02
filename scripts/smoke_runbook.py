# -*- coding: utf-8 -*-
"""
smoke_runbook.py —— 操作单生成器（Runbook）冒烟

覆盖九案（全部 offscreen + :memory: 种子库，主库零接触）：
    [A] 数据模型：new_runbook 默认 / validate / save→load 回读字段无损 /
        normalize 坏值兜底
    [B] rollback 实时带出（裁决③）：变更类 resolve 出 rollback 文本；
        查询类免 rollback；变更类缺 rollback → missing_rollback 标记
    [C] 导出 HTML：token 同源校验通过；零外部请求（无 <script / http(s):// /
        url()）；A4 @page、勾选框、回退汇总页、条目失效警告、缺回退红字俱在
    [D] 参数必填校验：ParamFormWidget errors（复用生成器必填口径）
    [E] 操作单 Tab：构造 / 程序化加步骤 / 预览含回退段 / 缺回退红标可见 /
        split_state 记忆 / 保存重开字段无损
    [F] 变更类缺 rollback 红标（Tab 层 lbl_rbmiss 可见性）
    [G] 场景包一键生成（P 批）：三包 × 厂商的步数与顺序 / 参数预填 /
        变更类回退在列 / 非命令引用记账 / 未选厂商与引用失效兜底
    [H] 六段式阶段模板（P 批）：启发式标注 / 不覆盖人工标注 / HTML 分组 /
        取消清空 / 旧 v1 文件兼容加载 / 脏值兜底
    [I] Tab 层分组与一键生成落点：段头行与行映射 / load_from_pack 草稿 /
        阶段下拉自动开模板 / 关闭分组回平铺

用法：python scripts/smoke_runbook.py   （EXIT=0 全过；需 PyQt5）
"""
import os
import sys
import json
import tempfile
import shutil

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 断言

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


# ======================================================================
# 公共：种子库（:memory:）+ 造数
# ======================================================================
def seeded_db():
    import db as dbmod
    d = dbmod.Database(":memory:")
    d.import_seed_dir()
    return d


def insert_raw_entry(d, uuid, title, commands, rollback, vendor="huawei",
                     os_family="vrp8", params="[]"):
    """直接往 :memory: 测试库插一条（仅测试库，主库操作禁手工 SQL 的纪律不适用）"""
    d.conn.execute(
        "INSERT INTO entries (uuid, platform, vendor, os_family, title, commands, "
        "rollback, params, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (uuid, "network", vendor, os_family, title, commands, rollback, params,
         "2026-10-12 00:00:00", "2026-10-12 00:00:00"))
    d.conn.commit()


def sample_runbook():
    import runbook_export as R
    data = R.new_runbook()
    data["meta"].update({
        "title": "冒烟测试操作单",
        "device_model": "S5720",
        "vendor": "huawei", "os_family": "vrp8",
        "window_start": "2026-10-20 00:00", "window_end": "2026-10-20 06:00",
        "operator": "冒烟", "reviewer": "复核员",
        "rollback_point": "已备份 vrpcfg.zip",
    })
    data["steps"] = [
        {"step_no": 1, "entry_uuid": "rb-query-1", "title_snapshot": "查询条目",
         "vendor_snapshot": "huawei", "params": {}, "expect": "能看到 VLAN 列表",
         "note": "", "checked": False, "phase": ""},
        {"step_no": 2, "entry_uuid": "rb-change-1", "title_snapshot": "变更条目",
         "vendor_snapshot": "huawei", "params": {"vlan_id": "100"},
         "expect": "display vlan 100 可见", "note": "窗口开始后执行", "checked": False,
         "phase": ""},
        {"step_no": 3, "entry_uuid": "rb-dead-1", "title_snapshot": "失效条目",
         "vendor_snapshot": "huawei", "params": {}, "expect": "", "note": "",
         "checked": False, "phase": ""},
    ]
    return data


def build_test_db():
    """种子库 + 三条定制条目：查询 / 变更(有回退) / 变更(无回退)"""
    d = seeded_db()
    insert_raw_entry(d, "rb-query-1", "冒烟·查询VLAN", "display vlan\n", "")
    insert_raw_entry(d, "rb-change-1", "冒烟·创建VLAN",
                     "system-view\nvlan {{vlan_id}}\nquit\n",
                     "system-view\nundo vlan {{vlan_id}}\nquit\n",
                     params=json.dumps([{"name": "vlan_id", "type": "int",
                                         "range": "1-4094", "required": True,
                                         "default": "", "description": "VLAN 编号"}]))
    insert_raw_entry(d, "rb-change-norb", "冒烟·变更无回退", "system-view\nvlan 300\nquit\n", "")
    return d


# ======================================================================
# [A] 数据模型
# ======================================================================
def case_a():
    import runbook_export as R
    data = sample_runbook()
    check("A1 new_runbook 结构", R.validate_runbook(data) == [], str(R.validate_runbook(data)))

    tmp = tempfile.mkdtemp(prefix="ntbx_smoke_rb_")
    path = os.path.join(tmp, "rb.json")
    err = R.save_runbook(data, path)
    check("A2 save_runbook 成功", err == "", err)
    loaded, err2 = R.load_runbook(path)
    check("A3 load_runbook 无错", err2 == "" and loaded is not None, err2)
    same_meta = all(str(loaded["meta"][k]) == str(data["meta"][k]) for k in data["meta"])
    check("A4 meta 回读无损", same_meta,
          json.dumps({k: (loaded["meta"][k], data["meta"][k])
                      for k in data["meta"] if str(loaded["meta"][k]) != str(data["meta"][k])},
                     ensure_ascii=False))
    same_steps = len(loaded["steps"]) == len(data["steps"]) and all(
        loaded["steps"][i] == data["steps"][i] for i in range(len(data["steps"])))
    check("A5 steps 回读无损", same_steps)

    bad, err3 = R.load_runbook(os.path.join(tmp, "nope.json"))
    check("A6 不存在文件防御（isfile）", bad is None and "不存在" in err3, err3)
    norm = R.normalize_runbook({"schema": 1, "meta": {"title": "x"},
                                "steps": [{"entry_uuid": "u"}, "垃圾", {"params": []}]})
    issues = R.validate_runbook(norm)
    # 缺 entry_uuid 的步骤 normalize 直接丢弃（不可回库对账）
    check("A7 normalize 坏值兜底", len(norm["steps"]) == 1 and issues == [],
          json.dumps({"n": len(norm["steps"]), "issues": issues}, ensure_ascii=False))
    shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# [B] rollback 实时带出（裁决③）
# ======================================================================
def case_b():
    import runbook_export as R
    d = build_test_db()
    data = sample_runbook()
    data["steps"].append(
        {"step_no": 4, "entry_uuid": "rb-change-norb", "title_snapshot": "变更无回退",
         "vendor_snapshot": "huawei", "params": {}, "expect": "", "note": "",
         "checked": False, "phase": ""})
    res = R.resolve_steps(data, d)
    by_uuid = {r["step"]["entry_uuid"]: r for r in res}

    q = by_uuid["rb-query-1"]
    check("B1 查询类判定", q["category"] == "query", str(q["category"]))
    check("B2 查询类不要求 rollback", q["rollback"] == "" and not q["missing_rollback"])

    c = by_uuid["rb-change-1"]
    check("B3 变更类判定", c["category"] == "change", str(c["category"]))
    check("B4 rollback 实时取库", "undo vlan" in c["rollback"], repr(c["rollback"][:40]))
    check("B5 参数已填入渲染", "vlan 100" in c["rendered"], repr(c["rendered"]))

    n = by_uuid["rb-change-norb"]
    check("B6 变更类缺 rollback 标记", n["category"] == "change" and n["missing_rollback"])

    dead = by_uuid["rb-dead-1"]
    check("B7 失效条目标记", dead["status"] == "missing" and dead["category"] is None)


# ======================================================================
# [C] 导出 HTML：token / 零外部请求 / 版式要素
# ======================================================================
def case_c():
    import runbook_export as R
    check("C1 打印 token 同源校验", R.verify_print_tokens_against_theme() == [],
          "; ".join(R.verify_print_tokens_against_theme()))
    d = build_test_db()
    data = sample_runbook()
    data["steps"].append(
        {"step_no": 4, "entry_uuid": "rb-change-norb", "title_snapshot": "变更无回退",
         "vendor_snapshot": "huawei", "params": {}, "expect": "", "note": "",
         "checked": False, "phase": ""})
    html = R.build_html(data, d)

    check("C2 零 script 标签", "<script" not in html.lower())
    check("C3 零外链 http(s)", "http://" not in html and "https://" not in html)
    check("C4 零 url() 资源引用", "url(" not in html.lower())
    check("C5 A4 @page", "@page" in html and "A4" in html)
    check("C6 勾选框元素", 'class="cb"' in html)
    check("C7 回退汇总页", "回退方案汇总" in html and "rb-page" in html)
    check("C8 回退页含 undo 命令", "undo vlan" in html)
    check("C9 失效条目警告", "条目已不在当前命令库" in html)
    check("C10 缺回退红字告警", "缺少回退方案" in html)
    check("C11 复核确认页", "复核确认" in html and "执行人" in html)
    check("C12 预期回显要点进单", "预期回显要点" in html and "display vlan 100 可见" in html)
    check("C13 元信息进单", "S5720" in html and "已备份 vrpcfg.zip" in html)


# ======================================================================
# [D] 参数必填校验（ParamFormWidget 复用口径）
# ======================================================================
def case_d():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import renderer
    from ui_generator import ParamFormWidget
    # 真实管线同款：传入前过 normalize_spec（type/range → validate 推导）。
    # 注：type=int+range 会生成 QSpinBox（永不为空、越界被钳制），必填/越界校验
    # 的真实触发路径是 QLineEdit 编辑器（无 type 或 validate= 规则型）——按此构造：
    form = ParamFormWidget(
        [renderer.normalize_spec({"name": "ifname", "required": True, "label": "接口名",
                                  "description": "接口名，必填"}),
         renderer.normalize_spec({"name": "vlan_id", "validate": "vlan", "required": True,
                                  "label": "VLAN", "description": "VLAN 编号 1-4094"})],
        vendor="huawei", os_family="vrp8")
    errs = form.errors()
    check("D1 必填空值报错", "ifname" in errs and "vlan_id" in errs, str(errs))
    form.set_values({"ifname": "GE0/0/1", "vlan_id": "100"})
    check("D2 填值后通过", form.errors() == {}, str(form.errors()))
    form.set_values({"vlan_id": "99999"})
    check("D3 越界值报错（vlan 1-4094）", "vlan_id" in form.errors(), str(form.errors()))


# ======================================================================
# [E]/[F] 操作单 Tab（offscreen 构造 + 程序化步骤 + 保存重开）
# ======================================================================
def case_e():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    from ui_runbook import RunbookTab
    import runbook_export as R

    d = build_test_db()
    tab = RunbookTab(d, None)     # main=None：offscreen 独立构造
    check("E1 Tab 构造", tab.data["schema"] == 2 and len(tab.data["steps"]) == 0,
          str(tab.data["schema"]))
    state = tab.split_state()
    check("E2 split_state 记忆 key", "runbook_split" in state, str(state))

    # 程序化加步骤（不走模态面板）：查询 + 变更(有回退) + 变更(无回退)
    tab.data["meta"]["title"] = "Tab 冒烟单"
    for uuid, title, params in (
            ("rb-query-1", "查询条目", {}),
            ("rb-change-1", "变更条目", {"vlan_id": "100"}),
            ("rb-change-norb", "变更无回退", {})):
        tab.data["steps"].append({"step_no": len(tab.data["steps"]) + 1,
                                  "entry_uuid": uuid, "title_snapshot": title,
                                  "vendor_snapshot": "huawei", "params": params,
                                  "expect": "预期要点", "note": "", "checked": False,
                                  "phase": ""})
    tab._reload_steps(keep=0)
    tab._show_step(0)
    check("E3 列表条数", tab.lst_steps.count() == 3, str(tab.lst_steps.count()))

    tab.lst_steps.setCurrentRow(1)   # 变更条目（有回退）
    preview = tab.txt_preview.toPlainText()
    check("E4 预览含渲染命令", "vlan 100" in preview, repr(preview[:60]))
    check("E5 变更类预览自动附回退段", "回退方案" in preview and "undo vlan" in preview)
    check("E6 有回退不亮红标", not getattr(tab, "_rbmiss_active", None))

    tab.lst_steps.setCurrentRow(2)   # 变更条目（无回退）
    # offscreen 窗口未 show → isVisible() 恒 False，用 _rbmiss_active 旗标断言意图
    check("F1 缺回退亮红标", tab._rbmiss_active is True)
    tab.lst_steps.setCurrentRow(0)   # 查询类
    check("F2 查询类不亮红标", not tab._rbmiss_active)

    # 保存 → 重开 → 字段无损（含 checked / params / expect）
    tmp = tempfile.mkdtemp(prefix="ntbx_smoke_rb_")
    path = os.path.join(tmp, "tab_rb.json")
    tab.lst_steps.setCurrentRow(1)
    tab.chk_done.setChecked(True)
    check("E7 flush 后 data 含 checked", tab.data["steps"][1].get("checked") is True)
    ok = tab._save_to(path)
    data_before = json.loads(json.dumps(tab.data, ensure_ascii=False))
    tab.data = R.new_runbook()
    loaded, err = R.load_runbook(path)
    check("E8 重开无错", err == "" and loaded is not None, err)
    same = all(loaded["steps"][i] == data_before["steps"][i]
               for i in range(len(data_before["steps"])))
    check("E9 保存重开 steps 无损", same)
    check("E10 meta 无损", loaded["meta"]["title"] == "Tab 冒烟单")
    shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# [G] 场景包一键生成（P 批 2026-11-02）
# ======================================================================
PACK_CASES = (
    ("new-device-onboarding", "huawei", 9),
    ("pre-cutover-check", "huawei", 10),
    ("emergency-rollback", "cisco", 2),
)


def pack_entry_titles(d, pack, vendor):
    """独立复算「该厂商视图下应生成的步骤标题」（按 seq 排序，不复用被测函数）"""
    items = sorted([it for it in (pack.get("items") or []) if it.get("ref_type") == "entry"],
                   key=lambda it: it.get("seq"))
    titles = []
    for it in items:
        bvm = it.get("by_vendor") or {}
        uid = bvm.get(vendor) if bvm else it.get("ref_id")
        entry = d.get_entry(uid) if uid else None
        if entry:
            titles.append(entry.get("title"))
    return titles


def case_g():
    import copy
    import runbook_export as R
    d = build_test_db()

    for pid, vendor, expect_n in PACK_CASES:
        pack = d.get_pack(pid)
        data, rep = R.build_from_pack(pack, d, vendor)
        got = [s["title_snapshot"] for s in data["steps"]]
        want = pack_entry_titles(d, pack, vendor)
        check("G1 %s/%s 步数与 seq 顺序" % (pid, vendor),
              rep["added"] == expect_n and got == want,
              json.dumps({"added": rep["added"], "expect": expect_n,
                          "顺序一致": got == want}, ensure_ascii=False))

    pack = d.get_pack("new-device-onboarding")
    data, rep = R.build_from_pack(pack, d, "huawei")
    p1 = data["steps"][0].get("params") or {}
    p5 = data["steps"][4].get("params") or {}
    check("G2 参数按条目默认值预填",
          p1.get("hostname") == "SW-CORE-01" and p5.get("vlan_id") == "10",
          json.dumps({"step1": p1, "step5": p5}, ensure_ascii=False))
    check("G3 无默认值必填项留空（不臆造）",
          "password" not in p1 and p1.get("username") == "netadmin",
          json.dumps(p1, ensure_ascii=False))

    res = R.resolve_steps(data, d)
    change_rows = [r for r in res if r["category"] == "change"]
    check("G4 变更类 rollback 在列（实时取库）",
          bool(change_rows) and all(r["rollback"].strip() for r in change_rows),
          "%d 变更步，缺 %d" % (len(change_rows),
                                sum(1 for r in change_rows if not r["rollback"].strip())))
    check("G5 rollback 不落盘（schema 纪律）",
          all("rollback" not in s for s in data["steps"]))

    pack3 = d.get_pack("emergency-rollback")
    d3, r3 = R.build_from_pack(pack3, d, "cisco")
    check("G6 非命令引用记账（树/报错）",
          len(r3["skipped"]) == 8
          and all("非命令条目" in s["reason"] for s in r3["skipped"])
          and r3["missing"] == [],
          json.dumps({"skipped": len(r3["skipped"]),
                      "added": r3["added"]}, ensure_ascii=False))
    check("G7 包内步骤说明进备注",
          any("第一动作" in (s.get("note") or "") for s in d3["steps"]),
          json.dumps([(s.get("note") or "")[:10] for s in d3["steps"]], ensure_ascii=False))

    data0, rep0 = R.build_from_pack(pack, d, "")
    check("G8 未选厂商不臆测",
          rep0["added"] == 0 and len(rep0["skipped"]) == 9
          and all("未指定厂商" in s["reason"] for s in rep0["skipped"]),
          json.dumps({"added": rep0["added"], "skipped": len(rep0["skipped"])}))

    _d4, r4 = R.build_from_pack(d.get_pack("pre-cutover-check"), d, "zte")
    check("G9 厂商不符跳过", r4["added"] == 0 and len(r4["skipped"]) == 10,
          json.dumps({"added": r4["added"], "skipped": len(r4["skipped"])}))

    broken = copy.deepcopy(pack)
    broken["items"][0]["by_vendor"]["huawei"] = "dead-uuid-not-in-db"
    _d5, r5 = R.build_from_pack(broken, d, "huawei")
    check("G10 引用失效记账不崩", r5["added"] == 8 and len(r5["missing"]) == 1,
          json.dumps({"added": r5["added"], "missing": len(r5["missing"])}))


# ======================================================================
# [H] 六段式阶段模板（P 批）
# ======================================================================
def case_h():
    import runbook_export as R
    d = build_test_db()
    pack = d.get_pack("pre-cutover-check")
    data, _rep = R.build_from_pack(pack, d, "huawei")

    n = R.apply_six_template(data, d)
    phases = [s["phase"] for s in data["steps"]]
    check("H1 模板开关 + 启发式标注",
          data["meta"]["template"] == "six" and n == 10
          and phases[0] == "pre" and "change" in phases and "validation" in phases,
          json.dumps(phases))

    data["steps"][0]["phase"] = "post"
    R.apply_six_template(data, d)
    check("H2 不覆盖人工标注", data["steps"][0]["phase"] == "post",
          data["steps"][0]["phase"])

    data["meta"]["abort_criteria"] = "变更后 5 分钟内 BGP 邻居未全部 Established 即回退"
    tmp = tempfile.mkdtemp(prefix="ntbx_smoke_rb_")
    path = os.path.join(tmp, "six.json")
    R.save_runbook(data, path)
    loaded, err = R.load_runbook(path)
    check("H3 六段式保存重开无损",
          err == "" and loaded["meta"]["template"] == "six"
          and loaded["meta"]["abort_criteria"] == data["meta"]["abort_criteria"]
          and [s["phase"] for s in loaded["steps"]] == [s["phase"] for s in data["steps"]]
          and loaded["meta"]["source_pack"] == "pre-cutover-check", err)

    html = R.build_html(data, d)
    six = ("Pre-check", "Change", "Validation", "回退条件", "Rollback", "Post-check")
    check("H4 HTML 六段俱全", all(x in html for x in six) and '<h3 class="phase">' in html,
          str([x for x in six if x not in html]))
    check("H5 回退条件文本进单", "BGP 邻居未全部 Established" in html)
    check("H6 分组提示且零外链",
          '<div class="grp-note">' in html and "http://" not in html
          and "<script" not in html.lower())

    flat = R.build_from_pack(pack, d, "huawei")[0]
    flat_html = R.build_html(flat, d)
    check("H7 平铺单无阶段段头（旧口径不变）",
          '<h3 class="phase">' not in flat_html
          and '<div class="grp-note">' not in flat_html)

    R.clear_six_template(data)
    check("H8 取消模板清空阶段",
          data["meta"]["template"] == "" and all(s["phase"] == "" for s in data["steps"]))

    v1 = {"schema": 1, "meta": {"title": "旧版操作单", "vendor": "huawei"},
          "steps": [{"step_no": 1, "entry_uuid": "rb-query-1", "title_snapshot": "旧步骤",
                     "vendor_snapshot": "huawei", "params": {}, "expect": "旧期望",
                     "note": "", "checked": True}]}
    v1path = os.path.join(tmp, "v1.json")
    with open(v1path, "w", encoding="utf-8") as fp:
        json.dump(v1, fp, ensure_ascii=False)
    v1data, v1err = R.load_runbook(v1path)
    check("H9 旧 v1 文件兼容加载（无警告）",
          v1err == "" and v1data["schema"] == 2
          and v1data["meta"]["template"] == "" and v1data["meta"]["abort_criteria"] == ""
          and v1data["steps"][0]["phase"] == "" and v1data["steps"][0]["checked"] is True,
          repr(v1err))
    v2path = os.path.join(tmp, "v1_resaved.json")
    R.save_runbook(v1data, v2path)
    back, backerr = R.load_runbook(v2path)
    check("H10 v1 → v2 另存无损",
          backerr == "" and back["schema"] == 2 and back["steps"] == v1data["steps"],
          repr(backerr))

    dirty = R.normalize_runbook({"schema": 1, "meta": {"template": "weird"},
                                 "steps": [{"entry_uuid": "u1", "phase": "not-a-phase"}]})
    check("H11 脏值兜底（模板/阶段）",
          dirty["meta"]["template"] == "" and dirty["steps"][0]["phase"] == ""
          and R.validate_runbook(dirty) == [],
          json.dumps({"tpl": dirty["meta"]["template"], "ph": dirty["steps"][0]["phase"]}))
    shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# [I] Tab 层：分组列表 + 一键生成落点
# ======================================================================
def case_i():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    from ui_runbook import RunbookTab
    import runbook_export as R

    d = build_test_db()
    tab = RunbookTab(d, None)
    ok, msg = tab.load_from_pack(d.get_pack("pre-cutover-check"), "huawei")
    check("I1 一键生成草稿（未保存态）",
          ok and len(tab.data["steps"]) == 10 and tab.dirty and tab.current_path is None, msg)
    check("I2 单头带出包信息",
          tab.data["meta"]["title"] == "割接前检查"
          and tab.data["meta"]["vendor"] == "huawei"
          and tab.data["meta"]["source_pack"] == "pre-cutover-check",
          json.dumps({k: tab.data["meta"][k] for k in ("title", "vendor", "source_pack")},
                     ensure_ascii=False))
    check("I3 平铺行映射恒等",
          tab.lst_steps.count() == 10 and tab._row_map == list(range(10)),
          json.dumps(tab._row_map))

    tab.btn_six.setChecked(True)
    headers = sum(1 for x in tab._row_map if x is None)
    check("I4 分组段头行",
          headers == 3 and tab.lst_steps.count() == 13,
          json.dumps({"headers": headers, "rows": tab.lst_steps.count()}))

    hrow = tab._row_map.index(None)
    before = tab._cur
    tab.lst_steps.setCurrentRow(hrow)
    check("I5 段头不可选中（当前步不变）", tab._cur == before,
          "cur=%r before=%r row=%r" % (tab._cur, before, hrow))

    tab.btn_six.setChecked(False)
    check("I6 关闭分组回平铺",
          tab._row_map == list(range(10))
          and all(s["phase"] == "" for s in tab.data["steps"]),
          json.dumps(tab._row_map))

    tab.lst_steps.setCurrentRow(2)
    tab.cmb_phase.setCurrentIndex(tab.cmb_phase.findData("rollback"))
    check("I7 选阶段自动开分组",
          tab.data["meta"]["template"] == "six"
          and tab.data["steps"][2]["phase"] == "rollback"
          and tab.lst_steps.count() > 10,
          json.dumps({"tpl": tab.data["meta"]["template"],
                      "phase": tab.data["steps"][2]["phase"],
                      "rows": tab.lst_steps.count()}))

    tmp = tempfile.mkdtemp(prefix="ntbx_smoke_rb_")
    path = os.path.join(tmp, "pack_draft.json")
    tab.ed_meta["abort_criteria"].setText("丢包 > 1% 即回退")
    tab._save_to(path)
    loaded, err = R.load_runbook(path)
    check("I8 草稿保存重开无损",
          err == "" and len(loaded["steps"]) == 10
          and loaded["meta"]["abort_criteria"] == "丢包 > 1% 即回退"
          and loaded["meta"]["template"] == "six", err)
    shutil.rmtree(tmp, ignore_errors=True)

    tab2 = RunbookTab(d, None)
    ok2, _m2 = tab2.load_from_pack(d.get_pack("new-device-onboarding"), "huawei")
    vals = dict(tab2._form.values()) if getattr(tab2, "_form", None) is not None else {}
    check("I9 参数预填进表单控件",
          ok2 and str(vals.get("hostname")) == "SW-CORE-01",
          json.dumps(vals, ensure_ascii=False))


def main():
    print("=" * 68)
    print("smoke_runbook —— 操作单生成器冒烟")
    print("=" * 68)
    for fn in (case_a, case_b, case_c, case_d, case_e, case_g, case_h, case_i):
        fn()
    fails = [r for r in RESULTS if not r[1]]
    print("-" * 68)
    for name, ok, detail in RESULTS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             ("  ← " + detail) if (detail and not ok) else ""))
    print("-" * 68)
    print("合计 %d 项，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    untouched = _se.verify_main_db_untouched()
    print("主库零接触断言：%s" % ("通过" if untouched else "失败"))
    return 0 if (not fails and untouched) else 1


if __name__ == "__main__":
    sys.exit(main())
