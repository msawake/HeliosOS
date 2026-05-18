# Making Science - Brand Guidelines

This document defines the visual identity guidelines for products aligned with Making Science branding.

---

## Color Palette

### Primary Colors

| Color | Hex | Usage |
|-------|-----|-------|
| **Hot Pink** | `#F0076F` | Primary accent, CTAs, links, interactive elements |
| **Black** | `#000000` | Primary text, dark backgrounds |
| **White** | `#FFFFFF` | Backgrounds, inverted text |

### Logo Gradient Colors

The Making Science logo uses a distinctive multi-color gradient blob:

| Color | Hex | Position |
|-------|-----|----------|
| **Magenta/Pink** | `#FF1493` | Top |
| **Coral/Orange** | `#FF6B35` | Upper-middle |
| **Amber/Yellow** | `#FFB800` | Center |
| **Purple/Violet** | `#7B68EE` | Bottom |

```css
/* Logo gradient CSS */
background: linear-gradient(135deg, #FF1493 0%, #FF6B35 30%, #FFB800 50%, #7B68EE 100%);
```

### Secondary/Accent Colors

| Color | Hex | Usage |
|-------|-----|-------|
| **Vivid Purple** | `#9B51E0` | Secondary accents |
| **Vivid Cyan Blue** | `#0693E3` | Info states, links |
| **Luminous Amber** | `#FCB900` | Warnings, highlights |
| **Vivid Green Cyan** | `#00D084` | Success states |
| **Vivid Red** | `#CF2E2E` | Error states |

### Neutral Colors

| Color | Hex | Usage |
|-------|-----|-------|
| **Gray 900** | `#1A1A1A` | Dark text alternative |
| **Gray 600** | `#666666` | Secondary text |
| **Gray 400** | `#999999` | Muted text, placeholders |
| **Gray 200** | `#E5E5E5` | Borders, dividers |
| **Gray 100** | `#F5F5F5` | Light backgrounds |

---

## Typography

### Font Family

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
```

### Font Weights

| Weight | Value | Usage |
|--------|-------|-------|
| Regular | `400` | Body text |
| Medium | `500` | Subtle emphasis |
| Semi-bold | `600` | Subheadings, labels |
| Bold | `700` | Headings, CTAs |

### Font Sizes

| Size | Pixels | Rem | Usage |
|------|--------|-----|-------|
| XS | 12px | 0.75rem | Badges, captions |
| SM | 13px | 0.8125rem | Small text, metadata |
| Base | 16px | 1rem | Body text |
| MD | 18px | 1.125rem | Lead text |
| LG | 20px | 1.25rem | Subheadings |
| XL | 24px | 1.5rem | Section headings |
| 2XL | 36px | 2.25rem | Page headings |
| 3XL | 42px | 2.625rem | Hero headings |

---

## Spacing

Use a consistent 4px base unit:

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | 4px | Tight spacing |
| `space-2` | 8px | Element gaps |
| `space-3` | 12px | Small padding |
| `space-4` | 16px | Standard padding |
| `space-6` | 24px | Section gaps |
| `space-8` | 32px | Large gaps |
| `space-12` | 48px | Section padding |
| `space-16` | 64px | Page sections |

---

## Components

### Buttons

#### Primary Button
```css
.btn-primary {
  background-color: #F0076F;
  color: #FFFFFF;
  border-radius: 8px;
  padding: 12px 24px;
  font-weight: 600;
  transition: all 0.3s ease;
}

.btn-primary:hover {
  background-color: #D10663;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(240, 7, 111, 0.3);
}
```

#### Secondary Button
```css
.btn-secondary {
  background-color: transparent;
  color: #F0076F;
  border: 2px solid #F0076F;
  border-radius: 8px;
  padding: 10px 22px;
  font-weight: 600;
}

.btn-secondary:hover {
  background-color: #F0076F;
  color: #FFFFFF;
}
```

### Cards

```css
.card {
  background: #FFFFFF;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
  transition: all 0.3s ease;
}

.card:hover {
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
  transform: translateY(-2px);
}
```

### Badges/Tags

```css
.badge {
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}

.badge-primary {
  background: rgba(240, 7, 111, 0.1);
  color: #F0076F;
}

.badge-purple {
  background: rgba(155, 81, 224, 0.1);
  color: #9B51E0;
}
```

---

## Visual Effects

### Border Radius

| Size | Value | Usage |
|------|-------|-------|
| Small | 4px | Inputs, small elements |
| Medium | 8px | Buttons, cards |
| Large | 12px | Modals, large cards |
| XL | 16px | Hero sections |
| Full | 9999px | Pills, avatars |

### Shadows

```css
/* Subtle */
box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);

/* Medium (cards) */
box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);

/* Elevated (hover, modals) */
box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);

/* Pink glow (primary buttons) */
box-shadow: 0 4px 20px rgba(240, 7, 111, 0.25);
```

### Glassmorphism (Optional)

```css
.glass {
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.2);
}
```

---

## Gradients

### Logo Gradient
```css
background: linear-gradient(135deg, #FF1493 0%, #FF6B35 30%, #FFB800 50%, #7B68EE 100%);
```

### Pink Accent Gradient
```css
background: linear-gradient(135deg, #F0076F 0%, #9B51E0 100%);
```

### Warm Gradient
```css
background: linear-gradient(135deg, #FF6B35 0%, #FCB900 100%);
```

---

## Animations

### Transitions
```css
/* Default transition */
transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);

/* Quick interactions */
transition: all 0.15s ease;

/* Emphasis */
transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
```

### Hover States
- Buttons: Slight lift (`translateY(-1px)`) + shadow
- Cards: Lift (`translateY(-2px)`) + enhanced shadow
- Links: Color transition to `#F0076F`

---

## Do's and Don'ts

### Do
- Use Hot Pink (`#F0076F`) for primary CTAs and key interactions
- Maintain generous white space
- Use rounded corners consistently (8-12px)
- Apply subtle shadows for depth
- Use the gradient for decorative elements sparingly

### Don't
- Overuse the gradient - it's for accents and branding moments
- Use pure black text on colored backgrounds (use white instead)
- Mix too many accent colors in the same view
- Use sharp corners (0px radius)
- Apply heavy shadows that look dated

---

## Application Examples

### Light Theme (Default)
- Background: `#FFFFFF` or `#F5F5F5`
- Text: `#000000` / `#666666`
- Accents: `#F0076F`

### Dark Theme (Optional)
- Background: `#0A0A0A` or `#1A1A1A`
- Text: `#FFFFFF` / `#999999`
- Accents: `#F0076F` (maintains brand recognition)

---

*Based on Making Science corporate identity - AI Powered Digital Acceleration*
