# -*- coding: utf-8 -*-
"""
smoke_backfill.py —— backfill_verify.py 回填工具冒烟

覆盖：
  A. uuid 精确匹配：无匹配报错退出 / 多重命中报错 / CSV 内重复 uuid 报错
  B. 四档语义：green 四元组 / syntax 升 verified-cli / red/hold 不动状态字段
  C. ruijie 误伤回归：同名不同 uuid，回填后非目标条目全字段零变化
  D. 事务与防护：green 缺四元组 ROLLBACK / 重复回填拦截 / dry-run 零写
  E. notes 模式：append 追加 / replace 替换
"""
import csv
import io
import json
import os
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
import _smoke_env as _se
_se.setup()  # 主库零接触

PASS = []
FAIL = []
SCRIPT = os.path.join(REPO, "scripts", "backfill_verify.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import backfill_verify as bf  # noqa: E402


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("✓ %s" % name)
    except Exception as exc:
        FAIL.append((name, exc))
        print("✗ %s  %r" % (name, exc))


def make_db(path):
    """建临时文件库并导入种子。"""
    import db as dbmod
    if os.path.exists(path):
        os.remove(path)
    d = dbmod.Database(path)
    d.import_seed_dir()
    d.close()
    return path


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=bf.CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def conn_of(path):
    c = sqlite3.connect(path, isolation_level=None)  # autocommit，避免持锁阻塞 bf 写连接
    c.row_factory = sqlite3.Row
    return c


def main():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="smoke_backfill_")
    dbp = make_db(os.path.join(tmp, "t.db"))
    csvp = os.path.join(tmp, "b.csv")
    c = conn_of(dbp)

    # 目标条目：取一条 cisco 与一条 ruijie（验证 notes 里两库存在同名 title 对）
    pair = c.execute(
        "SELECT e1.uuid AS cisco_uuid, e1.title, e2.uuid AS ruijie_uuid "
        "FROM entries e1 JOIN entries e2 ON e1.title = e2.title "
        "WHERE e1.vendor='cisco' AND e2.vendor='ruijie' LIMIT 1").fetchone()
    assert pair, "种子库需存在 cisco/ruijie 同名条目对"
    cisco_uuid, ruijie_uuid = pair["cisco_uuid"], pair["ruijie_uuid"]
    ruijie_before = tuple(c.execute("SELECT * FROM entries WHERE uuid=?", (ruijie_uuid,)).fetchone())

    def a1():
        write_csv(csvp, [{"table": "entries", "uuid": "no-such-uuid", "result": "green",
                          "verified_by": "t", "verified_model": "m", "verified_date": "2026-10-12",
                          "notes": "x", "notes_mode": "append"}])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 2, rc
        assert c.execute("SELECT verified FROM entries WHERE uuid=?", (cisco_uuid,)).fetchone()[0] == 0
    check("A1 uuid 无匹配 → rc=2 零写库", a1)

    def a2():
        # CSV 内重复 uuid → rc=2（库侧多重命中受 UNIQUE 约束结构性防护，防御分支留档）
        write_csv(csvp, [
            {"table": "entries", "uuid": cisco_uuid, "result": "hold", "verified_by": "",
             "verified_model": "", "verified_date": "", "notes": "x", "notes_mode": "append"},
            {"table": "entries", "uuid": cisco_uuid, "result": "hold", "verified_by": "",
             "verified_model": "", "verified_date": "", "notes": "y", "notes_mode": "append"},
        ])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 2, rc
    check("A2 CSV 内重复 uuid → rc=2（库侧多重命中受 UNIQUE 防护）", a2)

    def a3():
        write_csv(csvp, [
            {"table": "entries", "uuid": cisco_uuid, "result": "green", "verified_by": "t",
             "verified_model": "m", "verified_date": "2026-10-12", "notes": "实测✓", "notes_mode": "append"},
            {"table": "entries", "uuid": ruijie_uuid, "result": "hold", "verified_by": "",
             "verified_model": "", "verified_date": "", "notes": "挂起实录", "notes_mode": "append"},
        ])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 0, rc
        g = c.execute("SELECT verified, verified_by, verified_model, verified_date, exec_level, notes FROM entries WHERE uuid=?", (cisco_uuid,)).fetchone()
        assert g["verified"] == 1 and g["verified_by"] == "t" and g["verified_date"] == "2026-10-12"
        assert g["exec_level"] == ""  # green 不动 exec_level
        assert "实测✓" in g["notes"]
        h = c.execute("SELECT verified, exec_level, notes FROM entries WHERE uuid=?", (ruijie_uuid,)).fetchone()
        assert h["verified"] == 0 and h["exec_level"] == "" and "挂起实录" in h["notes"]
    check("B1 green 四元组 + hold 不动状态字段", a3)

    def b2():
        # syntax 档：verified 保持 0，exec_level 升级
        e = c.execute("SELECT uuid FROM entries WHERE verified=0 AND exec_level='' AND uuid<>? AND uuid<>? LIMIT 1",
                      (cisco_uuid, ruijie_uuid)).fetchone()
        write_csv(csvp, [{"table": "entries", "uuid": e["uuid"], "result": "syntax", "verified_by": "",
                          "verified_model": "", "verified_date": "", "notes": "语法核对", "notes_mode": "append"}])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 0, rc
        r = c.execute("SELECT verified, exec_level FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()
        assert r["verified"] == 0 and r["exec_level"] == "verified-cli"
    check("B2 syntax 升 verified-cli 且 verified 保持 0", b2)

    def c1():
        # ruijie 误伤回归：回填后 ruijie 条目全字段零变化
        ruijie_after = tuple(c.execute("SELECT * FROM entries WHERE uuid=?", (ruijie_uuid,)).fetchone())
        # hold 档允许 notes 追加（B1 已写入"挂起实录"），其余字段必须零变化
        rb = dict(zip([d[0] for d in c.execute("SELECT * FROM entries WHERE uuid=? LIMIT 1", (ruijie_uuid,)).description], ruijie_before))
        ra = dict(zip([d[0] for d in c.execute("SELECT * FROM entries WHERE uuid=? LIMIT 1", (ruijie_uuid,)).description], ruijie_after))
        diff = {k for k in rb if rb[k] != ra[k] and k != "notes"}
        assert not diff, "ruijie 非目标字段被改动: %s" % diff
        assert ra["notes"] == rb["notes"] + "\n挂起实录", "notes 追加语义不符"
    check("C1 ruijie 同名条目除 notes 追加外全字段零变化（误伤回归）", c1)

    def d1():
        # green 缺四元组 → ROLLBACK，库无变化
        e = c.execute("SELECT uuid FROM entries WHERE verified=0 AND exec_level='' LIMIT 1").fetchone()
        before_notes = c.execute("SELECT notes FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()[0]
        write_csv(csvp, [{"table": "entries", "uuid": e["uuid"], "result": "green", "verified_by": "",
                          "verified_model": "", "verified_date": "", "notes": "x", "notes_mode": "append"}])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 3, rc
        after = c.execute("SELECT verified, notes FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()
        assert after["verified"] == 0 and after["notes"] == before_notes, "ROLLBACK 未生效"
    check("D1 green 缺四元组 → rc=3 ROLLBACK 零残留", d1)

    def d2():
        # 重复回填拦截
        write_csv(csvp, [{"table": "entries", "uuid": cisco_uuid, "result": "green", "verified_by": "t",
                          "verified_model": "m", "verified_date": "2026-10-12", "notes": "again", "notes_mode": "append"}])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 2, rc
        rc = bf.main(["--db", dbp, "--csv", csvp, "--force"])
        assert rc == 0, rc
    check("D2 重复回填拦截，--force 放行", d2)

    def d3():
        # dry-run 零写
        e = c.execute("SELECT uuid, notes FROM entries WHERE verified=0 AND exec_level='' LIMIT 1").fetchone()
        write_csv(csvp, [{"table": "entries", "uuid": e["uuid"], "result": "hold", "verified_by": "",
                          "verified_model": "", "verified_date": "", "notes": "dry", "notes_mode": "append"}])
        rc = bf.main(["--db", dbp, "--csv", csvp, "--dry-run"])
        assert rc == 0, rc
        assert c.execute("SELECT notes FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()[0] == e["notes"]
    check("D3 dry-run 零写", d3)

    def e1():
        # replace 模式
        e = c.execute("SELECT uuid FROM entries WHERE verified=0 AND exec_level='' LIMIT 1").fetchone()
        write_csv(csvp, [{"table": "entries", "uuid": e["uuid"], "result": "red", "verified_by": "",
                          "verified_model": "", "verified_date": "", "notes": "红档实录全文", "notes_mode": "replace"}])
        rc = bf.main(["--db", dbp, "--csv", csvp])
        assert rc == 0, rc
        assert c.execute("SELECT notes FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()[0] == "红档实录全文"
        r = c.execute("SELECT verified, exec_level FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()
        assert r["verified"] == 0 and r["exec_level"] == ""
    check("E1 replace 模式 + red 不动状态字段", e1)

    c.close()
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
