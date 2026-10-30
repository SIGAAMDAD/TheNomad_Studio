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
    `The Nomad Studio — manually initiated ${packet.mode} packet`,
    'This browser conversation was manually opened by the human owner. A GitHub file cannot wake or stop this chat.',
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
    'Publish your GitHub HELLO/CHECKPOINT/HANDOFF receipts only while this browser response is active. Verify write/readback through the connected GitHub app.',
    packet.additionalInstructions.trim(),
  ].filter(Boolean).join('\n');
}
