import hashlib
import unittest
from unittest.mock import patch

from taskmaster import registry

NOW = '2026-10-17T20:00:00Z'
LATER = '2026-10-17T20:01:00Z'
END = '2026-10-17T20:02:00Z'
SID = '12345678-1234-1234-1234-123456789abc'
RID = 'dias:codex-hook:' + hashlib.sha256(SID.encode()).hexdigest()


class RegistryTests(unittest.TestCase):
    def row(self, objective='ops/one', provenance='detected'):
        return {'run_id': RID, 'objective': objective, 'provenance': provenance, 'candidates': []}

    def feed(self, session_id=SID):
        return [{'runId': RID, 'resume': {'provider': 'codex', 'session_id': session_id}}]

    def test_register_resume_and_keep_inactive_session_without_duplicates(self):
        first = registry.reconcile(None, [self.row()], self.feed(), {}, NOW, 'current', 70)
        gone = registry.reconcile(first, [], [], {}, LATER, 'current', 70)
        self.assertFalse(gone['sessions'][0]['active'])
        self.assertEqual(gone['sessions'][0]['session_id'], SID)
        resumed = registry.reconcile(gone, [self.row()], self.feed(), {}, END, 'current', 70)
        self.assertEqual(len(resumed['sessions']), 1)
        self.assertEqual(len(resumed['sessions'][0]['assignments']), 1)
        self.assertEqual(resumed['sessions'][0]['first_seen_at'], NOW)
        self.assertEqual(resumed['sessions'][0]['last_seen_at'], END)

    def test_correction_clear_and_historical_lookup(self):
        first = registry.reconcile(None, [self.row()], self.feed(), {}, NOW, 'current', 70)
        moved = registry.reconcile(first, [self.row('ops/two', 'operator')], self.feed(), {RID: 'ops/two'}, LATER, 'current', 70)
        cleared = registry.reconcile(moved, [], [], {RID: None}, END, 'current', 70)
        row = cleared['sessions'][0]
        self.assertIsNone(row['objective'])
        self.assertEqual([a['ended_at'] for a in row['assignments']], [LATER, END])
        self.assertEqual(len(registry.for_trail(cleared, 'ops/one')['sessions']), 1)
        self.assertIn(SID, registry.render(cleared))

    def test_missing_ids_can_be_enriched_but_mismatched_hash_never_admitted(self):
        missing = registry.reconcile(None, [self.row()], [{'runId': RID}], {}, NOW, 'current', 70)
        self.assertIsNone(missing['sessions'][0]['session_id'])
        bad = registry.reconcile(missing, [self.row()], self.feed('wrong-id'), {}, LATER, 'current', 70)
        self.assertIsNone(bad['sessions'][0]['session_id'])
        good = registry.reconcile(bad, [self.row()], self.feed(), {}, END, 'current', 70)
        self.assertEqual(good['sessions'][0]['session_id'], SID)

    def test_unavailable_feed_preserves_history_and_does_not_claim_active(self):
        first = registry.reconcile(None, [self.row()], self.feed(), {}, NOW, 'current', 70)
        unavailable = registry.reconcile(first, [], [], {}, LATER, 'unavailable', 70)
        self.assertEqual(unavailable['sessions'][0]['last_seen_at'], NOW)
        self.assertEqual(unavailable['sessions'][0]['objective'], 'ops/one')
        self.assertFalse(unavailable['sessions'][0]['active'])
        self.assertEqual(unavailable['status'], 'unavailable')

    def test_ambiguous_session_is_not_registered_to_a_trail(self):
        value = registry.reconcile(None, [self.row(None, 'ambiguous')], self.feed(), {}, NOW, 'current', 70)
        self.assertEqual(value['sessions'][0]['assignments'], [])

    def test_corrupt_history_and_capacity_fail_instead_of_losing_data(self):
        first = registry.reconcile(None, [self.row()], self.feed(), {}, NOW, 'current', 70)
        with patch.object(registry, 'MAX_SESSIONS', 0), self.assertRaises(ValueError):
            registry.reconcile(first, [], [], {}, LATER, 'current', 70)
        first['sessions'][0]['session_id'] = 'wrong'
        with self.assertRaises(ValueError):
            registry.reconcile(first, [], [], {}, LATER, 'current', 70)


if __name__ == '__main__':
    unittest.main()
