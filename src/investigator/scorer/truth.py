"""TruthView: what the scorer needs from the answer key of one incident.

Each decoy is resolved to the explanation it invites: a decoy change, log or metric points at
its service; a look-alike past incident points at its root component and category.
"""

from typing import Literal

from investigator.generator.read import read_dataset
from investigator.models.common import RootCauseCategory, StrictModel


class DecoyTarget(StrictModel):
    evidence_id: str
    decoy_type: str
    component: str | None
    category: RootCauseCategory | None     # None: any category on that component


class TruthView(StrictModel):
    incident_id: str
    expected_outcome: Literal["identifiable", "inconclusive"]
    root_component: str
    root_category: RootCauseCategory
    decisive_ids: list[str]
    decoys: list[DecoyTarget]


def evidence_targets(evidence):
    """evidence_id -> [component, category] that the item points at."""
    targets = {}
    for item in evidence.changes:
        targets[item.evidence_id] = [item.service, None]
    for item in evidence.logs:
        targets[item.evidence_id] = [item.service, None]
    for item in evidence.metrics:
        targets[item.evidence_id] = [item.service, None]
    for item in evidence.dependencies:
        targets[item.evidence_id] = [item.caller, None]
    for item in evidence.past_incidents:
        targets[item.evidence_id] = [item.root_cause_component, item.root_cause_category]
    return targets


def truth_from_dataset(dataset):
    scenario = dataset.truth.scenario
    targets = evidence_targets(dataset.evidence)

    decisive_ids = []
    for row in dataset.truth.decisive:
        decisive_ids.append(row.evidence_id)

    decoys = []
    for row in dataset.truth.decoys:
        component, category = targets[row.evidence_id]
        decoys.append(DecoyTarget(evidence_id=row.evidence_id, decoy_type=row.decoy_type,
                                  component=component, category=category))

    return TruthView(
        incident_id=dataset.evidence.incident.incident_id,
        expected_outcome=scenario.expected_outcome,
        root_component=scenario.root_component,
        root_category=scenario.root_category,
        decisive_ids=decisive_ids,
        decoys=decoys,
    )


def truth_for_incident(connection, incident_id):
    """connection must be able to read truth.* (scorer_ro)."""
    return truth_from_dataset(read_dataset(connection, incident_id))
