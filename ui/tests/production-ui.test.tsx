import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactElement } from 'react';
import App from '../src/App';
import { FieldEditor } from '../src/components/FieldEditor';
import { BibleWorkbench } from '../src/components/BibleWorkbench';
import { HandoffPanel } from '../src/components/HandoffPanel';
import { CommandForm, ErrorBox } from '../src/components/CommandForm';
import { RecordPanel } from '../src/components/RecordPanel';
import { StudioApiError, StudioClient } from '../src/client';
import { castValue, defaults, normalizeStatus, validateRequired } from '../src/form';
import { schemaFailure } from '../src/schema';
import { displayName, navigation, readOperations, writeOperations, manualShiftOperation, requiresConfirmation } from '../src/catalog';
import { makeHandoff, handoffComplete, type ShiftHandoff } from '../src/shift';
import { operations } from '../src/generated/operations';

function provider(element: ReactElement) {
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  return render(createElement(QueryClientProvider,{client},element));
}

beforeEach(() => {
  vi.stubGlobal('crypto', {randomUUID:()=> 'test-id',subtle:{digest:async()=>new Uint8Array(32).buffer}});
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({items:[]}), {
    status:200,headers:{'Content-Type':'application/json'}})));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('covers every generated operation with the original catalog and explicit owner restrictions', () => {
  expect(Object.keys(operations).length).toBe(72);
  expect(navigation.length).toBeGreaterThan(5);
  const listed = new Set<string>();
  for (const section of navigation) {
    for (const op of [...readOperations(section),...writeOperations(section)]) {
      listed.add(op);
      expect(displayName(op)).toBeTruthy();
      expect(operations[op]).toBeDefined();
    }
  }
  expect(listed.size).toBe(72);
  expect(requiresConfirmation('publishRelease')).toBe(true);
  expect(requiresConfirmation('listProjects')).toBe(false);
  expect(manualShiftOperation('beginShift')).toBe(true);
  expect(manualShiftOperation('listWorkers')).toBe(false);
});

it('validates every JSON schema primitive and nested object without coercing unknown inputs', () => {
  expect(schemaFailure(null,{type:'string'})).toContain('null');
  expect(schemaFailure(null,{type:'string',nullable:true})).toBeNull();
  expect(schemaFailure('none',{type:'string',enum:['valid']})).toContain('enum');
  expect(schemaFailure(2,{type:'string'})).toContain('string');
  expect(schemaFailure('a',{type:'string',minLength:2})).toContain('short');
  expect(schemaFailure('long',{type:'string',maxLength:2})).toContain('long');
  expect(schemaFailure(Infinity,{type:'number'})).toContain('finite');
  expect(schemaFailure(1.2,{type:'integer'})).toContain('integer');
  expect(schemaFailure(-1,{type:'integer',minimum:0})).toContain('minimum');
  expect(schemaFailure(5,{type:'integer',maximum:3})).toContain('maximum');
  expect(schemaFailure('true',{type:'boolean'})).toContain('boolean');
  expect(schemaFailure({},{type:'array'})).toContain('array');
  expect(schemaFailure(['bad'],{type:'array',items:{type:'integer'}})).toContain('[0]');
  expect(schemaFailure([1],{type:'array',items:{type:'integer'}})).toBeNull();
  expect(schemaFailure([],{type:'object'})).toContain('object');
  const schema={type:'object' as const,required:['id'],properties:{id:{type:'integer' as const}}};
  expect(schemaFailure({},schema)).toContain('missing');
  expect(schemaFailure({id:'bad'},schema)).toContain('.id');
  expect(schemaFailure({id:1},schema)).toBeNull();
});

it('exercises typed form defaults, casting, mandatory fields and status classes', () => {
  const meta=operations.createProject;
  const initial=defaults(meta,'project');
  expect(initial.params).toBeDefined();
  expect(initial.body.request_id).toBeTruthy();
  expect(validateRequired({},['id'])).toEqual(['id']);
  expect(validateRequired({id:'x'},['id'])).toEqual([]);
  expect(castValue('true',{type:'boolean'})).toBe(true);
  expect(castValue('5',{type:'integer',minimum:1,maximum:10})).toBe(5);
  expect(castValue('{"ok":1}',{type:'object'})).toEqual({ok:1});
  expect(castValue('[1]',{type:'array'})).toEqual([1]);
  for (const [value,schema] of [
    ['not JSON',{type:'object'}],['{}',{type:'array'}],['[1]',{type:'object'}],
    ['',{type:'integer'}],['2.2',{type:'integer'}],['-1',{type:'integer',minimum:0}],
    ['7',{type:'integer',maximum:5}],['wrong',{type:'string',enum:['right']}],
  ] as const) expect(()=>castValue(value,schema)).toThrow();
  expect(normalizeStatus('UNKNOWN')).toBe('unknown');
  expect(normalizeStatus('published')).toBe('observed');
  expect(normalizeStatus('failed')).toBe('terminal');
  expect(normalizeStatus('queued')).toBe('pending');
});

it('parses strict API responses and categorizes network failures without retries', async () => {
  const fetcher=vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({items:[]}),{
    status:200,headers:{'Content-Type':'application/json'}}));
  const client=new StudioClient(()=> 'token','https://api.example',fetcher);
  await client.call('listProjects',{params:{}});
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('https://api.example/v1/projects');
  await expect(new StudioClient(()=>'', '',fetcher).call('listProjects',{params:{}})).rejects.toMatchObject({code:'unauthenticated'});
  const disconnected=new StudioClient(()=> 'token','https://api.example',vi.fn(async()=>{throw Error('network down');}));
  await expect(disconnected.call('listProjects',{params:{}})).rejects.toMatchObject({code:'network_unavailable'});
  const invalidJson=new StudioClient(()=> 'token','https://api.example',vi.fn(async()=>new Response('invalid',{status:200})));
  await expect(invalidJson.call('listProjects',{params:{}})).rejects.toMatchObject({code:'invalid_json'});
  const badResponse=new StudioClient(()=> 'token','https://api.example',vi.fn(async()=>new Response(JSON.stringify([]),{status:200})));
  await expect(badResponse.call('listProjects',{params:{}})).rejects.toMatchObject({code:'invalid_response'});
  const denied=new StudioClient(()=> 'token','https://api.example',vi.fn(async()=>new Response(JSON.stringify({code:'forbidden',message:'denied'}),{status:403})));
  await expect(denied.call('listProjects',{params:{}})).rejects.toMatchObject({code:'forbidden',kind:'server'});
});

it('renders schema-derived input editors and flags invalid JSON', async () => {
  const change=vi.fn(), validity=vi.fn();
  const view=render(<FieldEditor name="object_field" schema={{type:'object'}} value={{}} onChange={change} onValidityChange={validity} required/>);
  fireEvent.change(view.getByRole('textbox'),{target:{value:'not json'}});
  expect(view.getByRole('alert').textContent).toBeTruthy();
  fireEvent.change(view.getByRole('textbox'),{target:{value:'{"x":1}'}});
  expect(change).toHaveBeenCalledWith({x:1});
  view.rerender(<FieldEditor name="bool" schema={{type:'boolean'}} value={false} onChange={change}/>);
  fireEvent.change(view.getByRole('combobox'),{target:{value:'true'}});
  expect(change).toHaveBeenCalledWith(true);
  view.rerender(<FieldEditor name="enum" schema={{type:'string',enum:['ready','failed']}} value="ready" onChange={change}/>);
  fireEvent.change(view.getByRole('combobox'),{target:{value:'failed'}});
  expect(change).toHaveBeenCalledWith('failed');
});

it('drafts local Bible text, computes digest and preserves separate technical draft', async () => {
  const digest=vi.fn(async()=>new Uint8Array(32).buffer);
  vi.stubGlobal('crypto',{subtle:{digest},randomUUID:()=> 'id'});
  render(<BibleWorkbench projectId="project"/>);
  fireEvent.change(screen.getByRole('textbox',{name:'design Bible local text'}),{target:{value:'Draft'}});
  fireEvent.click(screen.getByText('Compute exact UTF-8 SHA-256'));
  await waitFor(()=>expect(screen.getByText('0'.repeat(64))).toBeTruthy());
  fireEvent.click(screen.getByText('Technical Bible'));
  expect(screen.getByRole('textbox',{name:'technical Bible local text'})).toHaveProperty('value','');
});

it('creates a verified existing-chat handoff without pretending to start a model', () => {
  const packet:ShiftHandoff={projectId:'p',mode:'START',workerId:'worker',shiftId:'shift',
    canonSnapshotId:'canon',contractDigest:'digest',githubRelayPath:'github-path',checkpointId:'',additionalInstructions:'owner approved'};
  expect(handoffComplete(packet)).toBe(true);
  expect(makeHandoff(packet)).toContain('existing conversation work-shift START');
  expect(handoffComplete({...packet,mode:'RESUME'})).toBe(false);
  expect(makeHandoff({...packet,mode:'RESUME',checkpointId:'cp'})).toContain('Verified checkpoint ID: cp');
  expect(()=>makeHandoff({...packet,shiftId:''})).toThrow();
  const setRef=vi.fn();
  render(<HandoffPanel projectId="p" githubRef="https://github.com/o/r/tree/main" onGithubRefChange={setRef} hints={{workerId:'w'}}/>);
  expect(screen.getByText(/Open authenticated GitHub relay view/)).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox'),{target:{value:'RESUME'}});
  expect(screen.getByText('Verified checkpoint ID')).toBeTruthy();
});

it('mounts command modal, shows server failures and cancels safely', () => {
  const done=vi.fn(),close=vi.fn();
  const mockClient={call:vi.fn()} as unknown as StudioClient;
  render(<CommandForm operation="createProject" projectId="" client={mockClient} onDone={done} onClose={close}/>);
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
  expect(close).toHaveBeenCalledTimes(1);
  cleanup();
  render(<ErrorBox error={new StudioApiError('server',409,'unknown_effect','pending','request-1',['effect-1'])}/>);
  expect(screen.getByText('UNKNOWN outcome')).toBeTruthy();
  expect(screen.getByText(/Request ID/)).toBeTruthy();
});

it('renders project-isolated operator cockpit, connects token and navigates sections', async () => {
  provider(<App/>);
  expect(screen.getByText('Not connected')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Bearer token'),{target:{value:'temporary-token'}});
  fireEvent.click(screen.getByText('Use token'));
  await waitFor(()=>expect(screen.getByText(/Token provided/)).toBeTruthy());
  fireEvent.click(screen.getByRole('button',{name:/Worker shifts/}));
  expect(screen.getByText('Existing-conversation shift handoff')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:/Canon & bibles/}));
  expect(screen.getByText('Bible drafting workbench')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Filter operations'),{target:{value:'not-a-command'}});
  expect(screen.getByText('No matching commands.')).toBeTruthy();
});

it('renders project-scoped records using the C/J API projection rather than invented data',async()=>{
  const fake={call:vi.fn(async()=>({items:[{task_id:'task-1',state:'ready',note:'accepted'}]}))} as unknown as StudioClient;
  provider(<RecordPanel operation="listTasks" client={fake} projectId="project" section="Work planning" onAction={vi.fn()}/>);
  await waitFor(()=>expect(screen.getByText('task-1')).toBeTruthy());
  expect(screen.getByText('ready')).toBeTruthy();
});