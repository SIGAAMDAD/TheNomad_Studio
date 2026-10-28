"""The strict C8 gate must run when review is requested, not on every draft commit.

This test is independent of C8 test evidence, and preserves the strict gate
commands so a draft workflow cannot silently change acceptance criteria.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / '.github/workflows/studio-sqlite-c8-merge-gate.yml'


class DraftGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding='utf-8')

    def test_draft_prs_skip_strict_gate(self):
        expression = next(x for x in self.source.splitlines()
                          if x.strip().startswith('if: ${{'))
        self.assertIn('github.event.pull_request.draft == false', expression)
        self.assertIn("github.event_name == 'workflow_dispatch'", expression)
        self.assertNotIn('github.event.pull_request.draft != false', expression)

    def test_ready_for_review_and_subsequent_commits_trigger(self):
        events = next(x for x in self.source.splitlines()
                      if x.strip().startswith('types: ['))
        self.assertIn('ready_for_review', events)
        self.assertIn('synchronize', events)
        self.assertIn('reopened', events)
        self.assertIn('converted_to_draft', events)
        self.assertNotIn('\n  push:', self.source)

    def test_staging_gate_is_still_strict(self):
        commands = self.source
        self.assertIn('scripts/sqlite_acceptance.py --run-mapped --json', commands)
        self.assertNotIn('--report-only', commands)
        self.assertIn('scripts/sqlite_c8_merge_gate.py', commands)
        self.assertIn('strict-acceptance.json', commands)
        self.assertIn('NOMAD_STAGING_ATTESTATIONS_PATH', commands)
        self.assertIn('inputs.subject_sha', self.source)

    def test_no_untrusted_publish_or_mutation_permissions(self):
        self.assertIn('\n  contents: read\n', self.source)
        self.assertIn('\n  pull-requests: read\n', self.source)
        commands = self.source
        self.assertNotIn('git push', commands)
        self.assertNotIn('gh pr merge', commands)


if __name__ == '__main__':
    unittest.main()
