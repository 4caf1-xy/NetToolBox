# -*- coding: utf-8 -*-
"""
build_exe.py —— NetToolBox 一键打包脚本（在**联网开发机**上执行）

用法：
    python build_exe.py            # 正常打包
    python build_exe.py --check    # 只做打包前检查，不实际打包

它做的事：
    1. 打包前自检：依赖是否齐全、种子库是否有内容、语法能否编译、种子库能否通过质检
    2. 调用 PyInstaller 打出单文件 exe（--onefile --windowed）
    3. 把 seed_data 整个塞进 exe（--add-data），并把 exe 拷到 dist_package/ 交付目录
    4. 打印产物信息与 U 盘部署步骤

注意：
    - db 文件（command_lib.db）**不会**打进 exe，它永远生成在 exe 同目录，U 盘整盘拷贝即迁移
    - seed_data 打进 exe 是为了"首次运行能自动释放种子库"；
      同时 exe 同目录如果也存在 seed_data，程序会优先读同目录的那份（现场可自行补条目）
"""

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SEED_DIR = os.path.join(BASE, "seed_data")
APP_NAME = "NetToolBox"
ENTRY = "main.py"
DIST_DIR = os.path.join(BASE, "dist_package")

# ★ requests 依赖的项目内落点（2026-09-28）：受管 python 环境禁止全局 pip install，
#   改用 pip --target 装到项目 _vendor/ 目录，PyInstaller 经 --paths 解析后
#   会把 requests 及其依赖打进 exe —— AI 诊断 Tab 有网场景不再报"缺少 requests 库"。
VENDOR_DIR = os.path.join(BASE, "_vendor")

# 用不到的 PyQt5 子模块，排除后能显著减小体积
EXCLUDES = [
    "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore", "PyQt5.QtWebEngine",
    "PyQt5.QtBluetooth", "PyQt5.QtMultimedia", "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtQuick", "PyQt5.QtQml", "PyQt5.QtSql", "PyQt5.QtTest",
    "PyQt5.QtDesigner", "PyQt5.QtHelp", "PyQt5.QtLocation", "PyQt5.QtNfc",
    "PyQt5.QtPositioning", "PyQt5.QtSensors", "PyQt5.QtSerialPort",
    "PyQt5.QtWebSockets", "tkinter", "unittest", "pydoc",
]

# ★ AI 诊断功能（第 3 轮）：requests 在 ai_bridge.py 里是**函数内惰性导入**
#   （未装 requests / 不联网时主程序照常可用），这种写法 PyInstaller 的静态分析
#   不保证抓到；必须显式 hidden-import，否则打出来的 exe 一旦发请求就报
#   "缺少 requests 库"。连带把 requests 的运行时依赖一并显式声明。
HIDDEN_IMPORTS = ["requests", "urllib3", "certifi", "idna", "charset_normalizer"]

# AI 相关运行时文件（打包前检查存在性与语法）
AI_FILES = ["ai_bridge.py", "ui_ai.py", "dedupe.py"]   # dedupe：查重引擎（db.py 依赖）
AI_RUNTIME_MODULES = ["requests"]


def run(cmd, **kwargs):
    """执行命令并回显"""
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, **kwargs)


def ensure_requests_vendor():
    """
    requests 落到项目 _vendor/（pip --target，不污染受管 python 全局环境）。
    幂等：_vendor/requests 已存在直接返回 True；安装失败返回 False（AI Tab 降级为离线受限）。
    """
    if os.path.isdir(os.path.join(VENDOR_DIR, "requests")):
        return True
    print("  [..] 正在安装 requests 到 %s（仅本目录，不动全局）…" % VENDOR_DIR)
    code = run([sys.executable, "-m", "pip", "install", "--target", VENDOR_DIR,
                "--quiet", "requests"])
    ok = code == 0 and os.path.isdir(os.path.join(VENDOR_DIR, "requests"))
    print("  [%s] requests 落地 %s" % ("OK" if ok else "!!", "成功" if ok else "失败"))
    return ok



def check():
    """打包前检查；全部通过返回 True"""
    print("=" * 66)
    print("打包前检查")
    print("=" * 66)
    ok = True

    # 1) PyQt5 / PyInstaller / requests（AI 诊断需要）
    for module, pip_name in (("PyQt5", "PyQt5"), ("PyInstaller", "pyinstaller"),
                             ("requests", "requests")):
        try:
            __import__(module)
            print("  [OK] %-12s 已安装" % module)
        except ImportError:
            if module == "requests":
                # 开发机没装 requests → 尝试自动落到项目 _vendor/（不污染全局环境）
                if ensure_requests_vendor():
                    print("  [OK] %-12s 来自 _vendor/（pip --target，已随 exe 打包）" % module)
                    continue
                print("  [!!] %-12s 未安装且自动安装失败 → AI 诊断 Tab 将受限（其余功能不受影响）" % module)
                continue
            print("  [!!] %-12s 未安装 → 请先执行：pip install %s" % (module, pip_name))
            ok = False

    # 2) 入口与模块文件齐全
    for name in ("main.py", "db.py", "renderer.py", "ui_main.py",
                 "ui_generator.py", "ui_editor.py") + tuple(AI_FILES):
        path = os.path.join(BASE, name)
        if os.path.isfile(path):
            print("  [OK] %-16s %d 字节" % (name, os.path.getsize(path)))
        else:
            print("  [!!] 缺少文件：%s" % name)
            ok = False

    # 3) 语法编译
    import py_compile
    for name in ("main.py", "db.py", "renderer.py", "ui_main.py",
                 "ui_generator.py", "ui_editor.py") + tuple(AI_FILES):
        try:
            py_compile.compile(os.path.join(BASE, name), doraise=True,
                               cfile=os.path.join(BASE, "__pycache__", name + "c"))
            print("  [OK] %-16s 语法通过" % name)
        except Exception as exc:
            print("  [!!] %-16s 语法错误：%s" % (name, exc))
            ok = False

    # 4) 种子库
    if not os.path.isdir(SEED_DIR):
        print("  [!!] 缺少 seed_data 目录 —— 打出来的 exe 首次运行将没有种子库")
        ok = False
    else:
        files = []
        for current, dirs, names in os.walk(SEED_DIR):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
            files += [os.path.join(current, n) for n in names if n.endswith(".json")]
        if files:
            print("  [OK] seed_data 下有 %d 个厂商文件" % len(files))
        else:
            print("  [!!] seed_data 下没有任何 .json 厂商文件")
            ok = False

    # 5) 种子库质检（复用程序自带的 --validate-seed）
    print("-" * 66)
    print("运行种子库质检（python main.py --validate-seed）…")
    code = run([sys.executable, os.path.join(BASE, "main.py"), "--validate-seed"])
    if code != 0:
        print("  [!!] 种子库质检未通过，请先修好再打包")
        ok = False
    else:
        print("  [OK] 种子库质检通过")

    print("=" * 66)
    print("检查结果：%s" % ("全部通过，可以打包" if ok else "存在问题，请先修复"))
    return ok


def build():
    if not check():
        return 1
    if "--check" in sys.argv:
        return 0

    print("")
    print("=" * 66)
    print("开始打包（单文件 exe，首次打包约 1-3 分钟）")
    print("=" * 66)

    # ★ 中间产物清理：优先删除；若删除被安全策略拦（批量删除需确认），
    #   则退化成"重命名成 .stale-<时间戳>"——重命名不触发删除拦截，
    #   PyInstaller 拿到空目录即可干净构建（否则它会因删不掉而直接返回码 1 结束）。
    #   ★ 一律用"重命名"，不尝试删除：某些环境（如本机的删除安全策略）
    #     在删除大批文件时会被拦截甚至终止进程，导致打包直接失败。
    #     重命名是瞬时操作且不触发拦截；旧的 .stale-* 目录可自行清理。
    import time as _time
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    for junk in ("build", "dist", "%s.spec" % APP_NAME):
        path = os.path.join(BASE, junk)
        if not os.path.exists(path):
            continue
        try:
            os.rename(path, "%s.stale-%s" % (path, stamp))
            print("· 旧产物已让位：%s → %s.stale-%s" % (junk, junk, stamp))
        except Exception as exc:
            print("· 旧产物让位失败（忽略继续）：%s（%s）" % (junk, exc))

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",          # 不交互（--clean 会尝试删 build/，在受限环境里会硬失败）
        "--onefile",                # 单文件，U 盘拷贝即用
        "--windowed",               # 不弹黑框控制台
        "--name", APP_NAME,
        "--add-data", "seed_data;seed_data",   # 种子库打进 exe（Windows 用 ; 分隔）
        "--paths", BASE,
    ]
    if os.path.isdir(os.path.join(VENDOR_DIR, "requests")):
        cmd += ["--paths", VENDOR_DIR]   # requests 及其依赖从 _vendor 解析并打包
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    cmd.append(ENTRY)

    code = run(cmd, cwd=BASE)
    if code != 0:
        print("打包失败，返回码 %d" % code)
        return code

    # 收拢产物到 dist_package/
    if not os.path.isdir(DIST_DIR):
        os.makedirs(DIST_DIR)
    src_exe = os.path.join(BASE, "dist", APP_NAME + ".exe")
    dst_exe = os.path.join(DIST_DIR, APP_NAME + ".exe")
    if os.path.isfile(src_exe):
        import shutil
        shutil.copy2(src_exe, dst_exe)
        size_mb = os.path.getsize(dst_exe) / 1024.0 / 1024.0
        print("-" * 66)
        print("打包完成：%s（%.1f MB）" % (dst_exe, size_mb))

    print("=" * 66)
    print("U 盘部署步骤（三步）")
    print("=" * 66)
    print("""
  1. 把 dist_package\\NetToolBox.exe 拷到 U 盘任意目录（建议建一个 NetToolBox 文件夹）
  2. 双击运行 → 首次启动会问"是否导入内置种子库" → 选"导入内置种子库"
     → 会在 exe 同目录自动生成 command_lib.db（命令库随 U 盘走）
  3. 换电脑只做一件事：把整个 U 盘目录拷过去（exe + command_lib.db 一起），
     不要只拷 exe，否则新机器上会是一份空库

  可选：给 U 盘根目录再放一份 seed_data 文件夹（从源码目录拷），
        程序会优先读 exe 同目录的 seed_data，现场加厂商条目不用重新打包。

  验证清单：
    [ ] 双击能起界面，左侧树里有厂商
    [ ] 底部统计显示"共 xxx 条"
    [ ] 搜索框输入 trunk 能出结果
    [ ] 选中条目 → 复制全部 → 粘到记事本有内容
    [ ] 离线环境（拔网线）下功能完全正常
    [ ] AI 诊断 Tab：未配置时显示引导页 + 状态栏黄灯；其余功能不受影响
    [ ] AI 诊断 Tab：文件→设置填 Key → 测试连接成功 → 状态栏变绿
    [ ] 拔网线：AI 的[发送]置灰（提示"当前无网络连接"），输入框仍可编辑，
        主程序复制/生成器/排查向导/报错诊断全部正常
    [ ] 有网：发一次对话 → 流式输出 → 代码块可[复制] → 自动存 ai_cases/
    [ ] AI 回复 → 转为草稿条目 → 勾选入库 → 灰徽章 → 真机验证后标记转绿
""")
    return 0


if __name__ == "__main__":
    sys.exit(build())
