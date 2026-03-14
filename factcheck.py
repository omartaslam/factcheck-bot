from ai import factcheck_claim
from database import get_claim, save_claim

def run_factcheck(text):

```
cached = get_claim(text)

if cached:
    return f"Previously fact-checked:\n\n{cached}"

verdict = factcheck_claim(text)

save_claim(text,verdict)

return verdict
```
