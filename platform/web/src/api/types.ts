// 생성된 OpenAPI 타입(schema.d.ts)의 별칭. 응답 타입을 손으로 다시 쓰지 않는다.
// schema.d.ts는 `npm run gen:types`로 FastAPI OpenAPI 문서에서 만든다.
import type { components } from "./schema";

type S = components["schemas"];

export type RuntimeEvent = S["RuntimeEvent"];
export type RuntimeStatus = S["RuntimeStatus"];
export type VerificationResult = S["VerificationResult"];
export type RevalidationRequest = S["RevalidationRequest"];
export type EnvironmentDoc = S["Environment"];
export type PolicyDoc = S["Policy"];
export type EvidenceItem = S["EvidenceItem"];
export type Verdict = S["Common_verdict"];
export type DataOrigin = S["Common_data_origin"];
export type PolicyRef = S["Common_policy_ref"];
export type ObjectRef = S["Common_object_ref"];

export type Outcome = RuntimeEvent["outcome"];
export type Check = VerificationResult["checks"][number];
export type Side = Check["sides"][string];

export type SourceInfo = S["SourceInfo"];
export type InvalidItem = S["InvalidItem"];
export type Meta = S["Meta"];
export type Overview = S["Overview"];
export type RequestRow = S["RequestRow"];
export type RequestList = S["RequestList"];
export type RequestDetail = S["RequestDetail"];
export type RequestSummary = S["RequestSummary"];
export type RuntimeEventList = S["RuntimeEventList"];
export type RuntimeStatusResponse = S["RuntimeStatusResponse"];
export type RuntimeComparisons = S["RuntimeComparisons"];
export type ComparisonTable = S["ComparisonTable"];
export type VerificationList = S["VerificationList"];
export type VerificationDetail = S["VerificationDetail"];
export type RevalidationList = S["RevalidationList"];
export type EvidenceList = S["EvidenceList"];
export type EvidenceEntry = S["EvidenceEntry"];
export type RevisionMeta = S["RevisionMeta"];
export type RevisionList = S["RevisionList"];
export type EnvironmentRevision = S["EnvironmentRevision"];
export type PolicyRevision = S["PolicyRevision"];
export type EnvironmentSaved = S["EnvironmentSaved"];
export type PolicySaved = S["PolicySaved"];
export type SaveRejected = S["SaveRejected"];
export type FieldIssue = S["FieldIssue"];
export type RevisionDiff = S["RevisionDiff"];
