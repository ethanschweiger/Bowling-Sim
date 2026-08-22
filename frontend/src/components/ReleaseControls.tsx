import { useState, type KeyboardEvent } from 'react';
import { RELEASE_FIELDS, type ReleaseFieldConfig, type ReleaseFieldId } from '../domain/releaseFields';
import styles from './ReleaseControls.module.css';

interface ReleaseControlsProps {
  values: Record<ReleaseFieldId, number>;
  onChange: (id: ReleaseFieldId, value: number) => void;
  disabled?: boolean;
}

/** All six release inputs `POST /api/v1/games/{id}/throws` accepts, each a
 * paired range + number control sharing one committed value: the range for
 * quick, always-in-bounds adjustment, the number input for typing an exact
 * figure. Both are native, keyboard-operable controls. */
export function ReleaseControls({ values, onChange, disabled = false }: ReleaseControlsProps) {
  return (
    <div className={styles.fields}>
      {RELEASE_FIELDS.map((field) => (
        <ReleaseField key={field.id} config={field} value={values[field.id]} onChange={onChange} disabled={disabled} />
      ))}
    </div>
  );
}

interface ReleaseFieldProps {
  config: ReleaseFieldConfig;
  value: number;
  onChange: (id: ReleaseFieldId, value: number) => void;
  disabled: boolean;
}

function ReleaseField({ config, value, onChange, disabled }: ReleaseFieldProps) {
  // The number input keeps its own draft text so a user can freely type
  // (including transiently empty or out-of-range text) without the
  // committed value snapping it back mid-keystroke. It commits — clamped
  // to [min, max] — on blur, and stays in sync when `value` changes from
  // elsewhere (the paired range input, or a reset) — reset during render,
  // not in an effect, per React's guidance for adjusting state when a prop
  // changes: https://react.dev/learn/you-might-not-need-an-effect
  const [draftText, setDraftText] = useState(String(value));
  const [lastSyncedValue, setLastSyncedValue] = useState(value);
  if (value !== lastSyncedValue) {
    setLastSyncedValue(value);
    setDraftText(String(value));
  }

  function commitDraft(raw: string) {
    const parsed = Number(raw);
    if (Number.isFinite(parsed) && raw.trim() !== '') {
      const clamped = Math.min(config.max, Math.max(config.min, parsed));
      onChange(config.id, clamped);
      setDraftText(String(clamped));
    } else {
      setDraftText(String(value));
    }
  }

  function handleNumberKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.currentTarget.blur();
    }
  }

  const inputId = `release-${config.id}`;
  const helpId = `${inputId}-help`;

  return (
    <div className={styles.field}>
      <div className={styles.fieldHeader}>
        <label className={styles.label} htmlFor={inputId}>
          {config.label}
        </label>
        <span className={styles.readout} aria-hidden="true">
          {value}
          {config.unit}
        </span>
      </div>
      <div className={styles.controlsRow}>
        <input
          id={inputId}
          className={styles.range}
          type="range"
          min={config.min}
          max={config.max}
          step={config.step}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(config.id, Number(event.target.value))}
          aria-describedby={helpId}
        />
        <input
          className={styles.numberInput}
          type="number"
          aria-label={`${config.label}, exact value in ${config.unit}`}
          min={config.min}
          max={config.max}
          step={config.step}
          value={draftText}
          disabled={disabled}
          onChange={(event) => setDraftText(event.target.value)}
          onBlur={(event) => commitDraft(event.target.value)}
          onKeyDown={handleNumberKeyDown}
        />
      </div>
      <p className={styles.help} id={helpId}>
        {config.help}
      </p>
    </div>
  );
}
