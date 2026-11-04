from . import film_report


def test_readme_refresh_preserves_other_report_sections_and_replaces_once():
    original = 'Frozen hero\n<!-- RULE_STATUS_START -->running<!-- RULE_STATUS_END -->\n'
    first = film_report.replace_section(original, 'FILM_ANALYSIS', 'old result')
    second = film_report.replace_section(first, 'FILM_ANALYSIS', 'new result')
    assert original.strip() in second
    assert 'new result' in second and 'old result' not in second
    assert second.count('<!-- FILM_ANALYSIS_START -->') == 1


def test_readme_accepts_already_delimited_table_without_nested_markers():
    block = '<!-- FILM_ANALYSIS_START -->\nmeasured\n<!-- FILM_ANALYSIS_END -->'
    result = film_report.replace_section('intro', 'FILM_ANALYSIS', block)
    assert result.count('<!-- FILM_ANALYSIS_START -->') == 1


def test_observer_ignores_heartbeat_but_detects_catalogue_and_terminal_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(film_report, 'HERE', tmp_path)
    (tmp_path/'store').mkdir()
    (tmp_path/'backup-campaign').mkdir()
    catalogue = tmp_path/'store/catalogue.json'
    catalogue.write_text('{}')
    supervisor = tmp_path/'backup-campaign/supervisor.json'
    supervisor.write_text('{"status":"running","heartbeat":1}')
    initial = film_report.fingerprint()
    supervisor.write_text('{"status":"running","heartbeat":2}')
    assert film_report.fingerprint() == initial
    catalogue.write_text('{"new":"checkpoint"}')
    assert film_report.fingerprint() != initial
    before_complete = film_report.fingerprint()
    supervisor.write_text('{"status":"complete"}')
    assert film_report.fingerprint() != before_complete
