"""WG-F named SQL plans for registration by WG-C's scoped APSW executor.

Only the four F-owned tables are modified here. audit_events and effect_intents
are read-only: the C transaction layer owns event writes and external effects.
"""

OWNER = "F"

SELECTS = {
    "f.budget.one": """SELECT budget_id,resource_key,limit_amount,reserved_amount,spent_amount
        FROM budgets WHERE project_id=:project_id AND budget_id=:budget_id""",
    "f.budget.list": """SELECT budget_id,resource_key,limit_amount,reserved_amount,spent_amount
        FROM budgets WHERE project_id=:project_id AND budget_id>:after
        ORDER BY budget_id LIMIT :limit""",
    "f.budget.revision": """SELECT COUNT(*) AS revision FROM audit_events WHERE
        project_id=:project_id AND event_type='budget.configured' AND
        subject_type='budget' AND subject_id=:budget_id""",
    "f.reservation.one": """SELECT reservation_id,budget_id,operation_id,amount,state FROM reservations
        WHERE project_id=:project_id AND reservation_id=:reservation_id""",
    "f.reservation.by_operation": """SELECT reservation_id,budget_id,operation_id,amount,state FROM reservations
        WHERE project_id=:project_id AND operation_id=:operation_id
        ORDER BY reservation_id LIMIT :limit""",
    "f.usage.by_provider": """SELECT usage_id,reservation_id,provider_ref,amount,evidence_digest
        FROM usage_receipts WHERE project_id=:project_id AND provider_ref=:provider_ref""",
    "f.usage.by_id": """SELECT usage_id,reservation_id,provider_ref,amount,evidence_digest
        FROM usage_receipts WHERE project_id=:project_id AND usage_id=:usage_id""",
    "f.notification.one": """SELECT notification_id,kind,subject_id,delivery_state,
        acknowledged_at,ack_principal_id FROM notifications
        WHERE project_id=:project_id AND notification_id=:notification_id""",
    "f.notification.list": """SELECT notification_id,kind,subject_id,delivery_state,
        acknowledged_at,ack_principal_id FROM notifications
        WHERE project_id=:project_id AND notification_id>:after
        ORDER BY notification_id LIMIT :limit""",
    "f.report.one": """SELECT report_id,report_digest,delivery_state FROM reports
        WHERE project_id=:project_id AND report_id=:report_id""",
    "f.report.list": """SELECT report_id,report_digest,delivery_state FROM reports
        WHERE project_id=:project_id AND report_id>:after
        ORDER BY report_id LIMIT :limit""",
    "f.effect.observed": """SELECT effect_id,provider_key,remote_operation_id FROM effect_intents
        WHERE project_id=:project_id AND subject_type=:subject_type
        AND subject_id=:subject_id AND action=:action AND state='observed'
        ORDER BY effect_id LIMIT 1""",
    "f.report.budget_aggregate": """SELECT budget_id,resource_key,limit_amount,reserved_amount,spent_amount
        FROM budgets WHERE project_id=:project_id ORDER BY budget_id LIMIT :limit""",
    "f.report.task_states": """SELECT state,COUNT(*) AS n FROM tasks WHERE project_id=:project_id
        GROUP BY state ORDER BY state LIMIT 16""",
    "f.report.oversight_states": """SELECT state,COUNT(*) AS n FROM oversight_cases WHERE project_id=:project_id
        GROUP BY state ORDER BY state LIMIT 8""",
    "f.report.release_states": """SELECT state,COUNT(*) AS n FROM releases WHERE project_id=:project_id
        GROUP BY state ORDER BY state LIMIT 16""",
    "f.report.notification_states": """SELECT delivery_state,COUNT(*) AS n FROM notifications
        WHERE project_id=:project_id GROUP BY delivery_state ORDER BY delivery_state LIMIT 8""",
    "f.report.usage_totals": """SELECT COALESCE(SUM(amount),0) AS amount, COUNT(*) AS receipts
        FROM usage_receipts WHERE project_id=:project_id""",
    "f.report.recent_events": """SELECT event_seq,event_type,subject_type,subject_id,payload_digest
        FROM audit_events WHERE project_id=:project_id AND event_seq<:before
        ORDER BY event_seq DESC LIMIT :limit""",
    "f.report.department_summary": """SELECT d.department_id,d.name,
        COUNT(DISTINCT cm.member_id) AS members,
        COUNT(DISTINCT t.task_id) AS tasks
        FROM departments d
        JOIN specialist_roles sr ON sr.department_id=d.department_id
        JOIN cabal_members cm ON cm.role_id=sr.role_id AND cm.project_id=:project_id
        LEFT JOIN tasks t ON t.project_id=cm.project_id AND t.cabal_id=cm.cabal_id
        GROUP BY d.department_id,d.name ORDER BY d.department_id LIMIT :limit""",
    "f.report.department_one": """SELECT d.department_id,d.name,
        COUNT(DISTINCT cm.member_id) AS members,
        COUNT(DISTINCT t.task_id) AS tasks
        FROM departments d
        JOIN specialist_roles sr ON sr.department_id=d.department_id
        JOIN cabal_members cm ON cm.role_id=sr.role_id AND cm.project_id=:project_id
        LEFT JOIN tasks t ON t.project_id=cm.project_id AND t.cabal_id=cm.cabal_id
        WHERE d.department_id=:department_id
        GROUP BY d.department_id,d.name""",
    "f.report.department_evidence": """SELECT DISTINCT d.department_id,t.task_id,t.state,t.revision,
        t.acceptance_digest, s.submission_id, q.review_id,q.verdict
        FROM departments d
        JOIN specialist_roles sr ON sr.department_id=d.department_id
        JOIN cabal_members cm ON cm.role_id=sr.role_id AND cm.project_id=:project_id
        JOIN tasks t ON t.project_id=cm.project_id AND t.cabal_id=cm.cabal_id
        LEFT JOIN submissions s ON s.project_id=t.project_id AND s.task_id=t.task_id
        LEFT JOIN qa_reviews q ON q.project_id=s.project_id AND q.submission_id=s.submission_id
        WHERE d.department_id=:department_id AND t.task_id>:after
        ORDER BY t.task_id LIMIT :limit""",
    "f.report.verified_relay": """SELECT rr.receipt_id,rr.message_id,rr.remote_path,
        rr.remote_commit_sha,rr.payload_digest,rr.observed_at
        FROM relay_receipts rr WHERE rr.project_id=:project_id
        ORDER BY rr.observed_at DESC,rr.receipt_id DESC LIMIT :limit""",
}

WRITES = {
    "f.budget.insert": """INSERT INTO budgets(project_id,budget_id,resource_key,limit_amount)
        VALUES(:project_id,:budget_id,:resource_key,:limit_amount)""",
    "f.budget.cas_limit": """UPDATE budgets SET limit_amount=:limit_amount WHERE
        project_id=:project_id AND budget_id=:budget_id AND
        limit_amount=:old_limit AND reserved_amount=:old_reserved AND
        spent_amount=:old_spent AND reserved_amount+spent_amount<=:limit_amount""",
    "f.budget.reserve": """UPDATE budgets SET reserved_amount=reserved_amount+:amount WHERE
        project_id=:project_id AND budget_id=:budget_id AND
        limit_amount-spent_amount-reserved_amount>=:amount""",
    "f.budget.consume": """UPDATE budgets SET reserved_amount=reserved_amount-:reserved,
        spent_amount=spent_amount+:actual WHERE project_id=:project_id AND budget_id=:budget_id
        AND reserved_amount>=:reserved AND :actual<=:reserved""",
    "f.budget.release": """UPDATE budgets SET reserved_amount=reserved_amount-:amount
        WHERE project_id=:project_id AND budget_id=:budget_id AND reserved_amount>=:amount""",
    "f.reservation.insert": """INSERT INTO reservations(project_id,reservation_id,budget_id,operation_id,amount,state)
        VALUES(:project_id,:reservation_id,:budget_id,:operation_id,:amount,'reserved')""",
    "f.reservation.consume": """UPDATE reservations SET state='consumed' WHERE
        project_id=:project_id AND reservation_id=:reservation_id AND state='reserved'""",
    "f.reservation.release": """UPDATE reservations SET state='released' WHERE
        project_id=:project_id AND reservation_id=:reservation_id AND state='reserved'""",
    "f.usage.insert": """INSERT INTO usage_receipts(project_id,usage_id,reservation_id,
        provider_ref,amount,evidence_digest) VALUES(:project_id,:usage_id,
        :reservation_id,:provider_ref,:amount,:evidence_digest)""",
    "f.notification.insert": """INSERT INTO notifications(project_id,notification_id,kind,subject_id,
        delivery_state) VALUES(:project_id,:notification_id,:kind,:subject_id,'queued')""",
    "f.notification.delivery": """UPDATE notifications SET delivery_state=:delivery_state WHERE
        project_id=:project_id AND notification_id=:notification_id AND
        delivery_state=:expected_state""",
    "f.notification.ack": """UPDATE notifications SET acknowledged_at=:acknowledged_at,
        ack_principal_id=:ack_principal_id WHERE project_id=:project_id
        AND notification_id=:notification_id AND delivery_state=:expected_state
        AND acknowledged_at IS NULL""",
    "f.report.insert": """INSERT INTO reports(project_id,report_id,report_digest,delivery_state)
        VALUES(:project_id,:report_id,:report_digest,'queued')""",
    "f.report.delivery": """UPDATE reports SET delivery_state=:delivery_state WHERE
        project_id=:project_id AND report_id=:report_id AND
        delivery_state=:expected_state""",
}


def plans() -> dict[str, tuple[str, str]]:
    """Register under owner=F; SELECT plans may read other owners' state."""
    return {**{k:("select",v) for k,v in SELECTS.items()},
            **{k:("execute",v) for k,v in WRITES.items()}}
