---
name: fix-epub
description: Clean up a badly formatted epub - big gaps or double spacing between paragraphs, line spacing that ignores the reader's setting, ragged-right text that should be justified, paragraphs and lines broken mid-sentence (typical of PDF and OCR conversions), words split by stray hyphens, page numbers and running headers in the text, chapter or story titles left as plain text, and a missing or one-entry table of contents. Use whenever the user points at an .epub (often in Downloads) and says its spacing, margins, line breaks, chapters, TOC or general formatting is off, or asks to fix, clean up or tidy a book or ebook, even if they don't say "epub".
---

# Fix epub

The script is `scripts/fix_epub.py` in this skill's folder; call it by its full path. It reads the epub
straight from its zip, never extracting it. Python 3.11 or later, standard library only. Books from the
internet are untrusted input, so keep `-I`.

```powershell
python -I "<skill folder>\scripts\fix_epub.py" inspect "<book.epub>"
python -I "<skill folder>\scripts\fix_epub.py" fix "<book.epub>" [--plan plan.json] [--only ... | --skip ...] [--dry-run] [-o out.epub]
```

Redirect the output to a file in your scratchpad and read the file: reports run to hundreds of lines, and
piping through `Select-Object -First` or `head` cuts the log off.

## Which fixes the user asked for

| The user says | Fix |
|---|---|
| line spacing, lines too tight or loose, ignores my reader's setting | `line-height` |
| double spacing, gaps or blank lines between paragraphs, extra margins | `spacing` |
| ragged right edge, not justified, justify the text | `justify` |
| random line breaks, paragraphs or sentences cut in half | `rejoin` and `words` |
| words split with hyphens ("me- chanical") | `words` |
| no chapters, chapter titles are plain text, no or broken table of contents | `toc`, plus `headings` in the plan |
| clean it up, fix the formatting | everything `inspect` shows broken |

Pass `--only line-height` (comma-separated for several) when the user asked for one thing; `--skip`
leaves out named fixes instead. A plan is only needed for headings, junk lines and the other plan keys
below; skip it otherwise.

## Workflow

1. **Inspect** and read the whole report: the paragraph classes and the CSS that styles them, line-height
   rules, empty paragraphs and `<br>`s, existing headings and table of contents, what `fix` would do with no
   plan (with sample joins and the breaks it left alone), and lists of possible chapter titles, repeated
   short lines and number-only paragraphs.
2. **Plan** what needs judgment: chapter titles, junk lines, glued-on page markers. Write the plan with the
   Write tool into your scratchpad, not the user's folder.
3. **Dry run**: `fix --dry-run` with the same flags and plan. Review the log (see below), adjust, repeat.
4. **Fix**: the same command without `--dry-run`. The original is renamed `<name> (original).epub` and the
   fixed book takes the original name, which is how the user keeps their books. Running `fix` again rebuilds
   from the `(original)` file, so the plan can be changed and rerun; passing the `(original)` file works too.
5. **Report** to the user as a numbered list: what changed with counts, what was left alone and why
   (including breaks it couldn't be sure of), and where both files are. Say that nothing was opened in a
   reader app, and that a library app (Calibre, Kobo, Kindle) holding its own copy needs the book re-imported.

## What fix does by itself

- **spacing**: sets the top and bottom margins of the CSS rules for body paragraphs to 0 and adds a 1.5em
  first-line indent when no indent exists. A class counts as body text on 10% or more of paragraphs. Side
  margins, other classes (epigraphs, block quotes) and rules like `h1 + p` or `p.break + p` are untouched.
  Empty paragraphs are removed only when they sit between most paragraphs; a few are scene breaks.
- **line-height**: removes `line-height` from every rule for blocks of text, so the reader app's setting
  works. Rules for inline text (`span`, `sup`, anything with `vertical-align`) keep theirs: a small value there
  stops a superscript from stretching its line.
- **justify**: justifies body paragraphs. `text-align: left` or `start` in their CSS rules becomes `justify`,
  `justify` is added when no rule for body text sets an alignment, `text-justify: none` goes, and inline
  `style="text-align: left"` or `align="left"` on body paragraphs is removed because it overrides the
  stylesheet. Centred and right-aligned text (titles, signatures, datelines) and other classes stay as they
  are. Hard line breaks also stop lines from justifying; **rejoin** handles those.
- **rejoin**: merges a paragraph, or a `<br>` line, into the one before it when it continues that sentence.
  Well-made books break paragraphs on purpose (verse, addresses, index entries, chat logs, footnotes), so it
  runs only in files that show conversion damage: 1% of paragraph breaks, and 10 in the book, falling after
  60+ characters of prose mid-sentence before a lowercase word. The `info:` line gives the count and which
  files it runs in. It joins when the paragraph so far is 60+ characters and ends in a word, dash, hyphen,
  comma or semicolon before a lowercase word; before a capital, quote or digit only after a word like "the",
  "of" or "his", inside a line of capitals, after `Dr.`, or after a comma handing over to a quotation. Never
  across different classes, an image, rule or heading, after bold or a note marker, before a note link,
  between two `label: text` lines (glossaries, chat logs), or after a line that is mostly numbers. Long
  lines it left ending in a word before a capital are logged as breaks it couldn't be sure of.
- **words**: rejoins words a line end split (`me- chanical`, or `dis-` ending one paragraph and `turbing`
  starting the next). Whichever of `disturbing` and `dis-turbing` the book uses more elsewhere wins. When it
  uses neither, a hyphen before a small word (`than- the`, `nineteenth- and`) is a dash and stays, two parts
  that are both words elsewhere make a compound (`competent-looking`), and anything else is a syllable break
  (`mechanical`). Both kinds show in the log under split words.
- **toc**: rebuilds the NCX and the EPUB 3 nav from the book's headings when the current table of contents is
  broken: under 3 entries, or linking under half the files with chapter headings. It uses the top two heading
  levels (skipping a lone top-level book title), nests the second under the first, and keeps old entries for
  files without headings (cover, title page). A working publisher TOC is kept unless the plan says `"toc": true`.

Every run checks before writing: each paragraph's text must match the original paragraphs merged into it,
apart from joins, drops, removals and replacements; ids must survive; XML must still parse; no link may break
that worked before. A failure prints the problems and writes nothing. Don't work around it: it means a bug or
a bad plan, so tell the user what it said.

## The plan

JSON. Patterns are Python regexes (JSON-escaped: `\\d`). Rules for `headings` and `drop` match a paragraph's
or heading's whole text, tags removed and whitespace collapsed. The steps run in this order: `remove`,
`drop`, empty spacers, `headings`, joins, split words, `replace`.

```json
{
  "headings": ["Chapter [IVXLC\\d]+", {"match": "PART (?:ONE|TWO)", "level": 1, "nth": [2, 4]}],
  "heading_after": "[A-Z][a-z]+(?: [A-Z][a-z]+)*,? \\d{4}",
  "remove": ["\\s*<span class=\"pagenum\">\\d+</span>", "\\s*THE LOST WORLD\\s*$"],
  "drop": ["\\d{1,3}", "THE LOST WORLD", ""],
  "keep_breaks": ["^Tyger Tyger"],
  "replace": [["cbuld", "could"]],
  "skip_files": ["OEBPS/Text/poems.xhtml"],
  "toc": true,
  "rejoin": true,
  "join_capitals": true
}
```

- `headings`: a paragraph that matches becomes an `h2`, or the given `level`, styled like the book's existing
  headings of that level, with bold tags dropped. An existing heading that matches gets that level. `nth`
  picks which matches count (1-based, in reading order), for titles that also appear in a contents page.
- `heading_after`: the block right after one whose whole text matches becomes an `h2`, as in story
  collections where each story ends with a place and date and the next title follows.
- `remove`: regexes cut out of every paragraph's and heading's HTML before anything else, for page markers
  and running headers glued onto text (`... the <b>Page 5</b>`). A paragraph left empty is dropped. Use this
  rather than `replace` for junk, because a glued-on marker blocks the join that `remove` frees.
- `drop`: paragraphs and headings whose whole text matches are removed: page numbers, running headers,
  stray OCR letters. `""` drops empty paragraphs left at page breaks. Paragraphs holding an image are never
  dropped. Rules take `nth` like headings.
- `keep_breaks`: a paragraph whose text contains a match is never joined to its neighbours, nor its lines.
- `replace`: exact `[old, new]` replacements in paragraphs' and headings' HTML, made last, after joins. A
  page anchor (`<a id="p17"></a>`) may sit inside a word joined across pages; copy it into `old` and `new`.
  One that matches nothing is an error.
- `skip_files`: content files to leave alone entirely (a poetry section, an appendix of tables).
- `toc`: `true` always rebuilds the table of contents, `false` never does.
- `rejoin`: `true` rejoins in every file, even where the breaks look deliberate. Use it when the user says
  lines are broken but the `info:` line says it isn't rejoining somewhere.
- `join_capitals`: `true` also joins a long line ending in any word to a line starting with a capital. Use
  it when the breaks it couldn't be sure of are nearly all mid-sentence ("the post of Deputy" + "Director"),
  with `keep_breaks` for the exceptions.

### Finding chapter titles

Look for the pattern in "Possible chapter titles": "Chapter 12", roman numerals, a class used only on titles,
bold, all caps, or a line that always follows something, like the dateline ending each story. Keep patterns
tight, anchored to the chapter word or the exact titles, because front matter repeats the book's title and
author. If the titles share no pattern, list them exactly, escaped (`"Who's There\\?"`). The candidate list
leaves out lines ending in a quote and lines that repeat, so count the headings made against the book's real
chapters. A title split over two paragraphs ("CHAPTER ONE" then "The Beginning"): make the first the
heading and leave the second as text.

### Junk lines

Number-only paragraphs rising by one are page numbers; short lines repeating every few pages are running
headers; a lone letter between a dateline and a title is OCR debris. Drop them with patterns tight enough to
spare real text (`\\d{1,3}` matches only whole-paragraph numbers, never "1947" inside a sentence). Ask the
user before dropping anything that might be content.

OCR misspellings ("cbuld") are not fixed unasked. Mention notable ones in the report and fix them with
`replace` if the user wants.

## Reviewing the dry run

- **Rejoined before a capital, quote or digit**: read every one. Mid-sentence breaks ("on the far side of
  the" + "Moon?") are right. A running header caught mid-sentence ("The enormous load of" + "The Mysterious
  Island") needs a `remove` or `drop` rule; a title, signature or verse line needs a heading rule or
  `keep_breaks`.
- **Breaks it couldn't be sure of**: if nearly all are mid-sentence, set `join_capitals`; otherwise report
  them.
- **Split words**: check the ones joined across paragraphs; a foreign or rare compound can lose its hyphen
  ("L'Apres-midi"); put it back with `replace`.
- **Headings made**: the count should match the chapters or stories, with none from the front matter.
- **Table of contents**: entries in reading order, parts above their chapters.
- **Warnings**: a plan rule that matched nothing is usually a typo in the pattern.
- A number stuck in a joined sentence ("with 4" + "great emphasis") can be a printed line number; tell the
  user rather than guess which numbers are text.

## When it doesn't fit

- `inspect` reports very few paragraphs for a long book: the text is in `<div>`s or bare text, which this
  script doesn't touch. Tell the user, and offer a one-off conversion only if they want it.
- "DRM-protected" or "not an epub": stop and say so.
- A file that isn't UTF-8 is left alone with a warning; say which.
- Image-only books (manga, scans) have nothing for this script to fix.

## Tests

```powershell
python -m unittest discover -s tests\unit
python -m unittest discover -s tests\integration
```
