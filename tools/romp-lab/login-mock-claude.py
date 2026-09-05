#!/usr/bin/env python3
# The MOCKED login CLI for the T157 lab phase — reproduces the probed 2.1.221 login transcript
# shape (trust gate, REPL hints, /login, method picker, OSC-8-wrapped code=true URL, paste prompt,
# verdict). SYNTHETIC ONLY: the URL is inert, the accepted code is a fixture string, and nothing
# touches any credential store. Never a real account in tests (the T157 house rule).
import sys, hashlib, os

def say(s):
    sys.stdout.write(s)
    sys.stdout.flush()

say("Do you trust this folder?\n1. Yes, I trust this folder\n")
sys.stdin.readline()
say("Welcome back! Tips for getting started\n> Try \"how do I log an error?\"\n")
line = sys.stdin.readline()
if "/login" not in line:
    say("Not logged in\n")
    sys.exit(1)
say("Select login method:\n1. Claude account with subscription\n2. Anthropic Console account\n")
sys.stdin.readline()
url = ("https://claude.com/cai/oauth/authorize?code=true&client_id=SYNTHETIC"
       "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
       "&scope=user%3Aprofile+user%3Ainference&code_challenge=SYNTHETIC-CHALLENGE"
       "&code_challenge_method=S256&state=LABFIXTURE")
say("Browser didn't open? Use the url below to sign in\n")
say("\x1b]8;id=1;" + url + "\x1b\\" + url + "\x1b]8;;\x1b\\\n")
say("Paste code here if prompted >\n")
code = sys.stdin.readline().strip()
marker = os.environ.get("LOGIN_MOCK_MARKER")
if marker:
    open(marker, "w").write(hashlib.sha256(code.encode()).hexdigest())
if code == "LAB-SYNTH-CODE":
    say("Logged in as Lab Fixture (lab@example.invalid)\n")
else:
    say("Invalid code. Login error.\n")
