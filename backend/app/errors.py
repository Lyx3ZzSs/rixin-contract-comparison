from __future__ import annotations


class AppError(Exception):
    """Base class for application errors that can be mapped at API boundaries."""

    status_code = 500


class ValidationError(AppError, ValueError):
    status_code = 400


class NotFoundError(AppError, FileNotFoundError):
    status_code = 404


class ConflictError(AppError, ValueError):
    status_code = 409


class ExternalServiceError(AppError, RuntimeError):
    status_code = 502


class DocumentProcessingError(AppError, ValueError):
    status_code = 422


class TaskExecutionError(AppError, RuntimeError):
    status_code = 409


class TaskCancelled(TaskExecutionError):
    """Normal worker control flow when cancellation has been requested."""


class TaskStaleLeaseError(TaskExecutionError):
    """The worker no longer owns the lease required for an execution write."""


class TaskTransitionConflict(TaskExecutionError):
    """The requested Task or Job state transition is not legal."""


class PipelineContractError(TaskExecutionError):
    pass
