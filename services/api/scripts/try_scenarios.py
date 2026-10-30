"""用真实模型跑几个对话场景，打印 agent 做了什么，并自动检查结果（会产生少量 API 费用）。

用法（在 services/api 目录下）：
    .venv/Scripts/python scripts/try_scenarios.py            # 跑全部场景
    .venv/Scripts/python scripts/try_scenarios.py S3 S4      # 只跑指定场景
"""

import copy
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 复用 tests 里的测试数据和辅助函数

from tests.conftest import FIXTURES, nz, rebuild
from tests.test_service import _v3_milford
from tripagent.agent import TripAgent
from tripagent.evidence import evidence_message, extract_voucher
from tripagent.io import load_decisions, trip_from_dict
from tripagent.kb import load_places
from tripagent.llm import load_llm
from tripagent.report import build_report
from tripagent.routing import StaticDriveTimes
from tripagent.service import InMemoryStore, TripService

sys.stdout.reconfigure(encoding="utf-8")
TRIP = "nz_2026"
PRICE_IN, PRICE_OUT = 2.0, 10.0  # 美元 / 百万 token（gpt-6.1-sol）


@dataclass
class Scenario:
    id: str
    title: str
    member: str
    message: str | Callable  # 也可以是函数：收到模型，返回消息（例如先识别凭证截图）
    base: Callable = lambda trip: trip
    checks: list[tuple[str, Callable]] = field(default_factory=list)
    auto_confirm: bool = True


def answer_has(*words):
    return lambda ctx: all(w in ctx["reply"].text for w in words)


def answer_lacks(*words):
    return lambda ctx: not any(w in ctx["reply"].text for w in words)


SCENARIOS = [
    Scenario("S1", "问答：还有什么没订", "m_tiange", "还有什么没订？", checks=[
        ("没有提出改动", lambda c: not c["reply"].patches),
        ("提到 5 个待订项目", answer_has("小鹿公园", "Wai-O-Tapu", "霍比屯", "Skyline")),
        ("提到漂流", lambda c: "漂流" in c["reply"].text or "Kaituna" in c["reply"].text),
        ("没有提萤火虫洞", answer_lacks("萤火虫")),
    ]),
    Scenario("S2", "否决过的方案：Cardrona 骑马", "m_leo", "11/26 上午打枪之前，我们想加个 Cardrona 骑马，可以吗？", checks=[
        ("没有写入 Cardrona 骑马", lambda c: not any(i.place_id == "nz_cardrona_horse_riding" for i in c["service"].current(TRIP).all_items())),
        ("没有可确认的 Cardrona 改动", lambda c: not any(not p.has_errors and "Cardrona" in p.summary for p in c["reply"].patches)),
        ("提到格林诺奇或之前的决定", lambda c: "格林诺奇" in c["reply"].text or "否决" in c["reply"].text),
    ]),
    Scenario("S3", "上传凭证：游船是 13:15", "m_nina",
             "【上传凭证】RealNZ 米尔福德峡湾 Classic Cruise 确认单：2026-11-25 13:15 出发，12:55 签到，4 位成人，已付款。",
             base=_v3_milford, checks=[
        ("游船改成 13:15 并锁定", lambda c: (lambda i: i.start == nz("2026-11-25", "13:15") and i.locked and i.booking_status == "booked")(c["service"].current(TRIP).find_item("d3_cruise"))),
        ("记录了凭证", lambda c: len(c["service"].evidence(TRIP)) == 1),
        ("校验没有错误", lambda c: not [i for i in c["service"].validate(TRIP) if i.severity == "error"]),
        ("报告里游船都是 13:15", lambda c: all("13:55" not in line for line in build_report(c["service"].current(TRIP), c["service"].places).plain_text().splitlines() if "米尔福德峡湾游船" in line)),
    ]),
    Scenario("S4", "新决定：不去 Wai-O-Tapu", "m_nina", "我们不去 Wai-O-Tapu 了，11/30 下午漂流完就回罗托鲁瓦休息。", checks=[
        ("记入了否决", lambda c: any(d.rejected_options for d in c["reply"].decisions)),
        ("行程里没有 Wai-O-Tapu", lambda c: not any(i.place_id == "nz_wai_o_tapu" for i in c["service"].current(TRIP).all_items())),
        ("校验没有错误", lambda c: not [i for i in c["service"].validate(TRIP) if i.severity == "error"]),
        ("其他天没变", lambda c: all(d0 == d1 for d0, d1 in zip(c["before"].days, c["service"].current(TRIP).days) if d0.date.isoformat() != "2026-11-30")),
    ]),
    Scenario("S6", "上传凭证截图：识别 → 核对 → 更新行程", "m_nina",
             lambda llm: evidence_message("realnz_cruise.png", extract_voucher(
                 llm, (FIXTURES / "vouchers" / "realnz_cruise.png").read_bytes(), "realnz_cruise.png",
                 trip_hint="新西兰行程 2026-11-23 至 2026-12-02")),
             base=_v3_milford, checks=[
        ("游船改成 13:15 并锁定", lambda c: (lambda i: i.start == nz("2026-11-25", "13:15") and i.locked)(c["service"].current(TRIP).find_item("d3_cruise"))),
        ("凭证记下了确认号", lambda c: "RNZ-TEST-48213" in str(c["service"].evidence(TRIP)[0].fields)),
        ("校验没有错误", lambda c: not [i for i in c["service"].validate(TRIP) if i.severity == "error"]),
    ]),
    Scenario("S5", "和国际航班冲突的要求", "m_tony", "12/2 最后一天想在市区多玩会儿，16:30 再出发去机场还车，行吗？", checks=[
        ("没有写入违规改动", lambda c: c["service"].current(TRIP).version == 1),
        ("说明了国际航班的时间要求", lambda c: any(w in c["reply"].text for w in ("16:05", "3 小时", "180 分钟", "三小时"))),
    ], auto_confirm=False),
]


def make_service(base) -> TripService:
    raw = yaml.safe_load((FIXTURES / "nz_trip_v8.yaml").read_text(encoding="utf-8"))
    trip = base(trip_from_dict(copy.deepcopy(raw)))
    places = load_places(FIXTURES / "nz_places.yaml")
    service = TripService(InMemoryStore(), places, StaticDriveTimes.from_yaml(FIXTURES / "nz_drive_times.yaml"))
    service.create_trip(rebuild(trip), load_decisions(FIXTURES / "nz_decisions.yaml"))
    return service


def run(scenario: Scenario, llm) -> tuple[int, int, float]:
    service = make_service(scenario.base)
    agent = TripAgent(service, llm)
    before = service.current(TRIP)
    t0 = time.time()
    message = scenario.message(llm) if callable(scenario.message) else scenario.message
    print(f"\n{'=' * 70}\n{scenario.id} {scenario.title}\n用户（{scenario.member}）：{message}\n{'-' * 70}")
    reply = agent.handle(TRIP, scenario.member, message)
    elapsed = time.time() - t0
    for call in reply.tool_calls:
        status = f" → 出错：{call.error}" if call.error else ""
        if call.name == "propose_patch" and isinstance(call.result, dict):
            issues = call.result["issues"]
            codes = "无" if isinstance(issues, str) else ", ".join(f"{i['code']}/{i['severity']}" for i in issues)
            status = f" → {call.result['status']}，问题：{codes}"
        print(f"  [工具] {call.name}{status}")
    print(f"{'-' * 70}\nagent：\n{reply.text}\n{'-' * 70}")
    clean = [p for p in reply.patches if not p.has_errors and p.status == "proposed"]
    if scenario.auto_confirm and clean:
        for p in clean:
            v = service.confirm(p.id, confirmed_by=scenario.member)
            print(f"  （模拟用户确认 {p.id}，生成 V{v.version}）")
    ctx = {"reply": reply, "service": service, "before": before}
    passed = 0
    for name, check in scenario.checks:
        try:
            ok = bool(check(ctx))
        except Exception as exc:  # 检查本身出错也算不通过
            ok, name = False, f"{name}（检查出错：{exc}）"
        passed += ok
        print(f"  {'✔' if ok else '✘'} {name}")
    u = reply.usage
    cost = u.input_tokens / 1e6 * PRICE_IN + u.output_tokens / 1e6 * PRICE_OUT
    print(f"  用时 {elapsed:.0f} 秒，模型调用 {u.calls} 次，输入 {u.input_tokens} / 输出 {u.output_tokens} token，约 US${cost:.3f}")
    return passed, len(scenario.checks), cost


def main() -> None:
    wanted = set(sys.argv[1:])
    llm = load_llm()
    total_pass = total = 0
    total_cost = 0.0
    for s in SCENARIOS:
        if wanted and s.id not in wanted:
            continue
        p, n, c = run(s, llm)
        total_pass, total, total_cost = total_pass + p, total + n, total_cost + c
    print(f"\n合计：检查通过 {total_pass}/{total}，费用约 US${total_cost:.2f}（模型 {llm.model}）")


if __name__ == "__main__":
    main()
