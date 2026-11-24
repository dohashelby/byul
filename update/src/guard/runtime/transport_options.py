"""Common validation for bounded transport options."""

import math


def valid_duration(duration, maximum):
    return type(duration) in (int, float) and math.isfinite(duration) and 0 < duration <= maximum


def valid_kubectl_options(binary, duration):
    return type(binary) is str and bool(binary.strip()) and valid_duration(duration, 300)
