# -*- coding: utf-8 -*-
"""
dedupe.py —— 入库查重引擎（三级查重：精确 → 相似度 → 用户决策）

零依赖（difflib 为标准库），全程离线，不引入向量/嵌入模型。
被 db.find_duplicates（同步候选检索）与产出入库对话框（异步预查，第 2 轮）共用。

归一化（所有比对的前置，与 db.normalize_commands_text / normalize_error_text
合并统一——db 侧已改为委托本模块）：
    按行去首尾空白、去空行、去整行注释（! 或 # 开头）、压缩行内空白、统一小写

相似度算法按比对对象选择：
    命令块（命令库去向）：行集合 Jaccard 为主 + difflib.SequenceMatcher 序列比兜底，取较大值
    报错原文 / 现象 / 标题：SequenceMatcher 字符比（归一化后）

三级判定：
    score ≥0.95      高度疑似重复（块默认不勾选，标红）
    0.80~0.95        疑似相似（黄色警示，用户决定）
    <0.80            通过（零打扰）
"""
import difflib

HIGH_THRESHOLD = 0.95
WARN_THRESHOLD = 0.80
MAX_CANDIDATES = 5          # find_duplicates 返回候选上限


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
def normalize_lines(text, lower=True):
    """归一化行列表：去首尾空白、去空行、去整行注释（!/#）、压缩行内空白、统一小写"""
    lines = []
    for ln in str(text or "").splitlines():
        s = " ".join(ln.strip().split())
        if not s or s.startswith("!") or s.startswith("#"):
            continue
        if lower:
            s = s.lower()
        lines.append(s)
    return lines


def normalize_text(text, lower=True):
    """命令文本归一化（返回换行拼接的归一化串）"""
    return "\n".join(normalize_lines(text, lower))


def normalize_error_text(text, lower=True):
    """报错原文 / 现象 / 标题归一化：去全部空白 + 统一小写"""
    s = "".join(str(text or "").split())
    return s.lower() if lower else s


# ---------------------------------------------------------------------------
# 相似度
# ---------------------------------------------------------------------------
def jaccard_lines(a_text, b_text, lower=True):
    """行集合 Jaccard（归一化后）；双方均空 = 1.0，单方空 = 0.0"""
    a = set(normalize_lines(a_text, lower))
    b = set(normalize_lines(b_text, lower))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def sequence_ratio(a_text, b_text, lower=True):
    """SequenceMatcher 字符比（归一化后）；双方均空 = 1.0，单方空 = 0.0"""
    a = normalize_error_text(a_text, lower)
    b = normalize_error_text(b_text, lower)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def command_similarity(a_text, b_text):
    """命令块相似度：行集合 Jaccard 为主 + SequenceMatcher 序列比兜底，取较大值"""
    j = jaccard_lines(a_text, b_text)
    if j == 1.0:
        return 1.0
    if j == 0.0 and not normalize_text(a_text) and not normalize_text(b_text):
        return 1.0
    return max(j, sequence_ratio(a_text, b_text))


def text_similarity(a_text, b_text):
    """报错原文 / 现象 / 标题相似度：SequenceMatcher 字符比（归一化后）"""
    return sequence_ratio(a_text, b_text)


def verdict(score):
    """三级判定：high（≥0.95 标红默认跳过）/ warn（0.80~0.95 黄色警示）/ pass"""
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= WARN_THRESHOLD:
        return "warn"
    return "pass"


def percent(score):
    """相似度展示：0.923 → "92%\""""
    return "%d%%" % round(score * 100)


# ---------------------------------------------------------------------------
# diff 预览（unified_diff；差异行着色由 UI 渲染层处理）
# ---------------------------------------------------------------------------
def unified_diff_preview(a_text, b_text, max_lines=48):
    """
    生成 unified diff 预览：a = 库内已有，b = 新产出。
    行首标记：' ' 相同 / '-' 库内独有 / '+' 新产出独有 / '@@' 块头。
    """
    a_lines = normalize_lines(a_text) or [""]
    b_lines = normalize_lines(b_text) or [""]
    diff = difflib.unified_diff(a_lines, b_lines,
                                fromfile="库内已有", tofile="新产出", lineterm="")
    return "\n".join(list(diff)[:max_lines])
