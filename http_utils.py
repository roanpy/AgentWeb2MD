from __future__ import annotations


DEFAULT_MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class ResponseTooLarge(ValueError):
    pass


def buffer_response(response, max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
    """Buffer a streamed requests response without exceeding max_bytes."""
    declared = response.headers.get("content-length")
    if declared:
        try:
            declared_bytes = int(declared)
        except (TypeError, ValueError):
            declared_bytes = 0
        if declared_bytes > max_bytes:
            response.close()
            raise ResponseTooLarge(f"response exceeds {max_bytes} byte limit")

    content = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ResponseTooLarge(f"response exceeds {max_bytes} byte limit")
    finally:
        response.close()
    response._content = bytes(content)
    response._content_consumed = True
    return response
