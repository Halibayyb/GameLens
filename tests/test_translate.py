"""Auto-translated commentary: names kept, language matching, the API's ?lang=, and the voice per language."""
import io
import json
import urllib.request

import pytest
from fastapi.testclient import TestClient

from engine import speech, translate


def test_names_are_marked_not_to_translate_and_come_back_unchanged():
    html = translate.protect("Jack slices 5 defenders open, and Paul & co finish!", ["Jack", "Paul", "Pa"])
    assert '<span class="notranslate">Jack</span>' in html and '<span class="notranslate">Paul</span>' in html
    assert "Pa</span>ul" not in html and "&amp;" in html   # a shorter name inside a longer one is left alone
    assert translate.unprotect('<span class="notranslate">Jack</span> ทะลุ &amp; ยิง') == "Jack ทะลุ & ยิง"


@pytest.mark.parametrize("tag,code", [("es", "es"), ("es-MX", "es"), ("fr-CA", "fr"), ("lo", "lo"), ("th-TH", "th"),
                                      ("de-AT", "de"), ("zh-TW", "zh-Hans"), ("xx", "en"), (None, "en")])
def test_language_codes_match_the_supported_list(tag, code):
    assert translate.language(tag) == code


@pytest.fixture()
def api(monkeypatch, tmp_path, match):
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("AZURE_TRANSLATOR_ENDPOINT", "https://res.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    for k in ("AZURE_SPEECH_REGION", "AZURE_SPEECH_KEY", "AZURE_SPEECH_RESOURCE_ID", "TRANSLATE_LLM_ENDPOINT",
              "TRANSLATE_LLM_MODEL", "TRANSLATE_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    translate._CACHE.clear()
    translate._FALLBACK.clear()
    from api.main import app
    return TestClient(app), next(m for m in match.moments if m.kind == "goal")


def test_the_moment_comes_back_in_the_asked_language_with_names_kept(api, monkeypatch):
    client, goal = api
    sent = []

    def fake(req, timeout):
        sent.append(req)
        text = json.loads(req.data)[0]["Text"]
        return io.BytesIO(json.dumps([{"translations": [{"text": "TH: " + text, "to": "th"}]}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    body = client.get(f"/api/moments/{goal.id}?lang=th").json()
    assert body["commentary"]["lang"] == "th" and body["text"]["commentary"].startswith("TH: ")
    assert body["commentary"]["line_en"] and "<span" not in body["text"]["commentary"]
    assert goal.summary()["player"] in body["text"]["commentary"]
    assert "to=th" in sent[0].full_url and "textType=html" in sent[0].full_url
    assert 'class=\\"notranslate\\"' in sent[0].data.decode() or 'class="notranslate"' in sent[0].data.decode()
    client.get(f"/api/moments/{goal.id}?lang=th")
    assert len(sent) == 1   # cached
    assert client.get(f"/api/moments/{goal.id}").json()["commentary"]["lang"] == "en"


def test_a_failed_translation_falls_back_to_english(api, monkeypatch):
    client, goal = api

    def refused(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    body = client.get(f"/api/moments/{goal.id}?lang=fr").json()
    assert body["commentary"]["lang"] == "en" and "line_en" not in body["commentary"]
    assert client.get("/api/languages").json()["translated"] is True


def test_spanish_french_lao_and_thai_come_first_then_the_others(api):
    client, _ = api
    codes = [l["code"] for l in client.get("/api/languages").json()["languages"]]
    assert codes[:5] == ["en", "es", "fr", "lo", "th"] and {"pt", "de", "ja", "zh-Hans", "ar"} <= set(codes)


def test_each_language_has_its_own_voice_and_only_english_gets_the_excited_style(monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_VOICE", raising=False)
    assert speech.voice_for("es") == "es-ES-AlvaroNeural" and speech.voice_for("fr") == "fr-FR-HenriNeural"
    assert speech.voice_for("th") == "th-TH-NiwatNeural" and speech.voice_for("lo") == "lo-LA-ChanthavongNeural"
    assert speech.voice_for("en") == "en-US-GuyNeural" and speech.voice_for(None) == "en-US-GuyNeural"
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northcentralus")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req) or io.BytesIO(b"ID3"))
    monkeypatch.setattr(speech, "_cache_dir", lambda: __import__("pathlib").Path(__import__("tempfile").mkdtemp()))
    speech.synthesize("ประตู!", ["opener"], "th")
    speech.synthesize("Goal!", ["opener"], "en")
    th, en = (r.data.decode() for r in sent)
    assert "th-TH-NiwatNeural" in th and "xml:lang='th-TH'" in th and "express-as" not in th
    assert "en-US-GuyNeural" in en and "express-as" in en


def _llm(monkeypatch):
    monkeypatch.setenv("TRANSLATE_LLM_ENDPOINT", "https://res.openai.azure.com/openai/v1/")
    monkeypatch.setenv("TRANSLATE_LLM_MODEL", "gpt-5-mini")
    monkeypatch.setenv("TRANSLATE_LLM_API_KEY", "test-key")


def test_the_language_model_says_it_like_a_commentator_and_keeps_the_names(api, monkeypatch):
    client, goal = api
    _llm(monkeypatch)
    sent = []

    def fake(req, timeout):
        sent.append(req)
        body = json.loads(req.data)
        if "proofread" in body["messages"][0]["content"]:
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode())
        line = body["messages"][1]["content"]
        names = [n for n in (goal.summary()["player"], goal.summary()["team_name"]) if n and n in line]
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "¡Golazo! " + " y ".join(names)}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    body = client.get(f"/api/moments/{goal.id}?lang=es").json()
    assert body["commentary"]["lang"] == "es" and body["text"]["commentary"].startswith("¡Golazo!")
    req = json.loads(sent[0].data)
    assert len(sent) == 2 and "proofread" in json.loads(sent[1].data)["messages"][0]["content"]
    assert sent[0].full_url.endswith("/openai/v1/chat/completions") and req["model"] == "gpt-5-mini"
    assert "Spanish" in req["messages"][0]["content"] and goal.summary()["player"] in req["messages"][0]["content"]


def test_a_model_answer_that_changes_a_name_goes_to_the_translator_instead(api, monkeypatch):
    client, goal = api
    _llm(monkeypatch)
    urls = []

    def fake(req, timeout):
        urls.append(req.full_url)
        if "chat/completions" in req.full_url:
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "¡Gol de alguien!"}}]}).encode())
        text = json.loads(req.data)[0]["Text"]
        return io.BytesIO(json.dumps([{"translations": [{"text": "ES: " + text, "to": "es"}]}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    body = client.get(f"/api/moments/{goal.id}?lang=es").json()
    assert body["text"]["commentary"].startswith("ES: ") and len(urls) == 3 and "translator" in urls[2]   # model twice
    client.get(f"/api/moments/{goal.id}?lang=es")
    assert len(urls) == 3   # the stand-in is cached for a while...
    monkeypatch.setattr(translate.time, "time", lambda: 1e12)
    client.get(f"/api/moments/{goal.id}?lang=es")
    assert "chat/completions" in urls[3]   # ...then the model is asked again


def test_faithful_checks_names_and_numbers():
    src = "Jack slices 5 defenders open, and Paul finishes."
    assert translate.faithful(src, "Jack abre a 5 defensores y Paul remata.", ["Jack", "Paul"])
    assert not translate.faithful(src, "Jack abre a cinco defensores y Paul remata.", ["Jack", "Paul"])
    assert not translate.faithful(src, "Yak abre a 5 defensores y Paul remata.", ["Jack", "Paul"])
    assert not translate.faithful("Jack scores.", "Jack ยิงประตู!", ["Jack"], "lo")   # Thai script is not Lao
    assert translate.faithful("Jack scores.", "Jack ຍິງປະຕູ!", ["Jack"], "lo")


def test_lao_goes_straight_to_the_translator(api, monkeypatch):
    client, goal = api
    _llm(monkeypatch)
    urls = []

    def fake(req, timeout):
        urls.append(req.full_url)
        text = json.loads(req.data)[0]["Text"]
        return io.BytesIO(json.dumps([{"translations": [{"text": "LO: " + text, "to": "lo"}]}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert client.get(f"/api/moments/{goal.id}?lang=lo").json()["text"]["commentary"].startswith("LO: ")
    assert len(urls) == 1 and "translator" in urls[0]


def test_the_model_proofreads_its_translation_and_retries_with_the_problem(monkeypatch):
    _llm(monkeypatch)
    calls = []

    def fake(req, timeout):
        msgs = json.loads(req.data)["messages"]
        calls.append(msgs)
        if "proofread" in msgs[0]["content"]:
            ok = "remata" in msgs[1]["content"]
            answer = "OK" if ok else "PROBLEM: it says Alex headed it, the English does not"
        else:
            answer = "Kyle la levanta y Alex remata." if "rejected" in msgs[0]["content"] else "Kyle centra y Alex cabecea."
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": answer}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = translate.llm_translate("Kyle lifts it in, and Alex is there.", "es", ["Kyle", "Alex"])
    assert out == "Kyle la levanta y Alex remata." and len(calls) == 4
    assert "headed" in calls[2][0]["content"]   # the retry is told what was wrong

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(
        {"choices": [{"message": {"content": "PROBLEM: adds a header" if "proofread" in json.loads(req.data)["messages"][0]["content"]
                                  else "Kyle centra y Alex cabecea."}}]}).encode()))
    with pytest.raises(ValueError, match="header"):
        translate.llm_translate("Kyle lifts it in, and Alex is there.", "es", ["Kyle", "Alex"])


def test_every_caption_is_translated_and_each_part_can_be_spoken(api, monkeypatch):
    client, goal = api
    sent = []

    def fake(req, timeout):
        if "speech" in req.full_url or "tts" in req.full_url:
            return io.BytesIO(b"ID3audio")
        text = json.loads(req.data)[0]["Text"]
        sent.append(text)
        return io.BytesIO(json.dumps([{"translations": [{"text": "TH: " + text, "to": "th"}]}]).encode())

    from api.main import match
    goal = next(m for m in match().moments if m.kind == "goal"
                and len(client.get(f"/api/moments/{m.id}").json()["commentary"]["captions"]) == 3)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    said = client.get(f"/api/moments/{goal.id}?lang=th").json()["commentary"]
    assert [c["part"] for c in said["captions"]] == ["line", "smart", "finish"]
    assert all(c["text"].startswith("TH: ") for c in said["captions"]) and len(sent) == 3
    assert "%" in said["captions"][1]["text"]
    english = client.get(f"/api/moments/{goal.id}").json()["commentary"]["captions"]
    assert english[2]["text"] in ("Incredible vision!", "Sensational!", "Beautiful pass!", "What a ball!")
    for part in ("smart", "finish"):   # without Azure Speech: the caption comes back for the browser's voice
        body = client.get(f"/api/moments/{goal.id}/speech?lang=th&part={part}").json()
        assert body["speech"] == "browser" and body["text"] == next(c["text"] for c in said["captions"] if c["part"] == part)
    assert client.get(f"/api/moments/{goal.id}/speech?part=nope").status_code == 404


def test_cut_out_is_said_plainly_for_the_translators():
    assert translate.for_translation("Line-breaker from Jack: only one in three get through, five defenders cut out.") == \
        "Line-breaker from Jack: only one in three get through, the pass went past five defenders."
    assert translate.for_translation("one defender cut out.") == "the pass went past one defender."
