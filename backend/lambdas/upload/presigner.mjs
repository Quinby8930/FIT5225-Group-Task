export function createS3Presigner({ client, bucket, PutObjectCommand, getSignedUrl, expiresIn = 300 }) {
  return async function presignUpload({ objectKey, contentType, checksumSha256 }) {
    const command = new PutObjectCommand({
      Bucket: bucket,
      Key: objectKey,
      ContentType: contentType,
      ChecksumSHA256: checksumSha256,
    });
    return getSignedUrl(client, command, { expiresIn });
  };
}
