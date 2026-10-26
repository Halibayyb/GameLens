# How it works

## 1. Find the pass behind the goal

`engine/moments.py` reads the match (events plus player and ball tracking) and, for each goal, finds the
assist: the last completed pass to the scorer in the 10 seconds before the shot. The **freeze frame** is the
tracking frame at that pass. Everything below is worked out from that one frame.

## 2. Score every option (`engine/assist.py`)

At the freeze frame, every teammate of the passer is an option. Coordinates are a 120 x 80 pitch, attacking
towards x = 120.

| Metric | Question | How |
|---|---|---|
| **Arrives** | will the pass get there? | `0.98 - 0.012 x distance - 0.25 x defenders in the lane - 0.15 x tight marker - 0.10 x lofted`, kept between 0.05 and 0.98 |
| **Goal chance** | if they shoot from there, will it go in? | a distance-and-angle shot model, lowered by defenders in the shot triangle |
| **EV** | what is the pass worth? | arrives x goal chance |
| **Cut out** | how many defenders does it take out of the play? | outfield opponents between passer and receiver, near the pass line |

**Verdict**, the first rule that fits the pass that was played: highest EV is the **Best option on the
pitch**, else the highest goal chance is **The brave pass**, else the most defenders cut out is a
**Line-breaker**, else a **Smart pass**. A ball into space is scored where the receiver meets it.

Not built yet: an expected-threat (xT) grid and a trained xG model. Both need real possession data; the
code already takes the larger of shot xG and xT, so they slot in later.

## 3. Say it (`engine/commentary.py`)

Each goal gets three captions, timed to its video:

1. The verdict and the odds, in words: *"Line-breaker from Jack: only one in three get through, five
   defenders cut out."*
2. The numbers: *"32% arrived, 16% score."*
3. A closing word for the verdict: *"Incredible vision!"*

The wording is fixed by the data, so the same goal always reads the same. A goal without a pass analysis
gets a short reaction line instead (opener, equaliser, late drama), which never says who wins.

## 4. Translate it (`engine/translate.py`)

The page offers 14 languages (Spanish, French, Lao and Thai first). For each caption:

1. **gpt-5-mini** (Microsoft Foundry) says it the way a TV commentator in that language would, with a short
   glossary of the pass terms (so "cut out" is never read as "intercepted").
2. Code checks that every name and number survived and that the alphabet is right (Lao is not Thai).
3. The model **proofreads** its own answer against the English and rejects anything added, missing or
   reversed. One retry, then **Azure Translator** (word for word, but reliable).

Lao goes straight to Translator: gpt-5-mini's Lao was poor. Translations are cached in memory, and a
fallback is retried with the model after 10 minutes.

## 5. Speak it (`engine/speech.py`)

Each caption is read by an Azure neural voice for its language (`/api/moments/{id}/speech?lang=&part=`).
English uses the "excited" style. Audio is cached on the server, and the page asks with a fingerprint of the
caption, so a changed script is never replaced by an old recording. Without Azure the browser's own voice
reads it. While one goal plays, the page prepares the next goals' captions and voice (`web/src/prefetch.js`).

## API in short

| Endpoint | Returns |
|---|---|
| `GET /api/match` | teams, score, moments, momentum per minute |
| `GET /api/moments/{id}?lang=` | the moment, the pass analysis, the replay track, the commentary captions |
| `GET /api/moments/{id}/speech?lang=&part=` | one caption as MP3 (or its text for the browser voice) |
| `GET /api/languages` | the commentary languages, and whether translation is on |
| `WS /api/live?channel=tracking` | player and ball frames, for the live pitch |
| `/api/admin/*` | the upload page: sign-in, clip jobs, freeze-frame check, publish |
