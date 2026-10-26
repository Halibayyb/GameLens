# Vision pipeline

Goal: turn football video into the inputs the formulas need (a freeze frame with every player and the ball
in pitch metres, plus the pass and the shot), with **positions from computer vision only**.

`vision/` is its own service (it carries PyTorch; the API does not). The API sends it an uploaded clip and
polls for the result.

## From a clip to the freeze frame

```
clip -> detect + track -> pitch mapping -> teams -> ball -> events -> scene model -> freeze frame -> formulas
```

| Step | Code | What it does |
|---|---|---|
| Detect and track | `vision/detectors.py` | Football YOLOv8 models (players, goalkeepers, referees; the ball on tiles) and ByteTrack ids that follow the camera pan |
| Pitch mapping | `vision/pitch_keypoints.py` | 32 pitch keypoints per frame, a homography onto 105 x 68 m, kept only when the projected lines land on painted ones |
| Camera cuts | `vision/shots.py` | Finds cuts. Nothing is carried across a cut, and frames without a trusted mapping (close-ups) keep no positions rather than wrong ones |
| Teams | `vision/pipeline.py` | Shirt colours clustered into the two teams; goalkeepers and referee kept apart |
| Events | `vision/events.py` | Touches, passes, the shot and the goal, in the shape `engine/assist.py` reads |
| Scene model | `vision/scene_llm.py` | gpt-5-mini reads keyframes for what tracking cannot see: who passed or shot, a lofted ball, a pass into space, the scorebug. It never gives positions; coordinates in its answer are rejected |
| Check | `web/src/FreezeEditor.jsx` | On the admin page a person can drag pitch landmarks and fix teams, passer and receiver on the one frame the formulas use; the corrected frame is saved as verified ground truth |

## Accuracy so far

On the synthetic goal clips, with a calibrated camera, players land within 0.3 to 0.4 m of their true
position (median). `python scripts/vision_scorecard.py` compares the
pipeline with the synthetic truth: the event detector on the true tracking, and the detector on the video.
Without a calibration, the pitch model alone can be metres off on these renders, which is why the admin
page lets a person check the pass frame.

## The road to live

What a live broadcast needs that the pipeline does not do yet, roughly in order:

1. **Ground truth.** A set of broadcast-style clips with checked freeze frames (the admin check already saves them) to
   measure every change against.
2. **Speed.** About 5 s per frame on CPU today. A GPU (the Bicep has a serverless T4 option) and streaming
   frames instead of whole clips.
3. **Pitch mapping through the whole broadcast.** Hold the camera model through pans and zooms, and recover
   it quickly after a replay or a close-up.
4. **Who is who.** Read shirt numbers and keep each player's identity across cuts, so passer and scorer are
   named without the scene model guessing.
5. **Reliable events.** Pass, reception and shot detection tuned on the ground-truth set, with confidence,
   so only trusted moments reach the formulas.
6. **Live data alongside video.** A licensed live event feed behind the existing
   `feeds/` adapter would give the moments and names; vision would add the positions.

Until then, an uploaded clip is analysed offline and a person can confirm its freeze frame in under a minute.

**Already built towards it**

- **Live transport** (`stream/replay.py`): player and ball frames and match events streamed into Azure Event
  Hubs (`match-tracking`, `match-events`) and on to browsers through Web PubSub. Today it replays a recorded
  match; a live vision feed would publish into the same hubs.

## Run it

```bash
pip install -r vision/requirements.txt
python -m vision.models                         # fetch the pinned weights into models/
python -m vision --video clip.mp4 --out data/vision --seconds 8
MATCH_SOURCE=vision MATCH_DIR=data/vision uvicorn api.main:app --reload
```

Only use clips you own or have a licence for. The football weights are Ultralytics YOLOv8 models
(AGPL-3.0). Scene model setup (Foundry deployment, managed identity) is in
[run-and-deploy.md](run-and-deploy.md).
