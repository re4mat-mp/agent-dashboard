#!/usr/bin/env python3
"""Smoke test: the invariants the board depends on. Standard library only.

    python3 tests/smoke_test.py

Every check that runs generate.py runs it on a COPY of the repo in a temp folder,
so the real crew/ files are never touched. Exit code 0 = all passed.
"""
import ast
import atexit
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import generate  # noqa: E402

RESULTS = []


def check(fn):
    try:
        fn()
        RESULTS.append(("PASS", fn.__name__, ""))
    except AssertionError as exc:
        RESULTS.append(("FAIL", fn.__name__, str(exc)))
    except Exception as exc:  # a crash is a failure too, never a skip
        RESULTS.append(("FAIL", fn.__name__, f"{type(exc).__name__}: {exc}"))
    return fn


def scratch_copy():
    tmp = Path(tempfile.mkdtemp(prefix="board-"))
    atexit.register(shutil.rmtree, tmp, True)  # cleaned up when the run ends, pass or fail
    for name in ("generate.py", "dashboard.template.html", "crew", "tools"):
        src = REPO / name
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, tmp / name)
    return tmp


def run(repo, *args):
    return subprocess.run([sys.executable, str(repo / "generate.py"), *args],
                          capture_output=True, text=True, cwd=repo)


def payload(page_text):
    m = re.search(r'<script type="application/json" id="board-data">(.*?)</script>', page_text, re.S)
    assert m, "no board-data script in the page"
    return json.loads(m.group(1))


def crew_files(repo):
    return {p.name: p.read_bytes() for p in sorted((repo / "crew").iterdir())}


# ---- 1. Only --award writes XP -------------------------------------------------
@check
def plain_build_writes_nothing_in_crew():
    repo = scratch_copy()
    before = crew_files(repo)
    r = run(repo)
    assert r.returncode == 0, r.stderr
    assert crew_files(repo) == before, "a plain build changed a file in crew/"


@check
def award_appends_exactly_one_line():
    repo = scratch_copy()
    log = repo / "crew" / "award_log.jsonl"
    old = log.read_text()
    r = run(repo, "--award", "Scribe", "40", "Wrote the test's release note")
    assert r.returncode == 0, r.stderr
    new = log.read_text()
    assert new.startswith(old), "an award rewrote earlier lines"
    added = new[len(old):].splitlines()
    assert len(added) == 1, f"expected 1 new line, got {len(added)}"
    entry = json.loads(added[0])
    assert (entry["name"], entry["xp"]) == ("Scribe", 40), entry
    others = {k: v for k, v in crew_files(repo).items() if k != "award_log.jsonl"}
    assert others == {k: v for k, v in crew_files(REPO).items() if k != "award_log.jsonl"}, \
        "an award changed another crew file"


@check
def bad_awards_are_refused_and_write_nothing():
    repo = scratch_copy()
    before = crew_files(repo)
    for args in (["Nobody", "10", "x"], ["Lead", "10", "x"], ["Smith", "-5", "x"],
                 ["Smith", "0", "x"], ["Smith", "5000", "x"], ["Smith", "ten", "x"], ["Smith", "10", "  "]):
        r = run(repo, "--award", *args)
        assert r.returncode != 0, f"--award {args} was accepted"
    assert crew_files(repo) == before, "a refused award still wrote something"


@check
def only_one_function_opens_the_award_log_to_write():
    """Read generate.py's own code: only one function opens AWARD_LOG or writes
    to it by name, and that function is called from one place."""
    tree = ast.parse((REPO / "generate.py").read_text())
    writers = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        src = ast.get_source_segment((REPO / "generate.py").read_text(), fn)
        if re.search(r"AWARD_LOG\.(open|write_text|write_bytes)\(|open\(\s*AWARD_LOG\b", src):
            writers.add(fn.name)
    assert writers == {"append_award"}, f"functions that write the award log: {writers}"
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "append_award"]
    assert len(calls) == 1, f"append_award is called {len(calls)} times"
    # and the tools/ reader never touches it
    assert "AWARD_LOG" not in (REPO / "tools" / "read_transcripts.py").read_text()


# ---- 2. The curve --------------------------------------------------------------
@check
def level_curve_is_monotonic_and_exact():
    last = 0
    for xp in range(0, 40001):
        lvl = generate.level_for_xp(xp)
        assert lvl >= last, f"level went down at {xp} XP"
        assert generate.xp_for_level(lvl) <= xp < generate.xp_for_level(lvl + 1), f"wrong level at {xp} XP"
        last = lvl
    costs = [generate.xp_for_level(l + 1) - generate.xp_for_level(l) for l in range(1, 60)]
    assert costs == sorted(costs), "a level got cheaper than the one before"
    ranks = [generate.rank_index(l) for l in range(1, 60)]
    assert ranks == sorted(ranks), "rank went down as level went up"


# ---- 3. The Lead is the average ------------------------------------------------
@check
def lead_is_the_crew_average():
    repo = scratch_copy()
    for step in (None, ("Assayer", "999", "a big week"), ("Smith", "1", "a small one")):
        r = run(repo, *(["--award", *step] if step else []))
        assert r.returncode == 0, r.stderr
        data = payload((repo / "docs" / "index.html").read_text())
        totals = {p["name"]: 0 for p in generate.read_roster(repo / "crew" / "roster.md")}
        for line in (repo / "crew" / "award_log.jsonl").read_text().splitlines():
            if line.strip():
                a = json.loads(line)
                totals[a["name"]] += a["xp"]
        avg = sum(totals.values()) // len(totals)
        assert data["lead"]["xp"] == avg, f"Lead {data['lead']['xp']} != average {avg}"
        assert data["lead"]["level"] == generate.level_for_xp(avg)
        for p in data["crew"]:
            assert p["xp"] == totals[p["name"]], f"{p['name']} shows {p['xp']}, log says {totals[p['name']]}"
        # the Lead's rank-ups are replayed from the same average: each sits at the right level
        for c in data["lead"]["crossed"]:
            assert generate.RANKS[generate.rank_index(generate.level_for_xp(c["xp"]))][1] == c["rank"], c


# ---- 4. The reveal -------------------------------------------------------------
def one_award_from_a_rank(data):
    """Any crew member one award away from a new rank. Not a fixed name, so the
    check still works after you've awarded someone in the README's walkthrough."""
    who = next((p for p in data["crew"] if p["next_rank"]
                and 0 < p["next_rank"]["xp_to_go"] <= generate.MAX_AWARD), None)
    assert who, "nobody on the crew can reach a new rank in one award"
    return who


@check
def a_rank_crossing_is_pending_until_seen():
    repo = scratch_copy()
    assert run(repo, "--seen").returncode == 0
    assert payload((repo / "docs" / "index.html").read_text())["pending"] == []
    who = one_award_from_a_rank(payload((repo / "docs" / "index.html").read_text()))
    need = who["next_rank"]["xp_to_go"]
    r = run(repo, "--award", who["name"], str(need), "crossed on purpose")
    assert f"RANK UP: {who['name']}" in r.stdout, r.stdout
    pending = payload((repo / "docs" / "index.html").read_text())["pending"]
    assert [(p["name"], p["rank"]) for p in pending] == [(who["name"], who["next_rank"]["rank"])], pending
    assert pending[0]["why"] == "crossed on purpose"
    assert run(repo, "--seen").returncode == 0
    assert payload((repo / "docs" / "index.html").read_text())["pending"] == []


@check
def pending_reveals_come_oldest_first():
    repo = scratch_copy()
    assert run(repo).returncode == 0
    before = payload((repo / "docs" / "index.html").read_text())["pending"]
    who = one_award_from_a_rank(payload((repo / "docs" / "index.html").read_text()))
    assert run(repo, "--award", who["name"], str(who["next_rank"]["xp_to_go"]), "newest").returncode == 0
    pending = payload((repo / "docs" / "index.html").read_text())["pending"]
    assert [p["ts"] for p in pending] == sorted(p["ts"] for p in pending), "pending is not oldest first"
    keys = [(p["name"], p["rank"]) for p in pending]
    assert keys[:len(before)] == [(p["name"], p["rank"]) for p in before], "an older reveal lost its place"
    assert keys[-1] == (who["name"], who["next_rank"]["rank"]), "the newest crossing is not last"


# ---- 5. The page ---------------------------------------------------------------
ALLOWED_HOSTS = {"github.com", "re4mat-mp.github.io"}


def urls_in(text):
    return re.findall(r"https?://([^/\s\"'<>)]+)[^\s\"'<>)]*", text)


@check
def the_page_only_links_to_github():
    page = (REPO / "docs" / "index.html").read_text()
    hosts = set(urls_in(page))
    assert hosts <= {"github.com"}, f"the page links to {hosts - {'github.com'}}"
    links = set(re.findall(r'https?://[^\s"\'<>]+', page))
    assert len(links) <= 2, f"more than the two GitHub links on the page: {links}"
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=\"(https?:)?//", page), "the page loads something remote"


@check
def no_url_in_the_repo_leaves_github():
    for path in REPO.rglob("*"):
        if path.is_file() and ".git" not in path.parts and path.suffix in {".py", ".md", ".html", ".json", ".jsonl", ""}:
            hosts = set(urls_in(path.read_text(errors="ignore")))
            assert hosts <= ALLOWED_HOSTS, f"{path.relative_to(REPO)} links to {hosts - ALLOWED_HOSTS}"


@check
def the_guide_is_linked_exactly_once():
    guide = "github.com/re4mat-mp/" + "claude-setup-guide"  # split so this file is not a hit itself
    hits = sum(p.read_text(errors="ignore").count(guide) for p in REPO.rglob("*")
               if p.is_file() and not {".git", "tests"} & set(p.parts) and p.suffix in {".md", ".html", ".py"})
    assert hits == 1, f"the guide is linked {hits} times"


@check
def text_version_counts_match_the_data():
    repo = scratch_copy()
    assert run(repo).returncode == 0
    page = (repo / "docs" / "index.html").read_text()
    text = page[page.index('<section id="text"'):page.index("</section>", page.index('<section id="text"'))]
    shown = {k: int(v.replace(",", "")) for k, v in re.findall(r'data-count="(\w[\w-]*)">([\d,]+)<', text)}
    roster = generate.read_roster()
    awards = [json.loads(l) for l in (REPO / "crew/award_log.jsonl").read_text().splitlines() if l.strip()]
    acts = [json.loads(l) for l in (REPO / "crew/activity.jsonl").read_text().splitlines() if l.strip()]
    names = {r["name"] for r in roster}
    expected = {
        "crew": len(roster), "awards": len(awards), "xp": sum(a["xp"] for a in awards),
        "dispatches": len(acts),
        "handoffs": sum(1 for e in acts if e["from"] in names and e["to"] in names and e["from"] != e["to"]),
        "pending": len(payload(page)["pending"]),
    }
    assert shown == expected, f"text version says {shown}, data says {expected}"
    assert "#readme" in text, "the text version has no link back to the README"


@check
def page_is_current_and_build_is_deterministic():
    repo = scratch_copy()
    assert run(repo).returncode == 0
    first = (repo / "docs" / "index.html").read_bytes()
    assert run(repo).returncode == 0
    assert (repo / "docs" / "index.html").read_bytes() == first, "two builds on the same data differ"
    assert (REPO / "docs" / "index.html").read_bytes() == first, \
        "docs/index.html is stale: run python3 generate.py"


@check
def template_tokens_survive():
    tpl = (REPO / "dashboard.template.html").read_text()
    assert tpl.count(generate.DATA_TOKEN) == 1 and tpl.count(generate.TEXT_TOKEN) == 1
    page = (REPO / "docs" / "index.html").read_text()
    assert generate.DATA_TOKEN not in page and generate.TEXT_TOKEN not in page


@check
def a_script_tag_in_the_data_cannot_break_the_page():
    repo = scratch_copy()
    r = run(repo, "--award", "Keeper", "5", "</script><b>not markup</b>")
    assert r.returncode == 0, r.stderr
    page = (repo / "docs" / "index.html").read_text()
    data = payload(page)
    keeper = next(p for p in data["crew"] if p["name"] == "Keeper")
    assert keeper["recent"][0]["why"] == "</script><b>not markup</b>"
    assert "</script><b>not markup" not in page, "the reason was written into the page unescaped"


@check
def text_version_escapes_the_roster():
    repo = scratch_copy()
    roster = repo / "crew" / "roster.md"
    roster.write_text(roster.read_text().replace("tries to break it.", "tries to break it <i>x</i>."))
    assert run(repo).returncode == 0
    page = (repo / "docs" / "index.html").read_text()
    text = page[page.index('<section id="text"'):]
    assert "tries to break it &lt;i&gt;x&lt;/i&gt;." in text, "the text version did not escape the roster"
    assert "<i>x</i>" not in text


@check
def the_page_escapes_what_it_draws():
    """No JS runtime here, so read the template: esc() must replace & < > " and '."""
    tpl = (REPO / "dashboard.template.html").read_text()
    m = re.search(r"const esc = s => String\(s \?\? \"\"\)\.replace\(/\[(.*?)\]/g, c => \(\{(.*?)\}\[c\]\)\)", tpl)
    assert m, "esc() not found in the template"
    chars, table = m.group(1), m.group(2)
    for ch, ent in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("'", "&#39;")):
        assert ch in chars and ent in table, f"esc() does not replace {ch}"
    drawn_lines = [l for l in tpl.splitlines() if "const keyOf" not in l]  # keyOf builds a storage key, not markup
    drawn = re.findall(r"\$\{((?:p|a|e|t|r|L)\.(?:name|craft|agent|does|why|task|from|to))\}", "\n".join(drawn_lines))
    assert not drawn, f"text from the data drawn without esc(): {drawn}"


# ---- 6. The transcript reader keeps no descriptions ----------------------------
@check
def transcript_reader_keeps_only_three_fields():
    repo = scratch_copy()
    root = repo / "fake_transcripts" / "a-project"
    root.mkdir(parents=True)
    lines = [
        {"timestamp": "2025-05-01T10:00:00.123Z", "isSidechain": False, "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Agent",
             "input": {"subagent_type": "smith", "description": "SECRET-DESCRIPTION", "prompt": "SECRET-PROMPT"}}]}},
        {"timestamp": "2025-05-01T10:05:00Z", "isSidechain": True, "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "Task", "input": {"subagent_type": "skeptic"}}]}},
        {"timestamp": "2025-05-01T10:06:00Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"subagent_type": "smith"}}]}},  # duplicate id
        {"timestamp": "2025-05-01T10:07:00Z", "message": {"content": [
            {"type": "tool_use", "id": "t3", "name": "Agent", "input": {"subagent_type": "someone-else"}}]}},
        {"timestamp": "2025-05-01T10:08:00Z", "message": {"content": [  # malformed: skipped, never a crash
            {"type": "tool_use", "id": ["t4"], "name": "Agent", "input": {"subagent_type": "smith"}},
            {"type": "tool_use", "id": "t5", "name": "Agent", "input": {"subagent_type": {"x": 1}}},
            {"type": "tool_use", "id": "t6", "name": "Agent", "input": "smith"}]}},
    ]
    (root / "s.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\nnot json\n")
    r = subprocess.run([sys.executable, str(repo / "tools" / "read_transcripts.py"),
                        "--root", str(repo / "fake_transcripts"), "--write"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = (repo / "crew" / "activity.jsonl").read_text()
    assert "SECRET" not in out, "a description or prompt was copied"
    rows = [json.loads(l) for l in out.splitlines()]
    assert rows == [{"ts": "2025-05-01T10:00:00Z", "from": "Lead", "to": "Smith"},
                    {"ts": "2025-05-01T10:05:00Z", "from": "(an agent)", "to": "Skeptic"}], rows
    assert "someone-else" in r.stdout, "an agent with no roster row was not reported"
    assert run(repo).returncode == 0, "the board does not build from the reader's output"


if __name__ == "__main__":
    for status, name, why in RESULTS:
        print(f"{status}  {name}" + (f"\n      {why}" if why else ""))
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
