from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import MediaPipelineError
from .events import parse_s3_record
from .image_processor import create_thumbnail as create_image_thumbnail
from .video_processor import extract_frames as extract_video_frames


IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime"}
REPORTABLE_FAILURES = {
    "INVALID_MEDIA",
    "FRAME_EXTRACTION_FAILED",
    "INFERENCE_FAILED",
}


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
            lambda source, output_dir: extract_video_frames(
                source, output_dir, ffmpeg_path=ffmpeg_path
            )
        )

    def process_record(self, raw_record):
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
                        record, local_source, temporary_root
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

    def _process_video(self, record, local_source, temporary_root):
        uploaded_keys = []
        try:
            frames = self.extract_frames(local_source, temporary_root / "frames")
            frame_urls = []
            for frame in frames:
                frame_key = (
                    f"processing/{record.user_id}/{record.file_id}/frames/{frame.name}"
                )
                self.storage.upload(record.bucket, frame_key, frame, "image/jpeg")
                uploaded_keys.append(frame_key)
                frame_urls.append(self.storage.presign_get(record.bucket, frame_key))
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
