import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { StudioOperationId } from '../../../CONTRACTS/api.types';
import { StudioClient, StudioApiError } from '../client';
import { displayName, writeOperations, type Section } from '../catalog';
import { operations } from '../generated/operations';
import { normalizeStatus } from '../form';
import { ErrorBox } from './CommandForm';

interface Props {
  operation: StudioOperationId;
  client: StudioClient;
  projectId: string;
  section: Section;
  onAction: (operation: StudioOperationId, seed: Record<string, unknown>) => void;
}
function unwrap(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter((x): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x));
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const arr = Object.values(record).find((entry) => Array.isArray(entry));
    if (Array.isArray(arr)) { const nested = unwrap(arr); return nested.length || arr.length === 0 ? nested : [record]; }
    return [record];
  }
  return [];
}
function compact(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
function statusFor(rec: Record<string, unknown>): string | null {
  const status = ['state', 'delivery_state', 'verdict', 'ready'].map((k) => rec[k]).find((v) => typeof v === 'string' || typeof v === 'boolean');
  return status === undefined ? null : String(status);
}
export function RecordPanel({ operation, client, projectId, section, onAction }: Props) {
  const meta = operations[operation];
  const [inspectId, setInspectId] = useState('');
  const [cursor, setCursor] = useState(0);
  const needsId = meta.pathParams.some((name) => name !== 'project_id');
  const inspectName = meta.pathParams.find((name) => name !== 'project_id');
  const enabled = !!projectId && (!needsId || !!inspectId);
  const query = useQuery({
    queryKey: ['project', projectId, operation, inspectId, cursor],
    enabled,
    queryFn: ({signal}) => client.call(operation, {
      params: Object.fromEntries(meta.pathParams.map((name) => [name, name === 'project_id' ? projectId : inspectId])),
      ...(operation === 'listEvents' ? { query: { after: cursor, limit: 100 } } : {}),
      signal,
    } as never),
    retry: false,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const records = unwrap(query.data);
  const actions = writeOperations(section);
  return <section className="data-panel">
    <div className="panel-head"><div><h3>{displayName(operation)}</h3><p className="subtle">{needsId ? 'Inspect exact record ID' : 'Live API projection · no simulated records'}</p></div><button className="button-secondary compact" disabled={!enabled || query.isFetching} onClick={() => { void query.refetch(); }}>Refresh</button></div>
    {needsId ? <div className="inspect-row"><input aria-label={inspectName} placeholder={inspectName} value={inspectId} onChange={(e) => setInspectId(e.target.value)} /><button onClick={() => {void query.refetch();}} disabled={!inspectId}>Inspect</button></div> : null}
    {query.isPending && enabled ? <div className="empty">Loading {displayName(operation)}…</div> : null}
    {!enabled ? <div className="empty">{needsId ? 'Enter the exact record ID to inspect.' : 'Select an authenticated project.'}</div> : null}
    {query.error ? <ErrorBox error={query.error instanceof StudioApiError ? query.error : new StudioApiError('protocol', null, 'unknown', String(query.error))} /> : null}
    {query.isSuccess && !records.length ? <div className="empty">No records returned.</div> : null}
    {records.length > 0 ? <div className="records">{records.map((record, index) => {
      const status = statusFor(record);
      const fields = Object.entries(record);
      const identity = fields.find(([key]) => key.endsWith('_id'))?.[1] ?? index;
      return <article key={String(identity) + index} className="record">
        <div className="record-header"><strong title={String(identity)}>{String(identity)}</strong>{status ? <span className={`status status-${normalizeStatus(status)}`}>{status}</span> : null}</div>
        <dl>{fields.filter(([key]) => !key.endsWith('_id') || key === 'project_id').slice(0, 8).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd title={compact(value)}>{compact(value)}</dd></div>)}</dl>
        <div className="record-actions">{actions.filter((action) => operations[action].pathParams.some((param) => param !== 'project_id' && record[param] !== undefined)).map((action) =>
          <button key={action} className="link-action" onClick={() => onAction(action, record)}>{displayName(action)}</button>)}</div>
        <details><summary>Full server record</summary><pre>{JSON.stringify(record, null, 2)}</pre></details>
      </article>;
    })}</div> : null}
    {operation === 'listEvents' && query.isSuccess && 'next_cursor' in (query.data as object) ? <div className="form-actions"><button className="button-secondary" disabled={cursor === 0} onClick={() => setCursor(0)}>First page</button><button onClick={() => setCursor((query.data as {next_cursor:number}).next_cursor)}>Next cursor</button></div> : null}
  </section>;
}
