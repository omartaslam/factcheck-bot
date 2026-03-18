#!/usr/bin/env python3
"""
Fred test runner — tests the full pipeline via the /test endpoint on Railway.
No WhatsApp needed. All API keys used are Railway's live keys.

Usage:
  python3 test_format.py                    # run all default test cases
  python3 test_format.py "your claim"       # test a single claim
  python3 test_format.py --url http://...   # override Railway URL

Each test shows: verdict, confidence, truncation warning, full formatted output.
"""
import sys, os, json, requests

HOST = os.getenv("TEST_HOST", "https://web-production-1f0a4.up.railway.app")
TOKEN = os.getenv("VERIFY_TOKEN", "")

# Default test claims with expected verdicts (None = no assertion)
TEST_CASES = [
    {"claim": "Silverstein leased the World Trade Center six weeks before 9/11",
     "expect": "TRUE",
     "type": "text"},
    {"claim": "Silverstein recently bought US Bank Tower in Los Angeles",
     "expect": "MOSTLY TRUE",
     "type": "text"},
    {"claim": "Mark Carney called America a mafia state at WEF",
     "expect": None,
     "type": "text"},
]

def run_test(claim, source_type="text", expect=None):
    print(f"\n{'='*65}")
    print(f"CLAIM : {claim}")
    if expect:
        print(f"EXPECT: {expect}")
    print('='*65)

    try:
        r = requests.post(
            f"{HOST}/test",
            json={"claim": claim, "type": source_type, "token": TOKEN},
            timeout=90
        )
        if r.status_code == 403:
            print("❌ ERROR: Wrong VERIFY_TOKEN. Set env var: export VERIFY_TOKEN=your_token")
            return False
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"❌ REQUEST FAILED: {e}")
        return False

    if "error" in data:
        print(f"❌ ERROR: {data['error']}")
        return False

    verdict = data.get("verdict", "?")
    confidence = data.get("confidence", "?")
    truncated = data.get("truncated", False)
    passed = (expect is None) or (verdict == expect)

    print(f"\nVERDICT    : {verdict}  ({confidence})")
    if data.get("rating_reason"):
        print(f"REASON     : {data['rating_reason']}")
    print(f"TRUNCATED  : {'⚠️  YES' if truncated else '✅ NO'}")
    print(f"RESULT     : {'✅ PASS' if passed else f'❌ FAIL (expected {expect})'}")
    print(f"\n--- FORMATTED OUTPUT ---\n")
    print(data.get("formatted_output", ""))

    return passed and not truncated

if __name__ == "__main__":
    args = sys.argv[1:]

    # Parse --url override
    if "--url" in args:
        idx = args.index("--url")
        HOST = args[idx+1]
        args = [a for i,a in enumerate(args) if i not in (idx, idx+1)]

    if not TOKEN:
        print("⚠️  VERIFY_TOKEN not set. Export it first:")
        print("   export VERIFY_TOKEN=your_token")
        print("   (find it in Railway env vars)\n")

    if args:
        cases = [{"claim": " ".join(args), "type": "text", "expect": None}]
    else:
        cases = TEST_CASES

    results = []
    for case in cases:
        ok = run_test(case["claim"], case.get("type", "text"), case.get("expect"))
        results.append(ok)

    print(f"\n{'='*65}")
    print(f"SUMMARY: {sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
