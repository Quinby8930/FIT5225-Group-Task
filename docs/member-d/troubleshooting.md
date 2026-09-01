# Member D 排错说明（Troubleshooting）

按「症状 → 原因 → 解决」排列。数据库和跨模块 adapter 独立选择：本地 stub 必须
显式配置；SAM 生产环境固定 DynamoDB/Lambda/remote，缺失配置时 fail closed。

---

## 1. 本地测试报 `sqlite3.OperationalError: no such column: object_key`

**原因**：旧的 `data/pacific_bioarchive.db` 还留着老 schema。SQLite 建表用的是
`CREATE TABLE IF NOT EXISTS`，**不会**自动迁移已有表。

**解决**：删掉旧库重跑。

```bash
rm -f data/pacific_bioarchive.db
python seed.py
python -m pytest tests/ -q
```

---

## 2. 启动时报 Pydantic 警告 `Field name "model_version" shadows an attribute in parent "BaseModel"`

**原因**：Pydantic v2 的 `model_` 保留命名空间（`model_version` 里带 `model_` 前缀）。

**解决**：已修 —— `FileRecord` / `CompleteRequest` 都加了
`model_config = {"protected_namespaces": ()}`。如果你新增带 `model_` 前缀的字段，
同样要加这行。这是无害警告，不是错误。

---

## 3. `reserve` 返回 `409`，但你认为应该是新文件

**原因**：`processing` / `completed` 文件的 `(user_id, checksum)` 已被原子预约，
此时返回 `{"existing_file_id": "<uuid>"}`。`pending_upload` / `failed` 不会 409：
元数据一致时会返回旧 `file_id/object_key`，由上传 Lambda 重新生成 presigned URL。

**解决**：如果确实是重复上传，前端应引导用户「该文件已存在」，而不是重新上传。
如果返回 `409 METADATA_CONFLICT`，检查 filename、file_type、content_type、size_bytes
是否和旧预约一致。如果确实要重新创建，可先通过受控删除端点删除旧文件（会清理
reservation），或在测试中换 `checksum` / `user_id`。

---

## 4. `complete` 返回 `404 file not found`

**原因**：`PUT /internal/files/{file_id}/complete` 里的 `{file_id}` 必须和之前
`reserve` 时传的 `file_id` **完全一致**（大小写、连字符都算）。

**解决**：核对 `reserve.json` 里的 `file_id` 与 URL 中的 `{file_id}` 是否一致。
reserve 必须先于 complete 调用（状态机是 `reserve → processing → complete/failed`）。

---

## 5. DynamoDB 部署后 Lambda 报 `AccessDeniedException` 或 `ResourceNotFoundException`

**原因**（按概率）：

1. QueryFunction 尚未按双 Stack adoption 流程完成纳管和后续正常 UPDATE。
2. 表名/区域不匹配 —— `DYNAMODB_TABLE` / `RESERVATIONS_TABLE` / `AWS_REGION` 没设对。
3. 新表没部署 —— 订阅/通知依赖 `PacificBioArchiveSubscriptions` /
   `PacificBioArchiveNotifications` 两张表，旧模板没有。

**解决**：

1. 当前已有 Query Lambda/integration/routes 的账号必须使用新
   `PacificBioArchive-QueryAdoption` Stack 的
   [`aws-resource-adoption.md`](aws-resource-adoption.md)，不能把 query 资源部署进
   `PacificBioArchive-Database`，也不能直接重跑旧 SAM。全新空账号的两 Stack 创建顺序
   需要另行设计；现有 adoption 手册不覆盖 clean-room deploy。
2. Lambda 环境变量补齐：

   | 变量 | 值 |
   |------|-----|
   | `REPO_BACKEND` | `dynamodb` |
   | `DYNAMODB_TABLE` | `PacificBioArchiveFiles` |
   | `RESERVATIONS_TABLE` | `PacificBioArchiveUploadReservations` |
   | `SUBSCRIPTIONS_TABLE` | `PacificBioArchiveSubscriptions` |
   | `NOTIFICATIONS_TABLE` | `PacificBioArchiveNotifications` |
   | `AWS_REGION` | `ap-southeast-2` |

3. IAM 角色必须通过只读审计确认已有查询和 transaction 权限。Query Stack 的第一次
   正常 UPDATE 不修改数据库 Stack 拥有的 Role，也不部署 SNS Topic 或 `sns:Publish`；
   不得为了排错手工扩大 Role 权限。

所有上述 AWS 检查和环境变量修改均由成员 A 执行，B/D 不自行改 AWS。C 只处理阿里云
推理部署；共享 `INTERNAL_API_KEY` 仅 A/C 安全配置，不得进入 Git、文档或群聊。

---

## 6. 订阅了物种，但 `complete` 之后收不到通知

**原因**（按概率）：

1. 订阅没建立 —— 先 `POST /notifications/subscribe`，再以同一登录用户调用 `GET /notifications/subscriptions`
   确认在列表里。
2. **tags 用了错误的名字** —— 触发器按「团队简化名」匹配（见 `INTEGRATION.md §2`）。
   `complete` 里 `tags` 的 key 必须是 `"wombat"`、`"magpie"`、`"dingo"` 这类简化名，
   **不是** `Vombatus_ursinus`。
3. 该物种在 `tags` 里数量为 0 —— 触发器只对 `count >= 1` 的物种发通知。

**解决**：核对 `complete.json` 的 `tags` key 与订阅的 `species` 完全一致（简化名、
小写），然后用同一已登录用户调用 `GET /notifications` 检查 DynamoDB-backed durable
in-app inbox。当前第一次正常 UPDATE 不部署 SNS email，因此“没有收到邮件”不能作为
inbox 失败证据。每用户 email 需要另行批准 Cognito claims、IAM、SNS 和防串发设计。

---

## 7. ReservationsTable 纳管/部署后 verify 报缺 claim 或冲突

**原因**：旧 FilesTable 行没有对应 claim，或旧数据已违反 `(user_id, checksum)`
唯一性。运行时 fallback 不是正式迁移完成证明。

**解决**：这是 IMPORT 以外的业务数据变更。成员 A 必须先暂停所有
Files/Reservations mutation（reserve、processing/complete/failed 回调和 delete），然后
为 verify/backfill/verify 迁移另行提交方案和写操作批准。多 file/错误 claim 必须人工核对
并 fail closed；禁止删 claim 后直接恢复流量，也不得把 backfill 混入 adoption。

---

## 8. 存储删除时成员 B 返回 `403 FORBIDDEN_KEY`

**原因**：成员 B 的 guarded storage-delete Lambda 强制每个 key 必须在
`originals/{user_id}/`、`thumbnails/{user_id}/`、`processing/{user_id}/` 前缀下。
Member D 的 `delete` 已按 owner 分组，把 `user_id` 和受控 keys 一起传过去。

**解决**：确认被删文件的 `user_id` 与 S3 key 前缀里的 `{user_id}` 一致。如果
bulk delete 跨多个用户，Member D 会按 owner 拆成多次调用，每次只传该 owner 的
keys —— 这一层已在 `delete_files` 端点里实现。

---

## 9. 本地跑不起来：`ModuleNotFoundError: No module named 'app'` 或依赖缺失

**解决**：

```bash
cd backend/lambdas/query
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

注意 `boto3` 是**可选**依赖（DynamoDB 后端才需要，Lambda 运行时自带），本地
SQLite 模式通常不会调用它。`requirements.txt` 仍包含 boto3，因为生产 adapter
直接使用 DynamoDB、S3 和 Lambda 客户端。

---

## 10. 通知/订阅在本地测试互相污染

**原因**：订阅和通知写在同一个 SQLite 库文件的 `subscriptions` / `notifications`
表里，和文件表的 `files` 表共存于 `data/pacific_bioarchive.db`。

**解决**：重置演示数据时删掉整个库文件即可，三张表一起重建：

```bash
rm -f data/pacific_bioarchive.db && python seed.py
```

---

## 11. CloudFormation 为 `UPDATE_ROLLBACK_COMPLETE`，事件显示 `RouteKey ... already exists`

**原因**：Member D 的在线 API Gateway route/integration 是在 stack 外创建的，而当前 SAM
模板试图用相同 RouteKey 再创建一次。CloudFormation 回滚不会自动把现有资源纳管。

**解决**：

1. 不要再次运行相同的 `sam deploy` / `sam deploy --guided`；结果仍会冲突。
2. 不要运行 `delete-route`、`delete-integration`、`delete-function` 或 `delete-stack`，也不要
   在 Console 中手工删除在线资源。
3. 按 [`aws-resource-adoption.md`](aws-resource-adoption.md) 从只读 audit 开始，只为新的
   `PacificBioArchive-QueryAdoption` Stack 创建恰好 19 项的 IMPORT preview：既有
   reservations table、Lambda、integration 和 16 条 Member D 非 OPTIONS Route。
4. Preview validator 通过后必须停止。执行 IMPORT 需要另一项批准；若未来执行，必须立即
   通过 post-import runtime/API evidence gate。
5. 第一次正常 UPDATE 又是独立 preview/批准，只允许 QueryFunction 非替换 Modify、10 条
   OPTIONS Add 和 26 条 scoped permission Add。它不修改 Role，也不部署 SNS。

全新账号没有这些在线资源，不需要 adoption，但两 Stack clean-room 创建顺序仍需要另行
设计和验证，不能复用本手册。
