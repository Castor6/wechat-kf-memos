# 移交到 Memos 内置微信客服

决策日期：2026-09-19。用户选择将客服逻辑迁为定制 Memos 的 Go 内置模块；这不是把 Python 目录搬进同一仓库，也不是在 Memos 容器里同时托管两个进程。

主开发目录：~/Code/memos。主方案：该项目 docs/wechat-kf-integration.md。主任务：docs/tasks/TASK-20260919-wechat-kf-integration.md。审阅入口：[Memos PR #23](https://github.com/Castor6/memos/pull/23)；本仓库配套：[PR #1](https://github.com/Castor6/wechat-kf-memos/pull/1)。

本仓库保留 ~/Code/wechat-kf-memos 的旧实现、测试及历史事实，供行为等价迁移和切换前维护。新增内置功能、Task 和发布在 Memos 中完成。当前 0.1.2 的 API/PAT、独立 SQLite、systemd 和 Docker 示例均属于旧架构，不能当成内置实现的配置说明。

## 移交内容

- [需求](requirements.md)、[消息/标签](message-support.md)、[回执](receipts.md)、[接口约定](memos-contract.md)：保留现有行为边界。
- src/wechat_kf_memos 与 tests：官方加解密向量、消息类型、幂等、重试、白名单、回执窗口、发送不确定结果等实现参照。
- [任务索引](tasks/INDEX.md)：既有开发与验收事实，包括尚未真机覆盖的范围。
- 游标、去重、待处理任务、笔记关联、回执窗口和结果：通过受保护的迁移工具导入；保留稳定 ID，避免重复笔记或回复。

2026-09-19 已在 Memos 项目开始第一阶段 Go 核心实现与临时数据测试，具体范围以 Memos 主任务记录为准；本仓库未改 Python 运行代码，未停止旧服务或更改微信回调。切换须先完成验证与私有备份，停止旧消费者后转交内置模块，不能让两个消费者同时处理。旧仓库是否归档后续另行决定。
