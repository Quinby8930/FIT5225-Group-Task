# C Demo Checklist

1. Show `/health` and `/ready`.
2. Show a successful image inference and point out `tags`,
   `detections`, and `model_version`.
3. Show a video-frame request containing two or more frames.
4. Show an unauthenticated `/infer` request returning `401`.
5. Show an invalid source returning `422`.
6. Show a deliberately slow request or explain the configured timeout and
   expected `504` response.
7. Change `MODEL_VERSION` in configuration and explain that the caller API
   contract stays unchanged.
8. Explain that B owns S3 upload and one-frame-per-second extraction, while C
   owns the cross-cloud inference boundary.
