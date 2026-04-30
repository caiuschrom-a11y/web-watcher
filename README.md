# web-watcher

24/7 web-page change monitor. Watches N URLs on a schedule; Qwen
summarizes material changes (skips cosmetic/nav boilerplate).

## Pricing

- $99/mo for 100 watched URLs (hourly check)
- $299/mo for 500 URLs (15-min check)
- $999/mo for 5000 URLs (5-min check)
- $4999/mo enterprise (custom diff rules, slack/webhook delivery)

## Use cases

- Regulatory-page watching (FDA, FCC, FAA notices)
- Competitor pricing/feature page monitoring
- Hiring-page tracking (signals layoffs, pivots)
- SEC filings index, litigation court dockets
- Job-posting changes (compensation, requirements)

## Run

```powershell
cd C:\openclaw-products\web-watcher
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .

# First snapshot (no diff yet)
webwatch check https://stripe.com/pricing

# Run again later — diff against previous snapshot
webwatch check https://stripe.com/pricing

# Batch
webwatch batch urls.txt
```

## Roadmap
- [ ] Webhook delivery (Slack / Discord / email)
- [ ] Schedule via cron / Windows Task Scheduler
- [ ] Per-URL custom diff rules (CSS selector pruning)
- [ ] Visual-diff via Playwright screenshot + image diff
- [ ] Alert thresholds ("only fire if pricing-related text changed")
