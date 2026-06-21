import { useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowLeft, Check, Loader2, Search } from "lucide-react"
import {
  getSeries,
  metronSearch,
  updateSeries,
  verifySearch,
  type SeriesUpdate,
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
  comicvine_volume_id: z.string(),
  metron_series_id: z.string(),
  metron_annual_series_id: z.string(),
  annual_comicvine_volume_id: z.string(),
  getcomics_search_name: z.string(),
  issue_min: z
    .string()
    .refine((v) => v.trim() === "" || (Number.isInteger(Number(v)) && Number(v) >= 1), "Minimum 1"),
})
type FormValues = z.infer<typeof schema>

const intOrNull = parseIntOrNull
const str = (n: number | null | undefined) => (n == null ? "" : String(n))

export function SeriesEdit() {
  const { id } = useParams()
  const seriesId = Number(id)
  const nav = useNavigate()
  const qc = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ["series", seriesId],
    queryFn: () => getSeries(seriesId),
    enabled: Number.isFinite(seriesId),
  })

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>
  if (isError || !data) return <p className="text-destructive">Failed to load series.</p>

  return <EditForm key={data.id} initial={data} seriesId={seriesId} nav={nav} qc={qc} />
}

function EditForm({
  initial,
  seriesId,
  nav,
  qc,
}: {
  initial: Awaited<ReturnType<typeof getSeries>>
  seriesId: number
  nav: ReturnType<typeof useNavigate>
  qc: ReturnType<typeof useQueryClient>
}) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      publisher: initial.publisher,
      series_name: initial.series_name,
      year: str(initial.year),
      comicvine_volume_id: str(initial.comicvine_volume_id),
      metron_series_id: str(initial.metron_series_id),
      metron_annual_series_id: str(initial.metron_annual_series_id),
      annual_comicvine_volume_id: str(initial.annual_comicvine_volume_id),
      getcomics_search_name: initial.getcomics_search_name ?? "",
      issue_min: str(initial.issue_min),
    },
  })

  const [annualOpen, setAnnualOpen] = useState(false)
  const [annualQuery, setAnnualQuery] = useState("")
  const annualResults = useQuery({
    queryKey: ["metron-search", annualQuery],
    queryFn: () => metronSearch(annualQuery),
    enabled: annualQuery.trim().length >= 2,
  })

  const [verifyResults, setVerifyResults] = useState<{ title: string; url: string }[] | null>(null)
  const verify = useMutation({
    mutationFn: () => verifySearch(watch("series_name"), watch("getcomics_search_name")),
    onSuccess: (r) => setVerifyResults(r.comics),
    onError: (e: Error) => toast.error(`Verify error: ${e.message}`),
  })

  const save = useMutation({
    mutationFn: (payload: SeriesUpdate) => updateSeries(seriesId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["series-overview"] })
      qc.invalidateQueries({ queryKey: ["series", seriesId] })
      toast.success("Series updated")
      nav(`/series/${seriesId}`)
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const onSubmit = (v: FormValues) =>
    save.mutate({
      publisher: v.publisher.trim(),
      series_name: v.series_name.trim(),
      year: intOrNull(v.year),
      comicvine_volume_id: intOrNull(v.comicvine_volume_id),
      metron_series_id: intOrNull(v.metron_series_id),
      metron_annual_series_id: intOrNull(v.metron_annual_series_id),
      annual_comicvine_volume_id: intOrNull(v.annual_comicvine_volume_id),
      getcomics_search_name: v.getcomics_search_name.trim() || null,
      issue_min: v.issue_min.trim() === "" ? 1 : Number(v.issue_min),
    })

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">Edit Series</h1>
          <p className="text-sm text-muted-foreground">
            {initial.publisher} · {initial.series_name}
          </p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link to={`/series/${seriesId}`}>
            <ArrowLeft /> Back
          </Link>
        </Button>
      </div>

      <Card className="p-6">
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

          {/* Metron Annual Series ID + inline search */}
          <div className="md:col-span-12">
            <label className="text-sm font-medium" htmlFor="metron_annual_series_id">
              Metron Annual Series ID{" "}
              <span className="font-normal text-muted-foreground">
                — optional, for tracking annuals separately
              </span>
            </label>
            <div className="mt-1.5 flex max-w-[360px] gap-2">
              <Input
                id="metron_annual_series_id"
                type="number"
                {...register("metron_annual_series_id")}
              />
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-expanded={annualOpen}
                aria-label="Search Metron for annual series"
                onClick={() => setAnnualOpen((v) => !v)}
              >
                <Search />
              </Button>
            </div>
            {annualOpen && (
              <div className="mt-2 max-w-[480px]">
                <Input
                  placeholder="Search Metron for the annual series…"
                  value={annualQuery}
                  onChange={(e) => setAnnualQuery(e.target.value)}
                  aria-label="Metron annual search"
                />
                <div className="mt-1 flex flex-col gap-1">
                  {annualResults.isFetching && (
                    <p className="px-1 text-xs text-muted-foreground">Searching…</p>
                  )}
                  {annualResults.data?.results.map((r) => (
                    <button
                      key={r.id}
                      type="button"
                      onClick={() => {
                        setValue("metron_annual_series_id", String(r.id), { shouldDirty: true })
                        setAnnualOpen(false)
                      }}
                      className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-left text-sm transition-colors duration-150 hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      <span>
                        {r.name}
                        {r.year_began ? ` (${r.year_began})` : ""}
                        {r.publisher ? ` · ${r.publisher}` : ""}
                      </span>
                      <code className="text-xs text-status-continuing">#{r.id}</code>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              Search for and select the Metron ID of the annual series (e.g. "Spawn Annual").
            </p>
          </div>

          {/* Issue min */}
          <Field className="md:col-span-3" label="Issue min" error={errors.issue_min?.message}>
            <Input type="number" min={1} {...register("issue_min")} />
            <p className="mt-1 text-xs text-muted-foreground">
              Skip issues below this number. Upper bound comes automatically from Metron
              {initial.total_issues ? ` (currently #${initial.total_issues})` : ""}.
            </p>
          </Field>

          {/* getcomics search name + verify */}
          <div className="md:col-span-12">
            <label className="text-sm font-medium" htmlFor="getcomics_search_name">
              getcomics.org Search Name{" "}
              <span className="font-normal text-muted-foreground">— optional override</span>
            </label>
            <div className="mt-1.5 flex gap-2">
              <Input
                id="getcomics_search_name"
                placeholder={initial.series_name}
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
              Only set if the title on getcomics.org differs from the series name above.
            </p>
          </div>

          <div className="mt-2 flex gap-2 md:col-span-12">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : <Check />} Save Changes
            </Button>
            <Button type="button" variant="outline" asChild>
              <Link to={`/series/${seriesId}`}>Cancel</Link>
            </Button>
          </div>
        </form>
      </Card>
    </>
  )
}
