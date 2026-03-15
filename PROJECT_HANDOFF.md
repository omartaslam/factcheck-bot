# FactCheck Pro — Project Handoff Document
*Last updated: 2026-03-15. For any AI agent or developer picking up this project.*

---

## What This Project Is

**FactCheck Pro v3.2** is a WhatsApp chatbot that fact-checks claims, images, audio, video, and URLs sent by users on WhatsApp.

Users send a message (text, image, voice note, video URL, article URL, social media post URL) and the bot replies with a structured fact-check verdict including a truth rating, evidence, source links, and propaganda technique analysis.

---

## Deployment

| Item | Value |
|---|---|
| **Live URL** | https://web-production-1f0a4.up.railway.app/ |
| **Platform** | Railway (Hobby plan — $5/month) |
| **Railway project** | enchanting-wholeness |
| **GitHub repo** | https://github.com/omartaslam/factcheck-bot (branch: main) |
| **Deploy method** | Push to GitHub main → Railway auto-deploys |
| **Runtime** | Python/Flask + Gunicorn (4 workers, 120s timeout) |
| **Build config** | `nixpacks.toml` — apt installs ffmpeg, libsm6, libxext6, libxrender-dev |
| **Process** | `gunicorn bot:app --workers 4 --bind 0.0.0.0:$PORT --timeout 120` |

---

## Tech Stack

- **Python 3** / Flask + Gunicorn
- **Anthropic Claude** — `claude-sonnet-4-6` for synthesis/verdict, `claude-haiku-4-5-20251001` for OCR, debate pro/con, neutralization, claim extraction
- **OpenAI** — `whisper-1` for audio transcription (primary), `gpt-4o-mini` for OCR fallback, `gpt-4o` for analysis fallback
- **Google Fact Check Tools API** — cross-references claims against published fact-checks
- **yt-dlp** — downloads/extracts data from YouTube, Facebook, Instagram, TikTok, Twitter/X
- **RapidAPI** — TikTok downloader (7scorp), Twitter/Facebook downloader (vikas5914)
- **OpenCV (cv2) + PIL** — video frame extraction
- **APScheduler** — background job for WhatsApp token auto-refresh (every 50 days)
- **WhatsApp Business API** — via Meta Graph API v19.0

---

## Main File: `bot.py`

Single-file Flask app. Key sections:

### Environment Variables (all set in Railway)

| Variable | Purpose |
|---|---|
| `WHATSAPP_TOKEN` | Meta Graph API access token |
| `PHONE_NUMBER_ID` | WhatsApp Business phone number ID |
| `VERIFY_TOKEN` | Webhook verify token (default: `factcheck_verify_123`) |
| `ANTHROPIC_API_KEY` | Claude API key |
| `OPENAI_API_KEY` | OpenAI API key (Whisper + GPT-4o fallback) |
| `GOOGLE_FACT_CHECK_API_KEY` | Google Fact Check Tools API key |
| `FB_APP_ID` | Facebook App ID (default: `913551238207108`) |
| `FB_APP_SECRET` | Facebook App Secret (for token auto-refresh) |
| `FB_COOKIES_B64` | Base64-encoded Netscape-format Facebook cookies (for yt-dlp) |
| `IG_COOKIES_B64` | Base64-encoded Instagram cookies (for yt-dlp) |
| `RAPIDAPI_KEY` | RapidAPI key for TikTok/Twitter downloaders |
| `SRC_*` | Toggle individual fact-check sources on/off (see Sources section) |
| `CUSTOM_SOURCES` | Add extra sources without code changes (see format below) |

### Source Toggle Variables

All default to `"true"`. Set to `"false"` in Railway to disable any source.

**Fact-check organisations:** `SRC_SNOPES`, `SRC_FULLFACT`, `SRC_FACTCHECKORG`, `SRC_POLITIFACT`, `SRC_AFP`

**News outlets:** `SRC_ALJAZEERA`, `SRC_BBC`, `SRC_REUTERS`, `SRC_AP`, `SRC_GUARDIAN`, `SRC_CNN`

**Independent/alternative media:** `SRC_MEE`, `SRC_NOVARA`, `SRC_CANARY`, `SRC_ZETEO`, `SRC_YENISAFAK`, `SRC_972MAG`, `SRC_MONDOWEISS`, `SRC_EINTIFADA`, `SRC_INTERCEPT`, `SRC_HAARETZ`, `SRC_DDN`, `SRC_DEMOCRACYNOW`, `SRC_GRAYZONE`, `SRC_MINTPRESS`

**Journalist/commentator channels:** `SRC_OWENJONES`, `SRC_OWENJONES_SUB`, `SRC_CORBYN`, `SRC_CORBYN_SITE`, `SRC_ZARASULTANA`, `SRC_SULTANA_SITE`, `SRC_FINKELSTEIN`, `SRC_FINKELSTEIN_SUB`, `SRC_CODEPINK`, `SRC_CODEPINK_SITE`, `SRC_MOATS`, `SRC_MOATS_YT`, `SRC_GALLOWAY_SITE`, `SRC_PSC`, `SRC_SUBSTACK`, `SRC_DDN_YT`

**Custom sources format** (set in `CUSTOM_SOURCES` env var):
```
Name|https://site.com/search?q={q},Name2|https://site2.com/?s={q}
```
Use `{q}` for URL-encoded query, `{qt}` for short URL-encoded query.

### Message Flow

1. WhatsApp sends webhook POST to `/webhook`
2. Bot deduplicates message by ID (in-memory set, max 10,000)
3. Drops messages older than 5 minutes (stale guard)
4. Content extracted based on type:
   - **Text** → direct claim, or URL routing
   - **Image** → OCR via Claude Haiku (fallback: GPT-4o-mini)
   - **Audio** → transcription via OpenAI Whisper (fallback: Claude)
   - **Video** → frame extraction (OpenCV) + audio transcription
   - **Document** → raw text extracted (PDF etc.)
5. URL detection and routing:
   - **YouTube / TikTok / Twitter/X / Rumble etc.** → video download → frames + transcription
   - **Facebook/Instagram video URL** → yt-dlp with cookies → frames + transcription
   - **Facebook/Instagram post URL** → `_fb_ig_post_scrape()` using `facebookexternalhit` UA → og:image OCR + og:description
   - **Article URL** → HTML scrape → og:metadata extraction
6. User sees a confirm preview: "Reply Y to fact-check / N to cancel"
7. On Y: `run_check()` launches in background thread
8. `run_check()` pipeline (added 2026-03-15):
   a. **Neutralize** — `neutralize_claim()` strips emotional framing (Haiku)
   b. **Extract** — `extract_claims()` splits into ≤4 individual checkable claims (Haiku)
   c. **Scrape** — `scrape_sites()` + `google_fc()` run once, shared across all claims
   d. For each claim: **Debate** — `claude_analyse()` runs pro/con in parallel (Haiku×2) then synthesizes (Sonnet)
   e. Report sent per claim with `*— CLAIM N/M —*` header if multi-claim
9. Report formatted with verdict, truth gauge, truth score (1–10), key facts, sources, confidence

### Lenz.io-Inspired Pipeline (added 2026-03-15)

Three new functions implement the lenz.io approach:

| Function | Model | Purpose |
|---|---|---|
| `neutralize_claim(text)` | Haiku | Strip emotional/partisan framing before analysis |
| `extract_claims(text)` | Haiku | Identify ≤4 individual checkable claims in text |
| `_claude_call(prompt, ...)` | configurable | Single Claude call helper |
| `claude_analyse()` — debate step | Haiku×2 parallel | Pro argument + con argument simultaneously |
| `claude_analyse()` — synthesis | Sonnet | Final verdict synthesizing debate + evidence |

**New report fields:**
- `TRUTH SCORE  ████████░░  8/10` — numerical truthfulness score (1–10)
- Footer shows `⚖️ pro/con debate` or `single-pass` to indicate which path ran

**Timing:** Single claim ~20s. Multi-claim (e.g. 3 claims) ~55s total, sends each report as it completes.

### Token Auto-Refresh

APScheduler runs `refresh_whatsapp_token()` every 50 days using the Facebook Graph API token exchange endpoint. Requires `FB_APP_ID` and `FB_APP_SECRET` to be set.

### Verdict Ratings

`TRUE`, `MOSTLY TRUE`, `HALF TRUE`, `MOSTLY FALSE`, `FALSE`, `PANTS ON FIRE`, `UNVERIFIABLE`, `MISLEADING`, `NEEDS CONTEXT`

---

## Test Suite

`test_all.py` — 49 tests covering all media types and pipeline functions. Run with:
```bash
source venv/bin/activate
python test_all.py
```
All 49 tests pass as of 2026-03-15. Uses `unittest.mock` to patch `send()` and `download_media()` — no real WhatsApp calls made.

---

## Known Issues & Current State

### FB/IG Post Image OCR — PARTIALLY WORKING

**Status:** Post text extraction works. Image OCR for link-share posts is unreliable.

**Problem:** For FB link-share posts (e.g. `https://www.facebook.com/share/p/1B6EPjbASB/`), yt-dlp on Railway returns an incorrect thumbnail (keyboard meme "Talking/Typing" instead of the article thumbnail).

**Current code state:** Image OCR for FB/IG non-video posts is currently **disabled**. Only post text (title, description, uploader) is extracted via yt-dlp skip_download mode. Debug logging was added (`DEBUG thumbnail:`, `DEBUG url:`, `DEBUG thumbs[0]:`, `DEBUG fmt[0]:`) to reveal what yt-dlp returns on Railway with FB cookies.

**Next step:** Test the Dubai post URL in WhatsApp, check Railway logs for those DEBUG lines, paste them to diagnose the correct thumbnail field to use.

**Why it's hard:**
- Without cookies: yt-dlp fails entirely ("Cannot parse data")
- With cookies on Railway: partially works but thumbnail field points to wrong image
- `og:image` without auth: returns Facebook's generic promo image

### Usage Tracking — INCOMPLETE

Usage is tracked partially. The current implementation does not accurately aggregate:
- Total Anthropic API tokens used per conversation or per day
- OpenAI API costs (Whisper transcription, GPT-4o-mini OCR fallback)
- Google Fact Check API call counts
- RapidAPI call counts

There is no alerting when API credits are low or exhausted.

---

## Outstanding Tasks

### High Priority

1. **Low/zero API credit alerting**
   - When Anthropic or OpenAI API returns a credit/quota error, send a WhatsApp message to the admin number explaining the service is temporarily unavailable
   - Graceful degradation: if Anthropic is out of credits, fall back to OpenAI; if both fail, send a clear error message rather than timing out silently

2. **Add all SRC_* variables to Railway**
   - Not all source toggle variables defined in `bot.py` are set in Railway env vars
   - Add every `SRC_*` variable to Railway so they can be toggled without code changes

3. **Fix usage calculation accuracy**
   - Implement proper per-request token counting using Anthropic API response `usage` field
   - Track: input tokens, output tokens, model used, request type
   - Add a `/usage` admin endpoint or daily log summary

4. **Fix FB/IG link-share post image OCR** (see Known Issues above)

### Medium Priority

5. **Supporting website**
   - Simple web interface where users can paste a claim/URL and get a fact-check
   - Could use the same backend `bot.py` logic via a REST API endpoint
   - Tech options: simple HTML/JS frontend calling a `/factcheck` endpoint, or Streamlit/Gradio

6. **Monetisation — ads model**
   - Insert a short sponsored text line at the bottom of fact-check replies
   - Rotate sponsor messages from a configurable list

7. **Monetisation — subscription/freemium model**
   - Free tier: limited fact-checks per day per phone number
   - Premium tier: unlimited, faster response
   - Track usage per `wa_id` in SQLite or Railway Postgres

8. **Multi-platform expansion**
   - Facebook Messenger, Instagram DMs, Twitter/X DMs, TikTok comment replies
   - Consider a platform-agnostic message handler core that adapters plug into

### Low Priority / Future

9. **Database for persistent state**
   - Replace in-memory `processed_ids` and `pending` dicts with a database
   - Enables usage tracking per user, subscription state, server-restart resilience

10. **Multi-language support**
    - Detect input language, respond in same language
    - Claude handles many languages natively

11. **Admin dashboard**
    - Simple password-protected `/admin` page: messages processed, API cost estimate, top claims, error rate

---

## Repository Structure

```
whatsapp-factcheck/
├── bot.py              # Main application (single file, all logic)
├── test_all.py         # 49-test suite covering all media types and pipeline
├── requirements.txt    # Python dependencies
├── nixpacks.toml       # Railway build config (apt packages + start command)
├── Procfile            # Alternative start command
├── runtime.txt         # Python version
├── cookies.txt         # Facebook cookies (Netscape format, for local testing)
├── www.instagram.com_cookies.txt  # Instagram cookies (local testing)
├── update.sh           # Helper script to push updates
├── bot.py.bak          # Backup of previous bot.py
├── v3/                 # Earlier v3 iteration
└── venv/               # Local Python virtualenv (not deployed)
```

---

## How to Deploy a Change

```bash
cd /home/anon/whatsapp-factcheck
git add bot.py
git commit -m "describe change"
git push origin main
# Railway auto-deploys from main branch
```

---

## How to Check Logs

Railway dashboard → enchanting-wholeness project → Deployments → latest deployment → Logs tab.

Or via Railway CLI:
```bash
railway logs
```

---

## API Keys / Secrets Location

All secrets are in Railway environment variables. Do NOT commit them to the repo.

To view/edit: Railway dashboard → enchanting-wholeness → Variables tab.

Railway API token (project-scoped, cannot manage billing): `a150de81-9f32-42e3-acba-b0369b041ae3`

---

## Local Development

```bash
cd /home/anon/whatsapp-factcheck
source venv/bin/activate
python bot.py
# or: gunicorn bot:app --bind 0.0.0.0:5000
# run tests:
python test_all.py
```

For local FB/IG testing, `cookies.txt` and `www.instagram.com_cookies.txt` contain Netscape-format cookies exported from a logged-in browser session.

---

## System Prompt / Bot Persona

The bot presents itself as **FactCheck Pro** — a world-class fact-checker for journalists and activists, with deep expertise in the Gaza conflict, Iran-US-Israel tensions, West Bank, Hamas, Hezbollah, and regional players. It is designed to be rigorously balanced, calling out falsehoods from all sides equally, and flagging propaganda techniques and media bias.

This focus is intentional and reflects the target user base.
