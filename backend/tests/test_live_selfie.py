"""The live selfie: the engine's head-turn challenge and the checks on the app's camera frames."""
import io
import time

import numpy as np
import pytest

from backend import live_selfie
from backend.tests.conftest import ALL_CONSENTS, Applicant, selfie_image


def frame() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 320), (180, 150, 130)).save(buf, "JPEG")
    return buf.getvalue()


def row(turn: float, x: float = 100) -> np.ndarray:
    """A YuNet row with the eyes 60 px apart and the nose `turn` eye-distances right of their midpoint."""
    return np.array([x, 80, 120, 140, x + 30, 120, x + 90, 120, x + 60 + turn * 60, 150,
                     x + 35, 175, x + 85, 175, 0.95], dtype=np.float32)


class FakeFaces:
    """Stands in for model 9: hands out one set of faces per frame, in order."""

    def __init__(self, per_frame, similarity=0.9):
        self.per_frame, self.sim, self.i = list(per_frame), similarity, 0

    def faces(self, img):
        out = self.per_frame[self.i]
        self.i += 1
        return out

    def fingerprint(self, img, r):
        return np.ones(128, dtype=np.float32)

    def similarity(self, a, b):
        return self.sim


class FakeLive:
    def __init__(self, live=True):
        self.live = live

    def check(self, img, box):
        return {"model": "liveness", "model_version": "test", "live": self.live, "score": 0.97 if self.live else 0.1}


def run(turns: dict, steps=("center", "left", "right"), **kw):
    """turns: nose offset for each step; a list value means several faces in that frame."""
    faces = [[row(t) for t in turns[s]] if isinstance(turns[s], list) else [row(turns[s])] for s in steps]
    return live_selfie.check([frame()] * len(steps), list(steps), FakeFaces(faces, kw.get("sim", 0.9)),
                             FakeLive(kw.get("live", True)))


def test_yaw_reads_the_nose_against_the_eyes():
    assert live_selfie.yaw(row(0.0)) == pytest.approx(0.0)
    assert live_selfie.yaw(row(0.25)) == pytest.approx(0.25)
    assert live_selfie.yaw(row(-0.3, x=10)) == pytest.approx(-0.3)


def test_turning_as_asked_passes_in_either_order():
    r = run({"center": 0.02, "left": 0.3, "right": -0.25})
    assert r["live"] and r["head_turn"] == {"passed": True, "direction": "as asked", "threshold": live_selfie.TURN_AT}
    r = run({"center": 0.0, "right": -0.2, "left": 0.22}, steps=("center", "right", "left"))
    assert r["live"] and r["head_turn"]["direction"] == "as asked"
    assert r["fingerprint"] is not None and r["method"] == "live" and r["problems"] == []


def test_a_phone_that_mirrors_its_photos_still_passes():
    r = run({"center": 0.0, "left": -0.3, "right": 0.3})
    assert r["live"] and r["head_turn"]["direction"] == "mirrored"


def test_turns_are_measured_from_the_straight_frame():
    # A face that isn't quite frontal to start with: both turns still move clearly away from it.
    assert run({"center": 0.12, "left": 0.35, "right": -0.05})["live"]


@pytest.mark.parametrize("turns, why", [
    ({"center": 0.0, "left": 0.02, "right": -0.03}, "the head didn't turn as asked"),       # a still photo
    ({"center": 0.0, "left": 0.3, "right": 0.25}, "both turns went the same way"),
    ({"center": 0.0, "left": 0.3, "right": [-0.3, 0.0]}, "more than one face in a frame"),   # a photo held up beside
    ({"center": 0.0, "left": 0.3, "right": []}, "no face in a frame"),
])
def test_what_fails_the_head_turn(turns, why):
    r = run(turns)
    assert not r["live"] and why in r["problems"]


def test_a_different_face_or_a_spoof_fails():
    r = run({"center": 0.0, "left": 0.3, "right": -0.3}, sim=0.1)
    assert not r["live"] and "a different face appeared during the check" in r["problems"]
    r = run({"center": 0.0, "left": 0.3, "right": -0.3}, live=False)
    assert not r["live"] and r["head_turn"]["passed"] and r["liveness"]["live"] is False


def test_each_challenge_works_once_for_its_own_application(monkeypatch):
    c = live_selfie.new_challenge("HL-A")
    assert c["steps"][0] == "center" and sorted(c["steps"][1:]) == ["left", "right"] and c["expiresIn"] == 120
    with pytest.raises(live_selfie.LiveSelfieError, match="challenge_unknown"):
        live_selfie.take(c["challengeId"], "HL-B")                      # another application's challenge
    c = live_selfie.new_challenge("HL-A")
    assert live_selfie.take(c["challengeId"], "HL-A") == c["steps"]
    with pytest.raises(live_selfie.LiveSelfieError, match="challenge_unknown"):
        live_selfie.take(c["challengeId"], "HL-A")                      # used up
    old = live_selfie.new_challenge("HL-A")
    newer = live_selfie.new_challenge("HL-A")                           # asking again cancels the old one
    with pytest.raises(live_selfie.LiveSelfieError):
        live_selfie.take(old["challengeId"], "HL-A")
    real_time = time.time
    monkeypatch.setattr(live_selfie.time, "time", lambda: real_time() + 121)
    with pytest.raises(live_selfie.LiveSelfieError, match="challenge_expired"):
        live_selfie.take(newer["challengeId"], "HL-A")
    orders = {tuple(live_selfie.new_challenge(f"HL-{i}")["steps"]) for i in range(40)}
    assert len(orders) == 2                                             # the engine varies the order


def turned_yaw(monkeypatch):
    """The test photos can't turn their heads: make the two turn frames read as opposite turns."""
    values = iter([0.0, 0.3, -0.3])
    monkeypatch.setattr(live_selfie, "yaw", lambda r: next(values))


def test_live_selfie_route_with_the_real_models(client, monkeypatch):
    face = selfie_image()
    if face is None:
        pytest.skip("sample face photo not on this machine")
    a = Applicant(client, "9123400011", "dev-live-1")
    assert a.post("/selfie/challenge").json()["error"] == "profile_required"
    a.put("/consents", json=ALL_CONSENTS)
    a.post("/profile", json={"pan": "ABCPK1234K"})

    def send(challenge, n=3):
        files = [("frames", (f"f{i}.jpg", face, "image/jpeg")) for i in range(n)]
        return a.post("/selfie/live", data={"challengeId": challenge}, files=files)

    c = a.post("/selfie/challenge").json()
    still = send(c["challengeId"]).json()          # the same photo three times: no head turn
    assert still["faceFound"] and still["liveSelfie"] and not still["headTurn"] and not still["live"]
    assert send(c["challengeId"]).json()["error"] == "challenge_unknown"            # used up
    c = a.post("/selfie/challenge").json()
    assert send(c["challengeId"], n=2).json()["error"] == "bad_frames"
    turned_yaw(monkeypatch)
    c = a.post("/selfie/challenge").json()
    ok = send(c["challengeId"]).json()
    assert ok["live"] and ok["headTurn"] and ok["livenessScore"] > 0.5 and ok["problems"] == []
    from backend.app import store
    rec = store.apps[a.id].selfie
    assert rec["method"] == "live" and len(rec["frames"]) == 3 and "fingerprint" not in rec
