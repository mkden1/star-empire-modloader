"""The control connection's zlib3 handshake and the 'solar' route rules (tests we added to the dev's test_arena_connection.py; the dev's own version of the handshake test is listed in expected_failures.txt)."""
import unittest
from unittest.mock import Mock
from arena_client import ArenaClientMixin
from arena_connection import NetworkThread, check_route, worker_owns
from input_replication import ARRIVAL_INPUT_CAP
from types import SimpleNamespace
import json
import queue

import test_arena_connection as dev


class ControlConnectionTests(unittest.TestCase):
    """The control connection announces our zlib3 wire format; workers keep the official token."""

    setUp = dev.ConnectionTests.setUp

    def test_only_the_game_control_connection_announces_arrival_states(self):
        control = NetworkThread('127.0.0.1', 1, queue.Queue(), queue.Queue(), caps=(ARRIVAL_INPUT_CAP,))
        arena = NetworkThread('127.0.0.1', 1, queue.Queue(), queue.Queue(), role='arena')
        for thread in (control, arena):
            for wake in (thread._wake_r, thread._wake_w):
                self.addCleanup(wake.close)
        # MOD: the control connection announces our 'zlib3' wire format (vanilla: 'zlib2');
        # workers keep the official token.
        self.assertEqual(control.caps_line(), 'CAPS zlib3 arrival1')
        self.assertEqual(arena.caps_line(), 'CAPS zlib2')


class SolarRouteTests(unittest.TestCase):
    """A 'solar' route: the dungeon-shaped content hash is accepted, a non-loopback host is allowed, a malformed hash is not."""

    setUp = dev.ConnectionTests.setUp
    event = dev.ConnectionTests.event

    def test_solar_profile_accepts_a_dungeon_shaped_content_hash(self):
        # Regression: a live 'solar' route from this deployment's own
        # server was rejected outright (see arena_client's ARENA_ROUTE
        # comment) because stage() only ever special-cased 'dungeon' --
        # everything else had to carry the literal 'flight-range-v1'
        # content string. 'solar' ships the same 64-char hash shape as
        # dungeon instead.
        solar_route = dict(self.route, profile='solar',
                           content='c5ce8641c68534d789c96233305d41c7aa8a4f727758cc057e7eb45d6f9d810a')
        self.manager.stage(solar_route)  # must not raise
        self.assertEqual(self.manager.state, 'STAGED')

    def test_solar_profile_does_not_require_a_loopback_host(self):
        # Same live route: a real, non-loopback worker IP with
        # loopback_only=True and tls=False -- the signed ticket (verified
        # by the worker itself) is the actual authorization, not this
        # client-side host check. Confirmed fine for this server by the
        # person who runs it.
        solar_route = dict(self.route, profile='solar', host='24.179.194.118',
                           port=32002, tls=False, loopback_only=True,
                           content='c5ce8641c68534d789c96233305d41c7aa8a4f727758cc057e7eb45d6f9d810a')
        self.manager.stage(solar_route)  # must not raise
        self.assertEqual(self.manager.state, 'STAGED')

    def test_solar_profile_still_rejects_a_malformed_content_hash(self):
        # The relaxation is specifically "accept the dungeon-shaped hash
        # for solar too" -- a route that's neither the literal
        # 'flight-range-v1' string nor a real 64-char hash must still be
        # rejected, for either profile.
        solar_route = dict(self.route, profile='solar', content='not-a-hash')
        with self.assertRaises(ValueError):
            self.manager.stage(solar_route)

    SOLAR_HASH = 'c5ce8641c68534d789c96233305d41c7aa8a4f727758cc057e7eb45d6f9d810a'

    def activate_solar(self, layout=None):
        """Drive a solar worker stream to ACTIVE the way the live worker does."""
        route = dict(self.route, profile='solar', host='24.179.194.118', port=32002,
                     tls=False, loopback_only=True, content=self.SOLAR_HASH)
        self.manager.stage(route)
        self.route = route
        self.manager.receive(self.event('ARENA_ACTIVE', dict(route, stream_id='s1', attachment='att-1')))
        layout = layout if layout is not None else {'system_id': 3}
        lines = self.manager.receive(self.event('ARENA_LAYOUT', {'kind': 'solar', 'data': layout}))
        self.manager.receive(self.event('ARENA_BASELINE', {'sequence': 0, 'lines': []}))
        return lines

    def test_solar_layout_is_unwrapped_so_planets_and_gates_survive(self):
        # Regression: the worker sends its layout as {'kind': 'solar', 'data': {...}}.
        # Forwarding the wrapper itself as the system layout buried 'planets' and
        # 'warp_gates' one level too deep, so the system rendered with no planets
        # and no warp gates and the player could not leave it.
        layout = {'system_id': 3, 'planets': [{'id': 1}], 'warp_gates': [{'id': 2}]}
        lines = self.activate_solar(layout)
        self.assertEqual(lines, ['SOLAR_SYSTEM_DATA ' + json.dumps(layout)])

    def test_layout_for_a_different_worker_profile_fails_the_stream(self):
        route = dict(self.route, profile='solar', content=self.SOLAR_HASH)
        self.manager.stage(route)
        self.manager.receive(self.event('ARENA_ACTIVE', dict(route, stream_id='s1', attachment='a')))
        self.assertEqual(self.manager.receive(self.event('ARENA_LAYOUT', {'kind': 'dungeon', 'data': {}})), [])
        self.assertEqual(self.manager.state, 'FAILED')

    def test_solar_commands_carry_the_stream_attachment(self):
        self.activate_solar()
        self.assertTrue(self.manager.send('TURRETS_TOGGLE'))
        sent = json.loads(self.manager.connection.sent[-1])
        self.assertEqual((sent['command'], sent['attachment']), ('TURRETS_TOGGLE', 'att-1'))

    def test_worker_owns_gameplay_verbs_but_not_chat_or_unknown_ones(self):
        route = dict(profile='solar')
        for owned in ('TURRETS_TOGGLE', 'TRACTOR_TOGGLE', 'SOLAR_WARP 12', 'SPACE_INPUT {}', 'PING 1'):
            self.assertTrue(worker_owns(route, owned), owned)
        for control in ('CHAT hello', 'WHISPER x y', 'MARKET_LIST', 'SOMETHING_NEW'):
            self.assertFalse(worker_owns(route, control), control)
        self.assertFalse(worker_owns(None, 'TURRETS_TOGGLE'))

    def test_verbs_a_worker_does_not_own_go_to_the_control_connection(self):
        # Regression: these used to be handed to the arena and silently dropped.
        self.activate_solar()
        app = SimpleNamespace(_arena_connection=self.manager, _net=Mock())
        self.assertTrue(ArenaClientMixin._arena_send(app, 'MARKET_LIST'))
        app._net.send.assert_called_once_with('MARKET_LIST')
        before = len(self.manager.connection.sent)
        self.assertTrue(ArenaClientMixin._arena_send(app, 'TURRETS_TOGGLE'))
        self.assertEqual(len(self.manager.connection.sent), before + 1)

    def test_a_solar_route_is_accepted_and_staged_through_the_app_hook(self):
        app = ArenaClientMixin()
        app._arena_connection, app._authenticated = self.manager, True
        route = dict(self.route, profile='solar', content=self.SOLAR_HASH, tls=False)
        self.assertTrue(check_route(route))
        self.assertTrue(app._arena_control_line('ARENA_ROUTE ' + json.dumps(route)))
        self.assertEqual((self.manager.state, self.manager.route['profile']), ('STAGED', 'solar'))

    def test_control_connection_keeps_zlib3_but_every_worker_socket_uses_zlib2(self):
        # MOD: the control server accepts our extended-dictionary 'zlib3' format;
        # the arena/standby workers are separate services that drop a socket that
        # opens with anything but the official 'zlib2'.
        def token(role):
            thread = NetworkThread('127.0.0.1', 1, queue.Queue(), queue.Queue(), role=role)
            try:
                return thread.COMPRESS_CAP_TOKEN
            finally:
                for end in (thread._wake_r, thread._wake_w):
                    end.close()
        self.assertEqual(token('control'), 'CAPS zlib3')
        self.assertEqual(token('arena'), 'CAPS zlib2')
        self.assertEqual(token('standby'), 'CAPS zlib2')


if __name__ == "__main__":
    unittest.main()
