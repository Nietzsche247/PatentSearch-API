"""Distinct technical-sounding text for the registry tests (the same helper as tests/registry/conftest.py in the
patentref repo): two calls give unrelated shingle sketches, so publishing many records does not trip the near-duplicate
rule of checklist 10.10 unless a test means to."""
import random
import uuid

VOCAB = ("valve pump heater fuse sensor filter skimmer cover reel motor shaft seal gasket bracket spring lever latch "
         "housing inlet outlet bypass chamber membrane coil magnet relay switch timer probe flange nozzle impeller rotor "
         "stator bearing bushing clamp hinge damper baffle manifold header tube pipe elbow coupling fitting thread "
         "cap plug screen mesh cartridge resin media sand glass ceramic polymer steel copper brass aluminum titanium "
         "rubber silicone foam wire cable board circuit controller display button knob dial gauge meter scale load "
         "pressure flow temperature level speed torque voltage current signal pulse wave phase cycle stage mode state "
         "opens closes turns slides locks holds lifts drains fills heats cools mixes seals guides presses limits").split()


def words(n=120, seed=None):
    rng = random.Random(seed if seed is not None else uuid.uuid4().int)
    out = []
    for i in range(n):
        out.append(rng.choice(VOCAB))
        if i % 12 == 11:
            out[-1] += "."
    return " ".join(out).capitalize()
