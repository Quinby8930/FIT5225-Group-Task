# Member B Media Ingestion Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and locally verify Member B's protected pre-signed upload,
S3-triggered media preprocessing, service-adapter, storage-deletion, and AWS
infrastructure boundaries without requiring live cloud credentials.

**Architecture:** A Node.js upload Lambda validates authenticated requests,
reserves per-user checksums through Member D's HTTP contract, and returns a
checksum-bound S3 PUT URL. A Python processing Lambda consumes `originals/`
events, creates thumbnails or one-frame-per-second video samples, calls Member
C and D through HTTP adapters, and cleans temporary objects; an internal Node.js
Lambda provides guarded S3 deletion.

**Tech Stack:** Node.js 20 ESM with `node:test`, AWS SDK for JavaScript v3,
Python 3.12 with Pillow and pytest, FFmpeg process adapter, AWS SAM/CloudFormation.

**Spec:** `docs/superpowers/specs/2026-08-19-member-b-media-pipeline-design.md`

## Global Constraints

- Do not push any branch or commit.
- Produce no more than five local commits for the entire change.
- Keep all buckets and media objects private.
- Use Cognito `claims.sub` as `user_id`.
- Accept only JPEG, PNG, WebP, MP4, and QuickTime uploads.
- Validate a Base64-encoded 32-byte SHA-256 digest before reservation.
- Default pre-signed PUT expiry is 300 seconds.
- Generate thumbnails within 512 x 512 pixels at JPEG quality 82.
- Extract video at exactly one frame per second.
- Trigger processing only for the `originals/` prefix.
- Do not commit model weights, archives, credentials, tokens, or endpoint secrets.
- Keep Member C inference and Member D storage choices behind HTTP contracts.

---

### Task 1: Protected Upload and Duplicate Reservation

**Files:**
- Create: `backend/lambdas/upload/package.json`
- Create: `backend/lambdas/upload/index.mjs`
- Create: `backend/lambdas/upload/service.mjs`
- Create: `backend/lambdas/upload/validation.mjs`
- Create: `backend/lambdas/upload/metadata-client.mjs`
- Create: `backend/lambdas/upload/presigner.mjs`
- Create: `backend/lambdas/upload/test/validation.test.mjs`
- Create: `backend/lambdas/upload/test/service.test.mjs`
- Create: `backend/lambdas/upload/test/handler.test.mjs`
- Create: `backend/lambdas/upload/test/metadata-client.test.mjs`

**Interfaces:**
- Consumes: API Gateway v2 event with
  `event.requestContext.authorizer.jwt.claims.sub` and the upload request body.
- Produces: `createUploadService(dependencies).createUpload({userId, request})`
  and Lambda `handler(event)` returning the response contract in the spec.

- [ ] **Step 1: Write validation tests**

```javascript
test("accepts a JPEG request with a Base64 SHA-256 checksum", () => {
  const value = validateUploadRequest({
    filename: "wombat.jpg",
    content_type: "image/jpeg",
    size_bytes: 42,
    checksum_sha256: Buffer.alloc(32, 7).toString("base64"),
  });
  assert.equal(value.fileType, "image");
  assert.equal(value.filename, "wombat.jpg");
});

test("rejects a digest that is not exactly 32 decoded bytes", () => {
  assert.throws(
    () => validateUploadRequest({
      filename: "wombat.jpg",
      content_type: "image/jpeg",
      size_bytes: 42,
      checksum_sha256: Buffer.alloc(31).toString("base64"),
    }),
    (error) => error.code === "INVALID_CHECKSUM",
  );
});
```

- [ ] **Step 2: Run the validation tests and verify RED**

Run:

```powershell
node --test backend/lambdas/upload/test/validation.test.mjs
```

Expected: FAIL because `validation.mjs` does not exist.

- [ ] **Step 3: Implement request validation and safe filename handling**

```javascript
export function validateUploadRequest(input, maxBytes = 262_144_000) {
  if (!input || typeof input !== "object") {
    throw new UploadError("INVALID_REQUEST", "A JSON object is required", 400);
  }
  const media = MEDIA_TYPES[input.content_type];
  if (!media) {
    throw new UploadError("UNSUPPORTED_FILE_TYPE", "Unsupported media type", 400);
  }
  const digest = Buffer.from(input.checksum_sha256 || "", "base64");
  if (digest.length !== 32 || digest.toString("base64") !== input.checksum_sha256) {
    throw new UploadError("INVALID_CHECKSUM", "A Base64 SHA-256 checksum is required", 400);
  }
  return {
    filename: sanitizeFilename(input.filename),
    contentType: input.content_type,
    fileType: media.fileType,
    sizeBytes: validateSize(input.size_bytes, maxBytes),
    checksum: input.checksum_sha256,
  };
}
```

- [ ] **Step 4: Run validation tests and verify GREEN**

Run the Step 2 command. Expected: all validation tests pass.

- [ ] **Step 5: Write service tests for reservation, duplicate handling, and pre-signing**

```javascript
test("reserves before creating a pre-signed URL", async () => {
  const calls = [];
  const service = createUploadService({
    createFileId: () => "file-1",
    reserveUpload: async (record) => calls.push(["reserve", record]),
    presignUpload: async (record) => {
      calls.push(["presign", record]);
      return "https://upload.example";
    },
  });
  const result = await service.createUpload({userId: "user-1", request: validRequest});
  assert.deepEqual(calls.map(([name]) => name), ["reserve", "presign"]);
  assert.equal(result.object_key, "originals/user-1/file-1/wombat.jpg");
});
```

- [ ] **Step 6: Run service tests and verify RED**

Run:

```powershell
node --test backend/lambdas/upload/test/service.test.mjs
```

Expected: FAIL because `createUploadService` is not implemented.

- [ ] **Step 7: Implement the upload service, HTTP metadata adapter, AWS presigner, and Lambda handler**

The service calls `reserveUpload(record)` before `presignUpload(record)`. The
metadata adapter maps HTTP `409` to `DUPLICATE_FILE`. The presigner signs
`PutObjectCommand` with `Bucket`, `Key`, `ContentType`, and `ChecksumSHA256`.
The handler parses API Gateway JSON, reads `claims.sub`, and maps known errors
to their status codes without logging secrets or URLs.

- [ ] **Step 8: Run all upload tests and verify GREEN**

Run:

```powershell
node --test backend/lambdas/upload/test/*.test.mjs
```

Expected: every upload test passes with zero warnings.

- [ ] **Step 9: Commit Task 1**

```powershell
git add backend/lambdas/upload
git commit -m "feat: add protected media upload presigning"
```

---

### Task 2: Media Processing and Guarded Storage Deletion

**Files:**
- Create: `backend/lambdas/media-processing/handler.py`
- Create: `backend/lambdas/media-processing/requirements.txt`
- Create: `backend/lambdas/media-processing/media_pipeline/__init__.py`
- Create: `backend/lambdas/media-processing/media_pipeline/errors.py`
- Create: `backend/lambdas/media-processing/media_pipeline/events.py`
- Create: `backend/lambdas/media-processing/media_pipeline/image_processor.py`
- Create: `backend/lambdas/media-processing/media_pipeline/video_processor.py`
- Create: `backend/lambdas/media-processing/media_pipeline/http_clients.py`
- Create: `backend/lambdas/media-processing/media_pipeline/storage.py`
- Create: `backend/lambdas/media-processing/media_pipeline/pipeline.py`
- Create: `backend/lambdas/media-processing/tests/conftest.py`
- Create: `backend/lambdas/media-processing/tests/test_events.py`
- Create: `backend/lambdas/media-processing/tests/test_image_processor.py`
- Create: `backend/lambdas/media-processing/tests/test_video_processor.py`
- Create: `backend/lambdas/media-processing/tests/test_pipeline.py`
- Create: `backend/lambdas/storage-delete/package.json`
- Create: `backend/lambdas/storage-delete/index.mjs`
- Create: `backend/lambdas/storage-delete/service.mjs`
- Create: `backend/lambdas/storage-delete/test/service.test.mjs`

**Interfaces:**
- Consumes: S3 event records for
  `originals/{user_id}/{file_id}/{filename}` and injected storage, inference,
  metadata, image, and video adapters.
- Produces: `MediaPipeline.process_record(record) -> dict`, thumbnail and
  processing objects, metadata completion/failure calls, and storage deletion
  Lambda responses.

- [ ] **Step 1: Write S3 event and image thumbnail tests**

```python
def test_parse_original_key_extracts_identity():
    parsed = parse_original_key("originals/user-1/file-1/wombat.jpg")
    assert parsed.user_id == "user-1"
    assert parsed.file_id == "file-1"
    assert parsed.filename == "wombat.jpg"

def test_thumbnail_preserves_aspect_ratio(tmp_path):
    source = tmp_path / "source.png"
    target = tmp_path / "thumbnail.jpg"
    Image.new("RGB", (1200, 600), "green").save(source)
    create_thumbnail(source, target)
    with Image.open(target) as result:
        assert result.size == (512, 256)
        assert result.format == "JPEG"
```

- [ ] **Step 2: Run event/image tests and verify RED**

Run:

```powershell
python -m pytest backend/lambdas/media-processing/tests/test_events.py backend/lambdas/media-processing/tests/test_image_processor.py -q
```

Expected: collection fails because `media_pipeline` modules do not exist.

- [ ] **Step 3: Implement S3 parsing and thumbnail generation**

Use `urllib.parse.unquote_plus` for event keys. Reject non-`originals/` keys
and paths that do not contain user, file, and filename components. Use Pillow
`thumbnail((512, 512))`, convert transparency against white to RGB, and save an
optimized JPEG at quality 82.

- [ ] **Step 4: Run event/image tests and verify GREEN**

Run the Step 2 command. Expected: all event/image tests pass.

- [ ] **Step 5: Write video adapter and pipeline orchestration tests**

```python
def test_ffmpeg_command_extracts_one_frame_per_second(tmp_path):
    command = build_ffmpeg_command(
        ffmpeg_path="ffmpeg",
        input_path=tmp_path / "input.mp4",
        output_pattern=tmp_path / "frame-%06d.jpg",
    )
    assert command[command.index("-vf") + 1] == "fps=1"

def test_duplicate_event_stops_before_download(fakes, s3_record):
    fakes.metadata.should_process = False
    result = fakes.pipeline.process_record(s3_record)
    assert result["status"] == "skipped"
    assert fakes.storage.downloads == []

def test_video_frames_are_deleted_after_inference(fakes, video_record):
    result = fakes.pipeline.process_record(video_record)
    assert result["status"] == "completed"
    assert fakes.storage.deleted_keys == fakes.storage.uploaded_frame_keys
```

- [ ] **Step 6: Run video/pipeline tests and verify RED**

Run:

```powershell
python -m pytest backend/lambdas/media-processing/tests/test_video_processor.py backend/lambdas/media-processing/tests/test_pipeline.py -q
```

Expected: FAIL because the video adapter and pipeline are absent.

- [ ] **Step 7: Implement video, HTTP, storage, pipeline, and Lambda adapters**

`build_ffmpeg_command` must include `-vf fps=1`. `extract_frames` runs with
`check=True`, verifies at least one frame, and raises
`FRAME_EXTRACTION_FAILED` otherwise. `MediaPipeline` acquires the metadata
lease before downloading, uses deterministic S3 keys, calls inference once,
records completion, records bounded failure diagnostics, and deletes uploaded
frame keys in `finally`. `handler.py` constructs boto3/HTTP adapters from
environment variables and processes every S3 record.

- [ ] **Step 8: Write and implement guarded storage deletion with a RED/GREEN cycle**

```javascript
test("rejects deletion outside the authenticated user prefixes", async () => {
  const service = createStorageDeleteService({deleteKeys: async () => {}});
  await assert.rejects(
    service.deleteForUser("user-1", ["originals/user-2/file/image.jpg"]),
    (error) => error.code === "FORBIDDEN_KEY",
  );
});
```

Run the storage-delete test before creating `service.mjs`; verify the missing
module failure, implement prefix validation and 1,000-key batching, then rerun
until green.

- [ ] **Step 9: Run all Task 2 tests and verify GREEN**

```powershell
python -m pytest backend/lambdas/media-processing/tests -q
node --test backend/lambdas/storage-delete/test/*.test.mjs
```

Expected: all media-processing and storage-delete tests pass.

- [ ] **Step 10: Commit Task 2**

```powershell
git add backend/lambdas/media-processing backend/lambdas/storage-delete
git commit -m "feat: add event-driven media preprocessing"
```

---

### Task 3: AWS Infrastructure, Contracts, and Full Verification

**Files:**
- Create: `infrastructure/member-b/template.yaml`
- Create: `infrastructure/member-b/README.md`
- Create: `infrastructure/member-b/test_template.py`
- Create: `docs/member-b/api-contracts.md`
- Create: `docs/member-b/local-testing.md`
- Create: `docs/member-b/manual-aws-steps.md`
- Create: `tests/events/s3-image-upload.json`
- Create: `tests/events/s3-video-upload.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: existing HTTP API ID `2dd2aqb32j`, a deployment-time JWT authorizer
  ID, Member C/D endpoint parameters, and an FFmpeg Lambda layer ARN.
- Produces: a private media bucket, upload/processing/storage-delete functions,
  the protected `POST /upload-url` route integration, prefix-filtered S3 event,
  least-privilege IAM policies, and documented deployment outputs.

- [ ] **Step 1: Write infrastructure structural tests**

```python
def test_media_bucket_blocks_public_access(template):
    block = template["Resources"]["MediaBucket"]["Properties"]["PublicAccessBlockConfiguration"]
    assert all(block.values())

def test_s3_event_is_filtered_to_originals(template):
    event = template["Resources"]["MediaProcessingFunction"]["Properties"]["Events"]["OriginalUpload"]
    rules = event["Properties"]["Filter"]["S3Key"]["Rules"]
    assert {"Name": "prefix", "Value": "originals/"} in rules
```

- [ ] **Step 2: Run infrastructure tests and verify RED**

Run:

```powershell
python -m pytest infrastructure/member-b/test_template.py -q
```

Expected: FAIL because `template.yaml` is absent.

- [ ] **Step 3: Implement the SAM template and deployment README**

Define the S3 bucket with public access blocked, CORS for PUT/GET/HEAD, and a
recovery lifecycle rule for `processing/`. Define Node.js 20 upload and delete
functions and a Python 3.12 processing function. Give upload only
`s3:PutObject` on `originals/*`; give processing only required get/put/delete
prefix actions; give storage-delete only `s3:DeleteObject`. Integrate the
upload Lambda with the existing API through `AWS::ApiGatewayV2::Integration`,
`Route`, and `Permission` resources using API and authorizer parameters.

- [ ] **Step 4: Add exact API, local-test, manual AWS, and sample-event documents**

Document every environment variable and command. State explicitly that an
FFmpeg layer providing `/opt/bin/ffmpeg`, live Cognito testing, endpoint values,
and deployment confirmation require manual account access. Sample events use
non-secret example bucket names and URL-encoded object keys.

- [ ] **Step 5: Run infrastructure and complete repository verification**

```powershell
python -m pytest infrastructure/member-b/test_template.py backend/lambdas/media-processing/tests -q
node --test backend/lambdas/upload/test/*.test.mjs backend/lambdas/storage-delete/test/*.test.mjs
node frontend/node_modules/vite/bin/vite.js build --config frontend/vite.config.js
git diff --check
git status --short
```

If no `frontend/vite.config.js` exists, run Vite from the `frontend` working
directory with `node node_modules/vite/bin/vite.js build`.

- [ ] **Step 6: Commit Task 3**

```powershell
git add README.md infrastructure/member-b docs/member-b tests/events
git commit -m "docs: add media pipeline infrastructure and handoff"
```

- [ ] **Step 7: Audit the spec, plan, diff, and commit count**

Confirm every scoped requirement maps to code or an explicitly manual cloud
step. Confirm no credential-like values or model weights are tracked. Confirm
the branch is ahead by no more than four commits; use a fifth commit only for
review fixes that cannot be folded into the preceding commit without rewriting
published history. Do not push.

