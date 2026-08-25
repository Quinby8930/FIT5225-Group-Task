import sys

import pytest

import handler as handler_module
from media_pipeline.errors import MediaPipelineError


def record(bucket, key):
    return {
        "s3": {
            "bucket": {"name": bucket},
            "object": {"key": key, "sequencer": "abc123"},
        }
    }


class RecordingPipeline:
    def __init__(self):
        self.records = []
        self.remaining_time_providers = []

    def process_record(self, item, *, get_remaining_time_in_millis=None):
        self.records.append(item)
        self.remaining_time_providers.append(get_remaining_time_in_millis)
        return {"status": "completed", "file_id": item["s3"]["object"]["key"]}


def test_create_handler_processes_every_record_in_order():
    pipeline = RecordingPipeline()
    first = record("private-media", "originals/user-1/file-1/one.jpg")
    second = record("private-media", "originals/user-1/file-2/two.jpg")
    invoke = handler_module.create_handler(
        {"pipeline": pipeline, "media_bucket_name": "private-media"}
    )

    result = invoke({"Records": [first, second]}, None)

    assert pipeline.records == [first, second]
    assert result == {
        "results": [
            {"status": "completed", "file_id": "originals/user-1/file-1/one.jpg"},
            {"status": "completed", "file_id": "originals/user-1/file-2/two.jpg"},
        ]
    }
    assert pipeline.remaining_time_providers == [None, None]


def test_create_handler_passes_one_shared_lambda_time_provider_to_every_record():
    pipeline = RecordingPipeline()
    first = record("private-media", "originals/user-1/file-1/one.jpg")
    second = record("private-media", "originals/user-1/file-2/two.jpg")

    class Context:
        def get_remaining_time_in_millis(self):
            return 900_000

    context = Context()
    invoke = handler_module.create_handler(
        {"pipeline": pipeline, "media_bucket_name": "private-media"}
    )

    invoke({"Records": [first, second]}, context)

    assert len(pipeline.remaining_time_providers) == 2
    assert pipeline.remaining_time_providers[0] is pipeline.remaining_time_providers[1]
    assert pipeline.remaining_time_providers[0]() == 900_000


def test_create_handler_rejects_a_record_from_a_different_configured_bucket():
    pipeline = RecordingPipeline()
    invoke = handler_module.create_handler(
        {"pipeline": pipeline, "media_bucket_name": "private-media"}
    )

    with pytest.raises(MediaPipelineError) as caught:
        invoke(
            {"Records": [record("someone-elses-bucket", "originals/u/f/a.jpg")]},
            None,
        )

    assert caught.value.code == "INVALID_S3_EVENT"
    assert pipeline.records == []


def test_exported_handler_builds_runtime_dependencies_only_once(monkeypatch):
    builds = []
    pipeline = RecordingPipeline()

    def build_dependencies():
        builds.append("build")
        return {"pipeline": pipeline, "media_bucket_name": "private-media"}

    monkeypatch.setattr(handler_module, "_runtime_handler", None)
    monkeypatch.setattr(handler_module, "_build_dependencies", build_dependencies)
    event = {"Records": [record("private-media", "originals/u/f/a.jpg")]}

    handler_module.handler(event, None)
    handler_module.handler(event, None)

    assert builds == ["build"]
    assert pipeline.records == [event["Records"][0], event["Records"][0]]


def test_runtime_dependencies_use_all_environment_contracts_and_default_ffmpeg(
    monkeypatch,
):
    created = {}
    s3_client = object()

    class FakeBoto3:
        @staticmethod
        def client(service):
            assert service == "s3"
            return s3_client

    class FakeStorage:
        def __init__(self, client):
            created["storage"] = client

    class FakeMetadata:
        def __init__(self, url, *, internal_api_key):
            created["metadata"] = (url, internal_api_key)

    class FakeInference:
        def __init__(self, url, *, internal_api_key, timeout):
            created["inference"] = (url, internal_api_key, timeout)

    class FakePipeline:
        def __init__(self, **kwargs):
            created["pipeline"] = kwargs

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3)
    monkeypatch.setattr(handler_module, "S3Storage", FakeStorage)
    monkeypatch.setattr(handler_module, "MetadataClient", FakeMetadata)
    monkeypatch.setattr(handler_module, "InferenceClient", FakeInference)
    monkeypatch.setattr(handler_module, "MediaPipeline", FakePipeline)
    monkeypatch.setenv("MEDIA_BUCKET_NAME", "private-media")
    monkeypatch.setenv("METADATA_API_BASE_URL", "https://metadata.example")
    monkeypatch.setenv("INFERENCE_API_URL", "https://inference.example")
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-key")
    monkeypatch.setenv("INFERENCE_HTTP_TIMEOUT_SECONDS", "70")
    monkeypatch.delenv("FFMPEG_PATH", raising=False)

    dependencies = handler_module._build_dependencies()

    assert dependencies["media_bucket_name"] == "private-media"
    assert created["storage"] is s3_client
    assert created["metadata"] == ("https://metadata.example", "internal-key")
    assert created["inference"] == (
        "https://inference.example",
        "internal-key",
        70,
    )
    assert created["pipeline"]["ffmpeg_path"] == "/opt/bin/ffmpeg"
