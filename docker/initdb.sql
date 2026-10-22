-- Local/dev only. The app connects as a non-superuser so row-level security applies.
CREATE ROLE memaso_app LOGIN PASSWORD 'memaso_app' NOSUPERUSER NOBYPASSRLS;
