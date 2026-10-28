"""HR-FAULT-15 / PUB-09: old exact-revision review never authorizes a new tree."""
from dataclasses import replace
import sqlite3
import unittest

from studio_v16.gateway.contracts import canonical_hash, validate
from studio_v16.gateway.interfaces import VerifiedQA
from tests.v16.db import test_sqlite_publication_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteRevisionQAFenceC8(unittest.TestCase):
    setUp = fixture.SQLitePublicationC8.setUp
    setup_intent = fixture.SQLitePublicationC8.setup_intent
    activate_test = fixture.SQLitePublicationC8.activate_test

    @staticmethod
    def changed_tree(intent):
        new = dict(intent)
        new['target'] = {**intent['target'], 'proposed_tree_sha256': 'f'*64}
        validate(new, 'PUBLISH-V16/INTENT/1')
        return new

    def test_old_qa_subject_does_not_authorize_changed_tree_or_outbox(self):
        state, intent = self.setup_intent()
        self.activate_test()
        changed = self.changed_tree(intent)
        result = self.store.reserve_gateway_intent(changed, fixture.PRINCIPAL,
                    state, state.state_revision, canonical_hash(changed))
        self.assertEqual(result, 'QA_PENDING')
        with self.owner.connection(readonly=True) as conn:
            for table in ('publication_intents','source_publications','outbox','effect_dispatches'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0, table)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 1)

    def test_new_exact_tree_requires_distinct_new_review_while_old_verdict_remains(self):
        state, intent = self.setup_intent()
        self.activate_test()
        changed = self.changed_tree(intent)
        with self.owner.write() as (conn, now):
            conn.execute("UPDATE principals SET status='REVOKED' WHERE principal_id='reviewer_test_001'")
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) "
                         "VALUES('reviewer_second','TEST','reviewer_second','QA','ACTIVE')")
            conn.execute("INSERT INTO reviews(review_id,subject_publication_id,reviewer_principal_id,"
                         "subject_sha,verdict,evidence_ref) VALUES('review_new','pub_authority_001',"
                         "'reviewer_second',?,'PASS','urn:test:new-qa')", ('f'*64,))
        new_qa = VerifiedQA('review_new','reviewer_second','f'*64,
                             fixture.BASE,'urn:test:new-qa')
        fresh = replace(state, qa=new_qa)
        changed['independent_qa'] = {
            'review_id': new_qa.review_id,
            'reviewer_principal_id': new_qa.reviewer_principal_id,
            'subject_sha256': new_qa.subject_sha256,
            'source_sha': new_qa.source_sha,
            'evidence_ref': new_qa.evidence_ref,
            'verdict': 'PASS',
        }
        validate(changed, 'PUBLISH-V16/INTENT/1')
        self.assertEqual(self.store.reserve_gateway_intent(changed,fixture.PRINCIPAL,
                         fresh,fresh.state_revision,canonical_hash(changed)), 'COMMITTED')
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT qa_review_id FROM publication_intents').fetchone(),('review_new',))
            self.assertEqual(conn.execute('SELECT proposed_tree_hash FROM source_publications').fetchone(),('f'*64,))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)


    def test_self_review_cannot_reserve_or_begin_an_external_effect(self):
        state, intent = self.setup_intent()
        self.activate_test()
        with self.owner.write() as (conn, now):
            conn.execute("INSERT INTO reviews(review_id,subject_publication_id,reviewer_principal_id,"
                         "subject_sha,verdict,evidence_ref) VALUES('self-review','pub_authority_001',"
                         "?,?,'PASS','urn:test:self')", (fixture.PRINCIPAL,fixture.TREE))
        qa = VerifiedQA('self-review',fixture.PRINCIPAL,fixture.TREE,
                        fixture.BASE,'urn:test:self')
        self_review = replace(state,qa=qa)
        intent['independent_qa'] = dict(review_id=qa.review_id,reviewer_principal_id=qa.reviewer_principal_id,
                                       subject_sha256=qa.subject_sha256,source_sha=qa.source_sha,
                                       evidence_ref=qa.evidence_ref,verdict='PASS')
        validate(intent,'PUBLISH-V16/INTENT/1')
        self.assertEqual(self.store.reserve_gateway_intent(intent,fixture.PRINCIPAL,self_review,
                         self_review.state_revision,canonical_hash(intent)), 'QA_PENDING')
        with self.owner.connection(readonly=True) as conn:
            for table in ('publication_intents','source_publications','outbox','effect_dispatches'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)

    def test_revoked_owner_decision_and_stale_revision_deny_before_dispatch(self):
        state, intent = self.setup_intent()
        self.activate_test()
        stale = replace(state,state_revision='sqlite:999:grant_pub:1:1')
        self.assertEqual(self.store.reserve_gateway_intent(intent,fixture.PRINCIPAL,stale,
                         stale.state_revision,canonical_hash(intent)), 'AUTHORITY_REVISION_MISMATCH')
        with self.owner.write() as (conn, now):
            conn.execute('UPDATE decisions SET revoked_at=? WHERE decision_id=?',(now,'decision_pub'))
        self.assertEqual(self.store.reserve_gateway_intent(intent,fixture.PRINCIPAL,state,
                         state.state_revision,canonical_hash(intent)), 'REVOKED_GRANT')
        with self.owner.connection(readonly=True) as conn:
            for table in ('publication_intents','source_publications','outbox','effect_dispatches'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)


if __name__ == '__main__':
    unittest.main()
