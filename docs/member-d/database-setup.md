# Member D AWS 部署边界（成员 A）

> **当前没有 AWS 操作授权。** 现有账号的下一步只能在
> [`aws-resource-adoption.md`](aws-resource-adoption.md) 所列的独立批准后进行。

Member D 采用两个 CloudFormation Stack。它们按资源所有权拆分，并非部署了两套数据库
或两套 Query API。

## 1. 两个维护模板

| Template | Stack | 资源边界 |
|---|---|---|
| `infrastructure/member-d/dynamodb.yaml` | `PacificBioArchive-Database` | `FilesTable`、`SubscriptionsTable`、`NotificationsTable`、`QueryLambdaRole` |
| 初始 IMPORT 工具生成的 `import-template.json`，后续维护源为 `infrastructure/member-d/query-adoption.yaml` | `PacificBioArchive-QueryAdoption` | `ReservationsTable`、`QueryFunction`、`QueryIntegration`、16 条非 OPTIONS Route；后续增加 OPTIONS Route 和 scoped permissions |

数据库 Stack 已有的四项资源、Parameters 和 Outputs 保持原所有权。Query Stack 不复制
这些资源，也不增加 Export。跨 Stack 值使用普通参数，并由 validator 与只读审计证据精确
比较：

- `ExistingQueryLambdaRoleArn`
- `ExistingHttpApiId`
- `ExistingJwtAuthorizerId`
- `ExistingFilesTableName`
- `ExistingSubscriptionsTableName`
- `ExistingNotificationsTableName`

Query Stack 不管理或修复数据库 Stack 的 `QueryLambdaRole`。任何 IAM 权限变化都必须形成
独立的跨 Stack 设计和审批。

## 2. 现有账号的纳管顺序

当前账号已经有未纳管的 reservations table、Query Lambda、integration 和 16 条业务
Route。禁止直接把它们部署进 `PacificBioArchive-Database`，也禁止删除在线资源后重建。

顺序固定为：

1. 对数据库 Stack 和 19 项 unmanaged 资源做新鲜只读 audit；
2. 生成独立、无 Outputs、无 secret 的 Query IMPORT template 和 19 项自动标识 manifest；
3. 在获得 AWS 写批准后，只为 `PacificBioArchive-QueryAdoption` 创建 IMPORT preview；
4. Validator 证明 preview 恰好是 19 个 Import，无 Add/Modify/Remove/Replace；
5. 停止并等待执行 IMPORT 的另一项明确批准；
6. 若未来执行，立即运行 post-import runtime/API evidence gate；
7. 通过后才可设计并创建正常 UPDATE preview。

完整命令、恢复状态和 preview-only 停止点见
[`aws-resource-adoption.md`](aws-resource-adoption.md)。在本地测试通过和代码 push 之前，
不得要求操作者执行该手册。

## 3. 初始 IMPORT 与第一次正常 UPDATE 的区别

### 初始 IMPORT

- 精确 19 项：1 table、1 function、1 integration、16 条非 OPTIONS Route；
- 每项都有 `DeletionPolicy: Retain` 和 `UpdateReplacePolicy: Retain`；
- 只有三个普通参数：现有 Role ARN、API ID、authorizer ID；
- 没有 `Outputs`、`InternalApiKey`、SNS、OPTIONS Route、Lambda permission 或数据库 Stack
  资源；
- `QueryFunction.Environment` 暂时省略，避免读取或保存线上 secret；IMPORT 后用完整只读
  比较证明 Lambda 未改变。

### 第一次正常 UPDATE

它是 IMPORT 之后另行 preview、validator 和批准的操作。允许的 Change Set 固定为：

- `QueryFunction / Modify / Replacement=False`；
- 10 条 OPTIONS Route `Add`；
- 26 条 method/path-scoped Lambda permission `Add`。

不允许 Remove、Replace、其他 Modify、wildcard permission 或数据库 Stack 资源。模板不含
SNS Topic/Subscription，也不修改 `QueryLambdaRole`。如果 UPDATE 回滚，只能在完整证据
等同 `IMPORT_COMPLETE` 边界时继续；否则停止。

## 4. Secret 与通知边界

初始 IMPORT 完全不知道 `InternalApiKey`。该值只在第一次正常 UPDATE 获得独立批准后，
由 A 在 CloudFormation Console 的 `NoEcho` 密码框输入。不得将其放入 CLI、环境变量、
文件、日志、截图、Git 或聊天。不存在通过命令行传递当前 key 的受支持路径。

当前通知能力是 `PacificBioArchiveNotifications` 中的 durable per-user in-app inbox，以及
`PacificBioArchiveSubscriptions` 中的用户/物种订阅。第一次正常 UPDATE 不部署 SNS email。
每用户 email 必须从 Cognito verified claims 获取地址，并另行完成跨 Stack IAM/SNS 权限
设计、预览、审批和防串发测试；当前 Query Stack 方案不声称已完成该功能。

## 5. 数据和对象模型

- `PacificBioArchiveFiles` 保存 file metadata、detections、tag counts、processing state 和
  稳定对象 key。
- `PacificBioArchiveUploadReservations` 保存 `(user_id, checksum)` reservation claim。
- `PacificBioArchiveSubscriptions` 保存 `(user_id, species)` 订阅。
- `PacificBioArchiveNotifications` 保存 `(user_id, notification_id)` durable inbox。
- 原文件、缩略图和视频帧继续使用稳定的 `user/file` 范围 S3 key。按物种浏览由 DynamoDB
  tag query 形成逻辑目录；一张图片有多个物种时不会产生多份 S3 副本，也不会移动对象。

既有 reservation claim 的 backfill 仍是后续独立数据变更。执行前必须暂停所有 Files 和
Reservations mutation 并单独批准；adoption IMPORT 本身不修改表数据。

## 6. API Gateway 与运行时边界

- 公开 Query、tag、delete、subscription 和 notification 路由使用现有 JWT authorizer；
- internal reserve/processing/complete/failed/assets 路由在 API Gateway 为 `NONE`，应用层仍
  验证共享 internal key；
- 没有 `$default` 或 `ANY /{proxy+}`；
- 正常 UPDATE 的 26 条 permission 都绑定具体 method/path。

`AWS::ApiGatewayV2::Integration` 在此流程中不能依赖 CloudFormation drift detection。
IMPORT 后必须由 `verify-post-import` 直接读取 API Gateway integration 和全部 Route 并逐属性
比较。

## 7. 本地开发与验证

本地 Query API 默认使用 SQLite。Stub adapter 必须显式设置
`STORAGE_BACKEND=stub` 和 `TAG_DETECTOR_BACKEND=stub`；生产配置缺失时 fail closed。

从仓库根目录运行 Member D infrastructure/adoption tests：

```powershell
python -m pytest infrastructure/member-d/import/test_adoption.py infrastructure/member-d/import/test_prepare_import.py infrastructure/member-d/test_template.py -q -p no:cacheprovider
```

本次双 Stack 方案只解决已确认账号的资源纳管边界。全新空账号的两 Stack 创建顺序存在
新的资源依赖，必须另行设计和验证；不要把本手册的 IMPORT 流程当作 clean-room deploy。
