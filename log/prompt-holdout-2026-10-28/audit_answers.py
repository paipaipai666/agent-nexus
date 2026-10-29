"""Prepare anonymous answer packets and bind reviewed judgments to raw attempts."""

import argparse
import json
import random
from pathlib import Path

from agentnexus.evaluation.prompt_benchmark import digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['pack', 'apply'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    schedule = manifest['schedule'].copy()
    random.Random(947172).shuffle(schedule)
    mapping = {f'answer-{i:03d}': item for i, item in enumerate(schedule)}
    cases = {case['id']: case for case in manifest['cases']}
    if args.phase == 'pack':
        write_json(root / 'audit-mapping.json', mapping)
        for offset in range(0, 96, 24):
            rows = []
            for anonymous_id, item in list(mapping.items())[offset:offset + 24]:
                path = root / 'attempts' / f"{item['case_id']}--{item['arm']}.json"
                if not path.exists():
                    continue
                attempt = json.loads(path.read_text(encoding='utf-8'))
                case = cases[item['case_id']]
                rows.append({'id': anonymous_id, 'question': case['question'], 'category': case['category'],
                             'answer': attempt['answer'], 'events': attempt.get('events', []),
                             'lookups': attempt.get('lookups', []), 'records': case.get('records', []),
                             'artifacts': attempt.get('artifacts', {}),
                             'artifact_passed': attempt['artifact_passed'],
                             'agent_error': attempt.get('agent_error'), 'answer_sha256': digest(attempt['answer'])})
            write_json(root / 'audit-packets' / f'packet-{offset // 24}.json',
                       {'rubric': manifest['delivery_rubric'], 'answers': rows})
        print('Anonymous packets updated. Do not give audit-mapping.json to the reviewer.')
        return
    judgments = []
    seen = set()
    for path in sorted((root / 'audit-reviews').glob('*.json')):
        for row in json.loads(path.read_text(encoding='utf-8'))['judgments']:
            if row['id'] in seen or row['id'] not in mapping:
                raise ValueError('Duplicate or unknown judgment')
            seen.add(row['id'])
            item = mapping[row['id']]
            attempt = json.loads((root / 'attempts' / f"{item['case_id']}--{item['arm']}.json").read_text(encoding='utf-8'))
            if row['answer_sha256'] != digest(attempt['answer']):
                raise ValueError('Answer fingerprint mismatch')
            if any(type(row[field]) is not bool for field in ('delivery_passed', 'serious_error')):
                raise ValueError('Judgments must be boolean')
            judgments.append({**item, **row, 'reviewer': 'gpt-6-luna'})
    if len(judgments) != 96:
        raise ValueError(f'Expected 96 judgments, got {len(judgments)}')
    write_json(root / 'delivery-audit.json',
               {'method': 'Assignment-hidden model-assisted review; style may reveal assignment. '
                          'Synthetic tasks, not human labels. Primary adjudication recorded separately if needed.',
                'judgments': judgments})
    print('Applied 96 answer-bound judgments without changing raw attempts.')


if __name__ == '__main__':
    main()
