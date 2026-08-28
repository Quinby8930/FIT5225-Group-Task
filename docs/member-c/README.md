# Member C - Alibaba Cloud ML Inference

This folder documents Member C's contribution to the Pacific BioArchive
multi-cloud system. Member C owns the Alibaba Cloud inference service that is
called by Member B's AWS media pipeline after an image or video frame is
uploaded to S3.

## Scope

Member C implemented the secondary-cloud machine-learning endpoint. The service
accepts the stable request format agreed with Member B, downloads the temporary
image URL supplied by the AWS side, runs the detector/classifier pipeline, and
returns predicted tags, detections, and a model version. The service is hosted
on Alibaba Cloud Function Compute as a custom container.

Member C does not own the frontend, Cognito login, S3 upload flow, video frame
extraction, metadata database, or search/delete APIs. Those parts are handled
by the other team members through the agreed HTTP contracts.

## Deployment Summary

- Cloud provider: Alibaba Cloud
- Service: Function Compute
- Function name: `pacific-bioarchive-ml`
- Runtime: custom container
- Region: `ap-southeast-1`
- Public base URL: `https://pacificchive-ml-chidpnuwue.ap-southeast-1.fcapp.run`
- Health check: `GET /health`
- Readiness check: `GET /ready`
- Inference endpoint: `POST /infer`

The inference endpoint is protected by the shared internal API key header:

```http
X-Internal-Api-Key: <shared team secret>
```

The secret value is not stored in this repository.

## Request Contract

```http
POST /infer
Content-Type: application/json
X-Internal-Api-Key: <shared team secret>
```

```json
{
  "file_id": "inference-test-001",
  "media_type": "image",
  "image_urls": [
    "https://temporary-s3-presigned-image-url"
  ]
}
```

For video input, Member B sends one or more temporary frame URLs in
`image_urls`. Member C treats all URLs as short-lived sources and does not
store the original media.

## Response Contract

```json
{
  "tags": {
    "Alectura_lathami": 1
  },
  "detections": [
    {
      "species": "Alectura_lathami",
      "confidence": 0.999983
    }
  ],
  "model_version": "v1"
}
```

This response format is what Member B consumes before passing the result to
Member D's metadata service.

## Evidence Files

Screenshots are stored separately under `evidence/screenshots/`. They are not
embedded in this Markdown file so the evidence can be reused in the final team
report.

- `01_function_base_config.png`: Function Compute basic configuration
- `02_http_trigger.png`: HTTP trigger enabled for the deployed function
- `03_public_access_url.png`: public HTTPS endpoint shown in Alibaba Cloud
- `04_environment_variables_redacted.png`: runtime environment configuration,
  with the internal API key redacted
- `05_health_ready_terminal.png`: successful `/health` and `/ready` curl checks
- `06_infer_terminal.png`: successful `/infer` test using a valid S3 presigned
  image URL
- `07_github_commit.png`: GitHub commit history showing Member C commits merged
  into the project

Do not add API keys, tokens, full presigned URLs, or account secrets to this
folder.
