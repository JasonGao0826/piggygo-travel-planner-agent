"""记账的对话工具（设计规格 14.3、14.11 节）：猪猪听懂"晚饭我付了 NZ$128，天哥没吃"，生成草稿；回答"我现在欠谁多少"。

金额计算、汇率折算、结算方案都由代码完成，模型只读结果。
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from ..llm.base import Tool
from ..mcp_gateway import MCPGateway, untrusted
from ..money import ExpenseIn, MoneyService, gateway_rates
from ..schema.money import Split
from ..service import TripService


def build_money_tools(service: TripService, trip_id: str, member_id: str, gateway: MCPGateway | None) -> list[Tool]:
    rates = gateway_rates(gateway) if gateway and gateway.available("frankfurter") else None
    money = MoneyService(service, rates=rates)

    def propose_expense(args):
        splits = None
        if args.get("split_method") in ("shares", "exact") and args.get("splits"):
            splits = [Split(member_id=s["member_id"], shares=Decimal(str(s["shares"])) if s.get("shares") is not None else None,
                            amount=Decimal(str(s["amount"])) if s.get("amount") is not None else None) for s in args["splits"]]
        elif args.get("members"):
            splits = [Split(member_id=m) for m in args["members"]]
        e = money.add(trip_id, ExpenseIn(
            title=args["title"], category=args.get("category", "other"), payer=args.get("payer") or member_id,
            amount=Decimal(str(args["amount"])), currency=args["currency"].upper(),
            date=date.fromisoformat(args["date"]) if args.get("date") else date.today(),
            split_method=args.get("split_method", "equal"), splits=splits, exclude=args.get("exclude") or [],
            charged_amount=Decimal(str(args["charged_amount"])) if args.get("charged_amount") is not None else None,
            charged_currency=args.get("charged_currency"), item_id=args.get("item_id"), note=args.get("note", ""),
            status="draft"), member_id)
        view = next(x for x in money.summary(trip_id)["expenses"] if x["id"] == e.id)
        return {"expense_id": e.id, "status": "草稿（在「账本」页签确认后才入账）", "金额": view["amount_text"],
                "折算": view["settle_text"] + ("（按汇率暂估，出账后请补填实际扣款）" if e.estimated else ""),
                "分摊": [f"{s['name']} {s['text']}" for s in view["shares"]],
                "检查": untrusted("账本检查（账目名是成员写的）", datetime.now(timezone.utc),
                                [c["message"] for c in money.checks(trip_id) if e.id in c["expense_ids"]])}

    def get_balances(_args):
        s = money.summary(trip_id)
        return untrusted("账本（账目名、备注是成员写的）", datetime.now(timezone.utc), {"结算币种": s["settle_currency"],
                "每人": [{"成员": b["name"], "付了": b["paid_text"], "应承担": b["owed_text"], "净额": b["net_text"]}
                       for b in s["balances"]],
                "结算方案": [p["text"] for p in s["plan"]] or "大家已经两清了",
                "提醒": [c["message"] for c in s["checks"]], "还款记录": [f"{t['from_name']} → {t['to_name']} {t['text']}（{'已确认' if t['status'] == 'confirmed' else '等收款人确认'}）"
                                                                        for t in s["transfers"]]})

    def list_expenses(_args):
        rows = money.summary(trip_id)["expenses"]
        data = [{"id": e["id"], "日期": e["date"], "账目": e["title"], "付款人": e["payer_name"], "金额": e["amount_text"],
                 "折算": e["settle_text"], "分摊": e["split_text"], "状态": {"draft": "草稿", "confirmed": "已入账",
                                                                         "disputed": "有疑问"}[e["status"]]}
                for e in rows]
        return untrusted("账本（账目名是成员写的）", datetime.now(timezone.utc), data) if data else "还没有账目。"

    S = lambda props, required=(): {"type": "object", "properties": props, "required": list(required)}  # noqa: E731
    STR = {"type": "string"}
    return [
        Tool("propose_expense", "记一笔账（草稿，大家在账本里确认后才入账）。付款人默认是正在说话的人；分摊人默认按规则（事件参加人或全员）。", S({
            "title": {**STR, "description": "例如 皇后镇晚餐"},
            "category": {"type": "string", "enum": ["accommodation", "flight", "car", "fuel", "parking", "activity",
                                                    "food", "grocery", "visa", "other"]},
            "amount": {"type": "number", "description": "原币金额"},
            "currency": {**STR, "description": "原币，例如 NZD"},
            "payer": {**STR, "description": "付款人成员 id，默认是正在说话的人"},
            "date": {**STR, "description": "付款日期 YYYY-MM-DD，默认今天"},
            "exclude": {"type": "array", "items": STR, "description": "不分摊的成员 id，例如没吃这顿的人"},
            "members": {"type": "array", "items": STR, "description": "只在这些成员之间均分（不填就用默认）"},
            "split_method": {"type": "string", "enum": ["equal", "shares", "exact"]},
            "splits": {"type": "array", "items": {"type": "object", "properties": {
                "member_id": STR, "shares": {"type": "number"}, "amount": {"type": "number"}}, "required": ["member_id"]}},
            "charged_amount": {"type": "number", "description": "银行卡实际扣的金额（知道的话）"},
            "charged_currency": {**STR, "description": "实际扣款币种，默认结算币种"},
            "item_id": {**STR, "description": "关联的行程事件 id"},
            "note": STR,
        }, ["title", "amount", "currency"]), propose_expense),
        Tool("get_balances", "账本现状：每人付了多少、应承担多少、净额，以及转账次数最少的结算方案。", S({}), get_balances),
        Tool("list_expenses", "所有账目。", S({}), list_expenses),
    ]
