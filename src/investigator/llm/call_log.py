"""PostgresLlmCallLog: one eval.llm_calls row per LLM attempt (harness_rw, autocommit)."""


class PostgresLlmCallLog:
    def __init__(self, connection):
        self.connection = connection

    async def record(self, row):
        await self.connection.execute(
            """
            INSERT INTO eval.llm_calls
              (run_id, agent, attempt, status, input_tokens, output_tokens, latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (row.run_id, row.agent, row.attempt, row.status, row.input_tokens,
             row.output_tokens, row.latency_ms),
        )
