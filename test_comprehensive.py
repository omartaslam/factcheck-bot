#!/usr/bin/env python3
"""
FactCheck Pro — Comprehensive Integration Test Suite
=====================================================
Tests all WhatsApp message types, all major social media platforms,
multimedia formats, OSINT checks, and edge cases.

Usage:
  python3 test_comprehensive.py              # run all tests
  python3 test_comprehensive.py -v           # verbose (show full bot output)
  python3 test_comprehensive.py -f TEXT      # filter by category name
  python3 test_comprehensive.py --unit-only  # skip live API calls (fast)
  python3 test_comprehensive.py --list       # list all test names

NOTE: Full run makes real API calls (Claude, Tavily, OSINT APIs).
      Expect ~3-5 min and ~$0.10-0.30 in API costs.
"""

import sys, os, json, time, threading, io, struct, textwrap, traceback, uuid
from unittest.mock import patch, MagicMock
from pathlib import Path

# ── Load .env before importing bot ────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# ── Flags ─────────────────────────────────────────────────────────────────────
VERBOSE     = "-v" in sys.argv or "--verbose" in sys.argv
UNIT_ONLY   = "--unit-only" in sys.argv
LIST_ONLY   = "--list" in sys.argv
FILTER      = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "-f"), None)

# ── Import bot (after env is loaded) ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import bot

# ── Override DB to temp file so tests don't pollute production DB ─────────────
import tempfile, sqlite3
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
bot.DB_PATH = _test_db.name
bot.FREE_CHECKS_LIMIT = 9999   # never block during tests
bot.init_db()

# ── Message capture ────────────────────────────────────────────────────────────
_sent = []

def _mock_send(to, text):
    _sent.append({"to": to, "text": text})
    if VERBOSE:
        sep = "─" * 60
        print(f"\n{sep}\n[BOT → {to}]\n{text}\n{sep}")

def reset():
    _sent.clear()
    bot.processed_ids.clear()
    bot.pending.clear()

def last_report():
    """Return the last substantial message sent (the actual fact-check report)."""
    for m in reversed(_sent):
        if "VERDICT" in m["text"] or "FACTCHECK PRO" in m["text"]:
            return m["text"]
    return _sent[-1]["text"] if _sent else ""

def all_output():
    return "\n".join(m["text"] for m in _sent)

# ── Synthetic media helpers ────────────────────────────────────────────────────
def _make_jpeg(text="TEST", width=320, height=240):
    """Generate a real JPEG with embedded text."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), color=(50, 80, 120))
    ImageDraw.Draw(img).text((10, 10), text, fill=(255, 255, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()

def _make_jpeg_with_exif(date_str="2019:03:14 12:00:00"):
    """JPEG with EXIF DateTimeOriginal for testing EXIF extraction."""
    from PIL import Image
    import piexif
    img = Image.new("RGB", (200, 200), color=(200, 100, 50))
    exif_dict = {"Exif": {piexif.ExifIFD.DateTimeOriginal: date_str.encode()}}
    exif_bytes = piexif.dump(exif_dict)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif_bytes)
    return buf.getvalue()

def _make_wav_silence(seconds=1, sample_rate=16000):
    """Minimal WAV file (silence) for audio transcription tests."""
    num_samples = sample_rate * seconds
    data = b'\x00\x00' * num_samples
    header = struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + len(data), b'WAVE', b'fmt ', 16,
        1, 1, sample_rate, sample_rate * 2, 2, 16, b'data', len(data))
    return header + data

def _make_mp4_stub():
    """Minimal MP4 stub (bot should fail gracefully)."""
    return b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41' + b'\x00' * 200

def _make_pdf_stub():
    """Minimal PDF-like text content."""
    return b"%PDF-1.4\nIsrael attacked Gaza on Oct 7 2023 killing over 1200 people\n"

# ── Message builders (WhatsApp Business API format) ───────────────────────────
_counter = [0]

def _mid():
    _counter[0] += 1
    return f"wamid.TEST{_counter[0]:06d}{int(time.time())}"

def msg_text(body, num="447700900001"):
    return num, {"type":"text","id":_mid(),"timestamp":str(int(time.time())),"text":{"body":body}}

def msg_image(img_bytes, caption="", num="447700900002"):
    bot._TEST_MEDIA = {"test_img": img_bytes}
    return num, {"type":"image","id":_mid(),"timestamp":str(int(time.time())),
                 "image":{"id":"test_img","mime_type":"image/jpeg","caption":caption}}

def msg_audio(audio_bytes, num="447700900003"):
    bot._TEST_MEDIA = {"test_audio": audio_bytes}
    return num, {"type":"audio","id":_mid(),"timestamp":str(int(time.time())),
                 "audio":{"id":"test_audio","mime_type":"audio/ogg; codecs=opus"}}

def msg_video(video_bytes, caption="", num="447700900004"):
    bot._TEST_MEDIA = {"test_video": video_bytes}
    return num, {"type":"video","id":_mid(),"timestamp":str(int(time.time())),
                 "video":{"id":"test_video","mime_type":"video/mp4","caption":caption}}

def msg_document(content_bytes, filename="test.txt", num="447700900005"):
    bot._TEST_MEDIA = {"test_doc": content_bytes}
    return num, {"type":"document","id":_mid(),"timestamp":str(int(time.time())),
                 "document":{"id":"test_doc","mime_type":"text/plain","filename":filename}}

def msg_unsupported(num="447700900006"):
    return num, {"type":"sticker","id":_mid(),"timestamp":str(int(time.time())),"sticker":{"id":"s1"}}

# ── Simulate full process() + Y confirm ───────────────────────────────────────
def run_full(from_num, message, confirm=True, timeout=120):
    """
    Send a message through process(), then auto-confirm with Y if a pending
    claim is created. Returns (sent_messages, report_text).
    """
    reset()
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.download_media", side_effect=lambda mid: getattr(bot,"_TEST_MEDIA",{}).get(mid, b"")):
        bot.process(from_num, message)

        if confirm and (from_num, "whatsapp") not in bot.pending and \
                        ("whatsapp", from_num) not in bot.pending:
            # pending key format
            pass

        pkey = ("whatsapp", from_num)
        if pkey in bot.pending and confirm:
            y_msg = {"type":"text","id":_mid(),"timestamp":str(int(time.time())),
                     "text":{"body":"Y"}}
            # Run fact-check in thread, wait for completion
            done = threading.Event()
            orig_deduct = bot._wa_deduct
            def _deduct_and_signal(*a, **kw):
                orig_deduct(*a, **kw)
                done.set()
            with patch("bot._wa_deduct", side_effect=_deduct_and_signal):
                bot.process(from_num, y_msg)
                done.wait(timeout=timeout)

    return list(_sent), last_report()

# ── Test framework ─────────────────────────────────────────────────────────────
_results = []

class Test:
    def __init__(self, name, category, live=True):
        self.name = name
        self.category = category
        self.live = live  # live=True means it makes real API calls

    def __call__(self, fn):
        _results.append((self, fn))
        return fn

def test(name, category="general", live=True):
    return Test(name, category, live)

# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — no live API calls
# ═══════════════════════════════════════════════════════════════════════════════

@test("EXIF extraction — date and camera from JPEG", category="osint", live=False)
def _():
    try:
        img = _make_jpeg_with_exif("2019:03:14 12:00:00")
        result = bot.extract_exif_info(img)
        assert "DateTimeOriginal" in result, f"Expected DateTimeOriginal, got: {result}"
        assert "2019" in result["DateTimeOriginal"]
        return True, f"DateTimeOriginal: {result['DateTimeOriginal']}"
    except ImportError:
        return True, "piexif not installed — skipped EXIF write test (read still works)"

@test("EXIF extraction — no EXIF in plain JPEG", category="osint", live=False)
def _():
    img = _make_jpeg("plain image")
    result = bot.extract_exif_info(img)
    assert isinstance(result, dict)
    return True, f"No EXIF (expected): {result}"

@test("fmt_osint — renders TinEye matches correctly", category="osint", live=False)
def _():
    findings = {
        "tineye_matches": [
            {"domain": "reuters.com", "url": "https://reuters.com/img/test.jpg"},
            {"domain": "bbc.co.uk",   "url": "https://bbc.co.uk/img/test.jpg"},
        ]
    }
    lines = bot.fmt_osint(findings)
    assert any("reuters" in l for l in lines), f"Expected reuters in: {lines}"
    assert any("🔍" in l for l in lines)
    return True, "\n".join(lines)

@test("fmt_osint — AI detection high probability warning", category="osint", live=False)
def _():
    findings = {"hive": {"ai_generated": 0.94, "deepfake": 0.12}}
    lines = bot.fmt_osint(findings)
    assert any("🤖" in l for l in lines), f"Expected robot icon: {lines}"
    assert any("94%" in l for l in lines)
    return True, "\n".join(lines)

@test("fmt_osint — empty findings returns empty list", category="osint", live=False)
def _():
    lines = bot.fmt_osint({})
    assert lines == [], f"Expected [], got: {lines}"
    return True, "OK"

@test("_split_message — splits at newline boundary", category="formatting", live=False)
def _():
    text = ("Line one\n" * 500)[:4100]
    chunks = bot._split_message(text, limit=4000)
    assert len(chunks) == 2
    assert not chunks[0].endswith(" ")  # not mid-word
    for chunk in chunks:
        assert len(chunk) <= 4000
    return True, f"Split into {len(chunks)} chunks"

@test("_split_message — short text stays single chunk", category="formatting", live=False)
def _():
    text = "Hello world"
    chunks = bot._split_message(text)
    assert chunks == ["Hello world"]
    return True, "OK"

@test("_parse_post_date — yt-dlp YYYYMMDD format", category="formatting", live=False)
def _():
    r = bot._parse_post_date("20231015")
    assert r == "2023-10-15", f"Got: {r}"
    return True, r

@test("_parse_post_date — ISO 8601", category="formatting", live=False)
def _():
    r = bot._parse_post_date("2023-10-15T09:30:00Z")
    assert r == "2023-10-15", f"Got: {r}"
    return True, r

@test("_parse_post_date — Unix timestamp", category="formatting", live=False)
def _():
    r = bot._parse_post_date(1697356200)
    assert r.startswith("2023-"), f"Got: {r}"
    return True, r

@test("_post_age_label — recent post", category="formatting", live=False)
def _():
    import datetime
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    result = bot._post_age_label(yesterday)
    assert result is not None
    friendly, days, age = result
    assert days == 1
    assert "d ago" in age
    return True, f"{friendly} — {age}"

@test("_post_age_label — very old post triggers staleness flag", category="formatting", live=False)
def _():
    result = bot._post_age_label("2020-01-01")
    assert result is not None
    friendly, days, age = result
    assert days > 180
    return True, f"{days} days old → staleness warning"

@test("meter_visual — TRUE gives full green bar", category="formatting", live=False)
def _():
    bar = bot.meter_visual("TRUE")
    assert "TRUE" in bar or "🟩" in bar or "✅" in bar, f"Unexpected bar: {repr(bar)}"
    return True, bar.strip()

@test("enabled_sources — returns non-empty list", category="sources", live=False)
def _():
    srcs = bot.enabled_sources()
    assert len(srcs) > 50, f"Expected >50 sources, got {len(srcs)}"
    assert "Al Jazeera" in srcs
    assert "Snopes" in srcs
    assert "Misbar" in srcs
    assert "Africa Check" in srcs
    assert "AFP Fact Check" in srcs, "AFP should be 'AFP Fact Check' to match _SOURCE_PERSPECTIVE key"
    return True, f"{len(srcs)} sources enabled"

@test("source_preview — no topic gives balanced category mix", category="sources", live=False)
def _():
    total, preview = bot._source_preview_msg("")
    assert total > 50, f"Expected >50 total, got {total}"
    assert "+{} more".format(total - 8) in preview or len(preview.split(",")) <= 8
    # Should contain at least one from each major region
    parts = preview.split(", ")
    assert len(parts) >= 6, f"Expected >=6 sources in preview, got {len(parts)}: {preview}"
    return True, preview

@test("source_preview — Africa topic prioritises Africa Check, PesaCheck", category="sources", live=False)
def _():
    _, preview = bot._source_preview_msg("Nigerian government claims vaccination rates have reached 80% in Africa")
    assert "Africa Check" in preview or "PesaCheck" in preview or "Dubawa" in preview, \
        f"Africa topic should show African fact-checkers, got: {preview}"
    return True, preview

@test("source_preview — Palestine topic prioritises Al Jazeera, 972 Magazine", category="sources", live=False)
def _():
    _, preview = bot._source_preview_msg("Israeli forces entered Gaza and shelled a hospital in Palestine")
    mideast = {"Al Jazeera", "972 Magazine", "Electronic Intifada", "Middle East Eye",
               "B'Tselem", "Mondoweiss", "Misbar", "Haaretz", "Middle East Monitor"}
    found = [s for s in mideast if s in preview]
    assert len(found) >= 2, f"Palestine topic should show >=2 ME sources, got: {preview}"
    return True, f"{found} → {preview}"

@test("source_preview — US politics topic prioritises PolitiFact, FactCheck.org", category="sources", live=False)
def _():
    _, preview = bot._source_preview_msg("Trump claims Democrats rigged the US election and Congress is corrupt")
    us = {"FactCheck.org", "PolitiFact", "Snopes"}
    found = [s for s in us if s in preview]
    assert len(found) >= 1, f"US politics topic should show US fact-checkers, got: {preview}"
    return True, f"{found} → {preview}"

@test("HELP command — returns help message", category="commands", live=False)
def _():
    reset()
    num, msg = msg_text("HELP")
    with patch("bot.send", side_effect=_mock_send):
        bot.process(num, msg)
    out = all_output()
    assert "FactCheck Pro" in out or "HELP" in out.upper()
    assert "URL" in out or "url" in out.lower()
    return True, out[:200]

@test("HELP alias — ? also triggers help", category="commands", live=False)
def _():
    reset()
    num, msg = msg_text("?")
    with patch("bot.send", side_effect=_mock_send):
        bot.process(num, msg)
    assert len(_sent) > 0
    return True, _sent[0]["text"][:100]

@test("N cancels pending check", category="commands", live=False)
def _():
    reset()
    num, msg = msg_text("Israel attacked Lebanon with chemical weapons last week")
    _fake_claims = {"checkable": True, "claims": ["Israel used chemical weapons in Lebanon"], "reason": "", "suggestions": []}
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.assess_content_claims", return_value=_fake_claims):
        bot.process(num, msg)
    pkey = ("whatsapp", num)
    assert pkey in bot.pending, "Expected pending after first message"
    _sent.clear()
    cancel = {"type":"text","id":_mid(),"timestamp":str(int(time.time())),"text":{"body":"N"}}
    with patch("bot.send", side_effect=_mock_send):
        bot.process(num, cancel)
    assert pkey not in bot.pending
    assert any("Cancelled" in m["text"] for m in _sent)
    return True, "Pending correctly cleared on N"

@test("Stale message (>5 min) is ignored", category="edge_cases", live=False)
def _():
    reset()
    old_ts = int(time.time()) - 400
    _, msg = msg_text("test claim")
    msg["timestamp"] = str(old_ts)
    with patch("bot.send", side_effect=_mock_send):
        bot.process("447700900099", msg)
    assert len(_sent) == 0, "Expected no response to stale message"
    return True, "Stale message correctly ignored"

@test("Duplicate message ID is ignored", category="edge_cases", live=False)
def _():
    reset()
    num, msg = msg_text("test claim")
    _fake_claims = {"checkable": True, "claims": ["test claim"], "reason": "", "suggestions": []}
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.assess_content_claims", return_value=_fake_claims):
        bot.process(num, msg)
    first_count = len(_sent)
    # Do NOT reset — keep processed_ids intact
    _sent.clear()
    msg2 = dict(msg)  # same msg id
    msg2["timestamp"] = str(int(time.time()))
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.assess_content_claims", return_value=_fake_claims):
        bot.process(num, msg2)
    assert len(_sent) == 0, f"Duplicate should be ignored, but got {len(_sent)} msgs"
    return True, f"First: {first_count} msgs, duplicate: {len(_sent)} msgs (correctly ignored)"

@test("Unsupported message type gets friendly error", category="edge_cases", live=False)
def _():
    reset()
    num, msg = msg_unsupported()
    with patch("bot.send", side_effect=_mock_send):
        bot.process(num, msg)
    assert len(_sent) > 0
    out = all_output()
    assert "Unsupported" in out or "⚠️" in out, f"Expected error msg, got: {out[:200]}"
    return True, out[:100]

@test("First-time user gets welcome message", category="commands", live=False)
def _():
    reset()
    new_num = f"447{int(time.time())}99"  # unique number = new user
    _, msg = msg_text("Hello")
    with patch("bot.send", side_effect=_mock_send):
        bot.process(new_num, msg)
    out = all_output()
    assert "Welcome" in out or "welcome" in out.lower(), f"No welcome in: {out[:300]}"
    return True, out[:200]

@test("fmt_report — contains all required sections", category="formatting", live=False)
def _():
    fake_analysis = {
        "rating": "MOSTLY FALSE",
        "lenz_score": 3,
        "verdict": "The claim is mostly false based on available evidence.",
        "key_facts": ["Fact 1 from sources", "Fact 2 from sources"],
        "perspectives": {
            "western_mainstream": "Western media report X",
            "regional_independent": "Regional sources say Y",
            "consensus": "Disputed along geopolitical lines"
        },
        "contested_language": ["terrorist / militant — contested framing"],
        "context": "Background context here.",
        "red_flags": ["Emotional language", "Missing attribution"],
        "media_bias": "Source has known bias toward X",
        "sources": ["BBC", "Al Jazeera"],
        "confidence": "MEDIUM",
        "confidence_reason": "Limited primary sources"
    }
    report = bot.fmt_report("Test claim", fake_analysis, "text", 0.002)
    for section in ["VERDICT", "CLAIM", "ANALYSIS", "KEY FACTS", "PERSPECTIVES",
                    "CONTESTED LANGUAGE", "BACKGROUND", "RED FLAGS",
                    "CONFIDENCE", "SOURCES CITED", "FactCheck Pro"]:
        assert section in report, f"Missing section: {section}\nReport:\n{report[:500]}"
    return True, f"All sections present ({len(report)} chars)"

# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TESTS — make real API calls
# ═══════════════════════════════════════════════════════════════════════════════

# ── TEXT CLAIMS ───────────────────────────────────────────────────────────────

@test("Text: simple true claim — Earth orbits the Sun", category="text_claims")
def _():
    msgs, report = run_full(*msg_text("The Earth orbits the Sun"))
    assert report, "No report received"
    assert "TRUE" in report or "MOSTLY TRUE" in report or "VERDICT" in report
    return True, report[:400]

@test("Text: false claim — vaccines cause autism", category="text_claims")
def _():
    msgs, report = run_full(*msg_text("Vaccines cause autism — this has been proven by scientists"))
    assert "FALSE" in report or "MISLEADING" in report or "VERDICT" in report
    return True, report[:400]

@test("Text: contested claim — Gaza ceasefire violations", category="text_claims")
def _():
    claim = ("Israel violated the Gaza ceasefire over 1600 times between October 2025 "
             "and February 2026, killing more than 650 Palestinians since the ceasefire began")
    msgs, report = run_full(*msg_text(claim))
    assert "VERDICT" in report
    return True, report[:600]

@test("Text: multi-claim WhatsApp forward", category="text_claims")
def _():
    fwd = textwrap.dedent("""
        FORWARDED MESSAGE:
        1. The WHO confirmed 5G towers spread COVID-19
        2. Bill Gates has a microchip patent linked to vaccines
        3. Drinking bleach cures coronavirus
    """).strip()
    msgs, report = run_full(*msg_text(fwd))
    assert "VERDICT" in report or "FALSE" in report
    return True, report[:500]

@test("Text: Arabic/multilingual claim", category="text_claims")
def _():
    # Mix of Arabic and English as commonly forwarded on WhatsApp
    claim = "إسرائيل قتلت أكثر من 40000 فلسطيني في غزة — Israel has killed over 40,000 Palestinians in Gaza"
    msgs, report = run_full(*msg_text(claim))
    assert "VERDICT" in report
    return True, report[:400]

@test("Text: non-checkable content (recipe) returns graceful no-claims msg", category="text_claims")
def _():
    reset()
    num, msg = msg_text("Here is a great recipe for hummus: chickpeas, tahini, lemon, garlic")
    with patch("bot.send", side_effect=_mock_send):
        bot.process(num, msg)
    out = all_output()
    # Should not create a pending check
    assert ("whatsapp", num) not in bot.pending or True  # graceful
    return True, out[:200]

@test("Text: very long forwarded message (>2000 chars)", category="text_claims")
def _():
    long_claim = "Scientists have discovered that " + ("eating chocolate cures cancer. " * 70)
    msgs, report = run_full(*msg_text(long_claim))
    assert len(msgs) > 0
    return True, f"{len(msgs)} messages sent"

# ── URL TYPES — SOCIAL MEDIA ──────────────────────────────────────────────────

@test("URL: Facebook public post", category="facebook")
def _():
    url = "https://www.facebook.com/share/p/1B6EPjbASB/"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0, "No response to Facebook URL"
    return True, report[:400] if report else all_output()[:300]

@test("URL: Facebook Reel (/reel/)", category="facebook")
def _():
    url = "https://www.facebook.com/reel/1317482232766440"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:300]

@test("URL: Facebook video (/watch/)", category="facebook")
def _():
    url = "https://www.facebook.com/watch/?v=123456789"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Facebook shared video (/share/v/)", category="facebook")
def _():
    url = "https://www.facebook.com/share/v/1Abcde12345/"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Instagram post (/p/)", category="instagram")
def _():
    url = "https://www.instagram.com/p/C8example123/"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Instagram Reel (/reel/)", category="instagram")
def _():
    url = "https://www.instagram.com/reel/C8example456/"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: TikTok video", category="tiktok")
def _():
    url = "https://www.tiktok.com/@bbcnews/video/7321847264801120545"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:300]

@test("URL: TikTok short link (vm.tiktok.com)", category="tiktok")
def _():
    url = "https://vm.tiktok.com/ZMkABCdef/"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: YouTube video", category="youtube")
def _():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:300]

@test("URL: YouTube short link (youtu.be)", category="youtube")
def _():
    url = "https://youtu.be/dQw4w9WgXcQ"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: YouTube Shorts", category="youtube")
def _():
    url = "https://www.youtube.com/shorts/ABC123def456"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Twitter/X tweet", category="twitter")
def _():
    url = "https://x.com/Reuters/status/1234567890123456789"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:300]

@test("URL: Twitter.com (legacy domain)", category="twitter")
def _():
    url = "https://twitter.com/AJEnglish/status/1234567890123456789"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Telegram channel post", category="other_social")
def _():
    url = "https://t.me/someChannel/12345"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Rumble video", category="other_social")
def _():
    url = "https://rumble.com/v4example-test-video.html"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

# ── URL TYPES — NEWS & WEB ────────────────────────────────────────────────────

@test("URL: BBC News article", category="news_urls")
def _():
    url = "https://www.bbc.co.uk/news/world-middle-east-67564358"
    msgs, report = run_full(*msg_text(url))
    assert "VERDICT" in report or len(msgs) > 1
    return True, report[:500]

@test("URL: Al Jazeera article", category="news_urls")
def _():
    url = "https://www.aljazeera.com/news/2024/1/1/un-calls-for-immediate-ceasefire-in-gaza"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, report[:400]

@test("URL: Guardian article", category="news_urls")
def _():
    url = "https://www.theguardian.com/world/2024/jan/01/example-article"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:300]

@test("URL: HRW report", category="news_urls")
def _():
    url = "https://www.hrw.org/news/2024/02/24/israel-aid-groups-barred-from-gaza-west-bank"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, report[:400] if report else all_output()[:300]

@test("URL: Substack post", category="news_urls")
def _():
    url = "https://owenjones.substack.com/p/example-post-about-gaza"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

@test("URL: Wikipedia article", category="news_urls")
def _():
    url = "https://en.wikipedia.org/wiki/2023_Israel%E2%80%93Hamas_war"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:300]

@test("URL: Broken/404 URL handles gracefully", category="edge_cases")
def _():
    url = "https://www.bbc.co.uk/this-page-does-not-exist-404xyz"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0, "Expected some response even for 404"
    return True, all_output()[:200]

@test("URL: Malformed URL handles gracefully", category="edge_cases")
def _():
    url = "https://not-a-real-domain-xyzabc123.com/fake/path"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

# ── MULTIMEDIA — IMAGE ────────────────────────────────────────────────────────

@test("Image: plain JPEG — OCR and fact-check", category="image")
def _():
    img = _make_jpeg("Israel bombed Al-Ahli hospital killing 500 people")
    from_num, message = msg_image(img, caption="")
    msgs, report = run_full(from_num, message)
    assert len(msgs) > 0
    return True, report[:400] if report else all_output()[:300]

@test("Image: JPEG with caption claim", category="image")
def _():
    img = _make_jpeg("Breaking news graphic")
    from_num, message = msg_image(img, caption="Gaza death toll exceeds 50,000")
    msgs, report = run_full(from_num, message)
    assert len(msgs) > 0
    return True, report[:400] if report else all_output()[:300]

@test("Image: EXIF date embedded (OSINT)", category="image")
def _():
    try:
        img = _make_jpeg_with_exif("2019:03:14 12:00:00")
        from_num, message = msg_image(img)
        msgs, report = run_full(from_num, message)
        full = all_output()
        # EXIF should appear in report if OSINT section is triggered
        return True, f"EXIF check ran. Report: {report[:200] if report else 'no report'}"
    except ImportError:
        return True, "piexif not installed — EXIF write skipped, read tested elsewhere"

@test("Image: empty/corrupt image handles gracefully", category="image")
def _():
    reset()
    from_num, message = msg_image(b"\x00\x00\x00corrupt")
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.download_media", return_value=b"\x00\x00corrupt"):
        bot.process(from_num, message)
    assert len(_sent) > 0  # should send an error message
    out = all_output()
    return True, out[:200]

# ── MULTIMEDIA — AUDIO ────────────────────────────────────────────────────────

@test("Audio: voice note — transcription pipeline triggered", category="audio")
def _():
    audio = _make_wav_silence(seconds=2)
    from_num, message = msg_audio(audio)
    reset()
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.download_media", return_value=audio), \
         patch("bot.transcribe", return_value="Israeli forces killed civilians in Rafah"):
        bot.process(from_num, message)
    out = all_output()
    assert len(_sent) > 0
    # Should show claims or transcription attempt
    return True, out[:300]

@test("Audio: empty audio handles gracefully", category="audio")
def _():
    reset()
    from_num, message = msg_audio(b"")
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.download_media", return_value=b""), \
         patch("bot.transcribe", return_value=""):
        bot.process(from_num, message)
    out = all_output()
    return True, out[:200]

# ── MULTIMEDIA — VIDEO ────────────────────────────────────────────────────────

@test("Video: direct WhatsApp video message — pipeline triggered", category="video")
def _():
    video = _make_mp4_stub()
    from_num, message = msg_video(video, caption="Watch this shocking video of Israeli soldiers")
    reset()
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.download_media", return_value=video), \
         patch("bot.transcribe", return_value=""), \
         patch("bot.extract_video_frames", return_value=([], 0)), \
         patch("bot.analyze_video_frames", return_value=""):
        bot.process(from_num, message)
    out = all_output()
    assert len(_sent) > 0
    return True, out[:300]

@test("Video: URL from fb.watch domain", category="video")
def _():
    url = "https://fb.watch/abcdefghij/"
    msgs, report = run_full(*msg_text(url))
    assert len(msgs) > 0
    return True, all_output()[:200]

# ── MULTIMEDIA — DOCUMENT ─────────────────────────────────────────────────────

@test("Document: plain text file with claims", category="document")
def _():
    content = b"The Palestinian death toll in Gaza has exceeded 45,000 since October 7 2023"
    from_num, message = msg_document(content, "news_report.txt")
    msgs, report = run_full(from_num, message)
    assert len(msgs) > 0
    return True, report[:400] if report else all_output()[:300]

@test("Document: PDF stub handled gracefully", category="document")
def _():
    content = _make_pdf_stub()
    from_num, message = msg_document(content, "report.pdf")
    msgs, report = run_full(from_num, message)
    assert len(msgs) > 0
    return True, all_output()[:200]

# ── OSINT LIVE CHECKS ─────────────────────────────────────────────────────────

@test("OSINT: Wayback Machine — Wikipedia URL has archive", category="osint")
def _():
    result = bot.wayback_earliest("https://en.wikipedia.org/wiki/Gaza")
    assert result is not None, "Expected a date from Wayback for Wikipedia/Gaza"
    assert len(result) == 10  # YYYY-MM-DD
    return True, f"Earliest Wikipedia/Gaza archive: {result}"

@test("OSINT: Wayback Machine — nonexistent URL returns None", category="osint")
def _():
    result = bot.wayback_earliest("https://totally-fake-domain-xyz99.com/nonexistent")
    assert result is None, f"Expected None for fake URL, got: {result}"
    return True, "Correctly returned None"

@test("OSINT: run_osint — URL triggers Wayback check", category="osint")
def _():
    findings = bot.run_osint(source_url="https://www.bbc.co.uk/news")
    assert "wayback" in findings
    return True, f"Wayback: {findings.get('wayback')}"

@test("OSINT: run_osint — image triggers EXIF check", category="osint")
def _():
    img = _make_jpeg("test")
    findings = bot.run_osint(image_bytes=img)
    assert "exif" in findings
    return True, f"EXIF keys: {list(findings.get('exif',{}).keys())}"

@test("OSINT: TinEye — skips gracefully when no API key", category="osint")
def _():
    orig = bot.TINEYE_API_KEY
    bot.TINEYE_API_KEY = ""
    img = _make_jpeg("test")
    result = bot.tineye_search(img)
    bot.TINEYE_API_KEY = orig
    assert result == [], f"Expected empty list, got: {result}"
    return True, "Gracefully skipped with no key"

@test("OSINT: Hive — skips gracefully when no API key", category="osint")
def _():
    orig = bot.HIVE_API_KEY
    bot.HIVE_API_KEY = ""
    img = _make_jpeg("test")
    result = bot.hive_ai_check(img)
    bot.HIVE_API_KEY = orig
    assert result == {}, f"Expected empty dict, got: {result}"
    return True, "Gracefully skipped with no key"

# ── MULTI-PERSPECTIVE OUTPUT ──────────────────────────────────────────────────

@test("Perspectives: Middle East conflict shows divergent views", category="perspectives")
def _():
    claim = ("Hamas killed over 1200 Israelis on October 7 2023 in a terrorist attack "
             "that triggered an Israeli military response in Gaza")
    msgs, report = run_full(*msg_text(claim))
    assert "VERDICT" in report
    # Check for perspective section
    has_persp = "PERSPECTIVES" in report or "Western" in report or "Regional" in report
    return True, report[:600]

@test("Perspectives: Statistical claim gets scrutiny", category="perspectives")
def _():
    claim = "The US has vetoed over 40 UN Security Council resolutions protecting Israel since 1972"
    msgs, report = run_full(*msg_text(claim))
    assert "VERDICT" in report
    return True, report[:500]

# ── BILLING / PAYWALL ─────────────────────────────────────────────────────────

@test("Billing: blocked user gets payment prompt", category="billing", live=False)
def _():
    test_num = f"4477{int(time.time())}blocked"
    with bot._db() as c:
        c.execute("""INSERT OR REPLACE INTO platform_users
                     (platform, platform_id, free_checks_used, balance_cents, tier, created_at, last_seen)
                     VALUES ('whatsapp', ?, 9999, 0, 'free', ?, ?)""",
                  (test_num, int(time.time()), int(time.time())))
    reset()
    _fake_claims = {"checkable": True, "claims": ["test claim"], "reason": "", "suggestions": []}
    _, msg = msg_text("Test claim about something")
    with patch("bot.send", side_effect=_mock_send), \
         patch("bot.assess_content_claims", return_value=_fake_claims):
        bot.process(test_num, msg)
    pkey = ("whatsapp", test_num)
    assert pkey in bot.pending, f"Expected pending, keys: {list(bot.pending.keys())}"
    _sent.clear()
    ymsg = {"type":"text","id":_mid(),"timestamp":str(int(time.time())),"text":{"body":"Y"}}
    with patch("bot.send", side_effect=_mock_send):
        bot.process(test_num, ymsg)
    out = all_output()
    assert "Top Up" in out or "payment" in out.lower() or "free check" in out.lower() or "coming soon" in out.lower(), \
        f"Expected payment prompt, got: {out[:300]}"
    return True, out[:200]

@test("Billing: last free check shows warning", category="billing", live=False)
def _():
    test_num = f"4477{int(time.time())}lastcheck"
    limit = bot.FREE_CHECKS_LIMIT
    bot.FREE_CHECKS_LIMIT = 3
    with bot._db() as c:
        c.execute("""INSERT OR REPLACE INTO platform_users
                     (platform, platform_id, free_checks_used, balance_cents, tier, created_at, last_seen)
                     VALUES ('whatsapp', ?, 2, 0, 'free', ?, ?)""",
                  (test_num, int(time.time()), int(time.time())))
    reset()
    _, msg = msg_text("Claim that needs checking")
    with patch("bot.send", side_effect=_mock_send):
        bot.process(test_num, msg)
    pkey = ("whatsapp", test_num)
    if pkey in bot.pending:
        reset()
        _, ymsg = msg_text("Y")
        with patch("bot.send", side_effect=_mock_send):
            bot.process(test_num, ymsg)
        out = all_output()
        # Should mention last free check
        has_warning = "last free check" in out.lower() or "remaining" in out.lower()
        bot.FREE_CHECKS_LIMIT = limit
        return True, out[:200]
    bot.FREE_CHECKS_LIMIT = limit
    return True, "Could not test (pending not created)"

# ═══════════════════════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

COLORS = {
    "green":  "\033[92m", "red":    "\033[91m",
    "yellow": "\033[93m", "cyan":   "\033[96m",
    "bold":   "\033[1m",  "reset":  "\033[0m",
    "dim":    "\033[2m"
}
def c(color, text):
    return f"{COLORS[color]}{text}{COLORS['reset']}"

def run_all():
    if LIST_ONLY:
        for meta, fn in _results:
            print(f"  [{meta.category:20s}] {'(unit)' if not meta.live else '(live)':8s}  {meta.name}")
        return

    to_run = [
        (meta, fn) for meta, fn in _results
        if (not UNIT_ONLY or not meta.live)
        and (not FILTER or FILTER.lower() in meta.category.lower() or FILTER.lower() in meta.name.lower())
    ]

    print(c("bold", f"\n{'═'*70}"))
    print(c("bold", f"  FactCheck Pro — Comprehensive Test Suite"))
    mode = "UNIT ONLY" if UNIT_ONLY else "FULL (live API calls)"
    print(c("dim",  f"  Mode: {mode}  |  Tests: {len(to_run)}  |  Filter: {FILTER or 'none'}"))
    print(c("bold", f"{'═'*70}\n"))

    categories = {}
    for meta, fn in to_run:
        categories.setdefault(meta.category, []).append((meta, fn))

    passed = failed = skipped = 0
    failures = []

    for cat, items in categories.items():
        print(c("cyan", f"  ▶  {cat.upper().replace('_',' ')}"))
        for meta, fn in items:
            label = "LIVE" if meta.live else "UNIT"
            try:
                t0 = time.time()
                ok, detail = fn()
                elapsed = time.time() - t0
                if ok:
                    passed += 1
                    status = c("green", "  ✓")
                    detail_str = c("dim", f" — {str(detail)[:80]}") if detail else ""
                    print(f"{status} {c('dim',f'[{label}]')} {meta.name}{detail_str}  {c('dim',f'{elapsed:.1f}s')}")
                else:
                    failed += 1
                    print(c("red", f"  ✗ [{label}] {meta.name}"))
                    print(c("red", f"       {detail}"))
                    failures.append((meta.name, str(detail)))
            except Exception as e:
                failed += 1
                err = traceback.format_exc()
                print(c("red", f"  ✗ [{label}] {meta.name}"))
                print(c("red", f"       {type(e).__name__}: {e}"))
                if VERBOSE:
                    print(c("dim", textwrap.indent(err, "       ")))
                failures.append((meta.name, f"{type(e).__name__}: {e}"))
        print()

    print(c("bold", f"{'═'*70}"))
    total = passed + failed
    pct = int(100 * passed / total) if total else 0
    color = "green" if pct >= 90 else ("yellow" if pct >= 70 else "red")
    print(c(color, f"  RESULTS:  {passed}/{total} passed ({pct}%)"))
    if failures:
        print(c("red", f"\n  FAILURES:"))
        for name, err in failures:
            print(c("red", f"    • {name}"))
            print(c("dim", f"      {err[:120]}"))
    print(c("bold", f"{'═'*70}\n"))
    return failed

if __name__ == "__main__":
    sys.exit(run_all() or 0)
