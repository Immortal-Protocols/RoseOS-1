"""Explicit schema-4 alongside sessions, reusing Workspace's snapshot and stop guards."""
from .background import Workspace
import threading


class ScopedWorkspace(Workspace):
    def __init__(self, send, **kwargs):
        super().__init__(send, **kwargs)
        self.bound_id = None
        self.package = None
        self.uncertain = False
        self.stop_requested = threading.Event()

    def request_stop(self):
        # No lock: cancel may arrive while an action waits on the phone. Prevent
        # the next command in a multi-action tool (e.g. press_keys) immediately.
        self.stop_requested.set()

    def start(self, package):
        if self.stop_requested.is_set():
            return {'status': 'stopped_by_owner'}
        if self.package and package != self.package:
            return {'status': 'background_unavailable', 'reason': 'This session belongs to another app.'}
        if self.uncertain:
            return {'status': 'background_unavailable', 'reason': 'Start outcome unknown; inspect the phone before retrying.'}
        caps = self.send('capabilities', {})
        if caps.get('multiSession') is not True or caps.get('maxSessions', 0) < 2:
            return {'status': 'background_unavailable', 'reason': 'Phone does not support independent app sessions.'}
        self.package = package
        return super().start(package)

    def call(self, operation, params=None):
        if operation in ('start', 'command') and self.stop_requested.is_set():
            return {'status': 'stopped_by_owner'}
        params = dict(params or {})
        if operation == 'start':
            params['mode'] = 'alongside'
        elif operation in ('get_state', 'command', 'stop'):
            if not self.bound_id:
                if operation == 'stop' and self.uncertain:
                    return {'status': 'cleanup_failed', 'reason': 'Start reply was lost; workspace identity is unknown.'}
                return {'status': 'idle' if operation == 'get_state' else 'no_background_app'}
            params['sessionId'] = self.bound_id
        result = super().call(operation, params)
        if operation == 'start':
            self.bound_id = result.get('sessionId')
            if not self.bound_id:
                # A definitive refusal is terminal. A lost reply cannot be safely
                # reconciled by an unscoped get_state (that would read slot zero).
                self.uncertain = result.get('status') not in {
                    'multi_session_unavailable', 'session_limit', 'app_in_other_session',
                    'target_not_allowed', 'locked', 'user_stopped', 'rejected', 'invalid_mode'}
                return {'status': 'failed', 'error': result.get('status', 'unknown'),
                        'reason': 'Session was not bound; no other workspace was selected.'}
        return result
