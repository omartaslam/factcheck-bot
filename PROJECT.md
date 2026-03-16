# FactCheck Pro — Project Handover Document

> **Last updated:** 2026-03-16
> **Version:** v3.3 BETA
> **Status:** Live on Railway — beta-ready, OSINT pipeline integrated, 65 sources

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
**Build config:** `nixpacks.toml` (apt packages: ffmpeg, libsm6, libxext6, libxrender-dev)

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
| Real-time search | Tavily API |
| Fact-check API | Google Fact Check Tools API |
| OSINT — reverse image | TinEye HMAC API (`TINEYE_API_KEY`) |
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

### Real-time Search

| Variable | Description |
|---|---|
| `TAVILY_API_KEY` | Tavily search API — free 1000/month |

### OSINT (Optional — fully functional without these, features just disabled)

| Variable | Description | Status |
|---|---|---|
| `TINEYE_API_KEY` | TinEye reverse image search API key | ❌ Not set (get from tineye.com/services) |
| `TINEYE_API_SECRET` | TinEye HMAC signing secret | ❌ Not set |
| `HIVE_API_KEY` | Hive Moderation API key for AI/deepfake detection | ❌ Not set (get from thehive.ai) |

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

```python
1. Receive WhatsApp webhook → extract message type and content
2. For video URLs: download via vikas5914 RapidAPI → extract frames (cv2/ffmpeg) + audio (yt-dlp + Whisper)
3. For images: OCR via Claude Haiku → text
4. For audio: transcribe via Whisper → text
5. assess_content_claims(text, source_type)  # Sonnet call
   → returns {claims, checkable, reason, suggestions}
6. If not checkable: send no_claims_msg() → done
7. If checkable: store claims in pending{from_num} dict, send claims_confirm_msg()
8. User replies "Y" → run_check() with pre_claims=stored_claims
9. User replies "N" → cancel
```

### run_check() — the fact-check engine

```python
# OSINT starts immediately in background thread
osint_future = ThreadPoolExecutor.submit(run_osint, image_bytes, source_url)

# Source scraping runs concurrently (9-15 seconds)
for each claim:
    google_results = google_fc(claim)          # Google Fact Check API
    scraped_content = scrape_sites(claim)      # 65 sources, parallel
    tavily_results = tavily_search(claim)      # Real-time Tavily search

# Collect OSINT (usually done by this point — no added latency)
osint = osint_future.result(timeout=25)

verdict = claude_analyse(claim, evidence, osint)   # Sonnet with OSINT context
send verdict to user  # includes OSINT section in report
```

### Free check billing flow

```python
# After Y confirmation:
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
| `tineye_search(image_bytes)` | Reverse image search — find where image appears online | Yes (`TINEYE_API_KEY` + `TINEYE_API_SECRET`) |
| `tineye_search_url(image_url)` | Same but from URL instead of bytes | Yes |
| `hive_ai_check(image_bytes)` | AI-generated probability + deepfake probability | Yes (`HIVE_API_KEY`) |
| `run_osint(image_bytes, source_url)` | Parallel orchestrator — runs all applicable checks | — |
| `fmt_osint(findings)` | Formats findings as WhatsApp-friendly report section | — |

### Report output (OSINT section in report)

```
🔬 *OSINT VERIFICATION*
📷 EXIF: Taken 2024-08-15, Camera: iPhone 14 Pro, Software: Adobe Lightroom
🔍 TinEye: 47 matches — first seen 2023-11-02 (domain: example.com)
🤖 AI-generated probability: 94% ⚠️
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
vikas5914 RapidAPI → download video bytes
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

**Known issue:** ffmpeg is in `nixpacks.toml` `aptPkgs` but not found in Railway PATH during testing. Video frame analysis falls through to yt-dlp audio + OG scrape fallback. This works for most content.

---

## 10. Claim Extraction (assess_content_claims)

Uses `claude-sonnet-4-6`.

**Key prompt rules:**
- Claims must be 5–12 words, short and direct
- Use the speaker's own framing (not academic paraphrase)
- Do NOT infer context not explicitly stated
- Do NOT add background info unless explicitly stated in content
- Max 6 claims per check

---

## 11. Database Schema (SQLite)

Located at `/data/factcheck.db` (Railway Volume — persists across redeploys).

**Tables:**
- `users` — `phone`, `free_checks_used`, `credits_cents`, `subscription` (active/none), `created_at`, `last_active`
- `platform_users` — `platform` (whatsapp/messenger/telegram), `platform_id`, `user_id`, `created_at`
- `checks` — `id`, `phone`, `query_hash`, `source_type`, `cost_cents`, `billing_type`, `created_at`
- `payments` — `id`, `phone`, `amount_cents`, `stripe_payment_id`, `created_at`

Billing types: `free`, `credited`, `subscribed`

---

## 12. Billing / Monetisation

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

## 13. Multi-Perspective / Bias-Aware Fact-Checking

Key design goal: remove Western media bias and serve investigative journalists, activists, and Muslim/Middle Eastern communities.

### Source grouping by perspective

Evidence fed to Claude is grouped into labelled categories:
- `FACT-CHECK ORGS` — Snopes, FullFact, PolitiFact, AFP, Misbar, Africa Check, Alt News, Rappler, etc.
- `HUMAN RIGHTS & INTL LAW` — HRW, Amnesty, B'Tselem, UN News, Bellingcat
- `REGIONAL / MIDDLE EAST` — Al Jazeera, MEE, MEMO, 972 Magazine, Electronic Intifada, Mondoweiss, Anadolu, Al-Monitor, DAWN, Arab News, Haaretz, Yeni Safak
- `INDEPENDENT / ALTERNATIVE` — Grayzone, Intercept, Democracy Now, Novara, Canary, MintPress, Responsible Statecraft
- `WESTERN MAINSTREAM` — BBC, Reuters, AP, Guardian, CNN, Times of Israel

### Report fields
- **PERSPECTIVES** — `🌐 Western:` / `🕌 Regional:` / `⚖️ Consensus:` — shows where sources diverge by geopolitical view
- **CONTESTED LANGUAGE** — flags disputed terminology with all framings (e.g. "terrorist/militant/resistance fighter")

---

## 14. Beta Launch Features (added 2026-03-16)

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

## 15. Multi-Platform Support

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

## 16. Admin Features

- **Admin alerts:** If Anthropic/OpenAI API credits run out, alert WhatsApp message goes to `ADMIN_NUMBER` (throttled 1/hour per provider)
- **Token auto-refresh:** WhatsApp token refreshed every 50 days via APScheduler (requires `FB_APP_ID`, `FB_APP_SECRET`)
- **Admin number:** `34643994740` — receives credit exhaustion and error alerts

---

## 17. Test Suite (test_comprehensive.py)

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

## 18. Post Date & Staleness Detection

Post date extracted from:
- `yt-dlp` video downloads → `upload_date` field
- Facebook/Instagram OG scrape → `article:published_time` meta tag
- Twitter/X via fxtwitter → `created_at` field

Stored in `pending` dict → passed to `run_check` → `claude_analyse` (temporal context in synthesis prompt for posts >30 days old) + `fmt_report` (📅 Posted label + ⚠️ staleness warning for posts >180 days old).

---

## 19. Outstanding Tasks (priority order)

### Immediate
1. **Get TinEye API keys** — sign up at tineye.com/services → set `TINEYE_API_KEY` + `TINEYE_API_SECRET` in Railway → reverse image search fully live
2. **Get Hive API key** — sign up at thehive.ai → set `HIVE_API_KEY` in Railway → AI/deepfake detection fully live
3. **Set FREE_CHECKS_LIMIT for beta** — change from 9999 to 5-10 in Railway when ready to open to testers
4. **Share beta link** — `wa.me/447863795638` — ready to share now

### High Priority
5. **Stripe setup** — create Payment Links, set all Stripe env vars, reset `FREE_CHECKS_LIMIT=3` for launch
6. **Test PERSPECTIVES + CONTESTED LANGUAGE** — send real Middle East URLs to live bot, verify output
7. **Low credit / API key alert to user** — notify user (not just admin) when free checks exhausted; also notify admin when Anthropic/OpenAI credits are low or zero

### Medium Priority
8. **TikTok text overlay OCR** — switch `analyze_video_frames` to Sonnet or add pytesseract for styled text overlays
9. **ffmpeg on Railway** — declared in nixpacks.toml but not in PATH; video frames failing (yt-dlp audio fallback works but frames would be better)
10. **"Who benefits?" meta field** — add to Claude synthesis prompt and JSON schema for geopolitical framing analysis
11. **More accurate usage calculation** — audit cost tracking completeness (verify all Sonnet calls including `assess_content_claims` are captured)

### Lower Priority / Future
12. **Messenger/Telegram** — set tokens when ready to expand platforms
13. **Twitter/X** — activate when ready to pay (~$100/month)
14. **Supporting website** — standalone fact-check site as alternative access channel
15. **In-platform integrations** — native FB/TikTok/Instagram/Twitter bot integrations (beyond DMs)

---

## 20. How to Continue Development

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

## 21. Recent Git History

```
dc3fcb9  test: add source preview unit tests, fix AFP name + SOURCES CITED assertion
46d729b  feat: topic-aware source preview — show relevant sources per post
31c58fc  fix: rotate source preview with balanced per-category mix + AFP name fix
157cf4e  fix: show Claude-cited sources per claim + repo cleanup (deleted 15 obsolete files)
5a9537e  test: comprehensive integration test suite — 60+ tests covering all message types
8435f8e  feat: OSINT verification — reverse image, EXIF, Wayback Machine, AI/deepfake detection
7f9455e  feat: add 11 Global South fact-checkers — Misbar, Africa Check, Alt News, etc.
d3a66bd  feat: beta launch — welcome message, HELP command, BETA label, last-check warning
7e2b17f  fix: add missing _try_download_url/_extract_video_url + use claim as search query
ad7ca3c  fix: improve video claim extraction — more frames, concise prompt, larger text window
743e35a  feat: multi-perspective fact-checking — remove Western media bias
7145008  feat: post date extraction and staleness warnings in fact-check reports
```
