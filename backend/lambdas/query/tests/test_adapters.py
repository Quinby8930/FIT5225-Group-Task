"""Contract tests for Member D's production cross-module adapters."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import UUID

import pytest

from app.notification_client import SNSNotificationPublisher
from app.schemas import Notification
from app.storage_client import LambdaStorageClient, StorageClientError
from app.tag_detector import RemoteTagDetector, TagDetectionError, _NoRedirectHandler


class FakeSNSClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": "message-1"}


def _sns_publisher(monkeypatch, client, **kwargs):
    boto3 = ModuleType("boto3")

    def build_client(service_name, *, region_name):
        assert service_name == "sns"
        assert region_name == "ap-southeast-2"
        return client

    boto3.client = build_client
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    return SNSNotificationPublisher(region="ap-southeast-2", **kwargs)


def _notification(user_id="user-1"):
    return Notification(
        notification_id="notification-1",
        user_id=user_id,
        file_id="file-1",
        species="wombat",
        object_key=f"originals/{user_id}/file-1/wombat.jpg",
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )


def test_sns_notification_publisher_selects_the_topic_from_user_id(monkeypatch):
    sns = FakeSNSClient()
    publisher = _sns_publisher(
        monkeypatch,
        sns,
        topic_arn_template="arn:aws:sns:ap-southeast-2:123456789012:alerts-{user_id}",
    )

    publisher.publish(_notification("user-42"))

    assert sns.calls[0]["TopicArn"] == (
        "arn:aws:sns:ap-southeast-2:123456789012:alerts-user-42"
    )


def test_sns_notification_publisher_message_identifies_user_species_and_file(
    monkeypatch,
):
    sns = FakeSNSClient()
    publisher = _sns_publisher(
        monkeypatch,
        sns,
        topic_arn="arn:aws:sns:ap-southeast-2:123456789012:archive-alerts",
    )

    publisher.publish(_notification())

    message = json.loads(sns.calls[0]["Message"])
    assert {
        "user_id": message["user_id"],
        "species": message["species"],
        "file_id": message["file_id"],
    } == {"user_id": "user-1", "species": "wombat", "file_id": "file-1"}


def test_sns_notification_publisher_propagates_publish_failure(monkeypatch):
    publisher = _sns_publisher(
        monkeypatch,
        FakeSNSClient(error=RuntimeError("SNS unavailable")),
        topic_arn="arn:aws:sns:ap-southeast-2:123456789012:archive-alerts",
    )

    with pytest.raises(RuntimeError, match="SNS unavailable"):
        publisher.publish(_notification())


class FakePayload:
    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.read_limits = []

    def read(self, limit=-1):
        self.read_limits.append(limit)
        return self.body if limit < 0 else self.body[:limit]


class FakeLambdaClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _lambda_response(payload, *, status=200, function_error=None):
    response = {"StatusCode": status, "Payload": FakePayload(payload)}
    if function_error is not None:
        response["FunctionError"] = function_error
    return response


def test_lambda_storage_client_synchronously_invokes_member_b_contract():
    payload = FakePayload(
        {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"deleted_count": 2}),
        }
    )
    fake = FakeLambdaClient({"StatusCode": 200, "Payload": payload})

    LambdaStorageClient("storage-delete", lambda_client=fake).delete(
        "user-1",
        [
            "originals/user-1/file-1/a.jpg",
            "thumbnails/user-1/file-1/thumbnail.jpg",
        ],
    )

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["FunctionName"] == "storage-delete"
    assert call["InvocationType"] == "RequestResponse"
    assert json.loads(call["Payload"]) == {
        "user_id": "user-1",
        "keys": [
            "originals/user-1/file-1/a.jpg",
            "thumbnails/user-1/file-1/thumbnail.jpg",
        ],
    }
    assert payload.read_limits == [1_048_577]


@pytest.mark.parametrize(
    ("response", "error_match"),
    [
        (_lambda_response({}, status=202), "invocation status"),
        (_lambda_response({}, function_error="Unhandled"), "function error"),
        (_lambda_response(b"not-json"), "malformed"),
        (_lambda_response({"statusCode": 200, "body": "not-json"}), "malformed"),
        (
            _lambda_response(
                {"statusCode": 403, "body": json.dumps({"code": "FORBIDDEN_KEY"})}
            ),
            "status 403",
        ),
    ],
)
def test_lambda_storage_client_rejects_failed_or_malformed_boundaries(
    response, error_match
):
    with pytest.raises(StorageClientError, match=error_match):
        LambdaStorageClient(
            "storage-delete", lambda_client=FakeLambdaClient(response)
        ).delete("user-1", ["originals/user-1/file-1/a.jpg"])


def test_lambda_storage_client_rejects_oversized_response_with_bounded_read():
    payload = FakePayload(b"x" * 1_048_577)

    with pytest.raises(StorageClientError, match="size limit"):
        LambdaStorageClient(
            "storage-delete",
            lambda_client=FakeLambdaClient({"StatusCode": 200, "Payload": payload}),
        ).delete("user-1", ["originals/user-1/file-1/a.jpg"])

    assert payload.read_limits == [1_048_577]


class FakeS3Client:
    def __init__(
        self,
        presigned_url="https://signed.example/query-image",
        *,
        put_error=None,
        presign_error=None,
        delete_error=None,
    ):
        self.presigned_url = presigned_url
        self.put_error = put_error
        self.presign_error = presign_error
        self.delete_error = delete_error
        self.put_calls = []
        self.presign_calls = []
        self.delete_calls = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.put_error:
            raise self.put_error

    def generate_presigned_url(self, operation, **kwargs):
        self.presign_calls.append((operation, kwargs))
        if self.presign_error:
            raise self.presign_error
        return self.presigned_url

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        if self.delete_error:
            raise self.delete_error


_MISSING_STATUS = object()


class FakeHttpResponse:
    def __init__(self, payload, *, status=200):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if status is not _MISSING_STATUS:
            self.status = status
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False

    def read(self, limit=-1):
        self.read_limits.append(limit)
        return self.body if limit < 0 else self.body[:limit]


class FakeUrlOpen:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _remote_detector(s3, opener):
    return RemoteTagDetector(
        bucket_name="private-media",
        inference_api_url="https://inference.example/service",
        internal_api_key="internal-test-key",
        s3_client=s3,
        http_open=opener,
        uuid_factory=lambda: UUID("11111111-2222-4333-8444-555555555555"),
    )


def test_remote_tag_detector_stages_calls_c_normalizes_and_cleans_up():
    s3 = FakeS3Client()
    response = FakeHttpResponse(
        {
            "tags": {" Dingo ": 2, "WOMBAT": 1},
            "detections": [{"species": "dingo", "confidence": 0.9}],
            "model_version": "speciesnet-v1",
        }
    )
    opener = FakeUrlOpen(response)

    tags = _remote_detector(s3, opener).detect(
        user_id="user-1",
        file_name="../../camera trap?.jpg",
        content_type="image/jpeg",
        content=b"image-bytes",
    )

    key = (
        "query-inputs/user-1/11111111-2222-4333-8444-555555555555/"
        "camera_trap_.jpg"
    )
    assert tags == {"dingo": 2, "wombat": 1}
    assert s3.put_calls == [
        {
            "Bucket": "private-media",
            "Key": key,
            "Body": b"image-bytes",
            "ContentType": "image/jpeg",
        }
    ]
    assert s3.presign_calls == [
        (
            "get_object",
            {
                "Params": {"Bucket": "private-media", "Key": key},
                "ExpiresIn": 120,
            },
        )
    ]
    request, timeout = opener.calls[0]
    assert request.get_method() == "POST"
    assert urlsplit(request.full_url).path == "/service/infer"
    assert json.loads(request.data) == {
        "file_id": "11111111-2222-4333-8444-555555555555",
        "media_type": "image",
        "image_urls": ["https://signed.example/query-image"],
    }
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["content-type"] == "application/json"
    assert headers["x-internal-api-key"] == "internal-test-key"
    assert timeout == 25
    assert response.read_limits == [1_048_577]
    assert s3.delete_calls == [{"Bucket": "private-media", "Key": key}]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://inference.example/infer",
        "https://inference.example/infer/",
        "https://inference.example/api/infer",
        "https://inference.example/%69nfer",
        "https://inference.example/InFeR",
        "https://inference.example/api/%49nF%65r/",
    ],
)
def test_remote_tag_detector_rejects_a_decoded_infer_path_segment(base_url):
    with pytest.raises(ValueError, match="infer"):
        RemoteTagDetector(
            bucket_name="private-media",
            inference_api_url=base_url,
            internal_api_key="internal-test-key",
            s3_client=FakeS3Client(),
            http_open=FakeUrlOpen(None),
        )


@pytest.mark.parametrize(
    ("base_url", "expected_path"),
    [
        ("https://inference.example/inference", "/inference/infer"),
        ("https://inference.example/api/inferential", "/api/inferential/infer"),
    ],
)
def test_remote_tag_detector_accepts_infer_substrings_that_are_not_segments(
    base_url, expected_path
):
    s3 = FakeS3Client()
    opener = FakeUrlOpen(
        FakeHttpResponse({"tags": {}, "detections": [], "model_version": "v1"})
    )
    detector = RemoteTagDetector(
        bucket_name="private-media",
        inference_api_url=base_url,
        internal_api_key="internal-test-key",
        s3_client=s3,
        http_open=opener,
    )

    assert detector.detect(
        user_id="user-1",
        file_name="query.jpg",
        content_type="image/jpeg",
        content=b"image-bytes",
    ) == {}
    request, _ = opener.calls[0]
    assert urlsplit(request.full_url).path == expected_path
    assert len(s3.delete_calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeHttpResponse(b"not-json"),
        FakeHttpResponse({"tags": [], "detections": [], "model_version": "v1"}),
        FakeHttpResponse({"tags": {}, "detections": [], "model_version": ""}),
        FakeHttpResponse({"tags": {"dingo": True}, "detections": [], "model_version": "v1"}),
        FakeHttpResponse({"tags": {}, "detections": [], "model_version": "v1"}, status=503),
        FakeHttpResponse(
            {"tags": {}, "detections": [], "model_version": "v1"},
            status=_MISSING_STATUS,
        ),
    ],
)
def test_remote_tag_detector_rejects_malformed_or_non_success_c_response(response):
    s3 = FakeS3Client()

    with pytest.raises(TagDetectionError):
        _remote_detector(s3, FakeUrlOpen(response)).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert len(s3.delete_calls) == 1


def test_remote_tag_detector_caps_response_and_still_cleans_up():
    s3 = FakeS3Client()
    response = FakeHttpResponse(b"x" * 1_048_577)

    with pytest.raises(TagDetectionError, match="size limit"):
        _remote_detector(s3, FakeUrlOpen(response)).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert response.read_limits == [1_048_577]
    assert len(s3.delete_calls) == 1


@pytest.mark.parametrize(
    ("inference_url", "presigned_url"),
    [
        ("http://inference.example", "https://signed.example/image"),
        ("https://inference.example", "http://signed.example/image"),
    ],
)
def test_remote_tag_detector_rejects_non_https_endpoints_and_cleans_staged_object(
    inference_url, presigned_url
):
    s3 = FakeS3Client(presigned_url=presigned_url)
    opener = FakeUrlOpen(
        FakeHttpResponse({"tags": {}, "detections": [], "model_version": "v1"})
    )

    if inference_url.startswith("http://"):
        with pytest.raises(ValueError, match="HTTPS"):
            RemoteTagDetector(
                bucket_name="private-media",
                inference_api_url=inference_url,
                internal_api_key="key",
                s3_client=s3,
                http_open=opener,
            )
        assert s3.delete_calls == []
    else:
        with pytest.raises(TagDetectionError, match="HTTPS"):
            _remote_detector(s3, opener).detect(
                user_id="user-1",
                file_name="query.jpg",
                content_type="image/jpeg",
                content=b"image-bytes",
            )
        assert len(s3.delete_calls) == 1


def test_remote_tag_detector_attempts_cleanup_when_put_may_commit_then_raise():
    s3 = FakeS3Client(put_error=RuntimeError("ambiguous put failure"))

    with pytest.raises(TagDetectionError, match="staging"):
        _remote_detector(s3, FakeUrlOpen(None)).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert len(s3.put_calls) == 1
    assert len(s3.delete_calls) == 1


def test_remote_tag_detector_normalizes_presign_failure_and_cleans_up():
    s3 = FakeS3Client(presign_error=RuntimeError("raw presign failure"))

    with pytest.raises(TagDetectionError, match="presign") as caught:
        _remote_detector(s3, FakeUrlOpen(None)).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert "raw presign failure" not in str(caught.value)
    assert len(s3.delete_calls) == 1


def test_remote_tag_detector_primary_failure_is_not_masked_by_cleanup_failure():
    s3 = FakeS3Client(delete_error=RuntimeError("raw cleanup failure"))

    with pytest.raises(TagDetectionError, match="malformed") as caught:
        _remote_detector(s3, FakeUrlOpen(FakeHttpResponse(b"not-json"))).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert "raw cleanup failure" not in str(caught.value)
    assert len(s3.delete_calls) == 1


def test_remote_tag_detector_success_with_cleanup_failure_is_controlled():
    s3 = FakeS3Client(delete_error=RuntimeError("raw cleanup failure"))
    response = FakeHttpResponse(
        {"tags": {}, "detections": [], "model_version": "speciesnet-v1"}
    )

    with pytest.raises(TagDetectionError, match="cleanup") as caught:
        _remote_detector(s3, FakeUrlOpen(response)).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert "raw cleanup failure" not in str(caught.value)
    assert len(s3.delete_calls) == 1


def test_remote_tag_detector_rejects_redirect_without_second_destination_call():
    redirect = HTTPError(
        "https://inference.example/service/infer",
        302,
        "redirect",
        {"Location": "https://attacker.example/collect"},
        None,
    )
    opener = FakeUrlOpen(redirect)
    s3 = FakeS3Client()

    with pytest.raises(TagDetectionError, match="inference request failed"):
        _remote_detector(s3, opener).detect(
            user_id="user-1",
            file_name="query.jpg",
            content_type="image/jpeg",
            content=b"image-bytes",
        )

    assert len(opener.calls) == 1
    assert opener.calls[0][0].full_url == "https://inference.example/service/infer"


def test_authenticated_inference_http_handler_never_follows_redirects():
    request = _NoRedirectHandler().redirect_request(
        Request(
            "https://inference.example/service/infer",
            headers={"X-Internal-Api-Key": "must-not-forward"},
        ),
        None,
        302,
        "redirect",
        {"Location": "https://attacker.example/collect"},
        "https://attacker.example/collect",
    )

    assert request is None
