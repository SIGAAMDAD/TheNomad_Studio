-- A live manual response owns its worker's new claims, including legacy adapters.
CREATE UNIQUE INDEX one_running_response_idx ON response_attempts(principal_id,worker_id) WHERE status='RUNNING';
CREATE INDEX latest_worker_response_idx ON response_attempts(principal_id,worker_id,started_at,response_id);
CREATE INDEX department_sequence_idx ON department_events(recipient_department,sequence);
