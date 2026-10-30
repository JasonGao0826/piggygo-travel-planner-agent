"""景点知识库的读取。知识库存成 YAML，放在仓库的 data/kb 下，方便人工审核。"""

from pathlib import Path

import yaml

from .paths import KB_DATA
from .schema.place import Place

KB_ROOT = KB_DATA / "kb"


def load_kb(root: str | Path = KB_ROOT) -> dict[str, Place]:
    """读取知识库目录下所有 YAML（景点、机场、城镇……）。"""
    return load_places(*sorted(Path(root).rglob("*.yaml")))


def load_places(*paths: str | Path) -> dict[str, Place]:
    """读取一个或多个 YAML 文件（顶层是 places 列表），返回 id → Place。id 重复时报错。"""
    places: dict[str, Place] = {}
    for path in paths:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for raw in data.get("places", []):
            place = Place.model_validate(raw)
            if place.id in places:
                raise ValueError(f"地点 id 重复：{place.id}（{path}）")
            places[place.id] = place
    return places
