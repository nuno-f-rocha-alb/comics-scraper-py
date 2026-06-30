// Typed client for the FastAPI JSON endpoints (same-origin; Vite proxies /api).

export type SeriesStatus =
  | "continuing-complete"
  | "ended-complete"
  | "missing-monitored"
  | "missing-unmonitored"
  | "downloading"

export interface SeriesCard {
  id: number
  publisher: string
  series_name: string
  year: number | null
  cover_image_url: string | null
  total_issues: number | null
  enabled: boolean
  metron_series_id: number | null
  comicvine_volume_id: number | null
  getcomics_search_name: string | null
  local_count: number
  status: SeriesStatus
  ended: boolean
}

export interface SeriesStats {
  series: number
  ended: number
  continuing: number
  monitored: number
  unmonitored: number
  issues_total: number
  files_total: number
}

export interface SeriesOverview {
  series: SeriesCard[]
  stats: SeriesStats
}

export type IssueStatus = "downloaded" | "missing" | "upcoming" | "tba"
export type IssueType = "regular" | "annual"

export interface Issue {
  number: string | number
  title: string | null
  date: string | null
  status: IssueStatus
}

export interface IssuesData {
  has_metron: boolean
  rate_limited?: number
  regular: Issue[]
  annual: Issue[]
  monitored_regular?: string[]
  monitored_annual?: string[]
  has_monitoring?: boolean
  cached_at?: string | null
}

export interface SeriesDetail extends Series {
  local_count: number
}

export interface MetadataFields {
  fields: Record<string, string>
  filename: string
  from_metron: boolean
}

export interface RenameItem {
  folder: string
  current: string
  expected: string | null
}
export interface RenamePreview {
  changed: RenameItem[]
  correct_count: number
  unparseable: RenameItem[]
}

async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

export interface Series {
  id: number
  publisher: string
  series_name: string
  year: number | null
  comicvine_volume_id: number | null
  metron_series_id: number | null
  metron_annual_series_id: number | null
  annual_comicvine_volume_id: number | null
  getcomics_search_name: string | null
  issue_min: number
  cover_image_url: string | null
  total_issues: number | null
  enabled: boolean
  ended?: boolean
}

export interface SeriesUpdate {
  publisher: string
  series_name: string
  year: number | null
  comicvine_volume_id: number | null
  metron_series_id: number | null
  metron_annual_series_id: number | null
  annual_comicvine_volume_id: number | null
  getcomics_search_name: string | null
  issue_min: number
}

export interface MetronResult {
  id: number
  name: string
  publisher: string | null
  year_began: number | null
  issue_count: number | null
  series_type: string | null
  cv_id: number | null
  image: string
}

export interface SeriesCreate extends SeriesUpdate {
  cover_image_url: string | null
  total_issues: number | null
}

export interface VerifyResult {
  search_term: string
  comics: { title: string; url: string }[]
}

export const getSeriesOverview = () => http<SeriesOverview>("/api/series/overview")

export const getSeries = (id: number) => http<Series>(`/api/series/${id}`)

export const updateSeries = (id: number, payload: SeriesUpdate) =>
  http<Series>(`/api/series/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })

export const createSeries = (payload: SeriesCreate) =>
  http<Series>("/api/series", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })

export const metronSearch = (name: string) =>
  http<{ results: MetronResult[] }>(`/api/metron/results?name=${encodeURIComponent(name)}`)

export const verifySearch = (seriesName: string, getcomicsName: string) =>
  http<VerifyResult>(
    `/api/verify-search/json?series_name=${encodeURIComponent(seriesName)}&getcomics_search_name=${encodeURIComponent(getcomicsName)}`,
  )

export const postJSON = <T = { updated?: number; deleted?: number }>(
  url: string,
  body: unknown,
) =>
  http<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  })

// ── Series detail ─────────────────────────────────────────────────────────
export const getSeriesDetail = (id: number) => http<SeriesDetail>(`/api/series/${id}/detail`)
export const getIssues = (id: number, force = false) =>
  http<IssuesData>(`/api/series/${id}/issues${force ? "?force=true" : ""}`)
// issue numbers can be decimals/strings (#1.5, #1a) → encode the path segment
const iss = (n: string | number) => encodeURIComponent(String(n))
export const toggleIssueMonitor = (id: number, number: string | number, type: IssueType) =>
  http<{ monitored: boolean }>(
    `/api/series/${id}/issues/${iss(number)}/monitor?type=${type}`,
    { method: "POST" },
  )
export const monitorAll = (id: number) =>
  http<{ ok: boolean }>(`/api/series/${id}/monitor-all`, { method: "POST" })
export const unmonitorAll = (id: number) =>
  http<{ ok: boolean }>(`/api/series/${id}/monitor-all`, { method: "DELETE" })
export const downloadIssue = (id: number, number: string | number, url?: string) =>
  http<{ status: string }>(
    `/api/series/${id}/issues/${iss(number)}/download${url ? `?url=${encodeURIComponent(url)}` : ""}`,
    { method: "POST" },
  )
export const deleteIssue = (id: number, number: string | number, type: IssueType) =>
  http<{ ok: boolean }>(`/api/series/${id}/issues/${iss(number)}?type=${type}`, { method: "DELETE" })
export const bulkDeleteIssues = (id: number, items: { number: string | number; type: IssueType }[]) =>
  postJSON<{ deleted: number; errors: string[] }>(`/api/series/${id}/issues/bulk/delete`, { items })
export const scanSeries = (id: number) =>
  http<{ ok: boolean }>(`/api/series/${id}/scan`, { method: "POST" })
export const deleteSeries = (id: number) =>
  http<{ deleted: number }>(`/api/series/${id}`, { method: "DELETE" })
export const getIssueMetadata = (id: number, number: string | number, metron = false) =>
  http<MetadataFields>(`/api/series/${id}/issues/${iss(number)}/metadata${metron ? "?source=metron" : ""}`)
export const saveIssueMetadata = (id: number, number: string | number, fields: Record<string, string>) =>
  postJSON<{ ok: boolean }>(`/api/series/${id}/issues/${iss(number)}/metadata`, fields)
export const getSeriesXml = (id: number) =>
  http<{ fields: Record<string, string> }>(`/api/series/${id}/series-xml`)
export const saveSeriesXml = (id: number, fields: Record<string, string>) =>
  postJSON<{ ok: boolean }>(`/api/series/${id}/series-xml`, fields)
export const getRenamePreview = (id: number) =>
  http<RenamePreview>(`/api/series/${id}/rename-preview`)
export const applyRename = (id: number, renames: RenameItem[]) =>
  postJSON<{ renamed: number; errors: number }>(`/api/series/${id}/rename-apply`, { renames })
export const toggleSeries = (id: number, action: "pause" | "resume") =>
  postJSON<{ updated: number }>(`/api/series/bulk/toggle`, { ids: [id], action })

// ── Downloads ─────────────────────────────────────────────────────────────
export type JobStatus = "queued" | "downloading" | "done" | "failed" | "cancelled"
export interface DownloadJob {
  id: number
  series_id: number
  series_name: string | null
  issue_number: string
  search_term: string
  error: string | null
  filename: string | null
  source: "manual" | "scraper"
  status: JobStatus
  created_at: string | null
}
export interface ActiveJob extends DownloadJob {
  progress: { bytes: number; total: number; rate_bps: number } | null
}
export const getDownloads = () => http<{ jobs: DownloadJob[] }>("/api/downloads")
export const getActiveDownloads = () => http<{ jobs: ActiveJob[] }>("/api/downloads/active")
export const deleteDownload = (id: number) =>
  http<{ ok: boolean }>(`/api/downloads/${id}`, { method: "DELETE" })
export const cancelDownload = (id: number) =>
  http<{ ok: boolean }>(`/api/downloads/${id}/cancel`, { method: "POST" })
export const clearDownloads = () =>
  http<{ cleared: number }>("/api/downloads", { method: "DELETE" })

// ── Library ─────────────────────────────────────────────────────────────────
export interface ScanStatus {
  running: boolean
  last_scan_at: string | null
  last_scan_error: string | null
  progress: { current: string; done: number; total: number }
}
export const getLibraryStatus = () => http<ScanStatus>("/api/library/status")
export const startLibraryScan = (force: boolean) =>
  http<ScanStatus & { started: boolean }>(`/api/library/scan?force=${force}`, { method: "POST" })

// ── Metron refresh (background) ───────────────────────────────────────────────
export interface MetronRefreshStatus {
  running: boolean
  last_refresh_at: string | null
  last_error: string | null
  progress: { current: string; done: number; total: number }
  last_result: { refreshed?: number; ids_found?: number; skipped?: number; errors?: number }
}
export const getMetronRefreshStatus = () =>
  http<MetronRefreshStatus>("/api/metron/refresh/status")
export const startMetronRefresh = () =>
  http<MetronRefreshStatus & { started: boolean }>("/api/metron/refresh", { method: "POST" })

// ── Scheduler ─────────────────────────────────────────────────────────────────
export interface SchedulerStatus {
  running: boolean
  last_run_at: string | null
  last_run_error: string | null
  next_run_at: string | null
  mode: "interval" | "cron"
  value: string
}
export const getSchedulerStatus = () => http<SchedulerStatus>("/api/scheduler/status")
export const runSchedulerNow = () =>
  http<{ started: boolean } & SchedulerStatus>("/api/scheduler/run", { method: "POST" })
export const saveSchedule = (mode: "interval" | "cron", value: string) =>
  postJSON<SchedulerStatus>("/api/scheduler/config", { mode, value })

// ── Releases ──────────────────────────────────────────────────────────────────
export interface ReleaseMatch {
  series_id: number
  series_name: string
  cover_image_url: string | null
  issue_number: string
  title: string
  url: string
  pub_date: string | null
  downloaded: boolean
  queued: boolean
}
export const getReleases = () =>
  http<{ matches: ReleaseMatch[]; feed_size: number; error: string | null }>("/api/releases")

// ── Calendar ──────────────────────────────────────────────────────────────────
export type CalEventStatus = "downloaded" | "today" | "missing" | "upcoming"
export interface CalEvent {
  series_id: number
  series_name: string
  issue_number: string
  issue_name: string
  status: CalEventStatus
  is_annual: boolean
}
export interface CalDay {
  iso: string
  day: number
  is_today: boolean
  in_view_month: boolean
  events: CalEvent[]
}
export interface CalendarData {
  view: "month" | "week"
  weeks: CalDay[][]
  header_label: string
  prev_ref: string
  next_ref: string
  today_iso: string
  current_ref: string
}
export const getCalendar = (view: "month" | "week", date: string) =>
  http<CalendarData>(`/api/calendar?view=${view}${date ? `&date=${date}` : ""}`)

// ── Logs ──────────────────────────────────────────────────────────────────────
export type LogLineClass = "error" | "warning" | "meta" | "dl" | "info"
export interface LogLine {
  text: string
  cls: LogLineClass
}
export interface LogFile {
  name: string
  size: number
}
export interface LogsInfo {
  files: LogFile[]
  current_name: string
  retention_days: number
  lines_default: number
}
export const getLogs = () => http<LogsInfo>("/api/logs")
export const getLogFiles = () => http<{ files: LogFile[] }>("/api/logs/files")
export const getLogStream = (filename: string, lines: number, level: string) =>
  http<{ filename: string; lines: LogLine[] }>(
    `/api/logs/stream?filename=${encodeURIComponent(filename)}&lines=${lines}&level=${encodeURIComponent(level)}`,
  )
export const deleteLog = (filename: string) =>
  http<{ deleted: number }>(`/api/logs/${encodeURIComponent(filename)}`, { method: "DELETE" })
export const cleanupLogs = () =>
  http<{ deleted: number }>("/api/logs/cleanup", { method: "POST" })
export const saveLogSettings = (log_retention_days: number) =>
  postJSON<{ retention_days: number }>("/api/logs/settings", { log_retention_days })
export const logDownloadUrl = (filename: string) =>
  `/api/logs/${encodeURIComponent(filename)}/download`

// Form-post endpoints that redirect on success (cache refresh / cover sync).
export const postAction = (url: string) =>
  fetch(url, { method: "POST" }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  })

// ── Reading lists ───────────────────────────────────────────────────────────
export interface RLSearchResult {
  id: number
  name: string
  list_type: string | null
  attribution_source: string | null
  average_rating: number | null
  image: string | null
}
export interface RLPreviewItem {
  order: number
  issue_type: string
  metron_series_id: number | null
  series_name: string | null
  number: string | null
  cover_year: number | null
  series_tracked: boolean
  owned: boolean
}
export interface RLPreview {
  metron_id: number
  name: string
  desc: string | null
  image_url: string | null
  list_type: string | null
  attribution_source: string | null
  average_rating: number | null
  issue_type_counts: Record<string, number>
  items: RLPreviewItem[]
}
export interface ReadingListCard {
  id: number
  metron_id: number
  name: string
  list_type: string | null
  attribution_source: string | null
  image_url: string | null
  average_rating: number | null
  num_items: number
  monitored_issue_types: string[] | null  // null = monitor all, [] = none, [..] = specific types
  owned: number
  total: number
  synced_at: string | null
}
export type RLItemStatus = "owned" | "monitored" | "missing" | "untracked"
export interface RLItem {
  order: number
  issue_type: string
  series_name: string | null
  number: string | null
  cover_year: number | null
  series_id: number | null
  status: RLItemStatus
}
export interface ReadingListDetail extends ReadingListCard {
  items: RLItem[]
}

export const searchReadingLists = (params: Record<string, string>) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString()
  return http<{ results: RLSearchResult[] }>(`/api/reading-lists/search?${q}`)
}
export const previewReadingList = (metronId: number) =>
  http<RLPreview>(`/api/reading-lists/metron/${metronId}/preview`)
export const addReadingList = (metron_id: number, issue_types: string[] | null) =>
  postJSON<ReadingListCard>("/api/reading-lists", { metron_id, issue_types })
export const getReadingLists = () =>
  http<{ reading_lists: ReadingListCard[] }>("/api/reading-lists")
export const getReadingListDetail = (id: number) =>
  http<ReadingListDetail>(`/api/reading-lists/${id}`)
export const resyncReadingList = (id: number) =>
  postJSON<ReadingListCard>(`/api/reading-lists/${id}/resync`, {})
export const deleteReadingList = (id: number) =>
  http<{ ok: boolean }>(`/api/reading-lists/${id}`, { method: "DELETE" })
export const cblDownloadUrl = (id: number) => `/api/reading-lists/${id}/cbl`
export const getKomgaStatus = () => http<{ configured: boolean }>("/api/komga/status")

// Phase B — suggestions
export interface Suggestion {
  metron_id: number
  name: string
  image_url: string | null
  list_type: string | null
  attribution_source: string | null
  average_rating: number | null
  owned: number
  total: number
  coverage: number  // percent
}
export interface SuggestStatus {
  running: boolean
  last_run_at: string | null
  last_error: string | null
  progress: { current: string; done: number; total: number }
  last_result: { scanned: number; kept: number }
}
export const getSuggestions = () => http<{ suggestions: Suggestion[] }>("/api/reading-list-suggestions")
export const scanSuggestions = () =>
  http<SuggestStatus & { started: boolean }>("/api/reading-list-suggestions/scan", { method: "POST" })
export const getSuggestStatus = () => http<SuggestStatus>("/api/reading-list-suggestions/status")
export const getSuggestSettings = () =>
  http<{ threshold: number; min_rating: number; max_lists: number }>("/api/reading-list-suggestions/settings")
export const putSuggestThreshold = (threshold: number) =>
  http<{ threshold: number }>("/api/reading-list-suggestions/settings", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ threshold }),
  })
export const pushReadingListToKomga = (id: number) =>
  postJSON<{ created: boolean; matched: number; unmatched: string[]; readlist_id: string | null }>(
    `/api/reading-lists/${id}/push-komga`, {},
  )
