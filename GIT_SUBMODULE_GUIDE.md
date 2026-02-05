# PULSE Git 서브모듈 관리 가이드

PULSE 프로젝트는 모노레포 구조로, 세 개의 독립적인 서브모듈로 구성되어 있습니다.

## 📦 프로젝트 구조

```
PULSE (메인 레포)
├── pulse_FE/          → https://github.com/SKUnohtaekyung/pulse_FE
├── pulse_python/      → https://github.com/YJlang/pulse_python
└── pulse_spring/      → https://github.com/YJlang/pulse_spring
```

## 🚀 일상적인 작업 흐름

### 1. 서브모듈에서 코드 수정 후 커밋/푸시

각 서브모듈은 독립적인 Git 저장소이므로 **두 단계**가 필요합니다:

#### 예시: pulse_python에서 작업하는 경우

```powershell
# 1단계: 서브모듈 폴더로 이동하여 커밋/푸시
cd c:\pulse\pulse_python
git add .
git commit -m "설명적인 커밋 메시지"
git push origin main

# 2단계: 메인 레포로 돌아와서 서브모듈 참조 업데이트
cd c:\pulse
git add pulse_python
git commit -m "Update pulse_python submodule reference"
git push origin main
```

#### 여러 서브모듈을 동시에 수정한 경우

```powershell
# 각 서브모듈에서 커밋/푸시
cd c:\pulse\pulse_FE
git add .
git commit -m "Update frontend components"
git push origin main

cd c:\pulse\pulse_python
git add .
git commit -m "Update backend services"
git push origin main

cd c:\pulse\pulse_spring
git add .
git commit -m "Update Spring controllers"
git push origin main

# 메인 레포에서 모든 참조 업데이트
cd c:\pulse
git add pulse_FE pulse_python pulse_spring
git commit -m "Update all submodule references"
git push origin main
```

## 🔄 프로젝트 클론 및 초기 설정

### 새로운 팀원이 프로젝트를 시작하는 경우

```powershell
# 옵션 1: 서브모듈 포함하여 클론 (권장)
git clone --recurse-submodules https://github.com/YJlang/PULSE

# 옵션 2: 이미 클론한 경우 서브모듈 초기화
git clone https://github.com/YJlang/PULSE
cd PULSE
git submodule update --init --recursive
```

## 📥 서브모듈 업데이트 (최신 상태로 동기화)

### 다른 팀원이 서브모듈을 업데이트한 경우

```powershell
# 메인 레포에서 최신 변경사항 가져오기
cd c:\pulse
git pull origin main

# 서브모듈을 메인 레포가 참조하는 커밋으로 업데이트
git submodule update --remote --merge
```

## 🎯 자주 사용하는 명령어

### 상태 확인

```powershell
# 메인 레포 상태
cd c:\pulse
git status

# 모든 서브모듈 상태 한눈에 보기
git submodule status

# 개별 서브모듈 상태
git -C pulse_FE status
git -C pulse_python status
git -C pulse_spring status
```

### 브랜치 관리

```powershell
# 서브모듈의 현재 브랜치 확인
git -C pulse_FE branch
git -C pulse_python branch
git -C pulse_spring branch

# 서브모듈에서 브랜치 전환
cd c:\pulse\pulse_python
git checkout -b feature/new-feature
```

### 변경사항 확인

```powershell
# 각 서브모듈의 변경된 파일 확인
git -C pulse_FE diff
git -C pulse_python diff
git -C pulse_spring diff
```

## ⚠️ 주의사항

### 1. 두 단계 커밋을 잊지 마세요!
- ❌ 서브모듈에서만 커밋하고 메인 레포를 업데이트하지 않으면, 다른 팀원이 최신 변경사항을 못 받습니다.
- ✅ 항상 서브모듈 커밋 → 메인 레포 참조 업데이트 순서를 지키세요.

### 2. 작업 전 항상 최신 상태 확인
```powershell
cd c:\pulse
git pull origin main
git submodule update --remote --merge
```

### 3. Detached HEAD 상태 주의

서브모듈은 기본적으로 특정 커밋을 가리키므로, 서브모듈에서 작업하기 전에 브랜치에 있는지 확인하세요:

```powershell
cd c:\pulse\pulse_python
git checkout main  # 브랜치로 이동
git pull           # 최신 상태로 업데이트
# 이제 작업 시작
```

## 🔧 문제 해결

### "서브모듈 변경사항이 있는데 커밋이 안 돼요"

서브모듈 내부의 변경사항은 서브모듈 자체 저장소에서 커밋해야 합니다:

```powershell
cd c:\pulse\pulse_python
git status  # 어떤 파일이 변경되었는지 확인
git add .
git commit -m "Fix bug"
git push origin main
```

### "서브모듈이 올바른 커밋을 가리키지 않아요"

```powershell
cd c:\pulse
git submodule update --init --recursive
```

### "새로운 서브모듈이 추가되었어요"

```powershell
cd c:\pulse
git pull origin main
git submodule update --init --recursive
```

## 📝 빠른 참조 스크립트

### 전체 동기화 (모든 서브모듈 커밋/푸시)

프로젝트 루트에서 실행:

```powershell
# pulse_FE
cd c:\pulse\pulse_FE
git add .
git commit -m "Update frontend"
git push origin main

# pulse_python
cd c:\pulse\pulse_python
git add .
git commit -m "Update backend"
git push origin main

# pulse_spring
cd c:\pulse\pulse_spring
git add .
git commit -m "Update Spring Boot"
git push origin main

# 메인 레포 업데이트
cd c:\pulse
git add pulse_FE pulse_python pulse_spring
git commit -m "Update all submodule references"
git push origin main
```

---

## 💡 팁

- **VS Code Extension**: Git Submodules extension을 사용하면 GUI로 관리 가능
- **자동화**: 반복적인 작업은 PowerShell 스크립트로 자동화 고려
- **커밋 메시지**: 서브모듈과 메인 레포 모두 명확한 커밋 메시지 사용

---

**마지막 업데이트:** 2026-02-05
