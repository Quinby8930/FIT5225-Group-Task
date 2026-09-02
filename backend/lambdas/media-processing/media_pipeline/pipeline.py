from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import MediaPipelineError
from .events import parse_s3_record
from .image_processor import create_thumbnail as create_image_thumbnail
from .video_processor import (
    DEFAULT_FFMPEG_TIMEOUT_SECONDS,
    extract_frames as extract_video_frames,
)


IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime"}
FINALIZATION_RESERVE_MILLIS = 180_000
MAX_INFERENCE_BATCH_URLS = 30
MAX_AGGREGATE_DETECTIONS = 1000
MAX_AGGREGATE_TAG_COUNT = 1000
UNEXPECTED_PROCESSING_FAILURE_CODE = "PROCESSING_FAILED"
UNEXPECTED_PROCESSING_FAILURE_MESSAGE = "Media processing failed unexpectedly"


def _time_budget_error():
    return MediaPipelineError(
        "PROCESSING_TIME_BUDGET_EXHAUSTED",
        "Insufficient Lambda time remains to process this video safely",
        retryable=True,
    )


def _remaining_time(get_remaining_time_in_millis):
    if get_remaining_time_in_millis is None:
        return None
    return get_remaining_time_in_millis()


def _require_finalization_reserve(get_remaining_time_in_millis):
    remaining = _remaining_time(get_remaining_time_in_millis)
    if remaining is not None and remaining <= FINALIZATION_RESERVE_MILLIS:
        raise _time_budget_error()
    return remaining


class MediaPipeline:
    def __init__(
        self,
        *,
        storage,
        metadata,
        inference,
        create_thumbnail=create_image_thumbnail,
        extract_frames=None,
        ffmpeg_path="/opt/bin/ffmpeg",
    ):
        self.storage = storage
        self.metadata = metadata
        self.inference = inference
        self.create_thumbnail = create_thumbnail
        self.extract_frames = extract_frames or (
            lambda source, output_dir, *, timeout_seconds: extract_video_frames(
                source,
                output_dir,
                ffmpeg_path=ffmpeg_path,
                timeout_seconds=timeout_seconds,
            )
        )

    def process_record(self, raw_record, *, get_remaining_time_in_millis=None):
        record = parse_s3_record(raw_record)
        lease_payload = {
            "user_id": record.user_id,
            "object_key": record.key,
            "sequencer": record.sequencer,
        }
        lease_token = self._processing_lease_token(
            self.metadata.begin_processing(record.file_id, lease_payload)
        )
        if lease_token is None:
            return {"status": "skipped", "file_id": record.file_id}

        thumbnail_key = None
        thumbnail_uploaded = False
        completion_attempted = False
        try:
            content_type = self.storage.get_content_type(record.bucket, record.key)
            media_type = self._media_type(content_type)
            if media_type == "video":
                _require_finalization_reserve(get_remaining_time_in_millis)
            with TemporaryDirectory() as temporary_directory:
                temporary_root = Path(temporary_directory)
                local_source = temporary_root / record.filename
                self.storage.download(record.bucket, record.key, local_source)
                if media_type == "image":
                    local_thumbnail = temporary_root / "thumbnail.jpg"
                    self.create_thumbnail(local_source, local_thumbnail)
                    thumbnail_key = (
                        f"thumbnails/{record.user_id}/{record.file_id}/thumbnail.jpg"
                    )
                    self.storage.upload(
                        record.bucket,
                        thumbnail_key,
                        local_thumbnail,
                        "image/jpeg",
                    )
                    thumbnail_uploaded = True
                    original_url = self.storage.presign_get(
                        record.bucket, record.key
                    )
                    inference_result = self.inference.infer(
                        {
                            "file_id": record.file_id,
                            "media_type": "image",
                            "image_urls": [original_url],
                        }
                    )
                else:
                    thumbnail_key, inference_result = self._process_video(
                        record,
                        local_source,
                        temporary_root,
                        get_remaining_time_in_millis,
                    )

                completion = {
                    "user_id": record.user_id,
                    "file_type": media_type,
                    "original_key": record.key,
                    "thumbnail_key": thumbnail_key,
                    "tags": inference_result["tags"],
                    "detections": inference_result["detections"],
                    "model_version": inference_result["model_version"],
                    "status": "completed",
                }
                if lease_token:
                    completion["lease_token"] = lease_token
                completion_attempted = True
                self.metadata.complete(record.file_id, completion)
            return {"status": "completed", "file_id": record.file_id}
        except Exception as error:
            thumbnail_cleanup_error = None
            if thumbnail_uploaded and not completion_attempted:
                try:
                    self.storage.delete(record.bucket, [thumbnail_key])
                except Exception as cleanup_error:
                    thumbnail_cleanup_error = cleanup_error
            if isinstance(error, MediaPipelineError):
                error_code = error.code
                message = error.message[:240]
                retryable = error.retryable
            else:
                error_code = UNEXPECTED_PROCESSING_FAILURE_CODE
                message = UNEXPECTED_PROCESSING_FAILURE_MESSAGE
                retryable = True
            failure_recorded = False
            try:
                failure = {
                    "user_id": record.user_id,
                    "error_code": error_code,
                    "message": message,
                    "status": "failed",
                }
                if lease_token:
                    failure["lease_token"] = lease_token
                self.metadata.fail(record.file_id, failure)
                failure_recorded = True
            except Exception:
                pass
            if (
                thumbnail_cleanup_error is not None
                and failure_recorded
                and not retryable
            ):
                if (
                    isinstance(thumbnail_cleanup_error, MediaPipelineError)
                    and thumbnail_cleanup_error.retryable
                ):
                    raise thumbnail_cleanup_error from error
                raise MediaPipelineError(
                    "STORAGE_DELETE_FAILED",
                    "Thumbnail cleanup failed",
                    retryable=True,
                ) from thumbnail_cleanup_error
            if (
                isinstance(error, MediaPipelineError)
                and not retryable
                and failure_recorded
            ):
                return {
                    "status": "failed",
                    "file_id": record.file_id,
                    "error_code": error_code,
                }
            raise

    @staticmethod
    def _processing_lease_token(lease_response):
        if type(lease_response) is bool:
            return "" if lease_response else None
        if not isinstance(lease_response, dict):
            raise MediaPipelineError(
                "DEPENDENCY_UNAVAILABLE",
                "Metadata response did not match its contract",
                retryable=True,
            )

        should_process = lease_response.get("should_process")
        state = lease_response.get("state")
        if type(should_process) is not bool:
            raise MediaPipelineError(
                "DEPENDENCY_UNAVAILABLE",
                "Metadata response did not match its contract",
                retryable=True,
            )
        if state is None:
            return "" if should_process else None
        if state == "completed" and not should_process:
            return None
        if state == "lease_active" and not should_process:
            raise MediaPipelineError(
                "PROCESSING_LEASE_ACTIVE",
                "Processing lease is active",
                retryable=True,
            )
        if state == "acquired" and should_process:
            lease_token = lease_response.get("lease_token")
            if isinstance(lease_token, str) and 32 <= len(lease_token) <= 256:
                return lease_token
            raise MediaPipelineError(
                "DEPENDENCY_UNAVAILABLE",
                "Metadata response did not match its contract",
                retryable=True,
            )
        raise MediaPipelineError(
            "DEPENDENCY_UNAVAILABLE",
            "Metadata response did not match its contract",
            retryable=True,
        )

    @staticmethod
    def _media_type(content_type):
        if content_type in IMAGE_CONTENT_TYPES:
            return "image"
        if content_type in VIDEO_CONTENT_TYPES:
            return "video"
        raise MediaPipelineError("INVALID_MEDIA", "Unsupported media content type")

    def _process_video(
        self,
        record,
        local_source,
        temporary_root,
        get_remaining_time_in_millis,
    ):
        uploaded_keys = []
        primary_error = None
        try:
            remaining = _require_finalization_reserve(get_remaining_time_in_millis)
            extraction_timeout = DEFAULT_FFMPEG_TIMEOUT_SECONDS
            if remaining is not None:
                extraction_timeout = min(
                    DEFAULT_FFMPEG_TIMEOUT_SECONDS,
                    (remaining - FINALIZATION_RESERVE_MILLIS) / 1000,
                )
            frames = self.extract_frames(
                local_source,
                temporary_root / "frames",
                timeout_seconds=extraction_timeout,
            )
            for frame in frames:
                _require_finalization_reserve(get_remaining_time_in_millis)
                frame_key = (
                    f"processing/{record.user_id}/{record.file_id}/frames/{frame.name}"
                )
                self.storage.upload(record.bucket, frame_key, frame, "image/jpeg")
                uploaded_keys.append(frame_key)

            tags = Counter()
            detections = []
            model_version = None
            for offset in range(0, len(uploaded_keys), MAX_INFERENCE_BATCH_URLS):
                _require_finalization_reserve(get_remaining_time_in_millis)
                batch_keys = uploaded_keys[offset : offset + MAX_INFERENCE_BATCH_URLS]
                batch_urls = [
                    self.storage.presign_get(record.bucket, frame_key)
                    for frame_key in batch_keys
                ]
                batch_result = self.inference.infer(
                    {
                        "file_id": record.file_id,
                        "media_type": "video",
                        "image_urls": batch_urls,
                    }
                )
                batch_version = batch_result["model_version"]
                if model_version is None:
                    model_version = batch_version
                elif batch_version != model_version:
                    raise MediaPipelineError(
                        "INFERENCE_UNAVAILABLE",
                        "Inference model version changed between video batches",
                        retryable=True,
                    )
                tags.update(batch_result["tags"])
                if sum(tags.values()) > MAX_AGGREGATE_TAG_COUNT:
                    raise MediaPipelineError(
                        "INFERENCE_FAILED",
                        "Inference response exceeded the aggregate tag limit",
                    )
                detections.extend(batch_result["detections"])
                if len(detections) > MAX_AGGREGATE_DETECTIONS:
                    raise MediaPipelineError(
                        "INFERENCE_FAILED",
                        "Inference response exceeded the aggregate detection limit",
                    )
            return None, {
                "tags": {
                    species: 1
                    for species, count in sorted(tags.items())
                    if count > 0
                },
                "detections": detections,
                "model_version": model_version,
            }
        except Exception as error:
            primary_error = error
            raise
        finally:
            if uploaded_keys:
                try:
                    self.storage.delete(record.bucket, uploaded_keys)
                except Exception:
                    if primary_error is None:
                        raise
