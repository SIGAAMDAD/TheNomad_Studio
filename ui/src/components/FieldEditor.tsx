import { useState } from 'react';
import type { FieldSchema } from '../generated/operations';
import { castValue } from '../form';

interface Props {
  name: string;
  schema: FieldSchema;
  value: unknown;
  disabled?: boolean;
  required?: boolean;
  onChange: (value: unknown) => void;
  onValidityChange?: (error: string | null) => void;
}
export function FieldEditor({ name, schema, value, disabled, required, onChange, onValidityChange }: Props) {
  const [error, setError] = useState('');
  const isJson = schema.type === 'object' || schema.type === 'array';
  const [draft, setDraft] = useState<string | null>(null);
  const rendered = isJson ? JSON.stringify(value, null, 2) : String(value ?? '');
  const update = (raw: string) => {
    if (isJson) setDraft(raw);
    try {
      onChange(castValue(raw, schema)); setError(''); onValidityChange?.(null);
      if (isJson) setDraft(null);
    } catch (e) { const message = e instanceof Error ? e.message : 'Invalid field.'; setError(message); onValidityChange?.(message); }
  };
  return <label className="field">
    <span className="field-title">{name.replaceAll('_', ' ')} {required ? <span className="required">*</span> : null}</span>
    {schema.enum ? <select disabled={disabled} value={String(value ?? '')} onChange={(e) => update(e.target.value)}>
      <option value="">Select a value</option>{schema.enum.map((item) => <option key={item} value={item}>{item}</option>)}
    </select> : schema.type === 'boolean' ? <select disabled={disabled} value={String(value ?? 'false')} onChange={(e) => update(e.target.value)}><option value="false">False</option><option value="true">True</option></select>
      : isJson ? <textarea disabled={disabled} rows={5} spellCheck={false} value={draft ?? rendered} onChange={(e) => update(e.target.value)} />
      : <input disabled={disabled} spellCheck={false} value={rendered} onChange={(e) => update(e.target.value)} type={schema.type === 'integer' || schema.type === 'number' ? 'number' : 'text'} step={schema.type === 'integer' ? '1' : 'any'} />}
    {schema.description ? <small>{schema.description}</small> : null}
    {error ? <small className="error" role="alert">{error}</small> : null}
  </label>;
}
