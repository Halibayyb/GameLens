// Speaks the commentary. speakMoment() plays the API's spoken commentator line (Azure AI Speech, an excited neural voice,
// GET /api/moments/{id}/speech) and falls back to the browser's own voice (Web Speech API) when the API has no Azure
// Speech or the audio cannot play; speak() is the browser voice for the short captions.
export const RATE = 1.1;   // a little faster than normal, so a caption is spoken inside the seconds it is on screen
export const LANG = "en-US";   // the captions' language (they are written in English)
// BCP 47 tags for the browser's voice in each commentary language (lang.js)
export const SPEECH_TAG = { en: "en-US", es: "es-ES", fr: "fr-FR", lo: "lo-LA", th: "th-TH", pt: "pt-BR", de: "de-DE",
  it: "it-IT", ja: "ja-JP", ko: "ko-KR", "zh-Hans": "zh-CN", vi: "vi-VN", id: "id-ID", ar: "ar-SA" };

// v: a fingerprint of the caption the page shows, so a changed script is a new address and the browser never replays
// audio it kept from an older one (the API caches each recording for a day). The API ignores it and speaks only its own captions.
export const textVersion = (text) => {
  let h = 5381;
  for (const ch of String(text || "")) h = ((h * 33) ^ ch.codePointAt(0)) >>> 0;
  return h.toString(36);
};

export const speechSupported = () => typeof window !== "undefined" && "speechSynthesis" in window && typeof window.SpeechSynthesisUtterance === "function";

// synth / Utterance are injectable for tests. speak() drops whatever is still being said first, so the voice never lags behind the picture.
export function createSpeaker(synth = typeof window !== "undefined" ? window.speechSynthesis : null, Utterance = typeof window !== "undefined" ? window.SpeechSynthesisUtterance : null,
  fetchSpeech = defaultFetchSpeech, AudioCtor = typeof window !== "undefined" ? window.Audio : null) {
  // id|lang|part|caption -> Promise<{ url } | { text }>: each caption is fetched once. The page's own speakers share one
  // store with prefetchSpeech(), so audio fetched ahead of time is what they play; a speaker given its own fetchSpeech
  // (the tests) keeps its own.
  const audioFor = fetchSpeech === defaultFetchSpeech ? SHARED_AUDIO : new Map();
  let playing = null, turn = 0;
  const cancel = () => {
    turn += 1;   // a speakMoment() still waiting for its audio will not start after this
    try { synth?.cancel(); } catch { /* speech not available */ }
    if (playing) { try { playing.pause(); } catch { /* already gone */ } playing = null; }
  };
  const speakBrowser = (text, onEnd, lang) => {
    if (!synth || !Utterance || !text) { onEnd?.(); return; }
    try {
      synth.cancel();
      const u = new Utterance(text);
      u.lang = SPEECH_TAG[lang] || LANG; u.rate = RATE;
      if (onEnd) u.onend = onEnd;
      synth.speak(u);
    } catch { onEnd?.(); /* speech not available */ }
  };
  return {
    // onEnd(): called once the line has been said (or could not be)
    //   part: which of the moment's captions (line, smart, finish); each is fetched once per language
    async speakMoment(id, text, onEnd, lang = "en", part = "line") {
      cancel();
      const mine = turn;
      const key = `${id}|${lang}|${part}|${text}`;   // a changed caption is fetched again
      if (!audioFor.has(key)) audioFor.set(key, fetchSpeech(id, lang, part === "line" ? undefined : part, text).catch(() => ({})));
      const said = await audioFor.get(key);
      if (mine !== turn) return;   // cancelled (or another line started) while the audio was on its way
      if (said.url && AudioCtor) {
        try {
          playing = new AudioCtor(said.url);
          if (onEnd) playing.onended = onEnd;
          await playing.play();
          return;
        } catch { playing = null; /* autoplay blocked or bad audio: the browser voice instead */ }
      }
      speakBrowser(said.text || text, onEnd, said.lang || lang);
    },
    speak(text) {
      if (playing) { try { playing.pause(); } catch { /* already gone */ } playing = null; }
      speakBrowser(text);
    },
    cancel,
  };
}

const SHARED_AUDIO = new Map();

// Fetch one caption's audio ahead of time (prefetch.js); resolves when it is ready, never rejects.
export function prefetchSpeech(id, lang = "en", part = "line", text = "") {
  const key = `${id}|${lang}|${part}|${text}`;
  if (!SHARED_AUDIO.has(key)) SHARED_AUDIO.set(key, defaultFetchSpeech(id, lang, part === "line" ? undefined : part, text).catch(() => ({})));
  return SHARED_AUDIO.get(key);
}

async function defaultFetchSpeech(id, lang, part, text) {
  const { getMomentSpeech } = await import("./api.js");
  return getMomentSpeech(id, lang, part, text);
}
