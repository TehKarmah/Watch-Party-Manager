"""Persistent scheduling infrastructure for WASH."""

from .job_handler import JobExecutionResult, JobHandler, RetryableJobError
from .json_scheduler_repository import (
    DuplicateActiveJobError,
    InvalidSchedulerDataError,
    JsonSchedulerRepository,
    ScheduledJobNotFoundError,
)
from .retry_policy import RetryPolicy
from .scheduled_job import JobResult, JobStatus, ScheduledJob
from .scheduler_host import SchedulerHost
from .scheduler_repository import SchedulerRepository
from .scheduler_service import SchedulerService
from .vote_scheduling import (
    CLOSE_VOTE_JOB_TYPE,
    VOTE_REMINDER_JOB_TYPE,
    build_close_vote_job,
    build_vote_reminder_job,
    build_vote_scheduled_jobs,
    close_vote_logical_key,
    resolve_vote_reminder_settings,
    schedule_vote_jobs,
    vote_reminder_logical_key,
)

__all__ = [
    "DuplicateActiveJobError",
    "InvalidSchedulerDataError",
    "JobExecutionResult",
    "JobHandler",
    "JobResult",
    "JobStatus",
    "JsonSchedulerRepository",
    "RetryPolicy",
    "RetryableJobError",
    "ScheduledJob",
    "ScheduledJobNotFoundError",
    "SchedulerHost",
    "SchedulerRepository",
    "SchedulerService",
    "CLOSE_VOTE_JOB_TYPE",
    "VOTE_REMINDER_JOB_TYPE",
    "build_close_vote_job",
    "build_vote_reminder_job",
    "build_vote_scheduled_jobs",
    "close_vote_logical_key",
    "resolve_vote_reminder_settings",
    "schedule_vote_jobs",
    "vote_reminder_logical_key",
]
