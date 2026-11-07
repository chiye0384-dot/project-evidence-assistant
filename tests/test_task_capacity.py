"""Bounded retained history; synthetic stores and Git fixtures, no paid calls."""
import json

import pytest
from fastapi.testclient import TestClient

from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter, OUTLINE_GOAL, SECTION_GOAL
from project_evidence.task_storage import TaskStore, MAX_TASK_PAYLOAD_BYTES
import project_evidence.task_storage as task_storage
from test_task_storage import fixture_store
from test_learning_api import parent, steps
from test_m0_api import repository, git_exe, register
from test_agent_api import terminal


def test_101st_task_keeps_all_original_payloads_with_stable_pages_and_reopen(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    for _ in range(100):
        task = tasks.create(record, 'scripted', {})
        tasks.update(record.id, task['id'], status='failed', message='original failure preserved')
    with projects._connection() as db:
        before = dict(db.execute('SELECT id,payload_json FROM agent_tasks'))
    assert tasks.capacity(record.id)['remaining_records'] == 900
    new = tasks.create(record, 'scripted', {})
    tasks.update(record.id, new['id'], status='gap')
    with projects._connection() as db:
        after = dict(db.execute('SELECT id,payload_json FROM agent_tasks'))
        expected = [row[0] for row in db.execute('SELECT id FROM agent_tasks ORDER BY created_at DESC,id')]
    assert all(after[key] == value for key, value in before.items())
    first, second = tasks.list(record.id), tasks.list(record.id, offset=100)
    assert len(first) == 100 and len(second) == 1
    assert [x['id'] for x in first+second] == expected
    assert {x['id'] for x in first}.isdisjoint(x['id'] for x in second)
    assert tasks.list(record.id, offset=200) == []
    reopened = TaskStore(projects)
    assert reopened.capacity(record.id)['total_records'] == 101
    assert reopened.list(record.id, offset=100) == second
    assert all(reopened.get(record.id, key) == json.loads(value) for key, value in before.items())


@pytest.mark.parametrize('limit,offset', [(0,0),(101,0),(True,0),(1,-1),(1,1000),(1,True)])
def test_direct_page_validation_does_not_open_other_locations(tmp_path, limit, offset):
    _, record, tasks = fixture_store(tmp_path)
    with pytest.raises(ValueError, match='task_page_invalid'):
        tasks.list(record.id, limit=limit, offset=offset)


def test_record_bytes_are_utf8_bounded_and_failed_write_is_atomic(tmp_path):
    _, record, tasks = fixture_store(tmp_path)
    task = tasks.create(record, 'scripted', {})
    saved = tasks.get(record.id, task['id'])
    # Character count is below the byte cap; UTF-8 bytes exceed it.
    large = '界' * (MAX_TASK_PAYLOAD_BYTES // 3)
    assert len(large) < MAX_TASK_PAYLOAD_BYTES
    with pytest.raises(ValueError, match='task_payload_limit'):
        tasks.update(record.id, task['id'], status='completed', result={'overview': large})
    assert tasks.get(record.id, task['id']) == saved
    assert tasks.update(record.id, task['id'], status='failed', message='size rejection')
    assert tasks.get(record.id, task['id'])['result'] is None


def test_initial_record_size_rejection_creates_nothing(tmp_path, monkeypatch):
    _, record, tasks = fixture_store(tmp_path)
    monkeypatch.setattr(task_storage, 'MAX_TASK_PAYLOAD_BYTES', 100)
    with pytest.raises(ValueError, match='task_payload_limit'):
        tasks.create(record, 'scripted', {})
    assert tasks.list(record.id) == [] and tasks.capacity(record.id)['total_records'] == 0


def test_late_counter_write_cannot_bypass_size_bound_or_resurrect(tmp_path):
    _, record, tasks = fixture_store(tmp_path)
    task = tasks.create(record, 'scripted', {})
    tasks.update(record.id, task['id'], status='cancelled')
    saved = tasks.get(record.id, task['id'])
    with pytest.raises(ValueError, match='task_payload_limit'):
        tasks.record_counters(record.id, task['id'], {'model_calls': 2},
            [{'commit': record.commit, 'text': 'x' * MAX_TASK_PAYLOAD_BYTES}])
    assert tasks.get(record.id, task['id']) == saved
    tasks.record_counters(record.id, task['id'], {'model_calls': 2})
    result = tasks.get(record.id, task['id'])
    assert result['status'] == 'cancelled' and result['counters']['model_calls'] == 2


def test_paged_api_keeps_parent_learning_and_budget_after_101st_record(tmp_path, repository, git_exe):
    root, commit, _, _, transport = repository
    data = tmp_path/'agent'
    adapter = ScriptedAdapter(steps(OUTLINE_GOAL)+steps(SECTION_GOAL))
    with TestClient(create_app(data, github_transport=transport, git_executable=git_exe,
                              model_adapter=adapter), base_url='http://localhost:8123') as client:
        route = register(client, root, commit)
        did = parent(client, route)
        assert client.post(route+'/source/verify').json()['status'] == 'verified'
        task = client.post(route+'/learning/outline', json={'directory_task_id': did, 'chapter_index': 0}).json()
        assert terminal(client, route, task['id'])['status'] == 'completed'
        chapter = client.get(route+'/learning').json()[0]
        service = client.app.state.task_service
        record = client.app.state.project_store.get(route.rsplit('/',1)[-1])
        for _ in range(98):
            filler = service.history.create(record, 'scripted', {})
            service.history.update(record.id, filler['id'], status='failed')
        calls = adapter.calls
        assert client.get(route+'/tasks/capacity').json()['total_records'] == 100
        assert client.get(route+'/tasks/capacity').json()['can_create_task'] is True
        first = client.get(route+'/tasks').json()
        old = client.get(route+'/tasks/'+did).json()
        # The 101st is a real task-engine run against a scripted adapter only.
        body_task = client.post(route+'/learning/section', json={'outline_id': chapter['id'], 'section_id': 'entry'}).json()
        assert terminal(client, route, body_task['id'])['status'] == 'completed'
        assert client.get(route+'/tasks/capacity').json()['total_records'] == 101
        assert len(client.get(route+'/tasks?offset=100').json()) == 1
        assert client.get(route+'/tasks/'+did).json() == old
        assert client.get(route+'/learning/'+chapter['id']).json() == chapter
        assert len(client.get(route+'/learning').json()) == 2
        assert adapter.calls == calls+2
        assert client.get(route+'/tasks?limit=101').status_code == 422
        assert client.get(route+'/tasks?offset=1000').status_code == 422
        other = register(client, root, 'a'*40)
        assert client.get(other+'/tasks?offset=100').json() == []
        assert client.get(other+'/tasks/capacity').json()['total_records'] == 0
        assert client.get(other+'/tasks/'+did).status_code == 404
        assert client.get(route+'/tasks?limit=1').json()[0]['id'] == body_task['id']
        assert len(first) == 100
    with TestClient(create_app(data), base_url='http://localhost:8123') as reopened:
        assert reopened.get(route+'/tasks/capacity').json()['total_records'] == 101
        assert len(reopened.get(route+'/learning').json()) == 2
        assert reopened.get(route+'/tasks/'+did).json()['result'] == old['result']
