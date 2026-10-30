"""给模型看的知识库目录和车程表。模型只能从目录里选地点，车程都由程序算好给它。"""

from ..routing import DriveTimeProvider
from ..schema.place import Place

REGIONS = {"NZ": {"南岛", "北岛"}, "AU": {"澳洲"}}


def destination_places(places: dict[str, Place], destination: str) -> dict[str, Place]:
    regions = REGIONS.get(destination, set())
    return {pid: p for pid, p in places.items() if p.region in regions}


def _hours(p: Place) -> str:
    if p.always_open:
        return "全天"
    if not p.hours:
        return "营业时间未核实"
    r = p.hours[0]
    text = f"{r.open:%H:%M}–{r.close:%H:%M}"
    if r.last_entry:
        text += f" 最晚{r.last_entry:%H:%M}入场"
    if r.season:
        text += f"（{r.season}）"
    return text


def catalog_text(places: dict[str, Place]) -> str:
    """一行一个地点：id｜名称｜类型｜城镇｜项目类型｜营业｜时长｜预约｜限制。"""
    lines = []
    for p in sorted(places.values(), key=lambda x: (x.region, x.kind != "town", x.city or "", x.id)):
        parts = [p.id, f"{p.name_zh} / {p.name_en}", p.kind, p.city or "", ",".join(p.tags)]
        if p.kind not in ("town", "airport"):
            parts.append(_hours(p))
            if p.duration_min or p.duration_max:
                parts.append(f"建议 {p.duration_min or p.duration_max}–{p.duration_max or p.duration_min} 分钟")
            if p.checkin_before_min:
                parts.append(f"提前 {p.checkin_before_min} 分钟签到")
            if p.booking.required:
                parts.append("需预约" + (f"（提前 {p.booking.lead_days} 天开放）" if p.booking.lead_days else ""))
            if p.restrictions:
                parts.append("限制：" + "；".join(r.text for r in p.restrictions[:2]))
            if p.weather_dependent:
                parts.append("受天气影响")
        lines.append("｜".join(x for x in parts if x))
    return "\n".join(lines)


def travel_text(places: dict[str, Place], drive: DriveTimeProvider, *, max_minutes: int = 240) -> str:
    """城镇之间、城镇到景点的车程（分钟，未含停留），只列 max_minutes 以内的。"""
    towns = [p for p in places.values() if p.kind in ("town", "airport")]
    spots = [p for p in places.values() if p.kind not in ("town", "airport", "accommodation")]
    if hasattr(drive, "prefetch"):
        drive.prefetch([p.id for p in towns + spots])
    lines = []
    for t in towns:
        near = []
        for other in towns + spots:
            if other.id == t.id:
                continue
            est = drive.estimate(t.id, other.id)
            if est and est.minutes <= max_minutes:
                near.append((est.minutes, other.id, est.road))
        near.sort()
        if near:
            lines.append(f"{t.id} → " + "，".join(f"{pid} {m}分{'(山路)' if road == 'mountain' else ''}"
                                                 for m, pid, road in near[:25]))
    return "\n".join(lines)
