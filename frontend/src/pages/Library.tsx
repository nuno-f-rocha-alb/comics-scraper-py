import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-react"
import { getLibraryStatus, startLibraryScan } from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"

const Sep = () => <div className="hidden h-5 w-px bg-border sm:block" />

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) : null

export function Library() {
  const qc = useQueryClient()
  const [force, setForce] = useState(false)

  const status = useQuery({
    queryKey: ["library-status"],
    queryFn: getLibraryStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false),
  })
  const s = status.data

  const scan = () =>
    startLibraryScan(force)
      .then((r) => {
        toast.success(r.started ? "Scan started" : "A scan is already running")
        qc.setQueryData(["library-status"], r)
        qc.invalidateQueries({ queryKey: ["library-status"] })
      })
      .catch((e) => toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`))

  return (
    <>
      <h1 className="mb-4 text-xl font-bold">Library</h1>

      <Card className="mb-4 overflow-hidden">
        <div className="border-b border-border px-4 py-3 font-semibold">Scan Status</div>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          {s?.running ? (
            <>
              <span className="inline-flex items-center gap-1.5 rounded bg-primary/15 px-2.5 py-1 text-sm text-primary">
                <Loader2 className="size-3.5 animate-spin" /> Running…
              </span>
              {s.progress.total > 0 && (
                <span className="text-sm text-muted-foreground">
                  {s.progress.current || "…"}{" "}
                  <span className="font-medium text-foreground">
                    ({s.progress.done}/{s.progress.total})
                  </span>
                </span>
              )}
            </>
          ) : (
            <>
              <span className="inline-flex items-center gap-1.5 rounded bg-status-ended/15 px-2.5 py-1 text-sm text-status-ended">
                <CheckCircle2 className="size-3.5" /> Idle
              </span>
              <Sep />
              <div className="text-sm text-muted-foreground">
                <span className="font-medium text-foreground">Last scan:</span>{" "}
                {s?.last_scan_at ? (
                  <>
                    {fmtDate(s.last_scan_at)}
                    {s.last_scan_error && (
                      <span className="ml-1 text-destructive" title={s.last_scan_error}>
                        <AlertTriangle className="inline size-3.5" /> Error
                      </span>
                    )}
                  </>
                ) : (
                  <span className="opacity-50">never</span>
                )}
              </div>
            </>
          )}
        </div>
      </Card>

      <Card>
        <div className="border-b border-border px-4 py-3 font-semibold">Scan &amp; Retag</div>
        <div className="p-6">
          <p className="mb-3 text-sm text-muted-foreground">
            Scans all series folders and tags any CBZ/CBR files that are missing metadata. Use{" "}
            <strong>Force retag</strong> to overwrite existing tags.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Checkbox checked={force} onCheckedChange={(v) => setForce(v === true)} />
              Force retag (overwrite existing)
            </label>
            <Button onClick={scan} disabled={s?.running}>
              <RefreshCw /> Scan Library
            </Button>
          </div>
        </div>
      </Card>
    </>
  )
}
