"""预订网站的搜索链接：把城市、日期、人数带进去，用户点开就是搜索结果页，自己挑、自己订。

agent 不替用户下单（设计规格 D11）。只用各网站公开的搜索地址，不抓取页面内容。
Trip.com 的酒店搜索要用它内部的城市编号，按城市名打不开结果，暂时不放。
"""

from datetime import date
from urllib.parse import quote, urlencode

COUNTRY_NAMES = {"NZ": "New Zealand", "AU": "Australia"}


def stay_links(city: str, country: str, check_in: date, check_out: date, adults: int, rooms: int = 1) -> dict[str, str]:
    place = f"{city}, {COUNTRY_NAMES.get(country, country)}"
    slug = quote(f"{city}--{COUNTRY_NAMES.get(country, country)}".replace(" ", "-"))
    return {
        "Airbnb": f"https://www.airbnb.com/s/{slug}/homes?"
                  + urlencode({"checkin": check_in.isoformat(), "checkout": check_out.isoformat(), "adults": adults}),
        "Booking.com": "https://www.booking.com/searchresults.html?"
                       + urlencode({"ss": place, "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
                                    "group_adults": adults, "no_rooms": rooms, "group_children": 0}),
    }


def flight_links(origin: str, destination: str, day: date, adults: int) -> dict[str, str]:
    q = f"Flights from {origin} to {destination} on {day.isoformat()} for {adults} adults one way"
    return {"Google Flights": "https://www.google.com/travel/flights?" + urlencode({"q": q})}
