import unittest
from datetime import datetime, timezone
from email.utils import format_datetime

from vt220_news import (FEED_URLS, Story, balanced_headline, cache_feed,
                        countdown_update, footer, headline_parts, is_breaking,
                        merge_feeds, parse_feed, screen, ticker, ticker_text)


SAMPLE = b"""<rss><channel><item>
<title>News &amp; updates</title><description><![CDATA[<p>A caf\xc3\xa9 story.</p>]]></description>
<pubDate>Tue, 15 Sep 2026 12:00:00 GMT</pubDate><link>https://example.com/1</link>
</item></channel></rss>"""

OVERLAP = b"""<rss><channel>
<item><title>News &amp; updates</title><description>Duplicate link</description>
<pubDate>Tue, 15 Sep 2026 13:00:00 GMT</pubDate>
<link>https://example.com/1?utm_source=feed</link></item>
<item><title>News &amp; updates</title><description>Duplicate title</description>
<pubDate>Tue, 15 Sep 2026 13:00:00 GMT</pubDate>
<link>https://example.com/other</link></item>
<item><title>New story</title><description>Fresh report</description>
<pubDate>Tue, 15 Sep 2026 14:00:00 GMT</pubDate>
<link>https://example.com/2</link></item>
</channel></rss>"""


class NewsTest(unittest.TestCase):
    def test_feed_text(self):
        story = parse_feed(SAMPLE)[0]
        self.assertEqual(story.title, "News & updates")
        self.assertEqual(story.summary, "A cafe story.")

    def test_multi_feed_merge_and_cache(self):
        self.assertEqual(len(FEED_URLS), 3)
        self.assertEqual(FEED_URLS[-1], "https://feeds.npr.org/1019/rss.xml")
        stories = merge_feeds((SAMPLE, OVERLAP, OVERLAP))
        self.assertEqual([story.title for story in stories],
                         ["New story", "News & updates"])
        self.assertEqual(parse_feed(cache_feed(stories)), stories)

    def test_vt220_screen_has_no_web_link(self):
        rendered = screen(parse_feed(SAMPLE), 0, "Ready")
        self.assertIn("\x1b#3", rendered)
        self.assertIn("\x1b#4", rendered)
        self.assertIn("\x1b[6;13H\x1b#3", rendered)
        self.assertIn("\x1b(0", rendered)
        self.assertNotIn("\x1b(0l", rendered)
        self.assertNotIn("\x1b(0x", rendered)
        self.assertIn("\x1b[24;1H\x1b[1;7m", rendered)
        self.assertIn("ON AIR", rendered)
        self.assertIn("BULLETIN 01 / 01", rendered)
        self.assertIn("\x1b[1;7m", rendered)
        self.assertIn("\x1b[24;3H\x1b[1;5;7mON AIR", rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("N next", rendered)

    def test_wide_column_mode(self):
        rendered = screen(parse_feed(SAMPLE), 0, "Ready", width=132)
        self.assertIn("\x1b[?3h", rendered)
        self.assertEqual(len(footer(parse_feed(SAMPLE), 0, 132)), 131)

    def test_footer_progress_and_offline_state(self):
        stories = parse_feed(SAMPLE) * 3
        first = footer(stories, 0, 80)
        middle = footer(stories, 1, 80)
        last = footer(stories, 2, 80)
        self.assertEqual(len(first), 79)
        self.assertIn("[#--------------------]", first)
        self.assertIn("[----------#----------]", middle)
        self.assertIn("[--------------------#]", last)
        self.assertIn("BULLETIN 03 / 03", last)
        self.assertIn("OFF AIR", footer([], 0, 80))
        self.assertIn("[---------------------]", footer([], 0, 80))
        self.assertNotIn("\x1b[1;5;7m", screen([], 0, "Waiting"))

    def test_countdown_and_small_update(self):
        rendered = screen(parse_feed(SAMPLE), 0, "Ready", remaining=65)
        self.assertIn("ON AIR              065s  [#", rendered)
        update = countdown_update(4, 80, 24)
        self.assertEqual(update, "\x1b[24;23H\x1b[1;7m004s\x1b[0m")
        self.assertNotIn("\x1b[2J", update)

    def test_live_status_gets_ascii_art(self):
        rendered = screen(parse_feed(SAMPLE), 0, "LIVE")
        self.assertIn("\x1b[22;1H\x1b[1m  >>> LIVE <<<\x1b[0m", rendered)
        self.assertNotIn(">>> LIVE <<<", screen(parse_feed(SAMPLE), 0, "Ready"))

    def test_updated_time_shown_in_header(self):
        rendered = screen(parse_feed(SAMPLE), 0, "Ready", updated="02:11 PM")
        self.assertIn("\x1b[1;1H\x1b[1;7m", rendered)
        self.assertIn("UPDATED 02:11 PM", rendered.split("\x1b[2;1H")[0])
        self.assertNotIn("UPDATED", screen(parse_feed(SAMPLE), 0, "Ready"))

    def test_long_headline_is_complete(self):
        title = ("Tiny krill are turning up in deep-sea hydrothermal vents. "
                 "Scientists want to understand how they survive there")
        story = Story(title, "A summary", "", "https://example.com/krill")
        rendered = screen([story], 0, "Ready")
        self.assertIn("\x1b#3", rendered)
        self.assertIn("\x1b#4", rendered)
        self.assertIn("SCIENTISTS WANT TO UNDERSTAND HOW THEY SURVIVE THERE", rendered)
        lines = balanced_headline(title.upper(), 75)
        self.assertEqual(" ".join(lines), title.upper())
        self.assertLessEqual(abs(len(lines[0]) - len(lines[1])), 5)
        self.assertEqual(headline_parts(title, 39),
                         ("Tiny krill are turning up in deep-sea hydrothermal vents.",
                          "Scientists want to understand how they survive there"))
        self.assertIn("A summary", rendered)

    def test_colon_subtitle_and_unsplit_title(self):
        self.assertEqual(headline_parts("A headline: This is the supporting subtitle", 39),
                         ("A headline", "This is the supporting subtitle"))
        title = ("A very long uninterrupted headline that has no safe subtitle separator "
                 "and continues with enough additional words to need smaller type")
        self.assertEqual(headline_parts(title, 39), (title, ""))
        rendered = screen([Story(title, "A summary", "", "")], 0, "Ready")
        self.assertIn("A VERY LONG UNINTERRUPTED HEADLINE", rendered)
        self.assertNotIn("\x1b#3", rendered)

    def test_title_abbreviation_not_treated_as_sentence_end(self):
        title = ("Rep. Smith calls for hearings after report on agency spending "
                 "raises new questions")
        self.assertEqual(headline_parts(title, 39), (title, ""))

    def test_breaking_tag_shown_for_fresh_story_only(self):
        fresh = format_datetime(datetime.now(timezone.utc))
        stale = "Tue, 15 Sep 2020 12:00:00 GMT"
        self.assertTrue(is_breaking(fresh))
        self.assertFalse(is_breaking(stale))
        self.assertFalse(is_breaking("not a date"))
        breaking_story = Story("Old story headline", "A summary", fresh, "")
        calm_story = Story("Old story headline", "A summary", stale, "")
        self.assertIn("BREAKING NEWS", screen([breaking_story], 0, "Ready"))
        self.assertNotIn("BREAKING NEWS", screen([calm_story], 0, "Ready"))

    def test_ticker_shows_other_headlines_and_scrolls(self):
        stories = [Story("First headline", "", "", ""),
                  Story("Second headline", "", "", ""),
                  Story("Third headline", "", "", "")]
        text = ticker_text(stories, 0)
        self.assertNotIn("FIRST HEADLINE", text)
        self.assertIn("SECOND HEADLINE", text)
        self.assertIn("THIRD HEADLINE", text)
        self.assertEqual(ticker_text([stories[0]], 0), "")
        start = ticker(stories, 0, 0, 80, 24)
        shifted = ticker(stories, 0, 1, 80, 24)
        self.assertNotEqual(start, shifted)
        self.assertIn("\x1b[23;1H", start)
        rendered = screen(stories, 0, "Ready")
        self.assertIn("UP NEXT:", rendered)
        self.assertNotIn("FIRST HEADLINE", rendered.split("UP NEXT:")[1])


if __name__ == "__main__":
    unittest.main()
