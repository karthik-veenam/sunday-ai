"""Wiki-based knowledge store for Sunday.

Storage  : wiki/*.md — one page per topic, plain markdown
Retrieval: Gemini text-embedding-004 + cosine similarity (~20ms per query)
Synthesis: Gemini Flash to integrate new insights without duplication

Usage:
    wiki = WikiSearch(client)
    wiki.load()                           # once at startup
    ctx = await wiki.search("user text")  # before each LLM call
    await wiki.integrate("routines", "insight text")  # after save_memory
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from google import genai

WIKI_DIR = Path(__file__).parent / "wiki"
CACHE_FILE = WIKI_DIR / ".embed_cache.json"
EMBED_MODEL = "gemini-embedding-001"
SYNTH_MODEL = "gemini-2.5-flash-lite"

# Known pages and their one-line descriptions (shown to the model in save_memory)
PAGES = {
    "preferences": "Lighting, temperature, AV and general personal preferences.",
    "routines":    "Named scenes and action sequences (movie mode, sleep, watch mode, etc.).",
    "devices":     "Device-specific quirks, ordering rules, and corrections.",
    "schedule":    "Weekly timing patterns, badminton, gym, daily rhythms.",
}

_SYNTH_PROMPT = """\
You maintain Sunday's knowledge wiki. Integrate the new insight into `{page}.md`.

Rules:
- If the insight contradicts existing content, REPLACE the old entry.
- If it adds new information, add it concisely in the right section.
- If it is already fully covered, leave the page unchanged.
- Keep entries as short bullet points — no prose, no redundancy.
- Return ONLY the updated markdown. No preamble, no code fences.

Current `{page}.md`:
{current}

New insight:
{insight}

Updated `{page}.md`:"""


class WikiSearch:
    def __init__(self, client: genai.Client):
        self._client = client
        self._pages: dict[str, str] = {}
        self._embeddings: dict[str, np.ndarray] = {}

    # ── startup ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load all wiki pages and compute/restore embeddings synchronously.
        Call once at startup before the event loop is busy."""
        WIKI_DIR.mkdir(exist_ok=True)
        cache: dict = {}
        if CACHE_FILE.exists():
            try:
                cache = json.loads(CACHE_FILE.read_text())
            except Exception:
                pass

        for name in PAGES:
            path = WIKI_DIR / f"{name}.md"
            if not path.exists():
                path.write_text(f"# {name.capitalize()}\n\n")
            content = path.read_text().strip()
            self._pages[name] = content

            content_hash = str(hash(content))
            if cache.get(name, {}).get("hash") == content_hash and cache[name].get("emb"):
                self._embeddings[name] = np.array(cache[name]["emb"], dtype=np.float32)
            else:
                print(f"[Wiki] Embedding {name}.md ...")
                vec = self._client.models.embed_content(
                    model=EMBED_MODEL, contents=content or name,
                ).embeddings[0].values
                self._embeddings[name] = np.array(vec, dtype=np.float32)

        self._save_cache()
        print(f"[Wiki] Loaded {len(self._pages)} pages.")

    # ── retrieval ─────────────────────────────────────────────────────────────

    async def search(self, query: str, top_k: int = 2) -> str:
        """Embed query → cosine similarity → return top-k pages as context string."""
        if not self._pages:
            return ""
        result = await self._client.aio.models.embed_content(
            model=EMBED_MODEL, contents=query,
        )
        q = np.array(result.embeddings[0].values, dtype=np.float32)
        q_norm = np.linalg.norm(q)

        scores: dict[str, float] = {}
        for name, emb in self._embeddings.items():
            content = self._pages.get(name, "").strip()
            if not content or content == f"# {name.capitalize()}":
                continue  # skip empty pages
            scores[name] = float(np.dot(q, emb) / (q_norm * np.linalg.norm(emb) + 1e-9))

        if not scores:
            return ""

        top = sorted(scores, key=scores.__getitem__, reverse=True)[:top_k]
        print(f"[Wiki] query={query!r:.40} → {[(n, round(scores[n],2)) for n in top]}")
        parts = [f"### [{name}]\n{self._pages[name]}" for name in top]
        return "\n\n".join(parts)

    # ── synthesis ─────────────────────────────────────────────────────────────

    async def integrate(self, page: str, insight: str) -> None:
        """Synthesize a new insight into a wiki page, update embedding."""
        if page not in PAGES:
            page = "preferences"  # safe fallback
        current = self._pages.get(page, "")
        prompt = _SYNTH_PROMPT.format(page=page, current=current or "(empty)", insight=insight)
        resp = await self._client.aio.models.generate_content(
            model=SYNTH_MODEL, contents=prompt,
        )
        updated = (resp.candidates[0].content.parts[0].text or "").strip()
        if not updated:
            return

        # Persist
        path = WIKI_DIR / f"{page}.md"
        path.write_text(updated)
        self._pages[page] = updated

        # Re-embed
        vec = (await self._client.aio.models.embed_content(
            model=EMBED_MODEL, contents=updated,
        )).embeddings[0].values
        self._embeddings[page] = np.array(vec, dtype=np.float32)
        self._save_cache()
        print(f"[Wiki] {page}.md updated: {insight[:70]!r}")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _save_cache(self) -> None:
        cache = {
            name: {"hash": str(hash(self._pages[name])), "emb": self._embeddings[name].tolist()}
            for name in self._pages
            if name in self._embeddings
        }
        CACHE_FILE.write_text(json.dumps(cache))
