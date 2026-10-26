"""Montage handling on hand-made frames: plays, image-space touches, receiver trace, scene-named shooter."""
import numpy as np

from vision.events import _at_body, detect_events, plays, trace_back
from vision.pipeline import _kit_hist, _same_view
from vision.pitch_keypoints import MIRROR, WORLD_KEYPOINTS


def _f(i, shot, ball, players):
    return {"frame": i, "period": 1, "match_seconds": round(i * 0.2, 2), "clock": "00:00", "shot": {"id": shot},
            "ball": ball, "players": players}


def _p(pid, team, x, y, box=None):
    return {"player_id": pid, "team": team, "x": x, "y": y, "name": pid, "xyxy": box}


def _goal_play(shot, side, ids, start=0):
    """Passer holds the ball, passes to a teammate 10 m on, who controls it and shoots at the goal on ``side``."""
    d = 1 if side else -1
    a, b = ids
    ball = [60, 60, 60, 63.5, 67, 70, 70, 70, 80, 90, 100, 105 if side else 0]
    ball = [60 + d * (x - 60) for x in ball[:-1]] + [side]
    frames = []
    for k, bx in enumerate(ball):
        frames.append(_f(start + k, shot, {"x": float(bx), "y": 34.0}, [
            _p(a, "home", 60.0, 34.5), _p(b, "home", 60 + d * 10.0, 34.5), _p("X" + a, "away", 30.0, 10.0)]))
    return frames


def test_a_montage_is_read_play_by_play_with_its_own_attack_direction():
    frames = _goal_play(0, 105, ("H1", "H2")) + _goal_play(1, 0, ("A1s1", "A2s1"), start=12)
    assert [len(p) for p in plays(frames)] == [12, 12]
    found = detect_events(frames, 5)
    shots = [e for e in found["events"] if e["type"] == "shot"]
    assert [(e["play"], e["attack_right"]) for e in shots] == [(0, True), (1, False)]
    assert [e["outcome"] for e in shots] == ["goal", "goal"]


def test_ball_above_a_players_head_is_not_a_touch():
    body = (100, 100, 120, 160)
    assert _at_body((105, 140, 109, 144), body)        # at the feet / thigh
    assert not _at_body((105, 60, 109, 64), body)      # a lofted ball crossing above him


def test_receiver_is_traced_back_through_a_renumbered_and_missed_frame():
    frames = [_f(0, 0, None, [_p("H9", "home", 30, 20)]),
              _f(1, 0, None, [_p("H40", "home", 60, 50)]),          # he is not detected; someone far away is
              _f(2, 0, None, [_p("H40", "home", 31.5, 21)]),        # re-numbered after a pan
              _f(3, 0, None, [_p("H40", "home", 33, 22)])]
    assert trace_back(frames, "H40", 3, 0) == "H9"
    frames[0]["players"] = [_p("H9", "home", 90, 60)]               # nobody within running distance
    assert trace_back(frames, "H40", 3, 0) is None


def test_scene_model_names_the_shooter_when_cv_picks_a_defender():
    frames = _goal_play(0, 105, ("H1", "H2"))
    for f in frames[8:]:   # a defender stands on the ball's line just before the shot
        f["players"].append(_p("A9", "away", f["ball"]["x"] + 0.3, 34.2))
    hints = {"passes": [], "goals": [], "shots": [{"t": frames[8]["match_seconds"], "actor_id": "H2",
                                                   "type": "shot", "confidence": 0.9}]}
    shot = next(e for e in detect_events(frames, 5, hints=hints)["events"] if e["type"] == "shot")
    assert shot["player_id"] == "H2"


def test_pitch_halves_mirror_and_homography_agreement():
    for i, j in enumerate(MIRROR):
        (x, y), (mx, my) = WORLD_KEYPOINTS[i], WORLD_KEYPOINTS[j]
        assert abs(x + mx - 105) < 0.5 and abs(y - my) < 0.5
    a = np.eye(3)
    assert _same_view(a, a) and not _same_view(a, np.array([[1, 0, 30], [0, 1, 0], [0, 0, 1.0]]))


def test_kit_histogram_tells_stripes_from_pale_blue():
    stripes = np.zeros((40, 20, 3))
    stripes[:, ::2] = 240
    stripes[:, 1::2] = 15
    pale_blue = np.full((40, 20, 3), (150, 190, 225), dtype=float)
    s, b = _kit_hist(stripes, (0, 0, 20, 40)), _kit_hist(pale_blue, (0, 0, 20, 40))
    assert s[0] > 100 and s[1] > 100 and s[4] == 0          # white and black, no blue
    assert b[4] > 200 and b[0] == 0                          # blue, not white


def test_dense_freeze_adds_missing_players_and_flags_a_doubtful_mapping():
    from vision.freeze import densify
    frame = {"match_seconds": 1.0, "shot": {"id": 0}, "homography": [0.1, 0, 0, 0, -0.1, 68, 0, 0, 1],
             "players": [_p("H1", "home", 10.0, 60.0, [90, 60, 110, 80])]}
    image = np.full((200, 2000, 3), (150, 190, 225), dtype=float)
    boxes = [(2, [180, 120, 220, 160], 0.9),     # the tracked player again (full-res pixels = 2x)
             (2, [800, 300, 840, 360], 0.8),     # a missed player, on the pitch
             (3, [1000, 300, 1040, 360], 0.7)]   # the referee
    out = densify(frame, image, [1000, 100], lambda img: boxes)
    added = [p for p in out["players"] if p.get("dense")]
    assert [p["role"] for p in added] == ["OUT", "REF"] and out["dense"]["matched"] == 1
    assert out["dense"]["mapping"] == "ok"
    off = densify(frame, image, [1000, 100], lambda img: [(2, [100, 1900, 140, 1990], 0.8)] * 1 +
                  [(2, [300, 1900, 340, 1990], 0.8)])   # feet far below the pitch
    assert off["dense"]["mapping"] == "doubtful"
