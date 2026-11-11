#!/usr/bin/env python3
"""Build the workshop board: a page that shows a crew of Claude Code agents.

    python3 generate.py                          read crew/, write docs/index.html
    python3 generate.py --award NAME XP "WHY"    the ONLY way XP is ever written
    python3 generate.py --seen                   mark every rank-up as watched (CLI side)

Reads   crew/roster.md         who exists and what each one does (no numbers)
        crew/award_log.jsonl   every XP award, append-only
        crew/activity.jsonl    every dispatch: who sent whom, when
        crew/reveals_seen.json rank-ups already watched
Writes  docs/index.html        always, from dashboard.template.html
        crew/award_log.jsonl   only with --award, exactly one appended line
        crew/reveals_seen.json only with --seen

Standard library only. Same input, same output: the page carries the date of the
newest entry in the data, never the clock, so two runs on the same data give the
same file byte for byte.
"""
import argparse
import datetime
import html
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROSTER = ROOT / "crew" / "roster.md"
AWARD_LOG = ROOT / "crew" / "award_log.jsonl"
ACTIVITY = ROOT / "crew" / "activity.jsonl"
SEEN = ROOT / "crew" / "reveals_seen.json"
TEMPLATE = ROOT / "dashboard.template.html"
PAGE = ROOT / "docs" / "index.html"

DATA_TOKEN = "__BOARD_DATA__"           # replaced by the JSON payload
TEXT_TOKEN = "<!-- __TEXT_VERSION__ -->"  # replaced by the plain-HTML text version
LEAD = "Lead"                            # reserved: the Lead is computed, never a row
MAX_AWARD = 1000                         # one award can't be bigger than this
REPO_URL = "https://github.com/re4mat-mp/agent-dashboard"


class BoardError(Exception):
    """Bad input. Printed as one line and the run stops: nothing is written."""


# ---- 1. The curve: XP -> level -> rank -------------------------------------
# Reaching level L costs 25 * (L - 1)^2 XP in total, so each level costs a bit
# more than the one before. Level 2 is 25 XP, level 10 is 2,025, level 20 is
# 9,025. The inverse is one line, and integer-only, so there is no rounding to
# get wrong. Level and rank are NEVER stored anywhere: they are computed here
# every run, so they can't disagree with the XP they come from.
XP_STEP = 25


def xp_for_level(level):
    return XP_STEP * (level - 1) ** 2


def level_for_xp(xp):
    return 1 + math.isqrt(max(0, int(xp)) // XP_STEP)


RANKS = [  # (first level of the rank, rank name)
    (1, "Apprentice"),
    (5, "Journeyman"),
    (10, "Artisan"),
    (15, "Master"),
    (20, "Grandmaster"),
]


def rank_index(level):
    return max(i for i, (start, _) in enumerate(RANKS) if level >= start)


# ---- 2. Reading the three files ---------------------------------------------
def read_roster(path=ROSTER):
    """The first markdown table in roster.md -> [{name, craft, agent, does}]."""
    rows, header = [], None
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line.startswith("|"):
            if header and rows:
                break  # the table ended
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            for need in ("name", "craft", "agent", "what it does"):
                if need not in header:
                    raise BoardError(f"{path.name}:{lineno}: the table has no '{need}' column")
            continue
        if set(line) <= set("|-: "):
            continue  # the |---|---| separator
        row = dict(zip(header, cells))
        if row["name"] == LEAD:
            raise BoardError(f"{path.name}:{lineno}: '{LEAD}' is reserved, it is computed from the crew")
        rows.append({"name": row["name"], "craft": row["craft"],
                     "agent": row["agent"], "does": row["what it does"]})
    names = [r["name"] for r in rows]
    if not rows:
        raise BoardError(f"{path.name}: no crew table found")
    if len(names) != len(set(names)):
        raise BoardError(f"{path.name}: a name appears twice")
    return rows


def read_jsonl(path):
    """Append-only logs. A broken line stops the run with its line number:
    skipping it quietly would make the board disagree with the file."""
    if not path.exists():
        return []
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise BoardError(f"{path.name}:{lineno}: not valid JSON ({exc.msg})")
    return sorted(out, key=lambda r: r.get("ts", ""))  # stable: equal stamps keep file order


def read_seen(path=SEEN):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ---- 3. Rank crossings, and which ones nobody has watched -------------------
def crossings(timeline):
    """timeline = [(ts, xp_after, award)] in time order. Returns the first
    moment each rank above Apprentice was reached, with the award that did it."""
    out, best = [], 0
    for ts, xp, award in timeline:
        idx = rank_index(level_for_xp(xp))
        while best < idx:
            best += 1
            out.append({"rank": RANKS[best][1], "previous": RANKS[best - 1][1],
                        "level": level_for_xp(xp), "xp": xp, "ts": ts,
                        "by": award["name"], "award_xp": award["xp"], "why": award["why"]})
    return out


def person(name, xp, crossed, extra):
    """Everything a card needs. Computed, never stored."""
    level = level_for_xp(xp)
    idx = rank_index(level)
    nxt = RANKS[idx + 1] if idx + 1 < len(RANKS) else None
    return dict(extra, name=name, xp=xp, level=level, rank=RANKS[idx][1], rank_index=idx,
                level_floor=xp_for_level(level), level_next=xp_for_level(level + 1),
                next_rank=nxt and {"rank": nxt[1], "level": nxt[0],
                                   "xp_to_go": xp_for_level(nxt[0]) - xp},
                crossed=crossed)


# ---- 4. The whole board ------------------------------------------------------
def lead_xp(totals):
    """The Lead never earns XP. Its XP is the crew's average, rounded down."""
    return sum(totals.values()) // len(totals)


def build_board(roster, awards, activity, seen):
    names = [r["name"] for r in roster]
    for a in awards:
        if a.get("name") not in names:
            raise BoardError(f"{AWARD_LOG.name}: an award names '{a.get('name')}', who is not in the roster")
        if not isinstance(a.get("xp"), int) or isinstance(a.get("xp"), bool):
            raise BoardError(f"{AWARD_LOG.name}: an award to {a['name']} has no whole-number xp")

    totals = {n: 0 for n in names}
    timelines = {n: [] for n in names}
    lead_timeline = []
    for a in awards:  # replay the log in time order
        totals[a["name"]] += a["xp"]
        timelines[a["name"]].append((a["ts"], totals[a["name"]], a))
        # The Lead's XP moves with every award to anyone, so its rank-ups are
        # replayed the same way as a crew member's.
        lead_timeline.append((a["ts"], lead_xp(totals), a))

    sent_to = Counter(e["to"] for e in activity)
    last_sent = {e["to"]: e["ts"] for e in activity}
    crew = []
    for r in roster:
        mine = [a for a in awards if a["name"] == r["name"]]
        crew.append(person(r["name"], totals[r["name"]], crossings(timelines[r["name"]]), {
            "craft": r["craft"], "agent": r["agent"], "does": r["does"],
            "awards": len(mine), "recent": list(reversed(mine[-3:])),
            "dispatched": sent_to.get(r["name"], 0), "last_dispatched": last_sent.get(r["name"]),
        }))

    total = sum(totals.values())
    lead = person(LEAD, lead_xp(totals), crossings(lead_timeline),
                  {"sum": total, "n": len(names), "dispatched": sum(1 for e in activity if e.get("from") == LEAD)})

    # A hand-off is a dispatch whose sender is a craft, not the Lead.
    handoffs = [e for e in activity if e.get("from") in names and e.get("to") in names and e["from"] != e["to"]]
    pairs = Counter((e["from"], e["to"]) for e in handoffs)

    # Rank-ups nobody has watched yet: crossed, and not in reveals_seen.json.
    # The page subtracts what this browser has already watched (localStorage).
    pending = [dict(c, name=p["name"]) for p in [lead] + crew for c in p["crossed"]
               if c["rank"] not in seen.get(p["name"], [])]
    pending.sort(key=lambda c: (c["ts"], c["name"] != LEAD, c["name"]))

    stamps = [a["ts"] for a in awards] + [e["ts"] for e in activity]
    return {
        "as_of": max(stamps) if stamps else None,
        "curve": {"xp_step": XP_STEP, "formula": "XP to reach level L = 25 x (L - 1)^2"},
        "ranks": [{"level": lvl, "rank": name} for lvl, name in RANKS],
        "lead": lead,
        "crew": crew,
        "handoffs": {
            "total": len(handoffs),
            "pairs": [{"from": f, "to": t, "count": c}
                      for (f, t), c in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))],
            "recent": list(reversed(handoffs[-6:])),
        },
        "pending": pending,
        "counts": {"crew": len(crew), "awards": len(awards), "xp": total,
                   "dispatches": len(activity), "handoffs": len(handoffs), "pending": len(pending)},
    }


# ---- 5. Writing the page -----------------------------------------------------
def text_version(b):
    """Plain HTML written here, not by JavaScript, so it reads with JS off.
    Every count carries data-count so the smoke test can check it against the data."""
    e = html.escape
    c = b["counts"]
    lead = b["lead"]
    day = (b["as_of"] or "")[:10]
    lines = [
        '<h2 id="tv">Text version</h2>',
        '<p class="tvlead">What the board shows, for people and for AI. Every count is computed from <code>crew/</code>.</p>',
        f'<p>A board for a crew of <b data-count="crew">{c["crew"]}</b> Claude Code agents, each with one craft, '
        f'and the Lead who sends them work. All data is invented. It comes from '
        f'<b data-count="awards">{c["awards"]}</b> XP awards (<b data-count="xp">{c["xp"]:,}</b> XP in total) and '
        f'<b data-count="dispatches">{c["dispatches"]}</b> dispatches. The newest entry in either log is from {e(day)}.</p>',
        f'<p><b>The Lead:</b> {e(lead["rank"])}, level {lead["level"]}, {lead["xp"]:,} XP. '
        f'The Lead never earns XP: it is the crew\'s {lead["sum"]:,} XP divided by {lead["n"]}.</p>',
        "<ol>",
    ]
    for p in b["crew"]:
        lines.append(f'<li><b>{e(p["name"])}</b> ({e(p["craft"])}): {e(p["rank"])}, level {p["level"]}, '
                     f'{p["xp"]:,} XP from {p["awards"]} awards, dispatched {p["dispatched"]} times. {e(p["does"])}</li>')
    lines.append("</ol>")
    top = b["handoffs"]["pairs"][:3]
    top_txt = ", ".join(f'{e(t["from"])} to {e(t["to"])} ({t["count"]})' for t in top) or "none yet"
    lines.append(f'<p><b>Hand-offs</b>, a craft sending work to another craft: '
                 f'<b data-count="handoffs">{c["handoffs"]}</b>. The most frequent: {top_txt}.</p>')
    waiting = ", ".join(f'{e(r["name"])} reached {e(r["rank"])}' for r in b["pending"]) or "none"
    lines.append(f'<p><b>Rank-ups not yet marked as watched</b> in <code>crew/reveals_seen.json</code>: '
                 f'<b data-count="pending">{c["pending"]}</b> ({waiting}). With JavaScript on, each one plays once as a pop-up.</p>')
    lines.append(f'<p><b>The curve:</b> {e(b["curve"]["formula"])}. Ranks start at level '
                 + ", ".join(f'{r["level"]} ({e(r["rank"])})' for r in b["ranks"]) + ".</p>")
    lines.append(f'<nav><a href="{REPO_URL}#readme">How to build it (README)</a>'
                 f'<a href="{REPO_URL}/blob/main/generate.py">The generator, one file</a></nav>')
    return "\n  ".join(lines)


def render(board, template_text):
    if template_text.count(DATA_TOKEN) != 1 or template_text.count(TEXT_TOKEN) != 1:
        raise BoardError(f"{TEMPLATE.name}: each token must appear exactly once")
    # "</" inside a <script> would end it early, so escape it. "<\/" is still valid JSON.
    payload = json.dumps(board, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return template_text.replace(DATA_TOKEN, payload).replace(TEXT_TOKEN, text_version(board))


def load():
    return read_roster(), read_jsonl(AWARD_LOG), read_jsonl(ACTIVITY), read_seen()


def write_page(board):
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(render(board, TEMPLATE.read_text(encoding="utf-8")), encoding="utf-8")


# ---- 6. Awards: the only writer of XP ----------------------------------------
def check_award(roster, name, xp_text, why):
    names = [r["name"] for r in roster]
    if name == LEAD:
        raise BoardError("The Lead's XP is the crew's average. Award a craft instead.")
    if name not in names:
        raise BoardError(f"No one called '{name}'. The crew: {', '.join(names)}")
    try:
        xp = int(xp_text)
    except ValueError:
        raise BoardError(f"XP must be a whole number, got '{xp_text}'")
    if not 1 <= xp <= MAX_AWARD:
        raise BoardError(f"XP must be between 1 and {MAX_AWARD}, got {xp}")
    if not why.strip():
        raise BoardError("Say why: an award with no reason teaches nothing")
    return xp


def append_award(entry):
    """The one place in this repo that writes XP: one line, appended, never edited."""
    with AWARD_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def mark_all_seen(board):
    """--seen: every rank crossed so far counts as watched by the CLI."""
    seen = {p["name"]: [c["rank"] for c in p["crossed"]] for p in [board["lead"]] + board["crew"]}
    SEEN.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def summary(board):
    out = []
    for p in [board["lead"]] + board["crew"]:
        out.append(f'  {p["name"]:<13} {p["rank"]:<11} level {p["level"]:>2}  {p["xp"]:>6,} XP')
    out.append(f'  {board["counts"]["pending"]} rank-up(s) waiting to be watched. Wrote {PAGE.relative_to(ROOT)}')
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the workshop board.")
    ap.add_argument("--award", nargs=3, metavar=("NAME", "XP", "WHY"), help="append one award, then build")
    ap.add_argument("--seen", action="store_true", help="mark every rank-up so far as watched")
    args = ap.parse_args(argv)
    try:
        roster, awards, activity, seen = load()
        before = build_board(roster, awards, activity, seen)
        if args.award:
            name, xp_text, why = args.award
            xp = check_award(roster, name, xp_text, why)
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            append_award({"ts": now, "name": name, "xp": xp, "why": why.strip()})
            roster, awards, activity, seen = load()
        board = build_board(roster, awards, activity, seen)
        if args.seen:
            mark_all_seen(board)
            board = build_board(roster, awards, activity, read_seen())
        write_page(board)
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.award:
        was = {p["name"]: p for p in [before["lead"]] + before["crew"]}
        for p in [board["lead"]] + board["crew"]:
            if p["xp"] != was[p["name"]]["xp"]:
                print(f'{p["name"]}: {was[p["name"]]["xp"]:,} -> {p["xp"]:,} XP, level {p["level"]}')
            if p["rank"] != was[p["name"]]["rank"]:
                print(f'  RANK UP: {p["name"]} is now {p["rank"]}. Open {PAGE.relative_to(ROOT)} to watch it.')
    print(summary(board))
    return 0


if __name__ == "__main__":
    sys.exit(main())
