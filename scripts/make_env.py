"""Create .env from .env.example with fresh random passwords (only if .env does not exist).

    python scripts/make_env.py [--port 54330]

The compose database listens on IV_PG_PORT (default 54330, so it does not clash with a local
Postgres on 5432). .env is git-ignored; nothing here prints a password.
"""

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write .env with random passwords.")
    parser.add_argument("--port", default="54330")
    args = parser.parse_args(argv)

    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; left unchanged")
        return 0

    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key.endswith("_PASSWORD"):
                line = "%s=%s" % (key, secrets.token_urlsafe(18))
            elif key == "IV_PG_HOST":
                line = "IV_PG_HOST=127.0.0.1"
            elif key == "IV_PG_PORT":
                line = "IV_PG_PORT=%s" % args.port
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote .env with random passwords (port %s)" % args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
