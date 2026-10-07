"""Domain exceptions. Each carries the HTTP status the API should answer with."""


class AppError(Exception):
    """Base class for errors that map directly onto an HTTP response."""

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EmptyUploadError(AppError):
    status_code = 400


class UnsupportedFileTypeError(AppError):
    status_code = 415


class UploadTooLargeError(AppError):
    status_code = 413


class UploadNotFoundError(AppError):
    status_code = 404


class FileNotReadyError(AppError):
    """Results were requested for a file that is not COMPLETED."""

    status_code = 409


class ProcessingError(AppError):
    """An accepted file could not be processed (corrupt, unsafe, missing CRS...).

    The ingest service turns this into a FAILED file record plus an HTTP 422.
    """

    status_code = 422
