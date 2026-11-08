// Clerk styling that matches the site's dark theme (navy #0A2342, teal #00C9A7).
//
// Used by <ClerkProvider> (so the user menu matches) and passed explicitly to
// <SignIn /> and <SignUp />. Contrast MEASURED from rendered screenshots of the
// real pages (WCAG AA needs 4.5:1 for normal text):
//   heading 13.8:1   subtitle and divider 9.0:1   field labels 11.7:1
//   placeholder 6.1:1   navy text on the teal button 7.8:1
//   footer text 9.5:1   footer link 7.6:1   "Secured by Clerk" 9.1:1
//
// IMPORTANT: this version of Clerk ignores unknown variable names without any
// warning. The old names colorText, colorTextSecondary, colorInputText,
// colorInputBackground and colorTextOnPrimaryBackground do NOT exist; that is why
// the text used to render dark gray. Use the names below.
//
// `elements` use plain style objects (not Tailwind classes) so they work without
// any CSS-layer setup for Tailwind v4.

const NAVY = "#0A2342";
const CARD = "#0F2D52";
const TEAL = "#00C9A7";
const WHITE = "#FFFFFF";
const MUTED = "#C5D2E3";
const BORDER = "rgba(255, 255, 255, 0.14)";
const BORDER_STRONG = "rgba(255, 255, 255, 0.22)";

export const clerkAppearance = {
  variables: {
    colorPrimary: TEAL,
    colorPrimaryForeground: NAVY, // text on the teal button
    colorBackground: CARD,
    colorForeground: WHITE, // main text
    colorMutedForeground: MUTED, // secondary text
    colorMuted: "rgba(255, 255, 255, 0.06)",
    colorNeutral: WHITE, // dark themes use a light neutral (borders, hover states)
    colorInput: NAVY,
    colorInputForeground: WHITE,
    colorBorder: BORDER,
    colorRing: "rgba(0, 201, 167, 0.35)",
    colorDanger: "#FF8A8A", // readable on navy (the default red is too dark)
    colorShadow: "#000000",
    borderRadius: "0.75rem",
    fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  },
  elements: {
    card: {
      borderRadius: "16px",
      border: `1px solid ${BORDER}`,
      boxShadow: "0 24px 60px rgba(0, 0, 0, 0.45)",
    },
    headerTitle: { color: WHITE, fontSize: "24px", fontWeight: 700 },
    headerSubtitle: { color: MUTED },
    socialButtonsBlockButton: {
      color: WHITE,
      background: "rgba(255, 255, 255, 0.06)",
      border: `1px solid ${BORDER_STRONG}`,
      "&:hover": { background: "rgba(255, 255, 255, 0.12)" },
    },
    socialButtonsBlockButtonText: { color: WHITE, fontWeight: 500 },
    dividerLine: { background: BORDER_STRONG },
    dividerText: { color: MUTED },
    formFieldLabel: { color: "#E6EDF7", fontWeight: 500 },
    formFieldInput: {
      color: WHITE,
      background: NAVY,
      border: `1px solid ${BORDER_STRONG}`,
      "&::placeholder": { color: "#8FA3BE" },
      "&:focus": { borderColor: TEAL, boxShadow: "0 0 0 3px rgba(0, 201, 167, 0.25)" },
    },
    formFieldInputShowPasswordButton: { color: MUTED },
    formButtonPrimary: {
      color: NAVY,
      fontWeight: 600,
      textTransform: "none",
      boxShadow: "0 0 24px rgba(0, 201, 167, 0.3)",
      "&:hover": { filter: "brightness(1.08)" },
    },
    identityPreviewText: { color: WHITE },
    identityPreviewEditButton: { color: TEAL },
    formResendCodeLink: { color: TEAL },
    backLink: { color: TEAL },
    alternativeMethodsBlockButton: {
      color: WHITE,
      border: `1px solid ${BORDER_STRONG}`,
      "&:hover": { background: "rgba(255, 255, 255, 0.08)" },
    },
    // Clerk draws a semi-transparent dark overlay over the footer, which dims
    // whatever color is set here by roughly 20%. The footer colors below are
    // deliberately brighter than the rest of the card to compensate (measured
    // result: about 8:1 for the text and 7:1 for the link).
    footer: { background: CARD, borderTop: `1px solid ${BORDER}` },
    footerAction: { background: CARD },
    footerActionText: { color: "#F2F6FB" },
    footerActionLink: { color: "#5CF0D4", fontWeight: 600, "&:hover": { color: "#8FF6E3" } },
  },
};
