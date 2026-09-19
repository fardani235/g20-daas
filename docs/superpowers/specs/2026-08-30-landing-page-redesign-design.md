# Landing Page Redesign — Design Spec

**Date:** 2026-08-30
**Goal:** Refactor landing page to be more attractive and informational. Add About and Contact pages.
**Primary objective:** Lead generation (drive sign-ups)
**Approach:** Full redesign (rebuild Landing.vue from scratch)

---

## Scope

### New pages
- **About** (`/about`) — Company story, technology, industry solutions
- **Contact** (`/contact`) — Qualified leads form

### Modified pages
- **Landing** (`/`) — Full redesign with new sections

### Routes to add
```js
{ path: '/about', name: 'About', component: () => import('./pages/About.vue'), meta: { layout: false } }
{ path: '/contact', name: 'Contact', component: () => import('./pages/Contact.vue'), meta: { layout: false } }
```

---

## Landing Page Sections

### 1. Nav Bar
- Fixed top, h-16, subtle border-bottom
- Left: Logo + "G20 Tech"
- Center: How it works | Capabilities | Pricing | About
- Right: Sign in + "Get started" (primary CTA)
- Mobile: hamburger menu

### 2. Hero
- Full-viewport dark background with animated gradient mesh
- Left: Headline "Turn Drone Imagery Into Actionable Intelligence", subtext, two CTAs ("Get started free" primary, "Watch demo" outline)
- Right: Floating product screenshot with glass-morphism effect, parallax tilt on hover
- Below: "Trusted by" logos strip (placeholder)
- Stats row: Resolution | Pipeline steps | Multi-tenant

### 3. How It Works (Pipeline)
- Horizontal timeline with connecting line
- Each step: circle icon, number, title, description
- Alternating light/dark backgrounds
- Steps: Capture → Process → Measure → Share
- "See it in action" link at bottom

### 4. Capabilities
- 2-column alternating layout (image left, text right)
- Each: visual placeholder + capability description
- Capabilities: Orthophotos, DSM/DTM, 3D Models, Point Clouds, Measurements, Team Workspaces
- "View all outputs" CTA

### 5. Social Proof / Testimonials
- Header: "Trusted by surveyors, engineers, and mapping teams"
- 3 testimonial cards: avatar, name, role, company, quote, 5-star rating
- Logo strip of trusted companies (placeholder)
- Muted background for visual break

### 6. Pricing
- 3 tiers:
  - **Starter** (Rp 1.7M/month): 10 GB, 100 credits, ~2 GP, Orthophoto & DSM, Email support (5 days)
  - **Standard** (Rp 8.5M/month): 100 GB, 600 credits, ~12 GP, Change detection, Map sharing, Email support (2 days) — "Most popular"
  - **Enterprise** (Contact us): On-premise, Custom integrations, Dedicated support, SLA, API access, User access control
- Enterprise CTA: "Contact us" button
- "Have questions? Contact us" link below

### 7. Final CTA
- Dark gradient background with subtle pattern
- Headline: "Ready to transform your drone data?"
- Two CTAs: "Get started free" (primary) + "Contact sales" (outline)
- Trust signals: No credit card | Free trial | Cancel anytime

### 8. Footer
- 4-column layout:
  - Brand: Logo + tagline + social icons
  - Product: Features, Pricing, API docs, Changelog
  - Company: About, Careers, Blog, Contact
  - Legal: Privacy Policy, Terms, Cookies
- Bottom bar: Copyright + "Made with ❤️ by G20 Tech"

---

## About Page (`/about`)

### Sections
1. **Hero**: "About G20 Tech" headline, mission statement
2. **Our Story**: Founding, vision, milestones (placeholder)
3. **What We Do**: IT solutions — Drone mapping, GIS consulting, Software development, Infrastructure, Data analytics
4. **Technology**: ODM pipeline, accuracy, infrastructure (PostGIS, NodeODM, cloud)
5. **Team**: Grid of member cards (avatar, name, role) — placeholder
6. **CTA**: "Join us" / "Work with us"

---

## Contact Page (`/contact`)

### Layout: Split — form left, info right

### Form fields
- Full name (text, required)
- Email (email, required)
- Company (text, required)
- Phone (tel, optional)
- Organization size (select: 1-10, 11-50, 51-200, 201-1000, 1000+)
- Use case (select: Survey & Mapping, Construction, Agriculture, Mining, Environmental, Inspection, Other)
- Message (textarea, required)
- Submit: "Send message"

### Info side
- Office address (placeholder)
- Email: info@g20tech.com
- Phone: +62 xxx
- Business hours
- Social media links (placeholder)

### Success state
- "Thank you! We'll get back to you within 24 hours."

---

## Technical Notes

- **No backend required** for Contact form initially — can use `mailto:` or a simple Frappe webhook later
- **Existing UI components**: Button, Badge, Input, Label, Select, Textarea (all available)
- **Dark mode**: All sections should support dark mode via Tailwind `dark:` variants
- **Responsive**: Mobile-first, breakpoints at sm/md/lg
- **Animations**: Subtle fade-in on scroll (IntersectionObserver), no heavy JS libraries
- **Images**: Use placeholder SVGs/gradients initially, replace with real assets later

---

## Files to create/modify

| File | Action |
|------|--------|
| `src/pages/Landing.vue` | Rewrite |
| `src/pages/About.vue` | Create |
| `src/pages/Contact.vue` | Create |
| `src/main.js` | Add routes |
| `src/lib/nav.js` | Add About link |
