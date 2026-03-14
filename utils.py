import requests
import os

PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")

URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

def send_whatsapp_message(to,text):

```
requests.post(
    URL,
    headers={
        "Authorization":f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type":"application/json"
    },
    json={
        "messaging_product":"whatsapp",
        "to":to,
        "type":"text",
        "text":{"body":text}
    }
)
```
