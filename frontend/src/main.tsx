import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import "./index.css"
import { AppLayout } from "@/components/AppLayout"
import { SeriesList } from "@/pages/SeriesList"
import { SeriesEdit } from "@/pages/SeriesEdit"
import { SeriesAdd } from "@/pages/SeriesAdd"
import { SeriesDetail } from "@/pages/SeriesDetail"
import { Downloads } from "@/pages/Downloads"
import { Library } from "@/pages/Library"
import { Scheduler } from "@/pages/Scheduler"
import { Releases } from "@/pages/Releases"
import { Calendar } from "@/pages/Calendar"
import { ComingSoon } from "@/pages/ComingSoon"
import { Toaster } from "@/components/ui/sonner"

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Navigate to="/series" replace />} />
            <Route path="/series" element={<SeriesList />} />
            <Route path="/series/add" element={<SeriesAdd />} />
            <Route path="/series/:id/edit" element={<SeriesEdit />} />
            <Route path="/series/:id" element={<SeriesDetail />} />
            <Route path="/calendar" element={<Calendar />} />
            <Route path="/releases" element={<Releases />} />
            <Route path="/downloads" element={<Downloads />} />
            <Route path="/scheduler" element={<Scheduler />} />
            <Route path="/library" element={<Library />} />
            <Route path="/logs" element={<ComingSoon title="Logs" />} />
            <Route path="*" element={<ComingSoon title="Not Found" />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster richColors position="bottom-right" />
    </QueryClientProvider>
  </StrictMode>,
)
