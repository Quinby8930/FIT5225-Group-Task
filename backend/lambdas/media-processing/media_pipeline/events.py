from dataclasses import dataclass
from urllib.parse import unquote_plus

from .errors import MediaPipelineError


@dataclass(frozen=True)
class S3MediaRecord:
    bucket: str
    key: str
    user_id: str
    file_id: str
    filename: str
    sequencer: str


def _invalid_event():
    return MediaPipelineError("INVALID_S3_EVENT", "Invalid S3 media event")


def parse_s3_record(record):
    try:
        bucket = record["s3"]["bucket"]["name"]
        encoded_key = record["s3"]["object"]["key"]
        sequencer = record["s3"]["object"]["sequencer"]
    except (KeyError, TypeError):
        raise _invalid_event() from None

    if not all(isinstance(value, str) and value for value in (bucket, encoded_key, sequencer)):
        raise _invalid_event()

    key = unquote_plus(encoded_key)
    parts = key.split("/")
    unsafe_component = any(
        part in {".", ".."}
        or "\\" in part
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        for part in parts[1:]
    )
    if (
        len(parts) != 4
        or parts[0] != "originals"
        or not all(parts[1:])
        or unsafe_component
    ):
        raise _invalid_event()

    return S3MediaRecord(
        bucket=bucket,
        key=key,
        user_id=parts[1],
        file_id=parts[2],
        filename=parts[3],
        sequencer=sequencer,
    )
