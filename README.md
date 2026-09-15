# NPR News on a VT220

An unattended 80x24 NPR News bulletin for a real VT220 (or any terminal
emulator set to `vt220`). Stories rotate every 30 seconds and the feed
refreshes every 15 minutes. The display uses VT220 double-height headlines,
reverse video, underlining, DEC line drawing, and a hidden cursor.
`--width 132` switches the terminal into its wider column mode. Headline lines
are balanced and centered, including longer full-width titles. A scrolling
ticker previews upcoming headlines, and stories published in the last ten
minutes get a blinking BREAKING NEWS tag. It shows no web links.
Space or `N` skips to the next story; `P` goes back and `R` refreshes from
the VT220 keyboard for occasional manual control; `Q` exits only in local
preview mode.

## Getting started

Requires Python 3.9 or newer. For a local preview, no USB device or
dependency is needed:

```sh
python3 vt220_news.py --preview
```

To drive a physical terminal, install the one serial dependency and find the
adapter's port:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
ls /dev/cu.usbserial*
.venv/bin/python vt220_news.py --port /dev/cu.usbserial-XXXX
```

The preview needs an interactive terminal. Use `--dwell 20` to change the
rotation interval, repeat `--feed URL` to select custom RSS feeds, or use
`--baud` for a different line speed. The combined last-good bulletin is
cached at `~/.cache/vt220-news/feed.xml`; a network failure keeps those
stories on screen. A disconnected serial port is retried every 10 seconds.

## Running it automatically on macOS

After the adapter is connected and its `/dev/cu.*` name is known, edit the
absolute paths and port in `launchd/com.vt220.news.plist`. Copy it to
`~/Library/LaunchAgents/` and run:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vt220.news.plist
```

The agent starts at login and restarts after an unexpected exit. Keep the
Mac awake and connected to the internet while using the display. On Linux,
adapt the same idea with a systemd user service.

## Serial hardware

The VT220 EIA host connector is a **DB-25 RS-232 serial port**. The terminal
and computer adapter are both DTE, so the connection needs **null-modem**
wiring. The [StarTech 1P1FFCN-USB-SERIAL](https://www.startech.com/en-us/cards-adapters/1p1ffcn-usb-serial)
is an FTDI-based USB-A to DE-9-female RS-232 adapter with null-modem wiring
built in and macOS support. Add a **straight-through serial cable with a
DE-9-male end** for the adapter and a DB-25 end that mates with your VT220's
EIA host port. Check connector gender on your unit before ordering. A
USB-C-only computer also needs a USB-C to USB-A adapter. Do not buy a
USB-to-DB-25 *parallel printer* adapter or a TTL-level USB-UART board.

Set the VT220 to online mode, 9600 baud, 8 data bits, no parity, 1 stop bit,
and no hardware flow control. Match the app's `--baud` to the terminal. If
the screen stays blank, check the null-modem wiring and port name first.
Depending on the VT220's setup, modem-control signals may need to be looped
or crossed; a full-handshake null-modem cable is a useful choice.

The default feeds are NPR IDs 1001, 1002, and 1019. Stories from all three
are sorted newest first and deduplicated by article URL or headline before
rotation. Pass `--feed URL` (repeatable) to point at any other RSS feeds.

## Display design

The bulletin uses a reverse-video masthead showing the current time and, once
a fetch has succeeded, the last update time. A clear sentence break, colon,
or spaced dash can separate a longer RSS title into a large double-height
primary headline and a smaller centered subtitle; titles without a reliable
split stay complete in the full-width layout. A scrolling ticker previews
the other headlines above the footer, which is a reverse-video strip with a
seconds countdown, blinking ON AIR indicator, bulletin counter, and a
bracketed progress bar. The status line reads `>>> LIVE <<<` once a fetch has
succeeded, or explains why not otherwise. The graphics use the VT220's
built-in character set rather than DRCS soft-font art, which would need to
be loaded again after a terminal reset.
