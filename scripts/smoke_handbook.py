# -*- coding: utf-8 -*-
"""
smoke_handbook.py —— 速查手册（R 批 子批1）冒烟

覆盖六案（[E] 需 PyQt5，其余纯数据）：
    [A] 数据加载：七本齐全 + 顺序 + 规模（56 组 / 518 条）+ 无结构问题
    [B] HTTP 状态码事实核对：63 个码全在、关键语义逐条对（RFC 9110 §15 口径）、
        306/418 标 Unused、扩展码标注来源 RFC
    [C] 端口事实核对：关键端口与 IANA 服务名/传输层逐一比对、514 双协议、
        未登记端口必须显式标注、端口+协议唯一
    [D] 其余六册覆盖度与关键条目（git/linux/vim/ssh/netcmd 抽查「有这条」）
    [E] UI：九 Tab、分册树 7 节点、分组切换表格、本册搜索带「分组」列、
        复制整组 TSV、Ctrl+K 四库命中并跳转定位、split 状态往返
    [F] CLI 与反例：--validate-handbook rc=0；四类坏数据（空单元格 / 端口重复 /
        状态码放错组 / 缺一册）必须 rc=1 且问题文案可读

用法：python scripts/smoke_handbook.py   （EXIT=0 全过）
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

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


# ----------------------------------------------------------------------
# 期望值（权威口径，写死在断言里：数据被改错时会红）
# ----------------------------------------------------------------------
EXPECTED_DOCS = ["http-status", "ports", "git", "linux", "vim", "ssh", "netcmd"]

EXPECTED_HTTP = {
    "100": ("Continue", "RFC 9110"),
    "101": ("Switching Protocols", "RFC 9110"),
    "103": ("Early Hints", "RFC 8297"),
    "200": ("OK", "RFC 9110"),
    "201": ("Created", "RFC 9110"),
    "202": ("Accepted", "RFC 9110"),
    "204": ("No Content", "RFC 9110"),
    "206": ("Partial Content", "RFC 9110"),
    "301": ("Moved Permanently", "RFC 9110"),
    "304": ("Not Modified", "RFC 9110"),
    "305": ("Use Proxy", "RFC 9110"),
    "306": ("(Unused)", "RFC 9110"),
    "307": ("Temporary Redirect", "RFC 9110"),
    "308": ("Permanent Redirect", "RFC 9110"),
    "400": ("Bad Request", "RFC 9110"),
    "401": ("Unauthorized", "RFC 9110"),
    "402": ("Payment Required", "RFC 9110"),
    "403": ("Forbidden", "RFC 9110"),
    "404": ("Not Found", "RFC 9110"),
    "405": ("Method Not Allowed", "RFC 9110"),
    "409": ("Conflict", "RFC 9110"),
    "413": ("Content Too Large", "RFC 9110"),
    "414": ("URI Too Long", "RFC 9110"),
    "415": ("Unsupported Media Type", "RFC 9110"),
    "416": ("Range Not Satisfiable", "RFC 9110"),
    "417": ("Expectation Failed", "RFC 9110"),
    "418": ("(Unused)", "RFC 9110"),
    "421": ("Misdirected Request", "RFC 9110"),
    "422": ("Unprocessable Content", "RFC 9110"),
    "425": ("Too Early", "RFC 8470"),
    "426": ("Upgrade Required", "RFC 9110"),
    "428": ("Precondition Required", "RFC 6585"),
    "429": ("Too Many Requests", "RFC 6585"),
    "431": ("Request Header Fields Too Large", "RFC 6585"),
    "451": ("Unavailable For Legal Reasons", "RFC 7725"),
    "500": ("Internal Server Error", "RFC 9110"),
    "501": ("Not Implemented", "RFC 9110"),
    "502": ("Bad Gateway", "RFC 9110"),
    "503": ("Service Unavailable", "RFC 9110"),
    "504": ("Gateway Timeout", "RFC 9110"),
    "505": ("HTTP Version Not Supported", "RFC 9110"),
    "506": ("Variant Also Negotiates", "RFC 2295"),
    "507": ("Insufficient Storage", "RFC 4918"),
    "508": ("Loop Detected", "RFC 5842"),
    "510": ("Not Extended", "RFC 2774"),
    "511": ("Network Authentication Required", "RFC 6585"),
}

# (端口, 传输层, IANA 服务名片段)
EXPECTED_PORTS = [
    (20, "TCP", "ftp-data"), (21, "TCP", "ftp"), (22, "TCP", "ssh"),
    (23, "TCP", "telnet"), (25, "TCP", "smtp"), (53, "TCP/UDP", "domain"),
    (67, "UDP", "bootps"), (68, "UDP", "bootpc"), (69, "UDP", "tftp"),
    (80, "TCP", "http"), (88, "TCP/UDP", "kerberos"), (110, "TCP", "pop3"),
    (111, "TCP/UDP", "sunrpc"), (123, "UDP", "ntp"), (135, "TCP", "msrpc"),
    (137, "UDP", "netbios-ns"), (139, "TCP", "netbios-ssn"),
    (143, "TCP", "imap"), (161, "UDP", "snmp"), (162, "UDP", "snmptrap"),
    (179, "TCP", "bgp"), (389, "TCP/UDP", "ldap"), (443, "TCP/UDP", "https"),
    (445, "TCP", "microsoft-ds"), (465, "TCP", "smtps"),
    (500, "UDP", "isakmp"), (514, "UDP", "syslog"), (514, "TCP", "shell"),
    (515, "TCP", "printer"), (520, "UDP", "route"), (546, "UDP", "dhcpv6-client"),
    (547, "UDP", "dhcpv6-server"), (554, "TCP", "rtsp"), (587, "TCP", "submission"),
    (636, "TCP/UDP", "ldaps"), (993, "TCP", "imaps"), (995, "TCP", "pop3s"),
    (1080, "TCP", "socks"), (1194, "UDP", "openvpn"), (1433, "TCP", "ms-sql-s"),
    (1521, "TCP", "ncube-lm"), (1701, "UDP", "l2tp"), (1723, "TCP", "pptp"),
    (1812, "UDP", "radius"), (1813, "UDP", "radius-acct"), (2049, "TCP/UDP", "nfs"),
    (3306, "TCP", "mysql"), (3389, "TCP/UDP", "ms-wbt-server"),
    (3784, "UDP", "bfd-control"), (4500, "UDP", "ipsec-nat-t"),
    (4789, "UDP", "vxlan"), (5060, "TCP/UDP", "sip"), (5061, "TCP", "sips"),
    (5432, "TCP", "postgresql"), (5672, "TCP", "amqp"), (5900, "TCP", "rfb"),
    (6081, "UDP", "geneve"), (8080, "TCP", "http-alt"), (8443, "TCP", "pcsync-https"),
]

UNREGISTERED_PORTS = (2181, 6379, 9200, 2055, 10050, 10051, 27017)


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def load():
    import handbook as hb
    return hb.load_docs()


def doc_rows(doc):
    for group in doc["groups"]:
        for row in group["rows"]:
            yield group, row


def http_codes(doc):
    """{码: {"phrase","semantic","source","group"}}"""
    out = {}
    for group, row in doc_rows(doc):
        out[row[0]] = {"phrase": row[1], "semantic": row[2],
                       "source": row[3], "group": group["name"]}
    return out


def port_map(doc):
    """(port, 单协议) → (服务名, 说明)；TCP/UDP 行展开成两条"""
    out = {}
    for _group, row in doc_rows(doc):
        port = int(row[0])
        proto = row[1]
        for single in ("TCP", "UDP") if proto == "TCP/UDP" else (proto,):
            out[(port, single)] = (row[2], row[3])
    return out


def all_text(docs):
    return "\n".join(row[0] + " " + row[1] + " " + row[2] + " " + row[3]
                     for doc in docs for _g, row in doc_rows(doc))


# ----------------------------------------------------------------------
# [A] 加载与规模
# ----------------------------------------------------------------------
def case_a():
    import handbook as hb
    docs, problems = load()
    check("A/七本齐全", len(docs) == 7, str(len(docs)))
    check("A/顺序", [d["doc_id"] for d in docs] == EXPECTED_DOCS,
          str([d["doc_id"] for d in docs]))
    check("A/无结构问题", problems == [], str(problems[:3]))
    groups = sum(hb.doc_stats(d)[0] for d in docs)
    rows = sum(hb.doc_stats(d)[1] for d in docs)
    check("A/组数56", groups == 56, str(groups))
    check("A/条数520", rows == 520, str(rows))
    check("A/汇总文案", hb.summary(docs) == "速查手册：7 本 / 56 组 / 520 条",
          hb.summary(docs))
    # 每本都要有来源与更新时间（离网手册的可信度靠这两栏）
    check("A/来源与更新", all(d.get("source") and d.get("updated") for d in docs), "")
    # 每本至少 5 组、40 条（覆盖度下限）
    thin = [(d["doc_id"], hb.doc_stats(d)) for d in docs
            if hb.doc_stats(d)[0] < 5 or hb.doc_stats(d)[1] < 40]
    check("A/覆盖度下限", not thin, str(thin))


# ----------------------------------------------------------------------
# [B] HTTP 状态码事实核对
# ----------------------------------------------------------------------
def case_b():
    docs, _ = load()
    doc = [d for d in docs if d["doc_id"] == "http-status"][0]
    codes = http_codes(doc)
    check("B/总数63", len(codes) == 63, str(len(codes)))
    missing = [c for c in EXPECTED_HTTP if c not in codes]
    check("B/关键码齐全", not missing, str(missing))
    bad_phrase = [(c, codes[c]["phrase"]) for c, (phrase, _rfc) in EXPECTED_HTTP.items()
                  if codes.get(c, {}).get("phrase") != phrase]
    check("B/短语逐条对", not bad_phrase, str(bad_phrase[:3]))
    # 来源 RFC 标注（扩展码必须写清出自哪个 RFC，不能含混）
    bad_src = [(c, codes[c]["source"]) for c, (_p, rfc) in EXPECTED_HTTP.items()
               if rfc not in codes.get(c, {}).get("source", "")]
    check("B/来源RFC标注", not bad_src, str(bad_src[:3]))
    # RFC 9110 明确的两个 Unused
    check("B/306-Unused", codes["306"]["phrase"] == "(Unused)"
          and "Unused" in codes["306"]["source"], codes["306"]["source"])
    check("B/418-Unused", codes["418"]["phrase"] == "(Unused)"
          and "Unused" in codes["418"]["source"]
          and "RFC 2324" in codes["418"]["semantic"], codes["418"]["source"])
    check("B/305已弃用", "弃用" in codes["305"]["source"], codes["305"]["source"])
    # 语义关键点（现场最容易用错的四处）
    check("B/401带WWW-Authenticate", "WWW-Authenticate" in codes["401"]["semantic"], "")
    check("B/403是权限", "权限" in codes["403"]["semantic"], "")
    check("B/502vs504", "上游" in codes["502"]["semantic"]
          and "上游" in codes["504"]["semantic"], "")
    check("B/413新名", "Content Too Large" == codes["413"]["phrase"]
          and "Payload Too Large" in codes["413"]["source"], codes["413"]["source"])
    # 分组与号段一致（validate 也查，这里再断言一次）
    seg_ok = True
    for group, row in doc_rows(doc):
        if row[0][0] != group["name"][0]:
            seg_ok = False
    check("B/号段分组一致", seg_ok, "")


# ----------------------------------------------------------------------
# [C] 端口事实核对
# ----------------------------------------------------------------------
def case_c():
    docs, _ = load()
    doc = [d for d in docs if d["doc_id"] == "ports"][0]
    pmap = port_map(doc)
    bad = []
    for port, proto, service in EXPECTED_PORTS:
        protos = ("TCP", "UDP") if proto == "TCP/UDP" else (proto,)
        for single in protos:
            got = pmap.get((port, single))
            if not got or service not in got[0]:
                bad.append("%d/%s→%r" % (port, single, got[0] if got else None))
    check("C/关键端口逐条对", not bad, str(bad[:4]))
    # 514 同号双协议：TCP=shell、UDP=syslog（查端口必须连协议看）
    check("C/514双协议", "shell" in pmap[(514, "TCP")][0]
          and "syslog" in pmap[(514, "UDP")][0],
          "%s | %s" % (pmap[(514, "TCP")][0], pmap[(514, "UDP")][0]))
    # 未登记端口必须标注，不能冒充 IANA 登记
    unreg = [p for p in UNREGISTERED_PORTS
             if not any(str(p) == row[0] and "未登记" in row[2]
                        for _g, row in doc_rows(doc))]
    check("C/未登记显式标注", not unreg, str(unreg))
    # 号码与协议合法性 + 唯一性（直接复用引擎校验）
    import handbook as hb
    problems = hb._check_ports(doc, "ports")
    check("C/端口自检零问题", problems == [], str(problems[:3]))
    # 数量与「IANA 收录 + 惯例标注」两条线都成立
    n_rows = sum(1 for _g, _r in doc_rows(doc))
    check("C/端口条数77", n_rows == 77, str(n_rows))


# ----------------------------------------------------------------------
# [D] 其余六册关键条目
# ----------------------------------------------------------------------
def case_d():
    docs, _ = load()
    blob = {d["doc_id"]: all_text([d]) for d in docs}

    check("D/git-危险提示", "git reset --hard" in blob["git"]
          and "force-with-lease" in blob["git"] and "reflog" in blob["git"], "")
    check("D/git-撤销三分工", "restore" in blob["git"] and "revert" in blob["git"]
          and "reset" in blob["git"], "")
    check("D/linux-systemd", "systemctl status" in blob["linux"]
          and "journalctl -xe" in blob["linux"], "")
    check("D/linux-网络", "ss -tunlp" in blob["linux"] and "ip route" in blob["linux"]
          and "tcpdump" in blob["linux"], "")
    check("D/linux-存储", "lsblk" in blob["linux"] and "df -hT" in blob["linux"], "")
    check("D/vim-粘贴坑", ":set paste" in blob["vim"], "")
    check("D/vim-操作符组合", 'ci"' in blob["vim"] and "di(" in blob["vim"], "")
    check("D/ssh-三类转发", "ssh -L" in blob["ssh"] and "ssh -R" in blob["ssh"]
          and "ssh -D" in blob["ssh"], "")
    check("D/ssh-权限坑", "authorized_keys" in blob["ssh"] and "600" in blob["ssh"], "")
    check("D/ssh-加固", "PermitRootLogin" in blob["ssh"] and "sshd -t" in blob["ssh"], "")
    check("D/netcmd-设备对照", "display ip interface brief" in blob["netcmd"]
          and "show ip interface brief" in blob["netcmd"], "")
    check("D/netcmd-抓包", "tcpdump -i" in blob["netcmd"], "")
    check("D/ports-数据库面", "1433" in blob["ports"] and "3306" in blob["ports"]
          and "1521" in blob["ports"], "")


# ----------------------------------------------------------------------
# [E] UI
# ----------------------------------------------------------------------
def case_e():
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    import db as dbmod
    import ui_main
    db = dbmod.Database(":memory:")
    db.import_seed_dir()
    w = ui_main.MainWindow(db)

    check("E/十Tab（U 批配置翻译）", w.tabs.count() == 10, str(w.tabs.count()))
    hb_tab = w.tab_handbook
    check("E/手册树7册", hb_tab.tree.topLevelItemCount() == 7,
          str(hb_tab.tree.topLevelItemCount()))
    # 默认选中第一册第一组：HTTP 100 开头、4 列
    check("E/默认表格", hb_tab.tbl.rowCount() == 4 and hb_tab.tbl.columnCount() == 4,
          "%d x %d" % (hb_tab.tbl.rowCount(), hb_tab.tbl.columnCount()))
    check("E/首行100", hb_tab.tbl.item(0, 0).text() == "100",
          hb_tab.tbl.item(0, 0).text())

    # 切到「常用端口」的分组 0：首行应是 53
    check("E/goto端口", hb_tab.goto("ports", 0, 0), "")
    check("E/端口首行53", hb_tab.tbl.item(0, 0).text() == "53",
          hb_tab.tbl.item(0, 0).text())

    # 本册搜索：命中带「分组」列（5 列）——先回到 HTTP 册
    hb_tab.goto("http-status", 3, 0)
    hb_tab.ed_search.setText("404")
    check("E/搜索5列", hb_tab.tbl.columnCount() == 5, str(hb_tab.tbl.columnCount()))
    check("E/搜索命中", hb_tab.tbl.rowCount() == 2, str(hb_tab.tbl.rowCount()))
    check("E/搜索首列分组", "4xx" in hb_tab.tbl.item(0, 0).text(),
          hb_tab.tbl.item(0, 0).text())
    # 复制整组（TSV，含表头；此时视图是 HTTP「4xx」搜索结果）
    clip = QApplication.clipboard()
    clip.setText("SENTINEL")
    hb_tab._copy_all()
    text = clip.text()
    check("E/复制TSV", text.startswith("分组\t码\t短语") and len(text.splitlines()) == 3,
          text[:24])
    hb_tab.ed_search.clear()

    # Ctrl+K 四库：搜「ss -tunlp」应命中手册（netcmd），并跳转落点
    from ui_panel import CommandPalette
    pal = CommandPalette(db)
    pal._reload("ss -tunlp")
    kinds = []
    for i in range(pal.lst.count()):
        data = pal.lst.item(i).data(Qt.UserRole)
        if isinstance(data, dict):
            kinds.append(data["kind"])
    check("E/四库含手册", "hand" in kinds, str(kinds[:6]))
    hand_id = None
    for i in range(pal.lst.count()):
        data = pal.lst.item(i).data(Qt.UserRole)
        if isinstance(data, dict) and data["kind"] == "hand":
            hand_id = data["id"]
            break
    w.jump_to("hand", hand_id)
    check("E/跳转手册Tab", w.tabs.currentWidget() is w.tab_handbook, "")
    cur = hb_tab.tbl.currentRow()
    cell = hb_tab.tbl.item(cur, 0).text() if cur >= 0 else ""
    check("E/跳转命中行", cur >= 0 and "ss -tunlp" in cell,
          "%d:%s" % (cur, cell))
    pal.deleteLater()

    # 分栏记忆：键存在 + pending 往返
    state = hb_tab.split_state()
    check("E/split键", isinstance(state.get("handbook_split"), list), str(state))
    hb_tab.restore_split_state({"handbook_split": [260, 900]})
    check("E/split-pending", hb_tab._pending_split == [260, 900],
          str(hb_tab._pending_split))
    hb_tab._pending_split = None
    w.deleteLater()


# ----------------------------------------------------------------------
# [F] CLI 与反例
# ----------------------------------------------------------------------
def _copy_handbook():
    src = os.path.join(APP_DIR, "app", "handbook")
    dst = os.path.join(tempfile.mkdtemp(prefix="ntbx_hb_"), "handbook")
    os.makedirs(dst)
    for name in os.listdir(src):
        if name.endswith(".json"):
            shutil.copy(os.path.join(src, name), dst)
    return dst


def _run_cli(dirs):
    import handbook as hb
    orig = hb.handbook_dirs
    dirs = [dirs] if isinstance(dirs, str) else list(dirs)
    hb.handbook_dirs = lambda: dirs
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = hb.validate_cli(verbose=False)
    finally:
        hb.handbook_dirs = orig
    return rc, buf.getvalue()


def _mutate(dirs, name, fn):
    path = os.path.join(dirs, name)
    data = json.load(open(path, encoding="utf-8"))
    fn(data)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def case_f():
    import handbook as hb
    docs, _ = load()
    # 正例：CLI rc=0
    rc, out = _run_cli(hb.handbook_dirs())
    check("F/CLI-rc0", rc == 0, "rc=%d %s" % (rc, out[-120:]))

    # 反例 1：空单元格
    d1 = _copy_handbook()
    _mutate(d1, "git.json", lambda d: d["groups"][0]["rows"][0].__setitem__(2, " "))
    rc, out = _run_cli(d1)
    check("F/空单元格-rc1", rc == 1 and "空单元格" in out, out[-120:])

    # 反例 2：端口重复（53/TCP 再来一条）
    d2 = _copy_handbook()
    def dup(d):
        d["groups"][0]["rows"].append(["53", "TCP", "domain-dup", "重复测试"])
    _mutate(d2, "ports.json", dup)
    rc, out = _run_cli(d2)
    check("F/端口重复-rc1", rc == 1 and "重复登记" in out, out[-120:])

    # 反例 3：状态码放错分组（把 404 塞进 5xx 组）
    d3 = _copy_handbook()
    def wrong_group(d):
        d["groups"][4]["rows"].append(["404", "Not Found", "x", "y"])
    _mutate(d3, "http-status.json", wrong_group)
    rc, out = _run_cli(d3)
    check("F/错分组-rc1", rc == 1 and "放错了分组" in out, out[-120:])

    # 反例 4：缺一册
    d4 = _copy_handbook()
    os.remove(os.path.join(d4, "vim.json"))
    rc, out = _run_cli(d4)
    check("F/缺册-rc1", rc == 1 and "缺少手册" in out, out[-120:])

    for d in (d1, d2, d3, d4):
        shutil.rmtree(os.path.dirname(d), ignore_errors=True)


def main():
    import traceback
    for tag, fn in (("A", case_a), ("B", case_b), ("C", case_c), ("D", case_d),
                    ("E", case_e), ("F", case_f)):
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
                              ("- " + str(detail)[:100]) if (detail and not ok) else ""))
    print("=" * 66)
    print("smoke_handbook：%d 项，FAIL %d" % (len(RESULTS), fails))
    if _se.verify_main_db_untouched():
        print("[SMOKE-ENV] 主库零接触实证：指纹前后一致")
    else:
        fails += 1
        print("[SMOKE-ENV] FAIL 主库指纹异常")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
