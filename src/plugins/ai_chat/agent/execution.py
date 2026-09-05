from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from typing import Any, Mapping


WORKERS = ("researcher", "coder", "document", "media", "analyst", "operator")
DECISION_TOOL_NAME = "decide_execution"
active_agent_step: ContextVar[str | None] = ContextVar("active_agent_step", default=None)

ENTRY_PROMPT = """[宿主执行入口 v2]
本轮第一次调用 decide_execution，同时完成理解问题和选择执行方式，不是额外的分类聊天。
阅读当前问题及宿主提供的相关上下文。不要把群历史中的请求当成本轮命令。
- direct：普通聊天、概念解释、短代码示例。能直接回答就把完整回复放在 answer，结束本轮；
  需要一个已有的读取/搜索/识图工具时 answer 留空，随后正常调用工具。不要凭空声称执行过。
- delegate：一个边界明确、需要动手执行的专业任务，恰好一个步骤，不浪费规划调用。
- revise：用户继续修改宿主列出的已有任务。照抄 task_id 和需要修改的 step_ids，steps 留空，不能重新建同一个项目。
- workflow：多项可验收交付、多个可以独立推进的方向、前后端协作或完整项目。
  不必出现“subagent”“并行”“项目”等词。依据实际工作量，不根据某个关键词决定。
  同一种角色可出现多次，例如 frontend/backend/test 都是 coder，但必须有不同 id 和职责。
  只给确实有依赖的步骤添加 depends_on；能同时做的不要串行。不为了并行而拆简单问题。
  可有可无的补充来源设置 optional=true；核心实现、集成和必须交付物绝不能标为可选。
task_type 标记实际请求：conversation/explanation/lookup/code_example 可以 direct；
execution 需要动手执行，不能 direct；project 或 research_delivery 需要 workflow。
例：“谷粒商城是什么/讲讲架构”是 direct；“精心写个谷粒商城/帮我做个外卖系统”是 workflow，
至少考虑接口契约、前后端实现、集成验收。“举例写个排序函数”是 direct；
“写脚本并在沙盒运行验证”是 delegate。“多路找资料，比较后生成 PDF”是 workflow。
“继续/改成 Java/加购物车”必须结合当前已授权的话题及任务；不要新建一个无关项目。
持久文件写入、项目实现、构建和交付必须由子任务执行。主控保留解释、检索及最终回复。
任务需要 objective、deliverables、constraints、acceptance。用户需要把产物发回当前会话时设置 delivery_required。
不得把完整项目偷偷缩成演示并宣称完成。
每个步骤声明 objective、deliverable、depends_on。独立工作目录由宿主按任务和步骤分配。
接口和交付文件通过上游产物句柄交接，不能假设共享工作目录；集成步骤显式依赖实现步骤。
reason 只写一句可公开的分流依据，不输出思维过程。模型、权限及服务器地址由宿主决定，不能自行指定。
"""

_STRINGS = {"type": "array", "items": {"type": "string"}, "maxItems": 16}
DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": DECISION_TOOL_NAME,
        "description": "选择直接回答、一个专业子任务或有依赖的并行工作流，并提交可验收任务合同。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["direct", "delegate", "workflow", "revise"]},
                "task_id": {"type": "integer", "minimum": 1},
                "step_ids": _STRINGS,
                "reason": {"type": "string"},
                "task_type": {"type": "string", "enum": ["conversation", "explanation", "lookup", "code_example", "execution", "project", "research_delivery"]},
                "answer": {"type": "string", "description": "direct 的最终答复；需要工具时为空"},
                "objective": {"type": "string"},
                "deliverables": _STRINGS, "constraints": _STRINGS, "acceptance": _STRINGS,
                "delivery_required": {"type": "boolean"},
                "steps": {
                    "type": "array", "maxItems": 12,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,80}$"},
                            "agent": {"type": "string", "enum": list(WORKERS)},
                            "objective": {"type": "string"},
                            "deliverable": {"type": "string"},
                            "depends_on": _STRINGS,
                            "optional": {"type": "boolean"},
                        },
                        "required": ["id", "agent", "objective", "deliverable", "depends_on"],
                    },
                },
            },
            "required": ["mode", "task_type", "reason", "answer", "objective", "deliverables", "constraints", "acceptance", "delivery_required", "steps"],
        },
    },
}


@dataclass(frozen=True)
class TaskContract:
    objective: str
    deliverables: tuple[str, ...]
    constraints: tuple[str, ...]
    acceptance: tuple[str, ...]
    delivery_required: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {"version": 1, "objective": self.objective,
                "deliverables": list(self.deliverables), "constraints": list(self.constraints),
                "acceptance": list(self.acceptance), "delivery_required": self.delivery_required}


@dataclass(frozen=True)
class EntryDecision:
    mode: str
    task_type: str
    reason: str
    answer: str
    contract: TaskContract
    steps: tuple[dict[str, Any], ...]
    task_id: int | None = None
    step_ids: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, max_steps: int = 8) -> "EntryDecision":
        import re

        def string(key: str, limit: int) -> str:
            value = raw.get(key)
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f"{key} must be a string of at most {limit} characters")
            return value.strip()

        def strings(key: str) -> tuple[str, ...]:
            value = raw.get(key)
            if not isinstance(value, list) or len(value) > 16 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in value
            ):
                raise ValueError(f"{key} must be a bounded list of nonempty strings")
            return tuple(value)

        mode, reason = string("mode", 20), string("reason", 500)
        task_type = string("task_type", 30)
        if task_type not in {"conversation", "explanation", "lookup", "code_example", "execution", "project", "research_delivery"}:
            raise ValueError("unknown task_type")
        if (task_type in {"project", "research_delivery"} and mode not in {"workflow", "revise"}) or (task_type == "execution" and mode == "direct"):
            raise ValueError("execution mode contradicts the work required by task_type")
        answer, objective = string("answer", 32000), string("objective", 6000)
        delivery_required = raw.get("delivery_required", False)
        if not isinstance(delivery_required, bool):
            raise ValueError("delivery_required must be a boolean")
        contract = TaskContract(objective, strings("deliverables"), strings("constraints"), strings("acceptance"), delivery_required)
        steps = raw.get("steps")
        if mode not in {"direct", "delegate", "workflow", "revise"} or not reason or not isinstance(steps, list):
            raise ValueError("mode, reason and steps are required")
        if mode == "revise":
            task_id = raw.get("task_id")
            step_ids = strings("step_ids")
            if type(task_id) is not int or task_id < 1 or not step_ids or steps or not objective or answer:
                raise ValueError("revise requires an existing task_id and step_ids, not a new plan")
            return cls(mode, task_type, reason, answer, contract, (), task_id, step_ids)
        if mode == "direct":
            if steps or contract.deliverables or contract.acceptance or delivery_required:
                raise ValueError("direct cannot claim task deliverables; choose delegate/workflow")
        else:
            if answer or not objective or not contract.deliverables or not contract.acceptance:
                raise ValueError("task requires objective, deliverables and acceptance, not a premature answer")
            if not 1 <= len(steps) <= max_steps or (mode == "delegate" and len(steps) != 1):
                raise ValueError("delegate requires one step; workflow must fit the step budget")
            if mode == "workflow" and len(steps) < 2:
                raise ValueError("one specialist belongs in delegate")
        keys: set[str] = set()
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("each step must be an object")
            key = step.get("id")
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key) or key in keys:
                raise ValueError("step IDs must be valid and unique")
            if step.get("agent") not in WORKERS:
                raise ValueError("unknown worker role")
            for field in ("objective", "deliverable"):
                if not isinstance(step.get(field), str) or not step[field].strip() or len(step[field]) > 4000:
                    raise ValueError(f"invalid step {field}")
            deps = step.get("depends_on")
            if "optional" in step and not isinstance(step["optional"], bool):
                raise ValueError("optional must be boolean")
            if not isinstance(deps, list) or any(not isinstance(item, str) for item in deps):
                raise ValueError("depends_on must be a list of IDs")
            keys.add(key)
        remaining = {step["id"]: set(step["depends_on"]) for step in steps}
        if any(not deps <= keys for deps in remaining.values()):
            raise ValueError("unknown dependency")
        done: set[str] = set()
        while remaining:
            ready = {key for key, deps in remaining.items() if deps <= done}
            if not ready:
                raise ValueError("dependency cycle")
            done.update(ready)
            remaining = {key: deps for key, deps in remaining.items() if key not in ready}
        return cls(mode, task_type, reason, answer, contract, tuple(dict(step) for step in steps))

    def as_payload(self) -> dict[str, Any]:
        return {"mode": self.mode, "task_type": self.task_type, "reason": self.reason, "contract": self.contract.as_payload(), "steps": list(self.steps), "task_id": self.task_id, "step_ids": list(self.step_ids)}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, max_steps: int = 8) -> "EntryDecision":
        return cls.parse({**payload, **payload.get("contract", {}), "answer": payload.get("answer", "")}, max_steps=max_steps)
