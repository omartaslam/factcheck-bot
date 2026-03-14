import requests
import os

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

def factcheck_claim(text):

```
prompt = f"""
```

Fact check the following claim and return a short verdict.

Claim:
{text}
"""

```
r = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={
        "x-api-key":ANTHROPIC_KEY,
        "anthropic-version":"2023-06-01",
        "content-type":"application/json"
    },
    json={
        "model":"claude-sonnet-4",
        "max_tokens":600,
        "messages":[{"role":"user","content":prompt}]
    }
)

return r.json()["content"][0]["text"]
```
