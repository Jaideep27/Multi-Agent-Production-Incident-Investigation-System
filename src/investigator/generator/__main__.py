"""Build incidents from the plan and load them into Postgres.

    python -m investigator.generator [--only PG-1 ...] [--dry-run] [--plan PATH]

Connects as generator_rw (IV_PG_* and GENERATOR_RW_PASSWORD from the environment). Prints the
plan ID -> incident ID mapping for the operator; that mapping is answer-key information and
is never given to agents.
"""

import argparse
import sys

from investigator.generator.build import build_incident
from investigator.generator.dataset import dataset_checksum
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.generator.validate import find_problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build and load incidents from the plan.")
    parser.add_argument("--plan", default=str(DEFAULT_PLAN_PATH))
    parser.add_argument("--only", nargs="*", default=None, help="plan IDs to build (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="build and validate only")
    args = parser.parse_args(argv)

    plan = load_plan(args.plan)
    entries = []
    if args.only:
        for plan_id in args.only:
            try:
                entries.append(find_entry(plan, plan_id))
            except KeyError:
                print("no plan entry %s" % plan_id, file=sys.stderr)
                return 2
    else:
        entries = list(plan.incidents)

    connection = None
    if not args.dry_run:
        import psycopg

        from investigator import db
        connection = psycopg.connect(db.conninfo_for("generator_rw"))

    failures = 0
    try:
        for entry in entries:
            dataset = build_incident(entry)
            problems = find_problems(dataset)
            incident_id = dataset.evidence.incident.incident_id
            checksum = dataset_checksum(dataset)
            if problems:
                failures = failures + 1
                print("%s -> %s INVALID" % (entry.plan_id, incident_id))
                for problem in problems:
                    print("    " + problem)
                continue
            if connection is not None:
                from investigator.generator.load import load_incident
                load_incident(connection, dataset)
                print("%s -> %s loaded (checksum %s)" % (entry.plan_id, incident_id, checksum))
            else:
                print("%s -> %s valid (checksum %s)" % (entry.plan_id, incident_id, checksum))
    finally:
        if connection is not None:
            connection.close()

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
