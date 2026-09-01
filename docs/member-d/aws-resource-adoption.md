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
- 初始 IMPORT 的 Python、template 和工件不接收、引用或保存 `InternalApiKey`。AWS
  Lambda 不支持服务端字段投影：可信 AWS CLI 子进程会收到完整 function-configuration
  响应，再由 CLI 侧 JMESPath `--query` 在 stdout 到达 Python 前移除 secret value。因此
  Python、工件、日志、截图、argv 和操作者界面都不接收、保存或显示该值。此子进程信任
  边界仍须用户在任何未来 AWS 步骤前明确接受。该值只允许在日后单独获批的正常 UPDATE
  中，通过 CloudFormation Console 的 `NoEcho` 密码框输入。
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
| `UPDATE_ROLLBACK_COMPLETE` | 导入的 19 项仍精确归 Query Stack | 运行 `verify-update-rollback`，把完整所有权、Lambda runtime/policy/concurrency 和 API Gateway 证据与保存的 `IMPORT_COMPLETE` baseline 比较；只有完全等价才可接受 |
| 已证明为空的 Target Stack | 0 项且无应用资源 | 只生成清理清单；删除空壳 Stack 必须取得新的明确批准 |

只读恢复命令的真实接口为：

```bash
python3 -B -E -S infrastructure/member-d/import/prepare_import.py recovery-report \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --target-stack "$TARGET_STACK" \
  --expected-commit "$APPROVED_COMMIT" \
  --workdir "$WORK_DIR"
```

若 IMPORT Change Set 创建失败，再额外添加
`--import-change-set-creation-failed`。报告只分类；它不删除 Stack 或资源。

## 4. 未来第一次 IMPORT preview

> **DO NOT EXECUTE — awaiting separate AWS-write approval。**
>
> 本节只覆盖：新鲜审计、备份/生成工件、创建 IMPORT preview、验证并汇报。
> 它在执行 IMPORT 之前强制停止，且故意不提供 execute 命令。

### 4.1 新 audit attempt 的初始化

使用 A 的普通 AWS 身份打开 CloudShell；CloudShell 已自动认证，不得创建、索取或粘贴
Root/IAM access key。下面的变量块只用于开始一次**全新的** audit attempt，不用于恢复旧
attempt；不得假设旧变量仍存在。
`SOURCE_STACK` 和 `TARGET_STACK` 使用固定字面值，绝不会因占位符未填写而变成空名称。

先把两个尖括号占位符替换为**非敏感**的已批准值；未替换时下面的 `test` 会停止流程。

```bash
set +x
set +e
set -u
set -o pipefail
export AWS_PAGER=""
export AWS_REGION=ap-southeast-2

SOURCE_STACK=PacificBioArchive-Database
TARGET_STACK=PacificBioArchive-QueryAdoption
API_ID=2dd2aqb32j
AUTHORIZER_ID=7ir7fs
INTEGRATION_ID=fbjojun
FUNCTION_NAME=PacificBioArchive-QueryLambda
IMPORT_CHANGE_SET=member-d-query-adoption-import-preview
APPROVED_COMMIT='<eventual-approved-full-commit-sha>'
ARTIFACT_BUCKET='<approved-private-versioned-artifact-bucket>'
ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
WORK_DIR="$HOME/member-d-query-adoption-$APPROVED_COMMIT-$ATTEMPT_ID"
CHECKOUT_DIR="$HOME/FIT5225-Group-Task-query-adoption-$ATTEMPT_ID"
INIT_SUCCEEDED=false
CHECKOUT_SUCCEEDED=false
READ_ONLY_CONTEXT_SUCCEEDED=false
TEST_EVIDENCE_APPROVED=false

if test "$SOURCE_STACK" = "PacificBioArchive-Database" &&
   test "$TARGET_STACK" = "PacificBioArchive-QueryAdoption" &&
   test "$SOURCE_STACK" != "$TARGET_STACK" &&
   test "$APPROVED_COMMIT" != '<eventual-approved-full-commit-sha>' &&
   test "$ARTIFACT_BUCKET" != '<approved-private-versioned-artifact-bucket>' &&
   test -n "$APPROVED_COMMIT" && test -n "$ARTIFACT_BUCKET" &&
   test "${#APPROVED_COMMIT}" -eq 40 &&
   printf '%s' "$APPROVED_COMMIT" | grep -Eq '^[0-9a-f]{40}$' &&
   test ! -e "$WORK_DIR" && test ! -e "$CHECKOUT_DIR"
then
  INIT_SUCCEEDED=true
  printf 'Fresh attempt workdir (retain for recovery): %s\n' "$WORK_DIR"
else
  printf 'STOP: initialization or fresh-path gate failed; do not continue.\n' >&2
fi
```

这里故意不启用顶层 `set -e`：后续只读 audit 的非零退出必须由条件块捕获并保留当前
CloudShell 会话、变量和诊断上下文。初始化只在全部断言成功后把
`INIT_SUCCEEDED` 设为 `true`；否则后续代码块会显式拒绝运行。不得用忽略失败或重新设置
状态变量的方式绕过门禁。每个新 attempt 的 `WORK_DIR` 和 checkout 都
包含新的 UTC 时间和 shell PID，并在 audit 前强制要求尚不存在，防止同一 commit 静默复用
旧 snapshot。`WORK_DIR` 故意位于 Git checkout 之外：audit 生成 snapshot 后，紧接着运行的
recovery process 仍能重新证明仓库处于同一完整 commit 且 clean；不得把它改回仓库中被
`.gitignore` 隐藏的 `.work` 目录。

若 CloudShell 在 audit **已生成**本次工件后重连，禁止重新运行上面的新-attempt 块，也
禁止生成新 `ATTEMPT_ID`。先从先前终端记录恢复屏幕上打印的**精确完整** `WORK_DIR`，再
重新设置其余非敏感变量并运行 `test -d "$WORK_DIR"`；随后只可针对该路径运行第 3 节的
只读 `recovery-report`。找不到精确路径、目录不存在或不确定它属于哪次 attempt 时立即
**STOP**，不得猜测、扫描其他 attempt、复用旧工件或继续 prepare。

### 4.2 从全新 checkout 绑定获批 commit

不要复用此前失败方案的 checkout 或 `.work`。在新的目录 clone，然后 detach 到最终获批
的完整 commit SHA：

```bash
if [ "$INIT_SUCCEEDED" != true ]
then
  printf 'STOP: initialization gate did not pass; no checkout was created.\n' >&2
elif cd "$HOME" &&
     git clone https://github.com/Quinby8930/FIT5225-Group-Task.git "$CHECKOUT_DIR" &&
     cd "$CHECKOUT_DIR" &&
     git fetch --prune origin &&
     git checkout --detach "$APPROVED_COMMIT" &&
     test "$(git rev-parse HEAD)" = "$APPROVED_COMMIT" &&
     test -z "$(git status --porcelain=v1 --untracked-files=all)" &&
     test -z "$(git ls-files --others --ignored --exclude-standard)"
then
  CHECKOUT_SUCCEEDED=true
  printf 'Fresh checkout is bound to %s.\n' "$APPROVED_COMMIT"
else
  printf 'STOP: fresh checkout or repository identity gate failed.\n' >&2
fi
```

### 4.3 只读核对调用者、区域和当前所有权

下面命令只读。调用者必须是审计工具允许的非 Root IAM 身份；区域固定为
`ap-southeast-2`。数据库 Stack 必须仍精确拥有 4 项，Target Stack 必须不存在：

```bash
if [ "$CHECKOUT_SUCCEEDED" != true ]
then
  printf 'STOP: checkout gate did not pass; no AWS read was attempted.\n' >&2
elif ACCOUNT_ID=$(aws sts get-caller-identity \
       --region "$AWS_REGION" --query Account --output text --no-cli-pager) &&
     CALLER_ARN=$(aws sts get-caller-identity \
       --region "$AWS_REGION" --query Arn --output text --no-cli-pager) &&
     test "$CALLER_ARN" = "arn:aws:iam::$ACCOUNT_ID:user/fit5225-cli-deployer" &&
     aws cloudformation describe-stacks \
       --region "$AWS_REGION" --stack-name "$SOURCE_STACK" \
       --query 'Stacks[0].{Name:StackName,Status:StackStatus}' \
       --output table --no-cli-pager &&
     aws cloudformation list-stack-resources \
       --region "$AWS_REGION" --stack-name "$SOURCE_STACK" \
       --query 'StackResourceSummaries[].{LogicalId:LogicalResourceId,Type:ResourceType,PhysicalId:PhysicalResourceId,Status:ResourceStatus}' \
       --output table --no-cli-pager &&
     TARGET_STATUS=$(aws cloudformation list-stacks \
       --region "$AWS_REGION" \
       --query "StackSummaries[?StackName=='PacificBioArchive-QueryAdoption' && StackStatus!='DELETE_COMPLETE'].StackStatus | [0]" \
       --output text --no-cli-pager) &&
     { test -z "$TARGET_STATUS" || test "$TARGET_STATUS" = "None"; }
then
  READ_ONLY_CONTEXT_SUCCEEDED=true
  printf 'Caller, source Stack and absent target checks passed.\n'
else
  printf 'STOP: caller, source Stack or target ownership gate failed.\n' >&2
fi
unset TARGET_STATUS
```

### 4.4 核对外部完整测试证据；CloudShell 不运行测试

本方案选择“完整测试证据绑定精确 commit SHA”，不使用 CloudShell 作为测试环境。AWS
说明 CloudShell 会持续更新预装软件，且只承诺 Python 3、Git 等工具可用，不承诺固定的
Python 3.12 或预装 pytest（[CloudShell 软件规格][cloudshell-software]）。因此不得在
CloudShell 运行 pytest、`pip install`、创建 venv、修改系统 Python，或把 CloudShell 的
Python 版本当成 Lambda 运行时测试证据。

在打开 CloudShell **之前**，批准人必须保存由受控本地环境或 CI 生成的完整测试记录。
记录至少包含：

- `git rev-parse HEAD` 输出的完整 40 位 SHA，且精确等于 `APPROVED_COMMIT`；
- 测试开始时 clean worktree 的证明；
- Python、Node/npm 版本，以及 `backend/lambdas/query/requirements.txt`、
  `infrastructure/member-d/import/member-d-query-build.lock.json` 和
  `frontend/package-lock.json` 的 SHA-256；
- 完整测试/构建命令、退出码和未截断结果；
- 证据生成时间与保存位置。

当前要求保存的完整命令集合如下；它们只在受控本地/CI checkout 中执行，不得复制到
CloudShell：

```bash
(cd backend/ml-inference && python -m pytest -q -p no:cacheprovider)
(cd backend/lambdas/query && python -m pytest -q -p no:cacheprovider)
(cd backend/lambdas/media-processing && python -m pytest -q -p no:cacheprovider)
python -m pytest -q -p no:cacheprovider infrastructure/member-b/test_template.py
python -m pytest -q -p no:cacheprovider \
  infrastructure/member-d/test_template.py \
  infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/test_prepare_import.py \
  infrastructure/member-d/import/test_yaml_audit.py \
  infrastructure/member-d/import/test_build_yaml_audit_artifact.py \
  infrastructure/member-d/import/test_runtime_gate.py
npm --prefix frontend test
npm --prefix frontend run build
```

GitHub 也明确建议用 commit ID（而非会移动的分支名）固定文件版本
（[GitHub permanent links][github-permalinks]）。证据不存在、SHA 不是完整 40 位、
证据 SHA 与 `APPROVED_COMMIT` 不同，或任一测试/构建失败时，明确 **STOP**；不得进入
CloudShell audit。CloudShell 的 §4.2 只证明当前 checkout 与已批准证据 SHA 相同且干净，
不会重新证明测试通过。

批准人逐项核对并保存全部证据后，才可在当前 shell 明确设置：

```bash
TEST_EVIDENCE_APPROVED=true
```

不得预先设置、代替批准人设置或在证据缺失时设置此变量。

### 4.5 新鲜 audit 和所有权恢复报告

成功的 `audit` 会遍历全部活动 Stack 和分页资源列表，证明全部 19 个物理资源仍然
unmanaged，并严格核对真实 Role ARN、API ID、authorizer ID、integration ID、function 和
16 条 Route。它只调用只读 AWS API，并只写净化后的本地 snapshot：

不要在顶层 `set -e` 下裸跑 `audit`：非零退出会直接关闭当前 CloudShell 命令会话，并
丢失本节需要保留的变量和诊断上下文。下面的条件块会捕获退出码；失败时明确输出
`STOP`、保留当前 shell 与变量，并且不会运行 recovery report。看到 `STOP` 后不得继续
任何后续 prepare 或 Change Set 步骤。

```bash
AUDIT_SUCCEEDED=false
RECOVERY_REPORT_SUCCEEDED=false

if [ "$READ_ONLY_CONTEXT_SUCCEEDED" != true ] || \
   [ "$TEST_EVIDENCE_APPROVED" != true ]
then
  printf 'STOP: checkout/caller/ownership or external-test evidence gate did not pass.\n' >&2
elif ! command -v python3 >/dev/null 2>&1 || \
   ! python3 -B -E -S -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
then
  printf 'STOP: Python 3.10+ is unavailable; do not install packages in CloudShell.\n' >&2
elif python3 -B -E -S infrastructure/member-d/import/prepare_import.py audit \
    --region "$AWS_REGION" \
    --stack "$SOURCE_STACK" \
    --api "$API_ID" \
    --authorizer "$AUTHORIZER_ID" \
    --integration "$INTEGRATION_ID" \
    --function "$FUNCTION_NAME" \
    --expected-commit "$APPROVED_COMMIT" \
    --workdir "$WORK_DIR"
then
  AUDIT_SUCCEEDED=true
else
  AUDIT_EXIT=$?
  printf 'STOP: read-only audit failed with exit %s; keep this shell open and do not continue.\n' \
    "$AUDIT_EXIT" >&2
fi

if [ "$AUDIT_SUCCEEDED" = true ]
then
  if python3 -B -E -S infrastructure/member-d/import/prepare_import.py recovery-report \
      --region "$AWS_REGION" \
      --source-stack "$SOURCE_STACK" \
      --target-stack "$TARGET_STACK" \
      --expected-commit "$APPROVED_COMMIT" \
      --workdir "$WORK_DIR"
  then
    RECOVERY_REPORT_SUCCEEDED=true
    printf 'Read-only audit and recovery report completed.\n'
  else
    RECOVERY_EXIT=$?
    printf 'STOP: recovery report failed with exit %s; keep this shell open and do not continue.\n' \
      "$RECOVERY_EXIT" >&2
  fi
fi
```

`GetTemplate` 的 `TemplateBody` 可以是 JSON 或 YAML 字符串；AWS 并不保证无 Transform
Stack 的 `Processed` template 会转换成 JSON（[GetTemplate API][get-template]、
[template formats][template-formats]）。因此 audit 在任何 AWS 查询之前，先验证当前完整
commit、clean tree、`-B -E -S` 解释器标志，再完整验证仓库内的
`member-d-yaml-audit.pyz` 与外部 lock manifest：archive 总哈希、逐 entry 哈希、固定
PyYAML 6.0.3 sdist 哈希、entry allowlist、路径/类型/解压限制和完整 MIT License 全部通过
后，才从**已经验证并保存在内存中的 entry bytes** 编译私有 `_pba_yaml`；导入过程不会
重新打开可被替换的 archive pathname，也不会把 archive 或外部路径加入 `sys.path`。最终
模块 `__file__` 仍必须精确指向该已验证 archive 内的 entry。恶意 `PYTHONPATH`、系统
site-packages、未锁定的 `yaml` 包或校验后的同路径替换都不能成为执行来源。

该 archive 只能由 `build_yaml_audit_artifact.py` 在受控本地/CI 中，从已下载的官方 sdist
离线构建；构建器先验证固定 PyPI SHA-256，且两次构建必须字节级一致。CloudShell 不构建
archive、不联网取依赖，也不得运行 `pip install`。任何缺失工件、manifest/hash/entry
不一致、SHA/clean-tree 不匹配或非零退出都必须按上面的 `STOP` 处理，不得临时安装依赖
或绕过模板验证。

报告必须把 target 分类为 `prepare`，且 source 仍是精确四资源边界；否则停止。

### 4.6 新鲜 prepare（未来单独获批的 AWS 写操作）

`prepare` 会再次采集完整只读证据，然后把当前 Query Lambda zip 以内容寻址方式上传到
已批准、私有、加密且启用版本的 artifact bucket。这个 versioned Lambda backup 会执行
`s3:PutObject`，因此 **prepare 本身也必须先取得 AWS 写批准**；当前批准不包含它。

§4.6 及之后的命令是未来流程参考，不属于本次 CloudShell 只读路径。未来若获写批准，
还必须先单独批准一个与 Lambda `python3.12` 和哈希锁一致的受控执行环境；不得在
CloudShell 的系统 Python 中临时安装 PyYAML、pytest 或 query requirements 后继续。

```bash
# FUTURE WRITE — 仅在 prepare 获得独立批准后运行。
PREPARE_SUCCEEDED=false
if [ "$AUDIT_SUCCEEDED" != true ] || [ "$RECOVERY_REPORT_SUCCEEDED" != true ]
then
  printf 'STOP: fresh audit and recovery-report gates did not both pass.\n' >&2
elif python3 -B -E -S infrastructure/member-d/import/prepare_import.py prepare \
    --region "$AWS_REGION" \
    --stack "$SOURCE_STACK" \
    --api "$API_ID" \
    --authorizer "$AUTHORIZER_ID" \
    --integration "$INTEGRATION_ID" \
    --function "$FUNCTION_NAME" \
    --artifact-bucket "$ARTIFACT_BUCKET" \
    --expected-commit "$APPROVED_COMMIT" \
    --workdir "$WORK_DIR"
then
  PREPARE_SUCCEEDED=true
  printf 'Prepare completed; do not create a preview without separate approval.\n'
else
  printf 'STOP: prepare failed; discard this attempt and do not create a preview.\n' >&2
fi
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
PREVIEW_CREATED=false
PREVIEW_READY=false
PREVIEW_VALIDATED=false

if [ "${PREPARE_SUCCEEDED:-false}" != true ]
then
  printf 'STOP: prepare gate did not pass; no Change Set was requested.\n' >&2
elif aws cloudformation create-change-set \
    --region "$AWS_REGION" \
    --stack-name "$TARGET_STACK" \
    --change-set-name "$IMPORT_CHANGE_SET" \
    --change-set-type IMPORT \
    --template-body "file://$WORK_DIR/import-template.json" \
    --resources-to-import "file://$WORK_DIR/resources-to-import.json" \
    --parameters "file://$WORK_DIR/import-parameters.json"
then
  PREVIEW_CREATED=true
else
  if ! python3 -B -E -S infrastructure/member-d/import/prepare_import.py recovery-report \
      --region "$AWS_REGION" \
      --source-stack "$SOURCE_STACK" \
      --target-stack "$TARGET_STACK" \
      --expected-commit "$APPROVED_COMMIT" \
      --workdir "$WORK_DIR" \
      --import-change-set-creation-failed
  then
    printf 'STOP: preview creation and recovery report both failed.\n' >&2
  else
    printf 'STOP: preview creation failed; discard this preparation bundle.\n' >&2
  fi
fi

if [ "$PREVIEW_CREATED" = true ]
then
  if aws cloudformation wait change-set-create-complete \
      --region "$AWS_REGION" \
      --stack-name "$TARGET_STACK" \
      --change-set-name "$IMPORT_CHANGE_SET"
  then
    PREVIEW_READY=true
  else
    if ! python3 -B -E -S infrastructure/member-d/import/prepare_import.py recovery-report \
        --region "$AWS_REGION" \
        --source-stack "$SOURCE_STACK" \
        --target-stack "$TARGET_STACK" \
        --expected-commit "$APPROVED_COMMIT" \
        --workdir "$WORK_DIR" \
        --import-change-set-creation-failed
    then
      printf 'STOP: preview wait and recovery report both failed.\n' >&2
    else
      printf 'STOP: preview is not CREATE_COMPLETE; do not retry.\n' >&2
    fi
  fi
fi

if [ "$PREVIEW_READY" = true ]
then
  if aws cloudformation describe-change-set \
      --region "$AWS_REGION" \
      --stack-name "$TARGET_STACK" \
      --change-set-name "$IMPORT_CHANGE_SET" \
      --query 'Changes[].ResourceChange.{Action:Action,LogicalId:LogicalResourceId,Type:ResourceType,Replacement:Replacement}' \
      --output table --no-cli-pager &&
     python3 -B -E -S infrastructure/member-d/import/prepare_import.py validate-change-set \
       --region "$AWS_REGION" \
       --source-stack "$SOURCE_STACK" \
       --stack "$TARGET_STACK" \
       --change-set "$IMPORT_CHANGE_SET" \
       --expected-type IMPORT \
       --api "$API_ID" \
       --authorizer "$AUTHORIZER_ID" \
       --integration "$INTEGRATION_ID" \
       --function "$FUNCTION_NAME" \
       --expected-commit "$APPROVED_COMMIT" \
       --workdir "$WORK_DIR" \
       --artifact-bucket "$ARTIFACT_BUCKET"
  then
    PREVIEW_VALIDATED=true
    printf 'STOP: report the validated 19-Import preview for a new approval.\n'
  else
    printf 'STOP: preview description or validator failed; it is not approved.\n' >&2
  fi
fi
```

Validator 必须从 CloudFormation 实际描述中确认：`ChangeSetType=IMPORT`、目标 Stack 精确
为 `PacificBioArchive-QueryAdoption`、恰好 19 个 `Import`、没有 Add/Modify/Remove/Replace、
没有数据库 Stack 资源、没有 Outputs/secret。它还会重新采集明确的 preview phase：Target
必须精确处于 `REVIEW_IN_PROGRESS`、拥有 0 项资源，且 19 项 owner 仍全部为 `None`；同时
caller/region、源 Stack 4 项映射、Lambda、API/authorizer、integration 和 16 条 Route 必须
与最初 absent-target baseline 完全一致。IMPORT 工件仍用最初 baseline 校验，不会改用
preview snapshot。

**到这里必须停止并汇报。第一次 preview 流程中不存在执行 IMPORT 的命令。**

## 5. 日后获批执行 IMPORT 后的强制证据门

若未来另行批准并执行该 IMPORT，Stack 到达 `IMPORT_COMPLETE` 后必须立即运行：

```bash
python3 -B -E -S infrastructure/member-d/import/prepare_import.py verify-post-import \
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

IMPORT template 暂时省略 `QueryFunction.Environment`，使 template 和工件保持 secret-free；
不能仅凭模板推断线上 Lambda 未改变。Lambda API 会把完整配置交给可信 AWS CLI 子进程，
CLI 侧 `--query` 在 stdout 到达 Python 前只保留完整变量名集合和允许比较的非敏感值。
只有上述真实 post-import comparison 能证明运行时边界未改变；未来运行前仍须明确接受
这一 AWS CLI 子进程信任边界。

## 6. 最终正常 UPDATE（仅记录边界，不是操作授权）

最终正常 UPDATE 是另一阶段，必须使用 `infrastructure/member-d/query-adoption.yaml` 新鲜
build/package，单独创建 preview、运行 validator，并再次取得人工批准。若当前 key 未被
Stack 注册，A 只能在 CloudFormation Console 的 `InternalApiKey` `NoEcho` 密码框输入
当前值；不存在 secret-bearing CLI 路径。

UPDATE preview validator 必须重新采集 `ownership_phase=post`，并把它与
`post-import-evidence.json` 的 `IMPORT_COMPLETE` baseline 做完整同边界比较。只有新鲜证据
可提供 Role ARN、API ID、authorizer ID 和三张 core table 名称；保存文件不能单独作为
最终判断。`EXPECTED_QUERY_INPUT_BUCKET`、`EXPECTED_STORAGE_DELETE_FUNCTION` 和
`EXPECTED_INFERENCE_API_BASE_URL` 是本次 UPDATE 的显式人工审阅输入，不是从现有 Media
Stack 自动发现的 live evidence，也不扩大本流程的 Stack 所有权范围。

未来创建 UPDATE preview 后，精确的只读 validator 接口是（变量、工件与 Change Set 都
必须来自同一次另行获批流程）：

```bash
python3 -B -E -S infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --stack "$TARGET_STACK" \
  --change-set "$UPDATE_CHANGE_SET" \
  --expected-type UPDATE \
  --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR" \
  --artifact-bucket "$ARTIFACT_BUCKET" \
  --built-template "$UPDATE_BUILT_TEMPLATE" \
  --packaged-template "$UPDATE_PACKAGED_TEMPLATE" \
  --built-code-dir "$UPDATE_BUILT_CODE_DIR" \
  --source-code-dir backend/lambdas/query \
  --dependency-manifest "$UPDATE_DEPENDENCY_MANIFEST" \
  --expected-commit "$APPROVED_COMMIT" \
  --expected-http-api-id "$API_ID" \
  --expected-jwt-authorizer-id "$AUTHORIZER_ID" \
  --expected-query-input-bucket "$EXPECTED_QUERY_INPUT_BUCKET" \
  --expected-storage-delete-function "$EXPECTED_STORAGE_DELETE_FUNCTION" \
  --expected-inference-api-base-url "$EXPECTED_INFERENCE_API_BASE_URL" \
  --expected-allow-legacy-processing-callbacks false \
  --expect-role-reconciliation false
```

第一次正常 UPDATE 的允许变更固定为 37 项：

- `QueryFunction`：唯一 `Modify`，且 AWS Change Set JSON 的 `Replacement` key 必须存在，
  wire value 必须是精确字符串 `"False"`；
- 10 条 OPTIONS Route：`Add`；
- 26 条 method/path-scoped `AWS::Lambda::Permission`：`Add`。

每个 `Add` 的 `Replacement` 只能省略或为精确字符串 `"False"`；显式 `null`、布尔值、
数字、大小写不同的字符串、`"True"` 或 `"Conditional"` 都会 fail closed。

禁止任何 Remove、Replace、额外 Modify、wildcard permission、数据库 Stack 资源或
`QueryLambdaRole` 变更。该 UPDATE 不新增 SNS Topic/Subscription。DynamoDB
`NotificationsTable` 仍提供 durable in-app inbox；每用户 email 订阅需要另行设计、审查并
批准跨 Stack IAM/SNS 方案。

如果 UPDATE 失败并进入 `UPDATE_ROLLBACK_COMPLETE`，必须运行 recovery report 和完整
runtime/API/ownership 验证。只有确认 19 项导入资源仍精确归 Query Stack、且全部运行时
证据等同 `IMPORT_COMPLETE` 稳定边界，才能把回滚视为成功恢复；否则冻结后续操作。

完整恢复验证不是 `verify-post-import` 的宽松变体；后者继续只接受
`IMPORT_COMPLETE`。回滚必须使用以下独立、只读命令，并把保存的 post-import evidence
作为 baseline：

```bash
python3 -B -E -S infrastructure/member-d/import/prepare_import.py verify-update-rollback \
  --region "$AWS_REGION" \
  --source-stack "$SOURCE_STACK" \
  --target-stack "$TARGET_STACK" \
  --baseline "$WORK_DIR/post-import-evidence.json" \
  --api "$API_ID" \
  --authorizer "$AUTHORIZER_ID" \
  --integration "$INTEGRATION_ID" \
  --function "$FUNCTION_NAME" \
  --workdir "$WORK_DIR"
```

命令只采集完整只读证据，并生成净化后的
`update-rollback-evidence.json`。状态必须精确为
`UPDATE_ROLLBACK_COMPLETE`，Target 必须仍拥有原 19 项，所有 owners 必须指向 Query
Stack，源 4 项、Lambda、API/authorizer、integration 和 16 条 Route 必须与
`IMPORT_COMPLETE` baseline 完全一致；任一差异都停止。

## 7. 相关文件

- 架构设计：[`../superpowers/specs/2026-09-01-member-d-query-adoption-stack-design.md`](../superpowers/specs/2026-09-01-member-d-query-adoption-stack-design.md)
- 数据库/Query Stack 说明：[`database-setup.md`](database-setup.md)
- core database template：[`../../infrastructure/member-d/dynamodb.yaml`](../../infrastructure/member-d/dynamodb.yaml)
- Query normal-UPDATE template：[`../../infrastructure/member-d/query-adoption.yaml`](../../infrastructure/member-d/query-adoption.yaml)
- adoption 工具：[`../../infrastructure/member-d/import/prepare_import.py`](../../infrastructure/member-d/import/prepare_import.py)
- 离线 YAML artifact builder：[`../../infrastructure/member-d/import/build_yaml_audit_artifact.py`](../../infrastructure/member-d/import/build_yaml_audit_artifact.py)
- parser artifact lock：[`../../infrastructure/member-d/import/member-d-yaml-audit.lock.json`](../../infrastructure/member-d/import/member-d-yaml-audit.lock.json)
- PyYAML notices/license：[`../../infrastructure/member-d/import/THIRD_PARTY_NOTICES.md`](../../infrastructure/member-d/import/THIRD_PARTY_NOTICES.md)

[cloudshell-software]: https://docs.aws.amazon.com/cloudshell/latest/userguide/vm-specs.html
[github-permalinks]: https://docs.github.com/en/repositories/working-with-files/using-files/getting-permanent-links-to-files
[get-template]: https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_GetTemplate.html
[template-formats]: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-formats.html
