// Parcours complet dans un vrai navigateur : faux fournisseur LLM, API FastAPI sur PostgreSQL, front Vite.

const LLM = 'http://127.0.0.1:8100/v1'

const shoot = (name: string) => {
  if (Cypress.expose('screenshots')) cy.screenshot(name, { capture: 'fullPage', overwrite: true })
}

describe('Évaluation de bout en bout', () => {
  it('un compte, cinq questions, deux modèles comparés', () => {
    const email = `e2e-${Date.now()}@atelier-nova.example`

    // Inscription
    cy.visit('/register')
    cy.findByLabelText(/^Adresse e-mail/).type(email)
    cy.findByLabelText(/^Mot de passe/).type('mot-de-passe-e2e')
    cy.findByLabelText(/^Nom de l'espace de travail/).type('Atelier Nova')
    cy.findByRole('button', { name: 'Créer le compte' }).click()
    cy.findByRole('heading', { name: 'Projets' }).should('be.visible')

    // Projet
    cy.findByLabelText(/^Nom du projet/).type('Customer Support AI')
    cy.findByRole('button', { name: 'Créer le projet' }).click()
    cy.findByRole('link', { name: /Customer Support AI/ }).click()
    cy.findByRole('heading', { name: 'Customer Support AI' }).should('be.visible')

    // Jeu de données : cinq questions, version immuable
    cy.findByLabelText(/^Nom du jeu/).type('FAQ support')
    cy.findByRole('button', { name: 'Créer le jeu' }).click()
    cy.findByRole('button', { name: 'Importer une version' }).click()
    cy.findByRole('button', { name: "Charger l'exemple" }).click()
    cy.findByRole('button', { name: 'Créer la version' }).click()
    cy.findByText('Version 1 créée : 5 cas.').should('be.visible')
    cy.findByText('v1').should('be.visible')
    cy.findByText('Pas encore de version.').should('not.exist')
    shoot('jeu-de-donnees')

    // Prompt versionné
    cy.findByRole('tab', { name: 'Prompts' }).click()
    cy.findByRole('button', { name: 'Enregistrer la version' }).click()
    cy.findByText('Version 1 enregistrée.').should('be.visible')

    // Deux modèles, un seul avec un prix
    cy.findByRole('tab', { name: 'Modèles' }).click()
    const models: [string, string, boolean][] = [
      ['Modèle sourcé', 'grounded', true],
      ['Modèle vague', 'vague', false],
    ]
    for (const [name, model, priced] of models) {
      cy.findByLabelText(/^Nom affiché/).clear().type(name)
      cy.findByLabelText(/^Adresse de l'API/).clear().type(LLM)
      cy.findByLabelText(/^Identifiant du modèle/).clear().type(model)
      cy.findByLabelText(/^Prix entrée/).clear()
      cy.findByLabelText(/^Prix sortie/).clear()
      if (priced) {
        cy.findByLabelText(/^Prix entrée/).type('0.27')
        cy.findByLabelText(/^Prix sortie/).type('1.10')
      }
      cy.findByRole('button', { name: 'Ajouter le modèle' }).click()
      cy.findByText(name, { selector: 'span' }).should('be.visible')
    }

    // Expérience et run
    cy.findByRole('tab', { name: 'Expériences' }).click()
    cy.findByLabelText(/^Nom de l'expérience/).type('Sourcé contre vague')
    cy.findByRole('checkbox', { name: /Modèle sourcé/ }).check()
    cy.findByRole('checkbox', { name: /Modèle vague/ }).check()
    cy.findByRole('checkbox', { name: /^exact_match/ }).check()
    cy.findByRole('checkbox', { name: /^contains_expected/ }).check()
    cy.findByRole('button', { name: "Créer l'expérience" }).click()
    cy.findByRole('button', { name: 'Lancer le run' }).click()

    // Résultats et comparaison
    cy.findByText('Terminé', { timeout: 30_000 }).should('be.visible')
    cy.findByText('10 / 10 réponses').should('be.visible')
    cy.findByRole('meter', { name: 'exact_match Modèle sourcé' }).should('have.attr', 'aria-valuenow', '100')
    cy.findByRole('meter', { name: 'exact_match Modèle vague' }).should('have.attr', 'aria-valuenow', '0')
    cy.findByRole('region', { name: 'Performance et coût' }).within(() => {
      cy.findByRole('row', { name: /Modèle vague/ }).should('contain.text', 'inconnu')
      cy.findByRole('row', { name: /Modèle sourcé/ }).should('not.contain.text', 'inconnu')
    })
    cy.findAllByTestId('result-row').should('have.length', 10)
    shoot('run-comparaison')
  })

  it('sans session, renvoie vers la connexion', () => {
    cy.visit('/')
    cy.findByRole('heading', { name: 'Connexion' }).should('be.visible')
  })
})
