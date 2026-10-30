// A human copies this packet into a previously enrolled ChatGPT conversation.
// It does not launch a browser session or bind the shift to a particular task.
export interface ShiftHandoff {
  projectId: string;
  mode: 'START' | 'RESUME';
  workerId: string;
  shiftId: string;
  canonSnapshotId: string;
  contractDigest: string;
  githubRelayPath: string;
  checkpointId: string;
  additionalInstructions: string;
}
export function handoffComplete(packet: ShiftHandoff): boolean {
  const fields = [packet.projectId, packet.workerId, packet.shiftId,
                  packet.canonSnapshotId, packet.contractDigest, packet.githubRelayPath];
  if (packet.mode === 'RESUME') fields.push(packet.checkpointId);
  return fields.every((x) => x.trim().length > 0);
}
export function makeHandoff(packet: ShiftHandoff): string {
  if (!handoffComplete(packet)) throw new Error('Verified worker, shift, Canon, contract and GitHub relay values required.');
  return [
    `The Nomad Studio — existing conversation work-shift ${packet.mode} packet`,
    'Use the already manually enrolled ChatGPT conversation; this packet starts one human-initiated work shift, not a new worker.',
    'Keep this single assistant response executing while receiving successive GitHub assignments; no intermediate final answer.',
    `Project ID: ${packet.projectId}`,
    `Persistent worker ID: ${packet.workerId}`,
    `Current shift ID: ${packet.shiftId}`,
    `Canon snapshot ID: ${packet.canonSnapshotId}`,
    `Contract digest: ${packet.contractDigest}`,
    `Verified GitHub inbox path: ${packet.githubRelayPath}`,
    ...(packet.mode === 'RESUME' ? [`Verified checkpoint ID: ${packet.checkpointId}`] : []),
    'Read the addressed GitHub mailbox at work checkpoints; verify recipient, assignment and source authority; execute work, publish results, and continue checking for commands.',
    'Publish one final shift receipt and return a single human-facing response only at the end of the shift.',
    'GitHub command publication does not awaken an idle or completed ChatGPT response.',
    packet.additionalInstructions.trim(),
  ].filter(Boolean).join('\n');
}
