#!/usr/bin/env python3
"""
Test Fred's formatting locally without WhatsApp.
Usage: python3 test_format.py "your claim here"
       python3 test_format.py  # runs default test claims
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Patch out WhatsApp/webhook dependencies before importing bot
import unittest.mock as mock
with mock.patch.dict(os.environ, {"WHATSAPP_TOKEN": "x", "VERIFY_TOKEN": "x"}):
    from bot import analyse, fmt_report, _trunc

TEST_CLAIMS = [
    "Silverstein leased the World Trade Center six weeks before 9/11",
    "Silverstein recently bought US Bank Tower in Los Angeles",
    "Mark Carney called America a mafia state at WEF",
]

def run_test(claim):
    print(f"\n{'='*60}")
    print(f"CLAIM: {claim}")
    print('='*60)
    try:
        result = analyse(claim, "text")
        if not result:
            print("ERROR: analyse() returned None")
            return
        report = fmt_report(claim, result, "text", 0)
        print(report)
        # Check for truncation
        if '…' in report:
            print("\n⚠️  WARNING: truncation detected (…) in output")
        else:
            print("\n✅ No truncation detected")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()

if __name__ == "__main__":
    claims = sys.argv[1:] if len(sys.argv) > 1 else TEST_CLAIMS
    for claim in claims:
        run_test(claim)
