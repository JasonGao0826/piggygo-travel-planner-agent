"""公众假日（R24）。用 holidays 库离线计算，不需要联网。

新西兰各地区有自己的周年纪念日（例如 West Coast Anniversary Day），只在当天行程所在的岛上才提示。
"""

from dataclasses import dataclass
from datetime import date

import holidays as hol

# 新西兰各地区假日属于哪个岛（地点知识库里 region 写的是"南岛""北岛"）
NZ_ISLAND = {
    "AUK": "北岛", "BOP": "北岛", "GIS": "北岛", "HKB": "北岛", "MWT": "北岛", "NTL": "北岛", "TKI": "北岛",
    "WGN": "北岛", "WKO": "北岛",
    "CAN": "南岛", "MBH": "南岛", "NSN": "南岛", "OTA": "南岛", "STL": "南岛", "TAS": "南岛", "WTC": "南岛",
    "South Canterbury": "南岛",
}
# 这几天大部分商店和超市不营业（新西兰的限制营业日）
CLOSED_DAYS = ("Christmas Day", "Good Friday", "Easter Sunday", "Anzac Day")


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str
    region: str | None = None   # None 表示全国
    region_name: str | None = None

    @property
    def shops_closed(self) -> bool:
        return self.region is None and any(self.name.startswith(n) for n in CLOSED_DAYS)


def holidays_between(country: str, start: date, end: date) -> list[Holiday]:
    """[start, end] 期间的全国假日和各地区假日。"""
    years = range(start.year, end.year + 1)
    try:
        national = hol.country_holidays(country, years=years)
    except NotImplementedError:
        return []
    cls = type(national)
    names: dict[str, str] = {}
    for name, code in getattr(cls, "subdivisions_aliases", {}).items():
        if len(name) > 3:
            names.setdefault(code, name)  # 每个地区取第一个全称，例如 WTC → West Coast
    out = [Holiday(d, n) for d, n in national.items() if start <= d <= end]
    for sub in getattr(cls, "subdivisions", ()):
        regional = hol.country_holidays(country, subdiv=sub, years=years)
        for d, n in regional.items():
            if start <= d <= end and d not in national:
                out.append(Holiday(d, n, sub, names.get(sub, sub)))
    return sorted(out, key=lambda h: (h.date, h.region or ""))
