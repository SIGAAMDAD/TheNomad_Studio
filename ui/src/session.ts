// The template is informational. Only the human can start an ordinary browser chat.
export interface SessionHandoff {
  projectId: string;
  mode: 'START' | 'RESUME';
  sessionId: string;
  taskId: string;
  generation: string;
  packageDigest: string;
  canonSnapshotId: string;
  contractDigest: string;
  githubRelayPath: string;
  checkpointId: string;
  additionalInstructions: string;
}
export function handoffComplete(packet: SessionHandoff): boolean {
  const fields = [packet.projectId, packet.sessionId, packet.taskId, packet.generation, packet.packageDigest, packet.canonSnapshotId, packet.contractDigest, packet.githubRelayPath];
  if (packet.mode === 'RESUME') fields.push(packet.checkpointId);
  return fields.every((x) => x.trim().length > 0);
}
export function makeHandoff(packet: SessionHandoff): string {
  if (!handoffComplete(packet)) throw new Error('Complete the verified session, lease, Canon, contract, and GitHub relay values first.');
  return [
    `The Nomad Studio — existing conversation work-shift ${packet.mode} packet`,
    'This is a shift of an already manually enrolled ChatGPT conversation, not a new worker or new chat for every assignment.',
    'Keep this one response executing while receiving and completing multiple GitHub assignments. Publish individual task receipts and then one final shift receipt before returning the human-facing response.',
    'A GitHub message is received only when this active response reads it; do not claim an idle chat was activated.',
    `Project ID: ${packet.projectId}`,
    `Session ID: ${packet.sessionId}`,
    `Task ID: ${packet.taskId}`,
    `Lease generation: ${packet.generation}`,
    `Package digest: ${packet.packageDigest}`,
    `Canon snapshot ID: ${packet.canonSnapshotId}`,
    `Contract digest: ${packet.contractDigest}`,
    `GitHub relay path (verify in connected GitHub): ${packet.githubRelayPath}`,
    ...(packet.mode === 'RESUME' ? [`Verified checkpoint ID: ${packet.checkpointId}`] : []),
    'Read the exact GitHub relay and governing instructions; do not infer approval or delivery from local HTTP success.',
    'During this single active response: check the addressed GitHub inbox, verify assignments and authority, execute tasks, publish checkpoints and receipts, then check for additional commands without finalizing between assignments. Publish the final shift receipt before returning one ChatGPT response.',
    packet.additionalInstructions.trim(),
  ].filter(Boolean).join('\n');
}
