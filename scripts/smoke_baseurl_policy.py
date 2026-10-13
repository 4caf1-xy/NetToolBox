# -*- coding: utf-8 -*-
"""
smoke_baseurl_policy.py —— 安全加固批 3：base_url 策略 + NetworkProbe 修复

覆盖：
    [1] validate_base_url（公网强制 HTTPS；127/私网/localhost 豁免；协议白名单）
    [2] is_local_endpoint（本网判定）
    [3] endpoint_addr（端口解析：https 443 / http 80 / 显式端口优先）
    [4] endpoint_approved（本网恒放行；陌生 host 需清单）
    [5] NetworkProbe（loopback 直接判可达，端口按 base_url 解析）
    [6] ChatWorker 运行时复核（http 公网地址发送被拒）
    [7] test_connection / list_models 端点放行门禁（审码修复批 F3）

用法：
    PYTHONPATH=app;_vendor python scripts/smoke_baseurl_policy.py
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _smoke_env as _se
_se.setup()                                     # ★ 先于任何 app 模块导入

import ai_bridge                                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ((" —— " + detail) if (detail and not cond) else ""))


def main():
    print("=" * 66)
    print("smoke_baseurl_policy —— base_url 策略（安全加固批 3）")
    print("=" * 66)

    # ---- [1] validate_base_url ----
    print("[1] validate_base_url")
    ok, why = ai_bridge.validate_base_url("https://api.deepseek.com/v1")
    check("https 公网放行", ok, why)
    ok, why = ai_bridge.validate_base_url("http://api.evil.example.com/v1")
    check("http 公网拒绝", not ok and "HTTPS" in why, why)
    for url in ("http://127.0.0.1:11434/v1", "http://localhost:8080",
                "http://10.1.2.3:8000/api", "http://172.16.0.5",
                "http://192.168.9.9:11434"):
        ok, why = ai_bridge.validate_base_url(url)
        check("本网 http 放行（%s）" % url, ok, why)
    ok, why = ai_bridge.validate_base_url("ftp://api.example.com")
    check("非 http(s) 协议拒绝", not ok, why)
    ok, why = ai_bridge.validate_base_url("api.example.com/v1")
    check("缺 scheme 拒绝", not ok, why)
    ok, why = ai_bridge.validate_base_url("")
    check("空地址拒绝", not ok, why)
    ok, why = ai_bridge.validate_base_url("https://localhost:1234")
    check("https localhost 放行", ok, why)

    # ---- [2] is_local_endpoint ----
    print("[2] is_local_endpoint")
    for url in ("http://localhost:11434", "http://127.0.0.1:8080",
                "http://[::1]:9000", "http://10.113.67.14",
                "http://172.16.0.5", "http://192.168.1.1:5000"):
        check("本网判定 True（%s）" % url, ai_bridge.is_local_endpoint(url))
    for url in ("https://api.deepseek.com/v1", "http://8.8.8.8",
                "http://59.175.1.1"):
        check("非本网判定 False（%s）" % url, not ai_bridge.is_local_endpoint(url))
    check("无法解析域名从严按非本地", not ai_bridge.is_local_endpoint("http://nonexistent.host.invalid"))

    # ---- [3] endpoint_addr ----
    print("[3] endpoint_addr")
    cases = [
        ("https://api.deepseek.com/v1", ("api.deepseek.com", 443)),
        ("http://127.0.0.1:11434/v1", ("127.0.0.1", 11434)),
        ("http://192.168.1.5/ollama", ("192.168.1.5", 80)),
        ("https://gw.corp.cn:8443/v1", ("gw.corp.cn", 8443)),
    ]
    for url, want in cases:
        got = ai_bridge.endpoint_addr(url)
        check("端口解析（%s）" % url, got == want, str(got))
    # F4（审码修复批 2026-10-13）：畸形/空配置不再回退 api.deepseek.com:443
    for url in ("", "https:///v1", "::::", "not a url"):
        got = ai_bridge.endpoint_addr(url)
        check("畸形配置返回 None（%r）" % url, got is None, str(got))

    # ---- [4] endpoint_approved ----
    print("[4] endpoint_approved")
    check("本网地址恒放行", ai_bridge.endpoint_approved("http://127.0.0.1:11434"))
    cfg = {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-x"}
    check("陌生公网端点未放行", not ai_bridge.endpoint_approved(cfg["base_url"], cfg))
    cfg["approved_endpoints"] = ["api.deepseek.com"]
    check("清单内端点放行", ai_bridge.endpoint_approved(cfg["base_url"], cfg))
    check("approved_endpoints 类型防御（脏数据）",
          ai_bridge.approved_endpoints({"approved_endpoints": "hack"}) == [])

    # ---- [5] NetworkProbe ----
    print("[5] NetworkProbe")
    from PyQt5.QtCore import QCoreApplication, QEventLoop
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    results = []

    def _probe(base_url):
        results.clear()
        probe = ai_bridge.NetworkProbe(base_url)
        probe.probe_result.connect(lambda ok, detail: results.append((ok, detail)))
        probe.start()
        deadline = QEventLoop()  # 等线程收尾（最长 3s）
        from PyQt5.QtCore import QTimer
        timer = QTimer(); timer.setSingleShot(True)
        loop = QEventLoop()
        probe.finished.connect(loop.quit)
        timer.timeout.connect(loop.quit)
        timer.start(3000)
        loop.exec()
        probe.wait(1000)
        return results[0] if results else (None, "no-signal")

    ok, detail = _probe("http://127.0.0.1:11434/v1")
    check("loopback 直接判可达", ok is True and "本地端点" in (detail or ""), str((ok, detail)))
    ok, detail = _probe("http://192.168.50.50:9999")
    check("私网地址不做误杀探测（直接可达）", ok is True, str((ok, detail)))
    ok, detail = _probe("::::")
    check("配置无效探测给出无效标记（不误导可达）",
          ok is False and "配置无效" in (detail or ""), str((ok, detail)))

    # ---- [6] ChatWorker 运行时复核 ----
    print("[6] ChatWorker 运行时复核")
    captured = []
    worker = ai_bridge.ChatWorker()
    worker.set_request({"base_url": "http://api.evil.example.com/v1",
                        "api_key": "sk-x", "model": "m"}, [{"role": "user", "content": "hi"}])
    worker.failed.connect(lambda msg: captured.append(msg))
    worker.run()            # 同线程直跑：信号直连同步回收
    check("http 公网发送被拒（含人话原因）",
          any("接口地址不合规" in m and "HTTPS" in m for m in captured), str(captured))
    captured.clear()
    worker2 = ai_bridge.ChatWorker()
    worker2.set_request({"base_url": "ftp://x", "api_key": "sk-x", "model": "m"},
                        [{"role": "user", "content": "hi"}])
    worker2.failed.connect(lambda msg: captured.append(msg))
    worker2.run()
    check("坏协议发送被拒", any("接口地址不合规" in m for m in captured), str(captured))

    # ---- [7] test_connection / list_models 端点放行门禁（F3，审码修复批）----
    print("[7] test_connection / list_models 放行门禁")
    stranger = {"base_url": "https://stranger.endpoint.example.com/v1",
                "api_key": "sk-x", "model": "m"}
    ok, msg, latency = ai_bridge.test_connection(stranger)
    check("test_connection 陌生端点拒绝（未发请求）",
          not ok and "端点未放行" in msg, str((ok, msg)))
    ok, names, msg = ai_bridge.list_models(stranger)
    check("list_models 陌生端点拒绝（未发请求）",
          not ok and "端点未放行" in msg, str((ok, msg)))
    # 清单内端点：门禁放行后会真的发网络请求（离网环境走 DNS 失败），只断言不是门禁拒绝
    approved_cfg = dict(stranger)
    approved_cfg["approved_endpoints"] = ["stranger.endpoint.example.com"]
    ok, msg, _lat = ai_bridge.test_connection(approved_cfg)
    check("test_connection 清单内端点不再被门禁拦（放行走网络层）",
          "端点未放行" not in str(msg), str((ok, msg)))
    ok, _names, msg = ai_bridge.list_models(approved_cfg)
    check("list_models 清单内端点不再被门禁拦",
          "端点未放行" not in str(msg), str((ok, msg)))
    # 本网端点恒放行（门禁不拦本地模型；连接失败是网络层人话）
    local_cfg = {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-x", "model": "m"}
    ok, msg, _lat = ai_bridge.test_connection(local_cfg)
    check("test_connection 本网端点门禁恒放行",
          "端点未放行" not in str(msg), str((ok, msg)))

    print("=" * 66)
    if FAIL:
        print("结果：%d 项失败：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("结果：全部通过（HTTPS 强制/本网豁免/端口解析/放行清单/探测修复/发送复核）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
