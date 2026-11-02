# -*- coding: utf-8 -*-
"""
smoke_tools.py —— 工具箱（R 批 子批2/3）冒烟

覆盖四案：
    [A] 子网/CIDR 计算器引擎：/0 /8 /12 /24 /28 /30 /31 /32 边界逐字段核对、
        CIDR 直填、掩码↔前缀互转、非法输入（前导零/越界/缺段/冲突/前缀越界）必须报错
    [B] 进制转换引擎：二/八/十/十六 四向一致、前缀识别与冲突、64 位上界、
        非法数位/负号/分隔符必须报错
    [C] curl 生成器引擎：三套 shell 的 golden 命令逐字符比对（含 & 中文 引号 特殊字符）、
        HEAD/GET 带体语义、多行体在 Windows 下拒绝、头注入拒绝、警告项
    [D] UI：工具箱三子页、子网 UI 边界与报错、进制 UI、curl UI 生成与复制、
        主窗 open_tools_tab 跳转、split 状态键

用法：python scripts/smoke_tools.py   （EXIT=0 全过）
"""

import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, "app"))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
import _smoke_env as _se
_se.setup()

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def expect_error(fn, *args, **kwargs):
    """返回 (是否抛 CalcError/CurlError, 消息)"""
    import tool_calc
    import tool_curl
    try:
        fn(*args, **kwargs)
    except (tool_calc.CalcError, tool_curl.CurlError) as exc:
        return True, str(exc)
    except Exception as exc:                       # 非预期异常也算失败，但消息不同
        return False, "%s: %s" % (type(exc).__name__, exc)
    return False, "（未报错）"


# ----------------------------------------------------------------------
# [A] 子网计算
# ----------------------------------------------------------------------
def case_a():
    import tool_calc as tc

    cases = [
        # (ip, prefix, network, broadcast, mask, first, last, count, total)
        ("1.2.3.4", 0, "0.0.0.0", "255.255.255.255", "0.0.0.0",
         "0.0.0.1", "255.255.255.254", 4294967294, 4294967296),
        ("10.0.0.0", 8, "10.0.0.0", "10.255.255.255", "255.0.0.0",
         "10.0.0.1", "10.255.255.254", 16777214, 16777216),
        ("172.16.0.0", 12, "172.16.0.0", "172.31.255.255", "255.240.0.0",
         "172.16.0.1", "172.31.255.254", 1048574, 1048576),
        ("192.168.1.130", 24, "192.168.1.0", "192.168.1.255", "255.255.255.0",
         "192.168.1.1", "192.168.1.254", 254, 256),
        ("192.168.1.130", 28, "192.168.1.128", "192.168.1.143", "255.255.255.240",
         "192.168.1.129", "192.168.1.142", 14, 16),
        ("10.20.30.40", 30, "10.20.30.40", "10.20.30.43", "255.255.255.252",
         "10.20.30.41", "10.20.30.42", 2, 4),
        ("10.0.0.0", 31, "10.0.0.0", "-", "255.255.255.254",
         "10.0.0.0", "10.0.0.1", 2, 2),
        ("10.1.1.1", 32, "10.1.1.1", "-", "255.255.255.255",
         "10.1.1.1", "10.1.1.1", 1, 1),
    ]
    for ip, prefix, net, bc, mask, first, last, count, total in cases:
        info = tc.subnet_info(ip, prefix)
        ok = (info["network"] == net and info["broadcast"] == bc
              and info["netmask"] == mask and info["first_host"] == first
              and info["last_host"] == last and info["host_count"] == count
              and info["total_addrs"] == total)
        check("A/%s-%d" % (ip, prefix), ok,
              "%s/%s/%s/%s-%s/%d/%d" % (info["network"], info["broadcast"],
                                        info["netmask"], info["first_host"],
                                        info["last_host"], info["host_count"],
                                        info["total_addrs"]))
    # 通配（反掩码）与 CIDR 表示
    info = tc.subnet_info("192.168.1.130", 28)
    check("A/反掩码", info["wildcard"] == "0.0.0.15", info["wildcard"])
    check("A/CIDR表示", info["cidr"] == "192.168.1.128/28", info["cidr"])
    check("A/私网识别", "私有" in info["scope"], info["scope"])
    check("A/分类C", info["class"].startswith("C 类"), info["class"])
    # CIDR 直填
    info = tc.subnet_info("192.168.1.130/26")
    check("A/CIDR直填", info["prefix"] == 26 and info["network"] == "192.168.1.128",
          info["network"])
    info = tc.subnet_info("192.168.1.130 26")
    check("A/空格分隔", info["prefix"] == 26, str(info["prefix"]))
    # 掩码 ↔ 前缀
    check("A/前缀转掩码", tc.prefix_to_mask(28) == "255.255.255.240",
          tc.prefix_to_mask(28))
    check("A/掩码转前缀", tc.mask_to_prefix("255.255.255.240") == 28, "")
    check("A/掩码往返", all(tc.mask_to_prefix(tc.prefix_to_mask(p)) == p
                          for p in range(0, 33)), "")
    check("A/非连续掩码拒绝", expect_error(tc.mask_to_prefix, "255.0.255.0")[0], "")

    # 非法输入（消息必须可读，不能是英文异常）
    bad_cases = [
        ("192.168.1.01", 24, "前导零"),
        ("192.168.1.256", 24, "超过 255"),
        ("1.2.3", 24, "4 段"),
        ("1.2.3.4.5", 24, "4 段"),
        ("abc", 24, "4 段"),
        ("", 24, "请输入"),
        ("10.0.0.1", 33, "越界"),
        ("10.0.0.1", "x", "整数"),
        ("10.0.0.1/24", 16, "冲突"),
        ("10.0.0.1", None, "缺前缀"),
        ("10.0.0.1 24/", 24, "缺前缀"),
    ]
    for ip, prefix, keyword in bad_cases:
        raised, msg = expect_error(tc.subnet_info, ip, prefix)
        check("A/拒绝-%r" % (ip or "(空)"), raised and keyword in msg, msg[:60])


# ----------------------------------------------------------------------
# [B] 进制转换
# ----------------------------------------------------------------------
def case_b():
    import tool_calc as tc

    # 四向一致：255 的四种写法必须互相等价
    forms = [("255", 10), ("0xFF", 16), ("0o377", 8), ("0b11111111", 2)]
    outs = [tc.convert(t, b) for t, b in forms]
    same = all(o["value"] == 255 and o["dec"] == "255" and o["hex"] == "0xFF"
               and o["oct"] == "0o377" and o["bin"] == "0b11111111" for o in outs)
    check("B/四向一致", same, str([o["dec"] for o in outs]))
    check("B/二进制分组", outs[0]["bin_grouped"] == "1111 1111",
          outs[0]["bin_grouped"])
    check("B/有效位", outs[0]["bits"] == 8, str(outs[0]["bits"]))
    # 自动识别
    check("B/自动hex", tc.convert_auto("0xff")["dec"] == "255", "")
    check("B/自动bin", tc.convert_auto("0b1111")["dec"] == "15", "")
    check("B/自动dec", tc.convert_auto("42")["dec"] == "42", "")
    # 边界
    check("B/零", tc.convert("0", 10)["bin"] == "0b0"
          and tc.convert("0", 10)["bits"] == 1, "")
    check("B/64位上界", tc.convert("18446744073709551615", 10)["dec"]
          == "18446744073709551615", "")
    check("B/32位全F", tc.convert("0xFFFFFFFF", 16)["dec"] == "4294967295", "")
    check("B/超64位拒绝", expect_error(tc.convert, "18446744073709551616", 10)[0], "")
    # 非法
    bad = [
        ("", 10, "请输入"),
        ("-1", 10, "非负"),
        ("0x1F", 10, "不一致"),
        ("0b10", 16, "不一致"),
        ("1_000", 10, "分隔符"),
        ("12G", 16, ""),          # 16 进制里 G 非法
        ("2", 2, "合法数位"),
        ("8", 8, "合法数位"),
        ("zz", 16, "合法数位"),
        ("0x", 16, "没有数值"),
    ]
    for text, base, keyword in bad:
        raised, msg = expect_error(tc.convert, text, base)
        check("B/拒绝-%r(%d)" % (text, base), raised and (keyword in msg or not keyword),
              msg[:60])
    check("B/未知进制拒绝", expect_error(tc.convert, "1", 3)[0], "")


# ----------------------------------------------------------------------
# [C] curl 生成器
# ----------------------------------------------------------------------
GOLDEN_SPEC = {
    "url": "https://h/p?a=1&b=2", "method": "POST", "headers": "X-A: b c",
    "data": '{"k":"v"}', "timeout": 5, "retries": 2,
    "resolve": "h:443:10.0.0.5", "location": True,
}
GOLDEN = {
    "posix": ("curl -X POST -L --max-time 5 --retry 2 --resolve h:443:10.0.0.5 "
              "-H 'X-A: b c' -d '{\"k\":\"v\"}' 'https://h/p?a=1&b=2'"),
    "cmd": ("curl -X POST -L --max-time 5 --retry 2 --resolve h:443:10.0.0.5 "
            "-H \"X-A: b c\" -d \"{\\\"k\\\":\\\"v\\\"}\" \"https://h/p?a=1&b=2\""),
    "powershell": ("curl -X POST -L --max-time 5 --retry 2 --resolve h:443:10.0.0.5 "
                   "-H 'X-A: b c' -d '{\\\"k\\\":\\\"v\\\"}' 'https://h/p?a=1&b=2'"),
}


def case_c():
    import tool_curl as tc

    # 三套 golden：逐字符比对（转义改错立刻红）
    for shell, expected in GOLDEN.items():
        got = tc.build_curl(GOLDEN_SPEC, shell)["cmd"]
        check("C/golden-%s" % shell, got == expected, got)

    # 特殊字符：中文 / & / 单引号 / 双引号 / 空格 / 尖括号
    spec = {
        "url": "https://h/搜索?q=a&b=中 文&x=1",
        "method": "PUT",
        "headers": "X-Note: a'b\"c<d>",
        "data": "line1",
        "timeout": None, "retries": 0, "resolve": "",
    }
    ok_url, _ = expect_error(tc.normalize_url, spec["url"])
    check("C/URL空格拒绝", ok_url, "")     # 空白必须报错，不做静默编码
    spec["url"] = "https://h/搜索?q=a&b=%E4%B8%AD&x=1"
    for shell in tc.SHELLS:
        cmd = tc.build_curl(spec, shell)["cmd"]
        good = cmd.startswith("curl -X PUT") and "X-Note" in cmd
        if shell == "posix":
            good = good and "'X-Note: a'\\''b\"c<d>'" in cmd and cmd.endswith("'")
        elif shell == "cmd":
            # cmd：整段双引号包裹，内部 " 转义为 \"，故 a'b\"c<d> 整体不变形
            good = good and '"X-Note: a\'b\\"c<d>"' in cmd
        else:
            good = good and "'X-Note: a''b\\\"c<d>'" in cmd
        check("C/特殊字符-%s" % shell, good, cmd[:120])

    # 语义：HEAD 用 --head；GET 带体必须显式 -X GET；GET 无体不加 -X
    head = tc.build_curl({"url": "https://h/", "method": "HEAD"}, "posix")["cmd"]
    check("C/HEAD", "--head" in head and "-X" not in head, head)
    get_body = tc.build_curl({"url": "https://h/", "method": "GET",
                              "data": "x=1"}, "posix")["cmd"]
    check("C/GET带体", "-X GET" in get_body and "-d x=1" in get_body, get_body)
    get_plain = tc.build_curl({"url": "https://h/", "method": "GET"}, "posix")["cmd"]
    check("C/GET无体", "-X" not in get_plain, get_plain)

    # --resolve 多行 & 重试为 0 时不写 --retry
    multi = tc.build_curl({"url": "https://h/", "resolve":
                           "a.com:443:10.0.0.1\nb.com:80:10.0.0.2",
                           "retries": 0}, "posix")["cmd"]
    check("C/resolve多行", multi.count("--resolve") == 2 and "--retry" not in multi, multi)

    # 警告：JSON 无 Content-Type / 含 %
    warn = tc.build_curl({"url": "https://h/", "data": '{"a":1}'}, "posix")["warnings"]
    check("C/警告-ContentType", any("Content-Type" in w for w in warn), str(warn))
    warn2 = tc.build_curl({"url": "https://h/%7Bx%7D"}, "cmd")["warnings"]
    check("C/警告-百分号", any("%" in w for w in warn2), str(warn2))
    no_warn = tc.build_curl({"url": "https://h/", "data": '{"a":1}',
                             "headers": "Content-Type: application/json"}, "posix")["warnings"]
    check("C/无多余警告", no_warn == [], str(no_warn))

    # 非法输入
    bad = [
        ({"url": ""}, "URL 不能为空"),
        ({"url": "api.example.com/x"}, "协议头"),
        ({"url": "ftp://h/x"}, "只支持 http"),
        ({"url": "https://h/a b"}, "空白"),
        ({"url": "https://h/\nx"}, "换行"),
        ({"url": "https://h/", "headers": "BadLine"}, "冒号"),
        ({"url": "https://h/", "headers": ": v"}, "名称为空"),
        ({"url": "https://h/", "headers": "X: a\rInjected: b"}, "回车"),
        ({"url": "https://h/", "headers": "X Y: v"}, "非法字符"),
        ({"url": "https://h/", "resolve": "h:443"}, "格式非法"),
        ({"url": "https://h/", "resolve": "h:99999:1.1.1.1"}, "端口越界"),
        ({"url": "https://h/", "resolve": "h:443:1.1.1.999"}, "IP 段越界"),
        ({"url": "https://h/", "method": "FETCH"}, "方法"),
        ({"url": "https://h/", "timeout": "abc"}, "整数"),
        ({"url": "https://h/", "timeout": 9999}, "超出范围"),
        ({"url": "https://h/", "method": "HEAD", "data": "x"}, "HEAD"),
    ]
    for spec, keyword in bad:
        raised, msg = expect_error(tc.build_curl, spec, "posix")
        check("C/拒绝-%s" % keyword, raised and keyword in msg, msg[:70])

    # 多行体：POSIX 可用，Windows 两套明确拒绝（不静默替换换行）
    multi_body = {"url": "https://h/", "method": "POST", "data": "{\n\"a\":1\n}"}
    ok_posix = "\\n" not in tc.build_curl(multi_body, "posix")["cmd"]
    check("C/多行体-posix", ok_posix, "")
    for shell in ("cmd", "powershell"):
        raised, msg = expect_error(tc.build_curl, multi_body, shell)
        check("C/多行体-%s拒绝" % shell, raised and "多行" in msg, msg[:60])

    # 粘贴还原（★ 机械证据）：
    #   把生成的命令行交给「目标平台真正的参数解析器」反解，argv 必须与构造时一字不差 ——
    #   POSIX 用 shlex.split（bash 词法），Windows CMD 用 CommandLineToArgvW（CRT 规则，
    #   即 curl.exe 实际收到的参数）。这条比"肉眼看引号对不对"强得多。
    import shlex
    spec2 = {
        "url": "https://h/p?a=1&b=2", "method": "POST",
        "headers": "X-A: b c\nAuthorization: Bearer a'b\"c",
        "data": '{"k":"v"}', "timeout": 5, "retries": 2,
        "resolve": "h:443:10.0.0.5", "location": True,
    }
    posix_out = tc.build_curl(spec2, "posix")
    check("C/posix-argv还原",
          shlex.split(posix_out["cmd"]) == ["curl"] + posix_out["argv"],
          str(shlex.split(posix_out["cmd"])[:6]))
    if sys.platform == "win32":
        import ctypes
        cmd_out = tc.build_curl(spec2, "cmd")
        argv_w = ctypes.windll.shell32.CommandLineToArgvW
        argv_w.restype = ctypes.POINTER(ctypes.c_wchar_p)
        count = ctypes.c_int(0)
        ptr = argv_w(cmd_out["cmd"], ctypes.byref(count))
        parsed = [ptr[i] for i in range(count.value)]
        ctypes.windll.kernel32.LocalFree(ptr)
        check("C/cmd-argv还原", parsed == ["curl"] + cmd_out["argv"],
              str(parsed[:6]))
        # 中文 + 尖括号 + 反斜杠路径风格的额外一档（cmd 下最容易翻车的组合）
        spec3 = {"url": "https://h/搜索?q=%E4%B8%AD", "method": "GET",
                 "headers": "X-Ref: a<b>\\c"}
        cmd3 = tc.build_curl(spec3, "cmd")
        ptr = argv_w(cmd3["cmd"], ctypes.byref(count))
        parsed3 = [ptr[i] for i in range(count.value)]
        ctypes.windll.kernel32.LocalFree(ptr)
        check("C/cmd-argv还原-中文反斜杠", parsed3 == ["curl"] + cmd3["argv"],
              str(parsed3))

    # 三套一次生成
    allc = tc.build_all(GOLDEN_SPEC)
    check("C/build_all", set(allc) == set(tc.SHELLS)
          and allc["posix"]["cmd"] == GOLDEN["posix"], str(list(allc)))


# ----------------------------------------------------------------------
# [D] UI
# ----------------------------------------------------------------------
def case_d():
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import db as dbmod
    import ui_main
    import ui_tools

    tab = ui_tools.ToolsTab()
    check("D/三子页", tab.sub.count() == 3, str(tab.sub.count()))
    check("D/子页标题", [tab.sub.tabText(i) for i in range(3)]
          == ["子网计算器", "进制转换", "curl 生成器"],
          str([tab.sub.tabText(i) for i in range(3)]))

    # 子网 UI：/31 边界 + 非法输入
    sub = tab.tab_subnet
    sub.ed_ip.setText("10.0.0.0")
    sub.cmb_prefix.setCurrentIndex(31)
    sub.on_calc()
    check("D/UI-31可用数", sub.tbl.item(7, 1).text() == "2", sub.tbl.item(7, 1).text())
    check("D/UI-31广播", sub.tbl.item(5, 1).text() == "-", sub.tbl.item(5, 1).text())
    sub.cmb_prefix.setCurrentIndex(32)
    sub.on_calc()
    check("D/UI-32可用数", sub.tbl.item(7, 1).text() == "1", sub.tbl.item(7, 1).text())
    sub.ed_ip.setText("10.0.0.999")
    sub.on_calc()
    check("D/UI-非法清空", sub.tbl.rowCount() == 0, str(sub.tbl.rowCount()))
    check("D/UI-非法红字", "超过 255" in sub.lbl_err.text(), sub.lbl_err.text()[:60])
    sub.ed_ip.setText("192.168.1.130")
    sub.on_preset(28)
    check("D/UI-预设28", sub.cmb_prefix.currentIndex() == 28
          and sub.tbl.item(4, 1).text() == "192.168.1.128",
          sub.tbl.item(4, 1).text())

    # 进制 UI
    base = tab.tab_base
    base.cmb_base.setCurrentIndex(0)      # 自动识别（0=自动 / 1=二 / 2=八 / 3=十 / 4=十六）
    base.ed_value.setText("0xFF")
    check("D/UI-hex转dec", base.tbl.item(3, 1).text() == "255", base.tbl.item(3, 1).text())
    base.ed_value.setText("2")
    base.cmb_base.setCurrentIndex(1)      # 二进制
    check("D/UI-非法红字", "合法数位" in base.lbl_err.text(), base.lbl_err.text()[:50])
    check("D/UI-非法清表", base.tbl.rowCount() == 0, str(base.tbl.rowCount()))

    # curl UI：生成 + 复制
    curl = tab.tab_curl
    curl.ed_url.setText("https://h/p?a=1&b=2")
    curl.cmb_method.setCurrentText("POST")
    curl.ed_headers.setPlainText("Content-Type: application/json")
    curl.ed_data.setPlainText('{"k":"v"}')
    curl.cmb_shell.setCurrentIndex(0)      # cmd
    curl.on_generate()
    out = curl.txt_out.toPlainText()
    check("D/UI-cmd生成", out.startswith("curl -X POST") and '"https' in out, out[:60])
    clip = QApplication.clipboard()
    clip.setText("SENTINEL")
    curl.on_copy()
    check("D/UI-复制", clip.text() == out, clip.text()[:40])
    # 切 shell 自动重算
    curl.cmb_shell.setCurrentIndex(2)      # posix
    check("D/UI-posix重算", curl.txt_out.toPlainText().endswith("'"), 
          curl.txt_out.toPlainText()[-20:])
    # 错误态：URL 无协议 → 输出清空 + 红字
    curl.ed_url.setText("api.example.com/x")
    curl.on_generate()
    check("D/UI-错误清空", curl.txt_out.toPlainText() == "", "")
    check("D/UI-错误红字", "协议头" in curl.lbl_err.text(), curl.lbl_err.text()[:50])

    # split 键
    state = tab.split_state()
    check("D/split键", isinstance(state.get("tools_subnet_split"), list)
          and isinstance(state.get("tools_curl_split"), list), str(state))
    tab.restore_split_state({"tools_subnet_split": [400, 500],
                             "tools_curl_split": [480, 520]})
    check("D/split恢复", tab.tab_subnet.split.sizes()[0] >= 0, "")

    # 主窗集成：工具箱 Tab + 子页跳转
    db = dbmod.Database(":memory:")
    db.import_seed_dir()
    w = ui_main.MainWindow(db)
    w.open_tools_tab("curl")
    check("D/主窗跳转", w.tabs.currentWidget() is w.tab_tools
          and w.tab_tools.sub.currentIndex() == 2,
          str(w.tab_tools.sub.currentIndex()))
    w.open_handbook_tab()
    check("D/主窗手册", w.tabs.currentWidget() is w.tab_handbook, "")
    w.deleteLater()


def main():
    import traceback
    for tag, fn in (("A", case_a), ("B", case_b), ("C", case_c), ("D", case_d)):
        try:
            fn()
            print("[case %s] done" % tag)
        except Exception as exc:
            traceback.print_exc()
            check("%s/未捕获异常" % tag, False, str(exc))
    print("=" * 66)
    fails = 0
    for name, ok, detail in RESULTS:
        if not ok:
            fails += 1
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name,
                              ("- " + str(detail)[:110]) if (detail and not ok) else ""))
    print("=" * 66)
    print("smoke_tools：%d 项，FAIL %d" % (len(RESULTS), fails))
    if _se.verify_main_db_untouched():
        print("[SMOKE-ENV] 主库零接触实证：指纹前后一致")
    else:
        fails += 1
        print("[SMOKE-ENV] FAIL 主库指纹异常")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
