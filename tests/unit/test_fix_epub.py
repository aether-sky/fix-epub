"""Rejoining broken paragraphs, lines and words, plan rules, the CSS spacing and line-height fixes, the text
check, and TOC nesting."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import fix_epub
from fix_epub import Corpus, CssFixer, Doc, Log, Plan, Rule, join

EMPTY = Corpus([])
LONG = "He had been walking for most of the afternoon along the shore, and"  # a printed line of prose


def doc(body):
    return Doc.parse("c.xhtml", f"<html><head><title>t</title></head><body>\n{body}\n</body></html>")


def rejoined(body, corpus=EMPTY, plan=None):
    d = doc(body)
    fix_epub.rejoin_paragraphs(d, corpus, plan or Plan(), Log())
    return fix_epub.body_of(d.render()).strip()


def split_words_fixed(inner, corpus=EMPTY):
    d = doc(f"<p>{inner}</p>")
    fix_epub.join_split_words(d, corpus, Log())
    return d.paras[0].inner


def css_fixed(css, fixes=("spacing", "line-height"), body_classes=frozenset({"body"})):
    fixer = CssFixer(set(body_classes), set(fixes))
    fixer.survey(css)
    return fixer.fix(css, "style.css", Log())


class JoinTests(unittest.TestCase):
    def test_joins_a_sentence_broken_before_a_lowercase_word(self):
        self.assertEqual(join(LONG + " ", "nagging memory", EMPTY), LONG + " nagging memory")

    def test_keeps_a_break_after_a_finished_sentence(self):
        self.assertIsNone(join(LONG + " then he stopped.", "after that", EMPTY))

    def test_keeps_short_lines_apart_because_they_are_titles_verse_or_chat(self):
        self.assertIsNone(join("Sam: i can ask her tomorrow if you like", "me: sure, go ahead", EMPTY))

    def test_joins_before_a_capital_only_after_a_word_that_cannot_end_a_sentence(self):
        line = "I was offered a post at the observatory on the far side of the"
        self.assertEqual(join(line, "Moon, in the end.", EMPTY), line + " Moon, in the end.")
        line = "The village had been quiet all night and nobody heard them leave"
        self.assertIsNone(join(line, "Morning came grey and cold.", EMPTY))

    def test_joins_a_date_broken_after_the(self):
        line = "The balloon had left Richmond in the middle of a hurricane on the"
        self.assertEqual(join(line, "20th of March", EMPTY), line + " 20th of March")

    def test_join_capitals_joins_after_any_word(self):
        line = "The engineer was the first to see it; it was the man they called Cyrus"
        self.assertIsNone(join(line, "Harding who spoke", EMPTY))
        self.assertEqual(join(line, "Harding who spoke", EMPTY, capitals=True), line + " Harding who spoke")

    def test_joins_after_a_title_abbreviation(self):
        line = "The other astronomers became equally fond of her (it was old Dr."
        self.assertEqual(join(line, "Anderson who named her)", EMPTY), line + " Anderson who named her)")

    def test_joins_after_a_dash_without_adding_a_space(self):
        line = "We have been working on this project for the last three centuries--"
        self.assertEqual(join(line, "since the lamasery", EMPTY), line + "since the lamasery")

    def test_keeps_verse_lines_ending_in_a_comma_apart(self):
        line = "Good frend for Iesvs sake forbeare, to digg the dvst encloased heare,"
        self.assertIsNone(join(line, "Blest be ye man yt spares thes stones,", EMPTY))

    def test_never_joins_after_a_bold_credit_or_a_note_marker(self):
        self.assertIsNone(join(LONG + " <b>Chicago Daily News</b>", "and more", EMPTY))
        self.assertIsNone(join(LONG + '.<sup><a href="n.xhtml#3">3</a></sup>', "In fact", EMPTY))

    def test_never_joins_a_footnote(self):
        self.assertIsNone(join(LONG, '<a href="#fnj" id="rfnj">j</a> The original used', EMPTY))

    def test_keeps_glossary_and_chat_lines_apart_but_not_prose_before_a_colon(self):
        entry = "attention: manasikara, the careful attention given to what arises"
        self.assertIsNone(join(entry, "becoming: bhava, being", EMPTY))
        line = "Only philosophers were ever interested in what they called unanswerable"
        self.assertEqual(join(line, "questions: to most people", EMPTY), line + " questions: to most people")

    def test_never_joins_index_entries_that_end_in_page_numbers(self):
        entry = "anti-Bolshevism 72, 73, 77, 92-3, 150-53, 369-70, 403, 566, 598, 602-3, 714"
        self.assertIsNone(join(entry, "anti-capitalism 82, 92", EMPTY))

    def test_removes_a_soft_hyphen_at_a_line_end(self):
        self.assertEqual(join(LONG + " he was desper&shy;", "ately tired", EMPTY), LONG + " he was desperately tired")

    def test_keeps_the_hyphen_of_a_compound_broken_at_a_paragraph_end(self):
        line = "it was just possible he might survive this season of Icarus' four-"
        corpus = Corpus(["four of them", "four of us", "a hundred men", "one hundred"])
        self.assertEqual(join(line, "hundred-day year", corpus), line + "hundred-day year")

    def test_drops_the_hyphen_when_the_book_spells_the_word_whole(self):
        line = "Something about the way the creature moved its legs was deeply dis-"
        self.assertEqual(join(line, "turbing to him", Corpus(["a disturbing thought"])), line[:-1] + "turbing to him")


class ParagraphTests(unittest.TestCase):
    def test_merges_the_continuation_and_keeps_its_page_anchor(self):
        self.assertEqual(rejoined(f'<p class="t">{LONG}</p>\n<p class="t"><a id="p52"></a>then sat down.</p>'),
                         f'<p class="t">{LONG} <a id="p52"></a>then sat down.</p>')

    def test_merges_across_a_page_wrapper(self):
        out = rejoined(f'<div class="page"><p>{LONG}</p></div>\n<div class="page"><p>then sat down.</p></div>')
        self.assertIn(f"{LONG} then sat down.", out)

    def test_keeps_going_after_a_short_continuation(self):
        out = rejoined(f"<p>{LONG}</p>\n<p>then the</p>\n<p>sun set.</p>")
        self.assertEqual(out, f"<p>{LONG} then the sun set.</p>")

    def test_keeps_paragraphs_of_different_classes_apart(self):
        body = f'<p class="t">{LONG}</p>\n<p class="poem">then sat down.</p>'
        self.assertEqual(rejoined(body), body)

    def test_keeps_paragraphs_apart_across_a_rule_image_or_heading(self):
        for between in ("<hr/>", "<h2>Two</h2>"):
            body = f"<p>{LONG}</p>\n{between}\n<p>then sat down.</p>"
            self.assertEqual(rejoined(body), body)

    def test_keep_breaks_protects_a_paragraph_the_plan_names(self):
        body = f"<p>{LONG}</p>\n<p>then sat down.</p>"
        self.assertEqual(rejoined(body, plan=Plan(keep_breaks=[re.compile("^He had been walking")])), body)

    def test_logs_breaks_it_left_before_a_capital(self):
        d, log = doc(f"<p>{LONG} met</p>\n<p>Harding at the shore.</p>"), Log()
        fix_epub.rejoin_paragraphs(d, EMPTY, Plan(), log)
        self.assertEqual(len(log.of("left")), 1)

    def test_logs_how_a_hyphenated_word_came_back_together(self):
        d, log = doc(f"<p>{LONG} the opening bars of L'Apres-</p>\n<p>midi then</p>"), Log()
        fix_epub.rejoin_paragraphs(d, EMPTY, Plan(), log)
        self.assertIn("'Apresmidi'", log.of("word")[0][2])

    def test_joins_hard_wrapped_lines_inside_a_paragraph_but_keeps_finished_ones(self):
        d = doc(f"<p>{LONG}<br/>then sat down.<br/>Night fell.</p>")
        fix_epub.join_lines(d, EMPTY, Plan(), Log())
        self.assertEqual(d.paras[0].inner, f"{LONG} then sat down.<br/>Night fell.")

    def test_rejoins_only_files_that_show_conversion_damage(self):
        damaged = "\n".join(f"<p>{LONG}</p>\n<p>then sat down.</p>" for _ in range(3))
        intact = "\n".join(f"<p>{LONG}</p>\n<p>Then he sat down.</p>" for _ in range(3))
        self.assertEqual(fix_epub.count_breaks(doc(damaged), EMPTY, Plan()), (3, 5))
        self.assertEqual(fix_epub.count_breaks(doc(intact), EMPTY, Plan()), (0, 5))


class PlanRuleTests(unittest.TestCase):
    def test_drop_rule_with_nth_drops_only_those_occurrences(self):
        d = doc("<p>PART ONE</p>\n<p>x</p>\n<p>PART ONE</p>")
        fix_epub.drop_paragraphs([d], Plan(drop=[Rule(re.compile("PART ONE"), nth={1})]), Log())
        self.assertEqual([p.state for p in d.paras], ["dropped", "live", "live"])

    def test_drop_never_removes_an_image_paragraph(self):
        d = doc('<p><img src="a.jpg" alt=""/></p>\n<p></p>')
        fix_epub.drop_paragraphs([d], Plan(drop=[Rule(re.compile(""))]), Log())
        self.assertEqual([p.state for p in d.paras], ["live", "dropped"])

    def test_drop_removes_a_junk_heading(self):
        d = doc("<h2>Chapter 16</h2>\n<h3>The Mysterious Island</h3>\n<p>Text.</p>")
        fix_epub.drop_paragraphs([d], Plan(drop=[Rule(re.compile("The Mysterious Island"))]), Log())
        self.assertNotIn("<h3", d.render())

    def test_remove_cuts_a_page_marker_glued_to_a_line_so_it_can_rejoin(self):
        d = doc(f'<p>{LONG} the <b class="c3">Page 5</b></p>\n<p>sun set.</p>')
        plan = Plan(remove=[re.compile(r'\s*<b class="c3">Page \d+</b>')])
        fix_epub.remove_text([d], plan, Log())
        fix_epub.rejoin_paragraphs(d, EMPTY, plan, Log())
        self.assertIn(f"{LONG} the sun set.", d.render())
        self.assertEqual(fix_epub.visible_text(d.render()), d.expected_text(plan))

    def test_remove_drops_a_paragraph_it_empties(self):
        d = doc("<p>The Mysterious Island</p>\n<p>Text.</p>")
        fix_epub.remove_text([d], Plan(remove=[re.compile("The Mysterious Island")]), Log())
        self.assertEqual(d.paras[0].state, "dropped")

    def test_heading_rule_relevels_an_existing_heading(self):
        d = doc("<h3>Part One</h3>\n<p>Text.</p>")
        fix_epub.mark_headings([d], Plan(headings=[Rule(re.compile("Part One"), level=1)]), {}, Log())
        self.assertIn("<h1>Part One</h1>", d.render())

    def test_text_check_applies_replacements_to_paragraphs_not_headings(self):
        d = doc("<h3>The Island</h3>\n<p>left his The Island raft.</p>")
        plan = Plan(replace=[(" The Island", "")])
        fix_epub.replace_text([d], plan, Log())
        self.assertIn("<h3>The Island</h3>", d.render())
        self.assertEqual(fix_epub.visible_text(d.render()), d.expected_text(plan))


class SplitWordTests(unittest.TestCase):
    def test_rejoins_a_word_split_at_a_line_end(self):
        self.assertEqual(split_words_fixed("the me- chanical limbs"), "the mechanical limbs")

    def test_keeps_a_suspended_hyphen(self):
        self.assertEqual(split_words_fixed("nineteenth- and twentieth-century"), "nineteenth- and twentieth-century")

    def test_keeps_a_hyphen_used_as_a_dash_before_a_small_word(self):
        self.assertEqual(split_words_fixed("more than- the rest"), "more than- the rest")

    def test_keeps_a_compound_whose_parts_are_both_words_in_the_book(self):
        corpus = Corpus(["competent people", "competent work", "looking up", "looking down"])
        self.assertEqual(split_words_fixed("a competent-\n   looking man", corpus), "a competent-looking man")

    def test_follows_the_spelling_the_book_uses_more_often(self):
        corpus = Corpus(["a gentleman", "the gentleman", "one gentle-man"])
        self.assertEqual(split_words_fixed("the gentle- man", corpus), "the gentleman")

    def test_leaves_attribute_values_alone(self):
        self.assertEqual(split_words_fixed('<a title="well- known">x</a>'), '<a title="well- known">x</a>')


class SpacerTests(unittest.TestCase):
    def test_removes_empty_paragraphs_that_sit_between_most_paragraphs(self):
        d = doc("\n".join("<p>Text.</p>\n<p>&nbsp;</p>" for _ in range(5)))
        fix_epub.drop_spacers([d], Log())
        self.assertNotIn("&nbsp;", d.render())

    def test_keeps_a_rare_empty_paragraph_as_a_scene_break(self):
        d = doc("\n".join(["<p>Text.</p>"] * 10 + ["<p>&nbsp;</p>"]))
        fix_epub.drop_spacers([d], Log())
        self.assertIn("&nbsp;", d.render())


class CssTests(unittest.TestCase):
    def test_removes_the_gap_and_adds_an_indent_in_the_rules_own_format(self):
        self.assertEqual(css_fixed(".body {\n    display: block;\n    margin: 1em 0\n    }"),
                         ".body {\n    display: block;\n    margin: 0;\n    text-indent: 1.5em\n    }")

    def test_keeps_side_margins(self):
        self.assertIn("margin: 0 2em", css_fixed(".body { margin: 1em 2em; text-indent: 1em }"))

    def test_keeps_an_existing_indent(self):
        self.assertEqual(css_fixed("p { margin-top: 1em; text-indent: 2em; }").count("text-indent"), 1)

    def test_leaves_other_classes_and_scene_break_rules_alone(self):
        for css in (".epigraph { margin: 1em 3em }", "p.textbrk + p { margin-top: 2em }",
                    "blockquote p { margin: 1em }"):
            self.assertEqual(css_fixed(css), css)

    def test_removes_line_height_from_blocks_of_text(self):
        out = css_fixed("h1 { font-size: 2em; line-height: 1.2 }\n.body {\n\tline-height: 1.28em;\n\tcolor: red;\n}")
        self.assertNotIn("line-height", out)
        self.assertIn("color: red", out)

    def test_keeps_line_height_on_superscripts_and_other_inline_text(self):
        for css in ("span.epub-sup { vertical-align: super; font-size: 65%; line-height: 0.50em; }",
                    "span.tx-num { line-height: 0.65em; }", ".fn { vertical-align: sub; line-height: 0 }"):
            self.assertEqual(css_fixed(css), css)

    def test_justifies_body_text_that_had_no_alignment(self):
        self.assertEqual(css_fixed(".body {\n    margin: 0;\n    }", ["justify"]),
                         ".body {\n    margin: 0;\n    text-align: justify;\n    }")

    def test_turns_left_alignment_of_body_text_into_justify(self):
        self.assertEqual(css_fixed(".body { text-align: left !important; text-justify: none }", ["justify"]),
                         ".body { text-align: justify !important; }")

    def test_leaves_centred_titles_and_other_classes_alone_when_justifying(self):
        for css in (".body { text-align: center }", ".title { text-align: left }"):
            self.assertEqual(css_fixed(css, ["justify"]), css)

    def test_removes_inline_left_alignment_from_body_paragraphs_only(self):
        d = doc('<p class="body" style="text-align: left; color: red">a</p>\n<p class="body" align="left">b</p>\n'
                '<p class="title" style="text-align: left">c</p>')
        fix_epub.clear_left_alignment([d], {"body"}, Log())
        self.assertEqual([p.attrs for p in d.paras],
                         [' class="body" style="color: red"', ' class="body"', ' class="title" style="text-align: left"'])

    def test_reads_a_rule_after_a_charset_statement(self):
        self.assertIn("margin: 0", css_fixed('@charset "utf-8";\n.body { margin: 1em 0; text-indent: 1em }'))


class TocTests(unittest.TestCase):
    def test_nests_chapters_under_parts(self):
        entries = [(0, -1, 1, "Cover", "cover.xhtml"), (1, 0, 1, "Part One", "a.xhtml#h1"),
                   (1, 50, 2, "Chapter 1", "a.xhtml#h2"), (2, 0, 2, "Chapter 2", "b.xhtml#h3")]
        tree = fix_epub.nest(entries)
        self.assertEqual([label for label, _, _ in tree], ["Cover", "Part One"])
        self.assertEqual([label for label, _, _ in tree[1][2]], ["Chapter 1", "Chapter 2"])

    def test_keeps_chapters_before_the_first_part_at_the_top(self):
        entries = [(0, -1, 1, "Cover", "cover.xhtml"), (1, 0, 2, "Chapter 1", "a.xhtml#h1"),
                   (1, 9, 2, "Chapter 2", "a.xhtml#h2"), (2, 0, 1, "Part Two", "b.xhtml#h3")]
        self.assertEqual([label for label, _, _ in fix_epub.nest(entries)],
                         ["Cover", "Chapter 1", "Chapter 2", "Part Two"])


if __name__ == "__main__":
    unittest.main()
