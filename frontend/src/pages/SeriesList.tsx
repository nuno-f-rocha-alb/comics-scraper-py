import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Book,
  BookOpen,
  Bookmark,
  CheckSquare,
  Database,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  XCircle,
} from "lucide-react"
import {
  getSeriesOverview,
  postAction,
  postJSON,
  type SeriesCard,
  type SeriesStatus,
} from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const STATUS_BAR: Record<SeriesStatus, string> = {
  "continuing-complete": "bg-status-continuing",
  "ended-complete": "bg-status-ended",
  "missing-monitored": "bg-status-missing-monitored",
  "missing-unmonitored": "bg-status-missing-unmonitored",
  downloading: "bg-status-downloading",
}
const STATUS_TEXT: Record<SeriesStatus, string> = {
  "continuing-complete": "text-status-continuing",
  "ended-complete": "text-status-ended",
  "missing-monitored": "text-status-missing-monitored",
  "missing-unmonitored": "text-status-missing-unmonitored",
  downloading: "text-status-downloading",
}
const LEGEND: [SeriesStatus, string][] = [
  ["continuing-complete", "Continuing (All issues downloaded)"],
  ["ended-complete", "Ended (All issues downloaded)"],
  ["missing-monitored", "Missing Issues (Series monitored)"],
  ["missing-unmonitored", "Missing Issues (Series not monitored)"],
  ["downloading", "Downloading (One or more issues)"],
]

const pctOf = (s: SeriesCard) =>
  s.total_issues ? Math.round((s.local_count / s.total_issues) * 1000) / 10 : 0

export function SeriesList() {
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ["series-overview"],
    queryFn: getSeriesOverview,
  })
  const [params, setParams] = useSearchParams()
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const q = params.get("q") ?? ""
  const publisher = params.get("publisher") ?? ""
  const status = params.get("status") ?? ""
  const sort = params.get("sort") ?? "name-asc"

  const setParam = (key: string, value: string, defaultValue = "") => {
    const next = new URLSearchParams(params)
    if (value && value !== defaultValue) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const allSeries = data?.series ?? []
  const stats = data?.stats
  const publishers = useMemo(
    () => [...new Set(allSeries.map((s) => s.publisher))].sort(),
    [allSeries],
  )

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const [field, dir] = sort.split("-")
    const filtered = allSeries.filter((s) => {
      if (needle && !s.series_name.toLowerCase().includes(needle)) return false
      if (publisher && s.publisher !== publisher) return false
      if (status === "monitored" && !s.enabled) return false
      if (status === "paused" && s.enabled) return false
      return true
    })
    filtered.sort((a, b) => {
      if (field === "year" || field === "progress") {
        const av = field === "year" ? (a.year ?? 0) : pctOf(a)
        const bv = field === "year" ? (b.year ?? 0) : pctOf(b)
        return dir === "asc" ? av - bv : bv - av
      }
      const av = (field === "publisher" ? a.publisher : a.series_name).toLowerCase()
      const bv = (field === "publisher" ? b.publisher : b.series_name).toLowerCase()
      const cmp = av.localeCompare(bv)
      return dir === "asc" ? cmp : -cmp
    })
    return filtered
  }, [allSeries, q, publisher, status, sort])

  const filtersActive = !!(q || publisher || status || sort !== "name-asc")

  const toggleSelect = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  const clearSelection = () => setSelected(new Set())
  const selectAllVisible = () =>
    setSelected((prev) => {
      const ids = visible.map((s) => s.id)
      const allChecked = ids.every((id) => prev.has(id))
      return new Set(allChecked ? [] : ids)
    })

  const refetch = () => qc.invalidateQueries({ queryKey: ["series-overview"] })

  const headerAction = useMutation({
    mutationFn: (url: string) => postAction(url),
    onSuccess: () => {
      toast.success("Done")
      refetch()
    },
    onError: (e: Error) => toast.error(`Failed: ${e.message}`),
  })

  const ids = [...selected]
  const runBulk = async (fn: () => Promise<void>) => {
    if (!ids.length) return
    try {
      await fn()
      clearSelection()
      refetch()
    } catch (e) {
      toast.error(`Failed: ${(e as Error).message}`)
    }
  }
  const bulkToggle = (action: "pause" | "resume") =>
    runBulk(async () => {
      const r = await postJSON("/api/series/bulk/toggle", { ids, action })
      toast.success(`${action === "pause" ? "Paused" : "Resumed"} ${r.updated} series`)
    })
  const bulkMonitor = (mode: "all" | "missing" | "future" | "none") =>
    runBulk(async () => {
      const r = await postJSON("/api/series/bulk/monitor", { ids, mode })
      toast.success(`Monitoring updated on ${r.updated} series`)
    })
  const bulkRefresh = () =>
    runBulk(async () => {
      toast.success(`Refreshing ${ids.length} series… this may take a while.`)
      const r = await postJSON("/api/series/bulk/refresh", { ids })
      toast.success(`Refreshed ${r.updated} series`)
    })
  const bulkDelete = () => {
    if (!ids.length) return
    if (!confirm(`Delete ${ids.length} series from the DB? Local files are not touched.`)) return
    runBulk(async () => {
      const r = await postJSON("/api/series/bulk/delete", { ids })
      toast.success(`Deleted ${r.deleted} series`)
    })
  }

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>
  if (isError) return <p className="text-destructive">Failed to load series.</p>

  return (
    <>
      {/* Header */}
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">Series</h1>
          <p className="text-sm text-muted-foreground">
            {visible.length === allSeries.length
              ? `${allSeries.length} series tracked`
              : `${visible.length} of ${allSeries.length} series`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => headerAction.mutate("/api/metron/cache/refresh")}
          >
            <Database /> Refresh Cache
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => headerAction.mutate("/api/sync-covers")}
          >
            <RefreshCw /> Sync Covers
          </Button>
          <Button size="sm" asChild>
            <Link to="/series/add">
              <Plus /> Add Series
            </Link>
          </Button>
        </div>
      </div>

      {allSeries.length === 0 ? (
        <Card className="items-center py-12 text-center">
          <Book className="size-12 text-status-continuing/50" />
          <h2 className="mt-3 font-semibold">No series yet</h2>
          <p className="text-sm text-muted-foreground">Start tracking a series from Metron.</p>
          <Button asChild>
            <Link to="/series/add">
              <Plus /> Add Series
            </Link>
          </Button>
        </Card>
      ) : (
        <>
          {/* Filter bar */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <div className="relative max-w-[220px]">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                value={q}
                onChange={(e) => setParam("q", e.target.value)}
                placeholder="Search series…"
                aria-label="Search series"
                className="h-9 pl-8"
              />
            </div>
            <Select value={publisher || "all"} onValueChange={(v) => setParam("publisher", v === "all" ? "" : v)}>
              <SelectTrigger className="h-9 w-[170px]" aria-label="Filter by publisher">
                <SelectValue placeholder="All publishers" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All publishers</SelectItem>
                {publishers.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={status || "all"} onValueChange={(v) => setParam("status", v === "all" ? "" : v)}>
              <SelectTrigger className="h-9 w-[130px]" aria-label="Filter by status">
                <SelectValue placeholder="All status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All status</SelectItem>
                <SelectItem value="monitored">Monitored</SelectItem>
                <SelectItem value="paused">Paused</SelectItem>
              </SelectContent>
            </Select>
            <Select value={sort} onValueChange={(v) => setParam("sort", v, "name-asc")}>
              <SelectTrigger className="h-9 w-[170px]" aria-label="Sort series">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="name-asc">Name A → Z</SelectItem>
                <SelectItem value="name-desc">Name Z → A</SelectItem>
                <SelectItem value="publisher-asc">Publisher A → Z</SelectItem>
                <SelectItem value="year-desc">Year newest first</SelectItem>
                <SelectItem value="year-asc">Year oldest first</SelectItem>
                <SelectItem value="progress-desc">Progress most first</SelectItem>
                <SelectItem value="progress-asc">Progress least first</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={selectAllVisible}>
              <CheckSquare /> Select all visible
            </Button>
            {filtersActive && (
              <Button
                variant="link"
                size="sm"
                className="text-muted-foreground"
                onClick={() => setParams(new URLSearchParams(), { replace: true })}
              >
                <XCircle /> Reset
              </Button>
            )}
          </div>

          {/* Grid */}
          {visible.length === 0 ? (
            <div className="py-12 text-center text-muted-foreground">
              <Search className="mx-auto mb-2 size-7 opacity-50" />
              <p className="text-sm">No series match your filters.</p>
            </div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(148px,1fr))] gap-5">
              {visible.map((s) => (
                <SeriesGridCard
                  key={s.id}
                  s={s}
                  selected={selected.has(s.id)}
                  onToggle={() => toggleSelect(s.id)}
                />
              ))}
            </div>
          )}

          {/* Footer: legend + stats */}
          {stats && (
            <Card className="mt-6 p-4">
              <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
                <div className="flex flex-col gap-1 text-sm">
                  {LEGEND.map(([st, label]) => (
                    <div key={st} className="flex items-center gap-1.5">
                      <span className={cn("inline-block size-3 rounded-[2px]", STATUS_BAR[st])} />
                      {label}
                    </div>
                  ))}
                </div>
                <div className="ml-auto flex flex-wrap gap-6 text-sm">
                  <Stat label="Series" value={stats.series} />
                  <Stat label="Ended" value={stats.ended} />
                  <Stat label="Continuing" value={stats.continuing} />
                  <Stat label="Monitored" value={stats.monitored} />
                  <Stat label="Unmonitored" value={stats.unmonitored} />
                  <Stat label="Issues" value={stats.issues_total} />
                  <Stat label="Files" value={stats.files_total} />
                </div>
              </div>
            </Card>
          )}
        </>
      )}

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="fixed bottom-4 left-1/2 z-40 max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-xl border border-border bg-background p-2.5 shadow-2xl md:min-w-[600px]">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-semibold">{selected.size} selected</span>
            <Button variant="outline" size="sm" onClick={() => bulkToggle("pause")}>
              <Pause /> Pause
            </Button>
            <Button variant="outline" size="sm" onClick={() => bulkToggle("resume")}>
              <Play /> Resume
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Bookmark /> Set Monitoring
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem onClick={() => bulkMonitor("all")}>All issues</DropdownMenuItem>
                <DropdownMenuItem onClick={() => bulkMonitor("missing")}>Missing only</DropdownMenuItem>
                <DropdownMenuItem onClick={() => bulkMonitor("future")}>
                  Future only (≥ today)
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => bulkMonitor("none")}>None</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="outline" size="sm" onClick={bulkRefresh}>
              <RefreshCw /> Refresh from Metron
            </Button>
            <Button variant="outline" size="sm" className="text-destructive" onClick={bulkDelete}>
              <Trash2 /> Delete
            </Button>
            <Button
              variant="link"
              size="sm"
              className="ml-auto text-muted-foreground"
              onClick={clearSelection}
            >
              Clear
            </Button>
          </div>
        </div>
      )}
    </>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-muted-foreground">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  )
}

function SeriesGridCard({
  s,
  selected,
  onToggle,
}: {
  s: SeriesCard
  selected: boolean
  onToggle: () => void
}) {
  const pct = pctOf(s)
  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-xl bg-card shadow transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-xl motion-reduce:hover:translate-y-0",
        selected && "outline-2 outline-status-continuing -outline-offset-2",
      )}
    >
      <Link to={`/series/${s.id}`} className="block text-inherit no-underline">
        <div className="relative aspect-[2/3] overflow-hidden bg-status-continuing/10">
          {s.cover_image_url ? (
            <img
              src={s.cover_image_url}
              alt={s.series_name}
              loading="lazy"
              className="size-full object-cover"
            />
          ) : (
            <div className="flex size-full items-center justify-center text-4xl text-status-continuing">
              <BookOpen className="size-10" />
            </div>
          )}
          {!s.enabled && (
            <Badge variant="secondary" className="absolute right-1.5 top-1.5 text-[0.6rem]">
              Paused
            </Badge>
          )}
        </div>
        <div className="px-2.5 pb-2.5 pt-2">
          <div className="line-clamp-2 text-sm font-semibold leading-snug">{s.series_name}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {s.publisher}
            {s.year ? ` · ${s.year}` : ""}
          </div>
          {s.total_issues ? (
            <div className="mt-1.5">
              <div className="h-1 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn("h-full rounded-full", STATUS_BAR[s.status])}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className={cn("mt-1 text-xs", STATUS_TEXT[s.status])}>
                {s.local_count} / {s.total_issues}
              </div>
            </div>
          ) : s.local_count ? (
            <div className={cn("mt-1.5 text-xs", STATUS_TEXT[s.status])}>
              {s.local_count} downloaded
            </div>
          ) : null}
        </div>
      </Link>
      {/* Bulk select checkbox (hover/selected reveal; has-[:focus-visible]
          reveals on keyboard focus only — a mouse click won't make it linger) */}
      <span
        className={cn(
          "absolute left-1.5 top-1.5 z-10 flex rounded bg-black/55 p-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 has-[:focus-visible]:opacity-100",
          selected && "opacity-100",
        )}
        title="Select for bulk action"
        onClick={(e) => e.stopPropagation()}
      >
        <Checkbox
          checked={selected}
          onCheckedChange={onToggle}
          aria-label={`Select ${s.series_name}`}
        />
      </span>
    </div>
  )
}
