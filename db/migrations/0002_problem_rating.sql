-- Dynamic per-problem difficulty (Codeforces-style): rating is seeded from band,
-- then drifts from solve outcomes (app/codeclash/rating/problems.py); rating_count
-- tapers the Elo K as a problem calibrates. Matchmaking picks problems near the
-- players' rating (matchmaking/service.py), so this column must exist before the
-- matchmaker runs — its absence on prod is what broke "enter the duel".
ALTER TABLE problems
    ADD COLUMN IF NOT EXISTS rating       INTEGER NOT NULL DEFAULT 1500,
    ADD COLUMN IF NOT EXISTS rating_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS problems_rating_idx ON problems (rating);
