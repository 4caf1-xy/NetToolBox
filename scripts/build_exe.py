# -*- coding: utf-8 -*-
"""
build_exe.py —— NetToolBox 一键打包脚本（在**联网开发机**上执行）

用法：
    python scripts/build_exe.py            # 正常打包
    python scripts/build_exe.py --check    # 只做打包前检查，不实际打包

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

# 本脚本位于 scripts/，BASE 恒指仓库根（所有相对路径都以仓库根为基准）
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ★ 源码目录（2026-09 起代码/种子/样式都收进 app/；构建产物收拢到根 dist_package/）
APP_DIR = os.path.join(BASE, "app")
SEED_DIR = os.path.join(APP_DIR, "seed_data")
APP_NAME = "NetToolBox"
ENTRY = os.path.join(APP_DIR, "main.py")
DIST_DIR = os.path.join(BASE, "dist_package")
# ★ PyInstaller 中间产物定向（2026-09-29）：build/dist/spec 统一落仓库根（均已 gitignore），
#   不再污染 app/ 源码目录
WORK_DIR = os.path.join(BASE, "build")
OUT_DIR = os.path.join(BASE, "dist")
SPEC_PATH = BASE

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
                 "ui_generator.py", "ui_editor.py", "theme.qss") + tuple(AI_FILES):
        path = os.path.join(APP_DIR, name)
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
            py_compile.compile(os.path.join(APP_DIR, name), doraise=True,
                               cfile=os.path.join(APP_DIR, "__pycache__", name + "c"))
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
    code = run([sys.executable, ENTRY, "--validate-seed"])
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

    # ★ 中间产物让位（2026-09-29 重构）：构建前把根目录 build/ dist/ NetToolBox.spec
    #   一律 rename 成 .trash-<时间戳>——rename 是瞬时操作，任何环境都不阻塞。
    #   【禁止再生成 .stale-* 目录】；也【不要先尝试 rmtree】：某些受限环境
    #   （如 WorkBuddy 沙箱）对批量删除是进程级拦截，进程会被直接终止，
    #   连 except 降级的机会都没有。
    #   构建成功后再尝试彻底删除 .trash-*（正常环境零残留；删除仍被拦的环境
    #   保留待手动清，不影响构建，见 .gitignore 的 *.trash-* 条目）。
    import shutil
    import time as _time
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    trashed = []
    for junk in ("build", "dist", "%s.spec" % APP_NAME):
        path = os.path.join(BASE, junk)
        if not os.path.exists(path):
            continue
        t = "%s.trash-%s" % (path, stamp)
        try:
            os.rename(path, t)
            trashed.append(t)
            print("· 旧产物已让位：%s → %s" % (junk, os.path.basename(t)))
        except Exception as exc:
            print("· [!!] 旧产物移位失败：%s（%s）" % (junk, exc))
            print("      请手动删除 %s 后重试打包" % path)
            return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",          # 不交互
        "--onefile",                # 单文件，U 盘拷贝即用
        "--windowed",               # 不弹黑框控制台
        "--name", APP_NAME,
        "--add-data", os.path.join(APP_DIR, "seed_data") + ";seed_data",   # 种子库打进 exe（Windows 用 ; 分隔）
        "--add-data", os.path.join(APP_DIR, "theme.qss") + ";.",           # 全站样式表（theme.py 运行时读取；缺失有内置兜底）
        "--workpath", WORK_DIR,     # 中间产物定向根 build/（不再落在 app/ 下）
        "--distpath", OUT_DIR,      # 产物定向根 dist/
        "--specpath", SPEC_PATH,    # spec 定向根（gitignore 已含 NetToolBox.spec）
        "--paths", APP_DIR,
    ]
    if os.path.isdir(os.path.join(VENDOR_DIR, "requests")):
        cmd += ["--paths", VENDOR_DIR]   # requests 及其依赖从 _vendor 解析并打包
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    cmd.append(ENTRY)

    code = run(cmd)
    if code != 0:
        print("打包失败，返回码 %d" % code)
        return code

    # 收拢产物到 dist_package/
    if not os.path.isdir(DIST_DIR):
        os.makedirs(DIST_DIR)
    src_exe = os.path.join(OUT_DIR, APP_NAME + ".exe")
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
    # ★ 构建成功后的收尾：尝试彻底删除本次让位的 .trash-*（正常环境零残留；
    #   受限环境删除被拦则保留，不影响本次构建结果，手动清掉即可）
    for t in trashed:
        try:
            if os.path.isdir(t):
                shutil.rmtree(t)
            else:
                os.remove(t)
        except Exception:
            print("· .trash 副本删除被环境拦截（不影响本次构建），可手动清理：")
            print("  " + t)
    return 0


if __name__ == "__main__":
    sys.exit(build())
