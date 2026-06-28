import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { BookMarked, Search, Star, Plus } from "lucide-react"
import {
  searchReadingLists, previewReadingList, addReadingList, getReadingLists,
  type RLSearchResult, type RLPreview,
} from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
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
        <div className="mb-6 grid grid-cols-1 gap-2 md:grid-cols-2">
          {search.data.results.map((r) => <SearchCard key={r.id} r={r} onAdd={() => setPreview(r.id)} />)}
        </div>
      )}

      {/* Your lists */}
      <h2 className="mb-2 mt-2 text-sm font-bold uppercase tracking-wide text-muted-foreground">Your reading lists</h2>
      {mine.data && mine.data.reading_lists.length === 0 && (
        <p className="text-sm text-muted-foreground">None yet — search above and add one.</p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {mine.data?.reading_lists.map((rl) => {
          const pct = rl.total ? Math.round((rl.owned / rl.total) * 100) : 0
          return (
            <Link key={rl.id} to={`/reading-lists/${rl.id}`}>
              <Card className="flex h-full gap-3 p-3 transition-colors hover:bg-accent/40">
                {rl.image_url
                  ? <img src={rl.image_url} alt="" className="h-24 w-16 shrink-0 rounded object-cover" />
                  : <div className="flex h-24 w-16 shrink-0 items-center justify-center rounded bg-muted"><BookMarked className="size-6 text-muted-foreground" /></div>}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold">{rl.name}</div>
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {rl.list_type && <Badge variant="secondary">{rl.list_type}</Badge>}
                    {rl.attribution_source && <Badge variant="outline">{rl.attribution_source}</Badge>}
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">{rl.owned}/{rl.total} owned</div>
                  <div className="mt-1 h-1.5 w-full rounded-full bg-muted">
                    <div className="h-1.5 rounded-full bg-primary" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              </Card>
            </Link>
          )
        })}
      </div>

      {preview !== null && (
        <PreviewSheet
          metronId={preview}
          onClose={() => setPreview(null)}
          onAdded={() => { setPreview(null); qc.invalidateQueries({ queryKey: ["reading-lists"] }) }}
        />
      )}
    </>
  )
}

function SearchCard({ r, onAdd }: { r: RLSearchResult; onAdd: () => void }) {
  return (
    <Card className="flex items-center gap-3 p-3">
      {r.image
        ? <img src={r.image} alt="" className="h-16 w-11 shrink-0 rounded object-cover" />
        : <div className="flex h-16 w-11 shrink-0 items-center justify-center rounded bg-muted"><BookMarked className="size-5 text-muted-foreground" /></div>}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{r.name}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
          {r.list_type && <Badge variant="secondary">{r.list_type}</Badge>}
          {r.attribution_source && <Badge variant="outline">{r.attribution_source}</Badge>}
          {r.average_rating != null && <span className="inline-flex items-center gap-0.5"><Star className="size-3" /> {r.average_rating}</span>}
        </div>
      </div>
      <Button size="sm" onClick={onAdd}><Plus className="size-4" /> Add</Button>
    </Card>
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
  const selected = types ?? new Set(typeKeys.filter((t) => t)) // default: all named types
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
                        <Checkbox checked={selected.has(t)} onCheckedChange={() => toggle(t)} disabled={!t} />
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
