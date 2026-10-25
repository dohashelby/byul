import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, RouterProvider, createBrowserRouter, createMemoryRouter, type RouteObject } from "react-router-dom";
import { DashboardPage } from "../pages/DashboardPage";
import { DocListPage, EnvironmentEditorPage, PolicyEditorPage } from "../pages/DocumentsPage";
import { EventsPage } from "../pages/EventsPage";
import { EvidencePage } from "../pages/EvidencePage";
import { RequestsPage } from "../pages/RequestsPage";
import { ResultsPage } from "../pages/ResultsPage";
import { RevalidationPage } from "../pages/RevalidationPage";
import { RuntimeStatusPage } from "../pages/RuntimeStatusPage";
import { StateView } from "../components/Misc";
import { Layout } from "./Layout";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "environments", element: <DocListPage kind="environments" /> },
      { path: "environments/new", element: <EnvironmentEditorPage key="new" /> },
      { path: "environments/:id", element: <EnvironmentEditorPage /> },
      { path: "policies", element: <DocListPage kind="policies" /> },
      { path: "policies/new", element: <PolicyEditorPage key="new" /> },
      { path: "policies/:id", element: <PolicyEditorPage /> },
      { path: "update/results", element: <ResultsPage /> },
      { path: "update/revalidation", element: <RevalidationPage /> },
      { path: "runtime/requests", element: <RequestsPage /> },
      { path: "runtime/events", element: <EventsPage /> },
      { path: "runtime/status", element: <RuntimeStatusPage /> },
      { path: "evidence", element: <EvidencePage /> },
      { path: "*", element: <StateView kind="not_found" detail="없는 화면입니다." /> },
    ],
  },
];

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
  });
}

const client = makeQueryClient();
const router = typeof window !== "undefined" ? createBrowserRouter(routes) : createMemoryRouter(routes);

export function App() {
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
