import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const spec = JSON.parse(readFileSync(resolve(root,'CONTRACTS/openapi.json'),'utf8'));
const generatedPath = resolve(root,'ui/src/generated/operations.ts');
const generated = readFileSync(generatedPath,'utf8');
const source = Object.entries(spec.paths).flatMap(([path, route])=>Object.entries(route).filter(([method])=>['get','post','put','patch','delete'].includes(method)).map(([method,op])=>({id:op.operationId,method:method.toUpperCase(),path})));

test('frozen API covers 72 unique operations, with typed path and methods',()=>{
  assert.equal(source.length,72);
  assert.equal(new Set(source.map(x=>x.id)).size,72);
  assert.match(generated,/Record<StudioOperationId, OperationMeta>/);
  for(const entry of source){
    assert.ok(generated.includes(`"${entry.id}": {`),`Missing ${entry.id}`);
    assert.ok(generated.includes(`"path": "${entry.path}"`),`Missing ${entry.path}`);
  }
});

test('generated operation metadata is byte-for-byte deterministic',()=>{
  const before=readFileSync(generatedPath);
  execFileSync(process.execPath,[resolve(root,'ui/scripts/generate-api.mjs')],{cwd:resolve(root,'ui')});
  assert.deepEqual(readFileSync(generatedPath),before);
});

test('all commands are displayed in exactly one cockpit section',()=>{
  const catalog=readFileSync(resolve(root,'ui/src/catalog.ts'),'utf8');
  const ids=source.map(x=>x.id);
  for(const id of ids){
    const matches=catalog.match(new RegExp(`'${id}'`,'g'))??[];
    assert.ok(matches.length>=1,`Missing operation ${id}`);
  }
  const declared=[...catalog.matchAll(/^  '[^']+': \[([^\]]+)\]/gm)].flatMap((match)=>[...match[1].matchAll(/'([^']+)'/g)].map(x=>x[1]));
  // The unquoted Projects/Organization/Operations/Permissions section keys are included separately.
  const sectionSlices=[...catalog.matchAll(/^  (?:'[^']+'|[A-Za-z]+): \[([^\]]+)\]/gm)].flatMap((match)=>[...match[1].matchAll(/'([^']+)'/g)].map(x=>x[1]));
  assert.equal(sectionSlices.length,72);
  assert.deepEqual(new Set(sectionSlices),new Set(ids));
  assert.equal(new Set(sectionSlices).size,sectionSlices.length);
  assert.ok(declared.length>10);
});

test('UI does not claim to run browser workers automatically or call paid inference',()=>{
  const paths=['ui/src/client.ts','ui/src/App.tsx','ui/src/components/HandoffPanel.tsx','ui/src/shift.ts'];
  const content=paths.map(p=>readFileSync(resolve(root,p),'utf8')).join('\n');
  assert.doesNotMatch(content,/chat\.completions|responses\.create|api\.openai\.com|anthropic\.com\/v1/);
  assert.match(content,/manually enrolled|human begins|manually initiated/i);
  assert.match(content,/UNKNOWN/);
});
