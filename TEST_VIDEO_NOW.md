# 🎬 Test Video Processing NOW

## What I Just Deployed

A new version that **shows you exactly what's failing** instead of staying silent.

## Wait 2 Minutes

Railway is deploying right now. Wait **2 minutes**, then test.

## Test Steps

### Step 1: Send a Short Video
1. Open WhatsApp
2. Send a **short video** (10-15 seconds) to your bot
3. **Watch for messages** - you'll see step-by-step progress:

**Expected messages:**
```
🎬 Processing video...
✓ Downloaded (234KB). Transcribing...
✓ Transcribed: [first 100 chars]...
[Confirmation message]
```

**OR if something fails, you'll see:**
```
🎬 Processing video...
❌ Failed to download video from WhatsApp.
Debug info: download_media returned None
```

**OR:**
```
🎬 Processing video...
✓ Downloaded (234KB). Transcribing...
❌ Transcription error: [exact error message]
```

### Step 2: Share the Error with Me

Whatever message you see, **copy it exactly** and share it with me. Now I'll know:
- ✓ Did download work? (file size shown)
- ✓ Did transcription start?
- ✓ What exact error occurred?

## Check Health Endpoint (Optional)

To verify the bot is running and has all API keys:

```bash
curl https://[your-railway-url].up.railway.app/
```

Should return:
```json
{
  "status": "running",
  "version": "v3.1",
  "keys": {
    "whatsapp": true,
    "google_fc": true,
    "anthropic": true,
    "openai": true
  }
}
```

If any key is `false`, it's missing in Railway environment variables.

## Most Likely Scenarios

### Scenario A: Download Fails
```
❌ Failed to download video from WhatsApp
```
**Cause:** WhatsApp token expired or wrong permissions
**Fix:** Need to regenerate WhatsApp token with video permissions

### Scenario B: Transcription Fails
```
✓ Downloaded (234KB). Transcribing...
❌ Transcription error: 401 Unauthorized
```
**Cause:** OpenAI API key missing or invalid
**Fix:** Add/update OPENAI_API_KEY in Railway

### Scenario C: Empty Transcription
```
✓ Downloaded (234KB). Transcribing...
⚠️ Transcription returned empty string
```
**Cause:** Video has no audio, or transcription model can't process it
**Fix:** Try a video with clear speech

### Scenario D: It Works!
```
✓ Downloaded (234KB). Transcribing...
✓ Transcribed: [preview of audio]...
*FACTCHECK PRO* | Video
...
```
**Success!** Videos are working.

## What to Send Me

After testing, send me:
1. **Exact messages** you saw from the bot
2. **Type of video** (how long, what app recorded it)
3. **Railway health check response** (if you tested it)

Then we'll know exactly what to fix!

## Quick Checks

Before testing, verify in Railway dashboard:
- ✅ Latest deployment shows **"Success"** (green checkmark)
- ✅ Under **Variables** tab, these are set:
  - `WHATSAPP_TOKEN` (your new token)
  - `ANTHROPIC_API_KEY`
  - `OPENAI_API_KEY` (important for video!)
  - `GOOGLE_FACT_CHECK_API_KEY`
  - `PHONE_NUMBER_ID`

If `OPENAI_API_KEY` is missing → Videos won't transcribe (Whisper needs this)

## Ready?

**Wait 2 minutes** for Railway to deploy, then send a video and tell me what happens! 🚀
