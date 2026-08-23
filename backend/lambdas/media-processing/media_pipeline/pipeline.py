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
REPORTABLE_FAILURES = {
    "INVALID_MEDIA",
    "FRAME_EXTRACTION_FAILED",
    "INFERENCE_FAILED",
    "PROCESSING_TIME_BUDGET_EXHAUSTED",
}
FINALIZATION_RESERVE_MILLIS = 180_000


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
        if not self.metadata.begin_processing(record.file_id, lease_payload):
            return {"status": "skipped", "file_id": record.file_id}

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
                    thumbnail_key, inference_result = self._process_image(
                        record, local_source, temporary_root
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
                self.metadata.complete(record.file_id, completion)
            return {"status": "completed", "file_id": record.file_id}
        except MediaPipelineError as error:
            if error.code in REPORTABLE_FAILURES:
                self.metadata.fail(
                    record.file_id,
                    {
                        "user_id": record.user_id,
                        "error_code": error.code,
                        "message": error.message[:240],
                        "status": "failed",
                    },
                )
            raise

    @staticmethod
    def _media_type(content_type):
        if content_type in IMAGE_CONTENT_TYPES:
            return "image"
        if content_type in VIDEO_CONTENT_TYPES:
            return "video"
        raise MediaPipelineError("INVALID_MEDIA", "Unsupported media content type")

    def _process_image(self, record, local_source, temporary_root):
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
        original_url = self.storage.presign_get(record.bucket, record.key)
        result = self.inference.infer(
            {
                "file_id": record.file_id,
                "media_type": "image",
                "image_urls": [original_url],
            }
        )
        return thumbnail_key, result

    def _process_video(
        self,
        record,
        local_source,
        temporary_root,
        get_remaining_time_in_millis,
    ):
        uploaded_keys = []
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
            frame_urls = []
            for frame in frames:
                _require_finalization_reserve(get_remaining_time_in_millis)
                frame_key = (
                    f"processing/{record.user_id}/{record.file_id}/frames/{frame.name}"
                )
                self.storage.upload(record.bucket, frame_key, frame, "image/jpeg")
                uploaded_keys.append(frame_key)
                frame_urls.append(self.storage.presign_get(record.bucket, frame_key))
            _require_finalization_reserve(get_remaining_time_in_millis)
            result = self.inference.infer(
                {
                    "file_id": record.file_id,
                    "media_type": "video",
                    "image_urls": frame_urls,
                }
            )
            return None, result
        finally:
            if uploaded_keys:
                self.storage.delete(record.bucket, uploaded_keys)
