"""General background about each invented company, which reruns give to the agents.

It describes the healthy system only: what the company does and what each service is for. It
is written per company and never per incident, so it cannot carry an answer; tests check that
it names no fault and no detail of any incident. The case stories, which do contain answers,
live in a different file that only the viewer reads.
"""

from pathlib import Path

import yaml

DEFAULT_CONTEXT_PATH = Path(__file__).resolve().parents[2] / "configs" / "company_context.yaml"


def load_company_contexts(path=DEFAULT_CONTEXT_PATH):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def render_background(company):
    lines = [company["what_it_does"], company["how_it_works"], "What each service does:"]
    for service in company["services"]:
        lines.append("- %s: %s" % (service["name"], service["plain"]))
    return "\n".join(lines)


def add_background(brief, domain, enabled, companies=None):
    """The brief with its company background added, when a run asked for it and one exists."""
    if not enabled:
        return brief
    if companies is None:
        companies = load_company_contexts()
    if domain not in companies:
        return brief
    brief["background"] = render_background(companies[domain])
    return brief
