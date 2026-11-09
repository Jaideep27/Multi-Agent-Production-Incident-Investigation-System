"""PostgresCallLog: writes one eval.tool_calls row per physical attempt.

Give it an autocommit connection as harness_rw: each row is committed as soon as it is
written, so a run that crashes still leaves its log behind.
"""

from psycopg.types.json import Jsonb


class PostgresCallLog:
    def __init__(self, connection):
        self.connection = connection

    async def record(self, row):
        await self.connection.execute(
            """
            INSERT INTO eval.tool_calls
              (run_id, tool, args, attempt, status, latency_ms, evidence_ids)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (row.run_id, row.tool, Jsonb(row.args), row.attempt, row.status, row.latency_ms,
             row.evidence_ids),
        )
