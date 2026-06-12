"""External-service provider abstractions.

Module 006+ owns this package; each subpackage isolates a different
class of provider (``llm``, ``image``, ``tts``, ``r2``, ...). All
providers share the same shape: a narrow ABC + typed exceptions + a
``Fake`` implementation for tests.
"""
