import asyncio

from pantheon.models import model_metadata

MODELS = [
    {'id': '~deepseek/deepseek-flash-latest', 'context_length': 1048576,
     'supported_parameters': ['tools', 'reasoning'], 'architecture': {'input_modalities': ['text']}},
    {'id': 'deepseek/deepseek-v4-flash', 'context_length': 1048576, 'supported_parameters': ['tools']},
    {'id': 'openai/gpt-5.5', 'context_length': 400000, 'supported_parameters': ['tools'],
     'architecture': {'input_modalities': ['text', 'image']}},
]


class Client:
    async def get(self, url):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {'data': MODELS}
        return R()


def test_provider_model_ids_get_real_context_and_capabilities():
    model_metadata._cache.update(at=0.0, models=[])
    found = asyncio.run(model_metadata.suggest(['deepseek-flash', 'gpt-5.5', 'my-local-finetune'], client=Client()))
    # DeepSeek Flash was hand-published at 65,536; its real context is 1M.
    assert found['deepseek-flash'] == dict(context=1048576, tools=True, reasoning=True, vision=False,
                                           source='openrouter:~deepseek/deepseek-flash-latest')
    assert found['gpt-5.5']['vision'] is True and found['gpt-5.5']['context'] == 400000
    assert 'my-local-finetune' not in found


def test_suggestions_never_block_discovery():
    class Broken:
        async def get(self, url):
            raise OSError('offline')
    model_metadata._cache.update(at=0.0, models=[])
    assert asyncio.run(model_metadata.suggest(['deepseek-flash'], client=Broken())) == {}
