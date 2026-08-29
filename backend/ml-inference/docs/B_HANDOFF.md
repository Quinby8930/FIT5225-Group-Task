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

1. The B media service will provide short-lived AWS S3 HTTPS URLs in
   `image_urls`; C rejects other hosts and redirects by default.
2. B will extract video frames at one frame per second and send consecutive
   batches of at most 30 presigned URLs.
3. B sends `file_id`, `media_type` (`image` or `video`), and `image_urls`
   exactly as specified in `docs/API_CONTRACT.md`.
4. B will store `tags`, `detections`, and `model_version` through D's metadata
   API.
5. A and C will configure the same internal secret in AWS and Alibaba Cloud.
   It is required in production, configuration-only, and must not be
   committed or posted in group chat.

C returns `503` if its server key is absent and `401` if B omits or supplies
the wrong key. Unauthenticated local testing requires the explicit
`ALLOW_UNAUTHENTICATED_INFERENCE=true` switch; its default and production value
are `false`. Health and readiness routes remain public.

## Shared limits and timeouts

- Image uploads and C source downloads are capped at 12,582,912 bytes. Video
  uploads keep B's separate 262,144,000-byte cap.
- C also caps each decoded source at 40,000,000 pixels before pixel load or
  conversion. MegaDetector with no animal crop returns no species.
- C accepts at most 900 source URLs, while B sends at most 30 per video batch.
  Neither limit guarantees that a 900-frame video fits the end-to-end deadline.
  C returns at most 1,000 detections per request; B enforces the same 1,000 cap
  globally across the video, also caps the global tag-count sum at 1,000, and
  requires one exact model version.
- C's application deadline is 45 seconds, its Function Compute timeout is 60
  seconds, and B's inference HTTP timeout is 70 seconds. C also caps each
  source socket timeout to the remaining application deadline.
- C returns HTTP `422` with `detection_limit_exceeded` instead of a partial
  response when prediction 1,001 would be added, and returns HTTP `504` when
  the application deadline is crossed.
- C returns `503`/`504` for temporary source download failures so the B media
  service retries them; invalid/disallowed source input remains `422`.
- B treats C `401` as non-retryable `INFERENCE_AUTH_FAILED`, other C 4xx
  responses as non-retryable `INFERENCE_REJECTED`, and C 5xx/504 or transport
  failures as retryable `INFERENCE_UNAVAILABLE`.

The local service and real supplied models have already been tested. The
remaining deployment work is for C to publish the updated Alibaba image and
for A to configure its HTTPS endpoint, matching secret, signed-URL lifetime,
and request timeout in AWS.

## Integration smoke test

```bash
curl -sS -X POST "$INFERENCE_API_URL/infer" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  --data @samples/infer-image.json
```

Expected result: HTTP `200` and a JSON object containing `tags`,
`detections`, and `model_version`.
