# -*- coding: utf-8 -*-
"""
smoke_export_site.py —— 静态站点导出器冒烟

覆盖：
  A. 覆盖度对账：tier_counts 与仪表盘同源 SQL 逐项一致（临时库+种子导入）
  B. 脱敏正反例：植入假公网 IP / secret 真值必被拦；干净数据零命中
  C. 零写权断言：导出前后主库 sha256 逐字节一致
  D. 产物结构：DOCTYPE / 数据内联无占位残留 / CSS 变量注入 / 四档徽章文案在 JS
  E. token 同源：verify_tokens_against_theme 零漂移
"""
import hashlib
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import _smoke_env as _se
_se.setup()  # 主库零接触

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
    import tempfile
    import sqlite3
    import site_export
    import sanitize_scan
    import db as dbmod

    tmp = tempfile.mkdtemp(prefix="smoke_site_")
    dbp = os.path.join(tmp, "t.db")
    d = dbmod.Database(dbp)
    d.import_seed_dir()
    d.close()

    # ---- A. 对账 ----
    def a1():
        conn = sqlite3.connect(dbp)
        cv = site_export.tier_counts(conn, "entries", has_notes=True)
        # 直接 SQL 复算
        g = conn.execute("SELECT COUNT(*) FROM entries WHERE verified=1").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM entries WHERE verified<>1 AND exec_level='verified-cli'").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        assert cv["green"] == g and cv["syntax"] == s and cv["total"] == n, (cv, g, s, n)
        assert cv["green"] + cv["syntax"] + cv["suspended"] + cv["unverified"] == n
    check("A1 tier_counts 与直接 SQL 逐项一致", a1)

    # ---- B/C/D. 导出主流程 ----
    out = os.path.join(tmp, "site.html")
    def b1():
        rc = site_export.export(dbp, out)
        assert rc == 0, rc
        assert os.path.isfile(out)
    check("B1 干净库导出 rc=0", b1)

    def b2():
        before = site_export.sha256(dbp)
        site_export.export(dbp, out)
        assert site_export.sha256(dbp) == before, "导出改变了主库"
    check("B2 零写权：导出前后主库 sha256 一致", b2)

    def b3():
        html = open(out, encoding="utf-8").read()
        assert html.startswith("<!DOCTYPE html>")
        assert "__SITE_DATA__" not in html and "__CSS_TOKENS__" not in html, "占位未替换"
        assert "var(--accent)" in html and "--bg-window:" in html, "CSS 变量未注入"
        assert "语法核对" in html and "挂起" in html, "四档徽章文案缺失"
        # 数据内联 JSON 可被截取解析（防 </ 截断后 JSON 不完整）
        m = html.index("var DATA = ") + len("var DATA = ")
        end = html.index(";\n</script>", m)
        data = json.loads(html[m:end])
        assert len(data["entries"]) + len(data["trees"]) + len(data["errs"]) > 0
    check("B3 产物结构：内联 JSON 完整/CSS 变量/徽章文案", b3)

    def b4():
        drift = site_export.verify_tokens_against_theme()
        assert not drift, drift
    check("B4 token 同源：SITE_TOKENS 与 theme.py 零漂移", b4)

    # ---- C. 脱敏正反例 ----
    def c1():
        bad = {"entries": [{"uuid": "f1", "notes": "跳板机 45.33.20.1 实测"}],
               "trees": [], "errs": []}
        f = sanitize_scan.scan_site_data(bad)
        assert any(x.kind == "IP-WHITELIST" for x in f), f
        bad2 = {"entries": [{"uuid": "f2", "commands": "password Sup3rSecret!"}],
                "trees": [], "errs": []}
        f2 = sanitize_scan.scan_site_data(bad2)
        assert any(x.kind == "SECRET" for x in f2), f2
        try:
            sanitize_scan.assert_clean(bad)
            raise AssertionError("假 IP 未被拦截")
        except SystemExit as e:
            assert e.code == 5, e.code
    check("C1 脱敏反例：植入假 IP / secret 真值必拦（rc=5）", c1)

    def c2():
        good = {"entries": [{"uuid": "ok", "notes": "示例 192.0.2.10 / 10.255.1.1 / password {{pwd}} <旧口令>"}],
                "trees": [], "errs": []}
        assert sanitize_scan.scan_site_data(good) == []
    check("C2 脱敏正例：文档段/占位/人工位零命中", c2)

    # ---- D. 范围排除（结构性）----
    def d1():
        # 导出数据函数只取三库：连一个含 history/err_unresolved 的库验证输出不含其内容
        conn = sqlite3.connect(dbp)
        conn.execute("CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, action TEXT)")
        conn.execute("INSERT INTO history(action) VALUES ('SECRET-MARKER-history-row')")
        conn.commit()
        conn.close()
        data = site_export.load_data(dbp)
        blob = json.dumps(data, default=str)
        assert "SECRET-MARKER-history-row" not in blob, "history 泄漏进导出数据"
    check("D1 history 永不进站点（结构性排除）", d1)

    print()
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  FAIL:", n, repr(e))
        return 1
    print("[SMOKE-ENV] 主库零接触实证：指纹前后一致" if _se.verify_main_db_untouched() else "[SMOKE-ENV] 主库指纹异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
