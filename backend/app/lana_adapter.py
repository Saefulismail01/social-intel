import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Lana crime kanban columns — desk universe mirrors only these live phases.
# ACCUMULATION / NORMAL / watchlist / repeat_offenders stay out so the desk
# tracks the same board Lana shows, not the entire market.
KANBAN_PHASES = ("IGNITION", "SQUEEZE", "EXHAUSTION", "DUMP")


_KANBAN_SQL = """
SELECT COALESCE(json_agg(row_to_json(board) ORDER BY priority, symbol), '[]'::json)
FROM (
  SELECT replace(s.symbol, 'USDT', '') AS symbol,
         s.phase AS lana_phase,
         CASE s.phase
           WHEN 'IGNITION' THEN 0
           WHEN 'SQUEEZE' THEN 1
           WHEN 'EXHAUSTION' THEN 2
           WHEN 'DUMP' THEN 3
           ELSE 9
         END AS priority,
         'lana_phase_state' AS source,
         s.updated_at AS effective_at
  FROM crime_phase_state s
  WHERE s.phase IN ('IGNITION', 'SQUEEZE', 'EXHAUSTION', 'DUMP')
) board;
""".strip()


@dataclass(frozen=True)
class LanaUniverseAdapter:
    fixture_path: Path
    ssh_host: Optional[str] = None
    container: str = "lana-postgres"
    database_url: Optional[str] = None

    def fetch(self) -> list[dict]:
        """Return the current Lana kanban (active crime phases only)."""
        if self.database_url:
            return self._from_database_url()
        if self.ssh_host:
            return self._from_docker_or_ssh()
        return self._from_fixture()

    def _from_fixture(self) -> list[dict]:
        records = json.loads(self.fixture_path.read_text())
        allowed = set(KANBAN_PHASES)
        return [
            row for row in records
            if str(row.get("lana_phase", "")).upper() in allowed
        ]

    def _from_database_url(self) -> list[dict]:
        import psycopg

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(_KANBAN_SQL)
                row = cur.fetchone()
        payload = row[0] if row else []
        if isinstance(payload, str):
            payload = json.loads(payload)
        return list(payload or [])

    def _from_docker_or_ssh(self) -> list[dict]:
        """Host docker.sock / docker CLI, then SSH fallback."""
        direct = [
            "docker", "exec", self.container, "psql",
            "-U", "lana", "-d", "lana", "-At", "-c", _KANBAN_SQL,
        ]
        try:
            completed = subprocess.run(
                direct, capture_output=True, text=True, timeout=30, check=True,
            )
            return json.loads(completed.stdout.strip() or "[]") or []
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
            pass

        remote_command = " ".join(shlex.quote(part) for part in direct)
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.ssh_host,
            remote_command,
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=True,
        )
        return json.loads(completed.stdout.strip() or "[]") or []
