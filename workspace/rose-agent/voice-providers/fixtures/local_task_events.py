"""Task events from the real PR318 producer (prototypes/voice-phone-demo/rose: TaskManager._new/_emit/_finish,
Task.to_dict, Bus.emit), as LocalRoseLink receives them on /ws/events. Only the Mac's model and image libraries are
stubbed (not needed to build a task event). Prints one JSON array of bus messages. Offline; no Mac or phone."""
import itertools, json, sys, threading, types

class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub(name)
    def __call__(self, *a, **k):
        return _Stub("call")
    def __mro_entries__(self, bases):
        return (object,)

for name in ("mlx", "mlx.core", "mlx.nn", "mlx_lm", "mlx_vlm", "numpy", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont"):
    sys.modules.setdefault(name, _Stub(name))
sys.path.insert(0, sys.argv[1])  # prototypes/voice-phone-demo
from rose.bus import Bus  # noqa: E402
from rose.tasks import TaskManager  # noqa: E402

class _Phone:
    def close_session(self, name):
        pass

def manager(bus):
    m = TaskManager.__new__(TaskManager)  # the real emitters, without loading models
    m.bus, m.phone, m.on_done, m.tasks = bus, _Phone(), (lambda task: None), {}
    m._ids, m._lock = itertools.count(1), threading.Lock()
    return m

events, bus = [], Bus()
bus.subscribe(events.append)
m = manager(bus)
# A background phone task: started, one step (screen text and model reply, which must never reach the phone UI),
# a detail update, then a draft ready to send and finished; its finish is emitted once, a repeat finish is refused.
a = m._new("phone", "Reply to Sam that I'm running late", confirmed_send=False)
a.background = True
a.steps.append({"n": 1, "what": "opened Messages", "screen": "Messages: Sam Lee", "text": "secret model reply"})
a.detail = "typing the reply"
m._emit(a)
a.ready_to_send = {"text": "Running late, sorry!", "where": "Messages: Sam Lee"}
m._finish(a, "done", "Typed the reply; waiting for you to say send.")
m._finish(a, "failed", "late duplicate finish")  # the producer's own guard: no second terminal event
# A think task that is cancelled while running.
b = m._new("think", "How far is the moon?")
b.detail = "thinking (200 tokens)"
m._emit(b)
m._finish(b, "cancelled", "Cancelled.")
# A task on the user's own screen that fails.
c = m._new("phone", "Turn on Do Not Disturb", on_screen=True)
m._finish(c, "failed", "Something went wrong with that task.")
# The Mac restarts: ids start again at 1, a new task with the same id.
m2 = manager(bus)
d = m2._new("phone", "Set an alarm for 6:30")
print(json.dumps(events))
