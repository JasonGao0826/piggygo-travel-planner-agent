"""模型适配层的接口（设计规格 6.3 节）。

业务代码只认这里的接口，不直接调用某家的 SDK。换模型（例如以后换成境内备案模型）只需要新写一个实现。
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class ModelBudgetExceeded(Exception):
    """今天调用模型的额度用完了（调用接口之前就停下）。"""


# 每次调用模型接口之前要做的事：网站的门卫进门时放进来，用来按实际调用次数扣每天的额度
# （一次对话里猪猪查了几轮就扣几次，额度用完就停，抛 ModelBudgetExceeded）。没放就什么都不做。
MODEL_CALLS: ContextVar[Callable[[], None] | None] = ContextVar("model_calls", default=None)


def count_model_call() -> None:
    charge = MODEL_CALLS.get()
    if charge is not None:
        charge()


@dataclass
class Message:
    role: Literal["user", "assistant"]
    text: str


@dataclass
class Attachment:
    """随消息一起发给模型的文件：图片（png/jpeg/webp）或 PDF。"""

    data: bytes
    mime: str
    filename: str = "file"


@dataclass
class Tool:
    """给模型用的工具。handler 收到模型给的参数，返回结果（会转成 JSON 交回给模型）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: Any
    error: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls


@dataclass
class ToolRun:
    text: str
    calls: list[ToolCallRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stopped_early: bool = False   # 达到调用次数上限还没结束


class LLM(Protocol):
    model: str

    def complete(self, system: str, messages: list[Message], *, effort: str | None = None) -> str:
        """普通回答。"""

    def structured(self, system: str, messages: list[Message], schema: type[T], *, effort: str | None = None) -> T:
        """按数据结构输出，返回校验过的对象。"""

    def run_tools(self, system: str, messages: list[Message], tools: list[Tool], *,
                  effort: str | None = None, max_steps: int = 15) -> ToolRun:
        """让模型边推理边调用工具（ReAct），直到给出最终回答或达到上限。"""

    def extract(self, system: str, text: str, attachments: list[Attachment], schema: type[T], *,
                effort: str | None = None) -> T:
        """看图片或 PDF，按数据结构抽取信息。"""
