import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, RouterProvider, createBrowserRouter, createMemoryRouter, type RouteObject } from "react-router-dom";
import { DashboardPage } from "../pages/DashboardPage";
import { DocListPage, EnvironmentEditorPage, PolicyEditorPage } from "../pages/DocumentsPage";
import { EventsPage } from "../pages/EventsPage";
import { EvidencePage } from "../pages/EvidencePage";
import { RequestsPage } from "../pages/RequestsPage";
import { ResultsPage } from "../pages/ResultsPage";
import { RevalidationPage } from "../pages/RevalidationPage";
import { RuntimeEnforcePage } from "../pages/runtime/RuntimeEnforcePage";
import { RuntimeObservePage } from "../pages/runtime/RuntimeObservePage";
import { RuntimeOverviewPage } from "../pages/runtime/RuntimeOverviewPage";
import { RuntimePolicyPage } from "../pages/runtime/RuntimePolicyPage";
import { RuntimeSettingsPage } from "../pages/runtime/RuntimeSettingsPage";
import { RuntimeTargetPage } from "../pages/runtime/RuntimeTargetPage";
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
      { path: "runtime/overview", element: <RuntimeOverviewPage /> },
      { path: "runtime/requests", element: <RequestsPage /> },
      { path: "runtime/policy", element: <RuntimePolicyPage /> },
      { path: "runtime/target", element: <RuntimeTargetPage /> },
      { path: "runtime/observe", element: <Navigate to="/runtime/observe/collectors" replace /> },
      { path: "runtime/observe/:sub", element: <RuntimeObservePage /> },
      { path: "runtime/enforce", element: <Navigate to="/runtime/enforce/cilium" replace /> },
      { path: "runtime/enforce/:sub", element: <RuntimeEnforcePage /> },
      { path: "runtime/events", element: <EventsPage /> },
      { path: "runtime/settings", element: <RuntimeSettingsPage /> },
      // 이전 주소 호환
      { path: "runtime/status", element: <Navigate to="/runtime/overview" replace /> },
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
