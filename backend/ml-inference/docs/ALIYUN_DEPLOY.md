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
docker build --platform linux/amd64 \
  -t registry.example.com/pacific-bioarchive/ml-inference:v1 .
docker push registry.example.com/pacific-bioarchive/ml-inference:v1
```

Replace the registry image in `s.yaml` with the team's Alibaba Container
Registry image. Keep the registry private.

## Configure the secret

Set `INTERNAL_API_KEY` in the deployment environment or Alibaba Cloud secret
manager. It must match the value configured in B's `INTERNAL_API_KEY`.
Never put the real value in `s.yaml`, `.env`, or a Git commit.

## Deploy

```bash
export INTERNAL_API_KEY='use-a-long-random-secret'
s deploy -t s.yaml
```

After deployment, obtain the HTTPS trigger URL from Function Compute and give
that URL plus the secret-delivery method to B. B must call `/infer` with the
`X-Internal-Api-Key` header, matching B's `INFERENCE_API_URL` and
`INTERNAL_API_KEY` configuration.

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
