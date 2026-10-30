// Generated from CONTRACTS/openapi.json + operation_catalog.json; do not edit.
export interface ActorContext { principal_id: string; issuer: string; allowed_project_ids: string[]; trusted_proof_ref: string; }

export interface ArtifactSummary {
  "artifact_id": string;
  "kind": string;
  "content_digest": string;
}
export interface BibleSummary {
  "bible_id": string;
  "kind": string;
  "active_edition_id": string;
}
export interface BudgetSummary {
  "budget_id": string;
  "limit_amount": number;
  "reserved_amount": number;
  "spent_amount": number;
}
export interface CabalSummary {
  "cabal_id": string;
  "name": string;
  "state": string;
}
export interface CaseSummary {
  "case_id": string;
  "subject_id": string;
  "state": string;
}
export interface DecisionSummary {
  "decision_id": string;
  "subject_type": string;
  "subject_id": string;
  "action": string;
  "effect_digest": string;
  "state": string;
}
export interface DeliberationSummary {
  "deliberation_id": string;
  "subject_digest": string;
  "state": string;
}
export interface DepartmentSummary {
  "department_id": string;
  "name": string;
}
export interface Error {
  "code": "invalid" | "missing" | "forbidden" | "conflict" | "stale_revision" | "stale_snapshot" | "requires_owner" | "capability_unavailable" | "qa_failed" | "provider_unavailable" | "unknown_effect" | "internal";
  "message": string;
  "request_id": string | null;
  "related_refs": Array<string>;
}
export interface Event {
  "project_id": string;
  "event_seq": number;
  "event_type": string;
  "subject_id": string | null;
  "payload_digest": string;
}
export interface FeatureSummary {
  "feature_id": string;
  "snapshot_id": string;
  "state": string;
}
export interface GrantedToolSummary {
  "grant_id": string;
  "package_digest": string;
  "entrypoint_id": string;
  "revoked": boolean;
}
export interface NotificationSummary {
  "notification_id": string;
  "kind": string;
  "delivery_state": string;
}
export interface PackageSummary {
  "package_digest": string;
  "kind": string;
  "enabled": boolean;
}
export interface PolicyRuleInput {
  "rule_id": string;
  "subject_type": string;
  "action": string;
  "scope_digest": string;
  "disposition": "delegated" | "owner_required" | "forbidden";
}
export interface ProjectGrantSummary {
  "principal_id": string;
  "grant_code": string;
}
export interface ProjectSummary {
  "project_id": string;
  "name": string;
  "revision": number;
  "state": string;
}
export interface ReleaseSummary {
  "release_id": string;
  "source_revision_id": string;
  "state": string;
}
export interface ReportSummary {
  "report_id": string;
  "report_digest": string;
  "delivery_state": string;
}
export interface ReviewSummary {
  "review_id": string;
  "submission_id": string;
  "verdict": string;
}
export interface RoleSummary {
  "role_id": string;
  "role_revision": string;
  "state": string;
}
export interface SessionSummary {
  "session_id": string;
  "task_id": string;
  "state": string;
}
export interface TaskSummary {
  "task_id": string;
  "feature_id": string;
  "state": string;
  "priority": number;
  "revision": number;
}
export interface ToolGrantInput {
  "package_digest": string;
  "entrypoint_id": string;
  "workspace_id": string;
  "grant_digest": string;
}
export interface ackNotificationRequest {
  "request_id": string;
  "expected_state": string;
}
export interface ackNotificationResponse {
  "notification_id": string;
  "acknowledged": boolean;
}
export interface ackRelayRequest {
  "request_id": string;
  "session_id": string;
}
export interface ackRelayResponse {
  "message_id": string;
  "acknowledged": boolean;
}
export interface assignTaskRequest {
  "request_id": string;
  "cabal_id": string;
  "expected_revision": number;
}
export interface assignTaskResponse {
  "task_id": string;
  "cabal_id": string;
  "revision": number;
}
export interface attachConnectorRequest {
  "request_id": string;
  "provider_key": string;
  "credential_ref": string;
}
export interface attachConnectorResponse {
  "account_id": string;
  "state": string;
}
export interface cancelTaskRequest {
  "request_id": string;
  "expected_revision": number;
  "reason": string;
}
export interface cancelTaskResponse {
  "task_id": string;
  "state": string;
}
export interface cancelWorkerRequest {
  "request_id": string;
  "reason": string;
}
export interface cancelWorkerResponse {
  "session_id": string;
  "state": string;
}
export interface configureBudgetRequest {
  "request_id": string;
  "limit_amount": number;
  "expected_revision": number;
}
export interface configureBudgetResponse {
  "budget_id": string;
  "limit_amount": number;
}
export interface configureProjectRequest {
  "request_id": string;
  "config_digest": string;
  "expected_revision": number;
}
export interface configureProjectResponse {
  "project_id": string;
  "revision": number;
}
export interface createBibleRequest {
  "request_id": string;
  "title": string;
  "kind": string;
}
export interface createBibleResponse {
  "bible_id": string;
  "kind": string;
}
export interface createCabalRequest {
  "request_id": string;
  "name": string;
  "charter_digest": string;
}
export interface createCabalResponse {
  "cabal_id": string;
  "revision": number;
}
export interface createDeliberationRequest {
  "request_id": string;
  "subject_digest": string;
  "record_digest": string;
}
export interface createDeliberationResponse {
  "deliberation_id": string;
  "state": string;
}
export interface createProjectRequest {
  "request_id": string;
  "name": string;
  "charter_digest": string;
}
export interface createProjectResponse {
  "project_id": string;
  "revision": number;
}
export interface diffBibleResponse {
  "changed_clause_ids": Array<string>;
  "impacted_task_ids": Array<string>;
}
export interface disablePackRequest {
  "request_id": string;
  "package_digest": string;
}
export interface disablePackResponse {
  "package_digest": string;
  "enabled": boolean;
}
export interface dispatchTaskRequest {
  "request_id": string;
  "expected_revision": number;
  "worker_id": string;
}
export interface dispatchTaskResponse {
  "task_id": string;
  "lease_generation": number;
}
export interface enablePackRequest {
  "request_id": string;
  "package_digest": string;
  "grants": Array<ToolGrantInput>;
}
export interface enablePackResponse {
  "package_digest": string;
  "enabled": boolean;
}
export interface getArtifactResponse {
  "artifact_id": string;
  "content_digest": string;
  "kind": string;
}
export interface getBudgetsResponse {
  "budgets": Array<BudgetSummary>;
}
export interface getCanonResponse {
  "snapshot_id": string;
  "design_edition_id": string;
  "technical_edition_id": string;
}
export interface getProjectPolicyResponse {
  "policy_id": string;
  "revision": number;
  "policy_digest": string;
}
export interface getProjectResponse {
  "project_id": string;
  "name": string;
  "revision": number;
}
export interface grantProjectAccessRequest {
  "request_id": string;
  "principal_id": string;
  "grant_code": string;
}
export interface grantProjectAccessResponse {
  "principal_id": string;
  "grant_code": string;
}
export interface importRolesRequest {
  "request_id": string;
  "catalog_digest": string;
  "role_manifest_digests": Array<string>;
}
export interface importRolesResponse {
  "catalog_digest": string;
  "installed_count": number;
}
export interface inspectIntegrationResponse {
  "intent_id": string;
  "state": string;
}
export interface listArtifactsResponse {
  "artifacts": Array<ArtifactSummary>;
}
export interface listBiblesResponse {
  "bibles": Array<BibleSummary>;
}
export interface listCabalsResponse {
  "cabals": Array<CabalSummary>;
}
export interface listDecisionsResponse {
  "decisions": Array<DecisionSummary>;
}
export interface listDeliberationsResponse {
  "deliberations": Array<DeliberationSummary>;
}
export interface listDepartmentsResponse {
  "departments": Array<DepartmentSummary>;
}
export interface listEventsResponse {
  "events": Array<Event>;
  "next_cursor": number;
}
export interface listFeaturesResponse {
  "features": Array<FeatureSummary>;
}
export interface listNotificationsResponse {
  "notifications": Array<NotificationSummary>;
}
export interface listOversightCasesResponse {
  "cases": Array<CaseSummary>;
}
export interface listPacksResponse {
  "packages": Array<PackageSummary>;
}
export interface listProjectGrantsResponse {
  "grants": Array<ProjectGrantSummary>;
}
export interface listProjectsResponse {
  "items": Array<ProjectSummary>;
}
export interface listReleasesResponse {
  "releases": Array<ReleaseSummary>;
}
export interface listReportsResponse {
  "reports": Array<ReportSummary>;
}
export interface listReviewsResponse {
  "reviews": Array<ReviewSummary>;
}
export interface listRolesResponse {
  "roles": Array<RoleSummary>;
}
export interface listTasksResponse {
  "tasks": Array<TaskSummary>;
}
export interface listToolGrantsResponse {
  "grants": Array<GrantedToolSummary>;
}
export interface listWorkersResponse {
  "sessions": Array<SessionSummary>;
}
export interface observeEffectResponse {
  "effect_id": string;
  "state": string;
}
export interface observeRecoveryResponse {
  "recovery_state": string;
  "outstanding_effect_ids": Array<string>;
}
export interface openOversightCaseRequest {
  "request_id": string;
  "subject_id": string;
  "evidence_digest": string;
}
export interface openOversightCaseResponse {
  "case_id": string;
  "state": string;
}
export interface planFeatureRequest {
  "request_id": string;
  "snapshot_id": string;
  "expected_revision": number;
}
export interface planFeatureResponse {
  "feature_id": string;
  "task_ids": Array<string>;
}
export interface preflightProjectResponse {
  "project_id": string;
  "ready": boolean;
  "missing_capabilities": Array<string>;
}
export interface prepareReleaseRequest {
  "request_id": string;
  "source_revision_id": string;
  "snapshot_id": string;
  "artifact_ids": Array<string>;
  "release_digest": string;
}
export interface prepareReleaseResponse {
  "release_id": string;
  "state": string;
}
export interface promoteIntegrationRequest {
  "request_id": string;
  "expected_revision_id": string;
  "qa_review_ids": Array<string>;
}
export interface promoteIntegrationResponse {
  "source_revision_id": string;
  "state": string;
}
export interface publishBibleRequest {
  "request_id": string;
  "intent_id": string;
  "decision_id": string;
  "effect_digest": string;
  "expected_edition_id"?: string;
}
export interface publishBibleResponse {
  "edition_id": string;
  "snapshot_id": string;
}
export interface publishReleaseRequest {
  "request_id": string;
  "source_revision_id": string;
  "snapshot_id": string;
  "effect_digest": string;
  "decision_id": string;
}
export interface publishReleaseResponse {
  "release_id": string;
  "state": string;
}
export interface reconcileEffectRequest {
  "request_id": string;
  "expected_state"?: string;
}
export interface reconcileEffectResponse {
  "effect_id": string;
  "state": string;
}
export interface requestDecisionRequest {
  "request_id": string;
  "subject_type": string;
  "subject_id": string;
  "action": string;
  "effect_digest": string;
  "snapshot_id"?: string;
}
export interface requestDecisionResponse {
  "decision_id": string;
  "state": string;
}
export interface requestReportRequest {
  "request_id": string;
  "report_profile_digest": string;
}
export interface requestReportResponse {
  "report_id": string;
  "state": string;
}
export interface requestRestoreRequest {
  "request_id": string;
  "backup_digest": string;
  "expected_revision": number;
}
export interface requestRestoreResponse {
  "recovery_id": string;
  "state": string;
}
export interface requestReviewRequest {
  "request_id": string;
  "submission_id": string;
  "acceptance_digest": string;
}
export interface requestReviewResponse {
  "review_id": string;
  "state": string;
}
export interface resolveDecisionRequest {
  "request_id": string;
  "decision": string;
  "expected_effect_digest": string;
  "expected_revision": number;
}
export interface resolveDecisionResponse {
  "decision_id": string;
  "state": string;
}
export interface resolveOversightCaseRequest {
  "request_id": string;
  "resolution_digest": string;
}
export interface resolveOversightCaseResponse {
  "case_id": string;
  "state": string;
}
export interface resumeWorkerRequest {
  "request_id": string;
  "checkpoint_id": string;
}
export interface resumeWorkerResponse {
  "session_id": string;
  "state": string;
}
export interface revokeProjectAccessRequest {
  "request_id": string;
  "principal_id": string;
  "grant_code": string;
}
export interface revokeProjectAccessResponse {
  "principal_id": string;
  "grant_code": string;
}
export interface revokeToolGrantRequest {
  "request_id": string;
  "expected_state": string;
}
export interface revokeToolGrantResponse {
  "grant_id": string;
  "revoked": boolean;
}
export interface runBuildRequest {
  "request_id": string;
  "source_revision_id": string;
  "build_target": string;
}
export interface runBuildResponse {
  "ci_id": string;
  "state": string;
}
export interface sendRelayRequest {
  "request_id": string;
  "from_session_id": string;
  "to_session_id": string;
  "payload_digest": string;
}
export interface sendRelayResponse {
  "message_id": string;
  "state": string;
}
export interface setProjectPolicyRequest {
  "request_id": string;
  "policy_digest": string;
  "expected_revision": number;
  "rules": Array<PolicyRuleInput>;
}
export interface setProjectPolicyResponse {
  "policy_id": string;
  "revision": number;
  "policy_digest": string;
}
export interface staffCabalRequest {
  "request_id": string;
  "member_id": string;
  "role_id": string;
  "permission_digest": string;
}
export interface staffCabalResponse {
  "cabal_id": string;
  "member_id": string;
}
export interface stageBibleRequest {
  "request_id": string;
  "content_digest": string;
  "manifest_digest": string;
  "expected_edition_id"?: string;
}
export interface stageBibleResponse {
  "intent_id": string;
  "state": string;
}
export interface stageIntegrationRequest {
  "request_id": string;
  "submission_id": string;
  "expected_revision_id": string;
}
export interface stageIntegrationResponse {
  "intent_id": string;
  "state": string;
}
export interface startWorkerRequest {
  "request_id": string;
  "task_id": string;
  "lease_generation": number;
  "package_digest": string;
}
export interface startWorkerResponse {
  "session_id": string;
  "state": string;
}
export interface submitFeatureRequest {
  "request_id": string;
  "brief_digest": string;
  "snapshot_id": string;
  "acceptance_digest": string;
}
export interface submitFeatureResponse {
  "feature_id": string;
  "state": string;
}
export interface submitReviewRequest {
  "request_id": string;
  "verdict": string;
  "evidence_digests": Array<string>;
  "source_revision_id"?: string;
}
export interface submitReviewResponse {
  "review_id": string;
  "verdict": string;
}
export interface StudioApiOperations {
  "createProject": { method: "POST"; path: "/v1/projects"; params: Record<string, never>; query: Record<string, never>; body: createProjectRequest; response: createProjectResponse; };
  "listProjects": { method: "GET"; path: "/v1/projects"; params: Record<string, never>; query: Record<string, never>; body: never; response: listProjectsResponse; };
  "getProject": { method: "GET"; path: "/v1/projects/{project_id}"; params: { project_id: string }; query: Record<string, never>; body: never; response: getProjectResponse; };
  "configureProject": { method: "PUT"; path: "/v1/projects/{project_id}/config"; params: { project_id: string }; query: Record<string, never>; body: configureProjectRequest; response: configureProjectResponse; };
  "preflightProject": { method: "GET"; path: "/v1/projects/{project_id}/preflight"; params: { project_id: string }; query: Record<string, never>; body: never; response: preflightProjectResponse; };
  "listPacks": { method: "GET"; path: "/v1/projects/{project_id}/packages"; params: { project_id: string }; query: Record<string, never>; body: never; response: listPacksResponse; };
  "enablePack": { method: "POST"; path: "/v1/projects/{project_id}/packages/enable"; params: { project_id: string }; query: Record<string, never>; body: enablePackRequest; response: enablePackResponse; };
  "disablePack": { method: "POST"; path: "/v1/projects/{project_id}/packages/disable"; params: { project_id: string }; query: Record<string, never>; body: disablePackRequest; response: disablePackResponse; };
  "createCabal": { method: "POST"; path: "/v1/projects/{project_id}/cabals"; params: { project_id: string }; query: Record<string, never>; body: createCabalRequest; response: createCabalResponse; };
  "staffCabal": { method: "POST"; path: "/v1/projects/{project_id}/cabals/{cabal_id}/staff"; params: { project_id: string; cabal_id: string }; query: Record<string, never>; body: staffCabalRequest; response: staffCabalResponse; };
  "listCabals": { method: "GET"; path: "/v1/projects/{project_id}/cabals"; params: { project_id: string }; query: Record<string, never>; body: never; response: listCabalsResponse; };
  "listBibles": { method: "GET"; path: "/v1/projects/{project_id}/bibles"; params: { project_id: string }; query: Record<string, never>; body: never; response: listBiblesResponse; };
  "createBible": { method: "POST"; path: "/v1/projects/{project_id}/bibles"; params: { project_id: string }; query: Record<string, never>; body: createBibleRequest; response: createBibleResponse; };
  "stageBible": { method: "POST"; path: "/v1/projects/{project_id}/bibles/{bible_id}/stages"; params: { project_id: string; bible_id: string }; query: Record<string, never>; body: stageBibleRequest; response: stageBibleResponse; };
  "diffBible": { method: "GET"; path: "/v1/projects/{project_id}/bibles/{bible_id}/diff"; params: { project_id: string; bible_id: string }; query: Record<string, never>; body: never; response: diffBibleResponse; };
  "publishBible": { method: "POST"; path: "/v1/projects/{project_id}/bibles/{bible_id}/publish"; params: { project_id: string; bible_id: string }; query: Record<string, never>; body: publishBibleRequest; response: publishBibleResponse; };
  "getCanon": { method: "GET"; path: "/v1/projects/{project_id}/canon"; params: { project_id: string }; query: Record<string, never>; body: never; response: getCanonResponse; };
  "requestDecision": { method: "POST"; path: "/v1/projects/{project_id}/decisions"; params: { project_id: string }; query: Record<string, never>; body: requestDecisionRequest; response: requestDecisionResponse; };
  "resolveDecision": { method: "POST"; path: "/v1/projects/{project_id}/decisions/{decision_id}/resolve"; params: { project_id: string; decision_id: string }; query: Record<string, never>; body: resolveDecisionRequest; response: resolveDecisionResponse; };
  "listDecisions": { method: "GET"; path: "/v1/projects/{project_id}/decisions"; params: { project_id: string }; query: Record<string, never>; body: never; response: listDecisionsResponse; };
  "submitFeature": { method: "POST"; path: "/v1/projects/{project_id}/features"; params: { project_id: string }; query: Record<string, never>; body: submitFeatureRequest; response: submitFeatureResponse; };
  "planFeature": { method: "POST"; path: "/v1/projects/{project_id}/features/{feature_id}/plan"; params: { project_id: string; feature_id: string }; query: Record<string, never>; body: planFeatureRequest; response: planFeatureResponse; };
  "listFeatures": { method: "GET"; path: "/v1/projects/{project_id}/features"; params: { project_id: string }; query: Record<string, never>; body: never; response: listFeaturesResponse; };
  "listTasks": { method: "GET"; path: "/v1/projects/{project_id}/tasks"; params: { project_id: string }; query: Record<string, never>; body: never; response: listTasksResponse; };
  "cancelTask": { method: "POST"; path: "/v1/projects/{project_id}/tasks/{task_id}/cancel"; params: { project_id: string; task_id: string }; query: Record<string, never>; body: cancelTaskRequest; response: cancelTaskResponse; };
  "dispatchTask": { method: "POST"; path: "/v1/projects/{project_id}/tasks/{task_id}/dispatch"; params: { project_id: string; task_id: string }; query: Record<string, never>; body: dispatchTaskRequest; response: dispatchTaskResponse; };
  "startWorker": { method: "POST"; path: "/v1/projects/{project_id}/workers/start"; params: { project_id: string }; query: Record<string, never>; body: startWorkerRequest; response: startWorkerResponse; };
  "resumeWorker": { method: "POST"; path: "/v1/projects/{project_id}/workers/{session_id}/resume"; params: { project_id: string; session_id: string }; query: Record<string, never>; body: resumeWorkerRequest; response: resumeWorkerResponse; };
  "cancelWorker": { method: "POST"; path: "/v1/projects/{project_id}/workers/{session_id}/cancel"; params: { project_id: string; session_id: string }; query: Record<string, never>; body: cancelWorkerRequest; response: cancelWorkerResponse; };
  "listWorkers": { method: "GET"; path: "/v1/projects/{project_id}/workers"; params: { project_id: string }; query: Record<string, never>; body: never; response: listWorkersResponse; };
  "requestReview": { method: "POST"; path: "/v1/projects/{project_id}/reviews"; params: { project_id: string }; query: Record<string, never>; body: requestReviewRequest; response: requestReviewResponse; };
  "submitReview": { method: "POST"; path: "/v1/projects/{project_id}/reviews/{review_id}/verdict"; params: { project_id: string; review_id: string }; query: Record<string, never>; body: submitReviewRequest; response: submitReviewResponse; };
  "listReviews": { method: "GET"; path: "/v1/projects/{project_id}/reviews"; params: { project_id: string }; query: Record<string, never>; body: never; response: listReviewsResponse; };
  "listArtifacts": { method: "GET"; path: "/v1/projects/{project_id}/artifacts"; params: { project_id: string }; query: Record<string, never>; body: never; response: listArtifactsResponse; };
  "getArtifact": { method: "GET"; path: "/v1/projects/{project_id}/artifacts/{artifact_id}"; params: { project_id: string; artifact_id: string }; query: Record<string, never>; body: never; response: getArtifactResponse; };
  "attachConnector": { method: "POST"; path: "/v1/projects/{project_id}/connectors"; params: { project_id: string }; query: Record<string, never>; body: attachConnectorRequest; response: attachConnectorResponse; };
  "runBuild": { method: "POST"; path: "/v1/projects/{project_id}/builds"; params: { project_id: string }; query: Record<string, never>; body: runBuildRequest; response: runBuildResponse; };
  "stageIntegration": { method: "POST"; path: "/v1/projects/{project_id}/integrations"; params: { project_id: string }; query: Record<string, never>; body: stageIntegrationRequest; response: stageIntegrationResponse; };
  "promoteIntegration": { method: "POST"; path: "/v1/projects/{project_id}/integrations/{intent_id}/promote"; params: { project_id: string; intent_id: string }; query: Record<string, never>; body: promoteIntegrationRequest; response: promoteIntegrationResponse; };
  "publishRelease": { method: "POST"; path: "/v1/projects/{project_id}/releases/{release_id}/publish"; params: { project_id: string; release_id: string }; query: Record<string, never>; body: publishReleaseRequest; response: publishReleaseResponse; };
  "listReleases": { method: "GET"; path: "/v1/projects/{project_id}/releases"; params: { project_id: string }; query: Record<string, never>; body: never; response: listReleasesResponse; };
  "reconcileEffect": { method: "POST"; path: "/v1/projects/{project_id}/effects/{effect_id}/reconcile"; params: { project_id: string; effect_id: string }; query: Record<string, never>; body: reconcileEffectRequest; response: reconcileEffectResponse; };
  "configureBudget": { method: "PUT"; path: "/v1/projects/{project_id}/budgets/{budget_id}"; params: { project_id: string; budget_id: string }; query: Record<string, never>; body: configureBudgetRequest; response: configureBudgetResponse; };
  "getBudgets": { method: "GET"; path: "/v1/projects/{project_id}/budgets"; params: { project_id: string }; query: Record<string, never>; body: never; response: getBudgetsResponse; };
  "listReports": { method: "GET"; path: "/v1/projects/{project_id}/reports"; params: { project_id: string }; query: Record<string, never>; body: never; response: listReportsResponse; };
  "listEvents": { method: "GET"; path: "/v1/projects/{project_id}/events"; params: { project_id: string }; query: { after?: number; limit?: number }; body: never; response: listEventsResponse; };
  "requestRestore": { method: "POST"; path: "/v1/projects/{project_id}/recovery"; params: { project_id: string }; query: Record<string, never>; body: requestRestoreRequest; response: requestRestoreResponse; };
  "prepareRelease": { method: "POST"; path: "/v1/projects/{project_id}/releases/prepare"; params: { project_id: string }; query: Record<string, never>; body: prepareReleaseRequest; response: prepareReleaseResponse; };
  "importRoles": { method: "POST"; path: "/v1/projects/{project_id}/roles/import"; params: { project_id: string }; query: Record<string, never>; body: importRolesRequest; response: importRolesResponse; };
  "listRoles": { method: "GET"; path: "/v1/projects/{project_id}/roles"; params: { project_id: string }; query: Record<string, never>; body: never; response: listRolesResponse; };
  "listDepartments": { method: "GET"; path: "/v1/projects/{project_id}/departments"; params: { project_id: string }; query: Record<string, never>; body: never; response: listDepartmentsResponse; };
  "createDeliberation": { method: "POST"; path: "/v1/projects/{project_id}/cabals/{cabal_id}/deliberations"; params: { project_id: string; cabal_id: string }; query: Record<string, never>; body: createDeliberationRequest; response: createDeliberationResponse; };
  "listDeliberations": { method: "GET"; path: "/v1/projects/{project_id}/cabals/{cabal_id}/deliberations"; params: { project_id: string; cabal_id: string }; query: Record<string, never>; body: never; response: listDeliberationsResponse; };
  "assignTask": { method: "POST"; path: "/v1/projects/{project_id}/tasks/{task_id}/assign"; params: { project_id: string; task_id: string }; query: Record<string, never>; body: assignTaskRequest; response: assignTaskResponse; };
  "sendRelay": { method: "POST"; path: "/v1/projects/{project_id}/relays"; params: { project_id: string }; query: Record<string, never>; body: sendRelayRequest; response: sendRelayResponse; };
  "ackRelay": { method: "POST"; path: "/v1/projects/{project_id}/relays/{message_id}/ack"; params: { project_id: string; message_id: string }; query: Record<string, never>; body: ackRelayRequest; response: ackRelayResponse; };
  "openOversightCase": { method: "POST"; path: "/v1/projects/{project_id}/oversight"; params: { project_id: string }; query: Record<string, never>; body: openOversightCaseRequest; response: openOversightCaseResponse; };
  "resolveOversightCase": { method: "POST"; path: "/v1/projects/{project_id}/oversight/{case_id}/resolve"; params: { project_id: string; case_id: string }; query: Record<string, never>; body: resolveOversightCaseRequest; response: resolveOversightCaseResponse; };
  "listOversightCases": { method: "GET"; path: "/v1/projects/{project_id}/oversight"; params: { project_id: string }; query: Record<string, never>; body: never; response: listOversightCasesResponse; };
  "requestReport": { method: "POST"; path: "/v1/projects/{project_id}/reports"; params: { project_id: string }; query: Record<string, never>; body: requestReportRequest; response: requestReportResponse; };
  "listNotifications": { method: "GET"; path: "/v1/projects/{project_id}/notifications"; params: { project_id: string }; query: Record<string, never>; body: never; response: listNotificationsResponse; };
  "ackNotification": { method: "POST"; path: "/v1/projects/{project_id}/notifications/{notification_id}/ack"; params: { project_id: string; notification_id: string }; query: Record<string, never>; body: ackNotificationRequest; response: ackNotificationResponse; };
  "observeEffect": { method: "GET"; path: "/v1/projects/{project_id}/effects/{effect_id}"; params: { project_id: string; effect_id: string }; query: Record<string, never>; body: never; response: observeEffectResponse; };
  "inspectIntegration": { method: "GET"; path: "/v1/projects/{project_id}/integrations/{intent_id}"; params: { project_id: string; intent_id: string }; query: Record<string, never>; body: never; response: inspectIntegrationResponse; };
  "observeRecovery": { method: "GET"; path: "/v1/projects/{project_id}/recovery"; params: { project_id: string }; query: Record<string, never>; body: never; response: observeRecoveryResponse; };
  "getProjectPolicy": { method: "GET"; path: "/v1/projects/{project_id}/policy"; params: { project_id: string }; query: Record<string, never>; body: never; response: getProjectPolicyResponse; };
  "setProjectPolicy": { method: "PUT"; path: "/v1/projects/{project_id}/policy"; params: { project_id: string }; query: Record<string, never>; body: setProjectPolicyRequest; response: setProjectPolicyResponse; };
  "listProjectGrants": { method: "GET"; path: "/v1/projects/{project_id}/access"; params: { project_id: string }; query: Record<string, never>; body: never; response: listProjectGrantsResponse; };
  "grantProjectAccess": { method: "POST"; path: "/v1/projects/{project_id}/access/grant"; params: { project_id: string }; query: Record<string, never>; body: grantProjectAccessRequest; response: grantProjectAccessResponse; };
  "revokeProjectAccess": { method: "POST"; path: "/v1/projects/{project_id}/access/revoke"; params: { project_id: string }; query: Record<string, never>; body: revokeProjectAccessRequest; response: revokeProjectAccessResponse; };
  "listToolGrants": { method: "GET"; path: "/v1/projects/{project_id}/tools/grants"; params: { project_id: string }; query: Record<string, never>; body: never; response: listToolGrantsResponse; };
  "revokeToolGrant": { method: "POST"; path: "/v1/projects/{project_id}/tools/grants/{grant_id}/revoke"; params: { project_id: string; grant_id: string }; query: Record<string, never>; body: revokeToolGrantRequest; response: revokeToolGrantResponse; };
}
export type StudioOperationId = keyof StudioApiOperations;

