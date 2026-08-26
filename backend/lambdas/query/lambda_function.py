"""Lambda entry point — runs the FastAPI query API behind API Gateway.

Deploy this whole ``db-query-api`` directory (with ``app/`` and dependencies)
as one Lambda, pointed at by an API Gateway HTTP API route
``ANY /{proxy+}`` (or ``$default``). The API Gateway ``CognitoJWTAuthorizer``
verifies the JWT before the Lambda runs; ``get_current_user`` then reads
``claims.sub`` from the injected event (see ``examples/cognito_auth_example.py``).

Environment variables to set on the Lambda:

    REPO_BACKEND=dynamodb
    DYNAMODB_TABLE=PacificBioArchiveFiles
    AWS_REGION=ap-southeast-2

Add ``mangum`` to ``requirements.txt`` before packaging.
"""

from mangum import Mangum

from app.main import app

adapter = Mangum(app, api_gateway_base_path="/dev")


def handler(event, context):
    # Let get_current_user read the API Gateway authorizer claims.
    app.state.lambda_event = event
    return adapter(event, context)
