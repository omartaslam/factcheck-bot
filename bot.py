"""FactCheck Pro v3.2 - Enhanced Video Analysis"""
import os, base64, json, logging, tempfile, threading, requests, re
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
WHATSAPP_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
GOOGLE_FC_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
COBALT_API = "https://api.cobalt.tools/api/json"
COBALT_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

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

def ocr_image(b):
    try:
        b64 = base64.b64encode(b).decode()
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
                {"type":"text","text":"Extract ALL text verbatim from this image. Then in 2 sentences describe what it depicts. Note any signs of manipulation."}
            ]}]}, timeout=30)
        r.raise_for_status(); return r.json()["content"][0]["text"].strip()
    except Exception as e: log.error("OCR: %s", e); return ""

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
            os.unlink(path); r.raise_for_status()
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
    """Chain RapidAPI video downloader services — FB, IG, TikTok, YT, Twitter etc."""
    if not RAPIDAPI_KEY:
        log.warning("RAPIDAPI_KEY not set — skipping RapidAPI downloaders")
        return None, ""

    apis = [
        # 1. coder2077 - Auto Download All In One (IG, TikTok, YT, Twitter, FB)
        {
            "host": "instagram-tiktok-youtube-downloader.p.rapidapi.com",
            "url": "https://instagram-tiktok-youtube-downloader.p.rapidapi.com/get-info",
            "params": {"url": url},
        },
        # 2. GoDownloader - TikTok, Instagram, Twitter/X
        {
            "host": "tiktok-download-video-no-watermark.p.rapidapi.com",
            "url": "https://tiktok-download-video-no-watermark.p.rapidapi.com/analysis",
            "params": {"url": url, "hd": "1"},
        },
    ]

    for api in apis:
        try:
            headers = {
                "x-rapidapi-key": RAPIDAPI_KEY,
                "x-rapidapi-host": api["host"],
            }
            log.info(f"Trying RapidAPI: {api['host']}")
            r = requests.get(api["url"], headers=headers, params=api["params"], timeout=25)
            log.info(f"RapidAPI {api['host']} status: {r.status_code}")
            if not r.ok:
                log.warning(f"RapidAPI {api['host']} returned {r.status_code}: {r.text[:200]}")
                continue
            data = r.json()
            log.info(f"RapidAPI {api['host']} response: {str(data)[:300]}")
            video_url, title = _extract_video_url(data)
            if video_url:
                content = _try_download_url(video_url, api["host"])
                if content:
                    return content, title
            log.warning(f"RapidAPI {api['host']}: no usable video URL found")
        except Exception as e:
            log.error(f"RapidAPI {api['host']} failed: {e}")
            continue

    return None, ""


def _ytdlp_download(url):
    """yt-dlp with spoofed headers — works for YouTube, Twitter/X, TikTok, FB (public posts), Instagram (public)."""
    try:
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

def _og_metadata(url):
    """Last resort: extract Open Graph tags (title, description) from the page."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        r.raise_for_status(); html = r.text; parts = []
        for prop in ("og:title","og:description","twitter:title","twitter:description"):
            m = re.search(rf'<meta[^>]+(?:property|name)=["\']?{re.escape(prop)}["\']?[^>]+content=["\']([^"\']+)["\']', html, re.I)
            if not m:
                m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']?{re.escape(prop)}["\']?', html, re.I)
            if m: parts.append(m.group(1).strip())
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

def scrape_sites(query):
    q = quote_plus(query[:100])
    sites = [("Snopes",f"https://www.snopes.com/?s={q}"),("FullFact",f"https://fullfact.org/search/?q={q}"),("FactCheck.org",f"https://www.factcheck.org/?s={q}"),("PolitiFact",f"https://www.politifact.com/search/?q={q}"),("AFP",f"https://factcheck.afp.com/?q={q}")]
    out = []
    for name, url in sites:
        txt = fetch(url, timeout=8)
        if txt and len(txt) > 150: out.append(f"[{name}]: {txt[:400]}")
    return "\n\n".join(out)

def estimate_cost(st):
    base = {"text":0.0085,"url":0.0095,"image":0.0110,"audio":0.0120,"video":0.0180,"document":0.0095}
    return base.get(st, 0.0085)

def claude_analyse(claim, google, scraped, st):
    g = "\n".join([f"• {x['source']} [{x['rating']}]: {x['claim']}\n  {x['url']}" for x in google[:5]])
    prompt = (
        f"Fact-check this claim (source: {st}).\n\nCLAIM:\n\"\"\"{claim[:800]}\"\"\"\n\n"
        f"GOOGLE FACT CHECK:\n{g or 'No matches.'}\n\nFACT-CHECK SITES:\n{scraped[:1500] or 'No results.'}\n\n"
        f"Respond ONLY with valid JSON:\n"
        f'{{"rating":"TRUE|MOSTLY TRUE|HALF TRUE|MOSTLY FALSE|FALSE|PANTS ON FIRE|UNVERIFIABLE|MISLEADING|NEEDS CONTEXT",'
        f'"verdict":"2-3 sentence verdict with evidence","key_facts":["fact1","fact2","fact3","fact4"],'
        f'"context":"background context","red_flags":["flag1","flag2"],"media_bias":"bias note or empty",'
        f'"sources":["Name — URL","Name — URL","Name — URL","Name — URL"],"confidence":"HIGH|MEDIUM|LOW","confidence_reason":"reason"}}'
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-sonnet-4-6","max_tokens":2000,"system":SYSTEM,"messages":[{"role":"user","content":prompt}]},
            timeout=45)
        r.raise_for_status(); text = r.json()["content"][0]["text"]
        s = text.find("{"); e = text.rfind("}") + 1
        if s >= 0 and e > s: return json.loads(text[s:e])
    except Exception as e: log.error("Claude: %s", e)
    return {"rating":"UNVERIFIABLE","verdict":"Analysis failed.","key_facts":[],"context":"","red_flags":[],"media_bias":"","sources":["Google FC — https://toolbox.google.com/factcheck/explorer","Snopes — https://www.snopes.com","FullFact — https://fullfact.org"],"confidence":"LOW","confidence_reason":"Unavailable"}

def fmt_report(claim, a, st, cost):
    rating = a.get("rating", "UNVERIFIABLE").upper()
    src_word = {"text":"Text","image":"Image","audio":"Voice","video":"Video","url":"Article","document":"Document"}
    badge_map = {"TRUE":"✅  VERDICT: TRUE","MOSTLY TRUE":"🟢  VERDICT: MOSTLY TRUE","HALF TRUE":"🟡  VERDICT: HALF TRUE","MOSTLY FALSE":"🟠  VERDICT: MOSTLY FALSE","FALSE":"❌  VERDICT: FALSE","PANTS ON FIRE":"🔥  VERDICT: PANTS ON FIRE","UNVERIFIABLE":"❓  VERDICT: UNVERIFIABLE","MISLEADING":"⚠️  VERDICT: MISLEADING","NEEDS CONTEXT":"📌  VERDICT: NEEDS CONTEXT"}
    badge = badge_map.get(rating, f"VERDICT: {rating}")
    lines = [f"*FACTCHECK PRO*  |  {src_word.get(st,'Text')}","",f"*{badge}*",meter_visual(rating),"","*CLAIM*",f"_{claim[:280]}_","","*ANALYSIS*",a.get("verdict",""),""]
    if a.get("key_facts"): lines += ["*KEY FACTS*"] + [f"{i}. {f}" for i,f in enumerate(a["key_facts"][:4],1)] + [""]
    if a.get("context"): lines += ["*BACKGROUND*", a["context"][:400], ""]
    if a.get("red_flags"): lines += ["*RED FLAGS*"] + [f"• {f}" for f in a["red_flags"][:3]] + [""]
    if a.get("media_bias"): lines += ["*BIAS NOTE*", a["media_bias"][:200], ""]
    conf = a.get("confidence","LOW")
    conf_icon = {"HIGH":"🟢","MEDIUM":"🟡","LOW":"🔴"}.get(conf,"")
    lines += [f"*CONFIDENCE*  {conf_icon} {conf}", f"_{a.get('confidence_reason','')[:200]}_",""]
    if a.get("sources"): lines += ["*SOURCES*"] + [f"• {s}" for s in a["sources"][:5]] + [""]
    lines += ["─────────────────────────────",f"_Cost: ${cost:.4f}  •  FactCheck Pro v3.2_","_Snopes • FullFact • PolitiFact • AFP_"]
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

def run_check(from_num, query, st, img_bytes, cost):
    send(from_num, "⚙️ Cross-referencing Snopes, FullFact, PolitiFact, AFP, FactCheck.org, Google FC...")
    g = google_fc(query); sc = scrape_sites(query)
    a = claude_analyse(query, g, sc, st)
    send(from_num, fmt_report(query, a, st, cost))

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
            video_domains = ["tiktok.com","youtube.com","youtu.be","twitter.com","x.com","instagram.com","facebook.com","fb.watch","rumble.com","bitchute.com","t.me"]
            is_video_link = any(d in url for d in video_domains)
            if is_video_link:
                # Detect Facebook private/login-gated links upfront
                fb_private = ("facebook.com/share" in url or "fb.watch" in url)
                if fb_private:
                    send(from_num, "Facebook private links require login. Please use a public Facebook URL, or try YouTube/TikTok/Instagram/Twitter instead.")
                    return
                try:
                    send(from_num, "🎬 Downloading video from URL...")
                    video_bytes, metadata = download_video_url(url)
                    if video_bytes:
                        send(from_num, f"✓ Downloaded ({len(video_bytes)//1024}KB)")
                        parts = []
                        if metadata: parts.append(f"Video: {metadata}")
                        send(from_num, "🎧 Transcribing audio...")
                        try:
                            transcript = transcribe(video_bytes, "video/mp4")
                            if transcript:
                                parts.append(f"Audio: {transcript}")
                                send(from_num, "✓ Got transcript")
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
                send(from_num, "Fetching article..."); query = fetch(url) or body; source_type = "url"
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
        send(from_num, "⚠️ VIDEO PROCESSING TEMPORARILY DISABLED\n\nFor now, please:\n\n• Send the video URL (TikTok/YouTube/Facebook/Instagram)\n• Or take a screenshot and send as image\n• Or describe the claim in text")
        log.warning("Video upload attempted - currently disabled"); return
    elif msg_type == "document":
        send(from_num, "📄 Reading..."); b = download_media(message["document"]["id"])
        if b: query = b.decode("utf-8", errors="ignore")[:2000]
        source_type = "document"
        if not query: send(from_num, "⚠️ Could not read."); return
    else:
        send(from_num, f"⚠️ Unsupported: {msg_type}"); return
    if not query: send(from_num, "⚠️ Could not extract content."); return
    query = clean_ocr(query) if source_type == "image" else query
    query = query.strip()[:800]
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
    missing = [k for k,v in {"WHATSAPP_TOKEN":WHATSAPP_TOKEN,"PHONE_NUMBER_ID":PHONE_NUMBER_ID,"GOOGLE_FACT_CHECK_API_KEY":GOOGLE_API_KEY,"ANTHROPIC_API_KEY":ANTHROPIC_KEY}.items() if not v]
    if missing: raise ValueError(f"Missing: {', '.join(missing)}")
    log.info("FactCheck Pro v3.2 starting...")
    app.run(host="0.0.0.0", port=5000, debug=False)
