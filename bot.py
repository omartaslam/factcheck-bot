"""FactCheck Pro v3"""
import os,base64,json,logging,tempfile,threading,requests
from flask import Flask,request,jsonify
from dotenv import load_dotenv
from html.parser import HTMLParser
from urllib.parse import quote_plus
import time as t
load_dotenv()
WHATSAPP_TOKEN=os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID=os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN=os.getenv("VERIFY_TOKEN","factcheck_verify_123")
GOOGLE_API_KEY=os.getenv("GOOGLE_FACT_CHECK_API_KEY")
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY")
ANTHROPIC_KEY=os.getenv("ANTHROPIC_API_KEY")
WHATSAPP_URL=f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
GOOGLE_FC_URL="https://factchecktools.googleapis.com/v1alpha1/claims:search"
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger(__name__)
app=Flask(__name__)
processed_ids=set()
processed_lock=threading.Lock()
pending={}
pending_lock=threading.Lock()
SYSTEM="""You are FactCheck Pro — world-class fact-checker for journalists and activists. Deep expertise in Gaza conflict, Iran-US-Israel tensions, West Bank, Hamas, Hezbollah, regional players. Rigorously balanced — call out falsehoods from ALL sides equally. Flag propaganda techniques and media bias."""
TRUTH_METER={"TRUE":("✅","TRUE",5),"MOSTLY TRUE":("🟢","MOSTLY TRUE",4),"HALF TRUE":("🟡","HALF TRUE",3),"MOSTLY FALSE":("🟠","MOSTLY FALSE",2),"FALSE":("❌","FALSE",1),"PANTS ON FIRE":("🔥","PANTS ON FIRE",0),"UNVERIFIABLE":("❓","UNVERIFIABLE",-1),"MISLEADING":("⚠️","MISLEADING",-1),"NEEDS CONTEXT":("📌","NEEDS CONTEXT",-1)}
def verdict_header(rating):
    styles={
        "TRUE":         ("✅","VERIFIED TRUE","Claim checks out"),
        "MOSTLY TRUE":  ("🟢","MOSTLY TRUE","Minor inaccuracies"),
        "HALF TRUE":    ("🟡","HALF TRUE","Mixed evidence"),
        "MOSTLY FALSE": ("🟠","MOSTLY FALSE","Mainly inaccurate"),
        "FALSE":        ("❌","FALSE","Not supported by evidence"),
        "PANTS ON FIRE":("🔥","PANTS ON FIRE","Dangerous disinformation"),
        "UNVERIFIABLE": ("🔍","UNVERIFIABLE","Cannot be confirmed"),
        "MISLEADING":   ("⚠️","MISLEADING","Framed to deceive"),
        "NEEDS CONTEXT":("📌","NEEDS CONTEXT","Missing crucial context"),
    }
    icon,label,sub=styles.get(rating,("❓",rating,""))
    return f"{icon} *{label}*\n_{sub}_"

def truth_gauge(rating):
    pos={"PANTS ON FIRE":0,"FALSE":1,"MOSTLY FALSE":2,"HALF TRUE":3,"MOSTLY TRUE":4,"TRUE":5}
    if rating not in pos: return ""
    segs=["▱","▱","▱","▱","▱","▱"]; segs[pos[rating]]="▰"
    return f"`{' '.join(segs)}`\n_FALSE          TRUE_"

RATINGS_MAP = {
    "TRUE":          ("[ ✓ VERIFIED TRUE ]", "▓▓▓▓▓▓", "Claim is accurate"),
    "MOSTLY TRUE":   ("[ ✓ MOSTLY TRUE   ]", "▓▓▓▓▓░", "Minor inaccuracies"),
    "HALF TRUE":     ("[ ◑ HALF TRUE     ]", "▓▓▓░░░", "Mixed evidence"),
    "MOSTLY FALSE":  ("[ ✗ MOSTLY FALSE  ]", "▓▓░░░░", "Mainly inaccurate"),
    "FALSE":         ("[ ✗ FALSE         ]", "▓░░░░░", "Not supported by evidence"),
    "PANTS ON FIRE": ("[ ✗ PANTS ON FIRE ]", "░░░░░░", "Dangerous disinformation"),
    "UNVERIFIABLE":  ("[ ? UNVERIFIABLE  ]", None,     "Cannot be confirmed"),
    "MISLEADING":    ("[ ! MISLEADING    ]", None,     "Framed to deceive"),
    "NEEDS CONTEXT": ("[ i NEEDS CONTEXT ]", None,     "Missing crucial context"),
}

RATINGS_MAP = {
    "TRUE":          ("✓", "VERIFIED TRUE",  "▓▓▓▓▓▓", "Claim is accurate"),
    "MOSTLY TRUE":   ("✓", "MOSTLY TRUE",    "▓▓▓▓▓░", "Minor inaccuracies"),
    "HALF TRUE":     ("~", "HALF TRUE",      "▓▓▓░░░", "Mixed evidence"),
    "MOSTLY FALSE":  ("✗", "MOSTLY FALSE",   "▓▓░░░░", "Mainly inaccurate"),
    "FALSE":         ("✗", "FALSE",          "▓░░░░░", "Not supported by evidence"),
    "PANTS ON FIRE": ("✗", "PANTS ON FIRE",  "░░░░░░", "Dangerous disinformation"),
    "UNVERIFIABLE":  ("?", "UNVERIFIABLE",   None,     "Cannot be confirmed"),
    "MISLEADING":    ("!", "MISLEADING",     None,     "Framed to deceive"),
    "NEEDS CONTEXT": ("i", "NEEDS CONTEXT",  None,     "Missing crucial context"),
}

RATINGS_MAP = {
    "TRUE":          ("VERIFIED TRUE",  "[++++++++++]", "Claim checks out"),
    "MOSTLY TRUE":   ("MOSTLY TRUE",    "[++++++++--]", "Minor inaccuracies"),
    "HALF TRUE":     ("HALF TRUE",      "[+++++-----]", "Mixed evidence"),
    "MOSTLY FALSE":  ("MOSTLY FALSE",   "[+++-------]", "Mainly inaccurate"),
    "FALSE":         ("FALSE",          "[++---------]","Not supported by evidence"),
    "PANTS ON FIRE": ("PANTS ON FIRE",  "[----------]", "Dangerous disinformation"),
    "UNVERIFIABLE":  ("UNVERIFIABLE",   None,           "Cannot be confirmed"),
    "MISLEADING":    ("MISLEADING",     None,           "Framed to deceive"),
    "NEEDS CONTEXT": ("NEEDS CONTEXT",  None,           "Missing crucial context"),
}

RATINGS_MAP = {
    "TRUE":          ("VERIFIED TRUE",   "[++++++]", "Claim checks out"),
    "MOSTLY TRUE":   ("MOSTLY TRUE",     "[+++++.]", "Minor inaccuracies"),
    "HALF TRUE":     ("HALF TRUE",       "[++++..]", "Mixed evidence"),
    "MOSTLY FALSE":  ("MOSTLY FALSE",    "[++....]", "Mainly inaccurate"),
    "FALSE":         ("FALSE",           "[+.....]", "Not supported by evidence"),
    "PANTS ON FIRE": ("PANTS ON FIRE",   "[......]", "Dangerous disinformation"),
    "UNVERIFIABLE":  ("UNVERIFIABLE",    None,       "Cannot be confirmed"),
    "MISLEADING":    ("MISLEADING",      None,       "Framed to deceive"),
    "NEEDS CONTEXT": ("NEEDS CONTEXT",   None,       "Missing crucial context"),
}

def clean_ocr(text):
    noise = ["This business uses a secure service from Meta",
             "Tap to learn more","manage this chat"]
    lines = text.split("\n")
    out = []
    for line in lines:
        line = line.strip()
        if not line or len(line)<=2: continue
        if len(line)==5 and line[2]==":" and line[:2].isdigit(): continue
        if any(n in line for n in noise): continue
        if line in ("Fact Check","FactCheck","Today","Yesterday"): continue
        out.append(line)
    return "\n".join(out).strip()

def verdict_block(rating):
    label,bar,subtitle = RATINGS_MAP.get(rating,("UNVERIFIABLE",None,"Cannot be confirmed"))
    lines = [f"*{label}*", f"_{subtitle}_"]
    if bar:
        lines.append(f"`{bar} FALSE→TRUE`")
    return "\n".join(lines)

def build_meter(r):
    return verdict_block(r)

def meter_visual(r):
    return verdict_block(r)


def html_text(html,lim=2000):
    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.t,self.s=[],False; self.b={"script","style","nav","footer","header","aside"}
        def handle_starttag(self,tag,_):
            if tag in self.b: self.s=True
        def handle_endtag(self,tag):
            if tag in self.b: self.s=False
        def handle_data(self,d):
            if not self.s and d.strip(): self.t.append(d.strip())
    p=P(); p.feed(html); return " ".join(p.t)[:lim]
def fetch(url,timeout=12):
    try:
        r=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=timeout); r.raise_for_status(); return html_text(r.text)
    except Exception as e: log.warning("fetch %s: %s",url,e); return ""
def download_media(mid):
    try:
        r=requests.get(f"https://graph.facebook.com/v19.0/{mid}",headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}"},timeout=10); r.raise_for_status()
        r2=requests.get(r.json()["url"],headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}"},timeout=30); r2.raise_for_status(); return r2.content
    except Exception as e: log.error("DL: %s",e); return None
def ocr_image(b):
    try:
        b64=base64.b64encode(b).decode()
        r=requests.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":"claude-haiku-4-5-20251001","max_tokens":1500,"messages":[{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},{"type":"text","text":"Extract ALL text verbatim from this image. Then in 2 sentences describe what it depicts. Note any signs of manipulation."}]}]},timeout=30)
        r.raise_for_status(); return r.json()["content"][0]["text"].strip()
    except Exception as e: log.error("OCR: %s",e); return ""
def transcribe(b,mime):
    try:
        ext={"audio/ogg":"ogg","audio/mpeg":"mp3","video/mp4":"mp4"}.get(mime,"ogg")
        with tempfile.NamedTemporaryFile(suffix=f".{ext}",delete=False) as f: f.write(b); path=f.name
        with open(path,"rb") as f: r=requests.post("https://api.openai.com/v1/audio/transcriptions",headers={"Authorization":f"Bearer {OPENAI_API_KEY}"},files={"file":(f"a.{ext}",f,mime)},data={"model":"whisper-1"},timeout=60)
        os.unlink(path); r.raise_for_status(); return r.json().get("text","").strip()
    except Exception as e: log.error("Transcribe: %s",e); return ""
def google_fc(query):
    try:
        r=requests.get(GOOGLE_FC_URL,params={"key":GOOGLE_API_KEY,"query":query[:200],"pageSize":8},timeout=10); r.raise_for_status()
        out=[]
        for c in r.json().get("claims",[]):
            for rv in c.get("claimReview",[]):
                out.append({"source":rv.get("publisher",{}).get("name",""),"rating":rv.get("textualRating",""),"claim":c.get("text","")[:200],"url":rv.get("url","")})
        return out
    except Exception as e: log.error("GFC: %s",e); return []
def scrape_sites(query):
    q=quote_plus(query[:100])
    sites=[("Snopes",f"https://www.snopes.com/?s={q}"),("FullFact",f"https://fullfact.org/search/?q={q}"),("FactCheck.org",f"https://www.factcheck.org/?s={q}"),("PolitiFact",f"https://www.politifact.com/search/?q={q}"),("AFP",f"https://factcheck.afp.com/?q={q}")]
    out=[]
    for name,url in sites:
        txt=fetch(url,timeout=8)
        if txt and len(txt)>150: out.append(f"[{name}]: {txt[:400]}")
    return "\n\n".join(out)
def estimate_cost(st):
    base={"text":0.0085,"url":0.0095,"image":0.0110,"audio":0.0120,"video":0.0150,"document":0.0095}
    return base.get(st,0.0085)
def claude_analyse(claim,google,scraped,st):
    g="\n".join([f"• {x['source']} [{x['rating']}]: {x['claim']}\n  {x['url']}" for x in google[:5]])
    prompt=f"""Fact-check this claim (source: {st}).\n\nCLAIM:\n\"\"\"{claim[:800]}\"\"\"\n\nGOOGLE FACT CHECK:\n{g or 'No matches.'}\n\nFACT-CHECK SITES:\n{scraped[:1500] or 'No results.'}\n\nRespond ONLY with valid JSON:\n{{"rating":"TRUE|MOSTLY TRUE|HALF TRUE|MOSTLY FALSE|FALSE|PANTS ON FIRE|UNVERIFIABLE|MISLEADING|NEEDS CONTEXT","verdict":"2-3 sentence verdict with evidence","key_facts":["fact1","fact2","fact3","fact4"],"context":"background context","red_flags":["flag1","flag2"],"media_bias":"bias note or empty","sources":["Name — URL","Name — URL","Name — URL","Name — URL"],"confidence":"HIGH|MEDIUM|LOW","confidence_reason":"reason"}}"""
    try:
        r=requests.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":"claude-sonnet-4-6","max_tokens":2000,"system":SYSTEM,"messages":[{"role":"user","content":prompt}]},timeout=45)
        r.raise_for_status(); text=r.json()["content"][0]["text"]
        s=text.find("{"); e=text.rfind("}")+1
        if s>=0 and e>s: return json.loads(text[s:e])
    except Exception as e: log.error("Claude: %s",e)
    return {"rating":"UNVERIFIABLE","verdict":"Analysis failed.","key_facts":[],"context":"","red_flags":[],"media_bias":"","sources":["Google FC — https://toolbox.google.com/factcheck/explorer","Snopes — https://www.snopes.com","FullFact — https://fullfact.org"],"confidence":"LOW","confidence_reason":"Unavailable"}
def fmt_report(claim,a,st,cost):
    rating=a.get("rating","UNVERIFIABLE").upper()
    src={"text":"Text message","image":"Image / Screenshot","audio":"Voice note","video":"Video","url":"Article / Link","document":"Document"}
    HDR="*━━━━━━━━━━━━━━━━━━━━*"
    lines=[
        HDR,"*FACTCHECK PRO*",
        f"_{src.get(st,st)}_",HDR,"",
        "*TRUTH-O-METER*","",
        verdict_block(rating),"",HDR,"",
        "*CLAIM REVIEWED*",f"_{claim[:240]}_","",
        "*ANALYSIS*",a.get("verdict",""),"",
    ]
    if a.get("key_facts"):
        lines+=["*KEY FACTS*"]
        for i,f in enumerate(a["key_facts"][:4],1): lines.append(f"{i}. {f}")
        lines.append("")
    if a.get("context"):
        lines+=["*CONTEXT*",a["context"][:320],""]
    if a.get("red_flags"):
        lines+=["*RED FLAGS*"]
        for f in a["red_flags"][:3]: lines.append(f"- {f}")
        lines.append("")
    if a.get("media_bias"):
        lines+=["*BIAS NOTE*",a["media_bias"][:180],""]
    conf=a.get("confidence","LOW")
    lines+=[
        "*CONFIDENCE*",f"*{conf}*",
        f"_{a.get('confidence_reason','')[:120]}_","",
    ]
    if a.get("sources"):
        lines+=["*SOURCES*"]
        for s in a["sources"][:4]: lines.append(f"> {s}")
        lines.append("")
    lines+=[HDR,f"_Cost: ${cost:.4f} · FactCheck Pro v3_","_Snopes · FullFact · PolitiFact · AFP_"]
    return "\n".join(lines)


def confirm_msg(st,preview,cost):
    src={"text":"Text","image":"Image","audio":"Voice Note","video":"Video","url":"Article","document":"Document"}
    HDR="*━━━━━━━━━━━━━━━━━━━━*"
    return (
        f"{HDR}\n*FACTCHECK PRO*\n_{src.get(st,st)}_\n{HDR}\n\n"
        f"*CLAIM PREVIEW*\n_{preview[:180]}_\n\n"
        f"_Est. cost: ${cost:.4f}_\n\n"
        f"Reply *Y* to fact-check\n"
        f"Reply *N* to cancel"
    )


def send(to,text):
    for chunk in [text[i:i+4000] for i in range(0,len(text),4000)]:
        try: requests.post(WHATSAPP_URL,json={"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":chunk}},headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}","Content-Type":"application/json"},timeout=10).raise_for_status()
        except Exception as e: log.error("Send: %s",e)
def run_check(from_num,query,st,img_bytes,cost):
    send(from_num,"⚙️ Cross-referencing Snopes, FullFact, PolitiFact, AFP, FactCheck.org, Google FC...")
    g=google_fc(query); sc=scrape_sites(query)
    a=claude_analyse(query,g,sc,st)
    send(from_num,fmt_report(query,a,st,cost))
def clean_query(q):
    lines=[l for l in q.split("\n") if not l.startswith("#") and not l.startswith("**") and l.strip()]
    return "\n".join(lines).strip()
def process(from_num,message):
    msg_id=message.get("id",""); msg_time=int(message.get("timestamp",0))
    with processed_lock:
        if msg_id in processed_ids: return
        if t.time()-msg_time>60: log.info("Stale, ignored"); return
        processed_ids.add(msg_id)
    msg_type=message.get("type")
    if msg_type=="text":
        body=message["text"]["body"].strip()
        body_upper=body.upper()
        # Only treat as Y/N if the message IS just Y/N — not a URL or sentence
        is_yn = body_upper in ("YES","Y","NO","N") or (len(body) < 10 and body_upper in ("YES","Y","NO","N"))
        with pending_lock: has_p=from_num in pending; data=pending.get(from_num)
        if has_p and is_yn:
            if body_upper in ("YES","Y"):
                with pending_lock: data=pending.pop(from_num)
                send(from_num,"Starting fact-check..."); run_check(from_num,data["query"],data["source_type"],data.get("image_bytes"),data["cost"]); return
            elif body_upper in ("NO","N"):
                with pending_lock: pending.pop(from_num,None)
                send(from_num,"Cancelled."); return
        elif has_p and not is_yn:
            # New content sent while pending — clear old pending and process new message
            with pending_lock: pending.pop(from_num,None)
            log.info("New content received, clearing stale pending")
    query,source_type,image_bytes="","text",None
    if msg_type=="text":
        body=message["text"]["body"].strip()
        urls=[w for w in body.split() if w.startswith("http")]
        if urls:
            url=urls[0]
            # Detect video platforms — fetch page text for claim context
            video_domains=["tiktok.com","youtube.com","youtu.be","twitter.com","x.com","instagram.com","facebook.com","fb.watch","rumble.com","bitchute.com","t.me"]
            is_video_link=any(d in url for d in video_domains)
            if is_video_link:
                send(from_num,"Video link detected. Fetching page content...")
                page_text=fetch(url) or ""
                # Use URL + any page text we can get as the claim
                query=f"Video URL: {url}\n\nPage content: {page_text[:600]}" if page_text else f"Video URL: {url}\n\nClaim visible in thumbnail/title: {body}"
                source_type="url"
            else:
                send(from_num,"Fetching article...")
                query=fetch(url) or body
                source_type="url"
        else:
            query,source_type=body,"text"
    elif msg_type=="image":
        send(from_num,"🖼 Analysing image..."); image_bytes=download_media(message["image"]["id"])
        if image_bytes: query=ocr_image(image_bytes)
        source_type="image"
        if not query: send(from_num,"⚠️ Could not analyse image."); return
    elif msg_type=="audio":
        send(from_num,"🎤 Transcribing..."); b=download_media(message["audio"]["id"])
        if b: query=transcribe(b,message["audio"].get("mime_type","audio/ogg"))
        source_type="audio"
        if not query: send(from_num,"⚠️ Could not transcribe."); return
    elif msg_type=="video":
        send(from_num,"Processing video...")
        b=download_media(message["video"].get("id",""))
        if b:
            query=transcribe(b,message["video"].get("mime_type","video/mp4"))
        source_type="video"
        if not query:
            send(from_num,"Could not transcribe video audio. If this is a TikTok/YouTube link, please send the URL as a text message instead.")
            return
    elif msg_type=="document":
        send(from_num,"📄 Reading..."); b=download_media(message["document"]["id"])
        if b: query=b.decode("utf-8",errors="ignore")[:2000]
        source_type="document"
        if not query: send(from_num,"⚠️ Could not read."); return
    else: send(from_num,f"⚠️ Unsupported: {msg_type}"); return
    if not query: send(from_num,"⚠️ Could not extract content."); return
    query=clean_ocr(query) if source_type=='image' else query
    query=query.strip()[:800]
    log.info("Received [%s]: %s",source_type,query[:100])
    cost=estimate_cost(source_type)
    with pending_lock: pending[from_num]={"query":query,"source_type":source_type,"image_bytes":image_bytes,"cost":cost,"timestamp":t.time()}
    send(from_num,confirm_msg(source_type,query,cost))
@app.route("/webhook",methods=["GET"])
def verify():
    if request.args.get("hub.mode")=="subscribe" and request.args.get("hub.verify_token")==VERIFY_TOKEN:
        return request.args.get("hub.challenge"),200
    return "Forbidden",403
@app.route("/webhook",methods=["POST"])
def receive():
    data=request.get_json()
    try:
        value=data["entry"][0]["changes"][0]["value"]
        if "messages" in value:
            msg=value["messages"][0]
            log.info("MSG TYPE=%s FROM=%s KEYS=%s", msg.get("type"), msg.get("from"), list(msg.keys()))
            if msg.get("type")=="text":
                log.info("TEXT BODY=%s", msg.get("text",{}).get("body","")[:200])
            process(msg["from"],msg)
    except(KeyError,IndexError) as e: log.warning("Parse: %s",e)
    return jsonify({"status":"ok"}),200
if __name__=="__main__":
    missing=[k for k,v in {"WHATSAPP_TOKEN":WHATSAPP_TOKEN,"PHONE_NUMBER_ID":PHONE_NUMBER_ID,"GOOGLE_FACT_CHECK_API_KEY":GOOGLE_API_KEY,"ANTHROPIC_API_KEY":ANTHROPIC_KEY}.items() if not v]
    if missing: raise ValueError(f"Missing: {', '.join(missing)}")
    log.info("FactCheck Pro v3 starting...")
    app.run(host="0.0.0.0",port=5000,debug=False)
