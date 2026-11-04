import { describe, expect, it } from 'vitest'
import { milliseconds, percent, templateVariables, usd } from './format'

describe('format', () => {
  it('ne présente jamais un coût inconnu comme nul', () => {
    expect(usd(null)).toBe('inconnu')
    expect(usd(undefined)).toBe('inconnu')
    expect(usd(0)).toBe('0 $')
    expect(usd(0.000123)).toBe('0,00012 $')
    expect(usd(0.0021)).toBe('0,0021 $')
    expect(usd(0.4567)).toBe('0,4567 $')
    expect(usd(12.5)).toBe('12,50 $')
  })

  it('affiche les taux et les durées', () => {
    expect(percent(null)).toBe('—')
    expect(percent(0.875)).toBe('88 %')
    expect(milliseconds(245.6)).toBe('246 ms')
    expect(milliseconds(1530)).toBe('1,5 s')
  })

  it('détecte les variables comme le serveur', () => {
    expect(templateVariables('{{ context }} puis {{question}} et {{ question }}')).toEqual(['context', 'question'])
    expect(templateVariables('{question.__class__} {{ 1x }}')).toEqual([])
  })
})
