"""Live selfie: a short head-turn challenge in the app's own camera, so an old photo, a printout or a
replayed video doesn't pass.

The engine picks the order of the steps (look straight, then turn left and right in a random order)
and gives the app two minutes to send one frame per step. Each frame must show exactly one face, of
the same person as the straight frame (SFace, model 9). The straight frame must pass passive
liveness (MiniFASNet, model 10); the others are scored too and recorded. The head turn is read from
YuNet's landmarks: how far the nose tip sits from the midpoint of the eyes, as a share of the eye
distance. Each turn must move it clearly away from the straight frame, the two turns in opposite
directions.

Which way is "left" depends on whether the phone mirrors its front-camera photos, and phones differ.
So a turn sequence that comes out exactly reversed is accepted too, and recorded as "mirrored".
"""
from __future__ import annotations

import math
import secrets
import threading
import time

CHALLENGE_SECONDS = 120
TURN_AT = 0.10            # nose offset change from the straight frame, as a share of the eye distance
SAME_PERSON_AT = 0.30     # SFace cosine between the straight frame and each turn (turns lower it a little)
STEPS = ("left", "right")

_lock = threading.Lock()
_challenges: dict[str, dict] = {}


class LiveSelfieError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def new_challenge(application_id: str) -> dict:
    """A fresh challenge for this application (the previous one, if any, stops working)."""
    steps = ["center", *secrets.SystemRandom().sample(STEPS, len(STEPS))]
    cid = secrets.token_urlsafe(18)
    with _lock:
        cutoff = time.time() - CHALLENGE_SECONDS
        for k in [k for k, c in _challenges.items() if c["at"] < cutoff or c["application_id"] == application_id]:
            del _challenges[k]
        _challenges[cid] = {"application_id": application_id, "steps": steps, "at": time.time()}
    return {"challengeId": cid, "steps": steps, "expiresIn": CHALLENGE_SECONDS}


def take(challenge_id: str, application_id: str) -> list[str]:
    """Use up a challenge: each works once, for its own application, within the time limit."""
    with _lock:
        c = _challenges.pop(challenge_id or "", None)
    if c is None or c["application_id"] != application_id:
        raise LiveSelfieError("challenge_unknown")
    if c["at"] < time.time() - CHALLENGE_SECONDS:
        raise LiveSelfieError("challenge_expired")
    return c["steps"]


def yaw(row) -> float:
    """Nose tip against the eye midpoint, as a share of the eye distance. YuNet rows hold
    x, y, w, h, then the right eye, left eye, nose tip and mouth corners (x, y each), then a score.
    Positive: the nose is right of the eyes in the picture (an unmirrored photo of someone turning
    to their own left)."""
    rx, ry, lx, ly, nx = (float(v) for v in row[4:9])
    return (nx - (rx + lx) / 2) / max(math.hypot(lx - rx, ly - ry), 1.0)


_SIGN = {"left": 1, "right": -1}


def check(frames: list[bytes], steps: list[str], face_model=None, live_model=None) -> dict:
    """Score the frames of one challenge. Returns the selfie record (with the straight frame's
    fingerprint under "fingerprint", for the caller to keep apart from the record)."""
    from models.face_match import face
    from models.liveness import liveness
    fm = face_model or face.model()
    lm = live_model or liveness.model()
    if len(frames) != len(steps):
        raise LiveSelfieError("bad_frames")
    per, rows, prints = [], [], []
    for data, step in zip(frames, steps):
        try:
            img = face.decode(data)
        except ValueError as e:
            raise LiveSelfieError("not_an_image") from e
        found = fm.faces(img)
        f = {"step": step, "faces": len(found), "yaw": None, "live": None, "live_score": None, "same_person": None}
        if found:
            r = found[0]
            box = [int(v) for v in r[:4]]
            lv = lm.check(img, box)
            f.update(yaw=round(yaw(r), 3), live=lv["live"], live_score=lv["score"],
                     too_small=bool(min(r[2], r[3]) < face.MIN_FACE_PX))
            rows.append(r)
            prints.append(fm.fingerprint(img, r))
            if step == "center":
                center_live = lv
        else:
            rows.append(None)
            prints.append(None)
        per.append(f)

    center = steps.index("center")
    problems = []
    if any(f["faces"] == 0 for f in per):
        problems.append("no face in a frame")
    if any(f["faces"] > 1 for f in per):
        problems.append("more than one face in a frame")
    if prints[center] is not None:
        for i, f in enumerate(per):
            if i != center and prints[i] is not None:
                f["same_person"] = round(fm.similarity(prints[center], prints[i]), 3)
        if any(f["same_person"] is not None and f["same_person"] < SAME_PERSON_AT for f in per):
            problems.append("a different face appeared during the check")

    turned, direction = False, None
    if not problems:
        y0 = per[center]["yaw"]
        moves = {f["step"]: f["yaw"] - y0 for i, f in enumerate(per) if i != center}
        big = all(abs(d) >= TURN_AT for d in moves.values())
        opposite = len(moves) == 2 and (moves["left"] > 0) != (moves["right"] > 0)
        if big and opposite:
            turned = True
            direction = "as asked" if moves["left"] > 0 else "mirrored"
        else:
            problems.append("the head didn't turn as asked" if not big else "both turns went the same way")
        for f in per:
            if f["step"] in moves:
                f["move"] = round(moves[f["step"]], 3)

    face_found = per[center]["faces"] > 0
    passive = per[center]["live"] is True
    live = face_found and passive and turned and not problems
    return {
        "method": "live", "steps": steps, "frames": per,
        "face_found": face_found, "faces": per[center]["faces"],
        "face_score": float(rows[center][-1]) if rows[center] is not None else None,
        "too_small": per[center].get("too_small", False),
        "live": live, "liveness": center_live if face_found else None,
        "head_turn": {"passed": turned, "direction": direction, "threshold": TURN_AT},
        "problems": problems,
        "fingerprint": prints[center],
    }
