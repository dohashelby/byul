"""Index declared file references from prepared model-read scopes."""

from collections.abc import Callable


def index_declared_files(
    files: object, *, fingerprint: Callable[[object], bool], error_code: str,
) -> dict[str, dict]:
    if type(files) is not list or not 0 < len(files) <= 256:
        raise ValueError(error_code)
    references = {}
    for row in files:
        if (type(row) is not dict or set(row) != {"normalized_path_sha256", "reference_sha256", "bytes"}
                or not fingerprint(row["normalized_path_sha256"]) or not fingerprint(row["reference_sha256"])
                or type(row["bytes"]) is not int or not 0 <= row["bytes"] < 2**63
                or row["normalized_path_sha256"] in references):
            raise ValueError(error_code)
        references[row["normalized_path_sha256"]] = row
    return references
