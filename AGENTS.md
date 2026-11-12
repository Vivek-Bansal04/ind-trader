# Rules for AI assistants working on this repo

These three rules are standing orders from the operator. Every model, every
session — OpenCode, Claude, Codex, Cursor, anything. When in doubt, the doubt
itself is a reason to stop and ask.

## 1. Never commit until explicitly asked

No `git commit` or `git push` unless the operator said so in this session
("commit it", "push it", or equally clear words). Drafting, editing, testing
and leaving files uncommitted is the normal state — the operator reviews first,
commits second. When told to commit: `git pull --ff-only` first (the operator's
laptop pushes to this repo in parallel), and commit only the files the review
covered.

## 2. Never change core logic until told

"Core logic" is anything a human's money or decisions depend on:

- the safety rails — no order path may ever appear, nothing may become
  intraday / short / leverage / F&O, human-confirmed execution stays human-confirmed;
- data truth — freshness rule, ratio conventions, unknown-vs-zero handling,
  parse-or-refuse, the walk-forward and health gates;
- any threshold, formula or constant listed in `docs/RULES.md` or
  `docs/ALGORITHMS.md`.

Adding or updating tests and docs is always allowed. Touching core logic needs
an explicit instruction — and if the code turns out to be wrong, *report it*
with evidence; proposing is free, changing is not.

## 3. Every code change keeps the docs current — in the same change

If a change makes any doc stale, that doc is edited in the same change, not
"later". Which doc to touch:

| Changed something in... | Update... |
|---|---|
| a rule, threshold, refusal, retry, constant | `docs/RULES.md` (§14 table too) |
| a formula or algorithm | `docs/ALGORITHMS.md` |
| fetching / ETL behaviour | `docs/DATA-FETCHING.md` and §1 of `docs/RULES.md` |
| how or where to run things | `docs/RUNNING.md`, `README.md` |
| a user-facing command or output shape | `README.md`, `docs/LAYMAN.md` |
| a laptop checklist item or its paste format | the open file in `test-rounds/` |

Doc edits are written from the code as it is *after* the change — re-read the
source, never document from memory. This rule exists because a documented
"3-day freshness rule" survived in `ALGORITHMS.md` after the code had moved to
a calendar-anchored one, and because a rules doc written partly from memory
missed rules that were in front of it. A stale doc is a bug, not a backlog item.

## 4. Every code change must be recorded in `CHANGELOG.md`

Before making any code change, add an entry to `CHANGELOG.md` with:

| Field | What to write |
|---|---|
| File(s) changed | Full path(s) of every file modified |
| What changed | Brief description of the change |
| Why changed | Reason for the change |
| Status | `not committed` / `committed` / `pushed` |
| Commit ID | Git commit ID (only after committing) |
| AGENTS.md rule | Which rule(s) this change touches (e.g., Rule 2) |

Every code change — no matter how small — gets a `CHANGELOG.md` entry.
This creates an auditable trail so anyone can see what changed, why, and
which commit contains it. If you change code but don't add a `CHANGELOG.md`
entry, the change is considered untracked.

## Where things live

```
README.md            the one-page story            docs/     all docs (LAYMAN, RULES,
AGENTS.md/CLAUDE.md  these rules — must stay root)           ALGORITHMS, DATA-FETCHING,
                                                             RUNNING, ARCHITECTURE,
                                                             HOME-VALIDATION)
test-rounds/         laptop checklists (ROUND*) and pasted
                     evidence (test-rounds/test-results/)
src/indtrader/       the code          tests/       offline pytest, must stay green
```
