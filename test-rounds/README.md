# test-rounds/ — the laptop's checklists and evidence

This folder holds the live validation record between the two machines: the
office box writes code, the MacBook runs it against real NSE data and pushes
the answers back as pasted text.

- `ROUND2-TODO.md` … `ROUND8-TODO.md` — one-off laptop checklists, each closed
  by its pasted answers (history — don't edit the paste boxes).
- `ROUND9-DAILY.md` — the OPEN one: a 2-minute evening paste per trading day;
  Phase 4 closes after 20 consecutive clean `[etl-loop]` days in it.
- `test-results/` — pasted pytest output / diffs from the laptop, kept as
  evidence of what actually ran there.

Checklists are self-contained: every command is inlined, and the `PASTE`
boxes are the answer format. "Didn't run" is a valid answer; a fake paste is
not. New rounds go here as `ROUND<N>-TODO.md` (name only, no folder gymnastics).
