"""Configuration for the database & query API (Member D).

The same code runs locally (SQLite) and on the cloud (DynamoDB) behind a
single repository interface, so nothing here needs to change when the team
deploys — only the `REPOSITORY_BACKEND` value and the DB connection string.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Shared secret used only by Member B when calling Member D's internal API.
    internal_api_key: str = os.getenv("INTERNAL_API_KEY", "")

    # Which repository backend to use: "sqlite" (local) or "dynamodb" (cloud).
    repository_backend: str = os.getenv("REPO_BACKEND", "sqlite")

    # Local SQLite file path (used when backend == "sqlite").
    sqlite_path: str = os.getenv("SQLITE_PATH", "data/pacific_bioarchive.db")

    # DynamoDB table names (used when backend == "dynamodb").
    dynamodb_table: str = os.getenv("DYNAMODB_TABLE", "PacificBioArchiveFiles")
    subscriptions_table: str = os.getenv(
        "SUBSCRIPTIONS_TABLE", "PacificBioArchiveSubscriptions"
    )
    notifications_table: str = os.getenv(
        "NOTIFICATIONS_TABLE", "PacificBioArchiveNotifications"
    )
    aws_region: str = os.getenv("AWS_REGION", "ap-southeast-2")

    # Cross-module adapters. Cloud deployment sets these explicitly to
    # "lambda" and "remote"; "stub" is an intentional local/test choice.
    storage_backend: str = os.getenv("STORAGE_BACKEND", "")
    storage_delete_function_name: str = os.getenv(
        "STORAGE_DELETE_FUNCTION_NAME", ""
    )
    tag_detector_backend: str = os.getenv("TAG_DETECTOR_BACKEND", "")
    query_input_bucket: str = os.getenv("QUERY_INPUT_BUCKET", "")
    inference_api_url: str = os.getenv("INFERENCE_API_URL", "")

    # Browser origins allowed to call the query API during local/cloud demos.
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    )

    # Notification delivery. Use "stub" locally or "sns" in AWS.
    notification_publisher: str = os.getenv("NOTIFICATION_PUBLISHER", "stub")
    sns_topic_arn: str = os.getenv("SNS_TOPIC_ARN", "")
    sns_topic_arn_template: str = os.getenv("SNS_TOPIC_ARN_TEMPLATE", "")


settings = Settings()
