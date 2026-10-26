"""Scene LLM extraction with a fake client: keyframes, id drawing, validation, the adapter, hints. No network."""
import io
import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from vision import scene_llm as sl


def _tracked():
    """Two shots: s1 wide (0-1.8 s, the ball goes H1 -> H3), s2 close (2.0-2.8 s)."""
    frames = []
    for i in range(15):
        t = i * 0.2
        ball = {"x": 30.0, "y": 30.0} if i < 5 else {"x": 50.0, "y": 30.0}
        frames.append({"frame": i, "match_seconds": t, "ball": ball if i < 10 else None,
                       "shot": {"id": "s1" if i < 10 else "s2", "type": "wide" if i < 10 else "close"},
                       "players": [{"player_id": "H1", "team": "home", "x": 30.5, "y": 30.0},
                                   {"player_id": "H3", "team": "home", "x": 50.5, "y": 30.0},
                                   {"player_id": "A2", "team": "away", "x": 40.0, "y": 35.0}]})
    return frames


def _debug():
    return {"detections": [{"frame": i, "boxes": [
        {"cls": "person", "track_id": 1, "xyxy": [10, 10, 20, 40]},
        {"cls": "person", "track_id": 3, "xyxy": [50, 10, 60, 40]},
        {"cls": "person", "track_id": 2, "xyxy": [30, 12, 40, 42]},
        {"cls": "ball", "track_id": 9, "xyxy": [21, 36, 24, 39]}]} for i in range(15)]}


FRAMES = [np.full((60, 96, 3), 40, dtype=np.uint8) for _ in range(15)]


def _answer(**over):
    data = {"segments": [{"shot_id": "s1", "type": "wide", "match_label": None,
                          "teams": {"home": None, "away": None, "home_kit": "red", "away_kit": None},
                          "attack_right": {"home": True}}],
            "events": [{"t": 0.8, "type": "pass", "actor_id": "H1", "receiver_id": "H3", "body_part": "foot",
                        "lofted": True, "into_space": False, "confidence": 0.8, "evidence": "Ball rises towards H3."},
                       {"t": 1.6, "type": "header", "actor_id": "H3", "receiver_id": None, "body_part": "head",
                        "lofted": None, "into_space": None, "confidence": 0.7, "evidence": "H3 heads it."}],
            "posture": [{"t": 1.6, "player_id": "H3", "facing": "towards_goal", "body": "jumping"}],
            "notes": []}
    data.update(over)
    return json.dumps(data)


# ---------- fake SDK clients ----------

class FakeOpenAI:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(model=kwargs["model"], usage=SimpleNamespace(prompt_tokens=900, completion_tokens=150),
                               choices=[SimpleNamespace(finish_reason="stop",
                                                        message=SimpleNamespace(content=reply, refusal=None))])


def _openai_400(message):
    import openai
    req = httpx.Request("POST", "https://x.openai.azure.com/openai/v1/chat/completions")
    return openai.BadRequestError(message, response=httpx.Response(400, request=req), body=None)


# ---------- keyframes and drawing ----------

def test_keyframes_cover_each_shot_and_the_holder_change():
    keys = sl.select_keyframes(_tracked(), max_images=12)
    assert 12 in keys                               # the close-up shot gets its middle frame
    assert any(k < 10 for k in keys)                 # the wide shot is covered
    assert 5 in keys                                 # the ball passes from H1 to H3 at frame 5
    assert keys == sorted(set(keys))
    assert len(sl.select_keyframes(_tracked(), max_images=2)) == 2


def test_keyframes_bracket_the_ball_moving_between_players():
    moments = sl.ball_moments(_tracked())
    assert (4, "ball leaves H1") in moments and (5, "ball reaches H3") in moments
    keys = sl.select_keyframes(_tracked(), max_images=3)
    assert keys == [3, 4, 5]        # before the release, the release, the reception: ball moments come first


def test_frame_captions_carry_the_trackers_ball_notes():
    out = sl.annotate_clip(FRAMES, _tracked(), 5, debug=_debug(), env={})
    assert out["prompt_version"] == "scene-extract/4" and "skipped" in out
    kf = {k["index"]: k for k in out["keyframes"]}
    assert kf[4]["vision"] == ["ball leaves H1"] and kf[5]["vision"] == ["ball reaches H3"]
    assert kf[3]["vision"] == []


def test_boxes_carry_cv_ids_and_are_drawn_in_team_colour():
    items = sl.keyframe_boxes(3, _tracked(), debug=_debug())
    assert {i["player_id"] for i in items} == {"H1", "H3", "A2", "ball"}
    from PIL import Image
    big = np.full((900, 2400, 3), 40, dtype=np.uint8)
    jpeg = sl.draw_ids(big, [{"player_id": "H1", "xyxy": [100, 300, 200, 600], "team": "home"}])
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and max(img.size) == sl.LONG_EDGE
    r, g, b = img.convert("RGB").getpixel((round(100 * sl.LONG_EDGE / 2400), 400 * sl.LONG_EDGE // 2400))
    assert r > 150 and g < 120                         # home outline is red


# ---------- validation ----------

def test_schema_rejects_coordinates_and_unknown_ids_are_nulled():
    with_xy = json.loads(_answer())
    with_xy["events"][0]["x"] = 31.5                   # a position: not in the schema
    with pytest.raises(ValueError, match="unexpected key 'x'"):
        sl.validate(with_xy, {"H1", "H3"}, 3.0)
    data = json.loads(_answer())
    data["events"][0]["receiver_id"] = "H99"
    data["events"][1]["evidence"] = "H3 heads it from 11 m out."
    data["events"].append({**data["events"][0], "t": 30.0})
    data["posture"].append({"t": 1.0, "player_id": "A77", "facing": None, "body": None})
    clean, rejected = sl.validate(data, {"H1", "H3", "A2"}, 2.8)
    assert clean["events"][0]["receiver_id"] is None
    assert clean["events"][1]["evidence"] == ""
    assert len(clean["events"]) == 2 and len(clean["posture"]) == 1
    assert len(rejected) == 4


def test_skipped_when_no_model_is_configured():
    out = sl.annotate_clip(FRAMES, _tracked(), 5, debug=_debug(), env={})
    assert "skipped" in out and out["prompt_version"] == sl.PROMPT_VERSION and out["keyframes"]


# ---------- the adapter ----------

def test_openai_adapter_degrades_from_json_schema_to_json_mode():
    fake = FakeOpenAI([_openai_400("response_format json_schema is not supported for this model"), _answer()])
    out = sl.annotate_clip(FRAMES, _tracked(), 5, client=fake, debug=_debug(), env={})
    assert "error" not in out and out["api"] == "openai" and out["structured"] == "json_object"
    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    assert fake.calls[1]["response_format"] == {"type": "json_object"}
    parts = fake.calls[1]["messages"][1]["content"]
    assert any(p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in parts)


def test_openai_invalid_json_once_then_valid():
    fake = FakeOpenAI(['{"segments": []', _answer()])
    out = sl.annotate_clip(FRAMES, _tracked(), 5, client=fake, debug=_debug(), env={})
    assert out["attempts"] == 2 and out["events"]


def test_openai_invalid_twice_is_an_error_and_no_images_is_a_skip():
    out = sl.annotate_clip(FRAMES, _tracked(), 5, client=FakeOpenAI(["nope", "still nope"]), debug=_debug(), env={})
    assert "error" in out
    out = sl.annotate_clip(FRAMES, _tracked(), 5, client=FakeOpenAI([_openai_400("image input is not supported")]),
                           debug=_debug(), env={})
    assert "image" in out["skipped"]


# ---------- configuration ----------

def test_config_prefers_managed_identity_and_normalises_endpoints():
    base = {"SCENE_LLM_ENDPOINT": "https://res.services.ai.azure.com", "SCENE_LLM_MODEL": "gpt-5-vision",
            "SCENE_LLM_API_KEY": "k"}
    assert sl.config(base)[0]["auth"] == "key"
    assert sl.config({**base, "IDENTITY_ENDPOINT": "http://localhost:42356/msi/token"})[0]["auth"] == "entra"
    assert sl.config({"SCENE_LLM_ENDPOINT": "https://res.services.ai.azure.com"})[0] is None
    assert sl.config({**base, "SCENE_LLM_IMAGES": "0"})[0] is None
    assert sl.openai_base_url("https://res.openai.azure.com/openai/v1/chat/completions") == \
        "https://res.openai.azure.com/openai/v1/"


# ---------- hints for the formulas ----------

def test_hints_map_lofted_into_space_and_header():
    result = {"prompt_version": sl.PROMPT_VERSION, "model": "m", "events": [
        {"t": 0.8, "type": "pass", "actor_id": "H1", "receiver_id": "H3", "lofted": True, "into_space": False,
         "body_part": "foot", "confidence": 0.8, "keyframe": 4},
        {"t": 1.6, "type": "header", "actor_id": "H3", "receiver_id": None, "lofted": None, "into_space": None,
         "body_part": "head", "confidence": 0.7},
        {"t": 4.0, "type": "pass", "actor_id": "A2", "receiver_id": "A5", "lofted": False, "into_space": True,
         "body_part": "foot", "confidence": 0.9},
        {"t": 5.0, "type": "pass", "actor_id": "A5", "receiver_id": "A6", "lofted": None, "into_space": None,
         "body_part": None, "confidence": 0.3}]}
    hints = sl.to_event_hints(result)
    h3, a5 = hints["by_receiver"]["H3"], hints["by_receiver"]["A5"]
    assert (h3["aerial"], h3["into_space"], h3["p_header"]) == (True, False, 1.0)
    assert (a5["aerial"], a5["into_space"], a5["p_header"]) == (False, True, 0.0)
    assert "A6" not in hints["by_receiver"]            # below the confidence floor
    assert sl.hint_for_pass(hints, "H3", 1.0) == h3 and sl.hint_for_pass(hints, "H3", 9.0) is None


def test_hint_receiver_is_inferred_from_the_passers_teammate_who_shoots_next():
    result = {"events": [
        {"t": 0.6, "type": "pass", "actor_id": "A16", "receiver_id": None, "lofted": True, "into_space": True,
         "confidence": 0.8},
        {"t": 3.0, "type": "shot", "actor_id": "A32", "receiver_id": None, "body_part": "foot", "confidence": 0.9},
        {"t": 3.2, "type": "goal", "actor_id": "A32", "receiver_id": None, "confidence": 0.9}]}
    h = sl.to_event_hints(result)
    (p,) = h["passes"]
    assert (p["actor_id"], p["receiver_id"], p["receiver_inferred"], p["p_header"]) == ("A16", "A32", True, 0.0)
    assert h["goals"] == [3.2] and [s["actor_id"] for s in h["shots"]] == ["A32", "A32"]
    result["events"][1]["actor_id"] = result["events"][2]["actor_id"] = "H5"   # an opponent shoots: no guess
    assert sl.to_event_hints(result)["passes"] == []
