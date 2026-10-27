from callreader.axes import Card, Finding, State
from callreader.report import render
from callreader.verify import quote_context

SRC = [{"id": "att3", "kind": "hwpx",
        "url": "https://e.example/FileDown.jsp?user_file_nm=%5B%EA%B3%B5%EA%B3%A0%EB%AC%B8%5D+notice.hwpx",
        "pages": ["다음과 같은 결함이 발견될 경우, 심사 대상에서 제외되며 수상 취소 등의 조치가 취해질 수 있습니다. "
                  "- 인공지능(AI) 프로그램을 활용하여 제작한 작품인 경우 - 타인의 작품을 표절한 경우"]}]
QUOTE = "인공지능(AI) 프로그램을 활용하여 제작한 작품인 경우"


def test_context_carries_the_governing_sentence():
    ctx = quote_context(QUOTE, SRC)
    assert "심사 대상에서 제외되며" in ctx and "인공지능(ai) 프로그램" in ctx


def test_report_without_page_images_marks_quote_in_text_and_names_the_file():
    card = Card(url="https://x.example/notice", title="t", sources=SRC)
    card.findings = [Finding("ai_use", State.FAIL, answer="ban", quote=QUOTE, source_id="att3", page=1)]
    html = render(card)
    assert "<mark>" in html and "hwpx · [공고문] notice.hwpx" in html
    assert "class='solo'" in html and "class='right'" not in html


def test_local_path_is_not_shown():
    card = Card(url="C:/Users/someone/Desktop/poster.jpg", title="t", sources=[])
    assert "someone" not in render(card)
