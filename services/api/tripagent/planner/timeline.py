"""S3 + S5：把路线框架（Skeleton）和每天的安排（DayPlans）排成完整行程。

住宿、租车、航班、开车、签到、入住退房都由这里按规则生成，车程用程序算，模型不写具体时刻（固定场次除外）。
排不下的地方（例如赶不上固定场次）记成 notes，交给校验器和模型修正。
"""

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..routing import DriveTimeProvider
from ..schema.intake import TripIntake
from ..schema.place import Place
from ..schema.trip import (Accommodation, BookingStatus, Day, FlightInfo, Item, ItemType, LockReason, Member,
                           Segment, Todo, Trip, TripMeta, TripPrefs, Vehicle)
from ..validator.context import ValidatorConfig
from .models import DayPlans, FlightPlan, Skeleton

DEST_NAME = {"NZ": "新西兰", "AU": "澳洲"}
DEFAULT_DEPART = time(8, 30)
CHECKOUT_MIN = 15
CHECKIN_MIN = 15
CAR_PICKUP_MIN = 30
CAR_DROPOFF_MIN = 15


def _hm(text: str | None) -> time | None:
    if not text:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", text.strip())
    return time(int(m.group(1)), int(m.group(2))) if m else None


def _ceil5(minutes: float) -> int:
    return max(5, int(math.ceil(minutes / 5.0) * 5))


def _round_up5(dt: datetime) -> datetime:
    extra = (-dt.minute) % 5
    return (dt + timedelta(minutes=extra)).replace(second=0, microsecond=0)


@dataclass
class _DayState:
    n: int
    day: date
    items: list[Item] = field(default_factory=list)
    seq: int = 0
    loc: str | None = None
    t: datetime | None = None


class TimelineBuilder:
    def __init__(self, intake: TripIntake, members: list[Member], skeleton: Skeleton, plans: DayPlans,
                 places: dict[str, Place], drive: DriveTimeProvider, *, trip_id: str, today: date,
                 config: ValidatorConfig | None = None, home_timezone: str = "Australia/Melbourne",
                 local_timezone: str = "Pacific/Auckland"):
        self.intake, self.members, self.skeleton, self.plans = intake, members, skeleton, plans
        self.places, self.drive, self.trip_id, self.today = places, drive, trip_id, today
        self.config = config or ValidatorConfig()
        self.home_tz, self.tz = ZoneInfo(home_timezone), ZoneInfo(local_timezone)
        self.home_timezone, self.local_timezone = home_timezone, local_timezone
        self.notes: list[str] = []
        self.assumptions: list[str] = []
        self.drivers = [m.id for m in members if m.can_drive and (m.age or 25) >= self.config.min_driver_age]
        self._driver_turn = 0

    # ---------- 工具 ----------

    def tz_of(self, place_id: str) -> ZoneInfo:
        p = self.places.get(place_id)
        return self.home_tz if p and p.region == "澳洲" else self.tz

    def at(self, d: date, t: time, place_id: str | None = None) -> datetime:
        return datetime.combine(d, t, self.tz_of(place_id) if place_id else self.tz)

    def name(self, place_id: str) -> str:
        p = self.places.get(place_id)
        return p.name_zh if p else place_id

    def add(self, st: _DayState, kind: ItemType, title: str, start: datetime, end: datetime, **kw) -> Item:
        st.seq += 1
        item = Item(id=f"d{st.n}_{st.seq:02d}_{kind.value}", type=kind, title=title, start=start, end=end, **kw)
        st.items.append(item)
        return item

    def vehicle_at(self, when: datetime) -> Vehicle | None:
        return next((v for v in self.vehicles if v.pickup_time <= when <= v.dropoff_time), None)

    def next_driver(self, vehicle: Vehicle) -> str | None:
        pool = [d for d in vehicle.drivers] or self.drivers
        if not pool:
            return None
        self._driver_turn += 1
        return pool[self._driver_turn % len(pool)]

    def move(self, st: _DayState, to: str) -> None:
        """从当前位置移动到 to：在租车时间内就开车，否则用接驳 / 公共交通。"""
        if st.loc is None or st.loc == to:
            st.loc = to
            return
        est = self.drive.estimate(st.loc, to)
        if est is None:
            self.notes.append(f"{st.day:%m/%d} {self.name(st.loc)} → {self.name(to)}：没有车程数据，先按 60 分钟估")
            minutes, road = 60, "normal"
        else:
            factor = self.config.factor_mountain if est.road == "mountain" else self.config.factor_normal
            minutes, road = est.minutes * factor, est.road
        dur = timedelta(minutes=_ceil5(minutes))
        title = f"{self.name(st.loc)} → {self.name(to)}"
        vehicle = self.vehicle_at(st.t)
        if vehicle and self.vehicle_at(st.t + dur) is vehicle:
            self.add(st, ItemType.DRIVE, title, st.t, st.t + dur, from_place_id=st.loc, to_place_id=to,
                     vehicle_id=vehicle.id, driver_id=self.next_driver(vehicle),
                     notes=["山路，注意安全"] if road == "mountain" else [])
        else:
            self.add(st, ItemType.TRANSFER, title, st.t, st.t + dur, from_place_id=st.loc, to_place_id=to,
                     notes=["公共交通或接驳，班次待确认"])
        st.t, st.loc = st.t + dur, to

    # ---------- 航班、住宿、租车 ----------

    def flight_times(self, f: FlightPlan) -> tuple[datetime, datetime]:
        d = date.fromisoformat(f.date)
        start = datetime.combine(d, _hm(f.depart) or time(9), self.tz_of(f.from_airport))
        if _hm(f.arrive) is None:  # 用户只给了起飞时间
            self.assumptions.append(f"航班 {f.number or ''} 没有写到达时间，先按飞行约 3 小时估算，以机票为准".replace("  ", " "))
            return start, start + timedelta(hours=3)
        arr_day = d + timedelta(days=1) if f.arrive_next_day else d
        end = datetime.combine(arr_day, _hm(f.arrive), self.tz_of(f.to_airport))
        if end <= start:
            end = start + timedelta(hours=2)
            self.notes.append(f"航班 {f.number or ''} {f.from_airport}→{f.to_airport} 到达时间早于起飞，先按 2 小时处理")
        return start, end

    def build_segments_and_vehicles(self) -> list[Segment]:
        segments, self.vehicles = [], []
        flights = [(f, *self.flight_times(f)) for f in self.skeleton.flights]
        for i, s in enumerate(self.skeleton.segments, 1):
            seg = Segment(id=f"seg_{i}", name=s.name, transport=s.transport,
                          start_date=date.fromisoformat(s.start_date), end_date=date.fromisoformat(s.end_date))
            if s.transport == "self_drive":
                # 转机时在最后一段落地的机场取车（例如皇后镇 → 奥克兰 → 罗托鲁瓦，在罗托鲁瓦取车）
                arrival = max(((f, e) for f, _, e in flights
                               if e.astimezone(self.tz).date() == seg.start_date and self.places.get(f.to_airport)
                               and self.places[f.to_airport].region != "澳洲"), key=lambda x: x[1], default=None)
                departure = next(((f, st) for f, st, _ in flights
                                  if st.astimezone(self.tz).date() == seg.end_date and self.places.get(f.from_airport)
                                  and self.places[f.from_airport].region != "澳洲"), None)
                if arrival:
                    f, e = arrival
                    pickup_place = f.to_airport
                    pickup = _round_up5(e + timedelta(minutes=45 if f.international else 30))
                else:
                    first_night = next((d.night_place_id for d in self.skeleton.days if d.date == s.start_date), None)
                    pickup_place, pickup = first_night or "", self.at(seg.start_date, time(9))
                if departure:
                    f, st = departure
                    need = self.config.international_flight_min if f.international else self.config.domestic_flight_min
                    dropoff_place, dropoff = f.from_airport, st - timedelta(minutes=need)
                else:
                    last_night = next((d.night_place_id for d in self.skeleton.days if d.date == s.end_date), None)
                    dropoff_place, dropoff = last_night or pickup_place, self.at(seg.end_date, time(18))
                vid = f"car_{i}"
                self.vehicles.append(Vehicle(
                    id=vid, model="SUV（按人数和行李选车型）", pickup_place_id=pickup_place, pickup_time=pickup,
                    dropoff_place_id=dropoff_place, dropoff_time=dropoff, drivers=list(self.drivers),
                    booking_status=BookingStatus.TO_BOOK, notes=["确认全险、自付额、押金、异地还车费和第二驾驶员费用"]))
                seg.vehicle_id = vid
            segments.append(seg)
        return segments

    def build_accommodations(self) -> list[Accommodation]:
        accs: list[Accommodation] = []
        for d in self.skeleton.days:
            if not d.night_place_id:
                continue
            day = date.fromisoformat(d.date)
            if accs and accs[-1].place_id == d.night_place_id and accs[-1].check_out == day:
                accs[-1].check_out = day + timedelta(days=1)
                continue
            slug = d.night_place_id.removeprefix("nz_").removeprefix("au_")
            budget = self.intake.room_budget_per_night
            accs.append(Accommodation(
                id=f"acc_{slug}_{len(accs) + 1}", name=f"{d.night_city or self.name(d.night_place_id)}住宿（待订）",
                city=d.night_city or self.name(d.night_place_id), place_id=d.night_place_id, check_in=day,
                check_out=day + timedelta(days=1), earliest_checkin=time(15), latest_checkout=time(10),
                rooms=self.intake.room_requirements, booking_status=BookingStatus.TO_BOOK,
                notes=[f"每间每晚预算约 {budget.amount:g} {budget.currency}"] if budget else []))
        return accs

    # ---------- 每天的时间线 ----------

    def build_day(self, n: int, sk_day, plan, accs: list[Accommodation], prev_night: str | None) -> Day:
        day = date.fromisoformat(sk_day.date)
        st = _DayState(n=n, day=day, loc=prev_night)
        flights = sorted(((f, *self.flight_times(f)) for f in self.skeleton.flights
                          if f.date == sk_day.date), key=lambda x: x[1])
        leaving = next((a for a in accs if a.check_out == day), None)
        arriving = next((a for a in accs if a.check_in == day), None)
        depart = _hm(plan.depart) if plan and plan.depart else DEFAULT_DEPART

        def checkout(at: datetime):
            if leaving:
                if leaving.latest_checkout:  # 不晚于最晚退房时间，多出来的时间就等着
                    at = min(at, self.at(day, leaving.latest_checkout))
                self.add(st, ItemType.CHECKOUT, f"{leaving.city}退房", at - timedelta(minutes=CHECKOUT_MIN), at,
                         place_id=leaving.place_id, accommodation_id=leaving.id)

        def take_flight(f: FlightPlan, start: datetime, end: datetime):
            need = self.config.international_flight_min if f.international else self.config.domestic_flight_min
            if st.loc != f.from_airport:
                est = self.drive.estimate(st.loc, f.from_airport) if st.loc else None
                drive_min = _ceil5(est.minutes * self.config.factor_normal) if est else 30
                leave = start - timedelta(minutes=need + drive_min + 5)
                v = self.vehicle_at(leave)
                if v and v.dropoff_place_id == f.from_airport:
                    leave -= timedelta(minutes=CAR_DROPOFF_MIN)
                if st.t is None:
                    checkout(leave)
                    st.t = leave
                self.move(st, f.from_airport)
                v = self.vehicle_at(st.t - timedelta(minutes=1))
                if v and v.dropoff_place_id == f.from_airport:
                    self.add(st, ItemType.CAR_DROPOFF, f"{self.name(f.from_airport)}还车", st.t,
                             st.t + timedelta(minutes=CAR_DROPOFF_MIN), place_id=f.from_airport, vehicle_id=v.id)
                    st.t += timedelta(minutes=CAR_DROPOFF_MIN)
            booked = f.booked
            self.add(st, ItemType.FLIGHT, f"{f.number or '航班'} {self.name(f.from_airport).removesuffix('机场')} → "
                     f"{self.name(f.to_airport).removesuffix('机场')}", start, end, from_place_id=f.from_airport,
                     to_place_id=f.to_airport, flight=FlightInfo(number=f.number or "待订", international=f.international),
                     checkin_before_min=need,
                     booking_status=BookingStatus.BOOKED if booked else BookingStatus.TO_BOOK,
                     locked=booked, lock_reason=LockReason.USER if booked else None,
                     source="user" if booked else "agent_inferred",
                     notes=[] if booked else ["航班时间是参考，以实际订到的为准"])
            st.t, st.loc = end, f.to_airport
            home = self.places.get(f.to_airport)
            if home and home.region == "澳洲" and self.intake.destination != "AU":
                return  # 回程落地后不再安排
            bags = 45 if f.international else 30
            self.add(st, ItemType.BUFFER, "入境检查、领取行李" if f.international else "领取行李", st.t,
                     st.t + timedelta(minutes=bags), place_id=f.to_airport)
            st.t += timedelta(minutes=bags)
            v = next((v for v in self.vehicles if v.pickup_place_id == f.to_airport
                      and abs((v.pickup_time - st.t).total_seconds()) < 3 * 3600), None)
            if v:
                st.t = max(st.t, v.pickup_time)
                self.add(st, ItemType.CAR_PICKUP, f"{self.name(f.to_airport)}取车", st.t,
                         st.t + timedelta(minutes=CAR_PICKUP_MIN), place_id=f.to_airport, vehicle_id=v.id)
                st.t += timedelta(minutes=CAR_PICKUP_MIN)

        def region(place_id: str) -> str | None:
            p = self.places.get(place_id)
            return p.region if p else None

        def after_flight(stop, k: int) -> bool:
            """这个地点排在第 k 班飞机之后吗？转机时排在最后一段之后；跨区域（例如南岛飞北岛）按地点所在区域判断，
            同区域按起飞时间判断。"""
            flight, start = flights[k][0], flights[k][1]
            if k > 0 and flights[k - 1][0].to_airport == flight.from_airport                     and start - flights[k - 1][2] < timedelta(hours=6):
                return True
            src, dst = region(flight.from_airport), region(flight.to_airport)
            if src != dst:
                return region(stop.place_id) == dst
            return start.astimezone(self.tz_of(flight.from_airport)).hour < 12

        stops = [x for x in (plan.stops if plan else []) if x.place_id in self.places]
        for x in (plan.stops if plan else []):
            if x.place_id not in self.places:
                self.notes.append(f"{day:%m/%d} {x.title}：地点 {x.place_id} 不在知识库里，已跳过")
        phases: list[list] = [[] for _ in range(len(flights) + 1)]  # 第 k 段是第 k 班飞机之前的地点
        for x in stops:
            k = 0
            while k < len(flights) and after_flight(x, k):
                k += 1
            phases[k].append(x)

        def visit(stop) -> None:
            self.move(st, stop.place_id)
            place = self.places[stop.place_id]
            checkin = place.checkin_before_min or 0 if stop.kind == "activity" else 0
            fixed = _hm(stop.start)
            if fixed:
                start = self.at(day, fixed)
                if st.t + timedelta(minutes=checkin) > start:
                    self.notes.append(f"{day:%m/%d} {stop.title} 固定 {fixed:%H:%M} 开始，但按车程 "
                                      f"{st.t.astimezone(self.tz):%H:%M} 才到（还要提前 {checkin} 分钟签到）")
            else:
                start = _round_up5(st.t + timedelta(minutes=checkin))
            end = start + timedelta(minutes=max(10, stop.duration_min))
            booking = {"booked": BookingStatus.BOOKED, "to_book": BookingStatus.TO_BOOK}.get(stop.booking, BookingStatus.NOT_REQUIRED)
            kind = {"activity": ItemType.ACTIVITY, "meal": ItemType.MEAL,
                    "free_time": ItemType.FREE_TIME, "buffer": ItemType.BUFFER}[stop.kind]
            weather = bool(stop.weather_backup) or (place.weather_dependent and stop.kind == "activity"
                                                    and any(t in place.tags for t in ("paragliding", "skydiving", "hiking")))
            self.add(st, kind, stop.title, start, end, place_id=stop.place_id, checkin_before_min=checkin,
                     booking_status=booking, locked=booking == BookingStatus.BOOKED,
                     lock_reason=LockReason.USER if booking == BookingStatus.BOOKED else None,
                     source="user" if booking == BookingStatus.BOOKED else "knowledge_base",
                     notes=list(stop.notes), weather_dependent=weather,
                     backup=stop.weather_backup or ("天气不好时改到其他日子，或换成附近的室内项目" if weather else None),
                     optional=stop.optional and booking != BookingStatus.BOOKED,
                     optional_reason=stop.optional_reason if stop.optional else None)
            st.t = max(st.t, end)

        for k in range(len(flights) + 1):
            if phases[k] and st.t is None:  # 早上先去景点，再坐飞机
                st.t = self.at(day, depart)
                checkout(st.t)
            for stop in phases[k]:
                visit(stop)
            if k < len(flights):
                take_flight(*flights[k])
        if st.t is None:
            st.t = self.at(day, depart)
            checkout(st.t)
        ends_home = flights and (self.places.get(flights[-1][0].to_airport) or None) and             self.places[flights[-1][0].to_airport].region == "澳洲" and self.intake.destination != "AU"
        if sk_day.night_place_id and not ends_home:
            self.move(st, sk_day.night_place_id)
            if arriving:
                self.add(st, ItemType.CHECKIN, f"入住{arriving.city}住宿", st.t,
                         st.t + timedelta(minutes=CHECKIN_MIN), place_id=arriving.place_id, accommodation_id=arriving.id)
        return Day(date=day, segment_id=next((s.id for s in self.segments if s.name == sk_day.segment), None),
                   night_city=sk_day.night_city, route=sk_day.route, items=st.items)

    # ---------- 待办 ----------

    def booking_todos(self, trip_items: list[Item], accs: list[Accommodation]) -> list[Todo]:
        todos = []
        start = self.intake.start_date or self.today
        default_due = max(self.today + timedelta(days=7), min(start - timedelta(days=21), self.today + timedelta(days=30)))
        for item in trip_items:
            if item.booking_status != BookingStatus.TO_BOOK:
                continue
            due = default_due
            place = self.places.get(item.place_id or "")
            notes = []
            if place and place.booking.lead_days:
                opens = item.start.astimezone(self.tz).date() - timedelta(days=place.booking.lead_days)
                due = max(opens, self.today)
                notes.append(f"提前 {place.booking.lead_days} 天开放预约（{opens.month}/{opens.day}）")
            if item.type == ItemType.FLIGHT:
                due, notes = self.today + timedelta(days=7), ["机票越早订越便宜"]
            todos.append(Todo(id=f"todo_book_{item.id}", title=f"预订：{item.title}", kind="booking",
                              item_ids=[item.id], due=due, notes=notes))
        for a in accs:
            todos.append(Todo(id=f"todo_book_{a.id}", title=f"预订{a.city}住宿（{a.check_in.month}/{a.check_in.day} 入住，"
                              f"{(a.check_out - a.check_in).days} 晚）", kind="booking", item_ids=[a.id],
                              due=self.today + timedelta(days=7)))
        for v in self.vehicles:
            todos.append(Todo(id=f"todo_book_{v.id}", title=f"预订租车（{self.name(v.pickup_place_id)}取车 → "
                              f"{self.name(v.dropoff_place_id)}还车）", kind="booking", item_ids=[v.id],
                              due=self.today + timedelta(days=7)))
        return todos

    # ---------- 组装 ----------

    def build(self, predeparture: list[Todo]) -> Trip:
        self.segments = self.build_segments_and_vehicles()
        accs = self.build_accommodations()
        plans = {p.date: p for p in self.plans.days}
        days, prev_night = [], None
        first_flight = min(self.skeleton.flights, key=lambda f: self.flight_times(f)[0], default=None)
        for n, sk in enumerate(self.skeleton.days, 1):
            if n == 1 and first_flight and first_flight.date == sk.date:
                prev_night = first_flight.from_airport
            days.append(self.build_day(n, sk, plans.get(sk.date), accs, prev_night))
            prev_night = sk.night_place_id or prev_night
        items = [i for d in days for i in d.items]
        intake = self.intake
        regions = "、".join(intake.regions)
        drive = "自驾" if any(s.transport == "self_drive" for s in self.segments) else ""
        meta = TripMeta(
            id=self.trip_id, title=f"{DEST_NAME.get(intake.destination, intake.destination)}{regions}{drive} "
                                   f"{len(days)} 天 {len(days) - 1} 晚",
            destination=intake.destination, start_date=date.fromisoformat(self.skeleton.days[0].date),
            end_date=date.fromisoformat(self.skeleton.days[-1].date), origin_city=intake.origin_city,
            home_timezone=self.home_timezone, local_timezone=self.local_timezone,
            local_currency="NZD" if intake.destination == "NZ" else "AUD", settle_currency=intake.settle_currency,
            prefs=TripPrefs(max_daily_drive_hours=intake.max_daily_drive_hours or 5.0,
                            earliest_departure=intake.earliest_start or time(7),
                            room_requirements=intake.room_requirements),
        )
        trip = Trip(meta=meta, members=self.members, segments=self.segments, days=days, accommodations=accs,
                    vehicles=self.vehicles, todos=predeparture + self.booking_todos(items, accs),
                    assumptions=list(dict.fromkeys(self.skeleton.assumptions + self.plans.assumptions
                                                   + self.assumptions)))
        return trip.normalized()
