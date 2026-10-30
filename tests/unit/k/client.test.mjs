import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..');
const scratch=mkdtempSync(join(tmpdir(),'wgk-client-'));
execFileSync('tsc',['--strict','--target','ES2022','--module','commonjs','--moduleResolution','node','--lib','es2022,dom','--outDir',scratch,
  resolve(root,'ui/src/client.ts'),resolve(root,'ui/src/catalog.ts'),resolve(root,'ui/src/form.ts'),resolve(root,'ui/src/session.ts')], {stdio:'pipe'});
const require=createRequire(import.meta.url);
const {StudioClient,StudioApiError}=require(join(scratch,'ui/src/client.js'));
const {handoffComplete,makeHandoff}=require(join(scratch,'ui/src/session.js'));
const {requiresConfirmation}=require(join(scratch,'ui/src/catalog.js'));
const {castValue,validateRequired}=require(join(scratch,'ui/src/form.js'));
process.on('exit',()=>rmSync(scratch,{recursive:true,force:true}));
const ok=(record)=>({ok:true,status:200,json:async()=>record});

test('typed HTTP GET uses project scoped path and bearer auth',async()=>{
  const seen=[];
  const c=new StudioClient(()=> 'token','',async(url,opts)=>{seen.push({url,opts});return ok({tasks:[]});});
  const response=await c.call('listTasks',{params:{project_id:'alpha/beta'}});
  assert.deepEqual(response,{tasks:[]});
  assert.equal(seen[0].url,'/v1/projects/alpha%2Fbeta/tasks');
  assert.equal(seen[0].opts.headers.Authorization,'Bearer token');
  assert.equal(seen[0].opts.credentials,'omit');
  assert.equal(seen[0].opts.method,'GET');
});

test('no token rejects locally without HTTP request',async()=>{
  let count=0;const c=new StudioClient(()=>'', '',async()=>{count++;return ok({});});
  await assert.rejects(c.call('listProjects',{params:{}}),(e)=>e.code==='unauthenticated');
  assert.equal(count,0);
});

test('missing path parameter rejected, no fallback ID',async()=>{
  let count=0;const c=new StudioClient(()=> 'x', '',async()=>{count++;return ok({});});
  await assert.rejects(c.call('getArtifact',{params:{project_id:'p',artifact_id:''}}),(e)=>e.code==='missing_path_parameter');
  assert.equal(count,0);
});

test('server error retains exact code, request ID and evidence refs',async()=>{
  const c=new StudioClient(()=> 'x','',async()=>({ok:false,status:409,json:async()=>({code:'stale_revision',message:'Mismatch',request_id:'r123',related_refs:['e1']})}));
  await assert.rejects(c.call('listTasks',{params:{project_id:'p'}}),(e)=>e instanceof StudioApiError && e.status===409 && e.code==='stale_revision' && e.requestId==='r123' && e.relatedRefs[0]==='e1');
});

test('network failure on a mutation becomes UNKNOWN, never retried',async()=>{
  let count=0;const c=new StudioClient(()=> 'x','',async()=>{count++;throw new Error('timeout');});
  await assert.rejects(c.call('sendRelay',{params:{project_id:'p'},body:{request_id:'a',from_session_id:'b',to_session_id:'c',payload_digest:'d'}}),(e)=>e.code==='unknown_effect');
  assert.equal(count,1);
});

test('network failure on a GET is reported without claiming mutation',async()=>{
  const c=new StudioClient(()=> 'x','',async()=>{throw new Error('offline');});
  await assert.rejects(c.call('listWorkers',{params:{project_id:'p'}}),(e)=>e.code==='network_unavailable');
});

test('exact JSON payload preserved, no digest synthesis',async()=>{
  let body=null;
  const c=new StudioClient(()=> 'x','',async(_url,opts)=>{body=JSON.parse(opts.body);return ok({edition_id:'ed',snapshot_id:'s'});});
  await c.call('publishBible',{params:{project_id:'p',bible_id:'b'},body:{request_id:'req',intent_id:'intent',decision_id:'dec',effect_digest:'sha:exact'}});
  assert.equal(body.effect_digest,'sha:exact');
  assert.equal(body.request_id,'req');
  assert.equal(body.decision_id,'dec');
  assert.equal(body.expected_edition_id,undefined);
  assert.equal(requiresConfirmation('publishBible'),true);
  assert.equal(requiresConfirmation('listBibles'),false);
});

test('query cursor serializes from exact contract',async()=>{
  let seen='';const c=new StudioClient(()=> 'x','',async(url)=>{seen=url;return ok({events:[],next_cursor:0});});
  await c.call('listEvents',{params:{project_id:'p'},query:{after:5,limit:42}});
  assert.equal(seen,'/v1/projects/p/events?after=5&limit=42');
});

test('field casting rejects invalid numbers, enum values and array JSON',()=>{
  assert.throws(()=>castValue('NaN',{type:'number'}));
  assert.throws(()=>castValue('2.8',{type:'integer'}));
  assert.throws(()=>castValue('bad',{enum:['owner_required','delegated']}));
  assert.throws(()=>castValue('{}',{type:'array'}));
  assert.deepEqual(castValue('["ok"]',{type:'array'}),['ok']);
  assert.deepEqual(validateRequired({a:'',b:'ok'},['a','b']),['a']);
});

test('existing-chat shift start / continuation requires verified packet',()=>{
  const p={mode:'START',projectId:'p',sessionId:'s',taskId:'t',generation:'2',packageDigest:'pkg',canonSnapshotId:'can',contractDigest:'contract',githubRelayPath:'mailbox/immutable.json',checkpointId:'',additionalInstructions:''};
  assert.equal(handoffComplete(p),true);
  const text=makeHandoff(p);
  assert.match(text,/already manually enrolled ChatGPT conversation/);
  assert.match(text,/multiple GitHub assignments/);
  assert.match(text,/active response reads it/);
  assert.equal(handoffComplete({...p,mode:'RESUME'}),false);
  assert.equal(handoffComplete({...p,githubRelayPath:''}),false);
  assert.match(makeHandoff({...p,mode:'RESUME',checkpointId:'chk'}),/Verified checkpoint ID: chk/);
});

test('API rejects success payloads that violate frozen response schema',async()=>{
  const c=new StudioClient(()=> 'x','',async()=>ok({tasks:[{task_id:'t',state:'queued'}]}));
  await assert.rejects(c.call('listTasks',{params:{project_id:'p'}}),e=>e.code==='schema_mismatch' && e.message.includes('feature_id'));
});

test('uncertain status remains visible rather than collapsed to a success',async()=>{
  const c=new StudioClient(()=> 'x','',async()=>ok({effect_id:'e',state:'UNKNOWN'}));
  const effect=await c.call('observeEffect',{params:{project_id:'p',effect_id:'e'}});
  assert.equal(effect.state,'UNKNOWN');
});
