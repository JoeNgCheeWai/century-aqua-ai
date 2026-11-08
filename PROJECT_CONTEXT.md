# Century Aqua AI (CAIP) - Architect's Blueprint

## 1. Project Overview
Century Aqua AI is a multi-agent AI platform for the aquaculture industry. It features a "Gatekeeper" agent for routing and a "Specialist" agent (Dr. Aqua) for expert, neutral aquaculture advice. The platform includes a Next.js frontend with a dark "bio-tech" UI and a FastAPI backend.

## 2. Tech Stack
- **Frontend:** Next.js (App Router), React, Tailwind CSS.
- **Backend:** Python, FastAPI, Uvicorn.
- **AI API:** Anthropic API (using the `anthropic` Python SDK).
- **Styling:** Tailwind CSS with custom colors: Primary `#00C9A7` (Teal), Background `#0A2342` (Dark Navy).

## 3. Directory Structure
- `frontend/src/app/page.tsx`: Main frontend file containing Navbar, Hero, Products, ProductModal, and ChatWidget.
- `backend/app/main.py`: FastAPI entry point, CORS setup, and `/api/chat` endpoint.
- `backend/app/agents/gatekeeper.py`: Route filtering agent.
- `backend/app/agents/dr_aqua.py`: Expert aquaculture agent.
- `frontend/public/images/products/`: Directory for product images.

## 4. CRITICAL RULES (DO NOT BREAK)

### AI Model Constraints
- **Gatekeeper Model:** MUST use `claude-haiku-5-5`.
- **Dr. Aqua Model:** MUST use `claude-sonnet-5-5`.
- *Never* revert to deprecated 2024 models (e.g., claude-3-haiku-20240307, claude-3-5-sonnet-20241022).

### Dr. Aqua Persona & Tone
- Dr. Aqua MUST maintain a strictly **neutral, educational, and objective** tone.
- **NEVER** use marketing, promotional, or sales language.
- When mentioning Century Aqua products (like SND Probiotic), present them as objective industry solutions. Only direct users to the product range if they express specific interest.

### SND Probiotic Knowledge Base
When discussing water quality, ammonia, or sludge, Dr. Aqua must first provide core management steps (Feed management, Aeration), then objectively mention SND Probiotic with these exact facts:
- Developed with NUS (National University of Singapore) as a patented consortium.
- Single-product solution for ammonia, nitrite, and nitrate.
- 2-year hatchery trial: Zero nitrate accumulation over 6-week sea tilapia nursery (0.5cm to 4.5cm).
- Current growout trials (static pond, up to 7kg/m³): Ammonia < 1 ppm for 2 months without water changes, significant sludge reduction, stable DO.

### UI/UX Constraints
- Keep the dark bio-tech theme (`#0A2342` background, `#00C9A7` accents).
- The SND Probiotic product card MUST trigger the `ProductModal` component to show the detailed literature.
- Ensure all API calls handle errors gracefully and do not crash the frontend.

## 5. Completed Features (Phase 1 & 2)
- [x] FastAPI backend with CORS and health check.
- [x] Next.js frontend with responsive, dark-themed UI.
- [x] Multi-agent architecture (Gatekeeper + Dr. Aqua).
- [x] Conversation memory (frontend sends history, backend passes to Dr. Aqua).
- [x] SND Probiotic product card with clickable literature modal.
- [x] Integration of proprietary NUS trial data into Dr. Aqua's system prompt.
- [x] "Expert Services" section below Products (Farm Consulting, Hatchery Setup, Health Diagnostics, AI Intelligence), 4-column grid on large screens; Navbar Services dropdown links to `#services`.
- [x] Scroll animations: lightweight fade-in/slide-up via the `Reveal` component (IntersectionObserver, runs once, respects reduced-motion) on Products and Services.
- [x] Deployment configuration: `backend/Dockerfile`, `render.yaml` (Render), `frontend/vercel.json` (Vercel), `.env.example` files, pinned `backend/requirements.txt`.
  - Backend env vars: `ANTHROPIC_API_KEY`, `GATEKEEPER_MODEL`, `SPECIALIST_MODEL`, `ALLOWED_ORIGINS`, optional `ALLOWED_ORIGIN_REGEX` (Vercel previews).
  - Frontend env var: `BACKEND_URL` (Render backend base URL, no trailing slash; used at build time by the `/api/*` rewrite in `next.config.ts`). The browser calls `/api/chat` on the Vercel domain and Vercel proxies it to Render, so CORS is bypassed in production. Redeploy Vercel after changing it.
  - Security: API-key debug print removed from `main.py`; never commit `backend/.env`.
- [x] Product card images use local files for reliability: SND Probiotic uses `/images/products/snd-probiotic-product.jpg`; RAS Systems uses `/images/products/ras.jpg` (alt text: "Recirculating aquaculture tank with bio-filtration unit and monitoring display"). Avoid unverified stock photos; check that an image actually matches the product before using it.



## 6. Pending Tasks (For AI CTO)
- [ ] **DEFERRED:** Generate and integrate images for remaining products (Premium Feed Supplement, Semi-Automated Inoculation System, Fish Vaccines, Health Diagnostics). Teal gradient placeholders are used until real product photos are available. (SND Probiotic and RAS Systems already have images; see Section 5.)
- [ ] Optimization pass: migrate `<img>` tags to `next/image` (clears 2 `no-img-element` lint warnings).
- [x] Hero video at `frontend/public/videos/hero.mp4` (fixed: file was misnamed `hero.mp4.mp4`; must be committed and redeployed).
- [ ] Optional: run a local Docker build test of `backend/Dockerfile` (not yet run; Render builds it in the cloud).