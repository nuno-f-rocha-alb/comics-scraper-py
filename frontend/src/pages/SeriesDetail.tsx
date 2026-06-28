import { useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  RotateCw,
  Book,
  Bookmark,
  Calendar,
  CheckCircle2,
  Clock,
  Download,
  Files,
  HelpCircle,
  Loader2,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react"
import {
  applyRename,
  bulkDeleteIssues,
  deleteIssue,
  deleteSeries,
  downloadIssue,
  getIssues,
  getRenamePreview,
  getSeriesDetail,
  monitorAll,
  scanSeries,
  toggleIssueMonitor,
  toggleSeries,
  unmonitorAll,
  type Issue,
  type IssueType,
} from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import { MetadataSheet } from "@/components/MetadataSheet"
import { SeriesNotes } from "@/components/SeriesNotes"
import { useConfirm } from "@/components/confirm"

const fmtDate = (d: string | null) =>
  d ? new Date(d + (d.length === 10 ? "T00:00:00" : "")).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  }) : "—"

export function SeriesDetail() {
  const { id } = useParams()
  const seriesId = Number(id)
  const nav = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()

  const detail = useQuery({
    queryKey: ["series-detail", seriesId],
    queryFn: () => getSeriesDetail(seriesId),
    enabled: Number.isFinite(seriesId),
  })
  const issues = useQuery({
    queryKey: ["series-issues", seriesId],
    queryFn: () => getIssues(seriesId),
    enabled: Number.isFinite(seriesId),
    refetchInterval: (q) => (q.state.data?.rate_limited ? q.state.data.rate_limited * 1000 : false),
  })

  const [editIssue, setEditIssue] = useState<string | null>(null)
  const [renameOpen, setRenameOpen] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set()) // "type:number"

  const refetchIssues = () => qc.invalidateQueries({ queryKey: ["series-issues", seriesId] })
  const refetchAll = () => {
    refetchIssues()
    qc.invalidateQueries({ queryKey: ["series-detail", seriesId] })
  }

  const action = <T,>(fn: () => Promise<T>, ok: string, after?: () => void) =>
    fn().then(() => { toast.success(ok); after?.() }).catch((e: Error) => toast.error(`Failed: ${e.message}`))

  if (detail.isLoading) return <p className="text-muted-foreground">Loading…</p>
  if (detail.isError || !detail.data) return <p className="text-destructive">Failed to load series.</p>
  const s = detail.data
  const pct = s.total_issues ? Math.min((s.local_count / s.total_issues) * 100, 100) : 0
  const d = issues.data

  const monitoredSet = (t: IssueType) =>
    new Set(t === "annual" ? d?.monitored_annual ?? [] : d?.monitored_regular ?? [])
  const isMonitored = (i: Issue, t: IssueType) =>
    !d?.has_monitoring || monitoredSet(t).has(String(i.number))

  const toggleSel = (key: string) =>
    setSelected((p) => { const n = new Set(p); n.has(key) ? n.delete(key) : n.add(key); return n })
  const selItems = [...selected].map((k) => {
    const [type, number] = k.split(":")
    return { number, type: type as IssueType }
  })

  return (
    <>
      <nav className="mb-3 text-sm text-muted-foreground">
        <Link to="/series" className="hover:text-foreground">Series</Link>
        <span className="mx-1.5">/</span>
        <span className="text-foreground">{s.series_name}</span>
      </nav>

      {/* Header */}
      <Card className="mb-4 p-6">
        <div className="flex flex-wrap items-start gap-6">
          <div className="w-40 shrink-0">
            {s.cover_image_url ? (
              <img src={s.cover_image_url} alt={s.series_name} loading="lazy"
                className="w-full rounded-lg object-cover" style={{ aspectRatio: "2/3" }} />
            ) : (
              <div className="flex w-full items-center justify-center rounded-lg bg-status-continuing/10 text-status-continuing"
                style={{ aspectRatio: "2/3" }}>
                <Book className="size-10" />
              </div>
            )}
          </div>

          <div className="min-w-0 flex-1">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold">{s.series_name}</h1>
              <Badge variant={s.enabled ? "default" : "secondary"}>
                {s.ended ? "Finished" : s.enabled ? "Monitored" : "Paused"}
              </Badge>
            </div>
            <p className="mb-3 text-muted-foreground">
              {s.publisher}
              {s.year ? ` · ${s.year}` : ""}
              {s.metron_series_id ? ` · Metron #${s.metron_series_id}` : ""}
              {(s.issue_min || s.total_issues) ? ` · Issues #${s.issue_min || 1} → ${s.total_issues ? `#${s.total_issues}` : "∞"}` : ""}
            </p>

            {s.total_issues ? (
              <div className="mb-3 max-w-[380px]">
                <div className="mb-1 flex justify-between text-sm text-muted-foreground">
                  <span>{s.local_count} downloaded</span>
                  <span>{s.total_issues} issues</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
                </div>
              </div>
            ) : s.local_count ? (
              <p className="mb-3 text-sm text-muted-foreground">{s.local_count} files downloaded</p>
            ) : null}

            <div className="mt-2 flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to={`/series/${seriesId}/edit`}><Pencil /> Edit</Link>
              </Button>
              <Button variant="outline" size="sm"
                onClick={() => action(() => toggleSeries(seriesId, s.enabled ? "pause" : "resume"),
                  s.enabled ? "Paused" : "Resumed", refetchAll)}>
                {s.enabled ? <><Pause /> Pause</> : <><Play /> Resume</>}
              </Button>
              <Button variant="outline" size="sm"
                onClick={() => action(() => scanSeries(seriesId), "Scan started…")}>
                <RefreshCw /> Scan Folder
              </Button>
              <Button variant="outline" size="sm" onClick={() => setRenameOpen((v) => !v)}>
                <Files /> Preview Rename
              </Button>
              <Button variant="outline" size="sm" className="ml-auto text-destructive"
                onClick={async () => {
                  if (await confirm({ title: `Delete ${s.series_name}?`, description: "This cannot be undone.", confirmText: "Delete", destructive: true }))
                    action(() => deleteSeries(seriesId), "Deleted", () => nav("/series"))
                }}>
                <Trash2 /> Delete
              </Button>
            </div>
          </div>
        </div>
      </Card>

      <SeriesNotes seriesId={seriesId} />

      {renameOpen && <RenamePanel seriesId={seriesId} onClose={() => setRenameOpen(false)} afterApply={refetchIssues} />}

      {/* Issues */}
      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <span className="font-semibold">Issues</span>
          {issues.isFetching && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
          <Button variant="ghost" size="sm" className="ml-auto text-muted-foreground"
            onClick={() => action(() => getIssues(seriesId, true).then((data) =>
              qc.setQueryData(["series-issues", seriesId], data)), "Refreshed from Metron")}>
            <RotateCw /> Refresh from Metron
          </Button>
        </div>

        {issues.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Loading issues…</p>
        ) : d?.rate_limited ? (
          <p className="m-3 rounded-md bg-status-missing-unmonitored/15 p-3 text-sm">
            Metron rate limit hit — retrying in {d.rate_limited}s…
          </p>
        ) : !d?.has_metron ? (
          <p className="m-3 rounded-md bg-muted p-3 text-sm text-muted-foreground">
            No Metron ID set — issue list unavailable.
          </p>
        ) : (d.regular.length || d.annual.length) ? (
          <>
            <IssuesTable
              issues={d.regular} type="regular" seriesId={seriesId}
              isMonitored={isMonitored}
              selected={selected} toggleSel={toggleSel}
              onEdit={setEditIssue} onChange={refetchAll}
            />
            {d.annual.length > 0 && (
              <>
                <div className="flex items-center gap-2 border-t border-border bg-status-continuing/5 px-4 py-2">
                  <Calendar className="size-4 text-muted-foreground" />
                  <span className="text-sm font-semibold">Annuals</span>
                  <Badge variant="secondary" className="text-[0.65rem]">{d.annual.length}</Badge>
                </div>
                <IssuesTable
                  issues={d.annual} type="annual" seriesId={seriesId}
                  isMonitored={isMonitored}
                  selected={selected} toggleSel={toggleSel}
                  onEdit={setEditIssue} onChange={refetchAll}
                />
              </>
            )}
            <div className="flex items-center gap-2 border-t border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
              <span>{d.cached_at ? `Cached ${fmtDate(d.cached_at.slice(0, 10))}` : "Not cached"}</span>
              {d.has_monitoring && (
                <Badge variant="secondary" className="text-[0.65rem]">
                  <Bookmark className="size-3" /> Selective ·{" "}
                  {(d.monitored_regular?.length ?? 0) + (d.monitored_annual?.length ?? 0)} monitored
                </Badge>
              )}
              <div className="ml-auto flex gap-3">
                <button className="hover:text-foreground"
                  onClick={() => action(() => monitorAll(seriesId), "All monitored", refetchIssues)}>
                  Monitor all
                </button>
                {d.has_monitoring && (
                  <button className="hover:text-foreground"
                    onClick={() => action(() => unmonitorAll(seriesId), "Cleared monitoring", refetchIssues)}>
                    Unmonitor all
                  </button>
                )}
              </div>
            </div>
          </>
        ) : (
          <p className="p-8 text-center text-sm text-muted-foreground">
            No issues found on Metron for this series.
          </p>
        )}
      </Card>

      {/* Bulk delete bar */}
      {selected.size > 0 && (
        <div className="fixed bottom-4 left-1/2 z-40 -translate-x-1/2 rounded-xl border border-border bg-background p-2.5 shadow-2xl">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold">{selected.size} selected</span>
            <Button variant="outline" size="sm" className="text-destructive"
              onClick={async () => {
                if (!(await confirm({ title: `Delete ${selected.size} local file(s)?`, description: "This removes the .cbz/.cbr from disk.", confirmText: "Delete", destructive: true }))) return
                bulkDeleteIssues(seriesId, selItems).then((r) => {
                  toast.success(`Deleted ${r.deleted} file(s)`)
                  r.errors?.slice(0, 3).forEach((e) => toast.error(e))
                  setSelected(new Set()); refetchAll()
                }).catch((e: Error) => toast.error(`Failed: ${e.message}`))
              }}>
              <Trash2 /> Delete local files
            </Button>
            <Button variant="link" size="sm" className="text-muted-foreground" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </div>
        </div>
      )}

      <MetadataSheet
        seriesId={seriesId}
        issueNum={editIssue}
        onClose={() => setEditIssue(null)}
        onSaved={refetchIssues}
      />
    </>
  )
}

const STATUS_BADGE: Record<Issue["status"], { cls: string; icon: typeof CheckCircle2; label: string }> = {
  downloaded: { cls: "bg-status-ended/15 text-status-ended", icon: CheckCircle2, label: "Downloaded" },
  missing: { cls: "bg-destructive/15 text-destructive", icon: XCircle, label: "Missing" },
  upcoming: { cls: "bg-primary/15 text-primary", icon: Clock, label: "Upcoming" },
  tba: { cls: "bg-muted text-muted-foreground", icon: HelpCircle, label: "TBA" },
}

function IssuesTable({
  issues, type, seriesId, isMonitored, selected, toggleSel, onEdit, onChange,
}: {
  issues: Issue[]
  type: IssueType
  seriesId: number
  isMonitored: (i: Issue, t: IssueType) => boolean
  selected: Set<string>
  toggleSel: (k: string) => void
  onEdit: (num: string) => void
  onChange: () => void
}) {
  const confirm = useConfirm()
  const act = (fn: () => Promise<unknown>, ok: string) =>
    fn().then(() => { toast.success(ok); onChange() }).catch((e: Error) => toast.error(`Failed: ${e.message}`))

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="w-8 py-2 pl-4" />
            <th className="w-9 py-2" />
            <th className="w-14 py-2">#</th>
            <th className="py-2">Title</th>
            <th className="w-32 py-2">Date</th>
            <th className="w-44 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((i) => {
            const mon = isMonitored(i, type)
            const key = `${type}:${i.number}`
            const badge = STATUS_BADGE[i.status]
            const Icon = badge.icon
            return (
              <tr key={key} className={cn("border-b border-border/50 hover:bg-accent/40", !mon && "opacity-50",
                selected.has(key) && "bg-status-continuing/10")}>
                <td className="py-1.5 pl-4">
                  {i.status === "downloaded" && (
                    <Checkbox checked={selected.has(key)} onCheckedChange={() => toggleSel(key)}
                      aria-label={`Select #${i.number}`} />
                  )}
                </td>
                <td className="py-1.5">
                  <button title={mon ? "Monitored — click to unmonitor" : "Not monitored — click to monitor"}
                    className={cn("transition-colors hover:text-foreground", mon ? "text-status-missing-unmonitored" : "text-muted-foreground")}
                    onClick={() => act(() => toggleIssueMonitor(seriesId, i.number, type), "Monitoring updated")}>
                    <Bookmark className={cn("size-4", mon && "fill-current")} />
                  </button>
                </td>
                <td className="py-1.5 font-medium text-muted-foreground">{i.number}</td>
                <td className="py-1.5">{i.title || "—"}</td>
                <td className="py-1.5 text-muted-foreground">{fmtDate(i.date)}</td>
                <td className="py-1.5">
                  {i.status === "missing" ? (
                    <button className={cn("inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs", badge.cls)}
                      onClick={async () => { if (await confirm({ title: `Download #${i.number}?`, confirmText: "Download" })) act(() => downloadIssue(seriesId, i.number), "Queued") }}>
                      <Icon className="size-3.5" /> {badge.label} <Download className="size-3" />
                    </button>
                  ) : (
                    <span className={cn("inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs", badge.cls)}>
                      <Icon className="size-3.5" /> {badge.label}
                    </span>
                  )}
                  {i.status === "downloaded" && (
                    <span className="ml-2 inline-flex gap-1">
                      <button className="text-muted-foreground hover:text-foreground" title="Edit metadata"
                        onClick={() => onEdit(String(i.number))}>
                        <Pencil className="size-3.5" />
                      </button>
                      <button className="text-muted-foreground hover:text-destructive" title="Delete local file"
                        onClick={async () => { if (await confirm({ title: `Delete local file for #${i.number}?`, confirmText: "Delete", destructive: true })) act(() => deleteIssue(seriesId, i.number, type), "Deleted") }}>
                        <Trash2 className="size-3.5" />
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function RenamePanel({ seriesId, onClose, afterApply }: { seriesId: number; onClose: () => void; afterApply: () => void }) {
  const qc = useQueryClient()
  const preview = useQuery({ queryKey: ["rename-preview", seriesId], queryFn: () => getRenamePreview(seriesId) })
  const apply = useMutation({
    mutationFn: () => applyRename(seriesId, preview.data!.changed),
    onSuccess: (r) => {
      toast.success(`${r.renamed} renamed${r.errors ? `, ${r.errors} error(s)` : ""}`)
      qc.invalidateQueries({ queryKey: ["rename-preview", seriesId] })
      afterApply()
    },
    onError: (e: Error) => toast.error(`Failed: ${e.message}`),
  })
  const p = preview.data
  return (
    <Card className="mb-4 p-4">
      <div className="mb-2 flex items-center">
        <span className="text-sm font-semibold">Preview Rename</span>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onClose}><XCircle /></Button>
      </div>
      {preview.isLoading ? (
        <p className="text-sm text-muted-foreground">Scanning…</p>
      ) : !p ? (
        <p className="text-sm text-destructive">Failed to load preview.</p>
      ) : (
        <>
          <p className="mb-2 text-sm text-muted-foreground">
            {p.changed.length} to rename · {p.correct_count} already correct · {p.unparseable.length} unparseable
          </p>
          {p.changed.length > 0 && (
            <ul className="mb-3 max-h-60 space-y-1 overflow-y-auto text-xs">
              {p.changed.map((c) => (
                <li key={c.current} className="rounded bg-muted/40 px-2 py-1">
                  <span className="text-muted-foreground line-through">{c.current}</span>
                  {" → "}
                  <span className="text-status-ended">{c.expected}</span>
                </li>
              ))}
            </ul>
          )}
          <Button size="sm" disabled={!p.changed.length || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? <Loader2 className="animate-spin" /> : <Files />} Apply {p.changed.length} rename(s)
          </Button>
        </>
      )}
    </Card>
  )
}
