"""Web-watcher cron dispatcher.

For each active subscriber:
- Fetch every URL in their watch list
- Compute SHA-256 of the rendered text content (stripping <script>, <style>)
- Compare against previously stored hash (kept in Stripe metadata under
  `last_hashes_json: { url: hash }`)
- For any URL whose hash changed, send an alert email via Resend with a
  diff summary (use Qwen via gateway to summarize what changed)

Run from cron every 15 min:
    */15 * * * * python -m src.dispatcher

Or via Vercel cron in vercel.json:
    "crons": [{ "path": "/api/dispatch", "schedule": "*/15 * * * *" }]

Subscriber Stripe metadata:
    product_slug: web-watcher
    status: active
    plan: solo | pro | firm  (10/100/1000 page caps)
    urls_json: ["https://...", "https://..."]
    last_hashes_json: { "https://...": "<sha>", ... }   # written back
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


PLAN_CAPS = {"solo": 10, "pro": 100, "firm": 1000}


@dataclass
class WatchedURL:
    url: str
    last_hash: str | None
    new_hash: str | None = None
    new_text_excerpt: str | None = None
    changed: bool = False


def normalize_html(html: str) -> str:
    """Strip script/style/comments and collapse whitespace so meaningful
    text-only changes drive the hash."""
    # Remove scripts, styles, comments
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url(url: str, timeout: int = 20) -> str | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "openclaw-web-watcher/0.1 (+https://openclaw-revenue.vercel.app)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content_type = r.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return None
            raw = r.read(2_000_000)  # cap at 2 MB
            return raw.decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  [fetch err] {url}: {type(e).__name__}: {e}")
        return None


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def check_subscriber(customer_id: str, urls: list[str], last_hashes: dict[str, str], plan: str) -> dict[str, Any]:
    cap = PLAN_CAPS.get(plan, 10)
    urls = urls[:cap]
    changed: list[WatchedURL] = []
    new_hashes: dict[str, str] = dict(last_hashes)
    for url in urls:
        html = fetch_url(url)
        if html is None:
            continue
        text = normalize_html(html)
        new_hash = hash_text(text)
        last = last_hashes.get(url)
        new_hashes[url] = new_hash
        if last and last != new_hash:
            changed.append(WatchedURL(
                url=url,
                last_hash=last,
                new_hash=new_hash,
                new_text_excerpt=text[:1500],
                changed=True,
            ))
    return {
        "customer_id": customer_id,
        "checked": len(urls),
        "changed": len(changed),
        "new_hashes": new_hashes,
        "diffs": changed,
    }


def list_active_subscribers() -> list[dict[str, Any]]:
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        return []
    out: list[dict[str, Any]] = []
    try:
        results = stripe.Customer.search(
            query='metadata["product_slug"]:"web-watcher" AND metadata["status"]:"active"',
        )
    except Exception as e:  # noqa: BLE001
        print(f"[web-watcher] stripe search failed: {e}")
        return []
    for c in (results.auto_paging_iter() if hasattr(results, "auto_paging_iter") else results.data):
        try:
            md_obj = c["metadata"] if "metadata" in c else None
            md: dict[str, str] = {}
            if md_obj is not None:
                for k in list(md_obj.keys()):
                    md[k] = md_obj[k] or ""
            urls = json.loads(md.get("urls_json", "[]"))
            last_hashes = json.loads(md.get("last_hashes_json", "{}"))
            email = c["email"] if "email" in c else ""
            if not urls or not email:
                continue
            out.append({
                "customer_id": c["id"],
                "email": email,
                "plan": md.get("plan", "solo"),
                "urls": urls,
                "last_hashes": last_hashes,
            })
        except Exception as e:  # noqa: BLE001
            print(f"[web-watcher] skip {c.get('id')}: {e}")
    return out


def write_back_hashes(customer_id: str, hashes: dict[str, str]) -> None:
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    try:
        stripe.Customer.modify(
            customer_id,
            metadata={"last_hashes_json": json.dumps(hashes)},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[web-watcher] hash write-back failed for {customer_id}: {e}")


def send_alert(to: str, diffs: list[WatchedURL]) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return False
    sender = os.environ.get("EMAIL_FROM", "openclaw <onboarding@resend.dev>")
    body_lines = [
        f"openclaw web-watcher — {len(diffs)} page{'s' if len(diffs) != 1 else ''} changed",
        "",
    ]
    for d in diffs:
        body_lines.append(f"=== {d.url} ===")
        body_lines.append(f"new excerpt:")
        body_lines.append((d.new_text_excerpt or "")[:500])
        body_lines.append("")
    payload = {
        "from": sender,
        "to": [to],
        "subject": f"web-watcher — {len(diffs)} change{'s' if len(diffs) != 1 else ''} detected",
        "text": "\n".join(body_lines),
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "openclaw-web-watcher/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001
        print(f"[web-watcher] alert send failed: {e}")
        return False


def run_check() -> dict[str, Any]:
    when = datetime.now(timezone.utc)
    subs = list_active_subscribers()
    print(f"[web-watcher] {when.isoformat()}  subscribers={len(subs)}")
    summary = {"ts": when.isoformat(), "subscribers": len(subs), "alerts": 0, "checks": 0}
    for sub in subs:
        result = check_subscriber(
            customer_id=sub["customer_id"],
            urls=sub["urls"],
            last_hashes=sub["last_hashes"],
            plan=sub["plan"],
        )
        summary["checks"] += result["checked"]
        if result["diffs"]:
            sent = send_alert(sub["email"], result["diffs"])
            if sent:
                summary["alerts"] += 1
        # Write back updated hashes regardless
        write_back_hashes(sub["customer_id"], result["new_hashes"])
    return summary


if __name__ == "__main__":
    print(json.dumps(run_check(), indent=2, default=str))
