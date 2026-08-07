from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from src.plugins.ai_chat.message_ir import (
    ForwardNode,
    MediaNode,
    MessageBody,
    MentionNode,
    TextNode,
    UnsupportedNode,
    body_from_json,
    body_to_json,
    render_fallback_text,
    render_prompt_text,
    resolve_mentions,
)
from src.plugins.ai_chat.message_lowering import (
    OutboundCapabilities,
    lower_message,
)
from src.plugins.ai_chat.onebot_codec import (
    compose_onebot_reply,
    decode_onebot_message,
    render_api_attachments,
    render_onebot_body,
)


class MessageIRTests(unittest.TestCase):
    def test_onebot_round_trip_preserves_supported_segments(self) -> None:
        original = Message(
            [
                MessageSegment.text("你好 "),
                MessageSegment.at(123),
                MessageSegment("face", {"id": "66", "raw": {"faceText": "爱心"}}),
                MessageSegment(
                    "file",
                    {
                        "file": "notes.pdf",
                        "file_id": "file-1",
                        "file_size": 42,
                    },
                ),
            ]
        )

        decoded = decode_onebot_message(original)
        restored = render_onebot_body(decoded.body)

        self.assertEqual(
            [(segment.type, dict(segment.data)) for segment in restored],
            [(segment.type, dict(segment.data)) for segment in original],
        )
        self.assertIn("@群成员", render_fallback_text(decoded.body))
        self.assertIn("notes.pdf", render_fallback_text(decoded.body))

    def test_prompt_uses_canonical_principal_and_media_handles(self) -> None:
        decoded = decode_onebot_message(
            [
                {"type": "at", "data": {"qq": "123", "name": "Alice"}},
                {
                    "type": "image",
                    "data": {"file": "photo.jpg", "summary": "截图"},
                },
            ]
        )
        resolved = resolve_mentions(
            decoded.body,
            lambda native_id, display: 7,
        )
        prompt = render_prompt_text(resolved, canonical_message_id=12)

        self.assertIn("[@#7: Alice]", prompt)
        self.assertIn("[image#12.1: 截图]", prompt)

    def test_versioned_json_round_trip_handles_raw_bytes(self) -> None:
        body = decode_onebot_message(
            Message([MessageSegment.record(b"silk-data")])
        ).body
        restored = body_from_json(body_to_json(body))

        self.assertEqual(restored, body)
        self.assertTrue(restored.has_media("audio"))

    def test_unknown_segment_always_has_text_fallback(self) -> None:
        body = decode_onebot_message(
            [{"type": "future_type", "data": {"value": 1}}]
        ).body

        self.assertIsInstance(body.nodes[0], UnsupportedNode)
        self.assertEqual(render_fallback_text(body), "[future_type]")
        rendered = render_onebot_body(body)
        self.assertEqual([segment.type for segment in rendered], ["text"])
        self.assertEqual(rendered.extract_plain_text(), "[future_type]")

    def test_sourceless_media_degrades_instead_of_emitting_invalid_segment(self) -> None:
        body = MessageBody((MediaNode(0, "image", description="截图"),))

        rendered = render_onebot_body(body)

        self.assertEqual([segment.type for segment in rendered], ["text"])
        self.assertEqual(rendered.extract_plain_text(), "[图片:截图]")

    def test_capability_lowering_is_auditable_and_chunks_utf8_safely(self) -> None:
        body = MessageBody(
            (
                MentionNode(0, "123", "Alice"),
                TextNode(1, "你好世界abcdef"),
                MediaNode(2, "image", source="photo.jpg"),
            )
        )
        lowered = lower_message(
            body,
            OutboundCapabilities(
                mention="text",
                image="drop",
                max_text_bytes=6,
            ),
            destination_platform="text-only",
        )

        self.assertTrue(any(note.node_kind == "MentionNode" for note in lowered.notes))
        self.assertTrue(any(note.action == "drop" for note in lowered.notes))
        for chunk in lowered.chunks:
            text = render_fallback_text(chunk)
            self.assertLessEqual(len(text.encode("utf-8")), 6)
        self.assertEqual(
            "".join(render_fallback_text(chunk) for chunk in lowered.chunks),
            "@Alice你好世界abcdef",
        )

    def test_forward_node_round_trips_but_lowers_to_text(self) -> None:
        body = MessageBody((ForwardNode(0, "forward-1", 3),))
        restored = body_from_json(body_to_json(body))
        rendered = render_onebot_body(restored)

        self.assertEqual(restored, body)
        self.assertEqual(rendered.extract_plain_text(), "[合并转发:3 条]")

    def test_voice_reply_is_not_quoted_or_mentioned(self) -> None:
        reply = compose_onebot_reply(
            MessageSegment.record(b"voice"),
            reply_native_message_id=99,
            mention_native_user_id=123,
        )

        self.assertEqual([segment.type for segment in reply], ["record"])

    def test_text_reply_is_quoted_and_mentions_sender(self) -> None:
        reply = compose_onebot_reply(
            "收到",
            reply_native_message_id=99,
            mention_native_user_id=123,
        )

        self.assertEqual(
            [segment.type for segment in reply],
            ["reply", "at", "text", "text"],
        )

    def test_file_attachment_exposes_only_canonical_handle(self) -> None:
        body = decode_onebot_message(
            [
                {
                    "type": "file",
                    "data": {
                        "file": "report.pdf",
                        "file_id": "abc",
                        "file_size": "9",
                    },
                }
            ]
        ).body

        attachments = render_api_attachments(body, 15)

        self.assertEqual(attachments[0]["handle"], "file#15.0")
        self.assertEqual(attachments[0]["file_size"], 9)
        self.assertNotIn("file_id", attachments[0])
        self.assertIsInstance(body.nodes[0], MediaNode)


if __name__ == "__main__":
    unittest.main()
