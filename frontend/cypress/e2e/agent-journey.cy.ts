// Un agent évalué sur sa trajectoire, et la fin du run annoncée par un webhook signé.
// L'agent de support réel tourne deux fois : sur 8200 piloté correctement, sur 8201 par un modèle négligent.
// Le faux fournisseur (8100) pilote les deux et reçoit les webhooks.

import { shoot } from '../support/shoot'

const AGENT_METRICS = ['tool_selection', 'tool_order', 'tool_arguments', 'step_efficiency', 'task_completion']

describe("Évaluation d'un agent", () => {
  it('le chemin compte, pas seulement la réponse ; webhook livré', () => {
    cy.visit('/register')
    cy.findByLabelText(/^Adresse e-mail/).type(`agent-${Date.now()}@atelier-nova.example`)
    cy.findByLabelText(/^Mot de passe/).type('mot-de-passe-e2e')
    cy.findByLabelText(/^Nom de l'espace de travail/).type('Atelier Nova')
    cy.findByRole('button', { name: 'Créer le compte' }).click()

    cy.findByLabelText(/^Nom du projet/).type('Agent de support')
    cy.findByLabelText(/^Type d'application/).select('Agent')
    cy.findByRole('button', { name: 'Créer le projet' }).click()
    cy.findByRole('link', { name: /Agent de support/ }).click()

    // Cinq demandes clients, avec les outils et arguments attendus
    cy.findByLabelText(/^Nom du jeu/).type('Demandes clients')
    cy.findByRole('button', { name: 'Créer le jeu' }).click()
    cy.findByRole('button', { name: 'Importer une version' }).click()
    cy.findByLabelText(/^Fichier JSON ou CSV/).selectFile('cypress/fixtures/demandes-clients.json')
    cy.findByRole('button', { name: 'Créer la version' }).click()
    cy.findByText('Version 1 créée : 5 cas.').should('be.visible')

    // Le message du client est transmis tel quel à l'agent
    cy.findByRole('tab', { name: 'Prompts' }).click()
    cy.findByLabelText(/^Nom du prompt/).clear().type('message-brut')
    cy.findByLabelText(/^Gabarit/).clear().type('{{ message }}', { parseSpecialCharSequences: false })
    cy.findByRole('button', { name: 'Enregistrer la version' }).click()
    cy.findByText('Version 1 enregistrée.').should('be.visible')

    // Deux versions du même agent : l'une suit la bonne démarche, l'autre non
    cy.findByRole('tab', { name: 'Modèles' }).click()
    const agents: [string, string, string][] = [
      ['Agent soigneux', 'http://127.0.0.1:8200', 'v1'],
      ['Agent négligent', 'http://127.0.0.1:8201', 'v2'],
    ]
    for (const [name, url, version] of agents) {
      cy.findByLabelText(/^Type de fournisseur/).select('evalforge_agent')
      cy.findByLabelText(/^Nom affiché/).clear().type(name)
      cy.findByLabelText(/^Adresse de l'API/).clear().type(url)
      cy.findByLabelText(/^Version de l'agent/).clear().type(version)
      cy.findByRole('button', { name: 'Ajouter le modèle' }).click()
      cy.findByText(name, { selector: 'span' }).should('be.visible')
    }

    // Un webhook prévenu de la fin du run
    cy.findByRole('tab', { name: 'Intégrations' }).click()
    cy.findByLabelText(/^URL du destinataire/).type('http://127.0.0.1:8100/webhooks/receive')
    cy.findByRole('button', { name: 'Ajouter le webhook' }).click()
    cy.findByTestId('webhook-secret').invoke('text').should('match', /^whsec_/)

    cy.findByRole('tab', { name: 'Expériences' }).click()
    cy.findByLabelText(/^Nom de l'expérience/).type('Soigneux contre négligent')
    cy.findByRole('checkbox', { name: /Agent soigneux/ }).check()
    cy.findByRole('checkbox', { name: /Agent négligent/ }).check()
    for (const metric of AGENT_METRICS) cy.findByRole('checkbox', { name: new RegExp(`^${metric}`) }).check()
    cy.findByRole('button', { name: "Créer l'expérience" }).click()
    cy.findByRole('button', { name: 'Lancer le run' }).click()

    // Les deux terminent leurs réponses ; seule la trajectoire les départage
    cy.findByText('Terminé', { timeout: 30_000 }).should('be.visible')
    for (const metric of AGENT_METRICS) {
      cy.findByRole('meter', { name: `${metric} Agent soigneux` }).should('have.attr', 'aria-valuenow', '100')
    }
    cy.findByRole('meter', { name: 'task_completion Agent négligent' }).should('have.attr', 'aria-valuenow', '100')
    cy.findByRole('meter', { name: 'tool_selection Agent négligent' }).should('have.attr', 'aria-valuenow', '20')
    cy.findByRole('meter', { name: 'tool_arguments Agent négligent' }).should('have.attr', 'aria-valuenow', '0')
    cy.findAllByTestId('result-row')
      .first()
      .should('contain.text', 'Agent soigneux')
      .findByRole('list', { name: "Trajectoire de l'agent" })
      .should('contain.text', 'find_order')
      .and('contain.text', 'get_return_policy')
    cy.findAllByTestId('result-row')
      .eq(1)
      .should('contain.text', 'Agent négligent')
      .findByRole('list', { name: "Trajectoire de l'agent" })
      .should('contain.text', 'escalate_to_human')
      .and('contain.text', 'find_order (arguments invalides)')
    shoot('trajectoire-agent')

    // Le webhook a été livré, signé
    cy.findByRole('link', { name: /Soigneux contre négligent/ }).click()
    cy.findByRole('tab', { name: 'Intégrations' }).click()
    cy.findByRole('button', { name: 'Livraisons' }).click()
    cy.findAllByTestId('delivery-row', { timeout: 15_000 }).first().should('contain.text', 'Livré')
    cy.request('http://127.0.0.1:8100/webhooks/received').then(({ body }) => {
      const last = body.at(-1)
      expect(last.event).to.equal('evaluation.completed')
      expect(last.signature).to.match(/^sha256=[0-9a-f]{64}$/)
      const rates = Object.fromEntries(last.payload.models.map((m: { model: string; pass_rates: Record<string, number> }) => [m.model, m.pass_rates]))
      expect(rates['Agent soigneux'].tool_selection).to.equal(1)
      expect(rates['Agent négligent'].tool_selection).to.equal(0.2)
    })
  })
})
