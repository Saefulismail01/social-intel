#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path("/opt/social-intelligence/data/social-intelligence.db")
DESTINATION = Path("/opt/social-intelligence/backups")
RETENTION = 30

DESTINATION.mkdir(mode=0o700, parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
target = DESTINATION / f"social-intelligence-{stamp}.db"
with sqlite3.connect(SOURCE) as source, sqlite3.connect(target) as destination:
    source.backup(destination)
target.chmod(0o600)
for expired in sorted(DESTINATION.glob("social-intelligence-*.db"), reverse=True)[RETENTION:]:
    expired.unlink()
print(target)
