# 公网 IP 回调验证

日期：2026-09-19，时区 Asia/Shanghai。

本文仅记录最初临时探针阶段；随后已部署正式剪藏服务，当前状态见 README.md。

## 结论

微信客服**独立后台**的未认证账号，使用 `https://<公网IPv4>/wechat/callback` 成功完成了真实回调校验。
无需为本次校验新增域名；沿用服务器既有 HTTPS 证书，未修改或重签。
这只是本次环境的实测结果，不推断微信对所有证书的校验策略。

## 实测证据

- HTTP 80：后台提示“openapi回调地址请求不通过”；公网访问得到空响应，Nginx 没有对应外部请求。服务器环回访问探针返回预期 403，说明本地服务可用；公网 HTTP 失败的具体原因未确认。
- HTTPS 443：12:37:30，Nginx 记录官方校验 GET 请求 `/wechat/callback` 返回 200。
- 同一时刻，探针记录 `callback_verification_succeeded`，表明签名、接收方和 AES 解密均通过，并原样回传 echostr。
- Chrome 页面从回调设置返回开发配置页，操作项变为“停用”，确认后台接受配置并启用 API。
- 使用 `scripts/verify_callback.py`，只提供 GET 验证；POST 返回 503，未拉取消息、未获取客服 Secret、未配置 Memos 令牌，也未执行 Memos 写入。

## 验证后的状态

- 已点击停用 API，刷新页面后确认显示“启用”，没有“停用”。
- 已停止临时 systemd 服务，删除临时部署目录及其远端回调密钥。
- 已撤除 HTTP 80 站点、HTTPS 回调 location 和专用日志格式配置，恢复原 Nginx 文件并比较一致。
- 临时 80/18080 监听均消失；Nginx active，Memos 容器 ID 和启动时间保持不变。
- Memos HTTPS 首页 200，匿名笔记 API 401，既有证书校验通过。
- 本地受限运维目录保留回调配置；项目内不记录账号标识、真实 IP 或凭据。

本地自动测试新增 GET-only 探针验证；15 项测试及 Ruff 检查通过。没有测试真实消息拉取、合并记录媒体下载或 Memos 写入。
