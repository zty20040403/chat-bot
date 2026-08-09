from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.delivery import DeliveryStore
from src.plugins.ai_chat.message_ir import MessageBody, TextNode


class DeliveryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = DeliveryStore(":memory:", max_attempts=3, lease_seconds=30)
        self.addCleanup(self.store.close)
        self.scope = ConversationScope("onebot-v11", "group", "100")
        self.body = MessageBody((TextNode(0, "hello"),))

    def enqueue(self, key: str = "turn:1:chunk:0"):
        return self.store.enqueue(
            idempotency_key=key,
            source_scope_key=self.scope.key,
            target_scope=self.scope,
            body=self.body,
            turn_id=1,
            now=100,
        )

    def test_enqueue_is_idempotent_and_claim_is_leased(self) -> None:
        first, created = self.enqueue()
        second, duplicate_created = self.enqueue()
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.delivery_id, second.delivery_id)

        claimed = self.store.claim_due(now=100)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].status, "sending")
        self.assertEqual(claimed[0].attempts, 1)

    def test_timeout_parks_until_echo_reconciles(self) -> None:
        delivery, _created = self.enqueue()
        self.store.begin_direct_attempt(delivery.delivery_id, now=100)
        self.assertTrue(
            self.store.mark_ambiguous(delivery.delivery_id, "timeout", now=101)
        )
        self.assertEqual(self.store.claim_due(now=1000), [])

        reconciled = self.store.reconcile_echo(
            self.scope,
            self.body,
            native_message_id="9001",
            observed_at=102,
        )
        self.assertIsNotNone(reconciled)
        self.assertEqual(reconciled.status, "committed")  # type: ignore[union-attr]
        self.assertEqual(reconciled.native_message_id, "9001")  # type: ignore[union-attr]

    def test_retryable_failure_uses_bounded_attempts(self) -> None:
        delivery, _created = self.enqueue()
        claimed = self.store.claim_due(now=100)[0]
        self.assertTrue(
            self.store.mark_failed(
                claimed.delivery_id,
                "network",
                retryable=True,
                retry_seconds=10,
                now=101,
            )
        )
        self.assertEqual(self.store.claim_due(now=110), [])
        second = self.store.claim_due(now=111)[0]
        self.assertEqual(second.attempts, 2)

    def test_interrupted_send_is_ambiguous_after_reopen_semantics(self) -> None:
        delivery, _created = self.enqueue()
        self.store.begin_direct_attempt(delivery.delivery_id, now=100)
        count = self.store.park_interrupted_attempts(now=101)
        self.assertEqual(count, 1)
        self.assertEqual(
            self.store.get(delivery.delivery_id).status,  # type: ignore[union-attr]
            "ambiguous",
        )

    def test_expired_lease_is_parked_without_retry(self) -> None:
        delivery, _created = self.enqueue("expired")
        self.store.begin_direct_attempt(delivery.delivery_id, now=100)
        self.assertEqual(self.store.park_expired_attempts(now=129), 0)
        self.assertEqual(self.store.park_expired_attempts(now=130), 1)
        self.assertEqual(
            self.store.get(delivery.delivery_id).status,  # type: ignore[union-attr]
            "ambiguous",
        )
        self.assertEqual(self.store.claim_due(now=200), [])


if __name__ == "__main__":
    unittest.main()
