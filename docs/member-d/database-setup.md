# Member D 数据库部署指南（给负责 AWS 的同学）

> **这是唯一要读的文件。** 看完照着做，就能把 Member D 的数据库在 AWS 上跑起来。
> 代码在 `backend/lambdas/query/`，基础设施模板在 `infrastructure/member-d/dynamodb.yaml`。

---

## 0. 一句话总结

用 CloudFormation 部署一个 **DynamoDB 表 + 一个 IAM 角色**，然后把查询 Lambda
指向这张表（改 3 个环境变量）。没有别的资源要建。

---

## 1. 部署 DynamoDB 表和 IAM 角色

模板文件：**`infrastructure/member-d/dynamodb.yaml`**（CloudFormation，区域
`ap-southeast-2`，和 Cognito / S3 / Lambda 同区）。

**方式 A：AWS 控制台**

1. 打开 CloudFormation → 创建堆栈 → 「上传模板文件」→ 选 `dynamodb.yaml`。
2. 堆栈名随意，如 `PacificBioArchive-Database`。
3. 勾选「我确认 AWS CloudFormation 可能创建 IAM 资源」→ 创建。

**方式 B：CLI**

```bash
aws cloudformation deploy \
  --template-file infrastructure/member-d/dynamodb.yaml \
  --stack-name PacificBioArchive-Database \
  --region ap-southeast-2 \
  --capabilities CAPABILITY_NAMED_IAM
```

**会创建：**

| 资源 | 名称 | 说明 |
|------|------|------|
| DynamoDB 表 | `PacificBioArchiveFiles` | 主键 `file_id`（字符串），按需计费（PAY_PER_REQUEST） |
| IAM 角色 | `PacificBioArchive-QueryLambdaRole` | 允许 Lambda 对这张表 Put/Get/Scan/Update/Delete |

---

## 2. 部署查询 Lambda（代码已就绪）

代码目录：**`backend/lambdas/query/`**（一个 FastAPI 应用，用 mangum 打包成单个 Lambda）。

1. 打包依赖：`backend/lambdas/query/requirements.txt`（含 `mangum`；`boto3` 由
   Lambda 运行时自带，不用打包）。
2. Lambda 配置：
   - **Handler**：`lambda_function.handler`
   - **Runtime**：`python3.12`
   - **执行角色**：选第 1 步创建的 `PacificBioArchive-QueryLambdaRole`
   - **环境变量**：

     | 变量 | 值 |
     |------|-----|
     | `REPO_BACKEND` | `dynamodb` |
     | `DYNAMODB_TABLE` | `PacificBioArchiveFiles` |
     | `AWS_REGION` | `ap-southeast-2` |

3. 不改任何代码 —— 后端切换靠 `REPO_BACKEND` 环境变量完成
   （见 `backend/lambdas/query/app/config.py`）。

---

## 3. API Gateway 路由（与成员 A 协调）

查询 Lambda 通过**现有的 HTTP API `2dd2aqb32j`** 暴露，两个用途：

- **公开查询路由**（`/query/*`、`/tags/edit`、`/files/delete`）：挂成员 A 的
  `CognitoJWTAuthorizer`，用户带 ID token 调用。
- **内部元数据路由**（`/internal/uploads/reserve`、`/internal/files/{id}/processing`
  、`/internal/files/{id}/complete`、`/internal/files/{id}/failed`）：成员 B 的
  上传/处理 Lambda 直接调用（无需 Cognito），对接契约见
  `docs/member-b/api-contracts.md`。

建议把整个 FastAPI 应用挂到 `ANY /{proxy+}`（或 `$default`），这样所有路由
自动转发，成员 A 只需给公开路由配 authorizer。

---

## 4. 验证

1. **表已建**：DynamoDB 控制台 → 表 → 能看到 `PacificBioArchiveFiles`。
2. **角色已建**：IAM → 角色 → 能看到 `PacificBioArchive-QueryLambdaRole`。
3. **Lambda 能读写**：在 Lambda 测试事件里塞一个 HTTP 请求（或直接调
   `POST /query/by-tags`，body `{"tags": {}}`），应返回 `{"results": [], "count": 0}`
   而不是 `InternalServerError`。

---

## 5. 常见问题

- **本地开发不用 AWS**：代码默认 `REPO_BACKEND=sqlite`，本地跑 `python seed.py`
  + `uvicorn app.main:app` 即可（见 `backend/lambdas/query/README.md`）。
- **表里没有数据是正常的**：数据由成员 B 的上传流程通过 `/internal/uploads/reserve`
  和 `/internal/files/{id}/complete` 写入；查询 Lambda 只负责读。
- **唯一性去重**：`(user_id, checksum)` 唯一是**应用层**保证的（reserve 端点
  先查再写）。DynamoDB 主键只有 `file_id`，没建 GSI —— 对这个数据量用 Scan 就够。
