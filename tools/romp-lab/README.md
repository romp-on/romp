# romp-lab — a full, hermetic, headless romp you can drive in a loop

The standing capability T106 asked for (the user 2026-08-26): run a COMPLETE romp —
real kernel, real SDK sessions replying on the cheapest usable model, the real
dashboard in a headless browser — entirely in the background, reproduce a reported
behavior by scripting the user's exact flow, and iterate until it demonstrably
matches intent. ui-verify (next door) renders static fixtures; romp-lab runs the
whole stack.

## Hermeticity (non-negotiable)

`lab.sh` exports `XDG_STATE_HOME` into a fresh temp root BEFORE anything loads —
the never-load-romp-modules-against-live-state rule: a kernel booted against live
state reconciles it (resumes turns, spawns writers). Its own `ROMP_SERVE_TOKEN`,
its own free port, `ROMP_KERNEL_NO_OPEN=1` (nothing pops up), headless Chromium
(the playwright cache vscode-extension pins). Live state, live postal, and every
visible display stay untouched. Session content is SYNTHETIC ONLY: invented
prompts, never anything from live transcripts.

## Run

```sh
tools/romp-lab/lab.sh                      # full highlight loop, screenshots + verdicts
tools/romp-lab/lab.sh --keep               # keep the temp root + kernel log for forensics
LAB_MODEL="Haiku 4.5" tools/romp-lab/lab.sh   # pick the reply model (default: the cheapest Haiku the menu offers)
```

The driver (`highlight-loop.mjs`) scripts the user's exact flow through the real
dashboard — create a session from the + picker, drop the model to the lab default,
send a prompt, get a REAL reply, select rendered text, Comment, send, follow up —
and asserts the comment-mark state at EVERY event boundary of the T102 contract:

1. send gesture → the busy pulse latches immediately (before any thread exists)
2. thread-open → no state change (sampled continuously: zero flicker)
3. the reply record lands → the pulse clears to settled yellow
4. a follow-up send → re-latches until ITS reply
5. after → nothing sticks (sampled well past the last push)

Screenshots land in the temp root's `shots/` per phase; the script exits non-zero
on the first divergence with the phase named, so it loops cleanly in a
fix → re-run cycle.

## Cost

A lab run spends a handful of short turns on the configured model (default Haiku)
against the machine's own key — the same key live sessions bill.
