import { useState } from 'react';

export function BibleWorkbench({ projectId }: { projectId:string }) {
  const [kind, setKind] = useState<'design'|'technical'>('design');
  const [drafts, setDrafts] = useState({design:'',technical:''});
  const [digest, setDigest] = useState('');
  const [error, setError] = useState('');
  const draft = drafts[kind];
  const change = (value:string) => {setDrafts((p)=>({...p,[kind]:value}));setDigest('');setError('');};
  async function hash() {
    if (!draft.trim()) return;
    try {
      const bytes = new TextEncoder().encode(draft);
      const result = await crypto.subtle.digest('SHA-256', bytes);
      setDigest([...new Uint8Array(result)].map((byte) => byte.toString(16).padStart(2,'0')).join(''));
    } catch { setError('SHA-256 unavailable: serve the cockpit over HTTPS or localhost.'); }
  }
  return <section className="workbench"><div className="workbench-heading"><div><h2>Bible drafting workbench</h2><p>Local unsaved text for computing source digests. Publishing requires verified immutable blob bytes, staging intent, and a matching owner decision via the API controls below.</p></div><span className="badge-neutral">Ephemeral · {projectId ? 'project scoped' : 'no project'}</span></div><div className="bible-body">
    <div className="workbench-tabs"><button className={kind==='design'?'active':''} onClick={()=>{setKind('design');setDigest('');}}>Design Bible</button><button className={kind==='technical'?'active':''} onClick={()=>{setKind('technical');setDigest('');}}>Technical Bible</button></div>
    <textarea className="bible-text" aria-label={`${kind} Bible local text`} value={draft} onChange={(e)=>change(e.target.value)} placeholder={`Draft ${kind} Bible text (not persisted to the Studio)`} rows={8} spellCheck={false}/>
    <div className="form-actions"><button className="button-secondary" disabled={!draft.trim()} onClick={()=>{ void hash(); }}>Compute exact UTF-8 SHA-256</button>{digest ? <><code className="digest">{digest}</code><button className="button-secondary" onClick={()=>{void navigator.clipboard.writeText(digest);}}>Copy digest</button></> : null}</div>
    {error ? <p className="error">{error}</p> : null}
    <small>Computed digest is not proof the content exists in server blob storage. Switching project removes this editor instance.</small>
  </div></section>;
}
