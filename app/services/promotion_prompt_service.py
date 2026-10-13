import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.utils.logger import get_logger

try:
    import dspy
    from dspy.teleprompt import LabeledFewShot
except Exception:  # pragma: no cover - optional dependency at runtime
    dspy = None
    LabeledFewShot = None

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local", override=False)

logger = get_logger(__name__)


class PromotionPromptMetadata(BaseModel):
    prompt_name: str = "PULSE_PROMOTION_VIDEO"
    base_style: str
    aspect_ratio: str = "9:16"
    duration: str = "8 seconds"
    location: str = "A busy local Korean restaurant"
    camera_setup: str
    optimizer: str | None = None


class PromotionTimelineItem(BaseModel):
    time: str
    action: str


class PromotionPromptPlan(BaseModel):
    title: str
    hashtags: list[str] = Field(default_factory=list)
    metadata: PromotionPromptMetadata
    key_elements: list[str] = Field(default_factory=list)
    negative_prompts: list[str] = Field(default_factory=list)
    timeline: list[PromotionTimelineItem] = Field(default_factory=list)


class PromotionConceptRecommendation(BaseModel):
    recommended_prompt: str


if dspy:
    class DSPyPromotionPromptSignature(dspy.Signature):
        """Create a structured Veo promotion prompt plan as JSON."""

        target_persona = dspy.InputField(desc="Target customer persona in Korean.")
        concept = dspy.InputField(desc="Exact menu or promotion concept in Korean.")
        target_vibe = dspy.InputField(desc="Desired ad vibe such as energetic, premium, or mood.")
        sns_marketing_hook = dspy.InputField(desc="Hook instruction for the first two seconds.")
        camera_angle = dspy.InputField(desc="Preferred camera angle guidance.")
        lens_optical_effects = dspy.InputField(desc="Preferred lens and focus guidance.")
        visual_keywords = dspy.InputField(desc="Core visual style keywords.")
        food_focus_rule = dspy.InputField(desc="Rule for keeping food as the hero.")
        negative_constraints = dspy.InputField(desc="Things the generated video must avoid.")
        image_visual_context = dspy.InputField(desc="Observed visual details from the reference image.")
        reference_style_blueprint = dspy.InputField(desc="Reference reel blueprint to imitate.")

        rationale = dspy.OutputField(desc="Short explanation of the prompt strategy.")
        final_veo_json = dspy.OutputField(desc="Valid JSON for the prompt plan schema.")


    class DSPyPromotionPromptModule(dspy.Module):
        def __init__(self):
            super().__init__()
            self.generate = dspy.ChainOfThought(DSPyPromotionPromptSignature)

        def forward(self, **kwargs):
            return self.generate(**kwargs)


class DSPyPromotionOptimizer:
    def __init__(
        self,
        *,
        api_key: str | None,
        assets_dir: Path,
        model_name: str,
        reference_blueprints: list[dict[str, Any]],
    ) -> None:
        optimizer_path = assets_dir / "veo_optimizer.json"
        has_reference_data = bool(reference_blueprints)
        has_precompiled_optimizer = optimizer_path.exists()
        self.enabled = (
            dspy is not None
            and bool(api_key)
            and (has_reference_data or has_precompiled_optimizer)
            and os.getenv("PROMOTION_DSPY_ENABLED", "true").lower() not in {"0", "false", "off"}
        )
        self.model_name = model_name
        self.optimizer_name = "dspy"
        self.module = None
        self.trainset: list[Any] = []

        if not self.enabled:
            if dspy is None:
                logger.info("[PromotionPromptService] DSPy is unavailable. Falling back to direct prompting.")
            elif not api_key:
                logger.info("[PromotionPromptService] DSPy is disabled because GEMINI_API_KEY is missing.")
            elif not has_reference_data and not has_precompiled_optimizer:
                logger.info(
                    "[PromotionPromptService] DSPy is disabled because no optimizer assets are available."
                )
            return

        try:
            lm = dspy.LM(model_name, api_key=api_key)
            dspy.configure(lm=lm)
            self.module = DSPyPromotionPromptModule()
            self.trainset = self._build_trainset(reference_blueprints)

            if optimizer_path.exists():
                try:
                    self.module.load(str(optimizer_path))
                    self.optimizer_name = "dspy-precompiled"
                    logger.info("[PromotionPromptService] Loaded DSPy optimizer from %s", optimizer_path)
                    return
                except Exception as exc:
                    logger.warning("[PromotionPromptService] Failed to load DSPy optimizer file: %s", exc)

            if self.trainset and LabeledFewShot is not None:
                few_shot = LabeledFewShot(k=min(4, len(self.trainset)))
                self.module = few_shot.compile(student=self.module, trainset=self.trainset)
                self.optimizer_name = "dspy-labeled-fewshot"
                logger.info(
                    "[PromotionPromptService] Compiled DSPy few-shot optimizer with %s reference demos.",
                    len(self.trainset),
                )
        except Exception as exc:
            logger.warning("[PromotionPromptService] DSPy initialization failed: %s", exc)
            self.enabled = False
            self.module = None

    @staticmethod
    def _build_trainset(reference_blueprints: list[dict[str, Any]]) -> list[Any]:
        trainset = []
        for blueprint in reference_blueprints:
            final_json = blueprint.get("final_veo_json") or "{}"
            if not isinstance(final_json, str):
                final_json = json.dumps(final_json, ensure_ascii=False)
            example = dspy.Example(
                target_persona=blueprint.get("target_persona", ""),
                concept=blueprint.get("concept", ""),
                target_vibe=blueprint.get("target_vibe", ""),
                sns_marketing_hook=blueprint.get("sns_marketing_hook", ""),
                camera_angle=blueprint.get("camera_angle", ""),
                lens_optical_effects=blueprint.get("lens_optical_effects", ""),
                visual_keywords=blueprint.get("visual_keywords", ""),
                food_focus_rule=blueprint.get("food_focus_rule", ""),
                negative_constraints=blueprint.get("negative_constraints", ""),
                image_visual_context=blueprint.get("image_visual_context", ""),
                reference_style_blueprint=blueprint.get("reference_style_blueprint", ""),
                rationale=blueprint.get("rationale", ""),
                final_veo_json=final_json,
            ).with_inputs(
                "target_persona",
                "concept",
                "target_vibe",
                "sns_marketing_hook",
                "camera_angle",
                "lens_optical_effects",
                "visual_keywords",
                "food_focus_rule",
                "negative_constraints",
                "image_visual_context",
                "reference_style_blueprint",
            )
            trainset.append(example)
        return trainset

    def generate_plan(self, **kwargs) -> tuple[dict[str, Any] | None, str | None]:
        if not self.enabled or self.module is None:
            return None, None

        try:
            prediction = self.module(**kwargs)
            raw = (
                getattr(prediction, "final_veo_json", None) or ""
            )
            clean = raw.replace("```json", "").replace("```", "").strip()
            if not clean:
                return None, None
            parsed = json.loads(clean)
            return parsed, self.optimizer_name
        except Exception as exc:
            logger.warning("[PromotionPromptService] DSPy prompt optimization failed: %s", exc)
            return None, None


class PromotionPromptService:
    STYLE_MAP = {
        "energy": "energetic",
        "energetic": "energetic",
        "premium": "luxury",
        "luxury": "luxury",
        "mood": "emotional",
        "emotional": "emotional",
    }

    VIBE_LABELS = {
        "energetic": "Energetic food reel",
        "luxury": "Premium food commercial",
        "emotional": "Mood-driven food story",
    }

    DEFAULT_TEMPLATE = {
        "id": "energetic",
        "visual_keywords": "Hyper-realistic food commercial, appetizing texture, steam, glossy highlights",
        "camera_angle": "Extreme close-up, vertical framing, gentle push-in",
        "lens_optical_effects": "Macro lens, shallow depth of field",
        "sns_marketing_hook": "Start with the most appetizing texture in the first two seconds.",
        "negative_prompt": "text overlay, subtitles, watermark, deformed hands, blurry frame",
        "food_focus_rule": "Keep the food as the main subject. Avoid unrelated people or menu drift.",
        "aspect_ratio": "9:16",
    }

    def __init__(self) -> None:
        self.assets_dir = BASE_DIR / "app" / "assets" / "promotion"
        self.templates = self._load_json(self.assets_dir / "templates.json").get("templates", [])
        self.reference_blueprints = self._load_json(
            self.assets_dir / "reference_blueprints.json"
        ).get("reference_videos", [])

        # GEMINI_API_KEY는 선택 사항인 DSPy 최적화 경로에서만 사용한다.
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.provider, self.client, self.model_name = self._build_text_client()
        self.vision_client, self.vision_model = self._build_vision_client()
        self.request_timeout = float(os.getenv("PROMOTION_PROMPT_TIMEOUT_SECONDS", "60"))
        self.dspy_model_name = os.getenv(
            "PROMOTION_DSPY_MODEL",
            "gemini/gemini-2.5-flash",
        )
        self.dspy_optimizer = DSPyPromotionOptimizer(
            api_key=self.api_key,
            assets_dir=self.assets_dir,
            model_name=self.dspy_model_name,
            reference_blueprints=self.reference_blueprints,
        )

        if not self.client:
            logger.warning(
                "[PromotionPromptService] No prompt LLM key is configured "
                "(DEEPSEEK_API_KEY or OPENAI_API_KEY). Falling back to deterministic prompts."
            )
        if not self.vision_client:
            logger.info(
                "[PromotionPromptService] OPENAI_API_KEY is missing, so uploaded images are not analyzed."
            )

    @staticmethod
    def _build_text_client() -> tuple[str | None, OpenAI | None, str]:
        """프롬프트 기획용 LLM. 프로젝트 기본 LLM인 DeepSeek을 우선 쓰고, 없으면 OpenAI를 쓴다."""
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        provider = (os.getenv("PROMOTION_PROMPT_PROVIDER") or "").strip().lower()
        if not provider:
            provider = "deepseek" if deepseek_key else "openai"

        if provider == "deepseek" and deepseek_key:
            # 화면에서 옵션을 바꿀 때마다 호출되므로 thinking을 끈 가벼운 모델을 기본값으로 둔다.
            model = os.getenv("PROMOTION_PROMPT_MODEL") or "deepseek-flash"
            base_url = os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
            return "deepseek", OpenAI(api_key=deepseek_key, base_url=base_url), model
        if openai_key:
            model = os.getenv("PROMOTION_PROMPT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5-mini"
            return "openai", OpenAI(api_key=openai_key, base_url=os.getenv("OPENAI_BASE_URL") or None), model
        return None, None, ""

    @staticmethod
    def _build_vision_client() -> tuple[OpenAI | None, str]:
        """업로드 사진 분석용. DeepSeek은 이미지 입력을 받지 않으므로 OpenAI를 사용한다."""
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return None, ""
        model = os.getenv("PROMOTION_VISION_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5-mini"
        return OpenAI(api_key=openai_key, base_url=os.getenv("OPENAI_BASE_URL") or None), model

    def _complete_json(self, *, system: str, user: str, temperature: float) -> str:
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "timeout": self.request_timeout,
        }
        if self.provider == "deepseek":
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        if self.model_name.startswith("gpt-5"):
            # GPT-5 계열은 temperature를 받지 않는다.
            params["reasoning_effort"] = "low"
        else:
            params["temperature"] = temperature

        response = self.client.chat.completions.create(**params)
        return response.choices[0].message.content or ""

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("[PromotionPromptService] Asset file is missing: %s", path)
            return {}

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _guess_mime_type(image_path: str | None) -> str:
        mime_type, _ = mimetypes.guess_type(image_path or "")
        return mime_type or "image/jpeg"

    def _normalize_style(self, style: str) -> str:
        return self.STYLE_MAP.get((style or "").strip().lower(), "energetic")

    def _get_template(self, style: str) -> dict[str, Any]:
        template_id = self._normalize_style(style)
        for template in self.templates:
            if template.get("id") == template_id:
                return template
        return dict(self.DEFAULT_TEMPLATE, id=template_id)

    def _get_reference_blueprint(self, style: str) -> str:
        style_key = self._normalize_style(style)
        vibe_hint = self.VIBE_LABELS.get(style_key, "")

        for blueprint in self.reference_blueprints:
            target_vibe = blueprint.get("target_vibe") or ""
            if vibe_hint and (
                vibe_hint.lower() in target_vibe.lower()
                or style_key in target_vibe.lower()
            ):
                return blueprint.get("reference_style_blueprint") or ""

        if self.reference_blueprints:
            return self.reference_blueprints[0].get("reference_style_blueprint") or ""

        return ""

    def _extract_image_context(self, image_path: str | None) -> str:
        if not image_path or not self.vision_client:
            return "No reference image analysis available."

        image_file = Path(image_path)
        if not image_file.exists():
            return "No reference image analysis available."

        try:
            with image_file.open("rb") as file:
                image_bytes = file.read()

            image_url = (
                f"data:{self._guess_mime_type(image_path)};base64,"
                f"{base64.b64encode(image_bytes).decode('ascii')}"
            )
            params: dict[str, Any] = {
                "model": self.vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Analyze this food photo for a short SNS ad. Return a compact description "
                                    "(under 80 words) of the dish, plating, lighting, texture, steam, sauce, "
                                    "dominant colors, and the strongest appetizing cues."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "timeout": self.request_timeout,
            }
            if self.vision_model.startswith("gpt-5"):
                params["reasoning_effort"] = "low"
            else:
                params["temperature"] = 0.2
            response = self.vision_client.chat.completions.create(**params)
            return (response.choices[0].message.content or "").strip() or "Image analysis was empty."
        except Exception as exc:
            logger.warning("[PromotionPromptService] Image analysis failed: %s", exc)
            return "Image analysis unavailable."

    @staticmethod
    def _concept_tokens(concept: str) -> list[str]:
        return [
            token.lower()
            for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", concept or "")
            if len(token) >= 2
        ]

    @classmethod
    def _normalize_hashtags(cls, title: str, concept: str, hashtags: list[str]) -> list[str]:
        normalized: list[str] = []
        candidates = hashtags + [
            concept.replace(" ", ""),
            title.replace(" ", ""),
            "홍보영상",
            "맛집",
        ]
        for candidate in candidates:
            cleaned = re.sub(r"[^0-9A-Za-z가-힣#]", "", candidate or "")
            if not cleaned:
                continue
            if not cleaned.startswith("#"):
                cleaned = f"#{cleaned}"
            if cleaned not in normalized:
                normalized.append(cleaned)
            if len(normalized) >= 5:
                break
        return normalized

    @classmethod
    def _is_grounded_in_concept(cls, concept: str, plan: PromotionPromptPlan) -> bool:
        tokens = cls._concept_tokens(concept)
        if not tokens:
            return True

        focus_text = " ".join(
            [
                plan.title,
                *plan.hashtags,
                *plan.key_elements,
                *[scene.action for scene in plan.timeline],
            ]
        ).lower()
        return any(token in focus_text for token in tokens)

    def _build_recommendation_fallback(
        self,
        *,
        target: str,
        style: str,
        store_name: str,
        store_summary: str,
        persona_label: str,
        persona_summary: str,
        persona_tags: list[str],
        action_recommendation: str,
    ) -> str:
        style_key = self._normalize_style(style)
        style_copy = {
            "energetic": "빠른 템포와 강한 음식 클로즈업으로 시선을 잡고",
            "luxury": "차분하고 고급스러운 톤으로 디테일을 살리고",
            "emotional": "따뜻하고 감성적인 분위기로 스토리를 담고",
        }

        subject = persona_label or target or "우리 가게 손님"
        summary = (persona_summary or store_summary or "우리 가게의 매력을 좋아할 손님").strip()
        summary = re.sub(r"\s+", " ", summary)
        if len(summary) > 44:
            summary = f"{summary[:43].rstrip()}…"

        tags = ", ".join(persona_tags[:2]).strip()
        persona_focus = f"{tags} 취향이 바로 반응할 수 있게" if tags else f"{summary}의 취향이 바로 반응할 수 있게"

        return " ".join(
            [
                f"{subject}를 위해 {style_copy.get(style_key, style_copy['energetic'])} {store_name or '가게'}의 대표 메뉴와 매장 분위기를 보여주세요.",
                f"{persona_focus} 먹는 순간의 표정, 김이 오르는 질감, 한입 뒤 만족감을 중심으로 구성해주세요.",
            ]
        ).strip()

    def recommend_concept_prompt(
        self,
        *,
        target: str,
        style: str,
        mode: str,
        store_name: str = "",
        store_summary: str = "",
        persona_label: str = "",
        persona_summary: str = "",
        persona_tags: list[str] | None = None,
        action_recommendation: str = "",
        image_path: str | None = None,
    ) -> str:
        persona_tags = persona_tags or []
        fallback_prompt = self._build_recommendation_fallback(
            target=target,
            style=style,
            store_name=store_name,
            store_summary=store_summary,
            persona_label=persona_label,
            persona_summary=persona_summary,
            persona_tags=persona_tags,
            action_recommendation=action_recommendation,
        )

        if not self.client:
            return fallback_prompt

        image_context = self._extract_image_context(image_path)
        style_key = self._normalize_style(style)
        style_hint = self.VIBE_LABELS.get(style_key, style_key)

        prompt = f"""
You are a Korean short-form marketing planner for restaurant reels.
Write one Korean concept description that can be pasted directly into a "video concept description" textarea.

Target persona: {target}
Persona label: {persona_label}
Persona summary: {persona_summary}
Persona tags: {", ".join(persona_tags)}
Store name: {store_name}
Store summary: {store_summary}
Action recommendation: {action_recommendation}
Requested style: {style_hint}
Requested mode: {mode}
Reference image context: {image_context}

Rules:
- Return only JSON.
- recommended_prompt must be 2 to 3 Korean sentences.
- Tailor it to the selected persona so each persona feels noticeably different.
- Reflect the requested style tone.
- Keep the focus on realistic restaurant promotion scenes.
- Do not invent discounts, events, or menu items that were not provided.
- Do not ask for subtitles, captions, or on-screen text.
""".strip()

        try:
            raw = self._complete_json(
                system='Return only a JSON object shaped like {"recommended_prompt": "..."}.',
                user=prompt,
                temperature=0.6,
            )
            recommendation = PromotionConceptRecommendation.model_validate_json(raw or "{}")
            text = (recommendation.recommended_prompt or "").strip()
            return text or fallback_prompt
        except Exception as exc:
            logger.warning("[PromotionPromptService] Concept recommendation failed: %s", exc)
            return fallback_prompt

    def _build_fallback_plan(
        self,
        *,
        target: str,
        concept: str,
        style: str,
        mode: str,
        image_context: str,
        optimizer_name: str = "fallback",
    ) -> PromotionPromptPlan:
        template = self._get_template(style)
        title = f"{concept} 한입 전에 반하는 순간"
        hook = template.get("sns_marketing_hook", "")
        blueprint = self._get_reference_blueprint(style)
        camera_angle = template.get("camera_angle", "Extreme close-up")
        lens = template.get("lens_optical_effects", "Macro lens")

        return PromotionPromptPlan(
            title=title,
            hashtags=self._normalize_hashtags(title, concept, [f"#{template.get('id', 'food')}", "#릴스"]),
            metadata=PromotionPromptMetadata(
                base_style=template.get("visual_keywords", ""),
                aspect_ratio=template.get("aspect_ratio", "9:16"),
                camera_setup=f"{camera_angle}; {lens}; mode={mode}",
                optimizer=optimizer_name,
            ),
            key_elements=[
                f"Target persona: {target}",
                f"Concept: {concept}",
                template.get("visual_keywords", ""),
                image_context,
            ],
            negative_prompts=[
                item.strip()
                for item in (template.get("negative_prompt") or "").split(",")
                if item.strip()
            ],
            timeline=[
                PromotionTimelineItem(
                    time="0-2s",
                    action=hook or "Explosive macro hook on the hero dish.",
                ),
                PromotionTimelineItem(
                    time="2-5s",
                    action=(
                        f"Show the most mouth-watering texture of {concept} with vertical commercial pacing. "
                        f"Reference style: {blueprint[:160]}"
                    ),
                ),
                PromotionTimelineItem(
                    time="5-8s",
                    action=(
                        f"Finish with the strongest appetizing angle of {concept} and make {target} want to visit immediately."
                    ),
                ),
            ],
        )

    def _build_direct_llm_plan(
        self,
        *,
        target: str,
        concept: str,
        style: str,
        mode: str,
        image_path: str | None,
        image_context: str,
        fallback: PromotionPromptPlan,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self.client:
            return None, None

        template = self._get_template(style)
        blueprint = self._get_reference_blueprint(style)
        style_key = self._normalize_style(style)

        prompt = f"""
Target persona: {target}
Concept: {concept}
Requested style: {style_key}
Requested mode: {mode}

Template visual keywords:
{template.get("visual_keywords", "")}

Camera guidance:
- angle: {template.get("camera_angle", "")}
- lens: {template.get("lens_optical_effects", "")}
- hook: {template.get("sns_marketing_hook", "")}
- food focus: {template.get("food_focus_rule", "")}
- negatives: {template.get("negative_prompt", "")}

Reference blueprint:
{blueprint}

Image context:
{image_context}

Return a JSON object with exactly these keys:
- title: catchy Korean short title (under 25 characters)
- hashtags: array of 3 to 5 short Korean hashtags
- metadata: object with string fields prompt_name, base_style, aspect_ratio, duration, location, camera_setup
- key_elements: array of short English phrases for the video model
- negative_prompts: array of short English phrases to avoid
- timeline: array of 3 objects covering 0 to 8 seconds in total, each with string fields "time" (e.g. "0-2s") and "action" (English scene direction)
""".strip()

        try:
            raw = self._complete_json(
                system=(
                    "You are a Korean SNS ad director and AI video prompt engineer. "
                    "Return only JSON that matches the requested schema. "
                    "Keep the video vertical, food-first, visually appetizing, and safe for AI video generation. "
                    "The menu concept must remain exactly aligned to the provided concept. "
                    "Do not swap in a different dish from examples or references. "
                    "Mention the concept's Korean menu name verbatim in the title."
                ),
                user=prompt,
                temperature=0.5,
            )
            data = json.loads(raw or "{}")
            # JSON 모드는 스키마를 강제하지 않으므로, 빠진 필수 metadata는 템플릿 값으로 채운다.
            data["metadata"] = {**fallback.metadata.model_dump(), **(data.get("metadata") or {})}
            plan = PromotionPromptPlan.model_validate(data)
            plan.metadata.optimizer = "llm-structured"
            if not self._is_grounded_in_concept(concept, plan):
                return fallback.model_dump(), "fallback"
            return plan.model_dump(), "llm-structured"
        except Exception as exc:
            logger.warning("[PromotionPromptService] LLM prompt generation failed: %s", exc)
            return None, None

    def _plan_from_dict(
        self,
        *,
        raw_plan: dict[str, Any],
        concept: str,
        fallback: PromotionPromptPlan,
        optimizer_name: str,
    ) -> PromotionPromptPlan:
        candidate = PromotionPromptPlan.model_validate(raw_plan)
        candidate.metadata.optimizer = optimizer_name
        candidate.hashtags = self._normalize_hashtags(candidate.title, concept, candidate.hashtags)
        if not candidate.timeline:
            candidate.timeline = fallback.timeline
        if not candidate.key_elements:
            candidate.key_elements = fallback.key_elements
        if not candidate.negative_prompts:
            candidate.negative_prompts = fallback.negative_prompts
        if not candidate.metadata.aspect_ratio:
            candidate.metadata.aspect_ratio = fallback.metadata.aspect_ratio
        if not candidate.metadata.base_style:
            candidate.metadata.base_style = fallback.metadata.base_style
        if not candidate.metadata.camera_setup:
            candidate.metadata.camera_setup = fallback.metadata.camera_setup
        if not self._is_grounded_in_concept(concept, candidate):
            logger.warning(
                "[PromotionPromptService] Generated prompt drifted away from concept '%s'. Using fallback.",
                concept,
            )
            return fallback
        return candidate

    def build_prompt_plan(
        self,
        *,
        target: str,
        concept: str,
        style: str,
        mode: str,
        image_path: str | None,
    ) -> dict[str, Any]:
        image_context = self._extract_image_context(image_path)
        fallback = self._build_fallback_plan(
            target=target,
            concept=concept,
            style=style,
            mode=mode,
            image_context=image_context,
        )

        template = self._get_template(style)
        dspy_plan, dspy_source = self.dspy_optimizer.generate_plan(
            target_persona=target,
            concept=concept,
            target_vibe=self.VIBE_LABELS.get(self._normalize_style(style), style),
            sns_marketing_hook=template.get("sns_marketing_hook", ""),
            camera_angle=template.get("camera_angle", ""),
            lens_optical_effects=template.get("lens_optical_effects", ""),
            visual_keywords=template.get("visual_keywords", ""),
            food_focus_rule=template.get("food_focus_rule", ""),
            negative_constraints=template.get("negative_prompt", ""),
            image_visual_context=image_context,
            reference_style_blueprint=self._get_reference_blueprint(style),
        )
        if dspy_plan:
            try:
                return self._plan_from_dict(
                    raw_plan=dspy_plan,
                    concept=concept,
                    fallback=fallback,
                    optimizer_name=dspy_source or "dspy",
                ).model_dump()
            except Exception as exc:
                logger.warning("[PromotionPromptService] DSPy output normalization failed: %s", exc)

        llm_plan, llm_source = self._build_direct_llm_plan(
            target=target,
            concept=concept,
            style=style,
            mode=mode,
            image_path=image_path,
            image_context=image_context,
            fallback=fallback,
        )
        if llm_plan:
            try:
                return self._plan_from_dict(
                    raw_plan=llm_plan,
                    concept=concept,
                    fallback=fallback,
                    optimizer_name=llm_source or "llm-structured",
                ).model_dump()
            except Exception as exc:
                logger.warning("[PromotionPromptService] LLM output normalization failed: %s", exc)

        return fallback.model_dump()
