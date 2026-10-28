CREATE TABLE job_departments (
 job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),department TEXT NOT NULL,
 decision_id TEXT NOT NULL UNIQUE REFERENCES decisions(decision_id),bound_at INTEGER NOT NULL
) STRICT;
CREATE INDEX job_department_idx ON job_departments(department,job_id);
CREATE INDEX ready_jobs_idx ON jobs(state,created_at,job_id);
CREATE INDEX dependency_prerequisite_idx ON job_dependencies(prerequisite_job_id,job_id);
CREATE INDEX response_job_start_idx ON response_job_starts(response_id,sequence);
CREATE INDEX response_job_report_idx ON response_attempt_jobs(response_id,attempt_id);
CREATE INDEX audit_observer_idx ON supervisory_observations(observer_principal_id,kind,sequence);
CREATE TRIGGER immutable_job_department_update BEFORE UPDATE ON job_departments
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_JOB_SCOPE'); END;
CREATE TRIGGER immutable_job_department_delete BEFORE DELETE ON job_departments
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_JOB_SCOPE'); END;
