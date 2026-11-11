"""Wiki memory I/O — Karpathy-style LLM-driven memory.

Storage: ~/sunday/wiki/*.md
- SCHEMA.md  — conventions (fed to integrator on every save)
- INDEX.md   — catalog (read by router and integrator)
- LOG.md     — append-only audit
- <topic>.md — content pages

Two operations:
- save_to_wiki(insight)         — async; routes insight to right page via small LLM
- fetch_relevant_pages(user_text) — async; returns list of page filenames to inject
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

WIKI_DIR = Path(__file__).parent / "wiki"
META_FILES = {"SCHEMA.md", "INDEX.md", "LOG.md"}
SAVE_MODEL = "gpt-5.4-mini"
ROUTE_MODEL = "gpt-5.4-nano"

_client: Optional[AsyncOpenAI] = None


def init(api_key: str) -> None:
    global _client
    _client = AsyncOpenAI(api_key=api_key)


def _read(name: str) -> str:
    p = WIKI_DIR / name
    return p.read_text() if p.exists() else ""


def _read_all_pages() -> str:
    parts = []
    for p in sorted(WIKI_DIR.glob("*.md")):
        if p.name in META_FILES:
            continue
        parts.append(f"### FILE: {p.name}\n{p.read_text()}")
    return "\n\n".join(parts)


def _append_log(line: str) -> None:
    with (WIKI_DIR / "LOG.md").open("a") as f:
        f.write(line.rstrip() + "\n")


def _append_index_entry(filename: str, summary: str) -> None:
    name = filename.rsplit(".md", 1)[0]
    line = f"- [{name}]({filename}) — {summary}\n"
    with (WIKI_DIR / "INDEX.md").open("a") as f:
        f.write(line)


_SAVE_SYSTEM = """You maintain Sunday's memory wiki. You are the integrator.

You receive:
- SCHEMA.md (rules — obey them)
- INDEX.md (current catalog)
- ALL_PAGES (every content page, with filename headers)
- INSIGHT (a new statement to memorize)

Decide ONE action: drop | append | edit | create.

Return strict JSON only, no prose, no fences:
{
  "action": "drop" | "append" | "edit" | "create",
  "page":   "<filename>.md" | null,
  "reason": "<one short line>",
  "patch":  "<for append: bullet line e.g. '- ...'; for edit/create: full new file body; for drop: null>",
  "index_summary": "<for create only: <=120 char summary for INDEX>" | null
}

Strict rules:
- Bias drop > edit > append > create.
- drop if the insight is already represented (verbatim or in spirit) anywhere.
- append is ONLY safe for pages that are a flat list of bullets with no internal `## sections` (rare). If the page has any `## headings`, do NOT append — use edit so you can place the bullet inside the correct section. Appending to a sectioned page leaves bullets stranded after the `_See also:` footer where retrieval will miss them.
- edit returns the FULL new file body (header + summary + all sections + footer), with the new content placed in the right `## section`. Add a new `## section` if no existing one fits. Preserve every existing line you aren't intentionally changing.
- create a new file only when the insight opens a coherent new topic AND you can foresee >=1 more fact joining it.
- Filenames: lowercase, hyphen-separated, .md.
- For create: first line `# Title`; second line `_Summary: ..._`; then `## sections` as needed; then `_See also: [[...]]_`.
"""


async def save_to_wiki(insight: str) -> dict:
    """Async — route an insight to the right page. Designed for fire-and-forget."""
    if _client is None:
        return {"action": "error", "reason": "wiki_io not initialised"}
    insight = (insight or "").strip()
    if not insight:
        return {"action": "drop", "reason": "empty insight"}

    try:
        schema = _read("SCHEMA.md")
        index = _read("INDEX.md")
        pages = _read_all_pages()
        user = (
            f"SCHEMA.md:\n{schema}\n\n"
            f"INDEX.md:\n{index}\n\n"
            f"ALL_PAGES:\n{pages}\n\n"
            f"INSIGHT: {insight}"
        )
        resp = await _client.chat.completions.create(
            model=SAVE_MODEL,
            messages=[
                {"role": "system", "content": _SAVE_SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        plan = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        _append_log(f'{datetime.now().isoformat()} error reason="LLM call failed: {e}" insight={insight!r}')
        return {"action": "error", "reason": str(e)}

    action = plan.get("action", "drop")
    page = plan.get("page")
    reason = plan.get("reason", "")
    patch = plan.get("patch")
    ts = datetime.now().isoformat()

    # Validate: coerce action against actual filesystem state.
    if action in ("append", "edit") and page and not (WIKI_DIR / page).exists():
        # Page named doesn't exist — coerce to create
        action = "create"
        plan["action"] = action
    elif action == "create" and page and (WIKI_DIR / page).exists():
        # Page already exists — model meant edit (full rewrite) not create
        action = "edit"
        plan["action"] = action

    try:
        if action == "drop":
            pass
        elif action == "append" and page and patch:
            with (WIKI_DIR / page).open("a") as f:
                f.write(patch.rstrip() + "\n")
        elif action == "edit" and page and patch:
            (WIKI_DIR / page).write_text(patch if patch.endswith("\n") else patch + "\n")
        elif action == "create" and page and patch:
            (WIKI_DIR / page).write_text(patch if patch.endswith("\n") else patch + "\n")
            summary = plan.get("index_summary") or reason or "(no summary)"
            _append_index_entry(page, summary)
        else:
            _append_log(f'{ts} skip reason="invalid plan: {plan}" insight={insight!r}')
            return {"action": "skip", "reason": "invalid plan", "plan": plan}
    except Exception as e:
        _append_log(f'{ts} error reason="apply failed: {e}" insight={insight!r}')
        return {"action": "error", "reason": str(e)}

    _append_log(f'{ts} {action} page={page} reason="{reason}" insight={insight!r}')
    print(f"[wiki] {action} → {page} ({reason})")
    return plan


_ROUTE_SYSTEM = """You are Sunday's memory router.

Given INDEX.md and a user utterance, pick up to 3 page filenames whose contents are most relevant. Return strict JSON only, no prose:

{ "pages": ["<file>.md", ...] }

Rules:
- Aim for 1-3 pages. Prefer fewer.
- If nothing in the index is plausibly relevant, return { "pages": [] }.
- File names exactly as listed in INDEX.md."""


_EXTRACT_SYSTEM = """You are Sunday's memory extractor.

You receive a user utterance and a small set of memory pages from Sunday's wiki. Return ONLY the specific bullet lines, rules, or short section snippets that are directly relevant to acting on or answering the utterance.

Strict rules:
- Quote lines verbatim from the source pages — do not paraphrase or summarise.
- Group output by source page using a header line `## <page>.md`.
- Under each header, include only the bullets that matter for this utterance.
- If a section heading from the source page helps a bullet make sense, include it as `### <heading>`.
- If a page has nothing relevant, omit it entirely.
- If no page has anything relevant, return an empty string.
- Keep total output under ~600 words.

Return plain markdown text only. No preamble, no explanation, no JSON."""


async def extract_snippets(user_text: str, page_names: list[str]) -> str:
    """Stage-2 distillation: take picked pages and the user text, return only relevant bullets."""
    if _client is None or not page_names:
        return ""
    bodies = []
    for n in page_names:
        p = WIKI_DIR / n
        if p.exists():
            bodies.append(f"### FILE: {n}\n{p.read_text().rstrip()}")
    if not bodies:
        return ""
    try:
        resp = await _client.chat.completions.create(
            model=ROUTE_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"Pages:\n\n{chr(10).join(bodies)}\n\nUtterance: {user_text}"},
            ],
            temperature=0.0,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[wiki] extract failed: {e}")
        return ""


async def fetch_relevant_pages(user_text: str, max_pages: int = 3) -> list[str]:
    """Async — return list of page filenames to inject. Empty list if nothing relevant."""
    if _client is None or not (user_text or "").strip():
        return []
    try:
        index = _read("INDEX.md")
        resp = await _client.chat.completions.create(
            model=ROUTE_MODEL,
            messages=[
                {"role": "system", "content": _ROUTE_SYSTEM},
                {"role": "user", "content": f"INDEX.md:\n{index}\n\nUtterance: {user_text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        names = data.get("pages") or []
    except Exception as e:
        print(f"[wiki] retrieval failed: {e}")
        return []

    valid: list[str] = []
    for n in names[:max_pages]:
        if isinstance(n, str) and (WIKI_DIR / n).exists() and n not in META_FILES:
            valid.append(n)
    return valid


def load_pages(names: list[str]) -> str:
    """Read page bodies for injection. Concatenated with filename headers."""
    parts = []
    for n in names:
        p = WIKI_DIR / n
        if p.exists():
            parts.append(f"### {n}\n{p.read_text().rstrip()}")
    return "\n\n".join(parts)


def all_pages_text() -> str:
    """Return all content pages concatenated — used by the get_memories tool."""
    return _read_all_pages()
