"""Run the viewer:  python -m investigator.web [--label eval-v4] [--port 8000] [--no-rerun]

Needs IV_PG_* and SCORER_RO_PASSWORD (scripts/demo_env.ps1); the Rerun button also needs
AGENT_RO_PASSWORD and HARNESS_RW_PASSWORD and an authenticated `claude` CLI, because it
starts `python -m investigator.batch` as a worker. Listens on 127.0.0.1 only. For a public
link (Tailscale Funnel) start it with --no-rerun, so visitors cannot spend Claude quota.
"""

import argparse
import sys

import uvicorn

from investigator.web.app import create_app
from investigator.web.data import DataStore
from investigator.web.rerun import RerunManager


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Read-only viewer for finished runs.")
    parser.add_argument("--label", default="eval-v4")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-rerun", action="store_true",
                        help="turn the Rerun button off (use this for a public link)")
    return parser.parse_args(argv)


def rerun_manager(args):
    """None turns reruns off: the API refuses them and the page hides the button."""
    if args.no_rerun:
        return None
    return RerunManager(args.label)


def main(argv=None):
    args = parse_args(argv)
    app = create_app(DataStore(args.label, runs_dir=args.runs_dir), rerun_manager(args))
    reruns = "off" if args.no_rerun else "on"
    print("viewer on http://127.0.0.1:%d  (label %s, reruns %s)" % (args.port, args.label, reruns))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
