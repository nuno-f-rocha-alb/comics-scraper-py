import { type ReactNode } from "react"
import { Link } from "react-router-dom"
import { BookMarked } from "lucide-react"

import { cn } from "@/lib/utils"

/** Reading-list card (Metron style): a full-width cover banner fills the top of
 *  the card, content sits below. Cover images are of unknown size, so the banner
 *  uses a fixed aspect + object-cover to crop-to-fill. `to` makes it a link. */
export function CoverCard({
  image, alt, to, topRight, bottomRight, children, className,
}: {
  image?: string | null
  alt?: string
  to?: string
  topRight?: ReactNode
  bottomRight?: ReactNode
  children?: ReactNode
  className?: string
}) {
  const base =
    "group block overflow-hidden rounded-xl bg-card text-inherit no-underline shadow transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-xl motion-reduce:hover:translate-y-0"
  const inner = (
    <>
      <div className="relative aspect-[16/9] w-full overflow-hidden bg-muted">
        {image ? (
          <img src={image} alt={alt ?? ""} loading="lazy" className="size-full object-cover" />
        ) : (
          <div className="flex size-full items-center justify-center text-muted-foreground">
            <BookMarked className="size-8" />
          </div>
        )}
        {topRight && <div className="absolute right-1.5 top-1.5">{topRight}</div>}
        {bottomRight && <div className="absolute bottom-1.5 right-1.5">{bottomRight}</div>}
      </div>
      {children && <div className="flex flex-col p-3">{children}</div>}
    </>
  )
  return to
    ? <Link to={to} className={cn(base, className)}>{inner}</Link>
    : <div className={cn(base, className)}>{inner}</div>
}

/** Medium card grid (matches the original reading-list layout, ~3 columns). */
export const COVER_GRID = "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
