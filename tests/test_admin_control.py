from __future__ import annotations

import unittest

from src.plugins.ai_chat.admin_control import (
    AdminControlStore,
    AdminVersionConflict,
    parse_expected_version,
)


class AdminControlStoreTests(unittest.TestCase):
    def test_mutation_versions_and_audits_state(self) -> None:
        store = AdminControlStore()
        result = store.mutate(
            "groups",
            expected_version=0,
            actor="Kenneth",
            action="group.enabled.set",
            target="930690526",
            before={"enabled": True},
            operation=lambda version: {"enabled": False, "version": version},
        )

        self.assertEqual(result.resource_version, 1)
        self.assertEqual(store.version("groups"), 1)
        self.assertEqual(store.audit()[0]["after"]["enabled"], False)
        with self.assertRaises(AdminVersionConflict):
            store.mutate(
                "groups",
                expected_version=0,
                actor="Kenneth",
                action="group.enabled.set",
                operation=lambda _version: None,
            )

    def test_if_match_parser_accepts_http_etag_forms(self) -> None:
        self.assertEqual(parse_expected_version('"12"'), 12)
        self.assertEqual(parse_expected_version('W/"13"'), 13)
        self.assertIsNone(parse_expected_version(None))
        with self.assertRaises(ValueError):
            parse_expected_version("latest")


if __name__ == "__main__":
    unittest.main()
