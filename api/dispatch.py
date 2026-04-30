"""Vercel serverless cron entry for web-watcher.

Schedule via vercel.json:
    "crons": [{ "path": "/api/dispatch", "schedule": "*/15 * * * *" }]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dispatcher import run_check  # noqa: E402


def handler(request):  # noqa: ARG001
    summary = run_check()
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(summary, default=str),
    }
