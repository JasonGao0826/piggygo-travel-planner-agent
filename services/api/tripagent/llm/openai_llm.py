"""OpenAI 实现，走 Responses API（GPT-6.1 Sol 的工具调用只支持 Responses API）。"""

import base64
import json
from typing import Any

from openai import OpenAI

from .base import LLM, Attachment, Message, T, Tool, ToolCallRecord, ToolRun, Usage, count_model_call


def _usage(resp) -> Usage:
    u = getattr(resp, "usage", None)
    return Usage(input_tokens=getattr(u, "input_tokens", 0) or 0,
                 output_tokens=getattr(u, "output_tokens", 0) or 0, calls=1)


def _to_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


class OpenAIResponsesLLM(LLM):
    def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None,
                 effort: str = "medium", client: OpenAI | None = None):
        self.model = model
        self.effort = effort
        self.client = client or OpenAI(api_key=api_key, base_url=base_url)
        self.total = Usage()  # 这个实例累计用了多少 token，用来算费用

    def _reasoning(self, effort: str | None) -> dict:
        return {"effort": effort or self.effort}

    @staticmethod
    def _input(messages: list[Message]) -> list[dict]:
        return [{"role": m.role, "content": m.text} for m in messages]

    def complete(self, system: str, messages: list[Message], *, effort: str | None = None) -> str:
        count_model_call()
        resp = self.client.responses.create(model=self.model, instructions=system, input=self._input(messages),
                                            reasoning=self._reasoning(effort))
        self.total.add(_usage(resp))
        return resp.output_text

    def structured(self, system: str, messages: list[Message], schema: type[T], *, effort: str | None = None) -> T:
        count_model_call()
        resp = self.client.responses.parse(model=self.model, instructions=system, input=self._input(messages),
                                           reasoning=self._reasoning(effort), text_format=schema)
        self.total.add(_usage(resp))
        return resp.output_parsed

    def extract(self, system: str, text: str, attachments: list[Attachment], schema: type[T], *,
                effort: str | None = None) -> T:
        content: list[dict] = [{"type": "input_text", "text": text}]
        for a in attachments:
            data_url = f"data:{a.mime};base64,{base64.b64encode(a.data).decode()}"
            if a.mime == "application/pdf":
                content.append({"type": "input_file", "filename": a.filename, "file_data": data_url})
            else:
                content.append({"type": "input_image", "image_url": data_url, "detail": "high"})
        count_model_call()
        resp = self.client.responses.parse(model=self.model, instructions=system,
                                           input=[{"role": "user", "content": content}],
                                           reasoning=self._reasoning(effort), text_format=schema)
        self.total.add(_usage(resp))
        return resp.output_parsed

    def run_tools(self, system: str, messages: list[Message], tools: list[Tool], *,
                  effort: str | None = None, max_steps: int = 15) -> ToolRun:
        by_name = {t.name: t for t in tools}
        specs = [{"type": "function", "name": t.name, "description": t.description,
                  "parameters": t.parameters, "strict": False} for t in tools]
        items: list[Any] = self._input(messages)
        run = ToolRun(text="")
        for _ in range(max_steps):
            count_model_call()
            resp = self.client.responses.create(model=self.model, instructions=system, input=items, tools=specs,
                                                reasoning=self._reasoning(effort))
            run.usage.add(_usage(resp))
            self.total.add(_usage(resp))
            calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
            if not calls:
                run.text = resp.output_text
                return run
            items += resp.output  # 把模型这一步的输出（含推理和调用）原样带回去，保持上下文连贯
            for call in calls:
                record = ToolCallRecord(name=call.name, arguments={}, result=None)
                try:
                    record.arguments = json.loads(call.arguments or "{}")
                    tool = by_name.get(call.name)
                    if tool is None:
                        raise ValueError(f"没有名为 {call.name} 的工具")
                    record.result = tool.handler(record.arguments)
                    output = _to_json(record.result)
                except Exception as exc:  # 工具报错也交回给模型，让它自己修正
                    record.error = f"{type(exc).__name__}: {exc}"
                    output = json.dumps({"error": record.error}, ensure_ascii=False)
                run.calls.append(record)
                items.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
        run.stopped_early = True
        run.text = "这一步需要的操作太多，我先停在这里。请把需求拆小一点再试。"
        return run
