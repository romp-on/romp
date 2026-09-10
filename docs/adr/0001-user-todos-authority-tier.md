# User todos are an authority tier the judges cannot clear

Status: proposed 2026-09-07 (the decision dates from 2026-08-21)

A user todo — a need an agent registers with the user while it keeps working — is cleared by
exactly three events: the user answers it, the user dismisses it, or the agent withdraws it.
The judges and the unblocker get no vote: nothing in `kernel/judge.py` may write the user-todo
store. We chose this because every mechanism that reasons about blocks by inference has
demonstrably erased this exact class of ask — the repo documents it about itself
(`WHY_UNBLOCK_UNSETTLED`, `kernel/judge.py`): blocked-on-the-user goals repeatedly flipped
back to working by "new work filed" and "answered in passing" rulings while the user's
decision was still outstanding. An object whose whole purpose is to survive that inference
cannot be clearable by it.

## Considered options

- **Teach the judges to file and preserve partial blocks.** Rejected: judges infer from
  transcript text, and inference over "I can't do X, continuing with Y" fails in both
  directions — filing blocks that aren't real and lifting ones that are. Tuning prompts moves
  the error rate; it cannot make an inference layer authoritative.
- **Let judges clear todos they deem moot.** Rejected for v1; preserved as a deferred option
  in the weaker form of a judge-*suggested* mootness rendered as a one-click confirm for the
  user — a suggestion, never a clear.

## Consequences

- The vanishing stops: once a user todo is visible, it stays visible until an accountable
  actor — the user, or the agent explicitly — says otherwise.
- The stated cost: an agent that forgets to withdraw leaves a moot todo sitting until the user
  dismisses it. The cost is taken deliberately — a stale visible todo costs a glance and a click; a
  silently vanished ask costs whatever was asked. Withdrawal is supported passively (the tool
  description's contract, open todos re-surfaced in the contexts the agent naturally receives
  after restart or compaction) — never by scheduled check-in turns, which were rejected as
  noise: at idle the common truth is that everything registered is still waiting.
- The precedent generalizes: this is the second authority tier, after agentTask nodes (an
  agent's open task vetoes an inferred done). Any future mechanism that can move cards must
  treat authority-tier state as read-only evidence, not something to rule on.
