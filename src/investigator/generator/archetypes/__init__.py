"""Archetype registry: name -> module-like object with Params and apply(draft, rng, params).

Archetype names are answer-key vocabulary: only the generator may mention them. They describe
the mechanism and differ from category names on purpose.
"""

from types import SimpleNamespace

from investigator.generator.archetypes import (
    capacity,
    data_layer,
    network,
    pool_size_regression,
    releases,
    stories,
)


def registered(params_model, apply_function):
    return SimpleNamespace(Params=params_model, apply=apply_function)


ARCHETYPES = {
    "pool_size_regression": pool_size_regression,
    "retry_policy_regression": registered(releases.RetryPolicyParams, releases.retry_policy_regression),
    "bad_release": registered(releases.BadReleaseParams, releases.bad_release),
    "traffic_surge": registered(capacity.TrafficSurgeParams, capacity.traffic_surge),
    "resource_saturation": registered(capacity.ResourceSaturationParams, capacity.resource_saturation),
    "rate_limit_throttling": registered(capacity.RateLimitParams, capacity.rate_limit_throttling),
    "cache_degradation": registered(data_layer.CacheDegradationParams, data_layer.cache_degradation),
    "lock_contention": registered(data_layer.LockContentionParams, data_layer.lock_contention),
    "lb_misbalance": registered(network.LbMisbalanceParams, network.lb_misbalance),
    "upstream_vendor_failure": registered(network.VendorFailureParams, network.upstream_vendor_failure),
    "certificate_expiry": registered(network.CertificateExpiryParams, network.certificate_expiry),
    # story-driven evaluation set
    "session_cache_saturation": registered(stories.CacheSaturationParams,
                                           stories.session_cache_saturation),
    "feature_flag_regression": registered(stories.FeatureFlagParams, stories.feature_flag_regression),
    "swallowed_error": registered(stories.SwallowedErrorParams, stories.swallowed_error),
    "rate_limiter_retry_storm": registered(stories.RetryStormParams, stories.rate_limiter_retry_storm),
    "schema_migration_backlog": registered(stories.MigrationParams, stories.schema_migration_backlog),
    "io_saturation": registered(stories.IoSaturationParams, stories.io_saturation),
    "crash_with_two_changes": registered(stories.CrashParams, stories.crash_with_two_changes),
    "missing_client_timeout": registered(stories.MissingTimeoutParams, stories.missing_client_timeout),
}
