# Tokenomics (measured)

Generated 2026-10-26T09:19 by `scripts/tokenomics.py` with gpt-6-luna ($0.1/1M input, $0.01/1M cached input, $0.5/1M output).

| Pass | Median input | Median cached input | Median output | Model calls | Portkey cache hits | Median cost (tokens) | Median billed | Median seconds |
|---|---|---|---|---|---|---|---|---|
| Fresh plan | 3,908 | 0 | 1,170 | 6 | 0 | $0.00098 | $0.00098 | 11.2 |
| Identical repeat | 3,908 | 0 | 1,170 | 6 | 6 | $0.00098 | $0.00000 | 2.8 |

Fresh plans per agent (mean over the plans that used it):

| Agent | Input | Cached input | Output | Calls | Max input | Max output | Cost | Plans |
|---|---|---|---|---|---|---|---|---|
| Planner | 723 | 0 | 79 | 1 | 728 | 228 | $0.00011 | 8 of 8 |
| Weather agent | 414 | 0 | 68 | 1 | 414 | 68 | $0.00008 | 8 of 8 |
| Pacing agent | 681 | 0 | 279 | 1 | 952 | 377 | $0.00021 | 8 of 8 |
| Calendar agent | 544 | 0 | 198 | 1 | 638 | 322 | $0.00015 | 8 of 8 |
| Briefing Writer | 1,222 | 0 | 434 | 1 | 1,529 | 654 | $0.00034 | 8 of 8 |
| Stops Scout | 682 | 0 | 177 | 1 | 688 | 208 | $0.00016 | 3 of 8 |
| Destination agent | 2,052 | 0 | 127 | 1 | 2,052 | 127 | $0.00027 | 1 of 8 |
| Run Club Captain | 684 | 0 | 448 | 1 | 684 | 448 | $0.00029 | 1 of 8 |

| Pass | Request | Input | Cached | Output | Calls | Cache hits | Cost | Billed | Seconds | OK |
|---|---|---|---|---|---|---|---|---|---|---|
| fresh | 5 mi loop | 3,440 | 0 | 700 | 5 | 0 | $0.00069 | $0.00069 | 10.5 | ✅ |
| repeat | 5 mi loop | 3,440 | 0 | 784 | 5 | 5 | $0.00074 | $0.00000 | 3.5 | ✅ |
| fresh | 5 mi ending at a bakery | 4,375 | 0 | 1,326 | 6 | 0 | $0.00110 | $0.00110 | 12.2 | ✅ |
| repeat | 5 mi ending at a bakery | 4,375 | 0 | 1,326 | 6 | 6 | $0.00110 | $0.00000 | 3.7 | ✅ |
| fresh | 4 mi, coffee halfway | 4,504 | 0 | 1,262 | 6 | 0 | $0.00108 | $0.00108 | 11.7 | ✅ |
| repeat | 4 mi, coffee halfway | 4,504 | 0 | 1,262 | 6 | 6 | $0.00108 | $0.00000 | 2.1 | ✅ |
| fresh | 6 mi out & back + water | 3,365 | 0 | 1,079 | 5 | 0 | $0.00088 | $0.00088 | 10.7 | ✅ |
| repeat | 6 mi out & back + water | 3,365 | 0 | 1,079 | 5 | 5 | $0.00088 | $0.00000 | 2.1 | ✅ |
| fresh | 4 mi to East Rock Summit | 3,441 | 0 | 978 | 5 | 0 | $0.00083 | $0.00083 | 9.0 | ✅ |
| repeat | 4 mi to East Rock Summit | 3,441 | 0 | 1,021 | 5 | 5 | $0.00085 | $0.00000 | 2.2 | ✅ |
| fresh | 5 mi surprise (street art) | 5,890 | 0 | 1,402 | 6 | 0 | $0.00129 | $0.00129 | 15.5 | ✅ |
| repeat | 5 mi surprise (street art) | 5,890 | 0 | 1,402 | 6 | 6 | $0.00129 | $0.00000 | 3.3 | ✅ |
| fresh | Run club, 3 groups, bakery | 5,373 | 0 | 1,944 | 7 | 0 | $0.00151 | $0.00151 | 12.8 | ✅ |
| repeat | Run club, 3 groups, bakery | 5,373 | 0 | 1,944 | 7 | 7 | $0.00151 | $0.00000 | 3.6 | ✅ |
| fresh | 3 mi loop | 3,071 | 0 | 876 | 5 | 0 | $0.00075 | $0.00075 | 9.2 | ✅ |
| repeat | 3 mi loop | 3,071 | 0 | 876 | 5 | 5 | $0.00075 | $0.00000 | 2.1 | ✅ |
