#!/usr/bin/env python3
"""Unattended NPR bulletin for a VT220 serial terminal."""

import argparse
import html
import math
import re
import sys
import time
import unicodedata
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from textwrap import wrap


FEED_URLS = tuple(f"https://feeds.npr.org/{feed}/rss.xml"
                  for feed in (1001, 1002, 1019))
ESC = "\x1b"


@dataclass(frozen=True)
class Story:
    title: str
    summary: str
    date: str
    link: str


def plain_text(value):
    value = html.unescape(re.sub(r"<[^>]*>", " ", value or ""))
    value = unicodedata.normalize("NFKD", value)
    return " ".join(value.encode("ascii", "ignore").decode("ascii").split())


def parse_feed(data):
    root = ET.fromstring(data)
    stories = []
    for item in root.findall("./channel/item"):
        title = plain_text(item.findtext("title", default=""))
        if title:
            stories.append(Story(title,
                                 plain_text(item.findtext("description", default="")),
                                 plain_text(item.findtext("pubDate", default="")),
                                 item.findtext("link", default="").strip()))
    return stories


def fetch_feed(url):
    request = urllib.request.Request(url, headers={"User-Agent": "vt220-news/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


def story_order(story):
    try:
        return parsedate_to_datetime(story.date).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


def merge_feeds(feeds):
    stories = []
    seen_links = set()
    seen_titles = set()
    for data in feeds:
        for story in parse_feed(data):
            parts = urlsplit(story.link)
            link = (parts.netloc.lower() + parts.path.rstrip("/")) if parts.netloc else ""
            title = story.title.casefold()
            if (link and link in seen_links) or title in seen_titles:
                continue
            stories.append(story)
            if link:
                seen_links.add(link)
            seen_titles.add(title)
    return sorted(stories, key=story_order, reverse=True)


def cache_feed(stories):
    root = ET.Element("rss")
    channel = ET.SubElement(root, "channel")
    for story in stories:
        item = ET.SubElement(channel, "item")
        for tag, value in (("title", story.title), ("description", story.summary),
                           ("pubDate", story.date), ("link", story.link)):
            ET.SubElement(item, tag).text = value
    return ET.tostring(root, encoding="utf-8")


def published(value):
    try:
        return parsedate_to_datetime(value).astimezone().strftime("%b %d  %I:%M %p")
    except (TypeError, ValueError, OverflowError):
        return value[:35]


BREAKING_WINDOW = timedelta(minutes=10)


def is_breaking(date, now=None):
    try:
        published_at = parsedate_to_datetime(date)
    except (TypeError, ValueError, OverflowError):
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age = (now or datetime.now(timezone.utc)) - published_at
    return timedelta(0) <= age <= BREAKING_WINDOW


def put(row, text, attribute="", line_attribute="", column=1):
    return f"{ESC}[{row};{column}H" + line_attribute + attribute + text + ESC + "[0m"


def rule(row, columns):
    return f"{ESC}[{row};1H" + ESC + "(0" + "q" * columns + ESC + "(B"


def countdown_field(remaining):
    return f"{remaining:03d}s"


def countdown_column(width):
    columns = width - 1
    bar_width = 21 if width == 80 else 33
    bar_start = (columns - (bar_width + 2)) // 2
    return bar_start - 6


def countdown_update(remaining, width, height):
    column = countdown_column(width) + 1
    return put(height, countdown_field(remaining), ESC + "[1;7m", column=column)


def breaking_tag(row, width):
    text = " BREAKING NEWS "
    column = (width - len(text)) // 2 + 1
    return put(row, text, ESC + "[1;5;7m", column=column)


def ticker_text(stories, index):
    others = stories[:index] + stories[index + 1:]
    if not others:
        return ""
    return ("UP NEXT:   " +
            "   ***   ".join(story.title.upper() for story in others) +
            "   ***   ")


def ticker(stories, index, offset, width, height):
    columns = width - 1
    row = height - 1
    text = ticker_text(stories, index)
    if not text:
        return put(row, " " * columns)
    doubled = text + text
    start = offset % len(text)
    return put(row, doubled[start:start + columns])


def ticker_update(stories, index, offset, width, height):
    return ticker(stories, index, offset, width, height)


def balanced_headline(text, width):
    count = len(wrap(text, width, break_long_words=True,
                     break_on_hyphens=False))
    words = []
    for word in text.split():
        words.extend(wrap(word, width, break_long_words=True,
                          break_on_hyphens=False))
    target = (sum(len(word) for word in words) + len(words) - count) / count

    @lru_cache(None)
    def arrange(start, lines_left):
        if lines_left == 0:
            return (0, ()) if start == len(words) else None
        best = None
        for end in range(start + 1, len(words) - lines_left + 2):
            line = " ".join(words[start:end])
            if len(line) > width:
                break
            rest = arrange(end, lines_left - 1)
            if rest is None:
                continue
            candidate = ((len(line) - target) ** 2 + rest[0],
                         (line,) + rest[1])
            if best is None or candidate[0] < best[0]:
                best = candidate
        return best

    result = arrange(0, count)
    return list(result[1]) if result else wrap(text, width)


def headline_parts(title, large_width):
    for match in re.finditer(r"(?<=[.!?])\s+|:\s+|\s+-\s+", title):
        primary = title[:match.start()].rstrip(" :-")
        subtitle = title[match.end():].strip()
        if (len(primary) >= 8 and len(subtitle) >= 10 and
                not re.search(r"\b(?:Dr|Mr|Mrs|Ms|St|Rep|Sen|Gov|Gen|Lt|Col|Jr|Sr"
                              r"|U\.S|U\.K)\.$", primary, re.IGNORECASE) and
                len(balanced_headline(primary.upper(), large_width)) <= 2):
            return primary, subtitle
    return title, ""


def footer(stories, index, width, remaining=None):
    columns = width - 1
    label = "ON AIR" if stories else "OFF AIR"
    counter = f"BULLETIN {index + 1:02d} / {len(stories):02d}" if stories else "BULLETIN 00 / 00"
    bar_width = 21 if width == 80 else 33
    if stories:
        marker = round(index * (bar_width - 1) / max(1, len(stories) - 1))
        bar = "[" + "-" * marker + "#" + "-" * (bar_width - marker - 1) + "]"
    else:
        bar = "[" + "-" * bar_width + "]"
    line = [" "] * columns
    positions = [(2, label), ((columns - len(bar)) // 2, bar),
                (columns - len(counter) - 2, counter)]
    if remaining is not None:
        positions.append((countdown_column(width), countdown_field(remaining)))
    for start, value in positions:
        line[start:start + len(value)] = value
    return "".join(line)


def screen(stories, index, status, width=80, height=24, remaining=None, ticker_offset=0,
          updated=None):
    columns = width - 1
    headline_columns = width // 2 - 1
    now = datetime.now().strftime("%a %b %d  %I:%M %p")
    if updated:
        now += "   UPDATED " + updated
    masthead = "  NPR / NEWS  "
    column_mode = ESC + ("[?3h" if width == 132 else "[?3l")
    output = [ESC + "[0m" + ESC + "[?25l" + column_mode +
              ESC + "[2J" + ESC + "[H",
              put(1, masthead + " " * max(1, columns - len(masthead) - len(now)) + now,
                  ESC + "[1;7m"), rule(2, columns)]
    if stories:
        story = stories[index]
        if is_breaking(story.date):
            output.append(breaking_tag(4, width))
        primary, subtitle = headline_parts(story.title, headline_columns)
        large_headline = balanced_headline(primary.upper(), headline_columns)
        if len(large_headline) <= 2:
            for position, text in enumerate(large_headline):
                row = 6 + position * 2
                column = (headline_columns - len(text)) // 2 + 1
                output.extend((put(row, text, ESC + "[1m", ESC + "#3", column),
                               put(row + 1, text, ESC + "[1m", ESC + "#4", column)))
            title_end = 5 + len(large_headline) * 2
        else:
            full_headline = balanced_headline(story.title.upper(), columns - 4)
            for position, text in enumerate(full_headline):
                column = (width - len(text)) // 2 + 1
                output.append(put(6 + position, text, ESC + "[1m", column=column))
            title_end = 5 + len(full_headline)
        if subtitle:
            subtitle_lines = balanced_headline(subtitle.upper(), columns - 4)
            for position, text in enumerate(subtitle_lines):
                column = (width - len(text)) // 2 + 1
                output.append(put(title_end + 2 + position, text, column=column))
            title_end += 1 + len(subtitle_lines)
        divider = max(11, title_end + 2)
        output.extend((rule(divider, columns),
                       put(divider + 2, "  " + published(story.date), ESC + "[4m")))
        summary = story.summary or "No summary supplied with this story."
        for offset, text in enumerate(wrap(summary, columns - 4,
                                           break_long_words=True)[:max(0, height - divider - 8)]):
            output.append(put(divider + 4 + offset, "  " + text))
    else:
        output.extend((put(6, "AWAITING NPR NEWS", ESC + "[1m", ESC + "#3"),
                       put(7, "AWAITING NPR NEWS", ESC + "[1m", ESC + "#4")))
    if stories:
        output.append(ticker(stories, index, ticker_offset, width, height))
    if status == "LIVE":
        status_row = put(height - 2, "  >>> LIVE <<<", ESC + "[1m")
    else:
        status_row = put(height - 2, "  " + status[:columns - 2])
    output.extend((rule(height - 3, columns), status_row,
                   put(height, footer(stories, index, width,
                                     remaining if stories else None), ESC + "[1;7m")))
    if stories:
        output.append(put(height, "ON AIR", ESC + "[1;5;7m", column=3))
    return "".join(output)


def open_serial(port, baud):
    import serial
    return serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                         timeout=0.2, write_timeout=5, xonxoff=False,
                         rtscts=False, dsrdtr=False)


def run(args):
    import select
    import termios
    import tty

    input_fd = sys.stdin.fileno() if args.preview else None
    old_mode = termios.tcgetattr(input_fd) if args.preview else None
    if args.preview:
        tty.setcbreak(input_fd)
    device = None
    stories = []
    index = 0
    status = "WAITING FOR NPR FEED"
    updated = None
    next_fetch = next_connect = 0
    next_story = time.monotonic() + args.dwell
    dirty = True
    shown_countdown = None
    ticker_offset = 0
    next_ticker = 0
    try:
        stories = parse_feed(args.cache.read_bytes())
        status = "SHOWING SAVED BULLETIN"
    except (OSError, ET.ParseError):
        pass

    try:
        while True:
            now = time.monotonic()
            if not args.preview and device is None and now >= next_connect:
                try:
                    device = open_serial(args.port, args.baud)
                    dirty = True
                except (OSError, ImportError) as error:
                    print(f"Serial unavailable: {error}", file=sys.stderr)
                    next_connect = now + 10
            if now >= next_fetch:
                try:
                    fresh = merge_feeds(fetch_feed(url) for url in args.feed)
                    if not fresh:
                        raise ValueError("feed contains no stories")
                    current = stories[index].link if stories else None
                    stories = fresh
                    index = next((i for i, story in enumerate(stories)
                                  if story.link == current), 0)
                    ticker_offset = 0
                    status = "LIVE"
                    updated = datetime.now().strftime("%I:%M %p")
                    try:
                        args.cache.parent.mkdir(parents=True, exist_ok=True)
                        args.cache.write_bytes(cache_feed(stories))
                    except OSError as error:
                        print(f"Cache unavailable: {error}", file=sys.stderr)
                except (OSError, ET.ParseError, ValueError) as error:
                    status = "NPR FEED UNAVAILABLE  /  RETRYING"
                    print(f"Feed unavailable: {error}", file=sys.stderr)
                next_fetch = now + args.refresh * 60
                dirty = True
            if now >= next_story and stories:
                index = (index + 1) % len(stories)
                next_story = now + args.dwell
                ticker_offset = 0
                dirty = True
            full_redraw = False
            if dirty and (args.preview or device is not None):
                try:
                    remaining = max(0, math.ceil(next_story - time.monotonic()))
                    frame = screen(stories, index, status, args.width, args.height,
                                   remaining if stories else None, ticker_offset, updated)
                    if args.preview:
                        sys.stdout.write(frame)
                        sys.stdout.flush()
                    else:
                        device.write(frame.encode("ascii"))
                    dirty = False
                    full_redraw = True
                    shown_countdown = remaining if stories else None
                except OSError as error:
                    print(f"Serial lost: {error}", file=sys.stderr)
                    device.close()
                    device = None
                    next_connect = now + 10
            if not dirty and stories and (args.preview or device is not None):
                remaining = max(0, math.ceil(next_story - time.monotonic()))
                if remaining != shown_countdown:
                    try:
                        update = countdown_update(remaining, args.width, args.height)
                        if args.preview:
                            sys.stdout.write(update)
                            sys.stdout.flush()
                        else:
                            device.write(update.encode("ascii"))
                        shown_countdown = remaining
                    except OSError as error:
                        print(f"Serial lost: {error}", file=sys.stderr)
                        device.close()
                        device = None
                        next_connect = now + 10
                        dirty = True
            if stories and now >= next_ticker and (args.preview or device is not None):
                length = len(ticker_text(stories, index))
                if length:
                    ticker_offset = (ticker_offset + 1) % length
                    next_ticker = now + 0.4
                    if not full_redraw:
                        try:
                            update = ticker_update(stories, index, ticker_offset,
                                                   args.width, args.height)
                            if args.preview:
                                sys.stdout.write(update)
                                sys.stdout.flush()
                            else:
                                device.write(update.encode("ascii"))
                        except OSError as error:
                            print(f"Serial lost: {error}", file=sys.stderr)
                            device.close()
                            device = None
                            next_connect = now + 10
                            dirty = True
            key = ""
            if args.preview:
                ready, _, _ = select.select([input_fd], [], [], 0.2)
                if ready:
                    key = sys.stdin.read(1).lower()
            elif device is not None:
                try:
                    key = device.read(1).decode("ascii", "ignore").lower()
                except OSError:
                    device.close()
                    device = None
                    next_connect = now + 10
                    dirty = True
            else:
                time.sleep(0.2)
            if key == "q" and args.preview:
                break
            if key == "r":
                next_fetch = 0
            if key in ("n", "p", " ") and stories:
                index = (index + (1 if key in ("n", " ") else -1)) % len(stories)
                next_story = time.monotonic() + args.dwell
                dirty = True
    finally:
        if args.preview:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_mode)
            sys.stdout.write(ESC + "[0m" + ESC + "[?25h" + "\r\n")
        elif device is not None:
            device.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="macOS port, e.g. /dev/cu.usbserial-XXXX")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--feed", action="append", help="RSS URL; repeat for multiple feeds")
    parser.add_argument("--refresh", type=int, default=15, metavar="MINUTES")
    parser.add_argument("--dwell", type=int, default=30, metavar="SECONDS")
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--height", type=int, default=24)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--cache", type=Path,
                        default=Path.home() / ".cache/vt220-news/feed.xml")
    args = parser.parse_args()
    args.feed = args.feed or FEED_URLS
    if not args.preview and not args.port:
        parser.error("specify --port or --preview")
    if args.refresh < 1 or args.dwell < 5 or args.width not in (80, 132) or args.height < 20:
        parser.error("refresh >= 1, dwell >= 5, width = 80 or 132, height >= 20 required")
    try:
        run(args)
    except KeyboardInterrupt:
        pass
    except (OSError, ImportError) as error:
        parser.exit(1, f"Cannot start: {error}\n")


if __name__ == "__main__":
    main()
