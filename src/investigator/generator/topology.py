"""Domain topologies: the services, their call edges and everyday vocabulary of one domain.

A topology knows nothing about any incident. Archetypes and decoys pick services from it by
name (the names come from the plan entry), so the same archetype works on any domain whose
topology has a matching edge.

Five domains: crypto_exchange, payment_gateway, ott, ecommerce, food_delivery. Every topology
names its load balancer edge-lb, so archetypes can refer to it in any domain.
"""

import copy

PAYMENT_GATEWAY = {
    "entry_service": "payment-api",
    # (metric name, unit, healthy mean, standard deviation, label used in incident summaries)
    "business_metric": ("upi_success_rate_pct", "percent", 99.2, 0.2, "UPI success rate"),
    "routes": ["/v1/upi/collect", "/v1/upi/pay", "/v1/refunds", "/v1/mandates"],
    "reporters": ["support-oncall", "merchant-success", "noc-shift-lead"],
    "authors": ["svc-infra", "team-payments", "team-platform", "release-bot"],
    # (name, role, instance count)
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("payment-api", "app", 4),
        ("payment-router", "app", 3),
        ("bank-adapter", "app", 3),
        ("postgres-payments", "database", 1),
        ("redis-idempotency", "cache", 2),
        ("kafka-events", "queue", 3),
        ("notification-service", "worker", 2),
        ("webhook-worker", "worker", 2),
        ("upi-switch", "external_api", 1),
    ],
    # (caller, callee, config). An edge with "max_pool" is a connection pool, and the
    # baseline gives its caller pool metrics.
    "edges": [
        ("edge-lb", "payment-api", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("payment-api", "payment-router", {"timeout_ms": 10000, "retries": 1}),
        ("payment-api", "redis-idempotency", {"timeout_ms": 50}),
        ("payment-router", "postgres-payments", {"max_pool": 100, "acquire_timeout_ms": 5000}),
        ("payment-router", "bank-adapter", {"timeout_ms": 3000, "retries": 2}),
        ("payment-router", "kafka-events", {"acks": "all"}),
        ("bank-adapter", "upi-switch", {"timeout_ms": 2500, "mtls": True}),
        ("notification-service", "kafka-events", {"consumer_group": "notify"}),
        ("webhook-worker", "kafka-events", {"consumer_group": "webhooks"}),
        ("webhook-worker", "postgres-payments", {"max_pool": 10, "acquire_timeout_ms": 5000}),
    ],
    # Unrelated history that every incident in this domain can show.
    "history": [
        {
            "title": "Refund API errors after a schema migration",
            "category": "bad_deploy",
            "component": "payment-api",
            "resolution": "rolled back the migration",
            "symptoms": ["refund errors", "5xx on /v1/refunds"],
        },
        {
            "title": "Webhook delivery delays during a merchant endpoint outage",
            "category": "dependency_failure",
            "component": "webhook-worker",
            "resolution": "paused retries to the failing merchant",
            "symptoms": ["webhook queue growth", "late merchant callbacks"],
        },
        {
            "title": "Idempotency cache evictions under festival peak",
            "category": "cache_failure",
            "component": "redis-idempotency",
            "resolution": "raised maxmemory and changed the eviction policy",
            "symptoms": ["duplicate payment attempts", "cache hit ratio drop"],
        },
    ],
}

CRYPTO_EXCHANGE = {
    "entry_service": "order-api",
    "business_metric": ("order_success_rate_pct", "percent", 99.4, 0.2, "order success rate"),
    "routes": ["/v1/orders", "/v1/orders/cancel", "/v1/withdrawals", "/v1/quotes"],
    "reporters": ["trader-support", "exchange-oncall", "risk-desk"],
    "authors": ["team-trading", "team-wallets", "svc-infra", "release-bot"],
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("order-api", "app", 4),
        ("matching-engine", "app", 3),
        ("price-feed-client", "app", 2),
        ("market-data-vendor", "external_api", 1),
        ("wallet-service", "app", 3),
        ("blockchain-node", "worker", 2),
        ("redis-orderbook", "cache", 2),
        ("postgres-ledger", "database", 1),
        ("kafka-trades", "queue", 3),
    ],
    "edges": [
        ("edge-lb", "order-api", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("order-api", "matching-engine", {"timeout_ms": 8000, "retries": 1}),
        ("order-api", "redis-orderbook", {"timeout_ms": 50}),
        ("order-api", "wallet-service", {"timeout_ms": 10000, "retries": 0}),
        ("matching-engine", "postgres-ledger", {"max_pool": 60, "acquire_timeout_ms": 5000}),
        ("matching-engine", "redis-orderbook", {"timeout_ms": 50}),
        ("matching-engine", "price-feed-client", {"timeout_ms": 2000, "retries": 1}),
        ("matching-engine", "kafka-trades", {"acks": "all"}),
        ("price-feed-client", "market-data-vendor", {"timeout_ms": 3000, "retries": 3}),
        ("wallet-service", "blockchain-node", {"timeout_ms": 15000}),
        ("wallet-service", "postgres-ledger", {"max_pool": 20, "acquire_timeout_ms": 5000}),
    ],
    "history": [
        {"title": "Withdrawal delays while a blockchain node resynced", "category": "dependency_failure",
         "component": "blockchain-node", "resolution": "switched to a healthy node",
         "symptoms": ["withdrawals pending", "node sync lag"]},
        {"title": "Matching latency after a ledger index was dropped", "category": "bad_deploy",
         "component": "matching-engine", "resolution": "restored the index",
         "symptoms": ["order latency", "ledger slow queries"]},
        {"title": "Quote staleness during a vendor maintenance window", "category": "dependency_failure",
         "component": "price-feed-client", "resolution": "failed over to the backup feed",
         "symptoms": ["stale quotes", "vendor errors"]},
    ],
}

OTT = {
    "entry_service": "api-gateway",
    "business_metric": ("play_start_success_pct", "percent", 99.1, 0.3, "video start success rate"),
    "routes": ["/v1/play", "/v1/catalog/home", "/v1/profiles/switch", "/v1/license"],
    "reporters": ["viewer-support", "streaming-noc", "social-monitoring"],
    "authors": ["team-playback", "team-catalog", "team-identity", "svc-infra"],
    "services": [
        ("edge-lb", "load_balancer", 3),
        ("api-gateway", "app", 4),
        ("playback-service", "app", 4),
        ("catalog-api", "app", 3),
        ("auth-service", "app", 3),
        ("license-service", "app", 2),
        ("drm-provider", "external_api", 1),
        ("redis-catalog", "cache", 2),
        ("postgres-meta", "database", 1),
        ("origin-west", "app", 2),
        ("origin-east", "app", 2),
        ("cdn-edge", "cdn", 1),
    ],
    "edges": [
        ("edge-lb", "api-gateway", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("edge-lb", "origin-west", {"timeout_ms": 10000, "route": "eu-west"}),
        ("edge-lb", "origin-east", {"timeout_ms": 10000, "route": "us-east"}),
        ("api-gateway", "playback-service", {"timeout_ms": 8000, "retries": 1}),
        ("api-gateway", "catalog-api", {"timeout_ms": 5000, "retries": 1}),
        ("api-gateway", "auth-service", {"timeout_ms": 3000, "retries": 0}),
        ("playback-service", "license-service", {"timeout_ms": 4000, "retries": 1}),
        ("playback-service", "cdn-edge", {"timeout_ms": 2000}),
        ("license-service", "drm-provider", {"timeout_ms": 3000, "retries": 2}),
        ("catalog-api", "redis-catalog", {"timeout_ms": 50}),
        ("catalog-api", "postgres-meta", {"max_pool": 40, "acquire_timeout_ms": 5000}),
        ("auth-service", "postgres-meta", {"max_pool": 30, "acquire_timeout_ms": 5000}),
    ],
    "history": [
        {"title": "Start failures during a regional CDN purge", "category": "cache_failure",
         "component": "cdn-edge", "resolution": "re-warmed the CDN",
         "symptoms": ["video start failures", "origin load spike"]},
        {"title": "Profile errors after a token signing key rotation", "category": "config_change",
         "component": "auth-service", "resolution": "re-published the old key",
         "symptoms": ["login errors", "profile switching errors"]},
        {"title": "Catalog slowness from an unindexed query", "category": "bad_deploy",
         "component": "catalog-api", "resolution": "added the index",
         "symptoms": ["home page slow", "database CPU high"]},
    ],
}

ECOMMERCE = {
    "entry_service": "storefront-api",
    "business_metric": ("checkout_success_rate_pct", "percent", 98.8, 0.3, "checkout success rate"),
    "routes": ["/v1/cart", "/v1/checkout", "/v1/orders", "/v1/products"],
    "reporters": ["customer-care", "sales-ops", "site-reliability"],
    "authors": ["team-checkout", "team-cart", "team-catalog", "release-bot"],
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("storefront-api", "app", 4),
        ("cart-service", "app", 3),
        ("checkout-service", "app", 3),
        ("inventory-service", "app", 2),
        ("redis-cart", "cache", 2),
        ("postgres-orders", "database", 1),
        ("payment-ext", "external_api", 1),
        ("order-worker", "worker", 2),
        ("rabbitmq-orders", "queue", 3),
    ],
    "edges": [
        ("edge-lb", "storefront-api", {"timeout_ms": 30000, "algorithm": "round_robin",
                                       "rate_limit_per_ip_rps": 100}),
        ("storefront-api", "cart-service", {"timeout_ms": 3000, "retries": 1}),
        ("storefront-api", "checkout-service", {"timeout_ms": 10000, "retries": 0}),
        ("cart-service", "redis-cart", {"timeout_ms": 50}),
        ("checkout-service", "inventory-service", {"timeout_ms": 3000, "retries": 1}),
        ("checkout-service", "payment-ext", {"timeout_ms": 8000, "retries": 1}),
        ("checkout-service", "postgres-orders", {"max_pool": 40, "acquire_timeout_ms": 5000}),
        ("checkout-service", "rabbitmq-orders", {"acks": "all"}),
        ("inventory-service", "postgres-orders", {"max_pool": 20, "acquire_timeout_ms": 5000}),
        ("order-worker", "rabbitmq-orders", {"consumer_group": "orders"}),
    ],
    "history": [
        {"title": "Checkout errors during a payment provider outage", "category": "dependency_failure",
         "component": "payment-ext", "resolution": "switched to the backup provider",
         "symptoms": ["checkout errors", "payment timeouts"]},
        {"title": "Cart losses after a cache failover", "category": "cache_failure",
         "component": "redis-cart", "resolution": "restored from replica",
         "symptoms": ["empty carts", "cart errors"]},
        {"title": "Slow product pages after a search index rebuild", "category": "capacity_scaling",
         "component": "storefront-api", "resolution": "added capacity during rebuilds",
         "symptoms": ["slow pages", "high CPU"]},
    ],
}

FOOD_DELIVERY = {
    "entry_service": "order-service",
    "business_metric": ("order_placement_success_pct", "percent", 99.0, 0.3,
                        "order placement success rate"),
    "routes": ["/v1/orders", "/v1/riders/assign", "/v1/eta", "/v1/menu"],
    "reporters": ["rider-support", "restaurant-ops", "delivery-noc"],
    "authors": ["team-orders", "team-dispatch", "team-notify", "svc-infra"],
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("order-service", "app", 4),
        ("rider-api", "app", 3),
        ("dispatch-service", "app", 3),
        ("dispatch-consumer", "worker", 2),
        ("maps-client", "app", 2),
        ("maps-api", "external_api", 1),
        ("postgres-orders", "database", 1),
        ("redis-geo", "cache", 2),
        ("kafka-dispatch", "queue", 3),
        ("notification-service", "worker", 2),
        ("push-provider", "external_api", 1),
    ],
    "edges": [
        ("edge-lb", "order-service", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("edge-lb", "rider-api", {"timeout_ms": 30000, "session_affinity": "none"}),
        ("order-service", "dispatch-service", {"timeout_ms": 5000, "retries": 1}),
        ("order-service", "postgres-orders", {"max_pool": 25, "acquire_timeout_ms": 5000}),
        ("order-service", "kafka-dispatch", {"acks": "all"}),
        ("dispatch-service", "maps-client", {"timeout_ms": 3000, "retries": 1}),
        ("dispatch-service", "redis-geo", {"timeout_ms": 50}),
        ("maps-client", "maps-api", {"timeout_ms": 2500, "retries": 2}),
        ("dispatch-consumer", "kafka-dispatch", {"consumer_group": "assignments"}),
        ("dispatch-consumer", "postgres-orders", {"max_pool": 10, "acquire_timeout_ms": 5000}),
        ("rider-api", "redis-geo", {"timeout_ms": 50}),
        ("notification-service", "kafka-dispatch", {"consumer_group": "notify"}),
        ("notification-service", "push-provider", {"timeout_ms": 4000, "mtls": True}),
    ],
    "history": [
        {"title": "Late orders during a maps provider brownout", "category": "dependency_failure",
         "component": "maps-api", "resolution": "fell back to cached routes",
         "symptoms": ["wrong ETAs", "maps errors"]},
        {"title": "Assignment backlog after a partition rebalance", "category": "resource_exhaustion",
         "component": "kafka-dispatch", "resolution": "added partitions",
         "symptoms": ["orders not assigned", "consumer lag"]},
        {"title": "Lunch peak slowdowns from undersized order fleet", "category": "capacity_scaling",
         "component": "order-service", "resolution": "raised autoscaling limits",
         "symptoms": ["order placement slow", "high CPU"]},
    ],
}

# ------------------------------------------------------------------ story platforms
# Three more platforms for the story-driven evaluation set. They are separate topologies so
# the five above (and every incident built on them) stay exactly as they were.

ONLINE_RETAIL = {
    "entry_service": "web-frontend",
    "business_metric": ("order_success_rate_pct", "percent", 98.9, 0.3, "order success rate"),
    "routes": ["/checkout", "/cart", "/product", "/login"],
    "reporters": ["customer-support", "storefront-oncall", "ops-shift-lead"],
    "authors": ["team-checkout", "team-catalog", "team-fulfillment", "svc-infra", "release-bot"],
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("web-frontend", "app", 4),
        ("session-cache", "cache", 2),
        ("checkout-api", "app", 3),
        ("orders-db", "database", 1),
        ("payment-service", "app", 3),
        ("fraud-scoring-api", "app", 2),
        ("catalog-graphql", "app", 3),
        ("inventory-grpc", "app", 3),
        ("fulfillment-service", "app", 3),
        ("shipping-carrier", "external_api", 1),
    ],
    "edges": [
        ("edge-lb", "web-frontend", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("web-frontend", "session-cache", {"timeout_ms": 50}),
        ("web-frontend", "checkout-api", {"timeout_ms": 10000, "retries": 0}),
        ("web-frontend", "catalog-graphql", {"timeout_ms": 5000, "retries": 1}),
        ("catalog-graphql", "inventory-grpc", {"timeout_ms": 2000, "retries": 1}),
        ("checkout-api", "orders-db", {"max_pool": 100, "acquire_timeout_ms": 5000}),
        ("checkout-api", "payment-service", {"timeout_ms": 8000, "retries": 1}),
        ("payment-service", "fraud-scoring-api", {"timeout_ms": 1500, "tls": "verify"}),
        ("checkout-api", "fulfillment-service", {"timeout_ms": 5000, "retries": 0}),
        # No client timeout on the carrier webhook call: a latent weakness of this platform.
        ("fulfillment-service", "shipping-carrier", {"protocol": "https", "retries": 0}),
    ],
    "history": [
        {"title": "Cart resets after a session-cache failover", "category": "cache_failure",
         "component": "session-cache", "resolution": "promoted the replica and warmed the cache",
         "symptoms": ["cart resets", "session misses"]},
        {"title": "Slow product pages during a catalog reindex", "category": "resource_exhaustion",
         "component": "catalog-graphql", "resolution": "throttled the reindex job",
         "symptoms": ["product page latency", "catalog CPU"]},
        {"title": "Checkout errors from an expired payment-service config secret",
         "category": "config_change", "component": "payment-service",
         "resolution": "restored the secret", "symptoms": ["payment declines", "checkout 5xx"]},
    ],
}

SEARCH_PLATFORM = {
    "entry_service": "search-service",
    "business_metric": ("search_success_rate_pct", "percent", 99.5, 0.2, "search success rate"),
    "routes": ["/search", "/suggest", "/recommendations", "/search/filters"],
    "reporters": ["search-oncall", "customer-support", "seo-team"],
    "authors": ["team-search", "team-relevance", "team-ml", "svc-infra"],
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("search-service", "app", 4),
        ("query-parser", "app", 3),
        ("elasticsearch", "database", 3),
        ("results-cache", "cache", 2),
        ("recommendation-engine", "app", 3),
        ("model-store", "cache", 2),
    ],
    "edges": [
        ("edge-lb", "search-service", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("search-service", "query-parser", {"timeout_ms": 5000, "retries": 0}),
        ("search-service", "elasticsearch", {"timeout_ms": 5000, "retries": 1}),
        ("search-service", "results-cache", {"timeout_ms": 50}),
        ("search-service", "recommendation-engine", {"timeout_ms": 3000, "retries": 1}),
        ("recommendation-engine", "model-store", {"timeout_ms": 100}),
    ],
    "history": [
        {"title": "Stale suggestions after a results-cache flush failed", "category": "cache_failure",
         "component": "results-cache", "resolution": "flushed and re-warmed the cache",
         "symptoms": ["stale suggestions", "cache errors"]},
        {"title": "Recommendation errors after model-store evictions", "category": "cache_failure",
         "component": "model-store", "resolution": "raised model-store memory",
         "symptoms": ["empty recommendations", "model load errors"]},
        {"title": "Slow facets after a search-service mapping change", "category": "bad_deploy",
         "component": "search-service", "resolution": "rolled back the mapping",
         "symptoms": ["facet latency", "search timeouts on filters"]},
    ],
}

SAAS_PLATFORM = {
    "entry_service": "api-gateway",
    "business_metric": ("api_success_rate_pct", "percent", 99.3, 0.2, "API success rate"),
    "routes": ["/v1/login", "/v1/profile", "/v1/invoices", "/v1/events", "/v1/media"],
    "reporters": ["platform-oncall", "customer-success", "billing-ops"],
    "authors": ["team-identity", "team-billing", "team-data", "team-media", "svc-infra"],
    "services": [
        ("edge-lb", "load_balancer", 2),
        ("api-gateway", "app", 4),
        ("auth-service", "app", 3),
        ("user-profile", "app", 3),
        ("notification-worker", "worker", 2),
        ("billing-service", "app", 3),
        ("billing-db", "database", 1),
        ("kafka-billing", "queue", 3),
        ("invoice-worker", "worker", 3),
        ("analytics-ingest", "app", 3),
        ("kafka-events", "queue", 3),
        ("media-transcoder", "worker", 2),
    ],
    "edges": [
        ("edge-lb", "api-gateway", {"timeout_ms": 30000, "algorithm": "round_robin"}),
        ("api-gateway", "auth-service", {"timeout_ms": 2000, "retries": 2}),
        ("api-gateway", "user-profile", {"timeout_ms": 3000, "retries": 2}),
        ("user-profile", "auth-service", {"timeout_ms": 2000, "retries": 3}),
        ("notification-worker", "user-profile", {"timeout_ms": 2000, "retries": 3}),
        ("notification-worker", "auth-service", {"timeout_ms": 2000, "retries": 3}),
        ("api-gateway", "billing-service", {"timeout_ms": 8000, "retries": 0}),
        ("billing-service", "billing-db", {"max_pool": 40, "acquire_timeout_ms": 5000}),
        ("billing-service", "kafka-billing", {"acks": "all"}),
        ("invoice-worker", "kafka-billing", {"consumer_group": "invoices"}),
        ("invoice-worker", "billing-db", {"max_pool": 10, "acquire_timeout_ms": 5000}),
        ("api-gateway", "analytics-ingest", {"timeout_ms": 2000, "retries": 0}),
        ("analytics-ingest", "kafka-events", {"acks": "1"}),
        ("media-transcoder", "kafka-events", {"consumer_group": "transcode"}),
    ],
    "history": [
        {"title": "Login latency during an auth-service token cache flush",
         "category": "cache_failure", "component": "auth-service",
         "resolution": "re-warmed the token cache", "symptoms": ["slow logins", "auth timeouts"]},
        {"title": "Invoice delays during a Kafka broker restart", "category": "resource_exhaustion",
         "component": "kafka-billing", "resolution": "moved partitions off the restarting broker",
         "symptoms": ["invoice delays", "consumer lag"]},
        {"title": "Profile errors after a user-profile deploy", "category": "bad_deploy",
         "component": "user-profile", "resolution": "rolled back", "symptoms": ["profile 5xx"]},
    ],
}

TOPOLOGIES = {
    "crypto_exchange": CRYPTO_EXCHANGE,
    "payment_gateway": PAYMENT_GATEWAY,
    "ott": OTT,
    "ecommerce": ECOMMERCE,
    "food_delivery": FOOD_DELIVERY,
    "online_retail": ONLINE_RETAIL,
    "search_platform": SEARCH_PLATFORM,
    "saas_platform": SAAS_PLATFORM,
}


def topology_for(domain):
    """A private copy, so a build can change edge configs without touching the template."""
    if domain not in TOPOLOGIES:
        raise ValueError("no topology for domain " + domain + " yet")
    return copy.deepcopy(TOPOLOGIES[domain])
