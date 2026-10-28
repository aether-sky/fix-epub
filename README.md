# fix-epub

A Claude Code skill that cleans up badly formatted epubs: the double spacing between paragraphs, line
spacing the reader app can't change, ragged-right text, paragraphs and words broken mid-sentence by a PDF or
OCR conversion, chapter titles that are just plain text, and a table of contents with one entry or none.

## Install

1. Copy this folder to `~/.claude/skills/fix-epub` (Claude Code picks it up next session).
2. Python 3.11 or later. No packages to install.

## Use

Ask Claude something like "the epub in Downloads has huge gaps between paragraphs and no chapters, fix it",
or run it yourself:

    python -I scripts/fix_epub.py inspect "Some Book.epub"
    python -I scripts/fix_epub.py fix "Some Book.epub" --plan plan.json

The original is kept as `Some Book (original).epub` and the fixed book takes its name. Running `fix` again
starts over from the original, so a plan can be adjusted and rerun.

## What it changes

- Paragraph gaps: the top and bottom margins of body-text paragraphs go to zero, and a first-line indent
  is added when the book has none.
- Line spacing: `line-height` is removed from blocks of text so the reader app's own setting applies;
  superscripts and other inline text keep theirs.
- Justified text: body paragraphs are justified, and left alignment set in their CSS or inline on them,
  which would override that, is removed. Centred titles stay centred.
- Broken paragraphs: only in books that show PDF-conversion damage, paragraphs and lines that stop
  mid-sentence are joined to the next. Verse, addresses, index entries, footnotes and chat logs in
  well-made books are left alone.
- Split words: `me- chanical` becomes `mechanical`, checked against how the rest of the book spells it.
- Chapters and contents: lines the plan names become headings, and a broken table of contents is rebuilt
  from the headings.

The plan (chapter titles, junk lines to drop, page markers glued onto text, exact replacements) is the
part that needs judgment; `inspect` lists the likely chapter titles, repeated running headers and page
numbers to base it on. See SKILL.md for its format.

Before writing, every run checks that no text was lost or changed beyond the planned fixes, that every id
and link still resolves, and that the files still parse. If any check fails, nothing is written.

## What it reads and writes

Reads the epub. Writes the fixed epub and renames the original. Nothing goes over the network.

## Tests

    python -m unittest discover -s tests/unit
    python -m unittest discover -s tests/integration
