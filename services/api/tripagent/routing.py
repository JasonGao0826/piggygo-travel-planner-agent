"""车程计算（设计规格 D8）。车程由程序计算，不让模型估。

第 1 阶段先用静态车程表（测试用）；之后接 Google Routes API，再换成自建 OSRM / Valhalla。
"""

import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Literal, NamedTuple, Protocol

import yaml

from .live.geo import thin

RoadType = Literal["normal", "mountain"]


class DriveEstimate(NamedTuple):
    minutes: int     # 路线引擎给出的纯驾驶时间，未乘修正系数
    road: RoadType   # 山路、景观路要乘更大的修正系数


class DriveTimeProvider(Protocol):
    def estimate(self, from_place: str, to_place: str) -> DriveEstimate | None: ...


class StaticDriveTimes:
    """从 YAML 车程表读取，两个方向通用。同一地点车程为 0。"""

    def __init__(self, entries: list[dict]):
        self._table: dict[tuple[str, str], DriveEstimate] = {}
        for e in entries:
            est = DriveEstimate(int(e["minutes"]), e.get("road", "normal"))
            self._table[(e["from"], e["to"])] = est
            self._table[(e["to"], e["from"])] = est

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StaticDriveTimes":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(data.get("drives", []))

    def estimate(self, from_place: str, to_place: str) -> DriveEstimate | None:
        if from_place == to_place:
            return DriveEstimate(0, "normal")
        return self._table.get((from_place, to_place))


class OsrmDriveTimes:
    """用 OSRM 路线服务按坐标计算车程，结果缓存到本地文件。

    默认用 OSRM 的公共演示服务器，只适合开发和少量使用；正式环境换成自建服务器或 Google Routes API。
    平均车速低于 55 km/h 的路线按山路处理（修正系数更大）。
    """

    MOUNTAIN_KMH = 55
    RETRY_AFTER_S = 600   # 查失败的路段 10 分钟后再试

    def __init__(self, places: dict, *, base_url: str = "https://router.project-osrm.org",
                 cache_path: str | Path | None = None, geometry_path: str | Path | None = None, timeout: float = 15):
        self.coords: dict[str, tuple[float, float]] = {}
        self.register(places.values())
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[str, list] = {}
        if self.cache_path and self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.geometry_path = Path(geometry_path) if geometry_path else None
        self.shapes: dict[str, list] = {}
        self.failed: dict[str, float] = {}   # 查失败的路段：一段时间内不再重试（每次校验都会查，不能每次都等超时）
        if self.geometry_path and self.geometry_path.exists():
            self.shapes = json.loads(self.geometry_path.read_text(encoding="utf-8"))

    def register(self, places) -> None:
        """登记地点坐标。行程自己的地点（例如民宿）在用到之前登记。"""
        for p in places:
            if p.lat is not None and p.lon is not None:
                self.coords[p.id] = (p.lat, p.lon)

    @staticmethod
    def _key(a: tuple[float, float], b: tuple[float, float]) -> str:
        return "|".join(f"{lat:.4f},{lon:.4f}" for lat, lon in sorted([a, b]))

    def _recently_failed(self, key: str) -> bool:
        at = self.failed.get(key)
        return at is not None and time.monotonic() - at < self.RETRY_AFTER_S

    def prefetch(self, place_ids: list[str]) -> None:
        """用 OSRM 的 table 接口一次算出多个地点两两之间的车程，存进缓存（生成行程前调用）。"""
        pts = list(dict.fromkeys(self.coords[p] for p in place_ids if p in self.coords))
        missing = [(i, j) for i in range(len(pts)) for j in range(i + 1, len(pts))
                   if self._key(pts[i], pts[j]) not in self.cache]
        if not missing or len(pts) > 100:
            return
        coords = ";".join(f"{lon},{lat}" for lat, lon in pts)
        url = f"{self.base_url}/table/v1/driving/{coords}?annotations=duration,distance"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout * 4) as resp:
                table = json.loads(resp.read())
        except Exception:
            return
        for i, j in missing:
            d, dist = table["durations"][i][j], table["distances"][i][j]
            if d is not None and dist is not None:
                self.cache[self._key(pts[i], pts[j])] = [d, dist]
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")

    def estimate(self, from_place: str, to_place: str) -> DriveEstimate | None:
        if from_place == to_place:
            return DriveEstimate(0, "normal")
        a, b = self.coords.get(from_place), self.coords.get(to_place)
        if a is None or b is None:
            return None
        key = self._key(a, b)
        if key not in self.cache:
            if self._recently_failed(f"route:{key}"):
                return None
            url = f"{self.base_url}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false"
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    route = json.loads(resp.read())["routes"][0]
            except Exception:
                self.failed[f"route:{key}"] = time.monotonic()
                return None  # 查不到就当作没有车程数据，校验器会提示
            self.cache[key] = [route["duration"], route["distance"]]
            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
        duration, distance = self.cache[key]
        kmh = distance / duration * 3.6 if duration else 0
        return DriveEstimate(math.ceil(duration / 60), "mountain" if kmh < self.MOUNTAIN_KMH else "normal")


    def geometry(self, from_place: str, to_place: str) -> list[tuple[float, float]] | None:
        """路线形状（纬度, 经度），用于检查路上有没有封路（R21）。点之间约隔 200 米，结果缓存到本地文件。"""
        a, b = self.coords.get(from_place), self.coords.get(to_place)
        if a is None or b is None or a == b:
            return None
        key = self._key(a, b)
        if key not in self.shapes:
            if self._recently_failed(f"shape:{key}"):
                return None
            url = (f"{self.base_url}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"
                   "?overview=full&geometries=geojson")
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    coords = json.loads(resp.read())["routes"][0]["geometry"]["coordinates"]
            except Exception:
                self.failed[f"shape:{key}"] = time.monotonic()
                return None
            self.shapes[key] = [[round(lat, 5), round(lon, 5)] for lat, lon in thin([(c[1], c[0]) for c in coords], 200)]
            if self.geometry_path:
                self.geometry_path.parent.mkdir(parents=True, exist_ok=True)
                self.geometry_path.write_text(json.dumps(self.shapes), encoding="utf-8")
        return [tuple(p) for p in self.shapes[key]]


class CompositeDriveTimes:
    """依次询问多个来源，用第一个有结果的。人工校准的车程表放在最前面。"""

    def __init__(self, *providers: DriveTimeProvider):
        self.providers = providers

    def register(self, places) -> None:
        for p in self.providers:
            if hasattr(p, "register"):
                p.register(places)

    def prefetch(self, place_ids: list[str]) -> None:
        for p in self.providers:
            if hasattr(p, "prefetch"):
                p.prefetch(place_ids)

    def estimate(self, from_place: str, to_place: str) -> DriveEstimate | None:
        for p in self.providers:
            if (est := p.estimate(from_place, to_place)) is not None:
                return est
        return None

    def geometry(self, from_place: str, to_place: str) -> list[tuple[float, float]] | None:
        for p in self.providers:
            if hasattr(p, "geometry") and (shape := p.geometry(from_place, to_place)):
                return shape
        return None
