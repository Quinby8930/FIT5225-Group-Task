import os

from media_pipeline.errors import MediaPipelineError
from media_pipeline.events import parse_s3_record
from media_pipeline.http_clients import InferenceClient, MetadataClient
from media_pipeline.pipeline import MediaPipeline
from media_pipeline.storage import S3Storage


def create_handler(dependencies):
    pipeline = dependencies["pipeline"]
    media_bucket_name = dependencies.get("media_bucket_name")

    def invoke(event, context):
        records = event.get("Records") if isinstance(event, dict) else None
        if not isinstance(records, list):
            raise MediaPipelineError("INVALID_S3_EVENT", "Invalid S3 media event")

        results = []
        for raw_record in records:
            if media_bucket_name:
                parsed = parse_s3_record(raw_record)
                if parsed.bucket != media_bucket_name:
                    raise MediaPipelineError(
                        "INVALID_S3_EVENT", "S3 event bucket does not match configuration"
                    )
            results.append(pipeline.process_record(raw_record))
        return {"results": results}

    return invoke


def _build_dependencies():
    import boto3

    internal_api_key = os.environ.get("INTERNAL_API_KEY")
    storage = S3Storage(boto3.client("s3"))
    metadata = MetadataClient(
        os.environ["METADATA_API_BASE_URL"],
        internal_api_key=internal_api_key,
    )
    inference = InferenceClient(
        os.environ["INFERENCE_API_URL"],
        internal_api_key=internal_api_key,
    )
    pipeline = MediaPipeline(
        storage=storage,
        metadata=metadata,
        inference=inference,
        ffmpeg_path=os.environ.get("FFMPEG_PATH", "/opt/bin/ffmpeg"),
    )
    return {
        "pipeline": pipeline,
        "media_bucket_name": os.environ.get("MEDIA_BUCKET_NAME"),
    }


_runtime_handler = None


def handler(event, context):
    global _runtime_handler
    if _runtime_handler is None:
        _runtime_handler = create_handler(_build_dependencies())
    return _runtime_handler(event, context)
