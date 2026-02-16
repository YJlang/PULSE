# AGENT: Environment Management Guide

This file serves as a guide for the AI Agent to manage the PULSE project environment.

## 🚀 One-Click Startup (Recommended)

To verifying the environment for all submodules (Frontend, Backend, AI), run the following workflow:

`view_file .agent/workflows/agent_startup.md` 
(or if supported, run it directly)

## 🛠 Manual Verification Steps

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

## 📦 Dependency Management
- Always check `requirements.txt`, `build.gradle`, and `package.json` before starting work.
- If a dependency is missing, install it and update the configuration file immediately.

## ⚠️ Troubleshooting
- **MongoDB Connection**: Ensure `mongod` is running. Check `.env` in `pulse_python` for `MONGO_URI`.
- **CUDA/GPU**: If `check_env.py` reports "CUDA is NOT available", pytorch will fall back to CPU. This is acceptable for dev but slower.
