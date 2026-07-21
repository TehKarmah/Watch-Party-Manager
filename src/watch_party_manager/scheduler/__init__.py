"""Persistent scheduling infrastructure for WASH."""

from .job_handler import DiscordChannelMessenger, JobExecutionResult, JobHandler, RetryableJobError
from .close_vote_job_handler import CloseVoteJobHandler
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
from .vote_reminder_job_handler import VoteReminderJobHandler
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
from .watch_party_reminder_job_handler import WatchPartyReminderJobHandler
from .watch_party_scheduling import (
    WATCH_PARTY_REMINDER_JOB_TYPE,
    build_watch_party_reminder_job,
    cancel_watch_party_reminder,
    reschedule_watch_party_reminder,
    resolve_watch_party_reminder_settings,
    schedule_watch_party_reminder,
    watch_party_reminder_logical_key,
)

__all__ = [
    "CloseVoteJobHandler",
    "DiscordChannelMessenger",
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
    "VoteReminderJobHandler",
    "WatchPartyReminderJobHandler",
    "CLOSE_VOTE_JOB_TYPE",
    "VOTE_REMINDER_JOB_TYPE",
    "WATCH_PARTY_REMINDER_JOB_TYPE",
    "build_close_vote_job",
    "build_vote_reminder_job",
    "build_vote_scheduled_jobs",
    "build_watch_party_reminder_job",
    "cancel_watch_party_reminder",
    "close_vote_logical_key",
    "reschedule_watch_party_reminder",
    "resolve_vote_reminder_settings",
    "resolve_watch_party_reminder_settings",
    "schedule_vote_jobs",
    "schedule_watch_party_reminder",
    "vote_reminder_logical_key",
    "watch_party_reminder_logical_key",
]
