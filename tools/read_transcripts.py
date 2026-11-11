#!/usr/bin/env python3
"""Turn your real Claude Code dispatches into crew/activity.jsonl.

    python3 tools/read_transcripts.py            print what it found, write nothing
    python3 tools/read_transcripts.py --write    replace crew/activity.jsonl with it

Claude Code keeps every session as a JSONL transcript under ~/.claude/projects/.
When the session sends a sub-agent, the transcript has a tool_use block named
"Agent" (older versions: "Task") whose input carries "subagent_type". That is a
dispatch. This script keeps three fields of it, and nothing else:

    ts    when                      (the line's "timestamp")
    from  "Lead", or "(an agent)"   ("isSidechain" is true when a sub-agent sent it)
    to    the crew Name             (matched through roster.md's Agent column)

The task description is NEVER copied. It is often private, and this file is meant
to be committed: what stays on your machine is the safe default.
Standard library only.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate  # noqa: E402  (reuses the roster parser, so both read the table the same way)

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def read_dispatches(transcripts, agent_to_name):
    events, unknown, seen_ids = [], Counter(), set()
    for path in sorted(transcripts.rglob("*.jsonl")):
        try:
            handle = path.open("rb")
        except OSError:
            continue
        with handle:
            for raw in handle:
                if b'"subagent_type"' not in raw:  # cheap test before parsing JSON
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                content = (line.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    call_id = block.get("id")
                    if block.get("name") not in ("Agent", "Task") or not isinstance(call_id, str) or call_id in seen_ids:
                        continue
                    seen_ids.add(call_id)
                    payload = block.get("input")
                    agent = payload.get("subagent_type") if isinstance(payload, dict) else None
                    if not isinstance(agent, str):
                        continue  # a malformed call, not an agent
                    if agent not in agent_to_name:
                        unknown[agent] += 1
                        continue
                    events.append({"ts": (line.get("timestamp") or "")[:19] + "Z",
                                   "from": "(an agent)" if line.get("isSidechain") else generate.LEAD,
                                   "to": agent_to_name[agent]})
    events.sort(key=lambda e: e["ts"])
    return events, unknown


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="where the transcripts are")
    ap.add_argument("--write", action="store_true", help="replace crew/activity.jsonl")
    args = ap.parse_args(argv)
    roster = generate.read_roster()
    events, unknown = read_dispatches(args.root, {r["agent"]: r["name"] for r in roster})
    by_name = Counter(e["to"] for e in events)
    print(f"{len(events)} dispatches to the crew in {args.root}")
    for name, n in by_name.most_common():
        print(f"  {name:<13} {n}")
    if unknown:
        print("Agents with no row in crew/roster.md (add a row to see them):")
        for agent, n in unknown.most_common(10):
            print(f"  {agent:<24} {n}")
    if args.write:
        generate.ACTIVITY.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        print(f"Wrote {generate.ACTIVITY.relative_to(ROOT)}. Run python3 generate.py to see it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
