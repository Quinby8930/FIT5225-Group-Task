# Member D 双 Stack 现有资源纳管手册

> **当前状态：DO NOT EXECUTE — awaiting separate AWS-write approval。**
>
> 本文记录未来获批后的操作顺序。它不是 AWS 写操作授权，也不授权创建、执行或删除
> Change Set。当前实现阶段没有执行任何 AWS 命令。

本手册只适用于已审计的现有账号。线上已有 19 项 Member D query 资源，但它们尚未被
任何 CloudFormation Stack 管理。此前向 `PacificBioArchive-Database` 导入资源的方案已
在真实 CloudFormation 服务端预检中失败，因此不得再向该 Stack 重试 IMPORT。

## 1. 资源所有权边界

双 Stack 是有意设计的资源所有权边界，不是重复部署。

| Stack | 稳定拥有的资源 |
|---|---|
| `PacificBioArchive-Database` | `FilesTable`、`SubscriptionsTable`、`NotificationsTable`、`QueryLambdaRole` |
| `PacificBioArchive-QueryAdoption` | `ReservationsTable`、`QueryFunction`、`QueryIntegration`、16 条 Member D 非 OPTIONS Route |

初始 IMPORT 的 16 条 Route 为：

1. `AuthTestRoute`
2. `QueryByTagsRoute`
3. `QueryBySpeciesRoute`
4. `QueryByThumbnailRoute`
5. `QueryByFileRoute`
6. `EditTagsRoute`
7. `DeleteFilesRoute`
8. `SubscribeRoute`
9. `UnsubscribeRoute`
10. `SubscriptionsRoute`
11. `NotificationsRoute`
12. `ReserveUploadRoute`
13. `AcquireProcessingRoute`
14. `CompleteFileRoute`
15. `FailFileRoute`
16. `AuthorizeAssetsRoute`

初始 IMPORT template 恰好包含上述 19 项，每项都声明
`DeletionPolicy: Retain` 和 `UpdateReplacePolicy: Retain`。它绝不包含数据库 Stack 的
4 项资源、`Outputs`、`InternalApiKey`、SNS 资源、OPTIONS Route 或 Lambda permission。

Query Stack 通过普通、非敏感参数接收经过只读审计的
`ExistingQueryLambdaRoleArn`、`ExistingHttpApiId` 和
`ExistingJwtAuthorizerId`。后续正常 UPDATE 还会接收三张 core table 的名称。它不使用
跨 Stack `Fn::GetAtt`，不新增 Export，也不改变数据库 Stack 的 Outputs。

## 2. 不可突破的安全不变量

- 不删除、不替换真实资源，不修改业务数据。
- 不把线上 drift 复制进维护模板；Query Stack 不修改或“收敛”数据库 Stack 拥有的
  `QueryLambdaRole`。
- 初始 IMPORT 不包含、读取或保存 `InternalApiKey`。该值只允许在日后单独获批的正常
  UPDATE 中，通过 CloudFormation Console 的 `NoEcho` 密码框输入。
- 密钥不得进入 CLI argv、环境变量、文件、snapshot、日志、截图、Git、聊天或群消息。
- 19 项 `resources-to-import` 必须由工具根据新鲜审计自动生成；操作者不得手工输入资源
  标识符。
- 每次 AWS 写操作前都必须有新鲜 validator 结果和独立人工批准。一次批准不能覆盖下一步。
- 失败尝试生成的 `.work` 文件、备份对象引用和 Change Set 结果不得复用。
- 原始对象继续使用稳定的 `user/file` 范围 S3 key。按物种浏览是 DynamoDB tags 构成的
  逻辑目录；不得按物种复制或移动 S3 对象。

## 3. 恢复状态机

任何异常先运行只读 `recovery-report`，再按下表处理。禁止用删除在线资源来清除冲突。

| Target Stack 状态 | 所有权要求 | 安全响应 |
|---|---|---|
| 不存在 | 19 项全部 unmanaged | 可从新鲜 audit 重新 prepare；创建预览仍需单独授权 |
| `REVIEW_IN_PROGRESS` | 通常为 0 项 | 只读检查 Stack、Change Set 和资源；不得重试。只有证明为空壳后，才可另行申请删除批准 |
| IMPORT Change Set 创建失败 | 0 项或未知 | 加 `--import-change-set-creation-failed` 生成 recovery report，丢弃本次工件并停止；不得自动重试 |
| `IMPORT_ROLLBACK_COMPLETE` | 必须重新审计 | 仅生成 recovery report；若有任一资源被管理则停止；若确认为空壳，仅可申请独立清理批准 |
| `IMPORT_ROLLBACK_FAILED` | 未知且不安全 | 冻结自动化，不删除、不重试；保留净化证据并交由 AWS Support/人工恢复评审 |
| `IMPORT_COMPLETE` | Query Stack 精确拥有 19 项 | 立即执行 post-import evidence gate；通过后才是稳定回滚边界 |
| `UPDATE_ROLLBACK_COMPLETE` | 导入的 19 项仍精确归 Query Stack | 完整验证所有权、Lambda runtime/policy/concurrency 和 API Gateway；只有与 `IMPORT_COMPLETE` 边界等价才可接受 |
| 已证明为空的 Target Stack | 0 项且无应用资源 | 只生成清理清单；删除空壳 Stack 必须取得新的明确批准 |

只读恢复命令的真实接口为：

```bash
python infrastructure/member-d/import/prepare_import.py recovery-report \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --target-stack "$TARGET_STACK" \
  --workdir "$WORK_DIR"
```

若 IMPORT Change Set 创建失败，再额外添加
`--import-change-set-creation-failed`。报告只分类；它不删除 Stack 或资源。

## 4. 未来第一次 IMPORT preview

> **DO NOT EXECUTE — awaiting separate AWS-write approval。**
>
> 本节只覆盖：新鲜审计、备份/生成工件、创建 IMPORT preview、验证并汇报。
> 它在执行 IMPORT 之前强制停止，且故意不提供 execute 命令。

### 4.1 新会话或 CloudShell 重连后的初始化

使用 A 的普通 AWS 身份打开 CloudShell；CloudShell 已自动认证，不得创建、索取或粘贴
Root/IAM access key。每次重连后从仓库目录重新运行整个变量块，不得假设旧变量仍存在。
`SOURCE_STACK` 和 `TARGET_STACK` 使用固定字面值，绝不会因占位符未填写而变成空名称。

先把两个尖括号占位符替换为**非敏感**的已批准值；未替换时下面的 `test` 会停止流程。

```bash
set +x
set -euo pipefail
export AWS_PAGER=""
export AWS_REGION=ap-southeast-2

SOURCE_STACK=PacificBioArchive-Database
TARGET_STACK=PacificBioArchive-QueryAdoption
API_ID=2dd2aqb32j
AUTHORIZER_ID=7ir7fs
INTEGRATION_ID=fbjojun
FUNCTION_NAME=PacificBioArchive-QueryLambda
WORK_DIR=infrastructure/member-d/import/.work/query-adoption-first-preview
IMPORT_CHANGE_SET=member-d-query-adoption-import-preview
APPROVED_COMMIT='<eventual-approved-full-commit-sha>'
ARTIFACT_BUCKET='<approved-private-versioned-artifact-bucket>'

test "$SOURCE_STACK" = "PacificBioArchive-Database"
test "$TARGET_STACK" = "PacificBioArchive-QueryAdoption"
test "$SOURCE_STACK" != "$TARGET_STACK"
test "$APPROVED_COMMIT" != '<eventual-approved-full-commit-sha>'
test "$ARTIFACT_BUCKET" != '<approved-private-versioned-artifact-bucket>'
test -n "$APPROVED_COMMIT" && test -n "$ARTIFACT_BUCKET"
```

### 4.2 从全新 checkout 绑定获批 commit

不要复用此前失败方案的 checkout 或 `.work`。在新的目录 clone，然后 detach 到最终获批
的完整 commit SHA：

```bash
cd ~
git clone https://github.com/Quinby8930/FIT5225-Group-Task.git \
  FIT5225-Group-Task-query-adoption-preview
cd FIT5225-Group-Task-query-adoption-preview
git fetch --prune origin
git checkout --detach "$APPROVED_COMMIT"
test "$(git rev-parse HEAD)" = "$APPROVED_COMMIT"
git diff --quiet
git diff --cached --quiet
```

### 4.3 只读核对调用者、区域和当前所有权

下面命令只读。调用者必须是审计工具允许的非 Root IAM 身份；区域固定为
`ap-southeast-2`。数据库 Stack 必须仍精确拥有 4 项，Target Stack 必须不存在：

```bash
ACCOUNT_ID=$(aws sts get-caller-identity \
  --region "$AWS_REGION" --query Account --output text --no-cli-pager)
CALLER_ARN=$(aws sts get-caller-identity \
  --region "$AWS_REGION" --query Arn --output text --no-cli-pager)
test "$CALLER_ARN" = "arn:aws:iam::$ACCOUNT_ID:user/fit5225-cli-deployer"

aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name "$SOURCE_STACK" \
  --query 'Stacks[0].{Name:StackName,Status:StackStatus}' \
  --output table --no-cli-pager

aws cloudformation list-stack-resources \
  --region "$AWS_REGION" --stack-name "$SOURCE_STACK" \
  --query 'StackResourceSummaries[].{LogicalId:LogicalResourceId,Type:ResourceType,PhysicalId:PhysicalResourceId,Status:ResourceStatus}' \
  --output table --no-cli-pager

TARGET_STATUS=$(aws cloudformation list-stacks \
  --region "$AWS_REGION" \
  --query "StackSummaries[?StackName=='PacificBioArchive-QueryAdoption' && StackStatus!='DELETE_COMPLETE'].StackStatus | [0]" \
  --output text --no-cli-pager)
test -z "$TARGET_STATUS" || test "$TARGET_STATUS" = "None"
unset TARGET_STATUS
```

### 4.4 运行本地 Member D 测试

```bash
python -m pytest \
  backend/lambdas/query/tests \
  infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/test_prepare_import.py \
  infrastructure/member-d/test_template.py \
  -q -p no:cacheprovider
```

测试不全绿则停止。测试通过只是本地 validator 证据，不等于 AWS 服务可行性证明。

### 4.5 新鲜 audit 和所有权恢复报告

成功的 `audit` 会遍历全部活动 Stack 和分页资源列表，证明全部 19 个物理资源仍然
unmanaged，并严格核对真实 Role ARN、API ID、authorizer ID、integration ID、function 和
16 条 Route。它只调用只读 AWS API，并只写净化后的本地 snapshot：

```bash
python infrastructure/member-d/import/prepare_import.py audit \
  --region "$AWS_REGION" \
  --stack "$SOURCE_STACK" \
  --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR"

python infrastructure/member-d/import/prepare_import.py recovery-report \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --target-stack "$TARGET_STACK" \
  --workdir "$WORK_DIR"
```

报告必须把 target 分类为 `prepare`，且 source 仍是精确四资源边界；否则停止。

### 4.6 新鲜 prepare（未来单独获批的 AWS 写操作）

`prepare` 会再次采集完整只读证据，然后把当前 Query Lambda zip 以内容寻址方式上传到
已批准、私有、加密且启用版本的 artifact bucket。这个 versioned Lambda backup 会执行
`s3:PutObject`，因此 **prepare 本身也必须先取得 AWS 写批准**；当前批准不包含它。

```bash
# FUTURE WRITE — 仅在 prepare 获得独立批准后运行。
python infrastructure/member-d/import/prepare_import.py prepare \
  --region "$AWS_REGION" \
  --stack "$SOURCE_STACK" \
  --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" \
  --artifact-bucket "$ARTIFACT_BUCKET" \
  --workdir "$WORK_DIR"
```

必须得到下面四个新生成文件，且不得编辑：

- `sanitized-snapshot.json`
- `import-template.json`
- `resources-to-import.json`
- `import-parameters.json`

`resources-to-import.json` 已含全部 19 项真实标识符。操作者只传入这个文件，绝不手工逐项
填写。`import-template.json` 没有 Outputs 或 secret parameter；`import-parameters.json`
只有三个经过审计的普通参数值。

### 4.7 创建并验证第一次 IMPORT preview

该写操作也需要与 prepare 分开的明确批准。它只能以新 Query Stack 为目标：

```bash
# FUTURE WRITE — 只创建 preview；不执行 IMPORT。
set +e
aws cloudformation create-change-set \
  --region "$AWS_REGION" \
  --stack-name "$TARGET_STACK" \
  --change-set-name "$IMPORT_CHANGE_SET" \
  --change-set-type IMPORT \
  --template-body "file://$WORK_DIR/import-template.json" \
  --resources-to-import "file://$WORK_DIR/resources-to-import.json" \
  --parameters "file://$WORK_DIR/import-parameters.json"
CREATE_RC=$?
set -e

if [ "$CREATE_RC" -ne 0 ]; then
  python infrastructure/member-d/import/prepare_import.py recovery-report \
    --region "$AWS_REGION" \
    --source-stack "$SOURCE_STACK" \
    --target-stack "$TARGET_STACK" \
    --workdir "$WORK_DIR" \
    --import-change-set-creation-failed
  echo 'STOP: preview creation failed; discard this preparation bundle.' >&2
  exit 1
fi
unset CREATE_RC

set +e
aws cloudformation wait change-set-create-complete \
  --region "$AWS_REGION" \
  --stack-name "$TARGET_STACK" \
  --change-set-name "$IMPORT_CHANGE_SET"
WAIT_RC=$?
set -e

if [ "$WAIT_RC" -ne 0 ]; then
  python infrastructure/member-d/import/prepare_import.py recovery-report \
    --region "$AWS_REGION" \
    --source-stack "$SOURCE_STACK" \
    --target-stack "$TARGET_STACK" \
    --workdir "$WORK_DIR" \
    --import-change-set-creation-failed
  echo 'STOP: preview is not CREATE_COMPLETE; do not retry.' >&2
  exit 1
fi
unset WAIT_RC

aws cloudformation describe-change-set \
  --region "$AWS_REGION" \
  --stack-name "$TARGET_STACK" \
  --change-set-name "$IMPORT_CHANGE_SET" \
  --query 'Changes[].ResourceChange.{Action:Action,LogicalId:LogicalResourceId,Type:ResourceType,Replacement:Replacement}' \
  --output table --no-cli-pager

python infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --stack "$TARGET_STACK" \
  --change-set "$IMPORT_CHANGE_SET" \
  --expected-type IMPORT \
  --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" \
  --artifact-bucket "$ARTIFACT_BUCKET"

echo 'STOP: report the validated 19-Import preview for a new approval.'
```

Validator 必须从 CloudFormation 实际描述中确认：`ChangeSetType=IMPORT`、目标 Stack 精确
为 `PacificBioArchive-QueryAdoption`、恰好 19 个 `Import`、没有 Add/Modify/Remove/Replace、
没有数据库 Stack 资源、没有 Outputs/secret，并在预览后再次证明 19 项仍 unmanaged。

**到这里必须停止并汇报。第一次 preview 流程中不存在执行 IMPORT 的命令。**

## 5. 日后获批执行 IMPORT 后的强制证据门

若未来另行批准并执行该 IMPORT，Stack 到达 `IMPORT_COMPLETE` 后必须立即运行：

```bash
python infrastructure/member-d/import/prepare_import.py verify-post-import \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --target-stack "$TARGET_STACK" \
  --baseline "$WORK_DIR/sanitized-snapshot.json" \
  --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR"
```

该 gate 会要求 Target Stack 精确拥有 19 项，并比较 Lambda 完整配置、环境变量名称和所有
安全值、`CodeSha256`、`RevisionId`、Role、reserved/provisioned concurrency、resource
policy 与 policy revision。因为 `AWS::ApiGatewayV2::Integration` 在此工作流中没有可用
的 CloudFormation drift detection，工具会直接调用 API Gateway 只读 API，逐属性比较
integration 和全部 16 条 Route。任何差异都停止，不得进入 UPDATE。

IMPORT template 暂时省略 `QueryFunction.Environment` 只是避免读取 secret；不能仅凭模板
推断线上 Lambda 未改变。只有上述真实 post-import comparison 能证明这一点。

## 6. 最终正常 UPDATE（仅记录边界，不是操作授权）

最终正常 UPDATE 是另一阶段，必须使用 `infrastructure/member-d/query-adoption.yaml` 新鲜
build/package，单独创建 preview、运行 validator，并再次取得人工批准。若当前 key 未被
Stack 注册，A 只能在 CloudFormation Console 的 `InternalApiKey` `NoEcho` 密码框输入
当前值；不存在 secret-bearing CLI 路径。

第一次正常 UPDATE 的允许变更固定为 37 项：

- `QueryFunction`：唯一 `Modify`，且 `Replacement=False`；
- 10 条 OPTIONS Route：`Add`；
- 26 条 method/path-scoped `AWS::Lambda::Permission`：`Add`。

禁止任何 Remove、Replace、额外 Modify、wildcard permission、数据库 Stack 资源或
`QueryLambdaRole` 变更。该 UPDATE 不新增 SNS Topic/Subscription。DynamoDB
`NotificationsTable` 仍提供 durable in-app inbox；每用户 email 订阅需要另行设计、审查并
批准跨 Stack IAM/SNS 方案。

如果 UPDATE 失败并进入 `UPDATE_ROLLBACK_COMPLETE`，必须运行 recovery report 和完整
runtime/API/ownership 验证。只有确认 19 项导入资源仍精确归 Query Stack、且全部运行时
证据等同 `IMPORT_COMPLETE` 稳定边界，才能把回滚视为成功恢复；否则冻结后续操作。

## 7. 相关文件

- 架构设计：[`../superpowers/specs/2026-09-01-member-d-query-adoption-stack-design.md`](../superpowers/specs/2026-09-01-member-d-query-adoption-stack-design.md)
- 数据库/Query Stack 说明：[`database-setup.md`](database-setup.md)
- core database template：[`../../infrastructure/member-d/dynamodb.yaml`](../../infrastructure/member-d/dynamodb.yaml)
- Query normal-UPDATE template：[`../../infrastructure/member-d/query-adoption.yaml`](../../infrastructure/member-d/query-adoption.yaml)
- adoption 工具：[`../../infrastructure/member-d/import/prepare_import.py`](../../infrastructure/member-d/import/prepare_import.py)
