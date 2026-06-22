import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { ChevronDown, Loader2, NotebookText, Save } from "lucide-react"
import { getSeriesXml, saveSeriesXml } from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

const FIELDS = ["Description", "Genre", "Characters", "Teams", "Locations", "Notes"]

export function SeriesNotes({ seriesId }: { seriesId: number }) {
  const [open, setOpen] = useState(false)
  const [fields, setFields] = useState<Record<string, string>>({})

  const xml = useQuery({
    queryKey: ["series-xml", seriesId],
    queryFn: () => getSeriesXml(seriesId),
    enabled: open,
  })
  // Populate once — don't clobber unsaved edits on a background refetch.
  const loaded = useRef(false)
  useEffect(() => {
    if (xml.data && !loaded.current) {
      setFields(xml.data.fields)
      loaded.current = true
    }
  }, [xml.data])

  const save = useMutation({
    mutationFn: () => saveSeriesXml(seriesId, fields),
    onSuccess: () => toast.success("Series notes saved."),
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  return (
    <Card className="mb-4 overflow-hidden p-0">
      <button
        className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-accent/40 focus-visible:outline-2 focus-visible:outline-ring"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <NotebookText className="size-4 text-muted-foreground" />
        <span className="font-semibold">Series Notes</span>
        <span className="text-sm text-muted-foreground">— local series.xml</span>
        <ChevronDown className={cn("ml-auto size-4 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border p-4">
          {xml.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading notes…</p>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {FIELDS.map((f) => (
                  <div key={f} className={f === "Description" || f === "Notes" ? "sm:col-span-2" : ""}>
                    <Label htmlFor={`sx-${f}`} className="text-xs">{f}</Label>
                    <Textarea id={`sx-${f}`} rows={f === "Description" || f === "Notes" ? 3 : 2}
                      value={fields[f] ?? ""} onChange={(e) => setFields((p) => ({ ...p, [f]: e.target.value }))}
                      className="mt-1" />
                  </div>
                ))}
              </div>
              <Button className="mt-3" size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
                {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save Notes
              </Button>
            </>
          )}
        </div>
      )}
    </Card>
  )
}
