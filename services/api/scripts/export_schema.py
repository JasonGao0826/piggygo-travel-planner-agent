"""把 Pydantic 数据结构导出成 JSON Schema，放到 packages/schema，供前端生成 TypeScript 类型。

用法（在 services/api 目录下）：.venv/Scripts/python scripts/export_schema.py
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from tripagent.schema import Decision, Patch, PatchOp, Place, Trip
from tripagent.schema.intake import TripIntake

OUT = Path(__file__).resolve().parents[3] / "packages" / "schema"

SCHEMAS = {
    "trip": Trip,
    "place": Place,
    "decision": Decision,
    "patch": Patch,
    "patch-op": TypeAdapter(PatchOp),
    "intake": TripIntake,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        schema = model.json_schema() if isinstance(model, TypeAdapter) else model.model_json_schema()
        (OUT / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {name}.schema.json")


if __name__ == "__main__":
    main()
