# Navigator ablation: is the Navigator agent worth its cost?

Generated 2026-10-26 09:20 by `scripts/navigator_ablation.py` (real model calls).

Requests where at least 2 safe candidates existed: **16**.

- Navigator (AI) picked the candidate that best fits the stated preference: **15 of 16**
- Code only (first safe candidate) did: **6 of 16**
- The Navigator uses about 713 tokens per plan (≈ $0.00007 at input prices; output is a short reason).

| Start | Preference | Safe candidates' values | Navigator's pick | Code-only pick |
|---|---|---|---|---|
| Cross Campus | prefer parks and paths | path_or_sidewalk_miles: 5.02, 5.18, 5.07 | 5.02 ❌ | 5.02 ❌ |
| Cross Campus | I want hills today | climb_ft: 110, 130, 126 | 130 ✅ | 110 ❌ |
| Cross Campus | flat and easy please | climb_ft: 110, 130, 126 | 110 ✅ | 110 ✅ |
| Cross Campus | new streets, no retracing | retraces_itself_pct: 0, 0, 0 | 0 ✅ | 0 ✅ |
| Wooster Square | prefer parks and paths | path_or_sidewalk_miles: 5, 5.08, 5.14 | 5.14 ✅ | 5 ❌ |
| Wooster Square | I want hills today | climb_ft: 53, 48, 80 | 80 ✅ | 53 ❌ |
| Wooster Square | flat and easy please | climb_ft: 53, 48, 80 | 48 ✅ | 53 ❌ |
| Wooster Square | new streets, no retracing | retraces_itself_pct: 0, 0, 0 | 0 ✅ | 0 ✅ |
| East Rock | prefer parks and paths | path_or_sidewalk_miles: 5.11, 5.12 | 5.12 ✅ | 5.11 ❌ |
| East Rock | I want hills today | climb_ft: 142, 132 | 142 ✅ | 142 ✅ |
| East Rock | flat and easy please | climb_ft: 142, 132 | 132 ✅ | 142 ❌ |
| East Rock | new streets, no retracing | retraces_itself_pct: 0, 0 | 0 ✅ | 0 ✅ |
| Westville | prefer parks and paths | path_or_sidewalk_miles: 4.94, 4.89, 4.95 | 4.95 ✅ | 4.94 ❌ |
| Westville | I want hills today | climb_ft: 139, 98, 148 | 148 ✅ | 139 ❌ |
| Westville | flat and easy please | climb_ft: 139, 98, 148 | 98 ✅ | 139 ❌ |
| Westville | new streets, no retracing | retraces_itself_pct: 1, 1, 2 | 1 ✅ | 1 ✅ |
