"""Bounded CLI diagnostics: labels are programmer constants, never input values."""
from contextlib import contextmanager
import argparse
import json


class CandidateError(ValueError):
    def __init__(self, code, label):
        super().__init__(f"{code}:{label}")


class CandidateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CandidateError("INVALID_INPUT", "arguments")


@contextmanager
def operation(label):
    try:
        yield
    except CandidateError:
        raise
    except PermissionError:
        raise CandidateError("PERMISSION_DENIED", label) from None
    except FileNotFoundError:
        raise CandidateError("NOT_FOUND", label) from None
    except FileExistsError:
        raise CandidateError("ALREADY_EXISTS", label) from None
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise CandidateError("INVALID_JSON", label) from None
    except OSError:
        raise CandidateError("IO_ERROR", label) from None
    except (ValueError, TypeError, KeyError):
        raise CandidateError("INVALID_INPUT", label) from None
