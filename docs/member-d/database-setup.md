# Member D 数据库部署指南（成员 A / AWS 操作）

> 这是 Member D 的部署入口。先判断是“全新账号”还是“当前已有在线资源的账号”；当前
> 账号必须先读 [`aws-resource-adoption.md`](aws-resource-adoption.md)。
> 代码在 `backend/lambdas/query/`，基础设施模板在 `infrastructure/member-d/dynamodb.yaml`。

---

## 0. 一句话总结

用 SAM 部署 **四张 DynamoDB 表 + SNS Topic + 查询 Lambda + 显式 HTTP API 路由**。
生产环境同时启用 Member B 删除 Lambda、Member C HTTPS 推理服务和 SNS 通知；
不完整配置会 fail closed。

---

## 1. 准备跨模块参数

成员 A 负责部署所有 AWS B/D 资源与写入环境变量；B/D 不自行配置 AWS。部署前由 A
核对其管理的 B/D AWS 资源，并向 C 获取仅部署在阿里云的推理地址：

| 参数 | 来源 |
|------|------|
| `ExistingHttpApiId` | Member A 现有 HTTP API ID（部署时必填） |
| `ExistingJwtAuthorizerId` | Member A JWT authorizer ID |
| `QueryInputBucketName` | 成员 A 管理的 Member B 私有媒体桶 |
| `StorageDeleteFunctionName` | 成员 A 管理的 Member B guarded-delete Lambda 名称（使用 B 栈的同名输出，不要填 ARN；ARN 只用于审计/IAM 参考） |
| `InferenceApiBaseUrl` | Member C 阿里云 HTTPS 根地址（不要加 `/infer`） |
| `InternalApiKey` | 仅 A/C 通过安全渠道配置的非空共享密钥 |
| `AllowLegacyProcessingCallbacks` | 滚动升级临时开关；默认 `false`，稳定态必须保持 `false` |
| `NotificationEmailEndpoint` | 可选；留空则只创建 Topic，不创建 email subscription |

模板不会提供推理地址或密钥默认值。

---

## 2. 先选择正确的部署路径

模板文件：**`infrastructure/member-d/dynamodb.yaml`**（CloudFormation，区域
`ap-southeast-2`，和 Cognito / S3 / Lambda 同区）。

### 2.1 全新普通 AWS 账号：正常 SAM 部署

只有在目标账号中**不存在** Member D 的 Query Lambda、integration、16 条业务路由和
对应 DynamoDB 资源时，才使用普通 SAM 部署：

```bash
sam build --template-file infrastructure/member-d/dynamodb.yaml
sam deploy --guided
```

在 guided prompts 中填入上表参数，并将区域设为 `ap-southeast-2`。密钥应通过团队
认可的安全部署流程输入；不要把真实值写入 `samconfig.toml`、shell 脚本、文档、Git
或群聊。C 只负责阿里云部署，不操作 AWS；B/D 不持有部署职责。

### 2.2 当前账号：先纳管现有在线资源

当前账号已经存在 `PacificBioArchive-QueryLambda`、单一 integration 和 16 条 Member D
非 OPTIONS 路由，但这些资源不完全属于 `PacificBioArchive-Database` stack。这里禁止直接
运行 `sam deploy --guided` 或重试失败的 UPDATE；必须完整执行
[`aws-resource-adoption.md`](aws-resource-adoption.md)：

1. 先创建并审查恰好 18 项的 IMPORT change set；
2. 得到第一次明确批准后执行 IMPORT，并证明运行时未改变；
3. 再创建并审查正常 UPDATE change set；
4. 得到第二次明确批准后执行 UPDATE。

如果 stack 是 `UPDATE_ROLLBACK_COMPLETE`，事件包含 `RouteKey ... already exists`，不要
重试同一模板，也不要删除在线 route/integration/Lambda/stack。该状态正说明要先纳管资源。

**会创建：**

| 资源 | 名称 | 说明 |
|------|------|------|
| DynamoDB 表 | `PacificBioArchiveFiles` | 文件元数据，主键 `file_id`（字符串），按需计费 |
| DynamoDB 表 | `PacificBioArchiveUploadReservations` | checksum 原子预约，主键 `reservation_key` |
| DynamoDB 表 | `PacificBioArchiveSubscriptions` | 订阅，主键 `user_id` + 排序键 `species` |
| DynamoDB 表 | `PacificBioArchiveNotifications` | 通知，主键 `user_id` + 排序键 `notification_id` |
| SNS Topic | `NotificationTopic` | QueryFunction 发布通知；可条件订阅 email |
| Lambda | `QueryFunction` | Python 3.12 / Mangum，30 秒，1024 MiB |
| HTTP API 资源 | 显式 routes + 单一 integration | 公开 JWT；internal/OPTIONS 为 NONE |

---

## 3. 生产环境变量（由模板写入）

代码目录：**`backend/lambdas/query/`**（一个 FastAPI 应用，用 mangum 打包成单个 Lambda）。

模板固定 `REPO_BACKEND=dynamodb`、`STORAGE_BACKEND=lambda`、
`TAG_DETECTOR_BACKEND=remote`、`NOTIFICATION_PUBLISHER=sns`，并把四张表、SNS Topic、
私有桶、删除函数、C 地址和内部密钥参数接入 Lambda。生产环境绝不会自动选择 stub。

### 3.1 B/D 安全滚动升级顺序

`AllowLegacyProcessingCallbacks` 默认 `false`，此时 complete/failed 回调必须携带
processing 返回的 32–256 字符 `lease_token`。若线上仍有旧 Member B，严格按以下顺序：

1. 先部署 Member D，并临时设置 `AllowLegacyProcessingCallbacks=true`；
2. 再部署会保存并转发 lease token 的 Member B，确认新回调均带 token；
3. 最后重新部署 Member D，设置 `AllowLegacyProcessingCallbacks=false`。

不能交换第 1、2 步，也不能把兼容开关留在稳定态。兼容开启时，无 token 回调仍必须满足
`status=processing`，绝不能修改 pending/failed/completed/deleting；但它暂时不比较 lease
token，因此旧 worker 和新 worker 同处 processing 状态时仍缺少 generation fencing。
这个 residual risk 是该开关只能短期开启的原因。

### 3.2 从旧 FilesTable 受控切换 reservation claims

新 `ReservationsTable` 部署后，**不能只依赖运行时 fallback Scan 当作迁移**。fallback
使用强一致 Scan 和条件 claim，只是 fail-closed 兼容保护。成员 A 必须：

1. 暂停**所有会修改 FilesTable 或 ReservationsTable 的路径**，包括 reserve/上传、
   processing/complete/failed 回调和删除，并确认没有请求在途。参数名
   `--confirm-uploads-paused` 为兼容保留，实际确认的是上述全部 mutation 已暂停。
2. 在 `backend/lambdas/query/` 先运行只读验证：

   ```bash
   python migrate_reservations.py verify --files-table PacificBioArchiveFiles --reservations-table PacificBioArchiveUploadReservations
   ```

3. 缺 claim 时，在全部 Files/Reservations mutation 仍暂停的前提下执行事务回填：

   ```bash
   python migrate_reservations.py backfill --files-table PacificBioArchiveFiles --reservations-table PacificBioArchiveUploadReservations --confirm-uploads-paused
   ```

4. 再运行 `verify`；只有 `claims_missing=0`、`claims_extra=0` 且退出 0 才恢复流量。

同一 `(user_id, checksum)` 多条 file 或 claim 指向错误 file 时工具立即 fail closed，
必须人工核对。脚本只使用 A 当前 AWS 凭据，不读取/输出 `INTERNAL_API_KEY`；Scan/Get
两张表的 Scan 及冲突后的 Get 均强一致。每条回填用同一个 `TransactWriteItems`：先对
目标 file 做存在且 user/checksum 匹配的 ConditionCheck，再条件 Put claim；竞争输家只
接受同一 file 的 winner claim，并发删除不会留下 orphan claim。

---

## 4. API Gateway 安全边界

查询 Lambda 通过成员 A 提供的**现有 HTTP API** 暴露，两个用途：

- **公开查询路由**（`/query/*`、`/tags/edit`、`/files/delete`、`/notifications/*`）：
  挂成员 A 的 `CognitoJWTAuthorizer`，用户带 ID token 调用。
- **内部元数据路由**（`/internal/uploads/reserve`、`/internal/files/{id}/processing`
  、`/internal/files/{id}/complete`、`/internal/files/{id}/failed`）：成员 B 的
  上传/处理 Lambda 直接调用（无需 Cognito），对接契约见
  `docs/member-b/api-contracts.md`。

模板逐条声明公开、internal 和 OPTIONS 路由，并为每条路由创建 method-scoped
invoke permission。没有 `ANY /{proxy+}` 或 `$default`。API Gateway 对 internal 路由
使用 `NONE`，但应用仍强制 `X-Internal-Api-Key`，缺失服务器密钥时返回 503。

---

## 5. 验证

1. 运行 `python -m pytest infrastructure/member-d/test_template.py -q`。
2. 确认四张 DynamoDB 表、SNS Topic 和 `QueryFunction` 已创建。
3. 无 JWT 的公开路由应被 API Gateway 拒绝；internal 路由无/错 key 应返回 401。
4. `/query/by-file` 只接受 JPEG/PNG/WebP 且最大 4,194,304 bytes；临时对象位于
   `query-inputs/`，presign 为 120 秒，C call timeout 为 25 秒，重定向被拒绝，
   并在每次 put 尝试后执行幂等删除。B/C 普通链路的 12,582,912-byte 上限不变。

---

## 6. 本地开发

- **本地开发不用 AWS**：`REPO_BACKEND` 默认 SQLite，但 adapter stub 必须显式设置：
  `STORAGE_BACKEND=stub` 和 `TAG_DETECTOR_BACKEND=stub`。省略时服务 fail closed。
- **表里没有数据是正常的**：数据由成员 B 的上传流程通过 `/internal/uploads/reserve`
  和 `/internal/files/{id}/complete` 写入；查询 Lambda 只负责读。
- **唯一性去重**：DynamoDB 用 `TransactWriteItems` 同时写 reservation claim 与 file row，
  并发请求只有一个能声明 `(user_id, checksum)`。删除文件时同一事务清理 claim，因此
  删除后可以重新上传。`pending_upload` / `failed` 可复用旧 `file_id/object_key` 重签；
  `processing` / `completed` 仍返回 duplicate。
- **订阅/通知两张表**：`complete` 时通知触发器会写 `PacificBioArchiveNotifications`
  （并查 `PacificBioArchiveSubscriptions`），所以 Lambda 的 IAM 角色必须同时授权
  这两张表（新模板已含 `dynamodb:Query`）。
- **SNS 临时失败**：只有 processing lease token 的条件更新成功后，才从已存储的 completed
  metadata 幂等创建确定性 notification inbox；过期 worker 不会留下 inbox side effect。
  publish 或 delivery-state 更新失败只写日志。completed replay 从已存储 metadata（不信任
  retry body）补齐 inbox 并重试 pending。若第一次 inbox 写入在 completion CAS 后失败，下一次
  S3 delivery 的 begin-processing `completed` 路径会先完成同样的 stored-metadata recovery，
  B 不会再次下载或推理；成功后标 `delivered`。本课程实现没有周期 worker/DLQ，
  恢复依赖自动重放或 A 人工重放同一 complete PUT。投递是 at-least-once：SNS 已接受
  但状态更新失败时可能重复，消费者应按确定性 `notification_id` 去重。
- **排错**：报错先看 `docs/member-d/troubleshooting.md`，绝大多数问题（旧 schema、
  IAM、表名/区域、简化名匹配）都有对应解法。
