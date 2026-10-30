#!/usr/bin/env node
import {readFileSync, existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const data=JSON.parse(readFileSync(resolve(root,'ui/HANDOFF_MANIFEST.json'),'utf8'));
let errors=0;
for(const [path,sha] of Object.entries(data.files)) {
  const target=resolve(root,path);
  if(!existsSync(target)){console.error(`MISSING ${path}`);errors++;continue;}
  const actual=createHash('sha256').update(readFileSync(target)).digest('hex');
  if(actual!==sha){console.error(`CHANGED ${path}: ${actual}`);errors++;}
}
const originalContracts={
  'CONTRACTS/openapi.json': data.contract_hashes.openapi,
  'CONTRACTS/api.types.ts': data.contract_hashes.api_types
};
for(const [path,sha] of Object.entries(originalContracts)) {
  const target=resolve(root,path);
  if(!existsSync(target)){console.error(`MISSING CONTRACT ${path}`);errors++;continue;}
  if(createHash('sha256').update(readFileSync(target)).digest('hex')!==sha){console.error(`CONTRACT DRIFT ${path}`);errors++;}
}
console.log(`WG-K handoff: ${Object.keys(data.files).length} files, ${errors} discrepancies`);
if(errors)process.exitCode=1;
