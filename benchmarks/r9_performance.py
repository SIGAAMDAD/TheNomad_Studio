#!/usr/bin/env python3
"""R9 performance measurements only. Synthetic workloads; no production claims.

Standalone, stdlib-only benchmark harness. Not discovered by pytest or release gates.
Never modifies the application, frozen schema, owner map, or credentials.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import socket
import sqlite3
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from nomad_studio.app import open_studio
from nomad_studio.contracts.runtime_ports_v1 import Result
from nomad_studio.gateway.api import GatewayServer, OwnerGateway
from nomad_studio.production.workgraph.core import critical_path
from nomad_studio.production.context.core import normalize_path, overlapping
from nomad_studio.registry.skills import verify_adapter_manifest
from nomad_studio.state.r9_authority import R9Authority, inspect_database
from nomad_studio.state.r9_cas import FileCAS


def rss_bytes() -> int | None:
    if sys.platform.startswith('linux'):
        try:
            for line in Path('/proc/self/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
    return None


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    pos = (len(values) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def summarize(values_ns: list[int], units: list[int]) -> dict[str, Any]:
    if not values_ns or len(values_ns) != len(units) or any(x <= 0 for x in units):
        raise RuntimeError('invalid benchmark samples')
    ms = [ns / 1e6 for ns in values_ns]
    per_op_us = [ns / count / 1e3 for ns, count in zip(values_ns, units)]
    ops_per_s = [count * 1e9 / max(1, ns) for ns, count in zip(values_ns, units)]
    return {
        'trials': len(ms), 'operations_per_trial': units,
        'wall_ms': {'median': statistics.median(ms), 'mean': statistics.mean(ms),
                    'p95': pct(ms, .95), 'p99': pct(ms, .99), 'min': min(ms), 'max': max(ms)},
        'latency_us_per_operation': {'median': statistics.median(per_op_us),
            'p95': pct(per_op_us, .95), 'p99': pct(per_op_us, .99)},
        'throughput_units_per_s': {'median': statistics.median(ops_per_s),
            'p95': pct(ops_per_s, .95)},
    }


class Bench:
    def __init__(self, profile: str, trials: int, seed: int):
        self.profile, self.trials, self.seed = profile, trials, seed
        self.records: list[dict[str, Any]] = []
        self.workload = {
            'smoke': {'history': (0, 1000), 'batch': 32, 'graph': (128, 1024),
                      'threads': (1, 4), 'iterations': 80, 'recovery': 300, 'gateway': 8},
            'standard': {'history': (0, 1000, 10000, 50000), 'batch': 128,
                         'graph': (128, 1024, 4096), 'threads': (1, 4, 8),
                         'iterations': 240, 'recovery': 3000, 'gateway': 24},
            'scale': {'history': (0, 1000, 10000, 50000, 250000), 'batch': 512,
                      'graph': (128, 1024, 4096, 16384), 'threads': (1, 4, 8, 16),
                      'iterations': 1000, 'recovery': 10000, 'gateway': 48},
        }[profile]

    def measure(self, name: str, fn: Callable[[], int], *, detail: dict[str, Any] | None = None,
                warmup: Callable[[], None] | None = None, repetitions: int | None = None) -> None:
        if warmup:
            warmup()
        values = []
        counts = []
        rss0 = rss_bytes()
        for _ in range(repetitions or self.trials):
            start = time.perf_counter_ns()
            n = fn()
            values.append(time.perf_counter_ns() - start)
            counts.append(n)
        rss1 = rss_bytes()
        self.records.append({'case': name, 'workload': detail or {},
            'statistics': summarize(values, counts),
            'rss_before_bytes': rss0, 'rss_after_bytes': rss1,
            'rss_delta_bytes': (rss1-rss0 if rss0 is not None and rss1 is not None else None),
            'samples_ns': values,
            'latency_sampling':'per trial; divided by units for per-unit estimates'})
        stat = self.records[-1]['statistics']
        print(f'{name}: median {stat["wall_ms"]["median"]:.3f} ms; '
              f'{stat["throughput_units_per_s"]["median"]:.1f} units/s', file=sys.stderr, flush=True)


def open_path(root: Path, name: str) -> Path:
    return root / name / 'authority.sqlite'


def seed_outbox(state: R9Authority, count: int, status: str, prefix: str = 'synthetic') -> None:
    if status not in ('PENDING', 'ACKED', 'UNKNOWN', 'CLAIMED'):
        raise ValueError('bad fixture state')
    # One bounded transaction via R9 P2 state authority; no external provider side effects.
    with state._transaction('P2') as tx:
        for i in range(count):
            tx.execute('INSERT INTO v1_outbox(message_id,project_id,feature_id,domain,event_type,'
                       'payload_digest,payload_json,state,created_at_us,retry_after_us) '
                       'VALUES(?,NULL,NULL,?,?,?,?,?,?,NULL)',
                       (f'{prefix}-{i}', 'production.director', 'FEATURE_RUN_TRANSITION',
                        'a'*64, '{}', status, i + 1))


def bench_startup(b: Bench, temp: Path) -> None:
    sequence = iter(range(b.trials + 3))
    def cold() -> int:
        with open_studio(sqlite_path=open_path(temp, f'cold-{next(sequence)}')) as runtime:
            if len(runtime.services) != 22:
                raise RuntimeError('missing measured service ports')
        return 1
    b.measure('runtime.cold_start_and_schema', cold, detail={'storage':'fresh durable SQLite'})
    db = open_path(temp, 'warm')
    with open_studio(sqlite_path=db):
        pass
    def warm() -> int:
        with open_studio(sqlite_path=db) as runtime:
            if runtime.closed:
                raise RuntimeError('runtime prematurely closed')
        return 1
    b.measure('runtime.warm_restart', warm, detail={'storage':'existing empty R9 SQLite'}, warmup=warm)
    def doctor() -> int:
        inspect_database(db)
        return 1
    b.measure('runtime.readonly_db_inspection', doctor, warmup=doctor)


def bench_outbox_scans(b: Bench, temp: Path) -> None:
    for size in b.workload['history']:
        db = open_path(temp, f'history-{size}')
        with open_studio(sqlite_path=db) as runtime:
            if size:
                seed_outbox(runtime.state, size, 'ACKED')
            def health() -> int:
                runtime.health()
                return 1
            b.measure('runtime.health_acked_history', health,
                detail={'acknowledged_history_rows':size},warmup=health)
            def pending() -> int:
                runtime.state.outbox_rows('PENDING')
                return 1
            b.measure('outbox.pending_poll_acked_history', pending,
                detail={'acknowledged_history_rows':size},warmup=pending)
            def query() -> int:
                runtime.state.read(lambda tx: tx.fetch_one('SELECT count(*) AS n FROM v1_outbox WHERE state=?',('PENDING',)))
                return 1
            b.measure('state.indexed_count_pending_history', query,
                detail={'acknowledged_history_rows':size},warmup=query)
        if size == 0:
            continue
        db = open_path(temp, f'backlog-{size}')
        with open_studio(sqlite_path=db) as runtime:
            seed_outbox(runtime.state, size, 'PENDING')
            def poll() -> int:
                runtime.state.outbox_rows('PENDING')
                return size
            b.measure('outbox.pending_scan_backlog', poll,
                detail={'pending_rows':size}, warmup=poll)
            def status() -> int:
                runtime.health()
                return 1
            b.measure('runtime.health_pending_backlog', status,
                detail={'pending_rows':size},warmup=status)


def bench_transactions(b: Bench, temp: Path) -> None:
    with open_studio(sqlite_path=open_path(temp, 'tx')) as runtime:
        cap = runtime.state.capability('CAB-B')
        def read_batch() -> int:
            for _ in range(b.workload['iterations']):
                cap.read(lambda tx: tx.fetch_one('SELECT count(*) AS n FROM v1_projects'))
            return b.workload['iterations']
        b.measure('sqlite.scoped_read', read_batch,
            detail={'reads_per_trial':b.workload['iterations']},warmup=lambda:cap.read(lambda tx:tx.fetch_one('SELECT 1')))
        seq = 0
        def write_batch() -> int:
            nonlocal seq
            for _ in range(b.workload['iterations']):
                seq += 1
                cap.write(lambda tx, k=seq: tx.execute(
                    'INSERT INTO v1_projects(project_id,owner_id,constitution_digest,state,authority_epoch) '
                    'VALUES(?,?,?,?,?)',(f'bench-project-{k}', 'bench-owner', 'a'*64, 'ACTIVE', 1)))
            return b.workload['iterations']
        b.measure('sqlite.scoped_durable_insert', write_batch,
            detail={'transactions_per_trial':b.workload['iterations']})

    for threads in b.workload['threads']:
        # Each run has the same total work; this is throughput scaling, not simulated agents.
        db = open_path(temp, f'concurrent-{threads}')
        with open_studio(sqlite_path=db) as runtime:
            cap = runtime.state.capability('CAB-B')
            serial = iter(range(1, 2_000_000))
            count = max(1, b.workload['iterations']//threads)
            def trial() -> int:
                def writer(worker: int) -> None:
                    for _ in range(count):
                        k=next(serial)
                        cap.write(lambda tx, key=k: tx.execute(
                            'INSERT INTO v1_projects(project_id,owner_id,constitution_digest,state,authority_epoch) '
                            'VALUES(?,?,?,?,?)',(f'worker-{key}', 'owner', 'a'*64,'ACTIVE',1)))
                with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
                    list(pool.map(writer,range(threads)))
                return count*threads
            b.measure('sqlite.concurrent_scoped_writes',trial,
                detail={'threads':threads,'operations_per_trial':count*threads})


def bench_outbox(b: Bench, temp: Path) -> None:
    batch=b.workload['batch']
    with open_studio(sqlite_path=open_path(temp,'outbox-append')) as runtime:
        seq = 0
        def append() -> int:
            nonlocal seq
            with runtime.state.capability('CAB-B').transaction() as tx:
                for _ in range(batch):
                    seq += 1
                    runtime.outbox.append(tx, {'domain':'production.director',
                        'event_type':'FEATURE_RUN_TRANSITION','idempotency_key':f'perf-{seq}'})
            return batch
        b.measure('outbox.append_atomic_batch',append,detail={'batch_size':batch})
    # Fully instrument the R9 dispatcher, including its exact receipt readback.
    batch=max(8,batch)
    with open_studio(sqlite_path=open_path(temp,'outbox-dispatch')) as runtime:
        def sender(_payload: dict[str,Any]) -> Result:
            return Result('OK','OK',receipt_ref='synthetic-performance-receipt')
        def readback(mid: str, ref: str) -> dict[str,Any]:
            row=runtime.state.read(lambda tx:tx.fetch_one(
                'SELECT payload_digest FROM v1_outbox WHERE message_id=?',(mid,)))
            return {'status':'VERIFIED','message_id':mid,'receipt_ref':ref,
                    'payload_digest':row['payload_digest']}
        runtime.outbox._readback=readback
        runtime.native_event_delivery=sender
        seq=0
        def dispatch() -> int:
            nonlocal seq
            with runtime.state.capability('CAB-B').transaction() as tx:
                for _ in range(batch):
                    seq += 1
                    runtime.outbox.append(tx,{'domain':'production.director',
                        'event_type':'FEATURE_RUN_TRANSITION','idempotency_key':f'dispatch-{seq}'})
            # Include only actual dispatch time, not setup; time inside fn is already
            # measured by measure(). The batch-build cost is separately reported.
            rows=runtime.state.outbox_rows('PENDING')
            for row in rows:
                result=runtime.dispatch_once(row['message_id'])
                if result.status != 'OK':
                    raise RuntimeError('synthetic provider delivery declined')
            return len(rows)
        # Includes durable batch setup + enumeration + delivery (label explicit).
        b.measure('outbox.append_poll_dispatch_ack', dispatch,
            detail={'batch_size':batch,'provider':'SIMULATED local receipt'})
        # Isolated request latency: pre-create work outside the stopwatch.
        with runtime.state.capability('CAB-B').transaction() as tx:
            for i in range(batch * b.trials):
                runtime.outbox.append(tx,{'domain':'production.director',
                    'event_type':'FEATURE_RUN_TRANSITION',
                    'idempotency_key':f'isolated-dispatch-{i}'})
        rows=runtime.state.outbox_rows('PENDING')
        samples=[]
        rss0=rss_bytes()
        for row in rows:
            start=time.perf_counter_ns()
            result=runtime.dispatch_once(row['message_id'])
            samples.append(time.perf_counter_ns()-start)
            if result.status != 'OK':
                raise RuntimeError('isolated simulated delivery declined')
        rss1=rss_bytes()
        b.records.append({'case':'outbox.dispatch_once_latency',
            'workload':{'requests':len(rows),'provider':'SIMULATED local receipt'},
            'statistics':summarize(samples,[1]*len(samples)),
            'rss_before_bytes':rss0,'rss_after_bytes':rss1,
            'rss_delta_bytes':rss1-rss0 if rss0 is not None and rss1 is not None else None,
            'samples_ns':samples,'latency_sampling':'individual request'})
        print(f'outbox.dispatch_once_latency: p99 '
              f'{pct(samples,.99)/1e6:.3f} ms across {len(samples)} calls',
              file=sys.stderr,flush=True)


def bench_recovery(b: Bench, temp: Path) -> None:
    n=b.workload['recovery']
    seq=0
    def one() -> int:
        nonlocal seq
        db=open_path(temp,f'recovery-{seq}')
        seq+=1
        with R9Authority(db) as state:
            seed_outbox(state,n,'CLAIMED')
        start=time.perf_counter_ns()
        with R9Authority(db) as reopened:
            if reopened.recovered_unknown_count != n:
                raise RuntimeError('restart recovery did not observe all claimed rows')
        elapsed=time.perf_counter_ns()-start
        return elapsed
    # Seed outside timer; record just restart/recovery and its accompanying verification.
    samples=[]
    rss0=rss_bytes()
    for _ in range(b.trials):
        samples.append(one())
    rss1=rss_bytes()
    b.records.append({'case':'runtime.claimed_to_unknown_restart','workload':{'claimed_rows':n},
        'statistics':summarize(samples,[n]*len(samples)),
        'rss_before_bytes':rss0,'rss_after_bytes':rss1,
        'rss_delta_bytes':rss1-rss0 if rss0 is not None and rss1 is not None else None,
        'samples_ns':samples,
        'latency_sampling':'per trial; recovery wall time divided by recovered rows'})
    print(f'runtime.claimed_to_unknown_restart: median {statistics.median(samples)/1e6:.3f} ms',file=sys.stderr)


def bench_cas(b: Bench, temp: Path) -> None:
    cas=FileCAS(temp/'cas-benchmark')
    rng=random.Random(b.seed)
    for size in (4096,65536,1048576):
        samples=[]
        # Data and digest are prepared outside timed IO, so this measures durable put/get.
        for trial in range(b.trials):
            data=bytes(rng.getrandbits(8) for _ in range(size))
            digest=hashlib.sha256(data).hexdigest()
            samples.append((data,digest))
        i=0
        def put() -> int:
            nonlocal i
            data,digest=samples[i]
            i+=1
            cas.put(data,digest)
            return len(data)
        b.measure('cas.durable_put_bytes',put,
            detail={'payload_bytes':size,'units':'bytes'},repetitions=len(samples))
        i=0
        def get() -> int:
            nonlocal i
            _,digest=samples[i]
            i+=1
            data=cas.get(digest)
            return len(data)
        b.measure('cas.read_verify_bytes',get,
            detail={'payload_bytes':size,'units':'bytes'},repetitions=len(samples))


def bench_graph(b: Bench) -> None:
    for shape in ('chain','wide','fan_in'):
        for n in b.workload['graph']:
            tasks=[]
            for i in range(n):
                if shape=='chain': parents=[f't{i-1}'] if i else []
                elif shape=='wide': parents=[]
                else: parents=[f't{j}' for j in range(max(0,i-3),i)]
                tasks.append({'task_id':f't{i}','dependencies':parents})
            graph={'tasks':tasks}
            def calc() -> int:
                critical_path(graph,set())
                return n
            b.measure('workgraph.critical_path',calc,
                detail={'tasks':n,'shape':shape},warmup=calc)



def bench_service_ports(b: Bench, temp: Path) -> None:
    """Measure real CAB-B APIs and CAB-I/G validation hot paths with local fixtures."""
    batch={'smoke':32,'standard':128,'scale':512}[b.profile]
    with open_studio(sqlite_path=open_path(temp,'cab-b-services')) as runtime:
        projects=runtime.port('ProjectPort')
        briefs=runtime.port('BriefPort')
        synthetic_owner=Result('OK','OK',{'principal_id':'benchmark-owner'},'synthetic-owner-proof')
        seq=0
        def register() -> int:
            nonlocal seq
            for _ in range(batch):
                seq+=1
                result=projects.register_project({
                    'project_id':f'perf-project-{seq}',
                    'owner_principal_id':'benchmark-owner',
                    'constitution_digest':'a'*64,
                    'initial_state':'ACTIVE'},synthetic_owner)
                if result.status!='OK':
                    raise RuntimeError(f'project registration refused: {result.code}')
            return batch
        b.measure('project.register_persistent',register,
            detail={'projects_per_trial':batch,'authority':'SIMULATED owner proof'})
        root=projects.register_project({'project_id':'perf-brief-root',
            'owner_principal_id':'benchmark-owner','constitution_digest':'a'*64,
            'initial_state':'ACTIVE'},synthetic_owner)
        if root.status!='OK':
            raise RuntimeError('benchmark project fixture unavailable')
        seq=0
        def admit() -> int:
            nonlocal seq
            for _ in range(batch):
                seq+=1
                receipt=f'synthetic-command-{seq}'
                result=briefs.admit_brief({'brief_id':f'brief-{seq}',
                    'project_id':'perf-brief-root','feature_id':f'feature-{seq}',
                    'original_text':'Synthetic benchmark input',
                    'owner_command_ref':receipt},
                    Result('OK','OK',{'principal_id':'benchmark-owner'},receipt))
                if result.status!='OK':
                    raise RuntimeError(f'brief admission refused: {result.code}')
            return batch
        b.measure('brief.admit_persistent',admit,
            detail={'briefs_per_trial':batch,'authority':'SIMULATED owner proof'})

    paths=[f'assets/region-{i}/hero/collision.mesh' for i in range(2000)]
    def normalize() -> int:
        for path in paths:
            normalize_path(path)
        return len(paths)
    b.measure('context.resource_path_normalize',normalize,
        detail={'synthetic_paths':len(paths)},warmup=normalize)
    def overlap() -> int:
        for path in paths:
            overlapping(path,'assets/region-1')
        return len(paths)
    b.measure('context.resource_overlap',overlap,
        detail={'synthetic_paths':len(paths)},warmup=overlap)
    manifest={'adapter_id':'synthetic-benchmark-adapter','source_digest':'a'*64,
        'capabilities':[f'capability-{i}' for i in range(64)],
        'evidence_ref':'synthetic-observation','observed':True}
    def verify() -> int:
        for _ in range(200):
            verify_adapter_manifest(manifest)
        return 200
    b.measure('registry.adapter_manifest_validate',verify,
        detail={'capability_entries':64, 'manifests_per_trial':200,
                'authority':'SIMULATED manifest fixture'},warmup=verify)


def bench_gateway(b: Bench, temp: Path) -> None:
    openssl=os.environ.get('OPENSSL', 'openssl')
    cert=temp/'perf-cert.pem'; key=temp/'perf-key.pem'
    try:
        result=subprocess.run([openssl,'req','-x509','-nodes','-newkey','rsa:2048',
            '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost',
            '-addext','basicConstraints=critical,CA:TRUE',
            '-addext','extendedKeyUsage=serverAuth,clientAuth'],
            capture_output=True,timeout=15,check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace')[-400:])
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        b.records.append({'case':'gateway.mtls_rejected_request','skipped':f'OpenSSL unavailable: {exc}'})
        return
    server_ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    server_ctx.load_cert_chain(certfile=str(cert),keyfile=str(key))
    server_ctx.load_verify_locations(cafile=str(cert))
    server_ctx.verify_mode=ssl.CERT_REQUIRED
    client_ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.load_verify_locations(cafile=str(cert))
    client_ctx.load_cert_chain(certfile=str(cert),keyfile=str(key))
    gateway=GatewayServer(OwnerGateway(identity=object(),commands=object(),
        certificate_identities={},audit=lambda _:None),tls_context=server_ctx,port=0)
    host,port=gateway.serve()
    payload=b'POST /v1/owner/command HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}'
    def req() -> int:
        with socket.create_connection((host,port),timeout=5) as plain:
            with client_ctx.wrap_socket(plain,server_hostname='localhost') as sock:
                sock.settimeout(5)
                sock.sendall(payload)
                received=bytearray()
                while b'\r\n\r\n' not in received:
                    segment=sock.recv(4096)
                    if not segment:
                        break
                    received.extend(segment)
                if b'HTTP/1.1 400' not in received:
                    raise RuntimeError('bounded TLS gateway changed response')
        return 1
    try:
        b.measure('gateway.mtls_rejected_request',req,
            detail={'provider':'SIMULATED; rejected request','scope':'TLS handshake + HTTP parsing'},warmup=req,
            repetitions=max(b.trials,b.workload['gateway']))
    finally:
        gateway.close()


def environment() -> dict[str, Any]:
    return {'python':sys.version.split()[0], 'sqlite':sqlite3.sqlite_version,
            'platform':platform.platform(),'processor':platform.processor(),
            'logical_cpus':os.cpu_count(),'pid':os.getpid(),
            'rss_measurement':'/proc/self/status VmRSS' if sys.platform.startswith('linux') else None,
            'clock':'time.perf_counter_ns', 'project':'TheNomad_Studio',
            'provider_mode':'local fixtures, never live providers'}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='Performance ONLY: R9 reproducible benchmark suite')
    parser.add_argument('--profile',choices=('smoke','standard','scale'),default='standard')
    parser.add_argument('--trials',type=int,default=5)
    parser.add_argument('--seed',type=int,default=90134)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if not 2 <= args.trials <= 50:
        parser.error('--trials must be between 2 and 50')
    b=Bench(args.profile,args.trials,args.seed)
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='r9-perf-') as folder:
        temp=Path(folder)
        bench_startup(b,temp)
        bench_outbox_scans(b,temp)
        bench_transactions(b,temp)
        bench_outbox(b,temp)
        bench_recovery(b,temp)
        bench_cas(b,temp)
        bench_graph(b)
        bench_service_ports(b,temp)
        bench_gateway(b,temp)
    payload={'schema':'r9-performance-results-v1','profile':args.profile,
        'seed':args.seed,'trials':args.trials,'environment':environment(),
        'duration_seconds':round(time.monotonic()-started,3),'results':b.records,
        'scope':'PERFORMANCE_ONLY','no_live_provider_or_agent_claims':True,
        'percentiles_note':'p95/p99 are distributions of trials except individual-request cases; no CI SLO gates',
        'thresholds':'none; measurements only'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(f'wrote {len(b.records)} performance case results to {args.output}',file=sys.stderr)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
