"""改动的应用与差异计算（设计规格 8.4 节）。

改动用 id 定位（见 schema/memory.py 的 PatchOp）。应用之后整份行程重新走一遍数据结构校验，
事件按开始日期自动归到对应的那一天。
"""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .schema.memory import AddOp, Change, FieldChange, PatchOp, RemoveOp, UpdateDayOp, UpdateMetaOp, UpdateOp
from .schema.trip import Trip


class PatchError(ValueError):
    """改动本身写错了（找不到 id、id 重复、改了不允许改的字段……）。"""


_IMMUTABLE = {"id"}
_DAY_IMMUTABLE = {"date", "items"}


def _find(rows: list[dict], row_id: str, collection: str) -> dict:
    for row in rows:
        if row.get("id") == row_id:
            return row
    raise PatchError(f"{collection} 里找不到 id 为 {row_id} 的记录")


def apply_ops(trip: Trip, ops: list[PatchOp]) -> Trip:
    """在行程副本上应用改动，返回新行程。改动不合法时抛出 PatchError 或 pydantic 的 ValidationError。"""
    data = trip.model_dump(mode="json")
    items = [item for day in data["days"] for item in day["items"]]
    rows = {"items": items, "accommodations": data["accommodations"],
            "vehicles": data["vehicles"], "todos": data["todos"]}

    for op in ops:
        if isinstance(op, AddOp):
            new_id = op.value.get("id")
            if not new_id:
                raise PatchError(f"新增到 {op.collection} 的记录缺少 id")
            if any(r.get("id") == new_id for r in rows[op.collection]):
                raise PatchError(f"{op.collection} 里已经有 id 为 {new_id} 的记录")
            rows[op.collection].append(dict(op.value))
        elif isinstance(op, UpdateOp):
            if _IMMUTABLE & op.set.keys():
                raise PatchError("不能修改 id")
            _find(rows[op.collection], op.id, op.collection).update(op.set)
        elif isinstance(op, RemoveOp):
            target = _find(rows[op.collection], op.id, op.collection)
            rows[op.collection].remove(target)
        elif isinstance(op, UpdateDayOp):
            if _DAY_IMMUTABLE & op.set.keys():
                raise PatchError("update_day 不能修改 date 和 items；事件请用 items 的改动")
            day = next((d for d in data["days"] if d["date"] == op.date.isoformat()), None)
            if day is None:
                raise PatchError(f"行程里没有 {op.date} 这一天")
            day.update(op.set)
        elif isinstance(op, UpdateMetaOp):
            data["meta"].update(op.set)

    # 事件按开始时刻在目的地的日期重新归到各天
    tz = ZoneInfo(data["meta"]["local_timezone"])
    by_date = {d["date"]: d for d in data["days"]}
    for d in data["days"]:
        d["items"] = []
    for item in items:
        start = item["start"]
        start_dt = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        day_key = start_dt.astimezone(tz).date().isoformat()
        if day_key not in by_date:
            raise PatchError(f"事件 {item.get('id')} 的开始日期 {day_key} 不在行程日期内")
        by_date[day_key]["items"].append(item)

    return Trip.model_validate(data).normalized()


# ---------------- 差异 ----------------

_FIELD_LABELS = {
    "start": "开始时间", "end": "结束时间", "title": "名称", "place_id": "地点", "from_place_id": "出发地",
    "to_place_id": "目的地", "participants": "参加人", "driver_id": "司机", "vehicle_id": "车辆",
    "booking_status": "预订状态", "locked": "锁定", "lock_reason": "锁定原因", "checkin_before_min": "签到提前量",
    "notes": "注意事项", "cost": "费用", "backup": "备用安排", "check_in": "入住日期", "check_out": "退房日期",
    "earliest_checkin": "最早入住", "latest_checkout": "最晚退房", "address": "地址", "pickup_time": "取车时间",
    "dropoff_time": "还车时间", "pickup_place_id": "取车地点", "dropoff_place_id": "还车地点", "due": "截止日期",
    "status": "状态", "owner_ids": "负责人", "route": "路线", "night_city": "住宿城市",
    "drivers": "司机", "evidence_ids": "关联凭证", "decision_ids": "关联决策", "accommodation_id": "住宿",
    "optional": "备选", "optional_reason": "备选原因", "weather_dependent": "受天气影响", "source": "来源",
    "beds": "房型和床", "parking": "停车位", "self_checkin": "入住方式", "rooms": "房间", "host": "房东",
    "company": "租车公司", "model": "车型", "insurance": "保险", "excess": "自付额", "deposit": "押金",
    "one_way_fee": "异地还车费", "pickup_desk_hours": "取车柜台营业时间", "kind": "类型", "item_ids": "关联安排",
    "passport_country": "护照", "visa": "签证", "can_drive": "能开车", "age": "年龄", "licence_country": "驾照国家",
    "licence_type": "驾照类型", "weight_kg": "体重",
}
# 只有本人能看到的字段：变更说明里只说改了，不写数值（设计规格 18 节）
_PRIVATE_FIELDS = {"weight_kg"}
# 值是成员 id 的字段：显示成员名字；值为空表示全员的字段
_MEMBER_FIELDS = {"participants", "driver_id", "owner_ids", "drivers"}
_EVERYONE_WHEN_EMPTY = {"participants", "owner_ids"}
# 值是一串 id 的字段：只说多了几个、少了几个
_COUNTED_FIELDS = {"evidence_ids": "份凭证", "decision_ids": "条决策", "item_ids": "个安排"}
_COUNTRIES = {"CN": "中国", "HK": "中国香港", "MO": "中国澳门", "TW": "中国台湾", "AU": "澳大利亚", "NZ": "新西兰",
              "MY": "马来西亚", "SG": "新加坡", "OTHER": "其他"}
_VALUE_LABELS = {
    "booking_status": {"not_required": "无需预订", "to_book": "待订", "booked": "已订", "cancelled": "已取消"},
    "lock_reason": {"evidence": "有预订凭证", "user": "用户要求固定"},
    "source": {"user": "用户说的", "evidence": "来自凭证", "verified_web": "官网核实", "knowledge_base": "景点资料",
               "guide": "攻略参考", "agent_inferred": "猪猪推断"},
    "status": {"open": "未完成", "done": "已完成"},
    "passport_country": _COUNTRIES,
    "licence_country": _COUNTRIES,
    "licence_type": {"full": "正式驾照", "p": "P 牌", "provisional": "P 牌", "learner": "L 牌", "other": "其他"},
}
LONG_VALUE = 60   # 新旧值加起来超过这么多字，就不逐字列出，只说"已更新"

_COLLECTION_LABELS = {"members": "成员", "items": "事件", "accommodations": "住宿", "vehicles": "租车", "todos": "待办",
                      "days": "当天安排", "meta": "行程信息"}


def _rows(trip: Trip) -> dict[str, dict[str, dict]]:
    data = trip.model_dump(mode="json")
    return {
        "members": {m["id"]: m for m in data["members"]},   # 改动不能动成员；成员本人改资料时才会变
        "items": {i["id"]: i for d in data["days"] for i in d["items"]},
        "accommodations": {a["id"]: a for a in data["accommodations"]},
        "vehicles": {v["id"]: v for v in data["vehicles"]},
        "todos": {t["id"]: t for t in data["todos"]},
        "days": {d["date"]: {k: v for k, v in d.items() if k != "items"} for d in data["days"]},
        "meta": {"meta": data["meta"]},
    }


def diff(before: Trip, after: Trip) -> list[Change]:
    changes: list[Change] = []
    old_all, new_all = _rows(before), _rows(after)
    for collection in old_all:
        old, new = old_all[collection], new_all[collection]
        for rid in old.keys() - new.keys():
            changes.append(Change(collection=collection, id=rid, kind="removed"))
        for rid in new.keys() - old.keys():
            changes.append(Change(collection=collection, id=rid, kind="added"))
        for rid in old.keys() & new.keys():
            o, n = old[rid], new[rid]
            fields =[FieldChange(field=k, old=o.get(k), new=n.get(k))
                      for k in sorted(o.keys() | n.keys()) if o.get(k) != n.get(k)]
            if fields:
                changes.append(Change(collection=collection, id=rid, kind="changed", fields=fields))
    order = {"added": 0, "changed": 1, "removed": 2}
    return sorted(changes, key=lambda c: (list(old_all).index(c.collection), order[c.kind], c.id))


def _fmt(value: Any, tz: ZoneInfo) -> str:
    if value is None:
        return "空"
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is not None:
                local = dt.astimezone(tz)
                return f"{local.month}/{local.day} {local:%H:%M}"
        except ValueError:
            pass
        try:
            d = date.fromisoformat(value)
            return f"{d.month}/{d.day}"
        except ValueError:
            return value
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "、".join(_fmt(v, tz) for v in value) or "空"
    return str(value)


_PLACE_FIELDS = {"place_id", "from_place_id", "to_place_id", "pickup_place_id", "dropoff_place_id"}


def describe(changes: list[Change], before: Trip, after: Trip, place_names: dict[str, str] | None = None) -> list[str]:
    """把差异写成给人看的变更说明。地点、成员显示名字，不显示内部 id；注意事项这类长内容只说改了什么。"""
    tz = ZoneInfo(after.meta.local_timezone)
    place_names = {**(place_names or {}), **{p.id: p.name_zh for t in (before, after) for p in t.custom_places}}
    member_names = {m.id: m.name for t in (before, after) for m in t.members}
    stays = {a.id: a.name for t in (before, after) for a in t.accommodations}

    def show(field: str, value) -> str:
        if field in _PLACE_FIELDS and isinstance(value, str):
            return place_names.get(value, value)
        if field in _MEMBER_FIELDS:
            ids = value if isinstance(value, list) else ([value] if value else [])
            if not ids:
                return "全员" if field in _EVERYONE_WHEN_EMPTY else "空"
            return "、".join(member_names.get(i, i) for i in ids)
        if field == "accommodation_id" and isinstance(value, str):
            return stays.get(value, value)
        if field in _VALUE_LABELS and isinstance(value, str):
            return _VALUE_LABELS[field].get(value, value)
        if isinstance(value, dict) and {"amount", "currency"} <= value.keys():
            return f"{value['currency']} {value['amount']}"
        return _fmt(value, tz)

    def field_text(f: FieldChange) -> str:
        label = _FIELD_LABELS.get(f.field, f.field)
        if f.field in _PRIVATE_FIELDS:
            return f"{label}：已更新（只有本人能看到）"
        if f.field in _COUNTED_FIELDS:
            old, new = set(f.old or []), set(f.new or [])
            unit = _COUNTED_FIELDS[f.field]
            parts = [f"新增 {len(new - old)} {unit}" if new - old else "", f"去掉 {len(old - new)} {unit}" if old - new else ""]
            return f"{label}：" + ("，".join(x for x in parts if x) or "调整")
        if isinstance(f.old, list) and isinstance(f.new, list) and f.field not in _MEMBER_FIELDS:
            added = [x for x in f.new if x not in f.old]
            removed = [x for x in f.old if x not in f.new]
            old_text, new_text = show(f.field, f.old), show(f.field, f.new)
            if len(old_text) + len(new_text) > LONG_VALUE:
                parts = [f"新增 {len(added)} 条" if added else "", f"删去 {len(removed)} 条" if removed else ""]
                return f"{label}：" + ("，".join(x for x in parts if x) or "调整了顺序")
            return f"{label} {old_text} → {new_text}"
        old_text, new_text = show(f.field, f.old), show(f.field, f.new)
        if len(old_text) + len(new_text) > LONG_VALUE:
            return f"{label}：已更新"
        return f"{label} {old_text} → {new_text}"

    names = {}
    for t in (after, before):  # 改动和删除用原来的名称，新增的用新名称
        names.update({i.id: i.title for i in t.all_items()})
        names.update({a.id: a.name for a in t.accommodations})
        names.update({v.id: v.model or v.id for v in t.vehicles})
        names.update({x.id: x.title for x in t.todos})
    lines = []
    for c in changes:
        label = _COLLECTION_LABELS.get(c.collection, c.collection)
        name = member_names.get(c.id, c.id) if c.collection == "members" else names.get(c.id, c.id)
        if c.kind == "added":
            lines.append(f"新增{label}：{name}")
        elif c.kind == "removed":
            lines.append(f"删除{label}：{name}")
        else:
            lines.append(f"修改{label}「{name}」：" + "；".join(field_text(f) for f in c.fields))
    return lines


def touched_ids(ops: list[PatchOp]) -> set[str]:
    """一个改动涉及哪些记录，用于判断两个并发改动是否冲突。"""
    ids = set()
    for op in ops:
        if isinstance(op, AddOp):
            ids.add(f"{op.collection}:{op.value.get('id')}")
        elif isinstance(op, (UpdateOp, RemoveOp)):
            ids.add(f"{op.collection}:{op.id}")
        elif isinstance(op, UpdateDayOp):
            ids.add(f"days:{op.date.isoformat()}")
        elif isinstance(op, UpdateMetaOp):
            ids.add("meta:meta")
    return ids


def changed_ids(changes: list[Change]) -> set[str]:
    return {f"{c.collection}:{c.id}" for c in changes}


__all__ = ["PatchError", "ValidationError", "apply_ops", "changed_ids", "describe", "diff", "touched_ids"]
