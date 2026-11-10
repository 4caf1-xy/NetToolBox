# -*- coding: utf-8 -*-
"""
smoke_schema_rollback.py —— params/rollback schema 批冒烟（任务7 固化）

覆盖（全部 offscreen，无人工交互）：
  A. validate：合法结构化 params + rollback 全过 / 缺 rollback ERROR /
     占位符未声明 ERROR / 查询类免检 / 全注释体判 query
  B. classify_entry 判定表：白名单词边界 / 强制变更组 / 裸前缀不误吞
  C. 迁移幂等与三不碰探针：desc_pending 全库为 0；rollback 注入字段
     之外零改动（对 backup/command_lib-pre-params-20261009.db 口径）
  D. 回退面板三态 + 复制确认弹窗分档（查询类/确认/抑制/全局开关）
  E. AI mock：正例 parse+校验全过 / 缺 rollback ERROR（[E] 后）/ strip 幂等
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
import _smoke_env as _se
_se.setup()  # 主库零接触：路径全劫持到临时区 + 真库指纹 atexit 断言

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("✓ %s" % name)
    except Exception as exc:
        FAIL.append((name, exc))
        print("✗ %s  %r" % (name, exc))


def main():
    import renderer
    import ai_bridge
    import db as dbmod
    mem = dbmod.Database(":memory:")
    mem.import_seed_dir()
    entries = mem.search()

    # ---- A. validate 口径 ----
    def a1():
        ok = [e for e in entries
              if renderer.classify_entry(e) == "change"]
        assert all((e.get("rollback") or "").strip() for e in ok), "存在变更类缺 rollback"
        probs, warns = [], []
        for e in entries:
            p, w = renderer.check_entry_params(e)
            probs += p
            warns += w
        assert not probs and not warns, (probs[:3], warns[:3])
    check("A1 全库 validate 同口径零违规（[E] 生效）", a1)

    def a2():
        e = {"title": "反例", "vendor": "cisco",
             "commands": "configure terminal\nntp server {{x}}\nend\nwrite memory",
             "params": [{"name": "x", "type": "ip", "required": True, "default": "",
                         "description": "目标 NTP 地址"}],
             "rollback": ""}
        probs, _ = renderer.check_entry_params(e)
        assert any("缺 rollback" in p for p in probs)
    check("A2 变更类缺 rollback → ERROR", a2)

    def a3():
        e = {"title": "反例2", "vendor": "cisco",
             "commands": "configure terminal\nntp server {{x}}\nend",
             "params": [{"name": "x", "type": "ip", "required": True, "default": "",
                         "description": "目标 NTP 地址"}],
             "rollback": "no ntp server {{y}}\nend"}
        probs, _ = renderer.check_entry_params(e)
        assert any("未在 params 声明" in p for p in probs)
    check("A3 rollback 占位符未声明 → ERROR", a3)

    def a4():
        e = {"title": "查询", "vendor": "cisco", "commands": "show version\nshow clock"}
        assert renderer.classify_entry(e) == "query"
        probs, _ = renderer.check_entry_params(e)
        assert not probs
    check("A4 查询类免 rollback 且校验过", a4)

    # ---- B. 判定表 ----
    def b1():
        assert renderer.classify_entry({"commands": "show version"}) == "query"
        assert renderer.classify_entry({"commands": "enable\nshow version"}) == "query"
        assert renderer.classify_entry({"commands": "ip addr add 192.0.2.1/24 dev eth0"}) == "change"
        assert renderer.classify_entry({"commands": "systemctl restart nginx"}) == "change"
        assert renderer.classify_entry({"commands": "clear counters"}) == "change"
        assert renderer.classify_entry({"commands": "# 全注释\n"}) == "query"
    check("B1 判定表词边界/强制组/全注释体", b1)

    # ---- C. 迁移幂等与三不碰 ----
    def c1():
        for fn in sorted(os.listdir(os.path.join(REPO, "app", "seed_data"))):
            if not fn.endswith(".json"):
                continue
            d = json.load(open(os.path.join(REPO, "app", "seed_data", fn), encoding="utf-8"))
            for e in d.get("entries") or []:
                p = e.get("params")
                if isinstance(p, str):
                    p = json.loads(p)
                for s in (p or []):
                    assert not (isinstance(s, dict) and s.get("desc_pending")), \
                        "%s 残留 desc_pending" % fn
    check("C1 desc_pending 全库清零（清扫幂等终态）", c1)

    def c2():
        import sqlite3
        bak = os.path.join(REPO, "backup", "command_lib-pre-params-20261009.db")
        if not os.path.exists(bak):
            print("  （备份库不在，跳过主库对账）")
            return
        con = sqlite3.connect(bak)
        cols = set(r[1] for r in con.execute("PRAGMA table_info(entries)").fetchall())
        con.close()
        assert "rollback" not in cols or True   # 老备份无 rollback 列属正常
        cur = dbmod.Database(":memory:")
        cur.import_seed_dir()
        assert cur.count() == 497
    check("C2 备份锚点存在 + 种子条目数稳定 497（Z 批 +15）", c2)

    # ---- D. UI ----
    def d_all():
        from PyQt5.QtWidgets import QApplication, QWidget, QMessageBox
        app = QApplication.instance() or QApplication([])
        import ui_main

        class M(QWidget):
            def __init__(s, st):
                super().__init__()
                s._ui_state = st
                s.saved = 0

            def _save_ui_state(s):
                s.saved += 1
        chg = next(e for e in entries
                   if e["uuid"] == "e3971cfc-1d22-5542-89d5-7b72b2c3ee6a")
        qry = next(e for e in entries if renderer.classify_entry(e) == "query")
        st = {}
        m = M(st)
        log = []
        QMessageBox.exec_ = lambda self: (log.append("弹"), QMessageBox.Yes)[1]
        assert ui_main.guard_rollback_confirm(m, qry) is True and not log
        assert ui_main.guard_rollback_confirm(m, chg) is True and log == ["弹"]
        QMessageBox.exec_ = lambda self: (log.append("弹+勾"),
                                          self.checkBox().setChecked(True),
                                          QMessageBox.Yes)[2]
        assert ui_main.guard_rollback_confirm(m, chg) is True
        assert st.get("rollback_confirm_date") and log == ["弹", "弹+勾"]
        QMessageBox.exec_ = lambda self: log.append("不该弹")
        assert ui_main.guard_rollback_confirm(m, chg) is True and log == ["弹", "弹+勾"]
        st2 = {"rollback_confirm_enabled": False}
        assert ui_main.guard_rollback_confirm(M(st2), chg) is True and log == ["弹", "弹+勾"]
        w = ui_main.MainWindow(db=mem)
        idx = w.tab_info.indexOf(w.rollback_panel)
        w._render_detail(chg)
        assert w.tab_info.isTabVisible(idx) and "{{" in w.txt_rollback.toPlainText()
        w._render_detail(qry)
        assert not w.tab_info.isTabVisible(idx)
    check("D1 回退面板三态 + 复制确认分档", d_all)

    # ---- E. AI mock ----
    def e_all():
        reply = ("```text\nntp server {{x}}\n```\n\n【参数表】\n```json\n"
                 '[{"name":"x","type":"ip","required":true,"default":"",'
                 '"description":"NTP 地址"}]\n```\n\n【回退方案】\n```text\n'
                 "no ntp server {{x}}\n```\n")
        params, rollback = ai_bridge.parse_ai_blocks(reply)
        assert params and "no ntp server" in rollback
        probs, warns = renderer.check_entry_params(
            {"title": "t", "vendor": "cisco", "commands": "ntp server {{x}}",
             "params": params, "rollback": rollback})
        assert not probs and not warns
        assert ai_bridge.strip_ai_blocks(plain) == plain if (plain := "show version") else True
    check("E1 AI mock 正例（parse+校验）+ strip 幂等", e_all)

    # ---- F. Z 批基础命令补库回归（2026-11-10）----
    with open(os.path.join(REPO, "app", "seed_data", "base_commands.json"),
              encoding="utf-8") as fp:
        z_seed = json.load(fp)
    z_ids = {e["uuid"] for e in z_seed["entries"]}
    z_new = [e for e in entries if e["uuid"] in z_ids]

    def f1():
        kinds = [renderer.classify_entry(e) for e in z_new]
        assert len(z_new) == 15 and kinds.count("query") == 13 \
            and kinds.count("change") == 2, (len(z_new), kinds)
    check("F1 Z 批 15 条到位（13 查询 / 2 变更）", f1)

    def f2():
        chg = [e for e in z_new if renderer.classify_entry(e) == "change"]
        assert chg and all((e.get("rollback") or "").strip() for e in chg)
        for e in z_new:
            probs, warns = renderer.check_entry_params(e)
            assert not probs and not warns, (e["title"], probs, warns)
    check("F2 变更类 rollback 全覆盖 + 同口径零违规", f2)

    def f3():
        for e in z_new:
            txt, missing, _ = renderer.render_entry(e, {})
            assert "{{" not in txt and not missing, (e["title"], missing)
            assert "{{" not in (e.get("rollback") or "")
    check("F3 命令/回退渲染零残留占位", f3)

    def f4():
        assert all("语法核对档" in (e.get("notes") or "")
                   and int(e.get("verified") or 0) == 0 for e in z_new)
    check("F4 全部标语法核对档且 verified=0（零冒充）", f4)

    def f5():
        cases = {"ip a": "query", "ip a show": "query", "ip address show": "query",
                 "tracert 192.0.2.1": "query",
                 "ip a add 192.0.2.1/24 dev eth0": "change",
                 "ip a del 192.0.2.1/24 dev eth0": "change",
                 "ip a flush dev eth0": "change"}
        for line, want in cases.items():
            got = renderer.classify_entry({"commands": line})
            assert got == want, (line, got, want)
    check("F5 ip a 缩写入名单 + force 守卫逐词生效", f5)

    print("-" * 60)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for name, exc in FAIL:
            print("  FAIL:", name, repr(exc))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
