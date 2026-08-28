"""Contract tests for archive media-reference normalization."""

import pytest

from app.services.media_reference_service import (
    MediaReferenceError,
    normalize_media_reference,
)


BUCKET = "private-media"


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("originals/u1/f1.jpg", "originals/u1/f1.jpg"),
        ("thumbnails/u1/f1.jpg", "thumbnails/u1/f1.jpg"),
        (
            "https://private-media.s3.ap-southeast-2.amazonaws.com/"
            "thumbnails/u1/f1%20photo.jpg?X-Amz-Signature=abc",
            "thumbnails/u1/f1 photo.jpg",
        ),
        (
            "https://s3.ap-southeast-2.amazonaws.com/private-media/"
            "originals/u1/f1.mp4?X-Amz-Signature=abc",
            "originals/u1/f1.mp4",
        ),
        (
            "https://private-media.s3.ap-southeast-2.amazonaws.com/"
            "originals/u1/a%2520b.jpg?X-Amz-Signature=abc",
            "originals/u1/a%20b.jpg",
        ),
        (
            "https://private-media.s3.ap-southeast-2.amazonaws.com/"
            "originals/u1/a%25name.jpg?X-Amz-Signature=abc",
            "originals/u1/a%name.jpg",
        ),
    ],
)
def test_normalize_media_reference_returns_canonical_archive_key(reference, expected):
    assert normalize_media_reference(reference, BUCKET) == expected


@pytest.mark.parametrize(
    "reference",
    [
        "http://private-media.s3.ap-southeast-2.amazonaws.com/originals/u1/f1.jpg",
        "https://attacker:secret@private-media.s3.ap-southeast-2.amazonaws.com/originals/u1/f1.jpg",
        "https://private-media.s3.ap-southeast-2.amazonaws.com/originals/u1/f1.jpg#fragment",
        "https://wrong-bucket.s3.ap-southeast-2.amazonaws.com/originals/u1/f1.jpg",
        "https://private-media.s3.ap-southeast-2.amazonaws.com:bad/originals/u1/f1.jpg",
        "https://private-media.s3.ap-southeast-2.amazonaws.com/not-archive/f1.jpg",
        "https://private-media.s3.ap-southeast-2.amazonaws.com//originals/u1/f1.jpg",
        "https://s3.ap-southeast-2.amazonaws.com//private-media/originals/u1/f1.jpg",
        "originals/u1/../f1.jpg",
        r"originals\\u1\\f1.jpg",
        "originals/u1/%zz.jpg",
        "originals/" + "a" * 1020,
    ],
)
def test_normalize_media_reference_rejects_untrusted_or_malformed_references(reference):
    with pytest.raises(MediaReferenceError):
        normalize_media_reference(reference, BUCKET)
