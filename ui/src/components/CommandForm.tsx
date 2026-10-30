import { useEffect, useMemo, useState } from 'react';
import type { StudioOperationId } from '../../../CONTRACTS/api.types';
import { StudioClient, StudioApiError } from '../client';
import { displayName, manualSessionOperation, requiresConfirmation } from '../catalog';
import { defaults, validateRequired, type Fields } from '../form';
import { operations } from '../generated/operations';
import { schemaFailure } from '../schema';
import { FieldEditor } from './FieldEditor';

interface Props {
  operation: StudioOperationId;
  projectId: string;
  client: StudioClient;
  seed?: Record<string, unknown>;
  onDone: (value: unknown) => void;
  onClose: () => void;
}
export function CommandForm({ operation, projectId, client, seed, onDone, onClose }: Props) {
  const meta = operations[operation];
  const initial = useMemo(() => {
    const base = defaults(meta, projectId);
    if (seed) {
      for (const name of meta.pathParams) {
        if (name !== 'project_id' && typeof seed[name] === 'string') base.params[name] = String(seed[name]);
      }
      for (const name of Object.keys(meta.body?.properties ?? {})) {
        if (name !== 'request_id' && seed[name] !== undefined) base.body[name] = seed[name];
      }
    }
    return base;
  }, [meta, projectId, seed]);
  const [params, setParams] = useState<Record<string, string>>(initial.params);
  const [body, setBody] = useState<Fields>(initial.body);
  const [query, setQuery] = useState<Fields>(initial.query);
  const [reviewing, setReviewing] = useState(false);
  const [confirmDigest, setConfirmDigest] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [busy, setBusy] = useState(false);
  const [invalidFields, setInvalidFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<StudioApiError | null>(null);
  const [result, setResult] = useState<unknown>(null);
  useEffect(() => { setParams(initial.params); setBody(initial.body); setQuery(initial.query); setReviewing(false); setConfirmDigest(''); setConfirmText(''); setInvalidFields({}); setError(null); setResult(null); }, [initial]);
  const isOwnerGated = requiresConfirmation(operation);
  const digest = ['effect_digest', 'expected_effect_digest', 'policy_digest', 'resolution_digest', 'release_digest'].map((key) => body[key]).find((value) => typeof value === 'string' && value.length > 0) as string | undefined;
  const missing = [...meta.pathParams.filter((key) => !params[key]), ...validateRequired(body, meta.body?.required), ...meta.query.filter((f) => f.required && query[f.name] === undefined).map((f) => f.name)];
  const invalid = meta.body ? schemaFailure(body, meta.body, 'request') : null;
  const candidate = { operation, params, query, ...(meta.body ? { body } : {}) };
  async function send() {
    setBusy(true); setError(null);
    try {
      // Dynamic form fields are generated from the same OpenAPI source as the typed client.
      // Type safety of every operation's request/response is enforced by StudioClient.
      const response = await client.call(operation, { params, query, ...(meta.body ? { body } : {}) } as never);
      setResult(response);
      onDone(response);
    } catch (e) {
      setError(e instanceof StudioApiError ? e : new StudioApiError('protocol', null, 'invalid_request', String(e)));
    } finally { setBusy(false); }
  }
  return <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-label={displayName(operation)}>
    <div className="modal-head"><div><p className="eyebrow">{meta.method} · {operation}</p><h2>{displayName(operation)}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close">×</button></div>
    {manualSessionOperation(operation) ? <div className="notice">This action updates local session/relay state. It cannot launch, resume, stop, or awaken a ChatGPT browser conversation.</div> : null}
    {result ? <div className="result"><h3>Server response</h3><pre>{JSON.stringify(result, null, 2)}</pre><p>Inspect subsequent observed state separately; a prepared operation is not a verified remote effect.</p><button onClick={onClose}>Close</button></div> :
    reviewing ? <div className="form-grid">
      <p className="notice">Verify the exact subject, revision, and effect digest before submitting. No mutation is retried automatically.</p>
      <pre className="preview">{JSON.stringify(candidate, null, 2)}</pre>
      {isOwnerGated ? <label className="field"><span className="field-title">Type CONFIRM to authorize this submitted request</span><input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} /></label> : null}
      {isOwnerGated && digest ? <label className="field"><span className="field-title">Repeat the exact digest</span><input value={confirmDigest} onChange={(e) => setConfirmDigest(e.target.value)} /></label> : null}
      {error ? <ErrorBox error={error} /> : null}
      <div className="form-actions"><button className="button-secondary" onClick={() => { setReviewing(false); setError(null); }}>Edit</button><button disabled={busy || (isOwnerGated && confirmText !== 'CONFIRM') || (isOwnerGated && !!digest && confirmDigest !== digest)} onClick={() => { void send(); }}>{busy ? 'Submitting…' : 'Submit exact request'}</button></div>
    </div> : <div className="form-grid">
      {meta.pathParams.filter((key) => key !== 'project_id').map((key) => <label className="field" key={key}><span className="field-title">{key.replaceAll('_',' ')} <span className="required">*</span></span><input value={params[key] ?? ''} onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))} /></label>)}
      {meta.query.map((field) => <FieldEditor key={field.name} name={field.name} schema={field.schema} value={query[field.name] ?? ''} required={field.required} onChange={(v) => setQuery((p) => ({...p, [field.name]:v}))} onValidityChange={(error) => setInvalidFields((p) => {const next={...p}; if (error) next[`query.${field.name}`]=error; else delete next[`query.${field.name}`]; return next;})} />)}
      {Object.entries(meta.body?.properties ?? {}).map(([key, schema]) => <FieldEditor key={key} name={key} schema={schema} value={body[key]} required={(meta.body?.required ?? []).includes(key)} onChange={(value) => setBody((p) => ({ ...p, [key]: value }))} onValidityChange={(error) => setInvalidFields((p) => {const next={...p}; if (error) next[`body.${key}`]=error; else delete next[`body.${key}`]; return next;})} />)}
      {missing.length ? <div className="error" role="alert">Required: {missing.join(', ')}</div> : null}
      {!missing.length && invalid ? <div className="error" role="alert">{invalid}</div> : null}
      {Object.keys(invalidFields).length ? <div className="error" role="alert">Fix invalid fields: {Object.keys(invalidFields).join(', ')}</div> : null}
      {error ? <ErrorBox error={error} /> : null}
      <div className="form-actions"><button className="button-secondary" onClick={onClose}>Cancel</button><button disabled={missing.length > 0 || !!invalid || Object.keys(invalidFields).length > 0} onClick={() => setReviewing(true)}>Review request</button></div>
    </div>}
  </section></div>;
}
export function ErrorBox({ error }: { error: StudioApiError }) {
  return <div className={`error-box ${error.code === 'unknown_effect' ? 'unknown-box' : ''}`} role="alert">
    <strong>{error.code === 'unknown_effect' ? 'UNKNOWN outcome' : error.kind === 'server' ? `API rejected: ${error.code}` : `${error.kind}: ${error.code}`}</strong>
    <div>{error.message}</div>
    {error.status !== null ? <small>HTTP {error.status}</small> : null}
    {error.requestId ? <small>Request ID: <code>{error.requestId}</code></small> : null}
    {error.relatedRefs.length ? <small>References: {error.relatedRefs.join(', ')}</small> : null}
  </div>;
}
