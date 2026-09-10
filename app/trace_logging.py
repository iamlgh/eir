import logging


TRACE: int = 5  # below DEBUG (10)


class TraceLogger(logging.Logger):
    def trace(self, message: object, *args: object, **kwargs: object) -> None: ...


def _trace(self, message: object, *args: object, **kwargs: object) -> None:
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


def setup_trace_level() -> None:
    if hasattr(logging, 'TRACE'):
        return  # already registered, skip

    logging.TRACE = TRACE  # type: ignore[attr-defined]
    logging.addLevelName(TRACE, 'TRACE')
    logging.Logger.trace = _trace  # type: ignore[attr-defined]


setup_trace_level()  # runs on import, so any logger can use the trace level automatically
