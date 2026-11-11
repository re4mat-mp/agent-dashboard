# The crew

One row per craft. This table says **who exists and what each one does**. It never holds XP,
level or rank: XP is the sum of `award_log.jsonl`, and level and rank are computed from XP every
time `generate.py` runs. A number that is computed is never stored, so it can never go stale.

- **Name** is what the board shows. `Lead` is reserved: the Lead is not a row, it is the crew's average.
- **Craft** is the kind of thinking the agent does.
- **Agent** is the `subagent_type` behind it, the file `~/.claude/agents/<agent>.md`. It is what
  `tools/read_transcripts.py` matches when it reads your real dispatches.
- Adding a craft is one new row here. No code changes.

| Name | Craft | Agent | What it does |
|---|---|---|---|
| Smith | builder | smith | Writes the change, runs it, and shows it working before calling it done. |
| Skeptic | reviewer | skeptic | Reads every change it did not write and tries to break it. |
| Cartographer | analyst | cartographer | Turns a pile of feedback into a ranked map of what hurts most. |
| Keeper | notekeeper | keeper | Keeps the notes tidy: what we know, where a fact belongs, what went stale. |
| Scribe | writer | scribe | Drafts the words a person will read: a summary, a release note, a reply. |
| Watch | operations | watch | Notices when something that should have run did not, and finds out why. |
| Assayer | data | assayer | Runs the numbers and says how sure it is about each one. |
