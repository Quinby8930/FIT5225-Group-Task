import { validateUploadRequest } from './validation.mjs';

export function createUploadService({ createFileId, reserveUpload, presignUpload, maxBytes = 262_144_000, expiresIn = 300 }) {
  return {
    async createUpload({ userId, request }) {
      const upload = validateUploadRequest(request, maxBytes);
      const fileId = createFileId();
      const objectKey = `originals/${userId}/${fileId}/${upload.filename}`;
      await reserveUpload({
        file_id: fileId,
        user_id: userId,
        checksum: upload.checksumSha256,
        filename: upload.filename,
        file_type: upload.fileType,
        content_type: upload.contentType,
        size_bytes: upload.sizeBytes,
        object_key: objectKey,
        status: 'pending_upload',
      });
      const uploadUrl = await presignUpload({
        objectKey,
        contentType: upload.contentType,
        checksumSha256: upload.checksumSha256,
        sizeBytes: upload.sizeBytes,
      });
      return {
        file_id: fileId,
        object_key: objectKey,
        upload_url: uploadUrl,
        expires_in: expiresIn,
        required_headers: {
          'Content-Type': upload.contentType,
          'x-amz-checksum-sha256': upload.checksumSha256,
        },
      };
    },
  };
}
