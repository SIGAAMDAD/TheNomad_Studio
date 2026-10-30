"""Durable R9 SQLite/outbox load test; provider receipts are simulated.

This is a measured local dispatcher/storage load, NOT a live agent-host scale claim.
"""
from __future__ import annotations

from nomad_studio.app import open_studio
from nomad_studio.contracts.runtime_ports_v1 import Result


def test_1024_durable_transitions_dispatch_recover_without_duplicates(tmp_path):
    path=tmp_path/'db'/'authority.sqlite'
    received=[]
    def native_delivery(message):
        received.append(message['idempotency_key'])
        return Result('OK','OK',receipt_ref='sim-receipt-'+message['idempotency_key'])
    def native_readback(message_id,receipt_ref):
        row=runtime.state.read(lambda tx:tx.fetch_one(
            'SELECT payload_digest FROM v1_outbox WHERE message_id=?', (message_id,)))
        return {'status':'VERIFIED','message_id':message_id,'receipt_ref':receipt_ref,
                'payload_digest':row['payload_digest']}
    count=1024
    with open_studio(sqlite_path=path, bindings={
        'native_event_delivery':native_delivery,
        'native_outbox_readback':native_readback}) as runtime:
        with runtime.state.capability('CAB-B').transaction() as tx:
            for i in range(count):
                runtime.outbox.append(tx,{'domain':'production.director',
                    'event_type':'FEATURE_RUN_TRANSITION','idempotency_key':f'load-{i:04}'})
        entries=runtime.state.outbox_rows('PENDING')
        assert len(entries)==count
        for record in entries:
            receipt=runtime.dispatch_once(record['message_id'])
            assert receipt.status=='OK', (receipt.status,receipt.code)
        assert runtime.state.outbox_rows('PENDING')==[]
        assert len(runtime.state.outbox_rows('ACKED'))==count
        assert runtime.state.outbox_rows('UNKNOWN')==[]
        assert len(received)==count and len(set(received))==count
    with open_studio(sqlite_path=path) as reopened:
        assert len(reopened.state.outbox_rows('ACKED'))==count
        assert reopened.state.outbox_rows('PENDING')==[]
        assert reopened.state.outbox_rows('UNKNOWN')==[]
        for entry in reopened.state.outbox_rows('ACKED')[:10]:
            # Reopening without provider credentials cannot replay an ACKED effect.
            assert reopened.dispatch_once(entry['message_id']).status=='NOT_VERIFIED'
            assert reopened.state.outbox_rows('PENDING')==[]
