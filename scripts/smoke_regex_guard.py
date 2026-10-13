# -*- coding: utf-8 -*-
"""
smoke_regex_guard.py —— 安全加固批 4：导入与 regex 防护回归断言

覆盖：
    [1] pattern_lint（嵌套量词 [E]：正例/负例；超长 [W]；字符类/转义/有限量词不误报）
    [2] 存量 82 条报错字典对账（预期零命中，有命中如实报告）
    [3] validate-seed 实跑 EXIT=0（lint 接入后的真机对账）
    [4] import_nlb 大小上限（超限拒读 / 正常导入不受影响）
    [5] 诊断输入截断助手（200KB / 1 万行双条件）

用法：
    PYTHONPATH=app;_vendor python scripts/smoke_regex_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _smoke_env as _se   # setup() 内含 sys.path 卫生 + 主库零接触断言
_se.setup()                                     # ★ 先于任何 app 模块导入

import error_match as _em                       # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ((" —— " + detail) if (detail and not cond) else ""))


def has_E(pattern):
    return any(lv == "E" for lv, _m in _em.pattern_lint(pattern))


def has_W(pattern):
    return any(lv == "W" for lv, _m in _em.pattern_lint(pattern))


def main():
    print("=" * 66)
    print("smoke_regex_guard —— 导入与 regex 防护（安全加固批 4）")
    print("=" * 66)

    # ---- [1] pattern_lint ----
    print("[1] pattern_lint")
    for pat in ("(a+)+", "(.*)*", "(?:\\w+)*", "(a|bb+){2,}", "(\\d+)*x",
                "((a+)+)", "(a*)+", "([0-9]+)+$"):
        check("[E] 嵌套量词命中（%s）" % pat, has_E(pat))
    for pat in ("a+b*", "(a|b)+", "(a+){3}", "[.*]+", "\\(a\\+\\)+",
                "(a+)$", "^\\s*%\\s*(Invalid input)", "Error:\\s*\\w+"):
        check("无害 pattern 不误报（%s）" % pat, not has_E(pat))
    check("有限量词组外不误报（(a+){3}）", not has_E("(a+){3}"))
    check("字符类内量词不误报（[*+]+）", not has_E("[*+]+x"))
    check("转义括号不误报（\\(a+\\)+）", not has_E("\\(a+\\)+"))
    check("超长 pattern 报 [W]（501 字符）", has_W("a" * 501))
    check("500 字符整不报 [W]", not has_W("a" * 500))
    check("空 pattern 零输出", _em.pattern_lint("") == [])
    check("None pattern 零输出", _em.pattern_lint(None) == [])

    # ---- [2] 存量对账（82 条，预期零命中）----
    print("[2] 存量报错字典对账")
    import db as dbmod
    database = dbmod.Database(":memory:")
    database.import_seed_dir()
    errs = database.all_errs()
    e_hits, w_hits = [], []
    for e in errs:
        for lv, msg in _em.pattern_lint(e.get("pattern")):
            (e_hits if lv == "E" else w_hits).append((e.get("err_id"), msg,
                                                      (e.get("pattern") or "")[:60]))
    check("存量 %d 条 pattern：[E] 零命中" % len(errs), not e_hits, str(e_hits[:3]))
    check("存量 pattern：[W] 零命中", not w_hits, str(w_hits[:3]))

    # ---- [3] validate-seed 实跑（lint 接入后的对账闭环）----
    print("[3] validate-seed 实跑")
    proc = subprocess.run([sys.executable, os.path.join(REPO, "app", "main.py"),
                           "--validate-seed"], capture_output=True, text=True,
                          cwd=REPO, timeout=300)
    out = proc.stdout or ""
    check("validate EXIT=0", proc.returncode == 0,
          "rc=%s\n%s" % (proc.returncode, (proc.stderr or out)[-300:]))
    check("质检通过行存在（338/26/82 口径）",
          "338 条条目 + 26 棵排查树 + 82 条报错字典" in out,
          out[-200:])
    check("[E] 报错字典零问题", "报错字典发现" not in out)

    # ---- [4] import_nlb 大小上限 ----
    print("[4] import_nlb 大小上限")
    real_max = dbmod.NLB_MAX_BYTES
    tmpdir = tempfile.mkdtemp(prefix="ntbx_nlb_")
    try:
        dbmod.NLB_MAX_BYTES = 1000        # 缩限便于造超限文件
        db_path = os.path.join(tmpdir, "lib.db")
        database2 = dbmod.Database(db_path)
        nlb_path = os.path.join(tmpdir, "big.nlb")
        with open(nlb_path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps({"entries": [], "pad": "x" * 2000}))
        try:
            database2.import_nlb(nlb_path)
            check("超限文件被拒", False, "未抛 ValueError")
        except ValueError as exc:
            check("超限文件被拒（ValueError 含上限提示）",
                  "超过" in str(exc) and "MB" in str(exc), str(exc))
        small_path = os.path.join(tmpdir, "ok.nlb")
        with open(small_path, "w", encoding="utf-8") as fp:
            json.dump({"entries": [{"uuid": "sm-001", "title": "smoke 条目",
                                    "vendor": "huawei", "os_family": "vrp8",
                                    "category": "网络配置", "device_type": "switch",
                                    "commands": "display version", "notes": "smoke"}]},
                      fp)
        added, updated, skipped = database2.import_nlb(small_path)
        check("限内正常导入不受影响", (added, updated, skipped) == (1, 0, 0),
              str((added, updated, skipped)))
        database2.close()
    finally:
        dbmod.NLB_MAX_BYTES = real_max

    # ---- [5] 诊断输入截断 ----
    print("[5] 诊断输入截断")
    from ui_errorfix import truncate_diagnose_input, MAX_DIAGNOSE_CHARS, MAX_DIAGNOSE_LINES
    t1 = "x" * (MAX_DIAGNOSE_CHARS - 1)
    out1, tr1 = truncate_diagnose_input(t1)
    check("199999 字符不截断", (out1 == t1, tr1) == (True, False))
    t2 = "x" * (MAX_DIAGNOSE_CHARS + 1)
    out2, tr2 = truncate_diagnose_input(t2)
    check("超 200KB 截断到上限", tr2 and len(out2) == MAX_DIAGNOSE_CHARS)
    t3 = "\n".join("err %d" % i for i in range(MAX_DIAGNOSE_LINES + 1))
    out3, tr3 = truncate_diagnose_input(t3)
    check("超 1 万行截断（行数收敛到上限）",
          tr3 and (out3.count("\n") + 1) == MAX_DIAGNOSE_LINES)
    out4, tr4 = truncate_diagnose_input("")
    check("空输入不截断", (out4, tr4) == ("", False))

    print("=" * 66)
    if FAIL:
        print("结果：%d 项失败：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("结果：全部通过（lint 正反例 + 存量对账零命中 + validate EXIT=0 + "
          "导入上限 + 截断助手）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
