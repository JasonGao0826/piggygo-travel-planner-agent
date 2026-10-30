"""后台定时刷新实时数据（设计规格第 22 节）。

出发前 10 天到行程结束，每隔一段时间给每个行程抓一次路况、预警和预报，结果进缓存。
用户打开网页或问助手时直接用缓存，不用现等；新出现的提醒在网页的"实时情况"里显示。
"""

import logging
import threading
from datetime import date, timedelta

from ..service import TripService
from .service import ROAD_DAYS

log = logging.getLogger(__name__)


class LiveMonitor:
    def __init__(self, service: TripService, *, interval_s: int = 900):
        self.service = service
        self.interval_s = interval_s
        self._stop = threading.Event()

    def due_trips(self, today: date) -> list[str]:
        ids = []
        for trip_id in self.service.trip_ids():
            meta = self.service.current(trip_id).meta
            if meta.start_date - timedelta(days=ROAD_DAYS) <= today <= meta.end_date:
                ids.append(trip_id)
        return ids

    def run_once(self, today: date | None = None) -> list[str]:
        if self.service.live is None:
            return []
        done = []
        for trip_id in self.due_trips(today or date.today()):
            try:
                self.service.live_snapshot(self.service.current(trip_id))
                done.append(trip_id)
            except Exception:  # 一个行程出错不影响其他行程
                log.exception("刷新实时数据失败：%s", trip_id)
        return done

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                self.run_once()
                self._stop.wait(self.interval_s)
        threading.Thread(target=loop, name="live-monitor", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
