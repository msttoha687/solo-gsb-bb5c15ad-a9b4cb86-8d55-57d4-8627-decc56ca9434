"""SRT 断行排版：排版算法单元测试 + 接口测试。

运行：python -m pytest tests/ -q
"""
import unicodedata

from fastapi.testclient import TestClient

from app.main import app
from app.parsers import parse_srt
from app.rewrap import (
    MAX_WIDTH,
    MIN_WIDTH,
    build_rewrapped_srt,
    char_display_width,
    clusterize,
    flatten_body,
    is_latin_letter,
    rewrap_block,
    rewrap_subtitles,
)

client = TestClient(app)


def _srt(*bodies, index_start=1):
    """按 (正文,) 或 (正文行1, 正文行2) 构造最简 SRT 文本。"""
    blocks = []
    for i, body in enumerate(bodies, start=index_start):
        start = f"00:00:{i:02d},000"
        end = f"00:00:{i + 1:02d},000"
        blocks.append("\n".join([str(i), f"{start} --> {end}", body]))
    return "\n\n".join(blocks) + "\n"


def _first_item(text):
    return parse_srt(text)[0]


# ---------- 宽度与字符判定 ----------


def test_display_width():
    assert char_display_width("a") == 1
    assert char_display_width("1") == 1
    assert char_display_width("，") == 2
    assert char_display_width("你") == 2
    assert char_display_width("é") == 1
    # 组合附加符号 0 宽
    assert char_display_width("́") == 0
    # 零宽连接符 0 宽
    assert char_display_width("‍") == 0


def test_latin_letter_detection():
    assert is_latin_letter("a") and is_latin_letter("Z")
    assert is_latin_letter("é") and is_latin_letter("ñ")
    assert not is_latin_letter("你")
    assert not is_latin_letter("1")
    assert not is_latin_letter("，")


# ---------- 组合字符不与前字拆开 ----------


def test_combining_marks_stay_attached():
    # café / naïve 既可以是预组字符，也可以是「基字符 + 组合附加符号」
    text = "e" + "́"           # e + U+0301 COMBINING ACUTE ACCENT
    clusters = clusterize(text)
    assert len(clusters) == 1
    assert clusters[0].text == text and clusters[0].width == 1

    sample = "nai" + "̈" + "ve re" + "́" + "sistance taxi"
    row = rewrap_block(_first_item(_srt(sample)), 15)
    assert row.status == "rewrapped"
    for ln in row.body_lines:
        assert not unicodedata.combining(ln[0])
    # 断行发生在词间空白处：压平空白后与原文一致（单词本身未被拆开）
    assert " ".join((" ".join(row.body_lines)).split()) == sample
    # 组合附加符号始终紧跟其基字符（不会出现以组合字符开头的行）
    assert "re" + "́" + "sistance" in row.body_lines[1]
    assert "i" + "̈" in row.body_lines[0]


# ---------- 正文展平 ----------


def test_flatten_body_cjk_vs_latin():
    item = _first_item(_srt("你好\n世界"))
    assert flatten_body(item) == "你好世界"
    item2 = _first_item(_srt("hello\nworld"))
    assert flatten_body(item2) == "hello world"
    item3 = _first_item(_srt("你好\nhello"))
    assert flatten_body(item3) == "你好 hello"


# ---------- 枚举断点：句末标点优先 + 宽度均衡 ----------


def test_balanced_two_cjk_lines():
    row = rewrap_block(_first_item(_srt("你好世界你好世界")), 12)
    assert row.status == "rewrapped"
    assert row.body_lines == ("你好世界", "你好世界")
    assert (row.width1, row.width2) == (8, 8)


def test_sentence_end_priority_beats_balance():
    # 14 个宽字符（总宽 28）：句号在第 4 字后，句末断点 10/18（差 8），
    # 中点断点 14/14（差 0）；limit=20 两者都合法时必须优先句末标点。
    text = "甲乙丙句。后面还有好多字呢真"
    assert len(text) == 14
    row = rewrap_block(_first_item(_srt(text)), 20)
    assert row.body_lines[0] == "甲乙丙句。"
    assert row.body_lines[1] == "后面还有好多字呢真"


def test_balance_when_no_sentence_end():
    # 逗号非句末标点：枚举所有合法断点后取宽度差最小者。
    # 「甲乙丙丁，戊己」(14) / 「庚辛壬癸子丑寅卯」(16) 差 2，
    # 而按逗号切（10/22）第二行超宽，不可行。
    row = rewrap_block(_first_item(_srt("甲乙丙丁，戊己庚辛壬癸子丑寅卯")), 16)
    assert row.body_lines == ("甲乙丙丁，戊己", "庚辛壬癸子丑寅卯")
    assert (row.width1, row.width2) == (14, 16)


def test_english_sentence_end_priority():
    row = rewrap_block(
        _first_item(_srt("Hello world. Bye now friends")), 20
    )
    assert row.body_lines[0].endswith(".")


def test_latin_words_never_split():
    text = "This is a short subtitle line here"
    row = rewrap_block(_first_item(_srt(text)), 18)
    assert row.status == "rewrapped"
    # 用空格拼回与原文一致：没有任何单词被截断
    assert " ".join(row.body_lines) == text
    for ln in row.body_lines:
        words = set(text.split())
        for w in ln.split():
            assert w in words


def test_no_fixed_count_greedy_cut():
    # 连续无标点中文：不能固定在第 N 字截断，必须选宽度最均衡的断点
    row = rewrap_block(_first_item(_srt("一二三四五六七八九十")), 14)
    # 总宽 20，均衡断点为 5/5 字 -> 10/10
    assert (row.width1, row.width2) == (10, 10)
    assert row.body_lines == ("一二三四五", "六七八九十")


# ---------- 无合法排法：保留原正文并标记人工处理 ----------


def test_zwj_emoji_sequence_never_split():
    # 👨‍👩‍👧‍👦 = 7 个码点（4 emoji + 3 ZWJ），任何断点都不允许拆开该序列
    zwj = "‍"
    family = zwj.join(["👨", "👩", "👧", "👦"])
    text = "你好呀" + family + "再见啦" + "朋友们大家好"
    row = rewrap_block(_first_item(_srt(text)), 20)
    # 无论结果如何，emoji 连接序列必须完整落在同一行
    assert any(family in ln for ln in row.body_lines)
    for ln in row.body_lines:
        assert not ln.endswith(zwj) and not ln.startswith(zwj)


def test_unbreakable_long_word_marked_manual():
    word = "a" * 40
    item = _first_item(_srt(word))
    row = rewrap_block(item, 20)
    assert row.status == "manual"
    assert row.reason
    # 原正文保留（单行）
    assert row.body_lines == item.body_lines


def test_manual_keeps_original_multiline_body():
    item = parse_srt(
        "5\n00:00:01,000 --> 00:00:02,000\n"
        + "a" * 40 + "\n第二行原文\n"
    )[0]
    row = rewrap_block(item, 20)
    assert row.status == "manual"
    assert row.body_lines == ("a" * 40, "第二行原文")


def test_short_single_line_unchanged():
    item = _first_item(_srt("短句"))
    row = rewrap_block(item, 20)
    assert row.status == "unchanged"
    assert row.body_lines == ("短句",)


def test_existing_good_two_lines_unchanged():
    item = parse_srt("3\n00:00:01,000 --> 00:00:02,000\n你好世界\n你好世界\n")[0]
    row = rewrap_block(item, 12)
    assert row.status == "unchanged"
    assert row.body_lines == ("你好世界", "你好世界")


def test_line_breaks_trim_spaces():
    row = rewrap_block(
        _first_item(_srt("hello   world   foo   bar   baz")), 20
    )
    assert row.status != "manual"
    for ln in row.body_lines:
        assert not ln.startswith(" ") and not ln.endswith(" ")


# ---------- 导出：时间码与序号保持不变 ----------


def test_build_srt_keeps_timecodes_and_indices():
    text = "\n".join(
        [
            "7",
            "00:00:01,500 --> 00:00:02,500",
            "你好世界你好世界",
            "",
            "9",
            "00:00:03,000 --> 00:00:04,000",
            "另一块比较长需要断行的字幕",
            "",
        ]
    )
    items = parse_srt(text)
    rows = rewrap_subtitles(items, 12)
    out = build_rewrapped_srt(rows)
    blocks = out.strip().split("\n\n")
    # 序号沿用原值，不重新编号
    assert blocks[0].splitlines()[0] == "7"
    assert blocks[1].splitlines()[0] == "9"
    # 时间码原样
    assert blocks[0].splitlines()[1] == "00:00:01,500 --> 00:00:02,500"
    assert blocks[1].splitlines()[1] == "00:00:03,000 --> 00:00:04,000"
    # 文字内容不变（仅换行差异）：去换行后与原文一致
    def flat(block):
        return "".join(block.splitlines()[2:]).replace(" ", "")
    assert flat(blocks[0]) == "你好世界你好世界"
    assert flat(blocks[1]) == "另一块比较长需要断行的字幕"
    # 每块最多两行
    for b in blocks:
        assert len(b.splitlines()) <= 4  # 序号 + 时间码 + 最多 2 行正文


def test_manual_block_exported_with_original_body():
    item = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n" + "a" * 40 + "\n")[0]
    rows = rewrap_subtitles([item], 20)
    out = build_rewrapped_srt(rows)
    assert "a" * 40 in out
    assert "00:00:01,000 --> 00:00:02,000" in out


# ---------- 接口 ----------


def test_api_rewrap_preview():
    resp = client.post(
        "/api/rewrap", json={"srt": _srt("你好世界你好世界"), "max_width": 12}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total"] == 1
    assert data["summary"]["rewrapped"] == 1
    row = data["rows"][0]
    assert row["lines"] == ["你好世界", "你好世界"]
    assert row["status"] == "rewrapped"
    assert row["width1"] == 8 and row["width2"] == 8
    assert row["start_tc"].startswith("00:00:")
    assert row["original_text"] == "你好世界你好世界"


def test_api_rewrap_statuses_in_summary():
    srt = _srt(
        "短句",                                   # unchanged
        "一二三四五六七八",                       # rewrapped
        "a" * 40,                                 # manual
    )
    resp = client.post("/api/rewrap", json={"srt": srt, "max_width": 12})
    assert resp.status_code == 200
    s = resp.json()["summary"]
    assert s["total"] == 3
    assert s["rewrapped"] == 1
    assert s["unchanged"] == 1
    assert s["manual"] == 1
    statuses = [r["status"] for r in resp.json()["rows"]]
    assert statuses == ["unchanged", "rewrapped", "manual"]


def test_api_rewrap_download_keeps_timecodes():
    srt = _srt("你好世界你好世界", "This is another subtitle line here")
    resp = client.post("/api/rewrap.srt", json={"srt": srt, "max_width": 18})
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    text = resp.content.decode("utf-8")
    blocks = text.strip().split("\n\n")
    assert len(blocks) == 2
    # 时间码保持
    assert blocks[0].splitlines()[1].startswith("00:00:01,000 -->")
    # 序号保持 1、2（不重编号）
    assert [b.splitlines()[0] for b in blocks] == ["1", "2"]
    # 英文单词完整：去换行后逐词与原文一致
    words = " ".join(blocks[1].splitlines()[2:]).split()
    assert words == "This is another subtitle line here".split()


def test_api_rewrap_default_width():
    resp = client.post("/api/rewrap", json={"srt": _srt("你好")})
    assert resp.status_code == 200
    assert resp.json()["summary"]["max_width"] == 30


def test_api_rewrap_width_bounds():
    for w in (MIN_WIDTH - 1, MAX_WIDTH + 1, 0, 100):
        resp = client.post(
            "/api/rewrap", json={"srt": _srt("你好"), "max_width": w}
        )
        assert resp.status_code == 422, w
    for w in (MIN_WIDTH, MAX_WIDTH, 30):
        resp = client.post(
            "/api/rewrap", json={"srt": _srt("你好"), "max_width": w}
        )
        assert resp.status_code == 200


def test_api_rewrap_empty_srt_400():
    resp = client.post("/api/rewrap", json={"srt": "", "max_width": 30})
    assert resp.status_code == 400


def test_api_rewrap_bad_srt_400():
    resp = client.post("/api/rewrap", json={"srt": "没有时间码的内容", "max_width": 30})
    assert resp.status_code == 400
    assert "SRT" in resp.json()["detail"]


def test_index_serves_rewrap_tab():
    html = client.get("/").text
    assert "SRT 断行排版" in html
    assert "/static/rewrap.js" in html
    assert client.get("/static/rewrap.js").status_code == 200
