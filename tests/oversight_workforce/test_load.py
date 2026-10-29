"""Deterministic 500 logical-worker / 800 receipt queue load, not a live host probe."""
import hashlib
import statistics
import time
import unittest

from oversight_workforce import WorkforcePlanning
from oversight_workforce.contracts import CoverageNeed, Identity, Result
from oversight_workforce.store import LocalJournal


class SyntheticLoadTests(unittest.TestCase):
    def test_500_workers_800_events_no_duplicate_identity(self):
        journal=LocalJournal()
        dig=hashlib.sha256(b'fixture').hexdigest()
        identities=[Identity(f'worker-{i:04d}', 'general','dept',1,1,1,dig,
                             frozenset({'read'}), True) for i in range(500)]
        planner=WorkforcePlanning(journal)
        latency=[]
        for i in range(800):
            begin=time.perf_counter_ns()
            status=journal.record_work_event(f'event-{i:04d}',identities[i % 500].worker_id,dig,1_900_000_000+i)
            latency.append((time.perf_counter_ns()-begin)/1_000_000)
            self.assertEqual(status,'RECORDED')
        self.assertEqual(journal.count_events(),800)
        self.assertEqual(len(journal.pending_work_events(1000)),800)
        self.assertEqual(journal.acknowledge_work_event('event-0001',dig),'ACKED')
        self.assertEqual(journal.acknowledge_work_event('event-0001',dig),'DUPLICATE')
        self.assertEqual(len(journal.pending_work_events(1000)),799)
        self.assertEqual(journal.count_workers(),500)
        self.assertEqual(journal.record_work_event('event-0001',identities[1].worker_id,dig,1),'DUPLICATE')
        other=hashlib.sha256(b'changed').hexdigest()
        self.assertEqual(journal.record_work_event('event-0001',identities[1].worker_id,other,1),'DENIED_REPLAY_CONFLICT')
        self.assertEqual(journal.acknowledge_work_event('event-0002',other),'DENIED_DIGEST_MISMATCH')
        gaps=planner.coverage(identities,[CoverageNeed('general','dept',500)])
        self.assertEqual(gaps[0].deficit,0)
        self.assertEqual(len(set(gaps[0].worker_ids)),500)
        suggestion=planner.schedule(identities,500,'approved-synthetic-fixture')
        self.assertEqual(suggestion.status,Result.REVIEW_RECOMMENDED)
        self.assertEqual(len(suggestion.worker_ids),500)
        self.assertGreater(journal.database_bytes(),0)
        sorted_l=sorted(latency)
        p50=sorted_l[int((len(sorted_l)-1)*.50)]
        p95=sorted_l[int((len(sorted_l)-1)*.95)]
        p99=sorted_l[int((len(sorted_l)-1)*.99)]
        print(f'LOAD_FIXTURE event_count=800 distinct_workers=500 db_bytes={journal.database_bytes()} '
              f'event_insert_p50_ms={p50:.6f} p95_ms={p95:.6f} p99_ms={p99:.6f} '
              f'avg_ms={statistics.mean(latency):.6f}')
        journal.close()


if __name__=='__main__': unittest.main()
