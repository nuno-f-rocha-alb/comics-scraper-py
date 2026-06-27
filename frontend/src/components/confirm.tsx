import * as React from "react"
import { AlertDialog } from "radix-ui"

import { Button } from "@/components/ui/button"

type ConfirmOptions = {
  title: string
  description?: string
  confirmText?: string
  cancelText?: string
  destructive?: boolean
}

const ConfirmContext = React.createContext<(o: ConfirmOptions) => Promise<boolean>>(
  () => Promise.resolve(false),
)

/** Promise-based replacement for window.confirm(), styled to match the app.
 *  Usage: const confirm = useConfirm(); if (await confirm({ title })) { … } */
export function useConfirm() {
  return React.useContext(ConfirmContext)
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<{
    opts: ConfirmOptions
    resolve: (ok: boolean) => void
  } | null>(null)

  const confirm = React.useCallback(
    (opts: ConfirmOptions) => new Promise<boolean>((resolve) => setState({ opts, resolve })),
    [],
  )

  // resolve() on an already-settled promise is a no-op, so a Cancel-then-close
  // double fire is harmless.
  const settle = (ok: boolean) => {
    state?.resolve(ok)
    setState(null)
  }

  const opts = state?.opts
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <AlertDialog.Root open={!!state} onOpenChange={(open) => { if (!open) settle(false) }}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/50 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-background p-6 shadow-lg data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
            <AlertDialog.Title className="text-base font-semibold text-foreground">
              {opts?.title}
            </AlertDialog.Title>
            {opts?.description && (
              <AlertDialog.Description className="mt-2 text-sm text-muted-foreground">
                {opts.description}
              </AlertDialog.Description>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <Button variant="outline" size="sm">{opts?.cancelText ?? "Cancel"}</Button>
              </AlertDialog.Cancel>
              <AlertDialog.Action asChild>
                <Button
                  variant={opts?.destructive ? "destructive" : "default"}
                  size="sm"
                  onClick={() => settle(true)}
                >
                  {opts?.confirmText ?? "Confirm"}
                </Button>
              </AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </ConfirmContext.Provider>
  )
}
