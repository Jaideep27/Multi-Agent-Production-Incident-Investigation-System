# The 12 evaluation incidents (label eval-v2)

Generated from the demo database (same data as the web viewer). Each system saw only the
brief and the five MCP tools; the answer key below was hidden from both.

How "right" is scored (`src/investigator/scorer/metrics.py`):
- **Identifiable incident:** right only when the top hypothesis has the exact true service
  **and** the true category. Saying "inconclusive" does not change this. The "said" label is
  shown separately below.
- **Inconclusive incident (CX-4, PG-5):** right when the system says "inconclusive", whatever
  its top guess.

Known strictness cases:
- **OTT-4 and FD-2:** both systems named the external vendor (drm-provider, maps-api) instead
  of our client service.
- **OTT-2:** the single agent named redis-catalog with category config_change, while the key
  says cache_failure.

## CX-1 · crypto exchange
- **Brief (what users saw):** order success rate dropped to 71% during a busy period
- **True root cause:** `order-api` · capacity scaling: Traffic rose about 3.8x; order-api stayed at its maximum of 4 replicas, saturated its CPU and request queue, and shed load.
- **Expected outcome:** identifiable
- **Misleading evidence:** price-feed-client deploy: price-feed-client deploy v2.29.0 *(red herring change)*
- **Noise:** clock skew, noisy warnings · **Tool failures:** none
- **Team:** ❌ wrong — identified `edge-lb` (conf 0.6) · 23 tool calls, 22 LLM calls, 210K input tokens, 193 s
- **Single agent:** ✅ right — identified `order-api` (conf 0.8) · 8 tool calls, 4 LLM calls, 69K input tokens, 57 s

## CX-2 · crypto exchange
- **Brief (what users saw):** order success rate dropped to 85%
- **True root cause:** `wallet-service` · resource exhaustion: wallet-service hit its memory limit (512Mi) under load; containers were OOM-killed and restarted, failing in-flight requests.
- **Expected outcome:** identifiable
- **Misleading evidence:** blockchain-node log: peer sync lagging 3 blocks behind tip *(downstream symptom)*
- **Noise:** noisy warnings · **Tool failures:** query_metrics (transient timeout)
- **Team:** ✅ right — inconclusive, top guess `wallet-service` (conf 0.65) · 40 tool calls, 34 LLM calls, 430K input tokens, 297 s
- **Single agent:** ✅ right — identified `wallet-service` (conf 0.8) · 7 tool calls, 4 LLM calls, 70K input tokens, 64 s

## CX-4 · crypto exchange
- **Brief (what users saw):** order success rate dropped to 79%
- **True root cause:** `postgres-ledger` · db contention: A long-running transaction (pid 6691) held row locks on ledger_entries in postgres-ledger; writes queued behind it and timed out.
- **Expected outcome:** inconclusive (Lock-wait logs and lock metrics were never collected, and the diff of the matching-engine deploy that would rule it out cannot be fetched.)
- **Misleading evidence:** matching-engine deploy: matching-engine deploy v2.10.0 *(red herring change)*
- **Noise:** conflicting observations, noisy warnings · **Tool failures:** get_change_history (persistent error)
- **Team:** ✅ right — inconclusive, top guess `postgres-ledger` (conf 0.35) · 40 tool calls, 42 LLM calls, 519K input tokens, 423 s
- **Single agent:** ✅ right — inconclusive, top guess `matching-engine` (conf 0.45) · 25 tool calls, 13 LLM calls, 177K input tokens, 148 s

## EC-1 · ecommerce
- **Brief (what users saw):** checkout success rate dropped to 72%
- **True root cause:** `postgres-orders` · db contention: Many concurrent checkouts updated the same inventory row on postgres-orders; they queued on its row lock until the lock wait timed out.
- **Expected outcome:** identifiable
- **Misleading evidence:** checkout-service log: payment-ext call slow: 2800ms *(downstream symptom)*
- **Noise:** noisy warnings · **Tool failures:** none
- **Team:** ✅ right — inconclusive, top guess `postgres-orders` (conf 0.4) · 40 tool calls, 38 LLM calls, 502K input tokens, 434 s
- **Single agent:** ❌ wrong — inconclusive, top guess `None` (conf -) · 8 tool calls, 6 LLM calls, 108K input tokens, 74 s

## EC-3 · ecommerce
- **Brief (what users saw):** checkout success rate dropped to 81% after a release
- **True root cause:** `checkout-service` · bad deploy: Release v5.2.0 of checkout-service keeps every session in an unbounded map; memory grows until the container is OOM-killed, roughly every 40 minutes.
- **Expected outcome:** identifiable
- **Misleading evidence:** edge-lb infra: edge-lb node pool kernel patch *(red herring change)*; inventory-service scaling: inventory-service scaled from 3 to 5 replicas *(red herring change)*
- **Noise:** noisy warnings · **Tool failures:** get_change_history (partial )
- **Team:** ✅ right — inconclusive, top guess `checkout-service` (conf 0.55) · 40 tool calls, 34 LLM calls, 403K input tokens, 319 s
- **Single agent:** ✅ right — identified `checkout-service` (conf 0.85) · 12 tool calls, 7 LLM calls, 94K input tokens, 87 s

## FD-2 · food delivery
- **Brief (what users saw):** order placement success rate dropped to 74%
- **True root cause:** `maps-client` · rate limiting: maps-client exhausted its request quota at maps-api (100000 requests a day); every further call was rejected with 429.
- **Expected outcome:** identifiable
- **Misleading evidence:** dispatch-service deploy: dispatch-service deploy v2.20.0 *(red herring change)*
- **Noise:** missing logs, noisy warnings · **Tool failures:** none
- **Team:** ❌ wrong — identified `maps-api` (conf 0.7) · 40 tool calls, 36 LLM calls, 562K input tokens, 372 s
- **Single agent:** ❌ wrong — identified `maps-api` (conf 0.8) · 15 tool calls, 8 LLM calls, 129K input tokens, 92 s

## FD-5 · food delivery
- **Brief (what users saw):** Push notifications to riders and customers are delayed by about 20 minutes
- **True root cause:** `notification-service` · cert expiry: The client certificate notification-service presents to push-provider expired at 2026-10-20T00:00:00Z; every mutual-TLS handshake has failed since.
- **Expected outcome:** identifiable
- **Misleading evidence:** notification-service deploy: notification-service deploy v2.21.0 *(red herring change)*; kafka-dispatch metric consumer_lag *(unrelated spike)*
- **Noise:** noisy warnings · **Tool failures:** get_service_dependencies (transient timeout)
- **Team:** ❌ wrong — inconclusive, top guess `notification-service` (conf 0.35) · 40 tool calls, 37 LLM calls, 620K input tokens, 369 s
- **Single agent:** ✅ right — identified `notification-service` (conf 0.65) · 26 tool calls, 11 LLM calls, 193K input tokens, 139 s

## OTT-2 · ott
- **Brief (what users saw):** video start success rate dropped to 83%
- **True root cause:** `redis-catalog` · cache failure: A config change gave every redis-catalog key the same 600s TTL; keys expire together, and each mass expiry sends a burst of misses to the database.
- **Expected outcome:** identifiable
- **Misleading evidence:** postgres-meta log: CPU at 86% serving catalog queries *(downstream symptom)*
- **Noise:** conflicting observations, noisy warnings · **Tool failures:** none
- **Team:** ✅ right — inconclusive, top guess `redis-catalog` (conf 0.45) · 40 tool calls, 40 LLM calls, 504K input tokens, 382 s
- **Single agent:** ❌ wrong — identified `redis-catalog` (conf 0.8) · 8 tool calls, 5 LLM calls, 94K input tokens, 76 s

## OTT-4 · ott
- **Brief (what users saw):** video start success rate dropped to 74%
- **True root cause:** `license-service` · dependency failure: drm-provider started failing with 503; license-service retried every call up to three times, multiplying load and stalling its callers.
- **Expected outcome:** identifiable
- **Misleading evidence:** playback-service deploy: playback-service deploy v2.28.0 *(red herring change)*
- **Noise:** noisy warnings · **Tool failures:** none
- **Team:** ❌ wrong — inconclusive, top guess `drm-provider` (conf 0.4) · 40 tool calls, 44 LLM calls, 545K input tokens, 434 s
- **Single agent:** ❌ wrong — identified `drm-provider` (conf 0.6) · 26 tool calls, 13 LLM calls, 246K input tokens, 183 s

## PG-1 · payment gateway
- **Brief (what users saw):** UPI success rate dropped to 68%
- **True root cause:** `payment-router` · config change: A config change cut the payment-router connection pool to postgres-payments from 100 to 20; requests wait for a free connection, time out, and the UPI success rate drops.
- **Expected outcome:** identifiable
- **Misleading evidence:** notification-service deploy: notification-service deploy v2.26.0 *(red herring change)*; past incident: UPI failures during partner bank outage *(lookalike history)*
- **Noise:** missing logs, noisy warnings · **Tool failures:** query_logs (transient timeout)
- **Team:** ✅ right — identified `payment-router` (conf 0.6) · 39 tool calls, 36 LLM calls, 416K input tokens, 353 s
- **Single agent:** ✅ right — identified `payment-router` (conf 0.85) · 8 tool calls, 5 LLM calls, 87K input tokens, 64 s

## PG-2 · payment gateway
- **Brief (what users saw):** Some requests time out; UPI success rate at 86%
- **True root cause:** `edge-lb` · lb imbalance: A load-balancer config change gave payment-api-0 most of the payment-api traffic; that one instance saturated while the fleet average looked fine.
- **Expected outcome:** identifiable
- **Misleading evidence:** bank-adapter metric p99_latency_ms *(unrelated spike)*
- **Noise:** noisy warnings · **Tool failures:** none
- **Team:** ✅ right — inconclusive, top guess `edge-lb` (conf 0.4) · 40 tool calls, 40 LLM calls, 465K input tokens, 346 s
- **Single agent:** ✅ right — identified `edge-lb` (conf 0.8) · 8 tool calls, 4 LLM calls, 69K input tokens, 44 s

## PG-5 · payment gateway
- **Brief (what users saw):** UPI success rate dropped to 90%
- **True root cause:** `bank-adapter` · cert expiry: The client certificate bank-adapter presents to upi-switch expired at 2026-10-31T00:00:00Z; every mutual-TLS handshake has failed since.
- **Expected outcome:** inconclusive (The handshake errors that name the expired certificate were never logged, the handshake-failure metric was not collected, and the certificate rotation record cannot be fetched; only connection resets are visible.)
- **Misleading evidence:** past incident: UPI failures during partner bank outage *(lookalike history)*
- **Noise:** noisy warnings · **Tool failures:** get_change_history (persistent timeout)
- **Team:** ✅ right — inconclusive, top guess `bank-adapter` (conf 0.5) · 40 tool calls, 30 LLM calls, 354K input tokens, 290 s
- **Single agent:** ✅ right — inconclusive, top guess `payment-router` (conf 0.35) · 35 tool calls, 15 LLM calls, 219K input tokens, 284 s
