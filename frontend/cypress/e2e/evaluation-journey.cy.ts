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
    cy.findByLabelText(/^Fichier JSON ou CSV/).selectFile('cypress/fixtures/support-faq.csv')
    cy.findByRole('button', { name: 'Créer la version' }).click()
    cy.findByText('Version 1 créée : 5 cas.').should('be.visible')
    cy.findByText('v1').should('be.visible')
    cy.findByText('Pas encore de version.').should('not.exist')
    shoot('jeu-de-donnees')

    // Prompt versionné
    cy.findByRole('tab', { name: 'Prompts' }).click()
    cy.findByRole('button', { name: 'Enregistrer la version' }).click()
    cy.findByText('Version 1 enregistrée.').should('be.visible')

    // Deux modèles comparés (un seul avec un prix) et un modèle juge
    cy.findByRole('tab', { name: 'Modèles' }).click()
    const models: [string, string, boolean][] = [
      ['Modèle sourcé', 'grounded', true],
      ['Modèle vague', 'vague', false],
      ['Juge', 'judge', false],
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
    cy.findByRole('checkbox', { name: /^judge_correctness/ }).check()
    cy.findByLabelText(/^Modèle juge/).select('Juge (judge)')
    cy.findByRole('button', { name: "Créer l'expérience" }).click()
    cy.findByText(/juge : Juge/).should('be.visible')
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
    cy.findByRole('meter', { name: 'judge_correctness Modèle sourcé' }).should('have.attr', 'aria-valuenow', '100')
    cy.findByRole('meter', { name: 'judge_correctness Modèle vague' }).should('have.attr', 'aria-valuenow', '0')
    cy.findAllByText(/^Juge : La réponse reprend l'information attendue\./).should('have.length', 5)
    cy.findAllByTestId('result-row').should('have.length', 10)
    shoot('run-comparaison')

    // Rapport HTML téléchargé avec le jeton d'accès, puis relu
    cy.findByRole('button', { name: 'Rapport HTML' }).click()
    cy.location('pathname').then((path) => {
      const runId = path.split('/').pop()
      cy.readFile(`cypress/downloads/evalforge-run-${runId}.html`, { timeout: 15_000 })
        .should('contain', "Run d&#39;évaluation : Sourcé contre vague")
        .and('contain', 'Juge (judge)')
        .and('contain', 'inconnu')
    })

    // Baseline : ce run devient la référence du projet
    cy.findByRole('button', { name: 'Adopter comme baseline' }).click()
    cy.findByText('Ce run est la baseline du projet').should('be.visible')

    // Nouveau prompt, sans le contexte : le modèle sourcé ne trouve plus l'information
    cy.findByRole('link', { name: /Sourcé contre vague/ }).click()
    cy.findByRole('tab', { name: 'Prompts' }).click()
    cy.findByLabelText(/^Gabarit/)
      .clear()
      .type('Réponds en une phrase.\nQuestion : {{ question }}', { parseSpecialCharSequences: false })
    cy.findByRole('button', { name: 'Enregistrer la version' }).click()
    cy.findByText('Version 2 enregistrée.').should('be.visible')

    cy.findByRole('tab', { name: 'Expériences' }).click()
    cy.findByLabelText(/^Nom de l'expérience/).type('Prompt sans contexte')
    cy.findByLabelText(/^Version du prompt/).select('faq v2')
    cy.findByRole('checkbox', { name: /Modèle sourcé/ }).check()
    cy.findByRole('checkbox', { name: /Modèle vague/ }).check()
    cy.findByRole('checkbox', { name: /^exact_match/ }).check()
    cy.findByRole('checkbox', { name: /^contains_expected/ }).check()
    cy.findByRole('button', { name: "Créer l'expérience" }).click()
    cy.findByText('Prompt sans contexte').parents('section').first().within(() => {
      cy.findByRole('button', { name: 'Lancer le run' }).click()
    })

    // La comparaison à la baseline signale la régression, cas par cas
    cy.findByText('Terminé', { timeout: 30_000 }).should('be.visible')
    cy.findByText(/^Régression détectée face à la baseline/).should('be.visible')
    cy.findByRole('region', { name: 'Comparaison avec la baseline' })
      .findAllByTestId('comparison-row')
      .first()
      .should('contain.text', 'Modèle sourcé')
      .and('contain.text', '100 %')
      .and('contain.text', '-100 pts')
      .and('contain.text', '5 / 0')
    shoot('baseline-regression')
  })

  it('sans session, renvoie vers la connexion', () => {
    cy.visit('/')
    cy.findByRole('heading', { name: 'Connexion' }).should('be.visible')
  })
})
