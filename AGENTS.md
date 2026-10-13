# AGENT: Environment Management Guide

This file serves as a guide for the AI Agent to manage the PULSE project environment.

## OpenAI Docs MCP

Always use the OpenAI developer documentation MCP server if you need to work with the OpenAI API, ChatGPT Apps SDK, or Codex, unless the user explicitly asks for a different source.

## Context7 MCP

Always use Context7 when you need library or framework documentation, setup steps, configuration details, or current package usage examples, unless the official vendor documentation is more authoritative.

## Skill Directory

Use `./.agents/skills` as the canonical repository skill directory.
Do not recreate `./.agent/skills`; the repository now uses the official `.agents/skills` layout.

## One-Click Startup (Recommended)

To verify the environment for all submodules (Frontend, Backend, AI), run the following workflow:

`view_file .agents/workflows/agent_startup.md`
(or if supported, run it directly)

Current practical fallback in this repository:

- `powershell -ExecutionPolicy Bypass -File .\scripts\run_full_validation.ps1`
- This runs FE lint/build, Spring tests, Python pytest, and the Playwright smoke flow in sequence.

## Project Brief Reference

- Before making substantial product or code decisions, read `.agents/context/PULSE_PROJECT_BRIEF.md`.
- If documentation and implementation conflict, prefer the current code in `pulse_FE`, `pulse_spring`, and `pulse_python`.
- Note: `.agents/workflows/agent_startup.md` is currently not present in this repository, so use the manual verification steps below unless that workflow is added later.

## Frontend Guardrail

- The frontend UI structure and visual design are immutable unless the user explicitly asks for a UI/design change.
- Do not change layout hierarchy, spacing system, typography choices, colors, component composition, interaction patterns, or visual assets in `pulse_FE` just to make backend integration easier.
- Prefer backend/API adaptation or data-shape alignment first. If a frontend code change is unavoidable, keep the rendered UI identical to the current mock-based experience.

## Manual Verification Steps

If you need to verify manually, follow these steps:

### 1. Python (AI Server)
- **Path**: `c:\PULSE\pulse_python`
- **Activation**: **MUST** activate virtual environment:
  - Windows: `.venv\Scripts\activate`
  - Mac/Linux: `source .venv/bin/activate`
- **Check Script**: Run `python check_env.py`
  - Checks Python version
  - Checks `requirements.txt` packages
  - Checks CUDA/GPU availability

### 2. Spring Boot (Main Backend)
- **Path**: `c:\PULSE\pulse_spring`
- **Build Check**: `./gradlew.bat clean build -x test`

### 3. Frontend (React)
- **Path**: `c:\PULSE\pulse_FE`
- **Dependency Check**: `npm list`

## Dependency Management

- Always check `requirements.txt`, `build.gradle`, and `package.json` before starting work.
- If a dependency is missing, install it and update the configuration file immediately.
- Whenever runtime environment variables change, update the corresponding `.env.example` file in the same turn so another machine can boot after `pull + env copy + install`.

## Troubleshooting

- **MongoDB Connection**: Ensure `mongod` is running. Check `.env` in `pulse_python` for `MONGO_URI`.
- **CUDA/GPU**: If `check_env.py` reports "CUDA is NOT available", pytorch will fall back to CPU. This is acceptable for dev but slower.

## Recurring Gotchas

- **Windows Python logging**: On Windows terminals, emoji/unicode log lines can trigger `cp949` encoding errors during FastAPI startup. Keep `pulse_python/check_env.py` and `pulse_python/app/utils/logger.py` configured to reconfigure stdout/stderr to UTF-8 with replacement.
- **Frontend auth state**: Any logout path must clear both `accessToken` and `analysisTaskId`. Navigating to `/login` without clearing storage breaks real login/logout verification and can hide auth bugs during E2E testing.
- **Persona fallback**: `BERTopic` can return zero topics on small review sets. Keep `pulse_python/app/services/llm_service.py` generating at least one fallback persona from the full review set so the customer journey page never receives an empty `personas` array after a successful analysis.
- **Insight top3 contract**: The customer insight response must always preserve the mock-era data shape and provide three persona cards when analysis succeeds, even if topic extraction returns fewer than three clusters.
- **Promotion persona handoff**: In `홍보 영상 만들기`, target personas must come from the real analysis response and use the raw persona `id`. Do not remap them back to fixed hardcoded keys like `hangover/worker/couple`.
- **Promotion nav params**: `손님 분석 -> 맞춤 릴스 제작` depends on `navParams`. Sidebar-driven menu changes must clear stale params, or the promotion page can reopen with the wrong previously selected persona.
- **Promotion auto prompt contract**: AI auto-complete is expected to react to persona, style gallery, quality mode, and uploaded image changes. If the user manually edits the textarea, auto mode should turn off instead of overwriting their text again.
- **Promotion prompt performance**: Keep `/api/info/prompt-recommendation` lightweight. Do not run the full heavy video prompt planning pipeline on every persona/style toggle, or the UI will feel broken.
- **Promotion env contract**: When adding or changing video-generation integrations, update `pulse_python/.env.example` with every required variable. Right now promotion video generation uses Runway Seedance (`RUNWAYML_API_SECRET`), prompt planning/recommendation uses `DEEPSEEK_API_KEY` (falling back to `OPENAI_API_KEY`), and uploaded-image analysis uses `OPENAI_API_KEY`. The Google variables (`GEMINI_API_KEY`, `VEO_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `VERTEX_VIDEO_API_KEY`) are only needed when `PROMOTION_VIDEO_PROVIDER=veo`.
- **Promotion quota reality**: Veo/Gemini video generation can fail with `429 RESOURCE_EXHAUSTED` even when integration is correct. Treat quota/billing failures as external capacity issues first, and use prompt recommendation or prompt preview endpoints to verify code paths before suspecting a frontend/backend bug.
- **Playwright smoke browser dependency**: `pulse_FE/scripts/playwright-smoke.mjs` uses a locally installed Chrome/Edge unless `PULSE_PLAYWRIGHT_BROWSER_PATH` is set. On a fresh machine, browser availability is part of the E2E readiness check.
- **Review raw snapshots**: For review-management features, keep a latest-per-store raw review snapshot in MongoDB and avoid storing the full review array redundantly for every task. This keeps storage and retrieval costs bounded for real service usage.
- **Review data truthfulness**: In the review-management tab, never invent platform splits or category metrics. Naver/Kakao counts, ratings, and review content must come from actual stored review data.
- **Spring Boot 4 Jackson wiring**: In this repository, do not assume `com.fasterxml.jackson.databind.ObjectMapper` is auto-injectable as a Spring bean. Verify the actual configuration first or instantiate locally inside the service when needed.
- **FastAPI proxy encoding**: When Spring proxies Korean shop names or addresses to FastAPI, build a `URI` and pass that to `RestTemplate`. Pre-encoded strings can double-encode query parameters and break lookup.
- **Windows FastAPI background start**: In this environment, detached `cmd /c start "" /min powershell ... uvicorn ...` has been more reliable than direct `Start-Process` calls for keeping FastAPI alive in the background during E2E work.
- **Naver review crawling**: Do not treat `https://m.place.naver.com/restaurant/list?...` as a detail page just because the URL contains `/restaurant/`. Resolve the first real place detail link, move to `/restaurant/{placeId}/review/visitor`, then parse review cards from DOM after expand/scroll passes.
- **Review reply selection contract**: In `리뷰 관리 & 답변`, AI reply generation must follow the explicitly selected review cards, not the top-most reviews by default order. Keep selection state stable when switching between `리뷰관리` and `빠른 설정`.
- **Windows Spring bootRun background start**: In this environment, redirecting stdout/stderr from `gradlew.bat bootRun` can fail with console handle errors. Prefer `Start-Process cmd.exe /c gradlew.bat bootRun --console=plain` without output redirection when you need a detached Spring server for E2E work.
- **Windows Word document locks**: After any DOCX or PDF automation with Word COM, explicitly close the document, quit `WINWORD`, and verify there are no leftover `WINWORD` processes or `~$*.docx` lock files in the target folder. Before handing the file back for manual edits, confirm it can be opened with read/write access.
