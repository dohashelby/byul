import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, postJson } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type UpdateRun = S["UpdateRun"];
export type UpdateRunRequest = S["UpdateRunRequest"];
export type UpdatePreparation = S["UpdatePreparation"];

const runsKey = ["update", "runs"];
export const useUpdateExecutionSettings = () => useQuery({
  queryKey: ["update", "execution-settings"],
  queryFn: () => getJson<S["UpdateExecutionSettings"]>("/api/update/execution-settings"),
});
export const useUpdateRuns = () => useQuery({
  queryKey: runsKey, queryFn: () => getJson<S["UpdateRunList"]>("/api/update/runs"), refetchInterval: 3_000,
});
export const useUpdateRun = (id: string | null) => useQuery({
  queryKey: [...runsKey, id], queryFn: () => getJson<UpdateRun>(`/api/update/runs/${encodeURIComponent(id!)}`),
  enabled: !!id,
  refetchInterval: (query) => !query.state.data || ["preparing", "running"].includes(query.state.data.status) ? 1_500 : false,
});

export function usePrepareUpdate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: UpdateRunRequest) => postJson<UpdateRun>("/api/update/runs/prepare", request),
    onSuccess: (run) => {
      client.setQueryData([...runsKey, run.id], run);
      void client.invalidateQueries({ queryKey: runsKey });
    },
  });
}

export function useExecuteUpdate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => postJson<UpdateRun>(`/api/update/runs/${encodeURIComponent(id)}/execute`, {}),
    onSuccess: (run) => {
      client.setQueryData([...runsKey, run.id], run);
      void client.invalidateQueries({ queryKey: runsKey });
    },
  });
}
