"""校验器：用代码检查行程是否违反规则（设计规格第 11 节）。

模型提出的每个改动都要先过校验器；错误级问题不清零，改动就不能写入。
"""

from ..schema.memory import Issue, Severity
from .context import ValidationContext, ValidatorConfig
from .live_rules import LIVE_RULES
from .rules import RULES, check_locked_changes


def validate(ctx: ValidationContext) -> list[Issue]:
    issues: list[Issue] = []
    for rule in RULES + LIVE_RULES:
        issues.extend(rule(ctx))
    # 错误排在前面，同级按规则编号排序
    return sorted(issues, key=lambda i: (i.severity != Severity.ERROR, i.code))


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == Severity.ERROR]


__all__ = ["ValidationContext", "ValidatorConfig", "check_locked_changes", "errors", "validate"]
