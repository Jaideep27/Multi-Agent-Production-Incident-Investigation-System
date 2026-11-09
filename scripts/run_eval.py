"""Run the evaluation for a list of plan IDs, then write the results table.

    python scripts/run_eval.py --label eval-v3 --plans INC-01 INC-02 ...   (default: all INC-*)
        [--pattern exchange] [--company-context] [--cache-mode off]

Operator tooling: it reads the plan file only to turn plan IDs into incident IDs (the IDs are
deterministic, so no database access is needed for that). Agents never see this mapping.
Spends model quota for every run not already finished under the same label.
"""

import argparse
import sys
from pathlib import Path

from investigator import batch
from investigator.generator.build import build_incident
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.scorer import report


def incident_ids(plan_ids):
    plan = load_plan(DEFAULT_PLAN_PATH)
    if not plan_ids:
        plan_ids = []
        for entry in plan.incidents:
            if entry.plan_id.startswith("INC-"):
                plan_ids.append(entry.plan_id)
    found = []
    for plan_id in plan_ids:
        dataset = build_incident(find_entry(plan, plan_id))
        found.append(dataset.evidence.incident.incident_id)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate both systems on plan entries.")
    parser.add_argument("--label", default="eval-v3")
    parser.add_argument("--plans", nargs="*", default=None)
    parser.add_argument("--pattern", default="blackboard", choices=["blackboard", "exchange"])
    parser.add_argument("--company-context", action="store_true")
    parser.add_argument("--cache-mode", default="record", choices=["off", "record", "replay"])
    args = parser.parse_args(argv)

    ids = incident_ids(args.plans)
    command = ["--label", args.label, "--incidents"] + ids
    command = command + ["--pattern", args.pattern, "--cache-mode", args.cache_mode]
    if args.company_context:
        command.append("--company-context")
    code = batch.main(command)
    out = Path("results") / (args.label + ".md")
    report.main(["--label", args.label, "--out", str(out)])
    return code


if __name__ == "__main__":
    sys.exit(main())
