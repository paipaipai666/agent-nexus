import { create } from 'zustand'
import type { ProviderDraft } from './api'

export interface PersonaDraft {
  agent_name: string
  identity: string
  tone: string
  projects: { name: string; focus: string }[]
}

interface SettingsDraft {
  fields: Record<string, string>
  persona: PersonaDraft | null
  providers: ProviderDraft[] | null
  setField: (key: string, value: string) => void
  removeField: (key: string) => void
  setPersona: (value: PersonaDraft | null) => void
  setProviders: (value: ProviderDraft[] | null) => void
  reset: () => void
}

export const useSettingsDraft = create<SettingsDraft>((set) => ({
  fields: {}, persona: null, providers: null,
  setField: (key, value) => set(state => ({ fields: { ...state.fields, [key]: value } })),
  removeField: key => set(state => {
    const fields = { ...state.fields }
    delete fields[key]
    return { fields }
  }),
  setPersona: persona => set({ persona }),
  setProviders: providers => set({ providers }),
  reset: () => set({ fields: {}, persona: null, providers: null }),
}))
