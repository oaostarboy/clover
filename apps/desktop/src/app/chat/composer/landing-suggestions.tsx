import { type ComposerTarget, requestComposerInsert } from './focus'

const suggestions = [
  ['Draft a quote', 'Help me draft a quote for'],
  ['Plan my week', 'Help me plan my week:'],
  ['Reply to a client', 'Help me reply to a client:'],
  ['Feeling lucky?', 'Surprise me with an idea for']
] as const

/** Fill the draft, rather than invoking a slash command that may not exist. */
export function LandingSuggestions({ target }: { target: ComposerTarget }) {
  return (
    <div aria-label="Suggested prompts" className="landing-suggestions">
      {suggestions.map(([label, prompt]) => (
        <button
          className="landing-suggestion"
          key={label}
          onClick={() => requestComposerInsert(prompt, { target })}
          type="button"
        >
          {label}
        </button>
      ))}
    </div>
  )
}
