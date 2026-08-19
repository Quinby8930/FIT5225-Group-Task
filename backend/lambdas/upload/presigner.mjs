export function createS3Presigner({ client, bucket, PutObjectCommand, getSignedUrl, expiresIn = 300 }) {
  return async function presignUpload({ objectKey, contentType, checksumSha256, sizeBytes }) {
    const command = new PutObjectCommand({
      Bucket: bucket,
      Key: objectKey,
      ContentType: contentType,
      ContentLength: sizeBytes,
      ChecksumSHA256: checksumSha256,
    });
    return getSignedUrl(client, command, {
      expiresIn,
      signableHeaders: new Set(['content-type', 'content-length']),
      unhoistableHeaders: new Set(['x-amz-checksum-sha256']),
    });
  };
}
