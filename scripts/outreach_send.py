#!/usr/bin/env python3
"""
Fred Check — Daily outreach email sender.
Reads outreach/recipients.csv, sends up to DAILY_LIMIT personalised emails via SendGrid,
marks them sent, then emails Omar a daily report including manual X DMs to send.

Run via Railway cron or manually:  python3 scripts/outreach_send.py
"""

import csv
import json
import os
import sys
import urllib.request as _ur
from datetime import date
from pathlib import Path

REPO_ROOT      = Path(__file__).resolve().parent.parent
RECIPIENTS_CSV = REPO_ROOT / "outreach" / "recipients.csv"
SENDGRID_KEY   = os.environ.get("SENDGRID_API_KEY", "")
FROM_EMAIL     = "hello@fredcheck.com"
FROM_NAME      = "Omar · Fred Check"
REPORT_TO      = "omartanveeraslam@gmail.com"
DAILY_LIMIT    = 40  # stay well under SendGrid 100/day free tier

# ── Email templates by segment ─────────────────────────────────────────────

TEMPLATES = {
    "aljazeera": {
        "subject": "A fact-checker built for the global media landscape — not just Western sources",
        "body": """Hi {name},

Your reporting on {beat} covers stories where the standard fact-checking tools fall short — they're built around Western source hierarchies that treat Reuters silence as uncertainty and AFP as the default neutral.

I've built Fred: a fact-checker that searches 65+ outlets simultaneously across Arabic, Francophone, Russian, Chinese, Spanish and Western media — no default narrative, no hierarchy. Send a claim via WhatsApp or web and get a sourced verdict in under 60 seconds.

Fred is built on a published constitutional charter (fredcheck.com/charter) that explicitly prohibits conflict source hierarchy and Western framing bias. Al Jazeera Arabic, TRT World, and Dawn carry exactly the same evidentiary weight as AP.

7-day free trial, no card needed: fredcheck.com

Happy to demo if useful — just reply here.

Omar
Fred Check · fredcheck.com"""
    },

    "investigative": {
        "subject": "Fact-checking tool built for journalism that challenges official narratives",
        "body": """Hi {name},

{outlet}'s work on {beat} is exactly the context Fred was built for — stories where the mainstream fact-checkers either won't touch the claim or don't have the source breadth to check it properly.

Fred searches 65+ outlets across Arabic, Francophone, Russian, Chinese, Spanish and Western media simultaneously and returns a sourced verdict in under 60 seconds — via WhatsApp or web. It's built on a public charter that explicitly prohibits: treating official denials as disproof, conspiracy dismissal by label, and cover-up deference.

For your journalists working on investigations, it can corroborate or refute a claim across the entire global media landscape before filing — not just the wire services.

7-day free trial, no card: fredcheck.com

Omar
Fred Check · fredcheck.com"""
    },

    "mee": {
        "subject": "Fact-checking tool built for journalism that challenges official narratives",
        "body": """Hi {name},

Middle East Eye's work on {beat} is exactly the context Fred was built for — stories where the mainstream fact-checkers either won't touch the claim or don't have the source breadth to check it properly.

Fred searches 65+ outlets across Arabic, Francophone, Russian, Chinese, Spanish and Western media simultaneously and returns a sourced verdict in under 60 seconds. It's built on a public charter that explicitly prohibits conflict source hierarchy, Western framing bias, and treating official denials as disproof.

For your journalists working on investigations it can corroborate or refute a claim across the entire global media landscape — not just the wire services.

7-day free trial, no card: fredcheck.com

Omar
Fred Check · fredcheck.com"""
    },

    "factchecker": {
        "subject": "Fred Check — potential tool partnership or access for your team",
        "body": """Hi {name},

I'm reaching out because {outlet}'s work in {beat} is the context Fred was built for.

Fred is an AI fact-checking tool that searches 65+ sources simultaneously across Arabic, Francophone, Russian, Chinese, Spanish and Western media — returning a sourced verdict in under 60 seconds via WhatsApp or web. It's built on a public constitutional charter that explicitly requires equal weight for non-Western outlets and prohibits Western source hierarchy.

I think there are a few natural ways to work together — tool access for your team, source network sharing, or API integration into your existing workflow. I'd genuinely value your feedback as a fellow fact-checker.

2-week free trial, no commitment: fredcheck.com

Omar
Fred Check · fredcheck.com"""
    },

    "global_south": {
        "subject": "Fact-checking tool — 65+ sources across Arabic, Urdu, French, Chinese and Western media",
        "body": """Hi {name},

Most fact-checking tools treat Western wire services as the default and everything else as supporting evidence. Fred doesn't.

Fred searches 65+ outlets simultaneously — Arabic, Francophone, Russian, Chinese, Spanish, South Asian and Western — and returns a sourced verdict in under 60 seconds. It's built on a published charter (fredcheck.com/charter) that treats Dawn, Al Jazeera Arabic, and Xinhua as equal primary sources alongside Reuters and AP.

Given {outlet}'s readership I think your journalists would find it directly useful for international stories — particularly anything touching MENA, South Asia, or geopolitics where the wire services lag or frame poorly.

7-day free trial, no card: fredcheck.com

Omar
Fred Check · fredcheck.com"""
    },

    "freelance": {
        "subject": "A fact-checking tool built for international reporters (not just Western ones)",
        "body": """Hi {name},

Your work on {beat} covers stories where the standard fact-checking tools fall short — they're built around Western sources and treat non-Western corroboration as secondary.

I've built Fred: a fact-checker that searches Arabic, Francophone, Russian, Chinese and Western media simultaneously — no default hierarchy. Send a claim via WhatsApp or web and get a sourced verdict in under 60 seconds.

It's built specifically for journalists covering international stories where Al Jazeera Arabic, Le Monde, TRT World, and Dawn are as relevant as Reuters.

7-day free trial, no card needed: fredcheck.com

Happy to show you a demo if useful.

Omar
Fred Check · fredcheck.com"""
    },
}

UNSUBSCRIBE_FOOTER = """

--
Fred Check · fredcheck.com
To unsubscribe from these emails reply with UNSUBSCRIBE."""

X_DM_TEMPLATES = {
    "aljazeera":    "Hi {name} — I've built a fact-checker that searches Arabic, Francophone, Russian, Chinese + Western sources simultaneously. No Western default. Al Jazeera Arabic carries the same weight as AP. Free trial: fredcheck.com — would value your feedback.",
    "mee":          "Hi {name} — built a fact-checker with no Western default: Arabic, French, Russian, Chinese + Western sources simultaneously, 60-second verdict. Charter at fredcheck.com/charter explicitly bans conflict source hierarchy. Free trial: fredcheck.com",
    "investigative":"Hi {name} — built a fact-checker for investigations: 65+ sources across Arabic, Francophone, Russian, Chinese + Western media. Explicitly prohibits cover-up deference and dismissal by label. Free trial: fredcheck.com",
    "factchecker":  "Hi {name} — fellow fact-checker here. Built Fred: 65+ sources across Arabic, French, Russian, Chinese + Western media, 60-second verdict, public charter. Think there's a natural fit. Free trial at fredcheck.com — would love your thoughts.",
    "global_south": "Hi {name} — built a fact-checker that treats Dawn, Al Jazeera Arabic and Xinhua as equal primary sources — not supporting evidence awaiting Western confirmation. 65+ sources, 60-second verdict. Free trial: fredcheck.com",
    "freelance":    "Hi {name} — I've built a fact-checker that searches Arabic, Francophone, Russian, Chinese + Western sources simultaneously. No default hierarchy. Built for journalists covering stories where the wire services lag or frame poorly. Free trial: fredcheck.com",
}


def _send_email(to_email, to_name, subject, body):
    """Send a single email via SendGrid. Returns (success, error_msg)."""
    if not SENDGRID_KEY:
        return False, "No SENDGRID_API_KEY"
    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email, "name": to_name}]}],
        "from": {"email": FROM_EMAIL, "name": FROM_NAME},
        "reply_to": {"email": FROM_EMAIL, "name": FROM_NAME},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body + UNSUBSCRIBE_FOOTER}]
    }).encode()
    try:
        req = _ur.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
            method="POST"
        )
        with _ur.urlopen(req, timeout=15) as r:
            return r.status in (200, 202), ""
    except Exception as e:
        return False, str(e)


def _send_report(today_str, sent_list, skipped_list, x_dm_list):
    """Email Omar a daily report with results and X DMs to send manually."""
    if not SENDGRID_KEY:
        print("No SENDGRID_API_KEY — skipping report email")
        return

    lines = [
        f"Fred Check — Outreach Report {today_str}",
        f"{'='*50}",
        f"",
        f"EMAILS SENT TODAY: {len(sent_list)}",
        f"SKIPPED (X-only or error): {len(skipped_list)}",
        f"",
    ]

    if sent_list:
        lines.append("── EMAILS SENT ──")
        for r in sent_list:
            lines.append(f"  ✓ {r['name']} · {r['outlet']} · {r['email']}")
        lines.append("")

    if skipped_list:
        lines.append("── SKIPPED / ERRORS ──")
        for r in skipped_list:
            lines.append(f"  ✗ {r['name']} · {r['outlet']} · {r.get('error','x_only')}")
        lines.append("")

    if x_dm_list:
        lines.append("── X DMs TO SEND MANUALLY ──")
        lines.append("Copy-paste each DM and send from your @FredCheck X account:")
        lines.append("")
        for r in x_dm_list:
            tmpl = X_DM_TEMPLATES.get(r['segment'], X_DM_TEMPLATES['freelance'])
            dm = tmpl.format(name=r['name'].split()[0])
            lines.append(f"  TO: @{r['x_handle']}")
            lines.append(f"  → {dm}")
            lines.append("")

    lines += [
        "── REMAINING IN PIPELINE ──",
        f"  Check outreach/recipients.csv for full status.",
        "",
        "Fred Check · fredcheck.com",
    ]

    body = "\n".join(lines)
    payload = json.dumps({
        "personalizations": [{"to": [{"email": REPORT_TO, "name": "Omar"}]}],
        "from": {"email": FROM_EMAIL, "name": "Fred Check"},
        "subject": f"📬 Outreach report {today_str} — {len(sent_list)} sent, {len(x_dm_list)} X DMs to send",
        "content": [{"type": "text/plain", "value": body}]
    }).encode()
    try:
        req = _ur.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
            method="POST"
        )
        with _ur.urlopen(req, timeout=15) as r:
            print(f"Report sent to {REPORT_TO} (HTTP {r.status})")
    except Exception as e:
        print(f"Failed to send report: {e}")


def run():
    today_str = date.today().isoformat()

    if not RECIPIENTS_CSV.exists():
        print(f"Recipients file not found: {RECIPIENTS_CSV}")
        sys.exit(1)

    rows = []
    with open(RECIPIENTS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    pending_email = [r for r in rows if r["status"] == "pending" and r["email"].strip()]
    pending_x     = [r for r in rows if r["status"] == "x_only"  and r["x_handle"].strip()]

    to_send   = pending_email[:DAILY_LIMIT]
    x_dm_list = pending_x  # always include all pending X DMs in report

    sent_list    = []
    skipped_list = []

    for r in to_send:
        segment  = r["segment"].strip()
        tmpl     = TEMPLATES.get(segment, TEMPLATES["freelance"])
        subject  = tmpl["subject"]
        body     = tmpl["body"].format(
            name=r["name"].split()[0],
            outlet=r["outlet"],
            beat=r["beat"],
            role=r["role"],
        )
        ok, err = _send_email(r["email"].strip(), r["name"].strip(), subject, body)
        if ok:
            r["status"]    = "sent"
            r["sent_date"] = today_str
            sent_list.append(r)
            print(f"  ✓ Sent → {r['name']} <{r['email']}>")
        else:
            r["status"] = "error"
            r["sent_date"] = today_str
            skipped_list.append({**r, "error": err})
            print(f"  ✗ Failed → {r['name']}: {err}")

    # Write updated CSV
    if to_send:
        fieldnames = list(rows[0].keys())
        with open(RECIPIENTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    # Send daily report to Omar
    _send_report(today_str, sent_list, skipped_list, x_dm_list)

    print(f"\nDone. {len(sent_list)} sent, {len(skipped_list)} errors, {len(x_dm_list)} X DMs in report.")


if __name__ == "__main__":
    run()
