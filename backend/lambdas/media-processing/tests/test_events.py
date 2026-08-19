from dataclasses import FrozenInstanceError

import pytest

from media_pipeline.errors import MediaPipelineError
from media_pipeline.events import parse_s3_record


def make_record(
    key="originals/user-1/file-1/wombat.jpg",
    *,
    bucket="private-media",
    sequencer="0055AED6DCD90281E5",
):
    return {
        "s3": {
            "bucket": {"name": bucket},
            "object": {"key": key, "sequencer": sequencer},
        }
    }


def test_parse_s3_record_extracts_a_frozen_media_identity():
    parsed = parse_s3_record(make_record())

    assert parsed.bucket == "private-media"
    assert parsed.key == "originals/user-1/file-1/wombat.jpg"
    assert parsed.user_id == "user-1"
    assert parsed.file_id == "file-1"
    assert parsed.filename == "wombat.jpg"
    assert parsed.sequencer == "0055AED6DCD90281E5"
    with pytest.raises(FrozenInstanceError):
        parsed.file_id = "different-file"


def test_parse_s3_record_url_decodes_spaces_and_escaped_plus_signs():
    parsed = parse_s3_record(
        make_record("originals/user-1/file-1/little+wombat%2Bfriend.jpg")
    )

    assert parsed.key == "originals/user-1/file-1/little wombat+friend.jpg"
    assert parsed.filename == "little wombat+friend.jpg"


@pytest.mark.parametrize(
    "key",
    [
        "thumbnails/user-1/file-1/thumbnail.jpg",
        "originals/user-1/file-1",
        "originals//file-1/wombat.jpg",
        "originals/user-1//wombat.jpg",
        "originals/user-1/file-1/",
        "originals/user-1/file-1/folder/wombat.jpg",
        "originals/user-1/file-1/..",
        "originals/user-1/file-1/folder\\wombat.jpg",
    ],
)
def test_parse_s3_record_rejects_non_original_and_malformed_paths(key):
    with pytest.raises(MediaPipelineError) as caught:
        parse_s3_record(make_record(key))

    assert caught.value.code == "INVALID_S3_EVENT"


@pytest.mark.parametrize(
    "key",
    [
        "originals/user%00-1/file-1/wombat.jpg",
        "originals/user-1/file%00-1/wombat.jpg",
        "originals/user-1/file-1/wombat%00.jpg",
        "originals/user-1/file-1/wombat%1F.jpg",
        "originals/user-1/file-1/wombat%7F.jpg",
    ],
)
def test_parse_s3_record_rejects_decoded_c0_and_del_path_components(key):
    with pytest.raises(MediaPipelineError) as caught:
        parse_s3_record(make_record(key))

    assert caught.value.code == "INVALID_S3_EVENT"


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"s3": {"bucket": {}, "object": {"key": "originals/u/f/a.jpg", "sequencer": "1"}}},
        {"s3": {"bucket": {"name": "bucket"}, "object": {"sequencer": "1"}}},
        {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "originals/u/f/a.jpg"}}},
        {"s3": {"bucket": {"name": "bucket"}, "object": {"key": "originals/u/f/a.jpg", "sequencer": ""}}},
    ],
)
def test_parse_s3_record_rejects_missing_required_event_fields(record):
    with pytest.raises(MediaPipelineError) as caught:
        parse_s3_record(record)

    assert caught.value.code == "INVALID_S3_EVENT"
