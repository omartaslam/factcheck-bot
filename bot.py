"""FactCheck Pro v3.2 - Enhanced Video Analysis"""
import os, base64, json, logging, tempfile, threading, requests, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from html.parser import HTMLParser
from urllib.parse import quote_plus
import time as t
import cv2
import yt_dlp
from PIL import Image
import io

load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "factcheck_verify_123")
GOOGLE_API_KEY = os.getenv("GOOGLE_FACT_CHECK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
ADMIN_NUMBER = os.getenv("ADMIN_NUMBER", "")  # WhatsApp number to receive credit/error alerts
WHATSAPP_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
GOOGLE_FC_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

# Source toggles — set any to "false" in Railway to disable
SRC_SNOPES           = os.getenv("SRC_SNOPES",           "true").lower() == "true"
SRC_FULLFACT         = os.getenv("SRC_FULLFACT",          "true").lower() == "true"
SRC_FACTCHECKORG     = os.getenv("SRC_FACTCHECKORG",      "true").lower() == "true"
SRC_POLITIFACT       = os.getenv("SRC_POLITIFACT",        "true").lower() == "true"
SRC_AFP              = os.getenv("SRC_AFP",               "true").lower() == "true"
SRC_ALJAZEERA        = os.getenv("SRC_ALJAZEERA",         "true").lower() == "true"
SRC_MEE              = os.getenv("SRC_MEE",               "true").lower() == "true"
SRC_NOVARA           = os.getenv("SRC_NOVARA",            "true").lower() == "true"
SRC_CANARY           = os.getenv("SRC_CANARY",            "true").lower() == "true"
SRC_ZETEO            = os.getenv("SRC_ZETEO",             "true").lower() == "true"
SRC_YENISAFAK        = os.getenv("SRC_YENISAFAK",         "true").lower() == "true"
SRC_972MAG           = os.getenv("SRC_972MAG",            "true").lower() == "true"
SRC_MONDOWEISS       = os.getenv("SRC_MONDOWEISS",        "true").lower() == "true"
SRC_EINTIFADA        = os.getenv("SRC_EINTIFADA",         "true").lower() == "true"
SRC_INTERCEPT        = os.getenv("SRC_INTERCEPT",         "true").lower() == "true"
SRC_HAARETZ          = os.getenv("SRC_HAARETZ",           "true").lower() == "true"
SRC_DDN              = os.getenv("SRC_DDN",               "true").lower() == "true"
SRC_DEMOCRACYNOW     = os.getenv("SRC_DEMOCRACYNOW",      "true").lower() == "true"
SRC_GRAYZONE         = os.getenv("SRC_GRAYZONE",          "true").lower() == "true"
SRC_MINTPRESS        = os.getenv("SRC_MINTPRESS",         "true").lower() == "true"
SRC_OWENJONES        = os.getenv("SRC_OWENJONES",         "true").lower() == "true"
SRC_OWENJONES_SUB    = os.getenv("SRC_OWENJONES_SUB",     "true").lower() == "true"
SRC_CORBYN           = os.getenv("SRC_CORBYN",            "true").lower() == "true"
SRC_CORBYN_SITE      = os.getenv("SRC_CORBYN_SITE",       "true").lower() == "true"
SRC_ZARASULTANA      = os.getenv("SRC_ZARASULTANA",       "true").lower() == "true"
SRC_SULTANA_SITE     = os.getenv("SRC_SULTANA_SITE",      "true").lower() == "true"
SRC_FINKELSTEIN      = os.getenv("SRC_FINKELSTEIN",       "true").lower() == "true"
SRC_FINKELSTEIN_SUB  = os.getenv("SRC_FINKELSTEIN_SUB",   "true").lower() == "true"
SRC_CODEPINK         = os.getenv("SRC_CODEPINK",          "true").lower() == "true"
SRC_CODEPINK_SITE    = os.getenv("SRC_CODEPINK_SITE",     "true").lower() == "true"
SRC_MOATS            = os.getenv("SRC_MOATS",             "true").lower() == "true"
SRC_MOATS_YT         = os.getenv("SRC_MOATS_YT",          "true").lower() == "true"
SRC_GALLOWAY_SITE    = os.getenv("SRC_GALLOWAY_SITE",     "true").lower() == "true"
SRC_PSC              = os.getenv("SRC_PSC",               "true").lower() == "true"
SRC_SUBSTACK         = os.getenv("SRC_SUBSTACK",          "true").lower() == "true"
SRC_DDN_YT           = os.getenv("SRC_DDN_YT",            "true").lower() == "true"
SRC_BBC          = os.getenv("SRC_BBC",          "true").lower() == "true"
SRC_REUTERS      = os.getenv("SRC_REUTERS",      "true").lower() == "true"
SRC_AP           = os.getenv("SRC_AP",           "true").lower() == "true"
SRC_GUARDIAN     = os.getenv("SRC_GUARDIAN",     "true").lower() == "true"
SRC_CNN          = os.getenv("SRC_CNN",          "true").lower() == "true"
# Custom sources — add any source without code changes
# Format in Railway: "Name|https://site.com/search?q={q},Name2|https://site2.com/?s={q}"
# Use {q} for URL-encoded query, {qt} for URL-encoded short query
CUSTOM_SOURCES_RAW = os.getenv("CUSTOM_SOURCES", "")

COBALT_API = "https://api.cobalt.tools/api/json"
COBALT_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
FB_COOKIES_B64 = os.getenv("FB_COOKIES_B64", "")
FB_APP_ID = os.getenv("FB_APP_ID", "913551238207108")
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")
IG_COOKIES_B64 = os.getenv("IG_COOKIES_B64", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

# ── Admin alerting ────────────────────────────────────────────────────────────
_alert_sent = {}   # provider -> timestamp of last alert (throttle to 1/hour)
_alert_lock = threading.Lock()

def send_admin_alert(provider, message):
    """Send a WhatsApp alert to ADMIN_NUMBER — throttled to once per hour per provider."""
    if not ADMIN_NUMBER or not WHATSAPP_TOKEN:
        return
    now = t.time()
    with _alert_lock:
        last = _alert_sent.get(provider, 0)
        if now - last < 3600:
            return
        _alert_sent[provider] = now
    try:
        requests.post(
            WHATSAPP_URL,
            json={"messaging_product": "whatsapp", "to": ADMIN_NUMBER,
                  "type": "text", "text": {"body": f"⚠️ *FactCheck Pro Alert*\n\n{message}"}},
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
            timeout=10
        )
        log.warning(f"Admin alert sent ({provider}): {message[:100]}")
    except Exception as e:
        log.error(f"Admin alert failed: {e}")

def _is_credit_error(status_code, body_text):
    """Return True if the API response indicates an out-of-credit / quota error."""
    if status_code in (402, 529):
        return True
    low = body_text.lower()
    return any(k in low for k in ("credit_balance_too_low", "insufficient_quota",
                                   "exceeded your current quota", "billing_hard_limit",
                                   "rate_limit_exceeded", "insufficient credits"))


import atexit
from apscheduler.schedulers.background import BackgroundScheduler

def refresh_whatsapp_token():
    """Auto-refresh WhatsApp token every 50 days using app credentials."""
    global WHATSAPP_TOKEN
    app_id = os.getenv("FB_APP_ID", "")
    app_secret = os.getenv("FB_APP_SECRET", "")
    if not app_id or not app_secret or not WHATSAPP_TOKEN:
        log.warning("Token refresh skipped — FB_APP_ID or FB_APP_SECRET not set")
        return
    try:
        r = requests.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": WHATSAPP_TOKEN
            },
            timeout=15
        )
        data = r.json()
        new_token = data.get("access_token")
        expires_in = data.get("expires_in", 0)
        if new_token:
            WHATSAPP_TOKEN = new_token
            # Update WhatsApp URL with new token
            global WHATSAPP_URL
            log.info(f"WhatsApp token refreshed successfully. Expires in {expires_in//86400} days")
        else:
            log.error(f"Token refresh failed: {data}")
    except Exception as e:
        log.error(f"Token refresh error: {e}")

# Schedule token refresh every 50 days
_scheduler = BackgroundScheduler()
_scheduler.add_job(refresh_whatsapp_token, "interval", days=50, id="token_refresh")
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown())
log.info("Token auto-refresh scheduler started (every 50 days)")

processed_ids = set()
processed_lock = threading.Lock()
MAX_PROCESSED_IDS = 10_000

pending = {}
pending_lock = threading.Lock()
PENDING_TTL = 600

SYSTEM = """You are FactCheck Pro — world-class fact-checker for journalists and activists. Deep expertise in Gaza conflict, Iran-US-Israel tensions, West Bank, Hamas, Hezbollah, regional players. Rigorously balanced — call out falsehoods from ALL sides equally. Flag propaganda techniques and media bias."""

TRUTH_METER = {"TRUE": ("✅","TRUE",5),"MOSTLY TRUE": ("🟢","MOSTLY TRUE",4),"HALF TRUE": ("🟡","HALF TRUE",3),"MOSTLY FALSE": ("🟠","MOSTLY FALSE",2),"FALSE": ("❌","FALSE",1),"PANTS ON FIRE": ("🔥","PANTS ON FIRE",0),"UNVERIFIABLE": ("❓","UNVERIFIABLE",-1),"MISLEADING": ("⚠️","MISLEADING",-1),"NEEDS CONTEXT": ("📌","NEEDS CONTEXT",-1)}

def verdict_header(rating):
    styles = {
        "TRUE":          ("✅","VERIFIED TRUE","Claim checks out"),
        "MOSTLY TRUE":   ("🟢","MOSTLY TRUE","Minor inaccuracies"),
        "HALF TRUE":     ("🟡","HALF TRUE","Mixed evidence"),
        "MOSTLY FALSE":  ("🟠","MOSTLY FALSE","Mainly inaccurate"),
        "FALSE":         ("❌","FALSE","Not supported by evidence"),
        "PANTS ON FIRE": ("🔥","PANTS ON FIRE","Dangerous disinformation"),
        "UNVERIFIABLE":  ("🔍","UNVERIFIABLE","Cannot be confirmed"),
        "MISLEADING":    ("⚠️","MISLEADING","Framed to deceive"),
        "NEEDS CONTEXT": ("📌","NEEDS CONTEXT","Missing crucial context"),
    }
    icon, label, sub = styles.get(rating, ("❓", rating, ""))
    return f"{icon} *{label}*\n_{sub}_"

def truth_gauge(rating):
    pos = {"PANTS ON FIRE":0,"FALSE":1,"MOSTLY FALSE":2,"HALF TRUE":3,"MOSTLY TRUE":4,"TRUE":5}
    if rating not in pos: return ""
    segs = ["▱","▱","▱","▱","▱","▱"]; segs[pos[rating]] = "▰"
    return f"`{' '.join(segs)}`\n_FALSE          TRUE_"

RATINGS_MAP = {
    "TRUE":          ("VERIFIED TRUE",   "[++++++]", "Claim checks out"),
    "MOSTLY TRUE":   ("MOSTLY TRUE",     "[+++++.]", "Minor inaccuracies"),
    "HALF TRUE":     ("HALF TRUE",       "[++++..]", "Mixed evidence"),
    "MOSTLY FALSE":  ("MOSTLY FALSE",    "[+++...]", "Mainly inaccurate"),
    "FALSE":         ("FALSE",           "[++....]", "Not supported by evidence"),
    "PANTS ON FIRE": ("PANTS ON FIRE",   "[......]", "Dangerous disinformation"),
    "UNVERIFIABLE":  ("UNVERIFIABLE",    None,       "Cannot be confirmed"),
    "MISLEADING":    ("MISLEADING",      None,       "Framed to deceive"),
    "NEEDS CONTEXT": ("NEEDS CONTEXT",   None,       "Missing crucial context"),
}

def clean_ocr(text):
    noise = ["This business uses a secure service from Meta","Tap to learn more","manage this chat"]
    lines = text.split("\n"); out = []
    for line in lines:
        line = line.strip()
        if not line or len(line) <= 2: continue
        if len(line) == 5 and line[2] == ":" and line[:2].isdigit(): continue
        if any(n in line for n in noise): continue
        if line in ("Fact Check","FactCheck","Today","Yesterday"): continue
        out.append(line)
    return "\n".join(out).strip()

def verdict_block(rating):
    label, bar, subtitle = RATINGS_MAP.get(rating, ("UNVERIFIABLE", None, "Cannot be confirmed"))
    lines = [f"*{label}*", f"_{subtitle}_"]
    if bar: lines.append(f"`{bar} FALSE→TRUE`")
    return "\n".join(lines)

def build_meter(r):
    return verdict_block(r)

def meter_visual(r):
    patterns = {"TRUE":(0,10),"MOSTLY TRUE":(2,8),"HALF TRUE":(5,5),"MOSTLY FALSE":(7,3),"FALSE":(9,1),"PANTS ON FIRE":(10,0)}
    labels = {"TRUE":"✅ VERIFIED TRUE","MOSTLY TRUE":"🟢 MOSTLY TRUE","HALF TRUE":"🟡 HALF TRUE","MOSTLY FALSE":"🟠 MOSTLY FALSE","FALSE":"❌ FALSE","PANTS ON FIRE":"🔥 PANTS ON FIRE","UNVERIFIABLE":"❓ UNVERIFIABLE","MISLEADING":"⚠️ MISLEADING","NEEDS CONTEXT":"📌 NEEDS CONTEXT"}
    if r not in patterns: return labels.get(r, r)
    red, green = patterns[r]
    bar = "🟥" * red + "🟩" * green
    return f"\n{bar}\n{labels[r]}\n"

def html_text(html, lim=2000):
    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.t, self.s = [], False; self.b = {"script","style","nav","footer","header","aside"}
        def handle_starttag(self, tag, _):
            if tag in self.b: self.s = True
        def handle_endtag(self, tag):
            if tag in self.b: self.s = False
        def handle_data(self, d):
            if not self.s and d.strip(): self.t.append(d.strip())
    p = P(); p.feed(html); return " ".join(p.t)[:lim]

def fetch(url, timeout=12):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status(); return html_text(r.text)
    except Exception as e: log.warning("fetch %s: %s", url, e); return ""

def download_media(mid):
    try:
        log.info(f"Downloading media ID: {mid}")
        r = requests.get(f"https://graph.facebook.com/v19.0/{mid}", headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, timeout=10)
        r.raise_for_status()
        media_url = r.json()["url"]
        r2 = requests.get(media_url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, timeout=30)
        r2.raise_for_status()
        log.info(f"Downloaded {len(r2.content)} bytes")
        return r2.content
    except Exception as e: log.error(f"Media download failed: {e}"); return None

OCR_PROMPT = "Extract ALL text verbatim from this image. Then in 2 sentences describe what it depicts. Note any signs of manipulation."

_OCR_REFUSALS = ["i'm sorry", "i'm unable", "i cannot", "i can't", "unable to extract", "cannot extract", "can't extract", "no text", "no visible text"]

def _is_ocr_refusal(text):
    t = text.lower()
    return any(p in t for p in _OCR_REFUSALS)

def ocr_image(b):
    b64 = base64.b64encode(b).decode()
    # Try Claude first
    if ANTHROPIC_KEY:
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":[
                    {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
                    {"type":"text","text":OCR_PROMPT}
                ]}]}, timeout=30)
            if _is_credit_error(r.status_code, r.text):
                send_admin_alert("anthropic", f"Anthropic API credits exhausted (HTTP {r.status_code}). OCR falling back to OpenAI.")
                log.error(f"Anthropic credit error in OCR: {r.status_code}")
            else:
                r.raise_for_status()
                result = r.json()["content"][0]["text"].strip()
                if result and not _is_ocr_refusal(result):
                    return result
                log.info("OCR Claude: no usable text extracted")
        except Exception as e:
            log.warning("OCR Claude failed (%s), trying OpenAI...", e)
    # Fallback: OpenAI gpt-4o-mini vision
    if OPENAI_API_KEY:
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "max_tokens": 1500, "messages": [{"role": "user", "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]}]}, timeout=30)
            if _is_credit_error(r.status_code, r.text):
                send_admin_alert("openai", f"OpenAI API quota exceeded (HTTP {r.status_code}). OCR unavailable.")
                log.error(f"OpenAI credit error in OCR: {r.status_code}")
            else:
                r.raise_for_status()
                result = r.json()["choices"][0]["message"]["content"].strip()
                if result and not _is_ocr_refusal(result):
                    return result
                log.info("OCR OpenAI: no usable text extracted (refusal or empty)")
        except Exception as e:
            log.error("OCR OpenAI failed: %s", e)
    return ""

def transcribe(b, mime):
    log.info(f"Transcribing {len(b)} bytes, mime: {mime}")
    if OPENAI_API_KEY:
        try:
            ext = {"audio/ogg":"ogg","audio/mpeg":"mp3","video/mp4":"mp4"}.get(mime, "ogg")
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
                f.write(b); path = f.name
            with open(path, "rb") as f:
                r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": (f"a.{ext}", f, mime)},
                    data={"model": "whisper-1"}, timeout=60)
            os.unlink(path)
            if _is_credit_error(r.status_code, r.text):
                send_admin_alert("openai", f"OpenAI API quota exceeded (HTTP {r.status_code}). Audio transcription unavailable.")
                log.error(f"OpenAI credit error in Whisper: {r.status_code}")
                raise Exception(f"OpenAI quota error {r.status_code}")
            r.raise_for_status()
            transcript = r.json().get("text", "").strip()
            log.info(f"Whisper success: {len(transcript)} chars")
            return transcript
        except Exception as e: log.error(f"Whisper failed: {e}")
    log.info("Trying Claude audio fallback...")
    try:
        b64 = base64.b64encode(b).decode()
        media = {"audio/ogg":"audio/ogg","audio/mpeg":"audio/mpeg","video/mp4":"video/mp4"}.get(mime, "audio/ogg")
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":[
                {"type":"text","text":"Transcribe all spoken words. Return only the transcript."},
                {"type":"image","source":{"type":"base64","media_type":media,"data":b64}}
            ]}]}, timeout=60)
        r.raise_for_status()
        transcript = r.json()["content"][0]["text"].strip()
        log.info(f"Claude transcription success: {len(transcript)} chars")
        return transcript
    except Exception as e: log.error(f"Claude transcribe failed: {e}"); return ""

def extract_video_frames(video_bytes, num_frames=2):
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(video_bytes); video_path = f.name
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = total_frames / fps if fps > 0 else 0
        frame_indices = [int(total_frames * i / num_frames) for i in range(num_frames)]
        frames = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx); ret, frame = cap.read()
            if ret:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                buffer = io.BytesIO(); img.save(buffer, format="JPEG", quality=70)
                frames.append(buffer.getvalue())
        cap.release(); os.unlink(video_path)
        log.info(f"Extracted {len(frames)} frames (duration: {duration:.1f}s)")
        return frames, duration
    except Exception as e: log.error("Frame extraction: %s", e); return [], 0

def analyze_video_frames(frames):
    try:
        if not frames: return ""
        content = [{"type":"text","text":"Extract ALL visible text, describe what's shown, identify people/locations/events mentioned, note any manipulation signs."}]
        for frame_bytes in frames[:2]:
            b64 = base64.b64encode(frame_bytes).decode()
            content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}})
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":content}]},
            timeout=45)
        r.raise_for_status(); return r.json()["content"][0]["text"].strip()
    except Exception as e: log.error("Video frame analysis: %s", e); return ""

def _cobalt_download(url):
    """
    Platform-specific downloaders:
    - TikTok: 7scorp /index endpoint (confirmed working)
    - Instagram: yt-dlp handles this (handled in _ytdlp_download)
    - Facebook: yt-dlp with cookies (handled in _ytdlp_download)
    - YouTube: yt-dlp (handled in _ytdlp_download)
    - Twitter/X: vikas5914 /twitter endpoint
    """
    if not RAPIDAPI_KEY:
        log.warning("RAPIDAPI_KEY not set")
        return None, ""

    # TikTok — use 7scorp /index (confirmed working)
    if "tiktok.com" in url:
        try:
            host = "tiktok-downloader-download-tiktok-videos-without-watermark.p.rapidapi.com"
            headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": host}
            log.info(f"Trying 7scorp TikTok downloader for: {url}")
            r = requests.get(
                f"https://{host}/index",
                headers=headers,
                params={"url": url},
                timeout=20
            )
            r.raise_for_status()
            data = r.json()
            log.info(f"7scorp response: {str(data)[:200]}")
            video_urls = data.get("video", [])
            if video_urls:
                content = _try_download_url(video_urls[0], "7scorp-TikTok")
                if content:
                    title = data.get("title", "") or data.get("desc", "") or ""
                    return content, title
        except Exception as e:
            log.error(f"7scorp TikTok failed: {e}")

    # Twitter/X — use vikas5914 /twitter endpoint
    if "twitter.com" in url or "x.com" in url:
        try:
            host = "fastest-social-video-and-image-downloader.p.rapidapi.com"
            headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": host}
            log.info(f"Trying vikas5914 Twitter downloader for: {url}")
            r = requests.get(
                f"https://{host}/twitter",
                headers=headers,
                params={"url": url},
                timeout=20
            )
            r.raise_for_status()
            data = r.json()
            log.info(f"vikas5914 response: {str(data)[:200]}")
            if data.get("success"):
                video_url, title = _extract_video_url(data)
                if video_url:
                    content = _try_download_url(video_url, "vikas5914-Twitter")
                    if content:
                        return content, title
        except Exception as e:
            log.error(f"vikas5914 Twitter failed: {e}")

    # Facebook — use vikas5914 /facebook endpoint
    if "facebook.com" in url or "fb.watch" in url:
        try:
            host = "fastest-social-video-and-image-downloader.p.rapidapi.com"
            headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": host}
            log.info(f"Trying vikas5914 Facebook downloader for: {url}")
            r = requests.get(
                f"https://{host}/facebook",
                headers=headers,
                params={"url": url},
                timeout=20
            )
            r.raise_for_status()
            data = r.json()
            log.info(f"vikas5914 Facebook response: {str(data)[:200]}")
            if data.get("success"):
                video_url, title = _extract_video_url(data)
                if video_url:
                    content = _try_download_url(video_url, "vikas5914-Facebook")
                    if content:
                        return content, title
        except Exception as e:
            log.error(f"vikas5914 Facebook failed: {e}")

    # Instagram & everything else — fall through to yt-dlp
    return None, ""


def _ytdlp_download(url):
    """yt-dlp with cookies + spoofed headers."""
    cookies_file = None
    try:
        # Write Facebook cookies to temp file if available
        cookies_b64 = FB_COOKIES_B64 if "facebook.com" in url or "fb.watch" in url else (IG_COOKIES_B64 if "instagram.com" in url else "")
        if cookies_b64:
            import base64 as b64mod
            cookies_data = b64mod.b64decode(cookies_b64).decode("utf-8")
            cookies_file = tempfile.mktemp(suffix=".txt")
            with open(cookies_file, "w") as cf:
                cf.write(cookies_data)
            log.info(f"Using cookies file for yt-dlp: {url[:50]}")
        temp_path = tempfile.mktemp(suffix=".mp4")
        ydl_opts = {
            "format": "worst[ext=mp4]/worst/worst",
            "outtmpl": temp_path,
            "quiet": True,
            "no_warnings": True,
            "max_filesize": 30 * 1024 * 1024,
            "socket_timeout": 30,
            "retries": 3,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            "extractor_args": {
                "facebook": {"extract_from_video_page": ["1"]},
            },
            "ignoreerrors": False,
        }
        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, ""
            video_path = ydl.prepare_filename(info)
            if not os.path.exists(video_path):
                video_path = temp_path if os.path.exists(temp_path) else None
            if not video_path:
                return None, ""
            with open(video_path, "rb") as f:
                video_bytes = f.read()
            try:
                os.unlink(video_path)
            except Exception:
                pass
            title = info.get("title", "")
            description = info.get("description", "")[:200] if info.get("description") else ""
            log.info(f"yt-dlp downloaded: {title[:50]}")
            return video_bytes, f"{title}\n{description}".strip()
    except Exception as e:
        log.error(f"yt-dlp failed: {e}")
        return None, ""
    finally:
        if cookies_file and os.path.exists(cookies_file):
            try: os.unlink(cookies_file)
            except: pass

def _fb_ig_post_scrape(url):
    """Scrape a specific Facebook/Instagram POST URL to get full post text and post image.

    Uses specialised crawlers that FB/IG serve correct og: tags to:
      • facebookexternalhit — FB's own link-preview bot (gets post-specific og:image)
      • WhatsApp preview bot — the same UA WhatsApp itself uses for link cards

    For POST URLs (containing /posts/, /photo, /p/, /share/ etc.) the og:image
    returned is the actual post image, not the page profile picture.
    """
    import html as _html_mod
    POST_INDICATORS = ['/posts/', '/photo', '/p/', '/share/', 'story.php', 'fbid=', 'story_fbid=']
    is_post_url = any(s in url for s in POST_INDICATORS)

    UAS = [
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_udata.php)",
        "WhatsApp/2.24.6.77 A",
        "Twitterbot/1.0",
    ]
    for ua in UAS:
        try:
            r = requests.get(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
                             timeout=14, allow_redirects=True)
            if not r.ok:
                continue
            html = r.text
            result = {"is_post": is_post_url}
            for prop, key in [("og:title","title"), ("og:description","description"), ("og:image","image_url")]:
                # Use exact-match patterns with required quotes so og:image doesn't
                # accidentally match og:image:alt (which contains text, not a URL)
                for pat in [
                    rf'<meta[^>]+property=["\'](?:{re.escape(prop)})["\'][^>]+content=["\']([^"\']+)["\']',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:{re.escape(prop)})["\']'
                ]:
                    m = re.search(pat, html, re.I)
                    if m:
                        result[key] = _html_mod.unescape(m.group(1).strip())
                        break
            if result.get("description") or result.get("image_url"):
                log.info(f"FB/IG externalhit ({ua.split('/')[0]}): desc={bool(result.get('description'))} img={bool(result.get('image_url'))}")
                return result
        except Exception as e:
            log.debug(f"FB/IG externalhit failed ({ua[:20]}): {e}")
    return {"is_post": is_post_url}

def _og_metadata(url):
    """Last resort: extract Open Graph tags (title, description, image OCR) from the page."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        r.raise_for_status(); html = r.text; parts = []
        for prop in ("og:title","og:description","twitter:title","twitter:description"):
            m = re.search(rf'<meta[^>]+(?:property|name)=["\']?{re.escape(prop)}["\']?[^>]+content=["\']([^"\']+)["\']', html, re.I)
            if not m:
                m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']?{re.escape(prop)}["\']?', html, re.I)
            if m: parts.append(m.group(1).strip())
        # Also try og:image OCR to capture thumbnail headline text
        import html as _html3
        for pat in [r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']']:
            m = re.search(pat, html, re.I)
            if m:
                og_img = _html3.unescape(m.group(1).strip())
                if og_img.startswith("http"):
                    try:
                        img_r = requests.get(og_img, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
                        if img_r.ok and len(img_r.content) > 500:
                            ocr = ocr_image(img_r.content)
                            if ocr and len(ocr) > 20:
                                parts.append(f"Image text: {ocr[:300]}")
                                log.info(f"og:image OCR in _og_metadata: {ocr[:80]}")
                    except Exception:
                        pass
                break
        if parts:
            metadata = " — ".join(dict.fromkeys(parts))
            log.info(f"OG metadata: {metadata[:100]}")
            return metadata
    except Exception as e: log.error(f"OG metadata failed: {e}")
    return ""

def download_video_url(url):
    """Cobalt API → yt-dlp → OG metadata fallback."""
    video_bytes, metadata = _cobalt_download(url)
    if video_bytes: return video_bytes, metadata
    log.info("Cobalt failed, trying yt-dlp...")
    video_bytes, metadata = _ytdlp_download(url)
    if video_bytes: return video_bytes, metadata
    log.info("yt-dlp failed, extracting OG metadata...")
    return None, _og_metadata(url)

def google_fc(query):
    try:
        r = requests.get(GOOGLE_FC_URL, params={"key":GOOGLE_API_KEY,"query":query[:200],"pageSize":8}, timeout=10)
        r.raise_for_status(); out = []
        for c in r.json().get("claims", []):
            for rv in c.get("claimReview", []):
                out.append({"source":rv.get("publisher",{}).get("name",""),"rating":rv.get("textualRating",""),"claim":c.get("text","")[:200],"url":rv.get("url","")})
        return out
    except Exception as e: log.error("GFC: %s", e); return []


def parse_custom_sources():
    """Parse CUSTOM_SOURCES env var into list of (name, url_template) tuples."""
    if not CUSTOM_SOURCES_RAW.strip():
        return []
    sources = []
    for entry in CUSTOM_SOURCES_RAW.split(","):
        entry = entry.strip()
        if "|" not in entry:
            continue
        parts = entry.split("|", 1)
        if len(parts) == 2:
            name, url_tpl = parts[0].strip(), parts[1].strip()
            if name and url_tpl.startswith("http"):
                sources.append((name, url_tpl))
    return sources

def enabled_sources():
    """Return list of all currently enabled source names."""
    sources = []
    if SRC_SNOPES:          sources.append("Snopes")
    if SRC_FULLFACT:        sources.append("FullFact")
    if SRC_FACTCHECKORG:    sources.append("FactCheck.org")
    if SRC_POLITIFACT:      sources.append("PolitiFact")
    if SRC_AFP:             sources.append("AFP")
    if SRC_ALJAZEERA:       sources.append("Al Jazeera")
    if SRC_MEE:             sources.append("Middle East Eye")
    if SRC_NOVARA:          sources.append("Novara Media")
    if SRC_CANARY:          sources.append("The Canary")
    if SRC_ZETEO:           sources.append("Zeteo")
    if SRC_YENISAFAK:       sources.append("Yeni Safak")
    if SRC_972MAG:          sources.append("972 Magazine")
    if SRC_MONDOWEISS:      sources.append("Mondoweiss")
    if SRC_EINTIFADA:       sources.append("Electronic Intifada")
    if SRC_INTERCEPT:       sources.append("The Intercept")
    if SRC_HAARETZ:         sources.append("Haaretz")
    if SRC_DDN:             sources.append("Double Down News")
    if SRC_DEMOCRACYNOW:    sources.append("Democracy Now")
    if SRC_GRAYZONE:        sources.append("The Grayzone")
    if SRC_MINTPRESS:       sources.append("MintPress News")
    if SRC_PSC:             sources.append("Palestine Solidarity Campaign")
    if SRC_OWENJONES:       sources.append("Owen Jones (Twitter)")
    if SRC_OWENJONES_SUB:   sources.append("Owen Jones (Substack)")
    if SRC_CORBYN:          sources.append("Jeremy Corbyn (Twitter)")
    if SRC_CORBYN_SITE:     sources.append("Jeremy Corbyn (Site)")
    if SRC_ZARASULTANA:     sources.append("Zara Sultana (Twitter)")
    if SRC_SULTANA_SITE:    sources.append("Zara Sultana (Site)")
    if SRC_FINKELSTEIN:     sources.append("Norman Finkelstein (Twitter)")
    if SRC_FINKELSTEIN_SUB: sources.append("Norman Finkelstein (Substack)")
    if SRC_CODEPINK:        sources.append("CodePink (Twitter)")
    if SRC_CODEPINK_SITE:   sources.append("CodePink (Site)")
    if SRC_MOATS:           sources.append("Moats/Galloway (Twitter)")
    if SRC_MOATS_YT:        sources.append("Moats (YouTube)")
    if SRC_GALLOWAY_SITE:   sources.append("George Galloway (Site)")
    if SRC_DDN_YT:          sources.append("Double Down News (YouTube)")
    if SRC_SUBSTACK:        sources.append("Substack")
    if SRC_BBC:             sources.append("BBC News")
    if SRC_REUTERS:         sources.append("Reuters")
    if SRC_AP:              sources.append("AP News")
    if SRC_GUARDIAN:        sources.append("The Guardian")
    if SRC_CNN:             sources.append("CNN")
    for name, _ in parse_custom_sources():
        sources.append(f"{name} (custom)")
    return sources

def _fetch_source(name, url):
    """Fetch a single source — returns (name, text) or None."""
    try:
        txt = fetch(url, timeout=7)
        if txt and len(txt) > 150:
            return (name, txt[:400])
    except Exception as e:
        log.warning(f"Scrape failed {name}: {e}")
    return None

def scrape_sites(query):
    # Collapse newlines to spaces so search URLs don't contain %0A (causes 403/404)
    query_flat = " ".join(query.split())
    q = quote_plus(query_flat[:100])
    qt = quote_plus(query_flat[:80])

    # FAST TIER — fact-check DBs and major news outlets (run first, block until done)
    fast = []
    if SRC_SNOPES:        fast.append(("Snopes",              f"https://www.snopes.com/?s={q}"))
    if SRC_FULLFACT:      fast.append(("FullFact",            f"https://fullfact.org/search/?q={q}"))
    if SRC_FACTCHECKORG:  fast.append(("FactCheck.org",       f"https://www.factcheck.org/?s={q}"))
    if SRC_POLITIFACT:    fast.append(("PolitiFact",          f"https://www.politifact.com/search/?q={q}"))
    if SRC_AFP:           fast.append(("AFP Fact Check",      f"https://factcheck.afp.com/?q={q}"))
    if SRC_ALJAZEERA:     fast.append(("Al Jazeera",          f"https://www.aljazeera.com/search/{qt}"))
    if SRC_MEE:           fast.append(("Middle East Eye",     f"https://www.middleeasteye.net/search?search_api_fulltext={qt}"))
    if SRC_NOVARA:        fast.append(("Novara Media",        f"https://novaramedia.com/?s={q}"))
    if SRC_CANARY:        fast.append(("The Canary",          f"https://thecanary.co/?s={q}"))
    if SRC_ZETEO:         fast.append(("Zeteo",               f"https://zeteo.com/?s={q}"))
    if SRC_YENISAFAK:     fast.append(("Yeni Safak",          f"https://www.yenisafak.com/en/search?q={qt}"))
    if SRC_972MAG:        fast.append(("972 Magazine",        f"https://www.972mag.com/?s={q}"))
    if SRC_MONDOWEISS:    fast.append(("Mondoweiss",          f"https://mondoweiss.net/?s={q}"))
    if SRC_EINTIFADA:     fast.append(("Electronic Intifada", f"https://electronicintifada.net/search/site/{qt}"))
    if SRC_INTERCEPT:     fast.append(("The Intercept",       f"https://theintercept.com/search/?s={q}"))
    if SRC_HAARETZ:       fast.append(("Haaretz",             f"https://www.haaretz.com/search/#q={qt}"))
    if SRC_DDN:           fast.append(("Double Down News",    f"https://doubledownnews.com/?s={q}"))
    if SRC_DEMOCRACYNOW:  fast.append(("Democracy Now",       f"https://www.democracynow.org/search?q={qt}"))
    if SRC_GRAYZONE:      fast.append(("The Grayzone",        f"https://thegrayzone.com/?s={q}"))
    if SRC_MINTPRESS:     fast.append(("MintPress News",      f"https://www.mintpressnews.com/?s={q}"))
    if SRC_PSC:           fast.append(("Palestine Solidarity", f"https://palestinecampaign.org/?s={q}"))
    # Mainstream news
    if SRC_BBC:      fast.append(("BBC News",  f"https://www.bbc.co.uk/search?q={qt}&d=NEWS_PS"))
    if SRC_REUTERS:  fast.append(("Reuters",    f"https://www.reuters.com/search/news?blob={qt}"))
    if SRC_AP:       fast.append(("AP News",    f"https://apnews.com/search?q={qt}"))
    if SRC_GUARDIAN: fast.append(("Guardian",   f"https://www.theguardian.com/search?q={qt}"))
    if SRC_CNN:      fast.append(("CNN",        f"https://edition.cnn.com/search?q={qt}"))

    # SLOW TIER — personalities, substacks, nitter, YouTube (parallel, 5s timeout)
    slow = []
    if SRC_OWENJONES:       slow.append(("Owen Jones Twitter",    f"https://nitter.poast.org/OwenJones84/search?q={qt}"))
    if SRC_OWENJONES_SUB:   slow.append(("Owen Jones Substack",   f"https://owenjones.substack.com/search?query={qt}"))
    if SRC_CORBYN:          slow.append(("Corbyn Twitter",        f"https://nitter.poast.org/jeremycorbyn/search?q={qt}"))
    if SRC_CORBYN_SITE:     slow.append(("Corbyn Site",           f"https://jeremycorbyn.org.uk/?s={q}"))
    if SRC_ZARASULTANA:     slow.append(("Zara Sultana Twitter",  f"https://nitter.poast.org/zarasultana/search?q={qt}"))
    if SRC_SULTANA_SITE:    slow.append(("Zara Sultana Site",     f"https://zarasultana.co.uk/?s={q}"))
    if SRC_FINKELSTEIN:     slow.append(("Finkelstein Twitter",   f"https://nitter.poast.org/normfinkelstein/search?q={qt}"))
    if SRC_FINKELSTEIN_SUB: slow.append(("Finkelstein Substack",  f"https://normfinkelstein.substack.com/search?query={qt}"))
    if SRC_CODEPINK:        slow.append(("CodePink Twitter",      f"https://nitter.poast.org/codepink/search?q={qt}"))
    if SRC_CODEPINK_SITE:   slow.append(("CodePink Site",         f"https://www.codepink.org/search?q={qt}"))
    if SRC_MOATS:           slow.append(("Moats Twitter",         f"https://nitter.poast.org/georgegalloway/search?q={qt}"))
    if SRC_MOATS_YT:        slow.append(("Moats YouTube",         f"https://www.youtube.com/@MoatsTV/search?query={qt}"))
    if SRC_GALLOWAY_SITE:   slow.append(("Galloway Site",         f"https://www.georgegalloway.com/?s={q}"))
    if SRC_DDN_YT:          slow.append(("DDN YouTube",           f"https://www.youtube.com/@DoubleDownNews/search?query={qt}"))
    if SRC_SUBSTACK:        slow.append(("Substack",              f"https://substack.com/search?q={qt}"))

    # Custom sources from CUSTOM_SOURCES Railway variable
    for name, url_tpl in parse_custom_sources():
        try:
            custom_url = url_tpl.replace("{q}", q).replace("{qt}", qt)
            slow.append((name, custom_url))
        except Exception as e:
            log.warning(f"Custom source {name} URL error: {e}")

    results = []

    # Run fast tier in parallel threads
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_source, name, url): name for name, url in fast}
        for future in futures:
            try:
                r = future.result(timeout=9)
                if r:
                    results.append(f"[{r[0]}]: {r[1]}")
            except Exception:
                pass

    # Run slow tier in parallel threads with shorter timeout
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_source, name, url): name for name, url in slow}
        for future in futures:
            try:
                r = future.result(timeout=6)
                if r:
                    results.append(f"[{r[0]}]: {r[1]}")
            except Exception:
                pass

    log.info(f"Scraped {len(results)} sources")
    return "\n\n".join(results), [r.split("]")[0].replace("[","").strip() for r in results]


def estimate_cost(st):
    base = {"text":0.0085,"url":0.0095,"image":0.0110,"audio":0.0120,"video":0.0180,"document":0.0095}
    return base.get(st, 0.0085)

def _parse_json_result(text):
    s = text.find("{"); e = text.rfind("}") + 1
    if s < 0 or e <= s: return None
    raw = text[s:e]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raw = re.sub(r'[\x00-\x1f\x7f]', lambda m: '' if m.group() not in '\n\r\t' else m.group(), raw)
        return json.loads(raw)

ANALYSE_JSON_SCHEMA = (
    '{"rating":"TRUE|MOSTLY TRUE|HALF TRUE|MOSTLY FALSE|FALSE|PANTS ON FIRE|UNVERIFIABLE|MISLEADING|NEEDS CONTEXT",'
    '"lenz_score":7,'
    '"verdict":"2-3 sentence verdict with evidence","key_facts":["fact1","fact2","fact3","fact4"],'
    '"context":"background context","red_flags":["flag1","flag2"],"media_bias":"bias note or empty",'
    '"sources":["Name — URL","Name — URL","Name — URL","Name — URL"],"confidence":"HIGH|MEDIUM|LOW","confidence_reason":"reason"}'
)

def neutralize_claim(raw_text):
    """Strip emotional framing and return the neutral, testable core of a claim."""
    if not ANTHROPIC_KEY:
        return raw_text
    prompt = (
        "Strip ALL emotional language, sensationalism, and partisan framing from the text below. "
        "Return ONLY the neutral factual core as plain text — no bullet points, no preamble. "
        "If there are multiple claims, separate them with a newline.\n\n"
        f"TEXT:\n{raw_text[:1200]}"
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20)
        r.raise_for_status()
        result = r.json()["content"][0]["text"].strip()
        if result and len(result) > 10:
            log.info(f"Neutralized: {result[:80]}")
            return result
    except Exception as e:
        log.warning(f"Neutralize failed: {e}")
    return raw_text


def extract_claims(text):
    """Split text into individual checkable factual claims (max 4). Returns list of strings."""
    if len(text) < 60:
        return [text]
    if not ANTHROPIC_KEY:
        return [text]
    prompt = (
        "Identify the distinct, independently checkable factual claims in the text below. "
        "Return a JSON array of strings — one string per claim, self-contained and testable. "
        "Maximum 4 claims. If there is only one claim return a single-element array. "
        "Ignore pure opinion, emotion, and non-falsifiable statements.\n\n"
        f"TEXT:\n{text[:1500]}\n\n"
        'Respond ONLY with a JSON array, e.g.: ["Claim one", "Claim two"]'
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20)
        r.raise_for_status()
        raw = r.json()["content"][0]["text"].strip()
        s = raw.find("["); e = raw.rfind("]") + 1
        if s >= 0 and e > s:
            claims = json.loads(raw[s:e])
            claims = [c.strip() for c in claims if isinstance(c, str) and c.strip()][:4]
            if claims:
                log.info(f"Extracted {len(claims)} claim(s)")
                return claims
    except Exception as e:
        log.warning(f"Claim extraction failed: {e}")
    return [text]


def _claude_call(prompt, model="claude-haiku-4-5-20251001", max_tokens=600, system=None):
    """Single Claude API call. Returns text or None."""
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json=body, timeout=45)
        if _is_credit_error(r.status_code, r.text):
            send_admin_alert("anthropic", f"Anthropic API credits exhausted or quota exceeded (HTTP {r.status_code}). Fact-checking is degraded — top up at console.anthropic.com.")
            log.error(f"Anthropic credit error {r.status_code}: {r.text[:200]}")
            return None
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        log.warning(f"_claude_call {model}: {e}")
        return None


def claude_analyse(claim, google, scraped, st):
    g = "\n".join([f"• {x['source']} [{x['rating']}]: {x['claim']}\n  {x['url']}" for x in google[:5]])
    evidence = (
        f"GOOGLE FACT CHECK:\n{g or 'No matches.'}\n\n"
        f"FACT-CHECK SITES:\n{scraped[:1500] or 'No results.'}"
    )

    # ── Step 1 & 2: Debate — pro and con in parallel (Haiku, fast + cheap) ──
    pro_text, con_text = "", ""
    if ANTHROPIC_KEY:
        pro_prompt = (
            "You are a fact-checker. Using ONLY the evidence provided, make the strongest "
            "honest case that the claim below is TRUE or mostly accurate. Be specific, cite sources. "
            "3-4 sentences.\n\n"
            f"CLAIM: {claim[:800]}\n\n{evidence}"
        )
        con_prompt = (
            "You are a fact-checker. Using ONLY the evidence provided, make the strongest "
            "honest case that the claim below is FALSE or misleading. Be specific, cite sources. "
            "3-4 sentences.\n\n"
            f"CLAIM: {claim[:800]}\n\n{evidence}"
        )
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_pro = ex.submit(_claude_call, pro_prompt, "claude-haiku-4-5-20251001", 500)
            f_con = ex.submit(_claude_call, con_prompt, "claude-haiku-4-5-20251001", 500)
            pro_text = f_pro.result() or ""
            con_text = f_con.result() or ""
        log.info(f"Debate: pro={len(pro_text)}ch con={len(con_text)}ch")

    # ── Step 3: Synthesis — Sonnet reads both sides and produces verdict ──
    debate_section = ""
    if pro_text or con_text:
        debate_section = (
            f"\n\nSTRUCTURED DEBATE:\n"
            f"CASE FOR TRUE:\n{pro_text}\n\n"
            f"CASE FOR FALSE/MISLEADING:\n{con_text}"
        )

    synth_prompt = (
        f"Fact-check this claim (source: {st}). "
        f"You have evidence AND a structured pro/con debate. Synthesize everything into a balanced verdict.\n\n"
        f"CLAIM:\n\"\"\"{claim[:1200]}\"\"\"\n\n"
        f"{evidence}{debate_section}\n\n"
        f"Respond ONLY with valid JSON:\n{ANALYSE_JSON_SCHEMA}"
    )

    if ANTHROPIC_KEY:
        for attempt in range(2):
            try:
                r = requests.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-sonnet-4-6", "max_tokens": 2000, "system": SYSTEM,
                          "messages": [{"role": "user", "content": synth_prompt}]},
                    timeout=55)
                if _is_credit_error(r.status_code, r.text):
                    send_admin_alert("anthropic", f"Anthropic API credits exhausted (HTTP {r.status_code}). Falling back to OpenAI for synthesis.")
                    log.error(f"Anthropic credit error in synthesis: {r.status_code}")
                    break  # skip retry, go straight to OpenAI fallback
                r.raise_for_status()
                result = _parse_json_result(r.json()["content"][0]["text"])
                if result:
                    if pro_text: result["_debate_pro"] = pro_text
                    if con_text: result["_debate_con"] = con_text
                    return result
            except Exception as e:
                log.warning("Claude synthesis attempt %d: %s", attempt+1, e)
                if attempt == 0:
                    import time as _t; _t.sleep(1)

    # Fallback: OpenAI gpt-4o for synthesis
    if OPENAI_API_KEY:
        try:
            log.info("Falling back to OpenAI for synthesis...")
            r = requests.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": "gpt-4o", "max_tokens": 2000,
                      "messages": [{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": synth_prompt}]},
                timeout=55)
            if _is_credit_error(r.status_code, r.text):
                send_admin_alert("openai", f"OpenAI API quota exceeded (HTTP {r.status_code}). Both AI providers unavailable.")
                log.error(f"OpenAI credit error in synthesis: {r.status_code}")
            else:
                r.raise_for_status()
                result = _parse_json_result(r.json()["choices"][0]["message"]["content"])
                if result:
                    log.info("OpenAI synthesis succeeded")
                    return result
        except Exception as e:
            log.error("OpenAI analyse: %s", e)

    return {"rating": "UNVERIFIABLE", "verdict": "Analysis failed — both AI providers unavailable. Please try again shortly.",
            "key_facts": [], "context": "", "red_flags": [], "media_bias": "",
            "sources": ["Google FC — https://toolbox.google.com/factcheck/explorer",
                        "Snopes — https://www.snopes.com", "FullFact — https://fullfact.org"],
            "confidence": "LOW", "confidence_reason": "AI provider error"}

def fmt_report(claim, a, st, cost, used_sources=None):
    rating = a.get("rating", "UNVERIFIABLE").upper()
    src_word = {"text":"Text","image":"Image","audio":"Voice","video":"Video","url":"Article","document":"Document"}
    badge_map = {"TRUE":"✅  VERDICT: TRUE","MOSTLY TRUE":"🟢  VERDICT: MOSTLY TRUE","HALF TRUE":"🟡  VERDICT: HALF TRUE","MOSTLY FALSE":"🟠  VERDICT: MOSTLY FALSE","FALSE":"❌  VERDICT: FALSE","PANTS ON FIRE":"🔥  VERDICT: PANTS ON FIRE","UNVERIFIABLE":"❓  VERDICT: UNVERIFIABLE","MISLEADING":"⚠️  VERDICT: MISLEADING","NEEDS CONTEXT":"📌  VERDICT: NEEDS CONTEXT"}
    badge = badge_map.get(rating, f"VERDICT: {rating}")
    lines = [f"*FACTCHECK PRO*  |  {src_word.get(st,'Text')}","",f"*{badge}*",meter_visual(rating),"","*CLAIM*",f"_{claim[:280]}_","","*ANALYSIS*",a.get("verdict",""),""]
    if a.get("key_facts"): lines += ["*KEY FACTS*"] + [f"{i}. {f}" for i,f in enumerate(a["key_facts"][:4],1)] + [""]
    if a.get("context"): lines += ["*BACKGROUND*", a["context"][:400], ""]
    if a.get("red_flags"): lines += ["*RED FLAGS*"] + [f"• {f}" for f in a["red_flags"][:3]] + [""]
    if a.get("media_bias"): lines += ["*BIAS NOTE*", a["media_bias"][:200], ""]
    score = a.get("lenz_score")
    if score is not None:
        try:
            s = int(score)
            filled = "█" * s + "░" * (10 - s)
            lines += [f"*TRUTH SCORE*  `{filled}` {s}/10", ""]
        except (ValueError, TypeError):
            pass
    conf = a.get("confidence","LOW")
    conf_icon = {"HIGH":"🟢","MEDIUM":"🟡","LOW":"🔴"}.get(conf,"")
    lines += [f"*CONFIDENCE*  {conf_icon} {conf}", f"_{a.get('confidence_reason','')[:200]}_",""]
    if used_sources:
        lines += ["*SOURCES CONSULTED*"] + [f"• {s}" for s in used_sources[:10]] + [""]
    elif a.get("sources"):
        lines += ["*SOURCES*"] + [f"• {s}" for s in a["sources"][:5]] + [""]
    debate_indicator = "⚖️ pro/con debate" if a.get("_debate_pro") else "single-pass"
    lines += ["─────────────────────────────", f"_Cost: ${cost:.4f}  •  FactCheck Pro v3.2  •  {debate_indicator}_"]
    return "\n".join(lines)

def confirm_msg(st, preview, cost):
    src = {"text":"Text","image":"Image","audio":"Voice Note","video":"Video","url":"Article","document":"Document"}
    HDR = "*━━━━━━━━━━━━━━━━━━━━*"
    return (f"{HDR}\n*FACTCHECK PRO*\n_{src.get(st,st)}_\n{HDR}\n\n*CLAIM PREVIEW*\n_{preview[:180]}_\n\n_Est. cost: ${cost:.4f}_\n\nReply *Y* to fact-check\nReply *N* to cancel")

def send(to, text):
    # Sanitize: remove null bytes and non-BMP unicode that WhatsApp rejects
    text = text.replace("\x00", "").encode("utf-16", "surrogatepass").decode("utf-16")
    for chunk in [text[i:i+4000] for i in range(0,len(text),4000)]:
        try:
            r = requests.post(WHATSAPP_URL,
                json={"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":chunk}},
                headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}","Content-Type":"application/json"},
                timeout=10)
            if not r.ok:
                log.error(f"Send failed {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
        except Exception as e:
            log.error("Send: %s", e)

def run_check(from_num, query, st, img_bytes, cost, video_bytes=None):
    # Show all enabled sources
    all_src = enabled_sources()
    src_preview = ", ".join(all_src[:8])
    if len(all_src) > 8:
        src_preview += f" +{len(all_src)-8} more"
    send(from_num, f"⚙️ Cross-referencing {len(all_src)} sources:\n{src_preview}...")

    # For video content, extract frames before fact-checking
    if st == "video" and video_bytes:
        try:
            send(from_num, "🎞️ Analysing video frames...")
            frames, duration = extract_video_frames(video_bytes, num_frames=4)
            if frames:
                visual_analysis = analyze_video_frames(frames)
                if visual_analysis:
                    query = f"{query}\n\nVISUAL ANALYSIS:\n{visual_analysis}"
                    log.info(f"Visual analysis added: {len(visual_analysis)} chars")
        except Exception as e:
            log.error(f"Frame analysis failed: {e}")

    # ── Claim neutralization (text/audio/url only) ────────────────────────
    if st in ("text", "audio", "url"):
        send(from_num, "🔍 Identifying claims...")
        neutral = neutralize_claim(query)
        if neutral != query:
            log.info(f"Neutralized: {neutral[:80]}")
        query = neutral

    # ── Multi-claim extraction ────────────────────────────────────────────
    claims = extract_claims(query) if st in ("text", "audio", "url") else [query]
    multi = len(claims) > 1
    if multi:
        claim_preview = "\n".join(f"  {i+1}. {c[:100]}" for i, c in enumerate(claims))
        send(from_num, f"📋 Found {len(claims)} claims to check:\n{claim_preview}")

    # ── Scrape sources once, shared across all claims ─────────────────────
    g = google_fc(query)
    sc, used_sources = scrape_sites(query)
    gfc_sources = [x["source"] for x in g if x.get("source")]
    all_used = list(dict.fromkeys(gfc_sources + used_sources))

    # ── Analyse each claim (with pro/con debate) ──────────────────────────
    for i, claim in enumerate(claims):
        if multi:
            send(from_num, f"⚖️ Analysing claim {i+1}/{len(claims)}...")
        a = claude_analyse(claim, g, sc, st)
        report = fmt_report(claim, a, st, cost, all_used)
        if multi:
            send(from_num, f"*— CLAIM {i+1}/{len(claims)} —*\n" + report)
        else:
            send(from_num, report)

def clean_query(q):
    lines = []
    for l in q.split("\n"):
        s = l.strip()
        if not s: continue
        if s.startswith("#"): continue
        if s.startswith("**"): continue
        if s.lower().startswith("text extraction"): continue
        if s.lower().startswith("image description"): continue
        if s.lower().startswith("manipulation"): continue
        if s.lower().startswith("signs of"): continue
        lines.append(s)
    return "\n".join(lines).strip()

def expire_pending():
    now = t.time()
    with pending_lock:
        stale = [k for k,v in pending.items() if now - v.get("timestamp",0) > PENDING_TTL]
        for k in stale:
            log.info(f"Expiring stale pending for {k}")
            del pending[k]

def process(from_num, message):
    msg_id = message.get("id",""); msg_time = int(message.get("timestamp",0))
    with processed_lock:
        if msg_id in processed_ids: return
        if t.time() - msg_time > 300: log.info("Stale message (>5 min), ignored"); return
        processed_ids.add(msg_id)
        if len(processed_ids) > MAX_PROCESSED_IDS:
            to_keep = set(list(processed_ids)[MAX_PROCESSED_IDS//2:])
            processed_ids.clear(); processed_ids.update(to_keep)
    expire_pending()
    msg_type = message.get("type")
    if msg_type == "video":
        send(from_num, "📹 Video detected! Starting processing...")
        log.info("=== VIDEO MESSAGE RECEIVED ===")
    if msg_type == "text":
        body = message["text"]["body"].strip(); body_upper = body.upper()
        is_yn = body_upper in ("YES","Y","NO","N") or (len(body) < 10 and body_upper in ("YES","Y","NO","N"))
        with pending_lock: has_p = from_num in pending; data = pending.get(from_num)
        if has_p and is_yn:
            if body_upper in ("YES","Y"):
                with pending_lock: data = pending.pop(from_num)
                send(from_num, "Starting fact-check...")
                threading.Thread(target=run_check, args=(from_num,data["query"],data["source_type"],data.get("image_bytes"),data["cost"]), daemon=True).start()
                return
            elif body_upper in ("NO","N"):
                with pending_lock: pending.pop(from_num, None)
                send(from_num, "Cancelled."); return
        elif has_p and not is_yn:
            with pending_lock: pending.pop(from_num, None)
            log.info("New content received, clearing stale pending")
    query, source_type, image_bytes = "", "text", None
    if msg_type == "text":
        body = message["text"]["body"].strip()
        urls = [w for w in body.split() if w.startswith("http")]
        if urls:
            url = urls[0]
            # Video platforms — but only treat FB/IG as video if URL pattern suggests it
            video_domains = ["tiktok.com","youtube.com","youtu.be","twitter.com","x.com","rumble.com","bitchute.com","t.me","fb.watch"]
            video_path_hints = ["watch", "video", "reel", "shorts", "clip", "live", "/share/v/"]
            is_fb_ig = any(d in url for d in ["facebook.com","instagram.com"])
            is_video_link = (
                any(d in url for d in video_domains) or
                (is_fb_ig and any(h in url.lower() for h in video_path_hints))
            )
            if is_video_link:
                try:
                    send(from_num, "🎬 Downloading video from URL...")
                    video_bytes, metadata = download_video_url(url)
                    if video_bytes:
                        send(from_num, f"✓ Downloaded ({len(video_bytes)//1024}KB)")
                        parts = []
                        if metadata: parts.append(f"Video: {metadata}")
                        send(from_num, "🎞️ Analysing video frames...")
                        try:
                            frames, duration = extract_video_frames(video_bytes, num_frames=3)
                            if frames:
                                visual = analyze_video_frames(frames)
                                if visual:
                                    parts.append(f"Visual analysis:\n{visual}")
                                    log.info(f"URL video frame analysis: {len(visual)} chars")
                        except Exception as e:
                            log.error(f"URL video frame analysis: {e}")
                        send(from_num, "🎧 Transcribing audio...")
                        try:
                            transcript = transcribe(video_bytes, "video/mp4")
                            if transcript:
                                parts.append(f"Audio: {transcript}")
                                send(from_num, "✓ Got transcript")
                            else:
                                send(from_num, "⚠️ No speech detected or audio transcription unavailable")
                                log.warning("URL video: transcribe() returned empty")
                        except Exception as e:
                            send(from_num, f"⚠️ Transcription failed: {str(e)[:100]}")
                            log.error(f"URL video transcription: {e}")
                        query = "\n\n".join(parts) if parts else f"Video URL: {url}"
                        source_type = "video"
                    elif metadata:
                        send(from_num, "⚠️ Video download not available for this platform — analysing post text instead...")
                        query = f"Social media post: {metadata}\n\nURL: {url}"
                        source_type = "url"
                    else:
                        # Last resort: just use the URL itself as context
                        send(from_num, "⚠️ Could not access video content. Fact-checking based on URL context...")
                        page_text = fetch(url) or ""
                        query = f"Video URL: {url}\n\n{page_text[:600]}" if page_text else f"Video from: {url}"
                        if not query.strip() or query.strip() == f"Video from: {url}":
                            send(from_num, "❌ Could not extract any content from this URL. Please paste the claim as text or send a screenshot.")
                            return
                        source_type = "url"
                except Exception as e:
                    send(from_num, f"❌ Video error: {str(e)[:200]}\n\nTrying page scrape instead...")
                    page_text = fetch(url) or ""
                    query = f"Video URL: {url}\n\n{page_text[:400]}" if page_text else f"Video from: {url}"
                    source_type = "url"
            else:
                send(from_num, "🔍 Analysing post...")
                page_text = ""
                if "facebook.com" in url or "instagram.com" in url:
                    parts = []
                    img_candidates = []  # ordered list of image URLs to try for OCR

                    # ── STEP 1: facebookexternalhit scrape ───────────────────────
                    # FB/IG serve post-specific og:image and full og:description to
                    # their own link-preview crawlers. This is the most reliable way
                    # to get the actual post image (not the page profile picture) and
                    # the full post text without needing cookies or authentication.
                    fb_og = _fb_ig_post_scrape(url)
                    if fb_og.get("description"):
                        parts.append(f"Post text: {fb_og['description'][:1200]}")
                    if fb_og.get("title") and not parts:
                        parts.append(f"Title: {fb_og['title']}")
                    if fb_og.get("image_url") and fb_og["image_url"].startswith("http"):
                        # Only use og:image from post-specific URLs (not page profile pictures)
                        if fb_og.get("is_post", True):
                            img_candidates.append(fb_og["image_url"])
                            log.info(f"FB/IG post og:image: {fb_og['image_url'][:80]}")
                        else:
                            log.info(f"FB/IG page URL — skipping profile og:image")

                    # ── STEP 2: yt-dlp metadata (uploader, and additional text) ──
                    try:
                        cookies_b64 = FB_COOKIES_B64 if "facebook.com" in url else IG_COOKIES_B64
                        cookies_file = None
                        if cookies_b64:
                            import base64 as _b64
                            cookies_data = _b64.b64decode(cookies_b64).decode("utf-8")
                            cookies_file = tempfile.mktemp(suffix=".txt")
                            with open(cookies_file, "w") as cf:
                                cf.write(cookies_data)
                        ydl_opts = {
                            "quiet": True, "no_warnings": True, "skip_download": True,
                            "socket_timeout": 15,
                            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                        }
                        if cookies_file:
                            ydl_opts["cookiefile"] = cookies_file
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                            if info:
                                # Add title only if we don't have description from Step 1
                                title = info.get("title","")
                                if title and title not in ("Facebook","Instagram") and not parts:
                                    parts.append(f"Title: {title}")
                                # Add description if Step 1 didn't get it
                                desc = info.get("description","") or ""
                                if desc and "Post text:" not in "\n".join(parts):
                                    parts.append(f"Post text: {desc[:1200]}")
                                if info.get("uploader"):
                                    parts.append(f"Posted by: {info['uploader']}")
                                log.info(f"yt-dlp: title={title[:50]} desc={bool(desc)} thumb={bool(info.get('thumbnail'))}")
                                # Add yt-dlp thumbnail as fallback image candidate
                                if info.get("thumbnail"):
                                    img_candidates.append(info["thumbnail"])
                                # Also add direct image URLs from yt-dlp
                                raw_url = info.get("url","")
                                if raw_url and any(raw_url.lower().endswith(x) for x in (".jpg",".jpeg",".png",".webp")):
                                    img_candidates.append(raw_url)
                                for fmt in (info.get("formats") or []):
                                    if fmt.get("ext") in ("jpg","jpeg","png","webp") and fmt.get("url"):
                                        img_candidates.append(fmt["url"])
                                # For link-share posts: if description has external article URL,
                                # append its og:image as a further fallback
                                ext_urls = re.findall(r'https?://(?!(?:www\.)?facebook\.com)(?!(?:www\.)?instagram\.com)\S+', desc)
                                if ext_urls:
                                    try:
                                        import html as _html
                                        art_r = requests.get(ext_urls[0], headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                                        if art_r.ok:
                                            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', art_r.text, re.I)
                                            if not m:
                                                m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', art_r.text, re.I)
                                            if m:
                                                art_img = _html.unescape(m.group(1).strip())
                                                img_candidates.append(art_img)
                                                log.info(f"Article og:image fallback: {art_img[:80]}")
                                    except Exception as ae:
                                        log.warning(f"Article og:image failed: {ae}")
                        if cookies_file and os.path.exists(cookies_file):
                            os.unlink(cookies_file)
                    except Exception as e:
                        log.warning(f"yt-dlp info extraction failed: {e}")

                    # ── STEP 3: OCR the best image ────────────────────────────────
                    # Try candidates in order: og:image (post-specific) → yt-dlp thumbnail → formats
                    seen_urls = set()
                    ocr_succeeded = False
                    for img_url in img_candidates:
                        if not img_url or img_url in seen_urls: continue
                        seen_urls.add(img_url)
                        try:
                            img_r = requests.get(img_url, timeout=12,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                            if not img_r.ok or len(img_r.content) < 500: continue
                            ocr = ocr_image(img_r.content)
                            if ocr and len(ocr) > 20:
                                parts.append(f"Image text/content:\n{ocr}")
                                send(from_num, "🖼 Analysed image in post")
                                log.info(f"OCR success from {img_url[:60]}: {ocr[:80]}")
                                ocr_succeeded = True
                                break
                        except Exception as ie:
                            log.warning(f"Image OCR failed ({img_url[:60]}): {ie}")
                    if img_candidates and not ocr_succeeded:
                        log.warning(f"FB/IG: OCR failed for all {len(img_candidates)} image candidates")

                    if parts:
                        page_text = "\n\n".join(parts)
                        log.info(f"FB/IG extracted: {len(page_text)} chars, {len(img_candidates)} img candidates")

                if not page_text:
                    page_text = fetch(url) or ""

                # For non-FB/IG URLs: also OCR the og:image (captures headline graphics)
                if page_text and "facebook.com" not in url and "instagram.com" not in url:
                    try:
                        import html as _html2
                        html_r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                        if html_r.ok:
                            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html_r.text, re.I)
                            if not m:
                                m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html_r.text, re.I)
                            if m:
                                og_img = _html2.unescape(m.group(1).strip())
                                if og_img.startswith("http"):
                                    img_r = requests.get(og_img, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
                                    if img_r.ok and len(img_r.content) > 500:
                                        ocr = ocr_image(img_r.content)
                                        if ocr and len(ocr) > 20:
                                            page_text += f"\n\nImage text:\n{ocr}"
                                            log.info(f"og:image OCR for article: {ocr[:80]}")
                    except Exception as oe:
                        log.debug(f"Article og:image OCR failed: {oe}")

                query = page_text or body
                source_type = "url"
        else:
            query, source_type = body, "text"
    elif msg_type == "image":
        send(from_num, "🖼 Analysing image..."); image_bytes = download_media(message["image"]["id"])
        if image_bytes: query = clean_query(ocr_image(image_bytes))
        source_type = "image"
        if not query: send(from_num, "⚠️ Could not analyse image."); return
    elif msg_type == "audio":
        send(from_num, "🎤 Transcribing..."); b = download_media(message["audio"]["id"])
        if b: query = transcribe(b, message["audio"].get("mime_type","audio/ogg"))
        source_type = "audio"
        if not query: send(from_num, "⚠️ Could not transcribe."); return
    elif msg_type == "video":
        send(from_num, "🎬 Processing video...")
        vid_data = message.get("video", {})
        video_bytes = download_media(vid_data["id"]) if vid_data.get("id") else None
        if video_bytes:
            query_parts = []
            if vid_data.get("caption"):
                query_parts.append(f"Caption: {vid_data['caption']}")
            try:
                frames, duration = extract_video_frames(video_bytes, num_frames=3)
                if frames:
                    visual = analyze_video_frames(frames)
                    if visual:
                        query_parts.append(f"Visual analysis:\n{visual}")
            except Exception as ve:
                log.warning(f"Video frame analysis: {ve}")
            try:
                transcript = transcribe(video_bytes, vid_data.get("mime_type","video/mp4"))
                if transcript:
                    query_parts.append(f"Audio transcript:\n{transcript}")
                    send(from_num, "✓ Transcribed audio")
                else:
                    send(from_num, "⚠️ No speech detected or audio transcription unavailable")
                    log.warning("Direct video: transcribe() returned empty")
            except Exception as te:
                log.warning(f"Video transcription: {te}")
            query = "\n\n".join(query_parts) if query_parts else ""
            source_type = "video"
        if not query:
            send(from_num, "⚠️ Could not process video. Try sending the URL instead."); return
    elif msg_type == "document":
        send(from_num, "📄 Reading..."); b = download_media(message["document"]["id"])
        if b: query = b.decode("utf-8", errors="ignore")[:2000]
        source_type = "document"
        if not query: send(from_num, "⚠️ Could not read."); return
    else:
        send(from_num, f"⚠️ Unsupported: {msg_type}"); return
    if not query: send(from_num, "⚠️ Could not extract content."); return
    query = clean_ocr(query) if source_type == "image" else query
    query = query.strip()[:2000]
    log.info("Received [%s]: %s", source_type, query[:100])
    cost = estimate_cost(source_type)
    with pending_lock:
        pending[from_num] = {"query":query,"source_type":source_type,"image_bytes":image_bytes,"cost":cost,"timestamp":t.time()}
    send(from_num, confirm_msg(source_type, query, cost))

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status":"running","version":"v3.2","keys":{"whatsapp":bool(WHATSAPP_TOKEN),"google_fc":bool(GOOGLE_API_KEY),"anthropic":bool(ANTHROPIC_KEY),"openai":bool(OPENAI_API_KEY),"rapidapi":bool(RAPIDAPI_KEY)}}), 200

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def receive():
    data = request.get_json()
    try:
        value = data["entry"][0]["changes"][0]["value"]
        if "messages" in value:
            msg = value["messages"][0]
            msg_type = msg.get("type","unknown"); from_num = msg.get("from","unknown")
            log.info(f">>> Received {msg_type} from {from_num}")
            if msg_type == "text": log.info(f"    Text: {msg.get('text',{}).get('body','')[:100]}")
            elif msg_type == "video": log.info(f"    Video ID: {msg.get('video',{}).get('id','')}")
            elif msg_type == "image": log.info(f"    Image ID: {msg.get('image',{}).get('id','')}")
            try:
                process(from_num, msg)
            except Exception as e:
                log.error(f"!!! Process exception: {e}")
                try: send(from_num, f"❌ Bot error: {str(e)[:200]}\n\nPlease try again.")
                except Exception: pass
    except (KeyError, IndexError) as e: log.warning(f"Parse error: {e}")
    except Exception as e: log.error(f"Webhook error: {e}")
    return jsonify({"status":"ok"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info("FactCheck Pro v3.2 starting (dev mode)...")
    app.run(host="0.0.0.0", port=port, debug=False)
