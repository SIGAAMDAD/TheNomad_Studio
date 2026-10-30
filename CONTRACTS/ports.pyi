# Generated from CONTRACTS/openapi.json + operation_catalog.json; do not edit.
from __future__ import annotations
from typing import Protocol,TypedDict,Required,NotRequired,Any,Literal

class ActorContext(TypedDict):
    principal_id: str
    issuer: str
    allowed_project_ids: list[str]
    trusted_proof_ref: str

class ArtifactSummary(TypedDict):
    artifact_id: Required[str]
    kind: Required[str]
    content_digest: Required[str]

class BibleSummary(TypedDict):
    bible_id: Required[str]
    kind: Required[str]
    active_edition_id: Required[str]

class BudgetSummary(TypedDict):
    budget_id: Required[str]
    limit_amount: Required[int]
    reserved_amount: Required[int]
    spent_amount: Required[int]

class CabalSummary(TypedDict):
    cabal_id: Required[str]
    name: Required[str]
    state: Required[str]

class CaseSummary(TypedDict):
    case_id: Required[str]
    subject_id: Required[str]
    state: Required[str]

class DecisionSummary(TypedDict):
    decision_id: Required[str]
    subject_type: Required[str]
    subject_id: Required[str]
    action: Required[str]
    effect_digest: Required[str]
    state: Required[str]

class DeliberationSummary(TypedDict):
    deliberation_id: Required[str]
    subject_digest: Required[str]
    state: Required[str]

class DepartmentSummary(TypedDict):
    department_id: Required[str]
    name: Required[str]

class Error(TypedDict):
    code: Required[Literal['invalid', 'missing', 'forbidden', 'conflict', 'stale_revision', 'stale_snapshot', 'requires_owner', 'capability_unavailable', 'qa_failed', 'provider_unavailable', 'unknown_effect', 'internal']]
    message: Required[str]
    request_id: Required[str | None]
    related_refs: Required[list[str]]

class Event(TypedDict):
    project_id: Required[str]
    event_seq: Required[int]
    event_type: Required[str]
    subject_id: Required[str | None]
    payload_digest: Required[str]

class FeatureSummary(TypedDict):
    feature_id: Required[str]
    snapshot_id: Required[str]
    state: Required[str]

class GrantedToolSummary(TypedDict):
    grant_id: Required[str]
    package_digest: Required[str]
    entrypoint_id: Required[str]
    revoked: Required[bool]

class NotificationSummary(TypedDict):
    notification_id: Required[str]
    kind: Required[str]
    delivery_state: Required[str]

class PackageSummary(TypedDict):
    package_digest: Required[str]
    kind: Required[str]
    enabled: Required[bool]

class PolicyRuleInput(TypedDict):
    rule_id: Required[str]
    subject_type: Required[str]
    action: Required[str]
    scope_digest: Required[str]
    disposition: Required[Literal['delegated', 'owner_required', 'forbidden']]

class ProjectGrantSummary(TypedDict):
    principal_id: Required[str]
    grant_code: Required[str]

class ProjectSummary(TypedDict):
    project_id: Required[str]
    name: Required[str]
    revision: Required[int]
    state: Required[str]

class ReleaseSummary(TypedDict):
    release_id: Required[str]
    source_revision_id: Required[str]
    state: Required[str]

class ReportSummary(TypedDict):
    report_id: Required[str]
    report_digest: Required[str]
    delivery_state: Required[str]

class ReviewSummary(TypedDict):
    review_id: Required[str]
    submission_id: Required[str]
    verdict: Required[str]

class RoleSummary(TypedDict):
    role_id: Required[str]
    role_revision: Required[str]
    state: Required[str]

class SessionSummary(TypedDict):
    session_id: Required[str]
    task_id: Required[str]
    state: Required[str]

class TaskSummary(TypedDict):
    task_id: Required[str]
    feature_id: Required[str]
    state: Required[str]
    priority: Required[int]
    revision: Required[int]

class ToolGrantInput(TypedDict):
    package_digest: Required[str]
    entrypoint_id: Required[str]
    workspace_id: Required[str]
    grant_digest: Required[str]

class ackNotificationRequest(TypedDict):
    request_id: Required[str]
    expected_state: Required[str]

class ackNotificationResponse(TypedDict):
    notification_id: Required[str]
    acknowledged: Required[bool]

class ackRelayRequest(TypedDict):
    request_id: Required[str]
    session_id: Required[str]

class ackRelayResponse(TypedDict):
    message_id: Required[str]
    acknowledged: Required[bool]

class assignTaskRequest(TypedDict):
    request_id: Required[str]
    cabal_id: Required[str]
    expected_revision: Required[int]

class assignTaskResponse(TypedDict):
    task_id: Required[str]
    cabal_id: Required[str]
    revision: Required[int]

class attachConnectorRequest(TypedDict):
    request_id: Required[str]
    provider_key: Required[str]
    credential_ref: Required[str]

class attachConnectorResponse(TypedDict):
    account_id: Required[str]
    state: Required[str]

class cancelTaskRequest(TypedDict):
    request_id: Required[str]
    expected_revision: Required[int]
    reason: Required[str]

class cancelTaskResponse(TypedDict):
    task_id: Required[str]
    state: Required[str]

class cancelWorkerRequest(TypedDict):
    request_id: Required[str]
    reason: Required[str]

class cancelWorkerResponse(TypedDict):
    session_id: Required[str]
    state: Required[str]

class configureBudgetRequest(TypedDict):
    request_id: Required[str]
    limit_amount: Required[int]
    expected_revision: Required[int]

class configureBudgetResponse(TypedDict):
    budget_id: Required[str]
    limit_amount: Required[int]

class configureProjectRequest(TypedDict):
    request_id: Required[str]
    config_digest: Required[str]
    expected_revision: Required[int]

class configureProjectResponse(TypedDict):
    project_id: Required[str]
    revision: Required[int]

class createBibleRequest(TypedDict):
    request_id: Required[str]
    title: Required[str]
    kind: Required[str]

class createBibleResponse(TypedDict):
    bible_id: Required[str]
    kind: Required[str]

class createCabalRequest(TypedDict):
    request_id: Required[str]
    name: Required[str]
    charter_digest: Required[str]

class createCabalResponse(TypedDict):
    cabal_id: Required[str]
    revision: Required[int]

class createDeliberationRequest(TypedDict):
    request_id: Required[str]
    subject_digest: Required[str]
    record_digest: Required[str]

class createDeliberationResponse(TypedDict):
    deliberation_id: Required[str]
    state: Required[str]

class createProjectRequest(TypedDict):
    request_id: Required[str]
    name: Required[str]
    charter_digest: Required[str]

class createProjectResponse(TypedDict):
    project_id: Required[str]
    revision: Required[int]

class diffBibleResponse(TypedDict):
    changed_clause_ids: Required[list[str]]
    impacted_task_ids: Required[list[str]]

class disablePackRequest(TypedDict):
    request_id: Required[str]
    package_digest: Required[str]

class disablePackResponse(TypedDict):
    package_digest: Required[str]
    enabled: Required[bool]

class dispatchTaskRequest(TypedDict):
    request_id: Required[str]
    expected_revision: Required[int]
    worker_id: Required[str]

class dispatchTaskResponse(TypedDict):
    task_id: Required[str]
    lease_generation: Required[int]

class enablePackRequest(TypedDict):
    request_id: Required[str]
    package_digest: Required[str]
    grants: Required[list[ToolGrantInput]]

class enablePackResponse(TypedDict):
    package_digest: Required[str]
    enabled: Required[bool]

class getArtifactResponse(TypedDict):
    artifact_id: Required[str]
    content_digest: Required[str]
    kind: Required[str]

class getBudgetsResponse(TypedDict):
    budgets: Required[list[BudgetSummary]]

class getCanonResponse(TypedDict):
    snapshot_id: Required[str]
    design_edition_id: Required[str]
    technical_edition_id: Required[str]

class getProjectPolicyResponse(TypedDict):
    policy_id: Required[str]
    revision: Required[int]
    policy_digest: Required[str]

class getProjectResponse(TypedDict):
    project_id: Required[str]
    name: Required[str]
    revision: Required[int]

class grantProjectAccessRequest(TypedDict):
    request_id: Required[str]
    principal_id: Required[str]
    grant_code: Required[str]

class grantProjectAccessResponse(TypedDict):
    principal_id: Required[str]
    grant_code: Required[str]

class importRolesRequest(TypedDict):
    request_id: Required[str]
    catalog_digest: Required[str]
    role_manifest_digests: Required[list[str]]

class importRolesResponse(TypedDict):
    catalog_digest: Required[str]
    installed_count: Required[int]

class inspectIntegrationResponse(TypedDict):
    intent_id: Required[str]
    state: Required[str]

class listArtifactsResponse(TypedDict):
    artifacts: Required[list[ArtifactSummary]]

class listBiblesResponse(TypedDict):
    bibles: Required[list[BibleSummary]]

class listCabalsResponse(TypedDict):
    cabals: Required[list[CabalSummary]]

class listDecisionsResponse(TypedDict):
    decisions: Required[list[DecisionSummary]]

class listDeliberationsResponse(TypedDict):
    deliberations: Required[list[DeliberationSummary]]

class listDepartmentsResponse(TypedDict):
    departments: Required[list[DepartmentSummary]]

class listEventsResponse(TypedDict):
    events: Required[list[Event]]
    next_cursor: Required[int]

class listFeaturesResponse(TypedDict):
    features: Required[list[FeatureSummary]]

class listNotificationsResponse(TypedDict):
    notifications: Required[list[NotificationSummary]]

class listOversightCasesResponse(TypedDict):
    cases: Required[list[CaseSummary]]

class listPacksResponse(TypedDict):
    packages: Required[list[PackageSummary]]

class listProjectGrantsResponse(TypedDict):
    grants: Required[list[ProjectGrantSummary]]

class listProjectsResponse(TypedDict):
    items: Required[list[ProjectSummary]]

class listReleasesResponse(TypedDict):
    releases: Required[list[ReleaseSummary]]

class listReportsResponse(TypedDict):
    reports: Required[list[ReportSummary]]

class listReviewsResponse(TypedDict):
    reviews: Required[list[ReviewSummary]]

class listRolesResponse(TypedDict):
    roles: Required[list[RoleSummary]]

class listTasksResponse(TypedDict):
    tasks: Required[list[TaskSummary]]

class listToolGrantsResponse(TypedDict):
    grants: Required[list[GrantedToolSummary]]

class listWorkersResponse(TypedDict):
    sessions: Required[list[SessionSummary]]

class observeEffectResponse(TypedDict):
    effect_id: Required[str]
    state: Required[str]

class observeRecoveryResponse(TypedDict):
    recovery_state: Required[str]
    outstanding_effect_ids: Required[list[str]]

class openOversightCaseRequest(TypedDict):
    request_id: Required[str]
    subject_id: Required[str]
    evidence_digest: Required[str]

class openOversightCaseResponse(TypedDict):
    case_id: Required[str]
    state: Required[str]

class planFeatureRequest(TypedDict):
    request_id: Required[str]
    snapshot_id: Required[str]
    expected_revision: Required[int]

class planFeatureResponse(TypedDict):
    feature_id: Required[str]
    task_ids: Required[list[str]]

class preflightProjectResponse(TypedDict):
    project_id: Required[str]
    ready: Required[bool]
    missing_capabilities: Required[list[str]]

class prepareReleaseRequest(TypedDict):
    request_id: Required[str]
    source_revision_id: Required[str]
    snapshot_id: Required[str]
    artifact_ids: Required[list[str]]
    release_digest: Required[str]

class prepareReleaseResponse(TypedDict):
    release_id: Required[str]
    state: Required[str]

class promoteIntegrationRequest(TypedDict):
    request_id: Required[str]
    expected_revision_id: Required[str]
    qa_review_ids: Required[list[str]]

class promoteIntegrationResponse(TypedDict):
    source_revision_id: Required[str]
    state: Required[str]

class publishBibleRequest(TypedDict):
    request_id: Required[str]
    intent_id: Required[str]
    decision_id: Required[str]
    effect_digest: Required[str]
    expected_edition_id: NotRequired[str]

class publishBibleResponse(TypedDict):
    edition_id: Required[str]
    snapshot_id: Required[str]

class publishReleaseRequest(TypedDict):
    request_id: Required[str]
    source_revision_id: Required[str]
    snapshot_id: Required[str]
    effect_digest: Required[str]
    decision_id: Required[str]

class publishReleaseResponse(TypedDict):
    release_id: Required[str]
    state: Required[str]

class reconcileEffectRequest(TypedDict):
    request_id: Required[str]
    expected_state: NotRequired[str]

class reconcileEffectResponse(TypedDict):
    effect_id: Required[str]
    state: Required[str]

class requestDecisionRequest(TypedDict):
    request_id: Required[str]
    subject_type: Required[str]
    subject_id: Required[str]
    action: Required[str]
    effect_digest: Required[str]
    snapshot_id: NotRequired[str]

class requestDecisionResponse(TypedDict):
    decision_id: Required[str]
    state: Required[str]

class requestReportRequest(TypedDict):
    request_id: Required[str]
    report_profile_digest: Required[str]

class requestReportResponse(TypedDict):
    report_id: Required[str]
    state: Required[str]

class requestRestoreRequest(TypedDict):
    request_id: Required[str]
    backup_digest: Required[str]
    expected_revision: Required[int]

class requestRestoreResponse(TypedDict):
    recovery_id: Required[str]
    state: Required[str]

class requestReviewRequest(TypedDict):
    request_id: Required[str]
    submission_id: Required[str]
    acceptance_digest: Required[str]

class requestReviewResponse(TypedDict):
    review_id: Required[str]
    state: Required[str]

class resolveDecisionRequest(TypedDict):
    request_id: Required[str]
    decision: Required[str]
    expected_effect_digest: Required[str]
    expected_revision: Required[int]

class resolveDecisionResponse(TypedDict):
    decision_id: Required[str]
    state: Required[str]

class resolveOversightCaseRequest(TypedDict):
    request_id: Required[str]
    resolution_digest: Required[str]

class resolveOversightCaseResponse(TypedDict):
    case_id: Required[str]
    state: Required[str]

class resumeWorkerRequest(TypedDict):
    request_id: Required[str]
    checkpoint_id: Required[str]

class resumeWorkerResponse(TypedDict):
    session_id: Required[str]
    state: Required[str]

class revokeProjectAccessRequest(TypedDict):
    request_id: Required[str]
    principal_id: Required[str]
    grant_code: Required[str]

class revokeProjectAccessResponse(TypedDict):
    principal_id: Required[str]
    grant_code: Required[str]

class revokeToolGrantRequest(TypedDict):
    request_id: Required[str]
    expected_state: Required[str]

class revokeToolGrantResponse(TypedDict):
    grant_id: Required[str]
    revoked: Required[bool]

class runBuildRequest(TypedDict):
    request_id: Required[str]
    source_revision_id: Required[str]
    build_target: Required[str]

class runBuildResponse(TypedDict):
    ci_id: Required[str]
    state: Required[str]

class sendRelayRequest(TypedDict):
    request_id: Required[str]
    from_session_id: Required[str]
    to_session_id: Required[str]
    payload_digest: Required[str]

class sendRelayResponse(TypedDict):
    message_id: Required[str]
    state: Required[str]

class setProjectPolicyRequest(TypedDict):
    request_id: Required[str]
    policy_digest: Required[str]
    expected_revision: Required[int]
    rules: Required[list[PolicyRuleInput]]

class setProjectPolicyResponse(TypedDict):
    policy_id: Required[str]
    revision: Required[int]
    policy_digest: Required[str]

class staffCabalRequest(TypedDict):
    request_id: Required[str]
    member_id: Required[str]
    role_id: Required[str]
    permission_digest: Required[str]

class staffCabalResponse(TypedDict):
    cabal_id: Required[str]
    member_id: Required[str]

class stageBibleRequest(TypedDict):
    request_id: Required[str]
    content_digest: Required[str]
    manifest_digest: Required[str]
    expected_edition_id: NotRequired[str]

class stageBibleResponse(TypedDict):
    intent_id: Required[str]
    state: Required[str]

class stageIntegrationRequest(TypedDict):
    request_id: Required[str]
    submission_id: Required[str]
    expected_revision_id: Required[str]

class stageIntegrationResponse(TypedDict):
    intent_id: Required[str]
    state: Required[str]

class startWorkerRequest(TypedDict):
    request_id: Required[str]
    task_id: Required[str]
    lease_generation: Required[int]
    package_digest: Required[str]

class startWorkerResponse(TypedDict):
    session_id: Required[str]
    state: Required[str]

class submitFeatureRequest(TypedDict):
    request_id: Required[str]
    brief_digest: Required[str]
    snapshot_id: Required[str]
    acceptance_digest: Required[str]

class submitFeatureResponse(TypedDict):
    feature_id: Required[str]
    state: Required[str]

class submitReviewRequest(TypedDict):
    request_id: Required[str]
    verdict: Required[str]
    evidence_digests: Required[list[str]]
    source_revision_id: NotRequired[str]

class submitReviewResponse(TypedDict):
    review_id: Required[str]
    verdict: Required[str]

class StudioApiPort(Protocol):
    def createProject(self, actor: ActorContext, request: createProjectRequest) -> createProjectResponse: ...
    def listProjects(self, actor: ActorContext) -> listProjectsResponse: ...
    def getProject(self, actor: ActorContext, project_id: str) -> getProjectResponse: ...
    def configureProject(self, actor: ActorContext, project_id: str, request: configureProjectRequest) -> configureProjectResponse: ...
    def preflightProject(self, actor: ActorContext, project_id: str) -> preflightProjectResponse: ...
    def listPacks(self, actor: ActorContext, project_id: str) -> listPacksResponse: ...
    def enablePack(self, actor: ActorContext, project_id: str, request: enablePackRequest) -> enablePackResponse: ...
    def disablePack(self, actor: ActorContext, project_id: str, request: disablePackRequest) -> disablePackResponse: ...
    def createCabal(self, actor: ActorContext, project_id: str, request: createCabalRequest) -> createCabalResponse: ...
    def staffCabal(self, actor: ActorContext, project_id: str, cabal_id: str, request: staffCabalRequest) -> staffCabalResponse: ...
    def listCabals(self, actor: ActorContext, project_id: str) -> listCabalsResponse: ...
    def listBibles(self, actor: ActorContext, project_id: str) -> listBiblesResponse: ...
    def createBible(self, actor: ActorContext, project_id: str, request: createBibleRequest) -> createBibleResponse: ...
    def stageBible(self, actor: ActorContext, project_id: str, bible_id: str, request: stageBibleRequest) -> stageBibleResponse: ...
    def diffBible(self, actor: ActorContext, project_id: str, bible_id: str) -> diffBibleResponse: ...
    def publishBible(self, actor: ActorContext, project_id: str, bible_id: str, request: publishBibleRequest) -> publishBibleResponse: ...
    def getCanon(self, actor: ActorContext, project_id: str) -> getCanonResponse: ...
    def requestDecision(self, actor: ActorContext, project_id: str, request: requestDecisionRequest) -> requestDecisionResponse: ...
    def resolveDecision(self, actor: ActorContext, project_id: str, decision_id: str, request: resolveDecisionRequest) -> resolveDecisionResponse: ...
    def listDecisions(self, actor: ActorContext, project_id: str) -> listDecisionsResponse: ...
    def submitFeature(self, actor: ActorContext, project_id: str, request: submitFeatureRequest) -> submitFeatureResponse: ...
    def planFeature(self, actor: ActorContext, project_id: str, feature_id: str, request: planFeatureRequest) -> planFeatureResponse: ...
    def listFeatures(self, actor: ActorContext, project_id: str) -> listFeaturesResponse: ...
    def listTasks(self, actor: ActorContext, project_id: str) -> listTasksResponse: ...
    def cancelTask(self, actor: ActorContext, project_id: str, task_id: str, request: cancelTaskRequest) -> cancelTaskResponse: ...
    def dispatchTask(self, actor: ActorContext, project_id: str, task_id: str, request: dispatchTaskRequest) -> dispatchTaskResponse: ...
    def startWorker(self, actor: ActorContext, project_id: str, request: startWorkerRequest) -> startWorkerResponse: ...
    def resumeWorker(self, actor: ActorContext, project_id: str, session_id: str, request: resumeWorkerRequest) -> resumeWorkerResponse: ...
    def cancelWorker(self, actor: ActorContext, project_id: str, session_id: str, request: cancelWorkerRequest) -> cancelWorkerResponse: ...
    def listWorkers(self, actor: ActorContext, project_id: str) -> listWorkersResponse: ...
    def requestReview(self, actor: ActorContext, project_id: str, request: requestReviewRequest) -> requestReviewResponse: ...
    def submitReview(self, actor: ActorContext, project_id: str, review_id: str, request: submitReviewRequest) -> submitReviewResponse: ...
    def listReviews(self, actor: ActorContext, project_id: str) -> listReviewsResponse: ...
    def listArtifacts(self, actor: ActorContext, project_id: str) -> listArtifactsResponse: ...
    def getArtifact(self, actor: ActorContext, project_id: str, artifact_id: str) -> getArtifactResponse: ...
    def attachConnector(self, actor: ActorContext, project_id: str, request: attachConnectorRequest) -> attachConnectorResponse: ...
    def runBuild(self, actor: ActorContext, project_id: str, request: runBuildRequest) -> runBuildResponse: ...
    def stageIntegration(self, actor: ActorContext, project_id: str, request: stageIntegrationRequest) -> stageIntegrationResponse: ...
    def promoteIntegration(self, actor: ActorContext, project_id: str, intent_id: str, request: promoteIntegrationRequest) -> promoteIntegrationResponse: ...
    def publishRelease(self, actor: ActorContext, project_id: str, release_id: str, request: publishReleaseRequest) -> publishReleaseResponse: ...
    def listReleases(self, actor: ActorContext, project_id: str) -> listReleasesResponse: ...
    def reconcileEffect(self, actor: ActorContext, project_id: str, effect_id: str, request: reconcileEffectRequest) -> reconcileEffectResponse: ...
    def configureBudget(self, actor: ActorContext, project_id: str, budget_id: str, request: configureBudgetRequest) -> configureBudgetResponse: ...
    def getBudgets(self, actor: ActorContext, project_id: str) -> getBudgetsResponse: ...
    def listReports(self, actor: ActorContext, project_id: str) -> listReportsResponse: ...
    def listEvents(self, actor: ActorContext, project_id: str, after: int | None = None, limit: int | None = None) -> listEventsResponse: ...
    def requestRestore(self, actor: ActorContext, project_id: str, request: requestRestoreRequest) -> requestRestoreResponse: ...
    def prepareRelease(self, actor: ActorContext, project_id: str, request: prepareReleaseRequest) -> prepareReleaseResponse: ...
    def importRoles(self, actor: ActorContext, project_id: str, request: importRolesRequest) -> importRolesResponse: ...
    def listRoles(self, actor: ActorContext, project_id: str) -> listRolesResponse: ...
    def listDepartments(self, actor: ActorContext, project_id: str) -> listDepartmentsResponse: ...
    def createDeliberation(self, actor: ActorContext, project_id: str, cabal_id: str, request: createDeliberationRequest) -> createDeliberationResponse: ...
    def listDeliberations(self, actor: ActorContext, project_id: str, cabal_id: str) -> listDeliberationsResponse: ...
    def assignTask(self, actor: ActorContext, project_id: str, task_id: str, request: assignTaskRequest) -> assignTaskResponse: ...
    def sendRelay(self, actor: ActorContext, project_id: str, request: sendRelayRequest) -> sendRelayResponse: ...
    def ackRelay(self, actor: ActorContext, project_id: str, message_id: str, request: ackRelayRequest) -> ackRelayResponse: ...
    def openOversightCase(self, actor: ActorContext, project_id: str, request: openOversightCaseRequest) -> openOversightCaseResponse: ...
    def resolveOversightCase(self, actor: ActorContext, project_id: str, case_id: str, request: resolveOversightCaseRequest) -> resolveOversightCaseResponse: ...
    def listOversightCases(self, actor: ActorContext, project_id: str) -> listOversightCasesResponse: ...
    def requestReport(self, actor: ActorContext, project_id: str, request: requestReportRequest) -> requestReportResponse: ...
    def listNotifications(self, actor: ActorContext, project_id: str) -> listNotificationsResponse: ...
    def ackNotification(self, actor: ActorContext, project_id: str, notification_id: str, request: ackNotificationRequest) -> ackNotificationResponse: ...
    def observeEffect(self, actor: ActorContext, project_id: str, effect_id: str) -> observeEffectResponse: ...
    def inspectIntegration(self, actor: ActorContext, project_id: str, intent_id: str) -> inspectIntegrationResponse: ...
    def observeRecovery(self, actor: ActorContext, project_id: str) -> observeRecoveryResponse: ...
    def getProjectPolicy(self, actor: ActorContext, project_id: str) -> getProjectPolicyResponse: ...
    def setProjectPolicy(self, actor: ActorContext, project_id: str, request: setProjectPolicyRequest) -> setProjectPolicyResponse: ...
    def listProjectGrants(self, actor: ActorContext, project_id: str) -> listProjectGrantsResponse: ...
    def grantProjectAccess(self, actor: ActorContext, project_id: str, request: grantProjectAccessRequest) -> grantProjectAccessResponse: ...
    def revokeProjectAccess(self, actor: ActorContext, project_id: str, request: revokeProjectAccessRequest) -> revokeProjectAccessResponse: ...
    def listToolGrants(self, actor: ActorContext, project_id: str) -> listToolGrantsResponse: ...
    def revokeToolGrant(self, actor: ActorContext, project_id: str, grant_id: str, request: revokeToolGrantRequest) -> revokeToolGrantResponse: ...

