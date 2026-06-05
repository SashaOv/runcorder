# (spec) UserError
class UserError(Exception):
    """Raise to signal a user-visible error that should not produce a debug report.

    runcorder prints ``Error: <message>`` to stderr and exits non-zero, without
    writing a report or showing a traceback.
    """
