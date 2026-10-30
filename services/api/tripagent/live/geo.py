"""简单的地理计算：距离、点到折线的距离、点在多边形内。行程尺度（几百公里）用平面近似就够了。"""

import math
import re

from .models import LatLon

EARTH_M = 6_371_000


def _xy(p: LatLon, lat0: float) -> tuple[float, float]:
    """以 lat0 为基准投影成平面坐标（米）。"""
    return (math.radians(p[1]) * EARTH_M * math.cos(math.radians(lat0)), math.radians(p[0]) * EARTH_M)


def distance_m(a: LatLon, b: LatLon) -> float:
    lat0 = (a[0] + b[0]) / 2
    (ax, ay), (bx, by) = _xy(a, lat0), _xy(b, lat0)
    return math.hypot(ax - bx, ay - by)


def _point_segment_m(p: LatLon, a: LatLon, b: LatLon) -> float:
    lat0 = p[0]
    (px, py), (ax, ay), (bx, by) = _xy(p, lat0), _xy(a, lat0), _xy(b, lat0)
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_line_m(p: LatLon, line: list[LatLon]) -> float:
    if len(line) == 1:
        return distance_m(p, line[0])
    return min(_point_segment_m(p, a, b) for a, b in zip(line, line[1:]))


def bbox(points: list[LatLon], pad_deg: float = 0.0) -> tuple[float, float, float, float]:
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    return min(lats) - pad_deg, min(lons) - pad_deg, max(lats) + pad_deg, max(lons) + pad_deg


def bbox_overlap(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def lines_near(a: list[LatLon], b: list[LatLon], within_m: float) -> bool:
    """两条折线有没有哪里相距不到 within_m 米（只比较顶点到对方折线的距离，顶点足够密时够用）。"""
    if not a or not b:
        return False
    pad = within_m / 100_000
    if not bbox_overlap(bbox(a, pad), bbox(b, pad)):
        return False
    return any(point_line_m(p, b) <= within_m for p in a) or any(point_line_m(p, a) <= within_m for p in b)


def in_polygon(p: LatLon, poly: list[LatLon]) -> bool:
    """射线法判断点是否在多边形内。"""
    lat, lon = p
    inside = False
    for (lat1, lon1), (lat2, lon2) in zip(poly, poly[1:] + poly[:1]):
        if (lat1 > lat) != (lat2 > lat):
            cross = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            if lon < cross:
                inside = not inside
    return inside


def thin(points: list[LatLon], spacing_m: float) -> list[LatLon]:
    """去掉相距不到 spacing_m 的点，保留首尾，用来压缩路线形状。"""
    if len(points) <= 2:
        return points
    out = [points[0]]
    for p in points[1:-1]:
        if distance_m(out[-1], p) >= spacing_m:
            out.append(p)
    out.append(points[-1])
    return out


_GROUP = re.compile(r"\(([^()]+)\)")


def parse_wkt(wkt: str | None) -> list[list[LatLon]]:
    """解析 WKT（POINT / LINESTRING / MULTILINESTRING……），返回若干条（纬度, 经度）折线。WKT 里是"经度 纬度"。"""
    if not wkt:
        return []
    lines = []
    for group in _GROUP.findall(wkt):
        pts = []
        for pair in group.split(","):
            parts = pair.split()
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                pts.append((lat, lon))
        if pts:
            lines.append(pts)
    return lines
