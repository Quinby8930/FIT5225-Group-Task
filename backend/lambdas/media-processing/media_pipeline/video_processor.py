import subprocess
from pathlib import Path

from .errors import MediaPipelineError


DEFAULT_FFMPEG_TIMEOUT_SECONDS = 840
DEFAULT_MAX_FRAMES = 900


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
        "-i",
        str(input_path),
        "-vf",
        "fps=1",
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
):
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
    return frames
