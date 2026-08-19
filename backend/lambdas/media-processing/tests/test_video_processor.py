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
    assert command[command.index("-frames:v") + 1] == "901"
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
    assert options == {"check": True, "timeout": 840}


def test_extract_frames_maps_checked_process_failure(tmp_path):
    def failing_runner(command, **options):
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(MediaPipelineError) as caught:
        extract_frames(tmp_path / "input.mp4", tmp_path / "frames", runner=failing_runner)

    assert caught.value.code == "FRAME_EXTRACTION_FAILED"


def test_extract_frames_maps_subprocess_timeout(tmp_path):
    def timing_out_runner(command, **options):
        raise subprocess.TimeoutExpired(command, options["timeout"])

    with pytest.raises(MediaPipelineError) as caught:
        extract_frames(
            tmp_path / "input.mp4",
            tmp_path / "frames",
            runner=timing_out_runner,
        )

    assert caught.value.code == "FRAME_EXTRACTION_FAILED"


def test_extract_frames_rejects_a_successful_command_that_created_no_frames(tmp_path):
    def empty_runner(command, **options):
        return None

    with pytest.raises(MediaPipelineError) as caught:
        extract_frames(tmp_path / "input.mp4", tmp_path / "frames", runner=empty_runner)

    assert caught.value.code == "FRAME_EXTRACTION_FAILED"


def test_extract_frames_requests_cap_plus_one_and_rejects_an_over_limit_video(
    tmp_path,
):
    output_dir = tmp_path / "frames"
    calls = []

    def over_limit_runner(command, **options):
        calls.append((command, options))
        for number in range(1, 4):
            (output_dir / f"frame-{number:06}.jpg").write_bytes(b"frame")

    with pytest.raises(MediaPipelineError) as caught:
        extract_frames(
            tmp_path / "input.mp4",
            output_dir,
            runner=over_limit_runner,
            max_frames=2,
            timeout_seconds=30,
        )

    command, options = calls[0]
    assert command[command.index("-vf") + 1] == "fps=1"
    assert command[command.index("-frames:v") + 1] == "3"
    assert command[-1] == str(output_dir / "frame-%06d.jpg")
    assert options == {"check": True, "timeout": 30}
    assert caught.value.code == "FRAME_EXTRACTION_FAILED"
