# Integration Guide — Database & Query API (Member D)

这份文档是**唯一的对接依据**。成员 A/B/C/E 只要照这里做，就能和数据库/查询层无缝对接，不需要读代码。

---

## 1. 谁需要看这份文档

| 成员 | 需要对接什么 | 看哪一节 |
|------|-------------|----------|
| **C (ML)** | 输出 tags 给数据库 | §2 标签命名 + §4.1 |
| **B (上传/存储)** | 写库（reserve/complete 状态机）+ 删 storage | §3 数据 schema + §4.2 + §5.7 |
| **A (认证)** | 保护所有公开端点 | §4.3 |
| **E (前端)** | 调用查询/编辑/订阅/通知 API | §5 API 契约（含 §5.8 订阅通知） |
| **D (通知服务)** | 已实现 Dynamo inbox + SNS publisher | §4.4 NotificationPublisher |
| **E (通知体验)** | 实现前端/站内通知体验 | §5.8 订阅通知 API |

---

## 2. 标签命名契约（最重要，最容易错）

数据库里 `tags` 是「**团队简化名 → 数量**」的映射。简化名 = `labels.txt`
通用名的**最后一个单词**，**不是**科学名，也**不是**完整通用名。

**三个一错就废的坑：**

| 科学名（SpeciesNet 输出） | 正确的标签名（存库用这个） | ❌ 错误写法 |
|---------------------------|------------------------------|-------------|
| `Vombatus_ursinus` | `"wombat"` | `"common wombat"` / `"Vombatus_ursinus"` |
| `Gymnorhina_tibicen` | `"magpie"` | `"australian magpie"` |
| `Canis_familiaris` / `Canis_dingo` | `"dingo"`（两个都→dingo） | `"canis familiaris"` |
| `Macropus_giganteus` | `"kangaroo"`（eastern gray kangaroo） | `"eastern gray kangaroo"` |
| `Vulpes_vulpes` | `"fox"`（red fox） | `"red fox"` |

规则一句话：**取 labels.txt 通用名最后一个词**。单词名（`dingo`、`cattle`、
`human`）保持不变。匹配大小写不敏感。

**成员 C 不要自己写转换**，用共享工具：

```python
from app.species import get_mapper
mapper = get_mapper()
mapper.common_name("Vombatus_ursinus")   # -> "wombat"
mapper.common_name("Canis_familiaris")   # -> "dingo"
```

`samples` 的 `tags` 长这样：

```json
{"dingo": 2, "wombat": 1}
```

---

## 3. 数据 schema（FileRecord）

数据库每条记录对应 DynamoDB 表 `PacificBioArchiveFiles`（主键 `file_id`，
字符串）。存储位置全部用 S3 **key**（`object_key` / `thumbnail_key`），**不是 URL**。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id` | string | ✅ | UUID，主键 |
| `user_id` | string | ✅ | 上传者，用 Cognito `sub`（成员 A 提供） |
| `file_type` | `"image"` / `"video"` | ✅ | |
| `object_key` | string | ✅ | 原文件 S3 key，如 `originals/<sub>/<uuid>/a.jpg` |
| `thumbnail_key` | string | 图片必填 | 视频为 `null` |
| `filename` | string | | 原始文件名 |
| `content_type` | string | | MIME，如 `image/jpeg` |
| `size_bytes` | int | | 字节数 |
| `tags` | object(string→int) | ✅ | 见 §2 |
| `detections` | list | | `[{"species": "wombat", "confidence": 0.94}]` |
| `model_version` | string | | 模型版本，如 `speciesnet-v1` |
| `checksum` | string | ✅ | SHA-256（Base64），`(user_id, checksum)` 唯一去重 |
| `status` | enum | ✅ | `pending_upload` / `processing` / `completed` / `failed` |
| `error_code` / `message` | string | | 失败诊断，`message` 截断到 240 字符 |
| `processing_sequencer` / `lease_expires_at` | | | 处理租约，见 §5.7 |
| `upload_time` | string | ✅ | ISO-8601 时间戳 |

**成员 B 不要直接调 `repo.add` 写 completed 记录**，而是走 HTTP 状态机（§5.7）：
先 `reserve`（`pending_upload`），处理完再 `complete`（`completed`）。只有查询层
内部的 seed/测试才直接构造 `FileRecord`。

### 3.1 订阅 & 通知数据模型

除了文件表，Member D 还维护两张表（同样 SQLite / DynamoDB 双后端）：

**订阅表 `subscriptions`** —— 用户订阅某个物种标签：

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | Cognito `sub` |
| `species` | string | 团队简化名（§2） |

主键 `(user_id, species)`，幂等（重复订阅 = 无操作）。

**通知表 `notifications`** —— 触发器写出的通知记录：

| 字段 | 类型 | 说明 |
|------|------|------|
| `notification_id` | string | UUID |
| `user_id` | string | 被通知的订阅者 |
| `file_id` | string | 触发通知的文件 |
| `species` | string | 命中的物种 |
| `object_key` | string | 该文件原图 key |
| `created_at` | string | ISO-8601 |
| `delivery_status` | string | 内部 outbox 状态：`pending` / `delivered`（公开响应不暴露） |

---

## 4. 四个集成接口

### 4.1 成员 C — `TagDetector`

接口：`app/tag_detector.py`，方法
`detect(*, user_id, file_name, content_type, content: bytes) -> dict[str, int]`。
生产环境设置 `TAG_DETECTOR_BACKEND=remote`，`RemoteTagDetector` 会把受限图片临时
写入私有 S3 `query-inputs/`。此公开入口最大 4,194,304 bytes，以 120 秒 HTTPS
GET URL 和 25 秒无重定向 HTTP timeout 调用 C 的 `/infer`；D Lambda timeout 为
30 秒。每次尝试 put 后都会尝试幂等删除，cleanup 错误不会覆盖原始推理错误。返回
key 必须是 §2 的**简化名**。B/C 普通链路仍使用 12,582,912 bytes 和 45/60/70 秒顺序。

### 4.2 成员 B — `StorageClient`

接口：`app/storage_client.py`，方法 `delete(user_id: str, keys: list[str]) -> None`。

作用：删文件时成员 D 先调 `storage.delete(user_id, keys)` 删除原图+缩略图，成功后
再删 DB 记录（对应成员 B 的 guarded storage-delete Lambda，入参
`{"user_id": ..., "keys": [...]}`）。公开删除只允许 Cognito `sub` 与记录 owner
一致；请求中只要有一条外部 owner 记录，整批返回 `403 FORBIDDEN_OWNER` 且不产生副作用。
生产环境设置 `STORAGE_BACKEND=lambda` 和非空 `STORAGE_DELETE_FUNCTION_NAME`；D
同步调用 Lambda 并验证外层调用状态、FunctionError、1 MiB 响应边界和内层状态。
adapter 失败返回稳定的 502 且保留 metadata；未配置返回 503。stub 只可在本地/测试中显式选择。

### 4.3 成员 A — `get_current_user`

每个**公开**路由都使用 `Depends(get_current_user)`。标签编辑、文件删除、订阅和通知
操作都只使用已验证的 Cognito `sub`，不接受客户端提供的 `user_id`。

内部状态机端点（§5.7）由成员 B 的 Lambda 调用，并统一要求
`X-Internal-Api-Key`。D 的环境变量 `INTERNAL_API_KEY` 未设置/为空时返回 `503`；
其 `detail.code` 为 `INTERNAL_AUTH_NOT_CONFIGURED`。header 缺失或不匹配时返回
`401`，其 `detail.code` 为 `INVALID_INTERNAL_API_KEY`。所有 AWS B/D Lambda 资源与环境变量
由成员 A 操作；共享 secret 仅由 A 与负责阿里云部署的 C 通过安全渠道配置，B/D 不自行
配置。secret 不得提交 Git、写入文档或发送到群聊。

真实 Cognito 配置（成员 A 提供，已写进 `app/auth.py`）：

| 参数 | 值 |
|------|-----|
| User Pool ID | `ap-southeast-2_1hGEJyYO7` |
| Region | `ap-southeast-2` |
| App Client ID | `65dgspco2djehpbpunc13t2oml` |
| Issuer | `https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7` |
| JWKS | `https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7/.well-known/jwks.json` |

认证实现支持两种部署模式：

1. **Lambda 模式**：API Gateway 的 `CognitoJWTAuthorizer` 已验过 token，直接读
   `event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]`。
2. **本地模式**：用 `cognitojwt` 自己验 Bearer token（本地开发加这个依赖）。

接好后：未登录请求自动 401，且 `sub` 会一路流到 `FileRecord.user_id`。
**成员 B 写库时 `user_id` 必须用 `claims.sub`，不是 email。**

### 4.4 成员 D — `NotificationPublisher`（通知投递）

接口：`app/notification_client.py`，方法 `publish(notification) -> None`。

作用：新文件 `complete` 时，Member D 先用 `(file_id,user_id,species)` 确定性 ID
幂等 ensure pending inbox，再标记 file completed，最后调用 publisher。SAM 已将生产
publisher 接到单一 SNS Topic；成员 E 在此之上负责前端/站内通知体验。

通知数据模型见 §3.1，订阅/通知端点见 §5.8。

---

## 5. API 契约（成员 E 前端照此调用）

Base URL：本地 `http://localhost:8000`；云端是成员 A 提供的 API Gateway HTTP API
（ID 在部署时传入），所有公开路由都挂
`CognitoJWTAuthorizer`，请求头带 `Authorization: Bearer <id_token>`。

统一错误格式使用 FastAPI `detail`。owner 冲突返回
`{"detail":{"code":"FORBIDDEN_OWNER","message":"media is not owned by the authenticated user"}}`；
内部保留元数据冲突返回 `409` 且 `detail.code` 为 `METADATA_CONFLICT`。

**路由对齐**（成员 A 文档里的简称 → 我们的完整路径）：

| 成员 A 文档写法 | 我们的实际路径 | 说明 |
|-----------------|----------------|------|
| `POST /query` | `POST /query/by-tags` | 按 tags + 最低数量（AND） |
| — | `POST /query/by-species` | 按单个物种（A 文档未列） |
| — | `GET /query/by-thumbnail` | 缩略图 key→原图 key（A 文档未列） |
| `POST /query-by-file` | `POST /query/by-file` | 一致 |
| `POST /tags` | `POST /tags/edit` | 批量加/删 tag |
| `DELETE /files` | `POST /files/delete` | 批量删除 |

### 5.1 按标签查询（含最低数量，AND）

```
POST /query/by-tags
{"tags": {"dingo": 1, "wombat": 1}}
```

响应保留 `results/count`（图片给缩略图 key，视频给原图 key），并新增不含
owner ID 的 `items`。只有 `status=completed` 的归档记录会出现在查询结果中：

```json
{"results":["thumbnails/u1/a1.jpg","originals/u1/v1.mp4"],"count":2,"items":[{"file_id":"f1","file_type":"image","display_key":"thumbnails/u1/a1.jpg","original_key":"originals/u1/a1.jpg","thumbnail_key":"thumbnails/u1/a1.jpg","can_preview":true,"can_manage":true}]}
```

### 5.2 按物种查询（≥1 只）

```
POST /query/by-species
{"species": "magpie"}
```

响应同上。

### 5.3 缩略图 key → 原图 key

```
GET /query/by-thumbnail?key=thumbnails%2Fu1%2Fa1.jpg
```

`key` 也可为 `QUERY_INPUT_BUCKET` 的 trusted HTTPS/presigned URL；服务会只解析并
规范化为 key，不会抓取或记录 URL。非法引用返回 `422 INVALID_MEDIA_REFERENCE`，未找到
缩略图返回 `404 THUMBNAIL_NOT_FOUND`。成功响应保留 `original_key` 与 `file_id`，并新增完整的安全 `item`（字段同查询 `items`，供前端预览/管理）：
`{"original_key":"originals/u1/a1.jpg","file_id":"f1","item":{"file_id":"f1","file_type":"image","display_key":"thumbnails/u1/a1.jpg","original_key":"originals/u1/a1.jpg","thumbnail_key":"thumbnails/u1/a1.jpg","can_preview":true,"can_manage":true}}`

### 5.4 按上传文件查询（不落库）

```
POST /query/by-file        (multipart/form-data, 字段名 file)
```

响应：`{"results": [...], "count": N, "items": [...]}`。上传的查询文件**不会**被存进数据库。

### 5.5 批量改标签

```
POST /tags/edit
{"keys": ["originals/u1/a1.jpg"], "urls": ["https://bucket.s3.ap-southeast-2.amazonaws.com/originals/u1/a1.jpg?X-Amz-Signature=..."], "tags": ["dingo"], "operation": 1}
```

`operation`：`1`=添加，`0`=删除。`keys` 和 `urls` 可单独省略，但不能同时为空；它们会
规范化、去重后一起匹配。未知但有效的引用仍是 no-op。删除不存在的 tag 会被忽略（不报错）。

响应：`{"updated": 1, "matched_keys": ["originals/u1/a1.jpg"]}`

记录 owner 必须等于当前 Cognito `sub`；外部 owner 或混合 owner 请求整批返回 `403`，
且任何记录都不会被修改。传入的科学名会通过 `get_mapper().common_name()` 转成团队短名。

### 5.6 批量删除

```
POST /files/delete
{"keys": ["originals/u1/a5.jpg"], "urls": []}
```

响应：`{"deleted_db_records": 1, "storage_objects_removed": 2}`

记录 owner 必须等于当前 Cognito `sub`；外部 owner 或混合 owner 请求整批返回 `403`。
storage 删除发生在 metadata 删除之前，因此 storage 失败时 DB 记录仍保留。

### 5.7 内部元数据状态机（成员 B 上传/处理 Lambda 调用）

这 4 个端点对应成员 B 的 `docs/member-b/api-contracts.md`，是成员 B 写库的唯一入口。
每个请求都必须带 `X-Internal-Api-Key: <shared-secret>`。服务端未配置 secret 返回 `503`；
`detail.code` 为 `INTERNAL_AUTH_NOT_CONFIGURED`。header 缺失/错误返回 `401`，
`detail.code` 为 `INVALID_INTERNAL_API_KEY`。

**① 预约上传（reserve）**

```
POST /internal/uploads/reserve
{"file_id": "<uuid>", "user_id": "<cognito-sub>", "checksum": "<base64 sha256>",
 "filename": "wombat.jpg", "file_type": "image", "content_type": "image/jpeg",
 "size_bytes": 2849132, "object_key": "originals/<sub>/<uuid>/wombat.jpg",
 "status": "pending_upload"}
```

- 新预约 `201` → 返回 `file_id/object_key/status/reused=false`。
- 旧状态为 `pending_upload` / `failed` 且不可变元数据一致 → `201` 返回旧
  `file_id/object_key`、`reused=true`，供成员 B 重新 presign；failed 重置为 pending。
- 旧状态为 `processing` / `completed` → `409` + `existing_file_id`。
- filename/type/content-type/size 不一致 → `409 METADATA_CONFLICT`。
- DynamoDB 用 reservation table + transaction 保证并发唯一；删除文件同时释放 claim。
- 对已有 FilesTable，成员 A 必须暂停所有 Files/Reservations mutation（reserve、
  processing/complete/failed 回调和 delete）并用 `migrate_reservations.py` 执行
  verify → backfill → verify；运行时强一致 fallback transaction 只是 fail-closed 保护。

**② 获取处理租约（processing）**

```
POST /internal/files/{file_id}/processing
{"user_id": "<sub>", "object_key": "originals/.../wombat.jpg", "sequencer": "<S3事件序列号>"}
```

- `200 {"should_process": true, "state": "acquired"}` → 原子取得租约。
- `200 {"should_process": false, "state": "completed"}` → 已完成。
- `200 {"should_process": false, "state": "lease_active"}` → 已有活跃租约。
- 租约窗口 900 秒（对应成员 B 处理 Lambda 的 900s 超时）。
- `user_id` 或 `object_key` 与 reserve 记录不一致 → `409 METADATA_CONFLICT`，状态不变。

**③ 完成处理（complete，幂等 PUT）**

```
PUT /internal/files/{file_id}/complete
{"user_id": "<sub>", "file_type": "image",
 "original_key": "originals/.../wombat.jpg",
 "thumbnail_key": "thumbnails/.../thumbnail.jpg",     // 视频为 null
 "tags": {"wombat": 2},
 "detections": [{"species": "wombat", "confidence": 0.94}],
 "model_version": "speciesnet-v1", "status": "completed"}
```

- `200 {}` → 已标记 `completed`。重复调用幂等，不重复生效。
- `user_id`、`original_key` 或 `file_type` 与 reserve 记录不一致 →
  `409 METADATA_CONFLICT`，状态不变。
- `tags` key 和每条 detection 的 `species` 在写库/通知前统一映射为团队短名。

**④ 记录失败（failed，幂等 PUT）**

```
PUT /internal/files/{file_id}/failed
{"user_id": "<sub>", "error_code": "FRAME_EXTRACTION_FAILED",
 "message": "<诊断信息，自动截断到 240 字符>", "status": "failed"}
```

- `200 {}` → 已标记 `failed`。`error_code` 取值：`INVALID_MEDIA` /
  `FRAME_EXTRACTION_FAILED` / `INFERENCE_FAILED`。已 `completed` 的文件不会被降级为 `failed`。
- `user_id` 与 reserve 记录不一致 → `409 METADATA_CONFLICT`，状态不变。

### 5.8 订阅 & 通知（成员 E 前端调用）

**订阅一个物种**

```
POST /notifications/subscribe
{"species": "wombat"}
```

- `201 {"user_id": ..., "species": ..., "subscribed": true}`（幂等，重复订阅无副作用）。

**取消订阅**

```
DELETE /notifications/subscribe?species=wombat
```

- `200 {"user_id": ..., "species": ..., "subscribed": false}`（幂等）。

**列出我的订阅**

```
GET /notifications/subscriptions
```

- `200 {"species": ["wombat", "magpie"], "count": 2}`

**列出我的通知**

```
GET /notifications
```

- `200 {"notifications": [{"notification_id": ..., "user_id": ..., "file_id": ...,
  "species": "wombat", "object_key": "...", "created_at": "..."}], "count": 1}`（新的在前）

**触发时机**：当成员 B 对某个文件调用 `complete`（§5.7③）且该文件的 `tags` 里有
数量 ≥1 的物种时，Member D 会为**每个订阅了该物种的用户**写一条通知并调
`NotificationPublisher.publish` 投递（§4.4）。inbox 在 completed 前 ensure，故
mark_completed 失败时允许短暂 pre-completion pending；重试使用确定性 ID。completed
重放只 publish 已存在的 pending inbox，不重新读取当前订阅，也不给晚订阅者补发历史
通知。没有周期 worker/DLQ，恢复依赖自动/人工重放；投递为 at-least-once，SNS 成功但
delivered 更新失败时可能重复。
订阅/取消订阅的 `species` 也会在持久化前统一映射为团队短名。

---

## 6. 端到端流程（谁在哪个节点做什么）

```
用户上传文件
   └─> 成员 B 上传 Lambda：带 X-Internal-Api-Key 调 POST /internal/uploads/reserve（去重 + 预约，§5.7①）
          └─> S3 存原图 → ObjectCreated 事件触发处理 Lambda
          └─> 处理 Lambda：POST /internal/files/{id}/processing（取租约，§5.7②）
          └─> 抽帧/生成缩略图（成员 B）→ 调成员 C /infer 识别（§4.1）→ 得到 tags
          └─> 处理 Lambda：PUT /internal/files/{id}/complete（写结果，§5.7③）
                └─> 成员 D 触发器：匹配订阅 → 写通知 + publish（§5.8）
                └─> 失败则 PUT /internal/files/{id}/failed（§5.7④）
用户查询
   └─> 前端（成员 E）→ 成员 A 验证 token → 成员 D 的查询端点（§5.1–5.4）
用户订阅/查看通知
   └─> 前端（成员 E）→ POST /notifications/subscribe、GET /notifications（§5.8）
用户删除
   └─> 成员 D 删 DB 记录 + 按 owner 调成员 B 的 StorageClient 删 S3 对象（§4.2）
```

---

## 7. 本地快速验证

```bash
cd db-query-api
pip install -r requirements.txt
python seed.py
python -m uvicorn app.main:app --reload --port 8000
# 打开 http://localhost:8000/docs 有每个端点的在线调试表单
# 或导入 postman_collection.json
```

跑测试：`python -m pytest tests/ -v`
