"""Check QQ account availability separately from adapter connectivity."""
from __future__ import annotations

import asyncio
from nonebot.adapters.onebot.v11 import Bot


async def file_delivery_blocker(bot: Bot) -> str | None:
    try:
        status = await asyncio.wait_for(bot.call_api("get_status"), timeout=8)
    except Exception:
        return "暂时无法确认 QQ 在线状态，文件未上传，等待连接恢复后重试。"
    if not isinstance(status, dict) or type(status.get("online")) is not bool:
        return "QQ 返回的在线状态不完整，文件未上传，等待状态恢复后重试。"
    if status["online"] is False:
        return "QQ 账号当前离线，文件未上传；请在 NapCat 恢复登录。"
    if status.get("good") is False:
        return "QQ 适配器状态异常，文件未上传，等待恢复后重试。"
    return None
