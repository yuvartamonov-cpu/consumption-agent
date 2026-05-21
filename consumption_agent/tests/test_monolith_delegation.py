from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import consumption_agent_full_030526 as monolith
import gen_report
import init_db


def test_cmd_init_delegates_to_init_db(monkeypatch):
    calls = {}

    def fake_initialize_database(db_path=None, force=False):
        calls['db_path'] = db_path
        calls['force'] = force

    monkeypatch.setattr(init_db, 'initialize_database', fake_initialize_database)
    monolith.cmd_init(SimpleNamespace(db='test.db', force=True))

    assert calls == {'db_path': 'test.db', 'force': True}


def test_cmd_report_delegates_to_generate_report(monkeypatch, capsys):
    calls = {}

    def fake_generate_report(db_path=None, report_path=None):
        calls['db_path'] = db_path
        calls['report_path'] = report_path
        return '/tmp/fake-report.pdf'

    monkeypatch.setattr(gen_report, 'generate_report', fake_generate_report)
    monolith.cmd_report(SimpleNamespace(db='report.db'))

    out = capsys.readouterr().out
    assert calls['db_path'] == 'report.db'
    assert calls['report_path'].endswith('report_consumption_agent.pdf')
    assert 'PDF: /tmp/fake-report.pdf' in out
