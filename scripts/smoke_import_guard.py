# -*- coding: utf-8 -*-
"""
smoke_import_guard.py —— 导入护栏热修批回归（2026-10-12 三事故场景固化）

覆盖：
  A. 场景一（09:27 重演）：库内有会话状态、种子状态字段异 → 导入 →
     状态字段（verified 四字段/exec_level/notes/favorite）逐字段不变
  B. 场景二（二次事故重演）：main.py --db <库> --validate-seed 组合 →
     报错退出 rc=2 且库 hash 前后一致；--allow-main-db 放行可跑
  C. 场景三：不存在路径 → 报错退出不建空库
  D. 自动备份：导入触发 → backup/ 出现 command_lib-pre-import-<ts>.db 新文件
  E. INSERT 分支：新条目照收种子初始态（exec_level/notes/favorite）
"""
import glob
import hashlib
import io
import os
import subprocess
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
SESSION_FIELDS = ("verified", "verified_by", "verified_model", "verified_date",
                  "exec_level", "notes", "favorite")


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("✓ %s" % name)
    except Exception as exc:
        FAIL.append((name, exc))
        print("✗ %s  %r" % (name, exc))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    import tempfile
    import db as dbmod

    tmp = tempfile.mkdtemp(prefix="smoke_import_guard_")
    os.makedirs(os.path.join(tmp, "backup"))
    dbp = os.path.join(tmp, "t.db")
    d = dbmod.Database(dbp)
    d.import_seed_dir()
    d.close()

    def a1():
        # 会话状态注入（模拟验证会回填 + 收藏 + exec_level 升级 + notes 实录）
        c = sqlite3.connect(dbp)
        row = c.execute("SELECT uuid FROM entries WHERE vendor='centos' LIMIT 1").fetchone()
        uuid1 = row[0]
        c.execute("UPDATE entries SET verified=1, verified_by='sess', verified_model='m', "
                  "verified_date='2026-10-12', exec_level='verified-cli', favorite=1, "
                  "notes=notes||'\\n—— 实测 2026-10-12（场2）：回填实录' WHERE uuid=?", (uuid1,))
        c.commit()
        before = c.execute("SELECT %s FROM entries WHERE uuid=?" % ",".join(SESSION_FIELDS),
                           (uuid1,)).fetchone()
        # 种子再导入（重演 09:27：种子状态字段与库有差异）
        d2 = dbmod.Database(dbp)
        added, updated, skipped = d2.import_seed_dir()
        d2.close()
        after = c.execute("SELECT %s FROM entries WHERE uuid=?" % ",".join(SESSION_FIELDS),
                          (uuid1,)).fetchone()
        assert tuple(before) == tuple(after), "状态字段被导入改动: %s" % \
            [(f, b, a) for f, b, a in zip(SESSION_FIELDS, tuple(before), tuple(after)) if b != a]
        # 全库状态字段零变化（不只目标条目）
        bad = c.execute(
            "SELECT COUNT(*) FROM entries WHERE (verified=1 AND verified_by<>'sess') "
            "OR (exec_level='verified-cli' AND uuid<>?)", (uuid1,)).fetchone()[0]
        assert bad == 0, "其他条目状态字段被改动 %d 条" % bad
        c.close()
    check("A1 场景一重演：导入后会话状态字段逐字段不变", a1)

    def a2():
        # 漂移收敛汇总：种子与库状态有差异时应打印单行汇总而非逐条噪音
        d3 = dbmod.Database(dbp)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            d3.import_seed_dir()
        finally:
            sys.stdout = old
        d3.close()
        out = buf.getvalue()
        assert "[import-guard]" in out and "保留库值" in out, out[-300:]
        assert out.count("[import-guard]") == 1, "汇总非单行"
    check("A2 状态字段漂移收敛单行汇总", a2)

    def b1():
        # 场景二：--db + --validate-seed 组合 → rc=2 且库 hash 不变
        h0 = sha256(dbp)
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "app", "main.py"),
             "--db", dbp, "--validate-seed"],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        assert r.returncode == 2, (r.returncode, r.stderr[-300:])
        assert "组合被拒绝" in r.stderr
        assert sha256(dbp) == h0, "组合被拒但主库 hash 变化"
    check("B1 场景二重演：--db+--validate-seed 拒绝退出且零写入", b1)

    def b2():
        # --allow-main-db 放行可跑（护栏兜底）
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "app", "main.py"),
             "--db", dbp, "--validate-seed", "--allow-main-db"],
            capture_output=True, text=True, timeout=300,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        assert r.returncode == 0, (r.returncode, r.stderr[-300:])
        assert "[warn] --allow-main-db" in r.stderr
        c = sqlite3.connect(dbp)
        n = c.execute("SELECT COUNT(*) FROM entries WHERE verified=1").fetchone()[0]
        c.close()
        assert n >= 1, "放行导入后护栏失效"
    check("B2 --allow-main-db 放行且护栏兜底", b2)

    def c1():
        # 场景三：不存在路径 → 组合在连接前拒绝；直接连接由 was_created 建库是
        # GUI 合法路径，工具侧以 isfile 防御——这里验组合拒绝不产生新库文件
        ghost = os.path.join(tmp, "ghost.db")
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "app", "main.py"),
             "--db", ghost, "--validate-seed"],
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        assert r.returncode == 2, r.returncode
        assert not os.path.exists(ghost), "拒绝路径上仍创建了库文件"
    check("C1 场景三：不存在路径组合拒绝且不建空库", c1)

    def d1():
        # 自动备份：触发一次真实写入型导入 → backup/ 出现 pre-import 文件
        d4 = dbmod.Database(dbp)
        newe = dict(dbmod.DEMO_ENTRIES[0]) if hasattr(dbmod, "DEMO_ENTRIES") else None
        # 不依赖 DEMO：直接构造一条新条目走 import_entries INSERT 路径
        newe = {"uuid": "00000000-guard-test-0000", "platform": "linux", "vendor": "centos",
                "os_family": "centos7", "category": "排查命令", "title": "护栏测试条目",
                "description": "临时", "commands": "echo hi", "params": "[]",
                "notes": "n", "duration": "temp", "verified": 0, "favorite": 0,
                "exec_level": "", "interactive": 0, "rollback": "",
                "device_type": "服务器", "models": ""}
        before = set(glob.glob(os.path.join(tmp, "backup", "*.db")))
        d4.import_entries([newe], merge=True, operator="smoke")
        d4.close()
        after = set(glob.glob(os.path.join(tmp, "backup", "*.db")))
        new = [f for f in after - before if "pre-import-" in f]
        assert new, "导入未产生自动备份: before=%d after=%d" % (len(before), len(after))
        # 清理测试条目，保持库规模干净
        c = sqlite3.connect(dbp)
        c.execute("DELETE FROM entries WHERE uuid='00000000-guard-test-0000'")
        c.commit()
        c.close()
    check("D1 导入触发自动备份 pre-import-<ts>.db", d1)

    def e1():
        # INSERT 分支：新条目照收种子初始态（exec_level/notes/favorite 按种子）
        d5 = dbmod.Database(dbp)
        newe = {"uuid": "00000000-guard-test-1111", "platform": "linux", "vendor": "centos",
                "os_family": "centos7", "category": "排查命令", "title": "INSERT 初始态测试",
                "description": "临时", "commands": "echo hi", "params": "[]",
                "notes": "种子notes", "duration": "temp", "verified": 0, "favorite": 0,
                "exec_level": "skeleton", "interactive": 0, "rollback": "",
                "device_type": "服务器", "models": ""}
        d5.import_entries([newe], merge=True, operator="smoke")
        d5.close()
        c = sqlite3.connect(dbp)
        r = c.execute("SELECT exec_level, notes, favorite, verified FROM entries "
                      "WHERE uuid='00000000-guard-test-1111'").fetchone()
        c.execute("DELETE FROM entries WHERE uuid='00000000-guard-test-1111'")
        c.commit()
        c.close()
        assert r == ("skeleton", "种子notes", 0, 0), r
    check("E1 INSERT 分支照收种子初始态", e1)

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
