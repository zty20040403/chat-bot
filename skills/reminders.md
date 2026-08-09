# Reminders
Summary: 创建、查看和取消重启后仍保留的群聊或私聊提醒。

1. 用户明确要求“到某时提醒”时调用 `reminder_set`，不要只口头答应。
2. 把相对时间按 system prompt 中的 Asia/Shanghai 当前日期换算为带时区 ISO 8601。
3. 内容要短而可独立理解，保留用户真正需要做的事，不添加秘密或敏感凭据。
4. 创建后告诉用户规范 `reminder#` 句柄和绝对触发时间。
5. 查看用 `reminder_list`，取消必须使用当前会话可见的 `reminder#`。
6. 提醒是宿主持久状态，`/clear` 不会取消；不再需要时必须显式取消。
