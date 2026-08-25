# Member D 数据库部署指南（给负责 AWS 的同学）

> **这是唯一要读的文件。** 看完照着做，就能把 Member D 的数据库在 AWS 上跑起来。
> 代码在 `backend/lambdas/query/`，基础设施模板在 `infrastructure/member-d/dynamodb.yaml`。

---

## 0. 一句话总结

用 SAM 部署 **三张 DynamoDB 表 + 查询 Lambda + 显式 HTTP API 路由**。生产环境
同时启用 Member B 删除 Lambda 和 Member C HTTPS 推理服务；不完整配置会 fail closed。

---

## 1. 准备跨模块参数

部署前向 Member A/B/C 获取：

| 参数 | 来源 |
|------|------|
| `ExistingHttpApiId` | Member A 现有 HTTP API ID（部署时必填） |
| `ExistingJwtAuthorizerId` | Member A JWT authorizer ID |
| `QueryInputBucketName` | Member B 私有媒体桶 |
| `StorageDeleteFunctionName` | Member B guarded storage-delete Lambda 名称 |
| `InferenceApiBaseUrl` | Member C HTTPS 根地址（不要加 `/infer`） |
| `InternalApiKey` | B/C/D 共用的非空密钥；禁止提交到 Git |

模板不会提供推理地址或密钥默认值。

---

## 2. 构建并部署 SAM 堆栈

模板文件：**`infrastructure/member-d/dynamodb.yaml`**（CloudFormation，区域
`ap-southeast-2`，和 Cognito / S3 / Lambda 同区）。

```bash
sam build --template-file infrastructure/member-d/dynamodb.yaml
sam deploy --guided
```

在 guided prompts 中填入上表参数，并将区域设为 `ap-southeast-2`。密钥应通过团队
认可的安全部署流程输入；不要把真实值写入 `samconfig.toml`、shell 脚本或文档。

**会创建：**

| 资源 | 名称 | 说明 |
|------|------|------|
| DynamoDB 表 | `PacificBioArchiveFiles` | 文件元数据，主键 `file_id`（字符串），按需计费 |
| DynamoDB 表 | `PacificBioArchiveSubscriptions` | 订阅，主键 `user_id` + 排序键 `species` |
| DynamoDB 表 | `PacificBioArchiveNotifications` | 通知，主键 `user_id` + 排序键 `notification_id` |
| Lambda | `QueryFunction` | Python 3.12 / Mangum，30 秒，1024 MiB |
| HTTP API 资源 | 显式 routes + 单一 integration | 公开 JWT；internal/OPTIONS 为 NONE |

---

## 3. 生产环境变量（由模板写入）

代码目录：**`backend/lambdas/query/`**（一个 FastAPI 应用，用 mangum 打包成单个 Lambda）。

模板固定 `REPO_BACKEND=dynamodb`、`STORAGE_BACKEND=lambda`、
`TAG_DETECTOR_BACKEND=remote`，并把三张表、私有桶、删除函数、C 地址和内部密钥
参数接入 Lambda。生产环境绝不会自动选择 stub。

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
2. 确认三张 DynamoDB 表和 `QueryFunction` 已创建。
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
- **唯一性去重**：`(user_id, checksum)` 唯一是**应用层**保证的（reserve 端点
  先查再写）。DynamoDB 主键只有 `file_id`，没建 GSI —— 对这个数据量用 Scan 就够。
- **订阅/通知两张表**：`complete` 时通知触发器会写 `PacificBioArchiveNotifications`
  （并查 `PacificBioArchiveSubscriptions`），所以 Lambda 的 IAM 角色必须同时授权
  这两张表（新模板已含 `dynamodb:Query`）。
- **排错**：报错先看 `docs/member-d/troubleshooting.md`，绝大多数问题（旧 schema、
  IAM、表名/区域、简化名匹配）都有对应解法。
