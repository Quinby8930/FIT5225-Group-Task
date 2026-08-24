# Member C Inference API Contract

## Purpose

The service is the secondary-cloud ML component for Pacific BioArchive. B's
media-processing Lambda calls it over HTTPS after an image is uploaded or
after video frames have been extracted at one frame per second. C applies
MegaDetector to each received image and then classifies detected animal crops
with the supplied fine-tuned model.

## Authentication

When `INTERNAL_API_KEY` is configured, production requests must include:

```text
X-Internal-Api-Key: <shared secret>
```

The secret is supplied through `INTERNAL_API_KEY` and must never be committed
to Git. If the environment variable is unset, B omits the header and the
endpoint accepts the request, which supports local contract testing.
`/health` and `/ready` are intentionally unauthenticated for deployment health
checks. `/infer` is protected when the key is configured.

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
map as the file's tag-count metadata.

## Status codes

| Status | Meaning |
| --- | --- |
| `200` | Inference completed |
| `400` | Invalid JSON or empty request |
| `401` | Missing or invalid `X-Internal-Api-Key` when a key is configured |
| `413` | Request exceeds configured size |
| `422` | Invalid fields, source URL, or image |
| `504` | Inference exceeds the configured timeout |
| `502` | Model or upstream inference failure |
