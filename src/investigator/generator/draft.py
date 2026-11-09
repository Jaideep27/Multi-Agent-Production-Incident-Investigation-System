"""The mutable working set that archetypes, decoys and noise edit before finalize.py.

Items have no evidence IDs yet: IDs are assigned at the end, in time order, so an ID's
number says nothing about which item matters. Truth rows (decisive, decoys) hold the item
objects themselves and pick up the ID once it exists.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DraftEdge:
    caller: str
    callee: str
    config: dict
    evidence_id: str = ""


@dataclass
class DraftLog:
    service: str
    instance: str | None
    ts: datetime
    level: str
    message: str
    attrs: dict
    evidence_id: str = ""


@dataclass
class DraftMetric:
    service: str
    instance: str | None
    metric_name: str
    unit: str
    source: str
    interval_s: int
    points: list          # [timestamp, value] pairs in time order
    evidence_id: str = ""


@dataclass
class DraftChange:
    service: str
    ts: datetime
    change_type: str
    version: str | None
    author: str | None
    summary: str
    diff: dict | None
    evidence_id: str = ""


@dataclass
class DraftPastIncident:
    occurred_at: datetime
    title: str
    category: str
    component: str | None
    resolution: str | None
    symptoms: list
    evidence_id: str = ""


@dataclass
class Draft:
    domain: str
    topology: dict
    window_start: datetime
    window_end: datetime
    services: list = field(default_factory=list)     # [name, role, instance count]
    edges: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    past_incidents: list = field(default_factory=list)
    tool_rules: list = field(default_factory=list)   # [tool name, rules dict]

    # Set by the archetype.
    trigger_ts: datetime | None = None   # when the root cause starts (decoys are placed around it)
    reported_at: datetime | None = None
    summary: str = ""
    reporter: str = ""
    scenario: dict = field(default_factory=dict)
    # Services whose logs noise must never remove (the archetype protects the root cause).
    protected_services: list = field(default_factory=list)
    # For inconclusive incidents: the plan entry's insufficiency spec as a dict
    # (reason, withhold, unreachable, alternatives); None for identifiable ones.
    insufficiency: dict | None = None
    onset_ts: datetime | None = None

    # Truth, as [item, label] pairs; labels become availability or decoy_type.
    decisive: list = field(default_factory=list)
    decoys: list = field(default_factory=list)
    withheld: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    noise: list = field(default_factory=list)        # [noise type, params dict]

    def service_names(self):
        names = []
        for name, role, count in self.services:
            names.append(name)
        return names

    def service_role(self, name):
        for service_name, role, count in self.services:
            if service_name == name:
                return role
        raise ValueError("unknown service " + name)

    def instances_of(self, name):
        for service_name, role, count in self.services:
            if service_name == name:
                instances = []
                for number in range(count):
                    instances.append("%s-%d" % (name, number))
                return instances
        raise ValueError("unknown service " + name)

    def find_edge(self, caller, callee):
        for edge in self.edges:
            if edge.caller == caller and edge.callee == callee:
                return edge
        raise ValueError("no edge %s -> %s" % (caller, callee))

    def find_metric(self, service, metric_name):
        for metric in self.metrics:
            if metric.service == service and metric.metric_name == metric_name:
                return metric
        raise ValueError("no metric %s on %s" % (metric_name, service))
