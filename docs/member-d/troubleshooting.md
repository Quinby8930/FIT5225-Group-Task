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

**原因**：这是**去重逻辑在工作**。`(user_id, checksum)` 唯一，同一用户重复上传
同一文件（相同 SHA-256）会被拦下，返回 `{"existing_file_id": "<uuid>"}`。

**解决**：如果确实是重复上传，前端应引导用户「该文件已存在」，而不是重新上传。
如果是测试想强制新建，换一个 `checksum` 或 `user_id` 即可。

---

## 4. `complete` 返回 `404 file not found`

**原因**：`PUT /internal/files/{file_id}/complete` 里的 `{file_id}` 必须和之前
`reserve` 时传的 `file_id` **完全一致**（大小写、连字符都算）。

**解决**：核对 `reserve.json` 里的 `file_id` 与 URL 中的 `{file_id}` 是否一致。
reserve 必须先于 complete 调用（状态机是 `reserve → processing → complete/failed`）。

---

## 5. DynamoDB 部署后 Lambda 报 `AccessDeniedException` 或 `ResourceNotFoundException`

**原因**（按概率）：

1. 没有通过最新 SAM 模板部署 QueryFunction 或模板生成的执行角色。
2. 表名/区域不匹配 —— 环境变量 `DYNAMODB_TABLE` / `AWS_REGION` 没设对。
3. 新表没部署 —— 订阅/通知依赖 `PacificBioArchiveSubscriptions` /
   `PacificBioArchiveNotifications` 两张表，旧模板没有。

**解决**：

1. 用**最新**的 `infrastructure/member-d/dynamodb.yaml` 重新 `sam build` +
   `sam deploy`（会创建 3 张表、QueryFunction、策略和显式路由）。
2. Lambda 环境变量补齐：

   | 变量 | 值 |
   |------|-----|
   | `REPO_BACKEND` | `dynamodb` |
   | `DYNAMODB_TABLE` | `PacificBioArchiveFiles` |
   | `SUBSCRIPTIONS_TABLE` | `PacificBioArchiveSubscriptions` |
   | `NOTIFICATIONS_TABLE` | `PacificBioArchiveNotifications` |
   | `AWS_REGION` | `ap-southeast-2` |

3. IAM 角色确认授予了 `dynamodb:Query`（订阅/通知查询用），新模板已加。

---

## 6. 订阅了物种，但 `complete` 之后收不到通知

**原因**（按概率）：

1. 订阅没建立 —— 先 `POST /notifications/subscribe`，再 `GET /notifications/subscriptions?user_id=...`
   确认在列表里。
2. **tags 用了错误的名字** —— 触发器按「团队简化名」匹配（见 `INTEGRATION.md §2`）。
   `complete` 里 `tags` 的 key 必须是 `"wombat"`、`"magpie"`、`"dingo"` 这类简化名，
   **不是** `Vombatus_ursinus`。
3. 该物种在 `tags` 里数量为 0 —— 触发器只对 `count >= 1` 的物种发通知。

**解决**：核对 `complete.json` 的 `tags` key 与订阅的 `species` 完全一致（简化名、
小写）。通知只在新文件**首次** `complete` 时触发（幂等重放不会重复发）。

---

## 7. 存储删除时成员 B 返回 `403 FORBIDDEN_KEY`

**原因**：成员 B 的 guarded storage-delete Lambda 强制每个 key 必须在
`originals/{user_id}/`、`thumbnails/{user_id}/`、`processing/{user_id}/` 前缀下。
Member D 的 `delete` 已按 owner 分组，把 `user_id` 和受控 keys 一起传过去。

**解决**：确认被删文件的 `user_id` 与 S3 key 前缀里的 `{user_id}` 一致。如果
bulk delete 跨多个用户，Member D 会按 owner 拆成多次调用，每次只传该 owner 的
keys —— 这一层已在 `delete_files` 端点里实现。

---

## 8. 本地跑不起来：`ModuleNotFoundError: No module named 'app'` 或依赖缺失

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

## 9. 通知/订阅在本地测试互相污染

**原因**：订阅和通知写在同一个 SQLite 库文件的 `subscriptions` / `notifications`
表里，和文件表的 `files` 表共存于 `data/pacific_bioarchive.db`。

**解决**：重置演示数据时删掉整个库文件即可，三张表一起重建：

```bash
rm -f data/pacific_bioarchive.db && python seed.py
```
