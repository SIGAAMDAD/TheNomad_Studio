import { useState } from 'react';
import { makeHandoff, handoffComplete, type ShiftHandoff } from '../shift';

export function HandoffPanel({ projectId, githubRef, onGithubRefChange, hints }: { projectId: string; githubRef: string; onGithubRefChange: (value:string)=>void; hints: Record<string,string> }) {
  const [inputs, setInputs] = useState<Omit<ShiftHandoff, 'projectId'>>({mode:'START', workerId:'', shiftId:'', canonSnapshotId:'', contractDigest:'', githubRelayPath:'', checkpointId:'', additionalInstructions:''});
  const packet: ShiftHandoff = { ...inputs, projectId, workerId: inputs.workerId || hints.workerId || '', shiftId: inputs.shiftId || hints.shiftId || '' };
  const [copied, setCopied] = useState(false);
  const fields: {key:keyof typeof inputs; label:string}[] = [
    {key:'workerId', label:'Verified persistent worker ID'}, {key:'shiftId',label:'Current continuous-response shift ID'},
    {key:'canonSnapshotId',label:'Canon snapshot ID'}, {key:'contractDigest',label:'Contract digest'},
    {key:'githubRelayPath',label:'Verified GitHub mailbox path'},
    ...(inputs.mode === 'RESUME' ? [{key:'checkpointId' as const,label:'Verified checkpoint ID'}] : []),
  ];
  const update = (key:keyof typeof inputs, value:string) => { setCopied(false); setInputs((p) => ({...p,[key]:value})); };
  return <section className="workbench"><div className="workbench-heading"><div><h2>Existing-conversation shift handoff</h2><p>Use a verified coordinator packet. One running response processes multiple GitHub assignments; the operator must initiate the shift in the enrolled browser conversation.</p></div><span className="status status-unknown">Not verified here</span></div>
    <div className="handoff-layout"><div className="form-grid">
      <label className="field"><span className="field-title">Work-shift type</span><select value={inputs.mode} onChange={(e) => update('mode',e.target.value)}><option value="START">START · human begins response in enrolled chat</option><option value="RESUME">RESUME · human begins another response in existing chat</option></select></label>
      {fields.map(({key,label}) => <label key={key} className="field"><span className="field-title">{label}</span><input value={String(packet[key] ?? '')} onChange={(e)=>update(key,e.target.value)} placeholder={label} /></label>)}
      <label className="field"><span className="field-title">Additional owner instructions</span><textarea rows={3} value={inputs.additionalInstructions} onChange={(e) => update('additionalInstructions',e.target.value)} /></label>
      <button disabled={!handoffComplete(packet)} onClick={() => { void navigator.clipboard.writeText(makeHandoff(packet)).then(()=>setCopied(true)).catch(()=>setCopied(false)); }}>{copied ? 'Copied · use in existing worker conversation' : 'Copy existing-chat shift prompt'}</button>
    </div><div className="handoff-preview"><div className="eyebrow">PREVIEW · DOES NOT INITIATE BROWSER EXECUTION</div><pre>{handoffComplete(packet) ? makeHandoff(packet) : 'Enter the required verified worker, shift and GitHub fields to prepare a prompt.'}</pre><p>A STOP command may be observed only while this response is active. An inactive conversation has no assumed heartbeat.</p>
    <div className="divider-line"/><h3>GitHub relay locator</h3><p>Use the actual configured repository/ref. The cockpit does not claim a receipt was published.</p><label className="field"><span className="field-title">Verified GitHub repository/ref URL</span><input value={githubRef} onChange={(e)=>onGithubRefChange(e.target.value)} placeholder="https://github.com/owner/repo/tree/ref" /></label>{/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\//.test(githubRef) ? <a target="_blank" rel="noopener noreferrer" href={githubRef}>Open authenticated GitHub relay view ↗</a> : null}</div></div>
  </section>;
}
