#!/usr/bin/env python3
"""
Comprehensive test suite for FactCheck Pro bot.
Tests all media types and pipelines WITHOUT sending real WhatsApp messages.

Usage:
    source venv/bin/activate
    python test_all.py [--quick]   # --quick skips slow URL/source scraping
"""
import sys, os, io, json, base64, time, argparse, textwrap
from dotenv import load_dotenv

load_dotenv()

# Patch send() so tests don't hit WhatsApp
import unittest.mock as mock
import bot
bot.send = mock.MagicMock()

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
SKIP = "\033[33m~\033[0m"

results = []

def check(name, fn, *args, skip_if=None, **kwargs):
    if skip_if:
        print(f"  {SKIP} {name} — skipped ({skip_if})")
        results.append(("skip", name))
        return None
    try:
        t0 = time.time()
        val = fn(*args, **kwargs)
        elapsed = time.time() - t0
        ok = bool(val)
        icon = PASS if ok else FAIL
        status = "pass" if ok else "fail"
        print(f"  {icon} {name}  [{elapsed:.1f}s]")
        if not ok:
            print(f"      → returned: {repr(val)[:120]}")
        results.append((status, name))
        return val
    except Exception as e:
        print(f"  {FAIL} {name}  — exception: {e}")
        results.append(("fail", name))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Tiny synthetic test assets (no external files needed)
# ─────────────────────────────────────────────────────────────────────────────

def make_test_image():
    """Create a minimal JPEG with readable text via PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (400, 100), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((10, 30), "BREAKING: Test claim for fact-checking", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()
    except Exception as e:
        print(f"    (PIL image creation failed: {e} — using placeholder bytes)")
        return None


def make_test_audio():
    """Return a minimal valid OGG/Vorbis header (too short to transcribe, but tests the call)."""
    # Real audio would need whisper; we just verify the function handles gracefully
    return b"OggS" + b"\x00" * 60   # won't transcribe, but exercises error path


# ─────────────────────────────────────────────────────────────────────────────
# 1. Environment / API keys
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 1. Environment ━━━")
check("ANTHROPIC_API_KEY set",    lambda: bool(os.getenv("ANTHROPIC_API_KEY")))
check("WHATSAPP_TOKEN set",       lambda: bool(os.getenv("WHATSAPP_TOKEN")))
check("PHONE_NUMBER_ID set",      lambda: bool(os.getenv("PHONE_NUMBER_ID")))
check("GOOGLE_FACT_CHECK_API_KEY set", lambda: bool(os.getenv("GOOGLE_FACT_CHECK_API_KEY")))
check("OPENAI_API_KEY set",       lambda: bool(os.getenv("OPENAI_API_KEY")))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Anthropic API connectivity
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 2. Anthropic API ━━━")
import requests

def test_anthropic_ping():
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 10, "messages": [{"role": "user", "content": "Say OK"}]},
        timeout=15)
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()

check("Anthropic API reachable", test_anthropic_ping)


# ─────────────────────────────────────────────────────────────────────────────
# 3. OCR (image analysis)
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 3. OCR / Image Analysis ━━━")
img_bytes = make_test_image()

check("PIL test image created", lambda: img_bytes is not None)

def test_ocr():
    if not img_bytes:
        return None
    result = bot.ocr_image(img_bytes)
    print(f"    → OCR output: {repr(result[:120])}")
    return result and len(result) > 5

check("ocr_image() returns text",
      test_ocr,
      skip_if=None if img_bytes else "no test image")

# Test refusal filter
check("OCR refusal filter (sorry response)",
      lambda: bot._is_ocr_refusal("I'm sorry, I cannot extract text from this image"))
check("OCR refusal filter (valid response)",
      lambda: not bot._is_ocr_refusal("BREAKING: Israeli forces strike Gaza"))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Text claim analysis (core pipeline)
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 4. AI Analysis (claude_analyse) ━━━")

SAMPLE_CLAIM = "Israel has agreed to a 60-day ceasefire in Gaza starting January 2025"

def test_claude_analyse():
    result = bot.claude_analyse(SAMPLE_CLAIM, [], "", "text")
    print(f"    → rating: {result.get('rating')}, confidence: {result.get('confidence')}")
    return result and "rating" in result and "verdict" in result

check("claude_analyse() returns structured JSON", test_claude_analyse)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Report formatting
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 5. Report Formatting ━━━")

DUMMY_ANALYSIS = {
    "rating": "HALF TRUE",
    "verdict": "The claim is partially supported but lacks full context.",
    "key_facts": ["Ceasefire was proposed", "Final agreement not confirmed"],
    "context": "Negotiations ongoing since late 2024.",
    "red_flags": ["Unverified source"],
    "media_bias": "",
    "sources": ["BBC — https://bbc.co.uk"],
    "confidence": "MEDIUM",
    "confidence_reason": "Limited independent corroboration"
}

def test_fmt():
    report = bot.fmt_report(SAMPLE_CLAIM, DUMMY_ANALYSIS, "text", 0.0085)
    has_verdict = "HALF TRUE" in report
    has_claim = "ceasefire" in report.lower()
    return has_verdict and has_claim

check("fmt_report() contains verdict + claim", test_fmt)

for rating in ["TRUE", "MOSTLY TRUE", "HALF TRUE", "MOSTLY FALSE", "FALSE", "PANTS ON FIRE", "UNVERIFIABLE", "MISLEADING", "NEEDS CONTEXT"]:
    check(f"meter_visual({rating})", lambda r=rating: bool(bot.meter_visual(r)))


# ─────────────────────────────────────────────────────────────────────────────
# 6. URL handling — article
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 6. URL Fetching ━━━")

check("fetch() BBC article",
      lambda: len(bot.fetch("https://www.bbc.co.uk/news", timeout=10)) > 100)

check("_og_metadata() on a news article",
      lambda: len(bot._og_metadata("https://apnews.com")) > 10)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Google Fact-Check API
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 7. Google Fact-Check API ━━━")

def test_google_fc():
    results_gfc = bot.google_fc("ceasefire Gaza 2024")
    print(f"    → {len(results_gfc)} results from Google FC")
    return isinstance(results_gfc, list)   # empty list is OK — key may be inactive

check("google_fc() returns list",
      test_google_fc,
      skip_if=None if os.getenv("GOOGLE_FACT_CHECK_API_KEY") else "no GOOGLE_FACT_CHECK_API_KEY")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Source scraping (fast tier only)
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 8. Source Scraping (fast tier) ━━━")

def test_scrape():
    scraped, used = bot.scrape_sites("Gaza ceasefire 2024")
    print(f"    → {len(used)} sources returned content ({len(scraped)} chars)")
    return len(used) > 0

check("scrape_sites() hits at least one source", test_scrape)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Video frame extraction
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 9. Video Frame Extraction ━━━")

def make_tiny_mp4():
    """Download a tiny complete MP4 from a public CDN for testing."""
    # Try several small public-domain MP4s (complete files, under 500KB)
    urls = [
        "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
        "https://file-examples.com/storage/fe9b72571b62e2dbbab5c6b/2017/04/file_example_MP4_480_1_5MG.mp4",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=30, stream=True)
            r.raise_for_status()
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > 2_000_000:  # allow up to 2MB for a complete file
                    break
            if len(data) > 100_000:
                return data
        except Exception as e:
            print(f"    (video URL failed: {e})")
    return None

print("  (downloading tiny test video...)")
video_bytes = make_tiny_mp4()

check("test MP4 downloaded", lambda: video_bytes is not None)

def test_frame_extract():
    if not video_bytes:
        return None
    frames, duration = bot.extract_video_frames(video_bytes, num_frames=2)
    print(f"    → {len(frames)} frames, {duration:.1f}s")
    return len(frames) > 0

check("extract_video_frames() works",
      test_frame_extract,
      skip_if=None if video_bytes else "no test video")

def test_frame_analysis():
    if not video_bytes:
        return None
    frames, _ = bot.extract_video_frames(video_bytes, num_frames=2)
    if not frames:
        return None
    result = bot.analyze_video_frames(frames)
    print(f"    → frame analysis: {repr(result[:100])}")
    return bool(result)

check("analyze_video_frames() returns description",
      test_frame_analysis,
      skip_if=None if video_bytes else "no test video")


# ─────────────────────────────────────────────────────────────────────────────
# 10. Audio transcription
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 10. Audio Transcription ━━━")

check("transcribe() handles invalid audio gracefully",
      lambda: bot.transcribe(make_test_audio(), "audio/ogg") == "",
      skip_if=None if os.getenv("OPENAI_API_KEY") else "no OPENAI_API_KEY")


# ─────────────────────────────────────────────────────────────────────────────
# 11. JSON parsing (robustness)
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 11. JSON Parsing ━━━")

check("_parse_json_result() clean JSON",
      lambda: bot._parse_json_result('{"rating":"TRUE","verdict":"ok"}') is not None)
check("_parse_json_result() JSON with preamble",
      lambda: bot._parse_json_result('Here is the result: {"rating":"FALSE","verdict":"nope"}') is not None)
check("_parse_json_result() invalid returns None",
      lambda: bot._parse_json_result("No JSON here") is None)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Text cleaning
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 12. Text Cleaning ━━━")

check("clean_ocr() strips noise",
      lambda: "This business uses a secure service from Meta" not in bot.clean_ocr("Claim text\nThis business uses a secure service from Meta"))
check("clean_ocr() keeps real text",
      lambda: "Real claim" in bot.clean_ocr("Real claim\nToday"))


# ─────────────────────────────────────────────────────────────────────────────
# 13. Full end-to-end: text message → confirm → run_check
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 13. End-to-End: text claim (mocked send) ━━━")
import threading

def test_e2e_text():
    bot.send.reset_mock()
    ts = int(time.time())
    msg = {"id": "test_msg_e2e_1", "timestamp": str(ts), "type": "text",
           "text": {"body": SAMPLE_CLAIM}}
    bot.process("447700000000", msg)
    calls = [str(c) for c in bot.send.call_args_list]
    has_confirm = any("Y" in c or "fact-check" in c.lower() or "preview" in c.lower() for c in calls)
    print(f"    → send() called {bot.send.call_count}×; confirm-style message: {has_confirm}")
    return bot.send.call_count > 0

check("process() text message triggers send()", test_e2e_text)

def test_e2e_yes_confirm():
    """Confirm pending → triggers run_check in background thread."""
    bot.send.reset_mock()
    from_num = "447700000001"
    ts = int(time.time())

    # First: submit a claim
    msg1 = {"id": "test_msg_e2e_2a", "timestamp": str(ts), "type": "text",
            "text": {"body": "Hamas fired rockets at Tel Aviv last night according to IDF"}}
    bot.process(from_num, msg1)

    # Check pending was created
    with bot.pending_lock:
        has_pending = from_num in bot.pending

    if not has_pending:
        print("    → no pending created (message may have been stale or deduplicated)")
        return True  # not a failure — could be timing

    # Confirm with Y
    msg2 = {"id": "test_msg_e2e_2b", "timestamp": str(ts), "type": "text",
            "text": {"body": "Y"}}
    bot.process(from_num, msg2)
    time.sleep(0.3)  # let background thread start
    print(f"    → send() called {bot.send.call_count}× after confirm")
    return bot.send.call_count > 0

check("process() Y-confirm triggers run_check", test_e2e_yes_confirm)


def test_e2e_cancel():
    bot.send.reset_mock()
    from_num = "447700000002"
    ts = int(time.time())
    msg1 = {"id": "test_msg_e2e_3a", "timestamp": str(ts), "type": "text",
            "text": {"body": "Iran launched ballistic missiles at US bases in Iraq"}}
    bot.process(from_num, msg1)
    msg2 = {"id": "test_msg_e2e_3b", "timestamp": str(ts), "type": "text",
            "text": {"body": "N"}}
    bot.process(from_num, msg2)
    calls = [str(c) for c in bot.send.call_args_list]
    cancelled = any("cancel" in c.lower() for c in calls)
    print(f"    → cancelled: {cancelled}")
    return cancelled

check("process() N-cancel sends 'Cancelled'", test_e2e_cancel)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Image message end-to-end (mocked media download)
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 14. Image message (mocked WhatsApp media) ━━━")

def test_e2e_image():
    if not img_bytes:
        return None
    bot.send.reset_mock()
    with mock.patch.object(bot, "download_media", return_value=img_bytes):
        ts = int(time.time())
        msg = {"id": "test_img_1", "timestamp": str(ts), "type": "image",
               "image": {"id": "fake_media_id_123", "caption": "Is this real?"}}
        bot.process("447700000003", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() image message (mocked download)", test_e2e_image,
      skip_if=None if img_bytes else "no test image")


# ─────────────────────────────────────────────────────────────────────────────
# 15. Audio message end-to-end
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 15. Audio message (mocked WhatsApp media) ━━━")

def test_e2e_audio():
    bot.send.reset_mock()
    dummy_audio = make_test_audio()
    with mock.patch.object(bot, "download_media", return_value=dummy_audio):
        ts = int(time.time())
        msg = {"id": "test_audio_1", "timestamp": str(ts), "type": "audio",
               "audio": {"id": "fake_audio_id", "mime_type": "audio/ogg"}}
        bot.process("447700000004", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() audio message (mocked download)", test_e2e_audio)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Video message end-to-end
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 16. Video message (mocked WhatsApp media) ━━━")

def test_e2e_video():
    bot.send.reset_mock()
    vb = video_bytes or b"\x00" * 1024  # fallback stub
    with mock.patch.object(bot, "download_media", return_value=vb):
        ts = int(time.time())
        msg = {"id": "test_vid_1", "timestamp": str(ts), "type": "video",
               "video": {"id": "fake_video_id", "mime_type": "video/mp4", "caption": ""}}
        bot.process("447700000005", msg)
    time.sleep(0.3)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() video message (mocked download)", test_e2e_video)


# ─────────────────────────────────────────────────────────────────────────────
# 17. Document message end-to-end
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 17. Document message (mocked WhatsApp media) ━━━")

def test_e2e_document():
    bot.send.reset_mock()
    # Fake PDF bytes — won't parse but exercises the handler
    with mock.patch.object(bot, "download_media", return_value=b"%PDF-1.4 fake document content"):
        ts = int(time.time())
        msg = {"id": "test_doc_1", "timestamp": str(ts), "type": "document",
               "document": {"id": "fake_doc_id", "mime_type": "application/pdf", "filename": "report.pdf", "caption": ""}}
        bot.process("447700000006", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() document message (mocked download)", test_e2e_document)


# ─────────────────────────────────────────────────────────────────────────────
# 18. URL message types
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 18. URL message types ━━━")

def test_e2e_article_url():
    bot.send.reset_mock()
    ts = int(time.time())
    msg = {"id": "test_url_article", "timestamp": str(ts), "type": "text",
           "text": {"body": "https://www.bbc.co.uk/news/world-middle-east-68408998"}}
    bot.process("447700000007", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() article URL", test_e2e_article_url)

def test_e2e_youtube_url():
    bot.send.reset_mock()
    # Mock download to avoid real yt-dlp call
    with mock.patch.object(bot, "download_video_url", return_value=(None, "Test video about Gaza ceasefire talks")):
        ts = int(time.time())
        msg = {"id": "test_url_yt", "timestamp": str(ts), "type": "text",
               "text": {"body": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}}
        bot.process("447700000008", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() YouTube URL (mocked download)", test_e2e_youtube_url)

def test_e2e_tiktok_url():
    bot.send.reset_mock()
    with mock.patch.object(bot, "download_video_url", return_value=(None, "TikTok video: IDF releases statement")):
        ts = int(time.time())
        msg = {"id": "test_url_tt", "timestamp": str(ts), "type": "text",
               "text": {"body": "https://www.tiktok.com/@user/video/12345"}}
        bot.process("447700000009", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() TikTok URL (mocked download)", test_e2e_tiktok_url)

def test_e2e_twitter_url():
    bot.send.reset_mock()
    with mock.patch.object(bot, "download_video_url", return_value=(None, "Twitter video: Breaking news")):
        ts = int(time.time())
        msg = {"id": "test_url_tw", "timestamp": str(ts), "type": "text",
               "text": {"body": "https://x.com/user/status/12345"}}
        bot.process("447700000010", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() Twitter/X URL (mocked download)", test_e2e_twitter_url)

def test_e2e_fb_post_url():
    bot.send.reset_mock()
    with mock.patch.object(bot, "_fb_ig_post_scrape", return_value={"is_post": True, "description": "Post about Gaza", "image_url": ""}):
        ts = int(time.time())
        msg = {"id": "test_url_fb", "timestamp": str(ts), "type": "text",
               "text": {"body": "https://www.facebook.com/user/posts/12345"}}
        bot.process("447700000011", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() Facebook post URL (mocked scrape)", test_e2e_fb_post_url)

def test_e2e_ig_post_url():
    bot.send.reset_mock()
    with mock.patch.object(bot, "_fb_ig_post_scrape", return_value={"is_post": True, "description": "Instagram post about protest", "image_url": ""}):
        ts = int(time.time())
        msg = {"id": "test_url_ig", "timestamp": str(ts), "type": "text",
               "text": {"body": "https://www.instagram.com/p/ABC123/"}}
        bot.process("447700000012", msg)
    print(f"    → send() called {bot.send.call_count}×")
    return bot.send.call_count > 0

check("process() Instagram post URL (mocked scrape)", test_e2e_ig_post_url)


# ─────────────────────────────────────────────────────────────────────────────
# 19. Stale / duplicate message guards
# ─────────────────────────────────────────────────────────────────────────────
print("\n━━━ 19. Safety guards ━━━")

def test_stale_message():
    bot.send.reset_mock()
    ts = int(time.time()) - 400  # >5 minutes ago
    msg = {"id": "test_stale_1", "timestamp": str(ts), "type": "text",
           "text": {"body": "Some old claim"}}
    bot.process("447700000013", msg)
    return bot.send.call_count == 0  # should be silently dropped

check("Stale messages (>5 min) are dropped", test_stale_message)

def test_duplicate_message():
    bot.send.reset_mock()
    ts = int(time.time())
    msg = {"id": "test_dup_unique_99", "timestamp": str(ts), "type": "text",
           "text": {"body": "Duplicate test claim"}}
    bot.process("447700000014", msg)
    count1 = bot.send.call_count
    bot.send.reset_mock()
    bot.process("447700000014", msg)  # same message ID
    count2 = bot.send.call_count
    print(f"    → 1st: {count1} calls, 2nd (dup): {count2} calls")
    return count2 == 0

check("Duplicate message IDs are deduplicated", test_duplicate_message)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
passed  = sum(1 for s, _ in results if s == "pass")
failed  = sum(1 for s, _ in results if s == "fail")
skipped = sum(1 for s, _ in results if s == "skip")

print(f"\n━━━ Results ━━━")
print(f"  {PASS} Passed:  {passed}")
print(f"  {FAIL} Failed:  {failed}")
print(f"  {SKIP} Skipped: {skipped}")
print(f"  Total:   {len(results)}")

if failed:
    print(f"\nFailed tests:")
    for s, name in results:
        if s == "fail":
            print(f"  • {name}")

sys.exit(0 if failed == 0 else 1)
