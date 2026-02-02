# PULSE: Project Knowledge Base (Antigravity Optimized)

> **Role & Purpose**: This document serves as the **Single Source of Truth** for the PULSE project. It contains the project vision, core logic, feature specifications, and technical implementation details required for an AI agent to autonomously understand and develop the system.

---

## 1. Project Identity (Essence)

**PULSE** uses AI to automate the marketing loop for restaurant owners who lack time, knowledge, and resources.
It transforms the marketing process from "Owner does everything" to **"System suggests, Owner approves"**.

### Core Philosophy
1.  **Action-Centric**: Don't just show data; suggest the next step.
2.  **Single Loop**: Understand → Create → Evaluate. Keep it simple.
3.  **No Scroll Policy**: All key information must be visible without scrolling on the main dashboard.
4.  **Premium Aesthetic**: Use Deep Blue & Vivid Orange with glassmorphism for a high-end feel.

---

## 2. Technical Specification (Implementation Context)

### 2.1 Technology Stack
- **Framework**: React (Vite)
- **Styling**: Tailwind CSS (Vanilla CSS for specific animations)
- **Animation**: Framer Motion
- **Charts**: Recharts
- **Icons**: Lucide React
- **Language**: JavaScript (ES6+)

### 2.2 Directory Mapping (Source of Truth)
| Logic Layer | Directory Path | Key Components |
| :--- | :--- | :--- |
| **Dashboard** | `src/features/dashboard` | `DashboardHome.jsx` (Metrics, Weather), `SeasonAlert.jsx` |
| **Insight** | `src/features/insight` | `UnifiedInsightPage.jsx` (List/Detail), `LocalAnalysisSection.jsx` (Macro), `JourneyMapSection.jsx` (Micro) |
| **Promotion** | `src/features/promotion` | `PromotionPage.jsx`, `VideoCreator.jsx` (Storyboard/Result) |
| **Common** | `src/components/layout` | `DashboardLayout.jsx` (Side/Header), `Sidebar.jsx`, `Header.jsx` (Inline Chat) |
| **Navigation** | `src/App.jsx` | Routing configuration |

---

## 3. Core Logic (The Marketing Loop)

The user experience is defined by a continuous loop of three stages.

### Stage 1: Understand (손님 마음 읽기)
- **Goal**: Analyze reviews and local trends to determine *what* to market.
- **Input**: Online Reviews, Local Market Data.
- **Mechanism**:
    - **Unified View**: Users select between **Macro (Local Market)** and **Micro (Persona)** analysis.
    - **Persona Card**: Represents a customer segment (e.g., "Hangover Soup Lover", "Cost-effective Worker").
    - **Header Chat**: Interactive AI assistant located in the top-right header (InlineChat) to answer questions about data.
- **Output (Context)**: Passes `Target Persona` + `Vibe` to the next stage.

### Stage 2: Create (홍보 영상 만들기)
- **Goal**: Produce a high-quality short-form video (Reel) based on Stage 1 insights.
- **Input**:
    - **Context** (Auto-filled): Target, Vibe, Recommended Title.
    - **User Asset**: Photos uploaded by the owner.
- **Mechanism**:
    - **Context-Aware Navigation**: Navigating from Insight/Dashboard pre-fills the video creation settings.
    - **AI Storyboard**: Auto-generates a plan (Hook -> Body -> Outro).
    - **One-Click Generation**: Creates a vertical (9:16) video with music and captions.
- **Output**: `.mp4` video file ready for Instagram/YouTube.

### Stage 3: Evaluate & Action (우리 가게 현황)
- **Goal**: Review performance and prompt the next cycle.
- **Mechanism**:
    - **Action-Centric Dashboard**: Displays "Store Briefing" (Weather/Season) and "Traffic Trends" (Visitors/Search).
    - **Hero Section**: A centralized "AI Suggestion Card" that prompts a specific action (e.g., "Rainy day -> Make Pijeon Video").
    - **Loop Back**: Clicking the suggestion immediately leads back to **Stage 2 (Create)**.

---

## 4. UI/UX Design System

### 4.1 Color Palette
- **Primary**: Deep Blue (`#002B7A`) - Trust, Stability.
- **Action**: Vivid Orange (`#FF5A36`) - Buttons, Alerts, Calls to Action.
- **Background**: Cool Gray (`#F5F7FA`) - Reduced eye strain.

### 4.2 Component Styling
- **Rounded Corners**:
    - Containers: `24px`
    - Start Buttons: `Full Rounded`
    - Inner Elements: `12px`
- **Shadows**: Soft, diffused shadows (`0 4px 20px rgba(0, 43, 122, 0.15)`) to create depth.
- **Layout**: Split screen (Left: Navigation/List, Right: Detail/Workspace) to minimize page transitions.

---

## 5. Vision & Roadmap (Future Scope)

> **Note**: The following features are defined in the vision but are **not currently implemented** in the MVP code.

1.  **Influencer Matching (Pro)**: A dedicated loop for matching owners with food creators. (Currently conceptual).
2.  **Review Management**: Auto-reply and sentiment analysis hub. (Currently conceptual).
3.  **PULSE Score**: gamified marketing score.

---

## 6. Agent Rules (Constraint Checklist)

- [ ] **Never break the loop**: Always ensure a feature leads to the next logical step.
- [ ] **Preserve the aesthetic**: Do not introduce new colors outside the palette without reason.
- [ ] **Respect the functionality**: Do not hallucinate features (like Influencer Matching) as "working" if they are not in the `src` folder. Describe them as "planned".
- [ ] **Language**: All user-facing text must be in **Korean**.
