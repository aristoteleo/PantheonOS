"""Text/vision tier routing stays consistent through both image entry points."""
import json
from collections import OrderedDict
from unittest.mock import MagicMock, AsyncMock

import pytest

from pantheon.utils.model_selector import ModelSelector
from pantheon.utils import openrouter_catalog, vision_downgrade

PAIRS = [
    ('normal', 'openrouter/deepseek/deepseek-v4.1-flash', 'openrouter/deepseek/deepseek-v4.1-flash'),
    ('low', 'openrouter/~deepseek/deepseek-v4-flash-latest', 'openrouter/deepseek/deepseek-v4-flash-vision-exp'),
]


@pytest.fixture
def platform_selector(monkeypatch):
    monkeypatch.setenv('PLATFORM_MODEL_MODE', 'openrouter')
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: default
    settings.get_vision_model.return_value = 'auto'
    selector = ModelSelector(settings)
    selector._detected_provider = 'openrouter'
    monkeypatch.setattr(selector, '_effective_providers', lambda: {'openrouter'})
    # Exercise the bundled metadata, even when no live catalog can be fetched.
    catalog = openrouter_catalog._parse(json.loads(openrouter_catalog._SNAPSHOT.read_text()))
    monkeypatch.setattr(openrouter_catalog, '_CACHE', catalog)
    monkeypatch.setattr(openrouter_catalog, '_ensure_loaded_sync', lambda: None)
    monkeypatch.setattr('pantheon.utils.model_selector.get_model_selector', lambda: selector)
    monkeypatch.setattr('pantheon.settings.get_settings', lambda: settings)
    return selector


@pytest.mark.parametrize('tier,text,vision', PAIRS)
def test_tier_has_separate_text_and_vision_routes(platform_selector, tier, text, vision):
    selector = platform_selector
    assert selector.resolve_model(tier)[0] == text
    assert selector.resolve_model(tier + ',vision')[0] == vision
    assert selector.resolve_model_for_provider(tier + ',vision', 'openrouter')[0] == vision
    assert selector.find_vision_models(text)[0] == vision
    assert vision_downgrade._vision_candidates(text)[0] == vision
    assert selector._check_model_capability(text, 'vision') is (tier == 'normal')
    assert selector._check_model_capability(vision, 'vision') is True


def test_explicit_vision_choice_wins_over_active_tier(platform_selector):
    normal, low = PAIRS
    assert platform_selector.find_vision_models(low[1], 'normal')[0] == normal[2]
    assert platform_selector.find_vision_models(normal[1], 'low')[0] == low[2]
    assert platform_selector.find_vision_models(low[1], 'custom/vision-model')[0] == 'custom/vision-model'


def test_byok_does_not_inherit_platform_pairs(platform_selector, monkeypatch):
    monkeypatch.delenv('PLATFORM_MODEL_MODE')
    assert platform_selector._vision_models_for_tier('openrouter', 'low') == []
    assert platform_selector._vision_models_for_tier('openai', 'normal') == []


def test_custom_pair_override_and_provider_preference(platform_selector):
    selector = platform_selector
    selector.settings.get.side_effect = lambda key, default=None: (
        {'low': PAIRS[0][2]} if key == 'models.provider_vision_models.openrouter' else default
    )
    assert selector.find_vision_models(PAIRS[1][1])[0] == PAIRS[0][2]


async def test_normal_keeps_native_images_and_low_uses_its_companion(platform_selector, monkeypatch):
    describe = AsyncMock(return_value='low description')
    monkeypatch.setattr(vision_downgrade, '_describe', describe)
    monkeypatch.setattr(vision_downgrade, '_DESC_CACHE', OrderedDict())
    def history():
        return [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,test'}}]}]
    normal = await vision_downgrade.downgrade_blind_user_images(history(), PAIRS[0][1])
    low = await vision_downgrade.downgrade_blind_user_images(history(), PAIRS[1][1])
    assert normal[0]['content'][0]['type'] == 'image_url'
    assert 'low description' in low[0]['content'][0]['text']
    assert describe.await_count == 1


@pytest.mark.parametrize('tier,text,vision', PAIRS)
async def test_remote_observation_uses_callers_companion(platform_selector, monkeypatch, tmp_path, tier, text, vision):
    from PIL import Image
    from test_remote_image_sampling import remote_observe
    import pantheon.agent as agent_module

    image_path = tmp_path / 'pair.png'
    image = Image.new('RGB', (64, 32), 'red')
    image.putpixel((32, 16), (0, 0, 255))
    image.save(image_path)
    sample = AsyncMock(return_value={'success': True, 'response': 'Red with a blue dot.'})
    monkeypatch.setattr(agent_module, 'get_current_run_model', lambda: None)
    monkeypatch.setattr(agent_module, '_call_agent', sample)
    result, transmitted = await remote_observe(image_path, {'caller_models': [text]})
    assert transmitted[0]['context_variables']['caller_models'] == [text]
    assert result['success'] is True
    assert result['model_used'] == vision
    assert sample.await_args.kwargs['model'] == vision
