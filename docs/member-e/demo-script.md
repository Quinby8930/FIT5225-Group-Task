# Member E Final Demo Script

## Required Cloud Input

Member E needs only one runtime value from Member A:

```text
VITE_API_BASE_URL=https://<final-api-gateway-host>
```

The same API must expose:

- `POST /upload-url`
- `POST /asset-urls`
- `POST /query/by-tags`
- `POST /query/by-species`
- `GET /query/by-thumbnail`
- `POST /query/by-file`
- `POST /tags/edit`
- `POST /files/delete`
- `POST /notifications/subscribe`
- `DELETE /notifications/subscribe`
- `GET /notifications/subscriptions`
- `GET /notifications`

## Browser Walkthrough

1. Open the frontend and sign in through Cognito Hosted UI.
2. Use **Check auth** to prove the browser sends the Cognito ID token.
3. Upload one image. Confirm the UI shows the `file_id`, S3 object key and checksum.
4. Upload one short video. Confirm the upload is accepted and processing starts.
5. Wait for processing, then query by species such as `wombat` or `dingo`.
6. Query by tag counts such as `dingo:1, wombat:1` and explain that this is AND logic.
7. Click a thumbnail result's **Full image** action. The UI resolves the original through
   `GET /query/by-thumbnail`, then opens a signed URL returned by `POST /asset-urls`.
8. Use **Match by file** with an image under 4 MiB. Explain that query-only files are not
   stored permanently.
9. Select result keys, add a manual tag, then remove it.
10. Select a result key and delete it. Confirm the response removes both database and storage
    objects.
11. Subscribe to a species, process or seed a matching file, then refresh the notification inbox.
12. Demonstrate a retry/error path by repeating an existing upload or forcing an invalid query,
    then show that the UI displays the failure without losing the session.

## Screenshot Checklist

- Cognito sign-in or sign-up screen.
- Authenticated frontend session with user `sub`.
- Successful upload receipt with checksum.
- Image/video query result list.
- Full-image signed URL opening from a thumbnail.
- Bulk tag edit response.
- Delete response.
- Subscription list and notification inbox.
- One visible failed operation or retry attempt.
- Terminal output for `npm test` and `npm run build`.
