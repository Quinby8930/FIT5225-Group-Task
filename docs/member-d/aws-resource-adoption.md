# Member D 现有 AWS 资源纳管手册

本手册只适用于当前 AWS 账号：`PacificBioArchive-QueryLambda`、单一 API Gateway
integration 和 16 条 Member D 非 OPTIONS 路由已经在线，但尚未归属
`PacificBioArchive-Database` CloudFormation stack。目标是先通过 IMPORT 在不替换、不删除
资源的前提下纳管，再通过正常 UPDATE 部署当前模板。

如果是一个没有上述 Member D 资源的全新普通 AWS 账号，不要使用本手册；按
[`database-setup.md`](database-setup.md) 的“全新账号”路径正常部署 SAM。

## 不可突破的安全边界

- 本流程有且只有两次执行审批：IMPORT 执行前一次，UPDATE 执行前一次。看到
  `STOP` 后必须停止，只有用户明确批准对应操作才可继续。
- Root 只允许在阶段 1 为**既有** `fit5225-cli-deployer` 开启控制台登录。Root 不运行
  CloudShell、不部署、不创建或执行 change set；完成后立即退出。
- 禁止创建或使用 Root access key。禁止把普通用户 access key 分享给 Codex、微信、
  Git 或文档。实际部署必须在该 IAM 用户登录后的 CloudShell 中完成。
- 本项目没有 AWS Academy/Learner Lab 流程；不要寻找、创建或粘贴 Academy 临时凭据。
- `INTERNAL_API_KEY`、控制台密码、JWT、session token、presigned URL 不得进入命令行
  argv、shell history、环境转储、`.work/`、`samconfig.toml`、参数文件、截图、输出、Git
  或群聊。不要打开 shell tracing；第一条 shell 设置必须包含 `set +x`。
- 禁止运行 `delete-route`、`delete-integration`、`delete-function`、`delete-stack`。也不要
  为绕过冲突而删除或重建在线资源。
- 本手册中的 AWS 命令仅供已获授权的人工操作者复制。凡会改变 AWS 状态的命令都以
  `WRITE` 标记；未得到相应审批时不得运行。

## 阶段 1/10：Root 仅开启既有 IAM 用户的控制台登录

**WRITE（人工控制台操作）— 仅用于启用 IAM 用户登录，不用于部署。**

1. 用 Root 登录 AWS Console，进入 IAM → Users → `fit5225-cli-deployer`。
2. 确认这是既有用户，不新建同名用户；在 Security credentials 中启用 Console access。
3. 选择 AWS 自动生成的一次性密码，并要求用户首次登录时重置密码。
4. 通过团队认可的私密渠道把一次性密码交给当前操作者。不要截图、复制到 Codex/微信、
   写入本地文件或 Git。
5. 立即退出 Root。后续所有 CloudShell 与部署操作都禁止使用 Root。

如果无法确认用户身份、权限或安全交付方式，停止并联系账号所有者。

## 阶段 2/10：以 IAM 用户重新登录并精确验证身份

使用账号的 IAM sign-in URL 登录 `fit5225-cli-deployer`，完成强制密码重置，然后从该
会话打开 CloudShell。先运行以下只读检查；任何 `test` 失败都必须停止。

```bash
set +x
set -euo pipefail
export AWS_REGION=ap-southeast-2
STACK_NAME=PacificBioArchive-Database
API_ID=2dd2aqb32j
AUTHORIZER_ID=7ir7fs
INTEGRATION_ID=fbjojun
FUNCTION_NAME=PacificBioArchive-QueryLambda
ROLE_NAME=PacificBioArchive-QueryLambdaRole
WORK_DIR=infrastructure/member-d/import/.work
IMPORT_CHANGE_SET=member-d-adopt-existing
UPDATE_CHANGE_SET=member-d-deploy-current

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
test "$CALLER_ARN" = "arn:aws:iam::$ACCOUNT_ID:user/fit5225-cli-deployer"
```

仅仅“不是 Root”不够；ARN 必须与上面的动态账号 ID 和用户名完全一致。不要打印、记录
或截图 `ACCOUNT_ID`/`CALLER_ARN` 的实际输出。

## 阶段 3/10：取得最新代码并运行本地工具测试

在 CloudShell 中克隆仓库，或在既有 clone 中只做 fast-forward pull。下面是本地文件操作，
不会改变 AWS：

```bash
git clone https://github.com/Quinby8930/FIT5225-Group-Task.git
cd FIT5225-Group-Task
git pull --ff-only

python -m pytest infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/test_prepare_import.py \
  infrastructure/member-d/test_template.py -q
```

测试不全绿则停止。不要为了继续部署而跳过或修改测试。

## 阶段 4/10：只读审计现有资源

`audit` 只调用只读 AWS API，并在本地写入已净化 snapshot。参数名以当前工具的真实 CLI
接口为准：

```bash
python infrastructure/member-d/import/prepare_import.py audit \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" --workdir "$WORK_DIR"
```

它必须验证：调用者、stack 状态、Lambda/role/resource policy、integration、16 条 Member D
非 OPTIONS 路由、authorizer、其他 stack 所有权和 CloudFormation import identifiers。它不会
选择 Member B 的 `/upload-url`、`/asset-urls`，也不会选择 OPTIONS 路由。

## 阶段 5/10：人工批准 artifact bucket，再备份 Lambda

artifact bucket 必须由操作者明确指定。不要自动创建 bucket，不要在本流程中修改 bucket
设置，也不要改动共享的 `aws-sam-cli-managed-default` bucket。`prepare` 会先证明目标 bucket
属于同一账号和区域、不可公开、已加密、已启用版本、可读写，然后才上传内容寻址的精确
Lambda zip 备份并生成四个确定性文件。

```bash
read -r -p "Approved private, encrypted, versioned artifact bucket: " ARTIFACT_BUCKET

# WRITE — 在已批准 bucket 中上传精确 Lambda 备份；不会创建 CloudFormation change set。
python infrastructure/member-d/import/prepare_import.py prepare \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" --artifact-bucket "$ARTIFACT_BUCKET" \
  --workdir "$WORK_DIR"
```

必须得到以下四个本地文件，且不得手工编辑：

- `sanitized-snapshot.json`
- `import-template.json`
- `resources-to-import.json`
- `import-parameters.json`

## 阶段 6/10：确认只复用既有 InternalApiKey

导入参数文件只能包含 `UsePreviousValue: true`，不能含任何 `ParameterValue`。用本地 Python
做结构检查，不要显示参数值：

```bash
python - "$WORK_DIR/import-parameters.json" <<'PY'
import json
import pathlib
import sys

parameters = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert parameters, "parameter list is empty"
assert any(
    item == {"ParameterKey": "InternalApiKey", "UsePreviousValue": True}
    for item in parameters
), "InternalApiKey is not reused"
assert all(
    item.get("UsePreviousValue") is True and "ParameterValue" not in item
    for item in parameters
), "a literal parameter value is present"
PY
```

如果现有 stack 没有 `InternalApiKey` parameter，工具应 fail closed。此时停止：从团队认可
的安全存储取得与 C 当前 Alibaba FC 完全相同的值，并另外请求一次针对安全交互式输入方案
的批准。不要把值放进 argv、history、环境变量、`.work/`、`samconfig.toml`、参数文件、
输出或截图；在安全方案批准前不得创建 IMPORT change set。

## 阶段 7/10：创建并审查 IMPORT change set

下面第一条命令只创建 change set，不执行资源变更：

```bash
# WRITE — 只创建 IMPORT change set；不要执行。
aws cloudformation create-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET" --change-set-type IMPORT \
  --template-body "file://$WORK_DIR/import-template.json" \
  --resources-to-import "file://$WORK_DIR/resources-to-import.json" \
  --parameters "file://$WORK_DIR/import-parameters.json" \
  --capabilities CAPABILITY_NAMED_IAM

aws cloudformation wait change-set-create-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET"

aws cloudformation describe-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET" \
  --query 'Changes[].ResourceChange.{Action:Action,LogicalId:LogicalResourceId,Type:ResourceType,Replacement:Replacement}' \
  --output table --no-cli-pager

python infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" --stack "$STACK_NAME" \
  --change-set "$IMPORT_CHANGE_SET" --expected-type IMPORT \
  --workdir "$WORK_DIR"
```

验证器必须确认恰好 18 个 `Import`：1 个 Lambda、1 个 integration、16 条 Member D
非 OPTIONS 路由；不得出现 Add/Modify/Remove、Member B 路由或 OPTIONS 路由。

> **STOP 1 — 第一次明确批准：** 把净化后的审查结论交给用户。只有用户明确回复批准
> “执行 IMPORT change set”后才可进入阶段 8。沉默、模糊回复或“继续看看”都不算批准。

## 阶段 8/10：批准后执行 IMPORT，并证明运行时未改变

```bash
# WRITE — 仅在 STOP 1 获得明确批准后执行。
aws cloudformation execute-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET"

aws cloudformation wait stack-import-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME"

# WRITE — 启动只读性质的 drift 检测任务；不会修改 stack 资源。
DRIFT_ID=$(aws cloudformation detect-stack-drift \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --query StackDriftDetectionId --output text)

aws cloudformation wait stack-drift-detection-complete \
  --region "$AWS_REGION" --stack-drift-detection-id "$DRIFT_ID"

aws cloudformation describe-stack-drift-detection-status \
  --region "$AWS_REGION" --stack-drift-detection-id "$DRIFT_ID" \
  --output table --no-cli-pager

python infrastructure/member-d/import/prepare_import.py audit \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" --workdir "$WORK_DIR" \
  --baseline "$WORK_DIR/sanitized-snapshot.json"

aws lambda get-policy \
  --region "$AWS_REGION" --function-name "$FUNCTION_NAME" \
  --query Policy --output text --no-cli-pager

aws iam get-role --role-name "$ROLE_NAME" \
  --query 'Role.{RoleName:RoleName,Path:Path,PermissionsBoundary:PermissionsBoundary}' \
  --output table --no-cli-pager

aws apigatewayv2 get-integration \
  --region "$AWS_REGION" --api-id "$API_ID" \
  --integration-id "$INTEGRATION_ID" --output json --no-cli-pager

aws apigatewayv2 get-routes \
  --region "$AWS_REGION" --api-id "$API_ID" \
  --query 'sort_by(Items,&RouteKey)[].{Route:RouteKey,Target:Target,Auth:AuthorizationType,Authorizer:AuthorizerId}' \
  --output table --no-cli-pager
```

`audit --baseline` 必须证明 Lambda 完整配置与 resource policy、integration 和 16 条路由
保持不变；role 仍应与 stack 中的 `QueryLambdaRole` 一致。出现 drift、replacement、route
变化或权限差异时停止，不进入 UPDATE。

## 阶段 9/10：打包当前 SAM，创建并审查 UPDATE change set

先只读取得 Member B stack 输出。函数参数必须使用函数**名称**，不能传 ARN：

```bash
MEDIA_BUCKET=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name PacificBioArchive-Media \
  --query "Stacks[0].Outputs[?OutputKey=='MediaBucketName'].OutputValue | [0]" \
  --output text)
STORAGE_DELETE_ARN=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name PacificBioArchive-Media \
  --query "Stacks[0].Outputs[?OutputKey=='StorageDeleteFunctionArn'].OutputValue | [0]" \
  --output text)
STORAGE_DELETE_FUNCTION_NAME=$(aws lambda get-function-configuration \
  --region "$AWS_REGION" --function-name "$STORAGE_DELETE_ARN" \
  --query FunctionName --output text)

test -n "$MEDIA_BUCKET"
test -n "$STORAGE_DELETE_FUNCTION_NAME"

# WRITE — 上传 packaged template/code object；不会更新 stack。
sam package --region "$AWS_REGION" \
  --template-file infrastructure/member-d/dynamodb.yaml \
  --s3-bucket "$ARTIFACT_BUCKET" --s3-prefix member-d/current \
  --output-template-file "$WORK_DIR/packaged-template.yaml"

# WRITE — 只创建 UPDATE change set；不要执行。
aws cloudformation create-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET" --change-set-type UPDATE \
  --template-body "file://$WORK_DIR/packaged-template.yaml" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters \
    ParameterKey=ExistingHttpApiId,ParameterValue="$API_ID" \
    ParameterKey=ExistingJwtAuthorizerId,ParameterValue="$AUTHORIZER_ID" \
    ParameterKey=QueryInputBucketName,ParameterValue="$MEDIA_BUCKET" \
    ParameterKey=StorageDeleteFunctionName,ParameterValue="$STORAGE_DELETE_FUNCTION_NAME" \
    ParameterKey=InferenceApiBaseUrl,ParameterValue=https://pacificchive-ml-chidpnuwue.ap-southeast-1.fcapp.run \
    ParameterKey=InternalApiKey,UsePreviousValue=true

aws cloudformation wait change-set-create-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET"

aws cloudformation describe-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET" \
  --query 'Changes[].ResourceChange.{Action:Action,LogicalId:LogicalResourceId,Type:ResourceType,Replacement:Replacement}' \
  --output table --no-cli-pager

python infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" --stack "$STACK_NAME" \
  --change-set "$UPDATE_CHANGE_SET" --expected-type UPDATE \
  --workdir "$WORK_DIR"
```

验证器必须证明 processed template 没有隐式 `QueryFunctionRole`，`QueryFunction` 仍绑定
`QueryLambdaRole`，且 Lambda、integration、16 条已纳管路由和 `QueryLambdaRole` 都不会
replacement/remove。

> **STOP 2 — 第二次明确批准：** 把 UPDATE change set 的净化结果交给用户。只有用户
> 明确回复批准“执行 UPDATE change set”后才可进入阶段 10。

## 阶段 10/10：批准后执行 UPDATE、验收并迁移 reservations

```bash
# WRITE — 仅在 STOP 2 获得明确批准后执行。
aws cloudformation execute-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET"

aws cloudformation wait stack-update-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME"

aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}' \
  --output table --no-cli-pager

aws apigatewayv2 get-routes \
  --region "$AWS_REGION" --api-id "$API_ID" \
  --query 'sort_by(Items,&RouteKey)[].{Route:RouteKey,Auth:AuthorizationType,Authorizer:AuthorizerId}' \
  --output table --no-cli-pager
```

在浏览器中用 Cognito 已登录会话验证公开路由、上传、查询、编辑、删除和通知；不要把
JWT 或内部 key 复制到 shell。公开路由必须有 JWT，internal 路由在无/错 key 时必须 401，
OPTIONS 不挂 JWT。确认在线图片/视频处理与 C 的 inference endpoint 正常后再迁移旧数据。

reservations 迁移期间，必须暂停所有 Files/Reservations mutation，包括 reserve、上传、
processing/complete/failed 回调和删除，并确认没有请求在途。无法确认则停止。保持暂停状态
完成 verify/backfill/verify：

```bash
python backend/lambdas/query/migrate_reservations.py verify \
  --region "$AWS_REGION" \
  --files-table PacificBioArchiveFiles \
  --reservations-table PacificBioArchiveUploadReservations

# WRITE — 仅在所有 Files/Reservations mutation 仍暂停时创建缺失 claims。
python backend/lambdas/query/migrate_reservations.py backfill \
  --region "$AWS_REGION" \
  --files-table PacificBioArchiveFiles \
  --reservations-table PacificBioArchiveUploadReservations \
  --confirm-uploads-paused

python backend/lambdas/query/migrate_reservations.py verify \
  --region "$AWS_REGION" \
  --files-table PacificBioArchiveFiles \
  --reservations-table PacificBioArchiveUploadReservations
```

只有最后一次 verify 退出 0，且 `claims_missing=0`、`claims_extra=0`，才可恢复流量。

## 当前账号遇到 AlreadyExists 时

如果 stack 为 `UPDATE_ROLLBACK_COMPLETE`，事件中出现 `RouteKey ... already exists`：

1. 不要重试同一 SAM deploy；它仍会尝试创建在线 route。
2. 不要删除 route、integration、Lambda 或 stack。
3. 回到本手册阶段 4，从只读 audit 开始；只有完成 IMPORT 纳管后才能进行正常 UPDATE。
