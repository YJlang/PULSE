---
description: Check project health and environment status for all submodules
---

# Project Health Check & Startup

This workflow validates the environment for Frontend, Backend (Spring), and AI (Python) servers.

## 1. Python (AI Server) Environment Check
Runs the helper script to verify Python dependencies and CUDA status.

```bash
cd c:\PULSE\pulse_python
# Create venv if missing
if (!(Test-Path .venv)) { python -m venv .venv }
# Activate and run check
.venv\Scripts\python.exe check_env.py
```

## 2. Spring Boot (Main Backend) Build Check
Verifies that the Spring Boot project is buildable.

```bash
cd c:\PULSE\pulse_spring
./gradlew.bat --version
./gradlew.bat clean build -x test
```

## 3. Frontend (React) Setup Check
Verifies Node.js environment and dependencies.

```bash
cd c:\PULSE\pulse_FE
node -v
npm list react
```

## 4. Run Instructions
If all checks pass, you can start the servers in separate terminals:
- **FE**: `cd pulse_FE && npm run dev`
- **BE**: `cd pulse_spring && ./gradlew.bat bootRun`
- **AI**: `cd pulse_python && uvicorn app.main:app --reload`
