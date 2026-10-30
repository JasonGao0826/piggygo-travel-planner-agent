"""和外部数据比对的规则 R21–R24（设计规格第 22 节）。

R21–R23 用实时数据（ctx.live），没有实时数据时不检查。实时情况随时在变，所以都只报警告，不拦改动。
R24 公众假日离线计算，一直检查。
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from ..live.geo import bbox, bbox_overlap, distance_m, in_polygon, lines_near
from ..live.holidays import NZ_ISLAND, holidays_between
from ..live.models import DayForecast, LatLon
from ..live.sources import SNOW_CODES, STORM_CODES, WEATHER_TEXT
from ..schema.memory import Issue
from ..schema.trip import Item, ItemType
from .context import ValidationContext
from .rules import WARNING, _issue, _md

ROAD_IMPACT = {"Road Closed": "封路", "Delays": "可能延误", "Vehicle Restrictions": "限行"}
UNTIL_NOTICE_H = 48   # 突发事件（事故、塌方）写着"直到另行通知"时，只对这么多小时内的行程报
SHORT_STOP_MIN = 30   # 短暂停留（拍照、看一眼）不提醒天气


def _when(ctx: ValidationContext, start: datetime | None, end: datetime | None) -> str:
    def fmt(dt: datetime) -> str:
        d = ctx.local(dt)
        return f"{d.month}/{d.day} {d:%H:%M}"
    if start and end:
        return f"{fmt(start)} 至 {fmt(end)}"
    if start:
        return f"{fmt(start)} 起，直到另行通知"
    return f"至 {fmt(end)}" if end else "时间未定"


def _points(ctx: ValidationContext, item: Item, with_route: bool = False) -> list[LatLon]:
    pts = []
    for pid in (item.place_id, item.from_place_id, item.to_place_id):
        p = ctx.place(pid)
        if p and p.lat is not None and p.lon is not None:
            pts.append((p.lat, p.lon))
    if with_route and item.type == ItemType.DRIVE:
        pts += (ctx.route(item.from_place_id, item.to_place_id) or [])[::5]
    return pts


# ---------------- R21 路线上有封路、延误 ----------------

def r21_road_events(ctx: ValidationContext) -> list[Issue]:
    if not ctx.live or not ctx.live.road_events:
        return []
    events = [(e, bbox([p for line in e.lines for p in line]))
              for e in ctx.live.road_events if e.impact in ROAD_IMPACT and e.lines]
    fetched = ctx.live.fetched_at.get("roads")
    issues = []
    for item in ctx.trip.all_items():
        if item.type != ItemType.DRIVE or not events:
            continue
        candidates = []
        for ev, box in events:
            if not ev.active_during(item.start, item.end):
                continue
            if ev.end is None and not ev.planned and fetched and item.start > fetched + timedelta(hours=UNTIL_NOTICE_H):
                continue  # 突发事件一般几小时内解除；到时候还没解除，下次刷新会再报
            candidates.append((ev, box))
        ends = _points(ctx, item)
        if not candidates or len(ends) < 2:
            continue
        # 先粗筛：起终点周围（留出绕路的余量）没有事件，就不用去取路线形状
        lat_span, lon_span = abs(ends[0][0] - ends[1][0]), abs(ends[0][1] - ends[1][1])
        area = bbox(ends, max(0.3, 0.3 * max(lat_span, lon_span)))
        candidates = [ev for ev, box in candidates if bbox_overlap(area, box)]
        if not candidates:
            continue
        route = ctx.route(item.from_place_id, item.to_place_id)
        if not route:
            continue
        for ev in candidates:
            if not any(lines_near(line, route, ctx.config.road_event_m) for line in ev.lines):
                continue
            what = ROAD_IMPACT[ev.impact]
            text = ev.detail if len(ev.detail) <= 160 else ev.detail[:160].rstrip() + "…"
            detail = f"说明：{text}" if text else ""
            suggestions = (["到 NZTA 路况页确认封路时段", "换一条路线或调整出发时间"] if ev.impact == "Road Closed"
                           else ["这段路多留 20–30 分钟", "出发前再看一次路况"])
            issues.append(_issue("R21", WARNING,
                                 f"实时路况：{ctx.label(item)} 途经 {ev.title}，{what}（{ev.kind}，{_when(ctx, ev.start, ev.end)}）。{detail}",
                                 [item.id], suggestions))
    return issues


# ---------------- R22 天气预警覆盖行程 ----------------

def r22_weather_alerts(ctx: ValidationContext) -> list[Issue]:
    if not ctx.live or not ctx.live.weather_alerts:
        return []
    issues = []
    for alert in ctx.live.weather_alerts:
        if not alert.polygons:
            continue
        hit: dict[date, list[Item]] = defaultdict(list)
        for item in ctx.trip.all_items():
            if item.type == ItemType.FLIGHT or not alert.active_during(item.start, item.end):
                continue
            pts = _points(ctx, item, with_route=True)
            if any(in_polygon(p, poly) for p in pts for poly in alert.polygons):
                hit[ctx.local(item.start).date()].append(item)
        for day, items in sorted(hit.items()):
            names = "、".join(i.title for i in items[:4]) + ("等" if len(items) > 4 else "")
            area = "、".join(a for a in alert.areas if a)[:80]
            issues.append(_issue("R22", WARNING,
                                 f"天气预警：MetService 发布 {alert.event}（{area}，{_when(ctx, alert.onset, alert.expires)}），"
                                 f"影响 {_md(day)} 的 {names}",
                                 [i.id for i in items],
                                 ["关注 MetService 最新预警", "户外项目准备备用安排或改期", "山路自驾注意落石、积水和临时封路"]))
    return issues


# ---------------- R23 预报不适合户外项目 ----------------

def _forecast(ctx: ValidationContext, point: LatLon, day: date) -> DayForecast | None:
    best, best_d = None, 20_000.0
    for f in ctx.live.forecasts:
        if f.date == day and (d := distance_m(point, (f.lat, f.lon))) <= best_d:
            best, best_d = f, d
    return best


def _risks(ctx: ValidationContext, f: DayForecast) -> list[str]:
    cfg, out = ctx.config, []
    if f.code in STORM_CODES:
        out.append(WEATHER_TEXT[f.code])
    if f.code in SNOW_CODES:
        out.append(WEATHER_TEXT[f.code])
    if f.precip_mm is not None and f.precip_mm >= cfg.rain_mm and (f.precip_prob is None or f.precip_prob >= cfg.rain_prob):
        out.append(f"降雨约 {f.precip_mm:g} 毫米" + (f"（概率 {f.precip_prob}%）" if f.precip_prob is not None else ""))
    if f.gust_kmh is not None and f.gust_kmh >= cfg.gust_kmh:
        out.append(f"阵风 {f.gust_kmh:.0f} km/h")
    return out


def r23_forecast(ctx: ValidationContext) -> list[Issue]:
    if not ctx.live or not ctx.live.forecasts:
        return []
    issues = []
    for item in ctx.trip.all_items():
        day = ctx.local(item.start).date()
        if item.type == ItemType.ACTIVITY:
            place = ctx.place(item.place_id)
            if not (item.weather_dependent or (place and place.weather_dependent)):
                continue
            if (place and place.kind == "viewpoint") or item.end - item.start < timedelta(minutes=SHORT_STOP_MIN):
                continue
            pts = _points(ctx, item)
            f = _forecast(ctx, pts[0], day) if pts else None
            risks = _risks(ctx, f) if f else []
            if risks:
                suggestions = (["和天气好的一天对调", "确认商家的取消和改期规则"] if item.backup
                               else ["写一个备用方案", "和天气好的一天对调", "确认商家的取消和改期规则"])
                issues.append(_issue("R23", WARNING, f"天气预报：{ctx.label(item)} 当天{'、'.join(risks)}",
                                     [item.id], suggestions))
        elif item.type == ItemType.DRIVE:
            snowy = [f for p in _points(ctx, item) if (f := _forecast(ctx, p, day)) and f.code in SNOW_CODES]
            if snowy:
                issues.append(_issue("R23", WARNING, f"天气预报：{ctx.label(item)} 沿途可能下雪",
                                     [item.id], ["租车时带防滑链", "出发前看 NZTA 路况，山口可能临时封闭"]))
    return issues


# ---------------- R24 公众假日 ----------------

def _regions_on(ctx: ValidationContext, day) -> set[str]:
    regions = set()
    for item in day.items:
        for pid in (item.place_id, item.from_place_id, item.to_place_id):
            if (p := ctx.place(pid)) is not None:
                regions.add(p.region)
    return regions


def r24_holidays(ctx: ValidationContext) -> list[Issue]:
    meta = ctx.trip.meta
    days = {d.date: d for d in ctx.trip.days}
    issues = []
    for h in holidays_between(meta.destination, meta.start_date, meta.end_date):
        day = days.get(h.date)
        if day is None:
            continue
        if h.region is None:
            if h.shops_closed:
                msg = f"{_md(h.date)} 是公众假日 {h.name}：大部分商店和超市不营业，营业的餐厅一般加收假日附加费"
                sug = ["提前一天去超市采购", "提前订好当天的餐厅和项目"]
            else:
                msg = f"{_md(h.date)} 是公众假日 {h.name}：不少餐厅加收假日附加费（常见 10%–15%），热门景点和住宿更紧张"
                sug = ["提前订好当天的餐厅和项目"]
        else:
            regions = _regions_on(ctx, day)
            island = NZ_ISLAND.get(h.region) if meta.destination == "NZ" else None
            if not (island in regions or h.region in regions or h.region_name in regions):
                continue
            msg = f"{_md(h.date)} 是 {h.region_name} 地区的假日 {h.name}：当地部分商店营业时间可能调整，住宿和餐厅可能更忙"
            sug = ["提前订好当天的住宿和餐厅"]
        issues.append(_issue("R24", WARNING, msg, suggestions=sug))
    return issues


LIVE_RULES = [r21_road_events, r22_weather_alerts, r23_forecast, r24_holidays]
