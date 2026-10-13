from app.services.promotion_video_service import PromotionVideoService


def test_vertical_reel_omits_reference_images_by_default(monkeypatch):
    monkeypatch.setenv("PROMOTION_VEO_ASPECT_RATIO", "9:16")
    monkeypatch.delenv("PROMOTION_VEO_ALLOW_VERTICAL_REFERENCE_IMAGES", raising=False)

    service = PromotionVideoService()
    reference_images = [object()]

    assert service._reference_images_for_generation(reference_images) is None


def test_vertical_reference_images_can_be_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("PROMOTION_VEO_ASPECT_RATIO", "9:16")
    monkeypatch.setenv("PROMOTION_VEO_ALLOW_VERTICAL_REFERENCE_IMAGES", "true")

    service = PromotionVideoService()
    reference_images = [object()]

    assert service._reference_images_for_generation(reference_images) is reference_images


def test_landscape_generation_keeps_reference_images(monkeypatch):
    monkeypatch.setenv("PROMOTION_VEO_ASPECT_RATIO", "16:9")
    monkeypatch.delenv("PROMOTION_VEO_ALLOW_VERTICAL_REFERENCE_IMAGES", raising=False)

    service = PromotionVideoService()
    reference_images = [object()]

    assert service._reference_images_for_generation(reference_images) is reference_images


def _runway_service(monkeypatch, **env):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-runway-key")
    monkeypatch.delenv("PROMOTION_VIDEO_PROVIDER", raising=False)
    for name in ("MODEL", "RATIO", "FAST_RATIO", "DURATION", "AUDIO", "IMAGE_MODE"):
        monkeypatch.delenv(f"PROMOTION_RUNWAY_{name}", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return PromotionVideoService()


def test_runway_is_default_provider_when_key_is_set(monkeypatch):
    service = _runway_service(monkeypatch)

    assert service.video_provider == "runway"
    assert service.runway_model == "seedance2_5"


def test_veo_stays_default_provider_without_runway_key(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "")
    monkeypatch.delenv("PROMOTION_VIDEO_PROVIDER", raising=False)

    assert PromotionVideoService().video_provider == "veo"


def test_runway_text_request_uses_vertical_seedance_defaults(monkeypatch):
    service = _runway_service(monkeypatch)

    endpoint, payload = service._build_runway_request(prompt="국밥 홍보", mode="standard", image_uri=None)

    assert endpoint == "/v1/text_to_video"
    assert payload == {
        "model": "seedance2_5",
        "promptText": "국밥 홍보",
        "ratio": "1080:1920",
        "duration": 8,
        "audio": True,
    }


def test_runway_image_request_sends_photo_as_reference(monkeypatch, tmp_path):
    service = _runway_service(monkeypatch)
    photo = tmp_path / "food.png"
    photo.write_bytes(b"fake-image")

    image_uri = service._runway_image_uri(str(photo))
    endpoint, payload = service._build_runway_request(prompt="국밥 홍보", mode="standard_fast", image_uri=image_uri)

    assert endpoint == "/v1/image_to_video"
    assert image_uri.startswith("data:image/png;base64,")
    assert payload["promptImage"] == [{"uri": image_uri}]
    assert payload["ratio"] == "720:1280"


def test_runway_first_frame_mode_pins_photo_to_first_frame(monkeypatch):
    service = _runway_service(monkeypatch, PROMOTION_RUNWAY_IMAGE_MODE="first_frame")

    _, payload = service._build_runway_request(prompt="국밥 홍보", mode="pro", image_uri="runway://abc")

    assert payload["promptImage"] == [{"uri": "runway://abc", "position": "first"}]


def test_runway_generation_polls_task_and_saves_video(monkeypatch, tmp_path):
    from app.services import promotion_video_service as module

    service = _runway_service(monkeypatch, PROMOTION_RUNWAY_POLL_INTERVAL_SECONDS="0")
    service.videos_dir = tmp_path
    monkeypatch.setattr(
        service.prompt_service,
        "build_prompt_plan",
        lambda **_kwargs: {"title": "국밥 릴스", "hashtags": ["#국밥"], "timeline": [], "metadata": {}},
    )

    class FakeResponse:
        ok = True
        status_code = 200

        def __init__(self, body=None, content=b""):
            self._body = body
            self._content = content

        def json(self):
            return self._body

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self._content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    posted = {}
    task_states = iter([
        {"status": "RUNNING", "progress": 0.5},
        {"status": "SUCCEEDED", "output": ["https://cdn.example/video.mp4"], "cost": {"credits": 544}},
    ])

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted["headers"] = kwargs["headers"]
        posted["json"] = kwargs["json"]
        return FakeResponse({"id": "task-1", "estimatedCost": {"credits": 544}})

    def fake_get(url, **_kwargs):
        if url.endswith("/v1/tasks/task-1"):
            return FakeResponse(next(task_states))
        return FakeResponse(content=b"mp4-bytes")

    monkeypatch.setattr(module.requests, "post", fake_post)
    monkeypatch.setattr(module.requests, "get", fake_get)

    result = service.generate_video(target="직장인", concept="든든한 점심", style="warm", mode="standard")

    assert posted["url"] == "https://api.dev.runwayml.com/v1/text_to_video"
    assert posted["headers"] == {"Authorization": "Bearer test-runway-key", "X-Runway-Version": "2024-11-06"}
    assert posted["json"]["model"] == "seedance2_5"
    assert result["model"] == "seedance2_5"
    assert result["credits"] == 544
    saved = tmp_path / result["videoUrl"].rsplit("/", 1)[-1]
    assert saved.read_bytes() == b"mp4-bytes"
