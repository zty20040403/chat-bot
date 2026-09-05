from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .contracts import AgentSpec, SubAgentRole, WorkerRole


COMMON_READ_TOOLS = frozenset(
    {
        "get_message_by_id",
        "search_messages",
        "context_expand",
        "context_search",
        "memory_list",
        "inspect_source",
        "inspect_shared_content",
        "get_shared_content",
        "view_forward",
        "view_bilibili",
        "list_recent_files",
        "job_status",
        "say",
    }
)
SANDBOX_TOOLS = frozenset(
    {
        "sandbox_create",
        "sandbox_list",
        "sandbox_exec",
        "sandbox_write_file",
        "sandbox_read_file",
        "sandbox_destroy",
        "import_file_to_sandbox",
        "job_cancel",
    }
)
BROWSER_TOOLS = frozenset(
    {
        "web_search",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press_key",
        "browser_wait_for",
        "browser_scroll",
        "browser_close",
        "browser_clear",
    }
)


AGENT_SPECS: dict[SubAgentRole, AgentSpec] = {
    "supervisor": AgentSpec(
        role="supervisor",
        title="主控",
        description="拆分目标、检查依赖、验收结果并统一回复。",
        instructions="只负责任务设计和验收，不亲自调用执行工具。",
        allowed_tools=frozenset(),
        model_policy="reasoning",
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "artifacts", "memory"}
        ),
        context_budget_chars=8000,
    ),
    "researcher": AgentSpec(
        role="researcher",
        title="搜索",
        description="搜索互联网、浏览网页并交叉核实来源。",
        instructions=(
            "优先使用一手来源；区分事实、推断和未知信息。最终给出完整链接、"
            "关键事实、冲突信息和仍未确认的内容。"
        ),
        allowed_tools=COMMON_READ_TOOLS | BROWSER_TOOLS,
        model_policy="fast",
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "upstream"}
        ),
        context_budget_chars=6200,
    ),
    "coder": AgentSpec(
        role="coder",
        title="代码",
        description="在隔离沙盒中编写、运行和验证代码。",
        instructions=(
            "所有代码和命令必须在任务沙盒中执行。完成前检查实际输出；需要交付时"
            "返回真实文件句柄，由宿主验收后发送，并报告执行结果和未解决问题。"
        ),
        allowed_tools=COMMON_READ_TOOLS | BROWSER_TOOLS | SANDBOX_TOOLS | {"use_skill"},
        model_policy="coding",
        max_turns=20,
        timeout_seconds=1200,
        risk_level="controlled-write",
        background_default=True,
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "artifacts", "upstream"}
        ),
        context_budget_chars=7200,
    ),
    "document": AgentSpec(
        role="document",
        title="文件",
        description="读取群文件、PDF、表格和文档并生成交付物。",
        instructions=(
            "先取得真实文件，再解析内容；不得根据文件名猜测。生成文档后检查文件"
            "存在且可读取，并通过文件句柄交付。含中文的 PDF 必须使用沙盒里的 "
            "kennethbot-pdf 生成，再用 pdffonts 检查字体嵌入、pdftotext 检查中文；"
            "验收失败不得发送。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | SANDBOX_TOOLS
            | {"read_image_text", "view_image", "use_skill"}
        ),
        model_policy="document",
        max_turns=20,
        timeout_seconds=1200,
        risk_level="controlled-write",
        background_default=True,
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "artifacts", "upstream"}
        ),
        context_budget_chars=7600,
    ),
    "media": AgentSpec(
        role="media",
        title="媒体",
        description="理解图片、视频、字幕、语音和平台分享内容。",
        instructions=(
            "必须先实际读取媒体再评价。长视频先看元数据、字幕和关键帧；明确指出"
            "可观察内容、推断内容和无法确认的部分。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | BROWSER_TOOLS
            | {"read_image_text", "view_image", "view_video", "transcribe_voice"}
        ),
        model_policy="vision",
        max_turns=16,
        timeout_seconds=1900,
        background_default=True,
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "artifacts", "upstream"}
        ),
        context_budget_chars=6800,
    ),
    "analyst": AgentSpec(
        role="analyst",
        title="分析",
        description="整理数据、比较证据、计算并形成可审计结论。",
        instructions=(
            "先确定统计口径，再计算和比较。结论必须对应证据；发现缺失数据时明确"
            "说明，不要用猜测补齐。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | SANDBOX_TOOLS
            | {"query_alerts", "pin_message", "group_members"}
        ),
        model_policy="reasoning",
        risk_level="controlled-write",
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "artifacts", "upstream"}
        ),
        context_budget_chars=7000,
    ),
    "operator": AgentSpec(
        role="operator",
        title="运维",
        description="检查 Kennethbot、告警、任务、数据库和运行状态。",
        instructions=(
            "默认只读检查。涉及停止、重启、删除或修改服务时必须遵守宿主审批策略；"
            "报告影响范围、当前状态和建议动作。"
        ),
        allowed_tools=(
            COMMON_READ_TOOLS
            | {"query_alerts", "sandbox_list", "job_status", "group_members"}
        ),
        model_policy="operations",
        risk_level="privileged",
        max_attempts=1,
        context_channels=frozenset(
            {"conversation", "supporting", "evidence", "upstream"}
        ),
        context_budget_chars=5200,
    ),
}


class AgentRegistry:
    def __init__(self, specs: Mapping[SubAgentRole, AgentSpec]) -> None:
        copied = dict(specs)
        for role, spec in copied.items():
            if role != spec.role:
                raise ValueError(f"Agent registry key {role} does not match {spec.role}")
            if spec.version < 1:
                raise ValueError(f"Agent {role} has an invalid version")
        self._specs: Mapping[SubAgentRole, AgentSpec] = MappingProxyType(copied)

    def get(self, role: str) -> AgentSpec:
        try:
            return self._specs[role]  # type: ignore[index]
        except KeyError as exc:
            raise ValueError(f"Unknown Sub-Agent role: {role}") from exc

    def worker(self, role: str) -> AgentSpec:
        spec = self.get(role)
        if spec.role == "supervisor":
            raise ValueError("Supervisor cannot execute worker tasks")
        return spec

    @property
    def worker_roles(self) -> tuple[WorkerRole, ...]:
        return tuple(
            role  # type: ignore[misc]
            for role in self._specs
            if role != "supervisor"
        )

    def manifest(self) -> list[dict[str, object]]:
        return [spec.manifest() for spec in self._specs.values()]


DEFAULT_AGENT_REGISTRY = AgentRegistry(AGENT_SPECS)
WORKER_ROLES = DEFAULT_AGENT_REGISTRY.worker_roles
