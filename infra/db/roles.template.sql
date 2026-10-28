-- Run only after dedicated roles have been independently provisioned by operations.
-- Does not create accounts, passwords, extensions, databases, or network access.
-- The migration role that owns nomad_v16 must remain distinct from these roles.
REVOKE ALL ON SCHEMA nomad_v16 FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA nomad_v16 FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA nomad_v16 FROM PUBLIC;
-- Uncomment only after provisioning and testing the two dedicated roles:
-- GRANT USAGE ON SCHEMA nomad_v16 TO nomad_v16_gateway;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA nomad_v16 TO nomad_v16_gateway;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA nomad_v16 TO nomad_v16_gateway;
-- GRANT USAGE ON SCHEMA nomad_v16 TO nomad_v16_dashboard;
-- GRANT SELECT ON ALL TABLES IN SCHEMA nomad_v16 TO nomad_v16_dashboard;
-- Ensure default privileges for future migrations are locked down separately.
