"""Load and verify frozen OpenAPI + operation catalog; compile *all* HTTP routes.

No hand-written route table or inference/runtime service is used. This validator
supports every JSON schema keyword occurring in the frozen R5 contract and
fails closed if future contract versions introduce unsupported ones.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .errors import ApiError

ALLOWED_SCHEMA_KEYS = frozenset({
    'type', 'additionalProperties', 'properties', 'required', 'minLength',
    'items', '$ref', 'enum', 'minimum', 'maximum',
})
ALLOWED_TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}
HTTP_METHODS = frozenset({'get', 'post', 'put', 'patch', 'delete'})


@dataclass(frozen=True)
class Operation:
    name: str
    owner: str
    method: str
    path: str
    auth: str
    mutation: bool
    regex: re.Pattern[str]
    parameters: tuple[dict, ...]
    request_schema: dict | None
    response_schema: dict


class FrozenContracts:
    def __init__(self, root: Path | str):
        root = Path(root)
        self.root = root
        self.openapi = json.loads((root / 'openapi.json').read_text(encoding='utf-8'))
        catalog = json.loads((root / 'operation_catalog.json').read_text(encoding='utf-8'))
        semantics = json.loads((root / 'operation_semantics.json').read_text(encoding='utf-8'))
        self.schemas = self.openapi['components']['schemas']
        if self.openapi.get('openapi', '').split('.')[0] != '3':
            raise ValueError('expected OpenAPI v3')
        if catalog['contract_version'] != '1.0.0':
            raise ValueError('unexpected catalog version')
        for schema in self.schemas.values():
            self._check_schema(schema)
        c_ops = {x['id']: x for x in catalog['ops']}
        s_ops = {x['operation_id']: x for x in semantics['operations']}
        if len(c_ops) != len(catalog['ops']) or len(s_ops) != len(semantics['operations']):
            raise ValueError('duplicate contract operation id')
        self.operations: dict[str, Operation] = {}
        self.routes: list[Operation] = []
        for path, methods in self.openapi['paths'].items():
            for method, spec in methods.items():
                if method not in HTTP_METHODS:
                    continue
                name = spec['operationId']
                if name in self.operations or name not in c_ops or name not in s_ops:
                    raise ValueError(f'duplicate or unregistered operation {name}')
                c, s = c_ops[name], s_ops[name]
                if (c['path'], c['method']) != (path, method.upper()):
                    raise ValueError(f'catalog HTTP mismatch for {name}')
                if (s['path'], s['method'], s['owner']) != (path, method.upper(), c['owner']):
                    raise ValueError(f'semantic operation mismatch for {name}')
                if c['auth'] != s['required_auth'] or c['auth'] not in {'owner', 'project', 'authenticated'}:
                    raise ValueError(f'authentication contract mismatch for {name}')
                if bool(c['mutation']) != (method != 'get'):
                    raise ValueError(f'mutation contract mismatch for {name}')
                req = spec.get('requestBody')
                request_schema = self._resolve(req['content']['application/json']['schema']) if req else None
                response_schema = self._resolve(spec['responses']['200']['content']['application/json']['schema'])
                # The operation catalog is a second frozen source: reject drift
                # in fields, requiredness, types or success payloads at startup.
                if set(c['request_fields']) != (set(request_schema['properties']) if request_schema else set()):
                    raise ValueError(f'request catalog mismatch for {name}')
                if set(c['response_fields']) != set(response_schema['properties']):
                    raise ValueError(f'response catalog mismatch for {name}')
                for field, definition in c['request_fields'].items():
                    actual = request_schema['properties'][field]
                    if definition['type'] != actual.get('type') or bool(definition['required']) != (field in request_schema['required']):
                        raise ValueError(f'request field mismatch for {name}.{field}')
                for field, declared_type in c['response_fields'].items():
                    if declared_type != response_schema['properties'][field].get('type'):
                        raise ValueError(f'response field mismatch for {name}.{field}')
                if c['mutation'] and (request_schema is None or 'request_id' not in request_schema['required']):
                    raise ValueError(f'non-idempotent mutation contract {name}')
                if c['mutation'] and s['transaction_owner'] != c['owner']:
                    raise ValueError(f'wrong transaction owner for {name}')
                params = tuple(spec.get('parameters', ()))
                path_params = re.findall(r'{(\w+)}', path)
                if set(path_params) != {x['name'] for x in params if x['in'] == 'path'}:
                    raise ValueError(f'path parameter mismatch: {name}')
                for parameter in params:
                    if parameter['in'] not in {'path', 'query'}:
                        raise ValueError(f'unsupported parameter location: {name}')
                    self._check_schema(parameter['schema'])
                pattern = re.compile('^' + re.sub(r'{(\w+)}', r'(?P<\1>[^/]+)', path) + '$')
                op = Operation(name, c['owner'], method.upper(), path, c['auth'], bool(c['mutation']),
                               pattern, params, request_schema, response_schema)
                self.operations[name] = op
                self.routes.append(op)
        if set(self.operations) != set(c_ops) or set(self.operations) != set(s_ops) or len(self.routes) != 72:
            raise ValueError('incomplete frozen operation registration')
        self.routes.sort(key=lambda op: (op.path.count('{'), -len(op.path)))

    def _resolve(self, schema: dict) -> dict:
        if '$ref' not in schema:
            return schema
        target = schema['$ref']
        prefix = '#/components/schemas/'
        if not target.startswith(prefix):
            raise ValueError('external schema refs forbidden')
        return self.schemas[target[len(prefix):]]

    def _check_schema(self, schema: dict) -> None:
        if not isinstance(schema, dict) or not set(schema).issubset(ALLOWED_SCHEMA_KEYS):
            raise ValueError('unknown JSON schema keyword')
        if '$ref' in schema:
            self._resolve(schema)
            return
        types = schema.get('type', [])
        if isinstance(types, str):
            types = [types]
        if set(types) - ALLOWED_TYPES:
            raise ValueError('unsupported schema type')
        for child in schema.get('properties', {}).values():
            self._check_schema(child)
        if 'items' in schema:
            self._check_schema(schema['items'])

    def validate(self, value: Any, schema: dict, location: str = '$') -> None:
        schema = self._resolve(schema)
        kinds = schema.get('type', [])
        if isinstance(kinds, str):
            kinds = [kinds]
        valid = {
            'null': lambda x: x is None,
            'object': lambda x: isinstance(x, dict),
            'array': lambda x: isinstance(x, list),
            'string': lambda x: isinstance(x, str),
            'integer': lambda x: type(x) is int,
            'number': lambda x: type(x) in (int, float),
            'boolean': lambda x: type(x) is bool,
        }
        if kinds and not any(valid[t](value) for t in kinds):
            raise ApiError('invalid', f'{location}: incorrect JSON type')
        if 'enum' in schema and value not in schema['enum']:
            raise ApiError('invalid', f'{location}: invalid enumerated value')
        if isinstance(value, dict):
            properties = schema.get('properties', {})
            missing = set(schema.get('required', ())) - value.keys()
            if missing:
                raise ApiError('invalid', f'{location}: missing {sorted(missing)[0]}')
            if schema.get('additionalProperties') is False:
                unexpected = set(value) - properties.keys()
                if unexpected:
                    raise ApiError('invalid', f'{location}: unexpected {sorted(unexpected)[0]}')
            for key, child in properties.items():
                if key in value:
                    self.validate(value[key], child, f'{location}.{key}')
        if isinstance(value, list) and 'items' in schema:
            for i, child in enumerate(value):
                self.validate(child, schema['items'], f'{location}[{i}]')
        if isinstance(value, str) and len(value) < schema.get('minLength', 0):
            raise ApiError('invalid', f'{location}: string is too short')
        if type(value) is int:
            if 'minimum' in schema and value < schema['minimum']:
                raise ApiError('invalid', f'{location}: below minimum')
            if 'maximum' in schema and value > schema['maximum']:
                raise ApiError('invalid', f'{location}: above maximum')

    def resolve(self, method: str, path: str) -> tuple[Operation, dict[str, str]]:
        for op in self.routes:
            if op.method != method:
                continue
            match = op.regex.fullmatch(path)
            if match:
                params = {key: unquote(val, errors='strict') for key, val in match.groupdict().items()}
                for key, value in params.items():
                    if not value or '/' in value or '\\' in value or '\x00' in value or value in {'.', '..'}:
                        raise ApiError('invalid', f'unsafe path parameter: {key}')
                return op, params
        raise ApiError('missing', 'unknown operation')
