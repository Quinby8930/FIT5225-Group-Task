import subprocess

import pytest

from media_pipeline.errors import MediaPipelineError
from media_pipeline.video_processor import build_ffmpeg_command, extract_frames


def test_ffmpeg_command_uses_lambda_binary_and_exactly_one_frame_per_second(tmp_path):
    command = build_ffmpeg_command(
        ffmpeg_path="/opt/bin/ffmpeg",
        input_path=tmp_path / "input.mp4",
        output_pattern=tmp_path / "frame-%06d.jpg",
    )

    assert command[0] == "/opt/bin/ffmpeg"
    assert command[command.index("-vf") + 1] == "fps=1"
    assert command[-1] == str(tmp_path / "frame-%06d.jpg")


def test_extract_frames_runs_checked_command_and_returns_real_frames_in_lexical_order(
    tmp_path,
):
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video fixture")
    output_dir = tmp_path / "frames"
    calls = []

    def runner(command, **options):
        calls.append((command, options))
        output_dir.mkdir(exist_ok=True)
        (output_dir / "frame-000010.jpg").write_bytes(b"ten")
        (output_dir / "frame-000002.jpg").write_bytes(b"two")

    frames = extract_frames(input_path, output_dir, runner=runner)

    assert [path.name for path in frames] == [
        "frame-000002.jpg",
        "frame-000010.jpg",
    ]
    command, options = calls[0]
    assert command[0] == "/opt/bin/ffmpeg"
    assert command[command.index("-vf") + 1] == "fps=1"
    assert options == {"check": True}


def test_extract_frames_maps_checked_process_failure(tmp_path):
    def failing_runner(command, **options):
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(MediaPipelineError) as caught:
        extract_frames(tmp_path / "input.mp4", tmp_path / "frames", runner=failing_runner)

    assert caught.value.code == "FRAME_EXTRACTION_FAILED"


def test_extract_frames_rejects_a_successful_command_that_created_no_frames(tmp_path):
    def empty_runner(command, **options):
        return None

    with pytest.raises(MediaPipelineError) as caught:
        extract_frames(tmp_path / "input.mp4", tmp_path / "frames", runner=empty_runner)

    assert caught.value.code == "FRAME_EXTRACTION_FAILED"
