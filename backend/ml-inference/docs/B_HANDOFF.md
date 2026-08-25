# Handoff to B

## What is complete

C owns a standalone HTTPS inference service with:

- `POST /infer` using B's `file_id`, `media_type`, and `image_urls` request;
- deterministic JSON response with `tags`, `detections`, and `model_version`;
- fail-closed `X-Internal-Api-Key` authentication for `/infer`;
- request size, URL count, file type, and remote download limits;
- case-insensitive normalization from scientific labels to team short names;
- one-source-at-a-time processing with a 1,000-detection response cap;
- health and readiness endpoints;
- model path and version configuration;
- Docker packaging and tests.

## What B must confirm before integration

1. B will provide short-lived HTTPS URLs in `image_urls`.
2. B will extract video frames at one frame per second.
3. B sends `file_id`, `media_type` (`image` or `video`), and `image_urls`
   exactly as specified in `docs/API_CONTRACT.md`.
4. B will store `tags`, `detections`, and `model_version` through D's metadata
   API.
5. B will agree on the internal secret delivery mechanism. The secret is
   required in production, configuration-only, and must not be committed.

C returns `503` if its server key is absent and `401` if B omits or supplies
the wrong key. Unauthenticated local testing requires the explicit
`ALLOW_UNAUTHENTICATED_INFERENCE=true` switch; its default and production value
are `false`. Health and readiness routes remain public.

## Shared limits and timeouts

- Image uploads and C source downloads are capped at 12,582,912 bytes. Video
  uploads keep B's separate 262,144,000-byte cap.
- C accepts at most 900 source URLs and returns at most 1,000 detections.
- C's application deadline is 45 seconds, its Function Compute timeout is 60
  seconds, and B's inference HTTP timeout is 70 seconds.
- C returns HTTP `422` with `detection_limit_exceeded` instead of a partial
  response when prediction 1,001 would be added, and returns HTTP `504` when
  the application deadline is crossed.
- B treats C `401` as non-retryable `INFERENCE_AUTH_FAILED`, other C 4xx
  responses as non-retryable `INFERENCE_REJECTED`, and C 5xx/504 or transport
  failures as retryable `INFERENCE_UNAVAILABLE`.

The local service and real supplied models have already been tested. The only
remaining C/B work is to replace the local endpoint with the deployed Alibaba
Cloud HTTPS URL and confirm the exact signed-URL lifetime and request timeout.

## Integration smoke test

```bash
curl -sS -X POST "$INFERENCE_API_URL/infer" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  --data @samples/infer-image.json
```

Expected result: HTTP `200` and a JSON object containing `tags`,
`detections`, and `model_version`.
