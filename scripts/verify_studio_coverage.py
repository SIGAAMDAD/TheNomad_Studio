"""Read exact coverage artifacts for each Studio execution language.

Reports coverage only for production source roots, not tests or generated CI
runners. Missing files and empty/incorrect denominators fail closed. The
optional --require-all gate is enabled only after all three suites are measured.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]


def pct(covered: int, total: int) -> float:
    if total <= 0 or not 0 <= covered <= total:
        raise ValueError('invalid coverage denominator')
    return 100.0 * covered / total


def python_coverage(path: pathlib.Path) -> tuple[int, int]:
    root = ET.parse(path).getroot()
    lines = (int(root.attrib['lines-covered']), int(root.attrib['lines-valid']))
    if not root.findall('.//class'):
        raise ValueError('Python Cobertura report has no instrumented modules')
    return lines


def ts_coverage(path: pathlib.Path) -> tuple[int, int]:
    data = json.loads(path.read_text(encoding='utf-8'))
    sources = {p.resolve() for p in (ROOT/'ui'/'src').rglob('*')
               if p.is_file() and p.suffix in ('.ts', '.tsx') and not p.name.endswith('.d.ts')}
    observed = {}
    for name, item in data.items():
        if name == 'total':
            continue
        target = pathlib.Path(name).resolve()
        if target in sources:
            observed[target] = item['lines']
    missing = sources.difference(observed)
    if missing:
        raise ValueError('Uninstrumented TS source: ' + ', '.join(sorted(str(x.relative_to(ROOT)) for x in missing)))
    return sum(int(v['covered']) for v in observed.values()), sum(int(v['total']) for v in observed.values())


def authored_ts_coverage(path: pathlib.Path) -> tuple[int, int]:
    data=json.loads(path.read_text(encoding='utf-8'))
    sources={p.resolve() for p in (ROOT/'ui'/'src').rglob('*')
             if p.is_file() and p.suffix in ('.ts','.tsx')
             and not p.name.endswith('.d.ts')
             and '/generated/' not in p.as_posix()}
    observed={pathlib.Path(name).resolve():val['lines'] for name,val in data.items()
              if name!='total' and pathlib.Path(name).resolve() in sources}
    if sources.difference(observed):
        raise ValueError('Untested authored cockpit files: '+', '.join(sorted(str(x) for x in sources.difference(observed))))
    return sum(int(v['covered']) for v in observed.values()),sum(int(v['total']) for v in observed.values())


def host_coverage(path: pathlib.Path) -> tuple[int, int]:
    root = ET.parse(path).getroot()
    host_root = ROOT/'host'/'Nomad.ToolHost'
    # Include nested production files, but exclude SDK-generated build outputs.
    expected = {p.name for p in host_root.rglob('*.cs')
                if not {'bin', 'obj'}.intersection(p.relative_to(host_root).parts)}
    actual = {}
    for package in root.findall('.//package'):
        for cls in package.findall('.//class'):
            filename = pathlib.Path(cls.attrib.get('filename', '')).name
            if filename not in expected:
                continue
            source_name = cls.attrib.get('name', '')
            # Collect every host class under the production assembly, ignoring
            # instrumented tests with coincidentally matching file basenames.
            if not ('Nomad.ToolHost' in source_name or 'Nomad.ToolHost' in package.attrib.get('name', '')):
                continue
            hits = actual.setdefault(filename, {})
            for line in cls.findall('.//line'):
                number = int(line.attrib['number'])
                hits[number] = max(hits.get(number, 0), int(line.attrib['hits']))
    missing = expected.difference(actual)
    if missing:
        raise ValueError('Uninstrumented .NET host files: ' + ', '.join(sorted(missing)))
    return sum(sum(n > 0 for n in lines.values()) for lines in actual.values()), sum(len(lines) for lines in actual.values())


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument('--python', type=pathlib.Path)
    cli.add_argument('--typescript', type=pathlib.Path)
    cli.add_argument('--dotnet', type=pathlib.Path)
    cli.add_argument('--require-all', action='store_true')
    cli.add_argument('--minimum', type=float)
    args = cli.parse_args()
    checks = {'Python': (args.python, python_coverage),
              'TypeScript': (args.typescript, ts_coverage),
              '.NET': (args.dotnet, host_coverage)}
    failures = []
    missing = []
    for label, (path, load) in checks.items():
        if path is None:
            missing.append(label)
            continue
        covered, total = load(path)
        percent = pct(covered, total)
        print(f'{label}: {covered}/{total} production lines ({percent:.2f}%)')
        if percent < (args.minimum if args.minimum is not None else 90):
            failures.append(label)
    if args.typescript:
        covered,total=authored_ts_coverage(args.typescript)
        percent=pct(covered,total)
        print(f'Authored cockpit (excluding generated catalog): {covered}/{total} ({percent:.2f}%)')
        if percent<(args.minimum if args.minimum is not None else 90): failures.append('Authored cockpit')
    if args.minimum is not None and failures:
        raise SystemExit('Coverage below minimum: '+', '.join(failures))
    if args.require_all and (failures or missing):
        raise SystemExit('Coverage below 90% or missing instrumentation: '+', '.join(failures+missing))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())