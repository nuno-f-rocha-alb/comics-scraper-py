import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Download, Loader2, Save } from "lucide-react"
import { getIssueMetadata, saveIssueMetadata } from "@/lib/api"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"

const FIELDS = [
  "Series", "Number", "Title", "Publisher", "Year", "Month", "Web",
  "Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist",
  "Summary", "Genre", "Tags", "LanguageISO", "PageCount",
]

export function MetadataSheet({
  seriesId,
  issueNum,
  onClose,
  onSaved,
}: {
  seriesId: number
  issueNum: string | null
  onClose: () => void
  onSaved: () => void
}) {
  const open = issueNum !== null
  const [fields, setFields] = useState<Record<string, string>>({})
  const [filename, setFilename] = useState("")

  const meta = useQuery({
    queryKey: ["issue-metadata", seriesId, issueNum],
    queryFn: () => getIssueMetadata(seriesId, issueNum!),
    enabled: open,
  })
  // Populate once per issue — don't clobber unsaved edits on a background refetch.
  const loadedFor = useRef<string | null>(null)
  useEffect(() => {
    if (meta.data && loadedFor.current !== issueNum) {
      setFields(meta.data.fields)
      setFilename(meta.data.filename)
      loadedFor.current = issueNum
    }
  }, [meta.data, issueNum])

  const fromMetron = useMutation({
    mutationFn: () => getIssueMetadata(seriesId, issueNum!, true),
    onSuccess: (m) => { setFields((f) => ({ ...f, ...m.fields })); toast.success("Loaded from Metron") },
    onError: (e: Error) => toast.error(`Metron: ${e.message}`),
  })
  const save = useMutation({
    mutationFn: () => saveIssueMetadata(seriesId, issueNum!, fields),
    onSuccess: () => { toast.success("Metadata saved."); onSaved(); onClose() },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  })

  const set = (k: string, v: string) => setFields((f) => ({ ...f, [k]: v }))

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
        <SheetHeader className="border-b border-border">
          <SheetTitle>Issue #{issueNum} — Edit Metadata</SheetTitle>
          <SheetDescription>{filename || "ComicInfo.xml"}</SheetDescription>
        </SheetHeader>

        {meta.isLoading ? (
          <p className="p-6 text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="flex-1 overflow-y-auto p-4">
            <div className="grid grid-cols-2 gap-3">
              {FIELDS.map((f) => (
                <div key={f} className={f === "Summary" ? "col-span-2" : ""}>
                  <Label htmlFor={`md-${f}`} className="text-xs">{f}</Label>
                  {f === "Summary" ? (
                    <Textarea id={`md-${f}`} rows={4} value={fields[f] ?? ""}
                      onChange={(e) => set(f, e.target.value)} className="mt-1" />
                  ) : (
                    <Input id={`md-${f}`} value={fields[f] ?? ""}
                      onChange={(e) => set(f, e.target.value)} className="mt-1" />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 border-t border-border p-4">
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save
          </Button>
          <Button variant="outline" onClick={() => fromMetron.mutate()} disabled={fromMetron.isPending}>
            {fromMetron.isPending ? <Loader2 className="animate-spin" /> : <Download />} Load from Metron
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  )
}
