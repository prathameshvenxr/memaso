"""The only module (besides adapters/) that names concrete adapters. Core never imports them."""
from __future__ import annotations

from importlib import import_module

from .config import Config
from .container import Container
from .metering import MeteredEmbedder, MeteredLLM


def _plugin(name: str):
    """'package.module:factory' -> the factory. Lets a third party add an adapter with no change here."""
    mod, _, fn = name.partition(":")
    return getattr(import_module(mod), fn)


def _kw(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k != "adapter"}


def _llm(cfg: dict, host_call):
    name = cfg["adapter"]
    if host_call is not None or name == "host":
        if host_call is None:
            raise ValueError("llm.adapter 'host' needs the application's model call: build(cfg, llm=call_llm)")
        return import_module("memory_core.adapters.llm_host").HostLLM(host_call)
    if name == "none":
        return None
    if ":" in name:
        return _plugin(name)(**_kw(cfg))
    if name == "fake":
        return import_module("memory_core.adapters.llm_fake").FakeLLM()
    if name == "anthropic":
        return import_module("memory_core.adapters.llm_anthropic").AnthropicLLM(**_kw(cfg))
    if name == "openai":
        return import_module("memory_core.adapters.llm_openai").OpenAILLM(**_kw(cfg))
    return import_module("memory_core.adapters.llm_compat").CompatLLM(**_kw(cfg))


def _embedder(cfg: dict):
    name, kw = cfg["adapter"], _kw(cfg)
    if ":" in name:
        return _plugin(name)(**kw)
    if name == "fake":
        return import_module("memory_core.adapters.embed_fake").HashEmbedder(dim=kw.get("dim", 64))
    if name == "openai":
        if "dim" not in kw:
            raise ValueError("embedder.dim is required for the openai adapter (the model's output size)")
        return import_module("memory_core.adapters.embed_openai").OpenAIEmbedder(**kw)
    if name == "fastembed":
        return import_module("memory_core.adapters.embed_fastembed").FastEmbedder(**kw)
    return import_module("memory_core.adapters.embed_local").LocalEmbedder(**kw)


def _queue(cfg: dict):
    name = cfg["adapter"]
    if ":" in name:
        return _plugin(name)(**_kw(cfg))
    if name == "redis":
        return import_module("memory_core.adapters.queue_redis").RedisQueue(**_kw(cfg))
    local = {k: v for k, v in _kw(cfg).items() if k in ("workers", "max_attempts", "retry_delay")}
    return import_module("memory_core.adapters.queue_inproc").InProcQueue(**local)


def _auth(cfg: dict, aux):
    name = cfg["adapter"]
    if ":" in name:
        return _plugin(name)(aux, **_kw(cfg))  # factory(aux, **cfg) -> object with `async resolve(headers)`
    return import_module("memory_core.adapters.auth_apikey").ApiKeyAuth(aux)


def _parser(cfg: dict):
    name = cfg["adapter"]
    if ":" in name:
        return _plugin(name)(**_kw(cfg))
    return import_module("memory_core.adapters.parser_text").TextParser()


def _store(cfg: dict, dim: int):
    name = cfg.get("adapter", "postgres")
    build = _plugin(name) if ":" in name else import_module(f"memory_core.adapters.store_{name}").build
    return build({k: v for k, v in cfg.items() if k != "adapter"}, dim)


def build(cfg: Config, llm=None) -> Container:
    """`llm`: the host application's model call. When given, memory uses the app's own model; otherwise the
    adapter named in config is used, and `none` means no model at all (rules and verbatim storage only)."""
    inner = _embedder(cfg.embedder)
    store = _store(cfg.store, inner.dim)
    host = llm is not None or cfg.llm["adapter"] == "host"
    raw_llm = _llm(cfg.llm, llm)
    llm_port = raw_llm if (raw_llm is None or host) else MeteredLLM(raw_llm, store.aux)  # a host meters itself
    if cfg.truth.get("judge") == "llm" and raw_llm is None:
        raise ValueError("truth.judge is 'llm' but no model is configured: set llm.adapter, pass build(cfg, llm=...), "
                         "or use truth.judge 'auto'")
    if cfg.facts["extract"] == "llm" and raw_llm is None:
        raise ValueError("facts.extract is 'llm' but no model is configured: set llm.adapter, pass build(cfg, llm=...), "
                         "or use facts.extract 'rules'")
    return Container(cfg=cfg, pg=store.lifecycle, llm=llm_port, embedder=MeteredEmbedder(inner, store.aux),
                     docs=store.docs, mems=store.mems, vectors=store.vectors, keywords=store.keywords,
                     queue=_queue(cfg.queue), parser=_parser(cfg.parser),
                     auth=_auth(cfg.auth, store.aux), aux=store.aux, lock=store.lock, chunks=store.chunks,
                     chunk_keywords=store.chunk_keywords)
