"""Conservative source indicators for explicit prohibited execution models.

Static scan PASS means listed patterns were absent; runtime execution requires
separate positive/negative evidence. Scans exclude QA and test fixtures.
"""
from __future__ import annotations
import re
from pathlib import Path

PATTERNS = {
    'NEG-AI-API': [
        r'\b(?:from|import)\s+(?:openai|anthropic|litellm|autogen|crewai|langchain|agents\.sdk)\b',
        r'\b(?:OpenAI|AsyncOpenAI|Anthropic|AsyncAnthropic)\s*\(',
        r'\b(?:chat\.completions|responses\.create|messages\.create)\s*\(',
        r'\b(?:model_provider_api_key|OPENAI_API_KEY|ANTHROPIC_API_KEY)\b',
    ],
    'NEG-LEGACY': [
        r'\b(?:SQLiteStateStore|legacy_schema|migrate_from_v1|v1_compat)\b',
    ],
    'NEG-BRANCH-WAVE': [
        r'\b(?:create_workgroup_branch|second_implementation_wave|spawn_workgroup_branch)\s*\(',
        r'\bgit\s+(?:checkout\s+-b|switch\s+-c|branch\s+(?!-))\s*(?:wg[-_/]|workgroup)',
    ],
    'NEG-AUTOWAKE': [
        r'\b(?:auto_wake_chatgpt|spawn_chatgpt_session|resume_chatgpt_browser|force_stop_chatgpt)\s*\(',
        r'\b(?:chatgpt\.com/.*(?:start_session|wake_session))\b',
    ],
    'NEG-SELF-APPROVAL': [
        r'\b(?:worker_claim_is_owner|trust_relay_owner_approval|self_approve_owner_effect)\s*\(',
    ],
}
EXTS = {'.py', '.cs', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.sh', '.ps1'}


def scan(root: Path) -> list[dict]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    out = []
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        rel = p.relative_to(root)
        if rel.parts[0] in ('qa', 'tests', 'audit', 'docs') or any(x in ('node_modules', '.git', '__pycache__', 'bin', 'obj') for x in rel.parts):
            continue
        try:
            contents = p.read_text(encoding='utf-8')
        except UnicodeError:
            continue
        for lineno, line in enumerate(contents.splitlines(), 1):
            for requirement, regexes in PATTERNS.items():
                for expr in regexes:
                    if re.search(expr, line, re.IGNORECASE):
                        out.append({'requirement': requirement, 'path': rel.as_posix(),
                                    'line': lineno, 'pattern': expr})
    return out
