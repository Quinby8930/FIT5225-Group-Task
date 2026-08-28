# Pacific BioArchive ML Inference Service

This directory is the independent deliverable for member C: the Alibaba Cloud
serverless ML inference component for FIT5225 Assignment 2.

## Scope

The service provides:

- `POST /infer` using B's `file_id`, `media_type`, and `image_urls` contract;
- `tags`, `detections`, and model version;
- `X-Internal-Api-Key` authentication for the internal cross-cloud call;
- request/file/URL limits, validation, timeouts, structured errors, and logs;
- HTTPS-only, S3-host allowlisted source downloads with redirects disabled;
- `/health` and `/ready` deployment probes;
- configurable model path/version so model upgrades do not require caller code changes;
- MegaDetector animal detection followed by fine-tuned SpeciesNet classification;
- Docker packaging, tests, API contract, handoff notes, and demo checklist.

The AWS media member owns S3 upload and video frame extraction. The database
member owns persistence and query APIs. This service stops at the stable
cross-cloud inference contract.

## Local setup

The repository intentionally does not contain the supplied 492MB model files.
Copy `model.pt` and `mdv5a.pt` into `models/` from `PacificBioArchive.zip` for
production inference. The `config/labels.txt` file is included.

For contract and API development without PyTorch:

```bash
cd backend/ml-inference
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
INFERENCE_BACKEND=mock INTERNAL_API_KEY=local-demo-secret PORT=8080 ./scripts/run_local.sh
```

Then use a separate terminal:

```bash
curl -sS http://127.0.0.1:8080/health
```

For real model inference, install `requirements.txt` and then
`requirements-model-runtime.txt`, place the model files in `models/`, and run
with `INFERENCE_BACKEND=speciesnet`.

The runtime requirements explicitly pin `onnx` and `onnx2torch` because the
supplied `model.pt` pickle imports `onnx2torch` during `torch.load`.

## Production packaging

Build and run the container after copying the model files into `models/`:

```bash
docker build -t pacific-bioarchive-ml:dev .
docker run --rm -p 9000:9000 \
  --env-file .env \
  -v "$PWD/models:/app/models:ro" \
  pacific-bioarchive-ml:dev
```

For Alibaba Cloud Function Compute, use the same Python 3.12 application
contract or deploy the container image. Store model files in private OSS,
inject `MODEL_PATH`/`MODEL_VERSION` through the function environment, and put
`INTERNAL_API_KEY` in the platform secret store. Set `ML_IMAGE` to C's private
Alibaba Container Registry image before running `s deploy`; do not edit a real
registry path into `s.yaml`.

## Verification

```bash
pytest -q
```

Read `docs/API_CONTRACT.md` before integrating with B. The remaining
integration work is C's deployment of the Alibaba endpoint and A's
configuration of the AWS media service `INFERENCE_API_URL` and matching
`INTERNAL_API_KEY`.

## AI acknowledgement

Generative AI was used to assist with the initial service scaffolding,
validation, tests, and documentation. The team member must review, understand,
test, and modify the generated code as necessary, and acknowledge this use in
the final team and individual reports according to the assignment instructions.
