"""Lambda entry point — runs the FastAPI query API behind API Gateway.

The Member D SAM template deploys this directory as one Lambda behind explicit
HTTP API routes. Public routes use JWT authorization; internal and OPTIONS
routes are explicitly unauthenticated at API Gateway. The internal application
key still protects every internal route.

Environment variables to set on the Lambda:

    REPO_BACKEND=dynamodb
    DYNAMODB_TABLE=PacificBioArchiveFiles
    STORAGE_BACKEND=lambda
    STORAGE_DELETE_FUNCTION_NAME=<member-b-function-name>
    TAG_DETECTOR_BACKEND=remote
    QUERY_INPUT_BUCKET=<private-media-bucket>
    INFERENCE_API_URL=https://<member-c-host>
    INTERNAL_API_KEY=<shared-secret>
"""

from mangum import Mangum

from app.main import app

adapter = Mangum(app, api_gateway_base_path="/dev")


def handler(event, context):
    # Let get_current_user read the API Gateway authorizer claims.
    app.state.lambda_event = event
    return adapter(event, context)
