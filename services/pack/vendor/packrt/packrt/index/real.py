"""The real-embedding path of `packrt.index build` (EV-006 S4b, CHG-R08): the store's declared provider · model through the Pack's
models.yaml and the runtime's HttpProvider (OpenAI format), the key read from the environment variable --key-env names. Texts go in
batches of BATCH; calls and tokens (the provider's usage.prompt_tokens, which HttpProvider already counts) are returned for the LLMOps
cap (D-250 (5)). The key is never printed; a provider error reaches the caller as its type only (build.py).
EV-011 (CHG-K01 · K02, D-342 (2)): the key is found as the runner finds it — the process environment first, then the file
PACKRT_ENV_FILE names (LLMOps hands over only that path, B-8). The check reads the values the HttpProvider that sends the request has
read, so the check and the request header always pick the same value. A missing key says where it was looked for, by name only."""
from pathlib import Path

from ..ir import load_pack, strict_yaml
from ..run.providers import HttpProvider
from .errors import IndexBuildError

BATCH = 64
ENV_FILE = "PACKRT_ENV_FILE"


def _missing(key_env, env):
    """The text for a key found in neither place (CHG-K02). The file is only opened to tell 'cannot be read' from 'read, no key' —
    load_env_file keeps no read error — and what it holds is not looked at; no path, line or value goes into the text."""
    path = env.get(ENV_FILE)
    if not path:
        return f"키 환경 변수 {key_env}가 없습니다"   # 0.12.1, byte for byte
    try:
        with open(path, encoding="utf-8"):
            pass
    except OSError:
        return f"키 환경 변수 {key_env}가 없고, {ENV_FILE} 파일을 읽을 수 없습니다"
    return f"키 환경 변수 {key_env}가 환경에도 {ENV_FILE} 파일에도 없습니다"


def real_embedder(pack, graph, node, key_env, env=None):
    """(embedder, None): embedder(texts) -> (vectors, calls, tokens). The vector size is known from the first answer only."""
    import os
    env = dict(os.environ if env is None else env)
    res = load_pack(Path(pack))
    if not res.ok:
        raise IndexBuildError("index_invalid", f"Pack을 불러올 수 없습니다: {res.errors[0]['message'] if res.errors else '알 수 없는 오류'}")
    g = (res.doc.get("graphs") or {}).get(graph) or {}
    n = next((x for x in g.get("nodes", []) if x.get("id") == node), None)
    if n is None:
        raise IndexBuildError("index_invalid", f"그래프 '{graph}'에 node '{node}'가 없습니다")
    decl = (((res.doc.get("stores") or {}).get((n.get("params") or {}).get("store")) or {}).get("index") or {}).get("embedding") or {}
    provider, model = decl.get("provider"), decl.get("model")
    models_file = Path(pack) / "models.yaml"
    if not models_file.is_file():
        raise IndexBuildError("embedding_failed", "실제 임베딩에는 Pack의 models.yaml(provider 주소 · 키 환경 변수 이름)이 필요합니다")
    try:
        models = strict_yaml.load(models_file.read_text(encoding="utf-8"))[0] or {}
    except strict_yaml.StrictYAMLError as e:
        raise IndexBuildError("embedding_failed", f"models.yaml {e}") from None
    conf = ((models.get("providers") or {}).get(provider))
    if not isinstance(conf, dict):
        raise IndexBuildError("embedding_failed", f"models.yaml에 store 임베딩 provider '{provider}'가 없습니다")
    models = {**models, "providers": {**models["providers"], provider: {**conf, "env": key_env}}}   # the key named on the command line
    try:
        http = HttpProvider(models, env)   # reads PACKRT_ENV_FILE, as the runner does
    except UnicodeDecodeError:
        raise IndexBuildError("embedding_failed", f"{ENV_FILE} 파일을 UTF-8로 읽을 수 없습니다") from None
    if not (http.env.get(key_env) or http.env_file.get(key_env)):   # the order of HttpProvider._conf: the environment, then the file
        raise IndexBuildError("embedding_failed", _missing(key_env, env))

    def embed(texts):
        out, calls, before = [], 0, http.usage["tokens_in"]
        for i in range(0, len(texts), BATCH):
            out.extend(http.embed(provider, model, texts[i:i + BATCH]))
            calls += 1
        return out, calls, http.usage["tokens_in"] - before
    return embed, None
