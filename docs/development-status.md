# 开发历史与验证方法

记录整理日期：2026-09-19。以下是既有工作的事实摘要，不代表实时部署或 GitHub 状态；后续工作通过[任务索引](tasks/INDEX.md)检索。

| 历史阶段 | 实际结果与记录 |
| --- | --- |
| 账号与回调 | 未认证账号 HTTPS 公网 IP 校验成功；初次 GET 探针 15 项测试通过。[详情](tasks/TASK-20260919-onboarding-callback.md) |
| 剪藏初版 | 37 项单元/模拟 + 1 项临时真实 Memos 测试通过，随后真实文字及聊天记录保存。[详情](tasks/TASK-20260919-clipping-pipeline.md) |
| 0.1.1 定制标签 | 41 项测试通过，个人空间、独立标签与纯正文生产回读验证。[详情](tasks/TASK-20260919-custom-memos-tags.md) |
| 0.1.2 结果回执 | 51 项测试通过，真实接口接受一次结果回执，没有重复剪藏。[详情](tasks/TASK-20260919-result-receipts.md) |
| 项目管理 | 脱敏公开仓库、官方样例告警处理。[详情](tasks/TASK-20260919-repository-security.md) |

## 本机验证方法

```sh
uv sync --frozen --group dev
uv run pytest -q
uv run ruff check .
```

0.1.2 的上述历史测试包含 49 项单元/模拟测试和 2 项可选真实 API 测试；不设置 TEST_MEMOS_BINARY 时后两项明确跳过。相关运行当时有两条 Starlette/httpx/anyio 弃用提示。

复现真实 API 测试时，先在兼容的定制 Memos 源码目录执行：

```sh
go build -o /tmp/wechat-kf-memos-test-server ./cmd/memos
```

再回到客服项目：

```sh
TEST_MEMOS_BINARY=/tmp/wechat-kf-memos-test-server uv run pytest -q
```

测试启动临时空数据实例、环回随机端口和临时账号/PAT，不接受远程 Memos URL，不读取生产数据库。历史测试使用 Memos 源码 63d58930，覆盖两个标签模式、私有笔记及附件、正文、归属和重试去重；测试退出终止进程。

这些是历史验证证据与复现方法；本次 Task 文档整理没有重新运行应用测试。真实媒体、卡片、手机端结果可见性及生产故障场景的验证边界见对应任务，不能由模拟测试推定已经真机验收。Docker 示例也未在本次历史验收中实测。
