"""模型适配层。按配置创建模型；业务代码通过 tripagent.llm.base.LLM 接口使用。"""

import os
from pathlib import Path

from dotenv import load_dotenv

from .base import LLM, Attachment, Message, Tool, ToolCallRecord, ToolRun, Usage

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_llm(role: str = "main") -> LLM:
    """role="main" 用于规划和对话，role="light" 用于意图判断、信息抽取等轻任务。"""
    load_dotenv(ENV_FILE)
    provider = os.getenv("LLM_PROVIDER", "openai")
    if provider != "openai":
        raise ValueError(f"暂不支持的模型提供方：{provider}")
    from .openai_llm import OpenAIResponsesLLM

    model = os.getenv("OPENAI_MODEL_LIGHT", "gpt-6-luna") if role == "light" else os.getenv("OPENAI_MODEL", "gpt-6.1-sol")
    return OpenAIResponsesLLM(model, api_key=os.getenv("OPENAI_API_KEY"),
                              base_url=os.getenv("OPENAI_BASE_URL") or None,
                              effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"))


__all__ = ["LLM", "Attachment", "Message", "Tool", "ToolCallRecord", "ToolRun", "Usage", "load_llm"]
