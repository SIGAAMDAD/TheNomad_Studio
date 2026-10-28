CREATE TABLE response_job_starts (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,response_id TEXT NOT NULL REFERENCES response_attempts(response_id),
 job_id TEXT NOT NULL REFERENCES jobs(job_id),attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 started_at INTEGER NOT NULL,UNIQUE(response_id,attempt_id)
) STRICT;
CREATE TABLE response_control_observations (
 response_id TEXT NOT NULL,control_epoch INTEGER NOT NULL,
 observer_principal_id TEXT NOT NULL REFERENCES principals(principal_id),observed_at INTEGER NOT NULL,
 PRIMARY KEY(response_id,control_epoch),
 FOREIGN KEY(response_id,control_epoch) REFERENCES response_controls(response_id,control_epoch)
) STRICT;
CREATE TRIGGER response_start_no_update BEFORE UPDATE ON response_job_starts
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_ATTEMPT'); END;
CREATE TRIGGER response_start_no_delete BEFORE DELETE ON response_job_starts
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_ATTEMPT'); END;
CREATE TRIGGER response_observation_no_update BEFORE UPDATE ON response_control_observations
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CONTROL_OBSERVATION'); END;
CREATE TRIGGER response_observation_no_delete BEFORE DELETE ON response_control_observations
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CONTROL_OBSERVATION'); END;
