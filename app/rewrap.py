"""SRT 断行排版：逐块把正文排成不超过两行、每行宽度不超过上限。

规则：
- 显示宽度：东亚宽字符（Unicode East_Asian_Width 为 W/F 及大多数 Emoji）
  计 2，其余字符计 1；组合用附加符号（Mn/Me/Cf 类零宽连接符等）计 0 宽，
  并作为前字簇的一部分，绝不与前字拆开；
- 连续拉丁字母构成的单词不可从中间断开；
- 合法断点：前簇与后簇均非零宽组合字符，且不拆开连续拉丁字母单词
  （即断点两侧不是同属一个拉丁单词的字母簇），同时断点不能落在行首/行尾的
  空白里；
- 枚举全部合法断点，先按「句末标点优先」、再按「两行宽度最均衡」选最优，
  不做固定字数贪心截断；
- 没有任何合法两行排法的块（如超长拉丁单词）保留原正文并标记人工处理。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .parsers import SrtItem

MIN_WIDTH = 12
MAX_WIDTH = 60

# 句末标点：句号 / 问号 / 叹号 / 省略号 等（含中文全角）
_SENTENCE_END = frozenset("。．.？?！!…‼⁈⁉")
# 句末标点后允许再跟的闭合类符号（引号、括号、书名号等）
_CLOSING_AFTER_END = frozenset("\"'”’」』）)】》〉｣»")

# ---------- 字符宽度与拉丁字母判定 ----------


def char_display_width(ch: str) -> int:
    """单字符显示宽度：宽字符 2、组合/零宽字符 0、其余 1。"""
    cp = ord(ch)
    if cp == 0:
        return 0
    if unicodedata.combining(ch):
        return 0
    cat = unicodedata.category(ch)
    if cat == "Cf":
        # 零宽连接符/零宽空格等格式字符不占宽
        return 0
    ea = unicodedata.east_asian_width(ch)
    if ea in ("W", "F"):
        return 2
    if ea == "A":
        # 模糊宽度字符（部分希腊/西里尔符号、整段破折号等）按窄字符计 1
        return 1
    # Emoji 符号（East Asian Width 通常为 N）按宽字符计 2
    if cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF:
        return 2
    return 1


def is_latin_letter(ch: str) -> bool:
    """是否为拉丁字母（含拉丁扩展区，含带重音字母）。"""
    if "a" <= ch <= "z" or "A" <= ch <= "Z":
        return True
    if unicodedata.category(ch) not in ("Ll", "Lu"):
        return False
    return unicodedata.name(ch, "").startswith("LATIN")


# ---------- 簇（显示原子）切分 ----------


@dataclass
class Cluster:
    text: str           # 簇的完整文本（基字符 + 跟随的组合字符）
    width: int          # 显示宽度
    latin: bool         # 是否拉丁字母簇（连续拉丁单词的一部分）
    space: bool         # 是否空白簇
    glue_after: bool = False  # 末尾含 ZWJ 等连接符，与后一簇不可断开


def _is_space(ch: str) -> bool:
    return unicodedata.category(ch) == "Zs" or ch in ("\t",)


def clusterize(text: str) -> list[Cluster]:
    """把文本切成簇序列：组合字符（Mn/Me）与零宽格式符粘在前一字符上。

    字符串开头若直接出现组合字符，则单独成簇（其宽度为 0）。
    含 ZWJ（U+200D）等连接符时标记 ``glue_after``，保证整个 Emoji 连接序列
    不会在换行处被拆开。
    """
    clusters: list[Cluster] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me") or cat == "Cf":
            if clusters:
                prev = clusters[-1]
                prev.text += ch
                prev.width += char_display_width(ch)
                if ord(ch) == 0x200D:
                    prev.glue_after = True
                continue
        clusters.append(
            Cluster(
                text=ch,
                width=char_display_width(ch),
                latin=is_latin_letter(ch),
                space=_is_space(ch),
            )
        )
    return clusters


# ---------- 正文展平：把原多行正文按块内换行语义合并 ----------


def flatten_body(item: SrtItem) -> str:
    """把字幕块原多行正文合并为单行文本。

    换行位置两侧若都是 CJK 宽字符，直接相接；否则补一个空格（典型的
    英文跨行断词）。行首尾空白去除，连续空白不额外折叠。
    """
    lines = list(item.body_lines)
    if not lines:
        return item.raw_text.strip()
    result = lines[0].strip()
    for nxt in lines[1:]:
        nxt = nxt.strip()
        if not nxt:
            continue
        if result and nxt:
            left = result[-1]
            right = nxt[0]
            if unicodedata.east_asian_width(left) in ("W", "F", "A") and \
                    unicodedata.east_asian_width(right) in ("W", "F", "A"):
                result += nxt
            elif not _is_space(left):
                result += " " + nxt
            else:
                result += nxt
        elif nxt:
            result += nxt
    return result.strip()


# ---------- 句末标点判定 ----------


def _ends_sentence(clusters: list[Cluster], cut: int) -> bool:
    """切分点 ``cut`` 之前的内容是否以句末标点收尾（允许后跟闭合引号）。

    从切点向前跳过若干闭合类符号，再看再前一簇是否句末标点。
    """
    i = cut - 1
    # 跳过切点前可能存在的空白
    while i >= 0 and clusters[i].space:
        i -= 1
    skipped_closing = 0
    while i >= 0 and skipped_closing <= 3:
        t = clusters[i].text
        if len(t) == 1 and t in _CLOSING_AFTER_END:
            i -= 1
            skipped_closing += 1
            continue
        break
    if i < 0:
        return False
    t = clusters[i].text
    return len(t) == 1 and t in _SENTENCE_END


# ---------- 候选断点评估 ----------


@dataclass
class Candidate:
    cut: int
    width1: int
    width2: int
    sentence_end: bool


def _content_width(clusters: list[Cluster], lo: int, hi: int) -> int:
    """簇区间 [lo, hi) 去掉首尾空白簇后的显示宽度。"""
    while lo < hi and clusters[lo].space:
        lo += 1
    while hi > lo and clusters[hi - 1].space:
        hi -= 1
    return sum(c.width for c in clusters[lo:hi])


def _trimmed_bounds(clusters: list[Cluster], lo: int, hi: int) -> tuple[int, int]:
    """返回去掉区间首尾空白簇后的 [lo, hi) 边界。"""
    while lo < hi and clusters[lo].space:
        lo += 1
    while hi > lo and clusters[hi - 1].space:
        hi -= 1
    return lo, hi


def _enumerate_candidates(clusters: list[Cluster], limit: int) -> list[Candidate]:
    """枚举所有合法断点并按宽度约束过滤。

    合法断点 cut（切在簇 cut-1 与 cut 之间）：
    - 1 <= cut < n；
    - 不切开拉丁单词：两侧相邻的非空白簇不能都是拉丁字母簇；
    - 切点不位于前簇或后簇为组合字符处（clusterize 已把组合字符并入前簇）；
    - 两段（去除行首尾空白后）宽度均 <= limit。
    """
    n = len(clusters)
    prefix = [0]
    for c in clusters:
        prefix.append(prefix[-1] + c.width)

    cands: list[Candidate] = []
    for cut in range(1, n):
        # 前一簇以 ZWJ 等连接符结尾时，整段连接序列不可断开
        if clusters[cut - 1].glue_after:
            continue
        # 相邻内容簇是否同属一个拉丁单词：仅当两簇直接相邻（中间无空白）
        # 且都是拉丁字母簇时才禁止断开。
        if (
            not clusters[cut - 1].space
            and not clusters[cut].space
            and clusters[cut - 1].latin
            and clusters[cut].latin
        ):
            continue
        l0, l1 = _trimmed_bounds(clusters, 0, cut)
        r0, r1 = _trimmed_bounds(clusters, cut, n)
        if l0 >= l1 or r0 >= r1:
            continue
        w1 = prefix[l1] - prefix[l0]
        w2 = prefix[r1] - prefix[r0]
        if w1 > limit or w2 > limit:
            continue
        cands.append(
            Candidate(
                cut=cut,
                width1=w1,
                width2=w2,
                sentence_end=_ends_sentence(clusters, cut),
            )
        )
    return cands


def _line_text(clusters: list[Cluster], lo: int, hi: int) -> str:
    """拼接簇区间为一行，去掉行首尾空白；内部空白簇原样保留。"""
    while lo < hi and clusters[lo].space:
        lo += 1
    while hi > lo and clusters[hi - 1].space:
        hi -= 1
    return "".join(c.text for c in clusters[lo:hi])


# ---------- 单块排版 ----------


@dataclass
class RewrapRow:
    index: int                      # 原 SRT 声明序号
    start_tc: str
    end_tc: str
    original_lines: tuple[str, ...]
    body_lines: tuple[str, ...]     # 排版后的正文行（1 或 2 行）
    status: str                     # rewrapped / unchanged / manual
    reason: str = ""                # 人工处理原因等附加说明
    width1: int = 0
    width2: int = 0
    changed: bool = False


def rewrap_block(item: SrtItem, limit: int) -> RewrapRow:
    """对单个字幕块做断行排版。"""
    flat = flatten_body(item)
    original_lines = tuple(ln.strip() for ln in item.body_lines if ln.strip())
    clusters = clusterize(flat)
    total = sum(c.width for c in clusters)

    # 单行即可容纳：统一输出单行（顺带去掉原有的多余换行）
    if total <= limit:
        lines = (flat,) if flat else ("",)
        changed = lines != original_lines
        return RewrapRow(
            index=item.index,
            start_tc=item.start_tc,
            end_tc=item.end_tc,
            original_lines=original_lines,
            body_lines=lines,
            status="rewrapped" if changed else "unchanged",
            width1=total,
            width2=0,
            changed=changed,
        )

    cands = _enumerate_candidates(clusters, limit)
    if not cands:
        return RewrapRow(
            index=item.index,
            start_tc=item.start_tc,
            end_tc=item.end_tc,
            original_lines=original_lines,
            body_lines=original_lines or (item.raw_text,),
            status="manual",
            reason="无合法两行排法（可能含超出宽度上限且不可拆分的单词）",
            width1=0,
            width2=0,
            changed=False,
        )

    # 句末标点断点优先；同级别取两行宽度差最小（最均衡）；再平手取切点靠前
    # 的方案，保证结果稳定可复现。
    def rank(c: Candidate):
        balance = abs(c.width1 - c.width2)
        return (
            0 if c.sentence_end else 1,   # 句末优先
            balance,                       # 宽度均衡
            c.cut,                         # 稳定次序
        )

    best = min(cands, key=rank)
    line1 = _line_text(clusters, 0, best.cut)
    line2 = _line_text(clusters, best.cut, len(clusters))
    lines = (line1, line2)
    changed = lines != original_lines
    return RewrapRow(
        index=item.index,
        start_tc=item.start_tc,
        end_tc=item.end_tc,
        original_lines=original_lines,
        body_lines=lines,
        status="rewrapped" if changed else "unchanged",
        width1=best.width1,
        width2=best.width2,
        changed=changed,
    )


def rewrap_subtitles(items: list[SrtItem], limit: int) -> list[RewrapRow]:
    """逐块排版。"""
    return [rewrap_block(it, limit) for it in items]


def build_rewrapped_srt(rows: list[RewrapRow]) -> str:
    """生成排版后的 SRT：保持原时间码与序号，仅替换正文换行。"""
    blocks = []
    for row in rows:
        lines = [str(row.index), f"{row.start_tc} --> {row.end_tc}"]
        lines.extend(row.body_lines)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"
