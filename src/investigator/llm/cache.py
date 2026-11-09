"""ResponseCache: record and replay completions, so reruns cost no quota and repeat exactly.

`claude -p` has no temperature control, so the only way to reproduce a run is to replay what
the model said. Key = sha256 of [model, system prompt, prompt]; one JSON file per key.

Modes
  record  read the cache first; on a miss call the model and store the answer
  replay  read the cache only; a miss raises CacheMiss and the model is never called

Only completions are stored (including replies that later fail validation, so a replay goes
through the same repairs). Timeouts and errors are not stored: in a replay the next attempt
finds the stored answer directly, so attempt counts can differ from the recorded run there.
"""

import json
import os
from pathlib import Path

from investigator.llm.backend import Completion
from investigator.stable_hash import canonical_json, sha256_hex

MODES = ["record", "replay"]


class CacheMiss(Exception):
    pass


class ResponseCache:
    def __init__(self, directory, mode):
        if mode not in MODES:
            raise ValueError("cache mode must be one of %s, not %s" % (MODES, mode))
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mode = mode

    def key(self, model, system, prompt):
        return sha256_hex(canonical_json(["llm", model, system, prompt]))

    def path_for(self, key):
        return self.directory / (key + ".json")

    def load(self, key):
        path = self.path_for(key)
        if not path.exists():
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        return Completion.model_validate(stored["completion"])

    def store(self, key, model, system, prompt, completion):
        record = {
            "request": {"model": model, "system": system, "prompt": prompt},
            "completion": completion.model_dump(mode="json"),
        }
        path = self.path_for(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)     # never leave a half-written entry behind

    async def get_or_call(self, model, system, prompt, call):
        """call() is an async function returning a Completion; used only on a miss."""
        key = self.key(model, system, prompt)
        cached = self.load(key)
        if cached is not None:
            return cached
        if self.mode == "replay":
            raise CacheMiss("no recorded completion for key %s (replay mode)" % key)
        completion = await call()
        self.store(key, model, system, prompt, completion)
        return completion
