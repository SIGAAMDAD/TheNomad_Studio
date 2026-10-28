"""Lossless storage conversions. Wire timestamps remain unchanged."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import json

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
I64_MAX = (1 << 63) - 1


def integer(value, *, minimum=-(1 << 63), maximum=I64_MAX):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('signed integer out of range')
    return value


def epoch_us(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    delta = value.astimezone(timezone.utc) - EPOCH
    return integer((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)


def utc_us():
    return epoch_us(datetime.now(timezone.utc))


def utc_string(value):
    return (EPOCH + timedelta(microseconds=integer(value))).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def money_units(value):
    if isinstance(value, (float, bool)):
        raise ValueError('money requires Decimal or decimal text')
    try:
        with localcontext() as ctx:
            ctx.prec = 40
            amount = Decimal(value)
            units = amount * 10000
            if not units.is_finite() or units != units.to_integral_value():
                raise ValueError('money exceeds four decimal places')
            return integer(int(units), minimum=0, maximum=999999999999999999)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('invalid money') from exc


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def path_scope(paths):
    from studio_v16.gateway.paths import PATH
    normalized = []
    for path in paths:
        if not isinstance(path, str) or not path.endswith('/') or not PATH.fullmatch(path[:-1]):
            raise ValueError('canonical directory prefixes required')
        normalized.append(path)
    if not normalized:
        raise ValueError('empty path scope')
    return canonical(sorted(set(normalized)))
