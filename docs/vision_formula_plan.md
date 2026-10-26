# From video to the passing formulas

How the vision pipeline feeds the formulas in [how-it-works.md](how-it-works.md).
The formulas already exist in `engine/assist.py` (a port of the prototype's
`scoring.js` on the 120 x 80 pitch). The vision work fills in what
`build_assist()` takes as input: a freeze-frame and the pass that set up
the shot.

```
video -> detections (pixels) -> pitch metres 105 x 68 -> events (pass, shot, goal)
      -> freeze-frame at the pass -> to_proto() 120 x 80 -> assist.score_options -> verdict
```

## A. Per frame

| Variable | Used by | Source | Note |
|---|---|---|---|
| Image to pitch homography | everything | pitch keypoints + `findHomography` RANSAC; 4-point calibration file for fixed cameras | Needs about 1 m accuracy: the PASS lane threshold is 2.5 units, about 2.2 m. `green_quad` is far worse |
| Player x, y, track id | all | detector + ByteTrack, bottom-centre of the box | exists |
| Team | mates vs opponents | jersey colour clustering | exists |
| Role GK / outfield / referee | GK counts for the PASS lane, not for blockers or CUT; `gk*` features | football detector class | replaces the `_keepers` heuristic |
| Ball x, y | possession, passes | detector + `interpolate_ball` | wrong when airborne (ground-plane projection) |
| Goal posts | goal mouth x = 120, y = 36..44; goal event | pitch keypoints | also checks the homography |
| Attack direction | `to_proto` mirroring | the goal the shot heads to; fallback GK side | |
| Visible pitch area | missing players | inverse homography of the frame | off-screen defenders mean undercounted lanes and blockers |

## B. Events (from motion over time)

| Variable | How |
|---|---|
| Possession | nearest player within about 1.5 m of the ball for at least 2 frames |
| Pass moment (the scored frame) | possession ends and the ball accelerates away |
| Passer, receiver | holder before; next teammate to gain possession |
| Pass end point (into-space cases) | ball position at reception |
| `aerial` (lofted) | not observable in 2D: proxies are impossible ground speed, ball size growth, broken ground track. Default ground and flag |
| Shot, goal | ball heading at the goal mouth; crossing the goal line between the posts |
| `isHeader` | pose model: ball contact nearer the head than the feet. `pHeader` blend at pass time |

## C. Per passing option (120 x 80 units)

| Formula input | Computed as | Code |
|---|---|---|
| `passDistance`, `laneDefenders` (< 2.5, GK in), `tightMarker` (< 3) | segment and nearest-opponent geometry | `chance_arrives` |
| `distance`, `visibleAngle`, `blockers` (triangle, GK out) | receiver vs posts | `goal_angle`, `in_triangle` |
| `nearestDefenderDist`, `gkDistFromLine`, `gkDistToShooter`, `gkInShotPath` | GK and opponent positions | new, for XG AI |
| xT cell | receiver position on a 16 x 12 grid | new |
| CUT | outfield opponents bypassed | `defenders_cut_out` counts opponents between passer and receiver in x and within 12 units of the pass (`CUT_INVOLVED`), as the prototype does |
| Arrival-time positions (into space) | flight time = distance / ball speed of the played pass | `reception_points`: each teammate where the tracking has them when such a ball would arrive; needs the `into_space` flag |

## Order of work

1. Adapter: vision frames -> pass, shot and goal events -> `Match` -> `build_assist()`, unchanged formulas.
2. Scorecard on the three synthetic goals (`docs/goal_0N`): truth from `data/match_proto`, then the adapter on true tracking, then full vision on the `.mp4`.
3. Pitch keypoints + RANSAC homography.
4. Football detector (player, GK, referee, ball), keep the team colour clustering.
5. Event detection hardening: possession, passes, shot, goal.
6. Lofted and header (pose), then into-space predictions.
7. Confidence gating: when a lane or the shot triangle is off-screen, or the homography is poor, show "estimate unavailable" instead of a number.
8. The PDF's XG AI, xT and trained PASS on the same feature code.

## What users see per goal

Verdict; the played pass against the next-best and the safest option
(arrives, goal chance, EV, CUT); the one-sentence explanation. Values are
labelled as estimates, with a confidence badge from step 7.

## Scorecard (2026-10-25)

`python scripts/vision_scorecard.py` (YOLO 1280 px input, 8 fps, CPU; tracking is cached).

| | Goal 1 | Goal 2 | Goal 3 |
|---|---|---|---|
| Level 2 (detector on true tracking): passer, receiver | correct | correct | correct |
| Level 2 verdict | matches | matches | wrong: the into-space flag is missing |
| Level 2 worst option error | 0.0001 | 0.58 arrives: the lofted flag is missing | 0.29 goal: the into-space flag is missing |
| Level 3 players found / on screen | 19.8 / 22 | 20.0 / 22 | 19.2 / 22 |
| Level 3 position error, calibrated (median / p90) | 0.29 / 0.91 m | 0.41 / 1.04 m | 0.33 / 0.58 m |
| Level 3 position error, `green_quad` (median / p90) | 12.7 / 44.4 m | 12.9 / 45.6 m | 6.2 / 39.8 m |
| Level 3 ball | none (detects the penalty spot) | none (penalty spot, stray hits) | none |
| Level 3 pass found | no | no | no |

What it shows:
- The event logic is sound. With the lofted flag (goal 2) and the into-space flag (goal 3) supplied by hand, goal 3 matches exactly. Goal 2's only remaining gap is one option sitting exactly on the 3-unit `tightMarker` threshold, which positions rounded to 2 decimals tip over.
- With a calibration, player positions are within the roughly 1 m the formulas need. `green_quad` is not usable.
- The blocker is the ball: COCO `yolo11n` never finds it in these clips. Next step: a football ball detector (or a static-camera ball tracker), then the lofted and into-space flags.

## Resolved gap: `frames.txt` vs the engine

`frames.txt` and `engine/assist.py` used to disagree (goal 1, pass to #11: 32% / 16% / 5 in `frames.txt`, 34% /
11% / 6 from the engine). GameLens was one prototype commit behind (`e2242b1`: into-space reception points,
lofted-pass penalty, partial blockers, involved-only cut-outs). Since the sync, the engine reproduces every option
in the three tables, now kept in `data_gen/proto_frames/`, and `tests/test_proto_demo.py` checks them. The scorecard
above was measured before the sync. The `.mp4` clips were rendered from the scene positions as they were then, and
two positions have moved since (goal 1's Defender 11, goal 2's Player 8), so level 3 needs new clips.
