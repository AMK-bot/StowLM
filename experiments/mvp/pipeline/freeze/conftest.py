"""Stored suite workspaces are outputs, never freeze unit tests."""
collect_ignore = ['suites']
collect_ignore_glob = ['unit-tmp*', 'test-tmp*', 'pytest-*']
