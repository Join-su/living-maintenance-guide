"""Index building for the test ingest graph (custom_code@3: a plain function; runs only through packrt in the Pack environment)."""


def build_manual_index(batch):
    files = batch["files"]
    return {"summary": {"documents": len(files), "chunks": 0}}
