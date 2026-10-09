# -*- coding: utf-8 -*-
"""
_smoke_env.py —— smoke 套件统一测试环境（主库零接触改造，2026-10-09）

背景：smoke 套件会创建 MainWindow / 调用 ai_bridge，深处代码按"数据跟着程序走"
惯例会惰性创建 sessions/、写 ui_state.json 等。此前各套件各自为战（有的劫持了
state 路径、有的没有），零接触主库靠的是"恰好没走到写路径"，不是结构性保证。
本助手把改造收敛为两件事：

1. 路径全劫持 —— db.get_base_dir / db.get_db_path / ai_bridge._base_dir /
   theme.state_file_path 全部指向一次性临时目录，套件内任何代码（含 MainWindow
   深处的惰性创建）落盘都落在临时区；
2. 主库零写权断言 —— setup() 时对真主库（dist_package/command_lib.db，另防御性
   监视 app/command_lib.db）取 sha256 指纹，atexit 复核：不一致则以退出码 42
   硬失败（os._exit，绕过一切后续清理），保证"碰了主库"绝不可能静默通过。

用法（必须在 import db / ai_bridge / theme / ui_main 之前调用）：
    import _smoke_env as _se
    _se.setup()

依赖：无第三方依赖；PyQt5 缺失时跳过 theme 劫持（不影响 db/ai_bridge 劫持）。
"""

import atexit
import hashlib
import os
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 真主库（AGENTS.md 2026-10-09 备忘：开发态主库 = dist_package/command_lib.db）
_MAIN_DB = os.path.join(_REPO, "dist_package", "command_lib.db")
# 防御性监视：历史上下文中 app/ 下落过库；不存在时自动跳过
_DEFENSIVE_DBS = [os.path.join(_REPO, "app", "command_lib.db")]

_state = {"tmpdir": None, "fingerprints": {}}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint_real_dbs():
    fps = {}
    for p in [_MAIN_DB] + _DEFENSIVE_DBS:
        if os.path.exists(p):
            fps[p] = _sha256(p)
    return fps


def _atexit_verify():
    """套件退出兜底断言：受监视真库指纹必须与 setup() 时一致。"""
    if not _state["fingerprints"]:
        return
    changed = []
    for p, before in _state["fingerprints"].items():
        if not os.path.exists(p):
            changed.append((p, before, "<GONE>"))
            continue
        after = _sha256(p)
        if after != before:
            changed.append((p, before, after))
    if changed:
        print("\n" + "!" * 68)
        print("[SMOKE-ENV] 主库零写权断言失败 —— 真库在套件运行期间被改动：")
        for p, before, after in changed:
            print("  %s\n    before=%s\n    after =%s" % (p, before, after))
        print("!" * 68)
        os._exit(42)
    print("[SMOKE-ENV] 主库零接触实证：指纹前后一致（监视 %d 个文件）"
          % len(_state["fingerprints"]))


def verify_main_db_untouched():
    """供套件主体收尾处显式调用；True=指纹一致。atexit 兜底不受此影响。"""
    if not _state["fingerprints"]:
        return False
    for p, before in _state["fingerprints"].items():
        if not os.path.exists(p) or _sha256(p) != before:
            return False
    return True


def setup():
    """劫持全部路径解析到一次性临时目录 + 安装主库指纹断言。返回 tmpdir。

    必须在任何 app 模块（db/ai_bridge/theme/ui_main…）被 import 之前调用——
    ui_main 等模块 `from db import get_base_dir` 按名绑定，补丁须先于其导入。
    ai_bridge 的 sessions_dir/config_path 是调用期解析 `_base_dir()`，
    后打补丁也生效；此处统一在 setup 内完成。
    """
    if _state["tmpdir"] is not None:
        return _state["tmpdir"]
    tmpdir = tempfile.mkdtemp(prefix="ntbx_smoke_")
    _state["tmpdir"] = tmpdir
    _state["fingerprints"] = _fingerprint_real_dbs()
    atexit.register(_atexit_verify)

    # 1) db 层：基准目录与库路径 → tmpdir（ui_main/theme 的按名导入由此生效）
    import db as _db
    _db.get_base_dir = lambda: tmpdir
    _db.get_db_path = lambda: os.path.join(tmpdir, "command_lib.db")

    # 2) ai_bridge 层：sessions/cases/config 三处共用的 _base_dir → tmpdir
    import ai_bridge as _ab
    _ab._base_dir = lambda: tmpdir

    # 3) theme 层：显式劫持兜底（db 导入失败的回退分支也不会落到源码目录）；
    #    PyQt5 缺失时 theme 不可导入则跳过（db/ai_bridge 劫持不受影响）
    try:
        import theme as _th
        _th.state_file_path = lambda: os.path.join(tmpdir, "ui_state.json")
    except Exception:
        pass

    print("[SMOKE-ENV] 测试环境就绪：临时区=%s；监视真库 %d 个"
          % (tmpdir, len(_state["fingerprints"])))
    return tmpdir
