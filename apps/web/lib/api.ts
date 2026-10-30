// 后端接口的类型和调用函数。类型按 packages/schema 里的 OpenAPI 和 JSON Schema 手写。
// 网页只调用这些接口，业务逻辑都在后端。

// 本地开发：网页在 3000 端口，后端在 8765 端口。
// 上线：设成空字符串，网页和后端用同一个网址（Next.js 把 /api 转给后端），登录 cookie 才能带上。
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8765").replace(/\/+$/, "");

// ---------- 行程 ----------

export type Role = "owner" | "editor" | "member" | string;

export interface MemberBrief {
  id: string;
  name: string;
  role: Role;
}

export interface Member extends MemberBrief {
  passport_country?: string | null;
  visa?: string | null;
  can_drive?: boolean;
  age?: number | null;
  licence_country?: string | null;
  licence_type?: string | null;
  weight_kg?: number | null;
}

/** 成员本人能改的资料（不传的字段保持原样）。称呼和角色不在这里。 */
export interface MemberProfile {
  passport_country?: string | null;
  visa?: string | null;
  can_drive?: boolean;
  age?: number | null;
  licence_country?: string | null;
  licence_type?: string | null;
  weight_kg?: number | null;
}

/** 改资料的结果：改了直接生成新版本；issues 是这次新出现的行程检查问题。 */
export interface ProfileResult {
  changed: boolean;
  version: number;
  changelog: string[];
  issues: Issue[];
}

export interface TripSummary {
  id: string;
  title: string;
  start_date: string | null;
  end_date: string | null;
  version: number;
  destination?: string;
  members: MemberBrief[];
}

export interface TripMeta {
  id: string;
  title: string;
  destination: string;
  start_date: string | null;
  end_date: string | null;
  origin_city?: string;
  local_currency?: string;
  settle_currency?: string;
  [key: string]: unknown;
}

/** 完整的行程（trip.schema.json）。网页的展示内容来自报告模型，这里只用到少数字段。 */
export interface Trip {
  meta: TripMeta;
  members: Member[];
  version: number;
  [key: string]: unknown;
}

export type Severity = "error" | "warning";

export interface Issue {
  code: string;
  severity: Severity;
  message: string;
  item_ids: string[];
  suggestions: string[];
}

export interface PatchView {
  id: string;
  summary: string;
  status: "proposed" | "applied" | "rejected" | "conflict" | string;
  base_version: number;
  can_confirm: boolean;
  issues: Issue[];
  changes: string[];
}

export interface Decision {
  id: string;
  content: string;
  kind: string;
  priority?: string;
  status?: string;
  rationale?: string | null;
  supersedes?: string | null;
  agreed_by?: string[];
  proposed_by?: string | null;
  [key: string]: unknown;
}

export interface TripDetail {
  version: number;
  trip: Trip;
  issues: Issue[];
  pending: PatchView[];
  decisions: Decision[];
}

// ---------- 实时情况（路况、天气预警、天气预报、公众假日） ----------

/** 和外部数据比对的校验规则：网页单独放在"实时情况"里显示 */
export const LIVE_CODES = new Set(["R21", "R22", "R23", "R24"]);

export interface DayForecast {
  date: string;
  places: string[];
  weather: string;
  code: number | null;
  tmin: number | null;
  tmax: number | null;
  precip_mm: number | null;
  precip_prob: number | null;
  gust_kmh: number | null;
}

export interface LiveReport {
  enabled: boolean;
  issues: Issue[];
  /** 每天一行：当天各地点里最差的天气 */
  days: DayForecast[];
  forecasts: DayForecast[];
  notes: string[];
  errors: string[];
  fetched_at: Record<string, string>;
}

// ---------- 多人协作（偏好、汇总、投票、动态） ----------

export interface MustDo {
  what: string;
  activity_tag?: string | null;
  place_hint?: string | null;
  date?: string | null;
  fixed_activity: boolean;
  fixed_place: boolean;
  fixed_date: boolean;
}

export type Pace = "relaxed" | "balanced" | "packed";
export type ThrillLevel = "low" | "mid" | "high";

export interface PreferenceForm {
  must_do: MustDo[];
  wishlist: string[];
  avoid: string[];
  thrill_level: ThrillLevel | null;
  pace: Pace | null;
  max_daily_drive_hours: number | null;
  earliest_start: string | null;
  budget_per_person: { amount: string | number; currency: string; per?: string } | null;
  diet: string[];
  notes: string;
}

export interface MemberPreferences extends PreferenceForm {
  member_id: string;
  updated_at: string;
}

export interface PollOptionDraft {
  label: string;
  impact: string;
}

export interface Divergence {
  id: string;
  topic: string;
  kind: string;
  detail: string;
  positions: { member_ids: string[]; stance: string }[];
  suggestion: string;
  poll: { question: string; options: PollOptionDraft[] } | null;
  poll_id: string | null;
}

export interface PreferenceSummary {
  created_at: string;
  created_by: string | null;
  consensus: { topic: string; detail: string; member_ids: string[] }[];
  divergences: Divergence[];
  groupable: { topic: string; detail: string; member_ids: string[]; others: string }[];
  missing: string[];
  decision_ids: string[];
}

export interface Poll {
  id: string;
  question: string;
  options: { id: string; label: string; impact: string }[];
  created_by: string | null;
  created_at: string;
  deadline: string | null;
  status: "open" | "closed";
  votes: Record<string, string>;
  result: string | null;
  decision_id: string | null;
  divergence_id: string | null;
  closed_by: string | null;
  closed_at: string | null;
  note: string;
}

export interface GroupView {
  members: (MemberBrief & { filled: boolean; updated_at: string | null })[];
  preferences: Record<string, MemberPreferences>;
  summary: PreferenceSummary | null;
  summary_stale: boolean;
  polls: Poll[];
  all_filled: boolean;
}

export type VoucherKind = "flight" | "accommodation" | "car" | "activity" | "other";

/** 凭证夹里的一份凭证 */
export interface Voucher {
  id: string;
  kind: VoucherKind;
  kind_label: string;
  title: string | null;
  date: string | null;
  filename: string | null;
  mime: string | null;
  size: number | null;
  uploaded_by: string | null;
  uploaded_by_name: string | null;
  uploaded_at: string | null;
  notes: string;
  items: string[];
  confirmation_nos: string[];
  has_file: boolean;
  file: string | null;
}

// ---------- 账本（AA 记账与结算） ----------

export type ExpenseCategory = "accommodation" | "flight" | "car" | "fuel" | "parking" | "activity" | "food" | "grocery" | "visa" | "other";

export interface ExpenseSplitIn {
  member_id: string;
  shares?: string | null;
  amount?: string | null;
}

export interface ExpenseIn {
  title: string;
  category: ExpenseCategory;
  payer: string;
  amount: string;
  currency: string;
  date: string;
  split_method: "equal" | "shares" | "exact";
  splits: ExpenseSplitIn[] | null;
  exclude?: string[];
  fx_method: "auto" | "charged" | "cash" | "estimate";
  charged_amount: string | null;
  charged_currency: string | null;
  item_id: string | null;
  note: string;
  status?: "draft" | "confirmed";
}

export interface ExpenseView {
  id: string;
  title: string;
  category: ExpenseCategory;
  category_label: string;
  payer: string;
  payer_name: string;
  amount: string;
  currency: string;
  amount_text: string;
  settle_amount: string;
  settle_text: string;
  fx: { method: "same" | "charged" | "cash" | "estimate"; charged_amount: string | null; charged_currency: string | null; rate: string | null };
  date: string;
  split_method: "equal" | "shares" | "exact";
  splits: { member_id: string; shares: string | null; amount: string | null }[];
  split_text: string;
  shares: { member_id: string; name: string; amount: string; text: string }[];
  item_id: string | null;
  item_title: string | null;
  evidence_id: string | null;
  status: "draft" | "confirmed" | "disputed";
  note: string;
  dispute_note: string;
  created_by: string;
  created_by_name: string;
  history: { at: string; by: string; summary: string }[];
}

export interface MoneySummary {
  settle_currency: string;
  units: string[][];
  expenses: ExpenseView[];
  balances: { member_id: string; name: string; paid: string; owed: string; net: string; paid_text: string; owed_text: string; net_text: string }[];
  plan: { from: string[]; to: string[]; from_name: string; to_name: string; pay_member: string; receive_member: string; amount: string; text: string }[];
  transfers: { id: string; from_member: string; to_member: string; from_name: string; to_name: string; amount: string; text: string; status: "sent" | "confirmed"; note: string }[];
  checks: { code: string; severity: string; message: string; expense_ids: string[] }[];
  text: string;
  by_category: { category: string; label: string; amount: string; text: string }[];
  total_text: string;
}

// ---------- 小红书攻略 ----------

export interface Guide {
  id: string;
  url: string | null;
  title: string;
  summary: string;
  place_ids: string[];
  added_by: string;
  added_by_name: string;
  added_at: string;
  source: string;
  extraction: {
    title: string;
    summary: string;
    places: { name: string; place_id: string | null; place_name: string | null; what: string; tips: string[] }[];
    prices: string[];
    booking_tips: string[];
    cautions: string[];
  };
}

export interface ActivityEvent {
  id: number;
  at: string;
  actor: string | null;
  kind: string;
  text: string;
  refs: string[];
}

export interface VersionEntry {
  version: number;
  patch_id: string | null;
  changelog: string[];
}

// ---------- 报告内容模型（网页和 Word 用同一份） ----------

export interface TableRow {
  cells: string[];
  fill: string | null;
  bold: boolean;
}

export type ReportBlock =
  | { kind: "paragraph"; text: string; muted: boolean }
  | { kind: "bullets"; items: string[] }
  | { kind: "table"; columns: string[]; rows: TableRow[]; widths_mm: number[] | null }
  | {
      kind: "card";
      title: string;
      facts: [string, string][];
      links: { label: string; url: string }[];
      intro: string | null;
      fill: string | null;
    }
  | { kind: "heading"; text: string; fill: string | null };

export interface ReportSection {
  title: string;
  new_page: boolean;
  blocks: ReportBlock[];
}

export interface Report {
  title: string;
  cover_lines: string[];
  legend: { city: string; color: string }[];
  sections: ReportSection[];
}

// ---------- 对话 ----------

export interface ChatMessage {
  role: "user" | "assistant" | string;
  text: string;
}

export interface AgentResponse {
  reply: string;
  patches: PatchView[];
  decisions: Decision[];
  tool_calls: { name: string; error: string | null }[];
  usage: { input_tokens?: number; output_tokens?: number; calls?: number };
}

export interface ConfirmResult {
  version: number;
  changelog: string[];
}

// ---------- 凭证 ----------

export type BookingKind = "flight" | "accommodation" | "car" | "activity" | "other";

export interface BookingExtraction {
  kind: BookingKind | string | null;
  title: string | null;
  date: string | null;
  start_time: string | null;
  end_time: string | null;
  checkin_time: string | null;
  location: string | null;
  pax: number | null;
  participants: string[] | null;
  confirmation_no: string | null;
  price_amount: number | null;
  price_currency: string | null;
  flight_number: string | null;
  from_airport: string | null;
  to_airport: string | null;
  check_in_date: string | null;
  check_out_date: string | null;
  address: string | null;
  quotes: string[] | null;
  [key: string]: unknown;
}

export interface Extraction {
  bookings: BookingExtraction[];
  notes: string[];
  unreadable: boolean;
}

export interface EvidenceUpload {
  upload_id: string;
  filename: string;
  extraction: Extraction;
}

// ---------- 问卷（intake.schema.json） ----------

export interface Money {
  amount: number | string;
  currency: string;
  per?: "person" | "group" | "vehicle";
}

export interface MustDo {
  what: string;
  activity_tag?: string | null;
  place_hint?: string | null;
  date?: string | null;
  fixed_activity: boolean;
  fixed_place: boolean;
  fixed_date: boolean;
}

export interface MemberIntake {
  name: string;
  passport_country: string;
  visa?: string | null;
  can_drive: boolean;
  age?: number | null;
  licence_country?: string | null;
  licence_type?: string | null;
  must_do: MustDo[];
  wishlist: string[];
  avoid: string[];
  thrill_level?: "low" | "mid" | "high" | null;
  weight_kg?: number | null;
  diet: string[];
  large_luggage?: number | null;
  payment_note?: string | null;
}

export interface ExistingBooking {
  kind: BookingKind;
  description: string;
  file?: string | null;
}

export interface TripIntake {
  destination: string;
  regions: string[];
  start_date?: string | null;
  end_date?: string | null;
  month?: string | null;
  days?: number | null;
  origin_city: string;
  party: string;
  couples: string[][];
  transport: "self_drive" | "public" | "tour" | "mixed";
  budget_per_person?: Money | null;
  room_budget_per_night?: Money | null;
  settle_currency: string;
  accommodation_type: "whole_home" | "hotel" | "any";
  room_requirements: string;
  need_parking: boolean;
  max_daily_drive_hours?: number | null;
  earliest_start?: string | null;
  pace?: "relaxed" | "balanced" | "packed" | null;
  existing_bookings: ExistingBooking[];
  output_format: "word" | "pdf";
  members: MemberIntake[];
}

export type JobStatus = "running" | "needs_input" | "done" | "failed";

export interface JobQuestion {
  id?: string;
  text?: string;
  question?: string;
  [key: string]: unknown;
}

export interface SkeletonDay {
  date: string;
  route: string;
  night_city: string | null;
  focus: string;
}

export interface SkeletonFlight {
  number: string | null;
  from_airport: string;
  to_airport: string;
  date: string;
  depart: string;
  arrive: string;
  booked: boolean;
}

/** 路线框架（S2），等用户确认时随任务返回。 */
export interface JobSkeleton {
  summary: string;
  assumptions: string[];
  days: SkeletonDay[];
  flights: SkeletonFlight[];
}

export interface Job {
  status: JobStatus;
  stage: string | null;
  message: string | null;
  trip_id?: string | null;
  questions?: (string | JobQuestion)[] | null;
  skeleton?: JobSkeleton | null;
}

// ---------- 登录（专属邀请链接） ----------

/** open：本地开发，页面上可以切换"我是谁"；invite：上线后用邀请链接登录，身份固定。 */
export interface SessionInfo {
  mode: "open" | "invite";
  trips: { trip_id: string; member_id: string; name: string; role: Role }[];
  needs_beta_code: boolean;
}

/** 打开邀请链接前的检查：这是谁的链接；这台设备在这个行程里现在是谁。 */
export interface JoinCheck {
  trip_id: string;
  title: string;
  member_id: string;
  name: string;
  current_member_id: string | null;
  current_name: string | null;
}

export interface InviteStatus {
  member_id: string;
  name: string;
  role: Role;
  has_link: boolean;
  created_at: string | null;
}

export interface AdminTrip {
  id: string;
  title: string;
  members: MemberBrief[];
}

export interface InviteLink {
  member_id: string;
  name?: string;
  role?: Role;
  token: string;
}

/** 邀请链接的完整网址（发给成员的就是它）。 */
export function joinUrl(token: string): string {
  return `${window.location.origin}/join/${token}`;
}

// ---------- 请求 ----------

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** 接口还没上线：FastAPI 对不存在的路径返回 404 "Not Found"，对不支持的方法返回 405。 */
  get notAvailable(): boolean {
    return this.status === 405 || this.status === 501 || (this.status === 404 && this.detail === "Not Found");
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI 的 422 校验错误：[{loc, msg}]
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".")
            : "";
          return loc ? `${loc}：${(d as { msg: string }).msg}` : String((d as { msg: string }).msg);
        }
        return JSON.stringify(d);
      })
      .join("；");
  }
  return JSON.stringify(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
  } catch {
    // 浏览器拿不到响应：后端没启动、网络断了，或者后端内部出错（500 不带跨域头，浏览器看不到内容）
    throw new ApiError(0, `请求没有完成：连不上后端（${API_BASE}），或者后端处理时出错了。请稍后重试。`);
  }
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) detail = formatDetail(body.detail);
    } catch {
      // 返回的不是 JSON，就用状态文字
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const enc = encodeURIComponent;

/** 上传前先在浏览器里检查大小（后端上限 10MB），太大就不传了，免得传半天才报错。 */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

function checkSize(file: File): void {
  if (file.size > MAX_UPLOAD_BYTES) throw new ApiError(413, `文件太大（${(file.size / 1024 / 1024).toFixed(1)}MB），请上传 10MB 以内的文件。`);
}

export const api = {
  listTrips: () => request<TripSummary[]>("/api/trips"),
  createDemo: () => postJson<{ id: string }>("/api/demo"),
  getTrip: (id: string) => request<TripDetail>(`/api/trips/${enc(id)}`),
  getReport: (id: string) => request<Report>(`/api/trips/${enc(id)}/report`),
  reportDocxUrl: (id: string) => `${API_BASE}/api/trips/${enc(id)}/report.docx`,
  calendarUrl: (id: string) => `${API_BASE}/api/trips/${enc(id)}/calendar.ics`,
  getLive: (id: string) => request<LiveReport>(`/api/trips/${enc(id)}/live`),
  getVersions: (id: string) => request<VersionEntry[]>(`/api/trips/${enc(id)}/versions`),
  getMessages: (id: string, memberId: string) =>
    request<ChatMessage[]>(`/api/trips/${enc(id)}/messages?member_id=${enc(memberId)}`),
  chat: (id: string, memberId: string, text: string) =>
    postJson<AgentResponse>(`/api/trips/${enc(id)}/chat`, { member_id: memberId, text }),
  listPatches: (id: string, status: "proposed" | "all" = "proposed") =>
    request<PatchView[]>(`/api/trips/${enc(id)}/patches?status=${status}`),
  confirmPatch: (id: string, patchId: string, memberId: string) =>
    postJson<ConfirmResult>(`/api/trips/${enc(id)}/patches/${enc(patchId)}/confirm`, { member_id: memberId }),
  rejectPatch: (id: string, patchId: string) =>
    postJson<PatchView>(`/api/trips/${enc(id)}/patches/${enc(patchId)}/reject`),

  uploadEvidence: (id: string, memberId: string, file: File) => {
    checkSize(file);
    const form = new FormData();
    form.append("member_id", memberId);
    form.append("file", file);
    return request<EvidenceUpload>(`/api/trips/${enc(id)}/evidence`, { method: "POST", body: form });
  },
  applyEvidence: (id: string, uploadId: string, memberId: string, extraction: Extraction) =>
    postJson<AgentResponse>(`/api/trips/${enc(id)}/evidence/${enc(uploadId)}/apply`, {
      member_id: memberId,
      extraction,
    }),

  saveProfile: (id: string, memberId: string, profile: MemberProfile) =>
    request<ProfileResult>(`/api/trips/${enc(id)}/members/${enc(memberId)}/profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    }),
  getGroup: (id: string) => request<GroupView>(`/api/trips/${enc(id)}/group`),
  savePreferences: (id: string, memberId: string, form: PreferenceForm) =>
    request<GroupView>(`/api/trips/${enc(id)}/preferences/${enc(memberId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    }),
  summarize: (id: string, memberId: string) => postJson<GroupView>(`/api/trips/${enc(id)}/summary`, { member_id: memberId }),
  createPoll: (
    id: string,
    body: { member_id: string; question: string; options: PollOptionDraft[]; deadline_hours?: number | null; divergence_id?: string | null },
  ) => postJson<GroupView>(`/api/trips/${enc(id)}/polls`, body),
  vote: (id: string, pollId: string, memberId: string, optionId: string) =>
    postJson<GroupView>(`/api/trips/${enc(id)}/polls/${enc(pollId)}/vote`, { member_id: memberId, option_id: optionId }),
  closePoll: (id: string, pollId: string, memberId: string, optionId?: string | null) =>
    postJson<GroupView>(`/api/trips/${enc(id)}/polls/${enc(pollId)}/close`, { member_id: memberId, option_id: optionId ?? null }),
  listVouchers: (id: string) => request<Voucher[]>(`/api/trips/${enc(id)}/vouchers`),
  storeVoucher: (
    id: string,
    body: { member_id: string; file: File; title?: string; kind?: VoucherKind; date?: string; item_id?: string; notes?: string },
  ) => {
    checkSize(body.file);
    const form = new FormData();
    for (const [k, v] of Object.entries(body)) if (v) form.append(k, v as string | Blob);
    return request<Voucher>(`/api/trips/${enc(id)}/vouchers`, { method: "POST", body: form });
  },
  deleteVoucher: (id: string, evidenceId: string, memberId: string) =>
    request<{ ok: boolean }>(`/api/trips/${enc(id)}/vouchers/${enc(evidenceId)}?member_id=${enc(memberId)}`, { method: "DELETE" }),
  voucherFileUrl: (id: string, evidenceId: string, download = false) =>
    `${API_BASE}/api/trips/${enc(id)}/vouchers/${enc(evidenceId)}/file${download ? "?download=1" : ""}`,
  bundleUrl: (id: string) => `${API_BASE}/api/trips/${enc(id)}/bundle.zip`,
  getMoney: (id: string) => request<MoneySummary>(`/api/trips/${enc(id)}/money`),
  addExpense: (id: string, memberId: string, expense: ExpenseIn) =>
    postJson<MoneySummary>(`/api/trips/${enc(id)}/money/expenses`, { member_id: memberId, expense }),
  editExpense: (id: string, expenseId: string, memberId: string, expense: ExpenseIn) =>
    request<MoneySummary>(`/api/trips/${enc(id)}/money/expenses/${enc(expenseId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: memberId, expense }),
    }),
  confirmExpense: (id: string, expenseId: string, memberId: string) =>
    postJson<MoneySummary>(`/api/trips/${enc(id)}/money/expenses/${enc(expenseId)}/confirm`, { member_id: memberId }),
  disputeExpense: (id: string, expenseId: string, memberId: string, note: string) =>
    postJson<MoneySummary>(`/api/trips/${enc(id)}/money/expenses/${enc(expenseId)}/dispute`, { member_id: memberId, note }),
  setActual: (id: string, expenseId: string, memberId: string, chargedAmount: string, chargedCurrency: string | null) =>
    postJson<MoneySummary>(`/api/trips/${enc(id)}/money/expenses/${enc(expenseId)}/actual`, {
      member_id: memberId,
      charged_amount: chargedAmount,
      charged_currency: chargedCurrency,
    }),
  deleteExpense: (id: string, expenseId: string, memberId: string) =>
    request<MoneySummary>(`/api/trips/${enc(id)}/money/expenses/${enc(expenseId)}?member_id=${enc(memberId)}`, { method: "DELETE" }),
  addTransfer: (id: string, memberId: string, fromMember: string, toMember: string, amount: string) =>
    postJson<MoneySummary>(`/api/trips/${enc(id)}/money/transfers`, { member_id: memberId, from_member: fromMember, to_member: toMember, amount }),
  confirmTransfer: (id: string, transferId: string, memberId: string) =>
    postJson<MoneySummary>(`/api/trips/${enc(id)}/money/transfers/${enc(transferId)}/confirm`, { member_id: memberId }),
  deleteTransfer: (id: string, transferId: string, memberId: string) =>
    request<MoneySummary>(`/api/trips/${enc(id)}/money/transfers/${enc(transferId)}?member_id=${enc(memberId)}`, { method: "DELETE" }),
  setUnits: (id: string, memberId: string, units: string[][]) =>
    request<MoneySummary>(`/api/trips/${enc(id)}/money/units`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: memberId, units }),
    }),
  listGuides: (id: string) => request<Guide[]>(`/api/trips/${enc(id)}/guides`),
  readGuide: (id: string, memberId: string, input: { text?: string; file?: File }) => {
    if (input.file) checkSize(input.file);
    const form = new FormData();
    form.append("member_id", memberId);
    if (input.text) form.append("text", input.text);
    if (input.file) form.append("file", input.file);
    return request<Guide>(`/api/trips/${enc(id)}/guides`, { method: "POST", body: form });
  },
  deleteGuide: (id: string, guideId: string, memberId: string) =>
    request<{ ok: boolean }>(`/api/trips/${enc(id)}/guides/${enc(guideId)}?member_id=${enc(memberId)}`, { method: "DELETE" }),
  getConfirmNeeds: (id: string) => request<{ id: string; title: string; due: string | null }[]>(`/api/trips/${enc(id)}/confirm-needs`),
  createConfirmTodos: (id: string, memberId: string) =>
    postJson<PatchView>(`/api/trips/${enc(id)}/confirm-todos`, { member_id: memberId }),
  moneyCsvUrl: (id: string) => `${API_BASE}/api/trips/${enc(id)}/money.csv`,
  getActivity: (id: string, limit = 50) => request<ActivityEvent[]>(`/api/trips/${enc(id)}/activity?limit=${limit}`),

  createTrip: (intake: TripIntake, betaCode?: string) =>
    request<{ job_id: string }>("/api/trips", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(betaCode ? { "X-Beta-Code": betaCode } : {}) },
      body: JSON.stringify(intake),
    }),
  getJob: (jobId: string) => request<Job>(`/api/jobs/${enc(jobId)}`),
  answerJob: (jobId: string, text: string) => postJson<Job | Record<string, unknown>>(`/api/jobs/${enc(jobId)}/answer`, { text }),

  getSession: () => request<SessionInfo>("/api/session"),
  join: (token: string) => postJson<{ trip_id: string; member_id: string }>("/api/join", { token }),
  joinCheck: (token: string) => postJson<JoinCheck>("/api/join/check", { token }),
  logout: () => postJson<{ ok: boolean }>("/api/logout"),
  listInvites: (id: string, memberId: string) =>
    request<InviteStatus[]>(`/api/trips/${enc(id)}/invites?member_id=${enc(memberId)}`),
  newInvite: (id: string, forMember: string, memberId: string) =>
    postJson<InviteLink>(`/api/trips/${enc(id)}/invites/${enc(forMember)}?member_id=${enc(memberId)}`),

  adminTrips: (token: string) => request<AdminTrip[]>("/api/admin/trips", { headers: { "X-Admin-Token": token } }),
  adminDemo: (token: string) =>
    request<{ id: string }>("/api/admin/demo", { method: "POST", headers: { "X-Admin-Token": token } }),
  adminInvites: (token: string, id: string) =>
    request<InviteLink[]>(`/api/admin/trips/${enc(id)}/invites`, { method: "POST", headers: { "X-Admin-Token": token } }),
};

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return String(e);
}
