import os

import pytest

from memory_core.adapters.embed_fake import HashEmbedder
from memory_core.adapters.llm_fake import FakeLLM
from memory_core.adapters.queue_inproc import InProcQueue
from tests.contract.base import EmbedderContract, LLMContract, QueueContract


class TestFakeEmbedder(EmbedderContract):
    def make(self):
        return HashEmbedder(dim=32)


class TestFakeLLM(LLMContract):
    def make(self):
        return FakeLLM()


class TestInProcQueue(QueueContract):
    def make(self):
        return InProcQueue(workers=2, retry_delay=0.001)


@pytest.mark.live
class TestOpenAIEmbedderLive(EmbedderContract):
    def make(self):
        from memory_core.adapters.embed_openai import OpenAIEmbedder
        return OpenAIEmbedder("text-embedding-3-small", 256)


@pytest.mark.live
class TestAnthropicLive(LLMContract):
    def make(self):
        from memory_core.adapters.llm_anthropic import AnthropicLLM
        return AnthropicLLM(os.environ.get("MEMASO_LIVE_ANTHROPIC_MODEL", "claude-sonnet-5-5"))


@pytest.mark.live
class TestOpenAILive(LLMContract):
    def make(self):
        from memory_core.adapters.llm_openai import OpenAILLM
        return OpenAILLM(os.environ.get("MEMASO_LIVE_OPENAI_MODEL", "gpt-4o-mini"))


@pytest.mark.live
class TestRedisQueueLive(QueueContract):
    def make(self):
        import uuid
        from memory_core.adapters.queue_redis import RedisQueue
        return RedisQueue(url=os.environ.get("MEMASO_TEST_REDIS", "redis://localhost:6379/0"), name=f"memaso:test:{uuid.uuid4()}", workers=2, retry_delay=0.001)


@pytest.mark.live
class TestFastEmbedLive(EmbedderContract):
    def make(self):
        from memory_core.adapters.embed_fastembed import FastEmbedder
        return FastEmbedder()
