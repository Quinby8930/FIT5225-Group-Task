import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import MediaPipelineError


DEFAULT_MAX_PIXELS = 40_000_000


def validate_decoded_pixel_count(width, height, max_pixels=DEFAULT_MAX_PIXELS):
    pixel_count = width * height
    if pixel_count > max_pixels:
        raise MediaPipelineError(
            "INVALID_MEDIA", "Decoded image exceeds the pixel limit"
        )
    return pixel_count


def create_thumbnail(
    source,
    target,
    max_size=(512, 512),
    quality=82,
    max_pixels=DEFAULT_MAX_PIXELS,
):
    source = Path(source)
    target = Path(target)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as opened:
                validate_decoded_pixel_count(*opened.size, max_pixels=max_pixels)
                opened.load()
                image = opened.copy()
    except (
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        raise MediaPipelineError("INVALID_MEDIA", "Image could not be decoded") from error

    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    if "A" in image.getbands() or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        image = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        image = image.convert("RGB")

    image.save(target, format="JPEG", quality=quality, optimize=True)
    return target
