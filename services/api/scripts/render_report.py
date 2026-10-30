"""从行程数据生成 Word 报告。

用法（在 services/api 目录下）：
    .venv/Scripts/python scripts/render_report.py                      # 默认：新西兰 V8 测试行程
    .venv/Scripts/python scripts/render_report.py --trip 路径 --out 路径
"""

import argparse
from pathlib import Path

from tripagent.io import load_trip
from tripagent.kb import load_places
from tripagent.report import build_report, render_docx

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trip", default=API / "tests" / "fixtures" / "nz_trip_v8.yaml")
    parser.add_argument("--out", default=ROOT / "output" / "新西兰行程报告.docx")
    args = parser.parse_args()

    # 机场、住宿等只在行程数据旁边；景点用经官网核实的正式知识库，同 id 以正式知识库为准
    places = {**load_places(API / "tests" / "fixtures" / "nz_places.yaml"),
              **load_places(ROOT / "data" / "kb" / "nz" / "places.yaml")}
    report = build_report(load_trip(args.trip), places)
    path = render_docx(report, args.out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
