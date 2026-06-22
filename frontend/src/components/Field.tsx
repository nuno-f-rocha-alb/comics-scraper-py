import type { ReactNode } from "react"

export function Field({
  label,
  error,
  className,
  htmlFor,
  children,
}: {
  label: ReactNode
  error?: string
  className?: string
  htmlFor?: string
  children: ReactNode
}) {
  return (
    <div className={className}>
      <label className="text-sm font-medium" htmlFor={htmlFor}>
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
    </div>
  )
}
