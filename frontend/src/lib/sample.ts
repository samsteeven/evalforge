/** Jeu d'exemple : cinq questions de support client, la réponse attendue figure dans le contexte fourni. */
export const SAMPLE_CASES = [
  ['Quel est le délai de remboursement ?', 'Les remboursements sont possibles sous 14 jours.'],
  ['Les frais de retour sont-ils payants ?', 'Les retours sont gratuits en point relais.'],
  ['Peut-on payer en trois fois ?', 'Le paiement en trois fois est possible dès 100 euros.'],
  ['Où suivre ma commande ?', "Le suivi est dans l'onglet Mes commandes."],
  ['Comment joindre le support ?', 'Le support répond par chat de 9 h à 18 h.'],
].map(([question, answer], index) => ({
  id: `support-${String(index + 1).padStart(2, '0')}`,
  type: 'rag',
  input: { question },
  expected: { answer },
  context: [answer],
  metadata: { category: 'customer-support' },
}))

export const SAMPLE_TEMPLATE = 'Réponds en une phrase à partir du contexte.\nContexte : {{ context }}\nQuestion : {{ question }}'
