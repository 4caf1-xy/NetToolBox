# -*- coding: utf-8 -*-
"""
smoke_send_gate.py —— 安全加固批 2：AI 发送脱敏门禁回归断言

覆盖：
    [1] 门禁 IP 策略（拦 RFC1918/真实地址；豁免 TEST-NET/loopback/公共 DNS/x.x.x.x/掩码形态）
    [2] secret 真值 + 占位豁免 + 明文黑名单（引擎规则与站点扫描逐字一致）
    [3] span 正确性与脱敏回写（放行原文/未放行掩码；重叠区间并集）
    [4] 指纹稳定性与当日放行记忆（隔日自动失效）
    [4b] 放行指纹字段加密（L 批裁决 2：gate_allowances_enc 密文落盘/迁移/收口/降级）
    [5] build_pieces 组装范围（ctx 六字段 + 正文 + 文本附件；图片附件跳过）
    [6] SendGateDialog 离屏 UI（行数/全选/accept 后 allowed_fps）
    [7] 站点白名单行为回归（_ip_allowed 默认参数逐字不变）
    [8] 图片附件外发确认（L 批裁决 3：确认框内容/未确认不发送不落盘/确认后发送且落盘即密文）

用法：
    PYTHONPATH=app;_vendor python scripts/smoke_send_gate.py
"""
import base64
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _smoke_env as _se   # setup() 内含 sys.path 卫生（脚本目录挪末尾，防薄壳遮蔽）
_se.setup()                # ★ 先于任何 app 模块导入

import ai_bridge                                # noqa: E402
import ai_gate                                  # noqa: E402
import sanitize_scan                            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ((" —— " + detail) if (detail and not cond) else ""))


def gate_findings(text):
    return sanitize_scan.scan_free_text(
        text, ai_gate.GATE_ALLOWED_NETS, ai_gate.GATE_ALLOWED_IPS,
        ai_gate.GATE_ALLOWED_DNS, allow_nat_teaching=False)


def kinds(fs):
    return sorted((f["kind"], f["value"]) for f in fs)


def main():
    print("=" * 66)
    print("smoke_send_gate —— AI 发送脱敏门禁（安全加固批 2）")
    print("=" * 66)

    # ---- [1] 门禁 IP 策略 ----
    print("[1] 门禁 IP 策略")
    hits = gate_findings("到 10.1.1.5 和 172.16.5.1、192.168.1.1 都通")
    ips = [f["value"] for f in hits if f["kind"] == "IP"]
    check("RFC1918 三段全部命中", sorted(ips) == ["10.1.1.5", "172.16.5.1", "192.168.1.1"], str(ips))
    ips2 = [f["value"] for f in gate_findings("peer 1.2.3.4 与 59.175.1.1")]
    check("公网真实地址命中", set(ips2) == {"1.2.3.4", "59.175.1.1"})
    clean = gate_findings("ntp server 192.0.2.10；管理 127.0.0.1；dns 8.8.8.8 / 223.5.5.5")
    check("TEST-NET/loopback/公共 DNS 豁免", not [f for f in clean if f["kind"] == "IP"])
    clean2 = gate_findings("router-id 1.1.1.1 掩码 255.255.255.0 目 0.0.0.0")
    check("x.x.x.x 惯例/掩码/通配豁免", not [f for f in clean2 if f["kind"] == "IP"])
    nat = gate_findings("nat inside 200.1.1.1")
    check("NAT 教学段门禁不豁免（可逐项放行）",
          [f["value"] for f in nat if f["kind"] == "IP"] == ["200.1.1.1"])
    bad = gate_findings("地址 1.2.3.256 不是合法 IP")
    check("非法 IP 不算命中", not [f for f in bad if f["kind"] == "IP"])

    # ---- [2] secret / 占位 / 黑名单 ----
    print("[2] secret / 占位 / 黑名单")
    fs = gate_findings("password Admin123")
    check("password 真值命中", ("SECRET", "Admin123") in kinds(fs), str(kinds(fs)))
    fs2 = gate_findings("snmp-server community NtbxPub RO")
    check("community 真值命中", ("SECRET", "NtbxPub") in kinds(fs2), str(kinds(fs2)))
    fs3 = gate_findings("password {{pass}} 与 community read")
    check("占位/子命令豁免零命中", not [f for f in fs3 if f["kind"] == "SECRET"])
    fs4 = gate_findings("口令 ntbxlab 出现即拦")
    check("明文黑名单命中", ("BLACKLIST", "ntbxlab") in kinds(fs4))

    # ---- [3] span 与脱敏回写 ----
    print("[3] span 与脱敏回写")
    text = "login with password Admin123 to 10.0.0.9"
    fs5 = gate_findings(text)
    sec = [f for f in fs5 if f["kind"] == "SECRET"][0]
    check("SECRET span 指向值本身",
          text[sec["start"]:sec["end"]] == "Admin123", text[sec["start"]:sec["end"]])
    red = ai_gate.redact_text(text, [dict(fs5[0], fingerprint="A"),
                                     dict(fs5[1], fingerprint="B")],
                              allowed_fps={"A"})
    check("放行项原文、未放行项掩码",
          ("Admin123" in red and "10.0.0.9" not in red
           and "【IP已脱敏】" in red and "password" in red), red)
    both = ai_gate.redact_text(text, [dict(f, fingerprint="A") for f in fs5],
                               allowed_fps=set())
    check("全部未放行 → 两处均掩码", both.count("【已脱敏】") == 1
          and both.count("【IP已脱敏】") == 1, both)
    keep = ai_gate.redact_text(text, [dict(f, fingerprint="F%d" % i) for i, f in enumerate(fs5)],
                               allowed_fps={"F0", "F1"})
    check("全部放行 → 原文不变", keep == text, keep)
    # 占位符存在时的 span 映射（scrubbed 回映）
    text2 = " password {{old}} 与 password Real999"
    fs6 = gate_findings(text2)
    vals = [text2[f["start"]:f["end"]] for f in fs6 if f["kind"] == "SECRET"]
    check("占位符在场时 SECRET span 仍指向原文", vals == ["Real999"], str(vals))
    # 重叠区间并集
    fs7 = [{"kind": "IP", "value": "1.1.1.1", "start": 0, "end": 7},
           {"kind": "IP", "value": "1.1.1.1", "start": 5, "end": 9}]
    out = ai_gate.redact_text("1234567890abc", fs7, allowed_fps=set())
    check("重叠 span 合并不错位", out == "【IP已脱敏】0abc", out)

    # ---- [4] 指纹与当日放行记忆 ----
    print("[4] 指纹与当日放行记忆")
    f_a = {"kind": "SECRET", "value": "Admin123"}
    f_b = {"kind": "SECRET", "value": "Admin124"}
    fp_a = ai_gate.fingerprint(f_a)
    check("指纹确定性", fp_a == ai_gate.fingerprint(dict(f_a)))
    check("值变指纹变", fp_a != ai_gate.fingerprint(f_b))
    check("kind 参与指纹", fp_a != ai_gate.fingerprint({"kind": "IP", "value": "Admin123"}))
    cfg = {"gate_allowances": {"deadbeef": "2001-01-01", fp_a: ai_gate.day_stamp()}}
    allowances = ai_gate.load_allowances(cfg)
    check("隔日条目自动剪除", set(allowances) == {fp_a}, str(allowances))
    ai_bridge.save_config({"api_key": "sk-gate-smoke"})
    cfg2 = ai_bridge.load_config()
    ai_gate.remember_fps(cfg2, [fp_a, "ff00ff00ff00ff00"])
    cfg3 = ai_bridge.load_config()
    saved = ai_gate.load_allowances(cfg3)
    check("放行记忆落盘并可读回", fp_a in saved and "ff00ff00ff00ff00" in saved, str(saved))

    # ---- [4b] 放行指纹字段加密（L 批裁决 2）----
    print("[4b] 放行指纹字段加密（gate_allowances_enc）")
    with open(ai_bridge.config_path(), "r", encoding="utf-8") as fp:
        data_gate = json.load(fp)
    check("指纹以密文落盘（gate_allowances_enc）",
          "gate_allowances_enc" in data_gate
          and str(data_gate.get("gate_allowances_enc", "")).startswith("dpapi-v1:"),
          str(sorted(data_gate.keys())))
    check("迁移后 ai_config.json 无明文 gate_allowances",
          "gate_allowances" not in data_gate)
    plain_gate = ai_bridge.decrypt_gate_value(data_gate.get("gate_allowances_enc"))
    check("密文 round-trip 内容一致（含当日戳）",
          isinstance(plain_gate, dict) and plain_gate.get(fp_a) == ai_gate.day_stamp()
          and plain_gate.get("ff00ff00ff00ff00") == ai_gate.day_stamp(), str(plain_gate))
    check("load_allowances 从密文读回", set(ai_gate.load_allowances(cfg3)) >= {fp_a})
    # save 收口（ciphertext-wins）：密文在场时明文残留被清
    cfg_c = ai_bridge.load_config()
    cfg_c["gate_allowances"] = {"stale": "2020-01-01"}
    ai_bridge.save_config(cfg_c)
    with open(ai_bridge.config_path(), "r", encoding="utf-8") as fp:
        data_c = json.load(fp)
    check("save 收口：密文在场时明文残留被清",
          "gate_allowances" not in data_c and "gate_allowances_enc" in data_c)
    # 存量明文指纹 → load_config 事务式单字段迁移
    with open(ai_bridge.config_path(), "w", encoding="utf-8") as fp:
        json.dump({"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-legacy-gate",
                   "gate_allowances": {fp_a: ai_gate.day_stamp(),
                                       "ancient": "2001-01-01"}}, fp)
    ai_bridge.GATE_STATUS.update(state="ok", detail="")
    cfg_m = ai_bridge.load_config()
    with open(ai_bridge.config_path(), "r", encoding="utf-8") as fp:
        data_m = json.load(fp)
    check("存量明文指纹已迁移为密文",
          "gate_allowances" not in data_m
          and str(data_m.get("gate_allowances_enc", "")).startswith("dpapi-v1:"))
    check("GATE_STATUS=migrated", ai_bridge.GATE_STATUS["state"] == "migrated",
          ai_bridge.GATE_STATUS["state"])
    check("迁移 round-trip 内容一致（非当日项一并保留）",
          ai_bridge.decrypt_gate_value(data_m.get("gate_allowances_enc"))
          == {fp_a: ai_gate.day_stamp(), "ancient": "2001-01-01"})
    check("迁移后 load_allowances 从密文读回当日项",
          set(ai_gate.load_allowances(cfg_m)) == {fp_a}, str(ai_gate.load_allowances(cfg_m)))
    # 无 DPAPI 降级：明文兜底可用
    real_avail = ai_bridge._dpapi_available
    try:
        ai_bridge._dpapi_available = lambda: False
        cfg_d = {}
        ai_gate.remember_fps(cfg_d, ["bb11bb11bb11bb11"])
        check("无 DPAPI → 指纹明文兜底可用",
              cfg_d.get("gate_allowances", {}).get("bb11bb11bb11bb11") == ai_gate.day_stamp())
        check("无 DPAPI → 不写密文字段", "gate_allowances_enc" not in cfg_d)
    finally:
        ai_bridge._dpapi_available = real_avail

    # ---- [5] build_pieces 范围 ----
    print("[5] build_pieces 组装范围")
    ctx = {"vendor": "华为", "os": "", "model_name": "S5720",
           "symptom": "到 10.1.1.5 不通", "echo": "", "steps": "已重启"}
    atts = [{"name": "log.txt", "content": "uplink 192.168.9.9 down"},
            {"name": "shot.png", "is_image": True, "content": "<binary>"}]
    pieces = ai_gate.build_pieces(ctx, "正文里 password Zz98765", atts)
    labels = [p["label"] for p in pieces]
    check("空字段不组块/顺序稳定",
          labels == ["上下文·厂商", "上下文·设备型号", "上下文·故障现象",
                     "上下文·已尝试步骤", "正文", "附件·log.txt"], str(labels))
    fs8 = ai_gate.scan_pieces(pieces)
    check("三块命中全被扫出（含上下文与附件，图片跳过）",
          {f["value"] for f in fs8} == {"10.1.1.5", "192.168.9.9", "Zz98765"},
          str({f["value"] for f in fs8}))
    red_map = ai_gate.apply_redaction(
        pieces, [dict(f, fingerprint="fp%s" % f["value"]) for f in fs8],
        allowed_fps={"fpZz98765"})
    check("apply_redaction 只回写有变化的块", set(red_map) == {2, 5}, str(sorted(red_map)))
    check("放行块不回写（原文保留）", 3 not in red_map)

    # ---- [6] SendGateDialog 离屏 UI ----
    print("[6] SendGateDialog 离屏 UI")
    from PyQt5.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    import ui_ai
    pending = [dict(f, fingerprint="pf%d" % i) for i, f in enumerate(fs8)]
    dlg = ui_ai.SendGateDialog(None, pending)
    check("预览行数 = 命中数", len(dlg._boxes) == len(pending))
    for chk, _f in dlg._boxes:
        chk.setChecked(True)
    dlg.accept()
    check("全选放行 → allowed_fps 全集", dlg.allowed_fps == {f["fingerprint"] for f in pending})
    dlg2 = ui_ai.SendGateDialog(None, pending)
    dlg2._boxes[0][0].setChecked(True)
    dlg2.accept()
    check("逐项放行 → 仅勾选项", dlg2.allowed_fps == {"pf0"})

    # ---- [7] 站点白名单行为回归 ----
    print("[7] 站点白名单回归（默认参数逐字不变）")
    check("10/8 站点放行（门禁要拦，站点要放）", sanitize_scan._ip_allowed("10.255.0.1"))
    check("200.1.1.0/24 站点放行（NAT 教学段默认豁免）",
          sanitize_scan._ip_allowed("200.1.1.100", allow_nat_teaching=True))
    check("allow_nat_teaching=False 时拦截",
          not sanitize_scan._ip_allowed("200.1.1.100", allow_nat_teaching=False))
    bad_site = {"entries": [{"uuid": "u1", "title": "t", "commands": "x 10.255.0.1",
                             "description": "", "params": "[]", "rollback": "",
                             "notes": "", "models": "", "category": "c",
                             "vendor": "huawei", "os_family": "vrp8",
                             "verified_by": "", "verified_model": ""}],
                "trees": [], "errs": []}
    check("scan_site_data 对 10/8 占位段零命中（站点行为未变）",
          sanitize_scan.scan_site_data(bad_site) == [])

    # ---- [8] 图片附件外发确认（L 批裁决 3）----
    print("[8] 图片附件外发确认（不做内容分析，逐次确认）")
    from PyQt5.QtCore import QThread, pyqtSignal
    from PyQt5.QtWidgets import QDialog as _QDialog
    import ui_ai
    # 1x1 PNG
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    img_path = os.path.join(tempfile.mkdtemp(prefix="ntb_vision_"), "shot-l.png")
    with open(img_path, "wb") as fp:
        fp.write(png_bytes)
    img_att, err_img = ai_bridge.load_image_attachment(img_path)
    check("V1 图片附件构造（base64 data URL）",
          img_att is not None and img_att.get("is_image")
          and img_att.get("data_url", "").startswith("data:image/png;base64,"), err_img)
    # 确认框内容断言（离屏）
    dlg = ui_ai.VisionConfirmDialog(None, [img_att], "api.example.com")
    check("V2 确认框列出本批图片",
          [a.get("name") for a in dlg.images] == ["shot-l.png"])
    check("V3 明示外发端点 host", dlg.host == "api.example.com")
    check("V4 默认未确认", dlg.confirmed is False)
    dlg.accept()
    check("V5 accept → confirmed=True", dlg.confirmed is True)
    dlg2 = ui_ai.VisionConfirmDialog(None, [img_att], "api.example.com")
    dlg2.reject()
    check("V6 reject/关闭 → 保持未确认", dlg2.confirmed is False)
    # 未确认不发送（离屏实跑 AiTab；base_url 本网化——批 3 已知坑）
    ai_bridge.save_config({"api_key": "sk-vision", "model": "smoke-model",
                           "base_url": "http://127.0.0.1:9/v1",
                           "vision_model": "fake-vision"})
    tab = ui_ai.AiTab(db=None)
    tab.show()
    app.processEvents()
    tab._pending_attachments.append(dict(img_att))
    tab._refresh_attachment_bar()
    tab.txt_input.setPlainText("看这张截图")
    orig_exec = ui_ai.VisionConfirmDialog.exec_
    orig_worker = ai_bridge.ChatWorker
    _rej = []

    def reject_exec(self):
        self.confirmed = False
        _rej.append(self)
        return 0                                  # QDialog.Rejected

    ui_ai.VisionConfirmDialog.exec_ = reject_exec
    try:
        tab.on_send()
    finally:
        ui_ai.VisionConfirmDialog.exec_ = orig_exec
    check("V7 未确认：本轮不发送（消息流零新增）", len(tab.session["messages"]) == 0)
    check("V8 未确认：附件保留待重新确认", len(tab._pending_attachments) == 1)
    check("V9 未确认：会话/案例零落盘", not ai_bridge.list_sessions()
          and not ai_bridge.list_cases())
    # 确认后正常发送（假 worker，不发真实请求）
    class FakeWorker(QThread):
        chunk = pyqtSignal(str)
        failed = pyqtSignal(str)
        finished_full = pyqtSignal(dict)

        def __init__(self, parent=None):
            super(FakeWorker, self).__init__(parent)
            self._cancelled = False

        def set_request(self, config, messages):
            pass

        def cancel(self):
            pass

        def run(self):
            self.finished_full.emit({"usage": {}, "model": "fake"})

    import time as _time
    _acc = []

    def accept_exec(self):
        self.confirmed = True
        _acc.append(self)
        return _QDialog.Accepted

    ai_bridge.ChatWorker = FakeWorker
    ui_ai.VisionConfirmDialog.exec_ = accept_exec
    try:
        tab.on_send()
    finally:
        ui_ai.VisionConfirmDialog.exec_ = orig_exec
        ai_bridge.ChatWorker = orig_worker
    worker = tab._worker
    t0 = _time.time()
    while worker.isRunning() and _time.time() - t0 < 5:
        app.processEvents()
        _time.sleep(0.01)
    app.processEvents()
    check("V10 确认后正常发送（user+assistant 两条）",
          len(tab.session["messages"]) == 2, str(len(tab.session["messages"])))
    files = ai_bridge.list_sessions()
    check("V11 确认后会话落盘 1 个", len(files) == 1, str(len(files)))
    with open(files[0][0], "r", encoding="utf-8") as fp:
        raw_sess = fp.read()
    check("V12 会话落盘即密文（dpapi-v1）",
          raw_sess.lstrip().startswith("dpapi-v1:"), raw_sess[:40])
    check("V13 落盘内容零明文（无输入/图片数据）",
          "看这张截图" not in raw_sess and "iVBOR" not in raw_sess)

    print("=" * 66)
    if FAIL:
        print("结果：%d 项失败：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("结果：全部通过（门禁策略/引擎复用/脱敏回写/当日记忆/预览 UI/站点回归）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
