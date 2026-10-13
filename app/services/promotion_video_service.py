import base64
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv
from google.auth.exceptions import DefaultCredentialsError
from google import genai
from google.genai import types

from app.services.promotion_prompt_service import PromotionPromptService
from app.utils.logger import get_logger

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local", override=False)

logger = get_logger(__name__)


RUNWAY_API_VERSION = "2024-11-06"
RUNWAY_PROMPT_MAX_LENGTH = 15000
# base64로 인코딩하면 약 4/3배가 되므로, data URI 한도 5MB에 맞춰 원본 크기를 제한한다.
RUNWAY_DATA_URI_MAX_BYTES = 3_500_000

FALSEY_ENV_VALUES = {"0", "false", "off", "no"}
VERTICAL_REEL_ASPECT_RATIO = "9:16"
TRUEY_ENV_VALUES = {"1", "true", "on", "yes"}


class PromotionVideoService:
    def __init__(self) -> None:
        self.output_root = BASE_DIR / "output" / "promotion"
        self.uploads_dir = self.output_root / "uploads"
        self.videos_dir = self.output_root / "videos"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)

        self.prompt_service = PromotionPromptService()
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.veo_api_key = os.getenv("VEO_API_KEY")
        self.gemini_api_key = self.veo_api_key or os.getenv("GEMINI_API_KEY")
        self.vertex_api_key = os.getenv("VERTEX_VIDEO_API_KEY")
        self.vertex_output_gcs_uri = os.getenv("PROMOTION_VEO_OUTPUT_GCS_URI", "").strip()
        self.aspect_ratio = (os.getenv("PROMOTION_VEO_ASPECT_RATIO") or VERTICAL_REEL_ASPECT_RATIO).strip()
        if not self.aspect_ratio:
            self.aspect_ratio = VERTICAL_REEL_ASPECT_RATIO
        self.allow_vertical_reference_images = (
            os.getenv("PROMOTION_VEO_ALLOW_VERTICAL_REFERENCE_IMAGES", "false").strip().lower()
            in TRUEY_ENV_VALUES
        )
        self.provider = (os.getenv("PROMOTION_VEO_PROVIDER") or "").strip().lower()
        if not self.provider:
            self.provider = "vertex" if self.project_id else "gemini"
        self.enable_secondary_backend_fallback = (
            os.getenv("PROMOTION_VEO_ENABLE_SECONDARY_BACKEND_FALLBACK", "false").strip().lower()
            not in FALSEY_ENV_VALUES
        )

        # Gemini API defaults.
        self.standard_model = os.getenv("PROMOTION_VEO_STANDARD_MODEL", "veo-3.1-generate-preview")
        self.pro_model = os.getenv("PROMOTION_VEO_PRO_MODEL", self.standard_model)
        self.fast_model = os.getenv("PROMOTION_VEO_FAST_MODEL", "veo-3.1-fast-generate-preview")
        self.fallback_model = os.getenv("PROMOTION_VEO_FALLBACK_MODEL", "")

        # Vertex defaults use the GA Veo 3.1 line so image-based reels can stay vertical.
        self.vertex_standard_model = os.getenv(
            "PROMOTION_VEO_VERTEX_STANDARD_MODEL",
            "veo-3.1-fast-generate-001",
        )
        self.vertex_pro_model = os.getenv(
            "PROMOTION_VEO_VERTEX_PRO_MODEL",
            "veo-3.1-generate-001",
        )
        self.vertex_fast_model = os.getenv(
            "PROMOTION_VEO_VERTEX_FAST_MODEL",
            "veo-3.1-fast-generate-001",
        )
        self.vertex_fallback_model = os.getenv("PROMOTION_VEO_VERTEX_FALLBACK_MODEL", "")

        self.standard_resolution = os.getenv("PROMOTION_VEO_STANDARD_RESOLUTION", "1080p")
        self.pro_resolution = os.getenv("PROMOTION_VEO_PRO_RESOLUTION", "4k")
        self.fast_resolution = os.getenv("PROMOTION_VEO_FAST_RESOLUTION", "720p")
        self.fallback_resolution = os.getenv("PROMOTION_VEO_FALLBACK_RESOLUTION", "720p")
        self.vertex_standard_resolution = os.getenv("PROMOTION_VEO_VERTEX_STANDARD_RESOLUTION", "720p")
        self.vertex_pro_resolution = os.getenv("PROMOTION_VEO_VERTEX_PRO_RESOLUTION", self.vertex_standard_resolution)
        self.vertex_fast_resolution = os.getenv("PROMOTION_VEO_VERTEX_FAST_RESOLUTION", self.vertex_standard_resolution)
        self.vertex_fallback_resolution = os.getenv(
            "PROMOTION_VEO_VERTEX_FALLBACK_RESOLUTION",
            self.vertex_standard_resolution,
        )

        # Runway API (Seedance 등). RUNWAYML_API_SECRET이 있으면 기본 영상 백엔드로 사용한다.
        self.runway_api_key = (os.getenv("RUNWAYML_API_SECRET") or "").strip()
        self.video_provider = (os.getenv("PROMOTION_VIDEO_PROVIDER") or "").strip().lower()
        if not self.video_provider:
            self.video_provider = "runway" if self.runway_api_key else "veo"
        self.runway_base_url = (
            os.getenv("PROMOTION_RUNWAY_BASE_URL") or "https://api.dev.runwayml.com"
        ).rstrip("/")
        self.runway_model = os.getenv("PROMOTION_RUNWAY_MODEL", "seedance2_5")
        # ratio는 "가로:세로" 픽셀 값이며 해상도까지 함께 결정한다. (1080:1920 = 1080p 세로)
        self.runway_ratio = os.getenv("PROMOTION_RUNWAY_RATIO", "1080:1920")
        self.runway_fast_ratio = os.getenv("PROMOTION_RUNWAY_FAST_RATIO", "720:1280")
        self.runway_duration = int(os.getenv("PROMOTION_RUNWAY_DURATION", "8"))
        self.runway_audio = (
            os.getenv("PROMOTION_RUNWAY_AUDIO", "true").strip().lower() not in FALSEY_ENV_VALUES
        )
        # reference: 업로드 사진을 참고 이미지로만 사용, first_frame: 영상 첫 프레임으로 고정
        self.runway_image_mode = os.getenv("PROMOTION_RUNWAY_IMAGE_MODE", "reference").strip().lower()
        self.runway_poll_interval = float(os.getenv("PROMOTION_RUNWAY_POLL_INTERVAL_SECONDS", "5"))
        self.runway_timeout = float(os.getenv("PROMOTION_RUNWAY_TIMEOUT_SECONDS", "900"))

    @staticmethod
    def _guess_mime_type(filename: str | None) -> str:
        mime_type, _ = mimetypes.guess_type(filename or "")
        return mime_type or "image/jpeg"

    def save_upload(self, filename: str, content: bytes) -> str:
        suffix = Path(filename or "upload.jpg").suffix or ".jpg"
        upload_path = self.uploads_dir / f"{uuid.uuid4().hex}{suffix}"
        upload_path.write_bytes(content)
        return str(upload_path)

    def _build_prompt_text(self, plan: dict[str, Any]) -> str:
        metadata = plan.get("metadata") or {}
        timeline = plan.get("timeline") or []
        key_elements = plan.get("key_elements") or []
        negative_prompts = plan.get("negative_prompts") or []

        scene_lines = []
        for scene in timeline:
            time_label = scene.get("time", "")
            action = scene.get("action", "")
            if action:
                scene_lines.append(f"{time_label}: {action}".strip(": "))

        negative_text = ", ".join(str(item) for item in negative_prompts if item)
        prompt = (
            "Create a vertical 9:16 Korean restaurant promotional reel. "
            f"Base style: {metadata.get('base_style', '')}. "
            f"Camera setup: {metadata.get('camera_setup', '')}. "
            f"Location: {metadata.get('location', '')}. "
            f"Key elements: {', '.join(str(item) for item in key_elements if item)}. "
            f"Timeline: {' '.join(scene_lines)}."
        )
        if negative_text:
            prompt += f" Avoid: {negative_text}."
        return prompt.strip()

    def _build_reference_images(self, image_path: str | None) -> list[types.VideoGenerationReferenceImage] | None:
        if not image_path:
            return None

        file_path = Path(image_path)
        if not file_path.exists():
            return None

        with file_path.open("rb") as file:
            image_bytes = file.read()

        image = types.Image(
            imageBytes=image_bytes,
            mimeType=self._guess_mime_type(file_path.name),
        )
        return [
            types.VideoGenerationReferenceImage(
                image=image,
                referenceType=types.VideoGenerationReferenceType.ASSET,
            )
        ]

    def _reference_images_for_generation(
        self,
        reference_images: list[types.VideoGenerationReferenceImage] | None,
    ) -> list[types.VideoGenerationReferenceImage] | None:
        if not reference_images:
            return None

        if self.aspect_ratio == VERTICAL_REEL_ASPECT_RATIO and not self.allow_vertical_reference_images:
            logger.info(
                "[PromotionVideoService] Uploaded image was used for prompt analysis, but was not sent "
                "as a Veo reference image because reference-image generation does not reliably support "
                "%s output. Set PROMOTION_VEO_ALLOW_VERTICAL_REFERENCE_IMAGES=true only after the "
                "configured backend supports it.",
                VERTICAL_REEL_ASPECT_RATIO,
            )
            return None

        return reference_images

    def _client_builders(self) -> list[tuple[str, Callable[[], genai.Client]]]:
        available_builders: dict[str, Callable[[], genai.Client]] = {}
        if self.gemini_api_key:
            available_builders["gemini-api"] = lambda: genai.Client(api_key=self.gemini_api_key)
        if self.project_id:
            available_builders["vertex-adc"] = lambda: genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.location,
                http_options=types.HttpOptions(api_version="v1"),
            )

        if not available_builders:
            return []

        if self.provider == "gemini":
            priority = ["gemini-api", "vertex-adc"]
        else:
            priority = ["vertex-adc", "gemini-api"]

        ordered_names = [name for name in priority if name in available_builders]
        if self.enable_secondary_backend_fallback:
            return [(name, available_builders[name]) for name in ordered_names]

        return [(ordered_names[0], available_builders[ordered_names[0]])]

    def _mode_profiles(self, mode: str, *, client_name: str) -> list[dict[str, str]]:
        normalized_mode = (mode or "").strip().lower()
        if client_name.startswith("vertex"):
            standard_model = self.vertex_standard_model
            pro_model = self.vertex_pro_model
            fast_model = self.vertex_fast_model
            fallback_model = self.vertex_fallback_model
            standard_resolution = self.vertex_standard_resolution
            pro_resolution = self.vertex_pro_resolution
            fast_resolution = self.vertex_fast_resolution
            fallback_resolution = self.vertex_fallback_resolution
        else:
            standard_model = self.standard_model
            pro_model = self.pro_model
            fast_model = self.fast_model
            fallback_model = self.fallback_model
            standard_resolution = self.standard_resolution
            pro_resolution = self.pro_resolution
            fast_resolution = self.fast_resolution
            fallback_resolution = self.fallback_resolution

        if normalized_mode == "pro":
            profiles = [
                {"name": "pro", "model": pro_model, "resolution": pro_resolution},
                {"name": "standard", "model": standard_model, "resolution": standard_resolution},
            ]
        elif normalized_mode == "standard_fast":
            profiles = [
                {"name": "fast", "model": fast_model, "resolution": fast_resolution},
                {"name": "standard", "model": standard_model, "resolution": standard_resolution},
            ]
        else:
            profiles = [
                {"name": "standard", "model": standard_model, "resolution": standard_resolution},
                {"name": "fast", "model": fast_model, "resolution": fast_resolution},
            ]

        if fallback_model:
            profiles.append(
                {"name": "fallback", "model": fallback_model, "resolution": fallback_resolution}
            )

        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for profile in profiles:
            key = (profile["model"], profile["resolution"])
            if key in seen or not profile["model"]:
                continue
            seen.add(key)
            deduped.append(profile)
        return deduped

    @staticmethod
    def _duration_for_profile(resolution: str, reference_images: list[types.VideoGenerationReferenceImage] | None) -> int:
        if resolution.lower() in {"1080p", "4k"} or reference_images:
            return 8
        return 6

    def _build_video_config(
        self,
        *,
        client_name: str,
        profile: dict[str, str],
        plan: dict[str, Any],
        reference_images: list[types.VideoGenerationReferenceImage] | None,
        duration_seconds: int,
    ) -> types.GenerateVideosConfig:
        requested_aspect_ratio = ((plan.get("metadata") or {}).get("aspect_ratio") or "").strip()
        aspect_ratio = self.aspect_ratio
        if requested_aspect_ratio and requested_aspect_ratio != aspect_ratio:
            logger.info(
                "[PromotionVideoService] Overriding prompt aspect ratio %s -> %s for mobile reels.",
                requested_aspect_ratio,
                aspect_ratio,
            )

        config_kwargs: dict[str, Any] = {
            "aspect_ratio": aspect_ratio,
            "duration_seconds": duration_seconds,
            "resolution": profile["resolution"],
            "negative_prompt": ", ".join(plan.get("negative_prompts") or []),
        }

        if reference_images:
            config_kwargs["reference_images"] = reference_images

        if client_name.startswith("vertex") and self.vertex_output_gcs_uri:
            config_kwargs["output_gcs_uri"] = self.vertex_output_gcs_uri

        return types.GenerateVideosConfig(**config_kwargs)

    def _normalize_generation_error(self, errors: list[Exception]) -> RuntimeError:
        if not errors:
            return RuntimeError(
                "Video generation could not be completed. "
                "No usable Gemini or Vertex authentication method was found."
            )

        error = errors[-1]
        message = str(error)
        combined_message = " | ".join(str(item) for item in errors)
        deprecated_vertex_preview_models = (
            "veo-3.1-generate-preview",
            "veo-3.1-fast-generate-preview",
            "veo-3.0-generate-preview",
            "veo-3.0-fast-generate-preview",
        )

        if (
            any("RESOURCE_EXHAUSTED" in str(item) or "Quota exceeded" in str(item) for item in errors)
            and any(isinstance(item, DefaultCredentialsError) or "DefaultCredentialsError" in str(item) for item in errors)
        ):
            return RuntimeError(
                "The configured Gemini API key is out of quota, and the fallback Vertex AI path is not authenticated "
                "on this machine. Enable billing for the Gemini key or set up Vertex AI ADC credentials."
            )

        if isinstance(error, DefaultCredentialsError) or "DefaultCredentialsError" in message:
            return RuntimeError(
                "Vertex AI authentication is not configured on this machine. "
                "Set up Application Default Credentials (ADC) for the configured Google Cloud project "
                "before using Veo through Vertex AI. "
                "This backend does not rely on VERTEX_VIDEO_API_KEY alone."
            )

        if "RESOURCE_PROJECT_INVALID" in message:
            return RuntimeError(
                "The provided Vertex video key could not be mapped to a usable Google Cloud project for Veo. "
                "Use standard Vertex AI project authentication (ADC/service account) with "
                "GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION for video generation."
            )

        if any(model in combined_message for model in deprecated_vertex_preview_models) and (
            "NOT_FOUND" in combined_message or "404" in combined_message
        ):
            return RuntimeError(
                "The configured Vertex Veo preview model is no longer available. "
                "Use the GA model IDs veo-3.1-generate-001 or veo-3.1-fast-generate-001 instead."
            )

        if "Unsupported output video aspect ratio" in combined_message:
            return RuntimeError(
                "The configured Veo backend rejected this image-to-video request because the requested "
                "output aspect ratio is not supported with reference images. For vertical reels, use the "
                "uploaded image for prompt analysis but do not send it as a Veo reference image, or switch "
                "the output aspect ratio to a supported format."
            )

        if "RESOURCE_EXHAUSTED" in message or "Quota exceeded" in message:
            if "free_tier" in message or "FreeTier" in message:
                return RuntimeError(
                    "The configured Gemini API key has exhausted its free-tier quota. "
                    "Enable paid billing for that key or switch to Vertex AI with project credentials."
                )
            return RuntimeError(
                "Google rejected the Veo request because the configured account or project is out of quota "
                "or billing capacity."
            )

        return RuntimeError(f"Video generation could not be completed. {combined_message}")

    def _persist_generated_video(self, client: genai.Client, video: types.Video) -> str:
        local_path = self.videos_dir / f"{uuid.uuid4().hex}.mp4"

        # Vertex responses can inline the generated MP4 bytes instead of exposing
        # a downloadable file handle. Persist them directly when present.
        video_bytes = getattr(video, "video_bytes", None)
        if video_bytes:
            local_path.write_bytes(bytes(video_bytes))
            return f"/static/promotion/videos/{local_path.name}"

        try:
            client.files.download(file=video)
            video.save(str(local_path))
            return f"/static/promotion/videos/{local_path.name}"
        except Exception as exc:
            logger.warning("[PromotionVideoService] Download/save failed: %s", exc)

        uri = getattr(video, "uri", None)
        if uri:
            uri = str(uri)
            if uri.startswith("http://") or uri.startswith("https://"):
                return uri

        raise RuntimeError("Generated video could not be saved locally.")

    def _runway_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.runway_api_key}",
            "X-Runway-Version": RUNWAY_API_VERSION,
        }

    def _runway_ratio_for_mode(self, mode: str) -> str:
        if (mode or "").strip().lower() == "standard_fast":
            return self.runway_fast_ratio
        return self.runway_ratio

    def _runway_image_uri(self, image_path: str | None) -> str | None:
        if not image_path:
            return None

        file_path = Path(image_path)
        if not file_path.exists():
            return None

        image_bytes = file_path.read_bytes()
        mime_type = self._guess_mime_type(file_path.name)
        if len(image_bytes) <= RUNWAY_DATA_URI_MAX_BYTES:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

        # data URI 한도(5MB)를 넘는 사진은 Runway 임시 업로드를 거쳐 runway:// URI로 전달한다.
        response = requests.post(
            f"{self.runway_base_url}/v1/uploads",
            headers=self._runway_headers(),
            json={"filename": file_path.name, "type": "ephemeral"},
            timeout=30,
        )
        self._raise_for_runway_error(response)
        upload = response.json()
        upload_response = requests.post(
            upload["uploadUrl"],
            data=upload.get("fields") or {},
            files={"file": (file_path.name, image_bytes, mime_type)},
            timeout=120,
        )
        upload_response.raise_for_status()
        return upload["runwayUri"]

    def _build_runway_request(
        self,
        *,
        prompt: str,
        mode: str,
        image_uri: str | None,
    ) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.runway_model,
            "promptText": prompt[:RUNWAY_PROMPT_MAX_LENGTH],
            "ratio": self._runway_ratio_for_mode(mode),
            "duration": self.runway_duration,
            "audio": self.runway_audio,
        }
        if not image_uri:
            return "/v1/text_to_video", payload

        image: dict[str, str] = {"uri": image_uri}
        if self.runway_image_mode == "first_frame":
            image["position"] = "first"
        payload["promptImage"] = [image]
        return "/v1/image_to_video", payload

    @staticmethod
    def _raise_for_runway_error(response: requests.Response) -> None:
        if response.ok:
            return

        try:
            detail = response.json().get("error") or response.text
        except ValueError:
            detail = response.text

        if response.status_code == 401:
            raise RuntimeError("Runway API 키가 올바르지 않습니다. RUNWAYML_API_SECRET 값을 확인하세요.")
        if response.status_code == 429:
            raise RuntimeError(f"Runway 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요. {detail}")
        if response.status_code == 400 and "credit" in str(detail).lower():
            raise RuntimeError(f"Runway 크레딧이 부족합니다. {detail}")
        raise RuntimeError(f"Runway API 요청이 실패했습니다. ({response.status_code}) {detail}")

    def _wait_for_runway_task(self, task_id: str, notify: Callable[[int, str], None]) -> dict[str, Any]:
        deadline = time.monotonic() + self.runway_timeout
        while time.monotonic() < deadline:
            response = requests.get(
                f"{self.runway_base_url}/v1/tasks/{task_id}",
                headers=self._runway_headers(),
                timeout=30,
            )
            self._raise_for_runway_error(response)
            task = response.json()
            status = task.get("status")

            if status == "SUCCEEDED":
                return task
            if status in {"FAILED", "CANCELLED"}:
                raise RuntimeError(
                    "Runway 영상 생성이 실패했습니다. "
                    f"{task.get('failureCode') or status}: {task.get('failure') or ''}".strip()
                )

            progress = float(task.get("progress") or 0)
            notify(55 + int(progress * 35), "Runway가 영상을 생성하는 중입니다.")
            time.sleep(self.runway_poll_interval)

        raise RuntimeError(f"Runway 영상 생성이 {int(self.runway_timeout)}초 안에 끝나지 않았습니다. (task {task_id})")

    def _download_runway_video(self, url: str) -> str:
        local_path = self.videos_dir / f"{uuid.uuid4().hex}.mp4"
        with requests.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            with local_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    file.write(chunk)
        return f"/static/promotion/videos/{local_path.name}"

    def _generate_with_runway(
        self,
        *,
        plan: dict[str, Any],
        prompt: str,
        concept: str,
        mode: str,
        image_path: str | None,
        notify: Callable[[int, str], None],
        started_at: float,
    ) -> dict[str, Any]:
        if not self.runway_api_key:
            raise RuntimeError(
                "Runway API 키가 설정되지 않았습니다. pulse_python/.env에 RUNWAYML_API_SECRET을 넣어 주세요."
            )

        endpoint, payload = self._build_runway_request(
            prompt=prompt,
            mode=mode,
            image_uri=self._runway_image_uri(image_path),
        )
        notify(45, f"영상 생성 모델(runway:{payload['model']}, {payload['ratio']})에 요청하는 중입니다.")

        response = requests.post(
            f"{self.runway_base_url}{endpoint}",
            headers=self._runway_headers(),
            json=payload,
            timeout=60,
        )
        self._raise_for_runway_error(response)
        created = response.json()
        task_id = created["id"]
        logger.info(
            "[PromotionVideoService] Runway task %s created (model=%s, ratio=%s, duration=%ss, estimated credits=%s)",
            task_id,
            payload["model"],
            payload["ratio"],
            payload["duration"],
            (created.get("estimatedCost") or {}).get("credits"),
        )

        task = self._wait_for_runway_task(task_id, notify)
        outputs = task.get("output") or []
        if not outputs:
            raise RuntimeError("Runway 영상 생성이 결과물 없이 끝났습니다.")

        # Runway 결과 URL은 24~48시간 뒤 만료되므로 로컬에 저장해 제공한다.
        video_url = self._download_runway_video(outputs[0])
        elapsed = time.monotonic() - started_at
        notify(100, "홍보영상 생성이 완료되었습니다.")
        return {
            "videoUrl": video_url,
            "videoTitle": plan.get("title") or f"{concept} 홍보영상",
            "hashtags": plan.get("hashtags") or ["#홍보영상", "#맛집"],
            "generationTime": f"{elapsed:.1f}s",
            "promptPlan": plan,
            "model": payload["model"],
            "resolution": payload["ratio"],
            "credits": (task.get("cost") or {}).get("credits"),
        }

    def generate_video(
        self,
        *,
        target: str,
        concept: str,
        style: str,
        mode: str,
        image_path: str | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        notify = progress_callback or (lambda *_args: None)
        started_at = time.monotonic()

        notify(15, "홍보영상 콘셉트를 정리하는 중입니다.")
        plan = self.prompt_service.build_prompt_plan(
            target=target,
            concept=concept,
            style=style,
            mode=mode,
            image_path=image_path,
        )
        prompt = self._build_prompt_text(plan)

        if self.video_provider == "runway":
            return self._generate_with_runway(
                plan=plan,
                prompt=prompt,
                concept=concept,
                mode=mode,
                image_path=image_path,
                notify=notify,
                started_at=started_at,
            )

        uploaded_reference_images = self._build_reference_images(image_path)
        reference_images = self._reference_images_for_generation(uploaded_reference_images)
        optimizer_name = (plan.get("metadata") or {}).get("optimizer")

        client_builders = self._client_builders()
        if not client_builders:
            raise RuntimeError(
                "No Veo authentication is configured. "
                "Set VEO_API_KEY or GEMINI_API_KEY for the Gemini API, or configure "
                "GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION with Vertex AI ADC credentials."
            )

        attempt_errors: list[Exception] = []
        for client_name, build_client in client_builders:
            try:
                client = build_client()
            except Exception as exc:
                attempt_errors.append(exc)
                logger.warning(
                    "[PromotionVideoService] Failed to initialize %s client: %s",
                    client_name,
                    exc,
                )
                continue

            for profile in self._mode_profiles(mode, client_name=client_name):
                try:
                    duration_seconds = self._duration_for_profile(
                        profile["resolution"],
                        reference_images,
                    )
                    notify(
                        45,
                        (
                            f"영상 생성 모델({client_name}:{profile['model']}, "
                            f"{profile['resolution']}, optimizer={optimizer_name or 'fallback'})에 요청하는 중입니다."
                        ),
                    )

                    operation = client.models.generate_videos(
                        model=profile["model"],
                        prompt=prompt,
                        config=self._build_video_config(
                            client_name=client_name,
                            profile=profile,
                            plan=plan,
                            reference_images=reference_images,
                            duration_seconds=duration_seconds,
                        ),
                    )

                    progress = 55
                    while not operation.done:
                        notify(progress, "Veo가 영상을 생성하는 중입니다.")
                        time.sleep(10)
                        operation = client.operations.get(operation)
                        progress = min(progress + 10, 90)

                    generated_videos = []
                    if getattr(operation, "response", None):
                        generated_videos = getattr(operation.response, "generated_videos", []) or []
                    if not generated_videos and getattr(operation, "result", None):
                        generated_videos = getattr(operation.result, "generated_videos", []) or []

                    if not generated_videos:
                        error = getattr(operation, "error", None)
                        raise RuntimeError(f"Video generation finished without output. {error}")

                    video_url = self._persist_generated_video(client, generated_videos[0].video)
                    elapsed = time.monotonic() - started_at
                    notify(100, "홍보영상 생성이 완료되었습니다.")
                    return {
                        "videoUrl": video_url,
                        "videoTitle": plan.get("title") or f"{concept} 홍보영상",
                        "hashtags": plan.get("hashtags") or ["#홍보영상", "#맛집"],
                        "generationTime": f"{elapsed:.1f}s",
                        "promptPlan": plan,
                        "model": profile["model"],
                        "resolution": profile["resolution"],
                    }
                except Exception as exc:
                    attempt_errors.append(exc)
                    logger.warning(
                        "[PromotionVideoService] Video generation attempt via %s/%s failed: %s",
                        client_name,
                        profile["model"],
                        exc,
                    )

        raise self._normalize_generation_error(attempt_errors)
