from callreader.verify import check_quote

SRC = [{"id": "att1", "pages": [
    "Entry guidelines\n4. Works created with AI cannot be submitted, and if an award-\nwinning work is later found to use AI, the prize must be returned.",
    "생성형 AI를 활용한 작품은 제출할 수 없으며 수상 이후라도 확인되면 수상 취소",
]}]


def test_verbatim_quote_found_with_page():
    c = check_quote("Works created with AI cannot be submitted", SRC)
    assert c.found and c.source_id == "att1" and c.page == 1


def test_line_break_and_hyphenation_folded():
    c = check_quote("if an award-winning work is later found to use AI", SRC)
    assert c.found


def test_korean_quote_found():
    assert check_quote("생성형 AI를 활용한 작품은 제출할 수 없으며", SRC).found


def test_paraphrase_rejected():
    c = check_quote("AI-generated works are not allowed", SRC)
    assert not c.found and "not found" in c.reason


def test_translation_rejected():
    assert not check_quote("Works made with generative AI cannot be entered", SRC).found


def test_too_short_rejected():
    assert not check_quote("AI", SRC).found


def test_ellipsis_fragments_must_all_match():
    assert check_quote("Works created with AI ... the prize must be returned", SRC).found
    assert not check_quote("Works created with AI ... the prize is doubled", SRC).found
