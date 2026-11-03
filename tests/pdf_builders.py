"""Builds tiny valid PDFs for the M2 error-path tests, so they need neither the supplied packs nor any extra
dependency. Each page spec is a string (its lines of text; "" gives a genuinely blank page) or IMAGE_ONLY (a
page whose only content is an image, i.e. what a scanned page looks like to a text extractor)."""

IMAGE_ONLY = object()


def _escape(line: str) -> bytes:
    return line.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_pdf(pages: list) -> bytes:
    """Return the bytes of a PDF with one page per spec, with a correct xref table."""
    objects: dict[int, bytes] = {}
    font_num = 3
    objects[font_num] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    next_num = 4
    page_nums: list[int] = []

    for spec in pages:
        page_num, content_num = next_num, next_num + 1
        next_num += 2
        if spec is IMAGE_ONLY:
            image_num = next_num
            next_num += 1
            objects[image_num] = (
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                b"/BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream"
            )
            stream = b"q 200 0 0 200 100 500 cm /Im0 Do Q"
            resources = b"<< /XObject << /Im0 %d 0 R >> >>" % image_num
        else:
            lines = spec.split("\n") if spec else []
            stream = b""
            if lines:
                stream = b"BT /F1 12 Tf 72 720 Td 14 TL " + b" T* ".join(b"(" + _escape(x) + b") Tj" for x in lines) + b" ET"
            resources = b"<< /Font << /F1 %d 0 R >> >>" % font_num
        objects[content_num] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objects[page_num] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources " % content_num
            + resources + b" >>"
        )
        page_nums.append(page_num)

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % n for n in page_nums)
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_nums)

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at)
    return bytes(out)
