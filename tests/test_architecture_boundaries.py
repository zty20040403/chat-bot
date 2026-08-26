from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1] / "src" / "plugins" / "ai_chat"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_application_does_not_depend_on_chat_platform_sdk(self) -> None:
        self._assert_no_imports(
            PLUGIN_ROOT / "application",
            forbidden=("nonebot", ".adapters", "..adapters"),
        )

    def test_storage_is_independent_from_platform_and_use_cases(self) -> None:
        self._assert_no_imports(
            PLUGIN_ROOT / "storage",
            forbidden=("nonebot", ".application", "..application", ".adapters"),
        )

    def test_workers_do_not_import_matchers_or_platform_sdk(self) -> None:
        self._assert_no_imports(
            PLUGIN_ROOT / "workers",
            forbidden=("nonebot", ".matchers", "..matchers", ".adapters"),
        )

    def test_architecture_packages_exist(self) -> None:
        for package in (
            "adapters",
            "application",
            "agent",
            "tools",
            "storage",
            "workers",
        ):
            self.assertTrue((PLUGIN_ROOT / package / "__init__.py").is_file())
            imported = importlib.import_module(
                f"src.plugins.ai_chat.{package}"
            )
            self.assertIsNotNone(imported)

    def _assert_no_imports(
        self,
        root: Path,
        *,
        forbidden: tuple[str, ...],
    ) -> None:
        violations: list[str] = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    prefix = "." * node.level
                    names = [prefix + (node.module or "")]
                else:
                    continue
                for name in names:
                    if any(name == item or name.startswith(item + ".") for item in forbidden):
                        violations.append(f"{path.name}:{node.lineno} imports {name}")
        self.assertEqual(violations, [])
