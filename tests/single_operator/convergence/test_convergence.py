"""Pure advisory convergence projection; old convergence gateway retired."""
import unittest
from nomad_studio.single_operator.convergence import unresolved_dependencies

class ConvergenceProjectionTests(unittest.TestCase):
    def test_unresolved_only_and_no_mutation(self):
        data={'state':'OPEN','conflict':{'dependency_refs':['c','a','c']}}
        self.assertEqual(unresolved_dependencies(data),('a','c'))
        self.assertEqual(data['conflict']['dependency_refs'],['c','a','c'])
        self.assertEqual(unresolved_dependencies({'state':'RESOLVED','conflict':data['conflict']}),())
