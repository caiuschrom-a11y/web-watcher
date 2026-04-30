"""Watch a list of URLs; detect content changes; Qwen-summarize what changed.

Use cases:
- Regulatory pages (FDA, FCC, FAA notices)
- Competitor pricing / feature pages
- Hiring pages (signals layoffs / pivots)
- Lawsuit / SEC filings indexes
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from openai import OpenAI
from selectolax.parser import HTMLParser
from tenacity import retry, stop_after_attempt, wait_exponential

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3-30b"
USER_AGENT = "web-watcher/0.1"

DB_DEFAULT = Path.home() / ".webwatch" / "webwatch.sqlite"


@dataclass
class WatchResult:
    url: str
    changed: bool
    change_summary: str | None
    content_hash: str
    fetched_at: str


@contextmanager
def _db(path: Path | None = None):
    p = Path(path) if path else DB_DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
          url TEXT PRIMARY KEY,
          content_hash TEXT NOT NULL,
          last_text TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS changes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          url TEXT NOT NULL, detected_at TEXT NOT NULL,
          previous_hash TEXT, new_hash TEXT NOT NULL,
          summary TEXT
        );
    """)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
def fetch_text(url: str) -> str:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
    tree = HTMLParser(r.text)
    for tag in ("script", "style", "noscript", "nav", "footer"):
        for n in tree.css(tag):
            n.decompose()
    body = tree.css_first("body")
    return body.text(separator=" ", strip=True)[:50_000] if body else ""


def diff_summary(old_text: str, new_text: str) -> str:
    """Qwen-summarize what changed between snapshots."""
    c = OpenAI(base_url=DEFAULT_BASE_URL, api_key="local")
    user = (
        f"OLD page text:\n{old_text[:8000]}\n\n"
        f"NEW page text:\n{new_text[:8000]}\n\n"
        "What materially changed? Summarize in 2-4 sentences. Skip cosmetic / "
        "navigation / boilerplate changes. If nothing material changed, say "
        "'No material change.'"
    )
    resp = c.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "system", "content": "You summarize webpage changes for a watcher service."},
                  {"role": "user", "content": user}],
        max_tokens=400, temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def watch(url: str, *, db: Path | None = None) -> WatchResult:
    text = fetch_text(url)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    with _db(db) as conn:
        row = conn.execute("SELECT content_hash, last_text FROM snapshots WHERE url = ?", (url,)).fetchone()
        if row is None:
            # First-time snapshot
            conn.execute(
                "INSERT INTO snapshots (url, content_hash, last_text, updated_at) VALUES (?,?,?,?)",
                (url, h, text, now),
            )
            return WatchResult(url=url, changed=False, change_summary="first snapshot", content_hash=h, fetched_at=now)
        prev_hash, prev_text = row
        if prev_hash == h:
            return WatchResult(url=url, changed=False, change_summary=None, content_hash=h, fetched_at=now)

        # Changed
        try:
            summary = diff_summary(prev_text, text)
        except Exception:
            summary = "(qwen unavailable; raw diff captured)"

        conn.execute(
            "UPDATE snapshots SET content_hash=?, last_text=?, updated_at=? WHERE url=?",
            (h, text, now, url),
        )
        conn.execute(
            "INSERT INTO changes (url, detected_at, previous_hash, new_hash, summary) VALUES (?,?,?,?,?)",
            (url, now, prev_hash, h, summary),
        )
        return WatchResult(url=url, changed=True, change_summary=summary, content_hash=h, fetched_at=now)
