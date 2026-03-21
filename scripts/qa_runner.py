#!/usr/bin/env python3
"""
Fred QA Runner — tests the full pipeline against qa_fixtures.json
Usage:
  python3 scripts/qa_runner.py                    # run all fixtures
  python3 scripts/qa_runner.py --id text-true-covid  # run one fixture
  python3 scripts/qa_runner.py --layer extraction    # run by layer
  python3 scripts/qa_runner.py --fast              # skip slow video fixtures

Results printed to stdout. Exit code 0 = all pass, 1 = failures.
"""

import argparse, json, os, sys, time, requests

BASE_URL  = os.getenv("FRED_BASE_URL", "https://fredcheck.com")
ADMIN_TOK = os.getenv("FRED_ADMIN_TOKEN", "qc-test-fred-2026")
POLL_SECS = 150   # max wait per fixture
POLL_INT  = 8

VALID_RATINGS = {
    "TRUE", "MOSTLY TRUE", "HALF TRUE", "NEEDS CONTEXT",
    "MOSTLY FALSE", "MISLEADING", "FALSE", "PANTS ON FIRE", "UNVERIFIABLE"
}

# ── helpers ────────────────────────────────────────────────────────────────────

def start_job(message):
    r = requests.post(f"{BASE_URL}/admin/qc",
                      headers={"X-Admin-Token": ADMIN_TOK, "Content-Type": "application/json"},
                      json={"message": message}, timeout=15)
    r.raise_for_status()
    return r.json()["job_id"]

def poll_job(job_id):
    deadline = time.time() + POLL_SECS
    while time.time() < deadline:
        time.sleep(POLL_INT)
        r = requests.get(f"{BASE_URL}/admin/qc/{job_id}",
                         headers={"X-Admin-Token": ADMIN_TOK}, timeout=15)
        if r.status_code == 404:
            return None, ["ERROR: job not found (server redeployed?)"]
        d = r.json()
        if d.get("done"):
            return d["messages"], None
    return None, [f"TIMEOUT after {POLL_SECS}s"]

def extract_verdict_rating(messages):
    """Pull rating from the verdict message."""
    for m in messages:
        for rating in VALID_RATINGS:
            if f"VERDICT: {rating}" in m:
                return rating
    return None

def extract_sources_count(messages):
    """Count cited sources from the verdict message."""
    import re
    for m in reversed(messages):
        match = re.search(r"searched (\d+)", m)
        if match:
            return int(match.group(1))
        # count bullet source lines
        lines = [l for l in m.split("\n") if l.strip().startswith("•") and "—" in l]
        if lines:
            return len(lines)
    return 0

def extract_claims_count(messages):
    """Count how many claims were identified."""
    import re
    for m in messages:
        match = re.search(r"Found (\d+) verifiable", m)
        if match:
            return int(match.group(1))
    return 0

def content_was_extracted(messages):
    """Returns (extracted: bool, content_len: int, unavailable: bool)."""
    full_text = " ".join(messages)
    unavailable = any(x in full_text for x in [
        "private, deleted, or restricted",
        "Could not access this video",
    ])
    # Content is in the query that gets processed — proxy: no error messages
    has_content = not unavailable and len(messages) >= 4
    content_len = max(len(m) for m in messages) if messages else 0
    return has_content, content_len, unavailable

# ── evaluator ─────────────────────────────────────────────────────────────────

def evaluate(fixture, messages):
    """Returns list of (check_name, pass: bool, detail: str)."""
    results = []
    layers = fixture.get("layer", [])
    expect = fixture.get("expect", {})
    full_text = " ".join(messages)

    # ── Extraction checks ──────────────────────────────────────────────────
    if "extraction" in layers:
        ex = expect.get("extraction", {})
        extracted, content_len, unavailable = content_was_extracted(messages)

        if ex.get("no_unavailable"):
            ok = not unavailable
            results.append(("extraction:no_unavailable", ok,
                            "OK" if ok else f"FAIL — unavailability message fired"))

        if "min_content_len" in ex:
            ok = content_len >= ex["min_content_len"]
            results.append(("extraction:min_content_len", ok,
                            f"longest msg={content_len} (need {ex['min_content_len']})"))

        if "contains" in ex:
            ok = ex["contains"].lower() in full_text.lower()
            results.append(("extraction:contains", ok,
                            f"expected '{ex['contains']}' in output"))

        video_failed = "Could not access video content" in full_text
        if ex.get("video_optional"):
            results.append(("extraction:video_optional", True,
                            f"video={'failed' if video_failed else 'ok'} (optional)"))
        elif "video" in fixture.get("source_type", ""):
            results.append(("extraction:video_present", not video_failed,
                            "OK" if not video_failed else "FAIL — video not extracted"))

    # ── Claim formulation checks ───────────────────────────────────────────
    if "claim" in layers:
        ex = expect.get("claims", {})
        n = extract_claims_count(messages)

        if not ex.get("allow_uncheckable") and "min" in ex:
            ok = n >= ex["min"]
            results.append(("claim:min_count", ok, f"found {n} claims (need ≥{ex['min']})"))

        if "max" in ex and n > 0:
            ok = n <= ex["max"]
            results.append(("claim:max_count", ok, f"found {n} claims (need ≤{ex['max']})"))

        if n == 0 and not ex.get("allow_uncheckable"):
            results.append(("claim:extracted", False, "FAIL — 0 claims identified"))

    # ── Verdict checks ─────────────────────────────────────────────────────
    if "verdict" in layers:
        ex = expect.get("verdict", {})
        rating = extract_verdict_rating(messages)
        sources = extract_sources_count(messages)

        if rating is None:
            results.append(("verdict:present", False, "FAIL — no verdict found in output"))
        else:
            results.append(("verdict:present", True, f"rating={rating}"))

            if "rating" in ex:
                ok = rating == ex["rating"]
                results.append(("verdict:rating", ok,
                                f"got {rating} (expected {ex['rating']})"))

            if "rating_in" in ex:
                ok = rating in ex["rating_in"]
                results.append(("verdict:rating_in", ok,
                                f"got {rating} (expected one of {ex['rating_in']})"))

        if "min_sources" in ex:
            ok = sources >= ex["min_sources"]
            results.append(("verdict:min_sources", ok,
                            f"found {sources} sources (need ≥{ex['min_sources']})"))

        if "confidence" in ex and rating:
            for m in messages:
                if "CONFIDENCE" in m:
                    conf_ok = any(c in m for c in ex["confidence"])
                    results.append(("verdict:confidence", conf_ok,
                                    f"confidence level check"))
                    break

    return results

# ── main ──────────────────────────────────────────────────────────────────────

def run_fixture(fixture, verbose=True):
    fid = fixture["id"]
    desc = fixture["description"]
    notes = fixture.get("notes", "")

    print(f"\n{'='*60}")
    print(f"FIXTURE: {fid}")
    print(f"  {desc}")
    if notes:
        print(f"  NOTE: {notes}")
    print(f"  INPUT: {fixture['input'][:80]}")

    try:
        job_id = start_job(fixture["input"])
        print(f"  Job: {job_id} — polling ({POLL_SECS}s max)...")
        messages, err = poll_job(job_id)
    except Exception as e:
        print(f"  ❌ ERROR starting job: {e}")
        return False, 0, 1

    if err:
        print(f"  ❌ {err[0]}")
        return False, 0, 1

    if verbose:
        print(f"  Messages received: {len(messages)}")
        for i, m in enumerate(messages):
            print(f"    [{i}] {m[:120].replace(chr(10),' ')}")

    checks = evaluate(fixture, messages)
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = sum(1 for _, ok, _ in checks if not ok)

    print(f"\n  Results ({passed} pass, {failed} fail):")
    for name, ok, detail in checks:
        icon = "✅" if ok else "❌"
        print(f"    {icon} {name}: {detail}")

    return failed == 0, passed, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id",    help="Run single fixture by ID")
    parser.add_argument("--layer", help="Run fixtures containing this layer")
    parser.add_argument("--fast",  action="store_true", help="Skip video fixtures")
    parser.add_argument("--quiet", action="store_true", help="Suppress message dump")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "qa_fixtures.json")) as f:
        fixtures = json.load(f)["fixtures"]

    if args.id:
        fixtures = [f for f in fixtures if f["id"] == args.id]
    if args.layer:
        fixtures = [f for f in fixtures if args.layer in f.get("layer", [])]
    if args.fast:
        fixtures = [f for f in fixtures if "video" not in f["id"]]

    if not fixtures:
        print("No fixtures matched.")
        sys.exit(1)

    print(f"Running {len(fixtures)} fixture(s) against {BASE_URL}")

    total_pass = total_fail = 0
    fixture_results = []

    for fix in fixtures:
        ok, p, f = run_fixture(fix, verbose=not args.quiet)
        total_pass += p
        total_fail += f
        fixture_results.append((fix["id"], ok))
        time.sleep(3)  # brief pause between fixtures

    print(f"\n{'='*60}")
    print(f"SUMMARY: {total_pass} checks passed, {total_fail} failed")
    print()
    for fid, ok in fixture_results:
        print(f"  {'✅' if ok else '❌'} {fid}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
