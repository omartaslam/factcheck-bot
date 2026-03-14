import sqlite3

conn = sqlite3.connect("claims.db",check_same_thread=False)

cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS claims(
id INTEGER PRIMARY KEY,
claim TEXT,
verdict TEXT
)
""")

def get_claim(claim):

```
cursor.execute("SELECT verdict FROM claims WHERE claim=?",(claim,))
result = cursor.fetchone()

if result:
    return result[0]

return None
```

def save_claim(claim,verdict):

```
cursor.execute(
    "INSERT INTO claims(claim,verdict) VALUES (?,?)",
    (claim,verdict)
)

conn.commit()
```
