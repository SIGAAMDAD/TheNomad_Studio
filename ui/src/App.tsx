import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { StudioOperationId } from '../../CONTRACTS/api.types';
import { StudioClient, StudioApiError } from './client';
import { displayName, navigation, readOperations, writeOperations, type Section } from './catalog';
import { operations } from './generated/operations';
import { CommandForm, ErrorBox } from './components/CommandForm';
import { RecordPanel } from './components/RecordPanel';
import { HandoffPanel } from './components/HandoffPanel';
import { BibleWorkbench } from './components/BibleWorkbench';
import './style.css';

interface Action { operation: StudioOperationId; seed?: Record<string, unknown> }
export default function App() {
  const [draftToken, setDraftToken] = useState('');
  const [token, setToken] = useState('');
  const [projectId, setProjectId] = useState('');
  const [section, setSection] = useState<Section>('Projects');
  const [action, setAction] = useState<Action | null>(null);
  const [filter, setFilter] = useState('');
  const [showConnection, setShowConnection] = useState(false);
  const [githubRef, setGithubRef] = useState('');
  const [hints, setHints] = useState<Record<string, string>>({});
  const cache = useQueryClient();
  const client = useMemo(() => new StudioClient(() => token, import.meta.env.VITE_STUDIO_API_URL ?? ''), [token]);
  const projects = useQuery({
    queryKey: ['identity', token], enabled: !!token, retry: false, refetchOnWindowFocus: false,
    queryFn: ({signal}) => client.call('listProjects', { params: {}, signal }),
  });
  const current = projects.data?.items.find((entry) => entry.project_id === projectId);
  const reads = readOperations(section).filter((op) => displayName(op).toLowerCase().includes(filter.toLowerCase()));
  const writes = writeOperations(section).filter((op) => displayName(op).toLowerCase().includes(filter.toLowerCase()));
  const isolatedSelect = (id: string) => { cache.clear(); setProjectId(id); setAction(null); setHints({}); setGithubRef(''); };
  const connect = () => { cache.clear(); setProjectId(''); setAction(null); setToken(draftToken.trim()); setShowConnection(false); };
  const completed = (value: unknown) => {
    void cache.invalidateQueries();
    if (action?.operation === 'createProject' && value && typeof value === 'object' && 'project_id' in value && typeof value.project_id === 'string') {
      isolatedSelect(value.project_id);
    }
    if (value && typeof value === 'object' && 'worker_id' in value && typeof value.worker_id === 'string') {
      setHints((prior) => ({...prior, workerId: value.worker_id as string}));
    }
  };
  const launch = (operation: StudioOperationId, seed?: Record<string, unknown>) => setAction({operation, seed});
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon">N</div><div><strong>THE NOMAD</strong><span>STUDIO / OPERATOR</span></div></div>
      <div className="sidebar-label">WORKSPACES</div>
      <nav aria-label="Studio sections">{navigation.map((item) => <button key={item} className={`nav-item ${section === item ? 'selected' : ''}`} onClick={() => {setSection(item); setFilter('');}}><span className="nav-symbol">{({Projects:'◈','Canon & bibles':'▣',Organization:'⌘','Work planning':'▥','Worker shifts':'◷','QA & oversight':'◉','Source & releases':'⬡',Operations:'▤',Permissions:'◇'} as Record<Section,string>)[item]}</span>{item}<span className="nav-count">{readOperations(item).length + writeOperations(item).length}</span></button>)}</nav>
      <div className="sidebar-foot"><span className={`live-dot ${token ? 'connected' : ''}`} />{token ? 'Token provided · service unverified' : 'Not connected'}<small>Operator-initiated · no inference agents</small></div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb">STUDIO <span>/</span> {section.toUpperCase()}</div><div className="toolbar"><select aria-label="Active project" value={projectId} disabled={!token || projects.isPending} onChange={(e) => isolatedSelect(e.target.value)}><option value="">Select project</option>{projects.data?.items.map((p) => <option key={p.project_id} value={p.project_id}>{p.name} · {p.project_id}</option>)}</select><button className="button-secondary" onClick={() => setShowConnection(!showConnection)}>Connection</button></div></header>
    <main className="main">
      {showConnection || !token ? <section className="connect-panel"><div><p className="eyebrow">OPERATOR IDENTITY</p><h2>Connect to an authenticated Studio coordinator</h2><p>The token is held in this browser tab's memory only. Requests use the configured backend, not GitHub relay access or a model API.</p></div><div className="connect-form"><label htmlFor="token">Bearer token</label><input id="token" type="password" autoComplete="off" value={draftToken} onChange={(e) => setDraftToken(e.target.value)} placeholder="Token from your configured identity provider" /><button onClick={connect} disabled={!draftToken.trim()}>Use token</button>{token ? <button className="button-secondary" onClick={() => { cache.clear(); setToken(''); setDraftToken(''); setProjectId(''); }}>Disconnect</button> : null}</div></section> : null}
      {projects.error ? <ErrorBox error={projects.error instanceof StudioApiError ? projects.error : new StudioApiError('protocol', null, 'unavailable', String(projects.error))}/> : null}
      <section className="page-header"><div><div className="eyebrow">{current ? `PROJECT ${current.project_id} · REVISION ${current.revision}` : 'GLOBAL WORKSPACE'}</div><h1>{section}</h1><p className="lead">{subhead(section)}</p></div><div className="heading-right">{projectId ? <span className="project-chip">{current?.name ?? projectId}</span> : <span className="project-chip neutral">No project selected</span>}</div></section>
      <div className="overview-stats"><div className="stat-card"><span>Operations in section</span><strong>{readOperations(section).length + writeOperations(section).length}</strong><small>All from frozen OpenAPI</small></div><div className="stat-card"><span>Access scope</span><strong>{projectId ? 'Project' : 'Unselected'}</strong><small>{projectId ? projectId : 'Choose a scoped workspace'}</small></div><div className="stat-card"><span>Shift initiation</span><strong>Human</strong><small>GitHub relay observations only</small></div></div>
      <div className="section-toolbar"><div className="switch-legend"><span className="live-dot connected" /> API views <span className="dot-separator">·</span> <span className="badge-neutral">No invented records</span></div><input aria-label="Filter operations" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter commands and views…" /></div>
      {section === 'Worker shifts' ? <HandoffPanel key={projectId} projectId={projectId} githubRef={githubRef} onGithubRefChange={setGithubRef} hints={hints} /> : null}
      {section === 'Canon & bibles' ? <BibleWorkbench key={projectId} projectId={projectId} /> : null}
      {section === 'Permissions' ? <div className="notice">Policy rules are owner-supplied. The API returns policy revision/digest; it does not expose the existing rule document. Submit a full replacement only when you have verified its source and expected revision.</div> : null}
      {section === 'Source & releases' ? <div className="notice">CI, integration, and publication responses describe coordinator state. Confirm provider readback and QA evidence using the appropriate observation commands.</div> : null}
      {section === 'Operations' ? <div className="notice">UNKNOWN effect is an unresolved status. Reconcile its exact ID rather than repeating a mutation. Notifications and reports show server-recorded delivery state.</div> : null}
      <div className="group-title"><h2>Commands</h2><span>{writes.length} available</span></div>
      {writes.length ? <div className="command-grid">{writes.map((op) => <button key={op} className="command" disabled={op !== 'createProject' && !projectId || !token} onClick={() => launch(op)}><div><strong>{displayName(op)}</strong><small>{operations[op].method} · {op}</small></div><span>↗</span></button>)}</div> : <div className="empty">No matching commands.</div>}
      <div className="group-title"><h2>Data & inspection</h2><span>{reads.length} API views</span></div>
      {reads.length ? <div className="panel-grid">{reads.map((op) => op === 'listProjects' ? <section className="data-panel" key={op}><div className="panel-head"><h3>Projects</h3><button className="button-secondary compact" disabled={!token || projects.isFetching} onClick={() => { void projects.refetch(); }}>Refresh</button></div>{projects.isPending ? <div className="empty">Authenticate to list projects.</div> : projects.data?.items.length ? <div className="project-rows">{projects.data.items.map((p) => <button key={p.project_id} className={`project-row ${projectId === p.project_id ? 'active' : ''}`} onClick={() => isolatedSelect(p.project_id)}><span><strong>{p.name}</strong><small>{p.project_id}</small></span><span>Revision {p.revision}</span></button>)}</div> : <div className="empty">No accessible projects returned.</div>}</section> : <RecordPanel key={`${projectId}-${op}`} operation={op} client={client} projectId={projectId} section={section} onAction={launch} />)}</div> : <div className="empty">No matching data views.</div>}
      <footer className="footer">Studio operator cockpit · Typed OpenAPI HTTP · Project-isolated reads · GitHub-coordinated continuous worker shifts</footer>
    </main></div>
    {action ? <CommandForm key={`${projectId}-${action.operation}-${JSON.stringify(action.seed ?? {})}`} operation={action.operation} seed={action.seed} projectId={projectId} client={client} onDone={completed} onClose={() => setAction(null)} /> : null}
  </div>;
}
function subhead(section: Section): string {
  return ({
    Projects: 'Onboard projects, check capability readiness, and enable verified packs.',
    'Canon & bibles': 'Project-specific Design and Technical Bibles, staged revisions, owner decisions, and Canon snapshots.',
    Organization: 'Cabals, departments, specialists, membership, and recorded deliberations.',
    'Work planning': 'Scoped features, task assignment, dispatch, and revision-bound changes.',
    'Worker shifts': 'Human-started continuous work shifts, successive GitHub commands, and observed receipts.',
    'QA & oversight': 'Independent submission reviews, exact evidence digests, and oversight case resolution.',
    'Source & releases': 'Verified artifacts, CI intents, integration candidates, and release state.',
    Operations: 'Durable budgets, event cursors, report delivery, notifications, and uncertain-effect recovery.',
    Permissions: 'Fine-grained owner policy, project grants, and tool permissions with revision checks.',
  } satisfies Record<Section,string>)[section];
}
