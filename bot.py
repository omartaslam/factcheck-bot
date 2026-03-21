"""Fred Check v3.2 - Enhanced Video Analysis"""
import os, base64, json, logging, tempfile, threading, requests, re, sqlite3, hashlib, secrets, hmac, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_from_directory
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
DB_PATH = os.getenv("DB_PATH", "/tmp/factcheck.db")  # Set to a Railway Volume path for persistence

# ── Billing / monetisation config ─────────────────────────────────────────────
FREE_CHECKS_LIMIT   = int(os.getenv("FREE_CHECKS_LIMIT", "3"))   # free checks per WA number
PROFIT_MARGIN       = float(os.getenv("PROFIT_MARGIN", "2.0"))   # cost multiplier (2.0 = 100% margin)
STRIPE_SECRET_KEY   = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
TOPUP_1_LINK        = os.getenv("TOPUP_1_LINK", "")              # Stripe Payment Link for $1
TOPUP_5_LINK        = os.getenv("TOPUP_5_LINK", "")              # Stripe Payment Link for $5
TOPUP_10_LINK       = os.getenv("TOPUP_10_LINK", "")             # Stripe Payment Link for $10
TOPUP_25_LINK       = os.getenv("TOPUP_25_LINK", "")             # Stripe Payment Link for $25
SUB_LINK            = os.getenv("SUB_LINK", "")                  # Stripe Payment Link for subscription (not active)
BETA_MODE           = os.getenv("BETA_MODE", "true").lower() == "true"  # Show BETA label in reports
DEV_AUTOSELECT_NUM  = os.getenv("DEV_AUTOSELECT_NUM", "")               # Phone number that skips claim selection (dev only)
DEV_AUTOSELECT_ON   = os.getenv("DEV_AUTOSELECT_ON", "false").lower() == "true"  # Toggle dev auto-select
WA_CONVERSATION_COST = float(os.getenv("WA_CONVERSATION_COST", "0.041"))  # WhatsApp per-conversation charge (Europe/Spain rate)

# ── Multi-platform config ──────────────────────────────────────────────────────
MESSENGER_PAGE_TOKEN  = os.getenv("MESSENGER_PAGE_TOKEN", "")    # Facebook Page Access Token (Messenger + Instagram DMs)
MESSENGER_VERIFY_TOKEN = os.getenv("MESSENGER_VERIFY_TOKEN", "messenger_factcheck_verify")
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")      # Telegram bot token
APP_BASE_URL          = os.getenv("APP_BASE_URL", "https://web-production-1f0a4.up.railway.app")
WEBSITE_URL           = os.getenv("WEBSITE_URL", "https://fredcheck.com")  # Public marketing site
TWITTER_CONSUMER_KEY    = os.getenv("TWITTER_CONSUMER_KEY", "")    # Twitter/X app consumer key
TWITTER_CONSUMER_SECRET = os.getenv("TWITTER_CONSUMER_SECRET", "") # Twitter/X app consumer secret
TWITTER_ACCESS_TOKEN    = os.getenv("TWITTER_ACCESS_TOKEN", "")    # Twitter/X bot access token
TWITTER_ACCESS_SECRET   = os.getenv("TWITTER_ACCESS_SECRET", "")   # Twitter/X bot access token secret
TWITTER_WEBHOOK_SECRET  = os.getenv("TWITTER_WEBHOOK_SECRET", "")  # Optional extra signing secret

SPONSOR_ADS         = [a.strip() for a in os.getenv("SPONSOR_ADS", "").split("|") if a.strip()]

# Anthropic model pricing: USD per million tokens
_ANTHROPIC_PRICES = {
    "claude-sonnet-4-6":         {"in": 3.00,  "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 0.25,  "out":  1.25},
}
_OPENAI_PRICES = {
    "gpt-4o":      {"in": 2.50, "out": 10.00},
    "gpt-4o-mini": {"in": 0.15, "out":  0.60},
}

def _anthropic_cost_cents(model, in_tok, out_tok):
    p = _ANTHROPIC_PRICES.get(model, {"in": 3.0, "out": 15.0})
    raw = (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000
    return max(1, round(raw * PROFIT_MARGIN * 100))

def _openai_cost_cents(model, in_tok, out_tok):
    p = _OPENAI_PRICES.get(model, {"in": 2.5, "out": 10.0})
    raw = (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000
    return max(1, round(raw * PROFIT_MARGIN * 100))

def _whisper_cost_cents(duration_secs):
    return max(1, round((duration_secs / 60) * 0.006 * PROFIT_MARGIN * 100))

# Thread-local cost accumulator — tracks per-request API spend
_cost_local = threading.local()
def _cost_reset():  _cost_local.cents = 0
def _cost_add(c):   _cost_local.cents = getattr(_cost_local, "cents", 0) + max(0, c)
def _cost_get():    return getattr(_cost_local, "cents", 0)
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
SRC_BBC              = os.getenv("SRC_BBC",              "true").lower() == "true"
SRC_REUTERS          = os.getenv("SRC_REUTERS",          "true").lower() == "true"
SRC_AP               = os.getenv("SRC_AP",               "true").lower() == "true"
SRC_GUARDIAN         = os.getenv("SRC_GUARDIAN",         "true").lower() == "true"
SRC_CNN              = os.getenv("SRC_CNN",              "true").lower() == "true"
# Middle East expanded sources
SRC_MEMO             = os.getenv("SRC_MEMO",             "true").lower() == "true"
SRC_NEWARAB          = os.getenv("SRC_NEWARAB",          "true").lower() == "true"
SRC_BTSELEM          = os.getenv("SRC_BTSELEM",          "true").lower() == "true"
SRC_BELLINGCAT       = os.getenv("SRC_BELLINGCAT",       "true").lower() == "true"
SRC_HRW              = os.getenv("SRC_HRW",              "true").lower() == "true"
SRC_AMNESTY          = os.getenv("SRC_AMNESTY",          "true").lower() == "true"
SRC_UNNEWS           = os.getenv("SRC_UNNEWS",           "true").lower() == "true"
SRC_TOI              = os.getenv("SRC_TOI",              "true").lower() == "true"
SRC_ARABNEWS         = os.getenv("SRC_ARABNEWS",         "true").lower() == "true"
SRC_RESPSTATECRAFT   = os.getenv("SRC_RESPSTATECRAFT",   "true").lower() == "true"
SRC_ANADOLU          = os.getenv("SRC_ANADOLU",          "true").lower() == "true"  # Anadolu Agency (Turkey)
SRC_ALMONITOR        = os.getenv("SRC_ALMONITOR",        "true").lower() == "true"  # Al-Monitor (ME analysis)
SRC_DAWN             = os.getenv("SRC_DAWN",             "true").lower() == "true"  # DAWN (US foreign policy critique)
# Global South / non-Western fact-checkers
SRC_MISBAR           = os.getenv("SRC_MISBAR",           "true").lower() == "true"  # Misbar — MENA Arabic/English fact-checker
SRC_FATABYYANO       = os.getenv("SRC_FATABYYANO",       "true").lower() == "true"  # Fatabyyano — Jordan, Arabic fact-checker
SRC_VERIFYSY         = os.getenv("SRC_VERIFYSY",         "true").lower() == "true"  # Verify-Sy — Syria misinformation
SRC_AFRICACHECK      = os.getenv("SRC_AFRICACHECK",      "true").lower() == "true"  # Africa Check — Sub-Saharan Africa
SRC_PESACHECK        = os.getenv("SRC_PESACHECK",        "true").lower() == "true"  # PesaCheck — East Africa
SRC_DUBAWA           = os.getenv("SRC_DUBAWA",           "true").lower() == "true"  # Dubawa — West Africa
SRC_ALTNEWS          = os.getenv("SRC_ALTNEWS",          "true").lower() == "true"  # Alt News — India (counters Hindu nationalist misinfo)
SRC_BOOMLIVE         = os.getenv("SRC_BOOMLIVE",         "true").lower() == "true"  # Boom Live — India/South Asia
SRC_RAPPLER          = os.getenv("SRC_RAPPLER",          "true").lower() == "true"  # Rappler — Philippines/SE Asia
SRC_CHEQUEADO        = os.getenv("SRC_CHEQUEADO",        "true").lower() == "true"  # Chequeado — Latin America
SRC_LOGICALLY        = os.getenv("SRC_LOGICALLY",        "true").lower() == "true"  # Logically Facts — global independent
# OSINT / verification APIs
TINEYE_API_KEY       = os.getenv("TINEYE_API_KEY",  "")   # TinEye reverse image search — public key (legacy)
TINEYE_API_SECRET    = os.getenv("TINEYE_API_SECRET","")   # TinEye private/secret key
GOOGLE_VISION_KEY    = os.getenv("GOOGLE_VISION_KEY","")   # Google Cloud Vision API — web detection (reverse image search)
# REVERSE_IMAGE_ENGINE: "google" (default if key set) | "tineye" | "off"
REVERSE_IMAGE_ENGINE = os.getenv("REVERSE_IMAGE_ENGINE", "google" if os.getenv("GOOGLE_VISION_KEY","") else ("tineye" if os.getenv("TINEYE_API_SECRET","") else "off"))
HIVE_API_KEY         = os.getenv("HIVE_API_KEY",    "")   # Hive Moderation — AI/deepfake detection
# Real-time search APIs
TAVILY_API_KEY       = os.getenv("TAVILY_API_KEY", "")   # tavily.com — free 1000/month, AI-optimised
BRAVE_API_KEY        = os.getenv("BRAVE_API_KEY", "")    # TODO: Brave Search API — 2000/month when free tier available
PERPLEXITY_API_KEY   = os.getenv("PERPLEXITY_API_KEY", "")  # perplexity.ai Sonar — real-time web search AI, bridges Claude's Aug-2025 knowledge cutoff
YOUTUBE_API_KEY      = os.getenv("YOUTUBE_API_KEY", "")    # YouTube Data API v3 — search official channel videos (10k units/day free)
# Custom sources — add any source without code changes
# Format in Railway: "Name|https://site.com/search?q={q},Name2|https://site2.com/?s={q}"
# Use {q} for URL-encoded query, {qt} for URL-encoded short query
CUSTOM_SOURCES_RAW = os.getenv("CUSTOM_SOURCES", "")

MAX_VIDEO_MINUTES    = int(os.getenv("MAX_VIDEO_MINUTES", "10"))  # reject videos longer than this

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

# ── CORS — allow fredcheck.com to call /api/* from the browser ────────────────
_CORS_ORIGINS = {"https://fredcheck.com", "https://www.fredcheck.com", "https://fredcheck.co.uk"}

@app.after_request
def _cors(response):
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ORIGINS or origin.endswith(".railway.app"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/api/factcheck", methods=["OPTIONS"])
@app.route("/api/register", methods=["OPTIONS"])
@app.route("/api/login", methods=["OPTIONS"])
@app.route("/api/me", methods=["OPTIONS"])
@app.route("/api/contact", methods=["OPTIONS"])
def _preflight():
    return "", 204

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
                  "type": "text", "text": {"body": f"⚠️ *Fred Check Alert*\n\n{message}"}},
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
# Daily summary at 07:00 UTC added after _send_daily_summary is defined (see below)
_scheduler = BackgroundScheduler()
_scheduler.add_job(refresh_whatsapp_token, "interval", days=50, id="token_refresh")
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown())
log.info("Scheduler started: token refresh (50d)")

processed_ids = set()
processed_lock = threading.Lock()
MAX_PROCESSED_IDS = 10_000

pending = {}
pending_lock = threading.Lock()
PENDING_TTL = 600

# QC testing — capture send() output for specific test numbers instead of hitting Meta API
_qc_jobs = {}   # from_num -> {"messages": [], "done": False, "error": None, "_input": str}
_qc_lock = threading.Lock()

SYSTEM = """You are Fred Check — a bias-aware, multi-perspective fact-checker serving investigative journalists, activists, and communities underserved by Western media.

CORE PRINCIPLES:
1. Western government and media narratives are NOT the default neutral. Apply the same scepticism to BBC, CNN, Reuters and AP as you would to any state-adjacent outlet. Official statements from Western governments, militaries, and intelligence agencies require corroboration like any other source.
2. Palestinian, Arab, Muslim, African, and Global South perspectives carry equal epistemic weight to Western ones. The absence of Western coverage does not mean an event did not happen.
3. When sources disagree along geopolitical lines, report BOTH framings honestly — do not silently adopt the Western framing as objective fact.
4. Use internationally neutral language. Avoid contested terminology unless directly quoting. Where terminology is itself disputed (e.g. "terrorist"/"militant"/"resistance fighter", "war"/"genocide"/"conflict", "settlements"/"colonies", "Israel Defense Forces"/"Israeli military"/"Israeli occupation forces") — name the dispute and explain how different parties use the terms.
5. Apply identical standards to state violence and non-state violence. Military strikes, sieges, sanctions, and occupation that cause civilian death should be analysed with the same rigour as attacks by non-state actors.
6. Propaganda techniques appear in all media ecosystems. Flag dehumanising language, manufactured consent, false balance between unequal parties, and omission of structural context — regardless of which side employs them.
7. International law (Geneva Conventions, UN resolutions, ICJ/ICC rulings) provides an objective reference frame. Apply it consistently to all parties.
8. Statistical claims — casualty figures, percentages, area measurements — deserve specific scrutiny. Note when official figures conflict with independent counts.

EXPERTISE: Gaza conflict, West Bank occupation, Lebanese civil conflicts, Iran-US-Israel tensions, Iraqi and Syrian wars, Yemeni conflict, Sudanese crisis, global Muslim communities, colonialism's ongoing effects, Western foreign policy in MENA and beyond.
9. Never treat the social or political origin of a claim as evidence about its truth. A factually accurate claim is TRUE regardless of who shares it, what community circulates it, or what narrative it is used to support. Do not use labels like 'antisemitic framing', 'conspiracy theory framing', 'far-right narrative', or similar social/political categorisations as verdict modifiers — these are editorial categories, not factual assessments. Criticism of a government (including the Israeli government), a political ideology (including Zionism), or a state's actions is not the same as hatred of an ethnic or religious group and must not be treated as such."""

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
    bar = "🟩" * green + "🟥" * red
    return bar

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
        if r.status_code in _UNAVAIL_HTTP_CODES:
            log.warning("fetch %s: HTTP %s (unavailable/restricted)", url, r.status_code)
            return ""
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

def _repair_mp4(video_bytes):
    """Try to remux a fragmented/broken MP4 into a streamable one via ffmpeg -c copy.
    Fixes 'moov atom not found' errors from DASH/streaming downloads. Returns fixed
    bytes or None if repair fails."""
    import subprocess
    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(video_bytes); in_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            out_path = f.name
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-c", "copy", "-movflags", "faststart", out_path],
            capture_output=True, timeout=30)
        if r.returncode == 0 and os.path.getsize(out_path) > 1000:
            with open(out_path, "rb") as f:
                fixed = f.read()
            log.info(f"MP4 repair: {len(video_bytes)//1024}KB → {len(fixed)//1024}KB")
            return fixed
    except Exception as e:
        log.debug(f"MP4 repair failed: {e}")
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                try: os.unlink(p)
                except: pass
    return None

def _extract_audio_mp3(video_bytes):
    """Use ffmpeg to extract audio track from video bytes → MP3 bytes. Returns None on failure."""
    import subprocess
    # Try repairing fragmented MP4 first
    repaired = _repair_mp4(video_bytes)
    if repaired:
        video_bytes = repaired
    video_path = mp3_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
            vf.write(video_bytes); video_path = vf.name
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as af:
            mp3_path = af.name
        r = subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1",
             "-b:a", "64k", "-y", mp3_path],
            capture_output=True, timeout=30)
        if r.returncode == 0 and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            with open(mp3_path, "rb") as af:
                audio = af.read()
            log.info(f"ffmpeg audio extract: {len(audio)//1024}KB MP3")
            return audio
        else:
            log.warning(f"ffmpeg audio extract failed (rc={r.returncode}): {r.stderr[-200:].decode('utf-8','ignore')}")
    except Exception as e:
        log.error(f"ffmpeg audio extract: {e}")
    finally:
        for p in (video_path, mp3_path):
            if p and os.path.exists(p):
                try: os.unlink(p)
                except: pass
    return None

def transcribe(b, mime):
    log.info(f"Transcribing {len(b)} bytes, mime: {mime}")

    # For video files, extract audio to MP3 first — avoids Whisper 400s on
    # non-standard MP4 containers (common with Facebook/TikTok CDN videos)
    whisper_bytes, whisper_ext, whisper_mime = b, None, mime
    if mime == "video/mp4":
        audio = _extract_audio_mp3(b)
        if audio:
            whisper_bytes, whisper_ext, whisper_mime = audio, "mp3", "audio/mpeg"
            log.info("Using ffmpeg-extracted MP3 for Whisper")
        else:
            log.warning("ffmpeg audio extract failed — sending raw MP4 to Whisper")

    if OPENAI_API_KEY:
        # Try multiple mime/ext combinations — Facebook CDN MP4s often fail as
        # video/mp4 but succeed as audio/mp4 (m4a). Try both.
        attempts = []
        if whisper_ext:
            attempts.append((whisper_bytes, whisper_ext, whisper_mime))
        else:
            ext0 = {"audio/ogg":"ogg","audio/mpeg":"mp3","video/mp4":"mp4"}.get(whisper_mime, "ogg")
            attempts.append((b, ext0, whisper_mime))
            if whisper_mime == "video/mp4":
                attempts.append((b, "m4a", "audio/mp4"))  # retry as m4a
        for wb, ext, wm in attempts:
            try:
                with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
                    f.write(wb); path = f.name
                with open(path, "rb") as f:
                    r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                        files={"file": (f"a.{ext}", f, wm)},
                        data={"model": "whisper-1"}, timeout=60)
                os.unlink(path)
                if _is_credit_error(r.status_code, r.text):
                    send_admin_alert("openai", f"OpenAI API quota exceeded (HTTP {r.status_code}). Audio transcription unavailable.")
                    log.error(f"OpenAI credit error in Whisper: {r.status_code}")
                    raise Exception(f"OpenAI quota error {r.status_code}")
                r.raise_for_status()
                transcript = r.json().get("text", "").strip()
                log.info(f"Whisper success ({ext}): {len(transcript)} chars")
                return transcript
            except Exception as e:
                log.warning(f"Whisper attempt ({ext}) failed: {e}")

    # Claude audio fallback — send extracted MP3 if available, else raw bytes
    log.info("Trying Claude audio fallback...")
    try:
        fallback_bytes = whisper_bytes if whisper_ext == "mp3" else b
        fallback_mime = "audio/mpeg" if whisper_ext == "mp3" else (
            {"audio/ogg":"audio/ogg","audio/mpeg":"audio/mpeg","video/mp4":"video/mp4"}.get(mime, "audio/ogg"))
        b64 = base64.b64encode(fallback_bytes).decode()
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":[
                {"type":"text","text":"Transcribe all spoken words. Return only the transcript."},
                {"type":"image","source":{"type":"base64","media_type":fallback_mime,"data":b64}}
            ]}]}, timeout=60)
        r.raise_for_status()
        transcript = r.json()["content"][0]["text"].strip()
        log.info(f"Claude transcription success: {len(transcript)} chars")
        return transcript
    except Exception as e: log.error(f"Claude transcribe failed: {e}"); return ""

_PLATFORM_TITLES = {"facebook", "instagram", "tiktok", "youtube", "twitter", "x", "reels", "reel", "video"}

def _is_useless_title(title):
    """Return True if the title is just a platform name and carries no information."""
    return not title or title.strip().lower() in _PLATFORM_TITLES

def _is_video_bytes(data):
    """Return True if bytes look like a video container (MP4, WebM, AVI, MKV).
    Used to distinguish actual video downloads from images returned by social APIs."""
    if not data or len(data) < 12:
        return False
    # MP4/MOV: box type at offset 4 is a known video atom
    if data[4:8] in (b'ftyp', b'moov', b'mdat', b'free', b'wide', b'pnot', b'skip'):
        return True
    # WebM / MKV: EBML header
    if data[:4] == b'\x1a\x45\xdf\xa3':
        return True
    # AVI: RIFF....AVI
    if data[:4] == b'RIFF' and data[8:11] == b'AVI':
        return True
    return False

def _parse_post_date(raw):
    """Normalise various date formats to 'YYYY-MM-DD'. Returns '' on failure."""
    if not raw:
        return ""
    try:
        import datetime as _dt
        if isinstance(raw, (int, float)):
            return _dt.datetime.fromtimestamp(float(raw), tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        s = str(raw).strip()
        if len(s) == 8 and s.isdigit():  # yt-dlp "YYYYMMDD"
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        m = re.match(r'(\d{4}-\d{2}-\d{2})', s)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""

def _post_age_label(date_str):
    """Return (friendly, days, age_str) for 'YYYY-MM-DD', or None on failure."""
    try:
        import datetime as _dt
        posted = _dt.date.fromisoformat(date_str)
        days = (_dt.date.today() - posted).days
        if days < 0:
            return None
        friendly = posted.strftime("%-d %b %Y")
        if days == 0:   age = ""
        elif days < 7:  age = f"{days}d ago"
        elif days < 60: age = f"{days // 7}wk ago"
        elif days < 730: age = f"{days // 30}mo ago"
        else:           age = f"{days // 365}yr ago"
        return friendly, days, age
    except Exception:
        return None

def extract_video_frames(video_bytes, num_frames=2):
    """Extract frames with cv2; fall back to ffmpeg if cv2 reports 0 frames."""
    import subprocess
    video_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(video_bytes); video_path = f.name

        # ── Try cv2 first ────────────────────────────────────────────────
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = total_frames / fps if fps > 0 else 0
        frames = []
        if total_frames > 0:
            frame_indices = [int(total_frames * i / num_frames) for i in range(num_frames)]
            for idx in frame_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx); ret, frame = cap.read()
                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)
                    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=70)
                    frames.append(buf.getvalue())
        cap.release()

        # ── ffmpeg fallback if cv2 got nothing ───────────────────────────
        if not frames:
            # First try repairing the MP4 (fixes 'moov atom not found' from DASH downloads)
            repaired = _repair_mp4(video_bytes)
            if repaired:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as rf:
                    rf.write(repaired); video_path = rf.name
                cap2 = cv2.VideoCapture(video_path)
                if cap2.isOpened():
                    total = int(cap2.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 0:
                        for idx in [int(total * p) for p in [0.1, 0.35, 0.6, 0.8, 0.95]][:num_frames]:
                            cap2.set(cv2.CAP_PROP_POS_FRAMES, idx)
                            ok, frame = cap2.read()
                            if ok:
                                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                                img = Image.fromarray(frame_rgb)
                                buf = io.BytesIO(); img.save(buf, format="JPEG", quality=70)
                                frames.append(buf.getvalue())
                cap2.release()
            log.info("cv2 got 0 frames — trying ffmpeg fallback")
            try:
                # Extract frames at fixed offsets without needing ffprobe
                # Try 0s, 3s, 7s, 12s, 20s — covers most short social media clips
                offsets = [0, 3, 7, 12, 20][:num_frames + 1]
                for offset in offsets:
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                        out_path = tf.name
                    r = subprocess.run(
                        ["ffmpeg", "-ss", str(offset), "-i", video_path,
                         "-frames:v", "1", "-q:v", "3", "-y", out_path],
                        capture_output=True, timeout=20)
                    if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        with open(out_path, "rb") as img_f:
                            frames.append(img_f.read())
                    try: os.unlink(out_path)
                    except: pass
                if frames:
                    log.info(f"ffmpeg extracted {len(frames)} frames")
                    duration = offsets[len(frames) - 1]
            except Exception as fe:
                log.error("ffmpeg frame fallback: %s", fe)

        if video_path and os.path.exists(video_path):
            os.unlink(video_path)
        log.info(f"Extracted {len(frames)} frames (duration: {duration:.1f}s)")
        return frames, duration
    except Exception as e:
        if video_path and os.path.exists(video_path):
            try: os.unlink(video_path)
            except: pass
        log.error("Frame extraction: %s", e)
        return [], 0

def analyze_video_frames(frames):
    try:
        if not frames: return ""
        content = [{"type":"text","text":"You are helping fact-check a video. For each frame:\n1. Transcribe ALL visible text/captions/overlays WORD FOR WORD\n2. State the specific factual CLAIM being made as a plain sentence (e.g. 'Iran fired missiles at painted US planes')\n3. Note people, locations, events shown\nKeep your response concise. Lead with the claim, not descriptions."}]
        for frame_bytes in frames[:4]:
            b64 = base64.b64encode(frame_bytes).decode()
            content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}})
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":content}]},
            timeout=45)
        r.raise_for_status(); return r.json()["content"][0]["text"].strip()
    except Exception as e: log.error("Video frame analysis: %s", e); return ""

def _try_download_url(video_url, label):
    """Download video bytes from a direct URL. Returns bytes or None."""
    try:
        r = requests.get(video_url, timeout=30, stream=True)
        r.raise_for_status()
        content = b"".join(r.iter_content(chunk_size=1024*1024))
        if content:
            log.info(f"{label}: downloaded {len(content)//1024}KB")
            return content
    except Exception as e:
        log.error(f"{label} download failed: {e}")
    return None

def _extract_video_url(data):
    """Extract best video URL and title from vikas5914 API response."""
    title = data.get("title", "") or data.get("description", "") or ""
    _img_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

    def _is_video_url(u):
        if not isinstance(u, str) or not u.startswith("http"):
            return False
        ul = u.lower().split("?")[0]  # strip query params for ext check
        if any(ul.endswith(e) for e in _img_exts):
            return False  # skip thumbnail/image URLs
        return True

    # Try HD first, then SD, then any video key
    for key in ("hd", "sd", "video"):
        val = data.get(key)
        if isinstance(val, str) and _is_video_url(val):
            return val, title
        if isinstance(val, list):
            for item in val:
                if _is_video_url(item):
                    return item, title

    # Check 'links' dict — vikas5914 returns video URLs under labels like
    # "Download Low Quality", "Download HD" etc.
    links = data.get("links")
    if isinstance(links, dict):
        # Prefer HD, fall back to any video link
        for label in ("Download HD", "Download High Quality", "Download Low Quality", "Download"):
            u = links.get(label)
            if u and _is_video_url(u):
                return u, title
        for u in links.values():
            if _is_video_url(u):
                return u, title

    # Check 'media' list
    media = data.get("media")
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict):
                for u in item.values():
                    if _is_video_url(u):
                        return u, title

    # Search all string values for video URLs — exclude image extensions
    for v in data.values():
        if isinstance(v, str) and _is_video_url(v) and any(x in v for x in (".mp4", "video-", "fbcdn.net/v/")):
            return v, title
    return None, title

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
        return None, "", ""

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
                    return content, title, ""
        except Exception as e:
            log.error(f"7scorp TikTok failed: {e}")

    # Twitter/X — use vikas5914 /twitter endpoint for video, fxtwitter for text/image posts
    if "twitter.com" in url or "x.com" in url:
        # Try video download first
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
                        return content, title, ""
        except Exception as e:
            log.error(f"vikas5914 Twitter failed: {e}")

    # Facebook — use vikas5914 /facebook endpoint (no cookies needed)
    # Handles share/r/, share/v/, reel/, video/ and fb.watch URLs
    if "facebook.com" in url or "fb.watch" in url:
        # Resolve share/r/ and fb.watch short links to canonical URL first —
        # vikas5914 handles reel/video URLs better than opaque share links
        resolved_url = url
        if "/share/" in url or "fb.watch" in url:
            try:
                rr = requests.head(url, allow_redirects=True, timeout=10,
                                   headers={"User-Agent": "Mozilla/5.0"})
                if rr.url and rr.url != url:
                    resolved_url = rr.url
                    log.info(f"Resolved FB share URL: {url[:60]} → {resolved_url[:60]}")
            except Exception as e:
                log.warning(f"FB URL resolve failed: {e}")
        for _attempt_url in list(dict.fromkeys([resolved_url, url])):
            try:
                host = "fastest-social-video-and-image-downloader.p.rapidapi.com"
                headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": host}
                log.info(f"Trying vikas5914 Facebook downloader for: {_attempt_url}")
                r = requests.get(
                    f"https://{host}/facebook",
                    headers=headers,
                    params={"url": _attempt_url},
                    timeout=25
                )
                r.raise_for_status()
                data = r.json()
                log.info(f"vikas5914 Facebook response keys: {list(data.keys())} | full: {str(data)[:800]}")
                # Accept response with or without explicit "success" field —
                # some API versions omit it but still return valid video URLs
                video_url, title = _extract_video_url(data)
                if video_url:
                    content = _try_download_url(video_url, "vikas5914-Facebook")
                    if content:
                        return content, title, ""
                else:
                    log.warning(f"vikas5914 Facebook: no video URL in response — {str(data)[:200]}")
            except Exception as e:
                log.error(f"vikas5914 Facebook failed: {e}")

    # Instagram & everything else — fall through to yt-dlp
    return None, "", ""


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
                return None, "", ""
            video_path = ydl.prepare_filename(info)
            if not os.path.exists(video_path):
                video_path = temp_path if os.path.exists(temp_path) else None
            if not video_path:
                return None, "", ""
            with open(video_path, "rb") as f:
                video_bytes = f.read()
            try:
                os.unlink(video_path)
            except Exception:
                pass
            title = info.get("title", "")
            description = info.get("description", "")[:200] if info.get("description") else ""
            uploader = info.get("uploader", "") or info.get("channel", "")
            post_date = _parse_post_date(info.get("upload_date", ""))
            log.info(f"yt-dlp downloaded: {title[:50]} uploader={uploader[:30]} date={post_date or 'unknown'}")
            meta_parts = [p for p in [title, f"Creator: {uploader}" if uploader else "", description] if p]
            return video_bytes, "\n".join(meta_parts).strip(), post_date
    except Exception as e:
        log.error(f"yt-dlp failed: {e}")
        return None, "", ""
    finally:
        if cookies_file and os.path.exists(cookies_file):
            try: os.unlink(cookies_file)
            except: pass


def _ytdlp_captions(url):
    """Fetch auto-generated or manual English captions for a YouTube URL via yt-dlp.
    Returns plain text string or '' on failure."""
    import re as _re
    tmpdir = tempfile.mkdtemp()
    try:
        ydl_opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-US", "en-GB"],
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(tmpdir, "cap.%(ext)s"),
            "socket_timeout": 15,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        for fname in os.listdir(tmpdir):
            if fname.endswith(".vtt"):
                with open(os.path.join(tmpdir, fname), "r", errors="ignore") as f:
                    vtt = f.read()
                # Strip VTT headers, timestamps, and tags — keep only spoken text
                lines = []
                for line in vtt.splitlines():
                    line = line.strip()
                    if not line or line.startswith("WEBVTT") or "-->" in line:
                        continue
                    line = _re.sub(r"<[^>]+>", "", line)  # strip <c>, <00:00> tags
                    if line and line not in lines[-3:]:     # basic dedup of repeated cues
                        lines.append(line)
                text = " ".join(lines)
                log.info(f"yt-dlp captions: {len(text)} chars from {fname}")
                return text[:3000]
    except Exception as e:
        log.debug(f"yt-dlp captions failed: {e}")
    finally:
        import shutil as _sh
        try: _sh.rmtree(tmpdir)
        except: pass
    return ""


def _get_video_duration(url):
    """Return video duration in seconds without downloading. Returns -1 on failure, 0 for live/unknown."""
    try:
        ydl_opts = {"quiet": True, "no_warnings": True, "socket_timeout": 10}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return int(info.get("duration") or 0) if info else -1
    except Exception:
        return -1


def _ytdlp_audio_bytes(url):
    """
    Download best audio stream only via yt-dlp — returns (bytes, ext) or (None, '').
    Works without ffmpeg: yt-dlp saves raw DASH m4a/opus/webm which Whisper accepts.
    Used as fallback when video file transcription fails (e.g. Facebook CDN fragmented MP4).
    """
    cookies_file = None
    tmpdir = tempfile.mkdtemp()
    try:
        cookies_b64 = FB_COOKIES_B64 if "facebook.com" in url or "fb.watch" in url else (
            IG_COOKIES_B64 if "instagram.com" in url else "")
        if cookies_b64:
            cookies_data = base64.b64decode(cookies_b64).decode("utf-8")
            cookies_file = tempfile.mktemp(suffix=".txt")
            with open(cookies_file, "w") as cf:
                cf.write(cookies_data)
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "max_filesize": 30 * 1024 * 1024,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        }
        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        for fname in os.listdir(tmpdir):
            fp = os.path.join(tmpdir, fname)
            if os.path.getsize(fp) > 0:
                ext = os.path.splitext(fname)[1].lstrip(".") or "m4a"
                with open(fp, "rb") as af:
                    data = af.read()
                log.info(f"yt-dlp audio: {len(data)//1024}KB .{ext}")
                return data, ext
    except Exception as e:
        log.error(f"yt-dlp audio download: {e}")
    finally:
        if cookies_file and os.path.exists(cookies_file):
            try: os.unlink(cookies_file)
            except: pass
        import shutil
        try: shutil.rmtree(tmpdir)
        except: pass
    return None, ""

def _fb_ig_post_scrape(url):
    """Scrape a specific Facebook/Instagram POST URL to get full post text and post image.

    Uses specialised crawlers that FB/IG serve correct og: tags to:
      • facebookexternalhit — FB's own link-preview bot (gets post-specific og:image)
      • WhatsApp preview bot — the same UA WhatsApp itself uses for link cards

    For POST URLs (containing /posts/, /photo, /p/, /share/ etc.) the og:image
    returned is the actual post image, not the page profile picture.
    For Instagram URLs, also attempts a cookie-authenticated request to retrieve
    captions that may be hidden from unauthenticated bots.
    """
    import html as _html_mod
    import http.cookiejar as _cj_mod
    POST_INDICATORS = ['/posts/', '/photo', '/p/', '/share/', 'story.php', 'fbid=', 'story_fbid=']
    is_post_url = any(s in url for s in POST_INDICATORS)

    UAS = [
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_udata.php)",
        "WhatsApp/2.24.6.77 A",
        "Twitterbot/1.0",
    ]
    # For Instagram, append a cookie-authenticated attempt as final fallback
    _ig_cookie_session = None
    if "instagram.com" in url and IG_COOKIES_B64:
        try:
            _cookies_data = base64.b64decode(IG_COOKIES_B64).decode("utf-8")
            _cj = _cj_mod.MozillaCookieJar()
            import tempfile as _tmpmod
            with _tmpmod.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as _tf:
                _tf.write(_cookies_data); _tf_name = _tf.name
            _cj.load(_tf_name, ignore_discard=True, ignore_expires=True)
            os.unlink(_tf_name)
            _ig_cookie_session = requests.Session()
            _ig_cookie_session.cookies = requests.utils.cookiejar_from_dict(
                {c.name: c.value for c in _cj})
            log.info("IG cookie session prepared for post scrape")
        except Exception as _cse:
            log.debug(f"IG cookie session prep failed: {_cse}")

    _all_attempts = [(ua, None) for ua in UAS]
    if _ig_cookie_session:
        _all_attempts.append(("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", _ig_cookie_session))

    for ua, _sess in _all_attempts:
        try:
            _req = _sess if _sess else requests
            r = _req.get(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
                         timeout=14, allow_redirects=True)
            if not r.ok:
                continue
            html = r.text
            result = {"is_post": is_post_url}
            for prop, key in [("og:title","title"), ("og:description","description"), ("og:image","image_url"),
                              ("article:published_time","post_date"), ("og:updated_time","post_date")]:
                # Use exact-match patterns with required quotes so og:image doesn't
                # accidentally match og:image:alt (which contains text, not a URL)
                for pat in [
                    rf'<meta[^>]+property=["\'](?:{re.escape(prop)})["\'][^>]+content=["\']([^"\']+)["\']',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:{re.escape(prop)})["\']'
                ]:
                    m = re.search(pat, html, re.I)
                    if m:
                        val = _html_mod.unescape(m.group(1).strip())
                        if key == "post_date":
                            val = _parse_post_date(val)
                        if val and key not in result:
                            result[key] = val
                        break
            # Also look for the linked article URL — Facebook article shares embed
            # the source URL in the HTML (og:url or data-uri attrs pointing outside FB)
            if "article_url" not in result:
                for art_pat in [
                    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
                ]:
                    m = re.search(art_pat, html, re.I)
                    if m:
                        candidate = _html_mod.unescape(m.group(1).strip())
                        if candidate.startswith("http") and "facebook.com" not in candidate and "instagram.com" not in candidate:
                            result["article_url"] = candidate
                            log.info(f"FB/IG linked article URL: {candidate[:80]}")
                        break
            result["final_url"] = r.url  # track redirect destination
            if result.get("description") or result.get("image_url"):
                log.info(f"FB/IG scrape ({ua.split('/')[0]}): desc={bool(result.get('description'))} img={bool(result.get('image_url'))}")
                return result
        except Exception as e:
            log.debug(f"FB/IG scrape failed ({ua[:20]}): {e}")
    return {"is_post": is_post_url}

_UNAVAIL_TITLE_PHRASES = [
    # Facebook / Instagram
    "this content isn't available", "content isn't available",
    "this page isn't available", "page not found", "content not found",
    "log in to facebook", "log in to instagram", "login • instagram",
    "sorry, this page", "this link may be broken",
    # Twitter / X
    "this account doesn't exist", "account suspended",
    "this tweet is from a suspended account", "tweet not found",
    "caution: this account is temporarily restricted",
    # YouTube
    "video unavailable", "private video", "this video has been removed",
    "this video is unavailable", "this video is private",
    "this video is no longer available",
    # TikTok
    "this video is unavailable", "video currently unavailable",
    "this video has been removed", "this video is not available",
    # Generic
    "403 forbidden", "404 not found", "access denied", "error 404",
    "page does not exist", "no longer available", "410 gone",
]
_UNAVAIL_DESC_PHRASES = [
    # Facebook / Instagram
    "log in or sign up", "log in to see", "log into facebook",
    "create an account or log in", "see posts, photos and more",
    "to see more from", "join facebook to connect",
    # Twitter / X
    "these tweets are protected", "this account's tweets are protected",
    "you need to be following this person", "this account is suspended",
    # YouTube
    "sign in to confirm your age", "sign in to watch this video",
    # Generic
    "you don't have permission", "sign in to access",
]
_UNAVAIL_URL_FRAGMENTS = [
    "/login", "/checkpoint", "accounts/login", "login.php",
    "signin", "/suspended", "/unavailable", "age-gate",
]
_UNAVAIL_HTTP_CODES = {403, 404, 410, 451}

def _is_content_unavailable(fb_og):
    """Return True if the scraped og data signals private/deleted/restricted content."""
    title = (fb_og.get("title") or "").lower()
    desc  = (fb_og.get("description") or "").lower()
    final = (fb_og.get("final_url") or "").lower()

    if any(p in title for p in _UNAVAIL_TITLE_PHRASES):
        log.info(f"Content unavailable signal — title: {title[:80]}")
        return True
    if any(p in desc for p in _UNAVAIL_DESC_PHRASES):
        log.info(f"Content unavailable signal — desc: {desc[:80]}")
        return True
    if any(f in final for f in _UNAVAIL_URL_FRAGMENTS):
        log.info(f"Content unavailable signal — redirect to: {final[:80]}")
        return True
    # No description AND no image = nothing was served (private/deleted)
    if not fb_og.get("description") and not fb_og.get("image_url"):
        log.info("Content unavailable signal — no description and no image returned")
        return True
    return False


def _check_url_unavailable(url):
    """Lightweight check: returns True if URL signals private/deleted/restricted content.

    Does a single GET, checks HTTP status against _UNAVAIL_HTTP_CODES, then checks
    og:title/description for unavailability phrases. Used for YouTube, TikTok,
    Twitter/X and generic URLs when all download attempts return nothing.
    """
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12,
                         allow_redirects=True)
        final_url = r.url
        if r.status_code in _UNAVAIL_HTTP_CODES:
            log.info(f"Content unavailable — HTTP {r.status_code}: {url[:80]}")
            return True
        html = r.text
        og = {"final_url": final_url}
        for prop, key in [("og:title", "title"), ("og:description", "description"),
                          ("twitter:title", "title"), ("twitter:description", "description")]:
            m = re.search(rf'<meta[^>]+(?:property|name)=["\']?{re.escape(prop)}["\']?[^>]+content=["\']([^"\']+)["\']',
                          html, re.I)
            if not m:
                m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']?{re.escape(prop)}["\']?',
                              html, re.I)
            if m and key not in og:
                og[key] = m.group(1).strip()
        return _is_content_unavailable(og)
    except Exception as e:
        log.warning(f"_check_url_unavailable: {e}")
        return False


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

def _fxtwitter_text(url):
    """Extract tweet text and date via fxtwitter API. Returns (text, date_str)."""
    try:
        fx_url = re.sub(r"https?://(www\.)?(twitter\.com|x\.com)", "https://api.fxtwitter.com", url)
        log.info(f"Trying fxtwitter for tweet text: {fx_url}")
        r = requests.get(fx_url, timeout=10)
        r.raise_for_status()
        data = r.json()
        tweet = data.get("tweet", {})
        text = tweet.get("text", "")
        author = tweet.get("author", {}).get("name", "")
        post_date = _parse_post_date(tweet.get("created_at", ""))
        if text:
            combined = f"Tweet by {author}: {text}"
            # Include quoted tweet text — quote tweets are common and the original
            # post is often the subject of the fact-check
            quote = tweet.get("quote")
            if quote:
                q_author = quote.get("author", {}).get("name", "")
                q_text = quote.get("text", "")
                if q_text:
                    combined += f"\n\nQuoted tweet by {q_author}: {q_text}"
            # Download and OCR any photos — image posts carry claims in text overlays
            for photo in tweet.get("media", {}).get("photos", []):
                photo_url = photo.get("url", "")
                if photo_url:
                    try:
                        img_r = requests.get(photo_url, timeout=10,
                                             headers={"User-Agent": "Mozilla/5.0"})
                        if img_r.ok and len(img_r.content) > 500:
                            photo_ocr = ocr_image(img_r.content)
                            if photo_ocr and len(photo_ocr) > 20:
                                combined += f"\nImage text: {photo_ocr[:500]}"
                                log.info(f"fxtwitter photo OCR: {photo_ocr[:80]}")
                    except Exception as _pe:
                        log.debug(f"fxtwitter photo OCR failed: {_pe}")
            log.info(f"fxtwitter extracted: {combined[:100]} date={post_date or 'unknown'}")
            return combined, post_date
    except Exception as e:
        log.error(f"fxtwitter failed: {e}")
    return "", ""

def download_video_url(url):
    """Cobalt API → yt-dlp → fxtwitter (X/Twitter) → OG metadata fallback.
    Returns (video_bytes, metadata, post_date). post_date is 'YYYY-MM-DD' or ''."""
    video_bytes, metadata, post_date = _cobalt_download(url)
    if video_bytes: return video_bytes, metadata, post_date
    log.info("Cobalt failed, trying yt-dlp...")
    video_bytes, metadata, post_date = _ytdlp_download(url)
    if video_bytes: return video_bytes, metadata, post_date
    # For X/Twitter URLs: extract tweet text via fxtwitter before giving up
    if "twitter.com" in url or "x.com" in url:
        tweet_text, tweet_date = _fxtwitter_text(url)
        if tweet_text:
            return None, tweet_text, tweet_date
    log.info("yt-dlp failed, extracting OG metadata...")
    return None, _og_metadata(url), ""

# ── OSINT / Media Verification ────────────────────────────────────────────────

def extract_exif_info(image_bytes):
    """Extract key EXIF metadata. Returns dict with findings or {}."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        import io as _io
        img = Image.open(_io.BytesIO(image_bytes))
        raw = img._getexif()
        if not raw:
            return {}
        keep = ("DateTimeOriginal", "DateTime", "DateTimeDigitized",
                "GPSInfo", "Make", "Model", "Software", "ImageDescription")
        result = {}
        for tag_id, val in raw.items():
            tag = TAGS.get(tag_id, str(tag_id))
            if tag in keep:
                result[tag] = str(val)[:200]
        return result
    except Exception as e:
        log.debug(f"EXIF: {e}")
        return {}

def wayback_earliest(url):
    """Return earliest Wayback Machine archive date for a URL, or None."""
    try:
        r = requests.get(
            "http://web.archive.org/cdx/search/cdx",
            params={"url": url, "output": "json", "limit": 2,
                    "fl": "timestamp,statuscode", "filter": "statuscode:200"},
            timeout=8
        )
        if r.ok:
            rows = r.json()
            if len(rows) > 1:
                ts = rows[1][0]  # YYYYMMDDHHMMSS
                return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    except Exception as e:
        log.debug(f"Wayback: {e}")
    return None

def tineye_search(image_bytes):
    """Reverse image search via TinEye. Returns list of match dicts (url, domain, crawl_date)."""
    if not TINEYE_API_SECRET:
        return []
    try:
        r = requests.post(
            "https://api.tineye.com/rest/search/",
            headers={"X-API-KEY": TINEYE_API_SECRET},
            files={"image": ("img.jpg", image_bytes, "image/jpeg")},
            timeout=20
        )
        if r.ok:
            matches = r.json().get("results", {}).get("matches", [])
            return [{"url": m.get("backlinks", [{}])[0].get("url", ""),
                     "domain": m.get("domain", ""),
                     "crawl_date": m.get("crawl_date", "")} for m in matches[:6]]
    except Exception as e:
        log.warning(f"TinEye: {e}")
    return []

def tineye_search_url(image_url):
    """TinEye reverse search by image URL."""
    if not TINEYE_API_SECRET:
        return []
    try:
        r = requests.get(
            "https://api.tineye.com/rest/search/",
            headers={"X-API-KEY": TINEYE_API_SECRET},
            params={"url": image_url},
            timeout=15
        )
        if r.ok:
            matches = r.json().get("results", {}).get("matches", [])
            return [{"url": m.get("backlinks", [{}])[0].get("url", ""),
                     "domain": m.get("domain", ""),
                     "crawl_date": m.get("crawl_date", "")} for m in matches[:6]]
    except Exception as e:
        log.warning(f"TinEye URL: {e}")
    return []

def _google_vision_web(image_bytes=None, image_url=None):
    """Google Cloud Vision web detection — finds where an image appears on the web.
    Returns list of match dicts (url, domain, title) + best_guess_labels list."""
    if not GOOGLE_VISION_KEY:
        return []
    import base64 as _b64
    try:
        if image_bytes:
            content = _b64.b64encode(image_bytes).decode()
            body = {"requests": [{"image": {"content": content},
                                  "features": [{"type": "WEB_DETECTION", "maxResults": 10}]}]}
        else:
            body = {"requests": [{"image": {"source": {"imageUri": image_url}},
                                  "features": [{"type": "WEB_DETECTION", "maxResults": 10}]}]}
        r = requests.post(
            f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_KEY}",
            json=body, timeout=20)
        if not r.ok:
            log.warning(f"Google Vision: {r.status_code} {r.text[:100]}")
            return []
        web = r.json().get("responses", [{}])[0].get("webDetection", {})
        matches = []
        # Full matching images (exact copies)
        for m in web.get("fullMatchingImages", [])[:3]:
            url = m.get("url", "")
            if url:
                from urllib.parse import urlparse
                matches.append({"url": url, "domain": urlparse(url).netloc, "match_type": "exact"})
        # Pages with matching images
        for p in web.get("pagesWithMatchingImages", [])[:4]:
            url = p.get("url", "")
            title = p.get("pageTitle", "")
            if url:
                from urllib.parse import urlparse
                matches.append({"url": url, "domain": urlparse(url).netloc,
                                "title": title[:100], "match_type": "page"})
        # Best guess labels (what the image shows)
        labels = [e.get("label","") for e in web.get("bestGuessLabels", []) if e.get("label")]
        if labels:
            matches.insert(0, {"_labels": labels})  # prepend label metadata
        log.info(f"Google Vision: {len(matches)} results, labels={labels}")
        return matches
    except Exception as e:
        log.warning(f"Google Vision: {e}")
        return []

def _reverse_image_search(image_bytes=None, image_url=None):
    """Route reverse image search to Google Vision or TinEye based on REVERSE_IMAGE_ENGINE."""
    if REVERSE_IMAGE_ENGINE == "google":
        return _google_vision_web(image_bytes=image_bytes, image_url=image_url)
    elif REVERSE_IMAGE_ENGINE == "tineye":
        if image_bytes:
            return tineye_search(image_bytes)
        elif image_url:
            return tineye_search_url(image_url)
    return []

def hive_ai_check(image_bytes=None, image_url=None):
    """Check for AI-generated/deepfake content via Hive V3 API.
    Returns dict: {ai_generated: 0.0-1.0, deepfake: 0.0-1.0, generator: str} or {}."""
    if not HIVE_API_KEY:
        return {}
    try:
        url = "https://api.thehive.ai/api/v3/hive/ai-generated-and-deepfake-content-detection"
        hdrs = {"authorization": f"Bearer {HIVE_API_KEY}"}
        if image_url:
            r = requests.post(url, headers=hdrs,
                              json={"input": [{"media_url": image_url}]},
                              timeout=25)
        else:
            import base64 as _b64
            b64 = _b64.b64encode(image_bytes).decode()
            r = requests.post(url, headers=hdrs,
                              json={"input": [{"media_base64": b64}]},
                              timeout=25)
        if not r.ok:
            log.debug(f"Hive: {r.status_code} {r.text[:100]}")
            return {}
        classes = r.json().get("output", [{}])[0].get("classes", [])
        results = {}
        generators = []
        for cls in classes:
            name, val = cls.get("class", ""), cls.get("value", 0)
            if name == "ai_generated":
                results["ai_generated"] = round(val, 3)
            elif name == "deepfake":
                results["deepfake"] = round(val, 3)
            elif not name.startswith("not_") and name not in ("ai_generated", "deepfake", "none") and val > 0.1:
                generators.append((name, round(val, 3)))
        if generators:
            generators.sort(key=lambda x: -x[1])
            results["generator"] = generators[0][0]
        return results
    except Exception as e:
        log.warning(f"Hive: {e}")
        return {}

def run_osint(image_bytes=None, source_url=None, og_image_url=None):
    """Run all applicable OSINT checks in parallel. Returns findings dict."""
    findings = {}
    tasks = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        if image_bytes:
            tasks["exif"]   = ex.submit(extract_exif_info, image_bytes)
            tasks["revimg"] = ex.submit(_reverse_image_search, image_bytes, None)
            tasks["hive"]   = ex.submit(hive_ai_check, image_bytes, None)
        if og_image_url:
            if not image_bytes:
                tasks["revimg_url"] = ex.submit(_reverse_image_search, None, og_image_url)
                tasks["hive_url"]   = ex.submit(lambda: _hive_from_url(og_image_url))
        if source_url:
            tasks["wayback"] = ex.submit(wayback_earliest, source_url)

    for key, fut in tasks.items():
        try:
            findings[key] = fut.result(timeout=25)
        except Exception as e:
            log.debug(f"OSINT {key}: {e}")

    # Normalise reverse image results under one key
    rev_matches = findings.get("revimg") or findings.get("revimg_url") or []
    if rev_matches:
        findings["rev_matches"] = rev_matches

    return findings

def _hive_from_url(image_url):
    """Run Hive check directly by URL (V3 accepts media_url natively)."""
    return hive_ai_check(image_url=image_url)

def fmt_osint(findings):
    """Format OSINT findings as a WhatsApp-friendly section. Returns lines list."""
    if not findings:
        return []
    lines = ["*OSINT VERIFICATION*"]
    added = False

    # EXIF metadata
    exif = findings.get("exif", {})
    if exif:
        date_taken = exif.get("DateTimeOriginal") or exif.get("DateTime")
        if date_taken:
            lines.append(f"📷 _EXIF date taken: {date_taken[:19]}_")
            added = True
        camera = exif.get("Make","") + (" " + exif.get("Model","")).strip()
        if camera.strip():
            lines.append(f"📷 _Camera: {camera.strip()}_")
            added = True
        if exif.get("Software") and any(k in exif["Software"].lower() for k in ("photoshop","gimp","lightroom","edit")):
            lines.append(f"⚠️ _Edited with: {exif['Software']}_")
            added = True
        if exif.get("GPSInfo"):
            lines.append("📍 _GPS coordinates embedded in image_")
            added = True

    # Reverse image search results (Google Vision or TinEye)
    rev = findings.get("rev_matches") or findings.get("tineye_matches", [])
    ran_revimg = "revimg" in findings or "revimg_url" in findings or "tineye" in findings or "tineye_url" in findings
    if rev:
        # Extract Google Vision best-guess labels if present
        labels = []
        real_matches = []
        for m in rev:
            if "_labels" in m:
                labels = m["_labels"]
            else:
                real_matches.append(m)
        if labels:
            lines.append(f"🏷️ _Image shows: {', '.join(labels[:3])}_")
            added = True
        exact = [m for m in real_matches if m.get("match_type") == "exact"]
        pages = [m for m in real_matches if m.get("match_type") != "exact"]
        if exact:
            lines.append(f"🔍 _Reverse image: {len(exact)} exact copy/copies found online_")
            for m in exact[:2]:
                lines.append(f"   • {m.get('domain','')}")
            added = True
        if pages:
            lines.append(f"🔍 _Image appears on {len(pages)} web page(s)_")
            for m in pages[:3]:
                title = m.get("title","") or m.get("domain","")
                lines.append(f"   • {title[:60]}")
            added = True
        if not exact and not pages and not labels:
            # TinEye-style plain matches
            lines.append(f"🔍 _Reverse image: found in {len(rev)} other source(s)_")
            for m in rev[:3]:
                if m.get("domain"):
                    lines.append(f"   • {m['domain']}")
            added = True
    elif ran_revimg:
        lines.append("🔍 _Reverse image: no matches found (image appears original)_")
        added = True

    # Hive AI/deepfake detection
    hive = findings.get("hive") or findings.get("hive_url") or {}
    if isinstance(hive, dict):
        ai_score = hive.get("ai_generated")
        df_score = hive.get("deepfake")
        if ai_score is not None:
            pct = int(ai_score * 100)
            if ai_score > 0.7:
                generator = hive.get("generator", "")
                gen_label = f" _(likely {generator})_" if generator else ""
                lines.append(f"🤖 _AI-generated: {pct}% probability{gen_label}_")
            elif ai_score > 0.4:
                lines.append(f"⚠️ _AI-generated: {pct}% probability — treat with caution_")
            else:
                lines.append(f"✅ _Authenticity verified: {100-pct}% probability genuine — not AI-generated or manipulated_")
            added = True
        if df_score is not None:
            pct = int(df_score * 100)
            if df_score > 0.7:
                lines.append(f"🎭 _Deepfake: {pct}% probability_")
            elif df_score > 0.4:
                lines.append(f"⚠️ _Deepfake: {pct}% probability — treat with caution_")
            else:
                lines.append(f"✅ _No deepfake detected ({pct}% probability)_")
            added = True

    # Wayback Machine
    wayback = findings.get("wayback")
    if wayback:
        lines.append(f"🕰️ _Earliest web archive: {wayback}_")
        added = True

    if not added:
        return []
    lines.append("")
    return lines

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
    if SRC_AFP:             sources.append("AFP Fact Check")
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
    if SRC_MEMO:            sources.append("Middle East Monitor")
    if SRC_NEWARAB:         sources.append("The New Arab")
    if SRC_BTSELEM:         sources.append("B'Tselem")
    if SRC_BELLINGCAT:      sources.append("Bellingcat")
    if SRC_HRW:             sources.append("Human Rights Watch")
    if SRC_AMNESTY:         sources.append("Amnesty International")
    if SRC_UNNEWS:          sources.append("UN News")
    if SRC_TOI:             sources.append("Times of Israel")
    if SRC_ARABNEWS:        sources.append("Arab News")
    if SRC_RESPSTATECRAFT:  sources.append("Responsible Statecraft")
    if SRC_ANADOLU:         sources.append("Anadolu Agency")
    if SRC_ALMONITOR:       sources.append("Al-Monitor")
    if SRC_DAWN:            sources.append("DAWN")
    if SRC_MISBAR:          sources.append("Misbar")
    if SRC_FATABYYANO:      sources.append("Fatabyyano")
    if SRC_VERIFYSY:        sources.append("Verify-Sy")
    if SRC_AFRICACHECK:     sources.append("Africa Check")
    if SRC_PESACHECK:       sources.append("PesaCheck")
    if SRC_DUBAWA:          sources.append("Dubawa")
    if SRC_ALTNEWS:         sources.append("Alt News")
    if SRC_BOOMLIVE:        sources.append("Boom Live")
    if SRC_RAPPLER:         sources.append("Rappler")
    if SRC_CHEQUEADO:       sources.append("Chequeado")
    if SRC_LOGICALLY:       sources.append("Logically Facts")
    if TAVILY_API_KEY:        sources.append("Live Web Search")
    if BRAVE_API_KEY:         sources.append("Brave Search (live)")
    if PERPLEXITY_API_KEY:    sources.append("Perplexity Sonar (live)")
    for name, _ in parse_custom_sources():
        sources.append(f"{name} (custom)")
    return sources

# Topic → priority sources. Each entry: (set_of_keywords, [ordered_source_names]).
# Keywords matched case-insensitively against the combined query+claims text.
_TOPIC_SOURCE_MAP = [
    # Francophone Africa / France
    ({"france", "french", "francophone", "senegal", "mali", "burkina", "niger",
      "cameroon", "côte d'ivoire", "ivory coast", "guinea", "congo", "drc",
      "madagascar", "benin", "togo", "chad", "gabon", "rwanda", "burundi",
      "algeria", "morocco", "tunisia", "macron", "le pen", "paris"},
     ["RFI", "France 24", "Jeune Afrique", "Le Monde", "Africa Check"]),

    # Sub-Saharan Africa (English)
    ({"africa", "african", "kenya", "nigeria", "ghana", "ethiopia", "somalia",
      "sudan", "south africa", "zimbabwe", "tanzania", "uganda",
      "mozambique", "zambia", "malawi", "botswana", "namibia",
      "liberia", "sierra leone", "angola"},
     ["Africa Check", "PesaCheck", "Dubawa", "Daily Maverick", "Logically Facts"]),

    # South Asia / Urdu / Pakistan
    ({"pakistan", "pakistani", "imran khan", "pmln", "pti", "isi", "lahore",
      "karachi", "islamabad", "urdu", "punjab", "sindh", "balochistan",
      "kashmir", "india", "indian", "modi", "bjp", "hindutva", "delhi",
      "mumbai", "bangladesh", "sri lanka", "nepal"},
     ["Geo News", "Dawn (Pakistan)", "BBC Urdu", "ARY News", "Alt News", "Boom Live"]),

    # Palestine / Israel
    ({"palestine", "palestinian", "israel", "israeli", "gaza", "west bank",
      "hamas", "idf", "hezbollah", "al-aqsa", "jerusalem", "netanyahu",
      "occupation", "settler", "intifada", "zionist", "ceasefire", "rafah",
      "apartheid", "nakba", "iof"},
     ["Al Jazeera", "Middle East Eye", "972 Magazine", "Electronic Intifada",
      "B'Tselem", "Mondoweiss", "Middle East Monitor", "Misbar", "Haaretz"]),

    # Wider Middle East / Muslim world
    ({"iran", "iraq", "syria", "lebanon", "jordan", "saudi", "yemen", "bahrain",
      "qatar", "kuwait", "oman", "uae", "dubai", "riyadh", "tehran", "baghdad",
      "damascus", "beirut", "middle east", "mena", "muslim", "islamic", "islam",
      "mosque", "shia", "sunni", "quran", "isis", "isil", "daesh", "caliphate",
      "arab", "arabic"},
     ["Al Jazeera", "Middle East Eye", "Anadolu Agency", "Arab News",
      "Al-Monitor", "The New Arab", "Yeni Safak", "DAWN",
      "Fatabyyano", "Misbar", "Verify-Sy"]),

    # Turkey
    ({"turkey", "turkish", "erdogan", "ankara", "istanbul", "kurdish", "pkk"},
     ["Anadolu Agency", "Al-Monitor", "Yeni Safak", "Middle East Eye"]),

    # India / South Asia
    ({"india", "indian", "pakistan", "pakistani", "bangladesh", "modi", "bjp",
      "hindu", "kashmir", "delhi", "mumbai", "hindutva", "south asia",
      "sri lanka", "nepal"},
     ["Alt News", "Boom Live", "Logically Facts"]),

    # Philippines / Southeast Asia
    ({"philippines", "filipino", "marcos", "duterte", "manila", "southeast asia",
      "myanmar", "burma", "thailand", "indonesia", "malaysia", "vietnam",
      "cambodia", "laos", "singapore"},
     ["Rappler", "Boom Live", "Alt News"]),

    # Latin America
    ({"latin america", "argentina", "brazil", "venezuela", "colombia", "mexico",
      "chile", "peru", "bolivia", "ecuador", "uruguay", "paraguay",
      "central america", "cuba", "haiti", "dominican"},
     ["Chequeado", "Rappler"]),

    # Ukraine / Russia
    ({"ukraine", "ukrainian", "russia", "russian", "putin", "zelensky", "nato",
      "kremlin", "moscow", "kyiv", "donbas", "crimea", "wagner", "novichok"},
     ["Bellingcat", "Reuters", "AP News", "BBC News", "The Intercept"]),

    # US politics
    ({"trump", "biden", "republican", "democrat", "congress", "senate",
      "white house", "maga", "election fraud", "january 6", "cia", "fbi",
      "pentagon", "immigration", "border wall", "obamacare"},
     ["FactCheck.org", "PolitiFact", "Snopes", "AP News"]),

    # UK politics
    ({"uk", "britain", "england", "labour", "tory", "conservative", "parliament",
      "boris", "sunak", "starmer", "keir", "nhs", "brexit", "scotland",
      "wales", "northern ireland"},
     ["FullFact", "BBC News", "The Guardian", "Novara Media", "The Canary"]),

    # Human rights / war crimes
    ({"genocide", "war crime", "ethnic cleansing", "occupation", "refugee",
      "displaced", "civilian casualties", "airstrike", "massacre", "torture",
      "arbitrary detention", "extrajudicial"},
     ["Human Rights Watch", "Amnesty International", "UN News", "Bellingcat",
      "B'Tselem"]),

    # Disinfo / AI / media manipulation
    ({"deepfake", "ai generated", "artificial intelligence", "misinformation",
      "disinformation", "propaganda", "fake news", "manipulated", "edited video",
      "synthetic media"},
     ["Bellingcat", "Logically Facts", "Snopes", "Africa Check"]),
]


def _source_preview_msg(topic_text=""):
    """Return (total_count, preview_string) with a balanced, rotating source preview.

    Always shows a mix of Western, regional/Middle East, Spanish/LatAm and fact-check
    sources so the display signals Fred's impartiality regardless of topic.
    Up to 2 slots reserved for topic-relevant sources (shuffled so they rotate).
    """
    all_src = enabled_sources()
    total = len(all_src)
    all_src_set = set(all_src)
    ql = topic_text.lower()

    # Step 1: collect topic-priority sources, shuffled so different ones surface each time
    priority = []
    for keywords, sources in _TOPIC_SOURCE_MAP:
        if any(kw in ql for kw in keywords):
            for s in sources:
                if s in all_src_set and s not in priority:
                    priority.append(s)
    random.shuffle(priority)

    # Step 2: bucket all sources by region category
    by_cat = {}
    for s in all_src:
        cat = _SOURCE_PERSPECTIVE.get(s, "OTHER")
        by_cat.setdefault(cat, []).append(s)

    chosen = []

    # Always guarantee regional balance — one slot per region family + fact-checkers
    # Regions with no enabled sources simply skip, so new regions auto-activate
    _quota = [
        ("WESTERN MAINSTREAM",        2),   # BBC, Reuters, CNN etc.
        ("REGIONAL / MIDDLE EAST",    1),   # Al Jazeera, Middle East Eye etc.
        ("FRENCH / FRANCOPHONE",      1),   # RFI, France 24, Jeune Afrique etc.
        ("SOUTH ASIAN / URDU",        1),   # Geo News, Dawn, BBC Urdu etc.
        ("SPANISH / LATIN AMERICAN",  1),   # Chequeado, Maldita, El País etc.
        ("FACT-CHECK ORGS",           1),   # Snopes, FullFact, AFP Fact Check etc.
        ("INDEPENDENT / ALTERNATIVE", 1),   # Bellingcat, The Intercept, Meduza etc.
    ]
    for cat, n in _quota:
        pool = [s for s in by_cat.get(cat, []) if s not in chosen]
        if pool:
            chosen.extend(random.sample(pool, min(n, len(pool))))

    # Fill remaining slot(s) with topic-priority sources not already shown
    prio_new = [s for s in priority if s not in chosen]
    chosen.extend(prio_new[:max(0, 8 - len(chosen))])

    # Wildcard fill if still under 8
    others = [s for s in all_src if s not in chosen]
    if others and len(chosen) < 8:
        chosen.extend(random.sample(others, min(8 - len(chosen), len(others))))

    # Put topic-relevant sources first in the display, rest shuffled
    prio_shown = [s for s in chosen if s in set(priority)]
    rest = [s for s in chosen if s not in set(prio_shown)]
    random.shuffle(rest)
    final = (prio_shown + rest)[:8]

    preview = ", ".join(final)
    if total > 8:
        preview += f" +{total - 8} more"
    return total, preview


def _fetch_source(name, url):
    """Fetch a single source — returns (name, text) or None."""
    try:
        txt = fetch(url, timeout=7)
        if txt and len(txt) > 150:
            return (name, txt[:400])
    except Exception as e:
        log.warning(f"Scrape failed {name}: {e}")
    return None

def brave_search(query, count=5):
    """Brave Search API — activate via BRAVE_API_KEY env var (TODO: enable when free tier available)."""
    if not BRAVE_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query[:200], "count": count, "text_decorations": False},
            timeout=8
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("web", {}).get("results", []):
            snippet = f"{item.get('title','')} — {item.get('description','')} ({item.get('url','')})"
            results.append(("Brave Search", snippet[:400]))
        return results
    except Exception as e:
        log.warning("Brave Search failed: %s", e)
        return []


def brave_search_arabic(query, count=5):
    """Brave Search with Arabic language filter — surfaces Arabic-language news sources.
    Uses same BRAVE_API_KEY. Only called for MENA-related claims via _is_mena_topic().
    """
    if not BRAVE_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query[:200], "count": count, "text_decorations": False,
                    "search_lang": "ar", "country": "ae"},
            timeout=8
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("web", {}).get("results", []):
            url = item.get("url", "")
            source_name = _url_to_source_name(url)
            snippet = f"{item.get('title','')} — {item.get('description','')} ({url})"
            results.append((f"{source_name} (Arabic)", snippet[:400]))
        log.info(f"Brave Arabic: {len(results)} results")
        return results
    except Exception as e:
        log.warning("Brave Arabic search failed: %s", e)
        return []


def brave_search_spanish(query, count=5):
    """Brave Search with Spanish language filter — surfaces Latin American and Spanish news.
    Uses same BRAVE_API_KEY. Covers Chequeado, Maldita, Telesur, BBC Mundo, El País etc.
    """
    if not BRAVE_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query[:200], "count": count, "text_decorations": False,
                    "search_lang": "es", "country": "mx"},
            timeout=8
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("web", {}).get("results", []):
            url = item.get("url", "")
            source_name = _url_to_source_name(url)
            snippet = f"{item.get('title','')} — {item.get('description','')} ({url})"
            results.append((f"{source_name} (Spanish)", snippet[:400]))
        log.info(f"Brave Spanish: {len(results)} results")
        return results
    except Exception as e:
        log.warning("Brave Spanish search failed: %s", e)
        return []


def youtube_search(query, max_results=4):
    """Search YouTube Data API v3 for relevant videos from credible sources.
    Targets official news channels and verified organisations.
    Free tier: 10,000 units/day; each search costs ~100 units.
    Activate via YOUTUBE_API_KEY env var (Google Cloud Console).
    Returns list of (name, snippet) tuples."""
    if not YOUTUBE_API_KEY:
        return []
    # Append 'official' hint to bias results toward verified channels
    search_query = f"{query} official statement"[:150]
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": YOUTUBE_API_KEY,
                "q": search_query,
                "part": "snippet",
                "type": "video",
                "order": "relevance",
                "maxResults": max_results,
                "relevanceLanguage": "en",
                "safeSearch": "moderate",
            },
            timeout=10,
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("items", []):
            sn = item.get("snippet", {})
            channel = sn.get("channelTitle", "YouTube")
            title = sn.get("title", "")
            description = sn.get("description", "")[:200]
            vid_id = item.get("id", {}).get("videoId", "")
            url = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
            snippet = f"{title} — {description} ({url})"
            results.append((f"YouTube / {channel}", snippet[:450]))
        log.info(f"YouTube: {len(results)} results for query")
        return results
    except Exception as e:
        log.warning("YouTube search failed: %s", e)
        return []


def perplexity_search(query, post_date=None):
    """Query Perplexity Sonar (real-time web search AI) for current-events grounding.
    Bridges Claude's Aug-2025 knowledge cutoff. Returns list of (name, snippet) tuples.
    Activate via PERPLEXITY_API_KEY env var (perplexity.ai).
    Model: sonar — live web search + synthesis with citations. ~$0.005/query."""
    if not PERPLEXITY_API_KEY:
        return []
    try:
        import datetime as _dt_px
        today = _dt_px.date.today().isoformat()
        year = post_date[:4] if (post_date and len(post_date) >= 4) else str(_dt_px.date.today().year)
        dated_query = query if year in query else f"{query} {year}"
        prompt = (
            f"Today is {today}. You are a fact-checker with live web access. "
            f"What do the most recent sources say about the following claim? "
            f"Be specific — cite publication names, dates, and direct quotes where possible. "
            f"Focus on facts from {year} onwards.\n\nCLAIM: {dated_query[:400]}"
        )
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"},
            json={"model": "sonar", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 600, "temperature": 0.1},
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        citations = data.get("citations", [])
        results = []
        if answer:
            results.append(("Perplexity Sonar (live)", answer[:800]))
        # Add individual cited sources with their real publication names
        for cite_url in citations[:5]:
            src_name = _url_to_source_name(cite_url)
            results.append((src_name, f"(cited by Perplexity) {cite_url}"))
        log.info(f"Perplexity: {len(answer)} chars, {len(citations)} citations")
        return results
    except Exception as e:
        log.warning(f"Perplexity search failed: {e}")
        return []


_DOMAIN_TO_SOURCE = {
    # Wire services
    "reuters.com": "Reuters", "apnews.com": "AP News", "afp.com": "AFP",
    "bloomberg.com": "Bloomberg", "axios.com": "Axios",
    # Western mainstream news
    "bbc.com": "BBC", "bbc.co.uk": "BBC",
    "theguardian.com": "The Guardian", "guardian.com": "The Guardian",
    "nytimes.com": "New York Times", "washingtonpost.com": "Washington Post",
    "independent.co.uk": "The Independent", "telegraph.co.uk": "The Telegraph",
    "cnn.com": "CNN", "nbcnews.com": "NBC News", "cbsnews.com": "CBS News",
    "cnbc.com": "CNBC", "abcnews.go.com": "ABC News", "foxnews.com": "Fox News",
    "breitbart.com": "Breitbart", "huffpost.com": "HuffPost", "newsweek.com": "Newsweek",
    "time.com": "Time", "politico.com": "Politico", "thehill.com": "The Hill",
    "npr.org": "NPR", "pbs.org": "PBS NewsHour",
    "itv.com": "ITV News", "skynews.com": "Sky News", "sky.com": "Sky News",
    "msn.com": "MSN News", "yahoo.com": "Yahoo News",
    # European / Spanish-language
    "elpais.com": "EL PAÍS", "lemonde.fr": "Le Monde",
    "spiegel.de": "Der Spiegel", "euronews.com": "Euronews",
    # Entertainment / culture
    "hollywoodreporter.com": "The Hollywood Reporter",
    "rollingstone.com": "Rolling Stone", "variety.com": "Variety",
    "deadline.com": "Deadline", "people.com": "People",
    "ew.com": "Entertainment Weekly", "indiewire.com": "IndieWire",
    # Fact-checkers
    "snopes.com": "Snopes", "factcheck.org": "FactCheck.org",
    "politifact.com": "PolitiFact", "fullfact.org": "FullFact",
    # Middle East / regional
    "aljazeera.com": "Al Jazeera", "middleeasteye.net": "Middle East Eye",
    "haaretz.com": "Haaretz", "arabnews.com": "Arab News",
    "anadoluagency.com": "Anadolu Agency", "aa.com.tr": "Anadolu Agency",
    # Independent / alt
    "theintercept.com": "The Intercept", "democracynow.org": "Democracy Now",
    "novaramedia.com": "Novara Media",
    "responsiblestatecraft.org": "Responsible Statecraft",
    "jacobin.com": "Jacobin", "commondreams.org": "Common Dreams",
    "antiwar.com": "Antiwar.com",
    # Reference
    "wikipedia.org": "Wikipedia",
    # South Asian
    "dawn.com": "Dawn (Pakistan)", "thenews.com.pk": "The News International",
    "thehindu.com": "The Hindu", "ndtv.com": "NDTV", "hindustantimes.com": "Hindustan Times",
    "timesofindia.com": "Times of India", "tribuneindia.com": "The Tribune India",
    "geo.tv": "Geo News", "jang.com.pk": "Jang", "arynews.tv": "ARY News",
    "bbc.com/urdu": "BBC Urdu", "urdu.geo.tv": "Geo Urdu",
    # French-language
    "rfi.fr": "RFI", "france24.com": "France 24",
    "jeuneafrique.com": "Jeune Afrique", "liberation.fr": "Libération",
    "lefigaro.fr": "Le Figaro", "20minutes.fr": "20 Minutes",
    "afrik.com": "Afrik.com", "apanews.net": "APA News",
    # Swahili / East Africa
    "bbc.com/swahili": "BBC Swahili", "voaswahili.com": "VOA Swahili",
    "thecitizen.co.tz": "The Citizen Tanzania", "standardmedia.co.ke": "Standard Media Kenya",
    # Independent Russian
    "meduza.io": "Meduza",
    # African
    "dailymaverick.co.za": "Daily Maverick", "allafrica.com": "AllAfrica",
    "premiumtimesng.com": "Premium Times Nigeria", "punchng.com": "Punch Nigeria",
    "monitor.co.ug": "Daily Monitor Uganda", "nation.africa": "Nation Africa",
    "nairobitimes.co.ke": "Nairobi Times",
    "dailysabah.com": "Daily Sabah", "trtworld.com": "TRT World",
    # Video platforms
    "youtube.com": "YouTube", "youtu.be": "YouTube",
}

def _url_to_source_name(url):
    """Return a readable publication name from a URL, falling back to the domain."""
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        # Strip common non-www subdomains that mask the real domain
        _STRIP_PREFIXES = ("www.", "edition.", "en.", "m.", "mobile.", "amp.", "news.", "static.")
        domain = netloc
        for pfx in _STRIP_PREFIXES:
            if domain.startswith(pfx):
                domain = domain[len(pfx):]
                break
        if domain in _DOMAIN_TO_SOURCE:
            return _DOMAIN_TO_SOURCE[domain]
        # Try last two parts of domain (e.g. "politics.theguardian.com" → "theguardian.com")
        parts = domain.split(".")
        if len(parts) >= 2:
            sld = ".".join(parts[-2:])
            if sld in _DOMAIN_TO_SOURCE:
                return _DOMAIN_TO_SOURCE[sld]
        # Try matching on second-level domain root (e.g. "rollingstone.co.uk" → "rollingstone")
        base = parts[0]
        for k, v in _DOMAIN_TO_SOURCE.items():
            if k.startswith(base + "."):
                return v
        # Generic: capitalise domain root
        return base.replace("-", " ").title()
    except Exception:
        return "Live Web Search"

def tavily_search(query, max_results=12, post_date=None):
    """Query Tavily Search API for real-time results. Returns list of (name, snippet) tuples."""
    if not TAVILY_API_KEY:
        return []

    def _run_tavily(q, include_answer=True):
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": q[:400], "max_results": max_results,
                  "search_depth": "advanced", "include_answer": include_answer},
            timeout=15
        )
        r.raise_for_status()
        return r.json()

    def _parse_tavily(data):
        results = []
        answer = (data.get("answer") or "").strip()
        if answer:
            results.append(("Live Web Search", answer[:600]))
        for item in data.get("results", []):
            title = item.get("title", "")
            content = item.get("content", "")
            url = item.get("url", "")
            source_name = _url_to_source_name(url)
            snippet = f"{title} — {content} ({url})"
            results.append((source_name, snippet[:500]))
        return results

    try:
        import datetime as _dt_tav
        year = post_date[:4] if (post_date and len(post_date) >= 4) else str(_dt_tav.date.today().year)
        dated_query = query if year in query else f"{query} {year}"

        data = _run_tavily(dated_query)
        results = _parse_tavily(data)

        # If thin results (fewer than 5 named sources), retry without year anchor and without
        # vague temporal words like "recently" / "just" that bias Tavily toward current content
        # and filter out historical coverage of past events being recirculated.
        named = [r for r in results if r[0] != "Live Web Search"]
        if len(named) < 5:
            import re as _re
            timeless_query = _re.sub(r'\b(recently|just|newly|new|latest|current|now)\b', '', query, flags=_re.IGNORECASE).strip()
            log.info("Tavily main: thin results (%d named), retrying without year/temporal anchor", len(named))
            data2 = _run_tavily(timeless_query, include_answer=False)
            seen_snippets = {r[1][:80] for r in results}
            for item in data2.get("results", []):
                title = item.get("title", "")
                content = item.get("content", "")
                url = item.get("url", "")
                source_name = _url_to_source_name(url)
                snippet = f"{title} — {content} ({url})"
                if snippet[:80] not in seen_snippets:
                    results.append((source_name, snippet[:500]))
                    seen_snippets.add(snippet[:80])

        log.info("Tavily main: %d results — sources: %s", len(results),
                 [r[0] for r in results])
        return results
    except Exception as e:
        log.warning("Tavily Search failed: %s", e)
        return []

_MENA_KEYWORDS = {
    "iran","israel","palestine","palestinian","gaza","lebanon","syria","iraq","yemen",
    "saudi","egypt","jordan","turkey","hezbollah","hamas","idf","west bank","strait of hormuz",
    "houthi","netanyahu","sinwar","nasrallah","khamenei","irgc","mossad","cia","nsa",
    "occupation","intifada","settler","ceasefire","genocide","apartheid","zionist",
    "arab","muslim","islamic","sunni","shia","mosque","quran","prophet","allah",
    "iran nuclear","oil tanker","rafah","jerusalem","al-quds","tel aviv","beirut",
    "damascus","tehran","riyadh","cairo","baghdad","sanaa","ramallah","nablus",
    "african union","brics","global south","colonialism","nato","russia","ukraine",
}

def _is_mena_topic(text):
    t = text.lower()
    return any(kw in t for kw in _MENA_KEYWORDS)


_LATAM_KEYWORDS = {
    "venezuela","colombia","mexico","argentina","chile","peru","bolivia","ecuador","cuba","nicaragua",
    "brazil","brasil","latin america","latinoamerica","america latina","caribbean","central america",
    "maduro","chavez","lula","morales","ortega","castro","guaido","milei","petro","boric","amlo",
    "narco","cartel","drug war","us sanctions","imf","world bank","coup","golpe","dictator",
    "immigration","migrants","border","deportation","asylum","refugees","us mexico",
    "telesur","chequeado","maldita","efe","el pais","bbc mundo",
    "united states","trump","biden","pentagon","cia","us foreign policy","imperialism",
    "climate","amazon","deforestation","indigenous","nato","russia","china","brics",
}

def _is_latam_topic(text):
    t = text.lower()
    return any(kw in t for kw in _LATAM_KEYWORDS)


def tavily_search_regional(query, post_date=None):
    """Second Tavily pass targeting regional/Global South perspectives.

    Always queries TRT World, Al Jazeera English, Press TV scope.
    For MENA topics, also appends Arabic context terms to surface Arabic sources.
    Returns list of (name, snippet) tuples labelled as Regional.
    """
    if not TAVILY_API_KEY:
        return []
    import datetime as _dt_reg
    year = post_date[:4] if (post_date and len(post_date) >= 4) else str(_dt_reg.date.today().year)
    results = []
    try:
        # Scoped query — regional outlets
        regional_domains = "aljazeera.net OR middleeasteye.net OR presstv.ir OR arabnews.com OR trtworld.com OR africanews.com OR dawn.com OR thehindu.com OR rt.com"
        scoped = f'({query}) site:{" OR site:".join(regional_domains.replace(" OR site:","").split(" OR "))} {year}'
        # Simpler: just append regional context
        regional_query = f"{query} {year} Al Jazeera OR Middle East Eye OR TRT World OR Press TV OR Arab News OR Dawn"
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": regional_query[:400], "max_results": 5,
                  "search_depth": "advanced", "include_answer": False},
            timeout=15
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            url = item.get("url", "")
            source_name = _url_to_source_name(url)
            snippet = f"{item.get('title','')} — {item.get('content','')} ({url})"
            results.append((f"{source_name} (Regional)", snippet[:500]))
    except Exception as e:
        log.warning("Tavily regional search failed: %s", e)

    # For MENA topics: also run Arabic-context query
    if _is_mena_topic(query):
        try:
            arabic_query = f"{query} {year} الشرق الأوسط OR فلسطين OR إيران OR عربي"
            r2 = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": arabic_query[:400], "max_results": 4,
                      "search_depth": "advanced", "include_answer": False},
                timeout=15
            )
            r2.raise_for_status()
            for item in r2.json().get("results", []):
                url = item.get("url", "")
                source_name = _url_to_source_name(url)
                snippet = f"{item.get('title','')} — {item.get('content','')} ({url})"
                results.append((f"{source_name} (Arabic/Regional)", snippet[:500]))
        except Exception as e:
            log.warning("Tavily Arabic search failed: %s", e)

    log.info(f"Tavily regional: {len(results)} results (MENA={_is_mena_topic(query)})")
    return results


def tavily_search_social(query, post_date=None):
    """Tavily pass targeting social media discourse and trending reactions.

    Surfaces how claims are circulating on Twitter/X, Reddit, and public forums.
    Returns list of (name, snippet) tuples labelled as Social/Trending.
    """
    if not TAVILY_API_KEY:
        return []
    import datetime as _dt_soc
    year = post_date[:4] if (post_date and len(post_date) >= 4) else str(_dt_soc.date.today().year)
    results = []
    try:
        social_query = f"{query} {year} twitter OR reddit OR trending OR viral OR reaction"
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": social_query[:400], "max_results": 5,
                  "search_depth": "basic", "include_answer": False},
            timeout=12
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            url = item.get("url", "")
            source_name = _url_to_source_name(url)
            label = "Reddit" if "reddit.com" in url else ("Twitter/X" if "twitter.com" in url or "x.com" in url else f"{source_name} (Social/Trending)")
            snippet = f"{item.get('title','')} — {item.get('content','')} ({url})"
            results.append((label, snippet[:500]))
    except Exception as e:
        log.warning("Tavily social search failed: %s", e)
    log.info(f"Tavily social: {len(results)} results")
    return results


def tavily_search_spanish(query, post_date=None):
    """Tavily pass targeting Spanish-language and Latin American sources.

    Always queries BBC Mundo, El País, Telesur, Chequeado, Maldita, EFE, DW Español.
    Returns list of (name, snippet) tuples labelled as Spanish/LatAm.
    """
    if not TAVILY_API_KEY:
        return []
    import datetime as _dt_es
    year = post_date[:4] if (post_date and len(post_date) >= 4) else str(_dt_es.date.today().year)
    results = []
    try:
        spanish_query = f"{query} {year} BBC Mundo OR Telesur OR El País OR Chequeado OR Maldita OR EFE OR DW Español OR Al Jazeera Español"
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": spanish_query[:400], "max_results": 5,
                  "search_depth": "advanced", "include_answer": False},
            timeout=15
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            url = item.get("url", "")
            source_name = _url_to_source_name(url)
            snippet = f"{item.get('title','')} — {item.get('content','')} ({url})"
            results.append((f"{source_name} (Spanish/LatAm)", snippet[:500]))
    except Exception as e:
        log.warning("Tavily Spanish search failed: %s", e)

    # For LATAM topics: also run Spanish-language query
    if _is_latam_topic(query):
        try:
            latam_query = f"{query} {year} América Latina OR Venezuela OR México OR Argentina OR imperialismo OR EEUU"
            r2 = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": latam_query[:400], "max_results": 4,
                      "search_depth": "advanced", "include_answer": False},
                timeout=15
            )
            r2.raise_for_status()
            for item in r2.json().get("results", []):
                url = item.get("url", "")
                source_name = _url_to_source_name(url)
                snippet = f"{item.get('title','')} — {item.get('content','')} ({url})"
                results.append((f"{source_name} (Spanish/LatAm)", snippet[:500]))
        except Exception as e:
            log.warning("Tavily LatAm Spanish search failed: %s", e)

    log.info(f"Tavily Spanish: {len(results)} results (LATAM={_is_latam_topic(query)})")
    return results


_scrape_cache: dict = {}          # claim_key → (timestamp, result)
_scrape_cache_lock = threading.Lock()
_SCRAPE_CACHE_TTL = 0             # cache disabled

def scrape_sites(query, post_date=None):
    import time as _t
    cache_key = query.strip().lower()[:300]
    with _scrape_cache_lock:
        entry = _scrape_cache.get(cache_key)
        if entry and (_t.time() - entry[0]) < _SCRAPE_CACHE_TTL:
            log.info("scrape_sites cache HIT for: %s", cache_key[:60])
            return entry[1]

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
    # Middle East expanded
    if SRC_MEMO:           fast.append(("Middle East Monitor",   f"https://www.middleeastmonitor.com/?s={q}"))
    if SRC_NEWARAB:        fast.append(("The New Arab",          f"https://www.newarab.com/search?q={qt}"))
    if SRC_BTSELEM:        fast.append(("B'Tselem",              f"https://www.btselem.org/search/{qt}"))
    if SRC_BELLINGCAT:     fast.append(("Bellingcat",            f"https://www.bellingcat.com/?s={q}"))
    if SRC_HRW:            fast.append(("Human Rights Watch",   f"https://www.hrw.org/search?search={q}&content_type=country-page,report,world-report-chapter,news,dispatch,video,blog-post,feature,multimedia&regions[]=9727"))
    if SRC_AMNESTY:        fast.append(("Amnesty International", f"https://www.amnesty.org/en/search/?q={q}&content_type=Post,Page,Resource,Taxonomy&regions=middle-east-north-africa"))
    if SRC_UNNEWS:         fast.append(("UN News",               f"https://news.un.org/en/search?text={qt}"))
    if SRC_TOI:            fast.append(("Times of Israel",       f"https://www.timesofisrael.com/?s={q}"))
    if SRC_ARABNEWS:       fast.append(("Arab News",             f"https://www.arabnews.com/search/site/{qt}"))
    if SRC_RESPSTATECRAFT: fast.append(("Responsible Statecraft",f"https://responsiblestatecraft.org/?s={q}"))
    if SRC_ANADOLU:        fast.append(("Anadolu Agency",         f"https://www.aa.com.tr/en/search/?q={qt}"))
    if SRC_ALMONITOR:      fast.append(("Al-Monitor",             f"https://www.al-monitor.com/search#q={qt}"))
    if SRC_DAWN:           fast.append(("DAWN",                   f"https://dawnmena.org/?s={q}"))
    # Global South / non-Western fact-checkers
    if SRC_MISBAR:         fast.append(("Misbar",                 f"https://misbar.com/en/search?q={qt}"))
    if SRC_FATABYYANO:     fast.append(("Fatabyyano",             f"https://fatabyyano.net/?s={q}"))
    if SRC_VERIFYSY:       fast.append(("Verify-Sy",              f"https://verify-sy.com/?s={q}"))
    if SRC_AFRICACHECK:    fast.append(("Africa Check",           f"https://africacheck.org/?s={q}"))
    if SRC_PESACHECK:      fast.append(("PesaCheck",              f"https://pesacheck.org/?s={q}"))
    if SRC_DUBAWA:         fast.append(("Dubawa",                 f"https://dubawa.org/?s={q}"))
    if SRC_ALTNEWS:        fast.append(("Alt News",               f"https://www.altnews.in/?s={q}"))
    if SRC_BOOMLIVE:       fast.append(("Boom Live",              f"https://www.boomlive.in/fact-check?page-type=search&q={qt}"))
    if SRC_RAPPLER:        fast.append(("Rappler",                f"https://www.rappler.com/section/fact-check/?s={q}"))
    if SRC_CHEQUEADO:      fast.append(("Chequeado",              f"https://chequeado.com/?s={q}"))
    if SRC_LOGICALLY:      fast.append(("Logically Facts",        f"https://www.logically.ai/factchecks?query={qt}"))

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

    # Real-time web search — main + regional + social in parallel
    with ThreadPoolExecutor(max_workers=8) as _rtex:
        _is_mena     = _is_mena_topic(query_flat)
        _is_latam    = _is_latam_topic(query_flat)
        _ft_main     = _rtex.submit(tavily_search, query_flat, 12, post_date) if TAVILY_API_KEY else None
        _ft_regional = _rtex.submit(tavily_search_regional, query_flat, post_date) if TAVILY_API_KEY else None
        _ft_social   = _rtex.submit(tavily_search_social, query_flat, post_date) if TAVILY_API_KEY else None
        _ft_spanish  = _rtex.submit(tavily_search_spanish, query_flat, post_date) if TAVILY_API_KEY else None
        _ft_brave    = _rtex.submit(brave_search, query_flat) if BRAVE_API_KEY else None
        _ft_brave_ar = _rtex.submit(brave_search_arabic, query_flat) if (BRAVE_API_KEY and _is_mena) else None
        _ft_brave_es = _rtex.submit(brave_search_spanish, query_flat) if (BRAVE_API_KEY and _is_latam) else None
        _ft_perp     = _rtex.submit(perplexity_search, query_flat, post_date) if PERPLEXITY_API_KEY else None
        _ft_yt       = _rtex.submit(youtube_search, query_flat) if YOUTUBE_API_KEY else None

        if _ft_main:
            for name, snippet in (_ft_main.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_regional:
            for name, snippet in (_ft_regional.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_social:
            for name, snippet in (_ft_social.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_spanish:
            for name, snippet in (_ft_spanish.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_brave:
            for name, snippet in (_ft_brave.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_brave_ar:
            for name, snippet in (_ft_brave_ar.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_brave_es:
            for name, snippet in (_ft_brave_es.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_perp:
            for name, snippet in (_ft_perp.result() or []):
                results.append(f"[{name}]: {snippet}")
        if _ft_yt:
            for name, snippet in (_ft_yt.result() or []):
                results.append(f"[{name}]: {snippet}")
    # General Nitter search — corroborate claim across Twitter/X posts
    try:
        nitter_result = _fetch_source("Twitter/X (Nitter)", f"https://nitter.poast.org/search?q={qt}&f=tweets")
        if nitter_result:
            results.append(f"[{nitter_result[0]}]: {nitter_result[1]}")
    except Exception:
        pass

    log.info(f"Scraped {len(results)} sources")
    result = ("\n\n".join(results), [r.split("]")[0].replace("[","").strip() for r in results])
    with _scrape_cache_lock:
        _scrape_cache[cache_key] = (_t.time(), result)
    return result


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
    '"rating_reason":"1 sentence explaining specifically why this rating was chosen over a higher or lower one — e.g. which element could not be confirmed, or what makes it not fully true/false. Empty string if rating is TRUE or FALSE.",'
    '"verdict":"2-3 sentence factual verdict. Do not adopt Western framing by default. Max 400 chars.",'
    '"key_facts":["1 sentence per fact, max 120 chars each"],'
    '"perspectives":"Single sentence covering what all regions found — Western, Middle Eastern/Arabic, African, South Asian, Latin American. Mention any region by name only if it has actual coverage; otherwise say \'No coverage found across all regions\' or \'No coverage found except [region] which reports X\'. Max 150 chars.",'
    '"contested_language":["term — dispute in max 90 chars"],'
    '"context":"1-2 sentences of structural/historical background. Max 180 chars.",'
    '"red_flags":["1 sentence per flag, max 120 chars"],'
    '"who_benefits":"Who gains if this claim is believed. One sentence, max 120 chars. Empty string if benign.",'
    '"media_bias":"1 sentence on source concentration bias, or empty",'
    '"sources":["Name — URL","Name — URL","Name — URL","Name — URL"],'
    '"confidence":"HIGH|MEDIUM|LOW",'
    '"confidence_reason":"1 sentence, max 120 chars"}'
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
    """Split text into individual checkable factual claims (max 3). Returns list of strings."""
    if len(text) < 60:
        return [text]
    if not ANTHROPIC_KEY:
        return [text]
    prompt = (
        "Identify the distinct, independently checkable factual claims in the text below. "
        "Return a JSON array of strings — one string per claim, self-contained and testable. "
        "STRICT MAXIMUM 4 claims. If there is only one claim return a single-element array. "
        "Ignore pure opinion, emotion, and non-falsifiable statements.\n\n"
        "RULE 1 — DIRECT QUOTES TAKE PRIORITY: If the text contains a direct quote attributed "
        "to a named person, extract that ENTIRE quote as a single claim preserving the exact words. "
        "Example: if the text says Larijani posted \"Epstein's network has devised a 9/11-style plot to blame Iran\", "
        "the claim is: 'Larijani posted on X: \"Epstein's network has devised a 9/11-style plot to blame Iran\"' "
        "NOT three separate claims about plots, 9/11, and Iran. Keep the quote whole and verbatim.\n\n"
        "RULE 2 — SUBSTANCE NOT METADATA: Extract WHAT was claimed, never WHEN/WHERE/WHO reported it. "
        "Strip all day-of-week, date, time, and outlet context from claims. "
        "BAD: 'Bessent made this statement on Monday', 'AFP reported this on Sunday'. "
        "GOOD: 'US allowing Iranian oil tankers through Strait of Hormuz', 'Bessent said Iranian ships have been getting through'. "
        "The fact-check result (not the claim) is where timing context belongs.\n\n"
        "RULE 3 — DEDUPLICATE: A paraphrase and a direct quote of the same statement count as one claim. "
        "If the post text paraphrases something that is also quoted verbatim in the image text, "
        "use the verbatim quote and discard the paraphrase.\n\n"
        "RULE 4 — PRESERVE PROPER NOUNS EXACTLY: Copy names, titles, and organisations exactly as they appear "
        "in the source text. Never correct, normalise, or substitute a name — even if you think you recognise "
        "a similar name. If the text says 'Joe Kent', write 'Joe Kent'. If it says 'Jon Kent', write 'Jon Kent'. "
        "Do not change spellings of people's names.\n\n"
        f"TEXT:\n{text[:3000]}\n\n"
        'Respond ONLY with a JSON array, e.g.: ["Claim one", "Claim two"]'
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 400,
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


def assess_content_claims(text, source_type, post_date=None):
    """
    Analyse content and extract verifiable claims BEFORE asking user to confirm.
    Returns dict:
        claims:      list of neutral, self-contained, testable claim strings
        checkable:   bool — True if there are meaningful claims to verify
        reason:      str  — why not checkable (empty if checkable)
        suggestions: list of str — what the user could send instead
    """
    import datetime as _dt
    if not ANTHROPIC_KEY or not text or len(text.strip()) < 10:
        return {"claims": [text] if text and text.strip() else [], "checkable": bool(text and text.strip()), "reason": "", "suggestions": []}

    # Determine reference date for temporal context
    ref_date = post_date or _dt.datetime.utcnow().strftime("%B %Y")
    # Normalise post_date to "Month YYYY" if it's a full timestamp
    if post_date and len(post_date) > 8:
        try:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    ref_date = _dt.datetime.strptime(post_date[:19], fmt).strftime("%B %Y")
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    src_label = {"text": "text message", "image": "image", "audio": "voice note",
                 "video": "video", "url": "post/article", "document": "document"}.get(source_type, "content")
    prompt = (
        f"Analyse this {src_label} and extract ALL independently verifiable factual claims.\n\n"
        f"Today's date: {ref_date}.\n\n"
        "Return a JSON object with exactly these fields:\n"
        '  "claims": array of short, direct factual assertions (max 3), ranked by importance — most significant or potentially false claim first, least important last. State each claim concisely as it was made — do not add background, context, or inferred information not explicitly stated. Empty array if none.\n'
        '  "checkable": true if there are meaningful verifiable claims; false if content is purely opinion, satire, greeting, or too vague/incomplete to check.\n'
        '  "reason": if checkable=false, one short sentence explaining why. Empty string if checkable=true.\n'
        '  "suggestions": if checkable=false, list 1-3 specific things the user could send to enable fact-checking. Empty array if checkable=true.\n\n'
        "Rules:\n"
        "- Keep claims SHORT — 5 to 12 words ideally (e.g. 'Persians are not Arabs', 'Mark Carney called America a mafia state at WEF')\n"
        "- Use the speaker's own framing where possible, stripped of emotional language\n"
        "- Do NOT infer or add context not directly stated (e.g. do not add 'Mark Carney is PM of Canada' if that wasn't the claim made)\n"
        "- Include ALL distinct assertions — do not merge separate claims into one\n"
        "- Prioritise claims that are newsworthy, potentially disputed, or surprising. Deprioritise background biographical facts (job titles, roles, affiliations) that are widely known and uncontroversial — only include them if they are themselves disputed or central to the claim being verified.\n"
        "- Treat factual QUESTIONS as implicit claims to verify: convert them to assertions. "
        "e.g. 'Has Iran asked for a ceasefire?' → 'Iran has asked for a ceasefire'. "
        "'Did Bardem speak at the Oscars?' → 'Javier Bardem spoke at the Oscars'.\n"
        "- Exclude pure rhetoric, predictions, and non-falsifiable philosophical statements\n"
        "- NEVER extract metadata claims. The day/time it was said, which outlet reported it, and where it was published are NOT claims — they are reporting context. "
        "BAD (do not extract): 'Bessent made this statement on Monday', 'Reuters reported this on March 15', 'Bessent spoke at a press conference on Tuesday'. "
        "GOOD (do extract): 'US allowing Iranian oil tankers through Strait of Hormuz', 'Bessent said Iranian ships have been getting through'. "
        "Extract WHAT was claimed, never WHEN or WHERE or WHO reported it.\n"
        f"- For claims about current or recent events (news, conflicts, policy, breaking stories), append 'as of {ref_date}' to anchor them in time — but only when the date materially changes what is being claimed (e.g. 'Strait of Hormuz closed as of {ref_date}', not 'Water boils at 100°C as of {ref_date}')\n\n"
        f"CONTENT:\n{text[:6000]}\n\n"
        'Respond ONLY with valid JSON.'
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 800, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30)
        r.raise_for_status()
        raw = r.json()["content"][0]["text"].strip()
        s = raw.find("{"); e = raw.rfind("}") + 1
        if s >= 0 and e > s:
            data = json.loads(raw[s:e])
            claims = [c.strip() for c in data.get("claims", []) if isinstance(c, str) and c.strip()][:4]
            checkable = bool(data.get("checkable", bool(claims)))
            if claims:
                checkable = True  # if we have claims, always checkable
            reason = str(data.get("reason", "")).strip()
            suggestions = [str(sg).strip() for sg in data.get("suggestions", []) if str(sg).strip()][:3]
            log.info(f"assess_content_claims: checkable={checkable}, {len(claims)} claim(s)")
            return {"claims": claims, "checkable": checkable, "reason": reason, "suggestions": suggestions}
    except Exception as e:
        log.warning(f"assess_content_claims failed: {e}")
    # Fallback: treat as one claim
    return {"claims": [text[:500].strip()], "checkable": True, "reason": "", "suggestions": []}


def claims_confirm_msg(claims, source_type, cost, is_free=False):
    """Confirmation message that shows ranked, enumerated claims with number selection."""
    src = {"text": "Text", "image": "Image", "audio": "Voice Note", "video": "Video",
           "url": "Post / Article", "document": "Document"}
    HDR = "*━━━━━━━━━━━━━━*"
    plural = "claims" if len(claims) > 1 else "claim"
    claim_lines = "\n".join(f"  *{i+1}.* _{c[:150]}_" for i, c in enumerate(claims))
    if len(claims) == 1:
        reply_prompt = f"_Est. cost: ${cost:.4f}_\n\nReply *Y* to fact-check\nReply *N* to cancel"
    elif is_free:
        nums = ", ".join(f"*{i+1}*" for i in range(len(claims)))
        reply_prompt = (
            f"_Est. cost: ${cost:.4f}_\n\n"
            f"Reply {nums} to pick one claim (free plan — one claim per check)\n"
            f"Reply *N* to cancel"
        )
    else:
        nums = ", ".join(f"*{i+1}*" for i in range(len(claims)))
        reply_prompt = (
            f"_Est. cost: ${cost:.4f} per claim_\n\n"
            f"Reply {nums} or *ALL* to fact-check\n"
            f"Reply *N* to cancel"
        )
    return (
        f"{HDR}\n*FACTCHECK PRO*\n_{src.get(source_type, source_type)}_\n{HDR}\n\n"
        f"*Found {len(claims)} verifiable {plural}:*\n\n{claim_lines}\n\n"
        f"{reply_prompt}"
    )


def no_claims_msg(reason, source_type, suggestions):
    """Message sent when no verifiable claims can be extracted — explains why and suggests alternatives."""
    src_label = {"text": "message", "image": "image", "audio": "voice note",
                 "video": "video", "url": "post", "document": "document"}.get(source_type, "content")
    lines = ["⚠️ *No verifiable claims found*\n"]
    if reason:
        lines.append(f"This {src_label} {reason}.")
    else:
        lines.append(f"I couldn't identify any specific, verifiable facts in this {src_label}.")

    # Only show suggestions for non-URL types — for URL posts, Claude's suggestions
    # are often unhelpful (e.g. "share the video" when user already shared a URL)
    if source_type not in ("url",) and suggestions:
        lines.append("\n*To fact-check this, try:*")
        for sg in suggestions:
            lines.append(f"• {sg}")
    elif source_type not in ("url",):
        # Default suggestions for non-URL types
        if source_type == "video":
            lines += [
                "\n*To fact-check this video, try:*",
                "• Send the original URL (TikTok / YouTube / Facebook / Instagram link)",
                "• Take a screenshot of the text overlay or caption and send it as an image",
                "• Copy the claim text and paste it as a WhatsApp message",
            ]
        elif source_type == "image":
            lines += [
                "\n*To fact-check this image, try:*",
                "• Copy the text in the image and send it as a message",
                "• Describe the specific claim you want checked",
            ]
        else:
            lines += [
                "\n*Try sending:*",
                "• The specific claim as a text message",
                "• A screenshot of the content containing the claim",
            ]
    return "\n".join(lines)


def _claude_call(prompt, model="claude-haiku-4-5-20251001", max_tokens=600, system=None):
    """Single Claude API call. Returns text or None. Tracks token cost."""
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
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
        resp = r.json()
        usage = resp.get("usage", {})
        _cost_add(_anthropic_cost_cents(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0)))
        return resp["content"][0]["text"].strip()
    except Exception as e:
        log.warning(f"_claude_call {model}: {e}")
        return None


# Map source names to perspective categories so Claude gets grouped, labelled evidence
_SOURCE_PERSPECTIVE = {
    # Western mainstream wire / broadcast
    "BBC News":          "WESTERN MAINSTREAM",
    "Reuters":           "WESTERN MAINSTREAM",
    "AP News":           "WESTERN MAINSTREAM",
    "The Guardian":      "WESTERN MAINSTREAM",
    "CNN":               "WESTERN MAINSTREAM",
    "Times of Israel":   "WESTERN MAINSTREAM",
    # Fact-check organisations
    "Snopes":            "FACT-CHECK ORGS",
    "FullFact":          "FACT-CHECK ORGS",
    "FactCheck.org":     "FACT-CHECK ORGS",
    "PolitiFact":        "FACT-CHECK ORGS",
    "AFP Fact Check":    "FACT-CHECK ORGS",
    "Misbar":            "FACT-CHECK ORGS",
    "Fatabyyano":        "FACT-CHECK ORGS",
    "Verify-Sy":         "FACT-CHECK ORGS",
    "Africa Check":      "FACT-CHECK ORGS",
    "PesaCheck":         "FACT-CHECK ORGS",
    "Dubawa":            "FACT-CHECK ORGS",
    "Alt News":          "FACT-CHECK ORGS",
    "Boom Live":         "FACT-CHECK ORGS",
    "Rappler":           "FACT-CHECK ORGS",
    "Logically Facts":   "FACT-CHECK ORGS",
    # Human rights / international law
    "Human Rights Watch":    "HUMAN RIGHTS & INTL LAW",
    "Amnesty International": "HUMAN RIGHTS & INTL LAW",
    "B'Tselem":              "HUMAN RIGHTS & INTL LAW",
    "UN News":               "HUMAN RIGHTS & INTL LAW",
    "Bellingcat":            "HUMAN RIGHTS & INTL LAW",
    # Regional / Middle East / Global South
    "Al Jazeera":            "REGIONAL / MIDDLE EAST",
    "Middle East Eye":       "REGIONAL / MIDDLE EAST",
    "Middle East Monitor":   "REGIONAL / MIDDLE EAST",
    "The New Arab":          "REGIONAL / MIDDLE EAST",
    "Anadolu Agency":        "REGIONAL / MIDDLE EAST",
    "Al-Monitor":            "REGIONAL / MIDDLE EAST",
    "972 Magazine":          "REGIONAL / MIDDLE EAST",
    "Electronic Intifada":   "REGIONAL / MIDDLE EAST",
    "Mondoweiss":            "REGIONAL / MIDDLE EAST",
    "Haaretz":               "REGIONAL / MIDDLE EAST",
    "Arab News":             "REGIONAL / MIDDLE EAST",
    "TRT World":             "REGIONAL / MIDDLE EAST",
    "DAWN":                  "REGIONAL / MIDDLE EAST",
    # French-language
    "RFI":                   "FRENCH / FRANCOPHONE",
    "France 24":             "FRENCH / FRANCOPHONE",
    "Jeune Afrique":         "FRENCH / FRANCOPHONE",
    "Le Monde":              "FRENCH / FRANCOPHONE",
    "Libération":            "FRENCH / FRANCOPHONE",
    "Le Figaro":             "FRENCH / FRANCOPHONE",
    "20 Minutes":            "FRENCH / FRANCOPHONE",
    "Afrik.com":             "FRENCH / FRANCOPHONE",
    "APA News":              "FRENCH / FRANCOPHONE",
    # South Asian / Urdu
    "Dawn (Pakistan)":       "SOUTH ASIAN / URDU",
    "The News International":"SOUTH ASIAN / URDU",
    "Geo News":              "SOUTH ASIAN / URDU",
    "Jang":                  "SOUTH ASIAN / URDU",
    "ARY News":              "SOUTH ASIAN / URDU",
    "BBC Urdu":              "SOUTH ASIAN / URDU",
    "Geo Urdu":              "SOUTH ASIAN / URDU",
    "The Hindu":             "SOUTH ASIAN / URDU",
    "NDTV":                  "SOUTH ASIAN / URDU",
    "Hindustan Times":       "SOUTH ASIAN / URDU",
    "Times of India":        "SOUTH ASIAN / URDU",
    # Swahili / East Africa
    "BBC Swahili":           "SWAHILI / EAST AFRICA",
    "VOA Swahili":           "SWAHILI / EAST AFRICA",
    "The Citizen Tanzania":  "SWAHILI / EAST AFRICA",
    "Standard Media Kenya":  "SWAHILI / EAST AFRICA",
    # Independent Russian
    "Meduza":                "INDEPENDENT / ALTERNATIVE",
    # Independent / alternative
    "The Grayzone":           "INDEPENDENT / ALTERNATIVE",
    "The Intercept":          "INDEPENDENT / ALTERNATIVE",
    "Democracy Now":          "INDEPENDENT / ALTERNATIVE",
    "Novara Media":           "INDEPENDENT / ALTERNATIVE",
    "The Canary":             "INDEPENDENT / ALTERNATIVE",
    "Zeteo":                  "INDEPENDENT / ALTERNATIVE",
    "MintPress News":         "INDEPENDENT / ALTERNATIVE",
    "Responsible Statecraft": "INDEPENDENT / ALTERNATIVE",
    "Palestine Solidarity":   "INDEPENDENT / ALTERNATIVE",
    "Double Down News":       "INDEPENDENT / ALTERNATIVE",
    "Double Down News (YouTube)": "INDEPENDENT / ALTERNATIVE",
    # Real-time search AI
    "Perplexity Sonar (live)":    "LIVE WEB SEARCH",
    "Tavily Search":              "LIVE WEB SEARCH",
    "Tavily Summary":             "LIVE WEB SEARCH",
    "Live Web Search":            "LIVE WEB SEARCH",
    # Western mainstream — general news
    "New York Times":      "WESTERN MAINSTREAM",
    "Washington Post":     "WESTERN MAINSTREAM",
    "BBC":                 "WESTERN MAINSTREAM",
    "NBC News":            "WESTERN MAINSTREAM",
    "CBS News":            "WESTERN MAINSTREAM",
    "ABC News":            "WESTERN MAINSTREAM",
    "Fox News":            "WESTERN MAINSTREAM",
    "The Independent":     "WESTERN MAINSTREAM",
    "The Telegraph":       "WESTERN MAINSTREAM",
    "HuffPost":            "WESTERN MAINSTREAM",
    "Newsweek":            "WESTERN MAINSTREAM",
    "Time":                "WESTERN MAINSTREAM",
    "Politico":            "WESTERN MAINSTREAM",
    "The Hill":            "WESTERN MAINSTREAM",
    # Western mainstream — entertainment / culture
    "The Hollywood Reporter": "WESTERN MAINSTREAM",
    "Rolling Stone":          "WESTERN MAINSTREAM",
    "Rolling Stone UK":       "WESTERN MAINSTREAM",
    "Variety":                "WESTERN MAINSTREAM",
    "Deadline":               "WESTERN MAINSTREAM",
    "People":                 "WESTERN MAINSTREAM",
    "Entertainment Weekly":   "WESTERN MAINSTREAM",
    "IndieWire":              "WESTERN MAINSTREAM",
    # Spanish / Latin American
    "Chequeado":           "SPANISH / LATIN AMERICAN",
    "Maldita":             "SPANISH / LATIN AMERICAN",
    "EL PAÍS":             "SPANISH / LATIN AMERICAN",
    "El País":             "SPANISH / LATIN AMERICAN",
    "Telesur":             "SPANISH / LATIN AMERICAN",
    "La Silla Vacía":      "SPANISH / LATIN AMERICAN",
    "Aos Fatos":           "SPANISH / LATIN AMERICAN",
    "BBC Mundo":           "SPANISH / LATIN AMERICAN",
    "Infobae":             "SPANISH / LATIN AMERICAN",
    "La Nación":           "SPANISH / LATIN AMERICAN",
    # European mainstream
    "Der Spiegel":         "WESTERN MAINSTREAM",
    "Euronews":            "WESTERN MAINSTREAM",
    # Wire services
    "Reuters":             "WESTERN MAINSTREAM",
    "AP News":             "WESTERN MAINSTREAM",
    "Associated Press":    "WESTERN MAINSTREAM",
    "AFP":                 "WESTERN MAINSTREAM",
    "Bloomberg":           "WESTERN MAINSTREAM",
    "Axios":               "WESTERN MAINSTREAM",
    # Additional mainstream outlets
    "CNN":                 "WESTERN MAINSTREAM",
    "CNBC":                "WESTERN MAINSTREAM",
    "NPR":                 "WESTERN MAINSTREAM",
    "PBS NewsHour":        "WESTERN MAINSTREAM",
    "The Guardian":        "WESTERN MAINSTREAM",
    "ITV News":            "WESTERN MAINSTREAM",
    "Sky News":            "WESTERN MAINSTREAM",
    "Breitbart":           "WESTERN MAINSTREAM",
    "MSN News":            "WESTERN MAINSTREAM",
    "Yahoo News":          "WESTERN MAINSTREAM",
}

_PERSPECTIVE_ORDER = [
    "LIVE WEB SEARCH",
    "FACT-CHECK ORGS",
    "HUMAN RIGHTS & INTL LAW",
    "REGIONAL / MIDDLE EAST",
    "FRENCH / FRANCOPHONE",
    "SOUTH ASIAN / URDU",
    "SWAHILI / EAST AFRICA",
    "SPANISH / LATIN AMERICAN",
    "INDEPENDENT / ALTERNATIVE",
    "WESTERN MAINSTREAM",
    "OTHER SOURCES",
]

def _group_scraped_by_perspective(scraped_str):
    """Reorder and label scraped evidence blocks by source perspective category."""
    groups = {}
    for block in scraped_str.split("\n\n"):
        if not block.strip():
            continue
        m = re.match(r'^\[([^\]]+)\]:', block)
        src_name = m.group(1) if m else ""
        category = _SOURCE_PERSPECTIVE.get(src_name, "OTHER SOURCES")
        groups.setdefault(category, []).append(block)
    parts = []
    for cat in _PERSPECTIVE_ORDER:
        if cat in groups:
            parts.append(f"── {cat} ──\n" + "\n\n".join(groups[cat]))
    return "\n\n".join(parts) if parts else scraped_str


def claude_analyse(claim, google, scraped, st, post_date=None, osint=None, source_content=None):
    g = "\n".join([f"• {x['source']} [{x['rating']}]: {x['claim']}\n  {x['url']}" for x in google[:5]])
    grouped = _group_scraped_by_perspective(scraped) if scraped else ""

    # Build OSINT summary for Claude's context
    osint_note = ""
    if osint:
        parts = []
        exif = osint.get("exif", {})
        if exif.get("DateTimeOriginal") or exif.get("DateTime"):
            parts.append(f"EXIF date: {exif.get('DateTimeOriginal') or exif.get('DateTime')}")
        if exif.get("Software") and any(k in exif["Software"].lower() for k in ("photoshop","gimp","edit")):
            parts.append(f"Image edited with: {exif['Software']}")
        rev = osint.get("rev_matches") or osint.get("tineye_matches", [])
        real_rev = [m for m in rev if "_labels" not in m]
        labels = next((m["_labels"] for m in rev if "_labels" in m), [])
        if labels:
            parts.append(f"Image content (Google Vision): {', '.join(labels[:3])}")
        if real_rev:
            domains = ", ".join(m.get("domain","") for m in real_rev[:3] if m.get("domain"))
            parts.append(f"Reverse image search: found in {len(real_rev)} other sources ({domains})")
        hive = osint.get("hive") or osint.get("hive_url") or {}
        if isinstance(hive, dict):
            ai_score = hive.get("ai_generated", 0)
            df_score = hive.get("deepfake", 0)
            if ai_score > 0.5:
                parts.append(f"AI-generated detection: {int(ai_score*100)}% probability — treat as potentially synthetic")
            elif ai_score is not None and st in ("video", "image"):
                parts.append(f"AI/deepfake check passed: {int((1-ai_score)*100)}% probability genuine — treat submitted media as authentic primary evidence")
            if df_score > 0.5:
                parts.append(f"Deepfake detection: {int(df_score*100)}% probability — treat as potentially manipulated")
        elif st in ("video", "image") and osint.get("hive") is None and osint.get("hive_url") is None:
            # Hive not run — note that media authenticity is unverified
            parts.append("Media authenticity: not verified (Hive check not available)")
        if osint.get("wayback"):
            parts.append(f"Wayback Machine earliest archive: {osint['wayback']}")
        if parts:
            osint_note = "\n\nOSINT VERIFICATION:\n" + "\n".join(f"• {p}" for p in parts)
            # If media is confirmed genuine, explicitly instruct Claude to treat it as primary evidence
            if st in ("video", "image") and any("genuine" in p or "authentic" in p for p in parts):
                osint_note += (
                    "\n\nIMPORTANT: The submitted media has passed authenticity checks. "
                    "Treat it as primary evidence — what is shown/said in the video or image "
                    "is direct evidence of the claim, regardless of whether external fact-checkers "
                    "have indexed the event. Do not rate as UNVERIFIABLE solely because fact-checkers "
                    "haven't covered it yet if the media itself demonstrates the claim."
                )

    source_section = ""
    if source_content and st in ("url", "video"):
        label = "VIDEO CONTENT (transcript + frame analysis — treat as primary evidence)" if st == "video" else "SOURCE ARTICLE (extracted directly from the post/article — treat as primary evidence)"
        source_section = f"{label}:\n{source_content[:5000]}\n\n"

    evidence = (
        f"{source_section}"
        f"GOOGLE FACT CHECK:\n{g or 'No matches.'}\n\n"
        f"SOURCE EVIDENCE (grouped by perspective — note where perspectives diverge):\n"
        f"{grouped[:10000] or 'No results.'}"
        f"{osint_note}"
    )
    if source_section:
        evidence += (
            "\n\nIMPORTANT: The source content above was extracted directly from the submitted "
            "media. If it clearly confirms or contradicts the claim, use it as primary evidence "
            "and do not return UNVERIFIABLE solely because external fact-checkers haven't "
            "indexed it yet. For video authenticity claims, use the OSINT verification result."
        )

    # ── Step 1 & 2: Debate — pro and con in parallel (Haiku, fast + cheap) ──
    pro_text, con_text = "", ""
    if ANTHROPIC_KEY:
        pro_prompt = (
            "You are a fact-checker arguing from a WESTERN MAINSTREAM MEDIA perspective "
            "(BBC, Reuters, AP, CNN, NYT, official government and military statements). "
            "Using ONLY the evidence provided, make the strongest honest case that the claim "
            "below is TRUE or mostly accurate from this perspective. Be specific, cite sources. "
            "3-4 sentences.\n\n"
            f"CLAIM: {claim[:800]}\n\n{evidence}"
        )
        con_prompt = (
            "You are a fact-checker arguing from a REGIONAL / GLOBAL SOUTH / AFFECTED COMMUNITY "
            "perspective (Al Jazeera, Middle East Eye, regional outlets, independent journalists, "
            "people directly affected by the events, international law, human rights organisations). "
            "Using ONLY the evidence provided, make the strongest honest case that the claim "
            "below is FALSE, misleading, or missing crucial context from this perspective. "
            "IMPORTANT DISTINCTION: Only raise omitted context if it makes the claim actively "
            "misleading or creates a false impression — not if it merely adds further weight or "
            "significance to an already accurate claim. "
            "Be specific, cite sources. 3-4 sentences.\n\n"
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

    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    # Always tell Claude today's date — its knowledge cutoff is Aug 2025 so it cannot
    # independently know the year for events like "the 2026 Oscars".
    temporal_note = (
        f"\n\nTODAY'S DATE: {today_str}. "
        "Your knowledge cutoff is August 2025 — for any events after that date, "
        "rely entirely on the source evidence above, not your training data."
    )
    if post_date:
        age = _post_age_label(post_date)
        if age:
            friendly, days, age_str = age
            temporal_note += (
                f" This content was originally posted on {friendly} ({age_str}). "
                + ("Consider whether any claims may have been superseded, confirmed, or refuted since then. "
                   "Note if the claim was accurate at the time but circumstances have since changed."
                   if days > 30 else "This is recent content.")
            )
    synth_prompt = (
        f"Fact-check this claim (source: {st}).\n\n"
        f"CLAIM:\n\"\"\"{claim[:1200]}\"\"\"\n\n"
        f"{evidence}{debate_section}{temporal_note}\n\n"
        "INSTRUCTIONS:\n"
        "- Fill the 'perspectives' field with a single concise sentence (max 150 chars) summarising what was found across ALL regions: "
        "Western (BBC, Reuters, AP, CNN), Middle Eastern/Arabic (Al Jazeera, Middle East Eye, Press TV, Arab News, TRT World), "
        "African (Africanews, Africa Check), South Asian (Dawn, The Hindu), Latin American (Telesur, Chequeado, BBC Mundo). "
        "Name a region only if it has actual coverage from those sources. "
        "If sources disagree, state the disagreement in the same sentence (e.g. 'Western outlets confirm X; Arabic sources dispute the framing'). "
        "If nothing found anywhere, write 'No coverage found across all regions'.\n"
        "- If Social/Trending or Reddit evidence shows the claim is widely circulating or being debated online, note this in the verdict.\n"
        "- RATING RULE ON SOCIAL MEDIA EVIDENCE: Posts from Instagram, Reddit, Facebook, Twitter/X, TikTok, and other social platforms are evidence of how a claim is *circulating*, NOT evidence of its truth or falsehood. Never use the volume, tone, or framing of social media posts to downgrade or upgrade a rating. Only named news outlets, fact-checkers, academic sources, and official records count as evidentiary weight for the rating itself.\n"
        "- Fill 'contested_language' only if the claim or evidence uses terminology that is genuinely disputed across communities "
        "(e.g. how groups are labelled, how events are described). Leave empty array [] if language is uncontested.\n"
        "- Fill 'who_benefits': identify who gains from this claim being believed or spread — state actor, political party, media outlet, movement, or interest group. "
        "Be specific (e.g. 'Iranian state media — amplifies military deterrence narrative') not generic ('the government'). "
        "Leave empty string if the claim is mundane/benign with no clear political beneficiary.\n"
        "- If only Western sources were found, set confidence to LOW or MEDIUM and note the source gap in 'media_bias'.\n"
        "- Your verdict must reflect the actual state of evidence — including uncertainty and geopolitical dispute — "
        "not default to the Western official position.\n"
        "- UNVERIFIABLE means the claim cannot be assessed at all — e.g. no sources address it, or it is entirely unfalsifiable. "
        "It does NOT mean 'I have partial evidence'. If evidence directionally supports or contradicts the claim, commit to a "
        "rating (TRUE/MOSTLY TRUE/MOSTLY FALSE etc.) and use confidence level (LOW/MEDIUM/HIGH) to reflect certainty. "
        "Retreating to UNVERIFIABLE when two or more independent sources confirm the core claim is incorrect.\n"
        "- RATING RULE ON OMISSIONS: Ask: does the missing context strengthen, weaken, or neutrally add nuance to the claim? "
        "(1) If missing context STRENGTHENS the claim — do NOT downgrade. Rate the claim as stated. A factually correct but incomplete claim is TRUE. "
        "(2) If missing context WEAKENS or CONTRADICTS the claim — DO downgrade. A claim that omits facts which undermine it is MISLEADING or MOSTLY FALSE. "
        "(3) If missing context merely adds neutral background — do NOT downgrade. "
        "IMPORTANT: The CASE FOR FALSE/MISLEADING argument below is generated by a devil's advocate and will often cite "
        "missing context as a weakness. Apply the above test — only let it influence the rating if the missing context genuinely weakens the claim.\n"
        "- RATING RULE ON BREAKING NEWS: For very recent events (same-day or within 48 hours), full-text articles from "
        "any outlet may not yet be indexed. Do NOT downgrade confidence or rating because named Western outlets (Reuters, AP, "
        "BBC, CNN) have not yet published full-text coverage — their absence is a function of publication lag, not a reflection "
        "of the claim's accuracy. Similarly, absence of Western fact-checking organisation verdicts is expected for same-day "
        "stories and must not be used to downgrade. Live web search is a real-time aggregation of current news sources across "
        "the globe — treat it as sufficient primary corroboration for breaking news. "
        "Rate TRUE with MEDIUM confidence if only the live web search summary confirms with no named outlet articles visible. "
        "Rate TRUE with HIGH confidence if the live web search summary plus 2 or more named mainstream outlets in the evidence independently confirm the core claim without contradiction.\n"
        "- WESTERN SOURCE BIAS: Do not treat Reuters, AP, BBC, CNN, or Western fact-checkers as the gold standard for "
        "verification. Regional outlets, independent journalists, and non-Western sources carry equal evidentiary weight. "
        "Never cite absence of Western outlet coverage as a reason to downgrade a rating or confidence level.\n"
        "- RATING RULE ON SUPERLATIVES: For claims using 'first', 'largest', 'only' etc. — if the available sources "
        "directly state or confirm the superlative without contradiction, that is sufficient to verify it. Do not demand "
        "exhaustive historical comparison data that cannot reasonably exist for same-day breaking news.\n"
        "- RATING RULE ON CLAIM ORIGIN: A factually accurate claim is TRUE regardless of who shares it, what community "
        "circulates it, or what narrative it is used to support. Do NOT downgrade or switch to NEEDS CONTEXT because a "
        "claim is associated with conspiracy theory communities, fringe movements, or contested political narratives. "
        "Assess the factual accuracy of the claim as stated — nothing else.\n"
        "- RATING RULE ON NUMERICAL APPROXIMATIONS: Minor imprecision in numbers, dates, or timeframes MUST NOT cause any "
        "downgrade at all — not even from TRUE to MOSTLY TRUE. Ask: does the numerical difference change the substance or "
        "meaning of the claim? If no, rate TRUE. Example: 'six weeks' when the actual figure is 7 weeks does not change "
        "the substance that the event happened shortly before 9/11 — rate TRUE. '$3 billion' vs '$3.2 billion' does not "
        "change the substance of a large real-estate deal — rate TRUE. Only downgrade for numerical errors when the "
        "inaccuracy materially changes the meaning (e.g. 'days' vs 'months', or an order-of-magnitude difference). "
        "When a minor approximation exists, briefly note it in the verdict text and explain why it is immaterial to the "
        "central claim — e.g. 'The claim says six weeks; the actual interval was approximately seven weeks — a minor "
        "approximation that does not affect the substance of the claim.'\n"
        "- RATING RULE ON POLITICAL FRAMING LABELS: Never use labels such as 'antisemitic framing', 'far-right narrative', "
        "'conspiracy theory framing', 'Islamist framing', or similar social/political categorisations as a reason to "
        "downgrade a verdict or switch to NEEDS CONTEXT. These are editorial judgements, not factual assessments. "
        "If a claim is factually true, rate it TRUE. If the claim itself contains a factually false assertion, rate it "
        "on those factual merits — not because of its social associations.\n"
        "- RATING RULE ON SOURCE FRAMING: Fact-check the CLAIM AS STATED above — not the source content's broader "
        "narrative, implied conclusion, or framing. The claim has already been extracted; your job is to verify it as "
        "written. If the extracted claim is factually accurate, rate it TRUE even if the source material draws "
        "additional inferences beyond the claim that are not supported. Those additional inferences are outside the "
        "scope of this verdict. Example: if the claim is 'first responders reported secondary explosions' and the "
        "source video implies this means planted bombs, rate the claim on whether first responders reported secondary "
        "explosions — not on whether the 'planted bombs' conclusion is valid. Never downgrade a true claim because "
        "the source it came from makes a further unjustified leap.\n\n"
        f"Respond ONLY with valid JSON:\n{ANALYSE_JSON_SCHEMA}"
    )

    if ANTHROPIC_KEY:
        for attempt in range(2):
            try:
                r = requests.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-sonnet-4-6", "max_tokens": 2000, "temperature": 0,
                          "system": SYSTEM, "messages": [{"role": "user", "content": synth_prompt}]},
                    timeout=55)
                if _is_credit_error(r.status_code, r.text):
                    send_admin_alert("anthropic", f"Anthropic API credits exhausted (HTTP {r.status_code}). Falling back to OpenAI for synthesis.")
                    log.error(f"Anthropic credit error in synthesis: {r.status_code}")
                    break  # skip retry, go straight to OpenAI fallback
                r.raise_for_status()
                resp = r.json()
                usage = resp.get("usage", {})
                _cost_add(_anthropic_cost_cents("claude-sonnet-4-6", usage.get("input_tokens", 0), usage.get("output_tokens", 0)))
                result = _parse_json_result(resp["content"][0]["text"])
                if result:
                    if pro_text: result["_debate_pro"] = pro_text
                    if con_text: result["_debate_con"] = con_text
                    log.info("Verdict: %s | Confidence: %s", result.get("rating"), result.get("confidence"))
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
                resp = r.json()
                usage = resp.get("usage", {})
                _cost_add(_openai_cost_cents("gpt-4o", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))
                result = _parse_json_result(resp["choices"][0]["message"]["content"])
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

def _trunc(text, limit):
    """Truncate at sentence boundary where possible; fall back to word boundary. No ellipsis if clean cut."""
    if not text or len(text) <= limit:
        return text
    chunk = text[:limit]
    # Try to end at a sentence boundary (. ! ?)
    for sep in ('. ', '! ', '? '):
        idx = chunk.rfind(sep)
        if idx > limit // 2:  # only use if we keep at least half the content
            return chunk[:idx + 1]
    # Fall back to word boundary with ellipsis
    cut = chunk.rsplit(' ', 1)[0].rstrip('.,;:—- ')
    return cut + '…'

def fmt_report(claim, a, st, cost, used_sources=None, ad=None, post_date=None, osint=None, wa_cost=0.0):
    rating = a.get("rating", "UNVERIFIABLE").upper()
    src_word = {"text":"Text","image":"Image","audio":"Voice","video":"Video","url":"Article","document":"Document"}
    badge_map = {"TRUE":"✅  VERDICT: TRUE","MOSTLY TRUE":"🟢  VERDICT: MOSTLY TRUE","HALF TRUE":"🟡  VERDICT: HALF TRUE","MOSTLY FALSE":"🟠  VERDICT: MOSTLY FALSE","FALSE":"❌  VERDICT: FALSE","PANTS ON FIRE":"🔥  VERDICT: PANTS ON FIRE","UNVERIFIABLE":"❓  VERDICT: UNVERIFIABLE","MISLEADING":"⚠️  VERDICT: MISLEADING","NEEDS CONTEXT":"📌  VERDICT: NEEDS CONTEXT"}
    badge = badge_map.get(rating, f"VERDICT: {rating}")
    hdr_beta = " _(Beta)_" if BETA_MODE else ""
    lines = [f"*Fred Check*{hdr_beta}  |  {src_word.get(st,'Text')}","",f"*CLAIM*",f"_{claim}_","",f"*{badge}*","",meter_visual(rating),""]
    if rating not in ("TRUE", "FALSE") and a.get("rating_reason"):
        lines += [f"_Why {rating.title()}? {a['rating_reason']}_", ""]
    lines += ["*ANALYSIS*",_trunc(a.get("verdict",""), 500),""]
    if a.get("key_facts"): lines += ["*KEY FACTS*"] + [f"{i}. {_trunc(f,180)}" for i,f in enumerate(a["key_facts"][:3],1)] + [""]
    # Perspectives — single summary sentence across all regions
    persp = a.get("perspectives", "")
    if isinstance(persp, dict):
        # backwards compat: flatten old nested format
        parts = []
        if persp.get("western_mainstream") and persp["western_mainstream"] != "No coverage found":
            parts.append(persp["western_mainstream"])
        if persp.get("regional_independent") and persp["regional_independent"] != "No coverage found":
            parts.append(persp["regional_independent"])
        if persp.get("latin_american") and persp["latin_american"] != "No coverage found":
            parts.append(persp["latin_american"])
        if persp.get("consensus"):
            parts.append(persp["consensus"])
        persp = " ".join(parts) if parts else ""
    if persp and str(persp).strip():
        lines += ["*PERSPECTIVES*", _trunc(str(persp), 200), ""]
    # Contested language
    cl = a.get("contested_language", [])
    if cl and isinstance(cl, list):
        lines += ["*CONTESTED LANGUAGE*"] + [f"• {_trunc(t,160)}" for t in cl[:2]] + [""]
    if a.get("context"): lines += ["*BACKGROUND*", _trunc(a["context"], 300), ""]
    if a.get("red_flags"): lines += ["*RED FLAGS*"] + [f"• {_trunc(f,180)}" for f in a["red_flags"][:2]] + [""]
    if a.get("who_benefits"): lines += ["*WHO BENEFITS?*", f"_{_trunc(a['who_benefits'],200)}_", ""]
    if a.get("media_bias"): lines += ["*BIAS NOTE*", _trunc(a["media_bias"],180), ""]
    # Derive truth score from rating — deterministic, not Claude's lenz_score.
    _rating_score = {
        "TRUE": 10, "MOSTLY TRUE": 8, "HALF TRUE": 5,
        "MOSTLY FALSE": 3, "FALSE": 1, "PANTS ON FIRE": 0,
    }
    if rating in _rating_score:
        s = _rating_score[rating]
        filled = "█" * s + "░" * (10 - s)
        lines += [f"*TRUTH SCORE*  `{filled}` {s}/10", ""]
    elif rating in ("UNVERIFIABLE", "MISLEADING", "NEEDS CONTEXT"):
        lines += [f"*TRUTH SCORE*  `░░░░░░░░░░` ?/10  _(insufficient evidence to score)_", ""]
    conf = a.get("confidence","LOW")
    conf_icon = {"HIGH":"🟢","MEDIUM":"🟡","LOW":"🔴"}.get(conf,"")
    lines += [f"*CONFIDENCE*  {conf_icon} {conf}", f"_{_trunc(a.get('confidence_reason',''), 200)}_",""]
    # Show what Claude actually cited — these vary per claim based on evidence found.
    # Fall back to scraped source names only if Claude returned no citations.
    if a.get("sources"):
        src_count = f" _(searched {len(used_sources)})_" if used_sources else ""
        lines += [f"*SOURCES CITED*{src_count}"] + [f"• {s}" for s in a["sources"][:6]] + [""]
    elif used_sources:
        lines += ["*SOURCES SEARCHED*"] + [f"• {s}" for s in used_sources[:6]] + [""]
    osint_lines = fmt_osint(osint or {})
    if osint_lines:
        lines += osint_lines
    if post_date:
        age = _post_age_label(post_date)
        if age:
            friendly, days, age_str = age
            lines += [f"📅 *Posted: {friendly}*" + (f" _{age_str}_" if age_str else "")]
            if days > 180:
                lines += ["⚠️ _Older content — verify claims are still current_"]
            lines += [""]
    version = "Fred Check *(Beta)*" if BETA_MODE else "Fred Check"
    cost_str = f"Cost: ${cost + wa_cost:.4f}"
    footer = ["──────────────────────", f"{cost_str}  •  {version}"]
    if a.get("_debate_pro"):
        footer.append("⚖️ pro/con debate")
    footer.append(f"🌐 {WEBSITE_URL}")
    lines += footer
    if ad:
        lines += ["", f"💡 *Sponsored:* {ad}"]
    return "\n".join(lines)

def _welcome_msg():
    beta_suffix = " _(BETA)_" if BETA_MODE else ""
    beta_line = (
        "\n_🚧 Fred Check BETA — feedback welcome - "
        "WhatsApp +34643994740 or email hello@fredcheck.com. "
        "Reply HELP for more info._"
    ) if BETA_MODE else ""
    return (
        f"*Welcome to Fred • Fact Check{beta_suffix}* 👋\n\n"
        "I'm FRED, I fact check claims across 65+ sources from 6 world regions — "
        "with no default Western narrative.\n\n"
        "*Send me any of these:*\n"
        "• A text claim, headline or quote\n"
        "• A URL (news article, Facebook, Instagram, TikTok, YouTube)\n"
        "• An image, video or voice note\n\n"
        "Type *HELP* anytime for a full guide.\n"
        f"🌐 {WEBSITE_URL}"
        + beta_line
    )

HELP_MSG = (
    "*Fred — Help* 🌍\n\n"
    "*What I can check:*\n"
    "• URLs (Facebook, Instagram, TikTok, YouTube, news articles)\n"
    "• Videos and images\n"
    "• Voice notes / audio\n"
    "• Text claims or quotes\n\n"
    "*Commands:*\n"
    "• *Y* — confirm a fact-check\n"
    "• *N* — cancel\n"
    "• *HELP* — show this message\n\n"
    "*About Fred:*\n"
    "Balanced, bias-aware fact-checking using Western, Middle Eastern, Arabic, and independent sources. "
    "Identifies contested language, geopolitical framing, and who benefits from a claim.\n\n"
    f"🌐 {WEBSITE_URL}\n\n"
    "_Truth Beyond Borders — built for journalists, activists & curious minds._"
)

def confirm_msg(st, preview, cost):
    src = {"text":"Text","image":"Image","audio":"Voice Note","video":"Video","url":"Article","document":"Document"}
    HDR = "*━━━━━━━━━━━━━━*"
    return (f"{HDR}\n*FACTCHECK PRO*\n_{src.get(st,st)}_\n{HDR}\n\n*CLAIM PREVIEW*\n_{preview[:180]}_\n\n_Est. cost: ${cost:.4f}_\n\nReply *Y* to fact-check\nReply *N* to cancel")

def _split_message(text, limit=4000):
    """Split text at newline boundaries near limit to avoid mid-sentence cuts."""
    chunks = []
    while len(text) > limit:
        pos = text.rfind("\n", 0, limit)
        if pos <= 0:
            pos = limit
        chunks.append(text[:pos])
        text = text[pos:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks

def send(to, text):
    # QC test interception — capture messages instead of hitting Meta API
    if to.startswith("qctest_"):
        with _qc_lock:
            if to in _qc_jobs:
                _qc_jobs[to]["messages"].append(text)
        return
    # Sanitize: remove null bytes and non-BMP unicode that WhatsApp rejects
    text = text.replace("\x00", "").encode("utf-16", "surrogatepass").decode("utf-16")
    for chunk in _split_message(text):
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

_VERDICT_REACTION = {
    "TRUE":           "✅",
    "MOSTLY TRUE":    "👍",
    "HALF TRUE":      "🤔",
    "NEEDS CONTEXT":  "📌",
    "MOSTLY FALSE":   "👎",
    "MISLEADING":     "⚠️",
    "FALSE":          "❌",
    "PANTS ON FIRE":  "🔥",
    "UNVERIFIABLE":   "❓",
}
_VERDICT_PRIORITY = ["PANTS ON FIRE","FALSE","MISLEADING","MOSTLY FALSE",
                     "HALF TRUE","NEEDS CONTEXT","UNVERIFIABLE","MOSTLY TRUE","TRUE"]

def send_reaction(to, message_id, emoji):
    """React to a WhatsApp message with an emoji."""
    if not message_id:
        return
    try:
        r = requests.post(WHATSAPP_URL,
            json={"messaging_product":"whatsapp","to":to,"type":"reaction",
                  "reaction":{"message_id":message_id,"emoji":emoji}},
            headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}","Content-Type":"application/json"},
            timeout=10)
        if r.ok:
            log.info(f"Reaction {emoji} sent to msg {message_id[:20]}")
        else:
            log.warning(f"Reaction failed {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.error("send_reaction: %s", e)

def send_messenger(recipient_id, text):
    """Send a text message via Facebook Messenger/Instagram API."""
    if not MESSENGER_PAGE_TOKEN:
        log.warning("MESSENGER_PAGE_TOKEN not set")
        return
    for chunk in [text[i:i+2000] for i in range(0, len(text), 2000)]:
        try:
            r = requests.post(
                "https://graph.facebook.com/v19.0/me/messages",
                params={"access_token": MESSENGER_PAGE_TOKEN},
                json={"recipient": {"id": recipient_id}, "message": {"text": chunk}},
                timeout=10
            )
            if not r.ok:
                log.error("Messenger send failed %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            log.error("Messenger send error: %s", e)

def send_telegram(chat_id, text):
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set")
        return
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
                timeout=10
            )
            if not r.ok:
                # Retry without markdown if formatting caused the error
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk},
                    timeout=10
                )
        except Exception as e:
            log.error("Telegram send error: %s", e)

def _telegram_download(file_id):
    """Download a file from Telegram by file_id. Returns bytes or None."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=10
        )
        r.raise_for_status()
        file_path = r.json()["result"]["file_path"]
        r2 = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
            timeout=30
        )
        r2.raise_for_status()
        return r2.content
    except Exception as e:
        log.error("Telegram download failed: %s", e)
        return None

def _twitter_oauth1_header(method, url, params=None):
    """Build OAuth 1.0a Authorization header for Twitter API requests."""
    import urllib.parse, base64
    oauth_params = {
        "oauth_consumer_key": TWITTER_CONSUMER_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(int(t.time())),
        "oauth_token": TWITTER_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    all_params = dict(oauth_params)
    if params:
        all_params.update(params)
    sorted_params = "&".join(
        f"{urllib.parse.quote(str(k), safe='')}"
        f"={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(all_params.items())
    )
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(sorted_params, safe=""),
    ])
    signing_key = "&".join([
        urllib.parse.quote(TWITTER_CONSUMER_SECRET, safe=""),
        urllib.parse.quote(TWITTER_ACCESS_SECRET, safe=""),
    ])
    sig = hmac.new(signing_key.encode(), base_string.encode(), "sha256").digest()
    oauth_params["oauth_signature"] = base64.b64encode(sig).decode()
    header_value = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(str(v), safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    return header_value


def send_twitter_dm(recipient_id, text):
    """Send a Direct Message via Twitter/X API v2."""
    if not all([TWITTER_CONSUMER_KEY, TWITTER_CONSUMER_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        log.warning("Twitter credentials not set")
        return
    url = f"https://api.twitter.com/2/dm_conversations/with/{recipient_id}/messages"
    # Twitter DMs max 10000 chars; split if needed
    for chunk in [text[i:i+10000] for i in range(0, len(text), 10000)]:
        try:
            auth_header = _twitter_oauth1_header("POST", url)
            r = requests.post(
                url,
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                json={"text": chunk},
                timeout=15
            )
            if not r.ok:
                log.error("Twitter DM send failed %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            log.error("Twitter DM send error: %s", e)


def run_check(from_num, query, st, img_bytes, cost, video_bytes=None, billing_type="free", pre_claims=None, post_date=None, source_url="", msg_id=""):
    _cost_reset()  # reset per-request cost accumulator
    show_ad = (billing_type == "free" and bool(SPONSOR_ADS))
    topic_text = query + (" " + " ".join(pre_claims) if pre_claims else "")
    total_src, src_preview = _source_preview_msg(topic_text)
    # ── OSINT checks — run in background thread while sources scrape ────────
    osint_future = None
    needs_osint = img_bytes or source_url
    if needs_osint:
        send(from_num, "🔬 Running OSINT verification...")
    send(from_num, f"⚙️ Cross-referencing {total_src} sources:\n{src_preview}...")
    if needs_osint:
        _osint_ex = ThreadPoolExecutor(max_workers=1)
        osint_future = _osint_ex.submit(run_osint,
            image_bytes=img_bytes,
            source_url=source_url or None,
            og_image_url=None
        )

    # For video content, extract frames before fact-checking
    if st == "video" and video_bytes:
        try:
            send(from_num, "🎞️ Analysing video frames...")
            frames, duration = extract_video_frames(video_bytes, num_frames=5)
            if frames:
                visual_analysis = analyze_video_frames(frames)
                if visual_analysis:
                    query = f"{query}\n\nVISUAL ANALYSIS:\n{visual_analysis}"
                    log.info(f"Visual analysis added: {len(visual_analysis)} chars")
        except Exception as e:
            log.error(f"Frame analysis failed: {e}")

    # ── Claim extraction (use pre-extracted claims if available, avoids double Claude call) ──
    if pre_claims:
        claims = pre_claims
        log.info(f"Using {len(claims)} pre-extracted claim(s)")
    elif st in ("text", "audio", "url"):
        neutral = neutralize_claim(query)
        if neutral != query:
            log.info(f"Neutralized: {neutral[:80]}")
        query = neutral
        claims = extract_claims(query)
    elif st == "video":
        claims = extract_claims(query)
    else:
        claims = [query]

    # ── Collect OSINT results (non-blocking — were running in background) ──
    osint = {}
    if osint_future:
        try:
            osint = osint_future.result(timeout=25) or {}
            _osint_ex.shutdown(wait=False)
        except Exception as e:
            log.warning(f"OSINT collect: {e}")

    # ── Multi-claim header (skip — already shown before Y confirmation) ───
    multi = len(claims) > 1

    # ── Analyse each claim with its own search ────────────────────────────
    all_used_combined = []
    all_ratings = []
    for i, claim in enumerate(claims):
        g = google_fc(claim)
        sc, used_sources = scrape_sites(claim, post_date=post_date)
        gfc_sources = [x["source"] for x in g if x.get("source")]
        all_used = list(dict.fromkeys(gfc_sources + used_sources))
        all_used_combined = list(dict.fromkeys(all_used_combined + all_used))
        a = claude_analyse(claim, g, sc, st, post_date=post_date, osint=osint,
                           source_content=query if st in ("url", "video") else None)
        all_ratings.append(a.get("rating", "UNVERIFIABLE"))
        ad = get_random_ad() if show_ad else None
        report = fmt_report(claim, a, st, cost, all_used, ad=ad, post_date=post_date, osint=osint, wa_cost=WA_CONVERSATION_COST)
        _log_request("whatsapp", from_num, st, query, claim, a, report, cost)
        log.info("VERDICT SENT to %s:\n%s", from_num, report)
        if multi:
            send(from_num, f"*— CLAIM {i+1}/{len(claims)} —*")
        send(from_num, report)

    # ── React to original message with most significant verdict emoji ──────
    if msg_id and all_ratings:
        top = min(all_ratings, key=lambda r: _VERDICT_PRIORITY.index(r) if r in _VERDICT_PRIORITY else 99)
        emoji = _VERDICT_REACTION.get(top, "❓")
        send_reaction(from_num, msg_id, emoji)

    # ── Billing: record cost and deduct balance ────────────────────────────
    actual_cents = max(1, _cost_get())
    _wa_deduct(from_num, actual_cents, f"{st} fact-check", billing_type)
    log.info("Billing %s: type=%s cost=%d¢", from_num, billing_type, actual_cents)

def run_check_platform(platform, uid, query, st, billing_type, send_fn, pre_claims=None, post_date=None):
    """Platform-agnostic fact-check runner. Used by Messenger/Instagram/Telegram."""
    _cost_reset()
    show_ad = (billing_type == "free" and bool(SPONSOR_ADS))
    topic_text = query + (" " + " ".join(pre_claims) if pre_claims else "")
    total_src, src_preview = _source_preview_msg(topic_text)
    send_fn(f"⚙️ Cross-referencing {total_src} sources:\n{src_preview}...")

    if pre_claims:  # platform version — no OSINT line needed here
        claims = pre_claims
        log.info(f"Using {len(claims)} pre-extracted claim(s)")
    elif st in ("text", "audio", "url"):
        neutral = neutralize_claim(query)
        if neutral != query:
            log.info("Neutralized: %s", neutral[:80])
        query = neutral
        claims = extract_claims(query)
    elif st == "video":
        claims = extract_claims(query)
    else:
        claims = [query]

    multi = len(claims) > 1

    cost_est = estimate_cost(st)
    for i, claim in enumerate(claims):
        g = google_fc(claim)
        sc, used_sources = scrape_sites(claim, post_date=post_date)
        gfc_sources = [x["source"] for x in g if x.get("source")]
        all_used = list(dict.fromkeys(gfc_sources + used_sources))
        a = claude_analyse(claim, g, sc, st, post_date=post_date,
                           source_content=query if st in ("url", "video") else None)
        ad = get_random_ad() if show_ad else None
        report = fmt_report(claim, a, st, cost_est, all_used, ad=ad, post_date=post_date)
        _log_request(platform, uid, st, query, claim, a, report, cost_est)
        log.info("VERDICT SENT to %s/%s:\n%s", platform, uid, report)
        if multi:
            send_fn(f"*— CLAIM {i+1}/{len(claims)} —*\n" + report)
        else:
            send_fn(report)

    actual_cents = max(1, _cost_get())
    _pdeduct(platform, uid, actual_cents, f"{st} fact-check", billing_type)

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

# Shared pending state uses (platform, uid) as key
# WhatsApp process() uses ("whatsapp", from_num) — updated below

def _handle_platform_message(platform, uid, msg_type, text_body, send_fn,
                              image_bytes=None, audio_bytes=None, audio_mime=None,
                              msg_id=None, msg_time=None):
    """
    Platform-agnostic message handler for Messenger, Instagram, Telegram.
    Handles Y/N confirm flow, media processing, billing gate, and dispatching run_check_platform.
    """
    pkey = (platform, str(uid))
    # Dedup by message ID
    if msg_id:
        dedup_key = f"{platform}:{msg_id}"
        with processed_lock:
            if dedup_key in processed_ids: return
            if msg_time and t.time() - msg_time > 300:
                log.info("Stale %s message (>5 min), ignored", platform)
                return
            processed_ids.add(dedup_key)
            if len(processed_ids) > MAX_PROCESSED_IDS:
                to_keep = set(list(processed_ids)[MAX_PROCESSED_IDS//2:])
                processed_ids.clear(); processed_ids.update(to_keep)

    expire_pending()
    body_upper = (text_body or "").strip().upper()
    is_cancel = body_upper in ("NO", "N")
    is_check_all = body_upper in ("YES", "Y", "ALL", "A")
    is_selection = bool(re.match(r'^[\d][,\s\d]*$', body_upper.strip()))
    is_pending_response = is_cancel or is_check_all or is_selection

    with pending_lock:
        has_p = pkey in pending
        data = pending.get(pkey)

    if has_p and is_pending_response:
        if is_cancel:
            with pending_lock: pending.pop(pkey, None)
            send_fn("Cancelled.")
            return
        with pending_lock: data = pending.pop(pkey)
        # Filter to selected claims
        all_claims = data.get("claims") or []
        if is_check_all or not all_claims:
            selected_claims = all_claims or None
        else:
            nums = [int(x) for x in re.split(r'[,\s]+', body_upper.strip()) if x.isdigit()]
            selected_claims = [all_claims[n-1] for n in nums if 1 <= n <= len(all_claims)]
            if not selected_claims:
                selected_claims = all_claims
        bt = _pbilling_type(platform, uid)
        # Free users: restrict to single claim
        if bt == "free" and selected_claims and len(selected_claims) > 1:
            send_fn("_Free plan — checking first selected claim only. Upgrade for multi-claim checks._")
            selected_claims = selected_claims[:1]
        if bt == "blocked":
            u = _puser(platform, uid)
            _psend_payment_prompt(platform, uid, u["balance_cents"], send_fn)
            return
        if bt == "free":
            u = _puser(platform, uid)
            remaining = FREE_CHECKS_LIMIT - (u.get("free_checks_used") or 0) - 1
            send_fn(f"✓ Free check — {remaining} free check{'s' if remaining != 1 else ''} remaining")
        elif bt == "paid":
            u = _puser(platform, uid)
            send_fn(f"✓ Balance: ${u['balance_cents']/100:.2f}")
        elif bt == "subscriber":
            send_fn("✓ Subscriber — unlimited access")
        send_fn("Starting fact-check...")
        threading.Thread(
            target=run_check_platform,
            args=(platform, uid, data["query"], data["source_type"], bt, send_fn),
            kwargs={"pre_claims": selected_claims or data.get("claims")},
            daemon=True
        ).start()
        return
    elif has_p and not is_yn:
        with pending_lock: pending.pop(pkey, None)

    # ── Process content ────────────────────────────────────────────────────
    query, source_type = "", "text"

    if msg_type == "text":
        body = (text_body or "").strip()
        urls = [w for w in body.split() if w.startswith("http")]
        if urls:
            url = urls[0]
            send_fn("🔍 Analysing post/article...")
            page_text = fetch(url) or _og_metadata(url)
            query = page_text or body
            source_type = "url"
        else:
            query, source_type = body, "text"
            # Enrich short text with Tavily context so claim extraction has background
            if TAVILY_API_KEY and len(body) < 400:
                ctx = tavily_search(body, max_results=3)
                if ctx:
                    ctx_text = "\n".join(f"{n}: {s}" for n, s in ctx[:3])
                    query = f"{body}\n\nBACKGROUND CONTEXT (real-time web):\n{ctx_text}"

    elif msg_type == "image":
        send_fn("🖼 Analysing image...")
        if image_bytes:
            query = clean_query(ocr_image(image_bytes))
        source_type = "image"
        if not query:
            send_fn("⚠️ Could not analyse image.")
            return

    elif msg_type == "audio":
        send_fn("🎤 Transcribing...")
        if audio_bytes:
            query = transcribe(audio_bytes, audio_mime or "audio/ogg")
        source_type = "audio"
        if not query:
            send_fn("⚠️ Could not transcribe audio.")
            return

    if not query:
        send_fn("⚠️ Could not extract content. Please send text or a URL.")
        return

    query = query.strip()[:6000]
    log.info("[%s/%s] Received [%s]: %s", platform, uid, source_type, query[:100])
    cost = estimate_cost(source_type)

    # ── Extract claims before confirmation — show user what will be checked ──
    if source_type in ("text", "image", "audio", "url", "video"):
        send_fn("🔍 Identifying claims...")
        assessment = assess_content_claims(query, source_type, post_date=post_date)
        if not assessment["checkable"] or not assessment["claims"]:
            msg = no_claims_msg(assessment["reason"], source_type, assessment["suggestions"])
            if image_bytes and HIVE_API_KEY:
                hive = hive_ai_check(image_bytes=image_bytes)
                ai_score = hive.get("ai_generated", 0)
                df_score = hive.get("deepfake", 0)
                generator = hive.get("generator", "")
                log.info(f"No-claims Hive check: ai={ai_score:.2f} deepfake={df_score:.2f}")
                if ai_score > 0.5 or df_score > 0.5:
                    ai_line = ""
                    if ai_score > 0.5:
                        gen_label = f" _(likely {generator})_" if generator else ""
                        ai_line = f"🤖 *AI-generated image detected: {int(ai_score*100)}%*{gen_label}"
                    if df_score > 0.5:
                        ai_line += f"\n🎭 *Deepfake detected: {int(df_score*100)}%*"
                    msg = f"⚠️ *AI-Generated Content Detected*\n\n{ai_line}\n\n" + msg
            send_fn(msg)
            return
        claims = assessment["claims"]
        # Dev bypass: skip confirmation, auto-select all claims
        if DEV_AUTOSELECT_ON and DEV_AUTOSELECT_NUM and str(uid) == DEV_AUTOSELECT_NUM:
            send_fn(f"🚀 _Dev: auto-selecting all {len(claims)} claim(s)_")
            bt = _pbilling_type(platform, uid)
            threading.Thread(target=run_check_platform,
                             args=(platform, uid, query, source_type, bt, send_fn),
                             kwargs={"pre_claims": claims}, daemon=True).start()
            return
        bt_now = _pbilling_type(platform, uid)
        with pending_lock:
            pending[pkey] = {"query": query, "source_type": source_type, "image_bytes": image_bytes,
                             "cost": cost, "timestamp": t.time(), "claims": claims}
        send_fn(claims_confirm_msg(claims, source_type, cost, is_free=(bt_now == "free")))
    else:
        with pending_lock:
            pending[pkey] = {"query": query, "source_type": source_type,
                             "image_bytes": image_bytes, "cost": cost, "timestamp": t.time()}
        send_fn(confirm_msg(source_type, query, cost))

def _send_daily_summary(date_str=None):
    """
    Send a daily usage summary to hello@fredcheck.com.
    date_str: 'YYYY-MM-DD' of the day to report. Defaults to yesterday (UTC).
    """
    import datetime as _dt, urllib.request as _ur, json as _json
    sg_key = os.environ.get("SENDGRID_API_KEY")
    if not sg_key:
        log.warning("Daily summary: no SENDGRID_API_KEY, skipping")
        return
    if not date_str:
        date_str = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    # Build epoch range for the target date (UTC)
    day_start = int(_dt.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=_dt.timezone.utc).timestamp())
    day_end = day_start + 86400

    try:
        with _db() as c:
            # Per-user breakdown
            rows = c.execute("""
                SELECT r.uid, r.source_type, r.extracted_claim, r.rating, r.cost_usd,
                       p.profile_name
                FROM request_log r
                LEFT JOIN platform_users p ON p.platform='whatsapp' AND p.platform_id=r.uid
                WHERE r.platform='whatsapp'
                  AND r.created_at >= ? AND r.created_at < ?
                ORDER BY r.uid, r.created_at
            """, (day_start, day_end)).fetchall()

            # New users that day
            new_users = c.execute("""
                SELECT platform_id, profile_name FROM platform_users
                WHERE platform='whatsapp'
                  AND created_at >= ? AND created_at < ?
            """, (day_start, day_end)).fetchall()
    except Exception as e:
        log.error("Daily summary DB error: %s", e)
        return

    total_checks = len(rows)
    total_cost = sum(r["cost_usd"] or 0 for r in rows)

    # Group by user
    from collections import defaultdict as _dd
    by_user = _dd(list)
    for r in rows:
        by_user[r["uid"]].append(r)

    lines = [
        f"Fred Check — Daily Usage Summary",
        f"Date: {date_str} (UTC)",
        f"",
        f"Total checks: {total_checks}",
        f"Total cost:   ${total_cost:.4f}",
        f"Active users: {len(by_user)}",
        f"New users:    {len(new_users)}",
        f"",
    ]

    if new_users:
        lines.append("── New Users ──")
        for u in new_users:
            name = u["profile_name"] or "(no name)"
            lines.append(f"  +{u['platform_id']}  {name}")
        lines.append("")

    if by_user:
        lines.append("── Usage by User ──")
        for uid, checks in sorted(by_user.items(), key=lambda x: -len(x[1])):
            name = checks[0]["profile_name"] or "(no name)"
            user_cost = sum(r["cost_usd"] or 0 for r in checks)
            lines.append(f"\n+{uid}  {name}  ({len(checks)} check{'s' if len(checks)!=1 else ''}, ${user_cost:.4f})")
            for r in checks:
                claim = (r["extracted_claim"] or "")[:80]
                rating = r["rating"] or "—"
                src = r["source_type"] or "text"
                lines.append(f"  [{src}] {rating}: {claim}")
    else:
        lines.append("No checks recorded for this date.")

    lines += ["", "Fred Check"]
    body = "\n".join(lines)

    try:
        payload = _json.dumps({
            "personalizations": [{"to": [{"email": "hello@fredcheck.com"}]}],
            "from": {"email": "hello@fredcheck.com", "name": "Fred Check"},
            "subject": f"📊 Fred daily summary — {date_str} ({total_checks} checks, {len(by_user)} users)",
            "content": [{"type": "text/plain", "value": body}]
        }).encode()
        req = _ur.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization": f"Bearer {sg_key}", "Content-Type": "application/json"},
            method="POST"
        )
        _ur.urlopen(req, timeout=15)
        log.info("Daily summary sent for %s (%d checks)", date_str, total_checks)
    except Exception as e:
        log.error("Daily summary email failed: %s", e)

# Add daily summary to scheduler now that _send_daily_summary is defined
# misfire_grace_time=None means missed fires (e.g. on restart) are skipped — fires once at 07:00 UTC only
_scheduler.add_job(_send_daily_summary, "cron", hour=7, minute=0, id="daily_summary",
                   misfire_grace_time=None)
log.info("Daily summary scheduled: 07:00 UTC")


def _notify_new_user(wa_id, profile_name):
    """Email hello@fredcheck.com when a new WhatsApp user is detected."""
    try:
        import urllib.request as _ur, json as _json, datetime as _dt
        sg_key = os.environ.get("SENDGRID_API_KEY")
        if not sg_key:
            return
        joined = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        name_line = f"Name:   {profile_name}" if profile_name else "Name:   (not available)"
        body = (
            f"New Fred user joined during beta.\n\n"
            f"Number: +{wa_id}\n"
            f"{name_line}\n"
            f"Joined: {joined}\n\n"
            f"Fred Check"
        )
        payload = _json.dumps({
            "personalizations": [{"to": [{"email": "hello@fredcheck.com"}]}],
            "from": {"email": "hello@fredcheck.com", "name": "Fred Check"},
            "subject": f"🆕 New beta user: {profile_name or wa_id}",
            "content": [{"type": "text/plain", "value": body}]
        }).encode()
        req = _ur.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization": f"Bearer {sg_key}", "Content-Type": "application/json"},
            method="POST"
        )
        _ur.urlopen(req, timeout=10)
        log.info(f"New user notification sent for {wa_id} ({profile_name})")
    except Exception as e:
        log.error(f"New user notification failed: {e}")


def process(from_num, message, profile_name=None):
    msg_id = message.get("id",""); msg_time = int(message.get("timestamp",0))
    with processed_lock:
        if msg_id in processed_ids: return
        if t.time() - msg_time > 300: log.info("Stale message (>5 min), ignored"); return
        processed_ids.add(msg_id)
        if len(processed_ids) > MAX_PROCESSED_IDS:
            to_keep = set(list(processed_ids)[MAX_PROCESSED_IDS//2:])
            processed_ids.clear(); processed_ids.update(to_keep)
    expire_pending()
    # ── First-time user: send welcome and let them proceed ────────────────────
    is_new = _is_new_wa_user(from_num)
    if is_new:
        send(from_num, _welcome_msg())
        threading.Thread(target=_notify_new_user, args=(from_num, profile_name), daemon=True).start()
        # Fall through so they can also process content if sent with first message
    pkey = ("whatsapp", from_num)
    msg_type = message.get("type")
    # ── Early billing gate — block before any processing ─────────────────────
    # Skip for: new users (just joined, have free checks), text commands/responses
    _is_text = msg_type == "text"
    _body_upper = message.get("text", {}).get("body", "").strip().upper() if _is_text else ""
    _is_command = _body_upper in ("HELP", "?", "START", "INFO", "NO", "N", "YES", "Y", "ALL", "A") or bool(re.match(r'^[\d][,\s\d]*$', _body_upper))
    if not is_new and not _is_command:
        _bt_early = _wa_billing_type(from_num)
        if _bt_early == "blocked":
            u = _wa_user(from_num)
            _send_payment_prompt(from_num, u["balance_cents"])
            return
    if msg_type == "video":
        send(from_num, "📹 Video detected!")
        log.info("=== VIDEO MESSAGE RECEIVED ===")
    if msg_type == "text":
        body = message["text"]["body"].strip(); body_upper = body.upper()
        # ── HELP command ──────────────────────────────────────────────────
        if body_upper in ("HELP", "?", "START", "INFO"):
            send(from_num, HELP_MSG)
            return
        is_cancel = body_upper in ("NO", "N")
        is_check_all = body_upper in ("YES", "Y", "ALL", "A")
        is_selection = bool(re.match(r'^[\d][,\s\d]*$', body_upper.strip()))
        is_pending_response = is_cancel or is_check_all or is_selection
        with pending_lock: has_p = pkey in pending; data = pending.get(pkey)
        if has_p and is_pending_response:
            if is_cancel:
                with pending_lock: pending.pop(pkey, None)
                send(from_num, "Cancelled."); return
            with pending_lock: data = pending.pop(pkey)
            # ── Filter to selected claims ──────────────────────────────────
            all_claims = data.get("claims") or []
            if is_check_all or not all_claims:
                selected_claims = all_claims or None
            else:
                nums = [int(x) for x in re.split(r'[,\s]+', body_upper.strip()) if x.isdigit()]
                selected_claims = [all_claims[n-1] for n in nums if 1 <= n <= len(all_claims)]
                if not selected_claims:
                    selected_claims = all_claims
            # ── Billing gate ───────────────────────────────────────────────
            bt = _wa_billing_type(from_num)
            # Free users: restrict to single claim
            if bt == "free" and selected_claims and len(selected_claims) > 1:
                send(from_num, "_Free plan — checking first selected claim only. Upgrade for multi-claim checks._")
                selected_claims = selected_claims[:1]
            if bt == "blocked":
                u = _wa_user(from_num)
                _send_payment_prompt(from_num, u["balance_cents"])
                return
            if bt == "free":
                u = _wa_user(from_num)
                remaining = FREE_CHECKS_LIMIT - (u.get("free_checks_used") or 0) - 1
                if remaining <= 0:
                    suffix = "last free check"
                else:
                    suffix = f"{remaining} free check{'s' if remaining != 1 else ''} remaining today"
                status_line = f"✓ Free check — {suffix}"
                if remaining == 0:
                    status_line += "\n_This is your last free check today. Your allowance resets at midnight._"
            elif bt == "paid":
                u = _wa_user(from_num)
                status_line = f"✓ Balance: ${u['balance_cents']/100:.2f}"
            elif bt == "subscriber":
                status_line = "✓ Subscriber — unlimited access"
            else:
                status_line = "✓ Starting fact-check..."
            send(from_num, f"{status_line}\nStarting fact-check...")
            threading.Thread(target=run_check, args=(from_num,data["query"],data["source_type"],data.get("image_bytes"),data["cost"]),
                             kwargs={"billing_type": bt, "pre_claims": selected_claims or data.get("claims"),
                                     "post_date": data.get("post_date", ""),
                                     "source_url": data.get("source_url", ""),
                                     "msg_id": data.get("msg_id","")}, daemon=True).start()
            return
        elif has_p and not is_pending_response:
            with pending_lock: pending.pop(pkey, None)
            log.info("New content received, clearing stale pending")
    query, source_type, image_bytes, post_date, urls = "", "text", None, "", []
    if msg_type == "text":
        body = message["text"]["body"].strip()
        urls = [w for w in body.split() if w.startswith("http")]
        if urls:
            url = urls[0]
            post_date = ""  # will be set during content extraction if available
            # Video platforms — but only treat FB/IG as video if URL pattern suggests it
            video_domains = ["tiktok.com","youtube.com","youtu.be","twitter.com","x.com","rumble.com","bitchute.com","t.me","fb.watch"]
            video_path_hints = ["watch", "video", "reel", "shorts", "clip", "live", "/share/v/", "/share/r/"]
            is_fb_ig = any(d in url for d in ["facebook.com","instagram.com"])
            is_video_link = (
                any(d in url for d in video_domains) or
                (is_fb_ig and any(h in url.lower() for h in video_path_hints))
            )
            if is_video_link:
                try:
                    send(from_num, "🔍 Fetching content...")
                    duration_secs = _get_video_duration(url)
                    if duration_secs > MAX_VIDEO_MINUTES * 60:
                        mins = duration_secs // 60
                        send(from_num,
                            f"⏱️ This video is {mins} minutes long.\n\n"
                            f"Fred can only fact-check videos up to {MAX_VIDEO_MINUTES} minutes. "
                            f"Try sending a shorter clip or a specific timestamp.")
                        return jsonify({"status": "ok"}), 200
                    video_bytes, metadata, post_date = download_video_url(url)
                    if video_bytes and _is_video_bytes(video_bytes):
                        # Silently attempt extraction — only tell user "Video found" if
                        # we actually get frames or audio out of it
                        parts = []
                        if metadata and not _is_useless_title(metadata):
                            parts.append(f"Video: {metadata}")
                        try:
                            frames, duration = extract_video_frames(video_bytes, num_frames=5)
                            if frames:
                                visual = analyze_video_frames(frames)
                                if visual:
                                    parts.append(f"Visual analysis:\n{visual}")
                                    log.info(f"URL video frame analysis: {len(visual)} chars")
                            else:
                                log.warning("URL video: 0 frames extracted (cv2+ffmpeg both failed)")
                        except Exception as e:
                            log.error(f"URL video frame analysis: {e}")
                        transcript = ""
                        try:
                            transcript = transcribe(video_bytes, "video/mp4")
                        except Exception as e:
                            log.error(f"URL video transcription: {e}")
                        if not transcript:
                            log.info("Falling back to yt-dlp audio-only download for transcription")
                            audio_bytes, audio_ext = _ytdlp_audio_bytes(url)
                            if audio_bytes:
                                mime_map = {"m4a": "audio/mp4", "mp3": "audio/mpeg",
                                            "ogg": "audio/ogg", "webm": "audio/webm",
                                            "opus": "audio/ogg"}
                                audio_mime = mime_map.get(audio_ext, "audio/mp4")
                                try:
                                    transcript = transcribe(audio_bytes, audio_mime)
                                except Exception as e:
                                    log.error(f"yt-dlp audio transcription: {e}")
                        if transcript:
                            parts.append(f"Audio: {transcript}")
                        # For YouTube: if transcription failed, try auto-generated captions
                        if not transcript and any(d in url for d in ["youtube.com", "youtu.be"]):
                            try:
                                captions = _ytdlp_captions(url)
                                if captions:
                                    parts.append(f"Captions: {captions}")
                                    log.info(f"YouTube captions added: {len(captions)} chars")
                            except Exception as _ce:
                                log.debug(f"YouTube captions failed: {_ce}")
                        # For FB/IG — always include post caption alongside video analysis.
                        # The caption often contains claims not visible in the video itself
                        # (e.g. describing what the video shows, adding context or spin).
                        if is_fb_ig:
                            try:
                                fb_og = _fb_ig_post_scrape(url)
                                caption = fb_og.get("description", "").strip()
                                if caption and len(caption) > 20:
                                    parts.append(f"Post caption: {caption[:1200]}")
                                    log.info(f"FB/IG caption added: {caption[:80]}")
                            except Exception as e:
                                log.warning(f"FB/IG caption scrape: {e}")
                        # Only confirm "Video found" once we have actual video content
                        has_video_content = any(p.startswith(("Visual analysis:", "Audio:")) for p in parts)
                        if has_video_content:
                            send(from_num, f"🎬 Video found ({len(video_bytes)//1024}KB) — analysed frames and audio")
                        # If we got nothing useful from the video, fall back to OG post scrape
                        if not parts and is_fb_ig:
                            send(from_num, "⚠️ Could not analyse video content — extracting post text instead...")
                            fb_og = _fb_ig_post_scrape(url)
                            if fb_og.get("description"):
                                parts.append(f"Post text: {fb_og['description'][:1200]}")
                            if fb_og.get("title") and not _is_useless_title(fb_og.get("title", "")):
                                parts.append(f"Title: {fb_og['title']}")
                        if parts:
                            query = "\n\n".join(parts)
                            source_type = "video" if any("Visual analysis" in p or "Audio:" in p for p in parts) else "url"
                        else:
                            send(from_num, "❌ Could not extract any content from this video. Please paste the claim as text or send a screenshot.")
                            return
                    elif video_bytes:
                        # Downloaded bytes but not a video (e.g. Twitter image post) — OCR as image
                        log.info(f"video_link path: downloaded non-video bytes ({len(video_bytes)//1024}KB) — routing to image OCR")
                        parts = []
                        if metadata and not _is_useless_title(metadata):
                            parts.append(f"Post text: {metadata}")
                        ocr = ocr_image(video_bytes)
                        if ocr and len(ocr) > 20:
                            parts.append(f"Image text/content:\n{ocr}")
                            image_bytes = video_bytes
                            log.info(f"OCR from downloaded image: {ocr[:80]}")
                        else:
                            image_bytes = video_bytes
                        if parts:
                            query = "\n\n".join(parts)
                            source_type = "image" if image_bytes else "url"
                        elif is_fb_ig:
                            fb_og = _fb_ig_post_scrape(url)
                            if fb_og.get("description"):
                                query = f"Post text: {fb_og['description'][:1200]}"
                                source_type = "url"
                            else:
                                send(from_num, "❌ Could not extract any content from this post. Please paste the claim as text or send a screenshot.")
                                return
                        else:
                            send(from_num, "❌ Could not extract any content from this URL. Please paste the claim as text or send a screenshot.")
                            return
                    elif metadata:
                        # Check if the metadata itself signals unavailability before doing more work
                        if _is_content_unavailable({"title": metadata, "description": metadata}):
                            send(from_num, "🔒 This content appears to be private, deleted, or restricted and cannot be accessed.")
                            return
                        # Video download failed but we have metadata — try yt-dlp audio before falling back
                        send(from_num, "⚙️ Extracting audio from video...")
                        audio_bytes_fb, audio_ext_fb = _ytdlp_audio_bytes(url)
                        if audio_bytes_fb:
                            mime_map = {"m4a": "audio/mp4", "mp3": "audio/mpeg", "ogg": "audio/ogg", "webm": "audio/webm", "opus": "audio/ogg"}
                            try:
                                transcript_fb = transcribe(audio_bytes_fb, mime_map.get(audio_ext_fb, "audio/mp4"))
                                if transcript_fb:
                                    send(from_num, "✓ Audio extracted and transcribed")
                                    query = f"Social media post: {metadata}\n\nAudio transcript:\n{transcript_fb}"
                                    source_type = "video"
                                else:
                                    send(from_num, "⚠️ Could not transcribe audio — fact-checking post text only.\n_Note: the video content itself has not been verified._")
                                    query = f"Social media post: {metadata}\n\nURL: {url}"
                                    source_type = "url"
                            except Exception as e:
                                log.error(f"yt-dlp audio transcription fallback: {e}")
                                send(from_num, "⚠️ Could not access video content — fact-checking post text only.\n_Note: the video content itself has not been verified._")
                                query = f"Social media post: {metadata}\n\nURL: {url}"
                                source_type = "url"
                        else:
                            send(from_num, "⚠️ Could not access video content — fact-checking post text only.\n_Note: the video content itself has not been verified._")
                            query = f"Social media post: {metadata}\n\nURL: {url}"
                            source_type = "url"
                    else:
                        # No video bytes and no metadata — check if content is private/deleted
                        if _check_url_unavailable(url):
                            send(from_num, "🔒 This content appears to be private, deleted, or restricted and cannot be accessed.")
                        else:
                            send(from_num, "❌ Could not access this video. To fact-check it, please describe the claim in text or paste a direct quote.")
                        return
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
                    _fb_video_done = False  # set True if video download gives us content

                    # ── STEP 0: Try video download first ─────────────────────────
                    # FB/IG share links (/share/xxx/) often contain video even though
                    # they don't look like video URLs. Try downloading before falling
                    # back to og:image scrape.
                    try:
                        _fb_dur = _get_video_duration(url)
                        if _fb_dur > MAX_VIDEO_MINUTES * 60:
                            log.info(f"FB/IG video too long ({_fb_dur}s), skipping video download")
                            raise ValueError("video too long")
                        vid_bytes_try, vid_meta_try, vid_date_try = download_video_url(url)
                        if vid_bytes_try and _is_video_bytes(vid_bytes_try):
                            # Bytes look like a video container — silently attempt extraction
                            # before telling the user anything (avoids false "Video found" for
                            # static images packaged as MP4 by Facebook's CDN)
                            if vid_date_try:
                                post_date = vid_date_try
                            vid_parts = []
                            if vid_meta_try and not _is_useless_title(vid_meta_try):
                                vid_parts.append(f"Video: {vid_meta_try}")
                            _vid_frames = []
                            try:
                                _vid_frames, _ = extract_video_frames(vid_bytes_try, num_frames=5)
                                if _vid_frames:
                                    visual = analyze_video_frames(_vid_frames)
                                    if visual:
                                        vid_parts.append(f"Visual analysis:\n{visual}")
                            except Exception as vfe:
                                log.error(f"FB/IG video frame analysis: {vfe}")
                            _vid_transcript = ""
                            try:
                                _vid_transcript = transcribe(vid_bytes_try, "video/mp4")
                                if _vid_transcript:
                                    vid_parts.append(f"Audio: {_vid_transcript}")
                            except Exception as vte:
                                log.warning(f"FB/IG video transcription: {vte}")
                            # No audio transcript — Facebook CDN often wraps static images as
                            # short MP4s. OCR the first frame to capture any text overlay.
                            if not _vid_transcript and _vid_frames:
                                try:
                                    _frame_ocr = ocr_image(_vid_frames[0])
                                    if _frame_ocr and len(_frame_ocr) > 20:
                                        vid_parts.append(f"Image text:\n{_frame_ocr}")
                                        log.info(f"FB/IG: no audio — OCR'd first frame: {_frame_ocr[:80]}")
                                except Exception as _foe:
                                    log.warning(f"FB/IG frame OCR: {_foe}")
                            has_av = any(p.startswith(("Visual analysis:", "Audio:", "Image text:")) for p in vid_parts)
                            if has_av:
                                # Only now confirm to the user — actual video content confirmed
                                send(from_num, f"🎬 Video found ({len(vid_bytes_try)//1024}KB) — analysed frames and audio")
                                page_text = "\n\n".join(vid_parts)
                                source_type = "video"
                                video_bytes = vid_bytes_try
                                _fb_video_done = True
                                log.info(f"FB/IG video download succeeded: {len(page_text)} chars")
                                # Always fetch post caption — may contain claims not visible
                                # in the video/image itself (context, spin, attribution)
                                try:
                                    _post_og = _fb_ig_post_scrape(url)
                                    _caption = _post_og.get("description", "").strip()
                                    if _caption and len(_caption) > 20:
                                        vid_parts.append(f"Post caption: {_caption[:1200]}")
                                        page_text = "\n\n".join(vid_parts)
                                        log.info(f"FB/IG post caption added after video: {_caption[:80]}")
                                    if _post_og.get("post_date") and not post_date:
                                        post_date = _post_og["post_date"]
                                except Exception as _ce:
                                    log.warning(f"FB/IG post caption fetch after video: {_ce}")
                            else:
                                log.info(f"FB/IG: MP4 bytes but no extractable frames/audio — treating as image post")
                        elif vid_bytes_try:
                            # API returned bytes but they're an image, not a video — OCR directly
                            log.info(f"FB/IG download returned image bytes ({len(vid_bytes_try)//1024}KB) — routing to image OCR")
                            if vid_date_try:
                                post_date = vid_date_try
                            if vid_meta_try and not _is_useless_title(vid_meta_try):
                                parts.append(f"Post text: {vid_meta_try}")
                            ocr = ocr_image(vid_bytes_try)
                            if ocr and len(ocr) > 20:
                                parts.append(f"Image text/content:\n{ocr}")
                                image_bytes = vid_bytes_try
                                log.info(f"OCR from downloaded image: {ocr[:80]}")
                            else:
                                # Keep bytes as an image candidate for OSINT even if OCR found nothing
                                image_bytes = vid_bytes_try
                        else:
                            log.info("FB/IG: no downloadable content found — proceeding to post scrape")
                    except Exception as vde:
                        log.warning(f"FB/IG video download attempt failed: {vde}")

                    if not _fb_video_done:
                        # ── STEP 1: facebookexternalhit scrape ───────────────────────
                        fb_og = _fb_ig_post_scrape(url)
                        if fb_og.get("post_date"):
                            post_date = fb_og["post_date"]
                        if fb_og.get("description"):
                            parts.append(f"Post text: {fb_og['description'][:1200]}")
                            send(from_num, f"✓ Post text extracted ({len(fb_og['description'])} chars)")
                            # FB og:description is truncated — look for external article URLs
                            # in the description text and scrape the full article
                            _og_ext_urls = re.findall(
                                r'https?://(?!(?:www\.)?facebook\.com)(?!(?:www\.)?instagram\.com)\S+',
                                fb_og['description'])
                            if _og_ext_urls and "Article text:" not in "\n".join(parts):
                                try:
                                    art_text = fetch(_og_ext_urls[0])
                                    if art_text and len(art_text) > 100:
                                        parts.append(f"Article text:\n{art_text[:3000]}")
                                        log.info(f"OG-desc article scraped: {len(art_text)} chars")
                                except Exception as _ae:
                                    log.warning(f"OG-desc article scrape failed: {_ae}")
                        # Scrape the linked article if FB gave us the source URL
                        if fb_og.get("article_url") and "Article text:" not in "\n".join(parts):
                            try:
                                art_text = fetch(fb_og["article_url"])
                                if art_text and len(art_text) > 100:
                                    parts.append(f"Article text:\n{art_text[:3000]}")
                                    send(from_num, f"📰 Linked article scraped ({len(art_text)} chars)")
                                    log.info(f"article_url scraped: {len(art_text)} chars from {fb_og['article_url'][:80]}")
                            except Exception as _ae:
                                log.warning(f"article_url scrape failed: {_ae}")
                        if fb_og.get("title") and not parts:
                            parts.append(f"Title: {fb_og['title']}")
                        if fb_og.get("image_url") and fb_og["image_url"].startswith("http"):
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
                                    title = info.get("title","")
                                    if title and title not in ("Facebook","Instagram") and not parts:
                                        parts.append(f"Title: {title}")
                                    desc = info.get("description","") or ""
                                    if desc:
                                        # Replace truncated og:description with yt-dlp's
                                        # full description if it's longer
                                        existing = next((p for p in parts if p.startswith("Post text:")), None)
                                        existing_len = len(existing.replace("Post text: ","",1)) if existing else 0
                                        if len(desc) > existing_len:
                                            if existing:
                                                parts.remove(existing)
                                            parts.insert(0, f"Post text: {desc[:1200]}")
                                            send(from_num, f"✓ Post text extracted ({len(desc)} chars)")
                                            log.info(f"yt-dlp desc replaced og:desc: {len(desc)} > {existing_len} chars")
                                    if info.get("uploader"):
                                        parts.append(f"Posted by: {info['uploader']}")
                                    log.info(f"yt-dlp: title={title[:50]} desc={bool(desc)} thumb={bool(info.get('thumbnail'))}")
                                    if info.get("thumbnail"):
                                        img_candidates.append(info["thumbnail"])
                                    raw_url = info.get("url","")
                                    if raw_url and any(raw_url.lower().endswith(x) for x in (".jpg",".jpeg",".png",".webp")):
                                        img_candidates.append(raw_url)
                                    for fmt in (info.get("formats") or []):
                                        if fmt.get("ext") in ("jpg","jpeg","png","webp") and fmt.get("url"):
                                            img_candidates.append(fmt["url"])
                                    # Carousel/album posts: each entry has its own thumbnail
                                    for _entry in (info.get("entries") or []):
                                        if _entry.get("thumbnail"):
                                            img_candidates.append(_entry["thumbnail"])
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
                                    # Also scrape full article text — FB post captions are truncated
                                    # by og:description (~150 chars); the linked article has the full text
                                    if ext_urls and "Article text:" not in "\n".join(parts):
                                        try:
                                            art_text = fetch(ext_urls[0])
                                            if art_text and len(art_text) > 100:
                                                parts.append(f"Article text:\n{art_text[:3000]}")
                                                log.info(f"Linked article scraped: {len(art_text)} chars from {ext_urls[0][:80]}")
                                        except Exception as ae:
                                            log.warning(f"Linked article scrape failed: {ae}")
                            if cookies_file and os.path.exists(cookies_file):
                                os.unlink(cookies_file)
                        except Exception as e:
                            log.warning(f"yt-dlp info extraction failed: {e}")

                        # ── STEP 3: OCR all images (carousel/album aware) ─────────
                        seen_urls = set()
                        all_ocr_texts = []
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
                                    all_ocr_texts.append(ocr)
                                    if not image_bytes:
                                        image_bytes = img_r.content  # save first for OSINT
                                    log.info(f"OCR success from {img_url[:60]}: {ocr[:80]}")
                            except Exception as ie:
                                log.warning(f"Image OCR failed ({img_url[:60]}): {ie}")
                        if all_ocr_texts:
                            combined_ocr = "\n---\n".join(all_ocr_texts)
                            parts.append(f"Image text/content:\n{combined_ocr}")
                            n = len(all_ocr_texts)
                            send(from_num, f"🖼 {'Image' if n == 1 else f'{n} images'} analysed — extracted text ({len(combined_ocr)} chars)")
                            ocr_succeeded = True
                        elif img_candidates:
                            log.warning(f"FB/IG: OCR failed for all {len(img_candidates)} image candidates")
                            send(from_num, "⚠️ Could not read text from post image")

                        # ── STEP 4: Tavily article lookup when post text is short ──
                        # FB og:description is capped at ~150 chars; yt-dlp often fails
                        # on share URLs. Use Tavily to find and fetch the source article.
                        # When OCR succeeded, the image often contains the article headline —
                        # augment the query with that to target the right article.
                        post_text_part = next((p for p in parts if p.startswith("Post text:")), "")
                        post_text_len = len(post_text_part.replace("Post text: ", "", 1))
                        if post_text_len < 300 and "Article text:" not in "\n".join(parts) and TAVILY_API_KEY:
                            try:
                                search_query = post_text_part.replace("Post text: ", "", 1)[:200]
                                # Augment with OCR headline when post text is truncated.
                                # The post image often shows the article headline/outlet name
                                # which narrows the search to the right source article.
                                ocr_part = next((p for p in parts if p.startswith("Image text/content:")), "")
                                if ocr_part:
                                    ocr_text = ocr_part.replace("Image text/content:\n", "", 1).strip()
                                    # First non-empty line is usually the headline or outlet name
                                    ocr_headline = next((ln.strip() for ln in ocr_text.split("\n") if ln.strip()), "")[:150]
                                    if ocr_headline and ocr_headline.lower() not in search_query.lower():
                                        search_query = f"{search_query} {ocr_headline}".strip()
                                        log.info(f"Tavily query augmented with OCR headline: {search_query[:200]}")
                                tv = requests.post("https://api.tavily.com/search",
                                    json={"api_key": TAVILY_API_KEY, "query": search_query,
                                          "search_depth": "basic", "max_results": 3,
                                          "include_raw_content": True},
                                    timeout=15)
                                if tv.ok:
                                    for res in tv.json().get("results", []):
                                        raw = res.get("raw_content") or res.get("content") or ""
                                        src_url = res.get("url", "")
                                        if raw and len(raw) > 300 and "facebook.com" not in src_url:
                                            parts.append(f"Source article ({src_url}):\n{raw[:3000]}")
                                            log.info(f"Tavily article found: {len(raw)} chars from {src_url[:80]}")
                                            break
                            except Exception as te:
                                log.warning(f"Tavily article lookup failed: {te}")

                        if parts:
                            page_text = "\n\n".join(parts)
                            log.info(f"FB/IG extracted: {len(page_text)} chars, {len(img_candidates)} img candidates")

                        # Detect private/deleted/restricted content via og: signals
                        # rather than character counts (which are fragile).
                        # fb_og is always set by STEP 1 above.
                        if _is_content_unavailable(fb_og):
                            send(from_num,
                                "⚠️ This post appears to be private, deleted, or restricted.\n\n"
                                "Fred couldn't access the content — it may have been removed or shared "
                                "with a limited audience.\n\n"
                                "If the post is public, try copying the claim text directly and sending it as a message.")
                            return

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
            # For any additional URLs in the message, fetch article text and append.
            # Skip social/video platforms — those need the full pipeline.
            _skip_domains = ["youtube.com","youtu.be","twitter.com","x.com","tiktok.com",
                             "instagram.com","facebook.com","fb.watch","rumble.com","bitchute.com"]
            for _extra_url in urls[1:]:
                if any(d in _extra_url for d in _skip_domains):
                    continue
                try:
                    _extra_text = fetch(_extra_url)
                    if _extra_text and len(_extra_text) > 100:
                        query = query + f"\n\nAdditional source:\n{_extra_text[:2000]}"
                        log.info(f"Extra URL fetched: {len(_extra_text)} chars from {_extra_url[:60]}")
                except Exception as _eu:
                    log.debug(f"Extra URL fetch failed ({_extra_url[:60]}): {_eu}")
        else:
            query, source_type = body, "text"
            # Enrich short text with Tavily context so claim extraction has background
            if TAVILY_API_KEY and len(body) < 400:
                ctx = tavily_search(body, max_results=3)
                if ctx:
                    ctx_text = "\n".join(f"{n}: {s}" for n, s in ctx[:3])
                    query = f"{body}\n\nBACKGROUND CONTEXT (real-time web):\n{ctx_text}"
    elif msg_type == "image":
        send(from_num, "🖼 Analysing image..."); image_bytes = download_media(message["image"]["id"])
        if image_bytes:
            _img_parts = []
            _img_caption = message["image"].get("caption", "").strip()
            if _img_caption:
                _img_parts.append(f"Caption: {_img_caption}")
            _ocr = clean_query(ocr_image(image_bytes))
            if _ocr:
                _img_parts.append(_ocr)
            query = "\n\n".join(_img_parts)
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
                frames, duration = extract_video_frames(video_bytes, num_frames=5)
                if frames:
                    visual = analyze_video_frames(frames)
                    if visual:
                        query_parts.append(f"Visual analysis:\n{visual}")
                    # Use middle frame for Hive AI/deepfake detection — more representative than first frame
                    if frames and not image_bytes:
                        image_bytes = frames[len(frames) // 2]
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
    query = query.strip()[:6000]
    log.info("Received [%s]: %s", source_type, query[:100])
    cost = estimate_cost(source_type)

    # ── Extract claims before confirmation — show user what will be checked ──
    if source_type in ("text", "image", "audio", "url", "video"):
        send(from_num, "🔍 Identifying claims...")
        assessment = assess_content_claims(query, source_type, post_date=post_date)
        if not assessment["checkable"] or not assessment["claims"]:
            msg = no_claims_msg(assessment["reason"], source_type, assessment["suggestions"])
            # Still run AI/deepfake detection if we have an image from the post
            if image_bytes and HIVE_API_KEY:
                hive = hive_ai_check(image_bytes=image_bytes)
                ai_score = hive.get("ai_generated", 0)
                df_score = hive.get("deepfake", 0)
                generator = hive.get("generator", "")
                log.info(f"No-claims Hive check: ai={ai_score:.2f} deepfake={df_score:.2f}")
                if ai_score > 0.5 or df_score > 0.5:
                    ai_line = ""
                    if ai_score > 0.5:
                        gen_label = f" _(likely {generator})_" if generator else ""
                        ai_line = f"🤖 *AI-generated image detected: {int(ai_score*100)}%*{gen_label}"
                    if df_score > 0.5:
                        ai_line += f"\n🎭 *Deepfake detected: {int(df_score*100)}%*"
                    msg = f"⚠️ *AI-Generated Content Detected*\n\n{ai_line}\n\n" + msg
            send(from_num, msg)
            return
        claims = assessment["claims"]
        # Dev bypass: skip confirmation, auto-select all claims
        if DEV_AUTOSELECT_ON and DEV_AUTOSELECT_NUM and from_num == DEV_AUTOSELECT_NUM:
            send(from_num, f"🚀 _Dev: auto-selecting all {len(claims)} claim(s)_")
            bt_dev = _wa_billing_type(from_num)
            threading.Thread(target=run_check, args=(from_num, query, source_type, image_bytes, cost),
                             kwargs={"billing_type": bt_dev, "pre_claims": claims,
                                     "post_date": post_date, "source_url": urls[0] if urls else "",
                                     "msg_id": msg_id}, daemon=True).start()
            return
        bt_now = _wa_billing_type(from_num)
        with pending_lock:
            pending[pkey] = {"query": query, "source_type": source_type, "image_bytes": image_bytes,
                             "cost": cost, "timestamp": t.time(), "claims": claims,
                             "post_date": post_date, "source_url": urls[0] if urls else "",
                             "msg_id": msg_id}
        send(from_num, claims_confirm_msg(claims, source_type, cost, is_free=(bt_now == "free")))
    else:
        # document — no claim extraction, show raw preview
        with pending_lock:
            pending[pkey] = {"query": query, "source_type": source_type, "image_bytes": image_bytes,
                             "cost": cost, "timestamp": t.time(), "post_date": post_date,
                             "source_url": urls[0] if urls else "", "msg_id": msg_id}
        send(from_num, confirm_msg(source_type, query, cost))

# ── Web API: database, auth, rate-limiting ───────────────────────────────────

def _db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                tier TEXT NOT NULL DEFAULT 'free',
                balance_cents INTEGER NOT NULL DEFAULT 0,
                stripe_customer_id TEXT
            );
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wa_users (
                wa_id TEXT PRIMARY KEY,
                free_checks_used INTEGER NOT NULL DEFAULT 0,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                tier TEXT NOT NULL DEFAULT 'free',
                stripe_customer_id TEXT,
                created_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_type TEXT NOT NULL,
                user_id TEXT NOT NULL,
                txn_type TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                description TEXT NOT NULL,
                stripe_session_id TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_users (
                platform TEXT NOT NULL,
                platform_id TEXT NOT NULL,
                free_checks_used INTEGER NOT NULL DEFAULT 0,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                tier TEXT NOT NULL DEFAULT 'free',
                stripe_customer_id TEXT,
                created_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                PRIMARY KEY (platform, platform_id)
            );
            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                uid TEXT NOT NULL,
                source_type TEXT NOT NULL,
                raw_input TEXT,
                extracted_claim TEXT,
                rating TEXT,
                confidence TEXT,
                verdict_json TEXT,
                response_text TEXT,
                cost_usd REAL,
                feedback INTEGER,
                created_at INTEGER NOT NULL
            );
        """)
        # Migrations for existing deployments
        for col, defn in [("balance_cents","INTEGER NOT NULL DEFAULT 0"),
                          ("stripe_customer_id","TEXT")]:
            try: c.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except Exception: pass
        try: c.execute("ALTER TABLE platform_users ADD COLUMN profile_name TEXT")
        except Exception: pass
        try: c.execute("ALTER TABLE platform_users ADD COLUMN free_checks_date TEXT")
        except Exception: pass
        # Migrate existing wa_users into platform_users
        try:
            rows = c.execute("SELECT * FROM wa_users").fetchall()
            for r in rows:
                c.execute("""INSERT OR IGNORE INTO platform_users
                    (platform, platform_id, free_checks_used, balance_cents, tier, stripe_customer_id, created_at, last_seen)
                    VALUES ('whatsapp', ?, ?, ?, ?, ?, ?, ?)""",
                    (r["wa_id"], r["free_checks_used"], r["balance_cents"], r["tier"],
                     r["stripe_customer_id"], r["created_at"], r["last_seen"]))
        except Exception:
            pass
    log.info("DB initialised at %s", DB_PATH)

init_db()


def _log_request(platform, uid, source_type, raw_input, extracted_claim, a, report, cost_usd):
    """Log a fact-check request and Fred's response to request_log."""
    import time as _time, json as _json
    try:
        with _db() as c:
            c.execute("""
                INSERT INTO request_log
                    (platform, uid, source_type, raw_input, extracted_claim,
                     rating, confidence, verdict_json, response_text, cost_usd, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (platform, uid, source_type,
                 (raw_input or "")[:2000],
                 (extracted_claim or "")[:1000],
                 a.get("rating"), a.get("confidence"),
                 _json.dumps(a),
                 (report or "")[:4000],
                 cost_usd,
                 int(_time.time())))
    except Exception as e:
        log.warning("request_log insert failed: %s", e)

# ── WhatsApp user billing ─────────────────────────────────────────────────────

def _wa_user(wa_id, profile_name=None):
    return _puser("whatsapp", wa_id, profile_name=profile_name)

def _wa_billing_type(wa_id):
    return _pbilling_type("whatsapp", wa_id)

def _wa_deduct(wa_id, cents, description, billing_type):
    _pdeduct("whatsapp", wa_id, cents, description, billing_type)

def _wa_credit(wa_id, cents, description, stripe_session_id=None):
    _pcredit("whatsapp", wa_id, cents, description, stripe_session_id)

def _send_payment_prompt(wa_id, balance_cents):
    _psend_payment_prompt("whatsapp", wa_id, balance_cents, lambda text: send(wa_id, text))

def _is_new_wa_user(wa_id):
    """Return True if this WhatsApp number has never sent a message before."""
    with _db() as c:
        row = c.execute(
            "SELECT 1 FROM platform_users WHERE platform='whatsapp' AND platform_id=?",
            (str(wa_id),)
        ).fetchone()
    return row is None

def get_random_ad():
    """Return a random sponsor ad line, or empty string."""
    return random.choice(SPONSOR_ADS) if SPONSOR_ADS else ""

# ── Generalized platform billing ──────────────────────────────────────────────

def _puser(platform, uid, profile_name=None):
    """Get or create platform user record. Returns dict."""
    now = int(t.time())
    uid = str(uid)
    with _db() as c:
        row = c.execute("SELECT * FROM platform_users WHERE platform=? AND platform_id=?", (platform, uid)).fetchone()
        if not row:
            c.execute("INSERT INTO platform_users (platform, platform_id, profile_name, created_at, last_seen) VALUES (?,?,?,?,?)",
                      (platform, uid, profile_name or None, now, now))
            row = c.execute("SELECT * FROM platform_users WHERE platform=? AND platform_id=?", (platform, uid)).fetchone()
        else:
            c.execute("UPDATE platform_users SET last_seen=? WHERE platform=? AND platform_id=?", (now, platform, uid))
    return dict(row)

def _today():
    import datetime as _dt
    return _dt.date.today().isoformat()

# ── Daily free check logic (commented out — switched back to lifetime total) ──
# def _daily_free_used(u):
#     """Return how many free checks the user has used today (resets at midnight)."""
#     if u.get("free_checks_date") != _today():
#         return 0
#     return u.get("free_checks_used") or 0

def _pbilling_type(platform, uid):
    """Returns 'subscriber' | 'free' | 'paid' | 'blocked'."""
    u = _puser(platform, uid)
    if u["tier"] == "subscriber": return "subscriber"
    if (u.get("free_checks_used") or 0) < FREE_CHECKS_LIMIT: return "free"  # lifetime total
    # Daily mode: if _daily_free_used(u) < FREE_CHECKS_LIMIT: return "free"
    if u["balance_cents"] > 0: return "paid"
    return "blocked"

def _pdeduct(platform, uid, cents, description, billing_type):
    """Record usage and deduct balance."""
    uid = str(uid)
    now = int(t.time())
    txn_type = billing_type if billing_type in ("free", "subscriber") else "debit"
    if billing_type == "paid":
        with _db() as c:
            c.execute("UPDATE platform_users SET balance_cents = MAX(0, balance_cents - ?) WHERE platform=? AND platform_id=?", (cents, platform, uid))
    elif billing_type == "free":
        with _db() as c:
            c.execute("UPDATE platform_users SET free_checks_used = free_checks_used + 1 WHERE platform=? AND platform_id=?", (platform, uid))
        # ── Daily reset mode (commented out) ──
        # today = _today()
        # u = c.execute("SELECT free_checks_used, free_checks_date FROM platform_users WHERE platform=? AND platform_id=?", (platform, uid)).fetchone()
        # if u and u["free_checks_date"] != today:
        #     c.execute("UPDATE platform_users SET free_checks_used=1, free_checks_date=? WHERE platform=? AND platform_id=?", (today, platform, uid))
        # else:
        #     c.execute("UPDATE platform_users SET free_checks_used = free_checks_used + 1, free_checks_date=? WHERE platform=? AND platform_id=?", (today, platform, uid))
    with _db() as c:
        c.execute("INSERT INTO transactions (user_type,user_id,txn_type,amount_cents,description,created_at) VALUES (?,?,?,?,?,?)",
                  (platform, uid, txn_type, cents, description, now))
    log.info("Billing %s/%s: type=%s cost=%d¢", platform, uid, billing_type, cents)

def _pcredit(platform, uid, cents, description, stripe_session_id=None):
    """Credit a platform user's balance."""
    uid = str(uid)
    with _db() as c:
        c.execute("UPDATE platform_users SET balance_cents = balance_cents + ? WHERE platform=? AND platform_id=?", (cents, platform, uid))
        c.execute("INSERT INTO transactions (user_type,user_id,txn_type,amount_cents,description,stripe_session_id,created_at) VALUES (?,?,?,?,?,?,?)",
                  (platform, uid, "credit", cents, description, stripe_session_id, int(t.time())))
    log.info("Credited %s/%s: %d¢", platform, uid, cents)

def _psend_payment_prompt(platform, uid, balance_cents, send_fn):
    """Send Stripe payment links via any platform's send_fn."""
    cid = f"{platform[:4]}_{uid}"
    suffix = f"?client_reference_id={cid}"
    free_word = "check" if FREE_CHECKS_LIMIT == 1 else "checks"
    lines = [
        "💳 *Fred Check — Top Up Required*", "",
        f"You've used your {FREE_CHECKS_LIMIT} free {free_word}.",
        f"Current balance: *${balance_cents/100:.2f}*", "",
        "*Choose a top-up amount:*", "",
    ]
    if TOPUP_1_LINK:  lines.append(f"• *$1* (~5 checks)  {TOPUP_1_LINK}{suffix}")
    if TOPUP_5_LINK:  lines.append(f"• *$5* (~25 checks)  {TOPUP_5_LINK}{suffix}")
    if TOPUP_10_LINK: lines.append(f"• *$10* (~50 checks)  {TOPUP_10_LINK}{suffix}")
    if TOPUP_25_LINK: lines.append(f"• *$25* (~130 checks)  {TOPUP_25_LINK}{suffix}")
    if SUB_LINK:
        lines += ["", f"*♾ Unlimited* → {SUB_LINK}{suffix}"]
    if not any([TOPUP_1_LINK, TOPUP_5_LINK, TOPUP_10_LINK, TOPUP_25_LINK, SUB_LINK]):
        beta_note = "\n_We're in BETA — paid plans launching soon. Watch this space!_" if BETA_MODE else ""
        lines += ["", f"_Payment system coming soon._{beta_note}"]
    lines += ["", "_Secure payment by Stripe_"]
    send_fn("\n".join(lines))

# ── Stripe webhook helpers ────────────────────────────────────────────────────

def _verify_stripe_sig(payload_bytes, sig_header):
    """Return True if the Stripe-Signature header is valid."""
    if not STRIPE_WEBHOOK_SECRET:
        return True  # no secret configured → skip verification
    try:
        parts = {}
        for item in sig_header.split(","):
            k, v = item.split("=", 1)
            parts.setdefault(k, []).append(v)
        timestamp = parts.get("t", [""])[0]
        sigs = parts.get("v1", [])
        signed = f"{timestamp}.{payload_bytes.decode('utf-8')}"
        expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in sigs)
    except Exception as e:
        log.error("Stripe sig verification error: %s", e)
        return False

# Anonymous rate-limit: 5 fact-checks per IP per day
_rate_store = {}  # ip -> {"count": n, "date": "YYYY-MM-DD"}
_rate_lock = threading.Lock()
ANON_DAILY_LIMIT = 5

def _check_rate(ip):
    """Return True if request is allowed, False if limit exceeded."""
    today = t.strftime("%Y-%m-%d")
    with _rate_lock:
        entry = _rate_store.get(ip, {"count": 0, "date": today})
        if entry["date"] != today:
            entry = {"count": 0, "date": today}
        if entry["count"] >= ANON_DAILY_LIMIT:
            return False
        entry["count"] += 1
        _rate_store[ip] = entry
    return True

def _hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}:{h}"

def _verify_pw(pw, stored):
    try:
        salt, h = stored.split(":", 1)
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex() == h
    except Exception:
        return False

def _create_token(user_id):
    token = secrets.token_hex(32)
    expires = int(t.time()) + 30 * 86400  # 30 days
    with _db() as c:
        c.execute("INSERT INTO tokens VALUES (?,?,?,?)", (token, user_id, int(t.time()), expires))
    return token

def _auth_user():
    """Return user_id from Bearer token in request, or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    now = int(t.time())
    with _db() as c:
        row = c.execute("SELECT user_id FROM tokens WHERE token=? AND expires_at>?", (token, now)).fetchone()
    return row["user_id"] if row else None

def _factcheck_pipeline(query, source_type="text"):
    """Core pipeline: neutralize → extract → scrape → analyse. Returns list of result dicts."""
    if source_type in ("text", "url"):
        neutral = neutralize_claim(query)
        claims = extract_claims(neutral)
    else:
        neutral = query
        claims = [query]
    g = google_fc(neutral)
    sc, used_sources = scrape_sites(neutral)
    gfc_sources = [x["source"] for x in g if x.get("source")]
    all_used = list(dict.fromkeys(gfc_sources + used_sources))
    results = []
    for claim in claims:
        a = claude_analyse(claim, g, sc, source_type)
        # strip internal debate fields from API response
        a.pop("_debate_pro", None); a.pop("_debate_con", None)
        results.append({"claim": claim, "analysis": a, "sources_consulted": all_used[:15]})
    return results

# ── Web API endpoints ─────────────────────────────────────────────────────────

@app.route("/web")
@app.route("/web/")
def web_index():
    return send_from_directory("static", "index.html")

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    pw = data.get("password") or ""
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if len(pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    try:
        with _db() as c:
            c.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
                      (email, _hash_pw(pw), int(t.time())))
            uid = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
        token = _create_token(uid)
        log.info("New user registered: %s", email)
        return jsonify({"token": token, "email": email}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already registered"}), 409

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    pw = data.get("password") or ""
    with _db() as c:
        row = c.execute("SELECT id, password_hash FROM users WHERE email=?", (email,)).fetchone()
    if not row or not _verify_pw(pw, row["password_hash"]):
        return jsonify({"error": "Invalid email or password"}), 401
    token = _create_token(row["id"])
    return jsonify({"token": token, "email": email})

@app.route("/api/me", methods=["GET"])
def api_me():
    uid = _auth_user()
    if not uid:
        return jsonify({"error": "Unauthorised"}), 401
    with _db() as c:
        row = c.execute("SELECT email, tier, created_at FROM users WHERE id=?", (uid,)).fetchone()
        count = c.execute("SELECT COUNT(*) as n FROM history WHERE user_id=?", (uid,)).fetchone()["n"]
    return jsonify({"email": row["email"], "tier": row["tier"], "checks_total": count})

@app.route("/api/factcheck", methods=["POST"])
def api_factcheck():
    uid = _auth_user()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    # Rate-limit anonymous users
    if not uid and not _check_rate(ip):
        return jsonify({"error": f"Daily limit of {ANON_DAILY_LIMIT} fact-checks reached. Sign up for unlimited access."}), 429
    data = request.get_json() or {}
    query = (data.get("claim") or data.get("query") or "").strip()[:2000]
    if not query:
        return jsonify({"error": "No claim provided"}), 400
    source_type = "url" if query.startswith("http") else "text"
    # For article URLs, scrape the page text first
    if source_type == "url":
        page_text = fetch(query) or _og_metadata(query)
        if page_text:
            query = page_text
    try:
        results = _factcheck_pipeline(query, source_type)
        # Save to history if logged in
        if uid:
            with _db() as c:
                c.execute("INSERT INTO history (user_id, query, results_json, created_at) VALUES (?,?,?,?)",
                          (uid, query[:500], json.dumps(results), int(t.time())))
        return jsonify({"results": results})
    except Exception as e:
        log.error("API factcheck error: %s", e)
        return jsonify({"error": "Fact-check failed. Please try again."}), 500

@app.route("/api/history", methods=["GET"])
def api_history():
    uid = _auth_user()
    if not uid:
        return jsonify({"error": "Unauthorised"}), 401
    with _db() as c:
        rows = c.execute("SELECT id, query, results_json, created_at FROM history WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (uid,)).fetchall()
    return jsonify({"history": [{"id": r["id"], "query": r["query"], "created_at": r["created_at"],
                                  "results": json.loads(r["results_json"])} for r in rows]})

@app.route("/api/billing", methods=["GET"])
def api_billing():
    """Web user billing info: balance, tier, transaction history."""
    uid = _auth_user()
    if not uid:
        return jsonify({"error": "Unauthorised"}), 401
    with _db() as c:
        user = c.execute("SELECT email, tier, balance_cents FROM users WHERE id=?", (uid,)).fetchone()
        txns = c.execute("SELECT txn_type, amount_cents, description, created_at FROM transactions WHERE user_type='web' AND user_id=? ORDER BY created_at DESC LIMIT 50", (str(uid),)).fetchall()
    return jsonify({
        "email": user["email"], "tier": user["tier"],
        "balance_cents": user["balance_cents"], "balance": f"${user['balance_cents']/100:.2f}",
        "transactions": [dict(r) for r in txns]
    })

@app.route("/api/topup", methods=["POST"])
def api_topup():
    """Create a Stripe Checkout Session for web user top-up."""
    uid = _auth_user()
    if not uid:
        return jsonify({"error": "Unauthorised"}), 401
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Payment system not configured"}), 503
    data = request.get_json() or {}
    amount_cents = int(data.get("amount_cents", 500))
    if amount_cents not in (500, 1000, 2500):
        return jsonify({"error": "Invalid amount"}), 400
    cid = f"web_{uid}"
    try:
        # Use form-encoded POST (Stripe v1 API style)
        payload = {
            "mode": "payment",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][product_data][name]": "Fred Check Credits",
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][quantity]": "1",
            "client_reference_id": cid,
            "success_url": "https://web-production-1f0a4.up.railway.app/web?paid=1",
            "cancel_url": "https://web-production-1f0a4.up.railway.app/web",
        }
        r = requests.post("https://api.stripe.com/v1/checkout/sessions",
            headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=payload, timeout=15)
        r.raise_for_status()
        return jsonify({"url": r.json().get("url", "")})
    except Exception as e:
        log.error("Stripe checkout error: %s", e)
        return jsonify({"error": "Payment system error"}), 500

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """Handle Stripe payment events: credit user balance on successful payment."""
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    if not _verify_stripe_sig(payload, sig):
        log.warning("Stripe webhook: invalid signature")
        return jsonify({"error": "Invalid signature"}), 400
    try:
        event = json.loads(payload)
        etype = event.get("type", "")
        obj = event.get("data", {}).get("object", {})
        log.info("Stripe event: %s", etype)

        if etype == "checkout.session.completed":
            cid = obj.get("client_reference_id") or ""
            mode = obj.get("mode", "")
            amount = obj.get("amount_total", 0)
            session_id = obj.get("id", "")
            customer_id = obj.get("customer", "")

            # Parse platform prefix: wa_/msgr_/inst_/tg_/tw_/web_
            platform_map = {"wa": "whatsapp", "msgr": "messenger", "inst": "instagram",
                            "tg": "telegram", "tw": "twitter", "web": "web"}
            platform, uid = None, None
            for prefix, pname in platform_map.items():
                if cid.startswith(f"{prefix}_"):
                    platform = pname
                    uid = cid[len(prefix)+1:]
                    break

            if platform and uid and platform != "web":
                # Messenger/Telegram/WhatsApp user
                _puser(platform, uid)  # ensure record exists
                if mode == "payment":
                    _pcredit(platform, uid, amount, f"Top-up ${amount/100:.2f}", session_id)
                    platform_send = {
                        "whatsapp": lambda t, u=uid: send(u, t),
                        "messenger": lambda t, u=uid: send_messenger(u, t),
                        "instagram": lambda t, u=uid: send_messenger(u, t),
                        "telegram": lambda t, u=uid: send_telegram(u, t),
                        "twitter": lambda t, u=uid: send_twitter_dm(u, t),
                    }.get(platform, lambda t: None)
                    platform_send(f"✅ *Payment received!* ${amount/100:.2f} added to your balance.\n\nYou can now continue fact-checking. Send any claim to get started.")
                elif mode == "subscription":
                    with _db() as c:
                        c.execute("UPDATE platform_users SET tier='subscriber', stripe_customer_id=? WHERE platform=? AND platform_id=?", (customer_id, platform, uid))
                        c.execute("INSERT INTO transactions (user_type,user_id,txn_type,amount_cents,description,stripe_session_id,created_at) VALUES (?,?,'credit',?,?,?,?)",
                                  (platform, uid, amount, "Subscription activated", session_id, int(t.time())))
                    platform_send = {
                        "whatsapp": lambda t, u=uid: send(u, t),
                        "messenger": lambda t, u=uid: send_messenger(u, t),
                        "instagram": lambda t, u=uid: send_messenger(u, t),
                        "telegram": lambda t, u=uid: send_telegram(u, t),
                        "twitter": lambda t, u=uid: send_twitter_dm(u, t),
                    }.get(platform, lambda t: None)
                    platform_send("🎉 *Subscription activated!* You now have unlimited Fred Check access.")
                    log.info("Subscription activated for %s/%s", platform, uid)

            elif platform == "web" and uid:
                try:
                    web_uid = int(uid)
                    if mode == "payment":
                        with _db() as c:
                            c.execute("UPDATE users SET balance_cents = balance_cents + ?, stripe_customer_id=COALESCE(stripe_customer_id,?) WHERE id=?",
                                      (amount, customer_id, web_uid))
                            c.execute("INSERT INTO transactions (user_type,user_id,txn_type,amount_cents,description,stripe_session_id,created_at) VALUES ('web',?,'credit',?,?,?,?)",
                                      (str(web_uid), amount, f"Top-up ${amount/100:.2f}", session_id, int(t.time())))
                        log.info("Web user %d credited: %d¢", web_uid, amount)
                    elif mode == "subscription":
                        with _db() as c:
                            c.execute("UPDATE users SET tier='subscriber', stripe_customer_id=? WHERE id=?", (customer_id, web_uid))
                            c.execute("INSERT INTO transactions (user_type,user_id,txn_type,amount_cents,description,stripe_session_id,created_at) VALUES ('web',?,'credit',?,?,?,?)",
                                      (str(web_uid), amount, "Subscription activated", session_id, int(t.time())))
                        log.info("Web user %d subscribed", web_uid)
                except (ValueError, Exception) as e:
                    log.error("Web top-up webhook error: %s", e)

        elif etype == "customer.subscription.deleted":
            customer_id = obj.get("customer", "")
            if customer_id:
                with _db() as c:
                    c.execute("UPDATE platform_users SET tier='free' WHERE stripe_customer_id=?", (customer_id,))
                    c.execute("UPDATE wa_users SET tier='free' WHERE stripe_customer_id=?", (customer_id,))
                    c.execute("UPDATE users SET tier='free' WHERE stripe_customer_id=?", (customer_id,))
                log.info("Subscription cancelled for customer %s", customer_id)

        return jsonify({"status": "ok"})
    except Exception as e:
        log.error("Stripe webhook error: %s", e)
        return jsonify({"error": str(e)}), 500

# ── Facebook Messenger + Instagram DMs webhooks ───────────────────────────────

@app.route("/webhook/messenger", methods=["GET"])
def messenger_verify():
    if (request.args.get("hub.mode") == "subscribe" and
            request.args.get("hub.verify_token") == MESSENGER_VERIFY_TOKEN):
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook/messenger", methods=["POST"])
def messenger_receive():
    data = request.get_json()
    try:
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event.get("sender", {}).get("id", "")
                if not sender_id:
                    continue
                msg = event.get("message", {})
                if msg.get("is_echo"):
                    continue  # skip our own messages
                # Determine platform (Instagram vs Messenger by entry app)
                platform = "instagram" if entry.get("id", "").startswith("17") else "messenger"
                send_fn = lambda text, sid=sender_id: send_messenger(sid, text)
                msg_id = msg.get("mid", "")
                msg_time = int(event.get("timestamp", 0)) // 1000

                attachments = msg.get("attachments", [])
                if attachments:
                    att = attachments[0]
                    att_type = att.get("type", "")
                    payload = att.get("payload", {})
                    if att_type == "image":
                        img_url = payload.get("url", "")
                        img_bytes = None
                        if img_url:
                            try:
                                r = requests.get(img_url, timeout=15,
                                    headers={"User-Agent": "Mozilla/5.0"})
                                if r.ok and len(r.content) > 500:
                                    img_bytes = r.content
                            except Exception as e:
                                log.error("Messenger image download: %s", e)
                        threading.Thread(
                            target=_handle_platform_message,
                            args=(platform, sender_id, "image", None, send_fn),
                            kwargs={"image_bytes": img_bytes, "msg_id": msg_id, "msg_time": msg_time},
                            daemon=True
                        ).start()
                    elif att_type == "audio":
                        audio_url = payload.get("url", "")
                        audio_bytes = None
                        if audio_url:
                            try:
                                r = requests.get(audio_url, timeout=30,
                                    headers={"User-Agent": "Mozilla/5.0"})
                                if r.ok:
                                    audio_bytes = r.content
                            except Exception as e:
                                log.error("Messenger audio download: %s", e)
                        threading.Thread(
                            target=_handle_platform_message,
                            args=(platform, sender_id, "audio", None, send_fn),
                            kwargs={"audio_bytes": audio_bytes, "audio_mime": "audio/mpeg",
                                    "msg_id": msg_id, "msg_time": msg_time},
                            daemon=True
                        ).start()
                    else:
                        send_fn("⚠️ Unsupported attachment. Send text, image, voice note, or URL.")
                elif msg.get("text"):
                    threading.Thread(
                        target=_handle_platform_message,
                        args=(platform, sender_id, "text", msg["text"], send_fn),
                        kwargs={"msg_id": msg_id, "msg_time": msg_time},
                        daemon=True
                    ).start()
    except Exception as e:
        log.error("Messenger webhook error: %s", e)
    return jsonify({"status": "ok"}), 200


# ── Telegram webhook ──────────────────────────────────────────────────────────

@app.route("/webhook/telegram", methods=["POST"])
def telegram_receive():
    data = request.get_json()
    try:
        msg = data.get("message") or data.get("edited_message")
        if not msg:
            return jsonify({"status": "ok"}), 200
        chat_id = str(msg["chat"]["id"])
        msg_id = str(msg.get("message_id", ""))
        msg_time = int(msg.get("date", 0))
        send_fn = lambda text, cid=chat_id: send_telegram(cid, text)

        if "text" in msg:
            threading.Thread(
                target=_handle_platform_message,
                args=("telegram", chat_id, "text", msg["text"], send_fn),
                kwargs={"msg_id": msg_id, "msg_time": msg_time},
                daemon=True
            ).start()

        elif "photo" in msg:
            # Telegram sends multiple sizes; take the largest
            file_id = msg["photo"][-1]["file_id"]
            caption = msg.get("caption", "")
            def _tg_image(cid=chat_id, fid=file_id, cap=caption, sfn=send_fn, mid=msg_id, mt=msg_time):
                sfn("🖼 Analysing image...")
                img_bytes = _telegram_download(fid)
                _handle_platform_message("telegram", cid, "image", cap or None, sfn,
                                         image_bytes=img_bytes, msg_id=mid, msg_time=mt)
            threading.Thread(target=_tg_image, daemon=True).start()

        elif "voice" in msg or "audio" in msg:
            media = msg.get("voice") or msg.get("audio")
            file_id = media["file_id"]
            mime = media.get("mime_type", "audio/ogg")
            def _tg_audio(cid=chat_id, fid=file_id, m=mime, sfn=send_fn, mid=msg_id, mt=msg_time):
                sfn("🎤 Transcribing...")
                audio_bytes = _telegram_download(fid)
                _handle_platform_message("telegram", cid, "audio", None, sfn,
                                         audio_bytes=audio_bytes, audio_mime=m,
                                         msg_id=mid, msg_time=mt)
            threading.Thread(target=_tg_audio, daemon=True).start()

        elif "document" in msg:
            # Treat documents as text if small enough
            doc = msg["document"]
            caption = msg.get("caption", "")
            if doc.get("mime_type", "").startswith("text/") and doc.get("file_size", 0) < 50000:
                def _tg_doc(cid=chat_id, fid=doc["file_id"], cap=caption, sfn=send_fn, mid=msg_id, mt=msg_time):
                    raw = _telegram_download(fid)
                    text = raw.decode("utf-8", errors="ignore")[:2000] if raw else cap
                    _handle_platform_message("telegram", cid, "text", text or cap, sfn,
                                             msg_id=mid, msg_time=mt)
                threading.Thread(target=_tg_doc, daemon=True).start()
            else:
                send_fn("⚠️ Please send text, image, voice note, or URL to fact-check.")
        else:
            send_fn("⚠️ Send a text claim, image, voice note, or URL to get started.")

    except Exception as e:
        log.error("Telegram webhook error: %s", e)
    return jsonify({"status": "ok"}), 200


@app.route("/api/setup-telegram-webhook", methods=["POST"])
def setup_telegram_webhook():
    """Register the Telegram webhook URL. Call once after deployment."""
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN not set"}), 400
    webhook_url = f"{APP_BASE_URL}/webhook/telegram"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "edited_message"]},
            timeout=15
        )
        r.raise_for_status()
        return jsonify({"status": "ok", "webhook_url": webhook_url, "response": r.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/webhook/twitter", methods=["GET"])
def twitter_crc():
    """Twitter Account Activity API CRC challenge-response check."""
    crc_token = request.args.get("crc_token", "")
    if not crc_token:
        return "Missing crc_token", 400
    # Sign with consumer secret
    secret = TWITTER_CONSUMER_SECRET.encode() if TWITTER_CONSUMER_SECRET else b""
    sig = hmac.new(secret, crc_token.encode(), "sha256").digest()
    import base64
    response_token = "sha256=" + base64.b64encode(sig).decode()
    return jsonify({"response_token": response_token})


@app.route("/webhook/twitter", methods=["POST"])
def twitter_receive():
    """Twitter Account Activity API — receive DM events."""
    # Optional: verify Twitter signature header
    sig_header = request.headers.get("X-Twitter-Webhooks-Signature", "")
    if TWITTER_CONSUMER_SECRET and sig_header:
        import base64
        expected = "sha256=" + base64.b64encode(
            hmac.new(TWITTER_CONSUMER_SECRET.encode(), request.data, "sha256").digest()
        ).decode()
        if not hmac.compare_digest(sig_header, expected):
            log.warning("Twitter webhook signature mismatch")
            return "Forbidden", 403

    data = request.get_json(force=True) or {}
    try:
        for dm_event in data.get("direct_message_events", []):
            event_type = dm_event.get("type", "")
            if event_type != "message_create":
                continue
            msg_create = dm_event.get("message_create", {})
            sender_id = msg_create.get("sender_id", "")

            # Ignore messages sent by the bot itself
            if TWITTER_ACCESS_TOKEN and sender_id == TWITTER_ACCESS_TOKEN.split("-")[0]:
                continue

            msg_data = msg_create.get("message_data", {})
            text = msg_data.get("text", "").strip()
            attachment = msg_data.get("attachment", {})
            msg_id = dm_event.get("id", "")
            msg_time = int(dm_event.get("created_timestamp", 0)) // 1000

            send_fn = lambda txt, sid=sender_id: send_twitter_dm(sid, txt)

            if attachment.get("type") == "media":
                media = attachment.get("media", {})
                media_url = media.get("media_url_https", "") or media.get("media_url", "")
                media_type = media.get("type", "photo")
                if media_type == "photo" and media_url:
                    def _tw_image(sid=sender_id, url=media_url, cap=text, sfn=send_fn,
                                  mid=msg_id, mt=msg_time):
                        sfn("🖼 Analysing image...")
                        try:
                            img_resp = requests.get(url, timeout=30)
                            img_resp.raise_for_status()
                            img_bytes = img_resp.content
                        except Exception as e:
                            log.error("Twitter image download failed: %s", e)
                            img_bytes = None
                        _handle_platform_message("twitter", sid, "image", cap or None, sfn,
                                                 image_bytes=img_bytes, msg_id=mid, msg_time=mt)
                    threading.Thread(target=_tw_image, daemon=True).start()
                else:
                    # Video or animated GIF — pass URL as text claim
                    threading.Thread(
                        target=_handle_platform_message,
                        args=("twitter", sender_id, "text",
                              media_url or text or "(media)", send_fn),
                        kwargs={"msg_id": msg_id, "msg_time": msg_time},
                        daemon=True
                    ).start()
            elif text:
                threading.Thread(
                    target=_handle_platform_message,
                    args=("twitter", sender_id, "text", text, send_fn),
                    kwargs={"msg_id": msg_id, "msg_time": msg_time},
                    daemon=True
                ).start()

    except Exception as e:
        log.error("Twitter webhook error: %s", e)
    return jsonify({"status": "ok"}), 200


@app.route("/api/setup-twitter-webhook", methods=["POST"])
def setup_twitter_webhook():
    """Register the Twitter/X Account Activity API webhook. Call once after deployment."""
    if not all([TWITTER_CONSUMER_KEY, TWITTER_CONSUMER_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        return jsonify({"error": "Twitter credentials not set"}), 400
    webhook_url = f"{APP_BASE_URL}/webhook/twitter"
    url = "https://api.twitter.com/2/account_activity/webhooks"
    try:
        auth_header = _twitter_oauth1_header("POST", url)
        r = requests.post(
            url,
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            json={"url": webhook_url},
            timeout=15
        )
        if not r.ok:
            return jsonify({"error": r.text[:500], "status": r.status_code}), 400
        return jsonify({"status": "ok", "webhook_url": webhook_url, "response": r.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/contact", methods=["POST"])
def api_contact():
    data = request.get_json() or {}
    name    = (data.get("name") or "").strip()[:100]
    email   = (data.get("email") or "").strip()[:200]
    org     = (data.get("org") or "").strip()[:200]
    ctype   = (data.get("type") or "").strip()[:50]
    message = (data.get("message") or "").strip()[:2000]
    if not name or not email or "@" not in email:
        return jsonify({"error": "name and valid email required"}), 400
    try:
        with _db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS contact_requests
                         (id INTEGER PRIMARY KEY, name TEXT, email TEXT, org TEXT,
                          type TEXT, message TEXT, created_at INTEGER)""")
            c.execute("INSERT INTO contact_requests (name,email,org,type,message,created_at) VALUES (?,?,?,?,?,?)",
                      (name, email, org, ctype, message, int(__import__('time').time())))
    except Exception as e:
        log.error("contact DB error: %s", e)
    # Email notification via SendGrid
    try:
        import urllib.request, json as _json
        sg_key = os.environ.get("SENDGRID_API_KEY")
        if sg_key:
            payload = _json.dumps({
                "personalizations": [{"to": [{"email": "omartanveeraslam@gmail.com"}]}],
                "from": {"email": "hello@fredcheck.com", "name": "Fred Fact Check"},
                "subject": f"[Fred] Contact: {ctype} — {name}",
                "content": [{"type": "text/plain", "value": f"Name: {name}\nEmail: {email}\nOrg: {org}\nType: {ctype}\n\n{message}"}]
            }).encode()
            req = urllib.request.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=payload,
                headers={"Authorization": f"Bearer {sg_key}", "Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error("contact email error: %s", e)
    return jsonify({"ok": True})

@app.route("/privacy", methods=["GET"])
def privacy_policy():
    return send_from_directory("static", "privacy.html")

@app.route("/fred.vcf", methods=["GET"])
def contact_card():
    return send_from_directory("static", "Fred.vcf",
                               mimetype="text/vcard")

@app.route("/api/test", methods=["POST"])
def test_endpoint():
    """Dev testing endpoint — runs the full pipeline and returns formatted output + raw JSON.
    Usage: curl -X POST https://HOST/test -H 'Content-Type: application/json' \
             -d '{"claim": "...", "type": "text", "token": "VERIFY_TOKEN"}'
    """
    data = request.get_json(force=True) or {}
    if data.get("token") != VERIFY_TOKEN:
        return jsonify({"error": "forbidden"}), 403
    claim = data.get("claim", "").strip()
    source_type = data.get("type", "text")
    if not claim:
        return jsonify({"error": "claim required"}), 400
    try:
        results = _factcheck_pipeline(claim, source_type)
        first = results[0] if results else {}
        result = first.get("analysis", {})
        used_sources = first.get("sources_consulted", [])
        report = fmt_report(claim, result, source_type, 0, used_sources=used_sources)
        truncated = "_(message trimmed for length)_" in report
        return jsonify({
            "verdict": result.get("rating"),
            "confidence": result.get("confidence"),
            "rating_reason": result.get("rating_reason"),
            "truncated": truncated,
            "formatted_output": report,
            "raw": result,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"running","version":"v3.2","keys":{
        "whatsapp":bool(WHATSAPP_TOKEN),"google_fc":bool(GOOGLE_API_KEY),
        "anthropic":bool(ANTHROPIC_KEY),"openai":bool(OPENAI_API_KEY),
        "rapidapi":bool(RAPIDAPI_KEY),"messenger":bool(MESSENGER_PAGE_TOKEN),
        "telegram":bool(TELEGRAM_BOT_TOKEN),"stripe":bool(STRIPE_SECRET_KEY),
        "twitter":bool(TWITTER_CONSUMER_KEY)
    }}), 200

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
            profile_name = (value.get("contacts") or [{}])[0].get("profile", {}).get("name", "")
            log.info(f">>> Received {msg_type} from {from_num} ({profile_name or 'no name'})")
            if msg_type == "text": log.info(f"    Text: {msg.get('text',{}).get('body','')[:100]}")
            elif msg_type == "video": log.info(f"    Video ID: {msg.get('video',{}).get('id','')}")
            elif msg_type == "image": log.info(f"    Image ID: {msg.get('image',{}).get('id','')}")
            try:
                process(from_num, msg, profile_name=profile_name)
            except Exception as e:
                log.error(f"!!! Process exception: {e}")
                try: send(from_num, f"❌ Bot error: {str(e)[:200]}\n\nPlease try again.")
                except Exception: pass
    except (KeyError, IndexError) as e: log.warning(f"Parse error: {e}")
    except Exception as e: log.error(f"Webhook error: {e}")
    return jsonify({"status":"ok"}), 200

# ── Admin QC testing endpoints ────────────────────────────────────────────────
# Simulates a real WhatsApp message through the full process() → run_check() pipeline
# but captures send() output instead of hitting Meta's API.
_QC_ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "qc-test-fred-2026")

def _qc_worker(from_num, msg_text):
    """Background thread: runs full WhatsApp flow for QC testing."""
    import uuid as _uuid
    try:
        # Step 1: Send the URL/text message — process() will extract content,
        # identify claims, and set up pending with Y/N confirmation prompt
        fake_msg1 = {
            "id": f"qctest_{_uuid.uuid4().hex}",
            "timestamp": str(int(t.time())),
            "type": "text",
            "from": from_num,
            "text": {"body": msg_text}
        }
        process(from_num, fake_msg1)

        # Step 2: Auto-confirm with Y (replicate user pressing Y)
        # Wait briefly for pending to be populated
        for _ in range(20):
            t.sleep(0.5)
            pkey = ("whatsapp", from_num)
            with pending_lock:
                if pkey in pending:
                    break

        fake_msg2 = {
            "id": f"qctest_{_uuid.uuid4().hex}",
            "timestamp": str(int(t.time())),
            "type": "text",
            "from": from_num,
            "text": {"body": "Y"}
        }
        process(from_num, fake_msg2)

        # Step 3: Wait for run_check() to finish (max 3 min)
        # Detect completion by watching for verdict keywords in last message
        deadline = t.time() + 180
        last_count = 0
        idle_rounds = 0
        while t.time() < deadline:
            t.sleep(8)
            with _qc_lock:
                msgs = _qc_jobs[from_num]["messages"]
                cur_count = len(msgs)
            if cur_count == last_count:
                idle_rounds += 1
                # Only give up after 90s of silence (run_check API calls can take 60s+)
                if idle_rounds >= 12 and cur_count > 3:
                    break
            else:
                idle_rounds = 0
                last_count = cur_count
                # Check if latest message looks like the final fact-check report
                with _qc_lock:
                    last_msg = _qc_jobs[from_num]["messages"][-1] if _qc_jobs[from_num]["messages"] else ""
                if "Fred Check" in last_msg or "•  Fred" in last_msg or "FactCheck Pro v3" in last_msg:
                    t.sleep(8)  # allow any trailing multi-claim messages to arrive
                    with _qc_lock:
                        # For multi-claim jobs, only stop if last N messages all have the footer
                        recent = _qc_jobs[from_num]["messages"][-1]
                    if "Fred Check" in recent or "•  Fred" in recent or "FactCheck Pro v3" in recent:
                        break
    except Exception as e:
        log.error("QC worker error: %s", e)
        with _qc_lock:
            _qc_jobs[from_num]["error"] = str(e)
    finally:
        with _qc_lock:
            _qc_jobs[from_num]["done"] = True
        log.info("QC job done: %s (%d messages)", from_num, len(_qc_jobs[from_num]["messages"]))

@app.route("/admin/daily-summary", methods=["POST"])
def admin_daily_summary():
    """Manually trigger the daily summary email. Body: {"date": "YYYY-MM-DD"} optional."""
    if request.headers.get("X-Admin-Token", "") != _QC_ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 403
    date_str = (request.get_json() or {}).get("date")
    threading.Thread(target=_send_daily_summary, args=(date_str,), daemon=True).start()
    return jsonify({"ok": True, "date": date_str or "yesterday"})


@app.route("/admin/qc", methods=["POST"])
def admin_qc_start():
    """Start a QC test run. Returns job_id to poll for results."""
    if request.headers.get("X-Admin-Token", "") != _QC_ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json() or {}
    msg_text = (data.get("message") or "").strip()
    if not msg_text:
        return jsonify({"error": "No message provided"}), 400

    import uuid as _uuid
    job_id = _uuid.uuid4().hex[:12]
    from_num = f"qctest_{job_id}"
    with _qc_lock:
        _qc_jobs[from_num] = {"messages": [], "done": False, "error": None, "_input": msg_text}

    threading.Thread(target=_qc_worker, args=(from_num, msg_text), daemon=True).start()
    log.info("QC job started: %s — %s", job_id, msg_text[:80])
    return jsonify({"job_id": job_id, "from_num": from_num, "status": "processing"})

@app.route("/admin/qc/<job_id>", methods=["GET"])
def admin_qc_status(job_id):
    """Poll QC job status and retrieve captured messages."""
    if request.headers.get("X-Admin-Token", "") != _QC_ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 403
    from_num = f"qctest_{job_id}"
    with _qc_lock:
        job = _qc_jobs.get(from_num)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job_id,
        "done": job["done"],
        "error": job["error"],
        "message_count": len(job["messages"]),
        "messages": job["messages"]
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info("Fred Check v3.2 starting (dev mode)...")
    app.run(host="0.0.0.0", port=port, debug=False)
