import json
from types import SimpleNamespace

from app.services.promotion_prompt_service import PromotionPromptService


def _service(monkeypatch, **env):
    for name in (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "PROMOTION_PROMPT_PROVIDER",
        "PROMOTION_PROMPT_MODEL",
        "PROMOTION_VISION_MODEL",
        "OPENAI_MODEL",
    ):
        monkeypatch.setenv(name, "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return PromotionPromptService()


def _fake_completion(service, content):
    calls = []

    def create(**params):
        calls.append(params)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    service.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return calls


def test_deepseek_is_default_prompt_llm(monkeypatch):
    service = _service(monkeypatch, DEEPSEEK_API_KEY="deepseek-key", OPENAI_API_KEY="openai-key")

    assert (service.provider, service.model_name) == ("deepseek", "deepseek-flash")
    assert service.vision_model == "gpt-5-mini"


def test_openai_is_used_when_deepseek_key_is_missing(monkeypatch):
    service = _service(monkeypatch, OPENAI_API_KEY="openai-key")

    assert (service.provider, service.model_name) == ("openai", "gpt-5-mini")


def test_plan_falls_back_without_any_llm_key(monkeypatch):
    service = _service(monkeypatch)

    plan = service.build_prompt_plan(
        target="직장인", concept="순대국밥", style="energy", mode="standard", image_path=None
    )

    assert service.client is None
    assert plan["metadata"]["optimizer"] == "fallback"
    assert "drink foam" not in json.dumps(plan)


def test_plan_uses_llm_output_and_fills_missing_metadata(monkeypatch):
    service = _service(monkeypatch, DEEPSEEK_API_KEY="deepseek-key")
    calls = _fake_completion(
        service,
        json.dumps(
            {
                "title": "얼큰한 순대국밥",
                "hashtags": ["#순대국밥", "#점심추천"],
                "metadata": {"location": "A cozy gukbap restaurant"},
                "key_elements": ["steaming sundae gukbap"],
                "negative_prompts": ["text overlay"],
                "timeline": [{"time": "0-2s", "action": "Close-up of bubbling 순대국밥."}],
            },
            ensure_ascii=False,
        ),
    )

    plan = service.build_prompt_plan(
        target="직장인", concept="순대국밥", style="energy", mode="standard", image_path=None
    )

    assert plan["metadata"]["optimizer"] == "llm-structured"
    assert plan["metadata"]["location"] == "A cozy gukbap restaurant"
    assert plan["metadata"]["camera_setup"]
    assert plan["timeline"][0]["action"] == "Close-up of bubbling 순대국밥."
    assert calls[0]["model"] == "deepseek-flash"
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_plan_falls_back_when_llm_drifts_from_concept(monkeypatch):
    service = _service(monkeypatch, DEEPSEEK_API_KEY="deepseek-key")
    _fake_completion(
        service,
        json.dumps(
            {
                "title": "달콤한 크림 라떼",
                "hashtags": ["#라떼"],
                "metadata": {},
                "key_elements": ["latte art"],
                "negative_prompts": [],
                "timeline": [{"time": "0-2s", "action": "Pour milk into coffee."}],
            },
            ensure_ascii=False,
        ),
    )

    plan = service.build_prompt_plan(
        target="직장인", concept="순대국밥", style="energy", mode="standard", image_path=None
    )

    assert plan["metadata"]["optimizer"] == "fallback"


def test_recommendation_returns_llm_text(monkeypatch):
    service = _service(monkeypatch, DEEPSEEK_API_KEY="deepseek-key")
    _fake_completion(service, json.dumps({"recommended_prompt": "뚝배기 클로즈업으로 시작해주세요."}, ensure_ascii=False))

    text = service.recommend_concept_prompt(target="직장인", style="energy", mode="standard")

    assert text == "뚝배기 클로즈업으로 시작해주세요."
