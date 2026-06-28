import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowLeft, Download, RefreshCw, Trash2, UploadCloud } from "lucide-react"
import {
  getReadingListDetail, cblDownloadUrl, resyncReadingList, deleteReadingList,
  getKomgaStatus, pushReadingListToKomga, type RLItemStatus,
} from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useConfirm } from "@/components/confirm"

const STATUS: Record<RLItemStatus, { label: string; cls: string }> = {
  owned: { label: "Owned", cls: "bg-status-continuing/15 text-status-continuing" },
  monitored: { label: "Monitored", cls: "bg-primary/15 text-primary" },
  missing: { label: "Missing", cls: "bg-destructive/15 text-destructive" },
  untracked: { label: "Untracked", cls: "bg-muted text-muted-foreground" },
}

export function ReadingListDetail() {
  const { id } = useParams()
  const rlId = Number(id)
  const validId = Number.isFinite(rlId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()

  const { data, isLoading, isError } = useQuery({
    queryKey: ["reading-list", rlId],
    queryFn: () => getReadingListDetail(rlId),
    enabled: validId,
  })
  const komga = useQuery({ queryKey: ["komga-status"], queryFn: getKomgaStatus })

  const resync = useMutation({
    mutationFn: () => resyncReadingList(rlId),
    onSuccess: () => { toast.success("Re-synced from Metron"); qc.invalidateQueries({ queryKey: ["reading-list", rlId] }) },
    onError: (e: Error) => toast.error(`Failed: ${e.message}`),
  })
  const push = useMutation({
    mutationFn: () => pushReadingListToKomga(rlId),
    onSuccess: (r) => toast.success(`Komga: ${r.matched} matched${r.unmatched.length ? `, ${r.unmatched.length} unmatched` : ""}`),
    onError: (e: Error) => toast.error(`Komga push failed: ${e.message}`),
  })

  if (!validId) return <p className="text-sm text-destructive">Invalid reading list id.</p>
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (isError || !data) return <p className="text-sm text-destructive">Couldn't load this reading list.</p>
  const pct = data.total ? Math.round((data.owned / data.total) * 100) : 0

  return (
    <>
      <Link to="/reading-lists" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Reading Lists
      </Link>

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold">{data.name}</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {data.owned}/{data.total} owned ({pct}%)
              {data.list_type ? ` · ${data.list_type}` : ""}
              {data.attribution_source ? ` · ${data.attribution_source}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <a href={cblDownloadUrl(rlId)}>
              <Button variant="outline" size="sm"><Download className="size-4" /> Download CBL</Button>
            </a>
            {komga.data?.configured && (
              <Button variant="outline" size="sm" onClick={() => push.mutate()} disabled={push.isPending}>
                <UploadCloud className="size-4" /> {push.isPending ? "Pushing…" : "Push to Komga"}
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => resync.mutate()} disabled={resync.isPending}>
              <RefreshCw className="size-4" /> Re-sync
            </Button>
            <Button variant="outline" size="sm" className="text-destructive"
              onClick={async () => {
                if (await confirm({ title: `Remove "${data.name}"?`, description: "Series, files and monitoring are left untouched.", confirmText: "Remove", destructive: true }))
                  deleteReadingList(rlId).then(() => { toast.success("Removed"); nav("/reading-lists") }).catch((e: Error) => toast.error(e.message))
              }}>
              <Trash2 className="size-4" /> Remove
            </Button>
          </div>
        </div>
        <div className="mt-3 h-1.5 w-full rounded-full bg-muted">
          <div className="h-1.5 rounded-full bg-primary" style={{ width: `${pct}%` }} />
        </div>
      </Card>

      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase text-muted-foreground">
              <th className="px-3 py-2 w-10">#</th>
              <th className="px-3 py-2">Series</th>
              <th className="px-3 py-2 w-16">Issue</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2 w-24">Year</th>
              <th className="px-3 py-2 w-28">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((it) => {
              const st = STATUS[it.status]
              return (
                <tr key={it.order} className="border-b border-border/50 last:border-0">
                  <td className="px-3 py-1.5 text-muted-foreground">{it.order}</td>
                  <td className="px-3 py-1.5">
                    {it.series_id
                      ? <Link to={`/series/${it.series_id}`} className="hover:underline">{it.series_name}</Link>
                      : it.series_name}
                  </td>
                  <td className="px-3 py-1.5 text-muted-foreground">#{it.number}</td>
                  <td className="px-3 py-1.5 text-muted-foreground">{it.issue_type || "—"}</td>
                  <td className="px-3 py-1.5 text-muted-foreground">{it.cover_year ?? "—"}</td>
                  <td className="px-3 py-1.5">
                    <span className={cn("inline-flex items-center rounded px-2 py-0.5 text-xs", st.cls)}>{st.label}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>
    </>
  )
}
