import { useState } from 'react';
import { makeHandoff, handoffComplete, type SessionHandoff } from '../session';

export function HandoffPanel({ projectId, githubRef, onGithubRefChange, hints }: { projectId: string; githubRef: string; onGithubRefChange: (value:string)=>void; hints: Record<string,string> }) {
  const [inputs, setInputs] = useState<Omit<SessionHandoff, 'projectId'>>({mode:'START', sessionId:'', taskId:'', generation:'', packageDigest:'', canonSnapshotId:'', contractDigest:'', githubRelayPath:'', checkpointId:'', additionalInstructions:''});
  const packet: SessionHandoff = { ...inputs, projectId, sessionId: inputs.sessionId || hints.sessionId || '' };
  const [copied, setCopied] = useState(false);
  const fields: {key:keyof typeof inputs; label:string}[] = [
    {key:'sessionId', label:'Verified session ID'}, {key:'taskId',label:'Task ID'}, {key:'generation',label:'Lease generation'},
    {key:'packageDigest',label:'Package digest'}, {key:'canonSnapshotId',label:'Canon snapshot ID'},
    {key:'contractDigest',label:'Contract digest'}, {key:'githubRelayPath',label:'Verified GitHub relay mailbox path'},
    ...(inputs.mode === 'RESUME' ? [{key:'checkpointId' as const,label:'Verified checkpoint ID'}] : []),
  ];
  const update = (key:keyof typeof inputs, value:string) => { setCopied(false); setInputs((p) => ({...p,[key]:value})); };
  return <section className="workbench"><div className="workbench-heading"><div><h2>Manual session handoff</h2><p>Populate fields from a verified coordinator packet and GitHub relay. The API does not expose a complete launch packet retrieval operation.</p></div><span className="status status-unknown">Not verified here</span></div>
    <div className="handoff-layout"><div className="form-grid">
      <label className="field"><span className="field-title">Session type</span><select value={inputs.mode} onChange={(e) => update('mode',e.target.value)}><option value="START">START · human opens a new browser chat</option><option value="RESUME">RESUME · human opens a new browser chat</option></select></label>
      {fields.map(({key,label}) => <label key={key} className="field"><span className="field-title">{label}</span><input value={String(packet[key] ?? '')} onChange={(e)=>update(key,e.target.value)} placeholder={label} /></label>)}
      <label className="field"><span className="field-title">Additional owner instructions</span><textarea rows={3} value={inputs.additionalInstructions} onChange={(e) => update('additionalInstructions',e.target.value)} /></label>
      <button disabled={!handoffComplete(packet)} onClick={() => { void navigator.clipboard.writeText(makeHandoff(packet)).then(()=>setCopied(true)).catch(()=>setCopied(false)); }}>{copied ? 'Copied · paste in a new ChatGPT chat' : 'Copy manual START / RESUME prompt'}</button>
    </div><div className="handoff-preview"><div className="eyebrow">PREVIEW · NOT A SESSION LAUNCH</div><pre>{handoffComplete(packet) ? makeHandoff(packet) : 'Enter every required field from verified records to prepare a copyable prompt.'}</pre><p>The STOP_REQUESTED relay may be read only during an active browser response. A quiet or UNKNOWN worker has no assumed heartbeat.</p>
    <div className="divider-line"/><h3>GitHub relay locator</h3><p>Use the actual configured repository/ref. This UI does not claim a receipt was published.</p><label className="field"><span className="field-title">Verified GitHub repository/ref URL</span><input value={githubRef} onChange={(e)=>onGithubRefChange(e.target.value)} placeholder="https://github.com/owner/repo/tree/ref" /></label>{/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\//.test(githubRef) ? <a target="_blank" rel="noopener noreferrer" href={githubRef}>Open authenticated GitHub relay view ↗</a> : null}</div></div>
  </section>;
}
