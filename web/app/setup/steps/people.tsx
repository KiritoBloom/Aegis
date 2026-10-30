import { CONDITION_SUGGESTIONS } from "@/lib/console";
import type { SetupController } from "../controller";
import styles from "../setup.module.css";

export function PeopleStep({ c }: { c: SetupController }) {
  const {
    setup, customCondition, setCustomCondition,
    setOccupant, toggleCondition, addCustomCondition, addOccupant, removeOccupant,
  } = c;
  return (
    <section>
      <h1 className={styles.title}>Who is this house watching over?</h1>
      <p className={styles.lede}>
        For the person being cared for, the answers change when a fall becomes an emergency
        call. For everyone else, they help Aegis tell family from a stranger at night.
      </p>

      <div className={styles.peopleList}>
        {setup.occupants.map((person, i) => (
          <div key={person.id} className={styles.personCard}>
            <div className={styles.personHead}>
              <span className={styles.personIndex}>
                PERSON {String(i + 1).padStart(2, "0")}{i === 0 ? " · PRIMARY" : ""}
              </span>
              {setup.occupants.length > 1 ? (
                <button type="button" className={styles.personRemove}
                  onClick={() => removeOccupant(person.id)}
                  aria-label={`Remove ${person.name || "this person"}`}>
                  Remove
                </button>
              ) : null}
            </div>

            <div className={styles.fieldGrid}>
              <label className={styles.field}>
                <span className={styles.label}>Name</span>
                <input
                  className={styles.input}
                  value={person.name}
                  onChange={(event) => setOccupant(person.id, { name: event.target.value })}
                  placeholder={i === 0 ? "Margaret" : "Sam"}
                  autoComplete="off"
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Age</span>
                <input
                  className={styles.input}
                  type="number"
                  min={1}
                  max={129}
                  value={person.age ?? ""}
                  onChange={(event) => setOccupant(person.id, {
                    age: event.target.value ? Number(event.target.value) : null,
                  })}
                  placeholder="81"
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Relationship</span>
                <input
                  className={styles.input}
                  value={person.relationship}
                  onChange={(event) => setOccupant(person.id, { relationship: event.target.value })}
                  list={`relationships-${person.id}`}
                  autoComplete="off"
                />
                <datalist id={`relationships-${person.id}`}>
                  {["Resident", "Spouse", "Daughter", "Son", "Parent", "Friend", "Carer", "Family"].map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Living situation</span>
                <span className={styles.checkRow}>
                  <input
                    type="checkbox"
                    checked={person.livesAlone}
                    onChange={(event) => setOccupant(person.id, { livesAlone: event.target.checked })}
                  />
                  Lives alone
                </span>
                <span className={styles.help}>Leave off when someone else is in the house at night.</span>
              </label>
            </div>

            <div className={styles.conditionBlock}>
              <span className={styles.label}>Known conditions</span>
              <p className={styles.help}>
                Self-reported context, not a diagnosis. Aegis uses it to decide how quickly
                to call, never to name a cause in an alert.
              </p>
              <div className={styles.chips}>
                {CONDITION_SUGGESTIONS.map((condition) => {
                  const on = person.conditions.includes(condition.id);
                  return (
                    <button
                      key={condition.id}
                      type="button"
                      className={styles.chip}
                      data-on={on}
                      onClick={() => toggleCondition(person, condition.id)}
                      aria-pressed={on}
                    >
                      {condition.label}
                    </button>
                  );
                })}
                {person.conditions
                  .filter((condition) => !CONDITION_SUGGESTIONS.some((item) => item.id === condition))
                  .map((condition) => (
                    <button
                      key={condition}
                      type="button"
                      className={styles.chip}
                      data-on="true"
                      onClick={() => toggleCondition(person, condition)}
                      aria-pressed="true"
                    >
                      {condition} ×
                    </button>
                  ))}
              </div>
              <div className={styles.inlineForm}>
                <input
                  className={styles.input}
                  value={customCondition[person.id] ?? ""}
                  onChange={(event) => setCustomCondition((prev) => ({
                    ...prev,
                    [person.id]: event.target.value,
                  }))}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      addCustomCondition(person);
                    }
                  }}
                  placeholder="Add your own, e.g. epilepsy"
                  maxLength={40}
                  autoComplete="off"
                />
                <button type="button" className={styles.secondary} onClick={() => addCustomCondition(person)}>
                  Add
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className={styles.actions}>
        <button type="button" className={styles.secondary} onClick={addOccupant}
          disabled={setup.occupants.length >= 6}>
          Add another person
        </button>
        <span className={styles.help}>At least one name is required. Everyone else is optional.</span>
      </div>
    </section>
  );
}
