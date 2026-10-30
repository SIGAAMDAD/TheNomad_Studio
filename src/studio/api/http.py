"""Dependency-free WSGI HTTP adapter generated from the exact OpenAPI routes.

Pass this application to a configured WSGI server or gateway. `wsgiref`
provides a local smoke-test host only; source deployment belongs to WG-M.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qsl

from .contracts import FrozenContracts, Operation
from .errors import ApiError
from .service import StudioOperatorService

MAX_BODY_BYTES = 1024 * 1024
INT_PATTERN = re.compile(r'^(0|[1-9][0-9]*)$')


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON property')
        result[key] = value
    return result


def _decode_query(raw: str, op: Operation, contracts: FrozenContracts) -> dict:
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError('invalid', 'malformed query parameters') from exc
    query = {}
    permitted = {p['name']: p for p in op.parameters if p['in'] == 'query'}
    for name, value in pairs:
        if name not in permitted or name in query:
            raise ApiError('invalid', 'unknown or repeated query parameter')
        schema = permitted[name]['schema']
        if schema.get('type') == 'integer':
            if not INT_PATTERN.fullmatch(value):
                raise ApiError('invalid', f'{name}: invalid integer')
            parsed = int(value)
        elif schema.get('type') == 'boolean':
            if value not in {'true', 'false'}:
                raise ApiError('invalid', f'{name}: invalid boolean')
            parsed = value == 'true'
        else:
            parsed = value
        contracts.validate(parsed, schema, name)
        query[name] = parsed
    for parameter in permitted.values():
        if parameter.get('required') and parameter['name'] not in query:
            raise ApiError('invalid', f'missing query parameter {parameter["name"]}')
    return query


def _decode_body(environ: dict, op: Operation, contracts: FrozenContracts) -> dict | None:
    raw_length = environ.get('CONTENT_LENGTH', '')
    if not raw_length and op.mutation:
        raise ApiError('invalid', 'Content-Length required')
    if raw_length:
        if not raw_length.isascii() or not raw_length.isdigit():
            raise ApiError('invalid', 'invalid Content-Length')
        length = int(raw_length)
    else:
        length = 0
    if length > MAX_BODY_BYTES:
        raise ApiError('invalid', 'request body exceeds limit', status=413)
    if op.request_schema is None:
        if length:
            raise ApiError('invalid', 'GET operation does not accept body')
        return None
    media_type = environ.get('CONTENT_TYPE', '').split(';', 1)[0].strip().lower()
    if media_type != 'application/json':
        raise ApiError('invalid', 'JSON content type required', status=415)
    body = environ['wsgi.input'].read(length)
    if len(body) != length:
        raise ApiError('invalid', 'incomplete request body')
    try:
        result = json.loads(body.decode('utf-8'), object_pairs_hook=_no_duplicates,
                            parse_constant=lambda val: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    except (ValueError, UnicodeError) as exc:
        raise ApiError('invalid', 'malformed JSON body') from exc
    contracts.validate(result, op.request_schema)
    return result


def _bearer(environ: dict) -> str:
    value = environ.get('HTTP_AUTHORIZATION', '')
    kind, separator, credential = value.partition(' ')
    if kind.lower() != 'bearer' or not separator or not credential or ' ' in credential:
        raise ApiError('forbidden', 'authenticated bearer token required', status=401)
    return credential


class StudioWSGI:
    def __init__(self, service: StudioOperatorService):
        self.service = service

    def __call__(self, environ: dict, start_response: Callable):
        request_id = None
        try:
            method = environ.get('REQUEST_METHOD', '').upper()
            path = environ.get('PATH_INFO', '')
            if len(path) > 4096 or not path.startswith('/'):
                raise ApiError('invalid', 'invalid path')
            op, path_params = self.service.contracts.resolve(method, path)
            token = _bearer(environ)
            query = _decode_query(environ.get('QUERY_STRING', ''), op, self.service.contracts)
            body = _decode_body(environ, op, self.service.contracts)
            if body:
                request_id = body.get('request_id') if isinstance(body.get('request_id'), str) else None
            result = self.service.execute(op, token, path_params, query, body)
            status = 200
        except ApiError as error:
            status = error.http_status
            result = error.envelope(request_id)
        except Exception:
            # No stacktraces, exceptions, SQL or credentials in external errors.
            status = 500
            result = ApiError('internal', 'internal service error').envelope(request_id)
        body_bytes = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':'), allow_nan=False).encode('utf-8')
        reason = {
            200: 'OK', 400: 'Bad Request', 401: 'Unauthorized',
            403: 'Forbidden', 404: 'Not Found', 409: 'Conflict',
            413: 'Content Too Large', 415: 'Unsupported Media Type',
            500: 'Internal Server Error', 503: 'Service Unavailable',
        }.get(status, 'Error')
        start_response(f'{status} {reason}', [
            ('Content-Type', 'application/json; charset=utf-8'),
            ('Content-Length', str(len(body_bytes))),
            ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'),
        ])
        return [body_bytes]
