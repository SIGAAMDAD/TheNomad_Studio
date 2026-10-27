#!/usr/bin/env python3
"""Small, bounded GitHub REST/Projects V2 adapter. No third-party dependencies."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token=None, api_url=None, timeout=20):
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL", "https://api.github.com")).rstrip("/")
        self.timeout = timeout

    def request(self, method, path, payload=None, token=None):
        if not path.startswith("/"):
            raise ValueError("API path must start with /")
        url = self.api_url + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "NomadStudio/3.0"}
        credential = token if token is not None else self.token
        if credential:
            headers["Authorization"] = "Bearer " + credential
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            detail = err.read(1024).decode("utf-8", errors="replace")
            raise GitHubError(f"GitHub {method} {path}: HTTP {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            raise GitHubError(f"GitHub {method} {path}: {err.reason}") from err

    def pages(self, path, max_pages=20):
        """Bounded pagination; fail instead of silently ignoring a large board."""
        base = path
        sep = "&" if "?" in base else "?"
        result = []
        for page in range(1, max_pages + 1):
            data = self.request("GET", f"{base}{sep}per_page=100&page={page}")
            if not isinstance(data, list):
                raise GitHubError("Expected list from paginated REST endpoint")
            result.extend(data)
            if len(data) < 100:
                return result
        raise GitHubError(f"Pagination limit ({max_pages}) exceeded: {path}")

    def issues(self, repo):
        qrepo = _repo(repo)
        data = self.pages(f"/repos/{qrepo}/issues?state=all&sort=updated&direction=desc")
        return [item for item in data if "pull_request" not in item]

    def issue_events(self, repo, number):
        return self.pages(f"/repos/{_repo(repo)}/issues/{int(number)}/events")

    def graphql(self, query, variables, project_token=None):
        token = project_token or os.environ.get("STUDIO_PROJECTS_TOKEN", "")
        if not token:
            raise GitHubError("Projects API requires STUDIO_PROJECTS_TOKEN")
        # GitHub Enterprise hosts can set GITHUB_API_URL (same host/path semantics).
        result = self.request("POST", "/graphql", {"query": query, "variables": variables}, token=token)
        if result.get("errors"):
            raise GitHubError("Projects GraphQL: " + json.dumps(result["errors"])[:1400])
        return result.get("data", {})

    def project_items(self, owner, number, owner_type="user", project_token=None):
        if owner_type not in ("user", "organization"):
            raise ValueError("Project owner type must be user or organization")
        query = """
        query($owner:String!, $number:Int!, $after:String) {
          OWNER(login:$owner) { projectV2(number:$number) {
            id title items(first:100, after:$after) {
              nodes {
                id
                content {
                  ... on Issue { number repository { nameWithOwner } }
                  ... on DraftIssue { id title body }
                }
                fieldValueByName(name:"Status") {
                  ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
                }
              }
              pageInfo { hasNextPage endCursor }
            }
          } }
        }
        """.replace("OWNER(login:", f"{owner_type}(login:")
        items, after, project = [], None, None
        for _ in range(30):
            data = self.graphql(query, {"owner": owner, "number": int(number), "after": after},
                                project_token=project_token)
            project = (data.get(owner_type) or {}).get("projectV2")
            if not project:
                raise GitHubError(f"Project V2 not accessible: {owner}/{number}")
            connection = project["items"]
            items.extend(connection.get("nodes") or [])
            pageinfo = connection["pageInfo"]
            if not pageinfo.get("hasNextPage"):
                return {"id": project["id"], "title": project.get("title"), "items": items}
            after = pageinfo["endCursor"]
        raise GitHubError("Project V2 item pagination limit exceeded")

    def project_status_field(self, owner, number, owner_type="user", project_token=None):
        if owner_type not in ("user", "organization"):
            raise ValueError("Invalid project owner type")
        query = """
        query($owner:String!, $number:Int!) {
          OWNER(login:$owner) { projectV2(number:$number) {
            id field(name:"Status") {
              ... on ProjectV2SingleSelectField { id options { id name } }
            }
          } }
        }
        """.replace("OWNER(login:", f"{owner_type}(login:")
        data = self.graphql(query, {"owner": owner, "number": int(number)},
                            project_token=project_token)
        return (data.get(owner_type) or {}).get("projectV2")

    def set_project_status(self, project_id, item_id, field_id, option_id, project_token=None):
        query = """
        mutation($project:ID!, $item:ID!, $field:ID!, $option:String!) {
          updateProjectV2ItemFieldValue(input:{
            projectId:$project, itemId:$item, fieldId:$field,
            value:{singleSelectOptionId:$option}
          }) { projectV2Item { id } }
        }
        """
        return self.graphql(query, {"project": project_id, "item": item_id,
                                    "field": field_id, "option": option_id},
                            project_token=project_token)


def _repo(repo):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        raise ValueError("Invalid repository owner/name")
    return repo


def parse_project_url(url):
    match = re.fullmatch(r"https://github\.com/(users|orgs)/([A-Za-z0-9_.-]+)/projects/([0-9]+)/?",
                         (url or "").strip())
    if not match:
        raise ValueError("Expected https://github.com/users/OWNER/projects/N or /orgs/OWNER/projects/N")
    return match.group(2), int(match.group(3)), ("user" if match.group(1) == "users" else "organization")


def owner_label(issue, events, label_name, approved_actors):
    """Return True only while label is present AND its last change was a trusted addition."""
    labels = {item["name"] if isinstance(item, dict) else item for item in issue.get("labels", [])}
    if label_name not in labels:
        return False
    changes = [event for event in events
               if event.get("event") in ("labeled", "unlabeled") and
               (event.get("label") or {}).get("name") == label_name]
    if not changes:
        return False  # fail closed: a label alone does not prove owner intent
    changes.sort(key=lambda event: (event.get("created_at", ""), event.get("id", 0)))
    latest = changes[-1]
    return latest.get("event") == "labeled" and (latest.get("actor") or {}).get("login", "").lower() in {
        x.lower() for x in approved_actors
    }
