/** Capture pleine page pour le README, seulement si EVALFORGE_SCREENSHOTS=1. */
export const shoot = (name: string) => {
  if (Cypress.expose('screenshots')) cy.screenshot(name, { capture: 'fullPage', overwrite: true })
}
