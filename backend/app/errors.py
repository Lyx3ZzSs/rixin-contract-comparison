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


class PipelineContractError(TaskExecutionError):
    pass
