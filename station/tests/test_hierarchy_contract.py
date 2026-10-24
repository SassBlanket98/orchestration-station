"""Additive session hierarchy metadata; all harness files are temporary fixtures."""
import json
import os
import unittest
from unittest import mock

import test_session_data

import collect
import webdata
from model import GlobalSnapshot, Stage

NOW = test_session_data.NOW


class HierarchyCollection(unittest.TestCase):
    setUp = test_session_data.SessionData.setUp

    def test_sidecar_app_tmux_and_launch_environment(self) -> None:
        config = os.path.join(self.tmp.name, 'claude-fixture')
        os.makedirs(os.path.join(config, 'sessions'))
        sidecar = os.path.join(config, 'sessions', '42.json')
        lead = collect._co_Lead(42, 'claude', 'live', os.path.dirname(self.stage), NOW - 10)
        for bridge, locator, expected in (('bridge-1', 'tui-heading-depth:@3.%3', 'tui-heading-depth'),
                                          ('', 'plain-name', 'plain-name'), ('', '', '')):
            with self.subTest(bridge=bridge, locator=locator):
                with open(sidecar, 'w', encoding='utf-8') as fh:
                    json.dump({'sessionId': 'child', 'cwd': lead.cwd,
                               **({'bridgeSessionId': bridge} if bridge else {}), 'tmux': locator}, fh)
                env = {'CLAUDE_CONFIG_DIR': config, 'STATUS_PARENT_SESSION': 'launcher',
                       'STATUS_PARENT_HARNESS': 'claude', 'STATUS_ROOT_SESSION': 'root', 'STATUS_DEPTH': '2'}
                with mock.patch.object(collect, '_co_environ', return_value=env), \
                        mock.patch.object(collect, '_co_argv', return_value=[]):
                    meta = collect._co_lead_session(self.stage, lead)
                self.assertEqual((meta.get('app'), meta.get('app_id'), meta.get('tmux')),
                                 (bool(bridge), bridge, expected))
                self.assertEqual((meta.get('parent'), meta.get('root_session'), meta.get('launch_depth')),
                                 ('launcher', 'root', 2))

    def test_missing_and_invalid_launch_environment(self) -> None:
        lead = collect._co_Lead(42, 'cursor', 'live', os.path.dirname(self.stage), NOW - 10)
        for depth in ('', 'bad', '-1'):
            with self.subTest(depth=depth), mock.patch.object(collect, '_co_environ', return_value={'STATUS_DEPTH': depth}):
                meta = collect._co_lead_session(self.stage, lead)
            self.assertEqual((meta.get('parent'), meta.get('root_session'), meta.get('launch_depth')), ('', '', 0))


class HierarchyPayload(unittest.TestCase):
    def test_defaults_and_project_root(self) -> None:
        for path, directory in (('/p/.stage#123', ''), ('/virtual#sid', '/p/.stage/')):
            with self.subTest(path=path):
                stage = Stage(path=path, dir=directory, label='p')
                payload = webdata.global_payload(GlobalSnapshot(stages=[stage]))
                self.assertEqual(payload['v'], 2)
                row = payload['stages'][0]
                self.assertEqual({key: row.get(key) for key in (
                    'parent_session', 'root_session', 'launch_depth', 'app', 'tmux', 'project')},
                    {'parent_session': '', 'root_session': '', 'launch_depth': 0,
                     'app': {'on': False, 'id': ''}, 'tmux': '', 'project': '/p'})

    def test_populated_payload(self) -> None:
        stage = Stage(path='/p/.stage', label='p')
        stage.parent = 'launcher'
        stage.root_session = 'root'
        stage.launch_depth = 2
        stage.app = True
        stage.app_id = 'bridge'
        stage.tmux = 'heading'
        row = webdata._stage_element(stage)
        self.assertEqual((row.get('parent_session'), row.get('root_session'), row.get('launch_depth')),
                         ('launcher', 'root', 2))
        self.assertEqual((row.get('app'), row.get('tmux')), ({'on': True, 'id': 'bridge'}, 'heading'))
