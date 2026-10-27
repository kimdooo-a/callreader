import io
import struct
import zipfile

from callreader.docs import _hwp_para_text, extract, hwpx_pages, sniff
from callreader.fetch import _same_image, image_key, source_from_bytes
from callreader.segment import BOX
from PIL import Image


def _hwpx(sections: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        for i, body in enumerate(sections):
            z.writestr(f"Contents/section{i}.xml", f'<hs:sec xmlns:hp="x" xmlns:hs="y">{body}</hs:sec>')
    return buf.getvalue()


def test_sniff_by_bytes_not_extension():
    assert sniff(b"%PDF-1.7 ...") == "pdf"
    assert sniff(b"<!DOCTYPE html><html>error</html>") == "html"
    assert sniff(_hwpx(["<hp:p><hp:run><hp:t>x</hp:t></hp:run></hp:p>"])) == "hwpx"


def test_hwpx_paragraphs_and_entities():
    data = _hwpx(['<hp:p><hp:run><hp:t>생성형 AI 활용 </hp:t></hp:run><hp:run><hp:t>금지 &amp; 실격</hp:t></hp:run></hp:p>'
                  '<hp:p><hp:run><hp:t>둘째 문단</hp:t></hp:run></hp:p>'])
    assert hwpx_pages(data) == ["생성형 AI 활용 금지 & 실격\n둘째 문단"]
    assert extract(data)[0] == "hwpx"


def test_hwp_inline_control_is_skipped_whole():
    # a table control (extended, 8 code units) sits in front of the text
    ctrl = struct.pack("<8H", 11, 0x7462, 0x6C20, 0, 0, 0, 0, 11)
    text = "참가비 없음".encode("utf-16-le")
    assert _hwp_para_text(ctrl + text + struct.pack("<H", 13)) == "참가비 없음\n"


def test_sam_box_tokens_parse():
    out = "<0f>0<|box;x1=612;y1=1205;x2=733;y2=1324;w=1440;h=2092|><|mask;x=0;y=0;data=1,2,~!!|>"
    assert [tuple(map(int, m.groups())) for m in BOX.finditer(out)] == [(612, 1205, 733, 1324, 1440, 2092)]


def _png(w, h, color):
    im = Image.new("RGB", (w, h), "white")
    im.paste(color, (0, 0, w // 2, h // 2))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_logo_is_not_a_poster():
    assert "skip" in source_from_bytes(_png(120, 40, "black"), "u", "att1")
    assert "source" in source_from_bytes(_png(800, 1100, "black"), "u", "att1")


def test_thumbnail_matches_full_poster():
    assert _same_image(image_key(_png(1440, 2092, "navy")), image_key(_png(800, 1162, "navy")))
