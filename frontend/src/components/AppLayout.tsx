import { useEffect, useState } from "react"
import { Link, Outlet, useLocation } from "react-router-dom"
import {
  CalendarDays,
  Clock,
  Download,
  FolderOpen,
  Library,
  Menu,
  Moon,
  PlusCircle,
  Rss,
  Sun,
  Terminal,
} from "lucide-react"
import { applyTheme, getInitialTheme, type Theme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const NAV = [
  // Series is active on /series and /series/:id but NOT /series/add (matches base.html)
  {
    to: "/series",
    label: "Series",
    icon: Library,
    match: (p: string) => p === "/series" || (p.startsWith("/series/") && p !== "/series/add"),
  },
  { to: "/series/add", label: "Add Series", icon: PlusCircle, match: (p: string) => p === "/series/add" },
  { to: "/calendar", label: "Calendar", icon: CalendarDays, match: (p: string) => p.startsWith("/calendar") },
  { to: "/releases", label: "Releases", icon: Rss, match: (p: string) => p.startsWith("/releases") },
  { to: "/downloads", label: "Downloads", icon: Download, match: (p: string) => p.startsWith("/downloads") },
  { to: "/scheduler", label: "Scheduler", icon: Clock, match: (p: string) => p.startsWith("/scheduler") },
  { to: "/library", label: "Library", icon: FolderOpen, match: (p: string) => p.startsWith("/library") },
  { to: "/logs", label: "Logs", icon: Terminal, match: (p: string) => p.startsWith("/logs") },
]

export function AppLayout() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()

  useEffect(() => applyTheme(theme), [theme])

  return (
    <div className="min-h-screen">
      {/* Mobile top bar */}
      <div className="sticky top-0 z-40 flex items-center gap-2 border-b border-border bg-[#12141a] px-3 py-2 md:hidden">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-label="Open navigation"
          className="rounded-md p-2 text-slate-300 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <Menu className="size-5" />
        </button>
        <span className="text-sm font-semibold text-white">Comics Scraper</span>
      </div>

      {/* Backdrop (mobile) */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setOpen(false)}
          aria-hidden
        />
      )}

      {/* Sidebar */}
      <nav
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[220px] flex-col bg-[#12141a] p-3 transition-transform duration-200 md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="mb-3 flex items-center gap-2.5 border-b border-white/10 px-2 pb-4 pt-1">
          <Library className="size-7 text-status-continuing" />
          <div className="leading-tight">
            <div className="text-sm font-bold text-slate-200">Comics Scraper</div>
            <div className="text-xs font-normal text-slate-500">by nunobifes</div>
          </div>
        </div>

        <div className="px-3 pb-1 pt-2 text-[0.67rem] font-bold uppercase tracking-[0.09em] text-slate-500">
          Library
        </div>
        <ul className="mb-3 flex flex-1 flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon, match }) => {
            const active = match(pathname)
            return (
              <li key={to}>
                <Link
                  to={to}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-lg border-l-2 border-transparent px-3 py-2 text-sm text-slate-400 transition-colors duration-150 hover:bg-white/10 hover:text-slate-200 focus-visible:outline-2 focus-visible:outline-ring",
                    active &&
                      "border-status-continuing bg-status-continuing/15 pl-[calc(0.75rem-2px)] text-status-continuing",
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  {label}
                </Link>
              </li>
            )
          })}
        </ul>

        <div className="border-t border-white/10 pt-3">
          <button
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            className="flex w-full items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-400 transition-colors duration-150 hover:bg-white/10 hover:text-slate-200 focus-visible:outline-2 focus-visible:outline-ring"
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            {theme === "dark" ? "Light Mode" : "Dark Mode"}
          </button>
        </div>
      </nav>

      {/* Main */}
      <main className="min-h-screen px-5 py-4 md:ml-[220px] md:px-10 md:py-8">
        <Outlet />
      </main>
    </div>
  )
}
