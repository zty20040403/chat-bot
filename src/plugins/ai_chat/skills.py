from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    title: str
    summary: str
    body: str


class SkillRegistry:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def list(self) -> list[Skill]:
        if not self.directory.is_dir():
            return []
        skills: list[Skill] = []
        for path in sorted(self.directory.glob("*.md")):
            skill = self._load(path)
            if skill is not None:
                skills.append(skill)
        return skills

    def get(self, name: str) -> Skill | None:
        normalized = name.strip().casefold()
        return next(
            (
                skill
                for skill in self.list()
                if normalized in {skill.name.casefold(), skill.title.casefold()}
            ),
            None,
        )

    def prompt_index(self) -> str:
        skills = self.list()
        if not skills:
            return ""
        lines = [
            "[技能目录]",
            "这里只是简要索引。要执行对应流程时先调用 use_skill(name)，不要凭标题猜步骤。",
        ]
        lines.extend(f"- {skill.name}: {skill.summary}" for skill in skills)
        return "\n".join(lines)

    @staticmethod
    def _load(path: Path) -> Skill | None:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        lines = text.splitlines()
        title = lines[0].removeprefix("#").strip() or path.stem
        summary = ""
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.lower().startswith("summary:"):
                summary = stripped.split(":", 1)[1].strip()
                break
        if not summary:
            summary = next(
                (line.strip() for line in lines[1:] if line.strip()),
                title,
            )
        return Skill(
            name=path.stem,
            title=title,
            summary=summary[:200],
            body=text,
        )
