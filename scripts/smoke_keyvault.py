# -*- coding: utf-8 -*-
"""
smoke_keyvault.py —— 安全加固批 1：API Key DPAPI 加密存储回归断言

覆盖：
    T1  旧明文 key 事务式迁移 happy path（迁移后文件零明文、备份已删、round-trip 一致）
    T2  save→load 加密 round-trip（落盘即密文）
    T3  显式清空 key → 加密字段一并清除（注销语义）
    T4  坏 blob → 解密失败不崩、key 置空、状态 decrypt-failed
    T5  迁移 round-trip 验证失败 → 备份还原明文（介质回到迁移前）
    T6  DPAPI 加密失败 → 明文兜底保持可用（encrypt-failed-keep-plain）
    T7  非 Windows / DPAPI 不可用 → save 保持明文（降级路径）
    T8  发布 zip 零敏感文件回归断言（release.yml 白名单 + .gitignore + build_exe 无 ai_config）

用法：
    PYTHONPATH=app;_vendor python scripts/smoke_keyvault.py
依赖：_smoke_env（路径全劫持，主库零接触）；必须在 import ai_bridge 之前 setup()。
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _smoke_env as _se
_se.setup()                                     # ★ 先于任何 app 模块导入

import ai_bridge                                # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED = []


def check(name, ok, detail=""):
    print("  [%s] %s%s" % ("OK" if ok else "!!", name,
                           ("—— " + detail) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def write_cfg(payload):
    with open(ai_bridge.config_path(), "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def read_cfg():
    with open(ai_bridge.config_path(), "r", encoding="utf-8") as fp:
        return json.load(fp)


def main():
    print("=" * 66)
    print("smoke_keyvault —— DPAPI Key 存储（安全加固批 1）")
    print("=" * 66)
    if not ai_bridge._dpapi_available():
        print("[SKIP] 非 Windows 环境，DPAPI 用例不适用（仅跑 T8 断言）")
        _run_t8()
        return 1 if FAILED else 0

    # ---- T1 旧明文迁移 happy path ----
    print("-" * 66)
    print("T1 旧明文 key 事务式迁移")
    write_cfg({"base_url": "https://api.example.com/v1", "api_key": "sk-plain-123",
               "model": "m1", "timeout": 60})
    cfg = ai_bridge.load_config()
    check("T1a 返回值解密一致", cfg.get("api_key") == "sk-plain-123",
          repr(cfg.get("api_key")))
    data = read_cfg()
    check("T1b 落盘零明文（api_key 字段移除）", "api_key" not in data,
          str(sorted(data.keys())))
    check("T1c 加密字段为 dpapi-v1 密文",
          str(data.get(ai_bridge.KEY_FIELD_ENC, "")).startswith("dpapi-v1:"))
    check("T1d 备份已删（介质零明文残留）",
          not glob.glob(ai_bridge.config_path() + ".bak-*"))
    check("T1e 临时文件已转正", not os.path.exists(ai_bridge.config_path() + ".new-enc"))
    check("T1f 状态=migrated", ai_bridge.KEY_STATUS["state"] == "migrated",
          ai_bridge.KEY_STATUS["state"])
    check("T1g 其余字段保留", data.get("model") == "m1" and data.get("timeout") == 60)

    # ---- T2 save→load 加密 round-trip ----
    print("-" * 66)
    print("T2 save→load 加密 round-trip")
    ok = ai_bridge.save_config({"api_key": "sk-new-456", "base_url":
                                "https://api.example.com/v1", "model": "m2"})
    check("T2a save 成功", ok)
    data = read_cfg()
    check("T2b 落盘零明文", "api_key" not in data)
    cfg = ai_bridge.load_config()
    check("T2c 解密还原一致", cfg.get("api_key") == "sk-new-456", repr(cfg.get("api_key")))
    check("T2d 解密状态 ok", ai_bridge.KEY_STATUS["state"] == "ok",
          ai_bridge.KEY_STATUS["state"])
    check("T2e is_configured 判定", ai_bridge.is_configured(cfg))

    # ---- T3 显式清空 = 注销 ----
    print("-" * 66)
    print("T3 显式清空 key")
    ai_bridge.save_config({"api_key": ""})
    data = read_cfg()
    check("T3a 明文字段不在", "api_key" not in data)
    check("T3b 加密字段一并清除", ai_bridge.KEY_FIELD_ENC not in data)
    check("T3c 未配置判定", not ai_bridge.is_configured())

    # ---- T4 坏 blob：解密失败不崩 ----
    print("-" * 66)
    print("T4 坏 blob 防御")
    write_cfg({"base_url": "https://api.example.com/v1",
               ai_bridge.KEY_FIELD_ENC: "dpapi-v1:AAAA"})
    cfg = ai_bridge.load_config()
    check("T4a key 置空不崩", cfg.get("api_key") == "")
    check("T4b 状态 decrypt-failed", ai_bridge.KEY_STATUS["state"] == "decrypt-failed",
          ai_bridge.KEY_STATUS["state"])
    check("T4c 未配置判定", not ai_bridge.is_configured(cfg))

    # ---- T5 验证失败 → 备份还原 ----
    print("-" * 66)
    print("T5 迁移 round-trip 失败 → 备份还原")
    write_cfg({"base_url": "https://api.example.com/v1", "api_key": "sk-restore-789"})
    real_unprotect = ai_bridge._dpapi_unprotect

    def broken_unprotect(blob):
        raise OSError("simulated verify failure")

    try:
        ai_bridge._dpapi_unprotect = broken_unprotect
        cfg = ai_bridge.load_config()
    finally:
        ai_bridge._dpapi_unprotect = real_unprotect
    data = read_cfg()
    check("T5a 明文还原一致", data.get("api_key") == "sk-restore-789",
          repr(data.get("api_key")))
    check("T5b 加密字段已随还原消失", ai_bridge.KEY_FIELD_ENC not in data)
    check("T5c 备份已让位回原文件",
          not glob.glob(ai_bridge.config_path() + ".bak-*"))
    check("T5d 状态 verify-failed-restored",
          ai_bridge.KEY_STATUS["state"] == "verify-failed-restored",
          ai_bridge.KEY_STATUS["state"])
    check("T5e 本次会话 key 仍可用", cfg.get("api_key") == "sk-restore-789")

    # ---- T6 加密失败 → 明文兜底 ----
    print("-" * 66)
    print("T6 DPAPI 加密失败降级")
    write_cfg({"base_url": "https://api.example.com/v1", "api_key": "sk-degrade-000"})

    def broken_protect(plain_bytes):
        raise OSError("simulated protect failure")

    real_protect = ai_bridge._dpapi_protect
    try:
        ai_bridge._dpapi_protect = broken_protect
        cfg = ai_bridge.load_config()
    finally:
        ai_bridge._dpapi_protect = real_protect
    check("T6a 明文保持可用", cfg.get("api_key") == "sk-degrade-000")
    check("T6b 状态 encrypt-failed-keep-plain",
          ai_bridge.KEY_STATUS["state"] == "encrypt-failed-keep-plain",
          ai_bridge.KEY_STATUS["state"])

    # ---- T7 DPAPI 不可用（非 Windows）→ save 保持明文 ----
    print("-" * 66)
    print("T7 无 DPAPI 降级（save 路径）")
    real_avail = ai_bridge._dpapi_available
    try:
        ai_bridge._dpapi_available = lambda: False
        ai_bridge.save_config({"api_key": "sk-nodpapi-111"})
        cfg = ai_bridge.load_config()
    finally:
        ai_bridge._dpapi_available = real_avail
    check("T7a key 保存且可用（明文兜底）", cfg.get("api_key") == "sk-nodpapi-111",
          repr(cfg.get("api_key")))

    # ---- T9 双字段并存 + 密文有效 → ciphertext-wins（F7）----
    print("-" * 66)
    print("T9 ciphertext-wins：明文残留清除，密文不重加密")
    ai_bridge.save_config({"api_key": "sk-current-999", "model": "m9"})
    data = read_cfg()
    enc_valid = data.get(ai_bridge.KEY_FIELD_ENC)
    check("T9-pre 密文已建立", str(enc_valid).startswith("dpapi-v1:"))
    # 手工在文件里塞回一份陈旧明文（模拟旧工具/手工编辑残留）
    stale = dict(data)
    stale["api_key"] = "sk-stale-old"
    write_cfg(stale)
    cfg = ai_bridge.load_config()
    check("T9a 返回密文解出的现役 key（非陈旧明文）",
          cfg.get("api_key") == "sk-current-999", repr(cfg.get("api_key")))
    data = read_cfg()
    check("T9b 文件明文残留已清除", "api_key" not in data, str(sorted(data.keys())))
    check("T9c 密文原样未动（不重加密）", data.get(ai_bridge.KEY_FIELD_ENC) == enc_valid)
    check("T9d 状态 ok", ai_bridge.KEY_STATUS["state"] == "ok",
          ai_bridge.KEY_STATUS["state"])

    # ---- T10 双字段并存 + 密文损坏 → 保留明文不迁移（F7）----
    print("-" * 66)
    print("T10 密文损坏：保留明文可用，密文不被覆盖")
    manual = dict(read_cfg())
    manual["api_key"] = "sk-plain-keep"
    manual[ai_bridge.KEY_FIELD_ENC] = "dpapi-v1:AAAA"       # 损坏密文
    write_cfg(manual)
    cfg = ai_bridge.load_config()
    check("T10a 明文保留可用", cfg.get("api_key") == "sk-plain-keep",
          repr(cfg.get("api_key")))
    data = read_cfg()
    check("T10b 文件未被迁移改写（明文字段仍在）", "api_key" in data)
    check("T10c 损坏密文原样保留（未重加密覆盖）",
          data.get(ai_bridge.KEY_FIELD_ENC) == "dpapi-v1:AAAA")
    check("T10d 状态 decrypt-failed", ai_bridge.KEY_STATUS["state"] == "decrypt-failed",
          ai_bridge.KEY_STATUS["state"])
    check("T10e 未产生迁移备份", not glob.glob(ai_bridge.config_path() + ".bak-*"))

    # ---- T11 save 时加密失败 → 明文兜底且留状态（F6）----
    print("-" * 66)
    print("T11 save 加密失败留状态")
    real_protect = ai_bridge._dpapi_protect
    try:
        ai_bridge._dpapi_protect = broken_protect
        ok = ai_bridge.save_config({"api_key": "sk-savefail-222"})
    finally:
        ai_bridge._dpapi_protect = real_protect
    check("T11a save 成功（明文兜底）", ok)
    # 状态断言必须在 load 之前：load 会按 F7 语义重新判定 KEY_STATUS
    check("T11c 状态 encrypt-failed-keep-plain（不静默）",
          ai_bridge.KEY_STATUS["state"] == "encrypt-failed-keep-plain",
          "%s / %s" % (ai_bridge.KEY_STATUS["state"], ai_bridge.KEY_STATUS["detail"]))
    cfg = ai_bridge.load_config()
    check("T11b key 明文可用", cfg.get("api_key") == "sk-savefail-222",
          repr(cfg.get("api_key")))

    # ---- T12 会话落盘加密：save → 落盘即密文 → load round-trip（L 批裁决 1）----
    print("-" * 66)
    print("T12 会话文件加密 round-trip")
    sess = ai_bridge.new_session({"vendor": "华为", "symptom": "L批-T12 现象 password Admin123"})
    sess["messages"] = [{"role": "user", "content": "内容 password Admin123",
                         "attachments": []}]
    saved_path = ai_bridge.save_session(sess)
    check("T12a 会话落盘成功", bool(saved_path))
    with open(saved_path, "r", encoding="utf-8") as fp:
        raw = fp.read()
    check("T12b 落盘即密文（dpapi-v1 头）", raw.lstrip().startswith("dpapi-v1:"), raw[:40])
    check("T12c 落盘内容零明文（无现象/口令字样）",
          "Admin123" not in raw and "L批-T12" not in raw)
    back = ai_bridge.load_session(saved_path)
    check("T12d load round-trip 一致", isinstance(back, dict)
          and back.get("session_id") == sess["session_id"]
          and (back.get("messages") or [{}])[0].get("content", "").endswith("Admin123"))
    check("T12e list_sessions 可见该会话",
          any(p == saved_path for p, _d in ai_bridge.list_sessions()))

    # ---- T13 存量明文会话首启事务式迁移 ----
    print("-" * 66)
    print("T13 存量明文会话迁移")
    sid = "legacy-t13"
    legacy_path = os.path.join(ai_bridge.sessions_dir(), sid + ".json")
    with open(legacy_path, "w", encoding="utf-8") as fp:
        json.dump({"session_id": sid, "kind": "session",
                   "ctx": {"vendor": "Cisco", "symptom": "legacy plain"},
                   "messages": [{"role": "user", "content": "plain body 10.1.1.5",
                                 "attachments": []}]},
                  fp, ensure_ascii=False, indent=2)
    ai_bridge._STORE_MIGRATED_DIRS.clear()      # 模拟"首启"（每目录每进程一次）
    ai_bridge.STORE_STATUS.update(state="ok", detail="", migrated=0)
    rows = ai_bridge.list_sessions()
    with open(legacy_path, "r", encoding="utf-8") as fp:
        raw = fp.read()
    check("T13a 迁移后落盘为密文", raw.lstrip().startswith("dpapi-v1:"), raw[:40])
    check("T13b 明文内容不再在场", "10.1.1.5" not in raw and "legacy plain" not in raw)
    check("T13c 备份已删（介质零明文残留）",
          not glob.glob(legacy_path + ".bak-*")
          and not os.path.exists(legacy_path + ".new-enc"))
    check("T13d list_sessions 能读回该会话",
          any(d.get("session_id") == sid for _p, d in rows))
    check("T13e 状态计数 migrated=1", ai_bridge.STORE_STATUS["migrated"] == 1,
          str(ai_bridge.STORE_STATUS))
    ai_bridge._STORE_MIGRATED_DIRS.clear()
    ai_bridge.STORE_STATUS.update(migrated=0)
    ai_bridge.list_sessions()
    check("T13f 已加密文件幂等（重扫零重复迁移）",
          ai_bridge.STORE_STATUS["migrated"] == 0, str(ai_bridge.STORE_STATUS))

    # ---- T14 会话迁移 round-trip 验证失败 → 备份还原明文 ----
    print("-" * 66)
    print("T14 会话迁移验证失败 → 还原")
    p14 = os.path.join(ai_bridge.sessions_dir(), "legacy-t14.json")
    with open(p14, "w", encoding="utf-8") as fp:
        json.dump({"session_id": "legacy-t14", "ctx": {"vendor": "H3C"}, "messages": []}, fp)
    real_unprotect = ai_bridge._dpapi_unprotect

    def broken_unprotect(blob):
        raise OSError("simulated verify failure")

    ai_bridge._STORE_MIGRATED_DIRS.clear()
    try:
        ai_bridge._dpapi_unprotect = broken_unprotect
        ai_bridge.list_sessions()
    finally:
        ai_bridge._dpapi_unprotect = real_unprotect
    with open(p14, "r", encoding="utf-8") as fp:
        raw14 = fp.read()
    check("T14a 明文还原一致", json.loads(raw14).get("session_id") == "legacy-t14")
    check("T14b 备份已让位回原文件", not glob.glob(p14 + ".bak-*"))
    check("T14c 状态 verify-failed-restored",
          ai_bridge.STORE_STATUS["state"] == "verify-failed-restored",
          ai_bridge.STORE_STATUS["state"])

    # ---- T15 会话加密失败 → 明文兜底保持可用 ----
    print("-" * 66)
    print("T15 会话加密失败降级")
    p15 = os.path.join(ai_bridge.sessions_dir(), "legacy-t15.json")
    with open(p15, "w", encoding="utf-8") as fp:
        json.dump({"session_id": "legacy-t15", "ctx": {}, "messages": []}, fp)
    real_protect = ai_bridge._dpapi_protect

    def broken_protect(plain_bytes):
        raise OSError("simulated protect failure")

    ai_bridge._STORE_MIGRATED_DIRS.clear()
    try:
        ai_bridge._dpapi_protect = broken_protect
        ai_bridge.list_sessions()
    finally:
        ai_bridge._dpapi_protect = real_protect
    with open(p15, "r", encoding="utf-8") as fp:
        raw15 = fp.read()
    check("T15a 明文保持原样未动", json.loads(raw15).get("session_id") == "legacy-t15")
    check("T15b 状态 encrypt-failed-keep-plain",
          ai_bridge.STORE_STATUS["state"] == "encrypt-failed-keep-plain",
          ai_bridge.STORE_STATUS["state"])

    # ---- T16 只读介质降级（rename 失败 → 保持明文，中间态清理）----
    print("-" * 66)
    print("T16 只读介质降级")
    p16 = os.path.join(ai_bridge.sessions_dir(), "legacy-t16.json")
    with open(p16, "w", encoding="utf-8") as fp:
        json.dump({"session_id": "legacy-t16", "ctx": {}, "messages": []}, fp)
    real_rename = os.rename

    def broken_rename(src, dst, *a, **kw):
        if os.path.abspath(str(src)) == os.path.abspath(p16):
            raise PermissionError("simulated readonly media")
        return real_rename(src, dst, *a, **kw)

    ai_bridge._STORE_MIGRATED_DIRS.clear()
    try:
        os.rename = broken_rename
        ai_bridge.list_sessions()
    finally:
        os.rename = real_rename
    with open(p16, "r", encoding="utf-8") as fp:
        raw16 = fp.read()
    check("T16a 明文保持原样未动", json.loads(raw16).get("session_id") == "legacy-t16")
    check("T16b .new-enc 中间态已清理", not os.path.exists(p16 + ".new-enc"))
    check("T16c 状态 readonly-keep-plain",
          ai_bridge.STORE_STATUS["state"] == "readonly-keep-plain",
          ai_bridge.STORE_STATUS["state"])

    # ---- T17 ai_cases 明文案例迁移 + load_case 两格式兼容 ----
    print("-" * 66)
    print("T17 ai_cases 明文案例迁移")
    cpath = os.path.join(ai_bridge.cases_dir(), "case-legacy-t17.json")
    with open(cpath, "w", encoding="utf-8") as fp:
        json.dump({"context": {"vendor": "Ruijie"}, "response": "case body snmp NtbxPub"},
                  fp, ensure_ascii=False, indent=2)
    ai_bridge._STORE_MIGRATED_DIRS.clear()
    rows17 = ai_bridge.list_cases()
    with open(cpath, "r", encoding="utf-8") as fp:
        raw17 = fp.read()
    check("T17a 迁移后落盘为密文", raw17.lstrip().startswith("dpapi-v1:"), raw17[:40])
    check("T17b 明文内容不再在场", "NtbxPub" not in raw17)
    check("T17c 备份已删", not glob.glob(cpath + ".bak-*"))
    hit = [d for _p, d in rows17 if (d.get("context") or {}).get("vendor") == "Ruijie"]
    check("T17d list_cases/load_case 读回一致",
          hit and hit[0].get("response") == "case body snmp NtbxPub")

    # ---- T18 无 DPAPI 降级：会话明文兜底可用、存量迁移跳过留痕 ----
    print("-" * 66)
    print("T18 无 DPAPI 降级（会话路径）")
    real_avail = ai_bridge._dpapi_available
    try:
        ai_bridge._dpapi_available = lambda: False
        sess18 = ai_bridge.new_session({"vendor": "HW"})
        p18 = ai_bridge.save_session(sess18)
        with open(p18, "r", encoding="utf-8") as fp:
            raw18 = fp.read()
        check("T18a 无 DPAPI → 明文兜底落盘", not raw18.lstrip().startswith("dpapi-v1:"))
        check("T18b 明文会话可读回",
              (ai_bridge.load_session(p18) or {}).get("session_id") == sess18["session_id"])
        ai_bridge.STORE_STATUS.update(state="ok", detail="")
        p18b = os.path.join(ai_bridge.sessions_dir(), "legacy-t18.json")
        with open(p18b, "w", encoding="utf-8") as fp:
            json.dump({"session_id": "legacy-t18", "ctx": {}, "messages": []}, fp)
        ai_bridge._STORE_MIGRATED_DIRS.clear()
        ai_bridge.list_sessions()
        check("T18c 存量明文迁移跳过（保持明文）",
              not open(p18b, "r", encoding="utf-8").read().lstrip().startswith("dpapi-v1:"))
        check("T18d 状态 plain-no-dpapi",
              ai_bridge.STORE_STATUS["state"] == "plain-no-dpapi",
              ai_bridge.STORE_STATUS["state"])
    finally:
        ai_bridge._dpapi_available = real_avail

    # ---- T8 发布 zip 零敏感文件回归断言 ----
    _run_t8()

    print("=" * 66)
    if FAILED:
        print("结果：%d 项失败：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("结果：全部通过（DPAPI 迁移/round-trip/降级路径 + 发布 zip 零敏感断言）")
    return 0


def _run_t8():
    print("-" * 66)
    print("T8 发布 zip 零敏感文件回归断言")
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as fp:
        gitignore = fp.read()
    check("T8a gitignore 排除 ai_config.json", "ai_config.json" in gitignore)
    check("T8b gitignore 排除迁移备份/临时文件",
          "ai_config.json.bak-*" in gitignore and "ai_config.json.new-enc" in gitignore)
    with open(os.path.join(REPO, ".github", "workflows", "release.yml"),
              encoding="utf-8") as fp:
        release = fp.read()
    for needle in ("NetToolBox.exe", "check_db.py", "command_lib.db",
                   "seed_data/", "白名单外条目"):
        check("T8c release.yml 白名单含 %s" % needle, needle in release)
    with open(os.path.join(REPO, "scripts", "build_exe.py"), encoding="utf-8") as fp:
        build_src = fp.read()
    check("T8d build_exe.py 不引用 ai_config（exe 数据不含配置文件）",
          "ai_config" not in build_src)
    # --add-data 仅 seed_data / theme.qss / icon.ico（正向核对；带引号匹配避免数到文档字符串）
    add_data = [ln for ln in build_src.splitlines() if '"--add-data"' in ln]
    check("T8e --add-data 参数共 3 条（seed_data/theme.qss/icon.ico）", len(add_data) == 3,
          str(len(add_data)))


if __name__ == "__main__":
    sys.exit(main())
