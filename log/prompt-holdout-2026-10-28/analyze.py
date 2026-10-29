"""Check completed evidence and report delivery costs without mixing failed and successful work."""

import json
import statistics
from collections import Counter
from pathlib import Path

from agentnexus.evaluation.prompt_benchmark import digest, write_json


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    raw = [json.loads(path.read_text(encoding='utf-8')) for path in (root / 'attempts').glob('*.json')]
    planned = {(case['id'], arm) for case in manifest['cases'] for arm in ('old', 'new')}
    indexed = {(row['case_id'], row['arm']): row for row in raw}
    if len(indexed) != len(raw) or indexed.keys() != planned or any(row['api_error'] for row in raw):
        raise ValueError('Complete all 96 valid outcomes before analyzing')
    audit = json.loads((root / 'delivery-audit.json').read_text(encoding='utf-8'))
    judgments = {(row['case_id'], row['arm']): row for row in audit['judgments']}
    if judgments.keys() != planned or len(judgments) != 96:
        raise ValueError('All 96 answers must be reviewed exactly once')
    rows = []
    for key, row in indexed.items():
        judgment = judgments[key]
        if digest(row['answer']) != judgment['answer_sha256']:
            raise ValueError('Answer review fingerprint changed')
        rows.append({**row, 'automatic_passed': row['passed'],
                     'passed': row['passed'] and judgment['delivery_passed'], 'delivery_review': judgment})
    index = {(row['case_id'], row['arm']): row for row in rows}
    common = [case['id'] for case in manifest['cases'] if all(index[(case['id'], arm)]['passed'] for arm in ('old', 'new'))]
    errors = [json.loads(path.read_text(encoding='utf-8')) for path in (root / 'errors').glob('*.json')]
    starts = sorted(request['started_at'] for row in raw + errors for request in row['requests'])
    prompt_delivery = {}
    for arm in ('old', 'new'):
        calls = [request for row in raw if row['arm'] == arm for request in row['requests']]
        expected = manifest['prompts'][arm]['react.txt'].rstrip()
        prompt_delivery[arm] = {
            'calls': len(calls),
            'expected_system_prefix': sum(request['messages'][0]['content'].startswith(expected) for request in calls),
            'outgoing_models': sorted({request['model'] for request in calls}),
            'temperatures': sorted({request['temperature'] for request in calls}),
            'thinking_arguments': sorted({str(request['thinking']) for request in calls}),
            'native_tool_schema_calls': sum(bool(request['tools']) for request in calls),
        }
    first = Counter(min((index[(case['id'], arm)] for arm in ('old', 'new')),
                        key=lambda row: row['requests'][0]['started_at'])['arm'] for case in manifest['cases'])
    first_including_errors = Counter(min((row for row in raw + errors if row['case_id'] == case['id']),
                                        key=lambda row: row['requests'][0]['started_at'])['arm']
                                     for case in manifest['cases'])
    paired = []
    for case in manifest['cases']:
        old, new = (index[(case['id'], arm)] for arm in ('old', 'new'))
        if old['passed'] != new['passed']:
            paired.append({'case_id': case['id'], 'improved': new['passed'], 'old_answer': old['answer'],
                           'new_answer': new['answer'], 'old_agent_error': old.get('agent_error'),
                           'new_agent_error': new.get('agent_error')})
    costs = {}
    for label, group in [('all_outcomes', rows), ('common_success', [row for row in rows if row['case_id'] in common])]:
        costs[label] = {'paired_tasks': len(group) // 2}
        for metric in ('input_tokens', 'output_tokens', 'llm_calls'):
            costs[label][metric] = {arm: statistics.mean(row[metric] for row in group if row['arm'] == arm)
                                   for arm in ('old', 'new')}
    behavior = {}
    for arm in ('old', 'new'):
        group = [row for row in rows if row['arm'] == arm]
        repairs = [row for row in group if row.get('events')]
        reproduced = 0
        for row in repairs:
            events = row['events']
            first_edit = next((i for i, event in enumerate(events) if event['event'] == 'edit'), len(events))
            reproduced += any(event['event'] == 'check' and not event.get('passed') and not event.get('unavailable')
                              for event in events[:first_edit])
        behavior[arm] = {
            'automatic_passed': sum(row['automatic_passed'] for row in group),
            'reviewed_passed': sum(row['passed'] for row in group),
            'code_artifacts_passed': sum(row['artifact_passed'] is True for row in group),
            'reproduced_before_first_edit': reproduced,
            'extra_unavailable_checks': sum(max(0, sum(e.get('unavailable', False) for e in row.get('events', [])) - 1)
                                            for row in group),
            'runtime_errors': sum(bool(row.get('agent_error')) for row in group),
            'budget_exhausted': sum(row['answer'].startswith('（已达到最大步数') for row in group),
            'logical_calls_including_infrastructure': sum(row['llm_calls'] for row in group + [e for e in errors if e['arm'] == arm]),
        }
    result = {'costs': costs, 'behavior': behavior, 'discordant_pairs': paired,
              'prompt_delivery': prompt_delivery,
              'infrastructure_errors': len(errors), 'retained_attempt_first_request_order': dict(first),
              'all_attempts_first_request_order': dict(first_including_errors),
              'minimum_logical_invocation_gap_s': min(b - a for a, b in zip(starts, starts[1:])),
              'measurement_notes': 'Provider-reported usage may fall back to estimates in AgentLLM. '
                                   'A logical call may contain SDK-internal HTTP retries; HTTP attempts are not individually counted. '
                                   'Common-success comparisons are conditional, not a currency bill.'}
    write_json(root / 'diagnostics.json', result)
    print(json.dumps({key: value for key, value in result.items() if key != 'discordant_pairs'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
