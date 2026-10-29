"""Cost and critical path remain evidence-bound and expose missing data."""
from nomad_studio.production.status.api import ProductionStatusService

def test_path_parallel_segments_and_unknown():
    path=ProductionStatusService._critical_path(
        ['A','B','C','D'], [('A','B'),('A','C'),('B','D'),('C','D')],
        {'A':10,'B':20,'C':50,'D':5}, set())
    assert path['critical_path_task_ids']==['A','C','D']
    assert path['critical_path_elapsed_us']==65
    partial=ProductionStatusService._critical_path(['A','B'],[('A','B')],{'A':8}, {'B'})
    assert partial['critical_path_state']=='PARTIAL'
    assert partial['critical_path_elapsed_us'] is None
    cycle=ProductionStatusService._critical_path(['A','B'],[('A','B'),('B','A')],{},set())
    assert cycle['critical_path_state']=='INVALID_GRAPH'
