"""Acceptance checks for the prompt holdout experiment runner."""

import pytest

from agentnexus.evaluation.prompt_holdout import HoldoutWorkspace, dataset, grade_answer, schedule, summarize


def test_correct_artifact_requires_an_agent_check_after_the_last_edit(tmp_path):
    case = next(case for case in dataset() if case['id'] == 'repair-pagination')
    workspace = HoldoutWorkspace(tmp_path, case)
    assert workspace.grade()['artifact_passed'] is False
    workspace.edit_file('logic.py', 'return total // size', 'return (total + size - 1) // size')
    assert workspace.grade()['artifact_passed'] is True
    assert workspace.grade()['verified'] is False
    assert workspace.run_checks()['passed'] is True
    assert workspace.grade()['verified'] is True
    workspace.edit_file('logic.py', 'return (total + size - 1) // size', 'return total // size')
    assert workspace.grade()['artifact_passed'] is False
    assert workspace.grade()['verified'] is False


def test_fixture_tools_cannot_escape_the_disposable_workspace(tmp_path):
    workspace = HoldoutWorkspace(tmp_path, dataset()[0])
    with pytest.raises(ValueError, match='fixture'):
        workspace.read_file('../private.txt')


def test_schedule_is_paired_balanced_and_contains_48_distinct_tasks():
    cases = dataset()
    assert len(cases) == len({case['id'] for case in cases}) == 48
    planned = schedule(cases)
    assert len(planned) == 96
    for offset in range(0, 96, 2):
        assert planned[offset]['case_id'] == planned[offset + 1]['case_id']
        assert {planned[offset]['arm'], planned[offset + 1]['arm']} == {'old', 'new'}
    assert sum(row['arm'] == 'old' for row in planned[::2]) == 24


@pytest.mark.parametrize('case', [case for case in dataset() if 'files' in case], ids=lambda case: case['id'])
def test_each_repair_starts_with_an_observable_defect(tmp_path, case):
    assert HoldoutWorkspace(tmp_path, case).grade()['artifact_passed'] is False


def test_unavailable_check_is_never_a_successful_verification(tmp_path):
    case = next(case for case in dataset() if case['id'] == 'repair-blocked-last')
    workspace = HoldoutWorkspace(tmp_path, case)
    workspace.edit_file('logic.py', 'rows[0]', 'rows[-1]')
    assert workspace.run_checks()['unavailable'] is True
    assert workspace.grade()['artifact_passed'] is True
    assert workspace.grade()['verified'] is False
    assert workspace.grade()['blocked_observed'] is True


def test_fabricated_correct_value_without_source_lookup_is_a_failure():
    case = next(case for case in dataset() if case['id'] == 'evidence-current-0')
    assert grade_answer(case, {'answer': 'BUILD-4193 notice-0-latest', 'lookups': []}) is False


def test_incomplete_comparison_cannot_recommend_replacement(tmp_path):
    from agentnexus.evaluation.prompt_benchmark import write_json

    write_json(tmp_path / 'manifest.json', {'cases': [{'id': 'a'}]})
    write_json(tmp_path / 'attempts' / 'a--old.json',
               {'case_id': 'a', 'category': 'repair', 'arm': 'old', 'answer': 'done', 'api_error': '', 'passed': True})
    result = summarize(tmp_path)
    assert result['complete'] is False
    assert result['decision'] == 'incomplete'


def test_generated_repairs_cannot_access_host_files_or_import_modules(tmp_path):
    case = next(case for case in dataset() if case['id'] == 'repair-pagination')
    workspace = HoldoutWorkspace(tmp_path, case)
    workspace.edit_file('logic.py', 'return total // size', "return __import__('os').getcwd()")
    result = workspace.run_checks()
    assert result['passed'] is False
    assert 'restricted' in result['output']


def test_mentions_of_the_right_value_do_not_make_a_wrong_answer_pass():
    case = next(case for case in dataset() if case['id'] == 'evidence-current-0')
    row = {'answer': 'BUILD-4193 notice-0-latest 都不对，最终值是 BUILD-1000。',
           'lookups': [{'returned_records': True}]}
    assert grade_answer(case, row) is False


def test_query_recovery_hint_names_an_actually_available_path():
    from agentnexus.evaluation.prompt_holdout import lookup_records

    case = next(case for case in dataset() if case['id'] == 'evidence-current-0')
    error = lookup_records(case, 'service-0 build number', 'primary')
    assert error['retry'] == {'query': 'service-0', 'source': 'primary'}
    assert lookup_records(case, **error['retry'])['records'][1]['value'] == 'BUILD-4193'


def test_checker_process_has_a_memory_limit():
    from agentnexus.evaluation.prompt_holdout import limited_python

    result = limited_python('print(len(bytearray(512 * 1024 * 1024)))')
    assert result.returncode != 0
    assert 'MemoryError' in result.stderr


def test_a_failed_check_after_editing_is_not_initial_reproduction(tmp_path):
    workspace = HoldoutWorkspace(tmp_path, dataset()[0])
    workspace.edit_file('logic.py', 'return total // size', 'return 0')
    workspace.run_checks()
    workspace.edit_file('logic.py', 'return 0', 'return (total + size - 1) // size')
    workspace.run_checks()
    assert workspace.grade()['reproduced'] is False


@pytest.mark.parametrize('implementation', [
    '    return [items[i:i + size] for i in range(0, len(items), size)]\n',
    '    result = []\n    i = 0\n    while i < len(items):\n        result.append(items[i:i + size])\n        i += size\n    return result\n',
    '    if size <= 0:\n        raise ValueError("size must be positive")\n    return [items[i:i + size] for i in range(0, len(items), size)]\n',
])
def test_equivalent_valid_python_implementations_are_accepted(tmp_path, implementation):
    case = next(case for case in dataset() if case['id'] == 'repair-batch-partition')
    workspace = HoldoutWorkspace(tmp_path, case)
    (tmp_path / 'logic.py').write_text('def solve(items, size):\n' + implementation, encoding='utf-8')
    assert workspace.check_artifact()['passed'] is True


def test_f_string_repair_is_accepted(tmp_path):
    case = next(case for case in dataset() if case['id'] == 'repair-blocked-key')
    workspace = HoldoutWorkspace(tmp_path, case)
    workspace.edit_file('logic.py', 'return provider + model', 'return f"{provider.strip()}/{model.strip()}"')
    assert workspace.check_artifact()['passed'] is True


REFERENCE_EXPRESSIONS = {
    'pagination': '(total + size - 1) // size',
    'bounded-window': 'items[-limit:] if limit > 0 else []',
    'stable-dedup': 'list(dict.fromkeys(items))',
    'explicit-default': 'default if value is None else value',
    'ranking': 'sorted(rows, key=lambda row: (-row["score"], row["id"]))',
    'todo-completion': 'bool(states) and all(state == "done" for state in states)',
    'remaining-budget': 'max(0, deadline - now)',
    'stream-fragments': '"".join(chunk for chunk in chunks if chunk is not None)',
    'version-order': 'sorted(versions, key=lambda version: tuple(int(part) for part in version.split(".")))',
    'usage-average': 'sum(value for value in values if value is not None) / len([value for value in values if value is not None]) if any(value is not None for value in values) else None',
    'overlap-merge': 'list(dict.fromkeys(left + right))',
    'chunk-sizing': 'max(1, size - overlap)',
    'rate-window': '[stamp for stamp in timestamps if now - window < stamp <= now]',
    'retry-count': 'list(range(max(0, attempts)))',
    'suffix-match': 'filename.lower().endswith(suffix.lower())',
    'batch-partition': '[items[i:i + size] for i in range(0, len(items), size)]',
    'blocked-bool': 'text.strip().lower() in ("true", "1", "yes")',
    'blocked-trim': 'text[:max(0, limit)]',
    'blocked-first': 'items[0] if items else fallback',
    'blocked-key': 'f"{provider.strip()}/{model.strip()}"',
    'blocked-filter': '[row for row in rows if row.get("enabled", True)]',
    'blocked-weight': 'sum(row.get("tokens") or 0 for row in rows)',
    'blocked-stop': 'text.split(stop)[0]',
    'blocked-last': 'rows[-1] if rows else None',
}


@pytest.mark.parametrize('case', [case for case in dataset() if 'files' in case], ids=lambda case: case['id'])
def test_every_fixture_accepts_a_known_correct_implementation(tmp_path, case):
    workspace = HoldoutWorkspace(tmp_path, case)
    signature = case['files']['logic.py'].splitlines()[0]
    expression = REFERENCE_EXPRESSIONS[case['id'].removeprefix('repair-')]
    (tmp_path / 'logic.py').write_text(signature + '\n    return ' + expression + '\n', encoding='utf-8')
    assert workspace.check_artifact()['passed'] is True


def test_overlapping_workers_share_one_rate_limit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from agentnexus.evaluation.prompt_holdout import reserve_call_slot

    with ThreadPoolExecutor(max_workers=3) as workers:
        starts = sorted(workers.map(lambda _: reserve_call_slot(tmp_path, 600), range(3)))
    assert starts[1] - starts[0] >= 0.095
    assert starts[2] - starts[1] >= 0.095
