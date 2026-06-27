import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Book, Download, RefreshCw, Rss } from "lucide-react"
import { downloadIssue, getReleases } from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""

export function Releases() {
  const qc = useQueryClient()
  const { data, isLoading, isFetching } = useQuery({ queryKey: ["releases"], queryFn: getReleases })
  const [queued, setQueued] = useState<Set<string>>(new Set())

  const matches = data?.matches ?? []
  const feedSize = data?.feed_size ?? 0

  const dl = (seriesId: number, num: string, url: string) => {
    const key = `${seriesId}:${num}`
    downloadIssue(seriesId, num, url)
      .then(() => { setQueued((p) => new Set(p).add(key)); toast.success("Queued for download") })
      .catch((e) => toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`))
  }

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">Releases</h1>
          <p className="text-sm text-muted-foreground">
            Latest posts on getcomics.org that match your monitored series. New matches are
            downloaded automatically — use Download here to grab one immediately.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">
            {matches.length > 0
              ? `${matches.length} match${matches.length !== 1 ? "es" : ""} from ${feedSize} posts`
              : `${feedSize} posts checked`}
          </span>
          <Button variant="outline" size="sm"
            onClick={() => qc.invalidateQueries({ queryKey: ["releases"] })} disabled={isFetching}>
            <RefreshCw className={isFetching ? "animate-spin" : ""} /> Refresh
          </Button>
        </div>
      </div>

      {data?.error && (
        <div className="mb-3 rounded-md bg-destructive/15 p-3 text-sm text-destructive">
          Erro ao obter o feed: {data.error}
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : matches.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground">
          <Rss className="mx-auto mb-2 size-7 opacity-50" />
          <p className="text-sm">Nothing from your monitored series in the latest feed.</p>
          <p className="text-sm">The feed shows getcomics.org's last ~10 posts — they don't have to be yours.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {matches.map((m) => {
            const key = `${m.series_id}:${m.issue_number}`
            const isQueued = m.queued || queued.has(key)
            return (
              <Card key={`${key}-${m.url}`} className="flex flex-row items-center gap-4 p-2.5">
                {m.cover_image_url ? (
                  <img src={m.cover_image_url} alt="" loading="lazy"
                    className="h-[66px] w-11 shrink-0 rounded object-cover" />
                ) : (
                  <div className="flex h-[66px] w-11 shrink-0 items-center justify-center rounded bg-status-continuing/10 text-status-continuing">
                    <Book className="size-4" />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold">
                    <Link to={`/series/${m.series_id}`} className="hover:underline">{m.series_name}</Link>
                    <span className="ml-1 text-muted-foreground">#{m.issue_number}</span>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    <a href={m.url} target="_blank" rel="noopener noreferrer" className="hover:text-foreground">
                      {m.title}
                    </a>
                    {m.pub_date && ` · ${fmtDate(m.pub_date)}`}
                  </div>
                </div>
                <div className="shrink-0">
                  {m.downloaded ? (
                    <Badge className="bg-status-ended/15 text-status-ended">Downloaded</Badge>
                  ) : isQueued ? (
                    <Badge variant="secondary">Queued</Badge>
                  ) : (
                    <Button size="sm" onClick={() => dl(m.series_id, m.issue_number, m.url)}>
                      <Download /> Download
                    </Button>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </>
  )
}
