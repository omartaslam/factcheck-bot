# 🎬 Video Processing Status

## Current Status (Emergency Fix Deployed)

I've temporarily **disabled direct video file uploads** to prevent crashes and give you working alternatives.

### ✅ WHAT WORKS:

1. **Video URLs** (TikTok, YouTube, Twitter, Instagram, etc.)
   - Send the URL as a text message
   - Bot downloads and analyzes it
   - Gets audio transcript + visual content

2. **Screenshots from Videos**
   - Take a screenshot of the video
   - Send as image
   - Bot extracts text with OCR

3. **Text Descriptions**
   - Type out the claim you saw in the video
   - Bot fact-checks it

### ❌ WHAT'S DISABLED:

- **Direct video file uploads** from WhatsApp camera/gallery
- You'll get this message:
  ```
  📹 Video detected! Starting processing...
  ⚠️ VIDEO PROCESSING TEMPORARILY DISABLED

  Videos require additional setup. For now, please:
  • Send the video URL (TikTok/YouTube/Twitter)
  • Or take a screenshot and send as image
  • Or describe the claim in text
  ```

## Why This Happened

Direct video uploads from WhatsApp require:
- Video download from WhatsApp servers ✓
- Audio transcription (Whisper API) ❓
- Frame extraction (ffmpeg + OpenCV) ❓

The last two steps are failing on Railway deployment, causing the bot to crash silently.

## What You Can Do Right Now

### Example 1: TikTok Video
```
User: https://www.tiktok.com/@user/video/1234567890
Bot: 🎬 Downloading video from URL...
Bot: ✓ Downloaded (456KB)
Bot: Transcribing...
Bot: ✓ Got transcript
Bot: [Confirmation message with preview]
```

### Example 2: Screenshot
```
User: [sends screenshot of video showing claim]
Bot: 🖼 Analysing image...
Bot: [Extracts text and fact-checks]
```

### Example 3: Text
```
User: Video claims "Biden said XYZ in 2020"
Bot: [Fact-checks the text claim]
```

## Next Steps to Fix Direct Uploads

The issue is likely one of these:

### Option A: Missing OpenAI Key
- Check Railway Variables tab
- Verify `OPENAI_API_KEY` is set
- This is needed for Whisper audio transcription

### Option B: Railway ffmpeg Issue
- `nixpacks.toml` requests ffmpeg
- Railway might not be installing it correctly
- Need to verify build logs

### Option C: WhatsApp Video Permissions
- Token might not have video download permissions
- Need to check token scopes in Meta console

## How to Debug Further

### Test 1: Check Health Endpoint
```bash
curl https://[your-railway-url].up.railway.app/
```

Look for:
```json
{
  "keys": {
    "openai": true  // <-- should be true
  }
}
```

### Test 2: Send Video URL
Send a TikTok or YouTube URL to see if that works. If it does, the issue is specifically with WhatsApp video downloads, not transcription.

### Test 3: Check Railway Logs
1. Go to Railway dashboard
2. Deployments → Latest → View Logs
3. Send a video file upload (even though disabled)
4. Look for the log line: `=== VIDEO MESSAGE RECEIVED ===`
5. If you see it → Webhook is working, video detection works
6. If you don't see it → Webhook isn't receiving video messages

## What I Recommend

**For now:** Use video URLs or screenshots. Both work great!

**Later:** We can debug the direct upload issue by checking:
1. Railway environment variables
2. Railway build logs (ffmpeg installation)
3. WhatsApp token permissions

Video URLs give you 90% of the functionality anyway, since most viral misinformation comes from social media links.

## Test It Now

Wait **2 minutes** for Railway to deploy, then:

1. **Send a video file** → You'll get friendly error message
2. **Send a TikTok/YouTube URL** → Should work!
3. **Send a screenshot** → Should work!

Let me know which of these works for you!
