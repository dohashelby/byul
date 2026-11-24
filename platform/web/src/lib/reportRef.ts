import type { VerificationResult } from "../api/types";

export type ReportRef = Pick<VerificationResult["report_ref"], "id" | "revision" | "sha256">;

export function reportSearch(ref: ReportRef | string): string {
  const params = new URLSearchParams({ report: typeof ref === "string" ? ref : ref.id });
  if (typeof ref !== "string") {
    params.set("revision", String(ref.revision));
    params.set("sha256", ref.sha256);
  }
  return `?${params}`;
}
