import subprocess
from pathlib import Path

from .errors import MediaPipelineError


DEFAULT_FFMPEG_TIMEOUT_SECONDS = 600
DEFAULT_MAX_FRAMES = 900
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FRAME_DIMENSION = 1024
DEFAULT_JPEG_QUALITY = 5


def build_ffmpeg_command(
    *,
    ffmpeg_path,
    input_path,
    output_pattern,
    frame_limit=DEFAULT_MAX_FRAMES + 1,
):
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(input_path),
        "-vf",
        (
            f"fps=1,scale=w='min({DEFAULT_MAX_FRAME_DIMENSION},iw)':"
            f"h='min({DEFAULT_MAX_FRAME_DIMENSION},ih)':"
            "force_original_aspect_ratio=decrease"
        ),
        "-q:v",
        str(DEFAULT_JPEG_QUALITY),
        "-frames:v",
        str(frame_limit),
        str(output_pattern),
    ]


def extract_frames(
    input_path,
    output_dir,
    *,
    ffmpeg_path="/opt/bin/ffmpeg",
    runner=subprocess.run,
    timeout_seconds=DEFAULT_FFMPEG_TIMEOUT_SECONDS,
    max_frames=DEFAULT_MAX_FRAMES,
    max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
):
    if (
        timeout_seconds > DEFAULT_FFMPEG_TIMEOUT_SECONDS
        or max_frames > DEFAULT_MAX_FRAMES
        or max_output_bytes > DEFAULT_MAX_OUTPUT_BYTES
    ):
        raise ValueError("Video extraction configuration exceeds a hard limit")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(
        ffmpeg_path=ffmpeg_path,
        input_path=input_path,
        output_pattern=output_dir / "frame-%06d.jpg",
        frame_limit=max_frames + 1,
    )
    try:
        runner(command, check=True, timeout=timeout_seconds)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video frame extraction failed"
        ) from error

    frames = sorted(output_dir.glob("frame-*.jpg"))
    if not frames:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video frame extraction produced no frames"
        )
    if len(frames) > max_frames:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video exceeds the sampled frame limit"
        )
    try:
        total_output_bytes = sum(frame.stat().st_size for frame in frames)
    except OSError as error:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video frame output could not be inspected"
        ) from error
    if total_output_bytes > max_output_bytes:
        raise MediaPipelineError(
            "FRAME_EXTRACTION_FAILED", "Video frame output exceeds the size limit"
        )
    return frames
