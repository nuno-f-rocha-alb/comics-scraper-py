import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowDownCircle, Check, Clipboard, Download, FileText, Inbox, Pause, Play, Trash2, X,
} from "lucide-react"
import {
  cleanupLogs, deleteLog, getLogs, getLogStream, logDownloadUrl, saveLogSettings,
  type LogLine,
} from "@/lib/api"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { useConfirm } from "@/components/confirm"

const POLL_MS = 2000

const LINE_OPTIONS = [100, 200, 500, 1000]
const LEVELS = ["", "ERROR", "WARNING", "INFO"]

export function Logs() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const info = useQuery({ queryKey: ["logs-info"], queryFn: getLogs })

  const [filename, setFilename] = useState("")
  const [lines, setLines] = useState(200)
  const [level, setLevel] = useState("")
  const [retention, setRetention] = useState(7)

  const [paused, setPaused] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [copied, setCopied] = useState(false)

  const termRef = useRef<HTMLDivElement>(null)
  const seededRef = useRef(false)

  // Seed selection + retention once from server info.
  useEffect(() => {
    if (info.data && !seededRef.current) {
      seededRef.current = true
      setFilename(info.data.current_name)
      setLines(info.data.lines_default)
      setRetention(info.data.retention_days)
    }
  }, [info.data])

  const stream = useQuery({
    queryKey: ["log-stream", filename, lines, level],
    queryFn: () => getLogStream(filename, lines, level),
    enabled: !!filename,
    refetchInterval: paused ? false : POLL_MS,
  })

  const streamLines: LogLine[] = stream.data?.lines ?? []

  // Auto-scroll to bottom after each update unless the user disabled it.
  useEffect(() => {
    if (autoScroll && termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [streamLines, autoScroll])

  const files = info.data?.files ?? []

  const refreshFiles = () => qc.invalidateQueries({ queryKey: ["logs-info"] })

  const onDelete = async (name: string) => {
    if (!(await confirm({ title: `Delete ${name}?`, confirmText: "Delete", destructive: true }))) return
    deleteLog(name)
      .then(() => {
        toast.success(`${name} deleted`)
        refreshFiles()
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : String(e)))
  }

  const onCleanup = async () => {
    if (!(await confirm({ title: `Delete all log files older than ${retention} days?`, confirmText: "Delete", destructive: true }))) return
    cleanupLogs()
      .then((r) => {
        toast.success(`${r.deleted} log(s) deleted`)
        refreshFiles()
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : String(e)))
  }

  const onSaveRetention = () =>
    saveLogSettings(retention)
      .then((r) => {
        setRetention(r.retention_days)
        toast.success(`Retention set to ${r.retention_days} days`)
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : String(e)))

  const onCopy = async () => {
    const term = termRef.current
    if (!term) return
    const sel = window.getSelection()
    const text =
      sel && sel.toString().length > 0 && sel.anchorNode && term.contains(sel.anchorNode)
        ? sel.toString()
        : term.innerText
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      toast.error("Copy failed")
    }
  }

  return (
    <>
      <div className="mb-4">
        <h1 className="text-xl font-bold">Logs</h1>
        <p className="text-sm text-muted-foreground">Real-time log viewer</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[20rem_1fr] xl:grid-cols-[22rem_1fr]">
        {/* ── Left: files + settings ── */}
        <div className="flex flex-col gap-4">
          {/* File selector + filters */}
          <Card className="overflow-hidden">
            <div className="border-b border-border px-4 py-2 text-sm font-semibold">Log Files</div>
            <div className="space-y-2 p-3">
              <select
                aria-label="Select log file"
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
              >
                {files.length === 0 && <option disabled>No log files found</option>}
                {files.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name}
                  </option>
                ))}
              </select>
              <div className="flex items-center gap-2">
                <label htmlFor="log-lines" className="text-sm text-muted-foreground">
                  Lines
                </label>
                <select
                  id="log-lines"
                  className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                  value={lines}
                  onChange={(e) => setLines(Number(e.target.value))}
                >
                  {LINE_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Filter by level"
                  className="flex-1 rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                  value={level}
                  onChange={(e) => setLevel(e.target.value)}
                >
                  {LEVELS.map((lv) => (
                    <option key={lv} value={lv}>
                      {lv || "All levels"}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </Card>

          {/* File list */}
          <Card className="flex flex-1 flex-col overflow-hidden">
            <div className="flex items-center gap-2 border-b border-border px-4 py-2">
              <span className="text-sm font-semibold">All Files</span>
              <span className="rounded bg-primary/15 px-1.5 py-0.5 text-xs text-primary">
                {files.length}
              </span>
            </div>
            {files.length === 0 ? (
              <div className="flex flex-col items-center gap-1 py-8 text-sm text-muted-foreground">
                <Inbox className="size-6 opacity-50" />
                No log files.
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {files.map((f) => (
                  <li
                    key={f.name}
                    className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent"
                  >
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      onClick={() => setFilename(f.name)}
                    >
                      <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium" title={f.name}>
                          {f.name}
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {(f.size / 1024).toFixed(1)} KB
                        </span>
                      </span>
                    </button>
                    <a
                      className="shrink-0 text-muted-foreground hover:text-foreground"
                      href={logDownloadUrl(f.name)}
                      title="Download"
                    >
                      <Download className="size-3.5" />
                    </a>
                    <button
                      className="shrink-0 text-muted-foreground hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDelete(f.name)
                      }}
                      title="Delete"
                    >
                      <X className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* Retention */}
          <Card className="overflow-hidden">
            <div className="border-b border-border px-4 py-2 text-sm font-semibold">Retention</div>
            <div className="space-y-2 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor="log-retention" className="text-sm text-muted-foreground">
                  Keep
                </label>
                <input
                  id="log-retention"
                  type="number"
                  min={1}
                  max={365}
                  className="w-16 rounded-md border border-input bg-background px-2 py-1 text-sm"
                  value={retention}
                  onChange={(e) => setRetention(Number(e.target.value))}
                />
                <span className="text-sm text-muted-foreground">days</span>
                <Button variant="outline" size="sm" className="ml-auto" onClick={onSaveRetention}>
                  Save
                </Button>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="w-full text-destructive hover:text-destructive"
                onClick={onCleanup}
              >
                <Trash2 /> Clean old logs
              </Button>
            </div>
          </Card>
        </div>

        {/* ── Right: terminal ── */}
        <Card className="flex flex-col overflow-hidden" style={{ minHeight: "32rem" }}>
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
            <span className="inline-flex items-center gap-1.5 rounded bg-status-ended/15 px-2 py-0.5 text-xs text-status-ended">
              <span
                className={`size-1.5 rounded-full ${paused ? "bg-status-missing-unmonitored" : "bg-status-ended animate-pulse"}`}
              />
              LIVE
            </span>
            <span className="text-sm text-muted-foreground">{filename}</span>
            <span className="ml-auto flex items-center gap-2">
              <span className="text-xs text-muted-foreground">{streamLines.length} lines</span>
              <Button variant="outline" size="sm" onClick={onCopy} title="Copy visible lines">
                {copied ? <Check /> : <Clipboard />}
                {copied ? "Copied" : "Copy"}
              </Button>
              <a
                className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-1 text-xs hover:bg-accent"
                href={logDownloadUrl(filename)}
                title="Download this log file"
              >
                <Download className="size-3.5" /> Download
              </a>
              <Button
                variant={autoScroll ? "default" : "outline"}
                size="sm"
                onClick={() => setAutoScroll((v) => !v)}
                title="Toggle auto-scroll"
              >
                <ArrowDownCircle /> Auto-scroll
              </Button>
              <Button
                variant={paused ? "default" : "outline"}
                size="sm"
                onClick={() => setPaused((v) => !v)}
                title="Pause live updates"
              >
                {paused ? <Play /> : <Pause />}
                {paused ? "Resume" : "Pause"}
              </Button>
            </span>
          </div>

          {/* Terminal output */}
          <div
            ref={termRef}
            className="log-terminal flex-1 overflow-y-auto px-5 py-4"
            style={{ minHeight: 0 }}
          >
            {streamLines.length === 0 ? (
              <span className="ll-muted">
                {filename ? `No entries yet in ${filename}.` : "No log file found."}
              </span>
            ) : (
              streamLines.map((l, i) => (
                <span key={i} className={`ll-${l.cls} block`}>
                  {l.text}
                </span>
              ))
            )}
          </div>
        </Card>
      </div>
    </>
  )
}
