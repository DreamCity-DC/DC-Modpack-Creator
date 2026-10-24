class ExportError(Exception):
    """An actionable configuration or export error."""


class Cancelled(ExportError):
    pass
