import subprocess
from pathlib import Path

from .errors import MediaPipelineError


def build_ffmpeg_command(*, ffmpeg_path, input_path, output_pattern):
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        "fps=1",
        str(output_pattern),
    ]


def extract_frames(
    input_path,
    output_dir,
    *,
    ffmpeg_path="/opt/bin/ffmpeg",
    runner=subprocess.run,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(
        ffmpeg_path=ffmpeg_path,
        input_path=input_path,
        output_pattern=output_dir / "frame-%06d.jpg",
    )
    try:
        runner(command, check=True)
    except (subprocess.CalledProcessError, OSError) as error:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video frame extraction failed"
        ) from error

    frames = sorted(output_dir.glob("frame-*.jpg"))
    if not frames:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video frame extraction produced no frames"
        )
    return frames
