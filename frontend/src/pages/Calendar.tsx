import { Link, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { getCalendar, type CalEventStatus } from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
const BORDER: Record<CalEventStatus, string> = {
  downloaded: "border-l-status-ended",
  today: "border-l-status-missing-unmonitored",
  missing: "border-l-status-missing-monitored",
  upcoming: "border-l-status-continuing",
}
const DOT: Record<CalEventStatus, string> = {
  downloaded: "bg-status-ended",
  today: "bg-status-missing-unmonitored",
  missing: "bg-status-missing-monitored",
  upcoming: "bg-status-continuing",
}
const LEGEND: [CalEventStatus, string][] = [
  ["downloaded", "Downloaded"], ["today", "Today"], ["missing", "Missing"], ["upcoming", "Upcoming"],
]

export function Calendar() {
  const [params, setParams] = useSearchParams()
  const view = (params.get("view") === "week" ? "week" : "month") as "month" | "week"
  const date = params.get("date") ?? ""

  const { data, isLoading } = useQuery({
    queryKey: ["calendar", view, date],
    queryFn: () => getCalendar(view, date),
  })

  const go = (next: { view?: "month" | "week"; date?: string }) => {
    const p = new URLSearchParams(params)
    p.set("view", next.view ?? view)
    if (next.date !== undefined) p.set("date", next.date)
    setParams(p, { replace: true })
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-bold">Calendar</h1>
          <p className="text-sm text-muted-foreground">In-store release dates (Metron)</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex">
            <Button variant="outline" size="icon" aria-label="Previous" className="rounded-r-none"
              onClick={() => data && go({ date: data.prev_ref })}>
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="sm" className="rounded-none border-x-0"
              onClick={() => data && go({ date: data.today_iso })}>Today</Button>
            <Button variant="outline" size="icon" aria-label="Next" className="rounded-l-none"
              onClick={() => data && go({ date: data.next_ref })}>
              <ChevronRight />
            </Button>
          </div>
          <span className="mx-2 text-sm font-semibold whitespace-nowrap">{data?.header_label}</span>
          <div className="flex">
            <Button variant={view === "month" ? "default" : "outline"} size="sm" className="rounded-r-none"
              onClick={() => data && go({ view: "month", date: data.current_ref })}>Month</Button>
            <Button variant={view === "week" ? "default" : "outline"} size="sm" className="rounded-l-none"
              onClick={() => data && go({ view: "week", date: data.current_ref })}>Week</Button>
          </div>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap gap-3 text-sm text-muted-foreground">
        {LEGEND.map(([st, label]) => (
          <span key={st} className="flex items-center gap-1.5">
            <span className={cn("inline-block size-2 rounded-[2px]", DOT[st])} /> {label}
          </span>
        ))}
      </div>

      <Card className="overflow-hidden p-0">
        {isLoading || !data ? (
          <p className="p-8 text-center text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="grid grid-cols-7 gap-px bg-border">
            {DOW.map((d) => (
              <div key={d} className="bg-card px-2.5 py-2 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
                {d}
              </div>
            ))}
            {data.weeks.flat().map((day) => (
              <div key={day.iso} className={cn(
                "flex flex-col gap-0.5 bg-card p-1.5",
                view === "week" ? "min-h-[520px]" : "min-h-[110px]",
                !day.in_view_month && "opacity-45",
                day.is_today && "bg-status-continuing/10 shadow-[inset_0_0_0_1px_var(--status-continuing)]",
              )}>
                <div className="mb-0.5 flex items-center gap-1.5">
                  <span className={cn("text-xs font-semibold text-muted-foreground", day.is_today && "text-status-continuing")}>
                    {day.day}
                  </span>
                  {day.events.length > 0 && (
                    <span className="ml-auto text-[0.62rem] text-muted-foreground">{day.events.length}</span>
                  )}
                </div>
                {day.events.map((ev, i) => (
                  <Link key={i} to={`/series/${ev.series_id}`}
                    title={`${ev.series_name} #${ev.issue_number}${ev.issue_name ? ` — ${ev.issue_name}` : ""}`}
                    className={cn("block overflow-hidden rounded border-l-[3px] bg-accent/40 px-1.5 py-0.5 text-[0.7rem] leading-tight hover:bg-accent", BORDER[ev.status])}>
                    <span className="block truncate font-semibold">{ev.series_name}</span>
                    {ev.issue_name && <span className="block truncate opacity-70">{ev.issue_name}</span>}
                    <span className="text-[0.62rem] opacity-65">#{ev.issue_number}</span>
                  </Link>
                ))}
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  )
}
