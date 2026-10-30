"""用真实模型从信息清单生成整份行程（会产生 API 费用，约 0.5–1 美元）。

用法（在 services/api 目录下）：
    .\\.venv\\Scripts\\python.exe scripts\\try_generate.py              # 用回放测试里的新西兰清单
    .\\.venv\\Scripts\\python.exe scripts\\try_generate.py 清单.yaml     # 用自己的清单（TripIntake 格式）
路线框架会自动确认。生成的 Word 在仓库根目录的 output/ 下。
"""

import sys
import time
from datetime import date
from pathlib import Path

import yaml

from tripagent.kb import load_kb
from tripagent.llm import load_llm
from tripagent.planner import Planner
from tripagent.report import build_report, render_docx
from tripagent.routing import CompositeDriveTimes, OsrmDriveTimes, StaticDriveTimes
from tripagent.schema.intake import TripIntake

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
PRICE_IN, PRICE_OUT = 2.0, 10.0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "eval" / "replay_nz.yaml"
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    intake = TripIntake.model_validate(raw.get("intake", raw))

    places = load_kb()
    drive = CompositeDriveTimes(StaticDriveTimes.from_yaml(ROOT / "data" / "routing" / "nz_drive_times.yaml"),
                                OsrmDriveTimes(places, cache_path=API / "data" / "osrm_cache.json"))
    llm = load_llm()
    t0 = time.time()

    def progress(stage, msg):
        print(f"[{time.time() - t0:5.0f}s] {stage} {msg}", flush=True)

    def review(sk):
        print("\n路线框架：", sk.summary)
        for d in sk.days:
            print(f"  {d.date}｜{d.route}｜住 {d.night_city or '—'}")
        for f in sk.flights:
            print(f"  航班 {f.number or '待订'} {f.from_airport}→{f.to_airport} {f.date} {f.depart}–{f.arrive}"
                  f"{'（已订）' if f.booked else '（建议）'}")
        print("（自动确认）\n")
        return None

    result = Planner(llm, places, drive, today=date.today()).generate(intake, on_progress=progress, review_skeleton=review)
    trip = result.trip
    print(f"\n生成完成：{trip.meta.title}，{len(trip.all_items())} 个事件，修正 {result.rounds} 轮")
    for n, d in enumerate(trip.days, 1):
        acts = [i.title for i in d.items if i.type.value == "activity"]
        print(f"  D{n} {d.date}｜{d.route}｜{'、'.join(acts)}")
    print(f"\n校验：{len(result.errors)} 个错误，{len(result.issues) - len(result.errors)} 个提醒")
    for i in result.issues:
        print(f"  [{i.code}/{i.severity.value}] {i.message}")
    for n in result.notes:
        print(f"  [排程] {n}")
    u = result.usage
    print(f"\n模型调用 {u.calls} 次，输入 {u.input_tokens} / 输出 {u.output_tokens} token，"
          f"约 US${u.input_tokens / 1e6 * PRICE_IN + u.output_tokens / 1e6 * PRICE_OUT:.2f}，用时 {time.time() - t0:.0f} 秒")
    out = render_docx(build_report(trip, places), ROOT / "output" / f"生成-{trip.meta.title}.docx")
    (ROOT / "output" / "generated_trip.json").write_text(trip.model_dump_json(indent=1), encoding="utf-8")
    print(f"Word：{out}")


if __name__ == "__main__":
    main()
