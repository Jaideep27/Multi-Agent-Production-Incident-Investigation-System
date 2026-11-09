# The evaluation incidents (label eval-v4)

From the demo database. Each system saw only the brief and the five MCP tools; the
answer key below was hidden from both. Right = exact service and category.

## INC-01 - online retail
- **Brief:** Checkout requests fail with HTTP 500; order success rate at 72%
- **True root cause:** `checkout-api` - bad deploy: Release v2.4.1 of checkout-api dereferences the promo code without a null check; every checkout without a promo code throws and returns HTTP 500.
- **Expected outcome:** identifiable
- **Misleading evidence:** none
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - identified `checkout-api` (confidence 0.65) - 32 tool calls, 34 LLM calls, 407K input tokens, 347 s
- **Single agent:** right - identified `checkout-api` (confidence 0.90) - 6 tool calls, 3 LLM calls, 45K input tokens, 38 s

## INC-02 - online retail
- **Brief:** Users are being logged out across the site during a traffic spike; order success rate at 89%
- **True root cause:** `session-cache` - resource exhaustion: A traffic spike of about 4x filled session-cache to its memory limit; it began evicting live session keys, so web-frontend users were logged out.
- **Expected outcome:** identifiable
- **Misleading evidence:** none
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - identified `session-cache` (confidence 0.55) - 35 tool calls, 41 LLM calls, 476K input tokens, 403 s
- **Single agent:** wrong - identified `session-cache` (confidence 0.80) - 8 tool calls, 5 LLM calls, 90K input tokens, 65 s

## INC-03 - online retail
- **Brief:** order success rate dropped to 72%
- **True root cause:** `checkout-api` - config change: A config change cut the checkout-api connection pool to orders-db from 100 to 10; requests wait for a free connection, time out, and the order success rate drops.
- **Expected outcome:** identifiable
- **Misleading evidence:** none
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - identified `checkout-api` (confidence 0.68) - 32 tool calls, 33 LLM calls, 388K input tokens, 355 s
- **Single agent:** right - identified `checkout-api` (confidence 0.90) - 8 tool calls, 3 LLM calls, 47K input tokens, 33 s

## INC-04 - online retail
- **Brief:** order success rate dropped to 85%
- **True root cause:** `fraud-scoring-api` - cert expiry: The TLS server certificate of fraud-scoring-api expired at 2026-10-30T14:36:00Z; payment-service rejects every handshake to it, so its requests fail.
- **Expected outcome:** identifiable
- **Misleading evidence:** payment-service deploy: payment-service deploy v2.10.0 *(red herring change)*
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - inconclusive, top guess `fraud-scoring-api` (confidence 0.35) - 34 tool calls, 30 LLM calls, 350K input tokens, 321 s
- **Single agent:** right - identified `fraud-scoring-api` (confidence 0.85) - 8 tool calls, 5 LLM calls, 91K input tokens, 64 s

## INC-05 - search platform
- **Brief:** Search is very slow (p99 around 8 s); search success rate at 93%
- **True root cause:** `query-parser` - config change: Turning on the fuzzy_synonyms feature flag in query-parser added a regular expression with catastrophic backtracking; parsing pinned its CPU and search p99 latency rose to about 8 seconds.
- **Expected outcome:** identifiable
- **Misleading evidence:** past incident: Search p99 at 8 s caused by Elasticsearch GC pauses *(lookalike history)*
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - inconclusive, top guess `query-parser` (confidence 0.25) - 31 tool calls, 38 LLM calls, 461K input tokens, 566 s
- **Single agent:** right - identified `query-parser` (confidence 0.85) - 7 tool calls, 4 LLM calls, 47K input tokens, 49 s

## INC-06 - online retail
- **Brief:** Customers report that about 40% of product pages render empty, while the dashboards show 99.9% HTTP 200
- **True root cause:** `inventory-grpc` - bad deploy: Release v4.2.0 of inventory-grpc catches stock lookup failures and returns HTTP 200 with an errors list and no items; status-code dashboards stay green while about 40% of product pages render empty.
- **Expected outcome:** identifiable
- **Misleading evidence:** none
- **Noise:** noisy warnings, conflicting observations - **Tool failures:** none
- **Team:** wrong - identified `inventory-grpc` (confidence 0.38) - 36 tool calls, 38 LLM calls, 419K input tokens, 492 s
- **Single agent:** wrong - identified `inventory-grpc` (confidence 0.70) - 11 tool calls, 5 LLM calls, 90K input tokens, 65 s

## INC-07 - saas platform
- **Brief:** Logins and profile pages fail intermittently; API success rate at 87%
- **True root cause:** `auth-service` - config change: A config change cut the per-client rate limit of auth-service from 500 to 50 rps; its callers' aggressive retries turned the 429s into a retry storm and mutual timeouts.
- **Expected outcome:** identifiable
- **Misleading evidence:** user-profile log: auth-service call timed out after 2000ms; retrying (attempt 3/3) *(downstream symptom)*; notification-worker log: user-profile call timed out after 2000ms; retrying (attempt 3/3) *(downstream symptom)*
- **Noise:** noisy warnings, clock skew - **Tool failures:** none
- **Team:** right - inconclusive, top guess `auth-service` (confidence 0.25) - 38 tool calls, 45 LLM calls, 651K input tokens, 715 s
- **Single agent:** right - identified `auth-service` (confidence 0.80) - 8 tool calls, 4 LLM calls, 71K input tokens, 58 s

## INC-08 - saas platform
- **Brief:** Invoices have not been generated for a while and the billing queue keeps growing
- **True root cause:** `billing-service` - bad deploy: Release v3.0.0 of billing-service ran an online migration adding a NOT NULL column without a default and started publishing v3 events; the legacy v2 invoice-worker cannot deserialise them or insert rows, so messages pile up in the dead-letter queue and invoices stall about 45 minutes later.
- **Expected outcome:** identifiable
- **Misleading evidence:** analytics-ingest config_change: analytics-ingest configuration updated *(red herring change)*
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - identified `billing-service` (confidence 0.65) - 31 tool calls, 32 LLM calls, 341K input tokens, 278 s
- **Single agent:** wrong - identified `invoice-worker` (confidence 0.70) - 4 tool calls, 3 LLM calls, 38K input tokens, 39 s

## INC-09 - search platform
- **Brief:** search success rate dropped to 80%
- **True root cause:** `recommendation-engine` - resource exhaustion: recommendation-engine hit its memory limit (2Gi) under load; containers were OOM-killed and restarted, failing in-flight requests.
- **Expected outcome:** identifiable
- **Misleading evidence:** none
- **Noise:** noisy warnings - **Tool failures:** query_logs (transient timeout), query_metrics (partial )
- **Team:** right - identified `recommendation-engine` (confidence 0.40) - 35 tool calls, 37 LLM calls, 391K input tokens, 379 s
- **Single agent:** right - identified `recommendation-engine` (confidence 0.80) - 8 tool calls, 4 LLM calls, 67K input tokens, 57 s

## INC-10 - saas platform
- **Brief:** About 80% of analytics events are being dropped
- **True root cause:** `analytics-ingest` - resource exhaustion: The local disk of analytics-ingest reached its IOPS limit; the write-ahead buffer filled and the service started dropping about 80% of incoming events.
- **Expected outcome:** identifiable
- **Misleading evidence:** none
- **Noise:** noisy warnings - **Tool failures:** query_logs (persistent error)
- **Team:** right - inconclusive, top guess `analytics-ingest` (confidence 0.25) - 36 tool calls, 36 LLM calls, 369K input tokens, 393 s
- **Single agent:** right - identified `analytics-ingest` (confidence 0.60) - 23 tool calls, 11 LLM calls, 100K input tokens, 115 s

## INC-11 - saas platform
- **Brief:** media-transcoder crashed and restarted; transcoding jobs failed during the crash
- **True root cause:** `media-transcoder` - bad deploy: The GPU driver upgrade 550.54 -> 555.42 on media-transcoder faults under the transcoding load; the process crashed and auto-restarted.
- **Expected outcome:** inconclusive - The container's logs were wiped when it restarted and the crash-time GPU metrics were never scraped; a GPU driver upgrade and a base OS image update landed in the same minute, and nothing left distinguishes them.
- **Misleading evidence:** media-transcoder infra: media-transcoder base OS image update *(red herring change)*
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - inconclusive, top guess `media-transcoder` (confidence 0.25) - 32 tool calls, 29 LLM calls, 301K input tokens, 394 s
- **Single agent:** right - inconclusive, top guess `media-transcoder` (confidence 0.30) - 30 tool calls, 14 LLM calls, 131K input tokens, 157 s

## INC-12 - online retail
- **Brief:** Order fulfilment is failing and fulfillment-service pods keep restarting; order success rate at 87%
- **True root cause:** `fulfillment-service` - dependency failure: shipping-carrier slowed from about 100ms to 12s; fulfillment-service calls it with no client timeout, so worker threads blocked, the thread pool ran out, health checks failed and Kubernetes restarted the pods in a loop.
- **Expected outcome:** identifiable
- **Misleading evidence:** fulfillment-service scaling: fulfillment-service scaled from 3 to 5 replicas *(red herring change)*
- **Noise:** noisy warnings - **Tool failures:** none
- **Team:** right - identified `fulfillment-service` (confidence 0.60) - 34 tool calls, 36 LLM calls, 449K input tokens, 425 s
- **Single agent:** wrong - identified `fulfillment-service` (confidence 0.75) - 10 tool calls, 6 LLM calls, 88K input tokens, 84 s
