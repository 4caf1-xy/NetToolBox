# -*- coding: utf-8 -*-
"""
smoke_p1_fixes.py —— P1 修复永久回归（审计批次 2026-10-08，基线 8095ba6 → 修复批）

覆盖三案（全部 mock，不依赖真实网络/设备）：
    [A] P1-1  db 写入口异常反馈：mark_verified / unmark / 收件箱写入失败 → QMessageBox.critical
    [B] P1-2  导入不携带验证态：verified=1 的 .nlb 回导后仍为 0，且不产生 action=verify 历史
    [C] P1-3  慢端点取消立即恢复：cancel() → 2.5s 内退出并发出 __CANCELLED__

用法：python scripts/smoke_p1_fixes.py   （EXIT=0 全过；需 PyQt5 + requests）
"""
import os
import sys
import json
import time
import socket
import threading

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
# [B] P1-2 导入不携带验证态（纯数据层，无需 GUI）
# ======================================================================
def case_b():
    import db as dbmod
    d1 = dbmod.Database(":memory:")
    d1.import_seed_dir()
    e = d1.search(keyword="trunk")[0]
    # 真机回填流程产生验证态
    d1.mark_verified(e["uuid"], "tester", "mock-model", "2026-10-08")
    tmp = os.path.join(APP_DIR, ".workbuddy", "smoke_p1_tmp.nlb")
    d1.export_nlb(tmp, uuids=[e["uuid"]])
    with open(tmp, "r", encoding="utf-8") as fp:
        payload = json.load(fp)
    # 篡改：验证态 + 内容变更一起塞进导入源
    payload["entries"][0]["verified"] = 1
    payload["entries"][0]["verified_by"] = "evil"
    payload["entries"][0]["verified_model"] = "evil-model"
    payload["entries"][0]["verified_date"] = "2099-01-01"
    payload["entries"][0]["title"] = (payload["entries"][0].get("title") or "") + "（内容更新）"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)   # 篡改必须写回才进导入源

    # 场景1：全新空库导入（INSERT 路径）
    d2 = dbmod.Database(":memory:")
    d2.import_nlb(tmp)
    got = d2.get_entry(e["uuid"])
    check("P1-2/INSERT 验证态归零", int(got["verified"] or 0) == 0,
          "verified=%s" % got["verified"])
    check("P1-2/INSERT 四字段不采纳", not got["verified_by"] and not got["verified_model"],
          "by=%r model=%r" % (got["verified_by"], got["verified_model"]))

    # 场景2：向未验证库回导（UPDATE 路径）——内容应更新、验证态不采纳
    d3 = dbmod.Database(":memory:")
    d3.import_seed_dir()
    e3 = d3.get_entry(e["uuid"])
    check("P1-2/前置-库内未验证", int(e3["verified"] or 0) == 0, "")
    hist_before = len(d3.get_history(e["uuid"]))
    a, u, s = d3.import_nlb(tmp)
    got3 = d3.get_entry(e["uuid"])
    check("P1-2/UPDATE 内容白名单生效", "（内容更新）" in (got3["title"] or ""),
          "title=%s" % got3["title"][-14:])
    check("P1-2/UPDATE 验证态不被改写", int(got3["verified"] or 0) == 0 and not got3["verified_by"],
          "verified=%s by=%r" % (got3["verified"], got3["verified_by"]))
    hist_new = [h for h in d3.get_history(e["uuid"])][hist_before:]
    check("P1-2/UPDATE 不产生 verify 历史",
          all((h.get("action") or "") != "verify" for h in hist_new),
          "新增历史 %d 条 actions=%s" % (len(hist_new), [h.get("action") for h in hist_new]))

    # 场景3：已验证条目内容仍可经导入更新（守卫移除后的行为）
    d1.import_nlb(tmp, merge=True)
    got1 = d1.get_entry(e["uuid"])
    check("P1-2/已验证条目内容可更新", "（内容更新）" in (got1["title"] or ""),
          "title=%s" % got1["title"][-14:])
    check("P1-2/已验证条目验证态保持",
          int(got1["verified"] or 0) == 1 and got1["verified_by"] == "tester",
          "verified=%s by=%r" % (got1["verified"], got1["verified_by"]))
    os.remove(tmp)


# ======================================================================
# [A] P1-1 写入口异常反馈（GUI，offscreen + mock 弹窗）
# ======================================================================
def case_a():
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app  # 保持 QApplication 引用存活（offscreen 必需）
    import ui_main
    import db as dbmod

    criticals = []
    QMessageBox.critical = staticmethod(
        lambda *a, **kw: criticals.append((a[1] if len(a) > 1 else "",
                                           a[2] if len(a) > 2 else "")))
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **kw: QMessageBox.Ok)

    class RaisingDb(dbmod.Database):
        """db 写方法注入异常，模拟只读竞态/IO 错误"""
        def mark_verified(self, *a, **kw):
            raise RuntimeError("模拟：命令库处于只读状态，无法标记验证")
        def add_unresolved(self, *a, **kw):
            raise RuntimeError("模拟：收件箱写入失败")

    d = RaisingDb(":memory:")
    d.import_seed_dir()
    w = ui_main.MainWindow(d)
    w.current_entry = d.search(keyword="trunk")[0]

    # 桌面标记验证：db 抛异常 → critical 且不 refresh 崩溃
    import ui_main as um
    class FakeDlg:
        def exec_(self): return um.QDialog.Accepted
        def values(self): return ("tester", "mock", "2026-10-08")
    orig_dlg = um.VerifyDialog
    um.VerifyDialog = lambda *a, **kw: FakeDlg()
    try:
        criticals.clear()
        w.on_mark_verified()
        check("P1-1/mark_verified 异常弹窗",
              len(criticals) == 1 and "标记验证失败" in criticals[0][0]
              and "只读" in criticals[0][1],
              "criticals=%s" % (criticals,))
        criticals.clear()
        w.on_unmark_verified()
        check("P1-1/unmark_verified 异常弹窗",
              len(criticals) == 1 and "取消验证失败" in criticals[0][0],
              "criticals=%s" % (criticals,))
    finally:
        um.VerifyDialog = orig_dlg

    # 报错诊断收件箱写入失败 → critical
    ef = w.tab_errorfix
    criticals.clear()
    ef._mark_unsatisfied({"vendor": ""}, "模拟不匹配报错文本")
    check("P1-1/收件箱写入异常弹窗",
          len(criticals) == 1 and "收件箱写入失败" in criticals[0][0],
          "criticals=%s" % (criticals,))
    w.deleteLater()


# ======================================================================
# [C] P1-3 慢端点取消立即恢复（本地 socket mock：发响应头+1块后挂住）
# ======================================================================
def case_c():
    import ai_bridge

    # 挂起的 SSE 服务端：回 200 + 一个 data 块，然后不发 [DONE] 也不关
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    hold = []

    def serve():
        conn, _ = srv.accept()
        hold.append(conn)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
        conn.sendall(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        # 挂住不关：模拟端点停滞（连接保持、既不回 [DONE] 也不断开）
        threading.Event().wait(60)

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    cfg = {"base_url": "http://127.0.0.1:%d/v1" % port, "api_key": "sk-mock",
           "model": "mock", "timeout": 30}
    w = ai_bridge.ChatWorker()
    w.set_request(cfg, [{"role": "user", "content": "hi"}])

    got_failed = []
    w.failed.connect(lambda msg: got_failed.append(msg))

    timer = threading.Timer(0.6, w.cancel)   # 收到首块后取消（此刻 iter_lines 阻塞中）
    t0 = time.time()
    timer.start()
    w.run()                                   # 同步执行：阻塞在 iter_lines，cancel 关连接后立即退出
    elapsed = time.time() - t0
    timer.cancel()
    try:
        hold[0].close()
    except Exception:
        pass
    srv.close()

    check("P1-3/取消立即退出", elapsed < 2.5, "耗时 %.2fs（timeout=30，修复前要等满）" % elapsed)
    check("P1-3/发出 __CANCELLED__", got_failed == ["__CANCELLED__"],
          "failed=%s" % got_failed)


def main():
    print("=" * 66)
    print("NetToolBox P1 修复回归（smoke_p1_fixes）")
    print("=" * 66)
    case_b()
    case_a()
    case_c()
    print("-" * 66)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print("%s %s  %s" % ("✓" if ok else "✘", name, detail))
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
