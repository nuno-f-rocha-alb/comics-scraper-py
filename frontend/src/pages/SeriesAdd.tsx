import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowLeft, Book, Loader2, Plus, Search } from "lucide-react"
import {
  createSeries,
  metronSearch,
  verifySearch,
  type MetronResult,
  type SeriesCreate,
} from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Field } from "@/components/Field"
import { parseIntOrNull } from "@/lib/utils"

const schema = z.object({
  publisher: z.string().trim().min(1, "Publisher is required"),
  series_name: z.string().trim().min(1, "Series name is required"),
  year: z.string(),
  metron_series_id: z.string(),
  comicvine_volume_id: z.string(),
  annual_comicvine_volume_id: z.string(),
  getcomics_search_name: z.string(),
})
type FormValues = z.infer<typeof schema>
const intOrNull = parseIntOrNull
const str = (n: number | null | undefined) => (n == null ? "" : String(n))

export function SeriesAdd() {
  const [selected, setSelected] = useState<MetronResult | null>(null)
  const nav = useNavigate()

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">Add Series</h1>
          <p className="text-sm text-muted-foreground">Search Metron to add a new series</p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link to="/series">
            <ArrowLeft /> Back
          </Link>
        </Button>
      </div>

      <Card className="p-6">
        {selected ? (
          <AddForm result={selected} onBack={() => setSelected(null)} nav={nav} />
        ) : (
          <MetronSearchStep onSelect={setSelected} />
        )}
      </Card>
    </>
  )
}

function MetronSearchStep({ onSelect }: { onSelect: (r: MetronResult) => void }) {
  const [name, setName] = useState("")
  const [query, setQuery] = useState("")
  useEffect(() => {
    const t = setTimeout(() => setQuery(name), 400)
    return () => clearTimeout(t)
  }, [name])

  const { data, isFetching } = useQuery({
    queryKey: ["metron-search", query],
    queryFn: () => metronSearch(query),
    enabled: query.trim().length >= 2,
  })
  const results = data?.results ?? []

  return (
    <>
      <div className="relative">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Search by series name…"
          aria-label="Search Metron by series name"
          className="h-12 pl-10 text-base"
        />
      </div>

      <div className="mt-4">
        {query.trim().length < 2 ? (
          <Empty icon={<Book className="size-8 opacity-40" />} text="Type a series name to search Metron." />
        ) : isFetching ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Searching…</p>
        ) : results.length === 0 ? (
          <Empty text={`No results for "${query}".`} />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {results.map((r) => (
              <div key={r.id} className="flex gap-3 rounded-lg border border-border p-3">
                {r.image ? (
                  <img
                    src={r.image}
                    alt={r.name}
                    className="h-[108px] w-[72px] shrink-0 rounded-md object-cover"
                  />
                ) : (
                  <div className="flex h-[108px] w-[72px] shrink-0 items-center justify-center rounded-md bg-status-continuing/10 text-status-continuing">
                    <Book className="size-6" />
                  </div>
                )}
                <div className="flex min-w-0 flex-col">
                  <h3 className="truncate font-semibold" title={r.name}>
                    {r.name}
                  </h3>
                  <p className="text-sm text-muted-foreground">{r.publisher ?? "—"}</p>
                  <p className="mb-auto text-sm text-muted-foreground">
                    {r.year_began ?? ""}
                    {r.issue_count ? ` · ${r.issue_count} issues` : ""}
                  </p>
                  <Button size="sm" className="mt-2 self-start" onClick={() => onSelect(r)}>
                    <Plus /> Select
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function AddForm({
  result,
  onBack,
  nav,
}: {
  result: MetronResult
  onBack: () => void
  nav: ReturnType<typeof useNavigate>
}) {
  const qc = useQueryClient()
  const {
    register,
    handleSubmit,
    watch,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      publisher: result.publisher ?? "",
      series_name: result.name,
      year: str(result.year_began),
      metron_series_id: str(result.id),
      comicvine_volume_id: str(result.cv_id),
      annual_comicvine_volume_id: "",
      getcomics_search_name: "",
    },
  })

  const [verifyResults, setVerifyResults] = useState<{ title: string; url: string }[] | null>(null)
  const verify = useMutation({
    mutationFn: () => verifySearch(watch("series_name"), watch("getcomics_search_name")),
    onMutate: () => setVerifyResults(null),  // drop stale links while re-checking
    onSuccess: (r) => setVerifyResults(r.comics),
    onError: (e: Error) => {
      setVerifyResults(null)
      toast.error(`Verify error: ${e.message}`)
    },
  })

  const create = useMutation({
    mutationFn: (payload: SeriesCreate) => createSeries(payload),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ["series-overview"] })
      toast.success(`${s.series_name} added successfully.`)
      nav("/series")
    },
    onError: (e: Error) => toast.error(`Add failed: ${e.message}`),
  })

  const onSubmit = (v: FormValues) =>
    create.mutate({
      publisher: v.publisher.trim(),
      series_name: v.series_name.trim(),
      year: intOrNull(v.year),
      metron_series_id: intOrNull(v.metron_series_id),
      comicvine_volume_id: intOrNull(v.comicvine_volume_id),
      annual_comicvine_volume_id: intOrNull(v.annual_comicvine_volume_id),
      metron_annual_series_id: null,
      getcomics_search_name: v.getcomics_search_name.trim() || null,
      issue_min: 1,
      cover_image_url: result.image || null,
      total_issues: result.issue_count,
    })

  return (
    <div>
      <div className="mb-4 flex items-start gap-3 border-b border-border pb-3">
        {result.image ? (
          <img
            src={result.image}
            alt={result.name}
            className="h-[108px] w-[72px] shrink-0 rounded-md object-cover"
          />
        ) : (
          <div className="flex h-[108px] w-[72px] shrink-0 items-center justify-center rounded-md bg-status-continuing/10 text-status-continuing">
            <Book className="size-6" />
          </div>
        )}
        <div>
          <h2 className="font-bold">{result.name}</h2>
          <p className="text-sm text-muted-foreground">
            {result.publisher ?? "—"}
            {result.year_began ? ` · ${result.year_began}` : ""}
            {result.issue_count ? ` · ${result.issue_count} issues` : ""}
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="grid grid-cols-1 gap-4 md:grid-cols-12">
        <Field className="md:col-span-6" label="Publisher" error={errors.publisher?.message}>
          <Input {...register("publisher")} />
        </Field>
        <Field className="md:col-span-6" label="Series Name" error={errors.series_name?.message}>
          <Input {...register("series_name")} />
        </Field>
        <Field className="md:col-span-3" label="Year">
          <Input type="number" {...register("year")} />
        </Field>
        <Field className="md:col-span-3" label="Metron Series ID">
          <Input type="number" {...register("metron_series_id")} />
        </Field>
        <Field className="md:col-span-3" label="CV Volume ID">
          <Input type="number" {...register("comicvine_volume_id")} />
        </Field>
        <Field className="md:col-span-3" label="Annual CV ID">
          <Input type="number" {...register("annual_comicvine_volume_id")} />
        </Field>

        <div className="md:col-span-12">
          <label className="text-sm font-medium" htmlFor="getcomics_search_name">
            getcomics.org Search Name{" "}
            <span className="font-normal text-muted-foreground">— optional override</span>
          </label>
          <div className="mt-1.5 flex gap-2">
            <Input
              id="getcomics_search_name"
              placeholder="Leave blank to use Series Name above"
              {...register("getcomics_search_name")}
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => verify.mutate()}
              disabled={verify.isPending}
            >
              {verify.isPending ? <Loader2 className="animate-spin" /> : <Search />} Verify
            </Button>
          </div>
          {verifyResults && (
            <div className="mt-2 rounded-md border border-border p-2 text-sm">
              {verifyResults.length === 0 ? (
                <p className="text-muted-foreground">No results on getcomics.org page 1.</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {verifyResults.map((c) => (
                    <li key={c.url}>
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-status-continuing hover:underline"
                      >
                        {c.title}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            Only set if the title on getcomics.org differs from the series name.
          </p>
        </div>

        <div className="mt-2 flex gap-2 md:col-span-12">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Add Series
          </Button>
          <Button type="button" variant="outline" onClick={onBack}>
            <ArrowLeft /> Back to search
          </Button>
        </div>
      </form>
    </div>
  )
}

function Empty({ icon, text }: { icon?: React.ReactNode; text: string }) {
  return (
    <div className="flex flex-col items-center py-10 text-center text-muted-foreground">
      {icon ?? <Search className="size-7 opacity-40" />}
      <p className="mt-2 text-sm">{text}</p>
    </div>
  )
}
