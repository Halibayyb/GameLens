// Example form for the match summary: the last five matches of each side, as the assists each one produced. Static, made-up
// numbers for a synthetic match (the opponents are invented clubs), in the shape the pass analysis gives: the verdict id, the
// expected value (EV, 0-1) and xBest (EV as a multiple of the best other option). Oldest first. Keyed by home / away.
export const HISTORY = {
  home: [
    { opp: "Eastfield United", assists: [{ v: "best_option", ev: 0.112, x: 1.6 }, { v: "brave_pass", ev: 0.074, x: 0.8 }] },
    { opp: "Lakeside City", assists: [{ v: "line_breaker", ev: 0.058, x: 0.5 }] },
    { opp: "Oakridge FC", assists: [{ v: "best_option", ev: 0.141, x: 2.1 }, { v: "best_option", ev: 0.096, x: 1.3 }, { v: "brave_pass", ev: 0.083, x: 0.9 }] },
    { opp: "Stonebridge Wanderers", assists: [{ v: "brave_pass", ev: 0.066, x: 0.7 }, { v: "smart_pass", ev: 0.049, x: 0.4 }] },
    { opp: "Harbour Town", assists: [{ v: "line_breaker", ev: 0.101, x: 1.1 }, { v: "best_option", ev: 0.127, x: 1.8 }] },
  ],
  away: [
    { opp: "Westbrook Rangers", assists: [{ v: "brave_pass", ev: 0.088, x: 0.9 }] },
    { opp: "Millfield Town", assists: [{ v: "best_option", ev: 0.119, x: 1.7 }, { v: "line_breaker", ev: 0.062, x: 0.6 }] },
    { opp: "Ashford Albion", assists: [{ v: "line_breaker", ev: 0.071, x: 0.8 }, { v: "line_breaker", ev: 0.054, x: 0.5 }, { v: "brave_pass", ev: 0.092, x: 1.0 }] },
    { opp: "Kingsley Park", assists: [{ v: "best_option", ev: 0.133, x: 2.0 }] },
    { opp: "Redcliffe FC", assists: [{ v: "smart_pass", ev: 0.045, x: 0.5 }, { v: "brave_pass", ev: 0.079, x: 0.8 }] },
  ],
};
