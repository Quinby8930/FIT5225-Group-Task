# C Demo Checklist

1. Show `/health` and `/ready`.
2. Show a successful image inference and point out `tags`,
   `detections`, and `model_version`.
3. Show a video-frame request containing two or more frames.
4. Show an unauthenticated `/infer` request returning `401`.
5. Show a non-HTTPS or non-S3 source returning `422` without being fetched.
6. Explain that an expired S3 URL or temporary network failure returns a
   retryable `503`, while a download timeout returns `504`.
7. Show a deliberately slow inference or explain the configured timeout and
   expected `504` response.
8. Change `MODEL_VERSION` in configuration and explain that the caller API
   contract stays unchanged.
9. Explain that B owns S3 upload and one-frame-per-second extraction, while C
   owns the cross-cloud inference boundary.
