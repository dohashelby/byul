import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, postJson } from "./client";
import type {
  EnvironmentDoc,
  EnvironmentRevision,
  EnvironmentSaved,
  EvidenceList,
  Meta,
  Overview,
  PolicyDoc,
  PolicyRevision,
  PolicySaved,
  RequestDetail,
  RequestList,
  RequestSummary,
  RevalidationList,
  RevisionDiff,
  RevisionList,
  LabReportResponse,
  RuntimeEventList,
  RuntimeStatusResponse,
  VerificationDetail,
  VerificationList,
} from "./types";

const enc = encodeURIComponent;

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: () => getJson<Meta>("/api/meta"), refetchInterval: 30_000 });
export const useOverview = () => useQuery({ queryKey: ["overview"], queryFn: () => getJson<Overview>("/api/overview") });
// runtime 화면은 원래 runtime 대시보드처럼 3초마다 갱신한다.
export const RUNTIME_REFRESH_MS = 3_000;
const live = { refetchInterval: RUNTIME_REFRESH_MS, refetchIntervalInBackground: false };

export const useRuntimeEvents = () =>
  useQuery({ queryKey: ["runtime", "events"], queryFn: () => getJson<RuntimeEventList>("/api/runtime/events"), ...live });
export const useRequests = () =>
  useQuery({ queryKey: ["runtime", "requests"], queryFn: () => getJson<RequestList>("/api/runtime/requests"), ...live });
export const useLabReport = () =>
  useQuery({ queryKey: ["runtime", "lab-report"], queryFn: () => getJson<LabReportResponse>("/api/runtime/lab-report"), ...live });
export const useRequestDetail = (id: string | null) =>
  useQuery({
    queryKey: ["runtime", "request", id],
    queryFn: () => getJson<RequestDetail>(`/api/runtime/requests/${enc(id!)}`),
    enabled: !!id,
  });
export const useRequestSummary = () =>
  useQuery({ queryKey: ["runtime", "summary"], queryFn: () => getJson<RequestSummary>("/api/runtime/summary"), ...live });
export const useRuntimeStatus = () =>
  useQuery({ queryKey: ["runtime", "status"], queryFn: () => getJson<RuntimeStatusResponse>("/api/runtime/status"), ...live });
export const useResults = () =>
  useQuery({ queryKey: ["update", "results"], queryFn: () => getJson<VerificationList>("/api/update/results") });
export const useResult = (id: string | null) =>
  useQuery({
    queryKey: ["update", "result", id],
    queryFn: () => getJson<VerificationDetail>(`/api/update/results/${enc(id!)}`),
    enabled: !!id,
  });
export const useRevalidation = () =>
  useQuery({ queryKey: ["update", "revalidation"], queryFn: () => getJson<RevalidationList>("/api/update/revalidation"), ...live });
export const useEvidence = () => useQuery({ queryKey: ["evidence"], queryFn: () => getJson<EvidenceList>("/api/evidence") });

export type DocKind = "environments" | "policies";

export const useDocList = (kind: DocKind) =>
  useQuery({ queryKey: [kind, "list"], queryFn: () => getJson<RevisionList>(`/api/${kind}`) });
export const useDocRevisions = (kind: DocKind, id: string | null) =>
  useQuery({
    queryKey: [kind, "revisions", id],
    queryFn: () => getJson<RevisionList>(`/api/${kind}/${enc(id!)}/revisions`),
    enabled: !!id,
  });
export const useEnvironmentRevision = (id: string | null, rev: number | null) =>
  useQuery({
    queryKey: ["environments", "revision", id, rev],
    queryFn: () => getJson<EnvironmentRevision>(`/api/environments/${enc(id!)}/revisions/${rev}`),
    enabled: !!id && rev != null,
  });
export const usePolicyRevision = (id: string | null, rev: number | null) =>
  useQuery({
    queryKey: ["policies", "revision", id, rev],
    queryFn: () => getJson<PolicyRevision>(`/api/policies/${enc(id!)}/revisions/${rev}`),
    enabled: !!id && rev != null,
  });
export const useDocDiff = (kind: DocKind, id: string | null, from: number | null, to: number | null) =>
  useQuery({
    queryKey: [kind, "diff", id, from, to],
    queryFn: () => getJson<RevisionDiff>(`/api/${kind}/${enc(id!)}/diff?from_revision=${from}&to_revision=${to}`),
    enabled: !!id && from != null && to != null && from !== to,
  });

export function useSaveEnvironment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: EnvironmentDoc) => postJson<EnvironmentSaved>("/api/environments", doc),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["environments"] }),
  });
}

export function useSavePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: PolicyDoc) => postJson<PolicySaved>("/api/policies", doc),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policies"] }),
  });
}

export function useConfirmPolicy(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { revision: number; approved_by: string; approval_ref: string }) => postJson<PolicySaved>(`/api/policies/${enc(id)}/confirm`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policies"] }),
  });
}
