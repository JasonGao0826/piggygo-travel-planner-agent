"""凭证夹：大家上传的预订凭证（原文件 + 识别出的字段），所有成员都能查看、下载，并随行程一起打包下载。

原文件存在上传目录里（services/api/data/uploads/<行程>/），记录存在 evidence 表里。
"""

import itertools
import re
import shutil
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .evidence import STORABLE, VoucherExtraction, guess_mime, save_upload
from .schema.memory import Evidence
from .service import ServiceError, TripService

KIND_LABEL = {"flight": "机票", "accommodation": "住宿", "car": "租车", "activity": "活动", "other": "其他"}
CAN_DELETE_ANY = ("owner", "editor")
# 每个行程凭证夹的上限（Render 的硬盘是 1GB，还要放数据库）
MAX_TRIP_FILES = 80
MAX_TRIP_BYTES = 150 * 1024 * 1024
UPLOAD_ID = re.compile(r"^[0-9a-f]{10}\Z")
# 全站所有凭证加起来的上限，以及硬盘至少要剩多少空间
MAX_SITE_BYTES = 600 * 1024 * 1024
MIN_FREE_BYTES = 150 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(name: str) -> str:
    """打包时用的文件名：去掉 Windows 不允许的字符。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', " ", name)
    return re.sub(r"\s+", " ", cleaned).strip() or "凭证"


def _first_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


class VoucherService:
    def __init__(self, service: TripService, uploads_dir: Path):
        self.service = service
        self.store = service.store
        self.uploads_dir = Path(uploads_dir)

    # ---------- 存入 ----------

    def next_id(self, trip_id: str) -> str:
        existing = {e.id for e in self.service.evidence(trip_id)}
        return next(f"ev_{n:03d}" for n in itertools.count(1) if f"ev_{n:03d}" not in existing)

    def store_file(self, trip_id: str, member_id: str, filename: str, data: bytes, *, title: str | None = None,
                   kind: str = "other", day: date | None = None, item_ids: list[str] = (), notes: str = "",
                   fields: dict[str, Any] | None = None, upload_id: str | None = None) -> Evidence:
        """把一份凭证存进凭证夹。upload_id 不为空时，文件已经在识别那一步存过了，不再重复存。"""
        trip = self.service.current(trip_id)
        if trip.member(member_id) is None:
            raise ServiceError(f"行程里没有成员 {member_id}")
        known = {i.id for i in trip.all_items()} | {a.id for a in trip.accommodations} | {v.id for v in trip.vehicles}
        if bad := [i for i in item_ids if i not in known]:
            raise ServiceError(f"行程里没有这些安排：{', '.join(bad)}")
        if upload_id:
            path = self.pending_file(trip_id, upload_id)
            if path is None:
                raise ServiceError(f"找不到上传的文件 {upload_id}")
            if any(e.stored_as == path.relative_to(self.uploads_dir).as_posix() for e in self.service.evidence(trip_id)):
                raise ServiceError("这份上传已经存进凭证夹了")
            content = path.read_bytes()
        else:
            self.check_space(trip_id, len(data))
            path, content = None, data
        if guess_mime(filename, content) not in STORABLE:
            raise ServiceError("不支持的文件类型。凭证夹只能放图片（PNG、JPG、WEBP、GIF）、PDF 和确认邮件（.eml）。")
        if path is None:
            path = save_upload(self.uploads_dir, trip_id, self.next_id(trip_id).replace("ev_", "v"), filename, data)
        if day is None and item_ids:
            item = trip.find_item(item_ids[0])
            day = item.start.date() if item else None
        evidence = Evidence(
            id=self.next_id(trip_id), kind=kind, title=(title or "").strip() or Path(filename).stem, date=day,
            filename=filename, stored_as=path.relative_to(self.uploads_dir).as_posix(), mime=guess_mime(filename, content),
            size=len(content), uploaded_by=member_id, uploaded_at=_now(), item_ids=list(item_ids), notes=notes.strip(),
            fields=fields or {}, confirmed=bool(fields),
        )
        self.service.add_evidence(trip_id, evidence)
        return evidence

    def store_extraction(self, trip_id: str, member_id: str, upload_id: str, extraction: VoucherExtraction) -> Evidence:
        """识别过、用户核对过的凭证存进凭证夹。字段原样保存，标题和日期取第一笔预订。"""
        path = self.pending_file(trip_id, upload_id)
        if path is None:
            raise ServiceError(f"找不到上传的文件 {upload_id}")
        filename = path.name.removeprefix(f"{upload_id}_")
        bookings = extraction.bookings
        kinds = {b.kind for b in bookings}
        title = "；".join(b.title for b in bookings if b.title) or None
        day = next((d for b in bookings if (d := _first_date(b.date or b.check_in_date))), None)
        return self.store_file(trip_id, member_id, filename, b"", title=title, upload_id=upload_id,
                               kind=kinds.pop() if len(kinds) == 1 else "other", day=day,
                               fields=extraction.model_dump(mode="json", exclude_none=True))

    # ---------- 上传的文件 ----------

    def pending_file(self, trip_id: str, upload_id: str) -> Path | None:
        """识别那一步存下的文件。upload_id 只能是 10 位十六进制（不能带 * 之类的通配符去匹配别人的文件）。"""
        if not UPLOAD_ID.match(upload_id):
            return None
        folder = self.uploads_dir / trip_id
        if not folder.is_dir():
            return None
        return next((p for p in folder.iterdir() if p.is_file() and p.name.startswith(f"{upload_id}_")), None)

    def check_space(self, trip_id: str, incoming: int) -> None:
        """每个行程的凭证夹有空间上限；全站所有凭证加起来也有上限，硬盘剩余空间不能太少（硬盘上还有数据库）。"""
        folder = self.uploads_dir / trip_id
        files = [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
        if len(files) >= MAX_TRIP_FILES or sum(p.stat().st_size for p in files) + incoming > MAX_TRIP_BYTES:
            raise ServiceError(f"这个行程的凭证夹满了（最多 {MAX_TRIP_FILES} 个文件、{MAX_TRIP_BYTES // 1024 // 1024}MB），"
                               "请先删掉不需要的凭证。")
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        total = sum(p.stat().st_size for p in self.uploads_dir.rglob("*") if p.is_file())
        if total + incoming > MAX_SITE_BYTES or shutil.disk_usage(self.uploads_dir).free - incoming < MIN_FREE_BYTES:
            raise ServiceError("网站的存储空间快满了，暂时不能上传，请联系小猪游记团队。")

    # 识别过、还没确认的上传：记下是谁传的（只有他能确认），放在 <行程>/.pending/<upload_id>

    def remember_upload(self, trip_id: str, upload_id: str, member_id: str) -> None:
        folder = self.uploads_dir / trip_id / ".pending"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / upload_id).write_text(member_id, encoding="utf-8")

    def upload_owner(self, trip_id: str, upload_id: str) -> str | None:
        if not UPLOAD_ID.match(upload_id):
            return None
        path = self.uploads_dir / trip_id / ".pending" / upload_id
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def forget_upload(self, trip_id: str, upload_id: str) -> None:
        if UPLOAD_ID.match(upload_id):
            (self.uploads_dir / trip_id / ".pending" / upload_id).unlink(missing_ok=True)

    def prune_all(self) -> None:
        """启动时：所有行程里一直没确认的上传都清一遍。"""
        if self.uploads_dir.is_dir():
            for folder in self.uploads_dir.iterdir():
                if folder.is_dir():
                    self.prune_pending(folder.name)

    def prune_pending(self, trip_id: str, max_age_s: float = 24 * 3600) -> None:
        """识别过但一直没确认的上传，一天后删掉。"""
        folder = self.uploads_dir / trip_id
        if not folder.is_dir():
            return
        try:
            kept = {e.stored_as for e in self.service.evidence(trip_id) if e.stored_as}
        except Exception:   # 不是行程的目录
            return
        now = time.time()
        for p in folder.iterdir():
            rel = p.relative_to(self.uploads_dir).as_posix()
            if p.is_file() and rel not in kept and UPLOAD_ID.match(p.name[:10]) and now - p.stat().st_mtime > max_age_s:
                p.unlink(missing_ok=True)
                self.forget_upload(trip_id, p.name[:10])

    # ---------- 查看 ----------

    def path(self, trip_id: str, evidence_id: str) -> tuple[Evidence, Path]:
        ev = next((e for e in self.service.evidence(trip_id) if e.id == evidence_id), None)
        if ev is None:
            raise ServiceError(f"找不到凭证 {evidence_id}")
        if not ev.stored_as:
            raise ServiceError("这条凭证是对话里记的，没有原文件")
        path = (self.uploads_dir / ev.stored_as).resolve()
        if self.uploads_dir.resolve() not in path.parents or not path.exists():
            raise ServiceError("凭证文件不见了")
        return ev, path

    def linked_items(self, trip_id: str) -> dict[str, list[str]]:
        """凭证 id → 关联的安排名称（凭证自己记的，加上行程里引用它的事件）。"""
        trip = self.service.current(trip_id)
        names = {i.id: i.title for i in trip.all_items()}
        names.update({a.id: f"住宿：{a.name}" for a in trip.accommodations})
        names.update({v.id: f"租车：{v.model or v.id}" for v in trip.vehicles})
        out: dict[str, list[str]] = {}
        for ev in self.service.evidence(trip_id):
            out[ev.id] = [names[i] for i in ev.item_ids if i in names]
        for item in trip.all_items():
            for ev_id in item.evidence_ids:
                if ev_id in out and item.title not in out[ev_id]:
                    out[ev_id].append(item.title)
        records = [(a.evidence_ids, f"住宿：{a.name}") for a in trip.accommodations]
        records += [(v.evidence_ids, f"租车：{v.model or v.id}") for v in trip.vehicles]
        for ev_ids, label in records:
            for ev_id in ev_ids:
                if ev_id in out and label not in out[ev_id]:
                    out[ev_id].append(label)
        return out

    def views(self, trip_id: str) -> list[dict]:
        """凭证夹列表：按日期排，没日期的放最后。"""
        linked = self.linked_items(trip_id)
        rows = []
        for ev in self.service.evidence(trip_id):
            bookings = ev.fields.get("bookings") or []
            rows.append({
                **ev.model_dump(mode="json", exclude={"fields"}),
                "kind_label": KIND_LABEL.get(ev.kind, "其他"),
                "uploaded_by_name": self.service.name_of(trip_id, ev.uploaded_by) if ev.uploaded_by else None,
                "items": linked.get(ev.id, []),
                "confirmation_nos": [b["confirmation_no"] for b in bookings if b.get("confirmation_no")],
                "has_file": bool(ev.stored_as),
            })
        return sorted(rows, key=lambda r: (r["date"] or "9999", r["uploaded_at"] or ""))

    # ---------- 删除 ----------

    def delete(self, trip_id: str, evidence_id: str, by: str) -> None:
        member = self.service.current(trip_id).member(by)
        ev = next((e for e in self.service.evidence(trip_id) if e.id == evidence_id), None)
        if ev is None:
            raise ServiceError(f"找不到凭证 {evidence_id}")
        if member is None or (member.role not in CAN_DELETE_ANY and ev.uploaded_by != by):
            raise ServiceError("只有上传的人、发起人或编辑者能删除这份凭证")
        shared = any(e.stored_as == ev.stored_as for e in self.service.evidence(trip_id) if e.id != ev.id)
        if ev.stored_as and not shared:   # 别的凭证记录也用着这个文件时不删
            path = (self.uploads_dir / ev.stored_as).resolve()
            if self.uploads_dir.resolve() in path.parents and path.exists():
                path.unlink()
        self.store.delete_evidence(trip_id, evidence_id)
        self.service.log(trip_id, by, "evidence", f"{member.name} 删除了凭证：{ev.title or ev.filename or ev.id}", [ev.id])

    # ---------- 打包 ----------

    def bundle(self, trip_id: str, files: dict[str, bytes], target: Path) -> Path:
        """行程包：传进来的报告、日历等文件，加上凭证夹里所有原文件（放在"凭证"文件夹里，按日期命名）。
        直接写到 target 文件里，不在内存里拼（凭证多的时候很大）。"""
        used: set[str] = set()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
            for row in self.views(trip_id):
                if not row["has_file"]:
                    continue
                _, path = self.path(trip_id, row["id"])
                day = date.fromisoformat(row["date"]) if row["date"] else None
                stem = _safe(f"{day.month:02d}-{day.day:02d} {row['title']}" if day else row["title"])[:80].strip(". ") or "凭证"
                name = f"凭证/{stem}{path.suffix.lower()}"
                n = 2
                while name in used:
                    name = f"凭证/{stem} ({n}){path.suffix.lower()}"
                    n += 1
                used.add(name)
                z.write(path, name, compress_type=zipfile.ZIP_STORED)   # 图片和 PDF 本来就压缩过
        return target
