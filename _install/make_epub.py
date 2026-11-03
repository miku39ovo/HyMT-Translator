# -*- coding: utf-8 -*-
"""生成一个结构规范的中英混排 EPUB 样张，用于测试与演示。"""
import zipfile
from pathlib import Path

OUT = Path(r"D:\HyMT-Translator\示例文件\sample_book.epub")

MIMETYPE = "application/epub+zip"

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>本地翻译手册</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="bookid">urn:uuid:demo-0001</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:demo-0001"/></head>
  <docTitle><text>本地翻译手册</text></docTitle>
  <navMap>
    <navPoint id="n1" playOrder="1"><navLabel><text>Chapter 1</text></navLabel><content src="text/ch1.xhtml"/></navPoint>
    <navPoint id="n2" playOrder="2"><navLabel><text>Chapter 2</text></navLabel><content src="text/ch2.xhtml"/></navPoint>
  </navMap>
</ncx>
"""

CSS = """body { font-family: serif; line-height: 1.6; }
h1 { font-size: 1.4em; }
p { text-indent: 2em; margin: 0 0 .6em; }
pre { background: #f4f4f4; padding: 8px; }
"""

CH1 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 1</title>
  <link rel="stylesheet" type="text/css" href="../style.css"/>
</head>
<body>
  <h1>Chapter 1: Why Run a Model Locally</h1>
  <p>Running a translation model on your own machine keeps every document inside your computer.</p>
  <p>Nothing is uploaded, so <i>confidential contracts</i> and unpublished papers stay private.</p>
  <p>This paragraph has <b>bold text</b> in the middle, and a <a href="ch2.xhtml">link to the next chapter</a> as well.</p>
  <blockquote>Local inference trades a little speed for a lot of privacy.</blockquote>
  <pre><code>llama-server -m model.gguf -ngl 99</code></pre>
  <ul>
    <li>No network access required</li>
    <li>No per-character billing</li>
  </ul>
</body>
</html>
"""

CH2 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 2</title>
  <link rel="stylesheet" type="text/css" href="../style.css"/>
</head>
<body>
  <h2>Chapter 2: Handling Long Documents</h2>
  <p>Long papers are split into chunks before translation, and each chunk reuses the previous one as context.</p>
  <p>1200 characters per chunk keeps the model focused while still preserving coherence across paragraphs.</p>
  <table>
    <tr><th>Format</th><th>Note</th></tr>
    <tr><td>EPUB</td><td>Chapters are rewritten in place.</td></tr>
    <tr><td>SRT</td><td>Time codes are preserved.</td></tr>
  </table>
</body>
</html>
"""


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w") as z:
        zi = zipfile.ZipInfo("mimetype")
        z.writestr(zi, MIMETYPE, compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", OPF, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", NCX, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/text/ch1.xhtml", CH1, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/text/ch2.xhtml", CH2, zipfile.ZIP_DEFLATED)
    print("已生成:", OUT, OUT.stat().st_size, "字节")


if __name__ == "__main__":
    main()
