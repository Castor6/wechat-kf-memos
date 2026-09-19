# 定制版 Memos 接口约定

目标为个人版 Memos 0.2.0（官方 0.30.0 源码基线），已核对定制 memo_service.proto、memo_service.go、personal_space.go 的实现。本机临时合约测试及生产文字/聊天记录回读已验证相关行为；不能据此宣称任意上游版本兼容。

| 操作 | 请求与约定 |
|---|---|
| 认证 | Authorization: Bearer 专用 PAT；令牌放环境文件 |
| 创建笔记 | POST /api/v1/memos，查询参数 memoId 为来源生成的固定 ID；正文 content，visibility=PRIVATE |
| 独立标签 | MEMOS_TAG_MODE=explicit 时发送 tags 数组和 explicitTags=true，不向正文追加标签 |
| 更新笔记 | PATCH /api/v1/memos/{id}，updateMask=content,visibility,tags；保持 PRIVATE 及独立标签 |
| 查询去重 | GET /api/v1/memos/{id}；已存在需检查 PRIVATE，不盲目重建 |
| 上传附件 | POST /api/v1/attachments，attachmentId 为固定 ID；filename、type、base64 content、memo 指向所属笔记 |
| 附件复用 | GET /api/v1/attachments/{id}，验证 memo 归属；附件链接为 /file/{attachment name}/{编码文件名} |
| 空间 | 不发送 X-Memos-Space，返回 space 为空代表默认个人空间；浏览器当前选择不影响桥接 |

先创建私有笔记，再关联媒体，最后更新完整正文。已有笔记而附件失败时保留可恢复状态并给出部分保存结果。始终使用 API，不直接访问 Memos 数据库。

markdown 模式保留正文末尾标签兼容路径；本项目当前生产配置使用 explicit。MEMOS_PUBLIC_URL 仅影响附件链接，回执不包含笔记链接。自签证书通过 MEMOS_CA_FILE 提供可信 CA，不能关闭 TLS 验证。

真实 API 测试启动临时本地空实例、创建临时账号和令牌，验证两种标签模式、创建更新、附件访问、私有性和去重；不会使用生产 URL、配置或数据库。运行方式见 development-status.md。
