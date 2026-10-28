"""Inspect and fix an epub's formatting: gaps between paragraphs, forced line spacing, unjustified text,
paragraphs and words broken by a PDF conversion, chapter titles left as plain text, and a missing or incomplete
table of contents."""
import argparse
import html
import itertools
import json
import os
import posixpath
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
XHTML_TYPES = {"application/xhtml+xml", "text/html"}
NCX_TYPE = "application/x-dtbncx+xml"
FIXES = ("spacing", "line-height", "justify", "rejoin", "words", "toc")
ORIGINAL_SUFFIX = " (original)"

INDENT = "1.5em"
MIN_PROSE_LEN = 60       # text this long that stops mid-sentence is prose, not a title, dateline or verse line
BROKEN_MIN = 10          # mid-sentence paragraph breaks a book needs before any count as conversion damage
BROKEN_SHARE = 0.01      # share of a file's paragraph breaks that must fall mid-sentence for it to be rejoined
MAX_HEADING_LEN = 120
BODY_CLASS_SHARE = 0.1   # a class on at least this share of paragraphs styles body text
SPACER_SHARE = 0.2       # empty paragraphs this common separate paragraphs rather than mark scene breaks

BLOCK = re.compile(r"<(?P<tag>p|h[1-6])\b(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</(?P=tag)\s*>)", re.S)
BODY = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.S | re.I)
HEAD = re.compile(r"<head\b.*?</head\s*>", re.S | re.I)
HEADING = re.compile(r"<h(?P<level>[1-6])\b(?P<attrs>[^>]*)>(?P<inner>.*?)</h(?P=level)\s*>", re.S)
TAG = re.compile(r"<[^>]+>")
TAG_SPLIT = re.compile(r"(<[^>]+>)")
CLASS_ATTR = re.compile(r"""\sclass\s*=\s*(["'])(.*?)\1""", re.S)
ID_ATTR = re.compile(r"""(?<![\w:-])id\s*=\s*(["'])(.*?)\1""")
TARGET_ATTR = re.compile(r"""(?<![\w:-])(?:id|name)\s*=\s*(["'])(.*?)\1""")
LINK_ATTR = re.compile(r"""(?<![\w:-])(?:href|src)\s*=\s*(["'])(.*?)\1""")
STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.S)
STYLESHEET_LINK = re.compile(r"""<link\b[^>]*href\s*=\s*["']([^"']+\.css)["']""", re.I)
CSS_RULE = re.compile(r"(?P<selector>[^{}]+)\{(?P<body>[^{}]*)\}")
CSS_DECL = re.compile(r"(\s*)([\w-]+)(\s*:\s*)(.*?)(\s*)$", re.S)
COMBINATOR = re.compile(r"\s*[>+~]\s*|\s+")
SIMPLE_SELECTOR = re.compile(r"([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)")
ZERO = re.compile(r"-?0*\.?0+(?:[a-z%]+)?|auto", re.I)
ENDS_BOLD = re.compile(r"</(?:b|strong)>\s*$")
NOTE_END = re.compile(r"(?:</sup>|<a\b[^>]*\bhref[^>]*>[^<]{0,8}</a>)\s*$")
NOTE_START = re.compile(r"\s*(?:<a\b(?![^>]*\bhref)[^>]*>\s*</a>\s*)*<(?:sup\b|a\b[^>]*\bhref)")
BOLD_TAG = re.compile(r"</?(?:b|strong)\b[^>]*>")
BR = re.compile(r"(\s*<br\b[^>]*>\s*)")
LEFT_ALIGN = re.compile(r"(?:left|start|auto|initial|inherit|unset)\s*(?:!important)?", re.I)
STYLE_ATTR = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.S)
INLINE_TEXT_ALIGN_LEFT = re.compile(r"text-align\s*:\s*(?:left|start)\s*(?:!important\s*)?;?\s*", re.I)
INLINE_ALIGN_LEFT = re.compile(r"""\salign\s*=\s*["']left["']""", re.I)
INLINE_TAGS = {"span", "sup", "sub", "a", "i", "em", "b", "strong", "small", "big", "abbr", "cite", "code"}
MEDIA = re.compile(r"<(?:img|image|svg|hr|object|video|audio)\b", re.I)
GAP_MARKUP = re.compile(r"\s+|<!--.*?-->|</?div\b[^>]*>|<a\b[^>]*/>|<a\b[^>]*>\s*</a>|<span\b[^>]*/>"
                        r"|<span\b[^>]*>\s*</span>", re.S)
SPLIT_WORD = re.compile(r"([^\W\d_]+)-[ \t\r\n]+([^\W\d_]+)")
LAST_WORD = re.compile(r"[^\W\d_]+$")
FIRST_WORD = re.compile(r"[^\W\d_]+")
WORD = re.compile(r"[^\W\d_]+")
COMPOUND = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+")
ABBREVIATION = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|Prof|Capt|Col|Gen|Lt|Sgt|Mt|Rev)\.$")
LABEL = re.compile(r"[^\s:]{1,25}(?: [^\s:]{1,25}){0,3}: ")  # "Sam: are you awake", "becoming: bhava"
CONTINUING_WORDS = set("a an the of to in on at for with by from into onto upon about and or but nor as than that "
                       "which whose his her its their our my your this these those is was were are be been had has "
                       "have not very so if when while where".split())
DASH_FOLLOWERS = CONTINUING_WORDS | set("i he she it we they you me him them us".split())
SUSPENDED = {"and", "or", "to", "nor"}  # "nineteenth- and twentieth-century" keeps its hyphen and space
DASHES = ("--", "—", "–", "―")
HYPHENS = "-‐"
SOFT_HYPHEN = "\xad"
TRAILING_CHAR = {"\xad": r"(?:\xad|&shy;|&#173;|&#x[aA][dD];)", "-": "-", "‐": "(?:‐|&#8208;)"}
OPEN_QUOTES = "\"'“‘«"
CLOSE_QUOTES = "\"'”’»"
CHAPTER_WORDS = re.compile(r"(?i)^(?:chapter|part|book|prologue|epilogue|interlude|afterword|foreword|preface|"
                           r"introduction|appendix)\b")
ROMAN = re.compile(r"[IVXLC]+\.?")
NUMBER = re.compile(r"\d+\.?")


def plain(fragment):
    return " ".join(html.unescape(TAG.sub("", fragment or "")).split())


def normalized(fragment):
    """Text with whitespace and hyphens removed, for checking that no words were lost or garbled."""
    return re.sub(r"[\s\-‐\xad]", "", html.unescape(TAG.sub("", fragment)))


def body_of(document):
    m = BODY.search(document)
    return m.group(1) if m else document


def visible_text(fragment):
    return normalized(HEAD.sub("", fragment))


def decode_text(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def clean_selector(selector):
    """The selector without comments or an @charset/@import statement in front of it."""
    return re.sub(r"/\*.*?\*/", "", selector, flags=re.S).rsplit(";", 1)[-1].strip()


def resolve(base_file, href):
    """Archive path an href in base_file points at, with the fragment split off."""
    path, _, fragment = href.partition("#")
    if not path:
        return base_file, fragment
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), unquote(path))), fragment


def relative_href(target, from_file):
    path, _, fragment = target.partition("#")
    rel = posixpath.relpath(path, posixpath.dirname(from_file) or ".")
    return quote(rel, safe="/-._~") + (f"#{fragment}" if fragment else "")


@dataclass
class Book:
    infos: list
    data: dict
    opf_path: str
    version: str
    title: str
    spine: list
    stylesheets: list
    ncx: str | None
    nav: str | None


def load(path):
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            data = {i.filename: z.read(i) for i in infos}
    except zipfile.BadZipFile:
        sys.exit(f"{path} is not an epub: it is not a zip archive (a broken or partial download?)")
    if "META-INF/container.xml" not in data:
        sys.exit(f"{path} is not an epub: it has no META-INF/container.xml")
    encrypted = re.findall(r'URI="([^"]+)"', data.get("META-INF/encryption.xml", b"").decode("utf-8", "replace"))
    if any(not uri.lower().endswith((".otf", ".ttf", ".woff", ".woff2")) for uri in encrypted):
        sys.exit(f"{path} is DRM-protected: META-INF/encryption.xml encrypts its text")
    container = ET.fromstring(data["META-INF/container.xml"])
    opf_path = container.find(f".//{{{CONTAINER_NS}}}rootfile").get("full-path")
    opf = ET.fromstring(data[opf_path])
    items = {}
    for item in opf.iter(f"{{{OPF_NS}}}item"):
        items[item.get("id")] = (resolve(opf_path, item.get("href"))[0], item.get("media-type", ""),
                                 (item.get("properties") or "").split())
    spine_el = opf.find(f"{{{OPF_NS}}}spine")
    spine = [items[ref.get("idref")][0] for ref in spine_el.iter(f"{{{OPF_NS}}}itemref")
             if ref.get("idref") in items and items[ref.get("idref")][1] in XHTML_TYPES]
    ncx = items.get(spine_el.get("toc"), (None,))[0] or next(
        (p for p, t, _ in items.values() if t == NCX_TYPE), None)
    nav = next((p for p, _, props in items.values() if "nav" in props), None)
    title_el = opf.find(f".//{{{DC_NS}}}title")
    return Book(infos, data, opf_path, opf.get("version", "2.0"),
                (title_el.text or "").strip() if title_el is not None else "", spine,
                [p for p, t, _ in items.values() if t == "text/css" and p in data],
                ncx if ncx in data else None, nav if nav in data else None)


@dataclass
class Para:
    """A paragraph or heading."""
    attrs: str
    inner: str
    tag: str
    state: str = "live"  # "dropped" (by a plan rule or as a spacer) or "merged" (into the paragraph before it)
    parts: list = field(default_factory=list)  # the original inner markup of every paragraph merged into this one
    replaced: list = field(default_factory=list)  # the plan's (old, new) replacements made in this one

    def __post_init__(self):
        self.parts = self.parts or [self.inner]

    @property
    def text(self):
        return plain(self.inner)

    @property
    def classes(self):
        m = CLASS_ATTR.search(self.attrs)
        return tuple(sorted(m.group(2).split())) if m else ()

    def anchors(self, include_inner):
        ids = [m.group(2) for m in ID_ATTR.finditer(self.attrs + (self.inner if include_inner else ""))]
        return "".join(f'<a id="{i}"></a>' for i in ids)

    def render(self):
        if self.state == "merged":
            return ""
        if self.state == "dropped":
            return self.anchors(include_inner=True)
        return f"<{self.tag}{self.attrs}>{self.inner}</{self.tag}>"


@dataclass
class Doc:
    name: str
    source: str
    pieces: list  # text between blocks alternating with Para: str, Para, str, ..., Para, str

    @classmethod
    def parse(cls, name, source):
        pieces, pos = [], 0
        for m in BLOCK.finditer(source):
            pieces += [source[pos:m.start()], Para(m.group("attrs"), m.group("inner") or "", m.group("tag"))]
            pos = m.end()
        return cls(name, source, pieces + [source[pos:]])

    @property
    def paras(self):
        """Paragraphs and headings in reading order."""
        return self.pieces[1::2]

    def live(self, tag=None):
        return [p for p in self.paras if p.state == "live" and (tag is None or p.tag == tag)]

    def render(self):
        return "".join(p if isinstance(p, str) else p.render() for p in self.pieces)

    def expected_text(self, plan):
        """The visible text as the plan should leave it: without dropped paragraphs, and with each remaining
        paragraph's removals and replacements made on the text of the original paragraphs merged into it."""
        out = []
        for p in self.pieces:
            if isinstance(p, str):
                out.append(visible_text(p))
            elif p.state == "live":
                text = visible_text("".join(remove_fragments(part, plan.remove) for part in p.parts))
                for old, new in p.replaced:
                    text = text.replace(normalized(old), normalized(new))
                out.append(text)
        return "".join(out)


class Corpus:
    """Every word and hyphenated pair in the book, to tell a word broken at a line end from a compound."""

    def __init__(self, texts):
        self.words, self.pairs = Counter(), Counter()
        for text in texts:
            low = text.lower()
            self.words.update(WORD.findall(low))
            for compound in COMPOUND.findall(low):
                bits = compound.split("-")
                self.pairs.update(f"{a}-{b}" for a, b in zip(bits, bits[1:]))

    def split_kind(self, x, y):
        """How "x-" and "y" broken apart at a line end go back together: "joined" (dis + turbing), "hyphened"
        (competent + looking), or "dash" when the hyphen stands for a dash ("than- the", "nineteenth- and")."""
        lx, ly = x.lower(), y.lower()
        if ly in SUSPENDED:
            return "dash"
        joined, hyphened = self.words[lx + ly], self.pairs[f"{lx}-{ly}"]
        if joined != hyphened:
            return "joined" if joined > hyphened else "hyphened"
        if ly in DASH_FOLLOWERS:
            return "dash"
        return "hyphened" if self.words[lx] >= 2 and self.words[ly] >= 2 else "joined"


@dataclass
class Rule:
    """A pattern matched against a block's whole text, applied to the nth matches (1-based) or to all."""
    pattern: re.Pattern
    level: int = 2
    nth: set | None = None
    seen: int = 0
    hits: int = 0

    def applies(self, text):
        if not self.pattern.fullmatch(text):
            return False
        self.seen += 1
        if self.nth is None or self.seen in self.nth:
            self.hits += 1
            return True
        return False


@dataclass
class Plan:
    headings: list = field(default_factory=list)
    heading_after: re.Pattern | None = None
    drop: list = field(default_factory=list)
    remove: list = field(default_factory=list)
    keep_breaks: list = field(default_factory=list)
    replace: list = field(default_factory=list)
    skip_files: set = field(default_factory=set)
    toc: bool | None = None
    rejoin: bool = False         # rejoin even in files whose breaks look deliberate
    join_capitals: bool = False  # join a long line ending in a word to a next line starting with a capital

    def keeps_breaks(self, para):
        return any(rx.search(para.text) for rx in self.keep_breaks)


PLAN_KEYS = {"headings", "heading_after", "drop", "remove", "keep_breaks", "replace", "skip_files", "toc", "rejoin",
             "join_capitals"}


def parse_rule(raw):
    if isinstance(raw, str):
        return Rule(re.compile(raw))
    unknown = set(raw) - {"match", "level", "nth"}
    if unknown or "match" not in raw:
        sys.exit(f"plan: a rule is a pattern or {{\"match\", \"level\", \"nth\"}}, not {raw}")
    return Rule(re.compile(raw["match"]), int(raw.get("level", 2)), set(raw["nth"]) if "nth" in raw else None)


def load_plan(path):
    if path is None:
        return Plan()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"plan: {path} is not valid JSON: {e}")
    if set(raw) - PLAN_KEYS:
        sys.exit(f"plan: unknown keys {sorted(set(raw) - PLAN_KEYS)}; expected {sorted(PLAN_KEYS)}")
    try:
        headings = [parse_rule(r) for r in raw.get("headings", [])]
        heading_after = re.compile(raw["heading_after"]) if raw.get("heading_after") else None
        drop = [parse_rule(r) for r in raw.get("drop", [])]
        remove = [re.compile(p) for p in raw.get("remove", [])]
        keep_breaks = [re.compile(p) for p in raw.get("keep_breaks", [])]
    except re.error as e:
        sys.exit(f"plan: bad regular expression {e.pattern!r}: {e}")
    replace = [tuple(pair) for pair in raw.get("replace", [])]
    if any(len(pair) != 2 for pair in replace):
        sys.exit("plan: each replace entry is [old, new]")
    return Plan(headings, heading_after, drop, remove, keep_breaks, replace, set(raw.get("skip_files", [])),
                raw.get("toc"), bool(raw.get("rejoin", False)), bool(raw.get("join_capitals", False)))


class Log:
    def __init__(self):
        self.entries = []

    def add(self, kind, where, what):
        self.entries.append((kind, where, what))

    def of(self, kind):
        return [e for e in self.entries if e[0] == kind]


def remove_trailing(fragment, char):
    return re.sub(TRAILING_CHAR[char] + r"(?=(?:\s|</[^>]+>)*$)", "", fragment.rstrip(), count=1)


def join(a_html, b_html, corpus, capitals=False):
    """a_html and b_html as one run of text when b continues the sentence a breaks off, else None. capitals
    joins a line ending in any word to one starting with a capital."""
    a, b = plain(a_html), plain(b_html)
    if not a or not b or len(a) < MIN_PROSE_LEN or ENDS_BOLD.search(a_html) or NOTE_END.search(a_html) \
            or NOTE_START.match(b_html) or LABEL.match(a) and LABEL.match(b):
        return None
    first, last = b[0], a[-1]
    if last.isdigit() and sum(c.isdigit() for c in a) > 0.1 * len(a):
        return None  # an index entry or a list of page numbers
    if not first.islower():
        # Before a capital only where a sentence plainly can't have ended: after a word like "the", inside a
        # line of capitals, after "Dr.", or after a comma that hands over to a quotation.
        last_word = m.group() if (m := LAST_WORD.search(a)) else ""
        first_word = m.group() if (m := FIRST_WORD.search(b)) else ""
        if not (last_word.lower() in CONTINUING_WORDS and last.isalpha()
                or len(last_word) > 1 and last_word.isupper() and len(first_word) > 1 and first_word.isupper()
                and last.isalpha()
                or ABBREVIATION.search(a)
                or last == "," and first in OPEN_QUOTES
                or capitals and last.isalpha()):
            return None

    def glue(sep):
        return a_html.rstrip() + sep + b_html.lstrip()

    if last == SOFT_HYPHEN:
        return remove_trailing(a_html, SOFT_HYPHEN) + b_html.lstrip()
    if ABBREVIATION.search(a):
        return glue(" ")
    for dash in DASHES:
        if a.endswith(dash):
            return glue(" " if a[:-len(dash)].endswith(" ") else "") if first.islower() else None
    if last in HYPHENS and len(a) > 1 and a[-2].isalpha():
        y = FIRST_WORD.match(b)
        if not first.islower() or not y:
            return None
        kind = corpus.split_kind(LAST_WORD.search(a[:-1]).group(), y.group())
        if kind == "joined":
            return remove_trailing(a_html, last) + b_html.lstrip()
        return glue("" if kind == "hyphened" else " ")
    if last in CLOSE_QUOTES:
        return glue(" ") if first.islower() else None
    if last in ",;":
        return glue(" ") if first.islower() or first in OPEN_QUOTES else None
    if last.isalnum() and (first.isalnum() or first in OPEN_QUOTES):
        return glue(" ")
    return None


def describe_join(a, b):
    return f"...{a[-45:]!r} + {b[:35]!r}"


def hyphen_join(a_text, b_text, merged_text):
    """For the log: how a word that a_text broke off with a hyphen came back together, or None."""
    head, tail = LAST_WORD.search(a_text[:-1]), FIRST_WORD.match(b_text)
    if a_text[-1:] not in tuple(HYPHENS + SOFT_HYPHEN) or a_text.endswith("--") or not head or not tail:
        return None
    x, y = head.group(), tail.group()
    result = next((r for r in (f"{x}-{y}", x + y, f"{x}- {y}") if r in merged_text), "?")
    return f"{x + '-'!r} + {y!r} -> {result!r}"


def remove_fragments(fragment, patterns):
    for rx in patterns:
        fragment = rx.sub("", fragment)
    return fragment


def remove_text(docs, plan, log):
    """Cut the plan's remove patterns out of every block, dropping blocks left empty."""
    hits = Counter()
    for doc in docs:
        for para in doc.live():
            had_text = bool(para.text)
            for rx in plan.remove:
                para.inner, n = rx.subn("", para.inner)
                hits[rx.pattern] += n
            if had_text and not para.text and not MEDIA.search(para.inner):
                para.state = "dropped"
    for rx in plan.remove:
        log.add("remove" if hits[rx.pattern] else "warning", "plan",
                f"remove {rx.pattern!r}: {hits[rx.pattern]} cut" if hits[rx.pattern]
                else f"remove pattern {rx.pattern!r} matched nothing")


def drop_paragraphs(docs, plan, log):
    for doc in docs:
        for para in doc.live():
            if MEDIA.search(para.inner):
                continue
            if any(rule.applies(para.text) for rule in plan.drop):
                para.state = "dropped"
                log.add("drop", doc.name, repr(para.text) if para.tag == "p" else f"{para.tag} {para.text!r}")
    for rule in plan.drop:
        if not rule.hits:
            log.add("warning", "plan", f"drop rule {rule.pattern.pattern!r} matched nothing")


def drop_spacers(docs, log):
    live = [(doc, p) for doc in docs for p in doc.live("p")]
    spacers = [(doc, p) for doc, p in live if not p.text and not MEDIA.search(p.inner)]
    if not spacers:
        return
    if len(spacers) >= SPACER_SHARE * (len(live) - len(spacers)):
        for _, para in spacers:
            para.state = "dropped"
        log.add("spacer", "book", f"removed {len(spacers)} empty paragraphs used as spacing")
    else:
        log.add("info", "book", f"kept {len(spacers)} empty paragraphs; too few to be spacing, likely scene breaks")


def heading_classes(texts):
    by_level = defaultdict(Counter)
    for text in texts:
        for m in HEADING.finditer(text):
            c = CLASS_ATTR.search(m.group("attrs"))
            if c:
                by_level[int(m.group("level"))][c.group(2)] += 1
    return {level: counts.most_common(1)[0][0] for level, counts in by_level.items()}


def mark_headings(docs, plan, classes, log):
    """Make the plan's headings, or give an existing heading the rule's level."""
    after_hits = 0
    previous = None
    for doc in docs:
        for para in doc.live():
            text = para.text
            rule = next((r for r in plan.headings if r.applies(text)), None)
            level = rule.level if rule else None
            if level is None and plan.heading_after and previous is not None and text \
                    and plan.heading_after.fullmatch(previous):
                after_hits += 1
                level = 2
            previous = text
            if level is None or para.tag == f"h{level}":
                continue
            if len(text) > MAX_HEADING_LEN:
                log.add("warning", doc.name, f"not a heading, {len(text)} characters long: {text[:60]!r}...")
                continue
            attrs = CLASS_ATTR.sub("", para.attrs)
            if classes.get(level):
                attrs = f' class="{classes[level]}"' + attrs
            para.tag, para.attrs, para.inner = f"h{level}", attrs, BOLD_TAG.sub("", para.inner).strip()
            log.add("heading", doc.name, f"h{level} {text!r}")
    for rule in plan.headings:
        if not rule.hits:
            log.add("warning", "plan", f"heading rule {rule.pattern.pattern!r} matched nothing")
    if plan.heading_after and not after_hits:
        log.add("warning", "plan", f"heading_after {plan.heading_after.pattern!r} matched nothing")


def join_lines(doc, corpus, plan, log):
    """Join lines inside a paragraph that a <br/> breaks mid-sentence."""
    for para in doc.live():
        if para.tag != "p" or "<br" not in para.inner or plan.keeps_breaks(para):
            continue
        bits = BR.split(para.inner)
        lines, breaks = [bits[0]], []
        for br, line in zip(bits[1::2], bits[2::2]):
            merged = join(lines[-1], line, corpus, plan.join_capitals)
            if merged is None:
                breaks.append(br)
                lines.append(line)
            else:
                log.add("rejoin-line", doc.name, describe_join(plain(lines[-1]), plain(line)))
                lines[-1] = merged
        para.inner = lines[0] + "".join(br + line for br, line in zip(breaks, lines[1:]))


def paragraph_pairs(doc, plan):
    """(index of a, a, index of b, b, markup between) for each live paragraph b and the live paragraph a before
    it, when nothing but page-break markup separates them and both are plain paragraphs of the same class."""
    prev = None
    for i, para in enumerate(doc.pieces):
        if not isinstance(para, Para) or para.state != "live":
            continue
        if prev is not None:
            a = doc.pieces[prev]
            gap = "".join(p if isinstance(p, str) else p.render() for p in doc.pieces[prev + 1:i])
            if a.tag == para.tag == "p" and a.classes == para.classes and not GAP_MARKUP.sub("", gap) \
                    and not plan.keeps_breaks(a) and not plan.keeps_breaks(para):
                yield prev, a, i, para, gap
        if para.state == "live":  # a consumer that merged para into a leaves prev on a
            prev = i


def count_breaks(doc, corpus, plan):
    """Paragraph breaks in doc that fall mid-sentence before a lowercase word, and all its paragraph breaks."""
    pairs = list(paragraph_pairs(doc, plan))
    lower = sum(1 for _, a, _, b, _ in pairs if b.text[:1].islower() and join(a.inner, b.inner, corpus))
    return lower, len(pairs)


def rejoin_paragraphs(doc, corpus, plan, log):
    """Merge each paragraph into the one before it when it continues that paragraph's sentence, and log the
    long lines left ending in a word before a capital, which may be breaks this couldn't be sure of."""
    for prev, a, i, para, gap in paragraph_pairs(doc, plan):
        a_text, b_text = a.text, para.text
        merged = join(a.inner, para.anchors(include_inner=False) + para.inner, corpus, plan.join_capitals)
        if merged is None:
            if len(a_text) >= MIN_PROSE_LEN and a_text[-1].isalpha() and b_text and not b_text[0].islower():
                log.add("left", doc.name, describe_join(a_text, b_text))
            continue
        log.add("rejoin" if b_text[:1].islower() else "rejoin-capital", doc.name, describe_join(a_text, b_text))
        a.inner, para.state = merged, "merged"
        a.parts += para.parts
        word = hyphen_join(a_text, b_text, a.text)
        if word:
            log.add("word", doc.name, word)
        if not gap.strip():
            doc.pieces[prev + 1:i] = ["" if isinstance(p, str) else p for p in doc.pieces[prev + 1:i]]


def join_split_words(doc, corpus, log):
    def fix(m):
        x, y = m.group(1), m.group(2)
        kind = corpus.split_kind(x, y) if y[0].islower() else "dash"
        if kind == "dash":
            return m.group(0)
        new = x + y if kind == "joined" else f"{x}-{y}"
        log.add("word", doc.name, f"{m.group(0)!r} -> {new!r}")
        return new

    for para in doc.live():
        if para.tag == "p":
            bits = TAG_SPLIT.split(para.inner)
            para.inner = "".join(b if k % 2 else SPLIT_WORD.sub(fix, b) for k, b in enumerate(bits))


def replace_text(docs, plan, log):
    for old, new in plan.replace:
        count = 0
        for doc in docs:
            for para in doc.live():
                if old in para.inner:
                    count += para.inner.count(old)
                    para.inner = para.inner.replace(old, new)
                    para.replaced.append((old, new))
        if not count:
            sys.exit(f"plan: replace {old!r} matched nothing")
        log.add("replace", "book", f"{old!r} -> {new!r} ({count}x)")


def compound_parts(selector):
    return [p for p in COMBINATOR.split(selector.strip()) if p]


def parse_simple(part):
    m = SIMPLE_SELECTOR.fullmatch(part)
    return ((m.group(1) or "").lower(), set(m.group(2).split(".")[1:])) if m else None


def targets_body_text(selector, body_classes):
    """Whether a CSS selector styles ordinary body paragraphs."""
    cleaned = clean_selector(selector)
    if re.search(r"[+~]", cleaned):  # "h1 + p" or "p.break + p" style one paragraph; "p + p" spaces them all
        return bool(re.fullmatch(r"p\s*[+~]\s*p", cleaned))
    parts = compound_parts(cleaned)
    if not parts:
        return False
    *context, last = parts
    parsed = parse_simple(last)
    if not parsed:
        return False
    tag, classes = parsed
    if tag not in ("", "p") or not (tag or classes) or not classes <= body_classes:
        return False
    for part in context:
        parsed = parse_simple(part)
        if not parsed or parsed[0] not in ("html", "body", "div", "section", "p") or (
                parsed[0] in ("div", "section", "p") and parsed[1]):
            return False
    return True


def expand_box(values):
    return {1: lambda v: (v[0], v[0], v[0], v[0]), 2: lambda v: (v[0], v[1], v[0], v[1]),
            3: lambda v: (v[0], v[1], v[2], v[1]), 4: tuple}[len(values)](values)


def zero_vertical(prop, value):
    """The value with vertical margins set to 0, and whether a nonzero one was removed."""
    important = ""
    v = value.strip()
    if v.lower().endswith("!important"):
        important, v = " !important", v[:-10].strip()
    parts = v.split()
    if not parts or "(" in v:
        return value, False
    if prop in ("margin-top", "margin-bottom"):
        return "0" + important, not ZERO.fullmatch(parts[0])
    if len(parts) > 4:
        return value, False
    top, right, bottom, left = expand_box(parts)
    if ZERO.fullmatch(top) and ZERO.fullmatch(bottom):
        return value, False
    sides = right if right == left else f"{right} 0 {left}"
    return ("0" if ZERO.fullmatch(right) and right == left else f"0 {sides}") + important, True


class CssFixer:
    """Removes line-height from blocks of text and the vertical margins of body paragraphs, adding a
    first-line indent when paragraphs would otherwise run together, and justifies body paragraphs."""

    def __init__(self, body_classes, fixes):
        self.body_classes, self.fixes = body_classes, fixes
        self.has_indent = self.declares_margin = self.has_body_rule = self.declares_alignment = False

    def survey(self, css):
        for m in CSS_RULE.finditer(css):
            if any(targets_body_text(s, self.body_classes) for s in m.group("selector").split(",")):
                self.has_body_rule = True
                for chunk in m.group("body").split(";"):
                    d = CSS_DECL.match(chunk)
                    if not d:
                        continue
                    prop, value = d.group(2).lower(), d.group(4).strip()
                    if prop == "text-indent" and not ZERO.fullmatch(value.split()[0] if value else "0"):
                        self.has_indent = True
                    if prop in ("margin", "margin-top", "margin-bottom"):
                        self.declares_margin = True
                    if prop == "text-align":
                        self.declares_alignment = True

    def fix(self, css, where, log):
        removed_heights = 0

        def edit(m):
            nonlocal removed_heights
            selector, body = m.group("selector"), m.group("body")
            name = clean_selector(selector)
            body_text = any(targets_body_text(s, self.body_classes) for s in selector.split(","))
            inline = styles_inline_text(selector, body)
            justify = body_text and "justify" in self.fixes
            chunks, gap_removed = body.split(";"), False
            out = []
            for k, chunk in enumerate(chunks):
                d = CSS_DECL.match(chunk)
                prop = d.group(2).lower() if d else ""
                value = d.group(4).strip() if d else ""
                if prop == "line-height" and "line-height" in self.fixes and not inline \
                        or justify and prop == "text-justify" and value.lower().startswith("none"):
                    if prop == "line-height":
                        removed_heights += 1
                    else:
                        log.add("justify", where, f"removed text-justify: none from {name}")
                    if k == len(chunks) - 1:
                        out.append(chunk[len(chunk.rstrip()):])
                    continue
                if body_text and "spacing" in self.fixes and prop in ("margin", "margin-top", "margin-bottom"):
                    new, changed = zero_vertical(prop, d.group(4))
                    if changed:
                        gap_removed = True
                        chunk = d.group(1) + d.group(2) + d.group(3) + new + d.group(5)
                if justify and prop == "text-align" and LEFT_ALIGN.fullmatch(value):
                    important = " !important" if "!important" in value.lower() else ""
                    chunk = d.group(1) + d.group(2) + d.group(3) + "justify" + important + d.group(5)
                    log.add("justify", where, f"changed text-align: {value} to justify in {name}")
                out.append(chunk)
            if gap_removed:
                log.add("spacing", where, f"removed the paragraph gap in {name}")
                if not self.has_indent:
                    out = add_declaration(out, f"text-indent: {INDENT}")
                    log.add("spacing", where, f"added a {INDENT} first-line indent to {name}")
            if justify and not self.declares_alignment:
                out = add_declaration(out, "text-align: justify")
                log.add("justify", where, f"added text-align: justify to {name}")
            return selector + "{" + ";".join(out) + "}"

        css = CSS_RULE.sub(edit, css)
        if removed_heights:
            log.add("line-height", where, f"removed line-height from {removed_heights} rules")
        return css


def styles_inline_text(selector, body):
    """Whether a rule styles inline text such as a superscript, whose small line-height keeps it from
    stretching its line, rather than a block of text."""
    if re.search(r"vertical-align\s*:\s*(?!baseline)", body):
        return True
    tags = [parse_simple(parts[-1]) for s in clean_selector(selector).split(",") if (parts := compound_parts(s))]
    return bool(tags) and all(t and t[0] in INLINE_TAGS for t in tags)


def add_declaration(chunks, declaration):
    lead = re.match(r"\s*", chunks[0]).group() or " "
    if len(chunks) > 1 and not chunks[-1].strip():
        return chunks[:-1] + [lead + declaration] + chunks[-1:]
    content = chunks[-1].rstrip()
    return chunks[:-1] + [content, lead + declaration + chunks[-1][len(content):]]


def main_stylesheet(book, texts):
    linked = Counter()
    for name in book.spine:
        for href in STYLESHEET_LINK.findall(texts[name]):
            linked[resolve(name, href)[0]] += 1
    ranked = [p for p, _ in linked.most_common() if p in book.data]
    return (ranked or book.stylesheets or [None])[0]


def fix_styles(book, texts, body_classes, fixes, out, log):
    """Apply CssFixer to every stylesheet, adding each changed one to out, and to the <style> blocks in texts."""
    fixer = CssFixer(body_classes, fixes)
    sheets = {p: decode_text(book.data[p]) for p in book.stylesheets}
    for path in [p for p, css in sheets.items() if css is None]:
        log.add("warning", path, "stylesheet is not UTF-8; left alone")
        del sheets[path]
    for css in list(sheets.values()) + [m.group(2) for t in texts.values() for m in STYLE_BLOCK.finditer(t)]:
        fixer.survey(css)
    changed = {}
    for path, css in sheets.items():
        new = fixer.fix(css, path, log)
        if new != css:
            changed[path] = new
    for name, text in texts.items():
        texts[name] = STYLE_BLOCK.sub(lambda m: m.group(1) + fixer.fix(m.group(2), name, log) + m.group(3), text)
    target = main_stylesheet(book, texts)
    declarations = []
    if "spacing" in fixes and not fixer.declares_margin:
        declarations += ["margin-top: 0", "margin-bottom: 0"] + ([] if fixer.has_indent else [f"text-indent: {INDENT}"])
        if target in sheets:
            log.add("spacing", target, "no paragraph margins were set; added zero margins"
                    + ("" if fixer.has_indent else f" and a {INDENT} indent"))
    if "justify" in fixes and not fixer.has_body_rule:
        declarations.append("text-align: justify")
        if target in sheets:
            log.add("justify", target, "no rule styled body paragraphs; added one with text-align: justify")
    if declarations and target in sheets:
        rule = "p {\n" + ";\n".join(f"    {d}" for d in declarations) + "\n    }\n"
        changed[target] = changed.get(target, sheets[target]).rstrip() + "\n" + rule
    elif declarations:
        log.add("warning", "book", f"no stylesheet to add {'; '.join(declarations)} to; left as it is")
    out.update({p: css.encode("utf-8") for p, css in changed.items()})


def clear_left_alignment(docs, body_classes, log):
    """Remove inline left alignment from body paragraphs, which would override the stylesheet's justification."""
    count = 0
    for doc in docs:
        for para in doc.live("p"):
            if not set(para.classes) <= body_classes:
                continue
            attrs = INLINE_ALIGN_LEFT.sub("", para.attrs)
            attrs = STYLE_ATTR.sub(lambda m: m.group(1) + m.group(2) + INLINE_TEXT_ALIGN_LEFT.sub("", m.group(3))
                                   + m.group(2), attrs)
            attrs = re.sub(r"""\sstyle\s*=\s*(["'])\s*\1""", "", attrs)
            if attrs != para.attrs:
                para.attrs = attrs
                count += 1
    if count:
        log.add("justify", "book", f"removed inline left alignment from {count} paragraphs")


def old_toc(book):
    """(label, target) of each entry in the current NCX, or the nav document when there is no NCX."""
    if book.ncx:
        s = book.data[book.ncx].decode("utf-8")
        return [(plain(m.group(1)), "#".join(resolve(book.ncx, m.group(2))).rstrip("#"))
                for m in re.finditer(r"<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content\s+src=\"([^\"]*)\"",
                                     s, re.S)]
    if book.nav:
        s = book.data[book.nav].decode("utf-8")
        block = re.search(r"<nav\b[^>]*epub:type=\"[^\"]*\btoc\b[^\"]*\"[^>]*>(.*?)</nav>", s, re.S)
        return [(plain(m.group(2)), "#".join(resolve(book.nav, m.group(1))).rstrip("#"))
                for m in re.finditer(r"<a\b[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>", block.group(1) if block else "",
                                     re.S)]
    return []


def build_toc(book, texts, plan, out, log):
    """Rebuild the NCX and nav table of contents from the book's headings when the current one is broken: under
    3 entries, or linking fewer than half the files that hold chapter headings."""
    found = [(name, m) for name in book.spine for m in HEADING.finditer(texts[name])]
    levels = Counter(int(m.group("level")) for _, m in found)
    ranked = sorted(levels)
    if len(ranked) > 1 and levels[ranked[0]] == 1:
        ranked = ranked[1:]  # a lone top-level heading is the book's own title
    toc_levels = ranked[:2]
    headings = [(name, m) for name, m in found if int(m.group("level")) in toc_levels]
    old = old_toc(book)
    chapter_files = {name for name, m in headings if toc_levels and int(m.group("level")) == toc_levels[0]}
    linked = chapter_files & {target.partition("#")[0] for _, target in old}
    broken = len(headings) > len(old) and (len(old) < 3 or len(linked) < len(chapter_files) / 2)
    if plan.toc is False or (plan.toc is None and not broken):
        log.add("toc", "book", f"kept the table of contents: {len(old)} entries; {len(headings)} headings, and "
                               f"{len(linked)} of the {len(chapter_files)} files with chapter headings are linked")
        return
    ids = {name: set(m.group(2) for m in TARGET_ATTR.finditer(texts[name])) for name in book.spine}
    counter = itertools.count(1)
    entries, assigned = [], {}
    for name, m in headings:
        found_id = ID_ATTR.search(m.group("attrs"))
        hid = found_id.group(2) if found_id else None
        if hid is None:
            hid = next(f"heading-{n}" for n in counter if f"heading-{n}" not in ids[name])
            ids[name].add(hid)
            assigned[(name, m.start())] = hid
        depth = toc_levels.index(int(m.group("level"))) + 1
        entries.append((book.spine.index(name), m.start(), depth, plain(m.group("inner")), f"{name}#{hid}"))
    with_headings = {name for name, _ in headings}
    for label, target in old:
        path = target.partition("#")[0]
        if path not in with_headings and path in book.data:
            entries.append((book.spine.index(path) if path in book.spine else len(book.spine), -1, 1, label, target))
    entries.sort(key=lambda e: (e[0], e[1]))
    for name in with_headings:
        texts[name] = HEADING.sub(lambda m: add_id(m, assigned.get((name, m.start()))), texts[name])
    tree = nest(entries)
    if book.version.startswith("3") and not book.nav:
        create_nav(book, tree, out, log)
    elif book.nav:
        write_nav(book, tree, out)
    if book.ncx:
        write_ncx(book, tree, out)
    elif not book.version.startswith("3"):
        create_ncx(book, tree, out, log)
    log.add("toc", "book", f"rebuilt the table of contents: {len(old)} entries before, {len(entries)} now")
    for _, _, depth, label, _ in entries:
        log.add("toc-entry", "book", "  " * (depth - 1) + label)


def add_id(m, hid):
    if not hid:
        return m.group(0)
    level = m.group("level")
    return f'<h{level}{m.group("attrs")} id="{hid}">{m.group("inner")}</h{level}>'


def nest(entries):
    """Second-level entries under the top-level heading before them. An entry kept from the old table of
    contents (position -1) holds nothing, and second-level entries before any top-level heading stay on top."""
    tree, parent = [], None
    for _, position, depth, label, target in entries:
        node = (label, target, [])
        if depth == 2 and parent:
            parent[2].append(node)
        else:
            tree.append(node)
            parent = node if depth == 1 and position >= 0 else None
    return tree


def ncx_points(tree, ncx_path):
    lines, order = [], itertools.count(1)

    def emit(nodes, indent):
        for label, target, children in nodes:
            n = next(order)
            lines.extend([f'{indent}<navPoint id="navpoint-{n}" playOrder="{n}">',
                          f"{indent}  <navLabel>", f"{indent}    <text>{html.escape(label, quote=False)}</text>",
                          f"{indent}  </navLabel>", f'{indent}  <content src="{relative_href(target, ncx_path)}"/>'])
            emit(children, indent + "  ")
            lines.append(f"{indent}</navPoint>")

    emit(tree, "    ")
    return "<navMap>\n" + "\n".join(lines) + "\n  </navMap>"


def nav_list(tree, nav_path, indent="      "):
    lines = [f"{indent}<ol>"]
    for label, target, children in tree:
        link = f'<a href="{relative_href(target, nav_path)}">{html.escape(label, quote=False)}</a>'
        if children:
            lines += [f"{indent}  <li>{link}", nav_list(children, nav_path, indent + "    "), f"{indent}  </li>"]
        else:
            lines.append(f"{indent}  <li>{link}</li>")
    return "\n".join(lines + [f"{indent}</ol>"])


def depth(tree):
    return 2 if any(children for _, _, children in tree) else 1


def write_ncx(book, tree, out):
    s = book.data[book.ncx].decode("utf-8")
    s = re.sub(r"<navMap\b[^>]*>.*</navMap>", lambda _: ncx_points(tree, book.ncx), s, count=1, flags=re.S)
    s = re.sub(r'(<meta\s+name="dtb:depth"\s+content=")\d+', lambda m: m.group(1) + str(depth(tree)), s)
    out[book.ncx] = s.encode("utf-8")


def write_nav(book, tree, out):
    s = book.data[book.nav].decode("utf-8")
    block = re.search(r"(<nav\b[^>]*epub:type=\"[^\"]*\btoc\b[^\"]*\"[^>]*>)(.*?)(</nav>)", s, re.S)
    if not block:
        return
    before_list = block.group(2).split("<ol", 1)[0]
    new = block.group(1) + before_list.rstrip() + "\n" + nav_list(tree, book.nav) + "\n    " + block.group(3)
    out[book.nav] = (s[:block.start()] + new + s[block.end():]).encode("utf-8")


def add_manifest_item(book, out, item, spine_toc=None):
    opf = out.get(book.opf_path, book.data[book.opf_path]).decode("utf-8")
    opf = re.sub(r"(\s*)</manifest>", lambda m: f"{m.group(1)}  {item}{m.group(1)}</manifest>", opf, count=1)
    if spine_toc:
        opf = re.sub(r"<spine\b(?![^>]*\btoc=)", f'<spine toc="{spine_toc}"', opf, count=1)
    out[book.opf_path] = opf.encode("utf-8")


def free_path(book, name):
    base = posixpath.dirname(book.opf_path)
    path = posixpath.join(base, name) if base else name
    stem, ext = posixpath.splitext(path)
    return next(p for p in itertools.chain([path], (f"{stem}-{n}{ext}" for n in itertools.count(2)))
                if p not in book.data)


def create_ncx(book, tree, out, log):
    path = free_path(book, "toc.ncx")
    opf = ET.fromstring(book.data[book.opf_path])
    uid_el = opf.find(f".//*[@id='{opf.get('unique-identifier')}']")
    uid = html.escape((uid_el.text or "").strip() if uid_el is not None else "")
    out[path] = (f'<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                 f'version="2005-1">\n  <head>\n    <meta name="dtb:uid" content="{uid}"/>\n'
                 f'    <meta name="dtb:depth" content="{depth(tree)}"/>\n  </head>\n'
                 f"  <docTitle>\n    <text>{html.escape(book.title)}</text>\n  </docTitle>\n"
                 f"  {ncx_points(tree, path)}\n</ncx>\n").encode("utf-8")
    add_manifest_item(book, out, f'<item id="fix-epub-ncx" href="{relative_href(path, book.opf_path)}" '
                                 f'media-type="{NCX_TYPE}"/>', spine_toc="fix-epub-ncx")
    log.add("toc", path, "created an NCX table of contents")


def create_nav(book, tree, out, log):
    path = free_path(book, "nav.xhtml")
    out[path] = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                 'xmlns:epub="http://www.idpf.org/2007/ops">\n  <head>\n    <title>Contents</title>\n  </head>\n'
                 '  <body>\n    <nav epub:type="toc" id="toc">\n      <h1>Contents</h1>\n'
                 f"{nav_list(tree, path)}\n    </nav>\n  </body>\n</html>\n").encode("utf-8")
    add_manifest_item(book, out, f'<item id="fix-epub-nav" href="{relative_href(path, book.opf_path)}" '
                                 'media-type="application/xhtml+xml" properties="nav"/>')
    log.add("toc", path, "created a nav table of contents")


def body_paragraph_classes(docs):
    counts, total = Counter(), 0
    for doc in docs:
        for para in doc.paras:
            if para.tag == "p":
                total += 1
                counts.update(set(para.classes))
    return {c for c, n in counts.items() if n >= BODY_CLASS_SHARE * total}


def decode_spine(book, log):
    texts = {}
    for name in book.spine:
        text = decode_text(book.data[name])
        if text is None:
            log.add("warning", name, "not UTF-8; left alone")
        else:
            texts[name] = text
    return texts


def fix_book(book, plan, fixes, log):
    """Every changed or added file as {archive path: bytes}, and the parsed documents for verification."""
    texts = decode_spine(book, log)
    docs = {name: Doc.parse(name, text) for name, text in texts.items()}
    work = [d for name, d in docs.items() if name not in plan.skip_files]
    corpus = Corpus(p.text for d in docs.values() for p in d.paras)
    remove_text(work, plan, log)
    drop_paragraphs(work, plan, log)
    if "spacing" in fixes:
        drop_spacers(work, log)
    mark_headings(work, plan, heading_classes(texts.values()), log)
    if "rejoin" in fixes:
        counts = {doc.name: count_breaks(doc, corpus, plan) for doc in work}
        lower, total = sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values())
        broken = [doc for doc in work if plan.rejoin or lower >= BROKEN_MIN and counts[doc.name][0] >= max(
            2, BROKEN_SHARE * counts[doc.name][1])]
        skipped = [d.name for d in work if d not in broken and counts[d.name][0]]
        verdict = (f"rejoining in {len(broken)} of {len(work)} files" if broken
                   else "too few to be conversion damage, so not rejoining")
        if broken and skipped:
            verdict += (f"; left alone despite a few such breaks: {', '.join(skipped[:8])}"
                        + (f" and {len(skipped) - 8} more" if len(skipped) > 8 else ""))
        log.add("info", "book", f"{lower} of {total} paragraph breaks fall mid-sentence before a lowercase word: "
                                f"{verdict}")
        for doc in broken:
            join_lines(doc, corpus, plan, log)
            rejoin_paragraphs(doc, corpus, plan, log)
    for doc in work:
        if "words" in fixes:
            join_split_words(doc, corpus, log)
    replace_text(work, plan, log)
    body_classes = body_paragraph_classes(docs.values())
    if "justify" in fixes:
        clear_left_alignment(work, body_classes, log)
    texts = {name: doc.render() for name, doc in docs.items()}
    out = {}
    fix_styles(book, texts, body_classes, fixes, out, log)
    if "toc" in fixes:
        build_toc(book, texts, plan, out, log)
    for name, text in texts.items():
        if text != docs[name].source:
            out[name] = text.encode("utf-8")
    return out, docs


def parses(data):
    try:
        ET.fromstring(data)
        return True
    except ET.ParseError:
        return False


def broken_links(book, files):
    """(source, href) of each internal link in the content, NCX and nav that points nowhere."""
    targets = {}

    def ids_of(path):
        if path not in targets:
            text = files[path].decode("utf-8", "replace") if path in files else ""
            targets[path] = {m.group(2) for m in TARGET_ATTR.finditer(text)}
        return targets[path]

    broken = set()
    sources = [n for n in book.spine if n in files] + [p for p in (book.ncx, book.nav) if p and p in files]
    for source in sources:
        for m in LINK_ATTR.finditer(files[source].decode("utf-8", "replace")):
            href = m.group(2)
            if re.match(r"[a-z][a-z0-9+.-]*:", href, re.I) or not href.strip():
                continue
            path, fragment = resolve(source, href)
            if path not in files or (fragment and path.endswith(("html", "htm")) and fragment not in ids_of(path)):
                broken.add((source, href))
    return broken


def verify(book, out, docs, plan):
    """Problems the fixed book has that the original did not."""
    problems = []
    for name, data in out.items():
        if name.endswith((".html", ".xhtml", ".htm", ".ncx", ".opf")) and parses(book.data.get(name, b"<x/>")) \
                and not parses(data):
            problems.append(f"{name} is no longer well-formed XML")
    for name, doc in docs.items():
        if name in out and visible_text(out[name].decode("utf-8")) != doc.expected_text(plan):
            problems.append(f"{name}: text changed beyond joins, drops, removals and replacements")
        if name in out:
            before = {m.group(2) for m in TARGET_ATTR.finditer(doc.source)}
            after = {m.group(2) for m in TARGET_ATTR.finditer(out[name].decode("utf-8"))}
            if before - after:
                problems.append(f"{name}: lost ids {sorted(before - after)[:5]}")
    new_files = {**book.data, **out}
    for source, href in sorted(broken_links(book, new_files) - broken_links(book, book.data)):
        problems.append(f"{source}: link to {href} now points nowhere")
    return problems


def write_epub(book, out, dest):
    infos = sorted(book.infos, key=lambda i: i.filename != "mimetype")
    with zipfile.ZipFile(dest, "w") as z:
        for info in infos:
            data = out.get(info.filename, book.data[info.filename])
            stored = info.filename == "mimetype" or info.compress_type == zipfile.ZIP_STORED
            z.writestr(info, data, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
        for name in sorted(set(out) - set(book.data)):
            z.writestr(name, out[name], compress_type=zipfile.ZIP_DEFLATED)


LOG_LABELS = {
    "rejoin": "paragraphs rejoined before a lowercase word",
    "rejoin-capital": "paragraphs rejoined before a capital, quote or digit (check each)",
    "rejoin-line": "lines rejoined inside paragraphs",
    "word": "split words rejoined",
    "left": "long lines left ending in a word before a capital (breaks this couldn't be sure of)",
    "drop": "paragraphs and headings dropped by the plan",
    "heading": "headings made",
}


def show(items, limit, indent="  "):
    for item in items[:limit]:
        print(indent + item)
    if len(items) > limit:
        print(f"{indent}... {len(items) - limit} more")


def print_log(log, details=True):
    counts = Counter(kind for kind, _, _ in log.entries)
    print("Summary:")
    for kind, label in LOG_LABELS.items():
        if counts[kind]:
            print(f"  {counts[kind]:5} {label}")
    for kind in ("remove", "replace", "spacer", "spacing", "line-height", "justify", "toc", "info", "warning"):
        for _, where, what in log.of(kind):
            print(f"  {kind}: {what}" + ("" if where in ("book", "plan") else f" [{where}]"))
    if not details:
        return
    for kind in ("rejoin-capital", "left", "heading", "drop", "word", "rejoin-line", "rejoin"):
        entries = log.of(kind)
        if entries:
            print(f"\n{LOG_LABELS[kind]}:")
            show([f"{where}: {what}" for _, where, what in entries], len(entries))
    entries = log.of("toc-entry")
    if entries:
        print("\ntable of contents:")
        show([what for _, _, what in entries], len(entries))


def inspect(path):
    book = load(path)
    log = Log()
    texts = decode_spine(book, log)
    docs = [Doc.parse(name, text) for name, text in texts.items()]
    paras = [(doc.name, p) for doc in docs for p in doc.paras if p.tag == "p"]
    print(f"Book: {book.title!r}, EPUB {book.version}, {len(book.spine)} content files, {len(paras)} paragraphs")
    total = len(paras) or 1
    class_counts = Counter(c for _, p in paras for c in set(p.classes))
    print("\nParagraph classes (share of <p>):")
    for cls, n in class_counts.most_common(8):
        print(f"  {cls:24} {n:6} ({n / total:.0%})")
    body_classes = body_paragraph_classes(docs)
    print("\nCSS that styles body paragraphs:")
    sheets = {p: book.data[p].decode("utf-8", "replace") for p in book.stylesheets}
    sheets.update({f"<style> in {n}": m.group(2) for n, t in texts.items() for m in STYLE_BLOCK.finditer(t)})
    block_heights, inline_heights = Counter(), 0
    for where, css in sheets.items():
        for m in CSS_RULE.finditer(css):
            body = " ".join(m.group("body").split())
            if any(targets_body_text(s, body_classes) for s in m.group("selector").split(",")):
                print(f"  {where}: {' '.join(clean_selector(m.group('selector')).split())} {{ {body} }}")
            for value in re.findall(r"line-height\s*:\s*([^;]+)", body):
                if styles_inline_text(m.group("selector"), m.group("body")):
                    inline_heights += 1
                else:
                    block_heights[value.strip()] += 1
    if block_heights or inline_heights:
        values = ", ".join(f"{v} x{n}" for v, n in block_heights.most_common(6))
        print(f"line-height: {sum(block_heights.values())} rules for blocks of text ({values}), which fix removes; "
              f"{inline_heights} for inline text like superscripts, which it keeps")
    empty = sum(1 for _, p in paras if not p.text and not MEDIA.search(p.inner))
    brs = sum(len(BR.findall(p.inner)) for _, p in paras)
    print(f"\nEmpty paragraphs: {empty}    <br> inside paragraphs: {brs}")
    found = Counter(f"h{m.group('level')}" for t in texts.values() for m in HEADING.finditer(t))
    print("Existing headings: " + (", ".join(f"{k} x{v}" for k, v in sorted(found.items())) or "none"))
    for t in texts.values():
        for m in list(HEADING.finditer(t))[:3]:
            print(f"  h{m.group('level')}: {plain(m.group('inner'))!r}")
    old = old_toc(book)
    print(f"Table of contents ({book.ncx or book.nav or 'none'}): {len(old)} entries")
    show([f"{label!r} -> {target}" for label, target in old], 12)

    print("\nWhat `fix` does with no plan:")
    fix_log = Log()
    _, fixed_docs = fix_book(book, Plan(), set(FIXES), fix_log)
    print_log(fix_log, details=False)
    for kind, title in (("rejoin-capital", "joins before a capital, quote or digit"), ("rejoin", "sample joins"),
                        ("left", "breaks left alone that may be mid-sentence"),
                        ("rejoin-line", "sample line joins"), ("word", "sample split words")):
        entries = fix_log.of(kind)
        if entries:
            print(f"  {title}:")
            show([f"{where}: {what}" for _, where, what in entries], 12, "    ")

    paras = [(doc.name, p) for doc in fixed_docs.values() for p in doc.live("p")]
    repeated = Counter(p.text for _, p in paras if p.text and len(p.text) <= MIN_PROSE_LEN)
    print("\nPossible chapter titles (short lines that don't end like a sentence or a quote and don't repeat), with "
          "the line before, after the joins above:")
    candidates = []
    previous = ""
    for name, p in paras:
        t = p.text
        ends_sentence = t.endswith((".", ",", ";", ":")) and not t.endswith(("...", "…"))
        dialogue = len(t) > 1 and t[-1] in CLOSE_QUOTES and t[-2] in ".,?!-—…"
        if t and len(t) <= MIN_PROSE_LEN and not t[0].islower() and not ends_sentence and not dialogue \
                and repeated[t] < 3 and any(c.isalnum() for c in t):
            flags = [f for f, ok in (("bold", bool(re.match(r"\s*(?:<a\b[^>]*>\s*</a>\s*)*<(?:b|strong)\b", p.inner))),
                                     ("chapter-word", bool(CHAPTER_WORDS.match(t))),
                                     ("roman", bool(ROMAN.fullmatch(t))), ("number", bool(NUMBER.fullmatch(t))),
                                     ("caps", t.isupper() and len(t) > 3)) if ok]
            before = previous if len(previous) <= 40 else "…" + previous[-40:]
            candidates.append(f"{name} [{' '.join(p.classes)}] {' '.join(flags)}: {t!r}   <- {before!r}")
        previous = t
    show(candidates, 150)
    common = [(t, n) for t, n in repeated.most_common(15) if n >= 3]
    if common:
        print("\nShort lines that repeat (running headers, separators?):")
        for t, n in common:
            print(f"  {n:5}x {t!r}")
    numbers = [int(p.text.rstrip(".")) for _, p in paras if NUMBER.fullmatch(p.text)]
    if numbers:
        steps = sum(1 for a, b in zip(numbers, numbers[1:]) if 0 < b - a <= 2)
        kind = "mostly rising by 1-2, likely page numbers" if steps >= 0.6 * max(len(numbers) - 1, 1) else ""
        print(f"\nNumber-only paragraphs: {len(numbers)} {kind}: {numbers[:20]}")
    for _, where, what in log.entries:
        print(f"warning: {what} [{where}]")


def source_and_target(book, output):
    """The file to read, the file to write, and the backup to move the input to (None if not needed)."""
    if output:
        if output.resolve() == book.resolve():
            sys.exit("-o is the input file; leave out -o to fix the book in place with a backup")
        return book, output, None
    if book.stem.endswith(ORIGINAL_SUFFIX):
        return book, book.with_name(book.stem[:-len(ORIGINAL_SUFFIX)] + book.suffix), None
    backup = book.with_name(book.stem + ORIGINAL_SUFFIX + book.suffix)
    if backup.exists():
        return backup, book, None
    return book, book, backup


def fix(args):
    if args.skip and args.only:
        sys.exit("--skip and --only don't go together")
    named = {s.strip() for s in (args.only or args.skip).split(",") if s.strip()}
    if named - set(FIXES):
        sys.exit(f"--skip and --only take {', '.join(FIXES)}")
    fixes = named if args.only else set(FIXES) - named
    plan = load_plan(args.plan)
    source, target, backup = source_and_target(args.book, args.output)
    book = load(source)
    log = Log()
    out, docs = fix_book(book, plan, fixes, log)
    if any(info.filename == "mimetype" for info in book.infos) and book.infos[0].filename != "mimetype":
        log.add("info", "book", "moved mimetype to the front of the archive")
        out.setdefault("mimetype", book.data["mimetype"])
    problems = verify(book, out, docs, plan)
    if problems:
        print_log(log)
        print("\nNot written; the result failed verification:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(1)
    done = [f"Verified: text intact, XML well-formed, links resolve; {len(out)} "
            f"file{'' if len(out) == 1 else 's'} changed."]
    if args.dry_run:
        done.append("Dry run: nothing written.")
    else:
        fd, temp = tempfile.mkstemp(suffix=".epub", dir=target.parent)
        os.close(fd)
        try:
            write_epub(book, out, temp)
            if backup:
                source.rename(backup)
                done.append(f"Original kept as {backup.name}")
            elif source != target and not args.output:
                done.append(f"Rebuilt from {source.name}")
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.remove(temp)
        done.append(f"Wrote {target}")
    print_log(log)
    print("\n" + "\n".join(done))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_cmd = commands.add_parser("inspect", help="report the book's formatting problems and what fix would do")
    inspect_cmd.add_argument("book", type=Path)
    fix_cmd = commands.add_parser("fix", help="fix the book, keeping the original as '<name> (original).epub'")
    fix_cmd.add_argument("book", type=Path)
    fix_cmd.add_argument("--plan", type=Path, help=f"JSON with {', '.join(sorted(PLAN_KEYS))}; see SKILL.md")
    fix_cmd.add_argument("--skip", default="", help=f"comma-separated fixes to leave out: {', '.join(FIXES)}")
    fix_cmd.add_argument("--only", default="", help="comma-separated fixes to make, leaving out the rest")
    fix_cmd.add_argument("-o", "--output", type=Path, help="write here instead of fixing in place")
    fix_cmd.add_argument("--dry-run", action="store_true", help="report and verify without writing anything")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding.lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.command == "inspect":
        inspect(args.book)
    else:
        fix(args)


if __name__ == "__main__":
    main()
