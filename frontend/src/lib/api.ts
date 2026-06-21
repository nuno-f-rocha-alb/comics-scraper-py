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
  image: string
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

// Form-post endpoints that redirect on success (cache refresh / cover sync).
export const postAction = (url: string) =>
  fetch(url, { method: "POST" }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  })
