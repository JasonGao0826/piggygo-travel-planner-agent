"""测试用的"假模型"：按事先写好的剧本调用工具、给出回答，不联网、不花钱，结果固定。"""

from dataclasses import dataclass, field
from typing import Any

from .base import LLM, Attachment, Message, T, Tool, ToolCallRecord, ToolRun, Usage, count_model_call


@dataclass
class ScriptStep:
    tool: str | None = None             # 要调用的工具；为 None 表示这一步给出最终回答
    arguments: dict[str, Any] = field(default_factory=dict)
    text: str = ""


class ScriptedLLM(LLM):
    def __init__(self, steps: list[ScriptStep], model: str = "scripted"):
        self.model = model
        self.steps = list(steps)
        self.seen_systems: list[str] = []
        self.seen_messages: list[list[Message]] = []

    def complete(self, system: str, messages: list[Message], *, effort: str | None = None) -> str:
        self.seen_systems.append(system)
        self.seen_messages.append(list(messages))
        count_model_call()
        return self.steps.pop(0).text

    def structured(self, system: str, messages: list[Message], schema: type[T], *, effort: str | None = None) -> T:
        self.seen_systems.append(system)
        count_model_call()
        return schema.model_validate_json(self.steps.pop(0).text)

    def extract(self, system: str, text: str, attachments: list[Attachment], schema: type[T], *,
                effort: str | None = None) -> T:
        self.seen_systems.append(system)
        count_model_call()
        return schema.model_validate_json(self.steps.pop(0).text)

    def run_tools(self, system: str, messages: list[Message], tools: list[Tool], *,
                  effort: str | None = None, max_steps: int = 15) -> ToolRun:
        self.seen_systems.append(system)
        self.seen_messages.append(list(messages))
        by_name = {t.name: t for t in tools}
        run = ToolRun(text="", usage=Usage())
        while self.steps:
            step = self.steps.pop(0)
            run.usage.calls += 1          # 每一步相当于一次模型调用
            count_model_call()
            if step.tool is None:
                run.text = step.text
                return run
            record = ToolCallRecord(name=step.tool, arguments=step.arguments, result=None)
            try:
                record.result = by_name[step.tool].handler(step.arguments)
            except Exception as exc:
                record.error = f"{type(exc).__name__}: {exc}"
            run.calls.append(record)
        return run
