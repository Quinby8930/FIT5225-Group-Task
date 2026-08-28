# Alibaba Cloud Deployment

This service is packaged as a custom-container Function Compute function. The
runtime listens on port `9000`, which is the port configured in the container
image and `s.yaml`.

## Build the image

The supplied PyTorch model is large and should be downloaded from a private
OSS location or copied into the build context by the deployment operator. Do
not commit it to Git.

```bash
./scripts/prepare_assets.sh "$HOME/Downloads/PacificBioArchive.zip"
export ML_IMAGE='<your-private-acr-registry>/pacific-bioarchive/ml-inference:v1'
docker build --platform linux/amd64 \
  -t "$ML_IMAGE" .
docker push "$ML_IMAGE"
```

`s.yaml` reads `ML_IMAGE` from the deployment environment, so the real
Alibaba Container Registry path stays out of Git. Keep the registry private.

## Configure the secret

Set `INTERNAL_API_KEY` in the deployment environment or Alibaba Cloud secret
manager. It must match the value that A configures in the AWS media service.
Never put the real value in `s.yaml`, `.env`, or a Git commit. Keep
`ALLOW_UNAUTHENTICATED_INFERENCE=false` in `s.yaml`; without a configured key,
production `/infer` requests fail closed with `503`.

By default, the service downloads only HTTPS URLs hosted by standard AWS S3
endpoints. If A deploys the media bucket behind a different trusted hostname,
C must set `ALLOWED_SOURCE_HOSTS` to a comma-separated allowlist before
deployment. Do not add arbitrary public hosts or IP addresses.

## Deploy

```bash
export ML_IMAGE='<your-private-acr-registry>/pacific-bioarchive/ml-inference:v1'
export INTERNAL_API_KEY='use-a-long-random-secret'
s deploy -t s.yaml
```

After deployment, C gives the HTTPS trigger URL to A. A configures the AWS
media service to call `/infer` with the `X-Internal-Api-Key` header and the
same `INTERNAL_API_KEY`; the secret is never committed to Git or posted in a
group chat.

## Verify

```bash
curl -sS "$INFERENCE_API_URL/health"
curl -sS "$INFERENCE_API_URL/ready"
curl -sS -X POST "$INFERENCE_API_URL/infer" \
  -H 'Content-Type: application/json' \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  --data @samples/infer-image.json
```

The custom container must be built for the deployment architecture, and the
Function Compute HTTP trigger must expose the container's port `9000`.
