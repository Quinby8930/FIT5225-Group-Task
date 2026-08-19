from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import MediaPipelineError


def create_thumbnail(source, target, max_size=(512, 512), quality=82):
    source = Path(source)
    target = Path(target)
    try:
        with Image.open(source) as opened:
            opened.load()
            image = opened.copy()
    except (UnidentifiedImageError, OSError, ValueError) as error:
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
