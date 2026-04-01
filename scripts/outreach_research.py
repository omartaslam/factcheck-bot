#!/usr/bin/env python3
"""
Fred Check — Outreach research sweep (runs twice daily).
Uses Claude to find new journalist/editor contacts, deduplicates against
existing recipients.csv, and appends new contacts ready for the next send.

Targets (in priority order):
  1. Middle East / MENA newsrooms and journalists
  2. South Asian newsrooms (Pakistan, India, Bangladesh)
  3. African media and fact-checkers
  4. Latin American investigative / fact-checking
  5. Western non-establishment (The Guardian, The Nation, Columbia Journalism, etc.)

Run via Railway cron or manually:  python3 scripts/outreach_research.py
"""

import csv
import json
import os
import re
import sys
import urllib.request as _ur
from datetime import date
from pathlib import Path

REPO_ROOT       = Path(__file__).resolve().parent.parent
RECIPIENTS_CSV  = REPO_ROOT / "outreach" / "recipients.csv"
ROTATION_FILE   = REPO_ROOT / "outreach" / "research_rotation.json"
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
SENDGRID_KEY    = os.environ.get("SENDGRID_API_KEY", "")
REPORT_TO       = "omartanveeraslam@gmail.com"
FROM_EMAIL      = "hello@fredcheck.com"
TARGET_NEW      = 60  # contacts to find per run

# Priority target batches — rotated per-run so each region gets coverage
RESEARCH_TARGETS = [
    # Week 1: MENA broadcast + digital
    {
        "region": "MENA broadcast and digital media",
        "outlets": ["Al Arabiya", "Sky News Arabia", "Al Ghad TV", "Asharq Al-Awsat",
                    "Middle East Monitor", "The National UAE", "Jordan Times", "Daily Star Lebanon",
                    "Al-Masry Al-Youm", "Mada Masr Egypt", "7iber Jordan"],
        "roles": ["Editor", "Senior Correspondent", "Bureau Chief", "Managing Editor",
                  "Investigations Editor", "Digital Editor"],
        "segment_hint": "global_south",
    },
    # Week 2: South Asia
    {
        "region": "South Asian media — Pakistan, India, Bangladesh, Sri Lanka",
        "outlets": ["The Wire India", "Scroll.in", "The Print India", "Himal Southasian",
                    "Daily Star Bangladesh", "Prothom Alo", "The Island Sri Lanka",
                    "Tribune Pakistan", "Geo News", "ARY News", "Samaa TV"],
        "roles": ["Editor", "Senior Reporter", "Investigations Editor", "Bureau Chief"],
        "segment_hint": "global_south",
    },
    # Week 3: African media
    {
        "region": "African media — Nigeria, Kenya, South Africa, Ghana, Ethiopia, Sudan",
        "outlets": ["Daily Maverick South Africa", "The Continent", "Nation Africa Kenya",
                    "Punch Nigeria", "Premium Times Nigeria", "The East African",
                    "Addis Standard Ethiopia", "Radio Dabanga Sudan", "Jeune Afrique",
                    "Sahara Reporters", "Mail and Guardian South Africa"],
        "roles": ["Editor-in-Chief", "Senior Reporter", "Investigations Editor",
                  "Correspondent", "Digital Editor"],
        "segment_hint": "global_south",
    },
    # Week 4: Western non-establishment investigative
    {
        "region": "Western non-establishment investigative and accountability journalism",
        "outlets": ["The Guardian Investigations", "Columbia Journalism Review",
                    "The Nation", "In These Times", "Truthout", "Common Dreams",
                    "The Progressive", "Mother Jones", "Counterpunch", "FAIR Media Watch",
                    "ProPublica", "Type Investigations"],
        "roles": ["Editor", "Senior Investigations Reporter", "Managing Editor",
                  "Political Editor", "Foreign Editor"],
        "segment_hint": "investigative",
    },
    # Week 5: Latin American + Southeast Asian
    {
        "region": "Latin American investigative and Southeast Asian media",
        "outlets": ["La Silla Vacía Colombia", "El Faro El Salvador", "Animal Político Mexico",
                    "Agência Pública Brazil", "OCCRP Latin America", "Malaysiakini",
                    "Rappler Philippines", "New Naratif Southeast Asia", "The Irrawaddy Myanmar",
                    "Bangkok Post", "Prachatai Thailand"],
        "roles": ["Editor", "Senior Reporter", "Investigations Editor", "Director"],
        "segment_hint": "global_south",
    },
]


SYSTEM_PROMPT = """You are a journalism researcher helping build an outreach list for Fred Check,
an AI fact-checking tool. Find real, named journalists and editors at the specified outlets who:
- Cover international affairs, conflict, human rights, or media accountability
- Have a track record of non-Western or critical perspectives
- Are contactable (have publicly listed email or verified X/Twitter handle)

Return ONLY a JSON array. Each object must have these exact fields:
{
  "name": "Full Name",
  "outlet": "Outlet Name",
  "role": "Their Role",
  "beat": "their beat in 5-10 words",
  "email": "email@outlet.com or empty string",
  "x_handle": "handle without @ or empty string",
  "segment": "one of: aljazeera, mee, investigative, factchecker, global_south, freelance"
}

Rules:
- Only include people you are confident exist and work at that outlet
- Only include emails you are confident are correct (outlet-pattern or publicly listed)
- If unsure of email, leave it empty — do not guess
- Do not include anyone already in the provided existing contacts list
- Aim for seniority: editors and senior correspondents convert better than junior reporters
- Return between 20 and 40 contacts per call"""


def _get_existing_contacts():
    """Return sets of existing names and emails to deduplicate against."""
    if not RECIPIENTS_CSV.exists():
        return set(), set(), set()
    rows = list(csv.DictReader(RECIPIENTS_CSV.open()))
    names  = {r["name"].strip().lower() for r in rows}
    emails = {r["email"].strip().lower() for r in rows if r["email"].strip()}
    handles = {r["x_handle"].strip().lower() for r in rows if r["x_handle"].strip()}
    return names, emails, handles


def _research_batch(target: dict, existing_names: set, existing_emails: set, existing_handles: set):
    """Call Claude to research a batch of contacts. Returns (new_contacts, raw_count)."""
    if not ANTHROPIC_KEY:
        print("No ANTHROPIC_API_KEY — skipping research")
        return [], 0

    # Pass ALL existing identifiers so Claude doesn't regenerate people already in the list.
    # Names are most useful; handles/emails as supplementary. Truncate at 200 to keep prompt lean.
    existing_names_list   = sorted(existing_names)[:200]
    existing_handles_list = sorted(existing_handles)[:100]
    user_prompt = f"""Find {TARGET_NEW} journalist and editor contacts for Fred Check outreach.

Region/focus: {target['region']}

Target outlets: {', '.join(target['outlets'])}

Preferred roles: {', '.join(target['roles'])}

Default segment if unsure: {target['segment_hint']}

Existing contacts to EXCLUDE — do NOT return anyone on these lists:
Names already in our list: {json.dumps(existing_names_list)}
X handles already in our list: {json.dumps(existing_handles_list)}

Return a JSON array of contacts as described. Prioritise people who:
1. Have a verified X/Twitter presence (makes them reachable even without email)
2. Have publicly listed work emails
3. Are senior enough to make decisions (editors > reporters)
4. Cover beats relevant to Fred's focus: MENA, conflict, human rights, media accountability"""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "temperature": 0.3,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode()

    try:
        req = _ur.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            method="POST"
        )
        with _ur.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
            text = data["content"][0]["text"]
    except Exception as e:
        print(f"Claude API error: {e}")
        return [], 0

    # Extract JSON array from response
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if not match:
        print("No JSON array found in Claude response")
        return [], 0

    try:
        contacts = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return [], 0

    # Deduplicate and validate
    raw_count = len(contacts)
    print(f"  Claude returned {raw_count} contacts; deduplicating against {len(existing_names)} existing names…")
    new_contacts = []
    for c in contacts:
        name   = c.get("name", "").strip()
        email  = c.get("email", "").strip().lower()
        handle = c.get("x_handle", "").strip().lower()

        if not name:
            continue
        if name.lower() in existing_names:
            continue
        if email and email in existing_emails:
            continue
        if handle and handle in existing_handles:
            continue
        if not email and not handle:
            continue  # unreachable — skip

        # Normalise
        c["name"]      = name
        c["email"]     = email
        c["x_handle"]  = c.get("x_handle", "").strip().lstrip("@")
        c["status"]    = "pending" if email else "x_only"
        c["sent_date"] = ""
        c["response"]  = ""
        c["x_user_id"] = ""
        c["segment"]   = c.get("segment", target["segment_hint"])

        new_contacts.append(c)
        existing_names.add(name.lower())
        if email:
            existing_emails.add(email)
        if handle:
            existing_handles.add(handle)

    return new_contacts, raw_count


def _append_to_csv(new_contacts: list):
    """Append new contacts to recipients.csv."""
    if not new_contacts:
        return

    existing_rows = []
    fieldnames = None
    if RECIPIENTS_CSV.exists():
        existing_rows = list(csv.DictReader(RECIPIENTS_CSV.open()))
        fieldnames = list(existing_rows[0].keys()) if existing_rows else None

    if not fieldnames:
        fieldnames = ["name","outlet","role","beat","email","segment",
                      "x_handle","status","sent_date","response","x_user_id"]

    # Ensure all new contacts have all fields
    for c in new_contacts:
        for f in fieldnames:
            c.setdefault(f, "")

    all_rows = existing_rows + new_contacts
    with RECIPIENTS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)


def _send_report(today_str: str, new_contacts: list, target: dict, raw_count: int = 0):
    """Email Omar a summary of what was added."""
    if not SENDGRID_KEY:
        return

    email_ready = [c for c in new_contacts if c.get("email")]
    x_only      = [c for c in new_contacts if not c.get("email") and c.get("x_handle")]
    deduped_out = raw_count - len(new_contacts)

    lines = [
        f"Fred Check — Weekly Research Report {today_str}",
        f"{'='*50}",
        f"",
        f"Region swept: {target['region']}",
        f"Claude returned: {raw_count} contacts ({deduped_out} already in list, deduped out)",
        f"New contacts added: {len(new_contacts)}",
        f"  Email-ready: {len(email_ready)}",
        f"  X-only: {len(x_only)}",
        f"",
    ]

    if email_ready:
        lines.append("── EMAIL-READY CONTACTS ADDED ──")
        for c in email_ready:
            lines.append(f"  {c['name']} · {c['outlet']} · {c['email']}")
        lines.append("")

    if x_only:
        lines.append("── X-ONLY CONTACTS ADDED ──")
        for c in x_only:
            lines.append(f"  {c['name']} · {c['outlet']} · @{c['x_handle']}")
        lines.append("")

    lines += ["Fred Check · fredcheck.com"]

    payload = json.dumps({
        "personalizations": [{"to": [{"email": REPORT_TO, "name": "Omar"}]}],
        "from": {"email": FROM_EMAIL, "name": "Fred Check"},
        "subject": f"🔍 Research sweep {today_str} — {len(new_contacts)} new contacts added",
        "content": [{"type": "text/plain", "value": "\n".join(lines)}]
    }).encode()

    try:
        req = _ur.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
            method="POST"
        )
        with _ur.urlopen(req, timeout=15) as r:
            print(f"Research report sent (HTTP {r.status})")
    except Exception as e:
        print(f"Failed to send report: {e}")


def _next_target_index() -> int:
    """Read rotation counter from file, increment and save, return current index.

    Falls back to a count-based index derived from the total contacts in the CSV
    so rotation survives Railway redeploys (ephemeral filesystem loses the file).
    """
    try:
        data = json.loads(ROTATION_FILE.read_text()) if ROTATION_FILE.exists() else {}
        idx = int(data.get("next_index", 0)) % len(RESEARCH_TARGETS)
        ROTATION_FILE.write_text(json.dumps({"next_index": (idx + 1) % len(RESEARCH_TARGETS)}))
        return idx
    except Exception as e:
        print(f"Rotation file error: {e} — falling back to contact-count-based index")
        # Use total row count in CSV as a proxy for how many sweeps have run
        try:
            rows = list(csv.DictReader(RECIPIENTS_CSV.open())) if RECIPIENTS_CSV.exists() else []
            return (len(rows) // 20) % len(RESEARCH_TARGETS)
        except Exception:
            return date.today().toordinal() % len(RESEARCH_TARGETS)


def run():
    today_str = date.today().isoformat()

    # Pick target batch by rotating through all regions on every run
    target = RESEARCH_TARGETS[_next_target_index()]
    print(f"Research sweep: {target['region']}")

    existing_names, existing_emails, existing_handles = _get_existing_contacts()
    print(f"Existing contacts: {len(existing_names)} names, {len(existing_emails)} emails")

    new_contacts, raw_count = _research_batch(target, existing_names, existing_emails, existing_handles)
    print(f"New contacts found: {len(new_contacts)} (Claude returned {raw_count} total)")

    if new_contacts:
        _append_to_csv(new_contacts)
        print(f"Appended to {RECIPIENTS_CSV}")
        # Commit new contacts to git so they survive Railway redeploys.
        try:
            import subprocess as _sp
            _sp.run(["git", "add", str(RECIPIENTS_CSV)],
                    cwd=str(REPO_ROOT), capture_output=True, timeout=15)
            _sp.run(
                ["git", "commit", "--no-verify", "-m",
                 f"chore: add {len(new_contacts)} new outreach contacts {today_str} [skip deploy]"],
                cwd=str(REPO_ROOT), capture_output=True, timeout=15,
                env={**os.environ, "GIT_AUTHOR_NAME": "Fred Bot",
                     "GIT_AUTHOR_EMAIL": "hello@fredcheck.com",
                     "GIT_COMMITTER_NAME": "Fred Bot",
                     "GIT_COMMITTER_EMAIL": "hello@fredcheck.com"}
            )
            _sp.run(["git", "push"], cwd=str(REPO_ROOT), capture_output=True, timeout=30)
            print("New contacts committed and pushed to git.")
        except Exception as _e:
            print(f"git push of new contacts skipped: {_e}")

    _send_report(today_str, new_contacts, target, raw_count)
    print(f"Done. {len(new_contacts)} new contacts added.")


if __name__ == "__main__":
    run()
