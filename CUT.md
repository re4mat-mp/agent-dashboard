# What this repo keeps from my own board, and what it leaves out

I've run a private version of this board for months, for my own crew of agents. It grew big: one generator of
several thousand lines, a page of about the same size, a native app around it, and a lot of data about my own
work. This repo is not a copy of it. It's the same mechanisms, written again from scratch, small enough to read
in one sitting, with an original theme and invented data.

## Kept: the mechanisms

| Mechanism | Where it lives here |
|---|---|
| A roster that says who exists, with no numbers in it | `crew/roster.md` |
| XP as an append-only log, with one command as its only writer | `crew/award_log.jsonl`, `generate.py --award` |
| Level and rank computed from XP every run, never stored | `generate.py`, the curve |
| The Lead's level as the crew's average, never awarded | `generate.py`, `lead_xp` |
| Hand-offs between agents, counted by pair | `crew/activity.jsonl`, `generate.py` |
| A reveal pop-up per rank crossed, fired once, with two memories of "already watched" | `crew/reveals_seen.json`, `localStorage` |
| One template, data injected as JSON, a self-contained page | `dashboard.template.html` → `docs/index.html` |
| A plain-HTML text version that works with JavaScript off | written by `generate.py` |
| Reading real dispatches from Claude Code's own transcripts, without copying what the task was | `tools/read_transcripts.py` |
| A smoke test that guards the invariants, and checks that can be broken on purpose | `tests/smoke_test.py` |

## Left out, and why

| What | Why it's out |
|---|---|
| The theme of my own board | It borrows from a work of fiction that isn't mine to publish. The workshop theme here is original: the crafts, their glyphs and the ranks were drawn for this repo |
| My crew's real data: the awards, the dispatches, what each agent worked on | It's a record of my work, not a demo. Every name, number, date and reason here is invented |
| Panels that read my own setup: usage and cost, scheduled routines, memory, agent definitions | They read files that only exist on my machine, and they'd show what I work on |
| The native app shell and the phone build | Platform code that has nothing to teach about the board itself |
| Character art and images | Not mine to publish. The glyphs here are geometric SVG drawn by hand in the template |
| The many smaller features my board grew over time | Each one made sense for me. Together they'd hide the five or six mechanisms that are worth copying |

## How it was checked

- Written fresh: no file was copied from the private board. Its code was read to remember how each mechanism
  works, then each one was written again here, smaller.
- Every file was searched for the names of people, companies, systems and places, and for the names of my own
  agents: no hits.
- The only external links point to GitHub. A test enforces it for every file in the repo.
- The smoke test passes (18 checks, three runs), and thirteen bugs planted on purpose each turn their own check red.
- The page was rendered and looked at: desktop, phone width, the pop-up after an award, and the text version.
