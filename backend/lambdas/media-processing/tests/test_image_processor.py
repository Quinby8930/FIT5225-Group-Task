from pathlib import Path

import pytest
from PIL import Image

from media_pipeline.errors import MediaPipelineError
from media_pipeline.image_processor import create_thumbnail


@pytest.mark.parametrize(
    ("source_size", "expected_size"),
    [
        ((1200, 600), (512, 256)),
        ((600, 1200), (256, 512)),
        ((120, 60), (120, 60)),
    ],
)
def test_thumbnail_preserves_aspect_ratio_without_enlarging(
    tmp_path, source_size, expected_size
):
    source = tmp_path / "source.png"
    target = tmp_path / "thumbnail.jpg"
    Image.new("RGB", source_size, "green").save(source)

    result_path = create_thumbnail(source, target)

    assert result_path == target
    with Image.open(target) as result:
        result.verify()
    with Image.open(target) as result:
        assert result.size == expected_size
        assert result.mode == "RGB"
        assert result.format == "JPEG"


def test_thumbnail_composites_transparency_onto_white(tmp_path):
    source = tmp_path / "transparent.png"
    target = tmp_path / "thumbnail.jpg"
    image = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    image.putpixel((4, 4), (0, 0, 0, 255))
    image.save(source)

    create_thumbnail(source, target)

    with Image.open(target) as result:
        assert result.mode == "RGB"
        assert all(channel >= 245 for channel in result.getpixel((0, 0)))


def test_thumbnail_maps_corrupt_input_to_invalid_media(tmp_path):
    source = tmp_path / "corrupt.jpg"
    target = tmp_path / "thumbnail.jpg"
    source.write_bytes(b"this is not an image")

    with pytest.raises(MediaPipelineError) as caught:
        create_thumbnail(source, target)

    assert caught.value.code == "INVALID_MEDIA"
    assert not Path(target).exists()
