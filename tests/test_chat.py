import json
import threading

import pytest
from fastapi.testclient import TestClient

from quantpilot import answers, chat


def test_agent_arguments_allow_only_quantpilot_tools():
    args = chat.agent_args('claude.exe', 'mcp.json', 'system.txt', 'settings.json')
    assert args[args.index('--tools')+1] == ''
    allowed = args[args.index('--allowedTools')+1].split(',')
    assert allowed and all(t.startswith('mcp__quantpilot__') for t in allowed)
    assert '--strict-mcp-config' in args and '--no-session-persistence' in args
    assert args[args.index('--max-turns')+1] == '8'


def test_stream_output_yields_text_and_tools_used():
    lines = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name='mcp__quantpilot__ask_outlook')])),
             dict(type='assistant', message=dict(content=[dict(type='text', text='About a coin flip.')])),
             dict(type='result', is_error=False, result='About a coin flip.')]
    parsed = chat.parse_stream('\n'.join(json.dumps(l) for l in lines))
    assert parsed['text'] == 'About a coin flip.' and parsed['tools'] == ['ask_outlook'] and not parsed['is_error']


def test_send_stores_the_conversation_and_links_answers_from_the_run(account):
    def runner(transcript, thread_id, run_id):
        answer_id = answers.record(account, dict(question=dict(type='direction', horizon_hours=4),
                                                  asked_at='2026-10-20T00:00:00+00:00', headline={}, range=None),
                                   'chat', thread_id=thread_id, run_id=run_id)
        assert 'up or down next 4h' in transcript
        return dict(text='Roughly 51%, no edge.', tools=['ask_outlook'], status='ok')
    result = chat.send(account, 'up or down next 4h', runner=runner)
    thread = chat.thread(account, result['thread_id'])
    assert [m['role'] for m in thread['messages']] == ['user', 'assistant']
    assert thread['messages'][1]['tools'] == ['ask_outlook'] and len(thread['messages'][1]['answer_ids']) == 1
    follow = chat.send(account, 'and 24h?', thread_id=result['thread_id'],
                       runner=lambda t, *_: dict(text='ok', tools=[], status='ok') if 'up or down next 4h' in t else None)
    assert follow['reply']['text'] == 'ok'   # earlier turns are in the transcript


def test_long_messages_and_parallel_runs_are_rejected(account):
    with pytest.raises(ValueError):
        chat.send(account, 'x'*2001, runner=lambda *a: dict(text='', tools=[], status='ok'))
    gate = threading.Event()
    def slow(*a):
        gate.wait(5)
        return dict(text='done', tools=[], status='ok')
    worker = threading.Thread(target=chat.send, args=(account, 'first'), kwargs=dict(runner=slow))
    worker.start()
    try:
        with pytest.raises(chat.Busy):
            chat.send(account, 'second', runner=slow)
    finally:
        gate.set()
        worker.join()


def test_turn_limit_keeps_partial_text_and_marks_it_incomplete():
    lines = [dict(type='assistant', message=dict(content=[dict(type='text', text='Funding is high.')])),
             dict(type='result', is_error=True, subtype='error_max_turns', result='')]
    parsed = chat.parse_stream('\n'.join(json.dumps(l) for l in lines))
    assert parsed['subtype'] == 'error_max_turns' and parsed['text'] == 'Funding is high.'


def test_incomplete_runs_store_the_partial_answer_with_a_note(account):
    result = chat.send(account, 'why is btc moving', runner=lambda *a: dict(text='Funding is high.', tools=['get_indicators'],
                                                                            status='incomplete'))
    assert result['reply']['text'].startswith('Funding is high.') and 'incomplete' in result['reply']['text']


def test_ai_unavailable_reply_explains_why(account):
    result = chat.send(account, 'up next 4h', runner=lambda *a: dict(text='', tools=[], status='usage_limited'))
    assert 'usage limit' in result['reply']['text'].lower()


def test_chat_routes_are_local_only(account, monkeypatch):
    from quantpilot.api import app
    monkeypatch.setattr(chat, 'run_agent', lambda *a: dict(text='hello', tools=[], status='ok'))
    with TestClient(app, client=('203.0.113.5', 5000)) as remote:
        assert remote.post('/api/v1/chat', json=dict(message='hi')).status_code == 403
    with TestClient(app, client=('127.0.0.1', 5000)) as local:
        reply = local.post('/api/v1/chat', json=dict(message='hi')).json()['data']
        assert reply['reply']['text'] == 'hello'
        assert local.get('/api/v1/chat/threads').json()['data'][0]['id'] == reply['thread_id']
