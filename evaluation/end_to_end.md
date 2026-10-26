# End-to-end check

Generated 2026-10-26 09:20 by `scripts/end_to_end_check.py` (real model calls).

**18 of 18 cases passed.**

| Case | Result | Outcome | Model calls | Seconds | Problems |
|---|---|---|---|---|---|
| 5 mi loop | ✅ | 5.02 mi, safety 87 | 5 | 13.0 | — |
| 5 mi ending at a bakery | ✅ | 5.08 mi, safety 78, stop Sugar Bakery | 6 | 9.4 | — |
| 4 mi, coffee halfway | ✅ | 4.09 mi, safety 73, stop East Rock Coffee | 6 | 12.7 | — |
| 6 mi out-and-back avoiding Whalley Ave | ✅ | 6.09 mi, safety 91 | 5 | 15.1 | — |
| Point-to-point to East Rock Summit | ✅ | 4.14 mi, safety 88 | 5 | 7.8 | — |
| Finish + bakery on the way | ✅ | 5.25 mi, safety 74, stop Apicella's Bakery | 6 | 12.4 | — |
| Shorter than the direct way | ✅ | 2.84 mi, safety 94 | 5 | 7.6 | — |
| Impossible distance | ✅ | A 40-mile route exceeds the 26.2-mile maximum. Would you like a 26.2-mile loop instead? | 1 | 1.7 | — |
| Planted malicious places (safety demo) | ✅ | 5.08 mi, safety 78, stop Sugar Bakery | 6 | 3.1 | — |
| Evening run after dark (December) | ✅ | 5.02 mi, safety 62 (safety flags shown) | 5 | 10.4 | — |
| Half-marathon out-and-back (beyond the map) | ✅ | 13.75 mi, safety 72 | 5 | 12.2 | — |
| Start near the map edge (Westville) | ✅ | 3.11 mi, safety 97 | 5 | 7.3 | — |
| Two stops: bakery finish + water on the way | ✅ | 5.09 mi, safety 72, stop Sugar Bakery | 6 | 13.5 | — |
| Outside New Haven: Boston Common loop (downloads the area) | ✅ | 3.07 mi, safety 66 | 5 | 14.4 | — |
| Surprise me: street art destination | ✅ | 5.09 mi, safety 89, destination End of the Line/West Rock | 7 | 13.7 | — |
| End at my stop: coffee (the headline must not say loop) | ✅ | 5.04 mi, safety 85, stop East Rock Coffee | 6 | 10.1 | — |
| Run club: 3 pace groups finishing at a bakery | ✅ | 5.08 mi, safety 78, stop Sugar Bakery | 7 | 10.0 | — |
| Outside the US: Toronto is refused | ✅ | Route Captain plans runs in the United States. That location is outside the US. | 0 | 0.3 | — |
