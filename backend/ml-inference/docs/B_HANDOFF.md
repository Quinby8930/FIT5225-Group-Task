# Handoff to B

## What is complete

C owns a standalone HTTPS inference service with:

- `POST /infer` using B's `file_id`, `media_type`, and `image_urls` request;
- deterministic JSON response with `tags`, `detections`, and `model_version`;
- `X-Internal-Api-Key` authentication when `INTERNAL_API_KEY` is configured;
- request size, URL count, file type, and remote download limits;
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
   configuration-only and must not be committed.

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
