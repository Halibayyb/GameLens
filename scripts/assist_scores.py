"""Print every assist's options table, verdict and sentence (like scores.js in the prototype).

Run: python scripts/assist_scores.py [match_folder]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.assist import build_assist, verdict_template  # noqa: E402
from engine.moments import Match  # noqa: E402


def main(folder: Path) -> None:
    match = Match(folder)
    n = 0
    for m in match.moments:
        a = build_assist(match, m)
        if not a:
            continue
        n += 1
        p = a["pass"]
        print(f"\n{m.id} {m.event['minute']} {m.title}: {p['passer']} -> {p['receiver']}  "
              f"verdict={a['verdict']['key']}  tau={p['tau']}s  attack_right={a['attack_right']}")
        print(f"  {'option':<22}{'role':<5}{'arrives%':>9}{'goal%':>8}{'ev%':>7}{'cutOut':>7}")
        for o in a["options"]:
            print(f"  {o['player'] + (' *' if o['played'] else ''):<22}{o['role']:<5}{o['arrives'] * 100:>9.1f}"
                  f"{o['goal'] * 100:>8.1f}{o['ev'] * 100:>7.2f}{o['cut_out']:>7}")
        s = verdict_template(a["payload"])
        print(f"  {s}  [{len(s.split())} words]")
    print(f"\n{n} assists in {len(match.moments)} moments")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "match_007")
