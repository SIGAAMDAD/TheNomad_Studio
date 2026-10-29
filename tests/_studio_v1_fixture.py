"""Test-only access to sections of the single pinned Studio V1 initial schema."""
from nomad_studio.state.schema import _sql, SCHEMA_SHA256


def sql_for(module: str) -> str:
    marker = '-- Studio V1 initial schema: ' + module + '\n'
    content = _sql()
    if content.count(marker) != 1:
        raise ValueError('V1 schema has no exact section: ' + module)
    return content.split(marker, 1)[1].split('-- Studio V1 initial schema: ', 1)[0]
