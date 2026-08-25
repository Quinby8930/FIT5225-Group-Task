from pathlib import Path

import pytest

from media_pipeline.errors import MediaPipelineError
from media_pipeline.pipeline import MediaPipeline
from media_pipeline.storage import S3Storage


class RecordingS3Client:
    def __init__(self):
        self.calls = []

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        return {"ContentType": "image/webp"}

    def download_file(self, bucket, key, destination):
        self.calls.append(("download_file", (bucket, key, destination)))

    def upload_file(self, source, bucket, key, ExtraArgs):
        self.calls.append(("upload_file", (source, bucket, key, ExtraArgs)))

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        self.calls.append(("generate_presigned_url", (operation, Params, ExpiresIn)))
        return f"https://signed.example/{Params['Key']}"

    def delete_objects(self, **kwargs):
        self.calls.append(("delete_objects", kwargs))


def test_s3_storage_preserves_content_types_and_uses_900_second_signed_gets(tmp_path):
    client = RecordingS3Client()
    storage = S3Storage(client)
    destination = tmp_path / "download.webp"
    source = tmp_path / "thumbnail.jpg"
    source.write_bytes(b"jpeg")

    content_type = storage.get_content_type("private-media", "originals/u/f/a.webp")
    storage.download("private-media", "originals/u/f/a.webp", destination)
    storage.upload(
        "private-media",
        "thumbnails/u/f/thumbnail.jpg",
        source,
        "image/jpeg",
    )
    signed_url = storage.presign_get("private-media", "originals/u/f/a.webp")

    assert content_type == "image/webp"
    assert signed_url == "https://signed.example/originals/u/f/a.webp"
    assert client.calls == [
        (
            "head_object",
            {"Bucket": "private-media", "Key": "originals/u/f/a.webp"},
        ),
        (
            "download_file",
            ("private-media", "originals/u/f/a.webp", str(destination)),
        ),
        (
            "upload_file",
            (
                str(source),
                "private-media",
                "thumbnails/u/f/thumbnail.jpg",
                {"ContentType": "image/jpeg"},
            ),
        ),
        (
            "generate_presigned_url",
            (
                "get_object",
                {"Bucket": "private-media", "Key": "originals/u/f/a.webp"},
                900,
            ),
        ),
    ]


def test_s3_storage_deletes_in_service_limit_batches():
    client = RecordingS3Client()
    storage = S3Storage(client)
    keys = [f"processing/u/f/frames/frame-{number:06}.jpg" for number in range(1001)]

    storage.delete("private-media", keys)

    delete_calls = [arguments for name, arguments in client.calls if name == "delete_objects"]
    assert [len(call["Delete"]["Objects"]) for call in delete_calls] == [1000, 1]
    assert delete_calls[0]["Bucket"] == "private-media"
    assert delete_calls[0]["Delete"]["Objects"][0] == {"Key": keys[0]}
    assert delete_calls[1]["Delete"]["Objects"][0] == {"Key": keys[-1]}


def test_s3_storage_rejects_http_success_with_per_object_delete_errors():
    class MixedResultClient:
        def delete_objects(self, **kwargs):
            return {
                "Deleted": [{"Key": "processing/u/f/frames/frame-000001.jpg"}],
                "Errors": [
                    {
                        "Key": "processing/u/f/frames/private-frame.jpg",
                        "Code": "AccessDenied",
                        "Message": "sensitive AWS error body",
                    }
                ],
            }

    storage = S3Storage(MixedResultClient())

    with pytest.raises(MediaPipelineError) as caught:
        storage.delete(
            "private-media",
            [
                "processing/u/f/frames/frame-000001.jpg",
                "processing/u/f/frames/private-frame.jpg",
            ],
        )

    assert caught.value.code == "STORAGE_DELETE_FAILED"
    assert caught.value.retryable is True
    assert "private-frame.jpg" not in caught.value.message
    assert "AccessDenied" not in caught.value.message
    assert "sensitive AWS error body" not in caught.value.message


def s3_record(filename="wombat.jpg"):
    return {
        "s3": {
            "bucket": {"name": "private-media"},
            "object": {
                "key": f"originals/user-1/file-1/{filename}",
                "sequencer": "abc123",
            },
        }
    }


class FakeMetadata:
    def __init__(self, order, *, should_process=True):
        self.order = order
        self.should_process = should_process
        self.completed = []
        self.failed = []
        self.fail_error = None

    def begin_processing(self, file_id, payload):
        self.order.append("begin")
        self.begin = (file_id, payload)
        return self.should_process

    def complete(self, file_id, payload):
        self.order.append("complete")
        self.completed.append((file_id, payload))

    def fail(self, file_id, payload):
        self.order.append("fail")
        self.failed.append((file_id, payload))
        if self.fail_error:
            raise self.fail_error


class FakeStorage:
    def __init__(self, order, *, content_type="image/jpeg"):
        self.order = order
        self.content_type = content_type
        self.downloads = []
        self.uploads = []
        self.deleted_keys = []
        self.local_paths = []

    def get_content_type(self, bucket, key):
        self.order.append("content_type")
        return self.content_type

    def download(self, bucket, key, destination):
        self.order.append("download")
        self.downloads.append((bucket, key))
        self.local_paths.append(Path(destination))
        Path(destination).write_bytes(b"source media")

    def upload(self, bucket, key, source, content_type):
        self.order.append("upload")
        source = Path(source)
        assert source.exists()
        self.local_paths.append(source)
        self.uploads.append((bucket, key, content_type, source.read_bytes()))

    def presign_get(self, bucket, key):
        self.order.append("presign")
        return f"https://signed.example/{key}"

    def delete(self, bucket, keys):
        self.order.append("delete")
        self.deleted_keys.extend(keys)


class FakeInference:
    def __init__(self, order):
        self.order = order
        self.calls = []
        self.error = None

    def infer(self, payload):
        self.order.append("infer")
        self.calls.append(payload)
        if self.error:
            raise self.error
        return {
            "tags": {"wombat": 2},
            "detections": [{"species": "wombat", "confidence": 0.94}],
            "model_version": "speciesnet-v1",
        }


def fake_thumbnail(source, target):
    Path(target).write_bytes(b"thumbnail")


def fake_frames(source, output_dir, *, timeout_seconds=None):
    output_dir = Path(output_dir)
    output_dir.mkdir()
    first = output_dir / "frame-000001.jpg"
    second = output_dir / "frame-000002.jpg"
    first.write_bytes(b"frame one")
    second.write_bytes(b"frame two")
    return [first, second]


def make_pipeline(*, content_type="image/jpeg", should_process=True):
    order = []
    storage = FakeStorage(order, content_type=content_type)
    metadata = FakeMetadata(order, should_process=should_process)
    inference = FakeInference(order)
    pipeline = MediaPipeline(
        storage=storage,
        metadata=metadata,
        inference=inference,
        create_thumbnail=fake_thumbnail,
        extract_frames=fake_frames,
    )
    return pipeline, storage, metadata, inference, order


def test_duplicate_event_stops_before_any_s3_access():
    pipeline, storage, metadata, inference, order = make_pipeline(
        should_process=False
    )

    result = pipeline.process_record(s3_record())

    assert result == {"status": "skipped", "file_id": "file-1"}
    assert order == ["begin"]
    assert storage.downloads == []
    assert inference.calls == []
    assert metadata.begin == (
        "file-1",
        {
            "user_id": "user-1",
            "object_key": "originals/user-1/file-1/wombat.jpg",
            "sequencer": "abc123",
        },
    )


def test_image_pipeline_stores_deterministic_thumbnail_and_serializes_completion():
    pipeline, storage, metadata, inference, order = make_pipeline()

    result = pipeline.process_record(s3_record())

    assert result == {"status": "completed", "file_id": "file-1"}
    assert order[0] == "begin"
    assert storage.downloads == [
        ("private-media", "originals/user-1/file-1/wombat.jpg")
    ]
    assert storage.uploads == [
        (
            "private-media",
            "thumbnails/user-1/file-1/thumbnail.jpg",
            "image/jpeg",
            b"thumbnail",
        )
    ]
    assert inference.calls == [
        {
            "file_id": "file-1",
            "media_type": "image",
            "image_urls": [
                "https://signed.example/originals/user-1/file-1/wombat.jpg"
            ],
        }
    ]
    assert metadata.completed == [
        (
            "file-1",
            {
                "user_id": "user-1",
                "file_type": "image",
                "original_key": "originals/user-1/file-1/wombat.jpg",
                "thumbnail_key": "thumbnails/user-1/file-1/thumbnail.jpg",
                "tags": {"wombat": 2},
                "detections": [{"species": "wombat", "confidence": 0.94}],
                "model_version": "speciesnet-v1",
                "status": "completed",
            },
        )
    ]
    assert all(not path.exists() for path in storage.local_paths)


def test_video_pipeline_uploads_signed_frames_once_and_cleans_them_after_success():
    pipeline, storage, metadata, inference, _ = make_pipeline(content_type="video/mp4")

    result = pipeline.process_record(s3_record("wombat.mp4"))

    frame_keys = [
        "processing/user-1/file-1/frames/frame-000001.jpg",
        "processing/user-1/file-1/frames/frame-000002.jpg",
    ]
    assert result == {"status": "completed", "file_id": "file-1"}
    assert [upload[1] for upload in storage.uploads] == frame_keys
    assert all(upload[2] == "image/jpeg" for upload in storage.uploads)
    assert inference.calls == [
        {
            "file_id": "file-1",
            "media_type": "video",
            "image_urls": [f"https://signed.example/{key}" for key in frame_keys],
        }
    ]
    assert metadata.completed[0][1]["thumbnail_key"] is None
    assert storage.deleted_keys == frame_keys
    assert all(not path.exists() for path in storage.local_paths)


def test_video_pipeline_acknowledges_bounded_inference_failure_and_cleans_frames():
    pipeline, storage, metadata, inference, order = make_pipeline(
        content_type="video/quicktime"
    )
    inference.error = MediaPipelineError(
        "INFERENCE_FAILED", "x" * 1000
    )

    result = pipeline.process_record(s3_record("wombat.mov"))

    assert result == {
        "status": "failed",
        "file_id": "file-1",
        "error_code": "INFERENCE_FAILED",
    }
    assert metadata.failed[0][0] == "file-1"
    failure = metadata.failed[0][1]
    assert failure["user_id"] == "user-1"
    assert failure["error_code"] == "INFERENCE_FAILED"
    assert failure["status"] == "failed"
    assert len(failure["message"]) <= 240
    assert storage.deleted_keys == [
        "processing/user-1/file-1/frames/frame-000001.jpg",
        "processing/user-1/file-1/frames/frame-000002.jpg",
    ]
    assert order.index("delete") < order.index("fail")


@pytest.mark.parametrize("error_code", ["INFERENCE_AUTH_FAILED", "INFERENCE_REJECTED"])
def test_terminal_inference_categories_mark_failed_and_do_not_retry(error_code):
    pipeline, _, metadata, inference, order = make_pipeline()
    inference.error = MediaPipelineError(
        error_code, "terminal inference rejection", retryable=False
    )

    result = pipeline.process_record(s3_record())

    assert result == {
        "status": "failed",
        "file_id": "file-1",
        "error_code": error_code,
    }
    assert metadata.failed == [
        (
            "file-1",
            {
                "user_id": "user-1",
                "error_code": error_code,
                "message": "terminal inference rejection",
                "status": "failed",
            },
        )
    ]
    assert order[-1] == "fail"


def test_retryable_inference_unavailable_clears_lease_before_raising_for_retry():
    pipeline, _, metadata, inference, order = make_pipeline()
    inference.error = MediaPipelineError(
        "INFERENCE_UNAVAILABLE", "temporary inference outage", retryable=True
    )

    with pytest.raises(MediaPipelineError) as caught:
        pipeline.process_record(s3_record())

    assert caught.value.code == "INFERENCE_UNAVAILABLE"
    assert caught.value.retryable is True
    assert metadata.failed[0][1]["error_code"] == "INFERENCE_UNAVAILABLE"
    assert order[-1] == "fail"


def test_video_pipeline_reserves_finalization_time_from_ffmpeg_timeout():
    pipeline, _, _, _, _ = make_pipeline(content_type="video/mp4")
    observed_timeouts = []

    def recording_frames(source, output_dir, *, timeout_seconds):
        observed_timeouts.append(timeout_seconds)
        return fake_frames(source, output_dir, timeout_seconds=timeout_seconds)

    pipeline.extract_frames = recording_frames

    pipeline.process_record(
        s3_record("wombat.mp4"),
        get_remaining_time_in_millis=lambda: 700_000,
    )

    assert observed_timeouts == [520.0]


def test_video_pipeline_stops_between_frame_uploads_and_cleans_uploaded_frames():
    pipeline, storage, metadata, inference, _ = make_pipeline(content_type="video/mp4")
    remaining = {"milliseconds": 900_000}
    real_upload = storage.upload

    def upload_then_reduce_budget(bucket, key, source, content_type):
        real_upload(bucket, key, source, content_type)
        remaining["milliseconds"] = 179_999

    storage.upload = upload_then_reduce_budget

    with pytest.raises(MediaPipelineError) as caught:
        pipeline.process_record(
            s3_record("wombat.mp4"),
            get_remaining_time_in_millis=lambda: remaining["milliseconds"],
        )

    first_frame_key = "processing/user-1/file-1/frames/frame-000001.jpg"
    assert caught.value.code == "PROCESSING_TIME_BUDGET_EXHAUSTED"
    assert caught.value.retryable is True
    assert [upload[1] for upload in storage.uploads] == [first_frame_key]
    assert storage.deleted_keys == [first_frame_key]
    assert inference.calls == []
    assert metadata.completed == []
    assert metadata.failed[0][1]["error_code"] == "PROCESSING_TIME_BUDGET_EXHAUSTED"
    assert storage.order.index("delete") < storage.order.index("fail")


def test_video_pipeline_does_not_download_when_only_finalization_reserve_remains():
    pipeline, storage, metadata, inference, _ = make_pipeline(content_type="video/mp4")

    with pytest.raises(MediaPipelineError) as caught:
        pipeline.process_record(
            s3_record("wombat.mp4"),
            get_remaining_time_in_millis=lambda: 180_000,
        )

    assert caught.value.code == "PROCESSING_TIME_BUDGET_EXHAUSTED"
    assert caught.value.retryable is True
    assert storage.downloads == []
    assert storage.uploads == []
    assert inference.calls == []
    assert metadata.completed == []
    assert metadata.failed[0][1]["error_code"] == "PROCESSING_TIME_BUDGET_EXHAUSTED"


def test_unsupported_content_type_records_invalid_media_without_downloading():
    pipeline, storage, metadata, _, _ = make_pipeline(content_type="text/plain")

    result = pipeline.process_record(s3_record("notes.txt"))

    assert result == {
        "status": "failed",
        "file_id": "file-1",
        "error_code": "INVALID_MEDIA",
    }
    assert storage.downloads == []
    assert metadata.failed[0][1]["error_code"] == "INVALID_MEDIA"


def test_metadata_failure_while_recording_media_error_is_not_swallowed():
    pipeline, _, metadata, inference, _ = make_pipeline()
    inference.error = MediaPipelineError("INFERENCE_FAILED", "inference unavailable")
    metadata.fail_error = MediaPipelineError(
        "DEPENDENCY_UNAVAILABLE", "metadata unavailable", retryable=True
    )

    with pytest.raises(MediaPipelineError) as caught:
        pipeline.process_record(s3_record())

    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
