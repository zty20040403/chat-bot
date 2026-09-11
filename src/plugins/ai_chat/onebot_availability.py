"""Check QQ account availability separately from adapter connectivity."""
from __future__ import annotations

import asyncio
from nonebot.adapters.onebot.v11 import Bot


async def delivery_blocker(bot: Bot, *, pending_notice: str = "消息未发送") -> str | None:
    try:
        status = await asyncio.wait_for(bot.call_api("get_status"), timeout=8)
    except Exception:
        return f"暂时无法确认 QQ 在线状态，{pending_notice}，等待连接恢复后重试。"
    if not isinstance(status, dict) or type(status.get("online")) is not bool:
        return f"QQ 返回的在线状态不完整，{pending_notice}，等待状态恢复后重试。"
    if status["online"] is False:
        return f"QQ 账号当前离线，{pending_notice}；请在 NapCat 恢复登录。"
    if status.get("good") is False:
        return f"QQ 适配器状态异常，{pending_notice}，等待恢复后重试。"
    return None


async def file_delivery_blocker(bot: Bot) -> str | None:
    return await delivery_blocker(bot, pending_notice="文件未上传")
