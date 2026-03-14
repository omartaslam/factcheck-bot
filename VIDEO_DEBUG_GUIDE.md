# 🎬 Video Debugging Guide

## Current Status
- ✅ Text messages: **Working**
- ✅ Code deployed: **Latest version on Railway**
- ❓ Videos: **Testing needed**

## What Was Changed

### Priority: Audio Transcription First
Videos now process **audio first** (most reliable), then optionally try visual analysis:

1. Download video from WhatsApp
2. Transcribe audio using Whisper → Send to user
3. Try frame extraction (optional, non-critical)
4. If frames work → Combine visual + audio
5. If frames fail → Continue with audio-only

### Better Logging
Every step now logs details:
- Media download progress
- File sizes
- Transcription attempts
- Frame extraction success/failure

## How to Test

### Test 1: Upload a Short Video
1. Open WhatsApp and go to your bot
2. Record or send a **short video** (10-30 seconds)
3. Observe the messages:
   ```
   🎬 Processing video...
   Transcribing audio...
   [Y/N confirmation]
   ```
4. Reply **Y**
5. Should get fact-check results based on audio

### Test 2: Send a Video URL
1. Find a TikTok, YouTube Short, or Twitter video
2. Copy the URL
3. Send URL to bot
4. Observe messages:
   ```
   🎬 Downloading video...
   Transcribing audio...
   Analyzing visual content...
   [Y/N confirmation]
   ```
5. Reply **Y**

### Test 3: Check What Failed
If videos still don't work, we need to check Railway logs.

## Check Railway Logs

### Option 1: Railway Dashboard
1. Go to https://railway.app
2. Select your **factcheck-bot** project
3. Click **Deployments** tab
4. Click the latest deployment
5. Click **View Logs**
6. Look for errors after sending a video:
   ```
   INFO - Downloading media ID: xxx
   INFO - Downloaded 2453678 bytes
   INFO - Transcribing 2453678 bytes, mime: video/mp4
   ERROR - Whisper failed: [error details]
   INFO - Trying Claude audio fallback...
   ```

### Option 2: Use Railway CLI
```bash
# Install Railway CLI
curl -fsSL https://railway.app/install.sh | sh

# Login
railway login

# Link to your project
railway link

# Stream logs
railway logs
```

Then send a video and watch the logs in real-time.

## Common Issues & Fixes

### Issue 1: "Could not process video"
**Possible causes:**
- Download from WhatsApp failed
- Audio transcription failed (both Whisper and Claude)

**Check logs for:**
```
ERROR - Media download failed: [reason]
ERROR - Whisper failed: [reason]
ERROR - Claude transcribe failed: [reason]
```

**Fix:**
- If download fails → Check WhatsApp token expiration
- If Whisper fails → Check OPENAI_API_KEY in Railway
- If Claude fails → Check ANTHROPIC_API_KEY in Railway

### Issue 2: "Download failed. Analyzing page..."
**For video URLs only**

**Possible causes:**
- yt-dlp can't download from this platform
- Video is private/requires login
- File size > 30MB

**Workaround:**
- User can send a screenshot instead
- Or describe the video in text

### Issue 3: No response at all
**Possible causes:**
- Bot crashed (Railway restarting)
- WhatsApp webhook not receiving messages

**Fix:**
1. Check Railway deployment status (should be green)
2. Verify webhook in Meta Developer Console
3. Check Railway logs for crash errors

## Environment Variables to Check

Go to Railway → Variables tab and verify:

```
WHATSAPP_TOKEN=EAA... (valid token)
PHONE_NUMBER_ID=102...
ANTHROPIC_API_KEY=sk-ant-api03-...
OPENAI_API_KEY=sk-proj-... (optional but recommended)
GOOGLE_FACT_CHECK_API_KEY=AIza...
VERIFY_TOKEN=factcheck_verify_123
```

## Expected Behavior Now

### Video Uploaded to WhatsApp
```
User: [sends 15-second video]
Bot: 🎬 Processing video...
Bot: Transcribing audio...
Bot: [Confirmation message with preview]
User: Y
Bot: ⚙️ Cross-referencing fact-checkers...
Bot: [Full fact-check report based on audio transcript]
```

### Video URL (TikTok/YouTube)
```
User: https://tiktok.com/@user/video/123
Bot: 🎬 Downloading video...
Bot: Transcribing audio...
Bot: Analyzing visual content...
Bot: [Confirmation with preview]
User: Y
Bot: [Full report with audio + visual analysis]
```

### If Frame Extraction Fails (Non-Critical)
```
[Logs show]: WARNING - Frame extraction failed (non-critical)
[User sees]: Normal fact-check based on audio only
```

## What to Send Me

If videos still don't work, share:

1. **Railway logs** (copy/paste the error section)
2. **What type of video** (uploaded file vs URL?)
3. **Video duration** and file size (if known)
4. **Exact error message** shown to user

This will help pinpoint exactly what's failing!
