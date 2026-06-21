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
export const toggleIssueMonitor = (id: number, number: string | number, type: IssueType) =>
  http<{ monitored: boolean }>(
    `/api/series/${id}/issues/${number}/monitor?type=${type}`,
    { method: "POST" },
  )
export const monitorAll = (id: number) =>
  http<{ ok: boolean }>(`/api/series/${id}/monitor-all`, { method: "POST" })
export const unmonitorAll = (id: number) =>
  http<{ ok: boolean }>(`/api/series/${id}/monitor-all`, { method: "DELETE" })
export const downloadIssue = (id: number, number: string | number) =>
  http<{ status: string }>(`/api/series/${id}/issues/${number}/download`, { method: "POST" })
export const deleteIssue = (id: number, number: string | number, type: IssueType) =>
  http<{ ok: boolean }>(`/api/series/${id}/issues/${number}?type=${type}`, { method: "DELETE" })
export const bulkDeleteIssues = (id: number, items: { number: string | number; type: IssueType }[]) =>
  postJSON<{ deleted: number; errors: string[] }>(`/api/series/${id}/issues/bulk/delete`, { items })
export const scanSeries = (id: number) =>
  http<{ ok: boolean }>(`/api/series/${id}/scan`, { method: "POST" })
export const deleteSeries = (id: number) =>
  http<{ deleted: number }>(`/api/series/${id}`, { method: "DELETE" })
export const getIssueMetadata = (id: number, number: string | number, metron = false) =>
  http<MetadataFields>(`/api/series/${id}/issues/${number}/metadata${metron ? "?source=metron" : ""}`)
export const saveIssueMetadata = (id: number, number: string | number, fields: Record<string, string>) =>
  postJSON<{ ok: boolean }>(`/api/series/${id}/issues/${number}/metadata`, fields)
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

// Form-post endpoints that redirect on success (cache refresh / cover sync).
export const postAction = (url: string) =>
  fetch(url, { method: "POST" }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  })
