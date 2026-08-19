class MediaPipelineError(Exception):
    """A stable, serializable failure at the media-processing boundary."""

    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
