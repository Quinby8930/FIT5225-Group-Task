from .errors import MediaPipelineError


class S3Storage:
    def __init__(self, client):
        self.client = client

    def get_content_type(self, bucket, key):
        response = self.client.head_object(Bucket=bucket, Key=key)
        return response.get("ContentType")

    def download(self, bucket, key, destination):
        self.client.download_file(bucket, key, str(destination))

    def upload(self, bucket, key, source, content_type):
        self.client.upload_file(
            str(source),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def presign_get(self, bucket, key):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=900,
        )

    def delete(self, bucket, keys):
        for offset in range(0, len(keys), 1000):
            batch = keys[offset : offset + 1000]
            result = self.client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in batch]},
            )
            if isinstance(result, dict) and result.get("Errors"):
                raise MediaPipelineError(
                    "STORAGE_DELETE_FAILED",
                    "S3 reported one or more object deletion failures",
                    retryable=True,
                )
