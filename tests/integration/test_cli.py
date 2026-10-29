"""End to end on generated books: fix keeps the original as "(original)", rejoins split paragraphs, makes the
plan's headings, rebuilds a one-entry table of contents, and writes nothing when the plan is wrong."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fix_epub.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from epubs import PROSE, PROSE_REST, broken_chapters, write_epub

CSS = ".calibre1 {\n    display: block;\n    margin: 1em 0;\n    line-height: 1.28em\n    }\n"


def run(*args):
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                            encoding="utf-8")
    return result


class FixTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.book = self.dir / "Some Book.epub"
        self.backup = self.dir / "Some Book (original).epub"
        self.plan = self.dir / "plan.json"
        self.plan.write_text(json.dumps({"headings": ["Chapter \\d+"]}), encoding="utf-8")

    def tearDown(self):
        for path in sorted(self.dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.dir.rmdir()

    def fixed(self, name):
        with zipfile.ZipFile(self.book) as z:
            return z.read(name).decode("utf-8")

    def assertClean(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def test_fixes_a_pdf_conversion_and_keeps_the_original_beside_it(self):
        write_epub(self.book, broken_chapters(), CSS)
        original = self.book.read_bytes()
        self.assertClean(run("fix", self.book, "--plan", self.plan))
        self.assertEqual(self.backup.read_bytes(), original)
        chapter = self.fixed("OEBPS/chapter2.xhtml")
        self.assertIn('<h2 id="heading-2">Chapter 2</h2>', chapter)
        self.assertIn(f'{PROSE} <a id="p20"></a>{PROSE_REST}', chapter)
        css = self.fixed("OEBPS/style.css")
        self.assertNotIn("line-height", css)
        self.assertIn("margin: 0", css)
        self.assertIn("text-indent: 1.5em", css)
        self.assertIn("text-align: justify", css)
        ncx = self.fixed("OEBPS/toc.ncx")
        self.assertEqual(ncx.count("<navPoint"), 4)
        self.assertIn('<content src="chapter3.xhtml#heading-3"/>', ncx)
        with zipfile.ZipFile(self.book) as z:
            first = z.infolist()[0]
            self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))

    def test_rerunning_rebuilds_from_the_original(self):
        write_epub(self.book, broken_chapters(), CSS)
        original = self.book.read_bytes()
        self.assertClean(run("fix", self.book))
        self.assertClean(run("fix", self.book, "--plan", self.plan))
        self.assertEqual(self.backup.read_bytes(), original)
        self.assertIn("<h2", self.fixed("OEBPS/chapter1.xhtml"))

    def test_rebuilds_an_epub3_nav(self):
        write_epub(self.book, broken_chapters(), CSS, version="3.0", nav=True, ncx=False)
        self.assertClean(run("fix", self.book, "--plan", self.plan))
        nav = self.fixed("OEBPS/nav.xhtml")
        self.assertIn('<a href="chapter1.xhtml#heading-1">Chapter 1</a>', nav)
        self.assertIn("<h1>Contents</h1>", nav)

    def test_leaves_deliberate_paragraph_breaks_alone(self):
        verse = "\n".join(f'<p class="calibre1">{PROSE}</p>\n<p class="calibre1">So sang the {n} lark.</p>'
                          for n in range(10))
        write_epub(self.book, [("poem.xhtml", verse)], CSS)
        self.assertClean(run("fix", self.book))
        self.assertEqual(self.fixed("OEBPS/poem.xhtml").count("<p"), 20)

    def test_only_line_height_changes_nothing_but_the_stylesheet(self):
        write_epub(self.book, broken_chapters(), CSS + "span.sup { vertical-align: super; line-height: 0.5em }\n")
        self.assertClean(run("fix", self.book, "--only", "line-height"))
        with zipfile.ZipFile(self.book) as fixed, zipfile.ZipFile(self.backup) as original:
            changed = [n for n in original.namelist() if fixed.read(n) != original.read(n)]
        self.assertEqual(changed, ["OEBPS/style.css"])
        css = self.fixed("OEBPS/style.css")
        self.assertIn("margin: 1em 0", css)
        self.assertEqual(css.count("line-height"), 1)

    def test_page_markers_glued_to_lines_are_cut_in_one_pass_and_the_halves_rejoined(self):
        chapters = []
        for n in range(1, 4):
            body = [f"<h2>Chapter {n}</h2>"]
            for k in range(6):
                body += [f'<p class="calibre1">{PROSE} <b class="calibre3">Page {n}{k}</b></p>',
                         '<p class="calibre1"/>', f'<p class="calibre1">{PROSE_REST}</p>']
            chapters.append((f"chapter{n}.xhtml", "\n".join(body)))
        write_epub(self.book, chapters, CSS)
        self.plan.write_text(json.dumps({"remove": [r'\s*<b class="calibre3">Page \d+</b>'], "drop": [""]}),
                             encoding="utf-8")
        self.assertClean(run("fix", self.book, "--plan", self.plan))
        chapter = self.fixed("OEBPS/chapter1.xhtml")
        self.assertEqual(chapter.count(f"{PROSE} {PROSE_REST}"), 6)
        self.assertNotIn("Page", chapter)

    def test_dry_run_writes_nothing(self):
        write_epub(self.book, broken_chapters(), CSS)
        original = self.book.read_bytes()
        result = run("fix", self.book, "--plan", self.plan, "--dry-run")
        self.assertClean(result)
        self.assertIn("3 headings made", " ".join(result.stdout.split()))
        self.assertEqual(self.book.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_a_replacement_that_matches_nothing_stops_before_writing(self):
        write_epub(self.book, broken_chapters(), CSS)
        original = self.book.read_bytes()
        self.plan.write_text(json.dumps({"replace": [["cbuld", "could"]]}), encoding="utf-8")
        result = run("fix", self.book, "--plan", self.plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cbuld", result.stderr)
        self.assertEqual(self.book.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_inspect_reports_the_damage(self):
        write_epub(self.book, broken_chapters(), CSS)
        result = run("inspect", self.book)
        self.assertClean(result)
        self.assertIn("18 of", result.stdout)
        self.assertIn("'Chapter 1'", result.stdout)

    def test_refuses_a_file_that_is_not_an_epub(self):
        self.book.write_bytes(b"not a zip")
        result = run("inspect", self.book)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a zip archive", result.stderr)


if __name__ == "__main__":
    unittest.main()
