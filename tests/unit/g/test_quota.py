from datetime import datetime,timezone
import pytest
from studio.integrations.quota import parse_github_quota,QuotaError
NOW=datetime(2026,10,30,tzinfo=timezone.utc)
BASE={'X-RateLimit-Limit':'5000','X-RateLimit-Remaining':'4000','X-RateLimit-Reset':'1790000000'}

def test_rest_limit_observed_from_headers_not_student_assumption():
 q=parse_github_quota(BASE,account_scope='user:owner',observed_at=NOW,status_code=200)
 assert q.limit==5000 and q.remaining==4000 and not q.exhausted

def test_exhausted_primary_uses_reset():
 q=parse_github_quota({**BASE,'X-RateLimit-Remaining':'0'},account_scope='user:owner',observed_at=NOW,status_code=403)
 assert q.exhausted and q.next_allowed_epoch(now_epoch=123)==1790000000

def test_secondary_uses_explicit_retry_after():
 q=parse_github_quota({**BASE,'Retry-After':'60'},account_scope='user:owner',observed_at=NOW,status_code=429)
 assert q.next_allowed_epoch(now_epoch=100)==160

@pytest.mark.parametrize('headers,status',[({},200),({**BASE,'X-RateLimit-Remaining':'6000'},200),(BASE,429),(BASE,500)])
def test_unknown_capacity_fails_closed(headers,status):
 with pytest.raises(QuotaError):parse_github_quota(headers,account_scope='user:owner',observed_at=NOW,status_code=status)
