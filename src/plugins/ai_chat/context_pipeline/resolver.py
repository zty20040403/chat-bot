from __future__ import annotations

import re
from datetime import datetime

from ..conversation_scope import ConversationScope
from ..ledger import CanonicalMessage, MessageLedger
from .models import ContextCandidate, TurnContextPlan


_FOLLOW_UP_PATTERNS = (
    r"你(?:怎么|咋)看",
    r"你觉得(?:呢|怎么样)?",
    r"你认为(?:呢|怎么样)?",
    r"^(?:那|这个|那个|这件事|这话|上面)(?:呢|怎么样|怎么说|啥意思)?[？?]?$",
    r"^(?:为什么|为啥|然后呢|后来呢|真的吗|是吗|对吗|咋办|怎么办)[？?]?$",
    r"^(?:怎么说|你说呢|评价一下|锐评|细说|展开说说|接着说)[吧呢？?]*$",
)
_QUESTION_MARKERS = (
    "?",
    "？",
    "吗",
    "么",
    "怎么",
    "为什么",
    "为啥",
    "如何",
    "是否",
    "哪个",
    "什么",
    "谁",
    "几",
    "能不能",
    "要不要",
    "是不是",
    "咋",
)
_QUERY_FILLERS = (
    "你觉得",
    "你认为",
    "你怎么看",
    "你咋看",
    "这个",
    "那个",
    "这件事",
    "评价一下",
    "锐评",
    "一下",
    "呢",
    "吗",
)
_LOW_INFORMATION = re.compile(
    r"^(?:哈+|呵+|啊+|哦+|嗯+|草+|笑死|确实|好吧|行吧|可以|收到|[?？!！。.，,~～…]+)$",
    re.IGNORECASE,
)


class ReferenceResolver:
    def __init__(
        self,
        *,
        candidate_limit: int = 40,
        related_limit: int = 8,
    ) -> None:
        self.candidate_limit = min(max(int(candidate_limit), 8), 100)
        self.related_limit = min(max(int(related_limit), 2), 20)

    def resolve(
        self,
        ledger: MessageLedger,
        scope: ConversationScope,
        *,
        current_message_id: int,
        current_text: str,
        current_native_user_id: str | int,
        now: int | None = None,
    ) -> TurnContextPlan:
        current = ledger.get_in_scope(scope, current_message_id)
        current_principal_id = current.sender_principal_id if current else None
        recent = [
            message
            for message in ledger.recent_in_scope(scope, self.candidate_limit + 1)
            if message.canonical_message_id < current_message_id
            and message.message_kind != "command"
            and bool(message.prompt_text.strip())
        ]
        explicit_target = (
            ledger.get_in_scope(scope, current.reply_to_canonical_message_id)
            if current is not None
            and current.reply_to_canonical_message_id is not None
            else None
        )
        if explicit_target is not None:
            related = self._related_ids(
                recent,
                explicit_target.canonical_message_id,
                current_message_id,
            )
            rendered = self._render(
                explicit_target,
                self._messages_by_ids(recent, related),
                ambiguous=False,
            )
            return TurnContextPlan(
                scope_key=scope.key,
                current_message_id=current_message_id,
                current_principal_id=current_principal_id,
                focus_message_id=explicit_target.canonical_message_id,
                confidence=1.0,
                reason_codes=("explicit_reply", "same_scope"),
                related_message_ids=related,
                candidates=(
                    ContextCandidate(
                        explicit_target.canonical_message_id,
                        100.0,
                        ("explicit_reply", "same_scope"),
                    ),
                ),
                rendered_context=rendered,
            )

        follow_up = self._looks_like_follow_up(current_text)
        if not follow_up or not recent:
            return TurnContextPlan(
                scope_key=scope.key,
                current_message_id=current_message_id,
                current_principal_id=current_principal_id,
                focus_message_id=None,
                confidence=0.0,
                reason_codes=("standalone_message",),
                related_message_ids=(),
                candidates=(),
                rendered_context="",
            )

        timestamp = int(now if now is not None else current.occurred_at if current else 0)
        query_terms = self._query_terms(current_text)
        scored: list[tuple[CanonicalMessage, ContextCandidate]] = []
        for distance, message in enumerate(reversed(recent)):
            score, reasons = self._score(
                message,
                distance=distance,
                now=timestamp,
                current_native_user_id=str(current_native_user_id),
                query_terms=query_terms,
            )
            if score <= 0:
                continue
            scored.append(
                (
                    message,
                    ContextCandidate(
                        message_id=message.canonical_message_id,
                        score=round(score, 2),
                        reason_codes=tuple(reasons),
                    ),
                )
            )
        scored.sort(
            key=lambda item: (
                item[1].score,
                item[0].canonical_message_id,
            ),
            reverse=True,
        )
        top = scored[0] if scored else None
        runner_up = scored[1] if len(scored) > 1 else None
        if top is None or top[1].score < 45:
            candidates = tuple(item[1] for item in scored[:5])
            return TurnContextPlan(
                scope_key=scope.key,
                current_message_id=current_message_id,
                current_principal_id=current_principal_id,
                focus_message_id=None,
                confidence=0.0,
                reason_codes=("no_reliable_focus",),
                related_message_ids=(),
                candidates=candidates,
                rendered_context=(
                    "[追问指向不明确] 如果近期消息仍不足以确认对象，先简短问清楚。"
                ),
            )

        margin = top[1].score - (runner_up[1].score if runner_up else 0.0)
        confidence = min(
            0.99,
            max(
                0.45,
                (top[1].score / 100.0)
                * (0.78 + min(max(margin, 0.0) / 50.0, 0.22)),
            ),
        )
        ambiguous = runner_up is not None and margin < 8
        if ambiguous:
            confidence = min(confidence, 0.59)
        focus = top[0]
        related = self._related_ids(
            recent,
            focus.canonical_message_id,
            current_message_id,
        )
        reasons = tuple(dict.fromkeys((*top[1].reason_codes, "same_scope")))
        rendered = self._render(
            focus,
            self._messages_by_ids(recent, related),
            ambiguous=ambiguous,
        )
        return TurnContextPlan(
            scope_key=scope.key,
            current_message_id=current_message_id,
            current_principal_id=current_principal_id,
            focus_message_id=focus.canonical_message_id,
            confidence=round(confidence, 4),
            reason_codes=reasons,
            related_message_ids=related,
            candidates=tuple(item[1] for item in scored[:5]),
            rendered_context=rendered,
        )

    @classmethod
    def _looks_like_follow_up(cls, text: str) -> bool:
        compact = " ".join(str(text).split()).strip()
        if not compact:
            return False
        return len(compact) <= 40 and any(
            re.fullmatch(pattern, compact, re.IGNORECASE)
            for pattern in _FOLLOW_UP_PATTERNS
        )

    @classmethod
    def _score(
        cls,
        message: CanonicalMessage,
        *,
        distance: int,
        now: int,
        current_native_user_id: str,
        query_terms: tuple[str, ...],
    ) -> tuple[float, list[str]]:
        text = " ".join(message.prompt_text.split()).strip()
        score = max(42.0 - distance * 6.0, 4.0)
        reasons = ["recent_message"]
        age = max(now - message.occurred_at, 0) if now else 0
        if age <= 300:
            score += 15
            reasons.append("within_5m")
        elif age <= 1800:
            score += 8
            reasons.append("within_30m")
        if cls._is_question(text):
            score += 32
            reasons.append("recent_question")
        if message.direction == "inbound":
            score += 5
            reasons.append("human_message")
        else:
            score -= 12
            reasons.append("bot_message_penalty")
        if message.sender_native_user_id != current_native_user_id:
            score += 8
            reasons.append("other_participant")
        if _LOW_INFORMATION.fullmatch(text):
            score -= 35
            reasons.append("low_information_penalty")
        overlap = cls._term_overlap(query_terms, text)
        if overlap:
            score += min(overlap * 7, 21)
            reasons.append("lexical_overlap")
        if message.reply_to_canonical_message_id is not None:
            score += 3
            reasons.append("reply_chain")
        return max(score, 0.0), reasons

    @staticmethod
    def _is_question(text: str) -> bool:
        compact = text.strip().casefold()
        return any(marker in compact for marker in _QUESTION_MARKERS)

    @classmethod
    def _query_terms(cls, text: str) -> tuple[str, ...]:
        normalized = re.sub(r"[\W_]+", "", text.casefold())
        for filler in _QUERY_FILLERS:
            normalized = normalized.replace(
                re.sub(r"[\W_]+", "", filler.casefold()),
                "",
            )
        if len(normalized) < 2:
            return ()
        return tuple(
            dict.fromkeys(
                normalized[index : index + 2]
                for index in range(len(normalized) - 1)
            )
        )

    @staticmethod
    def _term_overlap(terms: tuple[str, ...], text: str) -> int:
        normalized = re.sub(r"[\W_]+", "", text.casefold())
        return sum(1 for term in terms if term in normalized)

    def _related_ids(
        self,
        recent: list[CanonicalMessage],
        focus_message_id: int,
        current_message_id: int,
    ) -> tuple[int, ...]:
        after_focus = [
            message.canonical_message_id
            for message in recent
            if focus_message_id < message.canonical_message_id < current_message_id
        ]
        reply_links = [
            message.canonical_message_id
            for message in recent
            if message.reply_to_canonical_message_id == focus_message_id
        ]
        return tuple(
            dict.fromkeys((*after_focus[-self.related_limit :], *reply_links))
        )[-self.related_limit :]

    @staticmethod
    def _messages_by_ids(
        recent: list[CanonicalMessage],
        message_ids: tuple[int, ...],
    ) -> list[CanonicalMessage]:
        wanted = set(message_ids)
        return [
            message
            for message in recent
            if message.canonical_message_id in wanted
        ]

    @classmethod
    def _render(
        cls,
        focus: CanonicalMessage,
        related: list[CanonicalMessage],
        *,
        ambiguous: bool,
    ) -> str:
        lines = [
            "[当前群追问焦点]",
            cls._message_line(focus),
        ]
        if related:
            lines.append("[相关消息]")
            lines.extend(cls._message_line(message) for message in related)
        if ambiguous:
            lines.append(
                "候选接近；证据不足时先问清楚指代。"
            )
        else:
            lines.append("围绕上述焦点回答，不要转去该用户的旧私聊。")
        return "\n".join(lines)

    @staticmethod
    def _message_line(message: CanonicalMessage) -> str:
        stamp = datetime.fromtimestamp(message.occurred_at).strftime("%m-%d %H:%M")
        sender = (
            f"[mention#{message.sender_principal_id}] {message.sender_display}"
            if message.sender_principal_id is not None
            else message.sender_display
        )
        return (
            f"[msg#{message.canonical_message_id} | {stamp} | {sender}] "
            f"{message.prompt_text[:500]}"
        )
