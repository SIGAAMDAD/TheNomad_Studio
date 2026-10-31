import type { StudioOperationId } from '../../CONTRACTS/api.types';
import { operations } from './generated/operations';

export const sections = {
  'Projects': ['listProjects', 'createProject', 'getProject', 'configureProject', 'preflightProject', 'listPacks', 'enablePack', 'disablePack'],
  'Canon & bibles': ['getCanon', 'listBibles', 'createBible', 'stageBible', 'diffBible', 'publishBible', 'listDecisions', 'requestDecision', 'resolveDecision'],
  'Organization': ['listCabals', 'createCabal', 'staffCabal', 'listDepartments', 'listRoles', 'importRoles', 'listDeliberations', 'createDeliberation'],
  'Work planning': ['listFeatures', 'submitFeature', 'planFeature', 'listTasks', 'assignTask', 'dispatchTask', 'cancelTask'],
  'Worker shifts': ['listWorkers', 'registerWorker', 'beginShift', 'stopShift', 'endShift', 'observeCommandReceipt'],
  'QA & oversight': ['listReviews', 'requestReview', 'submitReview', 'listOversightCases', 'openOversightCase', 'resolveOversightCase'],
  'Source & releases': ['listArtifacts', 'getArtifact', 'runBuild', 'stageIntegration', 'inspectIntegration', 'promoteIntegration', 'prepareRelease', 'listReleases', 'publishRelease', 'attachConnector'],
  'Operations': ['getBudgets', 'configureBudget', 'listNotifications', 'ackNotification', 'listReports', 'requestReport', 'listEvents', 'observeEffect', 'reconcileEffect', 'requestRestore', 'observeRecovery'],
  'Permissions': ['getProjectPolicy', 'setProjectPolicy', 'listProjectGrants', 'grantProjectAccess', 'revokeProjectAccess', 'listToolGrants', 'revokeToolGrant'],
} as const satisfies Record<string, readonly StudioOperationId[]>;
export type Section = keyof typeof sections;
export const navigation = Object.keys(sections) as Section[];
const OWNER_GATED = new Set<StudioOperationId>([
  'publishBible', 'publishRelease', 'promoteIntegration', 'resolveDecision', 'setProjectPolicy',
  'grantProjectAccess', 'revokeProjectAccess', 'revokeToolGrant', 'configureBudget',
  'requestRestore', 'enablePack', 'disablePack', 'attachConnector', 'resolveOversightCase',
]);
const HUMAN_MANUAL = new Set<StudioOperationId>(['registerWorker', 'beginShift']);
export function requiresConfirmation(id: StudioOperationId): boolean { return OWNER_GATED.has(id); }
export function manualShiftOperation(id: StudioOperationId): boolean { return HUMAN_MANUAL.has(id); }
export function displayName(name: string): string {
  return name.replace(/([a-z\d])([A-Z])/g, '$1 $2').replace(/[_-]/g, ' ').replace(/^./, (x) => x.toUpperCase());
}
export const readOperations = (section: Section): StudioOperationId[] => sections[section].filter((id) => operations[id].method === 'GET');
export const writeOperations = (section: Section): StudioOperationId[] => sections[section].filter((id) => operations[id].method !== 'GET');
