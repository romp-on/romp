---
name: romp-postal
description: How to message peer romp sessions (the Romp Postal Service). Use when you have the postal MCP tools (send_message / check_inbox / list_agents / set_working / check_sent / recall_message) or are inside a romp session and need to coordinate with, hand off to, or reply to sibling sessions. romp sessions get a short pointer to this at SessionStart; a plain Claude Code session has no peers and can ignore it.
allowed-tools: Bash
---

# Romp Postal Service (messaging peer sessions)

Only applies inside a romp session (a tmux session tagged `@romp`, or an SDK-backed romp session). A plain Claude Code session has no peers, so ignore this.

## Tools

Message sibling sessions with the postal MCP tools (each tool's own description carries the specifics): `send_message(to, body)`, `check_inbox()`, `list_agents()`, `set_working(text)`, `check_sent()`, `recall_message(to, id?)`. Inbox is also delivered automatically at each turn's end, so you rarely call `check_inbox` yourself.

Addressing is live-only: you can message only currently-live sessions (see `list_agents`). Dead names error, with no parked mail or reviving. A session's stable id (the uuid `list_agents` shows) also works as the recipient — rename-proof, unique by construction, so it never hits the shared-name ambiguity refusal.

Role-named recipients go stale: a role handed to a new session keeps the OLD name in your memory of it, and the retired name just bounces. Before sending to a role-style name (a router, a watcher, a manager), confirm it against `list_agents` and address whoever holds the role now.

Names are not guaranteed unique. If more than one live session answers to the one you used, the send is refused and the candidates are listed as `host:name`: pick one and resend. Your own name is refused outright, since a message there arrives in your own inbox looking exactly like a reply from someone else. Your row in `list_agents` is the one marked `(you)`.

From the shell (also how the human drives it): `romp mail send <name> "<text>"`, `romp mail inbox|agents|sent`, `romp mail working "<note>"`, `romp mail recall <name> [id]`.

## On a remote machine

If you SSH'd into another machine and are running romp there, run `romp mail remote` to connect it to the laptop's bus. It configures the remote side and prints the one tunnel command to run from the laptop (an `ssh -R` reverse forward, or a `~C` escape on the open connection), then auto-detects when it connects. Messaging before this setup nudges you to run it.

## Norms

**Keep it tight.** Message a peer only for something substantive: a question you need answered, information they need, or a result worth sharing. A message wakes the recipient and costs it a turn, so never send just to acknowledge, and stop once the exchange is done.

**Write so the recipient can act from your first line** (they share none of your context):
- Lead with `DELEGATE:` (you own this now, reply only to clarify), `COORDINATE:` (aligning or heads-up, reply optional), or `QUESTION:` (reply required).
- First sentence is the whole point: the ask or conclusion, not how you got there. Context after, only what they need to act.
- Name things exactly: files by path, sessions by name, the same term each time. Mark verified vs. suspected, and whose ask it is.
- End with the reply you need, or that none is. One point per message; when brevity and clarity conflict, clarity wins.

**An isolation refusal is final.** A mailbox toggled off is a boundary the user drew. If `send_message` refuses because a mailbox is off (yours or the recipient's), do NOT reroute the content through any other door — the kernel's `/send` route, tmux keystrokes, shared files, another peer as relay. Report the refusal to the user and stop; only they lift the isolation. (The kernel also refuses postal-shaped mail to isolated sessions on every route, but the rule is about intent, not shape: rerouting the same content as plain text is the same violation.)

**Prefer postal over Claude Code's native cross-session messaging.** Claude Code (2.1.224+) also lists peer sessions through its own `ListAgents`/`SendMessage`. For romp peers, always message through the postal tools: postal mail declares a kind, is tracked until answered, respects the user's per-host trust boundaries, and shows up on their dashboard; a native cross-session send bypasses all of that and is invisible to them. Native `SendMessage` stays the right tool for your own subagents and in-session teammates.

**Coordinate by reading state, not by waking peers.** Before editing a shared repo, run `list_agents` to see peers' branches and working-notes (overlap is a real collision only on the same branch), and publish yours with `set_working`. Declare what you own in your first line ("I own A/B, stay off them"). Resolve ownership by reading that state, never by messaging "do you still own this?": an idle peer's note may be stale, a peer with no note holds nothing, and romp auto-clears a note once a session's work is done. Use `check_sent` to see whether a message was read instead of asking. Never wake an idle session just to coordinate, which is the false interrupt romp exists to avoid.

When the human says "coordinate with X about Y," message X and act on the replies.
