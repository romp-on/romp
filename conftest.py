# Per-session worktrees live at worktrees/<session> (see CLAUDE.md "Worktrees");
# keep a bare `pytest` at the repo root from collecting the nested checkouts.
collect_ignore = ["worktrees"]
