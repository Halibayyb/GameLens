"""Verified freeze frames: N-point homography, corrections applied, the admin endpoint."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from highlights.verify import VerifyError, verify_freeze
from vision.homography import homography, reprojection_m
from vision.pitch_keypoints import LANDMARKS, project_landmarks

# image (960 x 540) -> metres for a camera on the left half
H = np.array([[0.11, 0.02, -2.0], [0.004, -0.13, 70.0], [0.00002, 0.0003, 1.0]])
INV = np.linalg.inv(H)


def to_image(x, y):
    p = INV @ [x, y, 1.0]
    return [float(p[0] / p[2]), float(p[1] / p[2])]


def box_at(x, y, w=14, h=40):
    u, v = to_image(x, y)
    return [u - w / 2, v - h, u + w / 2, v]   # feet at the bottom centre


def freeze():
    people = [("A1", "away", 30, 30), ("A2", "away", 20, 40), ("A3", "away", 25, 20), ("A4", "away", 40, 50),
              ("H1", "home", 15, 33), ("H2", "home", 22, 28), ("H3", "home", 5, 34), ("R1", "ref", 35, 10)]
    return {"t": 1.0, "frame_size": [960, 540], "passer_id": "A1", "receiver_id": "A2", "attack_right": False,
            "lofted": False, "into_space": False, "end": [20, 40], "homography": H.ravel().tolist(),
            "landmarks": project_landmarks(H, (960, 540)),
            "boxes": [{"player_id": pid, "team": t, "role": "GK" if pid == "H3" else "OUT", "xyxy": box_at(x, y)}
                      for pid, t, x, y in people]}


def marks(f, ids=(1, 6, 9, 13, 14, 33)):
    by = {l["id"]: l for l in f["landmarks"]}
    return [{"id": i, "image": to_image(*LANDMARKS[i - 1])} for i in ids if by[i]]


def test_n_point_homography_recovers_a_known_mapping():
    src = [to_image(*LANDMARKS[i]) for i in (0, 5, 8, 12, 13, 32)]
    dst = [LANDMARKS[i] for i in (0, 5, 8, 12, 13, 32)]
    g = homography(src, dst)
    assert np.allclose(g, H / H[2, 2], atol=1e-6) and reprojection_m(g, src, dst) < 1e-6
    with pytest.raises(ValueError):
        homography([(0, 0), (1, 1), (2, 2), (3, 3)], [(0, 0), (1, 1), (2, 2), (3, 3)])


def test_verified_frame_maps_players_exactly_and_scores_the_pass():
    f = freeze()
    v = verify_freeze(f, {"landmarks": marks(f)})
    pos = {p["player_id"]: (p["x"], p["y"]) for p in v["players"]}
    assert pos["A1"] == pytest.approx((30, 30), abs=0.05) and pos["H3"] == pytest.approx((5, 34), abs=0.05)
    assert v["fit_m"] < 0.01 and v["verified"] and v["passer_id"] == "A1"
    played = next(o for o in v["options"] if o["played"])
    assert played["player_id"] == "A2"
    assert "R1" not in {o["player_id"] for o in v["options"]}   # a referee is neither a teammate nor a defender


def test_corrections_change_team_add_a_player_and_pick_the_receiver():
    f = freeze()
    v = verify_freeze(f, {"landmarks": marks(f), "players": [{"player_id": "A3", "team": "ignore"}],
                          "add": [{"xyxy": box_at(26, 36), "team": "away"}], "receiver_id": "A4"})
    ids = {o["player_id"] for o in v["options"]}
    assert "A3" not in ids and "Am1" in ids
    assert next(o for o in v["options"] if o["played"])["player_id"] == "A4"


@pytest.mark.parametrize("body,msg", [({"landmarks": []}, "at least four"),
                                      ({"landmarks": [{"id": 99, "image": [1, 2]}] * 4}, "Unknown landmark"),
                                      ({"receiver_id": "H1"}, "teammates")])
def test_bad_corrections_are_refused_with_a_reason(body, msg):
    f = freeze()
    body = {"landmarks": marks(f), **body}
    with pytest.raises(VerifyError, match=msg):
        verify_freeze(f, body)


def test_admin_endpoint_saves_the_verified_frame_and_the_ground_truth(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_VIDEO", "1")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("PUBLISHED_DIR", str(tmp_path / "pub"))
    from api.main import app
    from highlights.store import _job_dir, load_result, save_result
    f = freeze()
    _job_dir("hl_test").mkdir(parents=True)
    save_result("hl_test", {"job_id": "hl_test", "assist": [{"moment_id": "e5", "options": [], "freeze": f}]})
    client = TestClient(app)
    url, auth = "/api/admin/video/jobs/hl_test/freeze/e5", {"X-Admin-Token": "secret-token"}
    assert client.post(url, json={"landmarks": marks(f)}).status_code == 401
    assert client.post(url, json={"landmarks": []}, headers=auth).status_code == 400
    ok = client.post(url, json={"landmarks": marks(f)}, headers=auth)
    assert ok.status_code == 200 and ok.json()["verified"]["verified"]
    assert load_result("hl_test")["assist"][0]["verified"]["receiver_id"] == "A2"
    truth = json.loads((_job_dir("hl_test") / "freeze_truth.json").read_text())
    assert truth[0]["moment_id"] == "e5" and truth[0]["verified"]["passer_id"] == "A1"
    assert client.post("/api/admin/video/jobs/hl_test/freeze/e9", json={}, headers=auth).status_code == 404


def test_a_midfield_frame_is_fixed_with_points_on_the_touchline():
    f = freeze()
    halfway = [{"id": i, "image": to_image(*LANDMARKS[i - 1])} for i in (14, 15, 33)] + \
              [{"id": 31, "image": to_image(*LANDMARKS[30])}]
    with pytest.raises(VerifyError, match="do not fix the pitch"):
        verify_freeze(f, {"landmarks": halfway})   # three of four on the halfway line
    touch = [{"line": "far_touchline", "image": to_image(x, 68)} for x in (30, 40)]
    v = verify_freeze(f, {"landmarks": halfway, "line_points": touch})
    pos = {p["player_id"]: (p["x"], p["y"]) for p in v["players"]}
    assert pos["A2"] == pytest.approx((20, 40), abs=0.05) and v["fit_m"] < 0.01


def test_clicks_that_only_repeat_known_lines_are_refused_not_collapsed():
    f = freeze()
    body = {"landmarks": [{"id": i, "image": to_image(*LANDMARKS[i - 1])} for i in (14, 15, 31)],
            "line_points": [{"line": "far_touchline", "image": [to_image(x, 68)[0] + 0.7, to_image(x, 68)[1]]} for x in (30, 40)]
            + [{"line": "halfway", "image": to_image(52.5, 10)}]}
    with pytest.raises(VerifyError, match="do not fix the pitch"):
        verify_freeze(f, body)
