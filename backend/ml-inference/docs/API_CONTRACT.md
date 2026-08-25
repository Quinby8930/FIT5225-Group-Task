# Member C Inference API Contract

## Purpose

The service is the secondary-cloud ML component for Pacific BioArchive. B's
media-processing Lambda calls it over HTTPS after an image is uploaded or
after video frames have been extracted at one frame per second. C applies
MegaDetector to each received image and then classifies detected animal crops
with the supplied fine-tuned model.

## Authentication

Production deployments must configure a non-empty `INTERNAL_API_KEY`, and
requests must include:

```text
X-Internal-Api-Key: <shared secret>
```

The secret must never be committed to Git. `/infer` fails closed with `503`
when the server key is absent and with `401` when a configured key is missing
or does not match; configured keys are compared in constant time. Explicit
unauthenticated local contract testing is available only by setting
`ALLOW_UNAUTHENTICATED_INFERENCE=true`, whose default and production value are
`false`. `/health` and `/ready` remain intentionally unauthenticated for
deployment health checks.

## `POST /infer`

### Image request

```json
{
  "file_id": "11111111-2222-4333-8444-555555555555",
  "media_type": "image",
  "image_urls": [
    "https://temporary-original-url.example/image.jpg"
  ]
}
```

`image_urls` contains exactly one short-lived GET URL for an image.

### Video request

```json
{
  "file_id": "11111111-2222-4333-8444-555555555555",
  "media_type": "video",
  "image_urls": [
    "https://temporary-frame-url-1.example/frame.jpg",
    "https://temporary-frame-url-2.example/frame.jpg"
  ]
}
```

B owns video extraction. `image_urls` contains the one-frame-per-second
temporary images in lexical order, as required by the assignment.

### Successful response

```json
{
  "tags": {
    "dingo": 2
  },
  "detections": [
    {
      "species": "dingo",
      "confidence": 0.934211
    }
  ],
  "model_version": "speciesnet-v1"
}
```

`tags` is an object containing aggregate predicted species counts.
`detections` is a list of species/confidence objects. D can persist the tags
map as the file's tag-count metadata. Species values on the wire are the
team's short names: C maps the scientific genus/species label from columns 5
and 6 of `labels.txt` to the final word in column 7, case-insensitively (for
example `Canis_familiaris` and `Canis_dingo` both become `dingo`). Labels not
present in the file pass through unchanged.

## Runtime limits

- The JSON request body is at most 26,214,400 bytes.
- Each downloaded source image is at most 12,582,912 bytes.
- A request contains at most 900 source URLs and C fetches, decodes, predicts,
  and closes one source before starting the next.
- A response contains at most 1,000 detections; C rejects the whole inference
  instead of returning a partial result.
- C's application deadline is 45 seconds, the Function Compute timeout is 60
  seconds, and B's caller timeout is 70 seconds. This ordering lets C return a
  bounded error before either platform terminates the request.

## Status codes

| Status | Meaning |
| --- | --- |
| `200` | Inference completed |
| `400` | Invalid JSON or empty request |
| `401` | Missing or invalid `X-Internal-Api-Key` when a key is configured |
| `413` | Request exceeds configured size |
| `422` | Invalid fields/source/image, or `detection_limit_exceeded` when the result would contain more than 1,000 detections |
| `504` | Inference exceeds the configured timeout |
| `502` | Model or upstream inference failure |
| `503` | Server `INTERNAL_API_KEY` is not configured |
