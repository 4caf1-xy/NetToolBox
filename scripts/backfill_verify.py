# -*- coding: utf-8 -*-
"""
backfill_verify.py —— 验证会四档回填工具（verify-guide 流程第 4 步固化版）

背景
    场 1/场 2（2026-10-09/12）回填为会话内手工 SQL，曾发生 ruijie 同名条目
    误伤（无 uuid 匹配、无对账断言，靠备份库人工恢复）。本脚本把回填固化：
    uuid 精确匹配 + 前后对账断言 + 事务包裹，误伤不可能静默通过。

用法
    python scripts/backfill_verify.py --csv backfill.csv             # 回填
    python scripts/backfill_verify.py --csv backfill.csv --dry-run   # 只对账不写
    python scripts/backfill_verify.py --db <path> --csv backfill.csv # 显式库

CSV 口径（verify-guide 流程第 4 步，列名固定不得增删改名）
    table,uuid,result,verified_by,verified_model,verified_date,notes,notes_mode
    table       : entries | trouble_trees | err_dict（本版实现 entries；另两库留接口）
    uuid        : 条目 uuid，精确匹配；0 命中 / 多重命中一律报错退出
    result      : green | syntax | red | hold（四档判定）
    notes_mode  : append（默认，追加到 notes 尾部）| replace（整体替换 notes）
    * green 回填四元组（verified=1）；syntax 升 exec_level=verified-cli（verified 保持 0）；
      red/hold 不动 verified/exec_level，仅 notes 实录——与 verify-guide 四档标准一致。

硬性约束
    · 零联网、零 PyQt：纯标准库
    · 事务包裹：任一断言失败 ROLLBACK，库不留半截状态
    · 对账断言：回填前后条目总数不变；非目标条目全字段零变化（逐行快照对比）
    · 重复回填防护：目标已 verified=1 或已有 exec_level 升级时，非 --force 报错
"""

from __future__ import print_function

import csv
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSV_COLUMNS = ["table", "uuid", "result", "verified_by", "verified_model",
               "verified_date", "notes", "notes_mode"]
RESULTS = ("green", "syntax", "red", "hold")


def find_db():
    """与 backup_db.py 同序的库定位。"""
    for p in (os.path.join(REPO, "app", "command_lib.db"),
              os.path.join(REPO, "dist_package", "command_lib.db"),
              os.path.join(os.getcwd(), "command_lib.db")):
        if os.path.isfile(p):
            return p
    raise SystemExit("[E] 未找到 command_lib.db，用 --db 显式指定")


def snapshot(conn, table, exclude_uuids):
    """全表快照 {uuid: row_tuple}，排除目标集合（用于非目标零变化断言）。"""
    cur = conn.execute("SELECT * FROM %s" % table)
    cols = [d[0] for d in cur.description]
    ui = cols.index("uuid")
    out = {}
    for row in cur.fetchall():
        if row[ui] in exclude_uuids:
            continue
        out[row[ui]] = row
    return out, cols


def load_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            missing = [k for k in CSV_COLUMNS if k not in (r.keys() or [])]
            if missing:
                raise SystemExit("[E] CSV 第 %d 行缺列: %s（列名以 verify-guide 口径为准）"
                                 % (i, missing))
            table = (r["table"] or "").strip()
            uuid = (r["uuid"] or "").strip()
            result = (r["result"] or "").strip().lower()
            mode = (r["notes_mode"] or "append").strip().lower() or "append"
            if table != "entries":
                raise SystemExit("[E] 第 %d 行 table=%r：本版仅实现 entries" % (i, table))
            if result not in RESULTS:
                raise SystemExit("[E] 第 %d 行 result=%r 必须是 %s" % (i, result, "/".join(RESULTS)))
            if mode not in ("append", "replace"):
                raise SystemExit("[E] 第 %d 行 notes_mode=%r 必须 append/replace" % (i, mode))
            if not uuid:
                raise SystemExit("[E] 第 %d 行 uuid 为空" % i)
            rows.append({"table": table, "uuid": uuid, "result": result,
                         "by": (r["verified_by"] or "").strip(),
                         "model": (r["verified_model"] or "").strip(),
                         "date": (r["verified_date"] or "").strip(),
                         "notes": r["notes"] or "", "mode": mode, "line": i})
    if not rows:
        raise SystemExit("[E] CSV 无数据行")
    seen = set()
    for r in rows:
        if r["uuid"] in seen:
            raise SystemExit("[E] CSV 内 uuid 重复: %s" % r["uuid"])
        seen.add(r["uuid"])
    return rows


def main(argv):
    db_path, csv_path, dry, force = None, None, False, False
    it = iter(argv)
    for a in it:
        if a == "--db":
            db_path = next(it, None)
        elif a == "--csv":
            csv_path = next(it, None)
        elif a == "--dry-run":
            dry = True
        elif a == "--force":
            force = True
        else:
            raise SystemExit("[E] 未知参数 %r" % a)
    if not csv_path:
        raise SystemExit("用法: python scripts/backfill_verify.py --csv backfill.csv [--dry-run] [--db path] [--force]")
    db_path = db_path or find_db()
    try:
        rows = load_csv(csv_path)
    except SystemExit as exc:
        print("[E] CSV 校验失败，未写库：", exc)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    errors = []

    # ---- 1) uuid 精确匹配：0 命中 / 多重命中一律报错 ----
    targets = {}
    for r in rows:
        hit = conn.execute("SELECT uuid, verified, exec_level, notes FROM entries WHERE uuid = ?",
                           (r["uuid"],)).fetchall()
        if len(hit) == 0:
            errors.append("CSV 第 %d 行 uuid 无匹配: %s" % (r["line"], r["uuid"]))
            continue
        if len(hit) > 1:
            errors.append("CSV 第 %d 行 uuid 命中 %d 条（库内重复，属数据损坏）: %s"
                          % (r["line"], len(hit), r["uuid"]))
            continue
        if r["result"] == "green" and hit[0]["verified"] == 1 and not force:
            errors.append("CSV 第 %d 行 uuid 已 verified=1（重复回填，--force 覆盖）: %s"
                          % (r["line"], r["uuid"]))
            continue
        if r["result"] == "syntax" and hit[0]["exec_level"] == "verified-cli" and not force:
            errors.append("CSV 第 %d 行 uuid 已是 verified-cli（重复回填，--force 覆盖）: %s"
                          % (r["line"], r["uuid"]))
            continue
        targets[r["uuid"]] = r
    if errors:
        print("[E] 匹配阶段 %d 处失败，未写库：" % len(errors))
        for e in errors:
            print("   -", e)
        return 2

    # ---- 2) 前置快照（非目标条目零变化断言的基线） ----
    exclude = set(targets)
    before, cols = snapshot(conn, "entries", exclude)
    total_before = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    if dry:
        print("[dry-run] 匹配 %d 条全部通过；统计：" % len(targets))
        for k in RESULTS:
            n = sum(1 for r in targets.values() if r["result"] == k)
            if n:
                print("   %-6s %d" % (k, n))
        print("[dry-run] 未写库。")
        return 0

    # ---- 3) 事务回填 ----
    try:
        conn.execute("BEGIN")
        for uuid, r in targets.items():
            note = r["notes"].replace("\\n", "\n") if r["notes"] else ""
            if r["mode"] == "append":
                conn.execute("UPDATE entries SET notes = notes || ? WHERE uuid = ?",
                             ("\n" + note if note else "", uuid))
            else:
                conn.execute("UPDATE entries SET notes = ? WHERE uuid = ?", (note, uuid))
            if r["result"] == "green":
                if not (r["by"] and r["model"] and r["date"]):
                    raise AssertionError("green 档四元组不全: %s（by/model/date 必填）" % uuid)
                conn.execute("UPDATE entries SET verified=1, verified_by=?, verified_model=?, verified_date=? WHERE uuid=?",
                             (r["by"], r["model"], r["date"], uuid))
            elif r["result"] == "syntax":
                conn.execute("UPDATE entries SET exec_level='verified-cli' WHERE uuid=?", (uuid,))
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        print("[E] 回填失败已 ROLLBACK:", exc)
        return 3

    # ---- 4) 后置对账断言 ----
    after, _ = snapshot(conn, "entries", exclude)
    total_after = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    problems = []
    if total_before != total_after:
        problems.append("条目总数变化: %d -> %d" % (total_before, total_after))
    if set(before) != set(after):
        problems.append("非目标条目集合变化（出现/消失 uuid）")
    else:
        changed = [u for u in before if before[u] != after[u]]
        if changed:
            problems.append("非目标条目 %d 条被改动（首例 %s）" % (len(changed), changed[0]))
    if problems:
        print("[E] 后置对账失败（库已回填但存在异常，请用备份恢复）:")
        for p in problems:
            print("   -", p)
        return 4

    print("[OK] 回填 %d 条：green=%d syntax=%d red=%d hold=%d；非目标条目 %d 条零变化。"
          % (len(targets),
             sum(1 for r in targets.values() if r["result"] == "green"),
             sum(1 for r in targets.values() if r["result"] == "syntax"),
             sum(1 for r in targets.values() if r["result"] == "red"),
             sum(1 for r in targets.values() if r["result"] == "hold"),
             len(before)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
