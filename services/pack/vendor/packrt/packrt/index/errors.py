"""Error codes of `packrt.index` (contract C · D-267 C-10): build gives corpus_missing · embedding_failed · index_invalid (recipe
errors included), corpus also corpus_conflict; chunker_failed is a reserved name only (0.12.0 never calls a chunker, D-267 C-5);
find_corpus adds corpus_ambiguous (EV-008 CHG-P02)."""

CODES = ("corpus_missing", "embedding_failed", "index_invalid", "corpus_conflict", "chunker_failed", "corpus_ambiguous")


class IndexBuildError(RuntimeError):
    def __init__(self, code, message):
        assert code in CODES, code
        self.code = code
        super().__init__(message)
