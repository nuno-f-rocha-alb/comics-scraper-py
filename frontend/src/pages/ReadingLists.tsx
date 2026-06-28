import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Search, Star, Plus, Sparkles, RotateCw } from "lucide-react"
import {
  searchReadingLists, previewReadingList, addReadingList, getReadingLists,
  getSuggestions, scanSuggestions, getSuggestStatus, getSuggestSettings, putSuggestThreshold,
  type RLSearchResult, type RLPreview, type Suggestion,
} from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { CoverCard, COVER_GRID } from "@/components/CoverCard"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription, SheetFooter,
} from "@/components/ui/sheet"

const LIST_TYPES = ["", "EVENT", "STORY", "CHARACTERS", "TEAMS", "MASTER"]
const SOURCES = ["", "CBRO", "CMRO", "CBH", "CBT", "MG", "HTLC", "LOCG", "OTHER"]
const ISSUE_PREVIEW_LIMIT = 8  // issues shown under each type box before "+N more"

export function ReadingLists() {
  const qc = useQueryClient()
  const [filters, setFilters] = useState({ name: "", publisher: "", list_type: "", attribution_source: "", average_rating__gte: "" })
  const [submitted, setSubmitted] = useState<Record<string, string> | null>(null)
  const [preview, setPreview] = useState<number | null>(null)

  const mine = useQuery({ queryKey: ["reading-lists"], queryFn: getReadingLists })
  const search = useQuery({
    queryKey: ["rl-search", submitted],
    queryFn: () => searchReadingLists(submitted!),
    enabled: !!submitted,
  })

  return (
    <>
      <div className="mb-3">
        <h1 className="text-xl font-bold">Reading Lists</h1>
        <p className="text-sm text-muted-foreground">
          Search Metron reading lists and add one — it pulls in the series and monitors only the issues the list needs.
        </p>
      </div>

      {/* Search */}
      <Card className="mb-5 p-4">
        <form
          className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-6"
          onSubmit={(e) => { e.preventDefault(); setSubmitted({ ...filters }) }}
        >
          <Input placeholder="Name" value={filters.name}
            onChange={(e) => setFilters({ ...filters, name: e.target.value })} className="lg:col-span-2" />
          <Input placeholder="Publisher" value={filters.publisher}
            onChange={(e) => setFilters({ ...filters, publisher: e.target.value })} />
          <select className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            value={filters.list_type} onChange={(e) => setFilters({ ...filters, list_type: e.target.value })}>
            {LIST_TYPES.map((t) => <option key={t} value={t}>{t || "Any type"}</option>)}
          </select>
          <select className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            value={filters.attribution_source} onChange={(e) => setFilters({ ...filters, attribution_source: e.target.value })}>
            {SOURCES.map((t) => <option key={t} value={t}>{t || "Any source"}</option>)}
          </select>
          <Button type="submit"><Search className="size-4" /> Search</Button>
        </form>
      </Card>

      {search.isFetching && <p className="text-sm text-muted-foreground">Searching…</p>}
      {search.data && search.data.results.length === 0 && (
        <p className="text-sm text-muted-foreground">No reading lists found.</p>
      )}
      {!!search.data?.results.length && (
        <div className={`mb-6 ${COVER_GRID}`}>
          {search.data.results.map((r) => <SearchCard key={r.id} r={r} onAdd={() => setPreview(r.id)} />)}
        </div>
      )}

      {/* Suggested for you */}
      <SuggestSection onAdd={(metronId) => setPreview(metronId)} />

      {/* Your lists */}
      <h2 className="mb-2 mt-6 text-sm font-bold uppercase tracking-wide text-muted-foreground">Your reading lists</h2>
      {mine.data && mine.data.reading_lists.length === 0 && (
        <p className="text-sm text-muted-foreground">None yet — search above and add one.</p>
      )}
      <div className={COVER_GRID}>
        {mine.data?.reading_lists.map((rl) => {
          const pct = rl.total ? Math.round((rl.owned / rl.total) * 100) : 0
          return (
            <CoverCard key={rl.id} to={`/reading-lists/${rl.id}`} image={rl.image_url} alt={rl.name}
              topRight={rl.average_rating != null
                ? <Badge variant="secondary" className="gap-0.5 text-[0.6rem]"><Star className="size-2.5" /> {rl.average_rating}</Badge>
                : undefined}
              bottomRight={<Badge variant="secondary" className="text-[0.6rem]">{rl.total} issues</Badge>}>
              <div className="flex items-center gap-1.5">
                {rl.list_type && <Badge variant="secondary" className="text-[0.6rem]">{rl.list_type}</Badge>}
                {rl.attribution_source && <Badge variant="outline" className="text-[0.6rem]">{rl.attribution_source}</Badge>}
              </div>
              <div className="mt-1 line-clamp-2 text-sm font-semibold leading-snug">{rl.name}</div>
              <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
              </div>
              <div className="mt-1 text-xs text-muted-foreground">{rl.owned}/{rl.total} owned</div>
            </CoverCard>
          )
        })}
      </div>

      {preview !== null && (
        <PreviewSheet
          metronId={preview}
          onClose={() => setPreview(null)}
          onAdded={() => {
            setPreview(null)
            qc.invalidateQueries({ queryKey: ["reading-lists"] })
            qc.invalidateQueries({ queryKey: ["rl-suggestions"] })  // added list drops out of suggestions
          }}
        />
      )}
    </>
  )
}

function SearchCard({ r, onAdd }: { r: RLSearchResult; onAdd: () => void }) {
  return (
    <CoverCard image={r.image} alt={r.name}
      topRight={r.average_rating != null
        ? <Badge variant="secondary" className="gap-0.5 text-[0.6rem]"><Star className="size-2.5" /> {r.average_rating}</Badge>
        : undefined}>
      <div className="line-clamp-2 text-sm font-semibold leading-snug">{r.name}</div>
      <div className="mt-0.5 flex flex-wrap gap-1">
        {r.list_type && <Badge variant="secondary" className="text-[0.6rem]">{r.list_type}</Badge>}
        {r.attribution_source && <Badge variant="outline" className="text-[0.6rem]">{r.attribution_source}</Badge>}
      </div>
      <Button size="sm" className="mt-2 w-full" onClick={onAdd}><Plus className="size-4" /> Add</Button>
    </CoverCard>
  )
}

function SuggestSection({ onAdd }: { onAdd: (metronId: number) => void }) {
  const qc = useQueryClient()
  const suggestions = useQuery({ queryKey: ["rl-suggestions"], queryFn: getSuggestions })
  const settings = useQuery({ queryKey: ["rl-suggest-settings"], queryFn: getSuggestSettings })
  const status = useQuery({
    queryKey: ["rl-suggest-status"],
    queryFn: getSuggestStatus,
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  })
  const running = !!status.data?.running

  // Refresh the suggestion list when a scan transitions running → done.
  const prevRunning = useRef(false)
  useEffect(() => {
    if (prevRunning.current && !running) {
      qc.invalidateQueries({ queryKey: ["rl-suggestions"] })
      toast.success(`Scan done — ${status.data?.last_result.kept ?? 0} suggestions`)
    }
    prevRunning.current = running
  }, [running, qc, status.data?.last_result.kept])

  const scan = useMutation({
    mutationFn: scanSuggestions,
    onSuccess: (r) => {
      if (!r.started) toast.message("Scan already running")
      prevRunning.current = true  // so a fast scan that finishes before we poll still triggers the refresh
      qc.invalidateQueries({ queryKey: ["rl-suggest-status"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const setThreshold = useMutation({
    mutationFn: putSuggestThreshold,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["rl-suggestions"] })
      qc.invalidateQueries({ queryKey: ["rl-suggest-settings"] })
    },
  })

  const list = suggestions.data?.suggestions ?? []
  return (
    <div className="mt-2">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Suggested for you</h2>
        <span className="text-xs text-muted-foreground">own ≥</span>
        <input type="number" min={1} max={100} key={settings.data?.threshold}
          className="h-7 w-16 rounded-md border border-input bg-background px-2 text-sm"
          defaultValue={settings.data?.threshold ?? 50}
          onBlur={(e) => {
            const parsed = Number(e.currentTarget.value)
            if (!e.currentTarget.value || !Number.isFinite(parsed)) {
              e.currentTarget.value = String(settings.data?.threshold ?? 50)
              return
            }
            const v = Math.min(100, Math.max(1, parsed))
            e.currentTarget.value = String(v)
            setThreshold.mutate(v)
          }} />
        <span className="text-xs text-muted-foreground">%</span>
        <Button size="sm" variant="outline" className="ml-auto" onClick={() => scan.mutate()} disabled={running}>
          {running ? <RotateCw className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          {running ? `Scanning… ${status.data?.progress.done ?? 0}/${status.data?.progress.total ?? 0}` : "Find suggestions"}
        </Button>
      </div>
      {list.length === 0 && !running && (
        <p className="text-sm text-muted-foreground">
          No suggestions yet — click "Find suggestions" to scan Metron for lists you already own a chunk of.
        </p>
      )}
      <div className={COVER_GRID}>
        {list.map((s) => <SuggestCard key={s.metron_id} s={s} onAdd={() => onAdd(s.metron_id)} />)}
      </div>
    </div>
  )
}

function SuggestCard({ s, onAdd }: { s: Suggestion; onAdd: () => void }) {
  return (
    <CoverCard image={s.image_url} alt={s.name}
      topRight={<Badge variant="secondary" className="text-[0.6rem]">{s.coverage}% owned</Badge>}>
      <div className="line-clamp-2 text-sm font-semibold leading-snug">{s.name}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">
        {s.owned}/{s.total}{s.attribution_source ? ` · ${s.attribution_source}` : ""}
      </div>
      <Button size="sm" className="mt-2 w-full" onClick={onAdd}><Plus className="size-4" /> Add</Button>
    </CoverCard>
  )
}

function PreviewSheet({ metronId, onClose, onAdded }: { metronId: number; onClose: () => void; onAdded: () => void }) {
  const { data, isLoading, isError } = useQuery<RLPreview>({
    queryKey: ["rl-preview", metronId],
    queryFn: () => previewReadingList(metronId),
  })
  const [types, setTypes] = useState<Set<string> | null>(null)  // null until loaded → defaults to all

  const add = useMutation({
    // null = no interaction → monitor all; an (even empty) Set = explicit choice.
    mutationFn: () => addReadingList(metronId, types === null ? null : [...types]),
    onSuccess: () => { toast.success("Reading list added"); onAdded() },
    onError: (e: Error) => toast.error(`Failed: ${e.message}`),
  })

  const counts = data?.issue_type_counts ?? {}
  const typeKeys = Object.keys(counts)
  const selected = types ?? new Set(typeKeys) // default: all types, incl. uncategorised
  const ownedCount = data?.items.filter((i) => i.owned).length ?? 0

  const toggle = (t: string) => {
    const next = new Set(selected)
    next.has(t) ? next.delete(t) : next.add(t)
    setTypes(next)
  }

  return (
    <Sheet open onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{data?.name ?? "Loading…"}</SheetTitle>
          <SheetDescription>
            {data ? `${data.items.length} issues · ${ownedCount} already owned` : ""}
          </SheetDescription>
        </SheetHeader>

        {isLoading && <p className="px-4 text-sm text-muted-foreground">Loading…</p>}
        {isError && <p className="px-4 text-sm text-destructive">Couldn't load this list (Metron may be rate-limited).</p>}

        {data && (
          <div className="flex flex-col gap-4 px-4">
            <div>
              <div className="mb-1 text-sm font-medium">Monitor which issues?</div>
              <p className="mb-2 text-xs text-muted-foreground">Unchecked types are still listed (and exported to CBL) but won't be downloaded.</p>
              <div className="flex flex-col gap-2">
                {typeKeys.map((t) => {
                  const ofType = data.items.filter((i) => (i.issue_type || "") === t)
                  const shown = ofType.slice(0, ISSUE_PREVIEW_LIMIT)
                  return (
                    <div key={t} className="rounded-md border border-border p-2">
                      <label className="flex items-center gap-2 text-sm font-medium">
                        <Checkbox checked={selected.has(t)} onCheckedChange={() => toggle(t)} />
                        <span>{t || "(uncategorised)"}</span>
                        <span className="text-muted-foreground">· {counts[t]}</span>
                      </label>
                      <ul className="mt-1 pl-6 text-xs text-muted-foreground">
                        {shown.map((i) => (
                          <li key={`${i.series_name}-${i.number}-${i.order}`} className="truncate">
                            {i.series_name} #{i.number}{i.cover_year ? ` (${i.cover_year})` : ""}
                            {i.owned && <span className="ml-1 text-status-continuing">· owned</span>}
                          </li>
                        ))}
                        {ofType.length > shown.length && (
                          <li className="italic">+{ofType.length - shown.length} more…</li>
                        )}
                      </ul>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}

        <SheetFooter>
          <Button onClick={() => add.mutate()} disabled={!data || add.isPending}>
            {add.isPending ? "Adding…" : "Add reading list"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
