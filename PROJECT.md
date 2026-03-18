# FactCheck Pro — Project Handover Document

> **Last updated:** 2026-03-18 (session 7)
> **Version:** v3.4 BETA
> **Status:** Live on Railway — 3/3 automated test claims passing with no truncation; verdict quality and claim-origin philosophy fixed

---

## 0. For AI Assistants Picking Up This Project

Read this section first. It captures hard-won rules that are not obvious from the code.

### Collaboration rules

- **Always read the relevant code before proposing changes.** Identify the root cause, explain the fix in plain English, and ask for confirmation before editing `bot.py`. This rule exists because rushed changes have twice caused cascading bugs (a source name rename broke `_SOURCE_PERSPECTIVE` lookup → 3× UNVERIFIABLE verdicts; a default parameter change was bypassed by a hardcoded call site).
- Exception: if the user says "yes go ahead" or "do both", proceed without further confirmation for that specific change.

### Verdict philosophy

- **TRUE means TRUE.** Never downgrade a verdict from TRUE to LIKELY TRUE because search returned thin or unnamed sources, if the claim is factually correct. The audience cares about truth and reality — Fred must reflect reality, not over-hedge on retrieval gaps.
- If search quality is the problem, the fix is to improve retrieval — not to downgrade the verdict.
- Defensive hedging that produces LIKELY TRUE for a demonstrably TRUE claim is a failure mode, not a safety feature.

### Key verdict logic rules (in `synth_prompt`)

1. **Omissions**: if omitted context strengthens the claim → don't downgrade; if it misleads/weakens → downgrade; if neutral → don't downgrade
2. **UNVERIFIABLE**: only when the claim genuinely cannot be assessed at all — not for partial evidence
3. **Breaking news**: Tavily live aggregation is sufficient for TRUE — no penalty for Western outlet publication lag
4. **Superlatives**: sources confirming "first/only" without contradiction = sufficient for TRUE
5. **Western bias**: absence of Reuters/AP/BBC must never downgrade rating or confidence
6. **Con-side debate**: only influences rating if it finds direct contradictions — not omissions that happen to strengthen the claim

### Known footguns

- `grouped[:N]` in `claude_analyse()` — the evidence string passed to Claude. Was 2000 chars (caused persistent MEDIUM confidence because named outlet snippets were cut). Now 10000. Do not reduce.
- `_SOURCE_PERSPECTIVE` dict must stay in sync with any source rename. If you rename a source (e.g. "Tavily Summary" → "Live Web Search"), add the new name to the dict or it falls through to "OTHER SOURCES" and Claude stops treating it as primary evidence.
- `pending{}` is in-memory only — lost on every redeploy. Users mid-flow will get "no pending check" errors after a deploy. Known issue, fix deferred.
- `source_content` parameter in `claude_analyse()` is the primary evidence for URL/video fact-checks. Without it, scrape_sites returns mostly 403/404 and verdicts default to UNVERIFIABLE. Do not remove.
- Two claim extraction functions exist (`assess_content_claims` and `extract_claims`) — keep prompt rules in sync across both.

### Source policy

Only include editorially independent sources with a track record of correcting errors. State-controlled outlets (RT, Sputnik, CGTN, Xinhua, Press TV) are excluded regardless of reach — they actively produce disinformation on geopolitical topics and pollute verdicts.

Al Jazeera is state-funded (Qatar) but editorially independent with a corrections policy — it stays.

---

## 1. What This Project Is

FactCheck Pro is a WhatsApp bot that fact-checks claims sent by users via WhatsApp message. Users send text, images, audio, documents, or URLs (articles, Facebook, Instagram, TikTok, YouTube, X/Twitter). The bot:

1. Extracts verifiable claims from the content (before asking user to confirm)
2. Shows the user a numbered list of claims and asks for confirmation (Y/N)
3. Researches each claim independently using Google Fact Check API + scraped fact-check sources + Tavily real-time search
4. Runs OSINT verification in parallel (reverse image search, EXIF, Wayback Machine, AI/deepfake detection)
5. Returns a per-claim verdict with evidence, sources, OSINT findings, and a truth rating

The target audience is people who want to quickly verify claims circulating on WhatsApp — particularly around politics and Middle East conflicts.

---

## 2. Architecture

```
WhatsApp Business API
        ↓ webhook POST /webhook
    Flask (bot.py)
        ↓
    [process() handler]
        ├── New user? → send welcome message (falls through)
        ├── HELP/START/INFO? → send HELP_MSG
        ├── Download media (WhatsApp API)
        ├── OCR / transcribe / frame-extract
        ├── assess_content_claims() → Sonnet → claims list
        ├── Store in pending{} dict → send claims_confirm_msg to user
        └── On "Y" reply → run_check() per claim
                ├── run_osint() [parallel thread]
                │   ├── extract_exif_info() — image EXIF metadata
                │   ├── wayback_earliest() — Wayback Machine CDX API
                │   ├── tineye_search() — reverse image search
                │   └── hive_ai_check() — AI-generated / deepfake detection
                ├── google_fc(claim) → Google Fact Check API
                ├── scrape_sites(claim) → multi-source scrape (65 sources)
                ├── tavily_search(claim) → real-time news
                └── claude_analyse() → Sonnet → verdict + OSINT context
```

**Deployment:** Railway (PaaS), auto-deploy from GitHub `main` branch
**Process manager:** Gunicorn, 4 workers, `--timeout 120`
**Build config:** `Dockerfile` (python:3.11-slim + apt: ffmpeg, libsm6, libxext6, libxrender-dev). nixpacks.toml kept but Railway now uses Dockerfile.

---

## 3. Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.13 |
| Web framework | Flask + Gunicorn |
| AI (analysis + claims) | Anthropic `claude-sonnet-4-6` |
| AI (OCR, image) | Anthropic `claude-haiku-4-5-20251001` |
| Audio transcription | OpenAI Whisper (`whisper-1`) |
| Video download | yt-dlp + vikas5914 RapidAPI |
| Video frame extraction | cv2 (OpenCV) + ffmpeg fallback |
| Real-time search | Tavily API (advanced depth) + Brave Search API |
| Real-time AI search | Perplexity Sonar (code ready, activate with `PERPLEXITY_API_KEY`) |
| Fact-check API | Google Fact Check Tools API |
| OSINT — reverse image | Google Cloud Vision web detection (`GOOGLE_VISION_KEY`) — primary; TinEye kept as fallback |
| OSINT — AI/deepfake | Hive Moderation API (`HIVE_API_KEY`) |
| OSINT — EXIF | Pillow / piexif |
| OSINT — Wayback | Wayback Machine CDX API (free, no key needed) |
| Database | SQLite (persisted on Railway Volume at `/data/factcheck.db`) |
| Scheduling | APScheduler (WhatsApp token auto-refresh every 50 days) |
| Platform | WhatsApp Business API (Meta Graph API v19.0) |

---

## 4. Key Files

```
whatsapp-factcheck/
├── bot.py                  ← ENTIRE application logic (3400+ lines)
├── requirements.txt        ← Python dependencies
├── nixpacks.toml           ← Railway build config (apt packages + start command)
├── test_comprehensive.py   ← Integration test suite (24 unit + 36+ live tests)
├── static/
│   └── index.html          ← Landing page with WhatsApp link (wa.me/447863795638)
├── v1/, v2/, v3/           ← Legacy versions (ignore)
└── PROJECT.md              ← This file
```

All logic is in `bot.py`. There is no separate config file — all configuration comes from environment variables.

---

## 5. Environment Variables (Railway)

### Required (bot won't work without these)

| Variable | Description |
|---|---|
| `WHATSAPP_TOKEN` | Meta Graph API bearer token (auto-refreshed every 50 days) |
| `PHONE_NUMBER_ID` | WhatsApp Business phone number ID |
| `VERIFY_TOKEN` | Webhook verification token (default: `factcheck_verify_123`) |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `OPENAI_API_KEY` | OpenAI API key for Whisper transcription |
| `GOOGLE_FACT_CHECK_API_KEY` | Google Fact Check Tools API key |
| `RAPIDAPI_KEY` | RapidAPI key for vikas5914 video downloader |

### Persistence & Admin

| Variable | Value | Description |
|---|---|---|
| `DB_PATH` | `/data/factcheck.db` | SQLite path — must be on Railway Volume |
| `ADMIN_NUMBER` | `34643994740` | WhatsApp number for admin alerts (no + prefix) |
| `FREE_CHECKS_LIMIT` | `9999` (testing) / `5-10` (beta) / `3` (launch) | Free checks per user before paywall |
| `PROFIT_MARGIN` | `2.0` | Cost multiplier for billing (2.0 = 100% margin) |
| `APP_BASE_URL` | `https://web-production-1f0a4.up.railway.app` | Used for webhook URLs |
| `BETA_MODE` | `true` | Shows BETA label in report footer, beta welcome message |
| `MAX_VIDEO_MINUTES` | `10` | Max video duration — rejects longer videos with friendly message |

### Real-time Search

| Variable | Description | Status |
|---|---|---|
| `TAVILY_API_KEY` | Tavily advanced search — free 1000/month basic, advanced depth used | ✅ Set |
| `BRAVE_API_KEY` | Brave Search API — $5 free credit/month (~1000 queries) | ✅ Set |
| `PERPLEXITY_API_KEY` | Perplexity Sonar — real-time AI search, bridges Claude Aug-2025 cutoff (~$0.005/query, no free tier) | ❌ Hold for post-beta |

### OSINT (Optional — fully functional without these, features just disabled)

| Variable | Description | Status |
|---|---|---|
| `GOOGLE_VISION_KEY` | Google Cloud Vision web detection — primary reverse image search (~$0.0015/search, 1000 free/month) | ✅ Set |
| `REVERSE_IMAGE_ENGINE` | `"google"` \| `"tineye"` \| `"off"` — auto-selects based on keys present | auto |
| `TINEYE_API_SECRET` | TinEye reverse image search — fallback, set `REVERSE_IMAGE_ENGINE=tineye` to use | ❌ No credits (buy at tineye.com/services) |
| `HIVE_API_KEY` | Hive V3 API — AI-generated + deepfake detection | ✅ Set |

EXIF and Wayback Machine checks run without any API key.

### Monetisation (Stripe — NOT YET SET UP)

| Variable | Description |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `TOPUP_5_LINK` | Stripe Payment Link for $5 top-up |
| `TOPUP_10_LINK` | Stripe Payment Link for $10 top-up |
| `TOPUP_25_LINK` | Stripe Payment Link for $25 top-up |
| `SUB_LINK` | Stripe Payment Link for $9.99/month subscription |

### Multi-Platform (NOT YET ACTIVE)

| Variable | Description |
|---|---|
| `MESSENGER_PAGE_TOKEN` | Facebook Page token (Messenger + Instagram DMs) |
| `MESSENGER_VERIFY_TOKEN` | Messenger webhook verify token |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TWITTER_CONSUMER_KEY` | Twitter/X app consumer key (requires paid dev tier) |
| `TWITTER_CONSUMER_SECRET` | Twitter/X app consumer secret |
| `TWITTER_ACCESS_TOKEN` | Twitter/X bot access token |
| `TWITTER_ACCESS_SECRET` | Twitter/X bot access token secret |

### Source Toggles (all set to `true` in Railway as of 2026-03-16)

Every fact-check source can be enabled/disabled from Railway without code changes:

```
# Western fact-checkers
SRC_SNOPES, SRC_FULLFACT, SRC_FACTCHECKORG, SRC_POLITIFACT, SRC_AFP

# Regional / Middle East
SRC_ALJAZEERA, SRC_MEE, SRC_NOVARA, SRC_CANARY, SRC_ZETEO,
SRC_MEMO, SRC_NEWARAB, SRC_BTSELEM, SRC_BELLINGCAT, SRC_HRW,
SRC_AMNESTY, SRC_UNNEWS, SRC_HAARETZ, SRC_ARABNEWS, SRC_GRAYZONE,
SRC_MINTPRESS, SRC_INTERCEPT, SRC_DEMOCRACYNOW, SRC_DDN, SRC_CODEPINK,
SRC_OWENJONES, SRC_CORBYN, SRC_ZARASULTANA, SRC_FINKELSTEIN, SRC_MOATS,
SRC_GALLOWAY_SITE, SRC_PSC, SRC_972MAG, SRC_MONDOWEISS, SRC_EINTIFADA,
SRC_RESPSTATECRAFT, SRC_YENISAFAK, SRC_ANADOLU, SRC_ALMONITOR, SRC_DAWN

# Western mainstream
SRC_BBC, SRC_REUTERS, SRC_AP, SRC_GUARDIAN, SRC_CNN

# Global South fact-checkers (added 2026-03-16)
SRC_MISBAR        ← Misbar (MENA/Arabic)
SRC_FATABYYANO    ← Fatabyyano (MENA/Arabic)
SRC_VERIFYSY      ← Verify-Sy (Syria/Arabic)
SRC_AFRICACHECK   ← Africa Check (Sub-Saharan Africa)
SRC_PESACHECK     ← PesaCheck (East Africa)
SRC_DUBAWA        ← Dubawa (West Africa)
SRC_ALTNEWS       ← Alt News (India)
SRC_BOOMLIVE      ← Boom Live (India)
SRC_RAPPLER       ← Rappler (Philippines)
SRC_CHEQUEADO     ← Chequeado (Latin America)
SRC_LOGICALLY     ← Logically Facts (Global)
```

**Total: 65 sources.** Set any to `"false"` in Railway to disable.

Custom sources without code changes:
```
CUSTOM_SOURCES="Name|https://site.com/search?q={q},Name2|https://site2.com/?s={q}"
```

---

## 6. Railway Infrastructure

| Item | Value |
|---|---|
| Platform | Railway (railway.app) |
| Project name | enchanting-wholeness |
| Project ID | `ec1bd295-60a7-4d3c-b2ea-00bfc5b10e79` |
| Service ID | `3ae3bd52-301e-4003-b2cd-291436c7af2d` |
| Environment ID | `ebb5147d-8292-4b55-bd76-6a2c1b3e6564` |
| Volume | `web-volume` mounted at `/data` |
| Personal API token | `bc2d9c22-2d89-458c-8c33-3635a57193c7` |
| Project API token | `a150de81-9f32-42e3-acba-b0369b041ae3` |
| Live URL | `https://web-production-1f0a4.up.railway.app` |
| WhatsApp number | `wa.me/447863795638` |
| GitHub repo | `https://github.com/omartaslam/factcheck-bot` (main branch) |

Auto-deploy: every push to `main` triggers a Railway redeploy.

---

## 7. Core Bot Flow

### New user + commands

```python
# process() at start of every message:
if _is_new_wa_user(wa_id):
    send(from_num, _welcome_msg())   # mentions free checks + BETA, falls through

if body_upper in ("HELP", "?", "START", "INFO"):
    send(from_num, HELP_MSG)
    return
```

### Incoming message → claim extraction → confirmation

```
1. Receive WhatsApp webhook POST /webhook → process() handler
2. Media download: WhatsApp API → bytes (image/audio/video/document)
3. Content extraction:
   - Image/document → Claude Haiku OCR → text
   - Audio/video     → OpenAI Whisper transcription → text
   - Video URL       → _get_video_duration() pre-check → yt-dlp download
                       → cv2/ffmpeg frames + Whisper transcript
   - Article URL     → requests HTML scrape → text
   - FB/IG post URL  → yt-dlp skip_download + OG scrape → post text + image
   - Twitter/X URL   → fxtwitter API → post text
4. assess_content_claims(text, source_type) — Sonnet call
   → returns {claims: [...], checkable: bool, reason: str, suggestions: [...]}
5. If not checkable: send no_claims_msg() → done
6. Store in pending{wa_id} dict, send claims_confirm_msg() to user
7. User replies with claim numbers (e.g. "1,3"), "ALL", or "N"
8. On selection: pop from pending{}, spawn background thread → run_check()
```

### pending{} dict — the confirmation state machine

This is in-memory (not persisted). Key = WhatsApp `wa_id` (phone number without `+`).

```python
pending[wa_id] = {
    "claims":      [...],        # full list of extracted claims
    "query":       "...",        # full extracted content (source article, transcript, etc.)
    "source_type": "url",        # text / image / audio / video / url / document
    "img_bytes":   b"...",       # image bytes for OSINT (or None)
    "source_url":  "https://...",# original URL for OSINT Wayback check (or "")
    "cost":        0.0004,       # estimated cost per claim in dollars
    "billing_type":"free",       # free / credited / subscribed
    "post_date":   "2026-01-15", # post date if known (for staleness detection)
    "msg_id":      "wamid.xxx",  # WhatsApp message ID of user's original message (for reactions)
}
```

User reply parsing:
- `"Y"` / `"YES"` / `"ALL"` → check all claims
- `"1"` / `"1,2"` / `"1 3"` → check selected claim numbers
- `"N"` / `"NO"` → cancel

### Two LLM calls for claim extraction

**Important:** There are TWO separate claim extraction paths depending on source type:

| Function | Used for | Model |
|---|---|---|
| `assess_content_claims(text, source_type)` | All content types — initial claim extraction before user confirmation | Sonnet |
| `extract_claims(text, source_type)` | Called inside `run_check()` when `pre_claims` is not provided — fallback path only | Sonnet |

In normal flow `pre_claims` is always provided (from `pending{}`) so `extract_claims` is rarely called. Both functions must be kept in sync — any prompt rule change (e.g. metadata claim exclusion) must be applied to **both**.

### run_check() — the fact-check engine

```
Runs in a background thread (threading.Thread). Returns nothing — sends WhatsApp messages directly.

1. Send status: "⚙️ Cross-referencing N sources: ..." (+ OSINT line if applicable)
2. Start OSINT in nested thread: run_osint(image_bytes, source_url) via ThreadPoolExecutor
3. For each selected claim:
   a. google_fc(claim)        → Google Fact Check API
   b. scrape_sites(claim)     → parallel scrape of up to 65 sources (9-15s)
   c. tavily_search(claim)    → Tavily real-time search
   d. osint = osint_future.result(timeout=25)  # collected once, reused for all claims
   e. claude_analyse(claim, google, scraped, source_type,
                     post_date, osint, source_content)  → Sonnet verdict
   f. send verdict to user
4. After all claims: send_reaction(from_num, msg_id, verdict_emoji)
```

**Critical — source_content parameter:** `claude_analyse()` receives `source_content` (= the full extracted article/transcript from Step 4 of the message flow). This is the PRIMARY evidence for URL and video fact-checks. Without it, Claude only sees `scrape_sites` output which is ~95% 403/404/timeout — causing systematic UNVERIFIABLE verdicts. Do not remove this.

### Free check billing flow

```python
# After selection confirmed:
"✓ Free check — 3 free checks remaining after this"
# When last free check:
"ℹ️ This is your last free check. Reply HELP for info on continuing after this."
# After free checks exhausted → paywall message with Stripe links
```

### Supported content types

| Type | Processing |
|---|---|
| Text | Direct claim extraction |
| Image | Claude Haiku OCR → text → claims |
| Audio (voice note) | Whisper transcription → text → claims |
| Document | Haiku OCR → text → claims |
| URL (article) | HTML scrape → text → claims |
| URL (YouTube/TikTok/FB Reel/IG Reel) | yt-dlp download → cv2/ffmpeg frames → yt-dlp audio → Whisper → claims |
| URL (FB/IG non-video post) | yt-dlp skip_download + OG scrape → post text → claims |
| URL (X/Twitter) | fxtwitter API → post text → claims |

---

## 8. OSINT Pipeline (added 2026-03-16)

Runs in parallel with source scraping via `ThreadPoolExecutor`. Zero added latency in most cases.

### Functions

| Function | What it does | API key needed? |
|---|---|---|
| `extract_exif_info(image_bytes)` | Date taken, GPS, camera, edit software from EXIF | No |
| `wayback_earliest(url)` | Earliest Wayback Machine archive date (CDX API) | No |
| `_google_vision_web(image_bytes/url)` | Google Vision web detection — exact copies, pages, best-guess labels | Yes (`GOOGLE_VISION_KEY`) |
| `tineye_search(image_bytes)` | TinEye reverse image search — kept as fallback | Yes (`TINEYE_API_SECRET`) |
| `_reverse_image_search(...)` | Router — calls Google Vision or TinEye based on `REVERSE_IMAGE_ENGINE` | — |
| `hive_ai_check(image_bytes/url)` | AI-generated probability + deepfake probability (Hive V3 API) | Yes (`HIVE_API_KEY`) |
| `run_osint(image_bytes, source_url)` | Parallel orchestrator — runs all applicable checks | — |
| `fmt_osint(findings)` | Formats findings as WhatsApp-friendly report section | — |

### Report output (OSINT section in report)

```
🔬 *OSINT VERIFICATION*
📷 EXIF: Taken 2024-08-15, Camera: iPhone 14 Pro, Software: Adobe Lightroom
🏷️ Image shows: Strait of Hormuz, military vessel, Persian Gulf
🔍 Image appears on 4 web page(s)
   • BBC News — Iran threatens to close Strait
   • reuters.com
🤖 AI-generated probability: 94% _(likely stable_diffusion)_
🎭 Deepfake probability: 12%
🕰️ Wayback Machine: First archived 2024-01-10
```

### OSINT findings flow

OSINT findings passed to `claude_analyse()` as additional evidence context — factors into verdict. Also rendered as a separate OSINT section in the WhatsApp report after the SOURCES block.

---

## 9. Video Pipeline Detail

Facebook/Instagram Reels and other social video URLs follow this chain:

```
URL detected as video (video_path_hints: watch/video/reel/shorts/clip/share/v/share/r/)
    ↓
_get_video_duration() → pre-check duration via yt-dlp metadata (no download)
    → if > MAX_VIDEO_MINUTES (default 10): reject with friendly message, zero cost
    ↓
vikas5914 RapidAPI → download video bytes (max 30MB)
    ↓
cv2 frame extraction → fails on fragmented MP4 (moov atom not found)
    ↓ (fallback)
ffmpeg frame extraction at fixed offsets [0, 3, 7, 12, 20s]
    → Note: ffmpeg in nixpacks.toml but may not be in PATH on Railway
    ↓ (fallback if ffmpeg fails)
yt-dlp audio-only download (_ytdlp_audio_bytes) → .m4a DASH stream
    ↓
Whisper transcription → transcript text
    ↓ (fallback if all above fails)
_fb_ig_post_scrape() → OG metadata from Facebook externalhit headers
    ↓
assess_content_claims(text) → Sonnet claim extraction
```

**Video length limit:** `MAX_VIDEO_MINUTES` env var (default 10). Pre-checks via yt-dlp metadata before any download. FB/IG path silently skips video and falls through to text/image scrape. `_ytdlp_audio_bytes` also capped at 30MB.

**Fragmented MP4 fix:** `_repair_mp4()` remuxes DASH/streaming downloads (which produce "moov atom not found") via `ffmpeg -c copy -movflags faststart`. Called automatically before audio extraction and frame extraction.

**ffmpeg:** Now installed via `Dockerfile` (Railpack was ignoring nixpacks.toml). Confirmed working on Railway.

---

## 10. Claim Extraction (assess_content_claims)

Uses `claude-sonnet-4-6`.

**Key prompt rules:**
- Claims must be 5–12 words, short and direct
- Use the speaker's own framing (not academic paraphrase)
- Do NOT infer context not explicitly stated
- Do NOT add background info unless explicitly stated in content
- Max 6 claims per check
- For current-affairs claims, appends "as of Month YYYY" (uses post_date if available, else current UTC month)

---

## 11. Threading Model

```
WhatsApp webhook POST (Flask worker thread)
    ↓
process() — runs synchronously, returns 200 OK immediately
    ↓
If new content → assess_content_claims() → store in pending{} → send claims_confirm_msg()
    returns 200 OK

If Y/selection reply → pop from pending{} →
    threading.Thread(target=run_check, ...).start()
    returns 200 OK immediately (WhatsApp requires <5s response)
        ↓
    run_check() [background thread — runs for 15-45 seconds]
        ↓
        ThreadPoolExecutor(max_workers=1).submit(run_osint, ...)  [nested background thread]
        ↓
        scrape_sites() [parallel requests inside run_check thread]
        ↓
        osint_future.result(timeout=25)  [waits for OSINT thread]
        ↓
        claude_analyse() × N claims  [sequential]
        ↓
        send_reaction()  [after all claims done]
```

**Important:** `pending{}` is a plain dict — not thread-safe for concurrent access but works in practice because each user key is independent. Do not replace with a shared cache without adding locking.

**Gunicorn workers:** 4 workers. Each handles its own `pending{}` dict in memory. If a user sends content on worker A and replies on worker B, the pending state is lost → user gets "no pending check" error. This is a known limitation — acceptable for current scale. Fix by moving `pending{}` to Redis/DB if needed.

---

## 12. Known Issues & Gotchas

### scrape_sites() returns mostly nothing
~95% of the 65 configured sources return 403/404/timeout when scraped. This is expected — most news sites block scrapers. This is **not a bug to fix**. The real evidence comes from `source_content` (the extracted article/transcript) and `tavily_search()`. Don't remove sources from the list — the few that do respond (some fact-check orgs) are valuable.

### Emoji reactions appear in Fred's chat only
WhatsApp Business Cloud API `type: "reaction"` reacts to a message **in the conversation Fred is part of** — i.e. the user's chat with Fred. It cannot reach back into a group chat or another conversation where the user originally saw the post. This is a WhatsApp API limitation, not a code issue.

### FB/IG cookies expire ~2026-03-30
`FB_COOKIES_B64` and `IG_COOKIES_B64` are base64-encoded Netscape-format cookie files from a logged-in browser session. They expire periodically. When expired, FB/IG video downloads and post scrapes degrade silently. Refresh by:
1. Log into Facebook/Instagram in browser
2. Export cookies via "EditThisCookie" or similar extension → Netscape format
3. `base64 -w0 cookies.txt` → paste value into Railway env var

### pending{} is lost on redeploy
Every Railway redeploy wipes in-memory state. Any user mid-flow (waiting at claim confirmation) will get "no pending check" on their next reply. Acceptable at current scale.

### Video authenticity claim + OSINT
The auto-injected claim "Is this video real and not AI-generated or manipulated?" relies on Hive AI (`HIVE_API_KEY`) for its evidence. If Hive key is missing/expired, this claim will return UNVERIFIABLE. Check `HIVE_API_KEY` is set in Railway.

### Max 6 claims but source_content truncated at 3000 chars
`claude_analyse()` truncates `source_content` to 3000 characters. For long articles this means Claude only sees the opening. Increase if needed — tradeoff is token cost.

### music.youtube.com vs youtube.com
yt-dlp supports `music.youtube.com` — treated identically to `youtube.com`. The `MAX_VIDEO_MINUTES` duration pre-check applies to both.

### Two-step deploy (git push alone is not always enough)
Railway auto-deploys on push to `main`. However, env var changes staged in the Railway dashboard are only applied on the next deploy. If you've changed env vars and pushed code, the vars may not be live until a fresh deploy is triggered. See Section 22 for the trigger command.

---

## 14. Database Schema (SQLite)

Located at `/data/factcheck.db` (Railway Volume — persists across redeploys).

**Tables:**
- `users` — `phone`, `free_checks_used`, `credits_cents`, `subscription` (active/none), `created_at`, `last_active`
- `platform_users` — `platform` (whatsapp/messenger/telegram), `platform_id`, `user_id`, `created_at`
- `checks` — `id`, `phone`, `query_hash`, `source_type`, `cost_cents`, `billing_type`, `created_at`
- `payments` — `id`, `phone`, `amount_cents`, `stripe_payment_id`, `created_at`

Billing types: `free`, `credited`, `subscribed`

---

## 15. Billing / Monetisation

**Current state:** `FREE_CHECKS_LIMIT=9999` — effectively unlimited for testing.

**Intended model:**
- Users get N free checks (configurable via `FREE_CHECKS_LIMIT`)
- After free checks: pay-as-you-go via Stripe top-up links or $9.99/month subscription
- Cost to user = actual API cost × `PROFIT_MARGIN` (default 2.0 = 100% markup)

**To activate:**
1. Create Stripe account, create Payment Links for $5/$10/$25 top-up and $9.99/month sub
2. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `TOPUP_5_LINK`, `TOPUP_10_LINK`, `TOPUP_25_LINK`, `SUB_LINK` in Railway
3. Set webhook endpoint in Stripe dashboard: `https://web-production-1f0a4.up.railway.app/stripe-webhook`
4. Set `FREE_CHECKS_LIMIT=3` in Railway

**Stripe webhook handler:** `POST /stripe-webhook` — already implemented, processes `checkout.session.completed` to add credits.

---

## 16. Multi-Perspective / Bias-Aware Fact-Checking ✅ Verified

Key design goal: remove Western media bias and serve investigative journalists, activists, and Muslim/Middle Eastern communities.

### Six parallel real-time searches per claim (session 5 — verified working)

| Search | Language | Sources |
|---|---|---|
| Tavily main | English | General news, Western coverage |
| Tavily regional | English | Al Jazeera EN, MEE, TRT World, Press TV, Arab News, Dawn |
| Tavily Arabic | Arabic | Arabic-language results (MENA topics only) |
| Brave English | English | Broad web search |
| Brave Arabic | Arabic | `search_lang=ar, country=ae` — MENA topics only via `_is_mena_topic()` |
| Tavily social | English | Twitter/Reddit/trending discourse |

All six run in parallel — no added latency. `_is_mena_topic()` keyword list gates Arabic searches.

### Pro/con debate (reframed session 5)

- **Pro:** Western mainstream perspective (BBC, Reuters, AP, CNN, NYT, official government statements)
- **Con:** Regional/Global South/affected community (Al Jazeera, MEE, regional outlets, intl law, affected people)
- Claude Sonnet synthesises both into balanced verdict

### Source grouping by perspective

Evidence fed to Claude is grouped into labelled categories:
- `LIVE WEB SEARCH` — Perplexity Sonar, Tavily Summary, Tavily Search, Regional, Arabic, Social/Trending
- `FACT-CHECK ORGS` — Snopes, FullFact, PolitiFact, AFP, Misbar, Africa Check, Alt News, Boom Live, Rappler, Logically Facts etc.
- `HUMAN RIGHTS & INTL LAW` — HRW, Amnesty, B'Tselem, UN News, Bellingcat
- `REGIONAL / MIDDLE EAST` — Al Jazeera, MEE, MEMO, 972 Magazine, Electronic Intifada, Mondoweiss, Anadolu, Al-Monitor, DAWN, Arab News, Haaretz, TRT World
- `FRENCH / FRANCOPHONE` — RFI, France 24, Jeune Afrique, Le Monde, Libération, Le Figaro, Afrik.com, APA News
- `SOUTH ASIAN / URDU` — Geo News, Dawn (Pakistan), BBC Urdu, ARY News, Jang, The Hindu, NDTV, Hindustan Times, Times of India
- `SWAHILI / EAST AFRICA` — BBC Swahili, VOA Swahili, The Citizen Tanzania, Standard Media Kenya
- `SPANISH / LATIN AMERICAN` — Chequeado, Maldita, EL PAÍS, Telesur, BBC Mundo, Aos Fatos, Infobae, La Nación
- `INDEPENDENT / ALTERNATIVE` — Grayzone, Intercept, Democracy Now, Novara, Canary, MintPress, Responsible Statecraft, Meduza (Russia)
- `WESTERN MAINSTREAM` — BBC, Reuters, AP, Guardian, CNN, NYT, WaPo, Der Spiegel, Euronews etc. (40+ outlets)

**State media excluded (disinfo risk):** RT, Sputnik, CGTN, Xinhua, Press TV, Yeni Safak — removed 2026-03-18.
Policy: only sources with editorial independence and a track record of correcting errors.

### Report fields
- **PERSPECTIVES** — `🌐 Western:` / `🕌 Regional:` / `⚖️ Consensus:` — shows where sources diverge by geopolitical view
- **CONTESTED LANGUAGE** — flags disputed terminology with all framings (e.g. "terrorist/militant/resistance fighter")
- **WHO BENEFITS?** — who stands to gain from the claim being believed/shared (state actor, party, outlet, movement). Empty for benign claims.

---

## 17. Beta Launch Features (added 2026-03-16)

### Welcome message
First-time users receive a welcome message that:
- Explains what the bot does (multi-perspective, bias-aware)
- Lists content types supported
- Shows free check count
- BETA label + feedback invite

### HELP command
Reply HELP, ?, START, or INFO to get full feature overview.

### BETA label in reports
Footer: `FactCheck Pro v3.3 BETA` when `BETA_MODE=true` (default).
Set `BETA_MODE=false` in Railway to remove BETA label at launch.

### Last free check warning
When user is on their last free check:
> "ℹ️ This is your last free check. Reply HELP for info on continuing after this."

---

## 18. Multi-Platform Support

Code is implemented for all platforms but most are dormant pending credentials:

| Platform | Status | What's needed |
|---|---|---|
| WhatsApp | ✅ LIVE | Running |
| Facebook Messenger | Code ready | Set `MESSENGER_PAGE_TOKEN` in Railway |
| Instagram DMs | Code ready | Same `MESSENGER_PAGE_TOKEN` (shared with Messenger) |
| Telegram | Code ready | Set `TELEGRAM_BOT_TOKEN` in Railway |
| Twitter/X DMs | Code ready | Paid Twitter Developer account (~$100/month), set TWITTER_* vars |

**Endpoints:**
- WhatsApp: `GET/POST /webhook`
- Messenger/Instagram: `GET/POST /messenger-webhook`
- Telegram: `POST /telegram-webhook`
- Twitter: `GET/POST /twitter-webhook`

---

## 19. Admin Features

- **Admin alerts:** If Anthropic/OpenAI API credits run out, alert WhatsApp message goes to `ADMIN_NUMBER` (throttled 1/hour per provider)
- **Token auto-refresh:** WhatsApp token refreshed every 50 days via APScheduler (requires `FB_APP_ID`, `FB_APP_SECRET`)
- **Admin number:** `34643994740` — receives credit exhaustion and error alerts

---

## 20. Test Suite (test_comprehensive.py)

Added 2026-03-16. Located at `/home/anon/whatsapp-factcheck/test_comprehensive.py`.

```bash
# Fast unit tests (no API calls, ~5 seconds):
python3 test_comprehensive.py --unit-only

# Full test suite (real API calls, ~10 minutes):
python3 test_comprehensive.py

# Filter by category or name:
python3 test_comprehensive.py -f facebook
python3 test_comprehensive.py -f osint

# Verbose output:
python3 test_comprehensive.py -v

# List all tests:
python3 test_comprehensive.py --list
```

**Coverage:**
- 24 unit tests (no API calls) — all passing ✅
- 36+ live integration tests (real API calls)
- Categories: text_claims, facebook, instagram, tiktok, youtube, twitter, other_social, news_urls, image, audio, video, document, osint, perspectives, formatting, sources, commands, billing, edge_cases

**How it works:**
- Patches `bot.send` to capture output without hitting WhatsApp API
- Patches `bot.download_media` with synthetic test media (real JPEG/WAV bytes generated via Pillow/struct)
- Uses unique phone numbers per test (time-based) + temp SQLite DB for isolation
- `run_full()` helper: calls `process()`, auto-confirms Y, waits for billing deduction event

---

## 21. Post Date & Staleness Detection

Post date extracted from:
- `yt-dlp` video downloads → `upload_date` field
- Facebook/Instagram OG scrape → `article:published_time` meta tag
- Twitter/X via fxtwitter → `created_at` field

Stored in `pending` dict → passed to `run_check` → `claude_analyse` (temporal context in synthesis prompt for posts >30 days old) + `fmt_report` (📅 Posted label + ⚠️ staleness warning for posts >180 days old).

---

## 22. Current State & Session History

### Session 7 — 2026-03-18 — what was fixed

| Commit | Change |
|---|---|
| (multiple) | fix: synth_prompt — CLAIM ORIGIN, POLITICAL FRAMING LABELS, SOCIAL MEDIA EVIDENCE, NUMERICAL APPROXIMATIONS rules added |
| (multiple) | fix: Tavily two-pass search — temporal word stripping + retry if <5 named sources |
| (multiple) | feat: `/api/test` endpoint for automated pipeline testing without WhatsApp |
| (multiple) | feat: `test_format.py` test runner hitting Railway `/api/test` live endpoint |
| `1881e97` | fix: `_trunc` cuts at sentence boundary to eliminate mid-thought ellipsis |

**Root causes fixed this session:**
1. **Model downgrading TRUE claims to NEEDS CONTEXT** — synth_prompt was lacking rules to stop the model treating conspiracy-theory association, "antisemitic framing", and claim origin as verdict modifiers. Fixed with four explicit RATING RULE blocks.
2. **MOSTLY TRUE instead of TRUE for minor numerical approximations** — "six weeks" vs ~7 weeks caused a downgrade. Fixed with NUMERICAL APPROXIMATIONS rule: minor imprecision in numbers/dates that doesn't change substance = TRUE.
3. **Tavily missing historical coverage** — "Silverstein recently bought US Bank Tower" was anchored to 2026, filtering out 2020 purchase news. Fixed with two-pass strategy: year-anchored first, then retry stripping temporal words if <5 named sources found.
4. **Truncation — `…` mid-sentence in output** — `_trunc` was cutting at word boundary and appending `…`. Fixed to cut at sentence boundary (`. `, `! `, `? `) with no ellipsis when a clean sentence end is found.
5. **Automated testing** — established `test_format.py` + `/api/test` endpoint so testing can be triggered without WhatsApp involvement.

**Verdict philosophy (now in synth_prompt):**
- `CLAIM ORIGIN` — TRUE is TRUE regardless of who circulates it or what narrative it supports
- `POLITICAL FRAMING LABELS` — "antisemitic framing", "conspiracy theory framing" etc. are editorial categories, not factual assessments; never use as verdict modifiers
- `SOCIAL MEDIA EVIDENCE` — social posts show circulation only, not truth/falsehood
- `NUMERICAL APPROXIMATIONS` — minor numerical imprecision that doesn't change substance = TRUE, never downgrade

**Automated test results (3/3 PASS, no truncation):**
- Claim 1 (WTC lease — "six weeks"): TRUE (HIGH) ✅
- Claim 2 (US Bank Tower purchase): TRUE (MEDIUM) ✅
- Claim 3 (Carney/mafia state): UNVERIFIABLE (LOW) ✅

**Test command:**
```bash
VERIFY_TOKEN=factcheck_verify_123 python3 test_format.py
# Single claim: python3 test_format.py "your claim"
```

### Session 6 — 2026-03-18 — what was fixed

| Commit | Change |
|---|---|
| `c528d24` | feat: French/Urdu/Swahili regions added; state media removed |
| `66de935` | fix: balanced regional source preview + SPANISH/LATIN AMERICAN category |
| `d62eed7` | fix: evidence cap 2000→10000 chars + subdomain source name lookup |
| `73cfd80` | docs: PROJECT.md updated for session 6 |

**Root cause fixed this session:** `grouped[:2000]` in `claude_analyse()` was truncating the evidence string to 2000 characters. With a Tavily Live Web Search summary consuming ~600 chars, only ~1400 chars remained for named outlet snippets (~500 chars each). Claude consistently saw 0–2 named outlets and returned MEDIUM confidence. Raising to 10000 chars fixed it — TRUE/HIGH confidence now verified working.

**Verified working:** TRUE + HIGH confidence for well-corroborated breaking news (Joe Kent/NCTC resignation story confirmed by Al Jazeera direct quote + BBC headline + Live Web Search).

### Session 5 — 2026-03-17 — what was fixed

| Commit | Change |
|---|---|
| `806995c` | fix: remove Western outlet bias from confidence/rating rules |
| `13026f0` | fix: word-boundary truncation (`_trunc`) + `rating_reason` field |
| `f912149` | fix: temperature=0 on claim extraction + deprioritise filler claims |
| `260b64f` | fix: restore con_prompt omission arguments with correct distinction |
| `8094b39` | fix: tighten UNVERIFIABLE — partial evidence must get a rating |
| `14b41f1` | fix: search result cache 1hr TTL + temperature=0 on verdict calls |
| `3f93b45` | fix: omissions rule — only downgrade if omission misleads |
| `0306e44` | fix: max claims 3, dividers 14 chars (fit iPhone screen) |
| `e6f2f1b` | fix: images use enumerated claims flow (not old CLAIM PREVIEW) |

### Source expansion — shipped vs still to do

**Shipped (2026-03-18):**
- `FRENCH / FRANCOPHONE`: RFI, France 24, Jeune Afrique, Le Monde, Libération, APA News
- `SOUTH ASIAN / URDU`: Geo News, Dawn, BBC Urdu, ARY News, Jang, The Hindu, NDTV
- `SWAHILI / EAST AFRICA`: BBC Swahili, VOA Swahili, The Citizen Tanzania, Standard Media Kenya
- `SPANISH / LATIN AMERICAN`: Chequeado, Maldita, EL PAÍS, Telesur, BBC Mundo, Aos Fatos
- Meduza (independent Russian, exiled) → `INDEPENDENT / ALTERNATIVE`
- State media removed: RT, Sputnik, CGTN, Xinhua, Press TV, Yeni Safak

**Still to do:**
- Tavily language passes — search is still English-only; French/Urdu/Swahili sources only surface if their English content appears in Tavily results
- BBC Swahili/Urdu subpath URL matching — `bbc.com/urdu` and `bbc.com/swahili` in domain map but subpath matching needs testing
- Turkish independent sources: Cumhuriyet, Bianet
- Farsi independent sources: IranWire, Iran International

---

## 23. Outstanding Tasks (priority order)

### Urgent
1. **FB cookies rotation** — `FB_COOKIES_B64` / `IG_COOKIES_B64` expire ~2026-03-30 (12 days). Refresh via EditThisCookie → base64 → Railway env var.

### Immediate
2. **fredcheck.co.uk** — add as custom domain in Railway
3. **WEBSITE_URL env var** — set to `https://fredcheck.com` in Railway
4. **Set FREE_CHECKS_LIMIT for beta** — change from 9999 to 5-10 when ready to open to testers
5. **Stripe setup** — create Payment Links, set all Stripe env vars, reset `FREE_CHECKS_LIMIT=3` for launch

### High Priority
6. **User feedback system** — reply FEEDBACK or 👍/👎 after a check. Store in DB. Use patterns to refine prompts.
7. **Low credit alerts** — notify user when free checks exhausted; admin alert when Anthropic/OpenAI credits low
8. **Persist `pending` state to DB** — lost on every redeploy; users mid-flow get errors

### Medium Priority
9. **Tavily language passes** — French/Urdu/Swahili search passes (query in target language for RFI/Geo etc.). Currently Tavily is English-only.
10. **Perplexity Sonar** — activate post-beta with `PERPLEXITY_API_KEY`
11. **TikTok OCR** — text overlay recognition (pytesseract or Sonnet)
12. **BBC Swahili/Urdu subpath URL matching** — test that `bbc.com/urdu` and `bbc.com/swahili` resolve correctly in `_url_to_source_name`

### Lower Priority / Future
13. **Messenger/Telegram** — set tokens when ready to expand platforms
14. **Twitter/X** — activate when ready to pay (~$100/month)
15. **Turkish/Farsi independent sources** — Cumhuriyet, Bianet, IranWire, Iran International
16. **Lenz.io integration** — contact for API access (Cloudflare blocks scraping)

### Done this session (2026-03-18) ✅
- ~~Evidence truncation bug~~ — `grouped[:2000]` → `grouped[:10000]` — Claude now sees all outlet snippets ✅
- ~~Subdomain source names~~ — `edition.cnn.com`→CNN, `en.wikipedia.org`→Wikipedia etc. ✅
- ~~TRUE/HIGH confidence~~ — verified working for well-corroborated breaking news ✅
- ~~Balanced source preview~~ — quota system, always shows all regions in cross-referencing line ✅
- ~~French/Urdu/Swahili regions~~ — 4 new regional categories added ✅
- ~~State media removed~~ — RT, Sputnik, CGTN, Xinhua, Press TV, Yeni Safak gone ✅

### Medium Priority
9. **TikTok text overlay OCR** — switch `analyze_video_frames` to Sonnet or add pytesseract for styled text overlays
10. **More accurate usage calculation** — audit cost tracking completeness
11. **TinEye credits** — buy bundle at tineye.com/services, set `REVERSE_IMAGE_ENGINE=tineye` to activate

### Lower Priority / Future
12. **Messenger/Telegram** — set tokens when ready to expand platforms
13. **Twitter/X** — activate when ready to pay (~$100/month)
14. **Supporting website** — standalone fact-check site as alternative access channel
15. **In-platform integrations** — native FB/TikTok/Instagram/Twitter bot integrations (beyond DMs)
16. **Lenz.io integration** — contact lenz.io for API access (they have `/api/purchase` + `/api/subscribe` endpoints suggesting B2B tiers exist). Their results JSON is well-structured (verdict score 0-10, panelist reasoning, sources) — a Claude-based parser would handle format changes gracefully. Add as `SRC_LENZ` toggle like other sources. **Blocker:** Cloudflare Turnstile prevents scraping without API key — do not attempt workaround, contact them directly.

---

## 24. How to Continue Development

### Local setup

```bash
git clone https://github.com/omartaslam/factcheck-bot
cd factcheck-bot
pip install -r requirements.txt
# Add API keys to environment
python bot.py
```

**Local dependencies for tests:**
```bash
pip install opencv-python-headless yt-dlp piexif apscheduler --break-system-packages
```

### Testing

```bash
python3 test_comprehensive.py --unit-only   # fast, no API keys needed
python3 test_comprehensive.py               # full suite (needs all API keys)
```

### Deploying

Push to `main` branch — Railway auto-deploys.

```bash
git add bot.py
git commit -m "fix/feat: description"
git push
```

### Railway CLI (for logs/env)

```bash
# Install: npm i -g @railway/cli
railway login
railway logs --service 3ae3bd52-301e-4003-b2cd-291436c7af2d
railway variables  # view env vars
```

Railway API (GraphQL):
```bash
curl -s -H "Authorization: Bearer bc2d9c22-2d89-458c-8c33-3635a57193c7" \
  "https://backboard.railway.app/graphql/v2" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ deployments(first: 5, input: { serviceId: \"3ae3bd52-301e-4003-b2cd-291436c7af2d\" }) { edges { node { id status createdAt } } } }"}'
```

---

## 25. Recent Git History

```
c528d24  feat: add French/Urdu/Swahili regions; remove state media sources
66de935  fix: balanced regional source preview + SPANISH/LATIN AMERICAN category
d62eed7  fix: raise evidence cap 2k→10k chars + fix subdomain source name lookup
05cfa92  fix: (session 5 — multiple verdict quality fixes)
806995c  fix: remove Western outlet bias from confidence/rating rules
13026f0  fix: word-boundary truncation + rating_reason explanation field
f912149  fix: temperature=0 on claim extraction + deprioritise biographical filler claims
7983ba8  feat: MAX_VIDEO_MINUTES limit (default 10) — pre-check duration before download
5df8f4c  fix: inject video authenticity claim before 0-claims gate
3f98823  feat: video questions treated as claims; authenticity claim auto-injected
aa98c6e  feat: emoji reactions on sender's message (verdict summary emoji)
d127a5a  ROOT FIX: pass source article to claude_analyse as primary evidence for URL fact-checks
7a49c46  feat: claim selection + ranked claims (reply 1,2,3 or ALL)
9d871d9  fix: truth score bar — green fills left (🟩🟩🟩🟥🟥 for MOSTLY TRUE)
95920bc  fix: strip day/time metadata from claims — concrete bad/good examples
adef79f  fix: combine status messages — 4 bubbles → 2
0ec3115  fix: augment Tavily query with OCR headline when FB post text truncated
c9ceb4d  fix: transparent status messages for FB/IG post text and image extraction
8afe851  fix: never say 'Video found' until frames/audio confirmed
b9d8efc  feat: add Perplexity Sonar real-time search (activate with PERPLEXITY_API_KEY)
93996ad  fix: always tell Claude today's date + anchor Tavily queries with current year
319a89c  feat: Tavily advanced depth, real publication names, 40+ outlets in _SOURCE_PERSPECTIVE
f113c75  fix: apply _is_video_bytes check to all URL download paths
804118b  fix: detect content type before claiming 'Video found' for FB/IG posts
a799382  feat: Google Vision web detection as primary reverse image search
6bb00a4  feat: add 'Who benefits?' field to fact-check reports
2d8c271  feat: add temporal context to claims for current-affairs posts
8420444  fix: repair fragmented MP4 (moov atom), require frames/audio for video success
d3a66bd  feat: beta launch — welcome message, HELP command, BETA label, last-check warning
```

## 26. Deploy Procedure

Always do **both** steps:
```bash
git add bot.py && git commit -m "fix/feat: description" && git push origin main
```
Then trigger Railway deploy via API (applies staged env var changes):
```bash
curl -s -X POST https://backboard.railway.app/graphql/v2 \
  -H "Authorization: Bearer bc2d9c22-2d89-458c-8c33-3635a57193c7" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation { serviceInstanceDeploy(serviceId: \"3ae3bd52-301e-4003-b2cd-291436c7af2d\", environmentId: \"ebb5147d-8292-4b55-bd76-6a2c1b3e6564\") }"}'
```
