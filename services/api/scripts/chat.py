"""在命令行里和 agent 对话，用新西兰 V8 行程试用（会产生 API 费用）。

用法（在 services/api 目录下）：
    .venv/Scripts/python scripts/chat.py [成员 id，默认 m_nina]

命令：
    确认 / /ok      确认最近一个改动          /撤回        撤回所有等待中的改动
    /报告           生成当前版本的 Word 报告   /版本        查看版本历史
    /上传 文件路径   识别凭证图片或 PDF，核对后交给 agent
    /重置           删掉数据库里的行程，重新载入 V8
    /退出
数据存在 services/api/data/tripagent.db，下次打开会接着上次的进度。
"""

import sys
from pathlib import Path

from tripagent.agent import TripAgent
from tripagent.evidence import evidence_message, extract_voucher
from tripagent.io import load_decisions, load_trip
from tripagent.kb import load_places
from tripagent.llm import load_llm
from tripagent.report import build_report, render_docx
from tripagent.routing import StaticDriveTimes
from tripagent.service import ServiceError, TripService
from tripagent.store import DEFAULT_SQLITE, SqlStore

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
FIXTURES = API / "tests" / "fixtures"
TRIP = "nz_2026"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    member = sys.argv[1] if len(sys.argv) > 1 else "m_nina"
    places = {**load_places(FIXTURES / "nz_places.yaml"), **load_places(ROOT / "data" / "kb" / "nz" / "places.yaml")}
    drive_times = StaticDriveTimes.from_yaml(FIXTURES / "nz_drive_times.yaml")
    service = TripService(SqlStore(), places, drive_times)
    if TRIP not in service.trip_ids():
        service.create_trip(load_trip(FIXTURES / "nz_trip_v8.yaml"), load_decisions(FIXTURES / "nz_decisions.yaml"))
    llm = load_llm()
    agent = TripAgent(service, llm)
    latest = service.latest(TRIP)
    print(f"已载入新西兰行程（第 {latest.version} 版），你是 {latest.state.member(member).name}。"
          "输入问题或修改要求，/退出 结束。")

    while True:
        try:
            text = input("\n你：").strip().lstrip("﻿")  # PowerShell 管道输入开头可能带 BOM
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/退出", "退出", "/exit", "exit"):
            break
        try:
            if text in ("确认", "/ok"):
                v = agent.confirm(TRIP, member)
                print(f"已确认，生成第 {v.version} 版：\n  " + "\n  ".join(v.changelog))
            elif text == "/撤回":
                for p in service.pending_patches(TRIP):
                    service.reject(p.id)
                print("已撤回所有等待中的改动。")
            elif text.startswith("/上传"):
                path = Path(text.removeprefix("/上传").strip().strip('"'))
                extraction = extract_voucher(llm, path.read_bytes(), path.name)
                print("识别结果：")
                for b in extraction.bookings:
                    fields = {k: v for k, v in b.model_dump().items() if v not in (None, [], "") and k != "quotes"}
                    print("  ", fields)
                for n in extraction.notes:
                    print("   注意：", n)
                if input("这些信息对吗？输入 y 交给助手更新行程：").strip().lower() == "y":
                    reply = agent.handle(TRIP, member, evidence_message(path.name, extraction))
                    print(f"
助手：{reply.text}")
            elif text == "/重置":
                service.store.engine.dispose()
                DEFAULT_SQLITE.unlink(missing_ok=True)
                print("已删除本地数据库，请重新运行。")
                break
            elif text == "/版本":
                for v in service.history(TRIP):
                    print(f"V{v.version}：{v.changelog[0]}")
            elif text == "/报告":
                latest = service.history(TRIP)[-1]
                path = render_docx(build_report(latest.state, places, changelog=latest.changelog),
                                   ROOT / "output" / f"新西兰行程报告-V{latest.version}.docx")
                print(f"已生成：{path}")
            else:
                reply = agent.handle(TRIP, member, text)
                print(f"\n助手：{reply.text}")
        except ServiceError as exc:
            print(f"（{exc}）")


if __name__ == "__main__":
    main()
