# 微信客服剪藏到 Memos

通过**微信客服独立后台的官方 API**，将主动发送或合并转发的消息保存为 Memos 私有笔记。
一次消息对应一篇 Markdown 正文，保留发送者、时间、顺序和可获取的附件。

当前版本 **0.1.2**，定位为个人自用项目，对接定制版 Memos（个人版 0.2.0，官方 0.30.0 源码基线）。代码可以公开查看，但不是承诺兼容任意 Memos 的通用产品；尚未授予开源许可证。

2026-09-19 已部署 systemd 服务，开启本人白名单、独立标签和最终结果回执。未认证账号的 HTTPS 公网 IP 回调、真实文字及聊天记录保存、私有可见性和一次结果回执已验证。真实媒体与各类卡片仍需逐项验收。早期临时探针停用是历史阶段，当前正式服务已经启用。

## 项目资料

- [开通与接入流程](docs/onboarding.md)：未认证账号、企业 ID、HTTPS 回调、本人绑定。
- [需求与决策](docs/requirements.md)：本次讨论确定的产品行为与不做的事情。
- [定制 Memos 接口](docs/memos-contract.md)：个人空间、独立标签、笔记与附件。
- [安全与提交约定](docs/security.md)：公开仓库范围及敏感资料隔离。
- [开发验证记录](docs/development-status.md)、[消息支持矩阵](docs/message-support.md)、[回执规则](docs/receipts.md)。

## 已实现

- GET 回调地址验证，POST 签名校验、AES-CBC 解密、接收方校验和十分钟时间窗口。
- `kf_msg_or_event` 通知先持久化再应答；后台调用 `kf/sync_msg`，缓存 access_token。
- 游标与待处理消息在同一 SQLite 事务中提交，处理空分页、重复通知及重启恢复。
- 客服账号限制、发送者白名单；空白名单不能启动正式剪藏。
- 覆盖独立客服文档的文字、媒体、位置、小程序、视频号、商品/订单、合并记录；独立微信笔记不建笔记，仅回复接口未提供内容。另保留链接兼容解析。
- 顶层文字中的 `#tag` 转为 Memos 标签；合并记录默认 `#微信聊天记录`，历史聊天内标签不误归类。
- 会话进入、发送失败、撤回等授权事件去重归档，保留 30 天；支持客服列表和授权客户信息只读导出。
- 完整规则及能力边界见 [消息与标签](docs/message-support.md)。
- 正文只保留消息内容；不加剪藏标题、来源、转发时间或接口辅助字段。不支持或缺失的内容明确标注。
- `MEMOS_TAG_MODE=explicit` 适配定制版独立标签；`markdown` 保留上游兼容。定制版不发送空间请求头时进入默认“个人”空间。
- 通过 Memos REST API 写入正文和附件，不直接读写 Memos 数据库。
- 根据来源消息生成固定 memo/attachment ID，重试先查已存在资源，避免写成功后断线导致重复创建。
- 附件先关联私有笔记，再完善正文；上传失败时保留正文和“附件处理中”标记。
- 最多 12 次自动重试，退避最长一小时；失败任务保留，支持人工重新排队。

## 本地启动

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```sh
uv sync --frozen
uv run python -m wechat_kf_memos init-env
```

上述命令创建权限为 `600` 的 `.env`，生成回调 Token 和 EncodingAESKey，**不会打印密钥**。
用本地编辑器填入微信客服后台「企业信息」中的企业 ID。不要提交 `.env`、状态数据库或真实聊天样本。

```sh
uv run --env-file .env uvicorn wechat_kf_memos.app:create_app --factory --host 127.0.0.1 --port 8080 --no-access-log
```

默认 `MODE=verify`，只用于 GET 回调验证。回调地址路径为 `/wechat/callback`。
`/healthz` 仅表示进程存活，**不代表 API 连通或剪藏成功**。

verify 模式收到新消息通知返回 503，避免假装已经保存；因此只短暂用于开通验证，不能长期作为已启用的客服服务。

## 官方后台配置顺序

1. 独立微信客服后台 → 开发配置 → 开始使用。
2. 填入公网可达回调地址以及本地 `.env` 中的 Token、EncodingAESKey。
3. 使用 HTTPS 回调；本次未认证账号已实际通过 IP 回调验证。其他账号仍需完成自己的回调校验。
4. 在本机填写客服 Secret、客服账号 ID（`open_kfid`）。所有配置均使用独立后台的信息，不混用企业微信自建应用 Secret。
5. 自己发送一条不含隐私的测试文字，用下列本机命令发现候选 `external_userid`：

   ```sh
   uv run --env-file .env python -m wechat_kf_memos discover-users
   ```

   命令仅输出发送者标识、不显示聊天正文，不修改拉取游标、不自动授权。确认哪个标识属于自己后填入 `WECHAT_ALLOWED_USERS`。
6. 填入 Memos URL、独立访问令牌；设置 `MEMOS_TAG_MODE=explicit`、`RECEIPTS_ENABLED=true`、`MODE=clip` 并重启。
7. 用文字、两人合并聊天记录和含图片记录分别验证；核对正文、私有可见性、附件和去重后再正式使用。

服务不会自动完成账号注册、付费认证或修改客服后台。本次未认证账号已通过 HTTPS 公网 IP 回调校验；HTTP 80 未通过。该结果仅代表本次账号与网络环境，不代表任意 IP、证书或账号均可用。

## Docker 部署示例（尚未实测）

当前实际部署使用宿主机 venv + systemd；以下是可选示例，不代表已验收。

```sh
docker compose up -d --build
```

默认只绑定 `127.0.0.1:8080`，通过 Nginx 暴露精确回调路径。参考 [Nginx 配置示例](docs/nginx.conf.example)。
不要直接覆盖现有 Memos 的站点或证书配置。没有必要公开 Memos 的容器端口或数据库端口。

容器中的 `127.0.0.1` 指容器本身。如果 Memos 只监听宿主机环回地址，应按实际部署选择：宿主机直接运行剪藏服务，或给两个服务配置私有 Docker 网络并用服务名访问。
示例 `.env` 的 `host.docker.internal` 不能保证访问到 Linux 宿主机上只绑定环回地址的服务，必须在部署时调整。

Memos HTTPS 可通过 `MEMOS_CA_FILE` 指定可信 CA 文件（容器需只读挂载该文件）；不关闭 TLS 校验。
`MEMOS_PUBLIC_URL` 只用于生成附件链接，不填则使用 Memos 同源相对路径。

仅运行一个进程、一个容器实例；本地文件锁会拒绝多个进程共用状态目录。
Docker 运行用户为 UID 10001，默认使用命名卷保存 `/data`。

## 存储与恢复

- `data/state.db`：拉取游标、回调临时 token、待处理消息、重试状态、成功消息 ID 和授权事件。事件表按 30 天保留，在拉取提交时清理。
- 未授权发送者的正文不会入队。完成任务清除队列中的原始正文，保留 ID 用于去重。
- 附件在内存中暂存（单个最多 20 MiB，base64 编码会额外占用内存），上传后只由 Memos 长期保管。
- **目前未做附件落盘缓存**。失败重试时需要再次向微信获取附件；微信素材过期后可能无法补回。若需要跨较长故障窗口恢复附件，后续可增加上传成功即删除的临时磁盘缓存。
- SQLite 和 WAL 仍可能含待处理内容与历史页面，整个 data 目录都应视为敏感资料；清除记录不等于取证级擦除。
- 备份剪藏状态时先停止剪藏服务并备份整个 data 目录；Memos 正文和附件由 Memos 自身备份方案负责。

```sh
uv run --env-file .env python -m wechat_kf_memos status
uv run --env-file .env python -m wechat_kf_memos retry --job-id '<失败消息ID>'
```

错误日志只记录异常类型，避免 HTTP 异常把含 access_token 的 URL 打印出来。Nginx 和 Uvicorn 示例均关闭回调访问日志。

## 兼容与待完成

- API 适配依据 Memos v0.30.0 基线及当前个人版源码：`/api/v1/memos`、`/api/v1/attachments`、`memoId`、`attachmentId`。
- 已在本机临时真实 Memos 实例（源码 `63d58930`）验证创建/更新、自定义 ID、标签、附件归属、私有访问和重试去重；生产文字、聊天记录、个人空间和独立标签也已验证，其他版本不能直接视为兼容。
- 长聊天可能超过 Memos 配置的正文限制。当前保留失败任务，不静默截断；暂未实现拆分笔记。
- 语音不转文字，图片不 OCR，文章不抓全文。合并转发内媒体是否完整返回需真机验证。
- 正文保留聊天字面含义，转义 Markdown/HTML 控制字符，避免聊天里的标题、图片语法改变笔记结构。
- 可开启最终结果回执，见 [回执规则](docs/receipts.md)。未实现管理员告警、网页管理、AI 检索、发布流水线或自动清理失败任务。
- 轮询补偿默认 300 秒，回调触发即时拉取；无 token 拉取有严格频率限制，正式联调后按官方额度调整。
- 项目面向个人自用；公开代码不包含生产配置或数据，尚未选择开源许可证。

## 验证

```sh
uv sync --frozen --group dev
uv run pytest -q
uv run ruff check .
```

测试覆盖腾讯公开加密样例、篡改/错接收方/旧请求拒绝、XML 实体拒绝、回调限流大小、队列事务及重启、白名单、空分页、合并记录渲染、token 更新、媒体大小限制、远端写成功但响应丢失的幂等恢复。
默认运行 49 项单元/模拟接口测试；另有两项可选测试启动本机临时真实 Memos，合计 51 项已实测通过。全部测试不会访问你的微信账号或生产 Memos。复现方式和实际结果见 [开发验证记录](docs/development-status.md)。

接口来源与验证边界见 [官方接口依据](docs/official-api.md)。

## 最终结果回执

`RECEIPTS_ENABLED=true` 开启。成功只回复“类型保存成功”；不回复“收到”，不带标题、标签或链接。失败在自动尝试结束后回复具体环节、HTTP 状态/微信错误码及尝试次数。独立微信笔记直接回复不支持原因，不创建占位笔记。详情见 [回执规则](docs/receipts.md)。
