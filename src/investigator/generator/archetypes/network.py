"""Archetypes at the edges of the system: load balancing, third-party vendors, certificates."""

from typing import Literal

from investigator.generator.story import tell
from investigator.models.common import NonEmptyText, StrictModel


class LbMisbalanceParams(StrictModel):
    component: NonEmptyText          # the load balancer
    backend: NonEmptyText            # the service whose traffic is skewed
    mode: Literal["weight_skew", "region_misroute", "sticky_sessions"]
    other_backend: str | None = None  # region_misroute: the origin that should have been used


def lb_misbalance(draft, rng, raw_params):
    params = LbMisbalanceParams.model_validate(raw_params)
    hot = params.backend + "-0"
    story = {
        "root": params.component,
        "category": "lb_imbalance",
        "summary": "Some requests time out; {label} at {floor}%",
        "floor": [84, 92],
    }
    if params.mode == "weight_skew":
        story["mechanism"] = ("A load-balancer config change gave %s most of the %s traffic; "
                              "that one instance saturated while the fleet average looked fine."
                              % (hot, params.backend))
        story["change"] = {"change_type": "config_change", "version": None,
                           "summary": "%s configuration updated" % params.component,
                           "diff": {"backend_weights": {"from": "equal", "to": "%s=70, others=10" % hot}}}
        story["edge_updates"] = [[params.component, params.backend, "algorithm", "weighted"]]
        story["metrics"] = [{"name": "cpu_pct", "unit": "percent", "service": params.backend,
                             "per_instance": True, "healthy_mean": 30.0, "healthy_sd": 3.0,
                             "bad_mean": 98.0, "bad_sd": 1.0}]
        story["logs"] = [["WARN", "backend %s slow: 3 consecutive health check timeouts" % hot, {}]]
    elif params.mode == "region_misroute":
        story["mechanism"] = ("A routing change sent eu-west viewers to %s instead of %s; the "
                              "long round trip made video start slowly and buffer."
                              % (params.backend, params.other_backend))
        story["change"] = {"change_type": "config_change", "version": None,
                           "summary": "%s configuration updated" % params.component,
                           "diff": {"route.eu-west": {"from": params.other_backend, "to": params.backend}}}
        story["edge_updates"] = [[params.component, params.backend, "route", "eu-west,us-east"]]
        story["metrics"] = [{"name": "cross_region_requests_pct", "unit": "percent",
                             "healthy_mean": 3.0, "healthy_sd": 1.0, "bad_mean": 86.0, "bad_sd": 3.0}]
        story["logs"] = [["WARN", "routing eu-west traffic to %s (RTT 182ms)" % params.backend, {}]]
    else:
        story["mechanism"] = ("Cookie session affinity was turned on at %s; after %s restarted, "
                              "most sessions stayed pinned to %s, which overloaded while the "
                              "others idled." % (params.component, params.backend, hot))
        story["change"] = {"change_type": "config_change", "version": None,
                           "summary": "%s configuration updated" % params.component,
                           "diff": {"session_affinity": {"from": "none", "to": "cookie"}}}
        story["edge_updates"] = [[params.component, params.backend, "session_affinity", "cookie"]]
        story["metrics"] = [{"name": "cpu_pct", "unit": "percent", "service": params.backend,
                             "per_instance": True, "healthy_mean": 28.0, "healthy_sd": 3.0,
                             "bad_mean": 97.0, "bad_sd": 1.5}]
        story["logs"] = [["WARN", "affinity cookie pins 81%% of sessions to %s" % hot, {}]]
    tell(draft, rng, story)


class VendorFailureParams(StrictModel):
    component: NonEmptyText          # our client of the vendor
    vendor: NonEmptyText
    status: int = 502


def upstream_vendor_failure(draft, rng, raw_params):
    params = VendorFailureParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "dependency_failure",
        "mechanism": ("%s started failing with %d; %s retried every call up to three times, "
                      "multiplying load and stalling its callers."
                      % (params.vendor, params.status, params.component)),
        "summary": "{label} dropped to {floor}%",
        "floor": [72, 86],
        "metrics": [
            {"name": "upstream_error_rate_pct", "unit": "percent", "healthy_mean": 0.4,
             "healthy_sd": 0.2, "bad_mean": 63.0, "bad_sd": 5.0},
            {"name": "outbound_requests_per_s", "unit": "req/s", "healthy_mean": 120.0,
             "healthy_sd": 8.0, "bad_mean": 470.0, "bad_sd": 30.0},
        ],
        "logs": [
            ["ERROR", "%s returned %d; retry 3/3 exhausted" % (params.vendor, params.status),
             {"status": params.status}],
            ["WARN", "retry queue depth %d" % rng.randint(3000, 6000), {}],
        ],
    })


class CertificateExpiryParams(StrictModel):
    component: NonEmptyText
    peer: NonEmptyText
    not_after: NonEmptyText
    rotation_change: bool = False     # a failed rotation job left a change record
    affects_business: bool = True     # False: no drop in the entry service's business metric
    summary: str | None = None        # incident report text when it is not the business metric
    # client: the component's client certificate (to peer) expired; server: the component's
    # own serving certificate expired and the peer, its caller, fails.
    side: Literal["client", "server"] = "client"


def certificate_expiry(draft, rng, raw_params):
    params = CertificateExpiryParams.model_validate(raw_params)
    story = {
        "root": params.component,
        "category": "cert_expiry",
        "mechanism": ("The client certificate %s presents to %s expired at %s; every mutual-TLS "
                      "handshake has failed since." % (params.component, params.peer, params.not_after)),
        "summary": "{label} dropped to {floor}%",
        "floor": [78, 90],
        "metrics": [{"name": "tls_handshake_failures_per_s", "unit": "failures/s",
                     "healthy_mean": 0.0, "healthy_sd": 0.0, "bad_mean": 42.0, "bad_sd": 4.0}],
        "logs": [["ERROR", "TLS handshake with %s failed: client certificate expired (notAfter %s)"
                  % (params.peer, params.not_after), {}]],
        "caller_message": "upstream {callee} call failed: connection reset route={route}",
    }
    if not params.affects_business:
        story["floor"] = None
    if params.summary is not None:
        story["summary"] = params.summary
    if params.rotation_change:
        story["change"] = {"change_type": "infra", "version": None,
                           "summary": "%s scheduled certificate rotation" % params.component,
                           "diff": {"client_certificate": {"from": "expires %s" % params.not_after,
                                                           "to": "rotation job failed; unchanged"}}}
    if params.side == "server":
        # The component's own serving certificate expired: its callers (the peer) see the
        # handshake errors, so the evidence lives on the peer.
        story["mechanism"] = ("The TLS server certificate of %s expired at %s; %s rejects every "
                              "handshake to it, so its requests fail."
                              % (params.component, params.not_after, params.peer))
        story["metrics"][0]["service"] = params.peer
        story["log_service"] = params.peer
        story["logs"] = [["ERROR", "TLS handshake with %s failed: certificate has expired "
                                   "(notAfter %s)" % (params.component, params.not_after), {}]]
    tell(draft, rng, story)
