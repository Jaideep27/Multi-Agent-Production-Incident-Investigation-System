"""Score one FinalReport against the answer key (docs/implementation_plan.md §6).

root_cause_correct
    identifiable incident: the top hypothesis names the true component and category, whatever
    outcome the report chose (outcome_correct records that separately);
    inconclusive incident: the report says inconclusive (naming the true cause with
    confidence is still wrong when the evidence could not support it).
outcome_correct          the report's outcome matches the incident's expected outcome
grounding_cited          distinct evidence IDs cited anywhere in the report
grounding_existing       ... of which this run's tool results actually returned
decisive_recall          share of the answer key's decisive evidence the report cites
decoy_hit                the top hypothesis is wrong and is the explanation a decoy invites
decoys_cited_as_support  decoy evidence listed as supporting the top hypothesis
confidence               the top hypothesis's confidence (None without hypotheses)

Component names are compared after trimming and lower-casing; categories exactly.
"""

from investigator.models.common import StrictModel


class ReportScore(StrictModel):
    root_cause_correct: bool
    outcome_correct: bool
    grounding_cited: int
    grounding_existing: int
    decisive_recall: float | None
    decoy_hit: bool
    decoys_cited_as_support: int
    confidence: float | None


def normalise(name):
    if name is None:
        return None
    return name.strip().lower()


def cited_ids(report):
    ids = []
    groups = []
    for fact in report.facts:
        groups.append(fact.evidence_ids)
    for hypothesis in report.hypotheses:
        groups.append(hypothesis.supporting_evidence_ids)
        groups.append(hypothesis.contradicting_evidence_ids)
    for action in report.recommended_actions:
        groups.append(action.evidence_ids)
    for group in groups:
        for evidence_id in group:
            if evidence_id not in ids:
                ids.append(evidence_id)
    return ids


def score_report(report, truth, seen_ids):
    top = None
    if report.hypotheses:
        top = report.hypotheses[0]

    top_matches = False
    if top is not None:
        same_component = normalise(top.component) == normalise(truth.root_component)
        top_matches = same_component and top.category == truth.root_category

    if truth.expected_outcome == "identifiable":
        root_cause_correct = top_matches
        outcome_correct = report.outcome == "root_cause_identified"
    else:
        root_cause_correct = report.outcome == "inconclusive"
        outcome_correct = report.outcome == "inconclusive"

    cited = cited_ids(report)
    existing = 0
    for evidence_id in cited:
        if evidence_id in seen_ids:
            existing = existing + 1

    decisive_recall = None
    if truth.decisive_ids:
        found = 0
        for evidence_id in truth.decisive_ids:
            if evidence_id in cited:
                found = found + 1
        decisive_recall = found / len(truth.decisive_ids)

    decoy_hit = False
    decoys_cited = 0
    confidence = None
    if top is not None:
        confidence = top.confidence
        if not top_matches:
            for target in truth.decoys:
                if normalise(target.component) != normalise(top.component):
                    continue
                if target.category is None or target.category == top.category:
                    decoy_hit = True
        for target in truth.decoys:
            if target.evidence_id in top.supporting_evidence_ids:
                decoys_cited = decoys_cited + 1

    return ReportScore(
        root_cause_correct=root_cause_correct, outcome_correct=outcome_correct,
        grounding_cited=len(cited), grounding_existing=existing,
        decisive_recall=decisive_recall, decoy_hit=decoy_hit,
        decoys_cited_as_support=decoys_cited, confidence=confidence,
    )
