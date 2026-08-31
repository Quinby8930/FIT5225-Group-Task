# Member D 现有 AWS 资源纳管手册

本手册只适用于当前 AWS 账号：`PacificBioArchive-QueryLambda`、单一 API Gateway
integration、16 条 Member D 非 OPTIONS 路由和未被任何 stack 管理的
`PacificBioArchiveUploadReservations` 表已经在线。`QueryLambdaRole` 还存在已审计的
reservation-only 权限漂移。目标是先通过 IMPORT 在不替换、不删除、不修改在线资源的
前提下原样纳管这 19 个资源，再通过独立 UPDATE 将角色收敛到当前模板的规范权限。

如果是一个没有上述 Member D 资源的全新普通 AWS 账号，不要使用本手册；按
[`database-setup.md`](database-setup.md) 的“全新账号”路径正常部署 SAM。

## 不可突破的安全边界

- 所有 AWS 写命令都标为 `WRITE`。其中 artifact 上传和 change set 创建不改变在线运行时，
  仍须由当前人工操作者明确选择运行；IMPORT 执行、首次 UPDATE 执行、精确移除旧宽泛
  Lambda permission、reservations backfill、Member B 部署，以及最终关闭兼容开关的 UPDATE
  执行则各有独立 `STOP`。任何一次批准都不能自动覆盖后续写操作。
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
export AWS_PAGER=""
STACK_NAME=PacificBioArchive-Database
API_ID=2dd2aqb32j
AUTHORIZER_ID=7ir7fs
INTEGRATION_ID=fbjojun
FUNCTION_NAME=PacificBioArchive-QueryLambda
ROLE_NAME=PacificBioArchive-QueryLambdaRole
WORK_DIR=infrastructure/member-d/import/.work
IMPORT_CHANGE_SET=member-d-adopt-existing
UPDATE_CHANGE_SET=member-d-deploy-current
HARDEN_CHANGE_SET=member-d-disable-legacy-callbacks
INFERENCE_API_BASE_URL=https://pacificchive-ml-chidpnuwue.ap-southeast-1.fcapp.run
ALLOW_LEGACY_PROCESSING_CALLBACKS=true

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
DEPLOYMENT_COMMIT=$(git rev-parse HEAD)
test "$DEPLOYMENT_COMMIT" = "$(git rev-parse origin/main)"
git diff --quiet
git diff --cached --quiet

python -m pytest infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/test_prepare_import.py \
  infrastructure/member-d/test_template.py -q
```

测试不全绿则停止。不要为了继续部署而跳过或修改测试。

## 阶段 4/10：只读审计现有资源

`audit` 只调用只读 AWS API，并在本地写入已净化 snapshot。参数名以当前工具的真实 CLI
接口为准：

开始前必须由账号操作者冻结对
`PacificBioArchiveUploadReservations` 的 `PutResourcePolicy` / `DeleteResourcePolicy` 操作，
直到阶段 9 的首次 UPDATE validator、首次 UPDATE 执行以及阶段 10 紧随其后的更新后复核
全部结束。阶段 9 会再次采集在线状态，因此不能在 IMPORT 验收后提前解除冻结。AWS 明确
说明 `GetResourcePolicy` 最终一致且没有承诺
最大传播时间；工具会在 30 秒稳定窗口内做三次 absence 确认，但这不能替代变更冻结。
无法保证没有其他人/自动化修改该 policy 时，本流程必须停止。

```bash
python infrastructure/member-d/import/prepare_import.py audit \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" --workdir "$WORK_DIR"
```

它必须验证：调用者、stack 状态、Lambda/role/resource policy、integration、16 条 Member D
非 OPTIONS 路由、authorizer、其他 stack 所有权和 CloudFormation import identifiers。
`ReservationsTable` 必须属于同一账号和区域、状态为 `ACTIVE`、按需计费，且只有字符串
HASH 主键 `reservation_key`；普通/向量索引、stream、replica/global witness、TTL、PITR、
标签、加密模式、删除保护、resource policy、有效 Kinesis streaming destination 或
Contributor Insights 出现无法由当前模板原样
表达的差异时必须停止。`QueryLambdaRole` 只允许 `IN_SYNC`，或精确
匹配已知 reservation-only 漂移：`QueryServiceAccess` 仅增加
`dynamodb:TransactWriteItems` 和 reservation 表 ARN，并额外存在内容完全匹配的
`UploadReservationsAccess`。任何其他 path、action、resource 或 policy 差异都必须失败关闭。
工具不会选择 Member B 的 `/upload-url`、`/asset-urls`，也不会选择 OPTIONS 路由。

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
  --api "$API_ID" --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" --artifact-bucket "$ARTIFACT_BUCKET"
```

验证器会重新只读采集在线状态，并与旧 snapshot 的运行时指纹比较；随后只信任这次实时
结果，从 Lambda `CodeSha256` 重建内容寻址 S3 key 和完整 IMPORT template，再要求本地
template、CloudFormation processed template
三者完全一致，并用 `head-object` 证明精确 S3 version 的 `ChecksumSHA256` 等于审计值。
change set 参数还必须与从 snapshot 重建的参数键集合一致，
且全部只有 `UsePreviousValue=true`、没有明文值；然后确认恰好 19 个 `Import`：1 张
`ReservationsTable`、1 个 Lambda、1 个 integration、16 条 Member D 非 OPTIONS 路由；
不得出现 Add/Modify/Remove、`QueryLambdaRole`、Member B 路由或 OPTIONS 路由。

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

`audit --baseline` 必须证明 Lambda 完整配置与 resource policy、integration、16 条路由和
`ReservationsTable` 保持不变，并确认 stack 已从原 4 个资源扩展为原 4 个加 19 个纳管资源，
共 23 个。`QueryLambdaRole` 只允许保持审计前记录的完全相同 reservation-only 漂移；漂移
扩大、缩小、改变，或出现任何其他运行时差异时停止，不进入 UPDATE。

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

test -n "$MEDIA_BUCKET" && test "$MEDIA_BUCKET" != "None"
test -n "$STORAGE_DELETE_FUNCTION_NAME" && \
  test "$STORAGE_DELETE_FUNCTION_NAME" != "None"

# 本地构建 — SAM 在受控目录中安装 requirements.txt 依赖；不会改变 AWS。
sam build --template-file infrastructure/member-d/dynamodb.yaml \
  --build-dir "$WORK_DIR/sam-build"
test -f "$WORK_DIR/sam-build/template.yaml"

# WRITE — 把精确 build tree 打成确定性 zip，上传内容寻址对象并固定 S3 VersionId；
# 不会创建或执行 CloudFormation change set。
python infrastructure/member-d/import/prepare_import.py package-update \
  --region "$AWS_REGION" --artifact-bucket "$ARTIFACT_BUCKET" \
  --built-template "$WORK_DIR/sam-build/template.yaml" \
  --built-code-dir "$WORK_DIR/sam-build/QueryFunction" \
  --source-code-dir backend/lambdas/query \
  --dependency-manifest infrastructure/member-d/import/member-d-query-build.lock.json \
  --expected-commit "$DEPLOYMENT_COMMIT" \
  --output-template "$WORK_DIR/packaged-template.yaml"

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
    ParameterKey=InferenceApiBaseUrl,ParameterValue="$INFERENCE_API_BASE_URL" \
    ParameterKey=AllowLegacyProcessingCallbacks,ParameterValue="$ALLOW_LEGACY_PROCESSING_CALLBACKS" \
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
  --api "$API_ID" --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" \
  --artifact-bucket "$ARTIFACT_BUCKET" \
  --built-template "$WORK_DIR/sam-build/template.yaml" \
  --built-code-dir "$WORK_DIR/sam-build/QueryFunction" \
  --source-code-dir backend/lambdas/query \
  --dependency-manifest infrastructure/member-d/import/member-d-query-build.lock.json \
  --expected-commit "$DEPLOYMENT_COMMIT" \
  --packaged-template "$WORK_DIR/packaged-template.yaml" \
  --expected-http-api-id "$API_ID" \
  --expected-jwt-authorizer-id "$AUTHORIZER_ID" \
  --expected-query-input-bucket "$MEDIA_BUCKET" \
  --expected-storage-delete-function "$STORAGE_DELETE_FUNCTION_NAME" \
  --expected-inference-api-base-url "$INFERENCE_API_BASE_URL" \
  --expected-allow-legacy-processing-callbacks "$ALLOW_LEGACY_PROCESSING_CALLBACKS" \
  --expect-role-reconciliation true
```

验证器必须先重新采集在线状态，再证明仓库源模板、SAM built template、内容寻址且固定
VersionId 的 packaged template 和 CloudFormation processed template 逐层对应；它要求
所有一方源码、维护模板及 dependency manifest 精确来自干净的 `$DEPLOYMENT_COMMIT`。
`requirements.txt` 固定完整 CPython 3.12/x86_64 依赖及 wheel SHA-256；build 中每个会被
打包的依赖文件还必须与 commit 内受审 manifest 的路径、大小和 SHA-256 完全一致，不能
用 build 内可同步伪造的 `.dist-info/RECORD` 自证。验证器随后重新生成确定性 zip，并通过
`head-object` 核对精确版本的 SHA-256。Lambda 的
processed `Code` 必须精确指向该固定版本，顶层 Parameters、
Conditions、Outputs 没有被替换或增加，且 change set 参数与上面人工确认的值逐项相同；
`InternalApiKey` 只能 `UsePreviousValue=true`，不能出现明文值。随后还必须证明 processed
template 没有隐式 `QueryFunctionRole`，`QueryFunction` 仍绑定
`QueryLambdaRole`，且 Lambda、integration、16 条已纳管路由、`ReservationsTable` 和
`QueryLambdaRole` 都不会 replacement/remove。若 snapshot 记录了允许的 reservation-only
漂移，UPDATE 对 `QueryLambdaRole` 只能是 `Modify / Replacement=False`，并且目标必须精确
收敛为模板中的单一规范 `QueryServiceAccess`；不得接受其他 IAM action、resource、policy、
boundary、tag 或 role 属性变化。此次 UPDATE 不只是修角色：它还更新 Lambda code/config，
新增 SNS Topic、OPTIONS routes、逐路由 invoke permissions 等模板资源，因此必须逐项审查。

这里明确使用 `AllowLegacyProcessingCallbacks=true`，因为当前 Member B 是否已经全部转发
lease token 尚未由运行证据证明。该值只用于安全滚动升级，稳定态必须在最后单独 UPDATE
为 `false`。

> **STOP 2 — 第二次明确批准：** 把 UPDATE change set 的净化结果交给用户。只有用户
> 明确回复批准“执行 UPDATE change set”后才可进入阶段 10。

## 阶段 10/10：执行首次 UPDATE、收窄 permission、迁移并关闭兼容开关

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

aws cloudformation detect-stack-resource-drift \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --logical-resource-id QueryLambdaRole \
  --query 'StackResourceDrift.{Status:StackResourceDriftStatus,Differences:PropertyDifferences}' \
  --output json --no-cli-pager

aws cloudformation describe-stack-resource \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --logical-resource-id ReservationsTable \
  --query 'StackResourceDetail.{CloudFormationStatus:ResourceStatus,PhysicalId:PhysicalResourceId}' \
  --output table --no-cli-pager

aws dynamodb describe-table \
  --region "$AWS_REGION" \
  --table-name PacificBioArchiveUploadReservations \
  --query 'Table.{Name:TableName,Status:TableStatus,Arn:TableArn}' \
  --output table --no-cli-pager
```

UPDATE 完成后，`QueryLambdaRole` 必须为 `IN_SYNC` 且没有 `PropertyDifferences`；
CloudFormation 的 `PhysicalId` 必须仍为 `PacificBioArchiveUploadReservations`，随后 DynamoDB
查询中的 `Status` 必须为 `ACTIVE`。CloudFormation 的 `ResourceStatus` 不能替代 DynamoDB 的
`TableStatus`。任一条件不满足时，不得恢复流量或开始迁移。

首次 UPDATE 会新增 26 条由 CloudFormation 管理的逐路由 invoke permissions，但不会自动
删除 IMPORT 前审计到的那一条 stack 外宽泛 permission。先用仓库验证器证明在线 policy
恰好是“26 条 scoped + 原审计的 1 条 legacy”。验证器从同一次在线 policy 响应原子输出
已经验证过的 Sid 与 RevisionId；禁止再从 snapshot 或第二次查询重取 Sid：

```bash
POLICY_GUARD=$(python infrastructure/member-d/import/prepare_import.py validate-lambda-policy \
  --region "$AWS_REGION" --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" --expect-legacy present --emit-revision)
readarray -t POLICY_GUARD_FIELDS < <(
  printf '%s' "$POLICY_GUARD" | python -c \
    'import json,sys; value=json.load(sys.stdin); print(value["legacy_sid"]); print(value["revision_id"])'
)
unset POLICY_GUARD
test "${#POLICY_GUARD_FIELDS[@]}" -eq 2
LEGACY_STATEMENT_ID="${POLICY_GUARD_FIELDS[0]}"
POLICY_REVISION_ID="${POLICY_GUARD_FIELDS[1]}"
test -n "$LEGACY_STATEMENT_ID"
test -n "$POLICY_REVISION_ID"
```

> **STOP 3 — 单独批准 permission 收窄：** 只有用户明确批准“精确移除已审计的 legacy
> Lambda statement `$LEGACY_STATEMENT_ID`”后，才能运行下一条命令。不能按前缀、模糊匹配
> 或批量删除其他 statement。

```bash
# WRITE — 只移除上一步审计并验证过的单一 legacy Sid。
aws lambda remove-permission \
  --region "$AWS_REGION" --function-name "$FUNCTION_NAME" \
  --statement-id "$LEGACY_STATEMENT_ID" \
  --revision-id "$POLICY_REVISION_ID"

python infrastructure/member-d/import/prepare_import.py validate-lambda-policy \
  --region "$AWS_REGION" --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" --expect-legacy absent
```

第二次验证必须证明没有任何无 `SourceArn` 的宽泛 statement，并且 26 条 scoped permissions
与模板的 16 条业务路由和 10 条 OPTIONS 路由逐一完全对应；否则停止。

在浏览器中用 Cognito 已登录会话验证公开路由、上传、查询、编辑、删除和通知；不要把
JWT 或内部 key 复制到 shell。公开路由必须有 JWT，internal 路由在无/错 key 时必须 401，
OPTIONS 不挂 JWT。确认在线图片/视频处理与 C 的 inference endpoint 正常后再迁移旧数据。

reservations 迁移期间，必须暂停所有 Files/Reservations mutation，包括 reserve、上传、
processing/complete/failed 回调和删除，并确认没有请求在途。无法确认则停止。保持暂停状态
先执行只读 verify：

```bash
python backend/lambdas/query/migrate_reservations.py verify \
  --region "$AWS_REGION" \
  --files-table PacificBioArchiveFiles \
  --reservations-table PacificBioArchiveUploadReservations
```

若 verify 已是 `claims_missing=0`、`claims_extra=0`，不要运行 backfill。若存在缺失 claim：

> **STOP 4 — 单独批准数据回填：** 保持所有 mutation 暂停，提交 verify 的净化统计；只有
> 用户明确批准“执行 reservations backfill”后，才可运行下面的写命令。

```bash
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

此时 Member D 仍临时允许旧 callback。下一步由成员 A 按
[`docs/member-b/manual-aws-steps.md`](../member-b/manual-aws-steps.md) 部署前面固定的
`$DEPLOYMENT_COMMIT`；不要在中途再次 `git pull` 或换 commit。无论该手册的 guided prompt
如何显示，对 `Save arguments to configuration file` 都回答 `N`，不得把共享 key 写入
`samconfig.toml`。部署后用新上传证明 processing 返回的 `lease_token` 被 complete/failed
原样转发；不能只凭代码版本号推断线上已升级。

> **STOP 5 — Member B 部署属于独立 AWS 写操作：** 必须按 Member B 手册单独审查和批准；
> 本手册不替它执行或扩大授权。验证新 callback 确实带 token 后才能继续。

Member B 部署完成后重新读取并验证输出，不能复用部署前的 shell 值：

```bash
test "$(git rev-parse HEAD)" = "$DEPLOYMENT_COMMIT"
git diff --quiet
git diff --cached --quiet

MEDIA_BUCKET=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name PacificBioArchive-Media \
  --query "Stacks[0].Outputs[?OutputKey=='MediaBucketName'].OutputValue | [0]" \
  --output text)
STORAGE_DELETE_FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name PacificBioArchive-Media \
  --query "Stacks[0].Outputs[?OutputKey=='StorageDeleteFunctionName'].OutputValue | [0]" \
  --output text)
test -n "$MEDIA_BUCKET" && test "$MEDIA_BUCKET" != "None"
test -n "$STORAGE_DELETE_FUNCTION_NAME" && \
  test "$STORAGE_DELETE_FUNCTION_NAME" != "None"
```

最后创建一个只把兼容开关改为 `false` 的独立 hardening UPDATE。复用已经校验的 built 和
packaged template；不要重新打包或改其他参数：

```bash
ALLOW_LEGACY_PROCESSING_CALLBACKS=false

# WRITE — 只创建 hardening change set；不要执行。
aws cloudformation create-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$HARDEN_CHANGE_SET" --change-set-type UPDATE \
  --template-body "file://$WORK_DIR/packaged-template.yaml" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters \
    ParameterKey=ExistingHttpApiId,ParameterValue="$API_ID" \
    ParameterKey=ExistingJwtAuthorizerId,ParameterValue="$AUTHORIZER_ID" \
    ParameterKey=QueryInputBucketName,ParameterValue="$MEDIA_BUCKET" \
    ParameterKey=StorageDeleteFunctionName,ParameterValue="$STORAGE_DELETE_FUNCTION_NAME" \
    ParameterKey=InferenceApiBaseUrl,ParameterValue="$INFERENCE_API_BASE_URL" \
    ParameterKey=AllowLegacyProcessingCallbacks,ParameterValue=false \
    ParameterKey=InternalApiKey,UsePreviousValue=true

aws cloudformation wait change-set-create-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$HARDEN_CHANGE_SET"

aws cloudformation describe-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$HARDEN_CHANGE_SET" \
  --query 'Changes[].ResourceChange.{Action:Action,LogicalId:LogicalResourceId,Type:ResourceType,Replacement:Replacement}' \
  --output table --no-cli-pager

python infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" --stack "$STACK_NAME" \
  --change-set "$HARDEN_CHANGE_SET" --expected-type UPDATE \
  --workdir "$WORK_DIR" \
  --artifact-bucket "$ARTIFACT_BUCKET" \
  --built-template "$WORK_DIR/sam-build/template.yaml" \
  --built-code-dir "$WORK_DIR/sam-build/QueryFunction" \
  --source-code-dir backend/lambdas/query \
  --dependency-manifest infrastructure/member-d/import/member-d-query-build.lock.json \
  --expected-commit "$DEPLOYMENT_COMMIT" \
  --packaged-template "$WORK_DIR/packaged-template.yaml" \
  --expected-http-api-id "$API_ID" \
  --expected-jwt-authorizer-id "$AUTHORIZER_ID" \
  --expected-query-input-bucket "$MEDIA_BUCKET" \
  --expected-storage-delete-function "$STORAGE_DELETE_FUNCTION_NAME" \
  --expected-inference-api-base-url "$INFERENCE_API_BASE_URL" \
  --expected-allow-legacy-processing-callbacks false \
  --expect-role-reconciliation false
```

验证器会重新读取当前 stack 的 processed template、参数、Lambda `CodeSha256`，并对
`QueryFunction` 单独执行 drift detection。只有当前函数为 `IN_SYNC`、当前与候选 template
完全相同、代码仍是同一固定 S3 版本、所有有效参数都不变且唯一差异为
`AllowLegacyProcessingCallbacks: true → false`，同时 change set 恰好只有一条
`QueryFunction / Modify / Replacement=False` 时才通过；任何其他代码、配置、role、资源或
参数变化都必须失败。

> **STOP 6 — 单独批准关闭兼容开关：** 只有用户明确批准执行该 hardening UPDATE 后继续。

```bash
# WRITE — 仅在 STOP 6 明确批准后执行。
aws cloudformation execute-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$HARDEN_CHANGE_SET"

aws cloudformation wait stack-update-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME"

python infrastructure/member-d/import/prepare_import.py validate-lambda-policy \
  --region "$AWS_REGION" --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" --expect-legacy absent
```

最后再测试：无 token 的 complete/failed 必须被拒绝，带当前 lease token 的回调成功；公开
路由仍要求 JWT，internal 路由仍要求正确内部 key。全部通过后兼容升级才算结束。

## 当前账号遇到 AlreadyExists 时

如果 stack 为 `UPDATE_ROLLBACK_COMPLETE`，事件中出现 `RouteKey ... already exists`：

1. 不要重试同一 SAM deploy；它仍会尝试创建在线 route。
2. 不要删除 route、integration、Lambda 或 stack。
3. 回到本手册阶段 4，从只读 audit 开始；只有完成 IMPORT 纳管后才能进行正常 UPDATE。
