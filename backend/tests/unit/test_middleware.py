"""
test_middleware.py — Request-id sanitisation for the access-log middleware.

The inbound X-Request-ID is attacker-controlled and gets copied onto every log
line and echoed in the response header, so app.middleware.sanitize_request_id
bounds and cleans it before it's trusted. These cases pin that behaviour.
"""

from app.middleware import _MAX_REQUEST_ID_LEN, sanitize_request_id


def test_valid_id_passes_through_unchanged():
    # A well-formed correlation id (letters, digits, . _ -) is preserved so
    # cross-service correlation with an upstream gateway still works.
    assert sanitize_request_id("abc123.DEF-4_5") == "abc123.DEF-4_5"


def test_none_and_empty_yield_none():
    assert sanitize_request_id(None) is None
    assert sanitize_request_id("") is None


def test_length_is_capped():
    assert sanitize_request_id("a" * 500) == "a" * _MAX_REQUEST_ID_LEN


def test_control_and_unsafe_chars_are_stripped():
    # Newlines/control chars are the log-injection vector; spaces and slashes
    # aren't valid id characters either — all removed, the rest kept.
    assert sanitize_request_id("ab\r\ncd GET /x\t42") == "abcdGETx42"


def test_all_unsafe_yields_none():
    # Nothing usable left after cleaning → caller falls back to a generated id.
    assert sanitize_request_id("///\n\n   ") is None
