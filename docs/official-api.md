# 官方接口依据与验证状态

核对日期：2026-09-19。使用 BrowserRig 在 Chrome 阅读微信客服独立后台文档。

| 主题 | 官方来源 | 实现依据 |
|---|---|---|
| 独立客服开发配置 | https://kf.weixin.qq.com/api/doc/path/93304 | 企业 ID + 客服 Secret 获取 access_token；回调 URL、Token、EncodingAESKey |
| 消息拉取 | https://kf.weixin.qq.com/api/doc/path/94745 | 通知事件后调用 `kf/sync_msg`；cursor、has_more、merged_msg |
| 素材下载 | https://kf.weixin.qq.com/api/doc/path/93349 | `media/get`；通过 media_id 获取二进制 |
| 客服列表 | https://kf.weixin.qq.com/api/doc/path/94746 | `kf/account/list`，offset/limit 分页 |
| 客户基础信息 | https://kf.weixin.qq.com/api/doc/path/95166 | `kf/customer/batchget`，白名单、48 小时窗口、会话上下文 |
| 企业状态说明 | https://kf.weixin.qq.com/api/doc/path/95168 | 指向企业授权接口，当前独立账号接入不实现服务商授权模式 |
| 回调加解密算法 | https://developer.work.weixin.qq.com/document/path/90968 | 共用的 AES-CBC、32 字节 PKCS#7、签名、接收方校验及公开测试向量 |
| 通用回调协议 | https://developer.work.weixin.qq.com/document/path/90930 | GET 原样返回解密的 echostr；POST 校验并解密事件 |

加解密算法参考企业微信协议文档，不代表选用了企业微信自建应用接入模式。业务认证和消息 API 按独立微信客服后台文档实现。

## 当前验证状态（2026-09-19）

- 未认证账号实际完成注册，后台当时显示累计接待 100 位客户额度；仍有平台分配的企业 ID。
- HTTPS 公网 IP 回调验证成功；HTTP 80 当时未通过且没有观察到到站请求，原因未定。这不是任意 IP 或证书均可用的承诺。
- 正式服务已启用，客服列表、消息拉取、真实文字和聊天记录写入生产 Memos、独立标签及个人空间已验证。
- 发送回执依据[发送消息接口](https://kf.weixin.qq.com/api/doc/path/94744)；真实接口已接受一次最终结果回执，不等同于手机端送达验收。
- 本机临时真实 Memos 已验证两种标签模式、笔记创建/更新、附件归属、私有访问及幂等恢复。
- 真实图片、语音、视频、文件和各种卡片尚需逐项真机验收，模拟测试不能替代官方实际返回数据验证。

生产配置与真实样本不在仓库内。公开加密测试向量来自腾讯文档，不是生产密钥。

详见[开通流程](onboarding.md)、[支持矩阵](message-support.md)、[开发记录](development-status.md)。
