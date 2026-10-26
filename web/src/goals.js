import { useCallback, useMemo } from "react";

// The moments you can open: the goals. Every other moment (shots, cards, substitutions...) is only shown on the timeline,
// with a hover text. A match with no goals falls back to all its moments so there is still something to open.
export const selectableMoments = (moments) => {
  const goals = moments.filter((m) => m.kind === "goal");
  return goals.length ? goals : moments;
};

// The selectable moments plus the previous / next maths. `setSelId` should be the app's manual-pick handler.
// prevIdx / nextIdx index into `goals` (-1 = none); they differ from idx +- 1 only when the selection is not one of them.
export function useGoals(moments, selId, setSelId) {
  const goals = useMemo(() => selectableMoments(moments), [moments]);
  const idx = goals.findIndex((m) => m.id === selId);
  const sel = moments.find((m) => m.id === selId);
  let prevIdx = idx - 1, nextIdx = idx >= 0 && idx < goals.length - 1 ? idx + 1 : -1;
  if (idx < 0 && sel) {
    prevIdx = goals.reduce((p, m, i) => (m.elapsed < sel.elapsed ? i : p), -1);
    nextIdx = goals.findIndex((m) => m.elapsed > sel.elapsed);
  }
  const go = useCallback((to) => { if (goals[to]) setSelId(goals[to].id); }, [goals, setSelId]);
  const noun = goals.every((m) => m.kind === "goal") ? "Goal" : "Moment";
  const label = idx >= 0 ? `${noun} ${idx + 1} of ${goals.length} · ${sel.minute} ${sel.title}` : sel ? `${sel.minute} ${sel.title}` : "";
  return { goals, idx, prevIdx, nextIdx, go, label, noun };
}
