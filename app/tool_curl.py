# -*- coding: utf-8 -*-
"""
tool_curl.py —— curl 命令生成器引擎（R 批 2026-11-02）

定位：把「URL / 方法 / 请求头 / 数据 / 超时 / 重试 / --resolve」填成一条可直接粘贴
执行的 curl 命令。纯字符串构造，**不联网、不执行**——生成的是给用户复制走的文本。

三种目标 shell（跨平台粘贴的核心）：
    posix      —— bash / zsh / sh：单引号包裹 + '\\'' 转义，支持换行
    cmd        —— Windows cmd.exe：双引号包裹 + \\" 转义（curl 自身解析反斜杠），单行
    powershell —— PowerShell：单引号包裹 + '' 转义（不做变量展开），单行

安全与诚实（不做静默猜测）：
    · URL 必须带 http/https 协议头，且不含空白与换行；
    · 请求头必须是「名称: 值」，名称只允许 RFC 7230 token 字符 —— 换行直接拒绝（防头注入）；
    · Windows 两种 shell 下请求体含换行 → 明确报错（单行命令无法安全承载换行），
      提示改 POSIX 或把 JSON 压成一行，而不是偷偷替换掉换行；
    · 含 % 的参数在 cmd 模式下给出「可能被变量展开」的告警（不改写用户内容）。

命令行自测（无需 PyQt5）：
    python app/tool_curl.py
"""

import re

SHELLS = ("posix", "cmd", "powershell")
SHELL_LABELS = {
    "posix": "Linux / macOS（bash / zsh）",
    "cmd": "Windows CMD",
    "powershell": "Windows PowerShell",
}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESOLVE_RE = re.compile(r"^[A-Za-z0-9.\-_]+:\d{1,5}:\d{1,3}(?:\.\d{1,3}){3}$")

# 各 shell 的安全字符集：命中则不加引号（保持可读）
_SAFE_POSIX = re.compile(r"^[A-Za-z0-9_@%+=:,./-]+$")
_SAFE_WIN = re.compile(r"^[A-Za-z0-9_@%+=:,./-]+$")


class CurlError(ValueError):
    """生成器输入错误（消息直接展示给用户）"""


# ---------------------------------------------------------------------------
# 引号转义
# ---------------------------------------------------------------------------
def quote(arg, shell="posix"):
    """
    按目标 shell 对单个参数加引号。空串返回 ''（两个单引号）。
        posix      : 'a'"'"'b'  → 用 '\\'' 拼接
        cmd        : "a\"b"     → 反斜杠转义双引号（curl 会还原）
        powershell : 'a''b'     → 单引号内两个单引号表示一个
    """
    text = "" if arg is None else str(arg)
    if shell not in SHELLS:
        raise CurlError("未知的目标 shell：%r（只支持 %s）" % (shell, "/".join(SHELLS)))
    if text == "":
        return "''" if shell != "cmd" else '""'
    if shell == "posix":
        if _SAFE_POSIX.match(text):
            return text
        return "'" + text.replace("'", "'\\''") + "'"
    if shell == "cmd":
        if _SAFE_WIN.match(text):
            return text
        # cmd 双引号内：& | < > ^ 都失去特殊含义；" 用 \" 交给 curl 还原
        return '"' + text.replace('"', '\\"') + '"'
    # powershell
    if _SAFE_WIN.match(text):
        return text
    # PowerShell 5.1 把参数交给原生 exe 时不会转义内层双引号（著名坑），
    # 因此这里把 " 统一转成 \" 交给 curl 自己还原；7.3+ 同样兼容。
    return "'" + text.replace('"', '\\"').replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------
def normalize_url(url):
    raw = (url or "").strip()
    if not raw:
        raise CurlError("URL 不能为空（示例：https://api.example.com/v1/items）")
    if "\n" in raw or "\r" in raw:
        raise CurlError("URL 含换行：一条命令只能有一个 URL，请删掉多余行")
    if " " in raw or "\t" in raw:
        raise CurlError("URL 含空白字符：请对查询参数做 URL 编码（空格→%%20）")
    if not _URL_RE.match(raw):
        if "://" not in raw:
            raise CurlError("URL 缺少协议头：请写成 http:// 或 https:// 开头")
        raise CurlError("只支持 http / https 协议（收到：%s）" % raw.split("://", 1)[0])
    return raw


def normalize_headers(text):
    """
    解析「一行一个请求头」的文本。返回 [(name, value)]。
    空行忽略；缺冒号/名称非法/含换行直接报错。
    """
    headers = []
    # 只按 \n 切行（不用 splitlines：它会把孤立的 \r 也当换行，从而漏掉 CR 注入）
    for lineno, raw in enumerate((text or "").split("\n"), 1):
        line = raw[:-1] if raw.endswith("\r") else raw
        if not line.strip():
            continue
        if "\r" in line:
            raise CurlError("第 %d 行请求头含回车符（疑似头注入），已拒绝" % lineno)
        name, sep, value = line.partition(":")
        name = name.strip()
        value = value.strip()
        if not sep:
            raise CurlError("第 %d 行请求头缺冒号：%r（正确写法 Content-Type: application/json）"
                            % (lineno, line.strip()))
        if not name:
            raise CurlError("第 %d 行请求头名称为空" % lineno)
        if not _HEADER_NAME_RE.match(name):
            raise CurlError("第 %d 行请求头名称含非法字符：%r（RFC 7230 只允许 token 字符）"
                            % (lineno, name))
        if "\n" in value:
            raise CurlError("第 %d 行请求头值含换行（疑似头注入），已拒绝" % lineno)
        headers.append((name, value))
    return headers


def normalize_resolve(text):
    """解析 --resolve 行（host:port:ip，一行一个）"""
    entries = []
    for lineno, raw in enumerate((text or "").split("\n"), 1):
        item = (raw[:-1] if raw.endswith("\r") else raw).strip()
        if not item:
            continue
        if not _RESOLVE_RE.match(item):
            raise CurlError("第 %d 行 --resolve 格式非法：%r（正确写法 example.com:443:10.0.0.5）"
                            % (lineno, item))
        host, port, ip = item.split(":")
        if not 1 <= int(port) <= 65535:
            raise CurlError("第 %d 行 --resolve 端口越界：%s" % (lineno, port))
        for part in ip.split("."):
            if int(part) > 255:
                raise CurlError("第 %d 行 --resolve IP 段越界：%s" % (lineno, ip))
        entries.append(item)
    return entries


def _int_field(value, name, low, high, default):
    if value in (None, ""):
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise CurlError("%s 必须是整数（收到 %r）" % (name, value))
    if not low <= number <= high:
        raise CurlError("%s 超出范围 %d-%d（收到 %d）" % (name, low, high, number))
    return number


# ---------------------------------------------------------------------------
# 命令构造
# ---------------------------------------------------------------------------
def build_argv(spec):
    """
    把表单规格转成参数列表（不含 curl 本身）。spec 字段：
        url(必填) / method / headers(文本) / data / timeout / retries /
        resolve(文本) / insecure / location / silent / head
    返回 (argv, warnings)
    """
    warnings = []
    url = normalize_url(spec.get("url"))
    method = (spec.get("method") or "GET").strip().upper()
    if method not in METHODS:
        raise CurlError("方法只支持 %s（收到 %r）" % ("、".join(METHODS), method))
    headers = normalize_headers(spec.get("headers"))
    resolves = normalize_resolve(spec.get("resolve"))
    body = spec.get("data") or ""
    timeout = _int_field(spec.get("timeout"), "超时（秒）", 1, 3600, None)
    retries = _int_field(spec.get("retries"), "重试次数", 0, 10, None)

    argv = []
    if method == "HEAD":
        argv.append("--head")
    elif method != "GET" or body.strip():
        # GET 默认不带 -X；带请求体时必须显式 -X GET，否则 curl 会按 POST 处理
        argv += ["-X", method]
    if spec.get("insecure"):
        argv.append("-k")
    if spec.get("location"):
        argv.append("-L")
    if spec.get("silent"):
        argv += ["-sS"]
    if timeout is not None:
        argv += ["--max-time", str(timeout)]
    if retries:                      # 0 = 不重试，不必写进命令
        argv += ["--retry", str(retries)]
        if spec.get("retry_delay") not in (None, ""):
            delay = _int_field(spec.get("retry_delay"), "重试间隔（秒）", 0, 60, 1)
            argv += ["--retry-delay", str(delay)]
    for item in resolves:
        argv += ["--resolve", item]
    for name, value in headers:
        argv += ["-H", "%s: %s" % (name, value)]
    if body.strip():
        if method == "HEAD":
            raise CurlError("HEAD 请求不应带请求体：请去掉数据或改用 GET/POST")
        argv += ["-d", body]
        if not any(h[0].lower() == "content-type" for h in headers) and body.lstrip().startswith(("{", "[")):
            warnings.append("请求体看起来是 JSON，但没设置 Content-Type: application/json（部分服务端会 415）")
    argv.append(url)

    if "%" in " ".join(argv):
        warnings.append("参数含 %：CMD 会先做变量展开（%VAR%），交互粘贴通常没问题，"
                        "写进 .bat 时需写成 %%")
    return argv, warnings


def build_curl(spec, shell=None):
    """
    生成命令文本。返回 dict：
        {"cmd","shell","shell_label","argv","warnings"}
    抛 CurlError 表示输入非法。
    """
    shell = (shell or spec.get("shell") or "cmd").lower()
    if shell not in SHELLS:
        raise CurlError("未知的目标 shell：%r" % shell)
    argv, warnings = build_argv(spec)
    body = spec.get("data") or ""
    if shell != "posix" and ("\n" in body.replace("\r\n", "\n").rstrip("\n")):
        # 尾部单个换行对 -d 影响不大，但中间换行在单行命令里无法安全表达
        raise CurlError("Windows 单行命令无法安全携带多行请求体：请把 JSON 压成一行，"
                        "或切到「Linux / macOS」模式生成")
    parts = ["curl"] + [quote(arg, shell) for arg in argv]
    return {
        "cmd": " ".join(parts),
        "shell": shell,
        "shell_label": SHELL_LABELS[shell],
        "argv": argv,
        "warnings": warnings,
    }


def build_all(spec):
    """一次生成三种 shell 的命令（UI 切换 tab 时无需重新校验）"""
    out = {}
    for shell in SHELLS:
        out[shell] = build_curl(spec, shell)
    return out


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def _selftest():
    spec = {
        "url": "https://api.example.com/v1/items?q=中文&page=2",
        "method": "POST",
        "headers": "Content-Type: application/json\nAuthorization: Bearer a'b\"c",
        "data": '{"name":"张三","note":"a&b|c"}',
        "timeout": 15,
        "retries": 2,
        "resolve": "api.example.com:443:10.0.0.5",
        "location": True,
        "silent": True,
    }
    for shell in SHELLS:
        out = build_curl(spec, shell)
        print("[%s] %s" % (shell, out["cmd"]))
        for w in out["warnings"]:
            print("   ! %s" % w)
    print("-" * 60)
    bad = [
        {"url": "api.example.com/x"},
        {"url": "ftp://h/x"},
        {"url": "https://h/x", "headers": "BadLine"},
        {"url": "https://h/x", "headers": "X: a\rInjected: b"},
        {"url": "https://h/x", "resolve": "h:443"},
        {"url": "https://h/x", "method": "FETCH"},
    ]
    for case in bad:
        try:
            build_curl(case, "posix")
            print("[X] 未拦截：%s" % case)
        except CurlError as exc:
            print("[OK] 拒绝 %-40s → %s" % (str(case)[:40], exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
