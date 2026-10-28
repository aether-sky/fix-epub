"""Builds small epubs shaped like the PDF conversions this skill cleans up."""
import zipfile

CONTAINER = ('<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
             "</rootfiles></container>")
PROSE = ("The ship had been drifting for three days before anyone thought to look at the stars again, and")
PROSE_REST = "when they did, the constellations were not the ones they had left behind."


def chapter(body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">\n<head>'
            '<title>x</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body class="calibre">\n{body}\n</body>\n</html>\n')


def write_epub(path, chapters, css, version="2.0", toc=(("Start", "cover.xhtml"),), nav=False, ncx=True):
    """chapters: [(file name, body html)]; toc: [(label, file)] for the existing table of contents."""
    items = ['<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
             '<item id="css" href="style.css" media-type="text/css"/>']
    items += [f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>' for i, (name, _) in
              enumerate(chapters)]
    if ncx:
        items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    if nav:
        items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    spine = '<itemref idref="cover"/>' + "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    spine_toc = ' toc="ncx"' if ncx else ""
    opf = (f'<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" '
           f'version="{version}" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:title>Test Book</dc:title><dc:identifier id="uid">test-book</dc:identifier></metadata>'
           f'<manifest>{"".join(items)}</manifest><spine{spine_toc}>{spine}</spine></package>')
    points = "".join(f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{label}</text></navLabel>'
                     f'<content src="{target}"/></navPoint>' for i, (label, target) in enumerate(toc, 1))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/style.css", css)
        z.writestr("OEBPS/cover.xhtml", chapter("<p>Cover</p>"))
        for name, body in chapters:
            z.writestr(f"OEBPS/{name}", chapter(body))
        if ncx:
            z.writestr("OEBPS/toc.ncx", '<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/'
                                        'z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:depth" '
                                        f'content="1"/></head><docTitle><text>Test Book</text></docTitle><navMap>'
                                        f"{points}</navMap></ncx>")
        if nav:
            links = "".join(f'<li><a href="{target}">{label}</a></li>' for label, target in toc)
            z.writestr("OEBPS/nav.xhtml", '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/'
                                          '1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Nav'
                                          '</title></head><body><nav epub:type="toc" id="toc"><h1>Contents</h1>'
                                          f"<ol>{links}</ol></nav></body></html>")


def broken_chapters(count=3):
    """Chapters whose paragraphs a PDF conversion split mid-sentence, each opening with a plain-text title."""
    chapters = []
    for n in range(1, count + 1):
        body = [f'<p class="calibre1">Chapter {n}</p>']
        for k in range(6):
            body.append(f'<p class="calibre1">{PROSE}</p>')
            body.append(f'<p class="calibre1"><a id="p{n}{k}"></a>{PROSE_REST}</p>')
        chapters.append((f"chapter{n}.xhtml", "\n".join(body)))
    return chapters
