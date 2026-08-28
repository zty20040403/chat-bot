from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import nonebot

nonebot.init()

from src.plugins.ai_chat.alert_notifier import (
    ActivityAlert,
    AlertNotificationService,
    format_alert_notification,
)


class Logger:
    def info(self, _message: object, *args: object, **kwargs: object) -> None:
        pass

    def warning(self, _message: object, *args: object, **kwargs: object) -> None:
        pass


class Bot:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_group_msg(self, **data: Any) -> dict[str, int]:
        self.calls.append(data)
        return {"message_id": 1}


def alert(
    fingerprint: str,
    *,
    name: str = "TailnetLinkPacketLoss",
    severity: str = "warning",
    instance: str = "h610",
    summary: str = "h610 到 tank 丢包 30%",
    starts_at: str = "2026-08-28T02:20:55.317Z",
) -> ActivityAlert:
    return ActivityAlert(
        name=name,
        severity=severity,
        instance=instance,
        peer="tank",
        summary=summary,
        description="链路连续丢包，请检查网络路径。",
        starts_at=starts_at,
        fingerprint=fingerprint,
    )


class AlertNotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_poll_only_establishes_baseline(self) -> None:
        current = [alert("existing")]
        bot = Bot()
        with tempfile.TemporaryDirectory() as directory:
            service = AlertNotificationService(
                alertmanager_url="http://alertmanager",
                group_id=611798505,
                check_seconds=30,
                state_path=Path(directory) / "alerts.json",
                logger=Logger(),
                fetcher=lambda: _result(current),
                bot_provider=lambda: [bot],
            )

            delivered = await service.run_once()

        self.assertEqual(delivered, 0)
        self.assertEqual(bot.calls, [])

    async def test_new_alert_mentions_all_and_is_not_repeated(self) -> None:
        current = [alert("existing")]
        bot = Bot()
        with tempfile.TemporaryDirectory() as directory:
            service = AlertNotificationService(
                alertmanager_url="http://alertmanager",
                group_id=611798505,
                check_seconds=30,
                state_path=Path(directory) / "alerts.json",
                logger=Logger(),
                fetcher=lambda: _result(current),
                bot_provider=lambda: [bot],
            )
            await service.run_once()
            current.append(
                alert(
                    "down",
                    name="HostUnreachable",
                    severity="critical",
                    instance="tank",
                    summary="tank 抓不到了",
                    starts_at="2026-08-28T03:00:00Z",
                )
            )

            delivered = await service.run_once()
            duplicate = await service.run_once()

        self.assertEqual(delivered, 1)
        self.assertEqual(duplicate, 0)
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0]["group_id"], 611798505)
        message = bot.calls[0]["message"]
        self.assertEqual(message[0].type, "at")
        self.assertEqual(message[0].data["qq"], "all")
        self.assertIn("tank 服务器寄了", str(message))
        self.assertIn("问题等级：严重（critical）", str(message))
        self.assertIn("2026-08-28 11:00:00", str(message))

    async def test_persisted_state_prevents_replay_after_restart(self) -> None:
        current = [alert("existing")]
        bot = Bot()
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "alerts.json"
            first = AlertNotificationService(
                alertmanager_url="http://alertmanager",
                group_id=611798505,
                check_seconds=30,
                state_path=state_path,
                logger=Logger(),
                fetcher=lambda: _result(current),
                bot_provider=lambda: [bot],
            )
            await first.run_once()
            restarted = AlertNotificationService(
                alertmanager_url="http://alertmanager",
                group_id=611798505,
                check_seconds=30,
                state_path=state_path,
                logger=Logger(),
                fetcher=lambda: _result(current),
                bot_provider=lambda: [bot],
            )

            delivered = await restarted.run_once()

        self.assertEqual(delivered, 0)
        self.assertEqual(bot.calls, [])

    def test_warning_notification_uses_calm_but_urgent_wording(self) -> None:
        text = format_alert_notification([alert("warning")])

        self.assertIn("【活动告警｜警告】", text)
        self.assertIn("需要尽快看一下", text)
        self.assertIn("发生了什么：链路连续丢包", text)


async def _result(value: list[ActivityAlert]) -> list[ActivityAlert]:
    return list(value)


if __name__ == "__main__":
    unittest.main()
