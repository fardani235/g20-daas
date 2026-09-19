## Purpose

The public marketing landing page at `/` that presents the WebODM drone-data
product to unauthenticated visitors: its visual identity, its sections and
navigation, and its interactive hero flight simulation.

## ADDED Requirements

### Requirement: Landing page visual identity

The landing page SHALL use the dark aeropulse visual identity regardless of the
application's light/dark theme setting: a near-black (`slate-950`) canvas, cyan
and gradient accents, translucent bordered surfaces, ambient glow accents, and
monospace technical labels. It MUST load and use the display, body, and
monospace font families of that identity (Space Grotesk, Plus Jakarta Sans,
JetBrains Mono), and fonts MUST fail gracefully to system fallbacks if the font
host is unreachable.

#### Scenario: Landing renders dark in light app theme

- **WHEN** the application theme is set to light and a visitor opens `/`
- **THEN** the landing page renders on the dark slate canvas with cyan accents
  and the display/monospace fonts, not the light theme's white background

#### Scenario: Font host unavailable

- **WHEN** the external font stylesheet cannot be loaded
- **THEN** the page still renders with system font fallbacks and no layout
  breakage

### Requirement: Landing page sections and content

The landing page SHALL present, in order, a navigation bar, a hero (copy plus
the flight simulation), a how-it-works pipeline, a capabilities grid, social
proof or testimonials, a pricing section, a final call to action, and a footer.
The G20 Tech / WebODM product copy, pricing plans, testimonial content, and
existing in-page and route links (sign-in, get-started, about, contact) MUST be
preserved; only presentation and the hero simulation change.

#### Scenario: Required sections present

- **WHEN** a visitor loads the landing page
- **THEN** every section listed above is rendered in order and the navigation
  anchor links scroll to their corresponding sections

#### Scenario: Existing links keep working

- **WHEN** a visitor activates the sign-in, get-started, about, or contact
  call-to-action on the landing page
- **THEN** the matching existing route is opened

### Requirement: Hero flight simulation

The hero SHALL contain an interactive canvas simulation of a drone mission over
a mapping scene, replacing the previous survey animation. The simulation SHALL
render an animated drone that follows waypoints and SHALL provide: a waypoint
path with a current target, layer/sensor switching across optical orthophoto,
thermal, and point-cloud/elevation views, a live telemetry readout (at minimum
altitude, speed, battery, and coordinates), and a detail panel describing a
selected area of interest. Clicking the canvas SHALL either select a mapped
area of interest or route the drone to a newly placed waypoint.

#### Scenario: Simulation animates on load

- **WHEN** a visitor loads the landing page
- **THEN** the drone moves along its waypoint path and the telemetry readout
  updates without any interaction

#### Scenario: Layer switching changes the scene

- **WHEN** the visitor selects the thermal or point-cloud layer control
- **THEN** the canvas renders that representation and the selected control is
  visibly active

#### Scenario: Click places a waypoint

- **WHEN** the visitor clicks empty canvas terrain
- **THEN** the drone's target waypoint is updated to that location and the
  route indicator reflects the new destination

#### Scenario: Pause, resume, and reset

- **WHEN** the visitor activates pause, resume, or reset
- **THEN** the drone motion stops, resumes, and returns to its initial waypoint
  path respectively

### Requirement: Hero simulation accessibility and motion

The hero simulation MUST be exposed to assistive technology as a labeled,
non-essential decoration whose canvas has an accessible name describing the
mission. All simulation controls MUST be operable as native buttons with
accessible names, and the simulation MUST honor `prefers-reduced-motion` by not
autoplaying continuous animation. The page MUST remain readable and usable when
the simulation is unavailable or motion is reduced.

#### Scenario: Control has accessible name

- **WHEN** a keyboard or screen-reader user reaches a simulation control
- **THEN** the control is a focusable button exposing a programmatic name and
  its action can be triggered from the keyboard

#### Scenario: Reduced motion

- **WHEN** the visitor has `prefers-reduced-motion: reduce` set
- **THEN** the simulation does not autoplay continuous motion and all page
  content remains available

#### Scenario: Simulation is non-essential

- **WHEN** the canvas is ignored by assistive technology or fails to render
- **THEN** the hero copy and calls to action remain fully present and usable

### Requirement: Hero simulation resource discipline

The simulation MUST NOT continuously consume animation frames when it is not
visible, and it MUST release its animation loop and event listeners when the
page is hidden or the component unmounts.

#### Scenario: Offscreen or hidden tab

- **WHEN** the hero scrolls out of view or the browser tab is hidden
- **THEN** the animation loop stops, and it resumes when the hero becomes
  visible again

### Requirement: Responsive landing layout

The landing page MUST be usable from small mobile widths through desktop: the
navigation SHALL collapse to a toggleable menu on small screens, and sections
MUST stack without horizontal overflow.

#### Scenario: Mobile navigation

- **WHEN** a visitor opens the landing page at a narrow viewport and activates
  the menu toggle
- **THEN** the navigation links and primary call to action are reachable and no
  content overflows horizontally
