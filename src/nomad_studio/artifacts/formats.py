"""Bounded structural checks; semantic checks for opaque formats use registered tools."""
from __future__ import annotations
import io
import json
import struct
import wave
import zlib
import zipfile
from typing import Callable, Mapping
from .models import ArtifactManifest, SchemaError


class MissingTool(SchemaError):
    def __init__(self, kind: str):
        super().__init__('BLOCKED_MISSING_TOOL', f'no registered format validator for {kind}')


def _png(data: bytes) -> None:
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('PNG signature invalid')
    pos = 8
    saw_ihdr = saw_data = saw_end = False
    compressed_parts: list[bytes] = []
    while pos + 12 <= len(data):
        n = struct.unpack_from('>I', data, pos)[0]
        if n > len(data) - pos - 12:
            raise ValueError('PNG truncated')
        tag = data[pos+4:pos+8]
        payload = data[pos+8:pos+8+n]
        crc = struct.unpack_from('>I', data, pos+8+n)[0]
        if (zlib.crc32(tag + payload) & 0xffffffff) != crc:
            raise ValueError('PNG CRC mismatch')
        if not saw_ihdr and (tag != b'IHDR' or n != 13):
            raise ValueError('PNG IHDR invalid')
        if tag == b'IHDR':
            if saw_ihdr or n != 13 or not all(struct.unpack_from('>II', payload)):
                raise ValueError('duplicate/invalid IHDR')
            saw_ihdr = True
        if tag == b'IDAT':
            saw_data = True
            compressed_parts.append(payload)
        if tag == b'IEND':
            if n or pos + 12 != len(data): raise ValueError('PNG trailing bytes')
            saw_end = True
            break
        pos += 12 + n
    if not (saw_ihdr and saw_data and saw_end):
        raise ValueError('PNG required chunks missing')
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(b''.join(compressed_parts), 128 * 1024 * 1024 + 1)
    if not decoder.eof or len(decoded) > 128 * 1024 * 1024:
        raise ValueError('PNG zlib data invalid or exceeds validation limit')


def _glb(data: bytes) -> None:
    if len(data) < 20 or data[:4] != b'glTF' or struct.unpack_from('<II', data, 4) != (2, len(data)):
        raise ValueError('GLB header/version/length invalid')
    n, tag = struct.unpack_from('<I4s', data, 12)
    if tag != b'JSON' or n == 0 or n % 4 or n + 20 > len(data):
        raise ValueError('GLB JSON chunk invalid')
    j = json.loads(data[20:20+n].decode('utf8').rstrip(' \x00'))
    if not isinstance(j, dict) or not isinstance(j.get('asset'), dict) or not str(j['asset'].get('version', '')).startswith('2.'):
        raise ValueError('GLB asset definition absent')
    pos = 20 + n
    if pos < len(data):
        if pos + 8 > len(data):raise ValueError('GLB chunk truncated')
        m, tag = struct.unpack_from('<I4s', data, pos)
        if tag != b'BIN\x00' or m % 4 or pos + 8 + m != len(data):
            raise ValueError('GLB BIN chunk invalid')


def _zip(data: bytes, docx: bool) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if not names or len(names) > 20000 or len(set(names)) != len(names):
            raise ValueError('invalid ZIP entry list')
        if sum(info.file_size for info in archive.infolist()) > 1024 * 1024 * 1024:
            raise ValueError('archive uncompressed size limit exceeded')
        for info in archive.infolist():
            if info.filename.startswith('/') or '\\' in info.filename or any(x in ('..', '.') for x in info.filename.split('/')):
                raise ValueError('unsafe archive path')
            if info.file_size > 1_000_000_000 or info.compress_size > 0 and info.file_size > 200 * info.compress_size:
                raise ValueError('archive decompression bomb')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('archive symlink')
        if docx and not {'[Content_Types].xml', 'word/document.xml'} <= set(names):
            raise ValueError('DOCX contents missing')
        if archive.testzip() is not None:
            raise ValueError('archive CRC failure')


def _wav(data: bytes) -> None:
    with wave.open(io.BytesIO(data), 'rb') as audio:
        if not audio.getnchannels() or not audio.getframerate() or not audio.getnframes():
            raise ValueError('invalid WAV parameters')
        if audio.getsampwidth() not in (1, 2, 3, 4):
            raise ValueError('unsupported WAV sample width')
        audio.readframes(audio.getnframes())


def _gltf(data: bytes) -> None:
    j = json.loads(data)
    if not isinstance(j, dict) or not isinstance(j.get('asset'), dict) or not str(j['asset'].get('version', '')).startswith('2.'):
        raise ValueError('glTF schema invalid')


def _text(data: bytes) -> None:
    if not data.decode('utf8').strip():
        raise ValueError('empty text')


def _json(data: bytes) -> None:
    if not isinstance(json.loads(data), (dict, list)):
        raise ValueError('JSON root invalid')


def _signature(data: bytes, prefix: bytes) -> None:
    if not data.startswith(prefix):
        raise ValueError('file signature invalid')


def validate(manifest: ArtifactManifest, data: bytes, tools: Mapping[str, Callable[[bytes, Mapping[str, object]], object]]) -> str:
    """Return validator identifier. A tool must return True or {'valid': True, 'tool_version':...}."""
    ext = '.' + manifest.filename.rsplit('.', 1)[-1].lower()
    structural = {
        '.png': _png, '.glb': _glb, '.gltf': _gltf, '.wav': _wav,
        '.zip': lambda d: _zip(d, False), '.docx': lambda d: _zip(d, True),
        '.md': _text, '.txt': _text, '.json': _json,
        '.ogg': lambda d: _signature(d, b'OggS'),
        '.flac': lambda d: _signature(d, b'fLaC'),
        '.jpg': lambda d: _signature(d, b'\xff\xd8\xff'),
        '.jpeg': lambda d: _signature(d, b'\xff\xd8\xff'),
        '.webp': lambda d: (_signature(d, b'RIFF'), _signature(d[8:], b'WEBP')),
        '.pdf': lambda d: (_signature(d, b'%PDF-'), b'%%EOF' in d[-2048:] or (_ for _ in ()).throw(ValueError('missing PDF EOF'))),
        '.svg': _text, '.tscn': _text, '.tres': _text,
    }
    try:
        if ext in structural:
            structural[ext](data)
        # Opaque, semantic and proprietary payloads require real format-specific tools.
        needs_tool = ext not in {'.png', '.wav', '.zip', '.docx', '.md', '.txt', '.json'} or manifest.kind in ('mesh', 'animation', 'fmod_bank', 'fmod_project', 'vfx', 'build')
        if needs_tool:
            tool = tools.get(manifest.kind) or tools.get(ext)
            if tool is None:
                raise MissingTool(manifest.kind + '/' + ext)
            try:
                result = tool(data, manifest.dict())
            except Exception as ex:
                raise SchemaError('TOOL_FAILED', 'registered validator unavailable or failed') from ex
            if result is True:
                raise ValueError('tool omitted version/evidence')
            if not isinstance(result, Mapping) or set(result) - {'valid', 'tool_version', 'evidence_sha256'} or result.get('valid') is not True or not isinstance(result.get('tool_version'), str) or not result['tool_version'].strip():
                raise ValueError('tool did not return a valid evidence-bearing result')
            return 'registered:' + manifest.kind + ':' + result['tool_version']
        return 'builtin:' + ext + ':1'
    except SchemaError:
        raise
    except (ValueError, OSError, EOFError, UnicodeError, struct.error, zipfile.BadZipFile, wave.Error, zlib.error) as ex:
        raise SchemaError('INVALID_FORMAT', str(ex)) from ex
