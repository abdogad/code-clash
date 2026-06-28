-- Dev logins (password for both: secret123). Seeded by app/codeclash/migrate.py
-- only on a non-production database whose users table is empty. The problem
-- catalog is seeded separately from db/seed/problems.sql.
INSERT INTO users (username, email, password_hash, rating) VALUES
    ('alice', 'alice@example.com', '$2b$12$RxBUd/bouGrfxfEoTB2WA.wWI9pL3B0qjmEkxmcJOtMsZCmydg5QW', 1200),
    ('bob',   'bob@example.com',   '$2b$12$RxBUd/bouGrfxfEoTB2WA.wWI9pL3B0qjmEkxmcJOtMsZCmydg5QW', 1200);
