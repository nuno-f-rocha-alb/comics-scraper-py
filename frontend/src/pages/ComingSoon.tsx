import { Construction } from "lucide-react"

export function ComingSoon({ title }: { title: string }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center text-muted-foreground">
      <Construction className="mb-3 size-12 opacity-40" />
      <h1 className="text-lg font-semibold text-foreground">{title}</h1>
      <p className="text-sm">This page is not migrated yet.</p>
    </div>
  )
}
