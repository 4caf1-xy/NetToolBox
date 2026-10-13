# -*- coding: utf-8 -*-
"""
ai_bridge.py —— NetToolBox AI 诊断桥接层（任务：主程序直接集成 AI 诊断）

职责边界（产品决策：单一 exe，AI 功能直接内置）：
    1. 配置读写：ai_config.json 存 exe 同目录（与 command_lib.db 同一惯例）；
       API Key 经 DPAPI 加密落盘（api_key_enc，dpapi-v1；旧明文首启事务式迁移）
    2. 连接测试：1 token 测试请求，3s 内返回成功/失败 + 延迟
    3. 网络探测：QThread 做 TCP connect 到配置端点的 443 端口（超时 2s）
    4. 流式对话：QThread 中 requests SSE 流式拉取，支持取消
    5. prompt 组装：系统提示固定模板 + 用户上下文结构化拼入 + 本地案例 few-shot
    6. 案例库：ai_cases/{时间戳}_{现象slug}.json 的存 / 列 / 载 / 删 / 相似检索

硬性约束：
    - 零新依赖：HTTP 只用 requests；socket 仅用于探测（标准库）
    - requests / socket 一律在函数/线程内部 lazy import —— 不装 requests、
      不联网时，本模块照常可导入，主程序其余功能完全不受影响
    - 业务模块（db/renderer/analyzer）零修改；本文件不反向依赖任何 UI 模块
"""

import base64
import json
import os
import re
import time
import uuid
import datetime

from PyQt5.QtCore import QThread, pyqtSignal

# ---------------------------------------------------------------------------
# 配置（ai_config.json，exe 同目录，encoding="utf-8"）
# ---------------------------------------------------------------------------
CONFIG_FILENAME = "ai_config.json"
CASES_DIRNAME = "ai_cases"
SESSIONS_DIRNAME = "sessions"

DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",   # 任意 OpenAI 兼容端点均可
    "api_key": "",                                # 空 = 未配置
    "model": "deepseek-chat",                     # 下拉可选也允许手输
    "timeout": 60,                                # 秒（对话读超时）
    "operator": "",                               # 操作人（写进案例留痕）
    "max_context_tokens": 16384,                  # 多轮历史预算基数（×0.6 为实际预算）
    "vision_model": "",                           # 图片识别模型（空=不支持图片附件，第 2 轮启用）
}


def _base_dir():
    """exe / 源码同目录（与 db.get_base_dir 同规则，但不 import db，保持零耦合）"""
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    """ai_config.json 完整路径"""
    return os.path.join(_base_dir(), CONFIG_FILENAME)


# ---------------------------------------------------------------------------
# API Key 加密存储（DPAPI-v1 · Windows 用户级，零新依赖）
# ---------------------------------------------------------------------------
# 威胁模型：U 盘/现场介质上的 ai_config.json 随介质外泄 → 明文 key 直接丢。
# 方案（安全加固批 1，2026-10-13 裁决 A）：api_key 经 Windows DPAPI
# （CryptProtectData，用户级作用域）加密后以 "dpapi-v1:<base64>" 存进
# ai_config.json 的 api_key_enc 字段；明文 api_key 字段迁移后不再落盘。
#  · 免口令、零新依赖（ctypes 直调）；
#  · 介质外泄时密文不可解（绑定本机当前用户）——这是特性不是缺陷；
#  · 换机/换 Windows 账户需重新配置一次 key；
#  · 非 Windows / DPAPI 不可用 → 保持明文兜底（KEY_STATUS 记录状态，
#    现场不能因为加密不可用而瘫痪；该状态在 dev-notes 有完整清单）。
#  · 熵与威胁边界（审码修复批 F5 留痕，2026-10-13）：CryptProtectData
#    未传 OptionalEntropy —— 同一 Windows 用户上下文下的任意进程都能
#    解开该密文。防御面 = 介质外泄（换机/换 Windows 账户解不开），
#    不防御本机同用户恶意进程；这是「零依赖 + 免口令」约束下的已知取舍。
# 旧明文配置的自动迁移是**事务式**的（任何时刻介质上最多一份明文）：
#   写加密版临时文件 → 原文件 rename 为 .bak-<ts> → 加密版转正 →
#   解密 round-trip 验证 → 通过删备份 / 失败用备份还原。
KEY_ENC_PREFIX = "dpapi-v1:"
KEY_FIELD_ENC = "api_key_enc"

# 最近一次 key 解密/迁移状态（运行期，不落盘；UI 侧可读作提示）
# state 取值：ok / migrated / migrated-backup-locked / verify-failed-restored /
#             verify-failed-restore-failed / readonly-keep-plain /
#             plain-no-dpapi / encrypt-failed-keep-plain / decrypt-failed
KEY_STATUS = {"state": "ok", "detail": ""}


def _dpapi_available():
    """DPAPI 是否可用（Windows 专属能力）"""
    import sys
    return sys.platform == "win32"


def _dpapi_protect(plain_bytes):
    """bytes → DPAPI 加密 bytes（用户级）。失败抛异常。
    ★ pbData 必须用 c_void_p：用 c_char_p 时取字段会被 ctypes 自动转成
      bytes 对象，塞给 LocalFree 会堆损坏 → 进程 127 硬死（实测踩坑）。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(plain_bytes, len(plain_bytes))
    buf_in = DATA_BLOB(len(plain_bytes), ctypes.addressof(buf))
    buf_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(buf_in), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(buf_out))
    if not ok:
        raise OSError("CryptProtectData failed (err=%s)"
                      % ctypes.GetLastError())
    try:
        return ctypes.string_at(buf_out.pbData, buf_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
        ctypes.windll.kernel32.LocalFree(buf_out.pbData)


def _dpapi_unprotect(blob):
    """DPAPI blob bytes → 明文 bytes。失败抛异常。（pbData 用 c_void_p，理由同上）"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(blob, len(blob))
    buf_in = DATA_BLOB(len(blob), ctypes.addressof(buf))
    buf_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(buf_in), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(buf_out))
    if not ok:
        raise OSError("CryptUnprotectData failed (err=%s)"
                      % ctypes.GetLastError())
    try:
        return ctypes.string_at(buf_out.pbData, buf_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
        ctypes.windll.kernel32.LocalFree(buf_out.pbData)


def _decrypt_key(cfg):
    """从配置 dict 解出明文 key；无加密字段返回 ""，解密失败返回 "" 并记状态。"""
    enc = str(cfg.get(KEY_FIELD_ENC) or "")
    if not enc:
        return ""
    if not enc.startswith(KEY_ENC_PREFIX):
        KEY_STATUS.update(state="decrypt-failed",
                          detail="api_key_enc 格式不认识（%s…）" % enc[:12])
        return ""
    if not _dpapi_available():
        KEY_STATUS.update(state="plain-no-dpapi",
                          detail="非 Windows 环境无法解密 DPAPI 密文")
        return ""
    try:
        blob = base64.b64decode(enc[len(KEY_ENC_PREFIX):])
        plain = _dpapi_unprotect(blob).decode("utf-8")
        KEY_STATUS.update(state="ok", detail="")   # 状态语义=最近一次 key 操作结果
        return plain
    except Exception as exc:
        KEY_STATUS.update(state="decrypt-failed",
                          detail="DPAPI 解密失败：%s（换机/换账户后需重新配置）"
                                 % str(exc)[:60])
        return ""


def _migrated_payload(cfg, enc_value):
    """迁移写盘的完整 payload：明文 key 字段移除，换成加密字段。"""
    payload = {k: v for k, v in cfg.items()
               if k != "api_key" and not str(k).startswith("_")}
    payload[KEY_FIELD_ENC] = enc_value
    return payload


# ---------------------------------------------------------------------------
# 事务式配置改写（批 1 舞步通用化，L 批起 key / 放行指纹字段共用）
# ---------------------------------------------------------------------------
def _transactional_config_rewrite(path, new_payload, verify_fn):
    """
    配置文件事务式改写（安全加固批 1 的迁移舞步通用化）：
        1. 写新版临时文件 <path>.new-enc（此刻介质上唯一明文/旧版 = 原文件）
        2. 原文件 rename 为 <path>.bak-<ts>
        3. 新版转正（此后介质上零旧版）
        4. verify_fn(转正后路径) → 通过：删备份；失败：备份还原
    返回 (状态串, detail)。状态串同 KEY_STATUS 语义族：
        migrated / migrated-backup-locked / verify-failed-restored /
        verify-failed-restore-failed / readonly-keep-plain
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path + ".bak-" + stamp
    new = path + ".new-enc"
    try:
        with open(new, "w", encoding="utf-8") as fp:
            json.dump(new_payload, fp, ensure_ascii=False, indent=2)
        os.rename(path, bak)        # 旧版唯一份让位给备份
        os.replace(new, path)       # 新版转正
    except Exception as exc:
        # 典型：只读介质。尽力还原 rename，保持旧版可用。
        try:
            if os.path.exists(new):
                os.remove(new)
            if not os.path.isfile(path) and os.path.isfile(bak):
                os.replace(bak, path)
        except Exception:
            pass
        return "readonly-keep-plain", str(exc)[:60]
    try:
        ok = bool(verify_fn(path))
    except Exception:
        ok = False
    if ok:
        try:
            os.remove(bak)
            return "migrated", ""
        except Exception as exc:
            return "migrated-backup-locked", str(exc)[:40]
    try:
        os.replace(bak, path)
        return "verify-failed-restored", ""
    except Exception as exc:
        return "verify-failed-restore-failed", str(exc)[:40]


def _drop_stale_config_field(path, raw, field):
    """密文字段在场时清除配置文件里残留的指定明文字段（ciphertext-wins 通用形，
    审码修复批 F7 同语义：密文原样保留、不重加密）。失败（只读介质）静默让位。"""
    try:
        payload = {k: v for k, v in raw.items() if k != field}
        tmp = path + ".tmp-plaindrop"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _migrate_legacy_key(cfg):
    """
    事务式迁移旧明文 key（load_config 检测到明文 api_key 时触发一次）。
    舞步与状态语义见 _transactional_config_rewrite；任何一步失败都降级为
    「保持明文可用」（现场不因迁移瘫痪），状态记 KEY_STATUS。
    """
    plain = str(cfg.get("api_key") or "")
    path = config_path()
    if not os.path.isfile(path):
        return          # 没有实体配置文件（如内存态），无从迁移
    try:
        blob = _dpapi_protect(plain.encode("utf-8"))
    except Exception as exc:
        KEY_STATUS.update(state="encrypt-failed-keep-plain",
                          detail="DPAPI 加密失败，保持明文（%s）" % str(exc)[:60])
        return
    enc_value = KEY_ENC_PREFIX + base64.b64encode(blob).decode("ascii")
    payload = _migrated_payload(cfg, enc_value)

    def verify(p):
        with open(p, "r", encoding="utf-8") as fp:
            check = json.load(fp)
        return _decrypt_key(check) == plain and plain

    state, detail = _transactional_config_rewrite(path, payload, verify)
    if state == "migrated":
        KEY_STATUS.update(state="migrated",
                          detail="旧明文 key 已迁移为 DPAPI 加密存储")
    elif state == "migrated-backup-locked":
        KEY_STATUS.update(state=state,
                          detail="迁移成功但备份删除失败（%s），请手动删除 ai_config.json.bak-*"
                                 % detail)
    elif state == "verify-failed-restored":
        KEY_STATUS.update(state=state,
                          detail="迁移 round-trip 验证失败，已从备份还原明文")
    elif state == "verify-failed-restore-failed":
        KEY_STATUS.update(state=state,
                          detail="验证失败且备份还原失败（%s）——请检查 ai_config.json.bak-*"
                                 % detail)
    else:
        KEY_STATUS.update(state=state,
                          detail="配置文件写入失败（只读介质？），key 保持明文：%s" % detail)


# ---------------------------------------------------------------------------
# 放行指纹字段加密（L 批裁决 2，2026-10-13）
# ---------------------------------------------------------------------------
# 威胁模型：gate_allowances 的指纹 = kind+value 的 sha256 前 16 位，值域是低熵
# secret（口令/SNMP community 等）。把指纹明文放 ai_config.json = 给离线字典攻击
# 留了 oracle（拿常用口令字典算指纹比对即可反推）。方案：指纹字典随 key 同走
# DPAPI——存 gate_allowances_enc（dpapi-v1），明文字段迁移后不落盘；当日有效
# 语义不变；存量单字段迁移同事务式（复用 keyvault 帮助函数）。
GATE_ALLOW_FIELD = "gate_allowances"          # 旧明文字段（迁移前/降级兼容）
GATE_ALLOW_FIELD_ENC = "gate_allowances_enc"  # L 批起的新字段

# 运行期状态（不落盘；KEY_STATUS 模式）
# state 取值：ok / migrated / migrated-backup-locked / verify-failed-restored /
#             verify-failed-restore-failed / readonly-keep-plain /
#             plain-no-dpapi / encrypt-failed-keep-plain / decrypt-failed
GATE_STATUS = {"state": "ok", "detail": ""}


def encrypt_gate_dict_value(dict_value):
    """dict → "dpapi-v1:<base64>" 加密串。失败抛异常（调用方决定降级路径）。"""
    blob = _dpapi_protect(json.dumps(dict_value or {}, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8"))
    return KEY_ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_gate_value(enc_value):
    """加密串 → dict。无加密串 / 失败返回 None 并记 GATE_STATUS。"""
    enc = str(enc_value or "")
    if not enc.startswith(KEY_ENC_PREFIX):
        return None
    if not _dpapi_available():
        GATE_STATUS.update(state="plain-no-dpapi",
                           detail="非 Windows 环境无法解密放行指纹密文")
        return None
    try:
        blob = base64.b64decode(enc[len(KEY_ENC_PREFIX):])
        data = json.loads(_dpapi_unprotect(blob).decode("utf-8"))
        if isinstance(data, dict):
            GATE_STATUS.update(state="ok", detail="")
            return data
    except Exception as exc:
        GATE_STATUS.update(state="decrypt-failed",
                           detail="放行指纹密文解不开：%s（当日放行记忆需重新确认）"
                                  % str(exc)[:60])
    return None


def _migrate_plain_gate_allowances(raw, plain_dict):
    """
    旧明文 gate_allowances 字段 → gate_allowances_enc（load_config 检测到
    明文字段时触发一次，事务式单字段迁移，复用 keyvault 舞步）。
    """
    path = config_path()
    if not os.path.isfile(path):
        return
    if not _dpapi_available():
        GATE_STATUS.update(state="plain-no-dpapi",
                           detail="非 Windows 环境无法加密放行指纹，保持明文")
        return
    try:
        enc_value = encrypt_gate_dict_value(plain_dict)
    except Exception as exc:
        GATE_STATUS.update(state="encrypt-failed-keep-plain",
                           detail="DPAPI 加密失败，放行指纹保持明文（%s）" % str(exc)[:60])
        return
    payload = {k: v for k, v in raw.items()
               if k != GATE_ALLOW_FIELD and not str(k).startswith("_")}
    payload[GATE_ALLOW_FIELD_ENC] = enc_value

    def verify(p):
        with open(p, "r", encoding="utf-8") as fp:
            check = json.load(fp)
        return decrypt_gate_value(check.get(GATE_ALLOW_FIELD_ENC)) == plain_dict

    state, detail = _transactional_config_rewrite(path, payload, verify)
    if state == "migrated":
        GATE_STATUS.update(state="migrated",
                           detail="明文放行指纹已迁移为 DPAPI 加密存储")
    elif state == "migrated-backup-locked":
        GATE_STATUS.update(state=state,
                           detail="迁移成功但备份删除失败（%s），请手动删除 ai_config.json.bak-*"
                                  % detail)
    elif state in ("verify-failed-restored", "verify-failed-restore-failed"):
        GATE_STATUS.update(state=state,
                           detail="放行指纹迁移验证失败（%s）" % (detail or "已还原明文"))
    else:
        GATE_STATUS.update(state=state,
                           detail="配置文件写入失败（只读介质？），放行指纹保持明文：%s" % detail)


def load_config():
    """
    读配置。文件不存在 / JSON 坏了 / 不是 dict → 视为未配置，返回默认值。
    （任何异常都不抛 —— 现场不能因为一个配置文件把主程序拖死）
    api_key 优先取加密字段解密；检测到旧明文 key 触发一次事务式迁移。
    ★ 双字段并存语义（审码修复批 F7，ciphertext-wins，2026-10-13）：
        · 密文有效 + 文件里还有明文 → 明文是陈旧残留，清掉明文字段，
          绝不用明文重加密覆盖密文（重加密会以陈旧 key 破坏现役密文）；
        · 密文损坏 + 文件里有明文 → 保留明文可用（不迁移、不动密文），
          KEY_STATUS 记 decrypt-failed（换机/换账户场景现场不瘫痪）。
    """
    cfg = dict(DEFAULT_CONFIG)
    raw = {}
    try:
        with open(config_path(), "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            raw = data
            cfg.update(data)
    except Exception:
        pass
    # 迁移判定只看「文件里的原始明文 key」——若看解密后的 cfg，会把每次解密
    # 出来的明文误判为待迁移，造成每轮 load 都重跑迁移（写 bak→删 bak 空转）
    file_had_plain_key = bool(str(raw.get("api_key") or "").strip())
    if cfg.get(KEY_FIELD_ENC):
        plain_from_enc = _decrypt_key(cfg)
        if plain_from_enc:
            cfg["api_key"] = plain_from_enc
            if file_had_plain_key:
                _drop_stale_config_field(config_path(), raw, "api_key")
        elif file_had_plain_key:
            # 密文损坏：cfg["api_key"] 维持文件明文（不覆盖为 ""），现场可用；
            # 状态 decrypt-failed 已由 _decrypt_key 记录，这里补语义明细
            KEY_STATUS.update(
                state="decrypt-failed",
                detail="密文解不开但文件内有明文 key：已保留明文可用（未迁移，密文原样保留）")
    elif file_had_plain_key:
        # 旧明文 key（无密文字段）→ 事务式迁移为加密存储（只改文件，不影响返回值）
        _migrate_legacy_key(raw)
    # ---- 放行指纹字段（L 批裁决 2）----
    # 迁移判定只看「文件里的原始明文字段」（同 key 迁移的坑 3 教训）；
    # 空字典 {} 无敏感性，不值得为它跑一轮文件改写。
    raw_gate = raw.get(GATE_ALLOW_FIELD)
    if isinstance(raw_gate, dict) and raw_gate:
        if not raw.get(GATE_ALLOW_FIELD_ENC):
            _migrate_plain_gate_allowances(raw, raw_gate)       # 事务式单字段迁移
        else:
            # ciphertext-wins（F7 同语义）：密文在场，明文是陈旧残留，清掉
            _drop_stale_config_field(config_path(), raw, GATE_ALLOW_FIELD)
    # 基本类型兜底
    cfg["timeout"] = _clamp_int(cfg.get("timeout"), 5, 600, DEFAULT_CONFIG["timeout"])
    cfg["max_context_tokens"] = _clamp_int(cfg.get("max_context_tokens"), 512, 1000000,
                                           DEFAULT_CONFIG["max_context_tokens"])
    for key in ("base_url", "api_key", "model", "operator", "vision_model"):
        cfg[key] = str(cfg.get(key) or "").strip()
    return cfg


def save_config(cfg):
    """写配置（显式 utf-8）。返回是否成功（U 盘只读时静默失败，由调用方提示）
    ★ 文件里已有而本次未提交的字段（如手工配的 max_context_tokens / vision_model）
      一并保留，避免设置对话框保存时把它们抹掉。
    ★ key 加密落盘（安全加固批 1）：提交非空 api_key 且 DPAPI 可用 → 存 api_key_enc、
      不落明文；提交空 api_key（显式带字段）→ 连加密字段一起清（用户清空即注销）；
      DPAPI 不可用（非 Windows）→ 保持明文兜底。"""
    try:
        data = dict(cfg or {})
        try:
            with open(config_path(), "r", encoding="utf-8") as fp:
                old = json.load(fp)
            if isinstance(old, dict):
                for key, value in old.items():
                    data.setdefault(key, value)
        except Exception:
            pass
        # ---- key 加密落盘 ----
        submitted = "api_key" in data
        plain_key = str(data.get("api_key") or "").strip()
        if plain_key and _dpapi_available():
            try:
                blob = _dpapi_protect(plain_key.encode("utf-8"))
                data[KEY_FIELD_ENC] = KEY_ENC_PREFIX + \
                    base64.b64encode(blob).decode("ascii")
                data.pop("api_key", None)
            except Exception as exc:
                # 加密失败 → 明文兜底是有意降级（现场不因加密不可用瘫痪），
                # 但必须留状态痕迹（审码修复批 F6，2026-10-13）——静默降级不可审计
                KEY_STATUS.update(state="encrypt-failed-keep-plain",
                                  detail="save 时 DPAPI 加密失败，key 以明文落盘（%s）"
                                         % str(exc)[:60])
        elif submitted and not plain_key:
            data.pop("api_key", None)
            data.pop(KEY_FIELD_ENC, None)   # 显式清空 = 注销 key
        # ---- 放行指纹字段（L 批裁决 2）：密文在场时清除明文残留（F7 同语义）。
        #      save_config 的 setdefault 合并会把旧文件里的明文字段带回来，
        #      这里统一收口，保证迁移后介质上不再出现明文指纹。----
        if data.get(GATE_ALLOW_FIELD_ENC) and "gate_allowances" in data:
            data.pop(GATE_ALLOW_FIELD, None)
        data["timeout"] = _clamp_int(data.get("timeout"), 5, 600, DEFAULT_CONFIG["timeout"])
        with open(config_path(), "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def is_configured(cfg=None):
    """是否已配置（api_key 非空即视为已配置）"""
    cfg = cfg or load_config()
    return bool((cfg.get("api_key") or "").strip())


def _clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def endpoint_host(base_url=None):
    """从 base_url 提取域名（网络探测用）；解析失败回退 DeepSeek 官方域名。
    安全加固批 3：改用 urlparse（正确处理 [::1] 方括号 IPv6 与显式端口；
    旧正则对 http://[::1]:9000 会把 host 截成 "["）。"""
    from urllib.parse import urlparse
    url = (base_url or DEFAULT_CONFIG["base_url"]).strip()
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        host = ""
    return host or "api.deepseek.com"


def chat_url(base_url=None):
    """拼接 /chat/completions 端点（容忍 base_url 结尾带不带斜杠、带不带 /v1）"""
    return _api_root(base_url) + "/chat/completions"


def models_url(base_url=None):
    """拼接 /models 端点（OpenAI 兼容端点用它列出真实可用的模型名）"""
    return _api_root(base_url) + "/models"


def _api_root(base_url=None):
    """API 根地址：补 /v1（若用户只填了 https://host 或已带版本号则原样）"""
    url = (base_url or DEFAULT_CONFIG["base_url"]).strip().rstrip("/")
    if not url.lower().endswith("/v1"):
        # OpenAI 兼容端点惯例：允许用户只填 https://host（如网关/中转），自动补 /v1
        if not re.search(r"/v\d+$", url):
            url += "/v1"
    return url


def list_models(cfg=None):
    """
    拉取端点可用模型名（GET {root}/models）。
    返回 (ok, [模型名…], message)。失败时列表为空、message 是人话。
    ★ 解决"模型名到底该填什么"——各家中转/网关的模型名千奇百怪，
      靠猜只会得到 503/404，列表一拉即知。
    """
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return False, [], "尚未填写 API Key"
    ok_url, reason_url = validate_base_url(cfg.get("base_url"))
    if not ok_url:
        return False, [], "接口地址不合规：%s" % reason_url
    # 端点放行门禁（审码修复批 F3，2026-10-13）：陌生端点未放行不发请求——
    # /models 探测同样会带 Authorization 头，key 不出机原则与发送路径一致
    if not endpoint_approved(cfg.get("base_url"), cfg):
        return False, [], "端点未放行：请在「文件→设置」确认放行该端点后再试（本次未发送任何请求）"
    try:
        import requests  # 依赖可用性探针：本函数经 open_session 间接使用，pyflakes F401 属预期
    except ImportError:
        return False, [], "缺少 HTTP 客户端库（打包时需带 hiddenimports）"
    headers = {"Authorization": "Bearer %s" % cfg["api_key"]}
    try:
        session = open_session(cfg.get("base_url"))
        resp = session.get(models_url(cfg.get("base_url")), headers=headers, timeout=(5, 10))
    except Exception as exc:
        return False, [], _friendly_net_error(exc)
    if resp.status_code != 200:
        reason = {401: "API Key 无效（401）", 403: "无权限（403）",
                  404: "端点没有 /models 接口（404）"}.get(resp.status_code,
                                                        "HTTP %s" % resp.status_code)
        detail = ""
        try:
            detail = (resp.json().get("error") or {}).get("message") or ""
        except Exception:
            detail = (resp.text or "")[:120]
        return False, [], ("%s %s" % (reason, detail)).strip()
    try:
        payload = resp.json()
    except Exception:
        resp.encoding = "utf-8"
        try:
            payload = json.loads(resp.text)
        except Exception as exc:
            return False, [], "返回内容不是 JSON（%s）" % str(exc)[:60]
    items = payload.get("data") if isinstance(payload, dict) else payload
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name") or ""
        else:
            name = str(item)
        name = str(name).strip()
        if name:
            names.append(name)
    names = sorted(set(names))
    if not names:
        return False, [], "端点没有返回任何模型（该服务可能不支持 /models）"
    return True, names, "共 %d 个模型" % len(names)


# ---------------------------------------------------------------------------
# 连接测试（1 token 测试请求，3s 内返回）
# ---------------------------------------------------------------------------
def _is_loopback_host(host):
    """是否本机地址（这类端点绝不能走系统代理——Ollama/本地网关/单机测试常见）"""
    host = (host or "").lower().strip("[]")
    return host in ("localhost", "::1") or host.startswith("127.")


# ---------------------------------------------------------------------------
# base_url 策略（安全加固批 3）
# ---------------------------------------------------------------------------
# 规则（批 3 裁决）：
#   · 非本网地址（非 127.0.0.1/私网段）强制 HTTPS —— 明文发 key 到公网是外泄面
#   · 陌生 endpoint 首次使用弹确认，放行清单随 ai_config（approved_endpoints）
#   · 本地地址豁免（保证 Local AI / Ollama 场景可用）
LOCAL_NETS = None      # 惰性初始化（import ipaddress 一次）

def _local_nets():
    global LOCAL_NETS
    if LOCAL_NETS is None:
        import ipaddress
        LOCAL_NETS = [ipaddress.ip_network(n) for n in
                      ("127.0.0.0/8", "10.0.0.0/8",
                       "172.16.0.0/12", "192.168.0.0/16")]
    return LOCAL_NETS


def is_local_endpoint(base_url=None):
    """本网地址判定：localhost / ::1 / 127.x / RFC1918 私网段（HTTPS 强制与
    陌生端点确认均豁免此类）。域名解析不了按非本地处理（从严）。"""
    host = endpoint_host(base_url or "").lower().strip("[]")
    if host in ("localhost", "::1") or host.startswith("127."):
        return True
    try:
        import ipaddress
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _local_nets())


def validate_base_url(base_url):
    """base_url 合规校验。返回 (ok, 人话原因)。
    保存时与发送前双道执行（防手改配置文件绕过界面校验）。"""
    url = (base_url or "").strip()
    if not url:
        return False, "base_url 为空"
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://([^/:?#]+)", url)
    if not m:
        return False, "接口地址格式应为 http(s)://主机[:端口]/…（当前：%s）" % url[:60]
    scheme = m.group(1).lower()
    if scheme not in ("http", "https"):
        return False, "只支持 http/https 协议（当前：%s）" % scheme
    if scheme == "http" and not is_local_endpoint(url):
        return False, ("公网/非本网地址必须使用 HTTPS（本地模型 127.0.0.1/"
                       "私网地址可用 http）")
    return True, ""


def endpoint_addr(base_url=None):
    """(host, port) 解析：显式端口优先；否则 https→443 / http→80（网络探测用）。
    审码修复批 F4（2026-10-13）：解析失败 / 空 host 返回 None（无效标记），
    不再静默回退 api.deepseek.com:443——坏配置不该被探测误报成"官方端点可达"。"""
    from urllib.parse import urlparse
    url = (base_url or "").strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        port = parsed.port
        scheme = (parsed.scheme or "https").lower()
    except Exception:
        return None
    if not host:
        return None
    if not port:
        port = 443 if scheme != "http" else 80
    return host, int(port)


def approved_endpoints(cfg=None):
    """放行清单（ai_config.approved_endpoints，随配置文件走）。"""
    cfg = cfg or load_config()
    value = cfg.get("approved_endpoints")
    return [str(h) for h in value if isinstance(h, str)] if isinstance(value, list) else []


def endpoint_approved(base_url=None, cfg=None):
    """陌生端点判定：本网地址恒放行；其余须在放行清单内。"""
    if is_local_endpoint(base_url):
        return True
    return endpoint_host(base_url) in approved_endpoints(cfg)


def open_session(base_url):
    """
    建 requests.Session：
        · 常规端点 → trust_env=True（公司内网经代理出网是正常路径）
        · loopback 端点（127.x / localhost / ::1）→ trust_env=False
          ★ 实测踩坑：环境注入的 http_proxy 会让发往本机网关的请求被代理转发，
            代理进程回连它自己的 127.0.0.1 → 502 upstream connect failed
    """
    import requests
    session = requests.Session()
    if _is_loopback_host(endpoint_host(base_url)):
        session.trust_env = False
    return session


def test_connection(cfg=None):
    """
    发一条 max_tokens=1 的测试请求。
    返回 (ok: bool, message: str, latency_ms: int)。
    阻塞最多约 3s（connect/read 各 3s 上限）—— 在对话框里用线程包一层即可不卡 UI。
    """
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return False, "尚未填写 API Key", 0
    # base_url 策略（安全加固批 3）：测试连接同样受 HTTPS 规则约束
    ok_url, reason_url = validate_base_url(cfg.get("base_url"))
    if not ok_url:
        return False, "接口地址不合规：%s" % reason_url, 0
    # 端点放行门禁（审码修复批 F3，2026-10-13）：陌生端点未放行不发请求、
    # key 不出机——测试连接与发送路径同一原则（UI 层在弹确认框，见 SettingsDialog）
    if not endpoint_approved(cfg.get("base_url"), cfg):
        return False, "端点未放行：陌生端点须先在设置中确认放行（本次未发送任何请求）", 0
    try:
        import requests  # 依赖可用性探针：本函数经 open_session 间接使用，pyflakes F401 属预期
    except ImportError:
        return False, "缺少 requests 库（打包时需带 hiddenimports）", 0

    payload = {
        "model": cfg.get("model") or DEFAULT_CONFIG["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    headers = {
        "Authorization": "Bearer %s" % cfg["api_key"],
        "Content-Type": "application/json",
    }
    start = time.monotonic()
    try:
        session = open_session(cfg.get("base_url"))
        resp = session.post(chat_url(cfg.get("base_url")), json=payload,
                            headers=headers, timeout=(3, 3))
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return True, "连接成功", latency
        # 常见错误码转人话
        reason = {
            401: "API Key 无效（401）",
            403: "无权限（403）",
            404: "端点不存在（404），检查 base_url 是否正确",
            429: "限流（429），稍后重试",
        }.get(resp.status_code, "HTTP %s" % resp.status_code)
        detail = ""
        try:
            body = resp.json()
            detail = (body.get("error") or {}).get("message") or ""
        except Exception:
            pass
        hint = ""
        if detail and ("model" in detail.lower() or "模型" in detail) \
                and resp.status_code in (400, 404, 422, 503):
            hint = "（点「获取模型」按钮拉取该端点的真实模型名，下拉框可直接手输）"
        return False, ("%s %s%s" % (reason, detail, hint)).strip(), latency
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        return False, _friendly_net_error(exc), latency


def _friendly_net_error(exc):
    """把 requests 异常转成现场能看懂的一句话"""
    text = str(exc) or exc.__class__.__name__
    if "NameResolutionError" in text or "getaddrinfo failed" in text or "name or service" in text.lower():
        return "无法解析域名（DNS 不通，当前可能没有网络）"
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "连接超时（网络不通或端点不可达）"
    if "Connection refused" in text:
        return "连接被拒绝（端点服务未开放）"
    if "SSLError" in text or "certificate" in text.lower():
        return "SSL 证书错误（%s）" % text[:80]
    return text[:120]


# ---------------------------------------------------------------------------
# 网络探测（QThread · TCP connect · 2s 超时）
# ---------------------------------------------------------------------------
class NetworkProbe(QThread):
    """
    一次性探测线程：TCP connect 到配置端点。
    UI 层负责"启动时探一次 + 每 60s 重探一次"（QTimer 重启本线程）。

    安全加固批 3 修复：
        · 按按 base_url 解析端口（https 默认 443 / http 默认 80 / 显式端口优先）
          —— 旧实现恒连 443，本地模型 http://127.0.0.1:11434 被探测判死，
          发送按钮永久置灰（Local AI 场景实际不可用的根因）；
        · loopback 端点直接判可达（批 3 裁决：本地服务起没起由发送时的
          连接错误给出人话，不做误杀探测）。

    信号：probe_result(bool reachable, str detail)
    """
    probe_result = pyqtSignal(bool, str)

    def __init__(self, base_url=None, parent=None):
        super(NetworkProbe, self).__init__(parent)
        addr = endpoint_addr(base_url)
        self._invalid = addr is None        # base_url 解析不出主机 = 配置无效（F4）
        self._host, self._port = addr if addr else ("", 0)
        self._is_local = is_local_endpoint(base_url)
        self._timeout = 2.0

    def run(self):
        # socket lazy import：保持模块级零网络依赖（离网审计友好）
        import socket
        if self._invalid:
            self.probe_result.emit(False, "配置无效：base_url 解析不出主机地址")
            return
        if self._is_local:
            self.probe_result.emit(True, "本地端点 %s:%d（不探测）"
                                   % (self._host, self._port))
            return
        try:
            sock = socket.create_connection((self._host, self._port),
                                            timeout=self._timeout)
            sock.close()
            self.probe_result.emit(True, "%s:%d" % (self._host, self._port))
        except Exception as exc:
            self.probe_result.emit(False, "%s:%d：%s"
                                   % (self._host, self._port,
                                      _friendly_net_error(exc)))


# ---------------------------------------------------------------------------
# prompt 组装
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是一名网络运维 AI 助手，服务对象是在客户现场做安全设备/网络设备交付与故障排查的工程师。\n"
    "回答必须遵守：\n"
    "1. 给出的每一条命令都必须标注「适用厂商」与「OS 版本」（例如：适用 华为 VRP8；适用 Cisco IOS 15.x）。\n"
    "2. 不确定、未经真机验证的命令必须明确标注「未经真机验证」，并提醒先在维护窗口验证。\n"
    "3. 优先给出诊断步骤（先执行什么、观察什么输出、依据什么判断下一步），而不是直接下结论。\n"
    "4. 全程使用简体中文；命令用 Markdown 代码块（```）包裹；步骤用有序列表。\n"
    "5. 涉及删除/重置/影响业务的操作必须显式警告风险。\n"
    "6. 产出可入库的命令块时必须遵守（入库校验会按此打回）：\n"
    "   a. 参数一律用 {{参数名}} 占位（小写下划线命名），每个参数标注：必填或选填、"
    "默认值、取值范围或可选值；\n"
    "   b. 关键步骤前加一行以 # 开头的注释（独立成行），说明该步做什么、"
    "是否影响转发、是否需要 commit/save 才生效；\n"
    "   c. 每个参数的说明要写清：控制什么、常用取值、安全注意（禁止只复述参数名充数）；\n"
    "   d. 参数较多或含枚举/范围时，在命令块之后追加一个『【参数表】』标注的 JSON 代码块，\n"
    "      数组元素字段：name/type(string|int|enum|ip|flag)/label/required/default/\n"
    "      description/example/choices(enum 必带)/range(int 建议)；\n"
    "   e. 命令为变更类（非纯查询，含配置修改/服务重启/文件写入）时，必须在命令块之后\n"
    "      追加一个『【回退方案】』标注的代码块：与命令同构的多行文本，# 注释行写前置检查\n"
    "      （如\"# 前置：确认配置已备份\"），undo/no/delete 序列以恢复变更前状态为终点，\n"
    "      复核命令收尾；只用该厂商真实存在的命令，禁止臆造；查询类免填。"
)


# 排查向导已给出结论时追加的"协同指令"（任务3）：
#   向导已经诊断过一轮，AI 不该从零重推 —— 而是评估/补漏/给验证路径。
#   未检测到结论标记时，这段不拼入，下游 prompt 与既有版本逐字一致。
WIZARD_CONCLUSION_GUIDE = (
    "【排查向导结论协同指令】\n"
    "排查向导已给出初步结论。你的任务不是从零诊断，而是：\n"
    "1. 评估该结论是否成立，指出可能遗漏的原因分支\n"
    "2. 给出结论成立的前提条件，以及不成立时的替代假设\n"
    "3. 补充向导未覆盖的排查角度与验证命令"
)

CONCLUSION_MARK = "【排查结论】"


def has_wizard_conclusion(context):
    """上下文中是否带排查结论（来自排查向导的【排查结论】段）"""
    ctx = context or {}
    return any(CONCLUSION_MARK in (ctx.get(k) or "")
               for k in ("steps", "echo", "symptom"))




# ---------------------------------------------------------------------------
# 会话/案例落盘加密（L 批裁决 1，2026-10-13）：复用批 1 DPAPI-v1 全套语义
# ---------------------------------------------------------------------------
# 威胁模型与批 1 同一：sessions/*.json / ai_cases/*.json 含设备信息、命令回显、
# 可能含口令，明文落 exe 同目录随介质走。方案（不引入 master key、不新建加密
# 框架）：文件级逐个加密——整文件 JSON 序列化后 DPAPI 加密，落盘内容 =
# "dpapi-v1:<base64>"（复用 KEY_ENC_PREFIX）。
#  · 写入即加密：save_session / save_case 一律先 protect 再落盘；
#  · 首启逐文件事务式迁移：旧明文文件 → 写 .new-enc → 原文件 rename .bak-<ts>
#    → 转正 → round-trip 验证 → 过删备份 / 败还原（同批 1 舞步，任何时刻介质上
#    最多一份明文）；
#  · 只读 FS / 非 Windows / DPAPI 失败 → 降级保明文可用 + 状态留痕（KEY_STATUS
#    模式，STORE_STATUS）；
#  · 取舍（裁决记录，正式出处 dev-notes L 批节）：DPAPI 绑定本机本账户 = 换机后
#    会话历史不可迁移——介质外泄优先，接受此代价。
STORE_STATUS = {"state": "ok", "detail": "", "migrated": 0}

# 已做过首启迁移扫描的目录（每目录每进程一次；.bak-*/.new-enc 中间态按文件名
# 过滤天然跳过——它们不以 .json 结尾）
_STORE_MIGRATED_DIRS = set()


def _store_protect(obj):
    """dict → "dpapi-v1:<base64>" 文件内容串。失败抛异常。"""
    return KEY_ENC_PREFIX + base64.b64encode(
        _dpapi_protect(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    ).decode("ascii")


def _read_store_json(path):
    """读会话/案例文件（两种格式兼容）：dpapi-v1 密文 → 解密解析；明文 JSON →
    原样解析。任何异常返回 None（与旧 load 语义一致：坏文件跳过）。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw = fp.read()
        if raw.lstrip().startswith(KEY_ENC_PREFIX):
            if not _dpapi_available():
                STORE_STATUS.update(state="plain-no-dpapi",
                                    detail="非 Windows 环境无法解密会话/案例文件")
                return None
            plain = _dpapi_unprotect(
                base64.b64decode(raw.strip()[len(KEY_ENC_PREFIX):]))
            data = json.loads(plain.decode("utf-8"))
        else:
            data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_store_json(path, obj):
    """写会话/案例文件：DPAPI 可用 → 加密落盘；不可用/加密失败 → 明文兜底
    （可用性优先，加密异常状态留痕不静默）。返回 (True, "") / (False, 错误)。"""
    content = None
    if _dpapi_available():
        try:
            content = _store_protect(obj)
        except Exception as exc:
            STORE_STATUS.update(state="encrypt-failed-keep-plain",
                                detail="会话/案例加密失败，以明文落盘：%s" % str(exc)[:60])
            content = None
    try:
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(content if content is not None
                     else json.dumps(obj, ensure_ascii=False, indent=2))
        return True, ""
    except Exception as exc:
        return False, str(exc)[:80]


def _migrate_plain_store_file(path):
    """单个明文会话/案例文件的事务式加密迁移（批 1 舞步，文件级）。返回状态串。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw_text = fp.read()
    except Exception:
        return "unreadable"
    if raw_text.lstrip().startswith(KEY_ENC_PREFIX):
        return "already-enc"
    try:
        if not isinstance(json.loads(raw_text), dict):
            return "not-dict-skip"
    except Exception:
        return "bad-json-skip"      # 坏文件不迁移保持原样（load 侧本来就跳过）
    if not _dpapi_available():
        STORE_STATUS.update(state="plain-no-dpapi",
                            detail="非 Windows 环境无法加密会话/案例文件，保持明文")
        return "plain-no-dpapi"
    try:
        enc_value = KEY_ENC_PREFIX + base64.b64encode(
            _dpapi_protect(raw_text.encode("utf-8"))).decode("ascii")
    except Exception as exc:
        STORE_STATUS.update(state="encrypt-failed-keep-plain",
                            detail="会话/案例加密失败，保持明文：%s" % str(exc)[:60])
        return "encrypt-failed-keep-plain"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path + ".bak-" + stamp
    new = path + ".new-enc"
    try:
        with open(new, "w", encoding="utf-8") as fp:
            fp.write(enc_value)
        os.rename(path, bak)        # 明文唯一份让位给备份
        os.replace(new, path)       # 加密版转正
    except Exception as exc:
        # 典型：只读介质。尽力还原 rename，保持明文可用。
        try:
            if os.path.exists(new):
                os.remove(new)
            if not os.path.isfile(path) and os.path.isfile(bak):
                os.replace(bak, path)
        except Exception:
            pass
        STORE_STATUS.update(state="readonly-keep-plain",
                            detail="会话/案例写入失败（只读介质？），保持明文：%s"
                                   % str(exc)[:60])
        return "readonly-keep-plain"
    # round-trip 验证：从转正后的文件读回、解密、逐字节比对
    try:
        with open(path, "r", encoding="utf-8") as fp:
            check = fp.read()
        roundtrip = _dpapi_unprotect(
            base64.b64decode(check.strip()[len(KEY_ENC_PREFIX):]))
    except Exception:
        roundtrip = b""
    if roundtrip == raw_text.encode("utf-8"):
        try:
            os.remove(bak)
            STORE_STATUS["migrated"] += 1
            return "migrated"
        except Exception:
            return "migrated-backup-locked"
    try:
        os.replace(bak, path)
        STORE_STATUS.update(state="verify-failed-restored",
                            detail="会话/案例迁移验证失败，已从备份还原明文")
        return "verify-failed-restored"
    except Exception as exc:
        STORE_STATUS.update(state="verify-failed-restore-failed",
                            detail="会话/案例迁移验证失败且还原失败：%s" % str(exc)[:60])
        return "verify-failed-restore-failed"


def _ensure_store_migrated(dirpath):
    """首启逐文件迁移：目录内明文 *.json 逐个事务式加密（每目录每进程一次）。"""
    if not dirpath or dirpath in _STORE_MIGRATED_DIRS:
        return
    _STORE_MIGRATED_DIRS.add(dirpath)
    try:
        names = os.listdir(dirpath)
    except Exception:
        return
    for name in sorted(names):
        if not name.lower().endswith(".json"):
            continue                    # .bak-<ts> / .new-enc 等中间态天然跳过
        path = os.path.join(dirpath, name)
        if os.path.isfile(path):
            _migrate_plain_store_file(path)


# ---------------------------------------------------------------------------
# 案例库（ai_cases/{时间戳}_{现象slug}.json）
# ---------------------------------------------------------------------------
def cases_dir():
    """案例目录（exe 同目录 / ai_cases），访问时惰性创建"""
    path = os.path.join(_base_dir(), CASES_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _symptom_slug(symptom, limit=40):
    """现象转文件名 slug：保留中英文数字，其余替换为下划线"""
    text = (symptom or "case").strip()
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text)[:limit].strip("_")
    return slug or "case"




def list_cases():
    """按时间倒序列出全部案例（返回 [(path, dict)]；坏文件跳过）。
    首次调用触发 ai_cases/ 明文文件逐个事务式加密迁移（L 批裁决 1）。"""
    result = []
    try:
        _ensure_store_migrated(cases_dir())
        names = os.listdir(cases_dir())
    except Exception:
        return result
    for name in sorted(names, reverse=True):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(cases_dir(), name)
        data = load_case(path)
        if data:
            result.append((path, data))
    return result


def load_case(path):
    """读取单个案例（明文/DPAPI 密文两格式兼容）；任何异常返回 None"""
    return _read_store_json(path)


def delete_case(path):
    """删除案例文件。返回是否成功"""
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def find_similar_case(vendor, symptom, max_chars=1500):
    """
    检索本地案例库：同厂商 + 现象关键字有交集 → 取最近 1 条摘要。
    检索范围 = 新会话库（sessions/）+ 旧案例库（ai_cases/），新旧格式兼容。
    返回摘要文本（≤max_chars）；没有匹配返回 ""。
    """
    vendor = (vendor or "").strip()
    symptom = (symptom or "").strip()
    if not symptom:
        return ""
    # 简易分词：连续中文按 2 字滑窗取词 + 英数按词取，命中任一即算"现象相近"
    tokens = set(re.findall(r"[A-Za-z0-9]{2,}", symptom.lower()))
    for i in range(len(symptom) - 1):
        chunk = symptom[i:i + 2]
        if re.match(r"^[\u4e00-\u9fff]{2}$", chunk):
            tokens.add(chunk)
    if not tokens:
        return ""

    for path, data in list_sessions() + list_cases():
        ctx = _record_ctx(data)
        if vendor and (ctx.get("vendor") or "").strip() != vendor:
            continue
        old_symptom = (ctx.get("symptom") or "").strip()
        hay = old_symptom.lower()
        if not any(tok in hay for tok in tokens):
            continue
        # 命中 → 摘要：现象 + AI 响应截断（新会话取最后一条 assistant，旧案例取 response）
        response = _record_response(data)
        if len(response) > max_chars:
            response = response[:max_chars] + "…（已截断）"
        if not response:
            continue
        return "历史现象：%s\n当时的处理要点：\n%s" % (old_symptom, response)
    return ""


# ---------------------------------------------------------------------------
# 多轮会话：结构 / 设备上下文 / 组装与裁剪（对话式改造 第 1 轮）
# ---------------------------------------------------------------------------
# 会话上下文六字段（面板 = 会话基线，顺序即组装顺序）
CTX_FIELDS = ("vendor", "os", "model_name", "symptom", "echo", "steps")
CTX_LABELS = (("vendor", "厂商"), ("os", "OS 版本"), ("model_name", "设备型号"),
              ("symptom", "故障现象"), ("steps", "已尝试步骤"), ("echo", "命令回显"))

# 文本附件约束（第 1 轮只做文本类；图片类第 2 轮随 vision_model 开放）
ATTACH_TEXT_EXTS = (".log", ".txt", ".cfg", ".conf", ".json")
MAX_ATTACH_BYTES = 200 * 1024          # 单文件上限
MAX_ATTACH_COUNT = 3                   # 单轮个数上限
ATTACH_TRUNCATE_BYTES = 100 * 1024     # 超过则拼入内容截断并标注

# 历史预算 = max_context_tokens × 0.6（系统提示 + 设备上下文永远保留，不占预算）
HISTORY_BUDGET_RATIO = 0.6

# 多轮版系统提示追加段：告知模型"设备上下文 = 会话固定基线，冲突时以最新消息为准"
SYSTEM_PROMPT_BASELINE = (
    "6. 对话中的【设备上下文】是本会话的固定基线信息（设备信息/故障现象/命令回显/已尝试步骤）。"
    "回答应结合基线展开；若用户最新消息与基线冲突，以最新消息为准，"
    "并提醒用户更新输入区上方的设备上下文面板。"
)


def new_session(ctx=None):
    """
    新建一个会话结构：
        {session_id, created_at, ctx:{六字段}, messages:[{role, content,
         attachments:[{name,type,size,content,…}], ts}]}
    attachments 内含 content（拼 prompt 需要跨轮复用），比规格多一字段，属于必要扩展。
    """
    ctx = dict(ctx or {})
    now = datetime.datetime.now()
    return {
        "session_id": "%s_%s" % (now.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:6]),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": "session",
        "ctx": {key: str(ctx.get(key) or "") for key in CTX_FIELDS},
        "messages": [],
    }


def build_device_context(ctx):
    """
    设备上下文块：六字段非空才拼行（空字段跳行），全部为空返回 ""（不拼块）。
    顺序固定：厂商/OS 版本/设备型号/故障现象/已尝试步骤/命令回显。
    """
    lines = []
    for key, label in CTX_LABELS:
        value = str((ctx or {}).get(key) or "").strip()
        if value:
            lines.append("%s：%s" % (label, value))
    if not lines:
        return ""
    return "【设备上下文】\n" + "\n".join(lines) + "\n【设备上下文结束】"


def merge_attachments_into_text(text, attachments):
    """
    把附件内容拼进本轮 user 消息：
        【附件：{文件名}】\\n{内容}\\n【附件结束】
    截断过的附件在尾部追加标注。text 为空且无附件返回 ""。
    """
    parts = []
    if (text or "").strip():
        parts.append(text)
    for att in attachments or []:
        content = str(att.get("content") or "")
        if att.get("truncated"):
            content += "\n…（附件内容已截断：原文件 %d 字节，仅前 %d 字节参与分析）" % (
                att.get("size") or 0, ATTACH_TRUNCATE_BYTES)
        parts.append("【附件：%s】\n%s\n【附件结束】" % (att.get("name") or "attachment", content))
    return "\n\n".join(parts)


def merged_message_content(msg):
    """会话历史里一条消息的文本部分 = content + 文本附件块（不含图片，供组装与 token 估算共用；
    图片附件的 token 走 IMAGE_TOKEN_ESTIMATE 常数，见 estimate_message_tokens）"""
    texts = [a for a in (msg.get("attachments") or []) if not a.get("is_image")]
    return merge_attachments_into_text(msg.get("content") or "", texts)


def load_text_attachment(path):
    """
    读取一个文本附件（.log/.txt/.cfg/.conf/.json）。
    返回 (attachment_dict 或 None, 错误提示)。约束：单文件 ≤200KB；>100KB 截断并标注。
    全程 encoding="utf-8"（个别字节解码失败用 replace 兜底，现场日志常混入二进制尾巴）。
    """
    try:
        size = os.path.getsize(path)
    except Exception as exc:
        return None, "无法读取文件：%s" % str(exc)[:60]
    if size > MAX_ATTACH_BYTES:
        return None, "文件超过 200KB 上限（%d KB）：%s" % (size // 1024, os.path.basename(path))
    try:
        # B11（审计 2026-10-08）：按字节读——此前按"字符"读却标注"字节"，
        # CJK 内容下截断量与标注差 3 倍量级；解码失败仍用 replace 兜底
        with open(path, "rb") as fp:
            raw_bytes = fp.read(ATTACH_TRUNCATE_BYTES + 1)
    except Exception as exc:
        return None, "读取失败：%s" % str(exc)[:60]
    truncated = len(raw_bytes) > ATTACH_TRUNCATE_BYTES
    content = raw_bytes[:ATTACH_TRUNCATE_BYTES].decode("utf-8", errors="replace")
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    return {
        "name": name,
        "type": ext,
        "size": size,
        "content": content,
        "truncated": truncated,
    }, ""


# ---------------------------------------------------------------------------
# 图片附件（OpenAI 兼容多模态；vision_model 已配置时启用）
# ---------------------------------------------------------------------------
IMAGE_EXTS = (".png", ".jpg", ".jpeg")
MAX_IMAGE_BYTES = 5 * 1024 * 1024     # 单图 ≤5MB
IMAGE_TOKEN_ESTIMATE = 1024           # 每图 token 粗估（多模态无本地 tokenizer，按保守常数计）


def vision_supported(cfg=None):
    """是否配置了图片识别模型（vision_model 非空即视为支持）"""
    cfg = cfg or load_config()
    return bool(str(cfg.get("vision_model") or "").strip())


def load_image_attachment(path):
    """
    读取一个图片附件（.png/.jpg/.jpeg，≤5MB）→ base64 data URL。
    返回 (attachment_dict 或 None, 错误提示)。附件结构：{name,type,size,is_image,data_url}。
    """
    try:
        size = os.path.getsize(path)
    except Exception as exc:
        return None, "无法读取文件：%s" % str(exc)[:60]
    if size > MAX_IMAGE_BYTES:
        return None, "图片超过 5MB 上限（%s）：%s" % (_human_size_bytes(size), os.path.basename(path))
    try:
        with open(path, "rb") as fp:
            raw = fp.read()
        base64.b64encode(raw).decode("ascii")       # 先校验可编码
    except Exception as exc:
        return None, "读取失败：%s" % str(exc)[:60]
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    return {
        "name": name,
        "type": ext,
        "size": size,
        "is_image": True,
        "data_url": "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")),
        "content": "",
    }, ""


def _human_size_bytes(num):
    num = int(num or 0)
    if num >= 1024 * 1024:
        return "%.1fMB" % (num / 1024.0 / 1024.0)
    if num >= 1024:
        return "%.0fKB" % (num / 1024.0)
    return "%dB" % num


def message_api_content(msg):
    """
    一条会话消息的 API content：
        · 无图片附件 → 纯文本（content + 文本附件块，与第 1 轮一致）
        · 有图片附件 → OpenAI 兼容多模态数组：
          [{"type":"text","text":…},{"type":"image_url","image_url":{"url":data_url}}…]
    """
    attachments = msg.get("attachments") or []
    images = [a for a in attachments if a.get("is_image") and a.get("data_url")]
    texts = [a for a in attachments if not a.get("is_image")]
    text = merge_attachments_into_text(msg.get("content") or "", texts)
    if not images:
        return text
    parts = [{"type": "text", "text": text if text.strip() else "（请分析图片内容）"}]
    for img in images:
        parts.append({"type": "image_url", "image_url": {"url": img["data_url"]}})
    return parts


def estimate_message_tokens(msg):
    """单条消息 token 估算 = 文本字符/4 + 每张图片固定常数"""
    tokens = estimate_tokens(merged_message_content(msg))
    images = [a for a in (msg.get("attachments") or []) if a.get("is_image")]
    return tokens + IMAGE_TOKEN_ESTIMATE * len(images)


def build_chat_messages(ctx, history):
    """
    多轮组装（每轮发送 = 系统提示 + 设备上下文 + 历史 + 本条）：
        · 系统提示 = SYSTEM_PROMPT + 基线说明；设备上下文块非空则拼在系统消息内
        · 历史 = 会话已存消息 + 本条消息，按「轮」（user 起、含其后 assistant）从最新
          往回装入预算（= max_context_tokens × 0.6）；最新一轮永远保留
        · 被裁掉的最早轮数通过返回值告知 UI（消息流顶部灰字提示）
    返回 (api_messages, trimmed_rounds)。
    """
    cfg = load_config()
    budget = _clamp_int(cfg.get("max_context_tokens"), 512, 1000000,
                        DEFAULT_CONFIG["max_context_tokens"]) * HISTORY_BUDGET_RATIO

    # 按「轮」分组：user 开一轮，后续 assistant 归入本轮（取消遗留的连续 user 也算一轮）
    rounds, current = [], []
    for msg in history or []:
        if msg.get("role") == "user" and current:
            rounds.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        rounds.append(current)

    def _round_cost(round_msgs):
        return sum(estimate_message_tokens(m) for m in round_msgs)

    keep, used = [], 0
    for rnd in reversed(rounds):
        cost = _round_cost(rnd)
        if keep and used + cost > budget:      # 最新一轮无条件保留
            break
        keep.insert(0, rnd)
        used += cost
    trimmed = len(rounds) - len(keep)

    system = SYSTEM_PROMPT + "\n" + SYSTEM_PROMPT_BASELINE
    ctx_block = build_device_context(ctx)
    if ctx_block:
        system += "\n\n" + ctx_block

    messages = [{"role": "system", "content": system}]
    for rnd in keep:
        for msg in rnd:
            messages.append({"role": msg.get("role") or "user",
                             "content": message_api_content(msg)})
    return messages, trimmed


# ---------------------------------------------------------------------------
# 会话库（sessions/{session_id}.json；旧 ai_cases 保留兼容读取）
# ---------------------------------------------------------------------------
def sessions_dir():
    """会话目录（exe 同目录 / sessions），访问时惰性创建"""
    path = os.path.join(_base_dir(), SESSIONS_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def save_session(session):
    """会话落盘（每个对话一个文件，整文件覆盖写 = 保留最新状态）。
    L 批裁决 1：DPAPI 可用 → 整文件加密落盘（写入即加密）；不可用/加密失败 →
    明文兜底 + STORE_STATUS 留痕。失败返回 "" """
    try:
        payload = dict(session)
        payload["saved_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path = os.path.join(sessions_dir(), "%s.json" % (session.get("session_id") or "session"))
        ok, _err = _write_store_json(path, payload)
        return path if ok else ""
    except Exception:
        return ""


def list_sessions():
    """按时间倒序列出全部会话（返回 [(path, dict)]；坏文件跳过）。
    首次调用触发 sessions/ 明文文件逐个事务式加密迁移（L 批裁决 1）。"""
    result = []
    try:
        _ensure_store_migrated(sessions_dir())
        names = os.listdir(sessions_dir())
    except Exception:
        return result
    for name in sorted(names, reverse=True):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(sessions_dir(), name)
        data = load_session(path)
        if data:
            result.append((path, data))
    return result


def load_session(path):
    """读取单个会话（明文/DPAPI 密文两格式兼容）；任何异常返回 None"""
    return _read_store_json(path)


def delete_session(path):
    """删除会话文件。返回是否成功"""
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def _record_ctx(record):
    """新旧格式兼容取上下文：会话存 ctx，旧案例存 context"""
    return record.get("ctx") or record.get("context") or {}


def _record_response(record):
    """新旧格式兼容取 AI 回复全文：旧案例取 response，会话取最后一条 assistant"""
    text = (record.get("response") or "").strip()
    if text:
        return text
    for msg in reversed(record.get("messages") or []):
        if msg.get("role") == "assistant" and (msg.get("content") or "").strip():
            return msg["content"].strip()
    return ""


# ---------------------------------------------------------------------------
# 流式对话（QThread · requests SSE）
# ---------------------------------------------------------------------------
class ChatWorker(QThread):
    """
    流式对话线程（UI 不卡死的保证）。
    信号：
        chunk(str delta)            —— 增量文本
        failed(str message)         —— 失败（网络/HTTP/解析）
        finished_full(dict info)    —— 正常结束（tokens / model / 全文）
    用法：set_request(config, messages) → start()；cancel() 可随时中断。
    """
    chunk = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished_full = pyqtSignal(dict)

    def __init__(self, parent=None):
        super(ChatWorker, self).__init__(parent)
        self._config = {}
        self._messages = []
        self._cancelled = False
        self._resp = None            # 当前响应对象引用：cancel() 用它立即断连（P1-3）

    def set_request(self, config, messages):
        self._config = dict(config)
        self._messages = list(messages)
        self._cancelled = False
        self._resp = None

    @staticmethod
    def _disconnect(resp):
        """强制断开底层连接。

        实测（Windows 10 / Python 3.13 / urllib3 2.x，复现见 scripts/smoke_p1_fixes.py）：
        - 跨线程 resp.close() 不唤醒阻塞中的 recv（urllib3 收尾不真正关 fd）；
        - shutdown(SHUT_RDWR) 也不唤醒（Windows 平台特性）；
        - 底层 socket 对象的 close() 因 http.client makefile 的 io 引用计数
          只减不关，同样无效；
        - **对底层 socket 调 _real_close()（绕过引用计数真正关 fd）才会让
          阻塞中的 iter_lines 立即抛 ConnectionAbortedError(10053) 退出。**
        Linux/macOS 上 close 语义正常，本路径对各平台均无害（fd 已关时静默）。"""
        try:
            sock = resp.raw._fp.fp.raw._sock
            try:
                sock._real_close()      # makefile 引用计数下的真关 fd（实测唯一有效）
            except AttributeError:
                sock.close()            # 旧版本无 _real_close 时的兜底
        except Exception:
            pass
        try:
            resp.close()
        except Exception:
            pass

    def cancel(self):
        """中断：置标志 + 立即断开底层连接（iter_lines 会立刻抛错退出循环）。

        P1-3 修复（2026-10-08）：此前注释称"关闭底层连接"但实际只置标志，
        端点挂起时取消要等满 read timeout。现保存 resp 引用，cancel 跨线程
        强制断连，阻塞中的读立即抛错退出。"""
        self._cancelled = True
        resp = self._resp
        if resp is not None:
            self._disconnect(resp)


    def run(self):
        try:
            import requests  # 依赖可用性探针：实际请求经 open_session 的会话发出，F401 属预期
        except ImportError:
            self.failed.emit("缺少 requests 库：请用 pip install requests 后重试")
            return

        cfg = self._config
        # base_url 策略运行时复核（安全加固批 3）：防手改配置文件绕过界面校验
        ok_url, reason_url = validate_base_url(cfg.get("base_url"))
        if not ok_url:
            self.failed.emit("接口地址不合规：%s" % reason_url)
            return
        headers = {
            "Authorization": "Bearer %s" % (cfg.get("api_key") or ""),
            "Content-Type": "application/json",
        }
        payload = {
            "model": cfg.get("model") or DEFAULT_CONFIG["model"],
            "messages": self._messages,
            "stream": True,
        }
        timeout = _clamp_int(cfg.get("timeout"), 5, 600, DEFAULT_CONFIG["timeout"])
        try:
            session = open_session(cfg.get("base_url"))
            resp = session.post(chat_url(cfg.get("base_url")), json=payload,
                                headers=headers, stream=True,
                                timeout=(min(15, timeout), timeout))
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit("无法连接：%s" % _friendly_net_error(exc))
            return
        self._resp = resp          # 供 cancel() 立即断连（P1-3）

        try:
            if resp.status_code != 200:
                detail = ""
                try:
                    body = resp.json()
                    detail = (body.get("error") or {}).get("message") or ""
                except Exception:
                    detail = resp.text[:120]
                reason = {401: "API Key 无效（401）", 403: "无权限（403）",
                          404: "端点不存在（404）", 429: "限流（429）",
                          500: "服务端错误（500）"}.get(resp.status_code,
                                                      "HTTP %s" % resp.status_code)
                hint = ""
                if detail and ("model" in detail.lower() or "模型" in detail) \
                        and resp.status_code in (400, 404, 422, 503):
                    hint = "（请在「文件→设置」点「获取模型」拉取真实模型名）"
                self.failed.emit(("%s %s%s" % (reason, detail, hint)).strip())
                return

            # ★ SSE 规范强制 UTF-8：端点响应头若不带 charset，requests 会按
            #   ISO-8859-1 解码 → 中文全成乱码（冒烟实测踩过），这里显式纠正
            resp.encoding = "utf-8"

            usage = {}
            for raw_line in resp.iter_lines(decode_unicode=True):
                if self._cancelled:
                    break
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content") or ""
                if delta:
                    self.chunk.emit(delta)
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit("流式传输中断：%s" % _friendly_net_error(exc))
            else:
                # cancel() 断连引发的异常同样要通知 UI 恢复（否则按钮永久卡"生成中"）
                self.failed.emit("__CANCELLED__")
            return
        finally:
            try:
                resp.close()
            except Exception:
                pass
            self._resp = None

        if self._cancelled:
            # 取消不算失败，也不算完整成功；UI 层按"已取消"处理
            self.failed.emit("__CANCELLED__")
            return
        self.finished_full.emit({"usage": usage, "model": payload["model"]})


# ---------------------------------------------------------------------------
# 工具：token 粗估 / 响应中命令块提取（第 3 轮"转草稿条目"用）
# ---------------------------------------------------------------------------
def estimate_tokens(text):
    """
    token 估算（字符数 / 4 粗估，中英文混排的现场够用）。
    ★ 参数兼容两种输入：str（按 len 计）或 int（已统计好的字符数）——
      传入 int 时绝不能走 len()（TypeError 在 Qt 槽里会触发 PyQt5 qFatal
      直接杀进程，表现为"无任何报错闪退"，第 1 轮冒烟实测踩过）。
    """
    if isinstance(text, int):
        return max(0, text // 4)
    return max(0, len(text or "") // 4)


def extract_code_blocks(markdown_text):
    """
    从 AI 的 Markdown 响应里提取 ``` 代码块（用于第 3 轮"转为草稿条目"勾选）。
    返回 [(code_text, 前置说明行)]；没有代码块返回 []。

    ★ 按"围栏行"切分（与 ui_ai.md_to_html 同一策略）：围栏后面的 info string
      可能是任意文本——AI 常见写法有 ```python、```bash，也有 ```华为 VRP8、
      ```cisco ios。早前用 [a-zA-Z0-9_+-]* 限制 ASCII 会漏掉中文 info，
      导致整块被当成正文（冒烟实测踩过）。未闭合的尾块按"正在输出的代码块"照收。
    """
    parts = re.split(r"^\s*```[^\n]*\n?", markdown_text or "", flags=re.M)
    blocks = []
    for i, part in enumerate(parts):
        if i % 2 != 1:                      # 奇数段 = ``` 之间的代码
            continue
        code = part.rstrip("\n")
        if not code.strip():
            continue
        desc = ""
        for ln in reversed((parts[i - 1] or "").rstrip().splitlines()):
            ln = ln.strip()
            if ln:
                desc = ln[:60]
                break
        blocks.append((code, desc))
    return blocks


# ---------------------------------------------------------------------------
# 提取层（阶段二）：块分类 / 合并模式 / 标题模板
# ---------------------------------------------------------------------------
# 命令动词表：命中任一（按"首词"匹配）即认定为命令行。覆盖网络 CLI 与 Linux 常用命令。
COMMAND_VERBS = frozenset("""
ip ifconfig ipconfig ping ping6 traceroute tracert nslookup dig mtr netstat ss route
arp ethtool tcpdump curl wget telnet ssh scp ftp tftp
show display undo no set get config configure write copy delete dir more find save
reboot shutdown reload reset clear debugging debug info-center syslog
interface vlan port link-aggregation bgp ospf isis static route-policy acl nat
firewall security-policy address service zone session
systemctl service chkconfig journalctl dmesg uname uptime
cat zcat grep egrep fgrep head tail less vi vim nano echo sed awk cut sort uniq wc
ls cd pwd cp mv rm mkdir rmdir touch chmod chown chgrp ln tar gzip gunzip zip unzip
df du free ps top pidof kill pkill killall date hostname useradd userdel usermod
passwd groupadd id who last su sudo env export source alias which whereis type file
stat md5sum sha256sum dd fdisk parted mkfs mount umount blkid lsblk lscpu lsmem
yum apt apt-get dnf rpm dpkg pip pip3 npm modprobe insmod rmmod lsmod
iptables ip6tables nft firewall-cmd nmcli nmtui ifup ifdown brctl vconfig
tcpdump6 snmpwalk snmpget mysql psql redis-cli mongo mongoexport
screen tmux nohup xargs tee watch sleep history man help exit logout
""".split())

# 管道 / 重定向 / 命令串联模式：出现即认定为命令行（不依赖动词表）
SHELL_PATTERN_RE = re.compile(r"(\||>>?|&&|\|\||;|<)")


def _strip_cli_prompt(line):
    """
    去掉命令行首的 CLI 提示符，返回 (剩余命令文本, 是否识别到提示符)。
    兼容：<H3C>、[huawei-GigabitEthernet0/0/1]、Router#、SW1>、~$、[root@host ~]#。
    """
    s = line.strip()
    prompt = False
    # 方括号 / 尖括号整段提示符（可连续多段，如 [root@host ~]# ）
    while True:
        m = re.match(r"^[<\[][^>\]]*[>\]]\s*", s)
        if not m:
            break
        s = s[m.end():]
        prompt = True
    # ★ 方括号提示符后紧跟的 "#"（如 "[root@host ~]# systemctl …"）：
    #   仅当其后是真命令（命中动词/管道）时才按提示符剥掉，
    #   否则保留 "#" 让上游按注释行处理，避免误吞真注释
    if s.startswith("#"):
        rest = s[1:].strip()
        first = re.split(r"[\s;|&]+", rest, 1)[0].strip("()\"'").lower() if rest else ""
        if first in COMMAND_VERBS or (rest and SHELL_PATTERN_RE.search(rest)):
            s = rest
            prompt = True
    # 主机名式提示符：Word# 或 Word> （Word 为不含空白的标识符）
    m = re.match(r"^[A-Za-z0-9_.\-]+[#>]\s*(\S.*)$", s)
    if m and not re.match(r"^[A-Za-z0-9_.\-]+[#>]", m.group(1)):
        s = m.group(1)
        prompt = True
    return s, prompt


def classify_code_block(code):
    """
    命令块 / 非命令块判定（任务2-1/2-2）。
    返回 {"kind": "command"|"non_command", "needs_review": bool, "reasons": [str]}。

    判定顺序：
        1. 树形字符行（├── └── │）占比 >40% → 非命令（原因树/目录树）
        2. 非注释行全部以 # / ! 开头（或无内容）→ 非命令（纯注释）
        3. 任一非注释行命中动词表或管道/重定向模式 → 命令块
        4. 未命中任何动词 → 非命令（needs_review=True，兜底文本可能是说明）
    """
    reasons = []
    lines = [ln for ln in (code or "").splitlines() if ln.strip()]
    if not lines:
        return {"kind": "non_command", "needs_review": True, "reasons": ["空块"]}
    tree_chars = ("├──", "└──", "│", "├─", "└─")
    tree_lines = sum(1 for ln in lines if any(c in ln for c in tree_chars))
    if tree_lines > len(lines) * 0.4:
        reasons.append("树形字符行占比 %d/%d" % (tree_lines, len(lines)))
        return {"kind": "non_command", "needs_review": False, "reasons": reasons}

    content_lines = []
    for ln in lines:
        text, _prompt = _strip_cli_prompt(ln)
        if text.startswith("#") or text.startswith("!"):
            continue                       # 注释行不计
        content_lines.append(text)
    if not content_lines:
        reasons.append("全部为注释行")
        return {"kind": "non_command", "needs_review": False, "reasons": reasons}

    verb_hits = 0
    for text in content_lines:
        first = re.split(r"[\s;|&]+", text.strip(), 1)[0].strip("()\"'").lower()
        if first in COMMAND_VERBS or SHELL_PATTERN_RE.search(text):
            verb_hits += 1
    if verb_hits == 0:
        reasons.append("无命令动词命中")
        return {"kind": "non_command", "needs_review": True, "reasons": reasons}
    if verb_hits < len(content_lines):
        reasons.append("%d/%d 行未命中动词（可能含说明文字，请人工核对）"
                       % (len(content_lines) - verb_hits, len(content_lines)))
        return {"kind": "command", "needs_review": True, "reasons": reasons}
    return {"kind": "command", "needs_review": False, "reasons": []}


def extract_ai_blocks(response_text):
    """
    把 AI 回复解析成结构化块列表（入库对话框数据源）：
        [{block_index, code, desc, kind, needs_review, reasons}]
    block_index 与 extract_code_blocks 编号一致（copycode:// 与 ai_imports 共用）。
    """
    result = []
    for i, (code, desc) in enumerate(extract_code_blocks(response_text)):
        verdict = classify_code_block(code)
        result.append({
            "block_index": i,
            "code": code,
            "desc": desc,
            "kind": verdict["kind"],
            "needs_review": verdict["needs_review"],
            "reasons": verdict["reasons"],
        })
    return result


# 注释符按厂商自动选择：Cisco / 华为 / 华三 CLI 用 !，Linux（含各发行版）/ 其余用 #
_EXCLAM_VENDORS = ("cisco", "huawei", "h3c", "hp", "arista")


def comment_char_for_vendor(vendor):
    slug = str(vendor or "").lower()
    return "!" if any(key in slug for key in _EXCLAM_VENDORS) else "#"


def build_entry_title(symptom, vendor_os):
    """合并单条目标题模板："{现象}（{厂商 OS}）排查"，缺项时优雅降级"""
    symptom = (symptom or "").strip() or "故障排查"
    vendor_os = (vendor_os or "").strip()
    return "%s（%s）排查" % (symptom, vendor_os) if vendor_os else "%s排查" % symptom


def build_block_title(symptom, desc):
    """逐块独立模式标题："{现象} · {块标题}"，缺现象时退化为块标题"""
    desc = (desc or "").strip() or "命令块"
    symptom = (symptom or "").strip()
    return "%s · %s" % (symptom, desc) if symptom else desc


# ---------------------------------------------------------------------------
# 提取层（阶段二·报错库去向）：预填启发式
# ---------------------------------------------------------------------------
_ERROR_HINT_RE = re.compile(
    r"(error|err-|fail|invalid|unrecognized|unknown|down|denied|refused|"
    r"timeout|unreachable|lost|%%|错误|失败|不通|无法|拒绝|超时|震荡)", re.I)


def find_error_lines_in_echo(echo, ai_text):
    """
    报错原文预填启发式：从上下文面板回显中找「AI 分析引用过」的行。
    优先级：AI 引用的报错样貌行 > AI 引用的其它行 >（调用方回退）全部回显。
    返回行列表（可为空）。
    """
    ai_text = ai_text or ""
    lines = []
    for ln in (echo or "").splitlines():
        s = ln.strip()
        if len(s) < 6:
            continue
        # 逐字引用，或剥掉时间戳/序号类前缀后（残余 ≥10 字）命中也算引用
        tokens = s.split()
        hit = s in ai_text
        if not hit:
            for cut in range(1, min(5, len(tokens))):
                rest = " ".join(tokens[cut:])
                if len(rest) >= 10 and rest in ai_text:
                    lines.append(rest)
                    hit = True
                    break
        if not hit and s in ai_text:
            lines.append(s)
    errs = [ln for ln in lines if _ERROR_HINT_RE.search(ln)]
    return errs or lines


_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*(.*)|\*\*(.+?)\*\*|【(.+?)】)\s*$")


def extract_cause_section(ai_text):
    """
    原因分析预填启发式：在 AI 回复里找「原因/分析/判断依据」类标题下的段落
    （Markdown 标题 / 加粗 / 【】标题均识别）。提不到返回 ""（由用户粘贴）。
    """
    text = ai_text or ""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = _HEADING_RE.match(ln)
        if not m:
            continue
        title = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not any(k in title for k in ("原因", "分析", "判断依据", "根因")):
            continue
        para = []
        for follow in lines[i + 1:]:
            if _HEADING_RE.match(follow):
                break
            if follow.strip():
                para.append(follow.strip())
            elif para:
                break                              # 段落结束
        if para:
            return "\n".join(para)[:2000]
    return ""


# ---------------------------------------------------------------------------
# 提取层（阶段二·排查树去向）：步骤提取
# ---------------------------------------------------------------------------
STEP_HEAD_RE = re.compile(r"^\s*(?:#{1,6}\s*)?步骤\s*(\d+)\s*[：:、.．]\s*(.+?)\s*$")
STEP_OBSERVE_RE = re.compile(r"^\s*(?:观察|观察点|预期|判断)\s*[:：]\s*(.+?)\s*$")


def extract_steps_from_response(response_text):
    """
    排查树去向的步骤提取（任务3-5）：
        1) "步骤 N：标题" 结构块 —— 标题行起一段，段内命令块归该步，
           "观察：…" 行作该步观察点
        2) 无明确步骤结构 → 每个命令块一个步骤（标题用块前置说明 desc）
    返回 (steps, used_markers, conclusion)：
        steps      = [{"title", "commands", "observe"}]
        used_markers = 是否命中了"步骤 N"结构（供 UI 提示提取方式）
        conclusion = 原因/结论段（叶子结论预填，可空）
    """
    text = response_text or ""
    parts = re.split(r"^\s*```[^\n]*\n?", text, flags=re.M)   # 与 extract_code_blocks 同策略
    steps, used_markers, current = [], False, None
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # 代码块：命令块归当前步骤（无步骤标记时跳过，走兜底路径）
            verdict = classify_code_block(part.rstrip("\n"))
            if verdict["kind"] == "command" and current is not None:
                current["commands"] = (current["commands"] + "\n" + part.strip()).strip()
            continue
        for ln in part.splitlines():
            m = STEP_HEAD_RE.match(ln)
            if m:
                used_markers = True
                current = {"title": m.group(2).strip()[:60], "commands": "", "observe": ""}
                steps.append(current)
                continue
            m2 = STEP_OBSERVE_RE.match(ln)
            if m2 and current is not None:
                current["observe"] = m2.group(1).strip()[:80]

    if not used_markers:
        # 兜底：每个命令块一个步骤，标题用块前置说明（建议标题）
        steps = [{"title": (b["desc"] or "步骤 %d" % (i + 1))[:60],
                  "commands": b["code"], "observe": ""}
                 for i, b in enumerate(extract_ai_blocks(text))
                 if b["kind"] == "command"]
    for i, s in enumerate(steps, 1):
        if not s["title"]:
            s["title"] = "步骤 %d" % i
    return steps, used_markers, extract_cause_section(text)


def parse_ai_blocks(reply_text):
    """
    从 AI 回复全文解析『【参数表】』与『【回退方案】』标注块（schema 2026-10-09 任务5）。

    约定输出形态（SYSTEM_PROMPT 第 6.d/6.e 条）：
        【参数表】
        ```json
        [ {...}, ... ]
        ```
        【回退方案】
        ```
        多行回退命令文本
        ```
    解析容错：标注行与代码围栏之间允许空行；JSON 解析失败按缺参处理（入库校验
    会按 check_entry_params 打回，与 validate 同口径）；返回 (params, rollback)。
    """
    text = reply_text or ""
    params, rollback = [], ""
    # ---- 【参数表】：标注行后第一个 ``` 围栏内的 JSON ----
    m = re.search(r"【参数表】\s*```[a-zA-Z]*\s*\n(.*?)```", text, re.S)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                params = [p for p in data if isinstance(p, dict) and p.get("name")]
        except Exception:
            params = []
    # ---- 【回退方案】：标注行后第一个围栏（有语言标记也认）----
    m = re.search(r"【回退方案】\s*```[a-zA-Z]*\s*\n(.*?)```", text, re.S)
    if m:
        rollback = m.group(1).strip("\n")
    return params, rollback


def strip_ai_blocks(text):
    """
    从命令块文本中剥离【参数表】/【回退方案】段（含标注行与围栏），避免随命令入库；
    无标注时原样返回（逐字不变，供 merge 流程幂等）。
    """
    if not text:
        return text
    out = re.sub(r"【参数表】\s*```[a-zA-Z]*\s*\n.*?```\s*", "", text, flags=re.S)
    out = re.sub(r"【回退方案】\s*```[a-zA-Z]*\s*\n.*?```\s*", "", out, flags=re.S)
    out = out.replace("【参数表】", "").replace("【回退方案】", "").rstrip()
    return out or text


def merge_code_blocks(blocks, vendor, include_notes_blocks=None):
    """
    合并模式（默认）：勾选块按出现顺序拼成单条目 commands，块间插入注释分隔行
    保留步骤结构（分隔行："!―― 步骤N：块标题 ――"，注释符按厂商自动选）。
        blocks:              extract_ai_blocks 的子集（已勾选/已过滤）
        vendor:              厂商（决定注释符）
        include_notes_blocks: 需要并入 notes 的非命令块列表（None = 忽略）
    返回 (commands_text, notes_text)。notes 为非命令块原文拼接（供"内容追加到
    条目 notes"复选框），无则空串。
    """
    c = comment_char_for_vendor(vendor)
    cmd_parts, notes_parts = [], []
    step_no = 0
    for blk in blocks or []:
        if blk.get("kind") == "non_command":
            notes_parts.append(blk.get("code") or "")
            continue
        step_no += 1
        head = "%s―― 步骤%d：%s ――" % (c, step_no, (blk.get("desc") or "命令").strip() or "命令")
        cmd_parts.append(head + "\n" + (blk.get("code") or "").strip())
    notes = "\n\n".join(p for p in notes_parts if p.strip())
    if include_notes_blocks:
        extra = "\n\n".join((b.get("code") or "").strip()
                            for b in include_notes_blocks if (b.get("code") or "").strip())
        if extra:
            notes = (notes + "\n\n" + extra).strip()
    return "\n\n".join(cmd_parts), notes


def ai_selfcheck():
    """
    集成自检（供 main.py --check-ai 调用，也便于现场排查"AI 按钮点了没反应"）：
    返回 dict，含配置状态 / HTTP 依赖是否可用 / 各路径。全程只读，不发任何请求。
    ★ 这里刻意不打印任何 URL 与依赖名（打印内容由 main.py 组织），
      避免把网络依赖字样带进 main.py 源码而破坏离网审计。
    """
    cfg = load_config()
    try:
        import requests
        dep_ok, dep_ver = True, getattr(requests, "__version__", "?")
    except Exception as exc:
        dep_ok, dep_ver = False, str(exc)[:60]
    return {
        "configured": is_configured(cfg),
        "http_dependency": dep_ok,
        "http_dependency_version": dep_ver,
        "endpoint_host": endpoint_host(cfg.get("base_url")),
        "model": cfg.get("model") or "",
        "timeout": cfg.get("timeout"),
        "config_path": config_path(),
        "cases_dir": cases_dir(),
        "case_count": len(list_cases()),
        "system_prompt_chars": len(SYSTEM_PROMPT),
    }
