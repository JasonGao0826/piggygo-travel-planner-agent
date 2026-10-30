"""各模块共用的基础类型。"""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """所有数据结构的基类：不允许出现未定义的字段，防止模型写入拼错的字段名。"""

    model_config = ConfigDict(extra="forbid")


class Source(StrEnum):
    """信息来源（设计规格 8.5 节：每条信息都标来源）。"""

    USER = "user"                      # 用户说的
    EVIDENCE = "evidence"              # 预订凭证
    VERIFIED_WEB = "verified_web"      # 官网核实过
    KNOWLEDGE_BASE = "knowledge_base"  # 景点知识库
    GUIDE = "guide"                    # 小红书等攻略（用户提供，只当参考）
    AGENT_INFERRED = "agent_inferred"  # 模型推测


class Money(Model):
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    per: Literal["person", "group", "vehicle"] = "person"
