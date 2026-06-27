import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  AlertCircle,
  ArrowDownCircle,
  Check,
  Clock,
  Hand,
  Hourglass,
  Inbox,
  Ban,
  Trash2,
  X,
  XCircle,
} from "lucide-react"
import {
  cancelDownload,
  clearDownloads,
  deleteDownload,
  getActiveDownloads,
  getDownloads,
  type DownloadJob,
  type JobStatus,
} from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { useConfirm } from "@/components/confirm"
import { cn } from "@/lib/utils"

const fmtBytes = (n: number | null | undefined) => {
  if (!n || n <= 0) return "–"
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`
  return `${(n / 1073741824).toFixed(2)} GB`
}
const fmtEta = (s: number | null) => {
  if (!s || s <= 0) return "–"
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}
const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"

const STATUS: Record<JobStatus, { cls: string; icon: typeof Check; label: string }> = {
  done: { cls: "bg-status-ended/15 text-status-ended", icon: Check, label: "Done" },
  failed: { cls: "bg-destructive/15 text-destructive", icon: XCircle, label: "Failed" },
  cancelled: { cls: "bg-muted text-muted-foreground", icon: Ban, label: "Cancelled" },
  downloading: { cls: "bg-primary/15 text-primary", icon: ArrowDownCircle, label: "Downloading" },
  queued: { cls: "bg-status-missing-unmonitored/15 text-status-missing-unmonitored", icon: Hourglass, label: "Queued" },
}
type Filter = "all" | "scraper" | "manual" | "failed"

export function Downloads() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [filter, setFilter] = useState<Filter>("all")

  const active = useQuery({
    queryKey: ["downloads-active"],
    queryFn: getActiveDownloads,
    refetchInterval: 3000,
  })
  const history = useQuery({ queryKey: ["downloads"], queryFn: getDownloads })

  const refetch = () => {
    qc.invalidateQueries({ queryKey: ["downloads"] })
    qc.invalidateQueries({ queryKey: ["downloads-active"] })
  }
  const act = (fn: () => Promise<unknown>, ok: string) =>
    fn().then(() => { toast.success(ok); refetch() })
      .catch((e) => toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`))

  const jobs = history.data?.jobs ?? []
  const visible = jobs.filter((j) =>
    filter === "all" ? true : filter === "failed" ? j.status === "failed" : j.source === filter)
  const activeJobs = active.data?.jobs ?? []

  return (
    <>
      <h1 className="mb-4 text-xl font-bold">Downloads</h1>

      {/* Active */}
      {activeJobs.length > 0 && (
        <Card className="mb-3 overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <span className="font-semibold">Active</span>
            <Badge className="bg-primary/15 text-primary">{activeJobs.length}</Badge>
          </div>
          <div className="divide-y divide-border/50">
            {activeJobs.map((j) => {
              const p = j.progress
              const pct = p && p.total ? Math.round((p.bytes / p.total) * 1000) / 10 : 0
              const eta = p && p.rate_bps > 0 && p.total ? (p.total - p.bytes) / p.rate_bps : null
              return (
                <div key={j.id} className="flex items-center gap-4 px-4 py-2">
                  <div className="w-44 shrink-0 truncate text-sm font-semibold">
                    {j.series_name ?? `#${j.series_id}`}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-muted-foreground">{j.search_term}</div>
                    {j.status === "downloading" && p && (
                      <>
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                          <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
                        </div>
                        <div className="mt-1 flex gap-3 text-xs text-muted-foreground">
                          <span>{fmtBytes(p.bytes)}{p.total ? ` / ${fmtBytes(p.total)}` : ""}</span>
                          <span>{pct}%</span>
                          {p.rate_bps > 0 && <span>{fmtBytes(p.rate_bps)}/s</span>}
                          {eta && <span>ETA {fmtEta(eta)}</span>}
                        </div>
                      </>
                    )}
                  </div>
                  <StatusBadge status={j.status} />
                  <Button variant="ghost" size="sm" className="text-destructive"
                    onClick={async () => { if (await confirm({ title: "Cancel this download?", confirmText: "Cancel download", cancelText: "Keep", destructive: true })) act(() => cancelDownload(j.id), "Cancelling…") }}>
                    <XCircle /> Cancel
                  </Button>
                </div>
              )
            })}
          </div>
        </Card>
      )}

      {/* History */}
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <span className="font-semibold">History</span>
          <Badge variant="secondary">{jobs.length}</Badge>
          <div className="ml-3 flex gap-1">
            {(["all", "scraper", "manual", "failed"] as Filter[]).map((f) => (
              <Button key={f} size="sm" variant={filter === f ? "default" : "outline"}
                className="h-7 px-2.5 text-xs capitalize" onClick={() => setFilter(f)}>
                {f}
              </Button>
            ))}
          </div>
          {jobs.length > 0 && (
            <Button size="sm" variant="outline" className="ml-auto text-destructive"
              onClick={async () => { if (await confirm({ title: "Clear all completed and failed jobs?", confirmText: "Clear all", destructive: true })) act(clearDownloads, "Cleared") }}>
              <Trash2 /> Clear All
            </Button>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="w-48 py-2 pl-4">Series</th>
                <th className="w-14 py-2">#</th>
                <th className="py-2">Search Term / Error</th>
                <th className="w-24 py-2">Source</th>
                <th className="w-28 py-2">Status</th>
                <th className="w-32 py-2">Date</th>
                <th className="w-10 py-2" />
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr><td colSpan={7} className="py-12 text-center text-muted-foreground">
                  <Inbox className="mx-auto mb-2 size-7 opacity-50" />No downloads yet.
                </td></tr>
              ) : visible.map((j) => (
                <tr key={j.id} className="border-b border-border/50 hover:bg-accent/40">
                  <td className="py-1.5 pl-4 font-semibold">
                    {j.series_name ? (
                      <Link to={`/series/${j.series_id}`} className="hover:underline">{j.series_name}</Link>
                    ) : <span className="text-muted-foreground">#{j.series_id}</span>}
                  </td>
                  <td className="py-1.5 font-medium">#{j.issue_number}</td>
                  <td className="py-1.5 break-all">
                    {j.status === "failed" && j.error ? (
                      <span className="text-destructive"><AlertCircle className="mr-1 inline size-3.5" />{j.error}</span>
                    ) : (
                      <>
                        <span className="text-muted-foreground">{j.search_term}</span>
                        {j.filename && <div className="text-xs text-muted-foreground/70">{j.filename}</div>}
                      </>
                    )}
                  </td>
                  <td className="py-1.5">
                    <Badge variant="secondary" className="text-[0.65rem]">
                      {j.source === "scraper" ? <Clock className="size-3" /> : <Hand className="size-3" />}
                      {j.source === "scraper" ? "Scraper" : "Manual"}
                    </Badge>
                  </td>
                  <td className="py-1.5"><StatusBadge status={j.status} /></td>
                  <td className="py-1.5 text-muted-foreground">{fmtDate(j.created_at)}</td>
                  <td className="py-1.5 pr-3 text-right">
                    {["done", "failed", "cancelled"].includes(j.status) && (
                      <button className="text-muted-foreground hover:text-foreground"
                        onClick={() => act(() => deleteDownload(j.id), "Removed")} title="Remove">
                        <X className="size-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}

function StatusBadge({ status }: { status: JobStatus }) {
  const st = STATUS[status]
  const Icon = st.icon
  return (
    <span className={cn("inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs", st.cls)}>
      <Icon className="size-3.5" /> {st.label}
    </span>
  )
}

export type { DownloadJob }
