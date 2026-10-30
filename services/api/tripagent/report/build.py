"""从行程数据生成报告内容（设计规格 17.2、17.3 节）。

所有时间、价格、地址都从行程状态和知识库里取，模型不参与写数字。章节结构参照我们的新西兰计划 V3 报告。
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..schema.common import Money
from ..schema.place import Place
from ..schema.trip import BookingStatus, Day, Item, ItemType, Trip
from ..checklist import bring_by_day_text, departure_checklist
from ..validator.context import ValidatorConfig
from .model import Bullets, Card, Heading, Link, Paragraph, Report, Row, Section, Table

WEEKDAYS = "一二三四五六日"
# 明亮、浅色的地点配色，按地点出现顺序分配
PALETTE = ["DCEBFA", "E2F3E0", "FFF1D2", "FCE4EC", "EDE7F6", "E0F2F1", "FBE9E7", "F1F8E9"]
VISIT_KINDS = {"activity", "attraction", "trailhead", "viewpoint", "restaurant"}
CURRENCY = {"NZD": "NZ$", "AUD": "A$", "CNY": "¥", "USD": "US$"}
STATUS = {BookingStatus.BOOKED: "已订", BookingStatus.TO_BOOK: "待订",
          BookingStatus.NOT_REQUIRED: "", BookingStatus.CANCELLED: "已取消"}
TODO_KINDS = {"booking": "预订", "visa": "签证入境", "licence_translation": "驾照", "payment": "支付",
              "confirm": "确认", "other": "其他"}
FULL_WIDTH_MM = 267  # 横向 A4 减去左右边距


def money(m: Money) -> str:
    amount = m.amount.normalize() if m.amount == m.amount.to_integral() else m.amount.quantize(Decimal("0.01"))
    per = {"person": "/人", "vehicle": "/车", "group": "/组"}[m.per]
    return f"{CURRENCY.get(m.currency, m.currency + ' ')}{amount:f}{per}"


class ReportBuilder:
    def __init__(self, trip: Trip, places: dict[str, Place], *, changelog: list[str] = (),
                 generated_on: date | None = None, vouchers: list[dict] = (), money: dict | None = None,
                 guide_links: dict[str, list[tuple[str, str]]] | None = None):
        self.trip = trip
        self.guide_links = guide_links or {}   # 地点 id → 大家贴过的小红书攻略（标题, 链接）
        self.vouchers = list(vouchers)  # 凭证夹（vault.VoucherService.views 的结果）
        self.money = money              # 账本（money.MoneyService.summary 的结果）
        self.bring = bring_by_day_text(trip, places)        # 每天要带的东西
        self.places = {**places, **{p.id: p for p in trip.custom_places}}
        self.changelog = list(changelog)
        self.generated_on = generated_on or date.today()
        self.tz = ZoneInfo(trip.meta.local_timezone)
        self.home_tz = ZoneInfo(trip.meta.home_timezone)
        self.names = {m.id: m.name for m in trip.members}
        self.colors: dict[str, str] = {}
        for city in [self.city_of(d) for d in trip.days] + [a.city for a in trip.accommodations]:
            if city and city not in self.colors:
                self.colors[city] = PALETTE[len(self.colors) % len(PALETTE)]

    # ---------- 小工具 ----------

    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz)

    def md(self, d: date) -> str:
        return f"{d.month}/{d.day}"

    def wd(self, d: date) -> str:
        return "周" + WEEKDAYS[d.weekday()]

    def hm(self, dt: datetime, tz: ZoneInfo | None = None) -> str:
        t = dt.astimezone(tz or self.tz)
        suffix = f" {t.tzname()}" if (tz and tz is not self.tz) else ""
        return f"{t:%H:%M}{suffix}"

    def tz_of(self, place_id: str | None) -> ZoneInfo:
        place = self.places.get(place_id or "")
        return self.home_tz if place and place.region == "澳洲" else self.tz

    def place_name(self, place_id: str | None) -> str:
        place = self.places.get(place_id or "")
        return place.name_zh if place else (place_id or "")

    def city_name(self, place_id: str | None) -> str:
        return self.place_name(place_id).removesuffix("机场")

    def city_of(self, day: Day) -> str | None:
        if day.night_city:
            return day.night_city
        earlier = [d.night_city for d in self.trip.days if d.date < day.date and d.night_city]
        return earlier[-1] if earlier else None

    def who(self, item: Item) -> str:
        if item.participants is None or set(item.participants) == self.trip.member_ids():
            return "全员"
        return "、".join(self.names.get(m, m) for m in item.participants)

    def time_range(self, item: Item) -> str:
        start_tz, end_tz = (self.tz_of(item.from_place_id), self.tz_of(item.to_place_id)) if item.is_movement else (None, None)
        start = self.hm(item.start, start_tz if start_tz is not self.tz else None)
        end = self.hm(item.end, end_tz if end_tz is not self.tz else None)
        return start if item.start == item.end else f"{start}–{end}"

    def drive_hours(self, day: Day) -> float:
        per_member: dict[str, float] = defaultdict(float)
        for item in day.items:
            if item.type == ItemType.DRIVE:
                for m in self.trip.participants_of(item):
                    per_member[m] += (item.end - item.start).total_seconds() / 3600
        return max(per_member.values(), default=0.0)

    def hours_text(self, place: Place, day: date) -> str:
        if place.always_open:
            return "全天开放"
        if not place.hours:
            return "未核实，出发前请确认"
        rule = place.hours_on(day)
        if rule is None:
            return "当天不营业"
        text = f"{rule.open:%H:%M}–{rule.close:%H:%M}"
        if rule.last_entry:
            text += f"，最晚 {rule.last_entry:%H:%M} 入场"
        return text

    @staticmethod
    def duration_text(place: Place) -> str | None:
        def fmt(minutes: int) -> str:
            return f"{minutes} 分钟" if minutes < 60 else f"{minutes / 60:g} 小时"
        lo, hi = place.duration_min, place.duration_max
        if lo and hi and lo != hi:
            if lo >= 60:
                return f"约 {lo / 60:g}–{hi / 60:g} 小时"
            if hi < 60:
                return f"约 {lo}–{hi} 分钟"
            return f"约 {fmt(lo)} 到 {fmt(hi)}"
        if lo or hi:
            return f"约 {fmt(lo or hi)}"
        return None

    def route(self) -> str:
        flights = [i for i in self.trip.all_items() if i.type == ItemType.FLIGHT]
        if not flights:
            return " → ".join(dict.fromkeys(c for d in self.trip.days if (c := self.city_of(d))))
        text = f"{self.city_name(flights[0].from_place_id)} ✈ {self.city_name(flights[0].to_place_id)}"
        for prev, nxt in zip(flights, flights[1:]):
            arrived = self.local(prev.end).date()
            seg = next((s for s in self.trip.segments
                        if s.start_date == arrived or s.start_date <= arrived < s.end_date), None)
            if seg:
                text += f" → {seg.name}"
            text += f" → {self.city_name(nxt.from_place_id)} ✈ {self.city_name(nxt.to_place_id)}"
        return text

    def todo_for(self, record_id: str):
        return next((t for t in self.trip.todos if record_id in t.item_ids), None)

    # ---------- 章节 ----------

    def cover(self) -> list[str]:
        meta, days = self.trip.meta, len(self.trip.days)
        names = "、".join(m.name for m in self.trip.members)
        first_flight = next((i for i in self.trip.all_items() if i.type == ItemType.FLIGHT), None)
        origin = self.city_name(first_flight.from_place_id) if first_flight else meta.origin_city
        return [
            f"人数：{len(self.trip.members)} 人（{names}）",
            f"时间：{meta.start_date.year} 年 {meta.start_date.month} 月 {meta.start_date.day} 日出发，"
            f"{meta.end_date.month} 月 {meta.end_date.day} 日返回，共 {days} 天 {days - 1} 晚",
            f"出发城市：{origin}",
            f"整体路线：{self.route()}",
            f"版本：V{self.trip.version} · 生成于 {self.generated_on:%Y-%m-%d}",
        ]

    def section_changes(self) -> Section | None:
        if not self.changelog:
            return None
        return Section("本版变更", [Bullets(self.changelog)])

    def section_assumptions(self) -> Section | None:
        confirm = [t for t in self.trip.todos if t.kind == "confirm" and t.status == "open"]
        # 要动手确认的放前面：清单长了网页上会折叠，别把它们折进去
        items = [f"待确认：{t.title}" + (f"（{self.md(t.due)} 前）" if t.due else "") for t in confirm]
        items += [f"假设：{a}" for a in self.trip.assumptions]
        if not items:
            return None
        return Section("我做了哪些假设 / 待确认事项", [
            Paragraph("下面这些是资料里没写清楚、先按默认处理的地方，有不对的请直接告诉我。", muted=True),
            Bullets(items),
        ])

    def section_flights(self) -> Section:
        rows = []
        for f in (i for i in self.trip.all_items() if i.type == ItemType.FLIGHT):
            dep_tz, arr_tz = self.tz_of(f.from_place_id), self.tz_of(f.to_place_id)
            dep = f.start.astimezone(dep_tz)
            rows.append(Row([
                f"{self.md(dep.date())} {self.wd(dep.date())}", f.flight.number if f.flight else "",
                f"{self.city_name(f.from_place_id)} → {self.city_name(f.to_place_id)}",
                self.hm(f.start, dep_tz), self.hm(f.end, arr_tz), STATUS[f.booking_status] or "—",
            ]))
        return Section("机票", [
            Table(["日期", "航班", "航线", "起飞（当地时间）", "到达（当地时间）", "状态"], rows,
                  [35, 30, 70, 50, 50, 32]),
        ])

    def section_visa(self) -> Section:
        rows = [Row([m.name, m.passport_country or "", m.visa or ""]) for m in self.trip.members]
        todos = [f"{t.title}" + (f"（{self.md(t.due)} 前）" if t.due else "")
                 for t in self.trip.todos if t.kind == "visa"]
        authority = {"NZ": "新西兰移民局", "AU": "澳洲内政部"}.get(self.trip.meta.destination, "目的地官方")
        blocks = [Table(["成员", "护照", "当前签证"], rows, [60, 60, 147])]
        if todos:
            blocks.append(Bullets(todos))
        blocks.append(Paragraph(f"签证和入境政策会变，出发前以{authority}官网为准。", muted=True))
        return Section("签证与入境", blocks)

    def section_money(self) -> Section:
        meta = self.trip.meta
        items = [f"当地货币：{meta.local_currency}；AA 结算统一折算成 {meta.settle_currency}"]
        items += [t.title + (f"（{self.md(t.due)} 前）" if t.due else "")
                  for t in self.trip.todos if t.kind == "payment"]
        return Section("钱和支付", [Bullets(items)])

    def section_cars(self) -> Section | None:
        if not self.trip.vehicles:
            return None
        english = ValidatorConfig().english_licence_countries
        drivers = []
        for mid in dict.fromkeys(d for v in self.trip.vehicles for d in v.drivers):
            m = self.trip.member(mid)
            if not m:
                continue
            notes = []
            if m.age is not None and m.age < 25:
                notes.append("不满 25 岁，可能收年轻司机附加费")
            if m.licence_country and m.licence_country not in english:
                notes.append("需要认可的英文翻译件")
            licence = " ".join(x for x in (m.licence_country, (m.licence_type or "").title()) if x)
            drivers.append(Row([m.name, str(m.age or "未填"), licence, "；".join(notes)]))
        seg_names = {s.vehicle_id: s.name for s in self.trip.segments if s.vehicle_id}
        cars = []
        for v in self.trip.vehicles:
            cars.append(Row([
                seg_names.get(v.id, v.id), v.model or "",
                f"{self.place_name(v.pickup_place_id)}\n{self.md(self.local(v.pickup_time).date())} {self.hm(v.pickup_time)}",
                f"{self.place_name(v.dropoff_place_id)}\n{self.md(self.local(v.dropoff_time).date())} {self.hm(v.dropoff_time)}",
                "、".join(self.names.get(d, d) for d in v.drivers), STATUS[v.booking_status] or "—",
            ]))
        blocks: list = [
            Table(["司机", "年龄", "驾照", "备注"], drivers, [40, 25, 50, 152]),
            Table(["用车", "车型", "取车", "还车", "司机", "状态"], cars, [30, 70, 55, 55, 35, 22]),
        ]
        contract = [Row([
            seg_names.get(v.id, v.id), v.insurance or "待确认", money(v.excess) if v.excess else "待确认",
            money(v.deposit) if v.deposit else "待确认", money(v.one_way_fee) if v.one_way_fee else "待确认",
            f"取 {v.pickup_desk_hours or '待确认'}\n还 {v.dropoff_desk_hours or '待确认'}",
            v.after_hours_return or "待确认", v.road_restrictions or "待确认",
        ]) for v in self.trip.vehicles]
        blocks.append(Table(["用车", "保险", "自付额", "押金", "异地还车费", "柜台营业时间", "营业时间外还车", "道路限制"],
                            contract, [26, 40, 25, 25, 25, 34, 42, 50]))
        notes = [f"{seg_names.get(v.id, v.id)}：{n}" for v in self.trip.vehicles for n in v.notes]
        if notes:
            blocks.append(Bullets(notes))
        return Section("租车与驾照", blocks)

    def section_stays(self) -> Section | None:
        if not self.trip.accommodations:
            return None
        rows, nights = [], 0
        for a in sorted(self.trip.accommodations, key=lambda a: a.check_in):
            n = (a.check_out - a.check_in).days
            nights += n
            rows.append(Row([
                f"{self.md(a.check_in)} {self.wd(a.check_in)}", f"{self.md(a.check_out)} {self.wd(a.check_out)}",
                a.city, a.name, a.address or "待确认", f"{n} 晚", STATUS[a.booking_status] or "—",
            ], fill=self.colors.get(a.city)))
        rows.append(Row(["合计", "", "", f"{len(rows)} 处住宿", "", f"{nights} 晚", ""], bold=True))
        blocks: list = [Table(["入住", "退房", "城市", "住宿", "地址", "晚数", "状态"], rows,
                              [28, 28, 25, 60, 84, 18, 24])]
        # 案例 V4 的做法：入住退房时间一张表，房型和设施一张表，不知道的写"待确认"
        accs = sorted(self.trip.accommodations, key=lambda a: a.check_in)

        def entry(a) -> str:
            if a.self_checkin:
                return a.self_checkin
            hint = next((n for n in a.notes if any(k in n for k in ("自助入住", "钥匙盒", "密码箱", "见房东", "前台"))), None)
            return hint or "待确认"

        def parking(a) -> str:
            if a.parking is not None:
                return "有" if a.parking else "没有"
            return "见房型说明" if "停车" in a.rooms else "待确认"

        blocks.append(Table(["住宿", "入住", "最早入住", "退房", "最晚退房", "入住方式"], [Row([
            a.name, f"{self.md(a.check_in)} {self.wd(a.check_in)}",
            f"{a.earliest_checkin:%H:%M} 后" if a.earliest_checkin else "待确认",
            f"{self.md(a.check_out)} {self.wd(a.check_out)}",
            f"{a.latest_checkout:%H:%M} 前" if a.latest_checkout else "待确认", entry(a),
        ], fill=self.colors.get(a.city)) for a in accs], [62, 28, 28, 28, 28, 93]))
        blocks.append(Table(["住宿", "房型和床", "停车", "房东", "备注"], [Row([
            a.name, a.beds or a.rooms or "待确认", parking(a), a.host or "—", "\n".join(a.notes) or "—",
        ], fill=self.colors.get(a.city)) for a in accs], [62, 70, 25, 30, 80]))
        if req := self.trip.meta.prefs.room_requirements:
            blocks.append(Paragraph(f"分房要求：{req}。房型写着「待确认」的，订好后向房东确认床型够不够分。", muted=True))
        return Section("住宿", blocks)

    def section_activities(self) -> Section | None:
        """全部游玩项目一览（案例 V4 第 6 节）：一张表看完每个项目几点签到、玩多久、去哪里集合、订了没有。"""
        rows = []
        for item in self.trip.all_items():
            if item.type != ItemType.ACTIVITY:
                continue
            place = self.places.get(item.place_id or "")
            minutes = round((item.end - item.start).total_seconds() / 60)
            hours, rest = divmod(minutes, 60)
            length = f"{hours} 小时 {rest} 分" if hours and rest else (f"{hours} 小时" if hours else f"{minutes} 分钟")
            arrive = (f"{self.hm(item.start - timedelta(minutes=item.checkin_before_min))} 签到"
                      if item.checkin_before_min else "—")
            status = STATUS[item.booking_status] or ("备选" if item.optional else "无需预约")
            notes = [status]
            if item.optional:
                notes.append(item.optional_reason or "时间不够可以跳过")
            if place:
                notes += [r.text for r in place.restrictions][:2]
            notes += item.notes[:2]
            day = self.day_of(item)
            rows.append(Row([
                f"{self.md(day.date)} {self.wd(day.date)}", item.title, arrive, f"{self.time_range(item)}（{length}）",
                (place.address or place.name_en) if place else "—", "\n".join(notes),
            ], fill=self.colors.get(self.city_of(day)), bold=item.booking_status == BookingStatus.BOOKED))
        if not rows:
            return None
        return Section("全部游玩项目", [
            Paragraph("状态：已订 = 已付款或有凭证；待订 = 还要预约；无需预约 = 到了直接去；备选 = 时间不够、天气不好或太累可以跳过。",
                      muted=True),
            Table(["日期", "项目", "签到", "游玩时段（时长）", "地址或集合点", "状态和注意事项"], rows, [24, 58, 22, 44, 62, 57]),
        ], new_page=True)

    def section_cards(self) -> Section:
        sessions: dict[str, list[Item]] = defaultdict(list)
        for item in self.trip.all_items():
            place = self.places.get(item.place_id or "")
            if item.type == ItemType.ACTIVITY and place and place.kind in VISIT_KINDS:
                sessions[place.id].append(item)
        by_region: dict[str, list[str]] = defaultdict(list)
        for pid in sessions:
            by_region[self.places[pid].region].append(pid)

        blocks: list = [Paragraph("以下时间均为当地时间。价格和营业时间来自官网，出发前请再确认。", muted=True)]
        for region, pids in by_region.items():
            blocks.append(Heading(region))
            for n, pid in enumerate(pids, 1):
                blocks.append(self.card(n, self.places[pid], sessions[pid]))
        return Section("项目详情", blocks, new_page=True)

    def card(self, n: int, place: Place, items: list[Item]) -> Card:
        statuses = {i.booking_status for i in items}
        if statuses == {BookingStatus.BOOKED}:
            status = "已预约"
        elif BookingStatus.TO_BOOK in statuses:
            status = "需预约"
        else:
            status = "需预约" if place.booking.required else "无需预约"
        first_day = self.local(items[0].start).date()
        plan = []
        for i in items:
            d = self.local(i.start).date()
            who = "" if self.who(i) == "全员" else f"（{self.who(i)}）"
            plan.append(f"{self.md(d)} {self.wd(d)} {self.time_range(i)}{who} {STATUS[i.booking_status]}".strip())
        facts = [("本次安排", "\n".join(plan))]
        if place.address:
            facts.append(("地址", place.address))
        facts.append(("开放时间", self.hours_text(place, first_day)))
        if place.prices:
            facts.append(("参考价格", "；".join(f"{p.label} {money(p.money)}" for p in place.prices[:3])))
        if duration := self.duration_text(place):
            facts.append(("体验时长", duration))
        checkin = max((i.checkin_before_min for i in items), default=0) or (place.checkin_before_min or 0)
        if checkin:
            facts.append(("签到", f"开始前 {checkin} 分钟"))
        cautions = [r.text for r in place.restrictions] + place.notes
        cautions += [note for i in items for note in i.notes if note not in cautions]
        cautions = cautions[:6]
        if cautions:
            facts.append(("注意事项", "\n".join(cautions)))
        facts.append(("资料核实", f"{place.verified_at:%Y-%m-%d}" if place.verified_at else "未核实，出发前请确认"))

        links = []
        if place.booking.url:
            links.append(Link("预约", place.booking.url))
        if place.official_url and place.official_url != place.booking.url:
            links.append(Link("官网", place.official_url))
        links += [Link(f"攻略 {k}", url) for k, url in enumerate(place.guide_links, 1)]
        links += [Link(f"小红书：{title[:18]}", url) for title, url in self.guide_links.get(place.id, [])
                  if url not in place.guide_links]
        return Card(title=f"{n}. {place.name_en}｜{place.name_zh}（{status}）", facts=facts, links=links,
                    intro=place.intro, fill=self.colors.get(self.city_of(self.day_of(items[0]))))

    def day_of(self, item: Item) -> Day:
        d = self.local(item.start).date()
        return next(day for day in self.trip.days if day.date == d)

    def section_overview(self) -> Section:
        rows = []
        for n, day in enumerate(self.trip.days, 1):
            acts = []
            for i in day.items:
                if i.type == ItemType.ACTIVITY or (i.type == ItemType.FLIGHT):
                    status = STATUS[i.booking_status] or ("备选" if i.optional else "")
                    acts.append(f"{self.time_range(i)} {i.title}" + (f"（{status}）" if status else ""))
            hours = self.drive_hours(day)
            rows.append(Row([
                f"D{n} {self.md(day.date)} {self.wd(day.date)}", day.route, "\n".join(acts),
                f"约 {hours:.1f} 小时" if hours else "—", day.night_city or "—",
            ], fill=self.colors.get(self.city_of(day))))
        return Section(f"{len(self.trip.days)} 天行程总览", [
            Table(["日期", "当天路线", "主要景点及活动", "预计自驾", "住宿"], rows, [28, 62, 125, 25, 27]),
            Paragraph("预计自驾是各段开车时间之和，不含停车、休息和游玩。", muted=True),
        ], new_page=True)

    def section_schedule(self) -> Section:
        blocks: list = []
        for n, day in enumerate(self.trip.days, 1):
            color = self.colors.get(self.city_of(day))
            blocks.append(Heading(f"D{n} {self.md(day.date)} {self.wd(day.date)}｜{day.route}", fill=color))
            if bring := self.bring.get(day.date):
                blocks.append(Paragraph(bring))
            rows = []
            for i in day.items:
                where = (f"{self.place_name(i.from_place_id)} → {self.place_name(i.to_place_id)}"
                         if i.is_movement else self.place_name(i.place_id))
                notes = []
                if i.checkin_before_min and i.type != ItemType.FLIGHT:
                    notes.append(f"提前 {i.checkin_before_min} 分钟签到")
                if i.type == ItemType.FLIGHT:
                    notes.append(f"提前 {i.checkin_before_min} 分钟到机场")
                if i.driver_id:
                    notes.append(f"{self.names.get(i.driver_id, i.driver_id)} 开车")
                notes += i.notes
                if i.backup:
                    notes.append(f"备用：{i.backup}")
                if i.optional:
                    notes.append(f"备选：{i.optional_reason or '时间不够可以跳过'}")
                status = STATUS[i.booking_status] or ("备选" if i.optional else "")
                rows.append(Row([self.time_range(i), i.title, where, self.who(i), status,
                                 "\n".join(notes)], bold=i.booking_status == BookingStatus.BOOKED))
            blocks.append(Table(["时间", "安排", "地点", "参加人", "状态", "备注"], rows, [32, 62, 62, 28, 15, 68]))
        return Section("每天具体时间安排", blocks, new_page=True)

    def section_to_book(self) -> Section | None:
        rows = []
        for i in self.trip.all_items():
            if i.booking_status == BookingStatus.TO_BOOK:
                d = self.local(i.start).date()
                todo = self.todo_for(i.id)
                place = self.places.get(i.place_id or "")
                notes = list(todo.notes) if todo else []
                if place and place.booking.notes and place.booking.notes not in notes:
                    notes.append(place.booking.notes)
                rows.append((todo.due if todo and todo.due else date.max, Row([
                    f"{self.md(d)} {self.wd(d)}", i.title, self.time_range(i),
                    self.md(todo.due) if todo and todo.due else "未定", "\n".join(notes)])))
        for v in self.trip.vehicles:
            if v.booking_status == BookingStatus.TO_BOOK:
                todo = self.todo_for(v.id)
                d = self.local(v.pickup_time).date()
                rows.append((todo.due if todo and todo.due else date.max, Row([
                    f"{self.md(d)} {self.wd(d)}", todo.title if todo else f"租车 {v.id}", "—",
                    self.md(todo.due) if todo and todo.due else "未定", "\n".join(todo.notes if todo else [])])))
        if not rows:
            return None
        rows.sort(key=lambda r: r[0])
        return Section("还没订的项目", [
            Table(["日期", "项目", "目标时间", "最晚预订", "备注"], [r for _, r in rows], [28, 85, 35, 25, 94]),
        ], new_page=True)

    def section_todos(self) -> Section | None:
        if not self.trip.todos:
            return None
        todos = sorted(self.trip.todos, key=lambda t: (t.status == "done", t.due or date.max))
        rows = [Row([
            t.title, TODO_KINDS.get(t.kind, t.kind),
            "、".join(self.names.get(o, o) for o in t.owner_ids) or "全员",
            self.md(t.due) if t.due else "—", "☑ 已完成" if t.status == "done" else "☐ 待完成", "\n".join(t.notes),
        ]) for t in todos]
        return Section("待办事项总表", [
            Table(["事项", "类型", "负责人", "截止", "状态", "备注"], rows, [95, 22, 30, 20, 25, 75]),
        ])

    def section_departure(self) -> Section:
        """出发前清单（参照 V3、V4）：证件、钱、手机、保险、衣物、药品，打勾用。"""
        rows = []
        for group, items in departure_checklist(self.trip):
            for n, item in enumerate(items):
                rows.append(Row([group if n == 0 else "", item, "☐"]))
        return Section("出发前清单", [
            Paragraph("每天要带的东西写在「每天具体时间安排」里各天的开头。", muted=True),
            Table(["类别", "要准备的", "好了"], rows, [30, 210, 20]),
        ])

    def section_booked(self) -> Section | None:
        rows = []
        for i in self.trip.all_items():
            if i.booking_status == BookingStatus.BOOKED:
                d = self.local(i.start).date()
                rows.append((d, Row([f"{self.md(d)} {self.wd(d)}", i.title, self.time_range(i),
                                     self.who(i), "已订"])))
        for a in self.trip.accommodations:
            if a.booking_status == BookingStatus.BOOKED:
                rows.append((a.check_in, Row([f"{self.md(a.check_in)}–{self.md(a.check_out)}", f"住宿 {a.name}",
                                              f"{(a.check_out - a.check_in).days} 晚", "全员", "已订"])))
        for v in self.trip.vehicles:
            if v.booking_status == BookingStatus.BOOKED:
                d = self.local(v.pickup_time).date()
                rows.append((d, Row([self.md(d), f"租车 {v.model or v.id}", self.hm(v.pickup_time), "—", "已订"])))
        if not rows:
            return None
        rows.sort(key=lambda r: r[0])
        return Section("已完成的预订", [
            Table(["日期", "项目", "时间", "参加人", "状态"], [r for _, r in rows], [32, 110, 55, 45, 25]),
        ])

    def section_vouchers(self) -> Section | None:
        """凭证夹清单：和行程包里"凭证"文件夹的文件一一对应。"""
        if not self.vouchers:
            return None
        rows = []
        for v in self.vouchers:
            d = date.fromisoformat(v["date"]) if v.get("date") else None
            rows.append(Row([
                f"{self.md(d)} {self.wd(d)}" if d else "—", v.get("title") or "—", v.get("kind_label", ""),
                "\n".join(v.get("items") or []) or "—", "、".join(v.get("confirmation_nos") or []) or "—",
                v.get("uploaded_by_name") or "—", v.get("filename") or "（对话里记录，没有原文件）",
            ]))
        return Section("预订凭证", [
            Paragraph("大家上传的预订凭证都在这里。下载的行程包里有一个「凭证」文件夹，原文件按日期命名，出行时可以直接打开出示。"),
            Table(["日期", "凭证", "类型", "关联安排", "确认号", "上传人", "原文件"], rows, [24, 62, 15, 58, 30, 18, 60]),
        ])

    def section_money_settle(self) -> Section | None:
        """费用与结算（14.10 节）：账目、每人付了多少、应承担多少、转账方案。"""
        m = self.money
        rows = [e for e in (m or {}).get("expenses", []) if e["status"] != "draft"]
        if not rows:
            return None
        cur = m["settle_currency"]
        method = {"same": "", "charged": "实际扣款", "cash": "现金按汇率", "estimate": "暂估"}
        table = [Row([
            self.md(date.fromisoformat(e["date"])), e["title"] + ("（有疑问）" if e["status"] == "disputed" else ""),
            e["category_label"], e["payer_name"], e["amount_text"],
            e["settle_text"] + (f"\n{method[e['fx']['method']]}" if method[e["fx"]["method"]] else ""), e["split_text"],
        ]) for e in rows]
        blocks: list = [
            Table(["日期", "账目", "类别", "付款人", "原币", f"折算（{cur}）", "分摊"], table, [18, 70, 22, 22, 30, 32, 73]),
            Table(["成员", "付了", "应承担", "净额"],
                  [Row([b["name"], b["paid_text"], b["owed_text"], b["net_text"]]) for b in m["balances"]], [40, 50, 50, 50]),
            Paragraph("净额是正数表示别人欠他，负数表示他欠别人。", muted=True),
            Bullets([p["text"] for p in m["plan"]] or ["现在大家已经两清了"]),
        ]
        if m.get("checks"):
            blocks.append(Bullets([c["message"] for c in m["checks"] if c["severity"] != "info"] or ["没有需要处理的账目问题"]))
        return Section("费用与结算", blocks, new_page=True)

    def build(self) -> Report:
        sections = [
            self.section_changes(), self.section_assumptions(), self.section_flights(), self.section_visa(),
            self.section_money(), self.section_cars(), self.section_stays(), self.section_activities(),
            self.section_cards(),
            self.section_overview(), self.section_schedule(), self.section_to_book(), self.section_todos(),
            self.section_departure(),
            self.section_booked(), self.section_vouchers(), self.section_money_settle(),
        ]
        sections = [s for s in sections if s is not None]
        for n, s in enumerate(sections, 1):
            s.title = f"{n}. {s.title}"
        return Report(title=f"{self.trip.meta.title}｜计划 V{self.trip.version}", cover_lines=self.cover(),
                      sections=sections, legend=list(self.colors.items()))


def build_report(trip: Trip, places: dict[str, Place], *, changelog: list[str] = (),
                 generated_on: date | None = None, vouchers: list[dict] = (), money: dict | None = None,
                 guide_links: dict[str, list[tuple[str, str]]] | None = None) -> Report:
    return ReportBuilder(trip, places, changelog=changelog, generated_on=generated_on, vouchers=vouchers,
                         money=money, guide_links=guide_links).build()
