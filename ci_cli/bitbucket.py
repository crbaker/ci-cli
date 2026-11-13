"""Minimal Bitbucket Cloud REST API client for repositories and pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import requests

API_ROOT = "https://api.bitbucket.org/2.0"


class BitbucketError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Pipeline:
    build_number: int
    uuid: str
    state: str           # PENDING | IN_PROGRESS | COMPLETED
    result: str | None   # SUCCESSFUL | FAILED | ERROR | STOPPED | EXPIRED | RUNNING | PAUSED ...
    ref_name: str | None
    created_on: datetime | None
    completed_on: datetime | None
    duration_seconds: int | None
    steps_done: int | None = None
    steps_total: int | None = None
    commit: str | None = None
    creator: str | None = None
    trigger: str | None = None
    # Extra detail (always parsed, mostly used by the detail screen)
    commit_hash: str | None = None
    ref_type: str | None = None          # branch | tag | named_branch | bookmark
    selector: str | None = None          # e.g. "default", "branches: main", "custom: deploy"
    pr_id: int | None = None
    pr_title: str | None = None
    pr_source: str | None = None
    pr_destination: str | None = None
    build_seconds_used: int | None = None
    run_number: int | None = None

    @property
    def status(self) -> str:
        """A single normalised status word for display."""
        if self.state == "COMPLETED":
            return self.result or "COMPLETED"
        if self.state == "IN_PROGRESS":
            # Stage is RUNNING or PAUSED (e.g. waiting on a manual step)
            return self.result or "RUNNING"
        return self.state

    @property
    def is_running(self) -> bool:
        return self.state in ("PENDING", "IN_PROGRESS")

    def elapsed_seconds(self) -> int | None:
        if self.duration_seconds and not self.is_running:
            return self.duration_seconds
        if self.created_on:
            end = self.completed_on or datetime.now(timezone.utc)
            return int((end - self.created_on).total_seconds())
        return None


@dataclass
class Command:
    phase: str   # setup | script | teardown
    name: str
    command: str


@dataclass
class Step:
    name: str
    state: str           # PENDING | READY | IN_PROGRESS | COMPLETED ...
    result: str | None   # SUCCESSFUL | FAILED | ERROR | STOPPED | NOT_RUN ...
    started_on: datetime | None
    completed_on: datetime | None
    duration_seconds: int | None
    uuid: str = ""
    image: str | None = None
    manual: bool = False
    max_time_minutes: int | None = None
    build_seconds_used: int | None = None
    run_number: int | None = None
    commands: list[Command] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.state == "COMPLETED":
            return self.result or "COMPLETED"
        if self.state == "IN_PROGRESS":
            return "RUNNING"
        return self.state

    @property
    def is_running(self) -> bool:
        return self.state == "IN_PROGRESS"

    @property
    def failed(self) -> bool:
        return self.state == "COMPLETED" and self.result in ("FAILED", "ERROR")

    def elapsed_seconds(self) -> int | None:
        if self.duration_seconds is not None and self.state == "COMPLETED":
            return self.duration_seconds
        if self.started_on:
            end = self.completed_on or datetime.now(timezone.utc)
            return int((end - self.started_on).total_seconds())
        return None


@dataclass
class CommitInfo:
    hash: str
    message: str
    author: str | None
    date: datetime | None


@dataclass
class FailedTest:
    name: str
    status: str
    reason: str | None = None


@dataclass
class TestReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    failures: list[FailedTest] = field(default_factory=list)


@dataclass
class Repo:
    slug: str
    full_name: str
    name: str
    updated_on: datetime | None
    pipelines: list[Pipeline] = field(default_factory=list)
    pipelines_error: str | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _q(uuid: str) -> str:
    """URL-encode a {uuid} for use in a path."""
    return quote(uuid, safe="")


def _parse_pipeline(item: dict) -> Pipeline:
    state = item.get("state") or {}
    sub = state.get("result") or state.get("stage") or {}
    target = item.get("target") or {}
    commit_hash = (target.get("commit") or {}).get("hash") or None
    selector = target.get("selector") or {}
    selector_text = None
    if selector.get("type"):
        selector_text = selector["type"]
        if selector.get("pattern"):
            selector_text += f": {selector['pattern']}"
    pr = target.get("pullrequest") or {}
    return Pipeline(
        build_number=item.get("build_number", 0),
        uuid=item.get("uuid", ""),
        state=state.get("name", "UNKNOWN"),
        result=sub.get("name"),
        ref_name=target.get("ref_name") or target.get("source") or (commit_hash or "")[:7] or None,
        commit=(commit_hash or "")[:7] or None,
        creator=(item.get("creator") or {}).get("display_name"),
        trigger=(item.get("trigger") or {}).get("name"),
        created_on=_parse_dt(item.get("created_on")),
        completed_on=_parse_dt(item.get("completed_on")),
        duration_seconds=item.get("duration_in_seconds"),
        commit_hash=commit_hash,
        ref_type=target.get("ref_type"),
        selector=selector_text,
        pr_id=pr.get("id"),
        pr_title=pr.get("title"),
        pr_source=target.get("source"),
        pr_destination=target.get("destination"),
        build_seconds_used=item.get("build_seconds_used"),
        run_number=item.get("run_number"),
    )


def _parse_step(s: dict) -> Step:
    state = s.get("state") or {}
    commands = []
    for phase in ("setup", "script", "teardown"):
        for c in s.get(f"{phase}_commands") or []:
            commands.append(Command(phase, c.get("name") or "", c.get("command") or ""))
    trigger = (s.get("trigger") or {}).get("type") or ""
    return Step(
        uuid=s.get("uuid", ""),
        name=s.get("name") or "(unnamed step)",
        state=state.get("name", "UNKNOWN"),
        result=(state.get("result") or {}).get("name"),
        started_on=_parse_dt(s.get("started_on")),
        completed_on=_parse_dt(s.get("completed_on")),
        duration_seconds=s.get("duration_in_seconds"),
        image=(s.get("image") or {}).get("name"),
        manual="manual" in trigger.lower(),
        max_time_minutes=s.get("maxTime") or s.get("max_time"),
        build_seconds_used=s.get("build_seconds_used"),
        run_number=s.get("run_number"),
        commands=commands,
    )


class BitbucketClient:
    def __init__(self, token: str, username: str | None = None, timeout: float = 20.0):
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if username:
            # Atlassian API tokens / app passwords use HTTP basic auth.
            self.session.auth = (username, token)
        else:
            # Workspace / project / repository access tokens use Bearer auth.
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    # -- low level -----------------------------------------------------------

    @staticmethod
    def _check(resp: requests.Response, url: str) -> None:
        if resp.status_code == 401:
            raise BitbucketError(
                "401 Unauthorized - check the API key, or run `ci-cli login` to replace it "
                "(Atlassian API tokens also need a username: your account email)",
                401,
            )
        if resp.status_code == 403:
            raise BitbucketError(f"403 Forbidden for {url} - token may lack the required scopes", 403)
        if not resp.ok:
            try:
                msg = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                msg = resp.text
            raise BitbucketError(f"{resp.status_code} for {url}: {msg}", resp.status_code)

    def _get(self, url: str, params: dict | None = None) -> dict:
        if not url.startswith("http"):
            url = API_ROOT + url
        resp = self.session.get(url, params=params, timeout=self.timeout)
        self._check(resp, url)
        return resp.json()

    def _paginate(self, url: str, params: dict | None = None, limit: int | None = None):
        count = 0
        while url:
            page = self._get(url, params)
            params = None  # the "next" URL already carries the query string
            for item in page.get("values", []):
                yield item
                count += 1
                if limit is not None and count >= limit:
                    return
            url = page.get("next")

    # -- API -----------------------------------------------------------------

    def list_workspaces(self) -> list[str]:
        """Discover workspaces the token can see.

        Tries the newer endpoint first, then the older (deprecated) one, then
        falls back to reading workspace slugs off the repositories the token
        is a member of. Raises the last error if every method fails.
        """
        last_error: BitbucketError | None = None
        for path in ("/user/workspaces", "/user/permissions/workspaces"):
            try:
                slugs = []
                for item in self._paginate(path, {"pagelen": 100}):
                    ws = item.get("workspace") or item
                    if ws.get("slug") and ws["slug"] not in slugs:
                        slugs.append(ws["slug"])
                if slugs:
                    return slugs
            except BitbucketError as exc:
                last_error = exc
        try:
            slugs = []
            for item in self._paginate("/repositories", {"role": "member", "pagelen": 100}, limit=500):
                slug = item.get("full_name", "").split("/")[0]
                if slug and slug not in slugs:
                    slugs.append(slug)
            if slugs:
                return slugs
        except BitbucketError as exc:
            last_error = exc
        if last_error:
            raise last_error
        return []

    def list_repositories(self, workspace: str, limit: int | None = None) -> list[Repo]:
        repos = []
        params = {"pagelen": 100, "sort": "-updated_on"}
        for item in self._paginate(f"/repositories/{workspace}", params, limit):
            repos.append(
                Repo(
                    slug=item["slug"],
                    full_name=item["full_name"],
                    name=item.get("name") or item["slug"],
                    updated_on=_parse_dt(item.get("updated_on")),
                )
            )
        return repos

    def recent_pipelines(self, full_name: str, count: int = 3) -> list[Pipeline]:
        data = self._get(
            f"/repositories/{full_name}/pipelines/",
            {"sort": "-created_on", "pagelen": count},
        )
        return [_parse_pipeline(item) for item in data.get("values", [])[:count]]

    def get_pipeline(self, full_name: str, pipeline_uuid: str) -> Pipeline:
        return _parse_pipeline(self._get(f"/repositories/{full_name}/pipelines/{_q(pipeline_uuid)}"))

    def pipeline_steps(self, full_name: str, pipeline_uuid: str) -> list[Step]:
        data = self._get(
            f"/repositories/{full_name}/pipelines/{_q(pipeline_uuid)}/steps/",
            {"pagelen": 100},
        )
        return [_parse_step(s) for s in data.get("values", [])]

    def fill_step_progress(self, full_name: str, pipeline: Pipeline) -> None:
        """For a running pipeline, count completed vs total steps."""
        steps = self.pipeline_steps(full_name, pipeline.uuid)
        pipeline.steps_total = len(steps)
        pipeline.steps_done = sum(1 for s in steps if s.state == "COMPLETED")

    def get_commit(self, full_name: str, commit_hash: str) -> CommitInfo:
        data = self._get(f"/repositories/{full_name}/commit/{commit_hash}")
        author = data.get("author") or {}
        name = (author.get("user") or {}).get("display_name") or author.get("raw")
        return CommitInfo(
            hash=data.get("hash", commit_hash),
            message=(data.get("message") or "").strip(),
            author=name,
            date=_parse_dt(data.get("date")),
        )

    def step_log(self, full_name: str, pipeline_uuid: str, step_uuid: str,
                 offset: int = 0) -> tuple[str, int, bool]:
        """Fetch a step's log from byte `offset` onwards.

        Returns (text, new_offset, replaced). `replaced` is True when the server
        ignored the range and sent the whole log, so the caller should replace
        rather than append. A log that doesn't exist yet returns ("", offset, False).
        """
        url = f"{API_ROOT}/repositories/{full_name}/pipelines/{_q(pipeline_uuid)}/steps/{_q(step_uuid)}/log"
        headers = {"Accept": "application/octet-stream, text/plain, */*"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        resp = self.session.get(url, headers=headers, timeout=self.timeout)
        if resp.status_code == 416:      # nothing new beyond offset
            return "", offset, False
        if resp.status_code == 404:      # step hasn't produced a log (yet)
            return "", offset, False
        self._check(resp, url)
        body = resp.content
        if resp.status_code == 206:
            return body.decode("utf-8", errors="replace"), offset + len(body), False
        # Full body. If it extends what we already have, return just the new part.
        if offset and len(body) >= offset:
            return body[offset:].decode("utf-8", errors="replace"), len(body), False
        return body.decode("utf-8", errors="replace"), len(body), True

    def test_report(self, full_name: str, pipeline_uuid: str, step_uuid: str,
                    max_failures: int = 25) -> TestReport | None:
        """Summary and failing tests for a step, or None if it published no test reports."""
        base = f"/repositories/{full_name}/pipelines/{_q(pipeline_uuid)}/steps/{_q(step_uuid)}/test_reports"
        try:
            summary = self._get(base)
        except BitbucketError as exc:
            if exc.status == 404:
                return None
            raise
        report = TestReport(
            total=summary.get("number_of_test_cases") or 0,
            passed=summary.get("number_of_successful_test_cases") or 0,
            failed=summary.get("number_of_failed_test_cases") or 0,
            errored=summary.get("number_of_error_test_cases") or 0,
            skipped=summary.get("number_of_skipped_test_cases") or 0,
        )
        if not report.total:
            return None
        if report.failed or report.errored:
            for case in self._paginate(f"{base}/test_cases", {"pagelen": 100}, limit=2000):
                status = (case.get("status") or "").upper()
                if status not in ("FAILED", "ERROR"):
                    continue
                name = case.get("fully_qualified_name") or case.get("name") or "(unnamed test)"
                reason = None
                if case.get("uuid"):
                    try:
                        r = self._get(f"{base}/test_cases/{_q(case['uuid'])}/test_case_reasons")
                        reason = r.get("message") or "\n".join(
                            v.get("message", "") for v in r.get("values", []) if v.get("message")
                        ) or None
                    except BitbucketError:
                        pass
                report.failures.append(FailedTest(name, status, reason))
                if len(report.failures) >= max_failures:
                    break
        return report
