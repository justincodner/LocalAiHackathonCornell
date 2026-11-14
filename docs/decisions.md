# Decisions

Append only, one line per entry: `YYYY-MM-DD: decision (reason).` (spec 12.2)

2026-11-14: Helper HTTP server uses FlyingFox (no extra deps, smaller build than Hummingbird).
2026-11-14: Default BEACON_STT_MODEL is tiny.en: large-v3-626MB measured 1.2-1.3 s per 3 s clip and 130-170 s load on the M1 8 GB (tiny.en: ~80 ms, 1.3 s load).
