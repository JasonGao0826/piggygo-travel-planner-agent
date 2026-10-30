"""连外部数据的工具（设计规格第 22 节）：机票、汇率、网页搜索、实时情况、预订链接。

外部返回的内容都用 untrusted() 包一层再交给模型：只当数据，不当指令。
这些工具都只读，不会改行程；要改行程还是走 propose_patch。没配置的服务对应的工具不出现。
"""

from datetime import date

from ..flights import flight_rows
from ..links import flight_links, stay_links
from ..live.report import live_report
from ..llm.base import Tool
from ..mcp_gateway import MCPGateway, untrusted
from ..service import TripService

MAX_TEXT = 6000


def search_flights_args(t, args: dict) -> dict:
    day = date.fromisoformat(args["date"])
    params = {"flyFrom": args["from"], "flyTo": args["to"], "departureDate": day.strftime("%d/%m/%Y"),
              "adults": args.get("adults") or len(t.members), "currency": args.get("currency") or t.meta.settle_currency,
              "sort": args.get("sort", "price"), "locale": "en"}
    if args.get("max_stops") is not None:
        params["max_sector_stopovers"] = args["max_stops"]
    if args.get("depart_after") is not None:
        params["dtime_from"] = args["depart_after"]
    if args.get("depart_before") is not None:
        params["dtime_to"] = args["depart_before"]
    return params


def build_external_tools(service: TripService, trip_id: str, gateway: MCPGateway | None) -> list[Tool]:
    def trip():
        return service.current(trip_id)

    S = lambda props, required=(): {"type": "object", "properties": props, "required": list(required)}  # noqa: E731
    STR, INT = {"type": "string"}, {"type": "integer"}
    tools: list[Tool] = []

    # ---------- 机票（Kiwi.com） ----------
    if gateway and gateway.available("kiwi"):
        def search_flights(args):
            t = trip()
            r = gateway.call("kiwi", "search-flight", search_flights_args(t, args))
            return untrusted("Kiwi.com 机票搜索", r.fetched_at, {
                "条件": r.data.get("query"), "币种": r.data.get("currency"), "结果": flight_rows(r.data),
                "提示": "价格是全部乘客的总价，随时会变，以预订页面为准；时间是当地时间。只能给链接，不能代订。",
            })

        tools.append(Tool("search_flights", "查真实航班和价格（Kiwi.com）。返回航班号、时间、总价和预订链接。", S({
            "from": {**STR, "description": "出发地 IATA 代码或城市，如 MEL"},
            "to": {**STR, "description": "目的地 IATA 代码或城市，如 ZQN"},
            "date": {**STR, "description": "YYYY-MM-DD"},
            "adults": {**INT, "description": "默认行程全员人数"},
            "currency": {**STR, "description": "默认结算币种"},
            "max_stops": {**INT, "description": "每段最多经停次数，0 表示直飞"},
            "depart_after": {**INT, "description": "最早起飞小时（0-23，当地时间）"},
            "depart_before": {**INT, "description": "最晚起飞小时（0-23，当地时间）"},
            "sort": {"type": "string", "enum": ["price", "duration", "quality"]},
        }, ["from", "to", "date"]), search_flights))

    # ---------- 汇率（Frankfurter） ----------
    if gateway and gateway.available("frankfurter"):
        def convert_currency(args):
            params = {"amount": args["amount"], "from": args["from"].upper(), "to": args["to"].upper()}
            if args.get("date"):
                params["date"] = args["date"]
            r = gateway.call("frankfurter", "convert", params)
            return untrusted("Frankfurter 汇率（各国央行参考价）", r.fetched_at,
                             {"原金额": f"{args['amount']} {params['from']}", "换算结果": r.data,
                              "提示": "参考汇率，和银行卡实际扣款会有差异"})

        tools.append(Tool("convert_currency", "货币换算（央行参考汇率）。不填 date 用最新汇率。", S({
            "amount": {"type": "number"}, "from": {**STR, "description": "如 NZD"}, "to": {**STR, "description": "如 AUD"},
            "date": {**STR, "description": "YYYY-MM-DD，查过去某天的汇率"},
        }, ["amount", "from", "to"]), convert_currency))

    # ---------- 网页搜索（Exa） ----------
    if gateway and gateway.available("exa"):
        def web_search(args):
            r = gateway.call("exa", "web_search_exa", {"query": args["query"], "objective": args["objective"],
                                                       "numResults": min(int(args.get("num_results", 5)), 8)})
            return untrusted("Exa 网页搜索", r.fetched_at, str(r.data)[:MAX_TEXT])

        def read_page(args):
            urls = args["urls"][:3]
            r = gateway.call("exa", "web_fetch_exa", {"urls": urls, "maxCharacters": 4000})
            return untrusted("网页内容（Exa 抓取）", r.fetched_at, str(r.data)[:MAX_TEXT * 2])

        tools.append(Tool("web_search", "上网搜索，用来核实变化快的信息：营业时间、季节性关闭、步道封闭、价格。优先引用官网。", S({
            "query": {**STR, "description": "完整描述想找的页面，英文效果更好"},
            "objective": {**STR, "description": "要从结果里找到什么事实"},
            "num_results": INT,
        }, ["query", "objective"]), web_search))
        tools.append(Tool("read_page", "读取网页正文（最多 3 个网址），例如官网的营业时间、DOC 步道的警示。", S({
            "urls": {"type": "array", "items": STR},
        }, ["urls"]), read_page))

    # ---------- 实时情况 ----------
    def live_conditions(_args):
        r = live_report(service, trip_id)
        result = {"和行程有关的提醒": r["issues"] or "没有发现路况、天气预警、天气预报和公众假日方面的问题。"}
        if r["enabled"]:
            result.update({"每天天气（当天各地点里最差的）": r["days"] or "暂无", "说明": r["notes"],
                           "没查到的来源": r["errors"], "数据时间": r["fetched_at"]})
        return result

    tools.append(Tool("live_conditions", "实时情况：路上的封路和施工、天气预警、天气预报、公众假日，以及它们对行程的影响。", S({}),
                      live_conditions))

    # ---------- 预订链接 ----------
    def booking_links(args):
        t = trip()
        adults = args.get("adults") or len(t.members)
        if args["kind"] == "stay":
            links = stay_links(args["city"], t.meta.destination, date.fromisoformat(args["check_in"]),
                               date.fromisoformat(args["check_out"]), adults, args.get("rooms") or 1)
        else:
            links = flight_links(args["from"], args["to"], date.fromisoformat(args["date"]), adults)
        return {"链接": links, "提示": "打开就是搜索结果页，由用户自己挑选和预订。"}

    tools.append(Tool("booking_links", "生成预订网站的搜索链接（住宿：Airbnb、Booking.com；机票：Google Flights），带上城市、日期和人数。", S({
        "kind": {"type": "string", "enum": ["stay", "flight"]},
        "city": {**STR, "description": "住宿城市英文名，如 Queenstown"},
        "check_in": {**STR, "description": "YYYY-MM-DD"}, "check_out": {**STR, "description": "YYYY-MM-DD"},
        "rooms": INT, "from": STR, "to": STR, "date": {**STR, "description": "YYYY-MM-DD"}, "adults": INT,
    }, ["kind"]), booking_links))
    return tools
