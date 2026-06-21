import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, Check, CheckCircle2, ExternalLink, Loader2, Play } from "lucide-react"
import { getSchedulerStatus, runSchedulerNow, saveSchedule } from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) : null

export function Scheduler() {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ["scheduler-status"],
    queryFn: getSchedulerStatus,
    refetchInterval: 3000,
  })
  const s = status.data

  const [mode, setMode] = useState<"interval" | "cron">("interval")
  const [interval, setIntervalVal] = useState("24")
  const [cron, setCron] = useState("0 3 * * *")
  // Seed form once from server config.
  const [seeded, setSeeded] = useState(false)
  useEffect(() => {
    if (s && !seeded) {
      setMode(s.mode)
      if (s.mode === "interval") setIntervalVal(s.value)
      else setCron(s.value)
      setSeeded(true)
    }
  }, [s, seeded])

  const run = () =>
    runSchedulerNow()
      .then((r) => {
        toast.success(r.started ? "Scrape triggered" : "Already running")
        qc.setQueryData(["scheduler-status"], r)
      })
      .catch((e) => toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`))

  const save = () =>
    saveSchedule(mode, mode === "interval" ? interval : cron)
      .then((r) => { toast.success("Schedule updated"); qc.setQueryData(["scheduler-status"], r) })
      .catch((e) => toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`))

  return (
    <>
      <h1 className="mb-4 text-xl font-bold">Scheduler</h1>

      {/* Status */}
      <Card className="mb-4 overflow-hidden">
        <div className="border-b border-border px-4 py-3 font-semibold">Status</div>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          {s?.running ? (
            <span className="inline-flex items-center gap-1.5 rounded bg-primary/15 px-2.5 py-1 text-sm text-primary">
              <Loader2 className="size-3.5 animate-spin" /> Running…
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded bg-status-ended/15 px-2.5 py-1 text-sm text-status-ended">
              <CheckCircle2 className="size-3.5" /> Idle
            </span>
          )}
          <Sep />
          <div className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Last run:</span>{" "}
            {s?.last_run_at ? (
              <>
                {fmtDate(s.last_run_at)}
                {s.last_run_error && (
                  <span className="ml-1 text-destructive" title={s.last_run_error}>
                    <AlertTriangle className="inline size-3.5" /> Error
                  </span>
                )}
              </>
            ) : <span className="opacity-50">never</span>}
          </div>
          <Sep />
          <div className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Next run:</span>{" "}
            {s?.next_run_at ? fmtDate(s.next_run_at) : <span className="opacity-50">—</span>}
          </div>
          <Button size="sm" className="ml-auto" onClick={run} disabled={s?.running}>
            <Play /> Run Now
          </Button>
        </div>
      </Card>

      {/* Config */}
      <Card>
        <div className="border-b border-border px-4 py-3 font-semibold">Schedule</div>
        <div className="grid grid-cols-1 items-end gap-4 p-6 md:grid-cols-3">
          <div>
            <label className="text-sm font-medium" htmlFor="sched-mode">Mode</label>
            <Select value={mode} onValueChange={(v) => setMode(v as "interval" | "cron")}>
              <SelectTrigger id="sched-mode" className="mt-1.5"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="interval">Every N hours</SelectItem>
                <SelectItem value="cron">Cron expression</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {mode === "interval" ? (
            <div>
              <label className="text-sm font-medium" htmlFor="sched-interval">Hours between runs</label>
              <div className="mt-1.5 flex items-center gap-2">
                <Input id="sched-interval" type="number" min={1} max={168} value={interval}
                  onChange={(e) => setIntervalVal(e.target.value)} />
                <span className="text-sm text-muted-foreground">h</span>
              </div>
            </div>
          ) : (
            <div>
              <label className="text-sm font-medium" htmlFor="sched-cron">
                Cron expression{" "}
                <a href="https://crontab.guru/" target="_blank" rel="noopener noreferrer"
                  className="ml-1 text-xs text-muted-foreground hover:underline">
                  <ExternalLink className="inline size-3" /> crontab.guru
                </a>
              </label>
              <Input id="sched-cron" className="mt-1.5 font-mono" placeholder="0 3 * * *" value={cron}
                onChange={(e) => setCron(e.target.value)} />
              <p className="mt-1 text-xs text-muted-foreground">
                e.g. <code>0 3 * * *</code> = every day at 03:00
              </p>
            </div>
          )}

          <Button onClick={save} className="w-full md:w-auto">
            <Check /> Save Schedule
          </Button>
        </div>
      </Card>
    </>
  )
}

const Sep = () => <div className="hidden h-5 w-px bg-border sm:block" />
